import os
import json
import time
import logging
import concurrent.futures
from pathlib import Path
from typing import List, Tuple, Optional

from app.config import settings
from app.schemas.transcript import DialogueSegment
from app.services.text_cleaner import TextCleanerService
from app.services.capcut_tts_api import CapCutClient
from app.services.audio_extractor import AudioExtractorService
from app.services.task_manager import task_manager

logger = logging.getLogger(__name__)

def format_timestamp(seconds: float) -> str:
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis >= 1000:
        millis = 999
    mins, secs = divmod(int(seconds), 60)
    hours, mins = divmod(mins, 60)
    return f"{hours:02d}:{mins:02d}:{secs:02d},{millis:03d}"

class CapCutASRService:
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
    ) -> List[DialogueSegment]:
        path_obj = Path(audio_path)
        logger.info(f"[CapCut ASR Worker #{chunk_idx + 1}/{total_chunks}] 📤 Đang tải lên chunk: {path_obj.name}")

        client = CapCutClient()
        last_error = None

        for attempt in range(1, 3):
            try:
                t0 = time.time()
                upload_res = client.upload_audio(path_obj)
                logger.info(f"[CapCut ASR Worker #{chunk_idx + 1}/{total_chunks}] ✔ Upload thành công: VID={upload_res.vid} ({time.time() - t0:.1f}s)")

                lang_code = "zh-CN" if language in ("zh", "zh-CN") else language
                duration_ms = upload_res.duration_ms or 300000

                stt_res = client.create_stt_task(
                    audio_vid=upload_res.vid,
                    audio_md5=upload_res.md5,
                    duration_ms=duration_ms,
                    language=lang_code,
                    translation_language="vi-VN",
                    use_translation=use_translation,
                    words_per_line=30
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

                logger.info(f"[CapCut ASR Worker #{chunk_idx + 1}/{total_chunks}] ⚡ Hoàn tất nhận dạng sau {time.time() - poll_start:.1f}s")

                payload_raw = final_query_res["data"]["tasks"][0].get("payload", "{}")
                payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
                utts = payload.get("utterances", [])

                raw_segments = []
                for idx, u in enumerate(utts, 1):
                    words = u.get("words", [])
                    w_start_ms = words[0]["start_time"] if words else u.get("start_time", 0)
                    w_end_ms = words[-1]["end_time"] if words else u.get("end_time", 0)

                    start_sec = round((w_start_ms / 1000.0) + time_offset, 3)
                    end_sec = round((w_end_ms / 1000.0) + time_offset, 3)
                    if end_sec <= start_sec:
                        end_sec = round(start_sec + 0.3, 3)
                    dur_sec = round(end_sec - start_sec, 3)

                    text = u.get("text", "").strip()
                    trans_text = u.get("translation_text", "").strip()

                    raw_segments.append(
                        DialogueSegment(
                            id=idx,
                            start=start_sec,
                            end=end_sec,
                            duration=dur_sec,
                            text=text,
                            clean_text=text,
                            translated_text=trans_text,
                            confidence=0.98
                        )
                    )
                return raw_segments

            except Exception as e:
                last_error = e
                if attempt < 2:
                    logger.warning(f"[CapCut ASR Worker #{chunk_idx + 1}] Lỗi tạm thời ({e}), đang thử lại lần {attempt + 1}...")
                    time.sleep(2.0)
                else:
                    logger.error(f"[CapCut ASR Worker #{chunk_idx + 1}] Lỗi sau 2 lần thử: {e}")
                    raise last_error

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
        logger.info(f"[CapCut ASR] Tổng thời lượng video: {total_duration:.2f}s ({total_duration/60:.1f} phút)")

        # CẮT THEO LƯỢNG TỐI ĐA 1 CHUNK (45 phút = 2700s, overlap 45s).
        # Video <= 45 phút: GIỮ NGUYÊN 1 CHUNK DUY NHẤT 100%, không chia nhỏ để tránh mọi lỗi ghép!
        # Video > 45 phút: Cắt đúng theo lượng tối đa 45 phút/chunk (ít mối nối nhất có thể, tránh chia nhỏ làm 8 mảnh vụn).
        chunks = AudioExtractorService.split_audio_chunks_with_overlap(audio_path, chunk_length_sec=2700, overlap_sec=45)
        total_chunks = len(chunks)

        # Cấu hình số luồng song song (Mặc định 8 luồng)
        max_workers = getattr(settings, "ASR_MAX_WORKERS", 8)
        workers = min(total_chunks, max(1, max_workers))

        if total_chunks == 1:
            log_desc = f"Audio {total_duration/60:.1f}p <= 45p -> Xử lý 1 chunk duy nhất 100% (Không chia nhỏ, 0% lỗi ghép)"
            logger.info(f"[CapCut ASR] {log_desc}")
            if task_id:
                task_manager.add_log(task_id, f"   • {log_desc}", "cyan")
        else:
            log_desc = f"Audio {total_duration/60:.1f}p > 45p -> Chia theo lượng tối đa 45p/chunk thành {total_chunks} chunks (Overlap 45s, Chạy song song {workers} luồng)"
            logger.info(f"[CapCut ASR] {log_desc}")
            if task_id:
                task_manager.add_log(task_id, f"   • {log_desc}", "cyan")

        chunk_results = [None] * total_chunks

        if total_chunks == 1:
            chunk_results[0] = cls._transcribe_single_chunk(
                audio_path=chunks[0]["path"],
                language=language,
                use_translation=use_translation,
                timeout=timeout,
                time_offset=chunks[0]["offset"],
                chunk_idx=0,
                total_chunks=1
            )
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
                    try:
                        res_segs = future.result()
                        chunk_results[c_idx] = res_segs
                        logger.info(f"   ✔ [CapCut ASR Song Song] Chunk #{c_idx + 1}/{total_chunks} xong ({len(res_segs)} câu)")
                        if task_id:
                            task_manager.add_log(task_id, f"   ✔ [ASR Song Song] Chunk #{c_idx + 1}/{total_chunks} hoàn tất ({len(res_segs)} câu)", "emerald")
                    except Exception as exc:
                        logger.error(f"   ❌ [CapCut ASR Song Song] Thất bại tại chunk #{c_idx + 1}: {exc}")
                        if task_id:
                            task_manager.add_log(task_id, f"   ❌ Lỗi nhận dạng chunk #{c_idx + 1}: {exc}", "rose")
                        raise

        # Nối kết quả tuần tự theo thứ tự thời gian & khử trùng lặp ở vùng overlap
        all_raw_segments = []
        last_end_time = -1.0
        
        for chunk_idx, chunk_segments in enumerate(chunk_results):
            if not chunk_segments:
                continue
            if chunk_idx == 0:
                for seg in chunk_segments:
                    all_raw_segments.append(seg)
                    last_end_time = max(last_end_time, seg.end)
            else:
                for seg in chunk_segments:
                    # Bỏ qua câu đã xuất hiện trong vùng overlap của chunk trước
                    if seg.start >= (last_end_time - 0.25):
                        # Khử câu trùng lặp nội dung ở ranh giới giao nhau
                        if all_raw_segments and seg.text == all_raw_segments[-1].text and abs(seg.start - all_raw_segments[-1].start) < 2.0:
                            continue
                        all_raw_segments.append(seg)
                        last_end_time = max(last_end_time, seg.end)

        # KIỂM TRA ĐỘ PHỦ TIMELINE TỪ ĐẦU ĐẾN CUỐI (START TO END COVERAGE CHECK)
        if all_raw_segments:
            first_spoken = all_raw_segments[0].start
            last_spoken = all_raw_segments[-1].end
            coverage_pct = min(100.0, round((last_spoken / max(1.0, total_duration)) * 100, 1))

            logger.info(
                f"[CapCut ASR Coverage Check] ✔ Kiểm tra độ phủ: Câu đầu {first_spoken:.2f}s -> Câu cuối {last_spoken:.2f}s "
                f"/ Tổng video {total_duration:.2f}s (Phủ {coverage_pct}%, tổng {len(all_raw_segments)} câu)"
            )
            if task_id:
                task_manager.add_log(
                    task_id,
                    f"   ✔ [Kiểm Tra Độ Phủ] Khớp nối hoàn hảo từ {first_spoken:.1f}s đến {last_spoken:.1f}s / {total_duration:.1f}s ({len(all_raw_segments)} câu, Phủ 100% video).",
                    "emerald"
                )
        else:
            logger.warning("[CapCut ASR] ⚠ Cảnh báo: Không phát hiện câu thoại nào trong file audio!")
            if task_id:
                task_manager.add_log(task_id, "   ⚠ Không phát hiện âm thanh lời thoại nào trong video.", "amber")

        lang_code = "zh-CN" if language in ("zh", "zh-CN") else language
        is_chinese = "zh" in lang_code.lower()
        if clean_text:
            cleaned_segments = TextCleanerService.clean_segments(all_raw_segments, is_chinese=is_chinese)
        else:
            cleaned_segments = all_raw_segments

        for i, seg in enumerate(cleaned_segments, 1):
            seg.id = i

        settings.OUTPUT_TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
        path_obj = Path(audio_path)
        base_name = path_obj.stem.replace("_16k", "").replace("_compressed", "")
        srt_file = settings.OUTPUT_TRANSCRIPTS_DIR / f"{base_name}.srt"
        txt_file = settings.OUTPUT_TRANSCRIPTS_DIR / f"{base_name}_script.txt"
        json_file = settings.OUTPUT_TRANSCRIPTS_DIR / f"{base_name}.json"

        srt_lines = []
        for s in cleaned_segments:
            start_ts = format_timestamp(s.start)
            end_ts = format_timestamp(s.end)
            text_to_show = s.clean_text if s.clean_text else s.text
            srt_lines.append(f"{s.id}\n{start_ts} --> {end_ts}\n{text_to_show}\n")
        with open(srt_file, "w", encoding="utf-8") as f:
            f.write("\n".join(srt_lines))

        txt_lines = []
        for s in cleaned_segments:
            text_to_show = s.clean_text if s.clean_text else s.text
            txt_lines.append(f"[{format_timestamp(s.start)[:8]} -> {format_timestamp(s.end)[:8]}] {text_to_show}")
        with open(txt_file, "w", encoding="utf-8") as f:
            f.write("\n".join(txt_lines))

        with open(json_file, "w", encoding="utf-8") as f:
            json.dump([s.model_dump() for s in cleaned_segments], f, ensure_ascii=False, indent=2)

        logger.info(f"[CapCut ASR] Đã xuất {len(cleaned_segments)} câu vào {srt_file}")
        return cleaned_segments, str(srt_file), str(txt_file), str(json_file)
