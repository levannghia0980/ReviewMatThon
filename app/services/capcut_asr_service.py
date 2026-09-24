import os
import json
import time
import logging
from pathlib import Path
from typing import List, Tuple, Optional

from app.config import settings
from app.schemas.transcript import DialogueSegment
from app.services.text_cleaner import TextCleanerService
from app.services.capcut_tts_api import CapCutClient

logger = logging.getLogger(__name__)

def format_timestamp(seconds: float) -> str:
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis >= 1000:
        millis = 999
    mins, secs = divmod(int(seconds), 60)
    hours, mins = divmod(mins, 60)
    return f"{hours:02d}:{mins:02d}:{secs:02d},{millis:03d}"

class CapCutASRService:
    """
    Dịch vụ Speech-to-Text (STT) siêu tốc từ CapCut Cloud API:
    - Bóc tách toàn bộ audio/video chỉ trong 5 - 10 giây (Cloud ByteDance).
    - Chuẩn xác từng mili-giây mở/đóng miệng (Word-level timestamps).
    - Thay thế hoàn toàn Whisper offline chạy CPU chậm chạp.
    - Hỗ trợ dịch thô song ngữ Trung - Việt trực tiếp nếu cần.
    """

    @classmethod
    def transcribe(
        cls,
        audio_path: str,
        language: str = "zh-CN",
        clean_text: bool = True,
        use_translation: bool = False,
        timeout: float = 120.0
    ) -> Tuple[List[DialogueSegment], str, str, str]:
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Không tìm thấy file audio: {audio_path}")

        path_obj = Path(audio_path)
        logger.info(f"[CapCut ASR] Bắt đầu upload và bóc tách STT cho: {path_obj.name}")

        client = CapCutClient()

        # 1. Upload audio lên VOD Cloud của CapCut
        t0 = time.time()
        upload_res = client.upload_audio(path_obj)
        logger.info(f"[CapCut ASR] Upload VOD thành công: VID={upload_res.vid} ({time.time() - t0:.1f}s)")

        # 2. Tạo task STT
        lang_code = "zh-CN" if language in ("zh", "zh-CN") else language
        duration_ms = upload_res.duration_ms or 300000

        stt_res = client.create_stt_task(
            audio_vid=upload_res.vid,
            audio_md5=upload_res.md5,
            duration_ms=duration_ms,
            language=lang_code,
            translation_language="vi-VN",
            use_translation=use_translation
        )

        tasks = (stt_res.get("data") or {}).get("tasks") or []
        if not tasks:
            raise RuntimeError(f"CapCut STT không tạo được task: {stt_res}")

        task_id = tasks[0]["id"]
        token = tasks[0]["token"]

        # 3. Chờ xử lý (Polling)
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

        logger.info(f"[CapCut ASR] Nhận diện hoàn tất sau {time.time() - poll_start:.1f}s")

        # 4. Trích xuất utterances & word timestamps
        payload_raw = final_query_res["data"]["tasks"][0].get("payload", "{}")
        payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
        utts = payload.get("utterances", [])

        raw_segments: List[DialogueSegment] = []
        for idx, u in enumerate(utts, 1):
            words = u.get("words", [])
            # Lấy chuẩn xác mili-giây phát âm đầu và cuối của từ (Khẩu hình miệng)
            w_start_ms = words[0]["start_time"] if words else u.get("start_time", 0)
            w_end_ms = words[-1]["end_time"] if words else u.get("end_time", 0)

            start_sec = round(w_start_ms / 1000.0, 3)
            end_sec = round(w_end_ms / 1000.0, 3)
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

        # 5. Làm sạch text qua TextCleanerService nếu được bật
        is_chinese = "zh" in lang_code.lower()
        if clean_text:
            cleaned_segments = TextCleanerService.clean_segments(raw_segments, is_chinese=is_chinese)
        else:
            cleaned_segments = raw_segments

        # Đánh lại ID
        for i, seg in enumerate(cleaned_segments, 1):
            seg.id = i

        # 6. Lưu kết quả ra thư mục output/transcripts/
        settings.OUTPUT_TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
        base_name = path_obj.stem.replace("_16k", "").replace("_compressed", "")
        srt_file = settings.OUTPUT_TRANSCRIPTS_DIR / f"{base_name}.srt"
        txt_file = settings.OUTPUT_TRANSCRIPTS_DIR / f"{base_name}_script.txt"
        json_file = settings.OUTPUT_TRANSCRIPTS_DIR / f"{base_name}.json"

        # Xuất SRT
        srt_lines = []
        for s in cleaned_segments:
            start_ts = format_timestamp(s.start)
            end_ts = format_timestamp(s.end)
            text_to_show = s.clean_text if s.clean_text else s.text
            srt_lines.append(f"{s.id}\n{start_ts} --> {end_ts}\n{text_to_show}\n")
        with open(srt_file, "w", encoding="utf-8") as f:
            f.write("\n".join(srt_lines))

        # Xuất TXT Script
        txt_lines = []
        for s in cleaned_segments:
            text_to_show = s.clean_text if s.clean_text else s.text
            txt_lines.append(f"[{format_timestamp(s.start)[:8]} -> {format_timestamp(s.end)[:8]}] {text_to_show}")
        with open(txt_file, "w", encoding="utf-8") as f:
            f.write("\n".join(txt_lines))

        # Xuất JSON
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump([s.model_dump() for s in cleaned_segments], f, ensure_ascii=False, indent=2)

        logger.info(f"[CapCut ASR] Đã xuất {len(cleaned_segments)} câu vào {srt_file}")
        return cleaned_segments, str(srt_file), str(txt_file), str(json_file)
