import asyncio
from fastapi import APIRouter, HTTPException, UploadFile, File
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
    translation_temperature: Optional[float] = None
    translation_provider: Optional[str] = None
    gemini_web_model: Optional[str] = None
    gemini_web_thinking: Optional[str] = None
    asr_threads: Optional[int] = None
    tts_threads: Optional[int] = None
    tts_engine: Optional[str] = None
    capcut_cookie: Optional[str] = None
    tiktok_session_id: Optional[str] = None
    bilibili_cookie_file: Optional[str] = None

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
    settings.reload_from_env()
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
        "gemini_model": settings.GEMINI_MODEL,
        "openrouter_api_key": settings.OPENROUTER_API_KEY,
        "openrouter_api_key_masked": mask_key(settings.OPENROUTER_API_KEY),
        "has_openrouter_key": bool(settings.OPENROUTER_API_KEY and settings.OPENROUTER_API_KEY.strip()),
        "translation_max_chars": getattr(settings, "TRANSLATION_MAX_CHARS", 50000),
        "translation_batch_size": getattr(settings, "TRANSLATION_BATCH_SIZE", 500),
        "translation_temperature": getattr(settings, "TRANSLATION_TEMPERATURE", 0.85),
        "translation_provider": getattr(settings, "TRANSLATION_PROVIDER", "gemini"),
        "gemini_web_model": getattr(settings, "GEMINI_WEB_MODEL", "flash"),
        "gemini_web_thinking": getattr(settings, "GEMINI_WEB_THINKING", "medium"),
        "asr_threads": getattr(settings, "ASR_MAX_WORKERS", 8),
        "tts_threads": getattr(settings, "TTS_MAX_WORKERS", 128),
        "tts_engine": getattr(settings, "TTS_ENGINE", "capcut"),
        "capcut_cookie": getattr(settings, "CAPCUT_COOKIE", ""),
        "tiktok_session_id": getattr(settings, "TIKTOK_SESSION_ID", ""),
        "bilibili_cookie_file": getattr(settings, "BILIBILI_COOKIE_FILE", ""),
        "resolved_bilibili_cookie": settings.get_bilibili_cookie_path(),
        "has_bilibili_cookie": bool(settings.get_bilibili_cookie_path()),
    }

