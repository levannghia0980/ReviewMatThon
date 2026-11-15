import os
from typing import Optional
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"

# Tải cấu hình từ .env nếu có
if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH, override=True)
else:
    load_dotenv()

# 1. Thư mục INPUT (Lưu trữ file đầu vào)
INPUT_DIR = BASE_DIR / "input"
INPUT_VIDEOS_DIR = INPUT_DIR / "videos"
INPUT_AUDIO_DIR = INPUT_DIR / "audio_raw"

# 2. Thư mục OUTPUT (Lưu trữ kết quả xử lý đa tầng chuẩn AIREAD)
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_PROJECTS_DIR = OUTPUT_DIR / "projects"
OUTPUT_FINAL_VIDEOS_DIR = OUTPUT_DIR / "final_videos"
TEMP_DIR = BASE_DIR / "temp"

# Tương thích ngược các folder cũ
OUTPUT_TRANSCRIPTS_DIR = OUTPUT_DIR / "transcripts"
OUTPUT_RAW_CLEANED_DIR = OUTPUT_DIR / "01_raw_cleaned"
OUTPUT_ENTITIES_DIR = OUTPUT_DIR / "02_entities"
OUTPUT_DICH_AI_LLM_DIR = OUTPUT_DIR / "03_dich_ai_llm"
OUTPUT_POST_PROCESSED_DIR = OUTPUT_DIR / "04_post_processed"
OUTPUT_TRANSLATIONS_DIR = OUTPUT_DIR / "translations"
OUTPUT_VOICEOVER_DIR = OUTPUT_DIR / "voiceover"
TEMP_TTS_DIR = TEMP_DIR / "tts_cache"


def get_project_dirs(project_id: int) -> dict:
    """
    Trả về cấu trúc thư mục chuẩn mực theo số thứ tự ID trong Database:
    output/projects/project_{id}/
      ├── entities/     (Folder con lưu thực thể từng lô và toàn bộ truyện)
      └── llm_io/       (Folder con lưu đầu vào / đầu ra LLM)

    Tất cả file rác, file đệm tạm thời gom chung về TEMP_DIR tổng để tiện dọn dẹp định kỳ 1 chỗ.
    """
    base_proj = OUTPUT_PROJECTS_DIR / f"project_{project_id}"
    entities_dir = base_proj / "entities"
    llm_io_dir = base_proj / "llm_io"
    tts_voiceover_dir = base_proj / "tts_voiceover"
    video_subtitles_dir = base_proj / "video_subtitles"

    for d in [base_proj, entities_dir, llm_io_dir, tts_voiceover_dir, video_subtitles_dir]:
        d.mkdir(parents=True, exist_ok=True)

    return {
        "root": base_proj,
        "entities": entities_dir,
        "llm_io": llm_io_dir,
        "tts_voiceover": tts_voiceover_dir,
        "video_subtitles": video_subtitles_dir,
        "temp": TEMP_DIR,
        "final_videos": OUTPUT_FINAL_VIDEOS_DIR
    }


# 4. Thư mục DATABASE (SQLite)
DB_DIR = BASE_DIR / "database"
DB_PATH = DB_DIR / "review_mat_thon.db"

# 5. Thư mục ASSETS (Từ điển, fonts, banner, khung...)
ASSETS_DIR = BASE_DIR / "assets"
ASSETS_DICT_DIR = ASSETS_DIR / "dictionaries"
ASSETS_FRAMES_DIR = ASSETS_DIR / "frames"
FRAME_CANVAS_PATH = ASSETS_FRAMES_DIR / "frame_matthon_cyber_silver.png"

# 6. Thư mục TOOLS (FFmpeg, yt-dlp, aria2c nhúng)
TOOLS_DIR = BASE_DIR / "tools"

# Tự động khởi tạo tất cả thư mục
ALL_DIRS = [
    INPUT_VIDEOS_DIR,
    INPUT_AUDIO_DIR,
    OUTPUT_PROJECTS_DIR,
    OUTPUT_FINAL_VIDEOS_DIR,
    TEMP_DIR,
    TEMP_TTS_DIR,
    DB_DIR,
    ASSETS_DIR,
    ASSETS_DICT_DIR,
    ASSETS_FRAMES_DIR,
    TOOLS_DIR
]

for d in ALL_DIRS:
    d.mkdir(parents=True, exist_ok=True)

try:
    from app.utils.bin_helper import setup_system_path
    setup_system_path()
