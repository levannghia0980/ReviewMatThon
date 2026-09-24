from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from groq import Groq
from app.config import settings

router = APIRouter()

class SettingsUpdateRequest(BaseModel):
    asr_engine: Optional[str] = None
    groq_api_key: Optional[str] = None
    groq_model: Optional[str] = None
    whisper_model_size: Optional[str] = None
    gemini_api_key: Optional[str] = None
    gemini_model: Optional[str] = None
    openrouter_api_key: Optional[str] = None
    translation_max_chars: Optional[int] = None
    translation_batch_size: Optional[int] = None
    tts_threads: Optional[int] = None
    tts_engine: Optional[str] = None
    capcut_cookie: Optional[str] = None
    tiktok_session_id: Optional[str] = None

class TestGroqRequest(BaseModel):
    api_key: Optional[str] = None

class TestGeminiRequest(BaseModel):
    api_key: Optional[str] = None
    model: Optional[str] = None

def mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}...{key[-4:]}"

@router.get("", summary="Lấy cấu hình hiện tại của hệ thống")
def get_settings():
    return {
        "asr_engine": settings.ASR_ENGINE,
        "groq_api_key": settings.GROQ_API_KEY,
        "groq_api_key_masked": mask_key(settings.GROQ_API_KEY),
        "has_groq_key": bool(settings.GROQ_API_KEY and settings.GROQ_API_KEY.strip()),
        "groq_model": settings.GROQ_MODEL,
        "whisper_model_size": settings.WHISPER_MODEL_SIZE,
        "whisper_device": settings.WHISPER_DEVICE,
        "whisper_compute_type": settings.WHISPER_COMPUTE_TYPE,
        "gemini_api_key": settings.GEMINI_API_KEY,
        "gemini_api_key_masked": mask_key(settings.GEMINI_API_KEY),
        "has_gemini_key": bool(settings.GEMINI_API_KEY and settings.GEMINI_API_KEY.strip()),
        "gemini_model": settings.GEMINI_MODEL or "gemini-2.5-flash",
        "openrouter_api_key": settings.OPENROUTER_API_KEY,
        "openrouter_api_key_masked": mask_key(settings.OPENROUTER_API_KEY),
        "has_openrouter_key": bool(settings.OPENROUTER_API_KEY and settings.OPENROUTER_API_KEY.strip()),
        "openrouter_model": settings.OPENROUTER_MODEL,
        "translation_max_chars": settings.TRANSLATION_MAX_CHARS or 15000,
        "translation_batch_size": settings.TRANSLATION_BATCH_SIZE or 50,
        "tts_threads": settings.TTS_MAX_WORKERS,
        "tts_engine": getattr(settings, "TTS_ENGINE", "capcut"),
        "capcut_cookie": getattr(settings, "CAPCUT_COOKIE", ""),
        "tiktok_session_id": getattr(settings, "TIKTOK_SESSION_ID", ""),
    }

