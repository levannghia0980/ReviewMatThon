import os
import json
import time
import logging
import concurrent.futures
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any

from app.config import settings
from app.schemas.transcript import DialogueSegment
from app.services.step_02_stt.text_cleaner import TextCleanerService
from app.services.step_04_tts.capcut_api import CapCutClient
from app.services.step_02_stt.audio_extractor import AudioExtractorService
from app.services.task_manager import task_manager

logger = logging.getLogger(__name__)

CHUNK_LENGTH_SEC = 1200
OVERLAP_SEC = 60


def format_timestamp(seconds: float) -> str:
    seconds = max(0.0, seconds)
    total_millis = int(round(seconds * 1000))
    hours, rem = divmod(total_millis, 3600 * 1000)
    mins, rem = divmod(rem, 60 * 1000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02d}:{mins:02d}:{secs:02d},{millis:03d}"


class CapCutASRService:
    @staticmethod
    def _join_words(words: List[Dict[str, Any]], is_chinese: bool) -> str:
        """Ghép từ an toàn cho cả tiếng Trung (không dấu cách) và ngôn ngữ khác (có dấu cách)."""
        if is_chinese:
            return "".join(w.get("text", "") for w in words).strip()
        parts = [w.get("text", "").strip() for w in words if w.get("text", "").strip()]
        return " ".join(parts).strip()

    @staticmethod
    def _extract_confidence(item: Dict[str, Any], words: Optional[List[Dict[str, Any]]] = None) -> float:
        """Lấy độ tin cậy thực tế từ payload của CapCut, nếu không có mới trả về -1.0 để đánh dấu."""
        if words:
            confs = [float(w["confidence"]) for w in words if "confidence" in w and w["confidence"] is not None]
            if confs:
                return round(sum(confs) / len(confs), 4)
        if "confidence" in item and item["confidence"] is not None:
            return round(float(item["confidence"]), 4)
        return -1.0

    @classmethod
    def _transcribe_single_chunk(
        cls,
        audio_path: str,
        language: str,
        use_translation: bool,
        timeout: float,
        time_offset: float = 0.0,
        chunk_idx: int = 0,
        total_chunks: int = 1
    ) -> Tuple[List[DialogueSegment], List[Dict[str, Any]]]:
        path_obj = Path(audio_path)
        logger.info(f"[CapCut ASR Worker #{chunk_idx + 1}/{total_chunks}] 📤 Đang tải lên chunk: {path_obj.name}")

        client = CapCutClient()
        last_error = None
        lang_code = "zh-CN" if language in ("zh", "zh-CN") else language
        is_chinese = "zh" in lang_code.lower()

        # Tính thời lượng thực tế của chunk để tránh lỗi fallback 300000ms (5 phút) làm cụt chunk 20 phút
        actual_chunk_duration_ms = int(AudioExtractorService.get_audio_duration(audio_path) * 1000)

        for attempt in range(1, 3):
            try:
                t0 = time.time()
                upload_res = client.upload_audio(path_obj)
                logger.info(f"[CapCut ASR Worker #{chunk_idx + 1}/{total_chunks}] ✔ Upload thành công: VID={upload_res.vid} ({time.time() - t0:.1f}s)")

                duration_ms = upload_res.duration_ms or actual_chunk_duration_ms or (CHUNK_LENGTH_SEC * 1000)

                stt_res = client.create_stt_task(
                    audio_vid=upload_res.vid,
                    audio_md5=upload_res.md5,
                    duration_ms=duration_ms,
                    language=lang_code,
                    translation_language="vi-VN",
                    use_translation=use_translation,
                    words_per_line=14
                )

                tasks = (stt_res.get("data") or {}).get("tasks") or []
                if not tasks:
                    raise RuntimeError(f"CapCut STT không tạo được task: {stt_res}")

                task_id = tasks[0]["id"]
                token = tasks[0]["token"]

                poll_start = time.time()
                final_query_res = None
                while time.time() - poll_start < timeout:
                    q_res = client.query_stt_task(task_id, token)
                    q_tasks = (q_res.get("data") or {}).get("tasks") or []
                    if q_tasks:
                        status = q_tasks[0].get("status")
                        if status in ("success", "succeed"):
                            final_query_res = q_res
                            break
                        elif status == "failed":
                            raise RuntimeError(f"CapCut STT Task thất bại: {q_res}")
                    time.sleep(1.5)

                if not final_query_res:
                    raise TimeoutError(f"CapCut STT Task quá thời gian chờ ({timeout}s)")

                payload_raw = final_query_res["data"]["tasks"][0].get("payload", "{}")
                payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
                utts = payload.get("utterances", [])

                raw_segments = []
                seg_counter = 1

                for u in utts:
                    words = u.get("words", [])
                    utt_text = u.get("text", "").strip()
                    utt_start_ms = u.get("start_time", 0)
                    utt_end_ms = u.get("end_time", utt_start_ms + 300)

                    if words and len(words) > 1:
                        current_word_group = []
                        for w in words:
                            if not current_word_group:
                                current_word_group.append(w)
                                continue

                            prev_w = current_word_group[-1]
                            gap_ms = w.get("start_time", 0) - prev_w.get("end_time", 0)
                            should_split = (gap_ms >= 350) or (len(current_word_group) >= 14 and gap_ms >= 220)

                            if should_split:
                                grp_text = cls._join_words(current_word_group, is_chinese)
                                if grp_text:
                                    s_ms = current_word_group[0].get("start_time", 0)
                                    e_ms = current_word_group[-1].get("end_time", s_ms + 200)
                                    s_sec = round((s_ms / 1000.0) + time_offset, 3)
                                    e_sec = round((e_ms / 1000.0) + time_offset, 3)
                                    if e_sec <= s_sec:
                                        e_sec = round(s_sec + 0.15, 3)

                                    raw_segments.append(
                                        DialogueSegment(
                                            id=seg_counter,
                                            start=s_sec,
                                            end=e_sec,
                                            duration=round(e_sec - s_sec, 3),
                                            text=grp_text,
                                            clean_text=grp_text,
                                            translated_text="",
                                            confidence=cls._extract_confidence(u, current_word_group)
                                        )
                                    )
                                    seg_counter += 1
                                current_word_group = [w]
                            else:
                                current_word_group.append(w)

                        if current_word_group:
                            grp_text = cls._join_words(current_word_group, is_chinese)
                            if grp_text:
                                s_ms = current_word_group[0].get("start_time", 0)
                                e_ms = current_word_group[-1].get("end_time", s_ms + 200)
                                s_sec = round((s_ms / 1000.0) + time_offset, 3)
                                e_sec = round((e_ms / 1000.0) + time_offset, 3)
                                if e_sec <= s_sec:
                                    e_sec = round(s_sec + 0.15, 3)

                                raw_segments.append(
                                    DialogueSegment(
                                        id=seg_counter,
                                        start=s_sec,
                                        end=e_sec,
                                        duration=round(e_sec - s_sec, 3),
                                        text=grp_text,
                                        clean_text=grp_text,
                                        translated_text="",
                                        confidence=cls._extract_confidence(u, current_word_group)
                                    )
                                )
                                seg_counter += 1
                    else:
                        start_sec = round((utt_start_ms / 1000.0) + time_offset, 3)
                        end_sec = round((utt_end_ms / 1000.0) + time_offset, 3)
                        if end_sec <= start_sec:
                            end_sec = round(start_sec + 0.15, 3)
                        raw_segments.append(
                            DialogueSegment(
                                id=seg_counter,
                                start=start_sec,
                                end=end_sec,
                                duration=round(end_sec - start_sec, 3),
                                text=utt_text,
                                clean_text=utt_text,
                                translated_text="",
                                confidence=cls._extract_confidence(u)
                            )
                        )
                        seg_counter += 1

                return raw_segments, utts

            except Exception as e:
                last_error = e
                if attempt < 2:
                    logger.warning(f"[CapCut ASR Worker #{chunk_idx + 1}] Lỗi tạm thời ({e}), thử lại lần {attempt + 1}...")
                    time.sleep(2.0)
                else:
                    logger.error(f"[CapCut ASR Worker #{chunk_idx + 1}] Lỗi sau 2 lần thử: {e}")
                    raise last_error

    @classmethod
    def _merge_overlapping_chunks(
        cls,
        chunk_results: List[List[DialogueSegment]],
        chunks_meta: List[Dict[str, Any]],
        overlap_sec: float = OVERLAP_SEC
    ) -> List[DialogueSegment]:
        """
        Ghép các chunk dựa trên ranh giới điểm giữa (midpoint) của vùng overlap.
        Tránh mất chữ do câu ở rìa cuối chunk trước bị cắt ngang.
        """
        total_chunks = len(chunk_results)
        if total_chunks == 1:
            return chunk_results[0] or []

        merged: List[DialogueSegment] = []
        half_overlap = overlap_sec / 2.0

        for idx, segments in enumerate(chunk_results):
            if not segments:
                continue

            chunk_offset = chunks_meta[idx]["offset"]
            # Ranh giới nhận câu của chunk hiện tại:
            # - Chunk đầu: lấy từ 0.0s
            # - Các chunk sau: lấy từ điểm giữa vùng overlap với chunk trước
            min_start = 0.0 if idx == 0 else (chunk_offset + half_overlap)

            # Ranh giới dừng của chunk hiện tại:
            # - Chunk cuối: lấy đến hết
            # - Các chunk trước: dừng tại điểm giữa vùng overlap với chunk tiếp theo
            if idx < total_chunks - 1:
                next_chunk_offset = chunks_meta[idx + 1]["offset"]
                max_start = next_chunk_offset + half_overlap
            else:
                max_start = float("inf")

            for seg in segments:
                # Sử dụng tâm của câu thoại để quyết định câu thuộc về bên nào của ranh giới
                seg_mid = (seg.start + seg.end) / 2.0
                if seg_mid < min_start:
                    continue
                if seg_mid >= max_start:
                    break

                # Khử trùng lặp tại sát đường ranh giới
                if merged:
                    prev = merged[-1]
                    if seg.text == prev.text and abs(seg.start - prev.start) < 1.5:
                        continue
                    # Nếu câu mới bị lùi nhẹ so với end của câu trước ở ranh giới, cắt chỉnh ranh giới
                    if seg.start < prev.end:
                        if (prev.end - seg.start) < 0.4:
                            prev.end = round(max(prev.start + 0.08, seg.start), 3)
                            prev.duration = round(prev.end - prev.start, 3)
                        else:
                            seg.start = prev.end
                            seg.duration = round(max(0.08, seg.end - seg.start), 3)

                merged.append(seg)

        return merged

    @classmethod
    def _validate_and_sanitize_timeline(
        cls,
        segments: List[DialogueSegment],
        total_duration: float
    ) -> Tuple[List[DialogueSegment], Dict[str, Any]]:
        """Kiểm định chất lượng timeline: kẹp biên, sửa chồng lấn, phát hiện khoảng trống."""
        stats = {
            "clamped_count": 0,
            "overlap_fixed_count": 0,
            "large_gaps": [],
            "total_spoken_sec": 0.0
        }
        if not segments:
            return [], stats

        # Đảm bảo thứ tự thời gian
        segments.sort(key=lambda x: (x.start, x.end))

        valid_segments: List[DialogueSegment] = []
        for seg in segments:
            orig_s, orig_e = seg.start, seg.end
            s = max(0.0, min(orig_s, total_duration))
            e = max(s + 0.05, min(orig_e, total_duration))

            if s != orig_s or e != orig_e:
                stats["clamped_count"] += 1
                seg.start = round(s, 3)
                seg.end = round(e, 3)
                seg.duration = round(seg.end - seg.start, 3)

            if valid_segments:
                prev = valid_segments[-1]
                # Chống chồng lấn giữa câu trước và câu sau
                if prev.end > seg.start:
                    stats["overlap_fixed_count"] += 1
                    prev.end = round(max(prev.start + 0.05, seg.start), 3)
                    prev.duration = round(prev.end - prev.start, 3)

                # Phát hiện khoảng trống > 10s giữa 2 câu thoại
                gap = round(seg.start - prev.end, 2)
                if gap >= 10.0:
                    stats["large_gaps"].append((prev.end, seg.start, gap))

            valid_segments.append(seg)
            stats["total_spoken_sec"] += seg.duration

        stats["total_spoken_sec"] = round(stats["total_spoken_sec"], 2)
        return valid_segments, stats

    @classmethod
    def transcribe(
        cls,
        audio_path: str,
        language: str = "zh-CN",
        clean_text: bool = True,
        use_translation: bool = False,
        timeout: float = 120.0,
        task_id: Optional[str] = None
    ) -> Tuple[List[DialogueSegment], str, str, str]:
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Không tìm thấy file audio: {audio_path}")

        total_duration = AudioExtractorService.get_audio_duration(audio_path)
        logger.info(f"[CapCut ASR] Tổng thời lượng audio: {total_duration:.2f}s ({total_duration/60:.1f} phút)")

        chunks = AudioExtractorService.split_audio_chunks_with_overlap(
            audio_path, chunk_length_sec=CHUNK_LENGTH_SEC, overlap_sec=OVERLAP_SEC
        )
        total_chunks = len(chunks)
        max_workers = getattr(settings, "ASR_MAX_WORKERS", 16)
        workers = min(total_chunks, max(1, max_workers))

        chunk_results: List[Optional[List[DialogueSegment]]] = [None] * total_chunks
        raw_payloads: List[Dict[str, Any]] = [None] * total_chunks

        if total_chunks == 1:
            segs, raw_utts = cls._transcribe_single_chunk(
                audio_path=chunks[0]["path"],
                language=language,
                use_translation=use_translation,
                timeout=timeout,
                time_offset=chunks[0]["offset"],
                chunk_idx=0,
                total_chunks=1
            )
            chunk_results[0] = segs
            raw_payloads[0] = {"chunk_idx": 0, "offset": chunks[0]["offset"], "utterances": raw_utts}
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                future_to_idx = {
                    executor.submit(
                        cls._transcribe_single_chunk,
                        audio_path=chunk["path"],
                        language=language,
                        use_translation=use_translation,
                        timeout=timeout,
                        time_offset=chunk["offset"],
                        chunk_idx=idx,
                        total_chunks=total_chunks
                    ): idx for idx, chunk in enumerate(chunks)
                }

                for future in concurrent.futures.as_completed(future_to_idx):
                    c_idx = future_to_idx[future]
                    res_segs, raw_utts = future.result()
                    chunk_results[c_idx] = res_segs
                    raw_payloads[c_idx] = {"chunk_idx": c_idx, "offset": chunks[c_idx]["offset"], "utterances": raw_utts}

        # Ghép chunk tại tâm vùng overlap
        all_raw_segments = cls._merge_overlapping_chunks(chunk_results, chunks, overlap_sec=OVERLAP_SEC)

        lang_code = "zh-CN" if language in ("zh", "zh-CN") else language
        is_chinese = "zh" in lang_code.lower()
        if clean_text:
            cleaned_segments = TextCleanerService.clean_segments(all_raw_segments, is_chinese=is_chinese)
        else:
            cleaned_segments = all_raw_segments

        # Mặc định STT_TIME_OFFSET_MS = 0 (chỉ trừ khi người dùng cấu hình rõ ràng sau khi đã đo thực nghiệm)
        offset_ms = getattr(settings, "STT_TIME_OFFSET_MS", 0) or 0
        offset_sec = round(float(offset_ms) / 1000.0, 3)
        if offset_sec != 0:
            for seg in cleaned_segments:
                new_start = max(0.0, round(seg.start - offset_sec, 3))
                new_end = max(round(new_start + 0.08, 3), round(seg.end - offset_sec, 3))
                seg.start = new_start
                seg.end = new_end
                seg.duration = round(new_end - new_start, 3)

        # Kiểm định chất lượng timeline
        cleaned_segments, qa_stats = cls._validate_and_sanitize_timeline(cleaned_segments, total_duration)

        for i, seg in enumerate(cleaned_segments, 1):
            seg.id = i

        if cleaned_segments:
            first_spoken = cleaned_segments[0].start
            last_spoken = cleaned_segments[-1].end
            span_pct = min(100.0, round((last_spoken / max(1.0, total_duration)) * 100, 1))
            spoken_ratio = round((qa_stats["total_spoken_sec"] / max(1.0, total_duration)) * 100, 1)
            logger.info(
                f"[CapCut ASR QA] Câu đầu: {first_spoken:.2f}s | Câu cuối: {last_spoken:.2f}s / {total_duration:.2f}s "
                f"(Biên độ: {span_pct}%, Mật độ thoại: {spoken_ratio}%, "
                f"Sửa chồng lấn: {qa_stats['overlap_fixed_count']}, Khoảng lặng >10s: {len(qa_stats['large_gaps'])})"
            )
            if task_id:
                task_manager.add_log(
                    task_id,
                    f"   ✔ [QA Timeline] {len(cleaned_segments)} câu ({first_spoken:.1f}s -> {last_spoken:.1f}s / {total_duration:.1f}s, "
                    f"mật độ thoại {spoken_ratio}%, khoảng lặng dài: {len(qa_stats['large_gaps'])}).",
                    "emerald"
                )
        else:
            logger.warning("[CapCut ASR] ⚠ Cảnh báo: Không phát hiện câu thoại nào trong file audio!")
            if task_id:
                task_manager.add_log(task_id, "   ⚠ Không phát hiện âm thanh lời thoại nào trong video.", "amber")

        # Xuất file (Bao gồm cả file raw_capcut.json để đối chiếu độc lập)
        settings.OUTPUT_TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
        path_obj = Path(audio_path)
        base_name = path_obj.stem.replace("_16k", "").replace("_compressed", "")
        srt_file = settings.OUTPUT_TRANSCRIPTS_DIR / f"{base_name}.srt"
        txt_file = settings.OUTPUT_TRANSCRIPTS_DIR / f"{base_name}_script.txt"
        json_file = settings.OUTPUT_TRANSCRIPTS_DIR / f"{base_name}.json"
        raw_json_file = settings.OUTPUT_TRANSCRIPTS_DIR / f"{base_name}_raw_capcut.json"

        with open(raw_json_file, "w", encoding="utf-8") as f:
            json.dump(raw_payloads, f, ensure_ascii=False, indent=2)

        srt_lines = [
            f"{s.id}\n{format_timestamp(s.start)} --> {format_timestamp(s.end)}\n{s.clean_text or s.text}\n"
            for s in cleaned_segments
        ]
        with open(srt_file, "w", encoding="utf-8") as f:
            f.write("\n".join(srt_lines))

        txt_lines = [
            f"[{format_timestamp(s.start)[:8]} -> {format_timestamp(s.end)[:8]}] {s.clean_text or s.text}"
            for s in cleaned_segments
        ]
        with open(txt_file, "w", encoding="utf-8") as f:
            f.write("\n".join(txt_lines))

        with open(json_file, "w", encoding="utf-8") as f:
            json.dump([s.model_dump() for s in cleaned_segments], f, ensure_ascii=False, indent=2)

        return cleaned_segments, str(srt_file), str(txt_file), str(json_file)
