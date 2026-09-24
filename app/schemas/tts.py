from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

class TTSSingleRequest(BaseModel):
    text: str = Field(..., description="Văn bản cần tổng hợp thành giọng đọc")
    voice_code: str = Field(default="BV074_streaming", description="Mã giọng đọc TikTok")
    session_id: Optional[str] = Field(default=None, description="TikTok Session ID tùy chọn")
    playback_speed: float = Field(default=1.05, description="Tốc độ phát âm thanh (1.0x - 1.25x)")
    apply_mastering: bool = Field(default=True, description="Áp dụng bộ lọc EQ & Mastering làm rõ chữ")

class TTSSingleResponse(BaseModel):
    status: str
    voice_code: str
    duration_sec: float
    audio_base64: str

class TTSProjectRequest(BaseModel):
    project_id: int = Field(..., description="ID của Project cần tạo lồng tiếng")
    voice_code: str = Field(default="BV074_streaming", description="Mã giọng đọc TikTok")
    session_id: Optional[str] = Field(default=None, description="TikTok Session ID tùy chọn")
    playback_speed: float = Field(default=1.05, description="Tốc độ phát âm thanh")
    apply_mastering: bool = Field(default=True, description="Bộ lọc rõ chữ & êm tai")
    auto_fit_timeline: bool = Field(default=True, description="Tự động co giãn tốc độ để khớp từng mili-giây với video gốc")

class TTSProjectResponse(BaseModel):
    task_id: str
    status: str
    message: str
