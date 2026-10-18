import uuid
import threading
import asyncio
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from app.db.session import SessionLocal, get_db
from app.schemas.translation import TranslateProjectRequest, TranslateBatchResponse
from app.schemas.pipeline import TaskStatusResponse
from app.services.step_03_translation.translation_pipeline import TranslationPipelineService
from app.services.task_manager import task_manager
from app.config import settings

router = APIRouter()

def _run_translation_worker(task_id: str, req: TranslateProjectRequest):
    """Worker chạy background thread độc lập để dịch thuật không block HTTP request"""
    db = SessionLocal()
    try:
        task_manager.update_task(task_id, status="running", step=1, progress=5)
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        effective_batch_size = req.batch_size or getattr(settings, "TRANSLATION_BATCH_SIZE", 500)
        effective_max_chars = req.max_chars or getattr(settings, "TRANSLATION_MAX_CHARS", 50000)
        effective_provider = getattr(settings, "TRANSLATION_PROVIDER", "gemini")

        loop.run_until_complete(
            TranslationPipelineService.translate_project_dialogues(
                task_id=task_id,
                project_id=req.project_id,
                db=db,
                genre=req.genre,
                batch_size=effective_batch_size,
                max_chars=effective_max_chars,
                provider=effective_provider
            )
        )
        task_manager.update_task(task_id, status="completed", progress=100, message="Đã hoàn tất dịch thuật!")
    except asyncio.CancelledError:
        db.rollback()
        task_manager.update_task(
            task_id,
            status="cancelled",
            message="Tiến trình dịch thuật đã dừng theo yêu cầu của người dùng."
        )
    except Exception as e:
        db.rollback()
        err_msg = str(e)
        task_manager.add_log(task_id, f"❌ LỖI DỊCH THUẬT: {err_msg}", "rose")
        task_manager.update_task(
            task_id,
            status="failed",
            error=err_msg,
            message=f"Lỗi dịch thuật: {err_msg}"
        )
    finally:
        db.close()
        # GIỮ NGUYÊN PHIÊN GEMINI WEB CHO CÁC TÁC VỤ TIẾP THEO (KHÔNG TẮT GIỮA CHỪNG)
        try:
            loop.close()
        except Exception:
            pass


@router.post("/project", response_model=TranslateBatchResponse, summary="Khởi chạy dịch thuật toàn diện AIREAD cho Project")
def start_project_translation(req: TranslateProjectRequest, db: Session = Depends(get_db)):
    task_id = f"task_trans_{uuid.uuid4().hex[:10]}"
    task_manager.create_task(task_id, meta={"project_id": req.project_id, "genre": req.genre})

    worker_thread = threading.Thread(
        target=_run_translation_worker,
        args=(task_id, req),
        daemon=True
    )
    worker_thread.start()

    return TranslateBatchResponse(
        task_id=task_id,
        status="processing",
        message="Tiến trình dịch thuật đa tầng AIREAD đã khởi chạy trong background thread."
    )


@router.get("/status/{task_id}", response_model=TaskStatusResponse, summary="Theo dõi tiến trình dịch thuật và log real-time")
def get_translation_status(task_id: str):
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy tác vụ {task_id}")
    return task


@router.post("/cancel/{task_id}", summary="Dừng ngay lập tức tác vụ dịch thuật và bảo toàn dữ liệu các lô đã dịch xong")
def cancel_translation(task_id: str):
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy tác vụ {task_id}")
    task_manager.cancel_task(task_id)
    return {"status": "success", "message": f"Đã dừng tác vụ dịch thuật {task_id}."}


@router.get("/gemini-web/status", summary="Kiểm tra trạng thái đăng nhập và worker Gemini Web")
def get_gemini_web_status():
    from app.services.step_03_translation.gemini_browser_session import GeminiBrowserSessionManager
    from app.services.step_03_translation.gemini_web_service import GeminiWebService
    from app.config import settings
    cached = GeminiBrowserSessionManager.load_cached_cookies()
    has_auth_cookie = bool(cached.get("__Secure-1PSID") or cached.get("SID"))
    worker_status = GeminiWebService.get_status()
    configured_thinking = getattr(settings, "GEMINI_WEB_THINKING", "medium")
    return {
        "logged_in": has_auth_cookie,
        "has_cached_session": has_auth_cookie,
        "worker_running": worker_status.get("is_running", False),
        "thinking_level": worker_status.get("thinking_level") or configured_thinking,
        "url": worker_status.get("url"),
        "message": "Đã có phiên đăng nhập Google" if has_auth_cookie else "Chưa đăng nhập Google"
    }


@router.post("/gemini-web/login", summary="Mở cửa sổ trình duyệt Chromium để người dùng đăng nhập tài khoản Google 1 lần")
async def trigger_gemini_web_login():
    from app.services.step_03_translation.gemini_browser_session import GeminiBrowserSessionManager
    from app.services.step_03_translation.gemini_web_service import GeminiWebService
    from app.config import settings
    res = await GeminiBrowserSessionManager.open_login_window()
    if res.get("success"):
        # Tự động nạp ngay worker ngầm duy nhất để sẵn sàng làm việc
        saved_thinking = getattr(settings, "GEMINI_WEB_THINKING", "medium")
        asyncio.create_task(GeminiWebService.init_worker(saved_thinking))
    return res


@router.post("/gemini-web/thinking", summary="Chuyển đổi Cấp độ tư duy trực tiếp trên phiên Gemini đang chạy")
async def set_gemini_thinking_level(thinking_level: str = "high"):
    from app.services.step_03_translation.gemini_web_service import GeminiWebService
    ok = await GeminiWebService.set_thinking_level(thinking_level)
    return {"success": ok, "thinking_level": thinking_level}


@router.post("/gemini-web/shutdown", summary="Chủ động đóng phiên Chromium ngầm để giải phóng RAM")
async def shutdown_gemini_worker():
    from app.services.step_03_translation.gemini_web_service import GeminiWebService
    await GeminiWebService.shutdown_worker()
    return {"success": True, "message": "Đã đóng phiên Chromium ngầm."}

