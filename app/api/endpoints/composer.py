import uuid
import threading
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from app.db.session import SessionLocal, get_db
from app.schemas.composer import VideoComposeRequest, VideoComposeResponse
from app.schemas.pipeline import TaskStatusResponse
from app.services.video.video_composer_service import VideoComposerService
from app.services.task_manager import task_manager

router = APIRouter()

def _run_compose_worker(task_id: str, req: VideoComposeRequest):
    """Worker chạy background thread độc lập để render video không block HTTP"""
    db = SessionLocal()
    try:
        task_manager.update_task(task_id, status="running", step=1, progress=5)
        
        VideoComposerService.compose_full_video(
            task_id=task_id,
            project_id=req.project_id,
            db=db,
            logo_path=req.logo_path,
            logo_position=req.logo_position,
            logo_size=req.logo_size,
            logo_opacity=req.logo_opacity,
            channel_name=req.channel_name,
            channel_opacity=req.channel_opacity,
            karaoke_highlight_color=req.karaoke_highlight_color,
            backdrop_opacity_hex=req.backdrop_opacity_hex,
            has_mask=req.has_mask,
            mask_top=req.mask_top,
            mask_left=req.mask_left,
            mask_width=req.mask_width,
            mask_height=req.mask_height,
            margin_v=req.margin_v
        )
    except Exception as e:
        db.rollback()
        err_msg = str(e)
        task_manager.add_log(task_id, f"❌ LỖI RENDER VIDEO: {err_msg}", "rose")
        task_manager.update_task(
            task_id,
            status="failed",
            error=err_msg,
            message=f"Lỗi Render: {err_msg}"
        )
    finally:
        db.close()


@router.post("/compose", response_model=VideoComposeResponse, summary="Khởi chạy render Video thành phẩm (Karaoke ASS + BGM Mix + Logo + Watermark)")
def start_video_composition(req: VideoComposeRequest, db: Session = Depends(get_db)):
    task_id = f"task_compose_{uuid.uuid4().hex[:10]}"
    task_manager.create_task(task_id, meta={"project_id": req.project_id, "channel": req.channel_name})

    worker_thread = threading.Thread(
        target=_run_compose_worker,
        args=(task_id, req),
        daemon=True
    )
    worker_thread.start()

    return VideoComposeResponse(
        task_id=task_id,
        status="processing",
        message="Tiến trình sản xuất video thành phẩm 1-pass đã khởi chạy."
    )


@router.get("/status/{task_id}", response_model=TaskStatusResponse, summary="Theo dõi tiến trình Render Video real-time")
def get_compose_status(task_id: str):
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy tác vụ {task_id}")
    return task
