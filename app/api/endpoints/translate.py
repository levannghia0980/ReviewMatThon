import uuid
import threading
import asyncio
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from app.db.session import SessionLocal, get_db
from app.schemas.translation import TranslateProjectRequest, TranslateBatchResponse
from app.schemas.pipeline import TaskStatusResponse
from app.services.translation.translation_pipeline import TranslationPipelineService
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
        
        effective_batch_size = req.batch_size or settings.TRANSLATION_BATCH_SIZE or 500
        effective_max_chars = req.max_chars or settings.TRANSLATION_MAX_CHARS or 50000

        loop.run_until_complete(
            TranslationPipelineService.translate_project_dialogues(
                task_id=task_id,
                project_id=req.project_id,
                db=db,
                genre=req.genre,
                batch_size=effective_batch_size,
                max_chars=effective_max_chars,
                provider=req.provider
            )
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
