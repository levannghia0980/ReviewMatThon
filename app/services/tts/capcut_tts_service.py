import os
import io
import re
import time
import json
import logging
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
from app.services.capcut_tts_api import CapCutClient, DeviceConfig
from app.utils.bin_helper import get_ffmpeg_cmd, get_ffprobe_cmd, setup_system_path

# Đảm bảo pydub luôn trỏ chính xác vào file ffmpeg.exe tuyệt đối
try:
    setup_system_path()
    _ff_bin = get_ffmpeg_cmd()[0]
    AudioSegment.converter = _ff_bin
    AudioSegment.ffmpeg = _ff_bin
except Exception:
    pass

logger = logging.getLogger(__name__)

# Cookie phiên làm việc (mặc định lấy từ biến môi trường hoặc cookie dự phòng)
DEFAULT_CAPCUT_COOKIE = os.getenv(
    "CAPCUT_COOKIE",
    os.getenv("TIKTOK_SESSION_ID", "410bfa37bdc185e1c6da82e1afb48409")
)

# ============================================================
def normalize_tts_text(text: str) -> str:
    """
    Chuẩn hóa văn bản trước khi gửi sang CapCut TTS:
    - Loại bỏ dấu ngoặc đơn, ngoặc kép, dấu gạch nối giữa các từ (tránh TTS đọc thành 'đến').
    - Giữ nguyên tiếng Việt tự nhiên: CapCut AI đọc chuẩn các từ tu tiên (tu luyện, linh khí, đột phá, v.v.).
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
    """Tính tổng thời gian ngắt nghỉ tự nhiên của các dấu câu."""
    if not text:
        return 0.0
    t = text.strip()
    total_pause = 0.0
    ellipsis_matches = re.findall(r'\.{3,}|…', t)
    total_pause += len(ellipsis_matches) * 0.35
    t_no_ellipsis = re.sub(r'\.{3,}|…', '', t)
    terminals = re.findall(r'[.?!]', t_no_ellipsis)
    total_pause += len(terminals) * 0.25
    commas = re.findall(r'(?<!\d)[,;](?!\d)', t_no_ellipsis)
    total_pause += len(commas) * 0.18
    colons = re.findall(r'[:—\-]', t_no_ellipsis)
    total_pause += len(colons) * 0.15
    return total_pause

# Connection Pool tối ưu cho 128 luồng song song
_GLOBAL_SESSION = requests.Session()
_adapter = HTTPAdapter(
    pool_connections=128,
    pool_maxsize=128,
    max_retries=Retry(total=3, backoff_factor=0.15)
)
_GLOBAL_SESSION.mount("https://", _adapter)
_GLOBAL_SESSION.mount("http://", _adapter)

# Danh sách giọng CapCut / ByteDance SAMI chuẩn 100% đã được kiểm tra thực tế
CAPCUT_VOICES = [
    # --- Top Giọng Nữ Review Phim & Truyện Tranh Đỉnh Cao ---
    {"id": "multi_female_richgirl_uranus_bigtts", "name": "Nữ: Review Phim new (Sang trọng, Cuốn hút, Kể chuyện đỉnh cao)", "gender": "female", "lang": "vi"},
    {"id": "BV074_streaming", "name": "Nữ: Cô Gái Hoạt Ngôn (Trẻ trung, Review Trend - Khuyên dùng)", "gender": "female", "lang": "vi"},
    {"id": "vi_female_huong", "name": "Nữ: Hương (Phổ thông miền Bắc, Chuẩn Review)", "gender": "female", "lang": "vi"},
    {"id": "BV421_vivn_streaming", "name": "Nữ: Nhỏ Ngọt Ngào (Êm ái, Nhẹ nhàng, Truyền cảm)", "gender": "female", "lang": "vi"},
    {"id": "BV562_streaming", "name": "Nữ: Mai (Trầm ấm, Thanh lịch, Kể chuyện dài)", "gender": "female", "lang": "vi"},
    {"id": "multi_female_daqi_uranus_bigtts", "name": "Nữ: Review Phim 3 (Khí chất, Đĩnh đạc, Kịch tính)", "gender": "female", "lang": "vi"},
    {"id": "multi_female_stokie_uranus_bigtts", "name": "Nữ: Review Phim 4 (Cá tính, Sắc sảo, Review phim hot)", "gender": "female", "lang": "vi"},
    {"id": "multi_female_yangguangnv_uranus_bigtts", "name": "Nữ: Ban Mai (Tươi sáng, Năng lượng, Trẻ trung)", "gender": "female", "lang": "vi"},
    {"id": "multi_female_peiqi_uranus_bigtts", "name": "Nữ: Gái Mới Lớn (Đáng yêu, Dễ thương)", "gender": "female", "lang": "vi"},
    {"id": "multi_female_kiwi_uranus_bigtts", "name": "Nữ: Sunny Idol (Tươi trẻ, Hiện đại)", "gender": "female", "lang": "vi"},

    # --- Top Giọng Nam Chuẩn Thuyết Minh & Sử Thi ---
    {"id": "multi_male_felipe_uranus_bigtts", "name": "Nam: Giọng Nam Trầm (Trầm hùng, Cuốn hút, Review kiếm hiệp)", "gender": "male", "lang": "vi"},
    {"id": "BV001_streaming", "name": "Nam: Thanh Niên Tự Tin (Dứt khoát, Hùng hồn)", "gender": "male", "lang": "vi"},
    {"id": "BV075_streaming", "name": "Nam: Hào Sảng (Trầm ấm, Thuyết minh phim)", "gender": "male", "lang": "vi"},
    {"id": "BV078_streaming", "name": "Nam: Trầm Ổn (Đĩnh đạc, Phóng sự)", "gender": "male", "lang": "vi"},
    {"id": "BV071_streaming", "name": "Nam: Trẻ Trung Năng Động", "gender": "male", "lang": "vi"},
    {"id": "BV072_streaming", "name": "Nam: Kể Chuyện Huyền Bí, Ma Mị", "gender": "male", "lang": "vi"},
    {"id": "BV560_streaming", "name": "Nam: Alex Đại Đế (Hùng tráng, Sử thi)", "gender": "male", "lang": "vi"},
]

_AUDIO_CACHE: Dict[str, AudioSegment] = {}

class CapCutTTSService:
    """
    Dịch vụ Text-to-Speech siêu tốc từ CapCut Cloud API (ByteDance SAMI Engine):
    - Đa luồng song song lên tới 128 luồng đồng thời.
    - Cơ chế 'Lách' Rate Limit: Tự động xoay vòng Device ID ngẫu nhiên (DeviceConfig.create_random()).
    - Kích thước chunk phù hợp: Hỗ trợ đoạn dài 250 - 300 ký tự (vượt trội so với 140 ký tự của TikTok).
    - Hỗ trợ tải trực tiếp từ CDN ByteDance, cache RAM và xử lý làm rõ tiếng (Mastering EQ).
    - Đồng tốc toàn bài (Uniform Speed) và căn giữa trọng tâm (Center Alignment).
    """

    @staticmethod
    def get_supported_voices() -> List[Dict[str, str]]:
        return CAPCUT_VOICES

    @staticmethod
    def apply_clarity_mastering(
        audio_seg: AudioSegment,
        boost_clarity_db: float = 3.5,
        de_harsh: bool = True
    ) -> AudioSegment:
        """Làm rõ chữ, tăng nét dải phụ âm tiếng Việt (3200Hz) & cắt tần số gắt."""
        try:
            samples = np.array(audio_seg.get_array_of_samples()).astype(np.float32)
            fs = audio_seg.frame_rate
            channels = audio_seg.channels
            if channels == 2:
                samples = samples.reshape((-1, 2))

            # 1. Cắt sub-bass < 80Hz
            sos_hp = signal.butter(2, 80, btype='highpass', fs=fs, output='sos')
            samples = signal.sosfilt(sos_hp, samples, axis=0)

            # 2. Peak EQ: Tăng độ nét phụ âm tiếng Việt ở 3200Hz
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

            b = [b0 / a0, b1 / a0, b2 / a0]
            a = [1.0, a1 / a0, a2 / a0]
            samples = signal.lfilter(b, a, samples, axis=0)

            # 3. De-harsh: Cắt tần số gắt trên 8500Hz
            if de_harsh:
                sos_lp = signal.butter(2, 8500, btype='lowpass', fs=fs, output='sos')
                samples = signal.sosfilt(sos_lp, samples, axis=0)

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
            trimmed = seg[max(0, start_trim - 15): max(0, duration - end_trim + 15)]
            return trimmed if len(trimmed) >= 100 else seg
        except Exception:
            return seg

    @classmethod
    def synthesize_single_chunk(
        cls,
        text: str,
        voice_code: str = "BV074_streaming",
        cookie: str = None,
        max_retries: int = 3
    ) -> AudioSegment:
        """
        Tổng hợp 1 chunk câu thoại ngắn qua CapCut API.
        Lách giới hạn: Mỗi lần gọi sinh 1 DeviceConfig ngẫu nhiên, tái sử dụng connection pool.
        """
        if not text or not text.strip():
            return AudioSegment.silent(duration=200)

        clean_text = normalize_tts_text(text.strip())
        cache_key = f"capcut:{voice_code}:{clean_text}"
        if cache_key in _AUDIO_CACHE:
            return _AUDIO_CACHE[cache_key]

        user_cookie = cookie or DEFAULT_CAPCUT_COOKIE

        for attempt in range(max_retries):
            try:
                # Kỹ thuật lách rate limit: Dynamic Random Device ID
                dev = DeviceConfig.create_random()
                client = CapCutClient(device=dev, session=_GLOBAL_SESSION, cookie=user_cookie)

                res = client.generate_speech(
                    texts=[clean_text],
                    voice=voice_code,
                    rate="1.0",
                    timeout=15.0
                )
                tasks = (res.get("data") or {}).get("tasks") or []
                if not tasks:
                    time.sleep(0.15)
                    continue

                raw_payload = tasks[0].get("payload", "{}")
                payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
                subs = payload.get("audio_subtitles") or []
                if not subs:
                    time.sleep(0.15)
                    continue

                speech_url = subs[0].get("speech_url")
                if not speech_url:
                    time.sleep(0.15)
                    continue

                # Tải trực tiếp file MP3 từ CDN
                dl_res = _GLOBAL_SESSION.get(speech_url, timeout=12)
                if dl_res.status_code == 200 and len(dl_res.content) > 200:
                    seg = AudioSegment.from_file(io.BytesIO(dl_res.content), format="mp3")
                    seg = cls.trim_audio_silence(seg)
                    if len(seg) > 100:
                        _AUDIO_CACHE[cache_key] = seg
                        return seg

                time.sleep(0.15)
            except Exception as exc:
                logger.warning(f"[CapCut TTS] Thử lần {attempt + 1} thất bại cho câu: {clean_text[:30]}... ({exc})")
                time.sleep(0.15)

        # Fallback an toàn sang giọng chuẩn BV074_streaming nếu sub-voice nghẽn
        if voice_code != "BV074_streaming":
            try:
                dev = DeviceConfig.create_random()
                client = CapCutClient(device=dev, session=_GLOBAL_SESSION, cookie=user_cookie)
                res = client.generate_speech([clean_text], voice="BV074_streaming", timeout=12.0)
                tasks = (res.get("data") or {}).get("tasks") or []
                if tasks:
                    payload = json.loads(tasks[0].get("payload", "{}"))
                    subs = payload.get("audio_subtitles") or []
                    if subs and subs[0].get("speech_url"):
                        dl_res = _GLOBAL_SESSION.get(subs[0]["speech_url"], timeout=10)
                        if dl_res.status_code == 200:
                            fb_seg = AudioSegment.from_file(io.BytesIO(dl_res.content), format="mp3")
                            return cls.trim_audio_silence(fb_seg)
            except Exception:
                pass

        return AudioSegment.silent(duration=400)

    @classmethod
    def synthesize_sentence(
        cls,
        text: str,
        voice_code: str = "BV074_streaming",
        cookie: str = None,
        apply_mastering: bool = True,
        playback_speed: float = 1.0
    ) -> AudioSegment:
        """
        Tổng hợp câu thoại CapCut TTS với chia chunk tối ưu (250 - 300 ký tự).
        """
        if not text or not text.strip():
            return AudioSegment.silent(duration=200)

        # CapCut xử lý tốt các câu đến 280 ký tự. Chỉ tách nếu câu dài hơn 280 ký tự.
        if len(text) > 280:
            sub_parts = re.split(r'(?<=[,;.!?:…—])\s+', text)
            combined = AudioSegment.empty()
            cur_part = ""
            for p in sub_parts:
                p = p.strip()
                if not p:
                    continue
                if len(cur_part) + len(p) < 260:
                    cur_part = f"{cur_part} {p}".strip()
                else:
                    if cur_part:
                        seg = cls.synthesize_single_chunk(cur_part, voice_code=voice_code, cookie=cookie)
                        combined += seg + AudioSegment.silent(duration=60)
                    cur_part = p
            if cur_part:
                seg = cls.synthesize_single_chunk(cur_part, voice_code=voice_code, cookie=cookie)
                combined += seg
            final_seg = combined if len(combined) > 100 else AudioSegment.silent(duration=300)
        else:
            final_seg = cls.synthesize_single_chunk(text, voice_code=voice_code, cookie=cookie)

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
        """Co giãn tốc độ âm thanh qua FFmpeg atempo (Bảo toàn 100% cao độ Pitch)."""
        if abs(speed_factor - 1.0) < 0.02 or len(audio_seg) <= 100:
            return audio_seg

        clamped_speed = max(0.75, min(speed_factor, 8.0))
        try:
            in_buf = io.BytesIO()
            audio_seg.export(in_buf, format="wav")

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

            cmd = [
                "ffmpeg", "-y", "-i", "pipe:0",
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
            if res.returncode == 0 and len(res.stdout) > 0:
                return AudioSegment.from_file(io.BytesIO(res.stdout), format="wav")
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
        """Co giãn thời lượng âm thanh theo Smart Headroom."""
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
        """Dọn dẹp file tạm khi tạo TTS."""
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
            for f in settings.BASE_DIR.glob("ffcache*"):
                try:
                    f.unlink(missing_ok=True)
                    deleted_count += 1
                except Exception:
                    pass
        except Exception:
            pass
        return deleted_count

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
        Quy trình sản xuất âm thanh CapCut TTS ĐỒNG TỐC TOÀN BÀI & CĂN GIỮA TRỌNG TÂM:
        - Sử dụng ThreadPoolExecutor chạy song song lên tới 128 luồng.
        - Mỗi luồng tự động xoay Device ID ngẫu nhiên để vượt qua Rate Limit của CapCut.
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

        task_manager.add_log(task_id, f"🎙️ BẮT ĐẦU TẠO ÂM THANH CAPCUT CLOUD TTS (Project #{project_id}: {project.title})", "purple")
        task_manager.add_log(task_id, f"   Động cơ: CapCut Cloud TTS (ByteDance SAMI) | Giọng: {voice_code} | Luồng xử lý: {num_workers} Workers (Device Randomization)", "cyan")

        project_audio_dir = settings.TEMP_TTS_DIR / project.video_id
        segments_dir = project_audio_dir / "segments"
        segments_dir.mkdir(parents=True, exist_ok=True)

        total_video_ms = int((project.duration or 0.0) * 1000)
        if total_video_ms == 0 and dialogues:
            total_video_ms = int(dialogues[-1].end_time * 1000) + 1000

        total_count = len(dialogues)
        raw_results_map = {}

        # =====================================================================
        # PHA 1: TẢI TOÀN BỘ ÂM THANH THÔ (1.0x) SONG SONG QUA THREADPOOL (128 LUỒNG)
        # =====================================================================
        def _fetch_raw_voice_worker(d_item):
            if task_manager.is_cancelled(task_id):
                return d_item.id, None

            text_to_speak = (d_item.translated_text or d_item.clean_text or d_item.original_text or "").strip()
            if not text_to_speak:
                return d_item.id, None

            # Bỏ hẳn mọi dấu câu ở cuối để giọng đọc liên tục tự nhiên, không ngắt hơi cưỡng bức vì dấu:
            text_to_speak = re.sub(r'[\.\,\!\?\…\:\;\—\s]+$', '', text_to_speak).strip()

            raw_seg = cls.synthesize_sentence(
                text=text_to_speak,
                voice_code=voice_code,
                cookie=session_id,
                apply_mastering=apply_mastering,
                playback_speed=playback_speed
            )
            return d_item.id, raw_seg

        task_manager.add_log(task_id, f"⚡ [Pha 1/2] Đang tải {total_count} câu thoại thô qua {num_workers} luồng song song (CapCut Cloud)...", "cyan")

        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(_fetch_raw_voice_worker, d) for d in dialogues]
            for f in as_completed(futures):
                if task_manager.is_cancelled(task_id):
                    raise RuntimeError("Tiến trình đã bị người dùng hủy bỏ!")
                try:
                    d_id, seg = f.result()
                    if seg and len(seg) > 100:
                        raw_results_map[d_id] = seg
                except Exception as ex:
                    logger.error(f"[CapCut TTS Worker Error] {ex}")

        valid_count = len(raw_results_map)
        task_manager.add_log(task_id, f"✔ Hoàn tất tải âm thanh thô: {valid_count}/{total_count} câu thoại hợp lệ.", "emerald")

        if valid_count == 0:
            raise RuntimeError("Không tải được câu thoại nào từ CapCut TTS!")

        # =====================================================================
        # PHA 2: KHỚP TRỰC TIẾP VÀO TIMELINE GỐC & CO GIÃN THÍCH ỨNG THEO TỪNG CÂU
        # =====================================================================
        task_manager.add_log(task_id, "⚙️ [Pha 2/2] Khớp trực tiếp mốc thời gian CapCut & Co giãn thích ứng (Adaptive Fitting)...", "cyan")

        master_track = AudioSegment.silent(duration=total_video_ms + 2000)

        for idx_d, d in enumerate(dialogues):
            raw_seg = raw_results_map.get(d.id)
            if not raw_seg:
                continue

            raw_dur_sec = len(raw_seg) / 1000.0
            next_start_time = dialogues[idx_d + 1].start_time if idx_d + 1 < len(dialogues) else (d.end_time + 1.0)
            # Đệm an toàn siêu nhỏ 50ms chỉ để chống đè âm sang câu kế tiếp
            max_allowed_dur = max(0.15, (next_start_time - d.start_time) - 0.05)

            # Chỉ co giãn nếu câu này dài hơn khung thời gian cho phép.
            # Câu bình thường giữ nguyên 1.0x tự nhiên, không ép tốc độ làm hở khoảng lặng!
            if auto_fit_timeline and raw_dur_sec > max_allowed_dur:
                compress_factor = min(1.40, raw_dur_sec / max_allowed_dur)
                fitted_seg = cls.time_stretch_by_factor(raw_seg, compress_factor)
                actual_speed = compress_factor
            else:
                fitted_seg = raw_seg
                actual_speed = 1.0

            seg_dur_sec = len(fitted_seg) / 1000.0

            # Khớp hoàn toàn câu mới vào time câu cũ: Bắt đầu chính xác tại d.start_time (không delay offset)
            start_ms = int(d.start_time * 1000)

            # Cập nhật thông số chuẩn xác vào CSDL
            d.voice_duration = round(seg_dur_sec, 3)
            d.speed_ratio = round(actual_speed, 2)
            d.status = "DUBBED"

            # Ghi đè vào master track
            master_track = master_track.overlay(fitted_seg, position=start_ms)

        # Lưu thay đổi thông số voice_duration vào CSDL
        db.commit()

        # Xuất file âm thanh tổng hợp
        settings.OUTPUT_VOICEOVER_DIR.mkdir(parents=True, exist_ok=True)
        master_track.export(str(master_voice_file), format="mp3", bitrate="192k")

        task_manager.add_log(task_id, f"🎉 ĐÃ XUẤT MASTER VOICEOVER THÀNH CÔNG: {master_voice_file.name}", "emerald")

        project.status = "DUBBED"
        db.commit()

        return {
            "status": "success",
            "project_id": project.id,
            "audio_path": str(master_voice_file),
            "master_voice_path": str(master_voice_file),
            "total_dialogues": len(dialogues),
            "engine": "capcut",
            "global_speed_ratio": 1.0
        }
