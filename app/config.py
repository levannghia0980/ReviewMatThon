import os
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
OUTPUT_TRANSCRIPTS_DIR = OUTPUT_DIR / "transcripts"
OUTPUT_RAW_CLEANED_DIR = OUTPUT_DIR / "01_raw_cleaned"
OUTPUT_ENTITIES_DIR = OUTPUT_DIR / "02_entities"
OUTPUT_DICH_AI_LLM_DIR = OUTPUT_DIR / "03_dich_ai_llm"
OUTPUT_POST_PROCESSED_DIR = OUTPUT_DIR / "04_post_processed"
OUTPUT_TRANSLATIONS_DIR = OUTPUT_DIR / "translations"
OUTPUT_VOICEOVER_DIR = OUTPUT_DIR / "voiceover"
OUTPUT_FINAL_VIDEOS_DIR = OUTPUT_DIR / "final_videos"

# 3. Thư mục TEMP & TTS CACHE (Lưu trữ file rác khi tạo TTS: chunk mp3, ffmpeg cache)
TEMP_DIR = BASE_DIR / "temp"
TEMP_TTS_DIR = TEMP_DIR / "tts_cache"

# 4. Thư mục DATABASE (SQLite)
DB_DIR = BASE_DIR / "database"
DB_PATH = DB_DIR / "review_mat_thon.db"

# 5. Thư mục ASSETS (Từ điển, fonts, banner...)
ASSETS_DIR = BASE_DIR / "assets"
ASSETS_DICT_DIR = ASSETS_DIR / "dictionaries"

# 6. Thư mục TOOLS (FFmpeg, yt-dlp, aria2c nhúng)
TOOLS_DIR = BASE_DIR / "tools"

# Tự động khởi tạo tất cả thư mục
ALL_DIRS = [
    INPUT_VIDEOS_DIR,
    INPUT_AUDIO_DIR,
    OUTPUT_TRANSCRIPTS_DIR,
    OUTPUT_RAW_CLEANED_DIR,
    OUTPUT_ENTITIES_DIR,
    OUTPUT_DICH_AI_LLM_DIR,
    OUTPUT_POST_PROCESSED_DIR,
    OUTPUT_TRANSLATIONS_DIR,
    OUTPUT_VOICEOVER_DIR,
    OUTPUT_FINAL_VIDEOS_DIR,
    TEMP_DIR,
    TEMP_TTS_DIR,
    DB_DIR,
    ASSETS_DIR,
    ASSETS_DICT_DIR,
    TOOLS_DIR
]

for d in ALL_DIRS:
    d.mkdir(parents=True, exist_ok=True)

try:
    from app.utils.bin_helper import setup_system_path
    setup_system_path()
except Exception:
    pass

class Settings:
    PROJECT_NAME: str = "ReviewMatThon Video Dubbing & Processing Backend"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    
    # SQLite Database URL
    DATABASE_URL: str = f"sqlite:///{DB_PATH}"
    
    # ASR Engine Selection: "groq" (Groq Whisper Cloud - Chuẩn nhất) | "faster_whisper" (Offline CPU/GPU)
    ASR_ENGINE: str = os.getenv("ASR_ENGINE", "capcut")
    
    # Groq API Configuration
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "whisper-large-v3")
    
    # Faster-Whisper Model Settings (Offline)
    WHISPER_MODEL_SIZE: str = os.getenv("WHISPER_MODEL_SIZE", "base")
    WHISPER_DEVICE: str = os.getenv("WHISPER_DEVICE", "auto")
    WHISPER_COMPUTE_TYPE: str = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
    
    # Gemini API Settings
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
    
    # OpenRouter API Settings
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
    OPENROUTER_MODEL: str = os.getenv("OPENROUTER_MODEL", "google/gemini-2.5-flash")

    # Translation Settings (Lô lớn 200 - 500 câu theo chuẩn AIRead)
    TRANSLATION_PROVIDER: str = os.getenv("TRANSLATION_PROVIDER", "gemini")
    TRANSLATION_GENRE: str = os.getenv("TRANSLATION_GENRE", "tienhiep")
    TRANSLATION_BATCH_SIZE: int = int(os.getenv("TRANSLATION_BATCH_SIZE", "500"))
    TRANSLATION_MAX_CHARS: int = int(os.getenv("TRANSLATION_MAX_CHARS", "50000"))
    
    # TTS Voiceover Settings: "capcut" (CapCut Cloud SAMI - Khuyên dùng) | "tiktok" (TikTok Legacy)
    TTS_ENGINE: str = os.getenv("TTS_ENGINE", "capcut")
    TTS_MAX_WORKERS: int = int(os.getenv("TTS_MAX_WORKERS", "16"))
    CAPCUT_COOKIE: str = os.getenv("CAPCUT_COOKIE", "")
    TIKTOK_SESSION_ID: str = os.getenv("TIKTOK_SESSION_ID", "410bfa37bdc185e1c6da82e1afb48409")
    
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

    @classmethod
    def save_to_env(cls, updates: dict):
        """Lưu cấu hình mới vào file .env và cập nhật biến runtime"""
        env_dict = {}
        if cls.ENV_PATH.exists():
            with open(cls.ENV_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        env_dict[k.strip()] = v.strip()
        
        int_fields = {"TTS_MAX_WORKERS", "TRANSLATION_BATCH_SIZE", "TRANSLATION_MAX_CHARS"}
        for k, v in updates.items():
            if v is not None:
                env_dict[k] = str(v)
                val = int(v) if k in int_fields or (isinstance(v, str) and v.isdigit()) else v
                setattr(cls, k, val)
                if 'settings' in globals():
                    setattr(globals()['settings'], k, val)
                os.environ[k] = str(v)
                
        with open(cls.ENV_PATH, "w", encoding="utf-8") as f:
            for k, v in sorted(env_dict.items()):
                f.write(f"{k}={v}\n")

settings = Settings()
