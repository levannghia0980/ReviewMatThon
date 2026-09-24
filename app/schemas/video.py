from typing import Optional, List
from pydantic import BaseModel, Field

class VideoDownloadRequest(BaseModel):
    url: str = Field(..., description="URL video Bilibili, YouTube, Douyin...")
    quality: str = Field(default="720p", description="Chất lượng video: 720p, 1080p, 480p, 360p")
    download_audio_only: bool = Field(default=False, description="Nếu true chỉ tải audio để tiết kiệm băng thông")
    custom_filename: Optional[str] = Field(default=None, description="Tên file tùy chỉnh nếu muốn")

class VideoInfoResponse(BaseModel):
    id: str
    title: str
    duration: float
    uploader: Optional[str] = None
    thumbnail: Optional[str] = None
    formats: List[str] = []

class VideoDownloadResponse(BaseModel):
    status: str
    message: str
    video_id: str
    title: str
    video_path: Optional[str] = None
    audio_path: Optional[str] = None
    duration: float
