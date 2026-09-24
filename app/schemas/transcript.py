from typing import Optional, List
from pydantic import BaseModel, Field

class DialogueSegment(BaseModel):
    id: int = Field(..., description="Số thứ tự câu")
    start: float = Field(..., description="Thời gian bắt đầu (giây)")
    end: float = Field(..., description="Thời gian kết thúc (giây)")
    duration: float = Field(..., description="Thời lượng câu nói (giây)")
    text: str = Field(..., description="Lời thoại gốc")
    clean_text: Optional[str] = Field(default="", description="Lời thoại đã làm sạch từ đệm & ký tự thừa")
    translated_text: Optional[str] = Field(default="", description="Bản dịch Tiếng Việt chuẩn AIREAD")
    final_text: Optional[str] = Field(default="", description="Bản dịch sau hậu xử lý & kiểm định")
    confidence: Optional[float] = Field(default=0.0, description="Độ tin cậy của AI ASR")

class TranscribeRequest(BaseModel):
    audio_or_video_path: str = Field(..., description="Đường dẫn file video MP4 hoặc audio WAV trên máy")
    language: str = Field(default="zh", description="Mã ngôn ngữ: 'zh' (tiếng Trung), 'ja' (tiếng Nhật), 'vi', 'en', 'auto'")
    clean_text: bool = Field(default=True, description="Tự động lọc từ đệm và chuẩn hóa text")
    model_size: Optional[str] = Field(default=None, description="base, small, medium, large-v3")

class TranscribeResponse(BaseModel):
    status: str
    language: str
    total_segments: int
    total_duration: float
    segments: List[DialogueSegment]
    srt_path: Optional[str] = None
    txt_path: Optional[str] = None
    json_path: Optional[str] = None

class CleanTextRequest(BaseModel):
    segments: List[DialogueSegment]
    remove_fillers: bool = True
    normalize_punctuation: bool = True
    filter_short_noise: bool = True

class CleanTextResponse(BaseModel):
    status: str
    original_count: int
    cleaned_count: int
    segments: List[DialogueSegment]
