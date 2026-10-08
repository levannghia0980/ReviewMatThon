import os
import io
import re
import time
import math
import base64
import random
import logging
import asyncio
import requests
import subprocess
import numpy as np
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from sqlalchemy.orm import Session
from concurrent.futures import ThreadPoolExecutor, as_completed
from pydub import AudioSegment

from app.config import settings
from app.models import ProjectTask, DialogueSegmentModel
from app.services.task_manager import task_manager
from app.utils.bin_helper import get_ffmpeg_cmd, get_ffprobe_cmd
from app.services.translation.chinese_guard import ensure_project_dialogues_vietnamese
from app.services.tts.proxy_manager import proxy_manager

logger = logging.getLogger(__name__)

DEFAULT_TIKTOK_SESSION_ID = getattr(settings, "TIKTOK_SESSION_ID", "410bfa37bdc185e1c6da82e1afb48409")
TIKTOK_API_ENDPOINT = "https://api16-normal-v4.tiktokv.com/media/api/text/speech/invoke/"

import tempfile

def _load_audio_from_bytes(data: bytes, format: str = "mp3") -> AudioSegment:
    """
    Giải mã âm thanh từ buffer sang AudioSegment siêu tốc & an toàn 100% trên Windows:
    - Sử dụng trực tiếp ffmpeg pipe decode sang WAV thô (tránh lỗi pydub ffprobe JSONDecodeError và seekback pipe:0).
    - Tương thích 100% mọi phiên bản Windows và FFmpeg.
    """
    if not data or len(data) < 100:
        return AudioSegment.silent(duration=200)
    try:
        ffmpeg_bin = get_ffmpeg_cmd()[0]
        cmd = [ffmpeg_bin, "-y", "-i", "pipe:0", "-f", "wav", "pipe:1"]
        res = subprocess.run(cmd, input=data, capture_output=True)
        if res.returncode == 0 and len(res.stdout) > 44:
            return AudioSegment.from_wav(io.BytesIO(res.stdout))
    except Exception:
        pass

    temp_dir = settings.TEMP_TTS_DIR
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        fd, temp_path = tempfile.mkstemp(suffix=f".{format}", dir=str(temp_dir))
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
        return AudioSegment.from_file(temp_path)
    except Exception:
        return AudioSegment.silent(duration=300)
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


# ============================================================
# BẢNG TỪ ĐIỂN PHÁT ÂM CHO THUẬT NGỮ TU TIÊN / XIANXIA
# ============================================================
XIANXIA_PHONETIC_DICT = {
    "chân nhân": "chân nhân",
    "đạo hữu": "đạo hữu",
    "tiền bối": "tiền bối",
    "sư phụ": "sư phụ",
    "tiểu tử": "tiểu tử",
    "lão quái": "lão quái",
    "đột phá": "đột phá",
    "linh đan": "linh đan",
    "linh khí": "linh khí",
    "nguyên anh": "nguyên anh",
    "kim đan": "kim đan",
    "trúc cơ": "trúc cơ",
    "hóa thần": "hóa thần",
    "luyện khí": "luyện khí",
    "đại năng": "đại năng",
}

