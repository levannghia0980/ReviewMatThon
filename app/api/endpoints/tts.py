import uuid
import base64
import io
import threading
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from app.db.session import SessionLocal, get_db
from app.schemas.tts import (
    TTSSingleRequest,
    TTSSingleResponse,
    TTSProjectRequest,
    TTSProjectResponse
)
from app.schemas.pipeline import TaskStatusResponse
from app.services.tts.capcut_tts_service import CapCutTTSService
from app.services.tts.tiktok_tts_service import TikTokTTSService
from app.services.task_manager import task_manager
from app.config import settings

router = APIRouter()

def _run_tts_worker(task_id: str, req: TTSProjectRequest):
    """Worker chạy background thread độc lập để sản xuất TTS không block HTTP"""
    db = SessionLocal()
    try:
        task_manager.update_task(task_id, status="running", step=1, progress=5)
        engine = getattr(settings, "TTS_ENGINE", "capcut").lower()

        if engine in ("capcut", "capcut_cloud"):
            try:
                task_manager.add_log(task_id, "🚀 Kích hoạt CapCut Cloud SAMI TTS Engine (128 Workers Concurrent)...", "cyan")
                CapCutTTSService.produce_project_voiceover(
                    task_id=task_id,
                    project_id=req.project_id,
                    db=db,
                    voice_code=req.voice_code,
                    session_id=req.session_id,
                    apply_mastering=req.apply_mastering,
                    playback_speed=req.playback_speed,
                    auto_fit_timeline=req.auto_fit_timeline,
                    max_workers=settings.TTS_MAX_WORKERS or 128
                )
                return
            except Exception as cap_err:
                task_manager.add_log(task_id, f"⚠️ CapCut TTS gặp sự cố ({cap_err}), tự động fallback sang TikTok TTS...", "amber")

        TikTokTTSService.produce_project_voiceover(
            task_id=task_id,
            project_id=req.project_id,
            db=db,
            voice_code=req.voice_code,
            session_id=req.session_id,
            apply_mastering=req.apply_mastering,
            playback_speed=req.playback_speed,
            auto_fit_timeline=req.auto_fit_timeline,
            max_workers=settings.TTS_MAX_WORKERS or 128
        )
    except Exception as e:
        db.rollback()
        err_msg = str(e)
        task_manager.add_log(task_id, f"❌ LỖI LỒNG TIẾNG TTS: {err_msg}", "rose")
        task_manager.update_task(
            task_id,
            status="failed",
            error=err_msg,
            message=f"Lỗi TTS: {err_msg}"
        )
    finally:
        db.close()


@router.get("/voices", summary="Lấy danh sách các giọng đọc hỗ trợ (CapCut & TikTok)")
def get_voices():
    return {
        "status": "success",
        "voices": CapCutTTSService.get_supported_voices()
    }


@router.post("/single", response_model=TTSSingleResponse, summary="Thử nghiệm tạo giọng đọc TTS cho 1 câu đơn lẻ")
def synthesize_single_preview(req: TTSSingleRequest):
    try:
        engine = getattr(settings, "TTS_ENGINE", "capcut").lower()
        if engine in ("capcut", "capcut_cloud"):
            try:
                audio_seg = CapCutTTSService.synthesize_sentence(
                    text=req.text,
                    voice_code=req.voice_code,
                    cookie=req.session_id,
                    apply_mastering=req.apply_mastering,
                    playback_speed=req.playback_speed
                )
            except Exception:
                audio_seg = TikTokTTSService.synthesize_sentence(
                    text=req.text,
                    voice_code=req.voice_code,
                    session_id=req.session_id,
                    apply_mastering=req.apply_mastering,
                    playback_speed=req.playback_speed
                )
        else:
            audio_seg = TikTokTTSService.synthesize_sentence(
                text=req.text,
                voice_code=req.voice_code,
                session_id=req.session_id,
                apply_mastering=req.apply_mastering,
                playback_speed=req.playback_speed
            )
        
        buffer = io.BytesIO()
        audio_seg.export(buffer, format="mp3", bitrate="192k")
        audio_b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
        
        return TTSSingleResponse(
            status="success",
            voice_code=req.voice_code,
            duration_sec=round(len(audio_seg) / 1000.0, 3),
            audio_base64=audio_b64
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/project", response_model=TTSProjectResponse, summary="Khởi chạy tiến trình lồng tiếng toàn bộ Project (TikTok TTS Background Task)")
def start_project_voiceover(req: TTSProjectRequest, db: Session = Depends(get_db)):
    task_id = f"task_tts_{uuid.uuid4().hex[:10]}"
    task_manager.create_task(task_id, meta={"project_id": req.project_id, "voice_code": req.voice_code})

    worker_thread = threading.Thread(
        target=_run_tts_worker,
        args=(task_id, req),
        daemon=True
    )
    worker_thread.start()

    return TTSProjectResponse(
        task_id=task_id,
        status="processing",
        message="Tiến trình sản xuất âm thanh lồng tiếng TikTok TTS đã khởi chạy."
    )


@router.get("/status/{task_id}", response_model=TaskStatusResponse, summary="Theo dõi tiến trình sản xuất TTS")
def get_tts_status(task_id: str):
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy tác vụ {task_id}")
    return task


@router.post("/clean-cache", summary="Dọn dẹp file rác TTS (chunk lẻ, ffmpeg cache) gom lại xóa định kỳ")
def clean_tts_cache_endpoint(video_id: str = None):
    count = TikTokTTSService.clean_tts_cache(video_id=video_id)
    return {
        "status": "success",
        "message": f"Đã dọn dẹp thành công {count} mục file rác/cache TTS!",
        "deleted_count": count
    }

