from typing import Optional, List, Any, Dict
from pydantic import BaseModel, Field
from app.schemas.transcript import DialogueSegment

class IngestPipelineRequest(BaseModel):
    url: str = Field(..., description="URL video Bilibili, YouTube...")
    quality: str = Field(default="480p", description="480p, 720p hoặc 1080p")
    source_language: str = Field(default="zh", description="Ngôn ngữ gốc của video: zh (Trung), ja (Nhật), vi, en")
    clean_text: bool = Field(default=True, description="Tự động lọc từ đệm và chuẩn hóa text")

class FullAutoPipelineRequest(BaseModel):
    url: Optional[str] = Field(default=None, description="URL video Bilibili/YouTube (để trống nếu truyền project_id)")
    project_id: Optional[int] = Field(default=None, description="Project ID nếu video đã tải sẵn")
    quality: str = Field(default="480p", description="480p, 720p hoặc 1080p")
    source_language: str = Field(default="zh", description="Ngôn ngữ gốc của video")
    genre: str = Field(default="tienhiep", description="Thể loại: tienhiep, cophong, kiemhiep, dothi, huyenhuyen")
    provider: str = Field(default="gemini", description="gemini hoặc openrouter")
    voice_code: str = Field(default="BV074_streaming", description="Mã giọng TikTok TTS")
    margin_v: int = Field(default=45, description="Vị trí thanh phụ đề từ dưới đáy màn hình")
    backdrop_opacity_hex: str = Field(default="99", description="Độ mờ của hộp nền phụ đề che chữ gốc (00 -> FF)")
    has_mask: bool = Field(default=True, description="Có bật vùng hộp đen che phụ đề gốc tiếng Trung hay không")
    mask_top: float = Field(default=80.0, description="Vị trí top của vùng che (%)")
    mask_left: float = Field(default=15.0, description="Vị trí left của vùng che (%)")
    mask_width: float = Field(default=70.0, description="Chiều rộng vùng che (%)")
    mask_height: float = Field(default=12.0, description="Chiều cao vùng che (%)")
    karaoke_highlight_color: str = Field(default="&H0000D7FF", description="Màu highlight chữ karaoke")
    channel_name: str = Field(default="@Mắt Thần Review", description="Tên kênh watermark")
    channel_opacity: float = Field(default=0.35, description="Độ mờ watermark kênh")
    logo_position: str = Field(default="top_left", description="top_left, top_right, bottom_left, bottom_right")
    logo_size: int = Field(default=120, description="Kích thước logo px")
    batch_size: Optional[int] = Field(default=None, description="Số câu một lô dịch (150-500 câu)")

class IngestPipelineResponse(BaseModel):
    status: str
    message: str
    video_id: str
    title: str
    video_path: str
    audio_path: str
    duration: float
    total_dialogues: int
    srt_path: str
    txt_path: str
    json_path: str
    dialogues: Optional[List[DialogueSegment]] = None

class AsyncTaskResponse(BaseModel):
    task_id: str
    status: str
    message: str

class TaskLogItem(BaseModel):
    time: str
    text: str
    type: str

class TaskStatusResponse(BaseModel):
    id: str
    status: str
    step: int
    message: str
    progress: int
    logs: List[TaskLogItem] = []
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None



