import os
import re
import json
import logging
import subprocess
from pathlib import Path
from typing import List, Tuple
from groq import Groq
from app.config import settings
from app.schemas.transcript import DialogueSegment
from app.services.text_cleaner import TextCleanerService
from app.utils.bin_helper import get_ffmpeg_cmd

logger = logging.getLogger(__name__)

def format_timestamp(seconds: float) -> str:
    millis = int((seconds - int(seconds)) * 1000)
    mins, secs = divmod(int(seconds), 60)
    hours, mins = divmod(mins, 60)
    return f"{hours:02d}:{mins:02d}:{secs:02d},{millis:03d}"

class GroqWhisperService:

    # Không dùng prompt cứng hướng dẫn nữa vì nó làm Whisper bị rò rỉ prompt và sinh ra câu like/sub YouTube
    WHISPER_PROMPT_ZH = None

    @staticmethod
    def _is_ghost_or_hallucination(text: str, no_speech_prob: float, avg_logprob: float = 0.0) -> bool:
        """
        Phát hiện các đoạn "ma" (ghost segments) do Whisper bắt nhầm từ nhạc nền / tiếng ồn,
        và các đoạn hallucination (Whisper tự bịa chữ lặp lại vô nghĩa hoặc quảng cáo YouTube).
        Trả về True nếu đoạn này nên bị loại bỏ.
        """
        stripped = text.strip()
        if not stripped:
            return True

        # 0. Loại bỏ ngay lập tức các mẫu quảng cáo / like sub YouTube kinh điển do Whisper bịa
        PROMO_PATTERNS = ["点赞", "订阅", "打赏", "转发", "明镜", "点点栏目", "关注频道", "请准确转录"]
        if any(p in stripped for p in PROMO_PATTERNS):
            logger.debug(f"Loại bỏ câu rác quảng cáo / promo YouTube: {stripped}")
            return True

        # 1. Xác suất không phải tiếng người: Phải kết hợp CẢ no_speech_prob > 0.85 VÀ avg_logprob < -1.0
        # (TUYỆT ĐỐI không chỉ dùng no_speech_prob > 0.6 vì khi có BGM nhạc nền, no_speech_prob luôn cao 0.7-0.8 dù nhân vật đang nói!)
        if no_speech_prob > 0.85 and avg_logprob < -1.0:
            logger.debug(f"Loại bỏ ghost segment (no_speech_prob={no_speech_prob:.2f}, avg_logprob={avg_logprob:.2f}): {text[:50]}")
            return True

        # 2. Ký hiệu nhạc / tiếng ồn thuần túy
        if re.fullmatch(r'[♪♫🎵🎶\s.,!?…—\-_]+', stripped):
            return True

        # 3. Hallucination: cùng 1 ký tự / cụm từ lặp lại liên tục (>= 4 lần)
        #    Ví dụ: "谢谢谢谢谢谢", "对对对对对对", "啊啊啊啊啊啊"
        if len(stripped) >= 4:
            # Kiểm tra 1 ký tự lặp
            if len(set(stripped.replace(' ', ''))) == 1:
                return True
            # Kiểm tra cụm 2-4 ký tự lặp >= 4 lần
            for pattern_len in range(2, 5):
                pat = stripped[:pattern_len]
                if len(pat) > 0 and stripped == pat * (len(stripped) // len(pat)) and len(stripped) // len(pat) >= 4:
                    return True

        # 4. Đoạn quá ngắn (1-2 ký tự) VÀ confidence rất thấp — thường là tiếng thở / tiếng gió
        if len(stripped) <= 2 and no_speech_prob > 0.5 and avg_logprob < -1.0:
            logger.debug(f"Loại bỏ đoạn ngắn nghi ngờ (no_speech_prob={no_speech_prob:.2f}): {stripped}")
            return True

        return False

    @staticmethod
    def _extract_word_boundaries(
        words_raw: list,
        seg_start: float,
        seg_end: float,
        time_offset: float = 0.0
    ):
        """
        Từ danh sách word-level timestamps của Groq, trích xuất:
        - true_start: thời điểm THẬT SỰ nhân vật bắt đầu mở miệng (first_word.start)
        - true_end:   thời điểm THẬT SỰ nhân vật ngậm miệng xong (last_word.end)

        Nếu không tìm được word nào trong khoảng [seg_start, seg_end],
        trả về (None, None) để loại bỏ segment ma (không có từ âm thanh thực tế).
        """
        seg_words = []
        for w in words_raw:
            if isinstance(w, dict):
                ws = float(w.get("start", 0.0)) + time_offset
                we = float(w.get("end", 0.0)) + time_offset
            else:
                ws = float(getattr(w, "start", 0.0)) + time_offset
                we = float(getattr(w, "end", 0.0)) + time_offset

            # Word thuộc segment nếu tâm của từ (midpoint) nằm trong [seg_start - 0.1s, seg_end + 0.1s]
            # hoặc có giao thoa thời gian với segment
            mid = (ws + we) / 2.0
            if (seg_start - 0.15 <= mid <= seg_end + 0.15) or (ws >= seg_start - 0.1 and we <= seg_end + 0.1):
                seg_words.append((ws, we))

        if not seg_words:
            return None, None

        true_start = round(seg_words[0][0], 3)
        true_end   = round(seg_words[-1][1], 3)

        # Ràng buộc vật lý âm học:
        # Nếu segment dài (> 2.0s) nhưng tỷ lệ thời gian phát âm (coverage) quá thấp (< 25%)
        # hoặc chỉ có 1 từ lẻ loi trong segment dài -> Đây là ảo giác kéo dài qua đoạn nhạc/im lặng
        seg_dur = seg_end - seg_start
        word_dur = true_end - true_start
        if seg_dur > 2.0:
            coverage = word_dur / seg_dur
            if coverage < 0.25 or (len(seg_words) <= 1 and seg_dur > 3.0):
                return None, None

        return true_start, true_end

    @staticmethod
    def _prepare_audio_for_groq(audio_path: str, duration_sec: float = 0.0) -> Tuple[str, bool]:
        """
        Tự động nén thích ứng (Dynamic Bitrate Compression) sao cho toàn bộ audio luôn < 24MB (giới hạn Groq là 25MB).
        - Video <= 45 phút: 32kbps mono 16kHz (~10MB)
        - Video 45 - 90 phút: 24kbps mono 16kHz (~16MB)
        - Video 90 - 150 phút (2.5 tiếng): 16kbps mono 16kHz (~18MB)
        - Video 150 - 240 phút (4 tiếng): 12kbps mono 16kHz (~21MB)
        Trả về (đường_dẫn_file, là_file_tạm_cần_dọn_dẹp)
        """
        src = Path(audio_path)
        file_size_mb = src.stat().st_size / (1024 * 1024)
        
        # Nếu file vốn đã là MP3/M4A và < 24MB thì dùng luôn trực tiếp
        if src.suffix.lower() in ('.mp3', '.m4a', '.aac') and file_size_mb <= 24.0:
            return str(src), False

        # Lấy thời lượng nếu chưa có
        if duration_sec <= 0:
            try:
                from pydub import AudioSegment
                seg = AudioSegment.from_file(str(src))
                duration_sec = len(seg) / 1000.0
            except Exception:
                duration_sec = 1800.0

        # Lựa chọn bitrate thông minh tối ưu cho Whisper
        if duration_sec <= 2700:       # <= 45 phút
            bitrate = "32k"
        elif duration_sec <= 5400:     # <= 90 phút
            bitrate = "24k"
        elif duration_sec <= 9000:     # <= 150 phút (2.5 tiếng)
            bitrate = "16k"
        else:                          # > 2.5 tiếng
            bitrate = "12k"

        compressed_mp3 = src.parent / f"{src.stem}_dyn_{bitrate}.mp3"
        ffmpeg_cmd = get_ffmpeg_cmd()
        cmd = [
            *ffmpeg_cmd, "-y", "-i", str(src),
            "-vn", "-ac", "1", "-ar", "16000",
            "-b:a", bitrate, str(compressed_mp3)
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if res.returncode == 0 and compressed_mp3.exists():
            return str(compressed_mp3), True
            
        return str(src), False

    @staticmethod
    def _sanitize_timestamps(segments: List[DialogueSegment], min_gap: float = 0.02) -> List[DialogueSegment]:
        """
        Chuẩn hóa timecode chính xác từ Whisper:
        - Giữ nguyên mốc thời gian tự nhiên của giọng nói mà Whisper đã căn khớp với video.
        - Khử overlap giữa các câu kề nhau một cách êm ái mà không làm biến dạng thời lượng câu.
        - Đảm bảo thời lượng tối thiểu 0.3s cho mỗi câu để hiển thị phụ đề và lồng tiếng chuẩn.
        """
        for i in range(len(segments)):
            s = segments[i]
            if s.end <= s.start:
                s.end = round(s.start + 0.5, 3)
            s.duration = round(s.end - s.start, 3)

            if i > 0:
                prev = segments[i - 1]
                if s.start < prev.end:
                    mid = round((prev.end + s.start) / 2.0, 3)
                    prev.end = max(prev.start + 0.3, round(mid - min_gap / 2.0, 3))
                    prev.duration = round(prev.end - prev.start, 3)
                    
                    s.start = min(s.end - 0.3, round(mid + min_gap / 2.0, 3))
                    s.duration = round(s.end - s.start, 3)

        return segments

    @staticmethod
    def _stitch_and_merge_segments(
        accumulated: List[DialogueSegment],
        incoming: List[DialogueSegment],
        overlap_window_start: float,
        overlap_window_end: float
    ) -> List[DialogueSegment]:
        """
        Ghép nối 2 chunk có vùng giao thoa gối đầu 1 PHÚT (60 giây Overlap):
        - Phát hiện và khôi phục câu thoại bị xé ngang ở ranh giới giao nhau.
        - Khử trùng lặp (deduplicate) các câu đã xuất hiện trọn vẹn ở chunk trước.
        - Nối liền mạch kịch bản thành chuỗi duy nhất 100% không mất chữ nào.
        """
        if not accumulated:
            return incoming
        if not incoming:
            return accumulated

        # 1. Kiểm tra câu cuối cùng của accumulated xem có bị xé dở không
        last_seg = accumulated[-1]
        is_last_cut = (last_seg.end >= overlap_window_end - 2.5)

        matched_incoming_idx = -1
        last_text_clean = last_seg.text.strip().lower()

        # 2. Tìm câu tương ứng trong incoming (ở vùng overlap)
        for idx, inc in enumerate(incoming):
            if inc.start > overlap_window_end + 5.0:
                break
            inc_text_clean = inc.text.strip().lower()
            
            # Nếu câu cuối bị cắt dở và câu incoming bao trùm nội dung hoặc tiếp nối
            if is_last_cut and (
                last_text_clean in inc_text_clean or 
                (len(last_text_clean) >= 3 and inc_text_clean.startswith(last_text_clean[:max(3, len(last_text_clean)//2)])) or
                abs(last_seg.start - inc.start) <= 1.5
            ):
                # Thay thế câu bị xé dở bằng câu trọn vẹn hoàn chỉnh từ incoming
                accumulated[-1] = inc
                matched_incoming_idx = idx
                break
            elif abs(last_seg.start - inc.start) <= 1.5 or (last_text_clean == inc_text_clean):
                matched_incoming_idx = idx

        # 3. Lấy mốc thời gian an toàn nhất để nối các câu tiếp theo của incoming
        safe_boundary = accumulated[-1].end if accumulated else overlap_window_end
        
        start_add_idx = (matched_incoming_idx + 1) if matched_incoming_idx >= 0 else 0
        for i in range(start_add_idx, len(incoming)):
            inc = incoming[i]
            # Bỏ qua nếu câu này đã nằm trọn trong quá khứ của accumulated
            if inc.end <= safe_boundary:
                continue
            # Nếu câu chớm chạm vào boundary, chỉnh start nhẹ
            if inc.start < safe_boundary:
                if inc.end - safe_boundary >= 0.3:
                    inc.start = round(safe_boundary + 0.02, 3)
                    inc.duration = round(inc.end - inc.start, 3)
                else:
                    continue
            accumulated.append(inc)
            safe_boundary = inc.end

        return accumulated

    @classmethod
    def transcribe(
        cls,
        audio_path: str,
        language: str = "zh",
        clean_text: bool = True,
        api_key: str = None,
        model_name: str = None
    ) -> Tuple[List[DialogueSegment], str, str, str]:
        """
        Bóc tách giọng nói thành văn bản siêu tốc bằng Groq Whisper API (whisper-large-v3).
        - Video <= 2.5 - 3 tiếng: Dynamic Bitrate nén vừa vặn < 24MB, gửi 1 file trọn vẹn, không cắt câu.
        - Video > 3 tiếng: Cắt gối đầu 1 phút (60s overlap) + Thuật toán ghép nối khôi phục câu xé ngang.
        """
        key = api_key or settings.GROQ_API_KEY
        if not key or not key.strip():
            raise ValueError("Chưa cấu hình Groq API Key! Vui lòng vào Cài Đặt (Settings) để nhập API Key.")

        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Không tìm thấy file audio: {audio_path}")

        from pydub import AudioSegment

        model = model_name or settings.GROQ_MODEL or "whisper-large-v3"
        client = Groq(api_key=key.strip())
        lang_param = None if (not language or language == "auto") else language

        # Tính thời lượng audio
        seg_audio = AudioSegment.from_file(audio_path)
        total_sec = len(seg_audio) / 1000.0

        # 1. Nén thích ứng theo thời lượng (Dynamic Bitrate Compression)
        upload_file, is_compressed_tmp = cls._prepare_audio_for_groq(audio_path, duration_sec=total_sec)
        file_size_mb = Path(upload_file).stat().st_size / (1024 * 1024)

        raw_list: List[DialogueSegment] = []
        is_chinese_detected = (language == "zh")

        # Prompt mồi cho Whisper theo ngôn ngữ
        whisper_prompt = cls.WHISPER_PROMPT_ZH if (language == "zh" or lang_param == "zh") else None

        # 2. NẾU FILE <= 24MB: GỬI TRỰC TIẾP 1 FILE NGUYÊN BẢN (KHÔNG CẮT CHUNK)
        if file_size_mb <= 24.0:
            api_params = {
                "model": model,
                "response_format": "verbose_json",
                "language": lang_param,
                "temperature": 0.0,
                "timestamp_granularities": ["word", "segment"]
            }
            if whisper_prompt:
                api_params["prompt"] = whisper_prompt

            with open(upload_file, "rb") as f:
                transcription = client.audio.transcriptions.create(
                    file=(Path(upload_file).name, f.read()),
                    **api_params
                )

            if getattr(transcription, "language", "") == "chinese":
                is_chinese_detected = True

            segments = getattr(transcription, "segments", []) or []
            words_raw = getattr(transcription, "words", []) or []
            ghost_count = 0
            word_fixed = 0
            idx = 1
            for seg in segments:
                if isinstance(seg, dict):
                    text = seg.get("text", "").strip()
                    s_start = float(seg.get("start", 0.0))
                    s_end = float(seg.get("end", 0.0))
                    avg_logprob = float(seg.get("avg_logprob", 0.0))
                    no_speech_prob = float(seg.get("no_speech_prob", 0.0))
                else:
                    text = getattr(seg, "text", "").strip()
                    s_start = float(getattr(seg, "start", 0.0))
                    s_end = float(getattr(seg, "end", 0.0))
                    avg_logprob = float(getattr(seg, "avg_logprob", 0.0))
                    no_speech_prob = float(getattr(seg, "no_speech_prob", 0.0))

                if not text:
                    continue

                # Lọc bỏ ghost segments (nhạc nền bắt nhầm) và hallucination (chữ lặp vô nghĩa)
                if cls._is_ghost_or_hallucination(text, no_speech_prob, avg_logprob):
                    ghost_count += 1
                    continue

                # Dùng word-level timestamps để lấy TRUE start/end (loại bỏ im lặng đầu/đuôi)
                true_start, true_end = cls._extract_word_boundaries(
                    words_raw, s_start, s_end, time_offset=0.0
                )
                if true_start is not None and true_end is not None:
                    s_start = true_start
                    s_end = true_end
                    word_fixed += 1
                else:
                    # Nếu segment không có bất kỳ từ nào được căn chỉnh âm thanh -> Câu ma từ nhạc nền/im lặng
                    logger.debug(f"Loại bỏ câu ma không có từ âm thanh: {text}")
                    ghost_count += 1
                    continue

                raw_list.append(
                    DialogueSegment(
                        id=idx,
                        start=round(s_start, 3),
                        end=round(s_end, 3),
                        duration=round(s_end - s_start, 3),
                        text=text,
                        clean_text=text,
                        confidence=round(avg_logprob, 3)
                    )
                )
                idx += 1

            if ghost_count > 0:
                logger.info(f"Đã lọc bỏ {ghost_count} đoạn ghost/hallucination từ nhạc nền")
            if word_fixed > 0:
                logger.info(f"Đã căn chỉnh word-level timestamps cho {word_fixed}/{len(segments)} segment")
        else:
            # 3. VIDEO SIÊU DÀI (> 3 TIẾNG): CẮT GỐI ĐẦU 1 PHÚT (60 GIÂY OVERLAP)
            # Khắc phục triệt để việc câu thoại bị xẻ đôi ở ranh giới
            chunk_duration = 3600.0   # Mỗi chunk 60 phút
            overlap_sec = 60.0        # Gối đầu trọn vẹn 1 phút

            temp_dir = Path(audio_path).parent / "tmp_groq_chunks"
            temp_dir.mkdir(parents=True, exist_ok=True)

            cur_start = 0.0
            chunk_idx = 0
            accumulated_segments: List[DialogueSegment] = []

            while cur_start < total_sec:
                cur_end = min(total_sec, cur_start + chunk_duration)
                chunk_file = temp_dir / f"chunk_{chunk_idx}.mp3"

                ffmpeg_cmd = get_ffmpeg_cmd()
                cmd = [
                    *ffmpeg_cmd, "-y",
                    "-ss", str(cur_start), "-to", str(cur_end),
                    "-i", str(upload_file),
                    "-vn", "-ac", "1", "-ar", "16000",
                    "-b:a", "32k", str(chunk_file)
                ]
                subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")

                if chunk_file.exists():
                    chunk_api_params = {
                        "model": model,
                        "response_format": "verbose_json",
                        "language": lang_param,
                        "temperature": 0.0,
                        "timestamp_granularities": ["word", "segment"]
                    }
                    if whisper_prompt:
                        chunk_api_params["prompt"] = whisper_prompt

                    with open(str(chunk_file), "rb") as f:
                        transcription = client.audio.transcriptions.create(
                            file=(chunk_file.name, f.read()),
                            **chunk_api_params
                        )

                    segments = getattr(transcription, "segments", []) or []
                    words_raw_chunk = getattr(transcription, "words", []) or []
                    incoming_chunk_segments: List[DialogueSegment] = []
                    for seg in segments:
                        if isinstance(seg, dict):
                            text = seg.get("text", "").strip()
                            s_start = float(seg.get("start", 0.0)) + cur_start
                            s_end = float(seg.get("end", 0.0)) + cur_start
                            avg_logprob = float(seg.get("avg_logprob", 0.0))
                            no_speech_prob = float(seg.get("no_speech_prob", 0.0))
                        else:
                            text = getattr(seg, "text", "").strip()
                            s_start = float(getattr(seg, "start", 0.0)) + cur_start
                            s_end = float(getattr(seg, "end", 0.0)) + cur_start
                            avg_logprob = float(getattr(seg, "avg_logprob", 0.0))
                            no_speech_prob = float(getattr(seg, "no_speech_prob", 0.0))

                        if not text:
                            continue

                        # Lọc bỏ ghost segments và hallucination cho từng chunk
                        if cls._is_ghost_or_hallucination(text, no_speech_prob, avg_logprob):
                            continue

                        # Dùng word-level timestamps cho chunk (offset = cur_start)
                        true_start, true_end = cls._extract_word_boundaries(
                            words_raw_chunk,
                            s_start - cur_start,  # so sanh voi raw word times (chua + cur_start)
                            s_end - cur_start,
                            time_offset=cur_start
                        )
                        if true_start is not None and true_end is not None:
                            s_start = true_start
                            s_end = true_end
                        else:
                            # Bỏ qua câu ma không có từ âm thanh
                            continue

                        incoming_chunk_segments.append(
                            DialogueSegment(
                                id=0,
                                start=round(s_start, 3),
                                end=round(s_end, 3),
                                duration=round(s_end - s_start, 3),
                                text=text,
                                clean_text=text,
                                confidence=round(avg_logprob, 3)
                            )
                        )

                    # Nối gối đầu: Khử câu trùng và vá câu bị xé ngang
                    if chunk_idx == 0:
                        accumulated_segments = incoming_chunk_segments
                    else:
                        accumulated_segments = cls._stitch_and_merge_segments(
                            accumulated=accumulated_segments,
                            incoming=incoming_chunk_segments,
                            overlap_window_start=cur_start,
                            overlap_window_end=cur_start + overlap_sec
                        )

                    try:
                        os.remove(chunk_file)
                    except Exception:
                        pass

                # Chunk tiếp theo lùi lại 60 giây (gối đầu)
                if cur_end >= total_sec:
                    break
                cur_start = max(0.0, cur_end - overlap_sec)
                chunk_idx += 1

            if temp_dir.exists():
                try:
                    temp_dir.rmdir()
                except Exception:
                    pass

            # Đánh lại ID tuần tự từ 1..N
            for i, s in enumerate(accumulated_segments, 1):
                s.id = i
            raw_list = accumulated_segments

        # Dọn dẹp file nén tạm nếu có
        if is_compressed_tmp and os.path.exists(upload_file):
            try:
                os.remove(upload_file)
            except Exception:
                pass

        is_chinese = is_chinese_detected

        if clean_text:
            cleaned_segments = TextCleanerService.clean_segments(raw_list, is_chinese=is_chinese)
        else:
            cleaned_segments = raw_list

        # Chuẩn hóa khử overlap êm ái, giữ nguyên 100% timecode tự nhiên của Whisper (khớp đúng miệng nhân vật)
        final_segments = cls._sanitize_timestamps(cleaned_segments)

        # Lưu file output/transcripts/
        base_name = Path(audio_path).stem.replace("_16k", "").replace("_compressed", "")
        srt_file = settings.OUTPUT_TRANSCRIPTS_DIR / f"{base_name}.srt"
        txt_file = settings.OUTPUT_TRANSCRIPTS_DIR / f"{base_name}_script.txt"
        json_file = settings.OUTPUT_TRANSCRIPTS_DIR / f"{base_name}.json"

        # 1. File SRT
        srt_lines = []
        for s in final_segments:
            start_ts = format_timestamp(s.start)
            end_ts = format_timestamp(s.end)
            text_to_show = s.clean_text if s.clean_text else s.text
            srt_lines.append(f"{s.id}\n{start_ts} --> {end_ts}\n{text_to_show}\n")
        with open(srt_file, "w", encoding="utf-8") as f:
            f.write("\n".join(srt_lines))

        # 2. File TXT Kịch bản có timecode
        txt_lines = []
        for s in final_segments:
            text_to_show = s.clean_text if s.clean_text else s.text
            txt_lines.append(f"[{format_timestamp(s.start)[:8]} -> {format_timestamp(s.end)[:8]}] {text_to_show}")
        with open(txt_file, "w", encoding="utf-8") as f:
            f.write("\n".join(txt_lines))

        # 3. File JSON
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump([s.model_dump() for s in final_segments], f, ensure_ascii=False, indent=2)

        return final_segments, str(srt_file), str(txt_file), str(json_file)