@router.post("", summary="Lưu cấu hình hệ thống vào .env")
def update_settings(req: SettingsUpdateRequest):
    updates = {}
    if req.asr_engine is not None:
        updates["ASR_ENGINE"] = req.asr_engine
    if req.tts_engine is not None:
        updates["TTS_ENGINE"] = req.tts_engine
    if req.capcut_cookie is not None:
        updates["CAPCUT_COOKIE"] = req.capcut_cookie.strip()
    if req.tiktok_session_id is not None:
        updates["TIKTOK_SESSION_ID"] = req.tiktok_session_id.strip()
    if req.groq_api_key is not None:
        updates["GROQ_API_KEY"] = req.groq_api_key.strip()
    if req.groq_model is not None:
        updates["GROQ_MODEL"] = req.groq_model
    if req.whisper_model_size is not None:
        updates["WHISPER_MODEL_SIZE"] = req.whisper_model_size
    if req.gemini_api_key is not None:
        updates["GEMINI_API_KEY"] = req.gemini_api_key.strip()
    if req.gemini_model is not None:
        updates["GEMINI_MODEL"] = req.gemini_model.strip()
    if req.openrouter_api_key is not None:
        updates["OPENROUTER_API_KEY"] = req.openrouter_api_key.strip()
    if req.translation_max_chars is not None:
        updates["TRANSLATION_MAX_CHARS"] = str(max(1000, min(50000, req.translation_max_chars)))
    if req.translation_batch_size is not None:
        updates["TRANSLATION_BATCH_SIZE"] = str(max(10, min(500, req.translation_batch_size)))
    if req.tts_threads is not None:
        updates["TTS_MAX_WORKERS"] = str(max(1, min(128, req.tts_threads)))

    settings.save_to_env(updates)

    return {
        "status": "success",
        "message": "Đã lưu cài đặt thành công!",
        "settings": {
            "asr_engine": settings.ASR_ENGINE,
            "tts_engine": getattr(settings, "TTS_ENGINE", "capcut"),
            "capcut_cookie": getattr(settings, "CAPCUT_COOKIE", ""),
            "tiktok_session_id": getattr(settings, "TIKTOK_SESSION_ID", ""),
            "groq_api_key": settings.GROQ_API_KEY,
            "has_groq_key": bool(settings.GROQ_API_KEY and settings.GROQ_API_KEY.strip()),
            "groq_model": settings.GROQ_MODEL,
            "whisper_model_size": settings.WHISPER_MODEL_SIZE,
            "gemini_api_key": settings.GEMINI_API_KEY,
            "has_gemini_key": bool(settings.GEMINI_API_KEY and settings.GEMINI_API_KEY.strip()),
            "gemini_model": settings.GEMINI_MODEL,
            "openrouter_api_key": settings.OPENROUTER_API_KEY,
            "has_openrouter_key": bool(settings.OPENROUTER_API_KEY and settings.OPENROUTER_API_KEY.strip()),
            "translation_max_chars": settings.TRANSLATION_MAX_CHARS,
            "translation_batch_size": settings.TRANSLATION_BATCH_SIZE,
            "tts_threads": settings.TTS_MAX_WORKERS,
        }
    }

@router.post("/test-groq", summary="Kiểm tra tính hợp lệ của Groq API Key")
def test_groq_connection(req: TestGroqRequest):
    key = (req.api_key or settings.GROQ_API_KEY or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="Chưa có API Key để kiểm tra!")

    try:
        client = Groq(api_key=key)
        # Ping nhẹ danh sách model
        models = client.models.list()
        model_ids = [m.id for m in models.data]
        return {
            "status": "success",
            "message": "Kết nối Groq Cloud API thành công! API Key hợp lệ.",
            "available_models_count": len(model_ids),
            "has_whisper": "whisper-large-v3" in model_ids or any("whisper" in m for m in model_ids)
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Không thể kết nối Groq API: {str(e)}")

@router.post("/test-gemini", summary="Kiểm tra tính hợp lệ của Google Gemini API Key")
async def test_gemini_connection(req: TestGeminiRequest):
    import httpx
    raw_key = (req.api_key or settings.GEMINI_API_KEY or "").strip()
    if not raw_key:
        raise HTTPException(status_code=400, detail="Chưa có Google Gemini API Key để kiểm tra!")

    # Lấy key đầu tiên nếu có nhiều key cách nhau bằng dấu phẩy
    key = raw_key.split(",")[0].strip()
    if key.startswith("gsk_"):
        raise HTTPException(status_code=400, detail="Bạn đang nhập nhầm Groq API Key (bắt đầu bằng 'gsk_...') vào ô Gemini! Google Gemini Key phải bắt đầu bằng 'AIzaSy...'.")

    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                models = [m.get("name", "").replace("models/", "") for m in data.get("models", [])]
                flash_models = [m for m in models if "flash" in m or "pro" in m]
                return {
                    "status": "success",
                    "message": "🎉 Kết nối Google Gemini API thành công! API Key hợp lệ và hoạt động tốt.",
                    "available_models": flash_models[:5],
                    "total_models": len(models)
                }
            elif resp.status_code == 400 or resp.status_code == 403:
                try:
                    err_json = resp.json()
                    err_msg = err_json.get("error", {}).get("message", resp.text)
                except Exception:
                    err_msg = resp.text
                raise HTTPException(status_code=400, detail=f"Google API từ chối Key: {err_msg}")
            else:
                raise HTTPException(status_code=400, detail=f"Google API trả về mã HTTP {resp.status_code}: {resp.text}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Lỗi kết nối tới Google Gemini API: {str(e)}")