except Exception:
    pass

_BOOL_DEFAULTS = {
    "TTS_MERGE_CHUNKS": False,
    "GEMINI_WEB_HEADLESS": False,
    "IS_SERVER_HOST": False,
    "ENABLE_VISUAL_SUB_REFINER": False,
}
_INT_DEFAULTS = {
    "ASR_MAX_WORKERS": 10,
    "STT_TIME_OFFSET_MS": 30,
    "TTS_MAX_WORKERS": 128,
    "TRANSLATION_BATCH_SIZE": 500,
    "TRANSLATION_MAX_CHARS": 50000,
    "GEMINI_THINKING_BUDGET": 8192,
    "GEMINI_TOP_K": 40,
    "VIDEO_RENDER_WORKERS": 0,
    "VIDEO_SEGMENT_DURATION": 300,
    "VIDEO_SINGLE_CHUNK_LIMIT": 1200,
    "OCR_WORKERS": 0,
    "OCR_CHUNK_SECONDS": 0,
}
_FLOAT_DEFAULTS = {
    "TRANSLATION_TEMPERATURE": 0.25,
    "GEMINI_TOP_P": 0.95,
    "GEMINI_REQUEST_TIMEOUT": 300.0,
    "GEMINI_PACING_INTERVAL": 4.1,
}


def _parse_env_value(key: str, raw: Optional[str]):
    """Chuyển đổi giá trị cấu hình .env sang đúng kiểu dữ liệu với fallback an toàn cho chuỗi rỗng."""
    val = "" if raw is None else str(raw).strip()
    if key in _BOOL_DEFAULTS:
        return val.lower() in ("true", "1", "yes") if val else _BOOL_DEFAULTS[key]
    if key in _FLOAT_DEFAULTS:
        if not val:
            return _FLOAT_DEFAULTS[key]
        try:
            return float(val)
        except Exception:
            return _FLOAT_DEFAULTS[key]
    if key in _INT_DEFAULTS:
        if not val:
            return _INT_DEFAULTS[key]
        try:
            return int(val)
        except Exception:
            return _INT_DEFAULTS[key]
    if key == "OCR_HWACCEL":
        return (val or "auto").lower()
    if val.isdigit() or (val.startswith("-") and val[1:].isdigit()):
        try:
            return int(val)
        except Exception:
            return val
    return val


def _env_str(key: str, default: str = "") -> str:
    val = os.getenv(key)
    return default if val is None or not str(val).strip() else str(val).strip()


def _env_bool(key: str) -> bool:
    return _parse_env_value(key, os.getenv(key))


def _env_int(key: str) -> int:
    return _parse_env_value(key, os.getenv(key))


def _env_float(key: str) -> float:
    return _parse_env_value(key, os.getenv(key))


