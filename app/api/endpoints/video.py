from fastapi import APIRouter, HTTPException, BackgroundTasks
from app.schemas.video import VideoDownloadRequest, VideoDownloadResponse, VideoInfoResponse
from app.services.downloader_service import DownloaderService
from app.services.audio_extractor import AudioExtractorService

router = APIRouter()

@router.get("/info", response_model=VideoInfoResponse, summary="Lấy thông tin tiêu đề, thời lượng video")
def get_video_info(url: str):
    try:
        info = DownloaderService.get_video_info(url)
        return VideoInfoResponse(
            id=info["id"],
            title=info["title"],
            duration=info["duration"],
            uploader=info.get("uploader"),
            thumbnail=info.get("thumbnail")
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/download", response_model=VideoDownloadResponse, summary="Tải video 720p/1080p và tự động trích xuất Audio 16kHz")
def download_video(req: VideoDownloadRequest):
    try:
        video_path, info = DownloaderService.download_video(
            url=req.url,
            quality=req.quality,
            custom_filename=req.custom_filename
        )
        
        # Tự động tách Audio 16kHz WAV sẵn sàng cho AI
        audio_path = AudioExtractorService.extract_audio_16k_wav(video_path)
        
        return VideoDownloadResponse(
            status="success",
            message="Tải video và trích xuất audio thành công",
            video_id=info["id"],
            title=info["title"],
            video_path=video_path,
            audio_path=audio_path,
            duration=info["duration"]
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