@router.post("", summary="Lưu cấu hình hệ thống vào .env")
async def update_settings(req: SettingsUpdateRequest):
    updates = {}
    if req.asr_engine is not None:
        updates["ASR_ENGINE"] = req.asr_engine
    if req.tts_engine is not None:
        updates["TTS_ENGINE"] = req.tts_engine
    if req.capcut_cookie is not None:
        updates["CAPCUT_COOKIE"] = req.capcut_cookie.strip()
    if req.tiktok_session_id is not None:
        updates["TIKTOK_SESSION_ID"] = req.tiktok_session_id.strip()
    if req.bilibili_cookie_file is not None:
        updates["BILIBILI_COOKIE_FILE"] = req.bilibili_cookie_file.strip()
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
    if req.translation_temperature is not None:
        updates["TRANSLATION_TEMPERATURE"] = str(max(0.1, min(1.0, round(float(req.translation_temperature), 2))))
    if req.translation_provider is not None:
        updates["TRANSLATION_PROVIDER"] = req.translation_provider.strip()
    if req.gemini_web_model is not None:
        updates["GEMINI_WEB_MODEL"] = req.gemini_web_model.strip()
    if req.gemini_web_thinking is not None:
        updates["GEMINI_WEB_THINKING"] = req.gemini_web_thinking.strip()
    if req.asr_threads is not None:
        updates["ASR_MAX_WORKERS"] = str(max(1, min(16, req.asr_threads)))
    if req.tts_threads is not None:
        updates["TTS_MAX_WORKERS"] = str(max(1, min(128, req.tts_threads)))

    settings.save_to_env(updates)

    # Nếu đang dùng chế độ Web hoặc vừa chuyển sang Web: cập nhật trực tiếp trên phiên Gemini duy nhất
    current_provider = (req.translation_provider or getattr(settings, "TRANSLATION_PROVIDER", "gemini")).strip()
    if current_provider == "gemini_web":
        try:
            from app.services.step_03_translation.gemini_web_service import GeminiWebService
            import asyncio
            target_t = (req.gemini_web_thinking or getattr(settings, "GEMINI_WEB_THINKING", "medium")).strip()
            print(f"[*] Chế độ Web đang bật -> Đang cập nhật mức tư duy '{target_t}' trên phiên Gemini duy nhất...", flush=True)
            asyncio.create_task(GeminiWebService.set_thinking_level(target_t))
        except Exception as e:
            print(f"⚠️ Không thể cập nhật mức tư duy Gemini Web: {e}", flush=True)

    return {
        "status": "success",
        "message": "Đã lưu cài đặt thành công!",
        "settings": {
            "asr_engine": settings.ASR_ENGINE,
            "tts_engine": getattr(settings, "TTS_ENGINE", "capcut"),
            "translation_provider": getattr(settings, "TRANSLATION_PROVIDER", "gemini"),
            "gemini_web_model": getattr(settings, "GEMINI_WEB_MODEL", "flash"),
            "gemini_web_thinking": getattr(settings, "GEMINI_WEB_THINKING", "medium"),
            "capcut_cookie": getattr(settings, "CAPCUT_COOKIE", ""),
            "tiktok_session_id": getattr(settings, "TIKTOK_SESSION_ID", ""),
            "bilibili_cookie_file": getattr(settings, "BILIBILI_COOKIE_FILE", ""),
            "resolved_bilibili_cookie": settings.get_bilibili_cookie_path(),
            "has_bilibili_cookie": bool(settings.get_bilibili_cookie_path()),
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
            "translation_temperature": getattr(settings, "TRANSLATION_TEMPERATURE", 0.85),
            "tts_threads": settings.TTS_MAX_WORKERS,
            "asr_threads": getattr(settings, "ASR_MAX_WORKERS", 8),
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
        if req.api_key and req.api_key.strip():
            settings.save_to_env({"GROQ_API_KEY": req.api_key.strip()})
        return {
            "status": "success",
            "message": "🎉 Kết nối Groq Cloud API thành công! Đã tự động lưu vĩnh viễn vào hệ thống (.env).",
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
                # TỰ ĐỘNG LƯU VĨNH VIỄN KEY HỢP LỆ VÀO .ENV NGAY LẬP TỨC
                if raw_key:
                    settings.save_to_env({"GEMINI_API_KEY": raw_key})

                data = resp.json()
                models = [m.get("name", "").replace("models/", "") for m in data.get("models", [])]
                flash_models = [m for m in models if "flash" in m or "pro" in m]
                return {
                    "status": "success",
                    "message": "🎉 Kết nối Google Gemini API thành công! Đã tự động lưu vĩnh viễn vào hệ thống (.env).",
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

@router.post("/upload-cookie", summary="Tải lên trực tiếp file cookies.txt vào hệ thống")
async def upload_cookie_file(file: UploadFile = File(...)):
    if not (file.filename or "").lower().endswith((".txt", ".cookie")):
        raise HTTPException(status_code=400, detail="Vui lòng tải lên file văn bản định dạng .txt (ví dụ: cookies.txt)!")
    
    content = await file.read()
    if not content or len(content.strip()) == 0:
        raise HTTPException(status_code=400, detail="File cookie rỗng, vui lòng chọn file cookie có dữ liệu hợp lệ!")
    
    target_path = settings.BASE_DIR / "cookies.txt"
    try:
        with open(target_path, "wb") as f:
            f.write(content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Không thể ghi file cookies.txt: {e}")
        
    settings.save_to_env({"BILIBILI_COOKIE_FILE": "cookies.txt"})
    
    return {
        "status": "success",
        "message": f"🎉 Đã nạp thành công file {file.filename} vào hệ thống!",
        "cookie_file": "cookies.txt",
        "has_bilibili_cookie": True,
        "resolved_bilibili_cookie": str(target_path.resolve())
    }