class Settings:
    PROJECT_NAME: str = "ReviewMatThon Video Dubbing & Processing Backend"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    
    # SQLite Database URL
    DATABASE_URL: str = f"sqlite:///{DB_PATH}"
    
    # Chế độ máy chủ phát triển
    IS_SERVER_HOST: bool = _env_bool("IS_SERVER_HOST")

    # ASR Engine Selection: "capcut" | "groq" | "faster_whisper"
    ASR_ENGINE: str = _env_str("ASR_ENGINE", "capcut")
    
    # Groq API Configuration
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "").strip()
    GROQ_MODEL: str = _env_str("GROQ_MODEL", "whisper-large-v3")
    
    # Faster-Whisper Model Settings (Offline)
    WHISPER_MODEL_SIZE: str = _env_str("WHISPER_MODEL_SIZE", "base")
    WHISPER_DEVICE: str = _env_str("WHISPER_DEVICE", "auto")
    WHISPER_COMPUTE_TYPE: str = _env_str("WHISPER_COMPUTE_TYPE", "int8")
    
    # Gemini API Settings (Khóa chuẩn thông số tối ưu cho 2 model Lite qua Gateway)
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "").strip()
    GEMINI_MODEL: str = _env_str("GEMINI_MODEL", "gemini-3.1-flash-lite")
    GEMINI_THINKING_BUDGET: int = _env_int("GEMINI_THINKING_BUDGET")
    GEMINI_TOP_P: float = _env_float("GEMINI_TOP_P")
    GEMINI_TOP_K: int = _env_int("GEMINI_TOP_K")
    GEMINI_REQUEST_TIMEOUT: float = _env_float("GEMINI_REQUEST_TIMEOUT")
    GEMINI_PACING_INTERVAL: float = _env_float("GEMINI_PACING_INTERVAL")
    
    # OpenRouter API Settings
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "").strip()
    OPENROUTER_MODEL: str = _env_str("OPENROUTER_MODEL", "google/gemini-2.5-flash")

    # Translation Settings (Lô lớn 200 - 500 câu theo chuẩn AIRead)
    TRANSLATION_PROVIDER: str = _env_str("TRANSLATION_PROVIDER", "gemini")
    GEMINI_WEB_MODEL: str = _env_str("GEMINI_WEB_MODEL", "flash")
    GEMINI_WEB_THINKING: str = _env_str("GEMINI_WEB_THINKING", "low")
    GEMINI_WEB_HEADLESS: bool = _env_bool("GEMINI_WEB_HEADLESS")
    TRANSLATION_GENRE: str = _env_str("TRANSLATION_GENRE", "tienhiep")
    TRANSLATION_BATCH_SIZE: int = _env_int("TRANSLATION_BATCH_SIZE")
    TRANSLATION_MAX_CHARS: int = _env_int("TRANSLATION_MAX_CHARS")
    TRANSLATION_TEMPERATURE: float = _env_float("TRANSLATION_TEMPERATURE")
    
    # STT ASR Settings (Nhận diện giọng nói đa luồng song song: 6 - 10 luồng tối ưu tốc độ)
    ASR_MAX_WORKERS: int = _env_int("ASR_MAX_WORKERS")
    # Độ trễ thời gian STT (mili-giây) trừ về trước để khớp giọng đọc với thoại gốc (Chuẩn 30ms)
    STT_TIME_OFFSET_MS: int = _env_int("STT_TIME_OFFSET_MS")
    # Tự động quét phụ đề cứng trên video để gộp câu & sửa đồng âm CapCut (Mặc định TẮT vì chậm và ít tác dụng thực tế; có thể bật lại trong Settings)
    ENABLE_VISUAL_SUB_REFINER: bool = _env_bool("ENABLE_VISUAL_SUB_REFINER")
    OCR_WORKERS: int = _env_int("OCR_WORKERS")
    OCR_CHUNK_SECONDS: int = _env_int("OCR_CHUNK_SECONDS")
    OCR_HWACCEL: str = _env_str("OCR_HWACCEL", "auto").lower()

    # TTS Voiceover Settings: "capcut" (CapCut Cloud SAMI - Khuyên dùng) | "tiktok" (TikTok Legacy)
    TTS_ENGINE: str = _env_str("TTS_ENGINE", "capcut")
    TTS_MAX_WORKERS: int = _env_int("TTS_MAX_WORKERS")
    CAPCUT_COOKIE: str = os.getenv("CAPCUT_COOKIE", "").strip()
    TIKTOK_SESSION_ID: str = _env_str("TIKTOK_SESSION_ID", "410bfa37bdc185e1c6da82e1afb48409")
    PROXY_LIST: str = os.getenv("PROXY_LIST", "").strip()
    
    # =========================================================================
    # CỜ CẤU HÌNH GỘP CÂU TTS (TTS CHUNK MERGING FLAG):
    # - False (MẶC ĐỊNH TEST): KHÔNG GỘP CÂU - Tôn trọng 100% từng câu LLM dịch.
    #   Mỗi câu LLM là 1 chunk TTS riêng biệt, triệt tiêu nguy cơ CapCut nuốt chữ khi chunk quá dài.
    # - True (KHÔI PHỤC LẠI): Bật gộp các câu thoại ngắn liên tiếp của cùng 1 nhân vật (<0.85s).
    # =========================================================================
    TTS_MERGE_CHUNKS: bool = _env_bool("TTS_MERGE_CHUNKS")
    
    # Render Video Settings (Render phân đoạn song song thích ứng phần cứng; 0 = auto-detect)
    VIDEO_RENDER_WORKERS: int = _env_int("VIDEO_RENDER_WORKERS")
    VIDEO_SEGMENT_DURATION: int = _env_int("VIDEO_SEGMENT_DURATION")
    VIDEO_SINGLE_CHUNK_LIMIT: int = _env_int("VIDEO_SINGLE_CHUNK_LIMIT")

    # Downloader / Bilibili Cookie Settings (Không dùng proxy, hỗ trợ cookie file để tải HD 1080p 60fps)
    BILIBILI_COOKIE_FILE: str = _env_str("BILIBILI_COOKIE_FILE", "cookies.txt")

    # Paths
    BASE_DIR: Path = BASE_DIR
    ENV_PATH: Path = ENV_PATH
    INPUT_DIR: Path = INPUT_DIR
    INPUT_VIDEOS_DIR: Path = INPUT_VIDEOS_DIR
    INPUT_AUDIO_DIR: Path = INPUT_AUDIO_DIR
    
    OUTPUT_DIR: Path = OUTPUT_DIR
    OUTPUT_TRANSCRIPTS_DIR: Path = OUTPUT_TRANSCRIPTS_DIR
    OUTPUT_RAW_CLEANED_DIR: Path = OUTPUT_RAW_CLEANED_DIR
    OUTPUT_ENTITIES_DIR: Path = OUTPUT_ENTITIES_DIR
    OUTPUT_DICH_AI_LLM_DIR: Path = OUTPUT_DICH_AI_LLM_DIR
    OUTPUT_POST_PROCESSED_DIR: Path = OUTPUT_POST_PROCESSED_DIR
    OUTPUT_TRANSLATIONS_DIR: Path = OUTPUT_TRANSLATIONS_DIR
    OUTPUT_VOICEOVER_DIR: Path = OUTPUT_VOICEOVER_DIR
    OUTPUT_FINAL_VIDEOS_DIR: Path = OUTPUT_FINAL_VIDEOS_DIR
    
    TEMP_DIR: Path = TEMP_DIR
    TEMP_TTS_DIR: Path = TEMP_TTS_DIR

    DB_DIR: Path = DB_DIR
    DB_PATH: Path = DB_PATH
    
    ASSETS_DIR: Path = ASSETS_DIR
    ASSETS_DICT_DIR: Path = ASSETS_DICT_DIR
    ASSETS_FRAMES_DIR: Path = ASSETS_FRAMES_DIR
    FRAME_CANVAS_PATH: Path = FRAME_CANVAS_PATH

    @classmethod
    def get_bilibili_cookie_path(cls) -> Optional[str]:
        """
        Tìm kiếm file cookie Bilibili hợp lệ:
        1. Đường dẫn cấu hình trong .env (BILIBILI_COOKIE_FILE), hỗ trợ cả tương đối và tuyệt đối
        2. File cookies.txt ngay tại thư mục gốc dự án
        Trả về đường dẫn tuyệt đối nếu tồn tại, ngược lại trả về None.
        Tuyệt đối không hardcode đường dẫn cục bộ máy tính.
        """
        custom_path = getattr(cls, "BILIBILI_COOKIE_FILE", "").strip().strip('"').strip("'")
        if custom_path:
            p = Path(custom_path)
            if not p.is_absolute():
                p = cls.BASE_DIR / custom_path
            if p.is_file() and p.stat().st_size > 0:
                return str(p.resolve())
        
        local_cookie = cls.BASE_DIR / "cookies.txt"
        if local_cookie.is_file() and local_cookie.stat().st_size > 0:
            return str(local_cookie.resolve())
            
        return None

    @classmethod
    def _cast_env_value(cls, k: str, v: str):
        return _parse_env_value(k, v)

    @classmethod
    def reload_from_env(cls):
        """Đọc lại toàn bộ cấu hình từ file .env vào runtime settings và os.environ ngay lập tức"""
        if not cls.ENV_PATH.exists():
            return
        with open(cls.ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip()
                    val = cls._cast_env_value(k, v)
                    setattr(cls, k, val)
                    if 'settings' in globals():
                        setattr(globals()['settings'], k, val)
                    os.environ[k] = v

    @classmethod
    def save_to_env(cls, updates: dict):
        """Lưu cấu hình mới vào file .env và cập nhật biến runtime ngay lập tức"""
        env_dict = {}
        if cls.ENV_PATH.exists():
            with open(cls.ENV_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        env_dict[k.strip()] = v.strip()

        for k, v in updates.items():
            if v is not None:
                str_v = str(v).strip()
                env_dict[k] = str_v
                val = cls._cast_env_value(k, str_v)
                setattr(cls, k, val)
                if 'settings' in globals():
                    setattr(globals()['settings'], k, val)
                os.environ[k] = str_v

        with open(cls.ENV_PATH, "w", encoding="utf-8") as f:
            for k, v in sorted(env_dict.items()):
                f.write(f"{k}={v}\n")

        cls.reload_from_env()

settings = Settings()
