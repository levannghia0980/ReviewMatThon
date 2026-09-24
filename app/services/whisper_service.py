import os
import json
from pathlib import Path
from typing import List, Dict, Any, Tuple
from app.config import settings
from app.schemas.transcript import DialogueSegment
from app.services.text_cleaner import TextCleanerService
from app.services.groq_whisper_service import GroqWhisperService

def format_timestamp(seconds: float) -> str:
    millis = int((seconds - int(seconds)) * 1000)
    mins, secs = divmod(int(seconds), 60)
    hours, mins = divmod(mins, 60)
    return f"{hours:02d}:{mins:02d}:{secs:02d},{millis:03d}"

class WhisperService:
    _models: Dict[str, Any] = {}

    @classmethod
    def get_model(cls, model_size: str = None) -> Any:
        # pyrefly: ignore [missing-import]
        from faster_whisper import WhisperModel
        size = model_size or settings.WHISPER_MODEL_SIZE
        if size not in cls._models:
            device = settings.WHISPER_DEVICE
            compute_type = settings.WHISPER_COMPUTE_TYPE
            
            try:
                if device in ("auto", "cuda"):
                    for c_type in ["float16", "int8_float16", "int8"]:
                        try:
                            cls._models[size] = WhisperModel(size, device="cuda", compute_type=c_type)
                            return cls._models[size]
                        except Exception:
                            continue
                
                # Nếu không có CUDA hoặc cấu hình CPU
                cls._models[size] = WhisperModel(
                    size,
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=4,
                    num_workers=1
                )
            except Exception:
                cls._models[size] = WhisperModel(
                    size,
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=4,
                    num_workers=1
                )
                
        return cls._models[size]

    @classmethod
    def transcribe(
        cls,
        audio_path: str,
        language: str = "zh",
        model_size: str = None,
        clean_text: bool = True
    ) -> Tuple[List[DialogueSegment], str, str, str]:
        """
        Tự động điều phối giữa CapCut Cloud STT (Mặc định siêu tốc, chuẩn mili-giây, 0% CPU) và Groq Cloud.
        Bỏ hoàn toàn CPU offline Whisper để không làm đơ máy.
        """
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Không tìm thấy file audio: {audio_path}")

        engine = getattr(settings, "ASR_ENGINE", "capcut").lower()

        # 1. Nếu chọn CapCut STT (Mặc định số 1: Siêu lấy time sub, chuẩn từng mili-giây, 0% CPU):
        if engine in ("capcut", "capcut_stt"):
            try:
                from app.services.capcut_asr_service import CapCutASRService
                cap_lang = "zh-CN" if language in ("zh", "zh-CN") else language
                return CapCutASRService.transcribe(
                    audio_path=audio_path,
                    language=cap_lang,
                    clean_text=clean_text
                )
            except Exception as cap_err:
                import logging
                logging.getLogger(__name__).warning(f"CapCut ASR gặp sự cố ({cap_err}), chuyển sang Groq fallback...")

        # 2. Nếu chọn Groq Whisper Cloud (hoặc fallback sang Groq):
        if settings.GROQ_API_KEY:
            try:
                return GroqWhisperService.transcribe(
                    audio_path=audio_path,
                    language=language,
                    clean_text=clean_text
                )
            except Exception as e:
                if engine == "groq":
                    raise RuntimeError(f"Groq Whisper ASR Lỗi: {str(e)}")

        # 3. Mặc định luôn gọi CapCut Cloud ASR
        from app.services.capcut_asr_service import CapCutASRService
        cap_lang = "zh-CN" if language in ("zh", "zh-CN") else language
        return CapCutASRService.transcribe(
            audio_path=audio_path,
            language=cap_lang,
            clean_text=clean_text
        )