def strip_leading_numbering(text: str) -> str:
    """
    Xóa sạch 100% số thứ tự đầu câu (ví dụ: '1. ', '02. ', '3: ', '4 - ', '[5] ', '(6) ', 'Câu 7: ', '1/100 '):
    - Đảm bảo khi gửi sang CapCut/TikTok TTS, giọng đọc KHÔNG đọc 'Một chấm...', 'Hai chấm...'
    - Giữ nguyên số nếu là số lượng nội dung thực tế (ví dụ: '10 vạn linh thạch', '2024 năm sau').
    """
    if not text:
        return ""
    t = text.strip()
    # 1. Bóc ngoặc vuông / ngoặc tròn / ngoặc nhọn chứa số ở đầu: [1], (1), {1}, 【1】
    t = re.sub(r'^(?:\[\s*\d+\s*\]|\(\s*\d+\s*\)|\{\s*\d+\s*\}|【\s*\d+\s*】)\s*[\.\:\-\–\—\s]*', '', t)
    # 2. Bóc các tiền tố dạng: Câu 1:, Thoại 1:, Đoạn 1:, STT 1:, Line 1:
    t = re.sub(r'^(?:câu|thoại|đoạn|stt|dòng|line)\s*\d+\s*[\.\:\-\–\—\)\/\]\s]*\s*', '', t, flags=re.IGNORECASE)
    # 3. Bóc dạng phân số/tổng số câu: 1/100 
    t = re.sub(r'^\d+\/\d+\s*[\.\:\-\–\—\s]*', '', t)
    # 4. Bóc số thứ tự đầu câu kèm dấu phân cách: 1. , 12. , 1: , 1 - , 1) 
    t = re.sub(r'^\d+\s*[\.\:\-\–\—\)\/\]]+\s*', '', t)
    # 5. Dọn dẹp dấu câu thừa còn sót lại ở đầu chuỗi sau khi bóc số
    t = re.sub(r'^[^\w\s\(\[\{]+', '', t).strip()
    return t


