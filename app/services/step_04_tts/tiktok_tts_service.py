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
from app.models import ProjectTask, DialogueSegmentModel, TTSDialogueSegmentModel
from app.services.task_manager import task_manager
from app.utils.bin_helper import get_ffmpeg_cmd, get_ffprobe_cmd
from app.services.step_04_tts.proxy_manager import proxy_manager
from app.services.step_04_tts.adaptive_trimmer import AdaptiveSilenceTrimmer
from app.services.step_04_tts.timeline_borrower import TimelineBorrower

logger = logging.getLogger(__name__)

DEFAULT_TIKTOK_SESSION_ID = getattr(settings, "TIKTOK_SESSION_ID", "")
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
    
    # 1. Chuyển đổi các biến thể của "vân vân" (v.v., v.v, v-v, v/v, vv...) để tránh đọc thành số
    text = re.sub(r'v\.v\.?', 'vân vân', text, flags=re.IGNORECASE)
    text = re.sub(r'\b(v[\-\/]v|vv)\b', 'vân vân', text, flags=re.IGNORECASE)
    
    # 2. Chuyển đổi "vi", "Vi", "VI" đứng độc lập thành "vy", "Vy" tránh TTS đọc thành số La Mã VI (sáu)
    text = re.sub(r'\bVI\b', 'Vy', text)
    text = re.sub(r'\bVi\b', 'Vy', text)
    text = re.sub(r'\bvi\b', 'vy', text)

    text = re.sub(r'[\r\n\t]+', ' ', text)
    text = re.sub(r'["“”„‟«»]', ' ', text)
    text = re.sub(r'[\(\)\[\]\{\}]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()

    # 3. Chuẩn hóa dấu câu ở GIỮA chunk: Chuyển toàn bộ các dấu kết câu (. ! ? : ... …) ở giữa câu thành dấu chấm phẩy ";"
    # để tránh làm hỏng âm/nghẹn giọng của TTS CapCut/TikTok giữa chừng.
    text = re.sub(r'(?:\.{3,}|…)', ';', text)
    text = re.sub(r'[\.\!\?\:\;]+(?=\s|$)', ';', text)
    text = re.sub(r'\s*;\s*', '; ', text)
    text = re.sub(r';+', ';', text).strip()

    # Dọn sạch dấu câu dở dang ở cuối
    text = re.sub(r'[\.\,\!\?\…\:\;\—\-\_\s]+$', '', text).strip()

    # Chuẩn hóa kết thúc: kết thúc bằng dấu chấm phẩy ";"
    return f"{text};" if text else ""


def sanitize_to_vietnamese(text: Optional[str], fallback_orig: Optional[str] = None) -> str:
    """Đảm bảo chuỗi đưa vào đọc là tiếng Việt chuẩn 100%, quét sạch 100% cú pháp JSON, tag rác."""
    t = (text or "").strip()
    if not t:
        t = (fallback_orig or "").strip()
    
    # Bóc tách nếu bị dính cú pháp JSON {"i": ..., "vi": "..."} hoặc {"vi": "..."}
    if "{" in t or '"vi"' in t or "'vi'" in t:
        m_vi = re.search(r'["\']vi["\']\s*:\s*["\']([^"\']+)["\']', t)
        if m_vi:
            t = m_vi.group(1).strip()
        else:
            try:
                import json
                json_m = re.search(r'\{.*\}', t)
                if json_m:
                    parsed = json.loads(json_m.group(0))
                    if isinstance(parsed, dict) and "vi" in parsed:
                        t = str(parsed["vi"]).strip()
            except Exception:
                pass
        t = re.sub(r'^\s*\{+\s*["\']?i["\']?\s*:\s*\d+\s*,\s*["\']?vi["\']?\s*:\s*["\']?', '', t)
        t = re.sub(r'["\']?\s*\}+\s*[\.\,\;]?\s*$', '', t)

    # Xóa các thẻ HTML/XML nếu có (vd: <s>, </s>, <p>...)
    t = re.sub(r'<[^>]+>', '', t).strip()
    return t



_GLOBAL_SESSION = requests.Session()
_GLOBAL_SESSION.headers.update({
    "User-Agent": "com.zhiliaoapp.musically/2022600030 (Linux; U; Android 7.1.2; es_ES; SM-G988N; Build/NRD90M;tt-ok/3.12.13.1)",
    "Accept": "application/json",
})

_AUDIO_CACHE: Dict[str, AudioSegment] = {}


class TikTokTTSService:
    VOICE_MAP = {
        "vi-VN-HoaiMyNeural": "Hoài My (Nữ Truyền Cảm - Chuẩn Review)",
        "hoaimy": "Hoài My (Nữ Truyền Cảm - Chuẩn Review)",
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
        force_regenerate: bool = True,
        use_exact_timeline_fit: bool = True  # [NOTE TEST PHƯƠNG ÁN 1]: Khớp 100% timecode gốc, tắt cân bằng & cắt xén
    ) -> Dict[str, Any]:
        """
        Quy trình sản xuất âm thanh TikTok TTS ĐỒNG TỐC VỚI HÀNG ĐỢI ƯU TIÊN ĐỘNG:
        - Sử dụng AsyncTTSEngine Priority Queue chạy song song đa luồng.
        - Ghép Master Audio Track bằng NumPy Buffer siêu tốc.
        """
        project = db.query(ProjectTask).filter(ProjectTask.id == project_id).first()
        if not project:
            raise ValueError(f"Không tìm thấy Project ID #{project_id}")

        # BẮT BUỘC: Đọc từ bảng câu gộp tts_dialogue_segments (Liền mạch chuẩn kịch bản cho lồng tiếng)
        tts_dialogues_db = db.query(TTSDialogueSegmentModel).filter(
            TTSDialogueSegmentModel.task_id == project_id
        ).order_by(TTSDialogueSegmentModel.tts_index.asc()).all()

        if not tts_dialogues_db:
            raise ValueError(f"Project #{project_id} chưa có kịch bản thoại gộp TTS (TTSDialogueSegmentModel)! Vui lòng chạy Dịch thuật trước để tạo kịch bản lồng tiếng hoàn chỉnh.")

        dialogues = tts_dialogues_db

        raw_workers = max_workers or getattr(settings, 'TTS_MAX_WORKERS', 128)
        try:
            num_workers = int(raw_workers)
        except Exception:
            num_workers = 128
        num_workers = max(1, min(128, num_workers))

        safe_title = "".join(c for c in project.title if c.isalnum() or c in (' ', '_', '-')).strip() or f"project_{project.id}"
        master_voice_file = settings.OUTPUT_VOICEOVER_DIR / f"{project.video_id}_{safe_title}_voiceover.mp3"

        # Luôn xóa file voiceover cũ nếu có để tạo mới 100%, tuyệt đối không lẫn bản cũ
        if master_voice_file.exists():
            try:
                master_voice_file.unlink(missing_ok=True)
            except Exception:
                pass

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
                fallback_orig=(getattr(d, 'clean_text', None) or d.original_text)
            ).strip()
            # Chuẩn hóa toàn diện: đổi v.v, vv thành vân vân; vi, Vi, VI thành vy, Vy tránh đọc thành số 6; xóa số thứ tự và dấu thừa
            text_to_speak = normalize_tts_text(raw_text)

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

        from app.services.step_04_tts.async_tts_engine import AsyncTTSEngine
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

        # Bản đồ số từ từng câu để gọt đuôi khoảng lặng thích ứng (<10 từ: -140ms, 10-19 từ: -80ms, >=20 từ: 0ms)
        dialogue_word_counts = {}
        for d in dialogues:
            txt = (d.translated_text or getattr(d, 'clean_text', None) or d.original_text or "").strip()
            w_count = len(txt.split()) if txt else 0
            dialogue_word_counts[d.id] = w_count

        # Giải mã In-Memory Buffer sang AudioSegment nguyên bản đa luồng siêu tốc
        decode_workers = min(16, max(4, (os.cpu_count() or 4)))
        with ThreadPoolExecutor(max_workers=decode_workers) as decode_pool:
            future_to_id = {
                decode_pool.submit(_load_audio_from_bytes, a_bytes, "mp3"): d_id
                for d_id, a_bytes in raw_audio_bytes_map.items()
                if a_bytes and len(a_bytes) > 100
            }
            for fut in as_completed(future_to_id):
                d_id = future_to_id[fut]
                try:
                    seg = fut.result()
                    if seg:
                        raw_results_map[d_id] = seg
                except Exception as e:
                    logger.warning(f"Lỗi giải mã audio cho dialogue #{d_id}: {e}")

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
                    fallback_orig=(getattr(m, 'clean_text', None) or m.original_text)
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

        # =========================================================================
        # 0. GỘP CÁC FILE ÂM THANH CÙNG MỘT NGƯỜI NÓI (Continuous Audio Chunk Merger)
        # TTS xong mới gộp thành câu to cả text nội dung và âm thanh để cùng 1 nhân vật
        # có thể đồng bộ tốc độ nói, vừa in vào start và end khi đã gộp xong.
        # =========================================================================
        from app.services.step_04_tts.capcut_tts_service import CapCutTTSService
        dialogues, raw_results_map, chunk_sub_offsets = CapCutTTSService.merge_continuous_audio_dialogues(
            dialogues=dialogues,
            raw_results_map=raw_results_map,
            db=db,
            project_id=project_id,
            task_id=task_id,
            gap_threshold=0.85
        )
        total_count = len(dialogues)

        # Hợp nhất các mốc sóng âm từ các sub-chunks tạo bởi AsyncTTSEngine
        engine_sub_timings = AsyncTTSEngine.get_last_sub_timings()
        if engine_sub_timings:
            for d in dialogues:
                if d.id in engine_sub_timings and (d.tts_index not in chunk_sub_offsets or len(chunk_sub_offsets[d.tts_index]) <= 1):
                    chunk_sub_offsets[d.tts_index] = engine_sub_timings[d.id]

        task_manager.add_log(task_id, f"🎯 Ghép nối {total_count} chunk thoại vào Master Audio Track (Engine: NumPy Siêu Tốc)...", "cyan")

        fs = 24000
        total_samples = int((total_video_ms / 1000.0) * fs) + fs
        # MONO int16: Giọng đọc chỉ cần mono 1 kênh, giảm dung lượng RAM từ 7GB xuống chỉ 1.7GB cho video 10 tiếng
        master_buffer = np.zeros(total_samples, dtype=np.int16)
        current_timeline_sec = 0.0
        trimmed_sentences_count = 0
        high_speed_anomalies = []

        # =========================================================================
        # 1. ĐIỀU PHỐI VAY MƯỢN THỜI GIAN TIMELINE (Timeline Borrower)
        # =========================================================================
        if use_exact_timeline_fit:
            # [CHẾ ĐỘ KHỚP GỐC & VAY NGHỈ SAU]: Tắt vay mượn giữa các câu thoại (không cướp thời gian câu khác)
            borrow_transactions = []
            borrow_map = {}
        else:
            # [CODE LOGIC GỐC ĐƯỢC BẢO TỒN NGUYÊN VẸN 100% - KHÔI PHỤC KHI CẦN]
            borrow_transactions, borrow_map = TimelineBorrower.rebalance_timeline(
                dialogues=dialogues,
                raw_results_map=raw_results_map,
                target_ratio=1.3
            )
            if borrow_transactions:
                task_manager.add_log(task_id, f"🤝 [Timeline Borrower] Đã điều phối cho vay {len(borrow_transactions)} câu thiếu hụt thành công (ngưỡng 1.3x)!", "emerald")
                print(f"🤝 [Timeline Borrower] Đã điều phối cho vay {len(borrow_transactions)} câu thiếu hụt thành công (ngưỡng 1.3x)!", flush=True)

        for idx_d, d in enumerate(dialogues):
            raw_seg = raw_results_map.get(d.id)
            if not raw_seg:
                continue

            raw_dur_sec = len(raw_seg) / 1000.0

            if use_exact_timeline_fit:
                # =========================================================================
                # [CHẾ ĐỘ MỚI: BẢO TOÀN START, CẮT ĐẦU ĐUÔI, VAY NGHỈ SAU TIẾN VỀ 1.2X, ĐỒNG BỘ CÂU TO]
                # =========================================================================
                # 1. Bảo toàn 100% mốc bắt đầu của câu thoại gốc (không vay nghỉ ở trước)
                start_sec = float(d.start_time)
                orig_end_sec = float(d.end_time) if (d.end_time and d.end_time > start_sec) else (start_sec + raw_dur_sec)
                base_dur = max(0.1, orig_end_sec - start_sec)

                # 2. Cắt âm đầu 140ms, cắt đuôi 140ms để dọn sạch khoảng lặng thừa, bảo vệ an toàn âm tiết
                seg_to_stretch, trim_meta = AdaptiveSilenceTrimmer.trim_audio_segment(
                    raw_seg,
                    target_dur_sec=base_dur,
                    target_ratio=1.0,
                    max_head_ms=140,
                    max_tail_ms=140
                )
                if trim_meta.get("trimmed"):
                    trimmed_sentences_count += 1
                    AdaptiveSilenceTrimmer.log_trim_to_terminal(d.index, raw_dur_sec, base_dur, trim_meta)

                trimmed_dur_sec = len(seg_to_stretch) / 1000.0

                # 3. Tính khoảng nghỉ phía sau (gap_after)
                if idx_d < len(dialogues) - 1:
                    next_d = dialogues[idx_d + 1]
                    next_start = float(next_d.start_time)
                    gap_after = max(0.0, next_start - orig_end_sec)
                else:
                    total_v_sec = total_video_ms / 1000.0
                    gap_after = max(0.0, total_v_sec - orig_end_sec)

                # 4. Tràn tự nhiên / vay ở khoảng nghỉ phía sau nếu câu dài hơn gốc và gap_after > 0.25s (1 nhịp dấu chấm)
                target_dur = base_dur
                if trimmed_dur_sec > base_dur and gap_after > 0.25:
                    deficit = trimmed_dur_sec - base_dur
                    available_gap = max(0.0, gap_after - 0.20)  # Giữ lại 0.20s im lặng an toàn trước câu kế
                    borrow_after = min(deficit, available_gap)
                    if borrow_after > 0:
                        target_dur = base_dur + borrow_after
                        d.borrowed_end_time = round(start_sec + target_dur, 2)
                        d.borrowed_duration = round(target_dur, 2)

                # 5. Co giãn: Nếu tỷ lệ nhỏ hơn hoặc bằng 1.0 -> Giữ nguyên 1.0x tự nhiên (khoảng thừa để end gánh)
                speed_factor = trimmed_dur_sec / target_dur
                if speed_factor <= 1.00:
                    fitted_seg = seg_to_stretch
                    actual_speed = 1.0
                else:
                    safe_speed = min(1.30, speed_factor)  # Khóa trần cứng tốc độ đọc <= 1.30x
                    fitted_seg = cls.time_stretch_by_factor(seg_to_stretch, safe_speed)
                    actual_speed = safe_speed

                seg_dur_sec = len(fitted_seg) / 1000.0

                # Cập nhật thông số timeline chuẩn xác vừa in vào start và end
                d.start_time = round(start_sec, 3)
                d.end_time = round(start_sec + seg_dur_sec, 3)
                d.duration = round(seg_dur_sec, 3)
                d.voice_duration = round(seg_dur_sec, 3)
                d.speed_ratio = round(actual_speed, 2)
                d.status = "DUBBED"
            else:
                # =========================================================================
                # [CODE LOGIC GỐC ĐƯỢC BẢO TỒN NGUYÊN VẸN 100% - KHÔI PHỤC KHI CẦN]
                # =========================================================================
                # 1. Sử dụng mốc sau vay mượn từ borrow_map nếu có, nếu không lấy start_time ASR gốc
                if d.id in borrow_map:
                    b_start, b_end = borrow_map[d.id]
                    ideal_start_sec = b_start
                    orig_end_sec = b_end
                    d.borrowed_start_time = b_start
                    d.borrowed_end_time = b_end
                    d.borrowed_duration = round(b_end - b_start, 2)
                else:
                    ideal_start_sec = float(d.start_time)
                    orig_end_sec = float(d.end_time) if (d.end_time and d.end_time > ideal_start_sec) else (ideal_start_sec + raw_dur_sec)

                start_sec = ideal_start_sec
                target_dur = max(0.1, orig_end_sec - ideal_start_sec)

                # 2. ÁP DỤNG THUẬT TOÁN GỌT THÍCH ỨNG TỶ LỆ 1:2 (Adaptive 1:2 Silence Trimmer)
                seg_to_stretch, trim_meta = AdaptiveSilenceTrimmer.trim_audio_segment(
                    raw_seg,
                    target_dur_sec=target_dur,
                    target_ratio=1.2,
                    max_head_ms=100,
                    max_tail_ms=200
                )
                if trim_meta.get("trimmed"):
                    trimmed_sentences_count += 1
                    # Báo trực tiếp ra Terminal để người dùng theo dõi
                    AdaptiveSilenceTrimmer.log_trim_to_terminal(d.index, raw_dur_sec, target_dur, trim_meta)

                trimmed_dur_sec = len(seg_to_stretch) / 1000.0
                cur_ratio = trimmed_dur_sec / target_dur

                # Kiểm tra ngưỡng bất thường > 2.0x để kích hoạt cảnh báo Vá Lô (Batch Reheal)
                if cur_ratio > 2.0:
                    high_speed_anomalies.append({
                        "dialogue_id": d.id,
                        "index": d.index,
                        "target_dur": target_dur,
                        "trimmed_dur": trimmed_dur_sec,
                        "ratio": cur_ratio
                    })
                    # Báo cảnh báo câu co giãn âm thanh trực tiếp ra Terminal
                    print(f"⚠️ [Timeline TTS] Câu #{d.index}: Co giãn {cur_ratio:.2f}x (Gốc {target_dur:.2f}s, Audio {trimmed_dur_sec:.2f}s)", flush=True)

                raw_speed_factor = trimmed_dur_sec / target_dur
                if auto_fit_timeline and raw_speed_factor > 1.00:
                    speed_factor = min(1.30, raw_speed_factor)  # Khóa trần cứng <= 1.30x
                    fitted_seg = cls.time_stretch_by_factor(seg_to_stretch, speed_factor)
                    actual_speed = speed_factor
                else:
                    # Giữ nguyên bản âm thanh tự nhiên 1.0x
                    fitted_seg = seg_to_stretch
                    actual_speed = 1.0

                seg_dur_sec = len(fitted_seg) / 1000.0

                # Mốc bắt đầu ghép theo mốc đã tính toán điều phối (Borrow Map hoặc ASR)
                start_sec = ideal_start_sec
                if start_sec < current_timeline_sec - 0.05:
                    # Đảm bảo không đè micro-gap nếu câu trước kéo dài hơn
                    start_sec = current_timeline_sec

                # Cập nhật mốc timeline khớp theo âm thanh thực tế
                current_timeline_sec = start_sec + seg_dur_sec

                # Cập nhật mốc thời gian thực tế đã cân bằng vào CSDL bảng câu gộp TTS
                d.start_time = round(start_sec, 3)
                d.end_time = round(current_timeline_sec, 3)
                d.duration = round(seg_dur_sec, 3)
                d.voice_duration = round(seg_dur_sec, 3)
                d.speed_ratio = round(actual_speed, 2)
                d.status = "DUBBED"

            # Dán trực tiếp vào NumPy Master Buffer (chuẩn Mono 24kHz int16)
            try:
                norm_seg = fitted_seg.set_frame_rate(fs).set_channels(1)
                seg_samples = np.array(norm_seg.get_array_of_samples(), dtype=np.int16)

                start_idx = int(start_sec * fs)
                seg_len = len(seg_samples)
                if use_exact_timeline_fit:
                    # Giới hạn không cho âm thanh lấn sang khung của câu kế tiếp
                    max_allowed_len = int(target_dur * fs)
                    fit_len = min(seg_len, max_allowed_len)
                else:
                    fit_len = seg_len
                end_idx = min(start_idx + fit_len, total_samples)

                if end_idx > start_idx:
                    master_buffer[start_idx:end_idx] = seg_samples[:end_idx - start_idx]
            except Exception as e:
                logger.error(f"Lỗi ghép câu #{d.index} vào timeline: {e}")

            # ── ĐỒNG BỘ MỐC TIMELINE CÂU CON THEO SÓNG ÂM THANH THỰC TẾ (AUDIO WAVEFORM SYNC) ──
            from app.services.step_04_tts.waveform_sub_syncer import WaveformSubSyncer
            WaveformSubSyncer.sync_subtitles_to_waveform(
                db=db,
                project_id=project_id,
                tts_index=d.tts_index,
                start_sec=start_sec,
                seg_dur_sec=seg_dur_sec,
                actual_speed=actual_speed,
                trimmed_dur_sec=trimmed_dur_sec,
                trim_meta=trim_meta,
                chunk_sub_offsets=chunk_sub_offsets
            )

            # In log tiến độ định kỳ mỗi 1,000 câu
            if (idx_d + 1) % 1000 == 0 or (idx_d + 1) == total_count:
                pct = int(((idx_d + 1) / total_count) * 100)
                task_manager.add_log(task_id, f"   • [Pha 2/2] Đã ghép {idx_d + 1:,} / {total_count:,} câu ({pct}%) vào Timeline...", "cyan")

        # Lưu thay đổi thông số voice_duration vào CSDL
        db.commit()

        # Báo cáo hiệu quả gọt thích ứng và cảnh báo câu vượt ngưỡng > 2.0x
        if trimmed_sentences_count > 0:
            trim_msg = f"✂️ [Adaptive 1:2 Trimmer] Đã gọt thích ứng {trimmed_sentences_count}/{total_count} câu (giảm tối đa phình âm thanh, đầu gánh 1/3 max 100ms, đuôi gánh 2/3 max 200ms)."
            task_manager.add_log(task_id, trim_msg, "emerald")
            print(f"\n{trim_msg}", flush=True)

        if high_speed_anomalies:
            task_manager.add_log(task_id, f"⚠️ Có {len(high_speed_anomalies)} câu thoại phải co giãn âm thanh > 1.8x để khớp timeline.", "amber")

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
            stderr=subprocess.DEVNULL
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

        # ── TÁI SINH BỘ 3 FILE PHỤ ĐỀ KARAOKE ĐỒNG BỘ 100% VỚI GIỌNG ĐỌC MASTER ──
        try:
            from app.services.step_05_video.karaoke_subtitle_service import KaraokeSubtitleService
            from app.services.step_05_video.subtitle_builder_service import format_timestamp
            from app.schemas.transcript import DialogueSegment
            import shutil

            all_db_dialogues = db.query(DialogueSegmentModel).filter(
                DialogueSegmentModel.task_id == project_id
            ).order_by(DialogueSegmentModel.index.asc()).all()

            if all_db_dialogues:
                from app.config import get_project_dirs
                p_dirs = get_project_dirs(project_id)
                sub_dir = p_dirs["video_subtitles"]
                sub_dir.mkdir(parents=True, exist_ok=True)

                all_recon = [
                    {
                        "id": d.index,
                        "start": float(d.start_time),
                        "end": float(d.end_time),
                        "duration": float(d.duration),
                        "zh": d.clean_text or d.original_text,
                        "vi": d.translated_text,
                        "role": getattr(d, 'speaker', 'NARRATOR'),
                        "tts_index": d.tts_index
                    }
                    for d in all_db_dialogues
                ]
                json_path = sub_dir / "subtitles_karaoke.json"
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(all_recon, f, ensure_ascii=False, indent=2)

                srt_path = sub_dir / "subtitles_karaoke.srt"
                with open(srt_path, "w", encoding="utf-8") as f:
                    for s in all_recon:
                        f.write(f"{s['id']}\n{format_timestamp(s['start'])} --> {format_timestamp(s['end'])}\n{s['vi']}\n\n")

                ass_path = sub_dir / "subtitles_karaoke.ass"
                segments_for_ass = [
                    DialogueSegment(
                        id=d.index,
                        start=float(d.start_time),
                        end=float(d.end_time),
                        duration=float(d.duration),
                        text=d.original_text,
                        clean_text=d.clean_text,
                        translated_text=d.translated_text
                    )
                    for d in all_db_dialogues
                ]
                KaraokeSubtitleService.create_karaoke_ass_file(
                    segments=segments_for_ass,
                    output_ass_path=str(ass_path),
                    video_title=project.title
                )

                root_ass = p_dirs["root"] / f"{project.video_id}_karaoke.ass"
                transcripts_ass = settings.OUTPUT_TRANSCRIPTS_DIR / f"{project.video_id}_karaoke.ass"
                if ass_path.exists():
                    shutil.copyfile(ass_path, root_ass)
                    shutil.copyfile(ass_path, transcripts_ass)
                task_manager.add_log(task_id, f"✔ Đã tái sinh phụ đề Karaoke (JSON, SRT, ASS) khớp 100% với giọng đọc Master!", "emerald")
        except Exception as e:
            logger.warning(f"Lỗi xuất phụ đề karaoke sau TikTok TTS: {e}")

        return {
            "status": "success",
            "project_id": project.id,
            "audio_path": str(master_voice_file),
            "master_voice_path": str(master_voice_file),
            "total_dialogues": len(dialogues),
            "engine": "tiktok",
            "global_speed_ratio": 1.0
        }
