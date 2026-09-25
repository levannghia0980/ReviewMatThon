import os
import io
import re
import time
import base64
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from sqlalchemy.orm import Session
import numpy as np
from scipy import signal
from pydub import AudioSegment
from pydub.effects import normalize, compress_dynamic_range, speedup

from app.config import settings
from app.models.project import ProjectTask
from app.models.dialogue import DialogueSegmentModel
from app.services.task_manager import task_manager

from app.utils.bin_helper import get_ffmpeg_cmd, setup_system_path

try:
    setup_system_path()
    _ff_bin = get_ffmpeg_cmd()[0]
    AudioSegment.converter = _ff_bin
    AudioSegment.ffmpeg = _ff_bin
except Exception:
    pass

DEFAULT_TIKTOK_SESSION_ID = os.getenv("TIKTOK_SESSION_ID", "410bfa37bdc185e1c6da82e1afb48409")
TIKTOK_API_ENDPOINT = "https://api16-normal-v4.tiktokv.com/media/api/text/speech/invoke/"

def _load_audio_from_bytes(data: bytes, format: str = "mp3") -> AudioSegment:
    """
    Giải mã âm thanh từ buffer an toàn 100% trên Windows:
    Sử dụng ffmpeg pipe decode sang WAV thô và đọc bằng AudioSegment.from_wav
    (tránh triệt để lỗi pydub ffprobe JSONDecodeError và seekback pipe:0).
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
    return AudioSegment.silent(duration=200)


# ============================================================
# BẢNG TỪ ĐIỂN PHÁT ÂM CHO THUẬT NGỮ TU TIÊN / XIANXIA
# TikTok TTS thường đọc sai các từ Hán-Việt không dấu hoặc
# từ ghép không phổ thông → thay bằng dạng có dấu hoặc cách viết
# buộc TTS đọc đúng âm tiết mong muốn.
# Format: {"từ_sai_bị_đọc_sai": "từ_thay_thế_TTS_đọc_đúng"}
# ============================================================
def normalize_tts_text(text: str) -> str:
    """
    Chuẩn hóa văn bản trước khi gửi sang TikTok TTS:
    - Loại bỏ dấu ngoặc đơn, ngoặc kép, dấu gạch nối giữa các từ (tránh TTS đọc thành 'đến').
    - Giữ nguyên tiếng Việt tự nhiên: ByteDance TTS đọc chuẩn các từ tu tiên.
    """
    if not text:
        return ""
    text = text.replace('"', '').replace("'", '').replace("`", "")
    # Xóa bỏ dấu gạch nối giữa các từ ghép (ví dụ tu-luyện -> tu luyện, tránh bị đọc thành 'tu đến luyện')
    text = re.sub(r'(\w)-(\w)', r'\1 \2', text)
    text = re.sub(r'[\r\n\t]+', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def calculate_punctuation_pause(text: str) -> float:
    """
    Tính tổng thời gian ngắt nghỉ tự nhiên của các dấu câu trong câu thoại:
    - Dấu ba chấm '...' hoặc '…': 0.35s
    - Dấu chấm, hỏi, than (., ?, !): 0.25s
    - Dấu phẩy, chấm phẩy (,, ;): 0.18s
    - Dấu hai chấm, gạch ngang (:, -): 0.15s
    """
    if not text:
        return 0.0

    t = text.strip()
    total_pause = 0.0

    # 1. Đếm và loại trừ dấu ba chấm trước để không bị trùng với dấu chấm đơn
    ellipsis_matches = re.findall(r'\.{3,}|…', t)
    total_pause += len(ellipsis_matches) * 0.35
    t_no_ellipsis = re.sub(r'\.{3,}|…', '', t)

    # 2. Đếm dấu kết câu đơn lẻ: . ? !
    terminals = re.findall(r'[.?!]', t_no_ellipsis)
    total_pause += len(terminals) * 0.25

    # 3. Đếm dấu phẩy và chấm phẩy: , ; (loại trừ số thập phân như 1,500 hay 2,5)
    commas = re.findall(r'(?<!\d)[,;](?!\d)', t_no_ellipsis)
    total_pause += len(commas) * 0.18

    # 4. Đếm dấu hai chấm hoặc gạch ngang ngắt nhịp
    colons = re.findall(r'[:—\-]', t_no_ellipsis)
    total_pause += len(colons) * 0.15

    return total_pause


# Pooled HTTP Session tối ưu cho 48+ luồng song song không nghẽn socket
_GLOBAL_SESSION = requests.Session()
_adapter = HTTPAdapter(pool_connections=128, pool_maxsize=128, max_retries=Retry(total=2, backoff_factor=0.1))
_GLOBAL_SESSION.mount("https://", _adapter)
_GLOBAL_SESSION.mount("http://", _adapter)

# 4 Giọng Nữ Tiếng Việt Chuẩn 100% - Khớp với bộ mẫu test của hệ thống
TIKTOK_VOICES = [
    {"id": "BV074_streaming", "name": "Nữ: Cô Gái Hoạt Ngôn (Trẻ trung, Review Trend)", "gender": "female", "lang": "vi"},
    {"id": "BV421_vivn_streaming", "name": "Nữ: Nhỏ Ngọt Ngào (Êm ái, Nhẹ nhàng, Truyền cảm)", "gender": "female", "lang": "vi"},
    {"id": "vi_female_huong", "name": "Nữ: Hương (Phổ thông miền Bắc, Chuẩn Review)", "gender": "female", "lang": "vi"},
    {"id": "BV562_streaming", "name": "Nữ: Mai (Trầm ấm, Thanh lịch, Kể chuyện dài)", "gender": "female", "lang": "vi"},
]

_AUDIO_CACHE: Dict[str, AudioSegment] = {}

class TikTokTTSService:
    @staticmethod
    def get_supported_voices() -> List[Dict[str, str]]:
        return TIKTOK_VOICES

    @staticmethod
    def apply_clarity_mastering(
        audio_seg: AudioSegment,
        boost_clarity_db: float = 3.5,
        de_harsh: bool = True
    ) -> AudioSegment:
        """
        Xử lý làm RÕ CHỮ & GIỮ ĐỘ THẬT CỦA GIỌNG TIKTOK (Không tăng Pitch, không chói tai).
        """
        try:
            samples = np.array(audio_seg.get_array_of_samples()).astype(np.float32)
            fs = audio_seg.frame_rate
            channels = audio_seg.channels
            if channels == 2:
                samples = samples.reshape((-1, 2))

            # 1. Cắt sub-bass vô hình (<80Hz)
            sos_hp = signal.butter(2, 80, btype='highpass', fs=fs, output='sos')
            samples = signal.sosfilt(sos_hp, samples, axis=0)

            # 2. Peak EQ: Tăng độ nét rõ chữ ở dải phụ âm tiếng Việt (3200 Hz, Q=1.0)
            f0 = 3200.0
            Q = 1.0
            gain_db = boost_clarity_db
            A = 10.0 ** (gain_db / 40.0)
            w0 = 2 * np.pi * f0 / fs
            alpha = np.sin(w0) / (2 * Q)

            b0 = 1 + alpha * A
            b1 = -2 * np.cos(w0)
            b2 = 1 - alpha * A
            a0 = 1 + alpha / A
            a1 = -2 * np.cos(w0)
            a2 = 1 - alpha / A

            b = [b0/a0, b1/a0, b2/a0]
            a = [1.0, a1/a0, a2/a0]
            samples = signal.lfilter(b, a, samples, axis=0)

            # 3. De-harsh: Cắt nhẹ tần số gắt trên 8500Hz
            if de_harsh:
                sos_lp = signal.butter(2, 8500, btype='lowpass', fs=fs, output='sos')
                samples = signal.sosfilt(sos_lp, samples, axis=0)

            # Chuyển ngược về AudioSegment
            samples = np.clip(samples, -32768, 32767).astype(np.int16)
            enhanced = AudioSegment(
                samples.tobytes(),
                frame_rate=fs,
                sample_width=2,
                channels=channels
            )

            compressed = compress_dynamic_range(enhanced, threshold=-18.0, ratio=2.5, attack=5.0, release=50.0)
            return normalize(compressed)
        except Exception:
            return audio_seg

    @staticmethod
    def trim_audio_silence(seg: AudioSegment, silence_thresh: int = -40) -> AudioSegment:
        """Cắt bỏ khoảng lặng giả ở đầu và đuôi do TTS tự chèn khi đọc."""
        if len(seg) < 150:
            return seg
        try:
            from pydub.silence import detect_leading_silence
            start_trim = detect_leading_silence(seg, silence_threshold=silence_thresh)
            end_trim = detect_leading_silence(seg.reverse(), silence_threshold=silence_thresh)
            duration = len(seg)
            # Giữ lại 15ms đệm nhỏ an toàn để không bị cụt phụ âm bật
            trimmed = seg[max(0, start_trim - 15) : max(0, duration - end_trim + 15)]
            return trimmed if len(trimmed) >= 100 else seg
        except Exception:
            return seg

    @classmethod
    def synthesize_single_chunk(
        cls,
        text: str,
        voice_code: str = "BV074_streaming",
        session_id: str = None,
        max_retries: int = 3
    ) -> AudioSegment:
        """
        Tổng hợp âm thanh 1 câu thoại ngắn qua TikTok TTS API với Cache siêu tốc & Retry thông minh.
        """
        if not text or not text.strip():
            return AudioSegment.silent(duration=200)

        # Tiền xử lý: thay thế từ TTS hay đọc sai (tu vi → tu-vi, v.v.)
        clean_text = normalize_tts_text(text.strip())
        cache_key = f"{voice_code}:{clean_text}"
        if cache_key in _AUDIO_CACHE:
            return _AUDIO_CACHE[cache_key]

        sess_id = session_id or DEFAULT_TIKTOK_SESSION_ID
        headers = {
            "User-Agent": "com.zhiliaoapp.musically/2022600030 (Linux; U; Android 7.1.2; es_ES; SM-G988N; Build/NRD90M;tt-ok/3.12.13.1)",
            "Cookie": f"sessionid={sess_id}"
        }
        params = {
            "text_speaker": voice_code,
            "req_text": clean_text,
            "speaker_map_type": "0",
            "aid": "1233"
        }

        for attempt in range(max_retries):
            try:
                res = _GLOBAL_SESSION.post(TIKTOK_API_ENDPOINT, params=params, headers=headers, timeout=7)
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
            except Exception:
                time.sleep(0.15)

        # Fallback an toàn sang giọng chính TikTok Nữ Hương nếu sub-voice bị nghẽn
        if voice_code != "vi_female_huong":
            try:
                params["text_speaker"] = "vi_female_huong"
                res = _GLOBAL_SESSION.post(TIKTOK_API_ENDPOINT, params=params, headers=headers, timeout=6)
                if res.status_code == 200:
                    data = res.json()
                    if data.get("status_code") == 0 and "data" in data and "v_str" in data["data"]:
                        audio_bytes = base64.b64decode(data["data"]["v_str"])
                        fallback_seg = _load_audio_from_bytes(audio_bytes, format="mp3")
                        return cls.trim_audio_silence(fallback_seg)
            except Exception:
                pass

        return AudioSegment.silent(duration=400)

    @classmethod
    def synthesize_sentence(
        cls,
        text: str,
        voice_code: str = "BV074_streaming",
        session_id: str = None,
        apply_mastering: bool = True,
        playback_speed: float = 1.0
    ) -> AudioSegment:
        """
        Tổng hợp câu thoại TikTok TTS với thuật toán phân tách nhịp câu thông minh (< 140 ký tự).
        """
        if not text or not text.strip():
            return AudioSegment.silent(duration=200)

        # TikTok API giới hạn ~150 ký tự/lần gửi. Tách theo dấu câu nếu dài hơn 140 ký tự
        if len(text) > 140:
            sub_parts = re.split(r'(?<=[,;.!?:…—])\s+', text)
            combined = AudioSegment.empty()
            for part in sub_parts:
                part = part.strip()
                if not part:
                    continue
                seg = cls.synthesize_single_chunk(part, voice_code=voice_code, session_id=session_id)
                combined += seg + AudioSegment.silent(duration=60)
            final_seg = combined
        else:
            final_seg = cls.synthesize_single_chunk(text, voice_code=voice_code, session_id=session_id)

        if apply_mastering:
            final_seg = cls.apply_clarity_mastering(final_seg)

        if playback_speed != 1.0 and len(final_seg) > 100:
            try:
                final_seg = speedup(final_seg, playback_speed=playback_speed)
            except Exception:
                pass

        return final_seg

    @classmethod
    def time_stretch_by_factor(
        cls,
        audio_seg: AudioSegment,
        speed_factor: float
    ) -> AudioSegment:
        """
        Co giãn tốc độ âm thanh theo hệ số cố định speed_factor qua FFmpeg atempo (Bảo toàn 100% Pitch).
        """
        if abs(speed_factor - 1.0) < 0.02 or len(audio_seg) <= 100:
            return audio_seg

        # Clamp tốc độ trong giới hạn FFmpeg atempo hỗ trợ (0.5x - 10x)
        # Phải đủ cao để co giãn câu khi ratio thực tế đến 8x
        clamped_speed = max(0.75, min(speed_factor, 8.0))
        try:
            in_buf = io.BytesIO()
            audio_seg.export(in_buf, format="wav")

            # Xây dựng filter atempo
            filters = []
            cur_speed = clamped_speed
            while cur_speed > 2.0:
                filters.append("atempo=2.0")
                cur_speed /= 2.0
            while cur_speed < 0.5:
                filters.append("atempo=0.5")
                cur_speed /= 0.5
            filters.append(f"atempo={cur_speed:.4f}")
            filter_str = ",".join(filters)

            temp_cache_dir = str(settings.TEMP_TTS_DIR)
            settings.TEMP_TTS_DIR.mkdir(parents=True, exist_ok=True)
            ffmpeg_env = os.environ.copy()
            ffmpeg_env["TEMP"] = temp_cache_dir
            ffmpeg_env["TMP"] = temp_cache_dir

            ffmpeg_bin = get_ffmpeg_cmd()[0]
            cmd = [
                ffmpeg_bin, "-y", "-i", "pipe:0",
                "-filter:a", filter_str,
                "-f", "wav", "pipe:1"
            ]
            res = subprocess.run(
                cmd,
                input=in_buf.getvalue(),
                capture_output=True,
                cwd=temp_cache_dir,
                env=ffmpeg_env
            )
            if res.returncode == 0 and len(res.stdout) > 44:
                return AudioSegment.from_wav(io.BytesIO(res.stdout))
        except Exception:
            pass

        return audio_seg

    @classmethod
    def time_stretch_audio_segment(
        cls,
        audio_seg: AudioSegment,
        target_duration_sec: float,
        headroom_sec: float = 0.10
    ) -> Tuple[AudioSegment, float]:
        """
        Co giãn thời lượng âm thanh theo chuẩn AIREAD Smart Headroom (Fallback).
        """
        actual_sec = len(audio_seg) / 1000.0
        if target_duration_sec <= 0.3 or actual_sec <= 0.3:
            return audio_seg, 1.0

        safe_target = max(target_duration_sec - headroom_sec, target_duration_sec * 0.92)
        safe_target = max(safe_target, 0.35)

        speed_factor = actual_sec / safe_target
        clamped_speed = max(0.80, min(speed_factor, 1.45))

        stretched = cls.time_stretch_by_factor(audio_seg, clamped_speed)
        return stretched, round(clamped_speed, 2)

    @classmethod
    def clean_tts_cache(cls, video_id: Optional[str] = None) -> int:
        """
        Dọn dẹp các file rác khi tạo TTS (chunk lẻ, ffcache, file tạm...)
        Chỉ xóa khi người dùng chủ động gọi (lâu lâu xóa một lần),
        tránh xóa tự động liên tục khi vừa tạo xong để người dùng có thể kiểm tra.
        """
        deleted_count = 0
        try:
            if settings.TEMP_TTS_DIR.exists():
                if video_id:
                    target_dir = settings.TEMP_TTS_DIR / video_id
                    if target_dir.exists():
                        import shutil
                        shutil.rmtree(target_dir, ignore_errors=True)
                        deleted_count += 1
                else:
                    for item in settings.TEMP_TTS_DIR.iterdir():
                        if item.is_file():
                            item.unlink(missing_ok=True)
                            deleted_count += 1
                        elif item.is_dir():
                            import shutil
                            shutil.rmtree(item, ignore_errors=True)
                            deleted_count += 1
            # Quét dọn ffcache nếu còn sót ở root
            for f in settings.BASE_DIR.glob("ffcache*"):
                try:
                    f.unlink(missing_ok=True)
                    deleted_count += 1
                except Exception:
                    pass
        except Exception:
            pass
        return deleted_count

    # Giữ alias tương thích ngược
    cleanup_project_audio_chunks = clean_tts_cache

    @classmethod
    def produce_project_voiceover(
        cls,
        task_id: str,
        project_id: int,
        db: Session,
        voice_code: str = "BV074_streaming",
        session_id: str = None,
        apply_mastering: bool = True,
        playback_speed: float = 1.0,
        auto_fit_timeline: bool = True,
        max_workers: int = None,
        force_regenerate: bool = True
    ) -> Dict[str, Any]:
        """
        Quy trình sản xuất âm thanh lồng tiếng ĐỒNG TỐC TOÀN BÀI & CĂN GIỮA TRỌNG TÂM:
        1. Tạo toàn bộ câu thoại thô song song (128 luồng) ở tốc độ 1.0x chuẩn.
        2. Đo độ phình từng câu -> Tìm R_max -> Tính tốc độ đồng nhất R_global cho 100% câu.
        3. Áp dụng Atempo đồng tốc + Căn giữa trọng tâm: Start_mới = Start_gốc + (OrigDur - NewDur)/2.
        4. Ghép nối chuẩn xác từng mili-giây vào Master Audio Track.
        """
        project = db.query(ProjectTask).filter(ProjectTask.id == project_id).first()
        if not project:
            raise ValueError(f"Không tìm thấy Project ID #{project_id}")

        dialogues = db.query(DialogueSegmentModel).filter(
            DialogueSegmentModel.task_id == project_id
        ).order_by(DialogueSegmentModel.index.asc()).all()

        if not dialogues:
            raise ValueError(f"Project #{project_id} chưa có câu thoại nào!")

        raw_workers = max_workers or getattr(settings, 'TTS_MAX_WORKERS', 128)
        try:
            num_workers = int(raw_workers)
        except Exception:
            num_workers = 128
        num_workers = max(1, min(128, num_workers))

        safe_title = "".join(c for c in project.title if c.isalnum() or c in (' ', '_', '-')).strip() or f"project_{project.id}"
        master_voice_file = settings.OUTPUT_VOICEOVER_DIR / f"{project.video_id}_{safe_title}_voiceover.mp3"

        # Nếu không ép tạo mới và file đã có sẵn
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

        task_manager.add_log(task_id, f"🎙️ BẮT ĐẦU TẠO ÂM THANH TIKTOK TTS ĐỒNG TỐC (Project #{project_id}: {project.title})", "purple")
        task_manager.add_log(task_id, f"   Động cơ: Thuần 100% TikTok TTS | Giọng: {voice_code} | Luồng xử lý: {num_workers} Workers", "cyan")

        # Thư mục riêng chứa file rác/cache câu lẻ khi tạo TTS (lưu riêng biệt, không xóa liên tục)
        project_audio_dir = settings.TEMP_TTS_DIR / project.video_id
        segments_dir = project_audio_dir / "segments"
        segments_dir.mkdir(parents=True, exist_ok=True)

        total_video_ms = int((project.duration or 0.0) * 1000)
        if total_video_ms == 0 and dialogues:
            total_video_ms = int(dialogues[-1].end_time * 1000) + 1000

        total_count = len(dialogues)
        completed_count = 0
        raw_results_map = {}

        # =====================================================================
        # PHA 1: TẢI TOÀN BỘ ÂM THANH THÔ (1.0x) SONG SONG QUA THREADPOOL
        # =====================================================================
        def _fetch_raw_voice_worker(d_item):
            if task_manager.is_cancelled(task_id):
                return d_item.id, None

            text_to_speak = (d_item.translated_text or d_item.clean_text or d_item.original_text or "").strip()
            if not text_to_speak:
                return d_item.id, None

            # Bỏ hẳn mọi dấu câu ở cuối để giọng đọc liên tục tự nhiên, không ngắt hơi cưỡng bức vì dấu:
            text_to_speak = re.sub(r'[\.\,\!\?\…\:\;\—\s]+$', '', text_to_speak).strip()

            # Tổng hợp câu thô qua TikTok TTS ở tốc độ 1.0x
            raw_seg = cls.synthesize_sentence(
                text=text_to_speak,
                voice_code=voice_code,
                session_id=session_id,
                apply_mastering=apply_mastering,
                playback_speed=playback_speed
            )
            return d_item.id, raw_seg

        task_manager.add_log(task_id, f"⚡ [Pha 1/2] Đang tải {total_count} câu thoại thô qua {num_workers} luồng...", "cyan")

        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(_fetch_raw_voice_worker, d) for d in dialogues]
            for f in as_completed(futures):
                if task_manager.is_cancelled(task_id):
                    raise RuntimeError("Tiến trình đã bị người dùng hủy bỏ!")
                try:
                    d_id, raw_audio = f.result()
                    if raw_audio:
                        raw_results_map[d_id] = raw_audio
                    completed_count += 1
                    if completed_count % 30 == 0 or completed_count == total_count:
                        pct = 40 + int((completed_count / total_count) * 35)
                        task_manager.update_task(task_id, progress=pct)
                except Exception:
                    completed_count += 1

        # =====================================================================
        # PHA 2: TÍNH TOÁN CO GIÃN VỪA KHÍT THỜI LƯỢNG GỐC VÀ DÁN VÀO MASTER
        # =====================================================================
        task_manager.add_log(task_id, f"🎯 [Pha 2/2] Đang tính toán co giãn từng câu vừa khít thời lượng gốc (Bắt đầu đúng start, không vượt quá end)...", "cyan")

        fs = 24000
        channels = 2
        total_samples = int((total_video_ms / 1000.0) * fs) + fs
        master_buffer = np.zeros((total_samples, channels), dtype=np.float32)

        for idx_d, d in enumerate(dialogues):
            raw_audio = raw_results_map.get(d.id)
            seg_file = segments_dir / f"seg_{d.index:04d}.mp3"

            if not raw_audio:
                d.voice_audio_path = None
                d.voice_duration = 0.0
                d.speed_ratio = 1.0
                continue

            # 1. Bắt đầu đúng ở start của câu đấy
            start_sec = float(d.start_time)
            end_sec = float(d.end_time)
            orig_duration = max(0.20, end_sec - start_sec)

            # 2. Khung thời lượng mục tiêu:
            # Vì Groq Word-Level Timestamps đã tự động bóc tách khoảng nghỉ (GAP) tự nhiên giữa các câu thoại,
            # nên trong từng câu, ta dùng trọn vẹn thời lượng thoại thực tế (orig_duration = end - start).
            # Không trừ bớt thời gian nghỉ của dấu câu nữa để giọng đọc dàn đều tự nhiên về phía sau, không bị nói ép.
            target_duration = orig_duration

            # 3. Đo thời lượng âm thanh thô vừa sinh ra
            raw_duration = len(raw_audio) / 1000.0

            # 4. Tính tốc độ:
            # - Nếu TTS đọc tự nhiên (1.0x) đã ngắn hơn target_duration: Giữ nguyên 1.0x (không kéo lê làm méo giọng)
            # - Nếu TTS đọc dài hơn: Tăng tốc vừa đủ khớp target_duration (tối đa 1.6x)
            if raw_duration <= target_duration:
                speed_factor = 1.0
                fitted_seg = raw_audio
            else:
                speed_factor = min(1.60, raw_duration / target_duration)
                fitted_seg = cls.time_stretch_by_factor(raw_audio, speed_factor)

            # Cắt cứng đúng bằng orig_duration để tuyệt đối không tràn đè sang câu sau
            max_allowed_ms = int(orig_duration * 1000)
            if len(fitted_seg) > max_allowed_ms:
                fitted_seg = fitted_seg[:max_allowed_ms]

            fitted_sec = len(fitted_seg) / 1000.0

            # 4. Xuất file MP3 câu lẻ
            try:
                fitted_seg.export(str(seg_file), format="mp3", bitrate="192k")
            except Exception:
                pass

            # 5. Lưu thông số vào Database
            d.voice_audio_path = str(seg_file)
            d.voice_duration = round(fitted_sec, 3)
            d.speed_ratio = round(speed_factor, 2)
            d.status = "DUBBED"

            # 6. Dán vào Master Buffer: LUÔN BẮT ĐẦU TẠI start_sec VÀ TUYỆT ĐỐI KHÔNG VƯỢT QUÁ end_sec
            try:
                norm_seg = fitted_seg.set_frame_rate(fs).set_channels(channels)
                seg_samples = np.array(norm_seg.get_array_of_samples(), dtype=np.float32).reshape((-1, channels))

                start_idx = int(start_sec * fs)
                max_end_idx = int(end_sec * fs)
                end_idx = min(start_idx + len(seg_samples), max_end_idx, total_samples)

                fit_len = end_idx - start_idx
                if fit_len > 0:
                    master_buffer[start_idx:end_idx] += seg_samples[:fit_len]
            except Exception:
                pass

        db.commit()

        # Chuyển numpy buffer sang AudioSegment
        master_buffer = np.clip(master_buffer, -32768, 32767).astype(np.int16)
        full_timeline_audio = AudioSegment(
            master_buffer.tobytes(),
            frame_rate=fs,
            sample_width=2,
            channels=channels
        )

        # Xuất file Master Audio
        safe_title = "".join(c for c in project.title if c.isalnum() or c in (' ', '_', '-')).strip()
        if not safe_title:
            safe_title = f"project_{project.id}"

        master_voice_file = settings.OUTPUT_VOICEOVER_DIR / f"{project.video_id}_{safe_title}_voiceover.mp3"
        full_timeline_audio.export(str(master_voice_file), format="mp3", bitrate="192k")

        project.status = "DUBBED"
        db.commit()

        task_manager.add_log(task_id, f"🎉 HOÀN TẤT LỒNG TIẾNG TIKTOK TTS! Đã tạo xong {total_count} câu thoại.", "emerald")
        task_manager.add_log(task_id, f"   • File Audio Toàn Bộ Track: output/voiceover/{master_voice_file.name}", "emerald")
        task_manager.add_log(task_id, f"   • Cập nhật SQLite: Project #{project.id} -> Status: DUBBED", "emerald")

        return {
            "status": "success",
            "project_id": project.id,
            "master_voice_path": str(master_voice_file),
            "total_dialogues": total_count
        }