def normalize_tts_text(text: str) -> str:
    """Chuẩn hóa ký tự trước khi đưa vào TikTok TTS."""
    if not text:
        return ""
    text = strip_leading_numbering(text)
    text = re.sub(r'[\r\n\t]+', ' ', text)
    text = re.sub(r'["“”„‟«»]', ' ', text)
    text = re.sub(r'[\(\)\[\]\{\}]', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    # Xóa sạch toàn bộ dấu cuối câu (. , ! ? … : ; - _) để TTS không chèn khoảng lặng nghỉ làm mất thời gian đọc từ
    text = re.sub(r'[\.\,\!\?\…\:\;\—\-\_\s]+$', '', text).strip()
    return text.strip()


def sanitize_to_vietnamese(text: Optional[str], fallback_orig: Optional[str] = None) -> str:
    """Đảm bảo chuỗi đưa vào đọc là tiếng Việt chuẩn 100%."""
    t = (text or "").strip()
    if not t:
        t = (fallback_orig or "").strip()
    return t


_GLOBAL_SESSION = requests.Session()
_GLOBAL_SESSION.headers.update({
    "User-Agent": "com.zhiliaoapp.musically/2022600030 (Linux; U; Android 7.1.2; es_ES; SM-G988N; Build/NRD90M;tt-ok/3.12.13.1)",
    "Accept": "application/json",
})

_AUDIO_CACHE: Dict[str, AudioSegment] = {}


class TikTokTTSService:
    VOICE_MAP = {
        "vi_male_standard": "Nam Tiêu Chuẩn (Giọng đọc truyện)",
        "vi_female_standard": "Nữ Tiêu Chuẩn (Truyền Cảm)",
        "vi_female_huong": "Nữ Hương (Ngọt Ngào)",
        "vi_female_mai": "Nữ Mai (Nhẹ Nhàng)",
        "vi_male_hung": "Nam Hùng (Hùng Hồn)",
        "vi_male_quan": "Nam Quân (Ấm Áp)",
    }

    @classmethod
    def get_supported_voices(cls) -> List[Dict[str, str]]:
        return [{"code": k, "name": v} for k, v in cls.VOICE_MAP.items()]

    @classmethod
    def trim_audio_silence(cls, audio_segment: AudioSegment, silence_thresh: int = -40, chunk_size: int = 10) -> AudioSegment:
        if len(audio_segment) < 100:
            return audio_segment
        try:
            from pydub.silence import detect_leading_silence
            start_trim = detect_leading_silence(audio_segment, silence_threshold=silence_thresh, chunk_size=chunk_size)
            end_trim = detect_leading_silence(audio_segment.reverse(), silence_threshold=silence_thresh, chunk_size=chunk_size)
            duration = len(audio_segment)
            trimmed = audio_segment[start_trim:duration - end_trim]
            return trimmed if len(trimmed) > 50 else audio_segment
        except Exception:
            return audio_segment

    @classmethod
    def synthesize_sentence(
        cls,
        text: str,
        voice_code: str = "vi_female_standard",
        session_id: str = None,
        apply_mastering: bool = True,
        playback_speed: float = 1.0,
        max_retries: int = 3
    ) -> Optional[AudioSegment]:
        clean_text = normalize_tts_text(text)
        if not clean_text:
            return None

        cache_key = f"{voice_code}_{clean_text}_{playback_speed}"
        if cache_key in _AUDIO_CACHE:
            return _AUDIO_CACHE[cache_key]

        actual_session = session_id or DEFAULT_TIKTOK_SESSION_ID
        params = {
            "text_speaker": voice_code,
            "req_text": clean_text,
            "speaker_map_type": "0",
            "aid": "1233",
        }
        headers = {
            "Cookie": f"sessionid={actual_session}",
            "User-Agent": "com.zhiliaoapp.musically/2022600030 (Linux; U; Android 7.1.2; es_ES; SM-G988N; Build/NRD90M;tt-ok/3.12.13.1)",
        }

        for attempt in range(1, max_retries + 1):
            try:
                res = _GLOBAL_SESSION.post(TIKTOK_API_ENDPOINT, params=params, headers=headers, timeout=12)
                if res.status_code == 200:
                    data = res.json()
                    if data.get("status_code") == 0 and "data" in data and "v_str" in data["data"]:
                        audio_bytes = base64.b64decode(data["data"]["v_str"])
                        seg = _load_audio_from_bytes(audio_bytes, format="mp3")
                        seg = cls.trim_audio_silence(seg)
                        if len(seg) > 100:
                            _AUDIO_CACHE[cache_key] = seg
                            return seg
                time.sleep(0.15)
            except Exception as exc:
                if attempt == max_retries:
                    logger.error(f"[TikTok TTS] Thất bại câu: {clean_text[:40]} -> {exc}")
        return None

    @classmethod
    def synthesize_chunk(cls, text: str, voice_code: str = "vi_female_standard", session_id: str = None) -> Optional[AudioSegment]:
        return cls.synthesize_sentence(text=text, voice_code=voice_code, session_id=session_id)

    @classmethod
    def time_stretch_by_factor(cls, audio_seg: AudioSegment, speed_factor: float) -> AudioSegment:
        if abs(speed_factor - 1.0) < 0.02:
            return audio_seg
        try:
            ffmpeg_bin = get_ffmpeg_cmd()[0]
            filters = []
            rem = float(speed_factor)
            while rem > 2.0:
                filters.append("atempo=2.0")
                rem /= 2.0
            while rem < 0.5:
                filters.append("atempo=0.5")
                rem /= 0.5
            filters.append(f"atempo={rem:.4f}")

            cmd = [
                ffmpeg_bin, "-y",
                "-f", "s16le", "-ar", str(audio_seg.frame_rate), "-ac", str(audio_seg.channels),
                "-i", "pipe:0",
                "-filter:a", ",".join(filters),
                "-f", "s16le", "-ar", str(audio_seg.frame_rate), "-ac", str(audio_seg.channels),
                "pipe:1"
            ]
            res = subprocess.run(cmd, input=audio_seg.raw_data, capture_output=True, timeout=10)
            if res.returncode == 0 and len(res.stdout) > 0:
                return AudioSegment(
                    data=res.stdout,
                    sample_width=audio_seg.sample_width,
                    frame_rate=audio_seg.frame_rate,
                    channels=audio_seg.channels
                )
        except Exception:
            pass

        # Fallback sang pydub speedup
        try:
            return audio_seg.speedup(playback_speed=speed_factor, chunk_size=50, crossfade=15)
        except Exception:
            return audio_seg

    @classmethod
    def produce_project_voiceover(
        cls,
        task_id: str,
        project_id: int,
        db: Session,
        voice_code: str = "vi_female_standard",
        session_id: str = None,
        apply_mastering: bool = True,
        playback_speed: float = 1.0,
        auto_fit_timeline: bool = True,
        max_workers: int = None,
        force_regenerate: bool = True
    ) -> Dict[str, Any]:
        """
        Quy trình sản xuất âm thanh TikTok TTS ĐỒNG TỐC VỚI HÀNG ĐỢI ƯU TIÊN ĐỘNG:
        - Sử dụng AsyncTTSEngine Priority Queue chạy song song đa luồng.
        - Ghép Master Audio Track bằng NumPy Buffer siêu tốc.
        """
        project = db.query(ProjectTask).filter(ProjectTask.id == project_id).first()
        if not project:
            raise ValueError(f"Không tìm thấy Project ID #{project_id}")

        # 0. QUÉT & TỰ ĐỘNG VÁ 100% CHỮ HÁN TRƯỚC KHI VÀO TTS
        fixed_leak = ensure_project_dialogues_vietnamese(db, project_id)
        if fixed_leak > 0:
            task_manager.add_log(task_id, f"🛡️ [Chinese Guard] Đã tự động phát hiện và dịch {fixed_leak} câu còn dính chữ Hán sang tiếng Việt chuẩn!", "emerald")

        dialogues = db.query(DialogueSegmentModel).filter(
            DialogueSegmentModel.task_id == project_id
        ).order_by(DialogueSegmentModel.index.asc()).all()

        if not dialogues:
            raise ValueError(f"Project #{project_id} chưa có câu thoại nào!")

        raw_workers = max_workers or getattr(settings, 'TTS_MAX_WORKERS', 64)
        try:
            num_workers = int(raw_workers)
        except Exception:
            num_workers = 64
        num_workers = max(1, min(128, num_workers))

        safe_title = "".join(c for c in project.title if c.isalnum() or c in (' ', '_', '-')).strip() or f"project_{project.id}"
        master_voice_file = settings.OUTPUT_VOICEOVER_DIR / f"{project.video_id}_{safe_title}_voiceover.mp3"

        if not force_regenerate and master_voice_file.exists() and master_voice_file.stat().st_size > 50000:
            task_manager.add_log(task_id, f"✔ Tái sử dụng file âm thanh lồng tiếng đã lưu sẵn: output/voiceover/{master_voice_file.name}", "emerald")
            project.status = "DUBBED"
            db.commit()
            return {
                "status": "success",
                "project_id": project.id,
                "audio_path": str(master_voice_file),
                "master_voice_path": str(master_voice_file),
                "total_dialogues": len(dialogues)
            }

        task_manager.add_log(task_id, f"🎙️ BẮT ĐẦU TẠO ÂM THANH TIKTOK TTS (Project #{project_id}: {project.title})", "purple")
        task_manager.add_log(task_id, f"   Động cơ: TikTok TTS | Giọng: {voice_code} | Luồng xử lý: {num_workers} Async Workers", "cyan")

        project_audio_dir = settings.TEMP_TTS_DIR / project.video_id
        segments_dir = project_audio_dir / "segments"
        segments_dir.mkdir(parents=True, exist_ok=True)

        total_video_ms = int((project.duration or 0.0) * 1000)
        if total_video_ms == 0 and dialogues:
            total_video_ms = int(dialogues[-1].end_time * 1000) + 1000

        total_count = len(dialogues)
        raw_results_map = {}

        # =====================================================================
        # PHA 1: TẢI TOÀN BỘ ÂM THANH QUA DYNAMIC IN-FLIGHT PRIORITY QUEUE
        # =====================================================================
        proxy_count = proxy_manager.count
        proxy_info = f" | Proxy: {proxy_count} Proxies xoay vòng" if proxy_count > 0 else " | Proxy: Direct (Không dùng proxy)"
        task_manager.add_log(task_id, f"⚡ [Priority Queue] Đang tải {total_count} câu thoại TikTok ({num_workers} luồng xử lý ưu tiên{proxy_info})...", "cyan")

        # Chuẩn bị dữ liệu thoại
        dialogues_data = []
        silent_dialogue_ids = set()
        for d in dialogues:
            raw_text = sanitize_to_vietnamese(
                d.translated_text,
                fallback_orig=(d.clean_text or d.original_text)
            ).strip()
            # Xóa sạch 100% số thứ tự đầu câu (1. , 2. , [1], Câu 1:...)
            text_to_speak = strip_leading_numbering(raw_text)
            text_to_speak = text_to_speak.replace('"', '').replace("'", '').replace("`", "").strip()
            # Xóa sạch toàn bộ dấu cuối câu (. , ! ? … : ; - _) để TTS không chèn khoảng lặng nghỉ làm mất thời gian đọc từ
            text_to_speak = re.sub(r'[\.\,\!\?\…\:\;\—\-\_\s]+$', '', text_to_speak).strip()

            # Phải có ít nhất 1 ký tự chữ cái hoặc số để API TTS có thể đọc
            if text_to_speak and any(c.isalnum() for c in text_to_speak):
                dialogues_data.append((d.id, text_to_speak))
            else:
                # Câu chỉ là dấu chấm '.' hoặc khoảng lặng không lời -> tạo đệm im lặng, không gửi lên API
                silent_dialogue_ids.add(d.id)

        if silent_dialogue_ids:
            task_manager.add_log(task_id, f"ℹ️ Phát hiện {len(silent_dialogue_ids)} câu là khoảng lặng/dấu chấm lẻ, tự động tạo đệm âm thanh im lặng chuẩn xác.", "cyan")

        def _update_progress(completed_count, total_target):
            progress_pct = int((completed_count / total_target) * 70)
            task_manager.update_task(task_id, progress=progress_pct, stage=f"TTS Priority Queue: {completed_count}/{total_target}")

        def _is_cancelled():
            return task_manager.is_cancelled(task_id)

        def _log_callback(msg: str, color: str = "cyan"):
            task_manager.add_log(task_id, msg, color)

        from app.services.tts.async_tts_engine import AsyncTTSEngine
        loop = asyncio.new_event_loop()
        try:
            raw_audio_bytes_map = loop.run_until_complete(
                AsyncTTSEngine.run_batch_priority_queue(
                    dialogues_data=dialogues_data,
                    engine="tiktok",
                    voice_code=voice_code,
                    session_id=session_id,
                    concurrency_limit=num_workers,
                    max_retries_per_item=5,
                    progress_callback=_update_progress,
                    log_callback=_log_callback,
                    is_cancelled_callback=_is_cancelled,
                )
            )
        finally:
            loop.close()

        if task_manager.is_cancelled(task_id):
            raise RuntimeError("Tiến trình đã bị người dùng hủy bỏ!")

        # Giải mã In-Memory Buffer sang AudioSegment
        for d_id, audio_bytes in raw_audio_bytes_map.items():
            if audio_bytes and len(audio_bytes) > 100:
                seg = _load_audio_from_bytes(audio_bytes, format="mp3")
                if len(seg) > 100:
                    raw_results_map[d_id] = seg

        # Bổ sung đệm im lặng cho các câu chỉ có dấu chấm / khoảng lặng
        for s_id in silent_dialogue_ids:
            raw_results_map[s_id] = AudioSegment.silent(duration=200)

        # ── VÉT CẠN CÂU THIẾU: Đảm bảo 100% câu thoại đều có âm thanh trước khi đóng gói ──
        missing_dialogues = [d for d in dialogues if d.id not in raw_results_map]
        if missing_dialogues:
            task_manager.add_log(task_id, f"⚠️ Phát hiện {len(missing_dialogues)} câu còn thiếu. Bắt đầu vét cạn cứu hộ để đạt 100%...", "amber")
            for m in missing_dialogues:
                raw_text = sanitize_to_vietnamese(
                    m.translated_text,
                    fallback_orig=(m.clean_text or m.original_text)
                ).strip()
                clean_text = strip_leading_numbering(raw_text)
                clean_text = re.sub(r'^[\.\,\!\?\…\:\;\—\-\s]+|[\.\,\!\?\…\:\;\—\-\s]+$', '', clean_text).strip()
                duration_ms = max(200, int((m.end_time - m.start_time) * 1000)) if (m.end_time and m.start_time) else 400

                if not clean_text or not any(c.isalnum() for c in clean_text):
                    # Câu chỉ là dấu chấm / khoảng lặng -> cấp đệm im lặng chuẩn
                    raw_results_map[m.id] = AudioSegment.silent(duration=min(1000, duration_ms))
                    task_manager.add_log(task_id, f"✔ Câu #{m.index} (Khoảng lặng/dấu chấm) -> Đã cấp đệm im lặng {duration_ms}ms.", "cyan")
                else:
                    try:
                        rescued_seg = cls.synthesize_chunk(clean_text, voice_code=voice_code)
                        if rescued_seg and len(rescued_seg) > 100:
                            raw_results_map[m.id] = rescued_seg
                            task_manager.add_log(task_id, f"✔ Cứu hộ thành công câu #{m.index}!", "emerald")
                    except Exception as e:
                        logger.error(f"Lỗi cứu hộ câu #{m.index}: {e}")

        valid_count = len(raw_results_map)
        task_manager.add_log(task_id, f"✔ Hoàn tất tải âm thanh tiếng Việt: {valid_count}/{total_count} câu thoại hợp lệ.", "emerald")

        if valid_count < total_count:
            still_missing = [d.index for d in dialogues if d.id not in raw_results_map]
            raise RuntimeError(f"Chưa hoàn thành đủ 100% số câu! Còn thiếu {len(still_missing)} câu: {still_missing[:10]}... Dừng đóng gói để bảo toàn chất lượng phim!")

        # =====================================================================
        # PHA 2: TÍNH TOÁN CO GIÃN VỪA KHÍT TIMELINE VÀ DÁN VÀO MASTER (NUMPY SIÊU TỐC)
        # =====================================================================
        task_manager.add_log(task_id, f"🎯 [Pha 2/2] Ghép nối {total_count} câu thoại vào Master Audio Track (Engine: NumPy Siêu Tốc)...", "cyan")

        fs = 24000
        total_samples = int((total_video_ms / 1000.0) * fs) + fs
        # MONO int16: Giọng đọc chỉ cần mono 1 kênh, giảm dung lượng RAM từ 7GB xuống chỉ 1.7GB cho video 10 tiếng
        master_buffer = np.zeros(total_samples, dtype=np.int16)

        current_timeline_sec = 0.0

        for idx_d, d in enumerate(dialogues):
            raw_seg = raw_results_map.get(d.id)
            if not raw_seg:
                continue

            raw_dur_sec = len(raw_seg) / 1000.0

            # 1. Điểm bắt đầu lý tưởng theo mốc ASR gốc
            ideal_start_sec = float(d.start_time)
            start_sec = max(ideal_start_sec, current_timeline_sec)

            orig_end_sec = float(d.end_time) if (d.end_time and d.end_time > ideal_start_sec) else (ideal_start_sec + raw_dur_sec)
            orig_frame_dur = max(0.05, orig_end_sec - ideal_start_sec)

            # Mốc bắt đầu của câu kế tiếp trong video (nếu có)
            if idx_d + 1 < len(dialogues):
                next_orig_start = float(dialogues[idx_d + 1].start_time)
            else:
                next_orig_start = orig_end_sec + 2.0

            # Khung thời lượng mục tiêu: KHÍT CHẶT KHUNG START - END CỦA CÂU GỐC
            # Chừa 20ms micro-pause ở cuối để dứt câu tự nhiên và không dính vào câu sau
            target_dur = max(0.35, orig_frame_dur - 0.02)
            if next_orig_start > start_sec:
                target_dur = min(target_dur, max(0.30, (next_orig_start - start_sec) - 0.02))

            # Co giãn thích ứng (Adaptive Time Stretch):
            # Nếu thời gian nói dài hơn gốc -> TĂNG TỐC ĐỘ để vừa khít khung thời gian lấy được
            if auto_fit_timeline and raw_dur_sec > target_dur:
                speed_factor = raw_dur_sec / target_dur
                # Giới hạn tăng tốc tối đa an toàn 1.8x để câu nói rõ chữ, không bị thé giọng
                speed_factor = min(1.8, max(1.0, speed_factor))
                fitted_seg = cls.time_stretch_by_factor(raw_seg, speed_factor)
                actual_speed = speed_factor
            else:
                fitted_seg = raw_seg
                actual_speed = 1.0

            # BẢO TOÀN 100% ÂM THANH - TUYỆT ĐỐI KHÔNG CẮT CỤT ĐUÔI CÂU:
            # Nhờ Prompt khống chế chuẩn độ phình 1.4 lần, câu đã ôm vừa khít khung thời lượng.
            # Giữ trọn vẹn từng từ ngữ đến hết câu, không bao giờ dùng lệnh chém đuôi âm thanh.

            seg_dur_sec = len(fitted_seg) / 1000.0

            # Cập nhật thông số chuẩn xác vào CSDL
            d.voice_duration = round(seg_dur_sec, 3)
            d.speed_ratio = round(actual_speed, 2)
            d.status = "DUBBED"

            # Cập nhật mốc timeline (chừa 20ms micro-pause) để câu sau không bao giờ bị đè
            current_timeline_sec = start_sec + seg_dur_sec + 0.02

            # Dán trực tiếp vào NumPy Master Buffer (chuẩn Mono 24kHz int16)
            try:
                norm_seg = fitted_seg.set_frame_rate(fs).set_channels(1)
                seg_samples = np.array(norm_seg.get_array_of_samples(), dtype=np.int16)

                start_idx = int(start_sec * fs)
                seg_len = len(seg_samples)
                end_idx = min(start_idx + seg_len, total_samples)

                fit_len = end_idx - start_idx
                if fit_len > 0:
                    master_buffer[start_idx:end_idx] = seg_samples[:fit_len]
            except Exception as e:
                logger.error(f"Lỗi ghép câu #{d.index} vào timeline: {e}")

            # In log tiến độ định kỳ mỗi 1,000 câu
            if (idx_d + 1) % 1000 == 0 or (idx_d + 1) == total_count:
                pct = int(((idx_d + 1) / total_count) * 100)
                task_manager.add_log(task_id, f"   • [Pha 2/2] Đã ghép {idx_d + 1:,} / {total_count:,} câu ({pct}%) vào Timeline...", "cyan")

        # Lưu thay đổi thông số voice_duration vào CSDL
        db.commit()

        # Xuất file âm thanh siêu tốc sang FFmpeg qua Streaming Pipe
        # KHÔNG tobytes() toàn bộ và KHÔNG bọc qua AudioSegment để tránh nhân bản thêm 7GB RAM
        settings.OUTPUT_VOICEOVER_DIR.mkdir(parents=True, exist_ok=True)
        task_manager.add_log(task_id, f"💾 Đang xuất file Master Audio MP3 192kbps (Streaming Pipe siêu tốc)...", "cyan")

        ffmpeg_cmd = get_ffmpeg_cmd()
        cmd = [
            *ffmpeg_cmd, "-y",
            "-f", "s16le",
            "-ar", str(fs),
            "-ac", "1",
            "-i", "pipe:0",
            "-c:a", "libmp3lame",
            "-b:a", "192k",
            str(master_voice_file)
        ]

        pipe_proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE
        )
        chunk_samples = fs * 60  # Mỗi khối 60 giây (~120KB)
        for i in range(0, total_samples, chunk_samples):
            chunk = master_buffer[i:i + chunk_samples]
            pipe_proc.stdin.write(chunk.tobytes())

        pipe_proc.stdin.close()
        pipe_proc.wait()
        del master_buffer

        task_manager.add_log(task_id, f"🎉 ĐÃ XUẤT MASTER VOICEOVER THÀNH CÔNG: {master_voice_file.name}", "emerald")

        project.status = "DUBBED"
        db.commit()

        return {
            "status": "success",
            "project_id": project.id,
            "audio_path": str(master_voice_file),
            "master_voice_path": str(master_voice_file),
            "total_dialogues": len(dialogues),
            "engine": "tiktok",
            "global_speed_ratio": 1.0
        }
