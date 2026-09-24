from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

class VideoComposeRequest(BaseModel):
    project_id: int = Field(..., description="ID của Project cần render video thành phẩm")
    logo_path: Optional[str] = Field(default=None, description="Đường dẫn file logo ảnh PNG")
    logo_position: str = Field(default="top_left", description="Vị trí đặt logo (top_left, top_right, bottom_left, bottom_right)")
    logo_size: int = Field(default=120, description="Kích thước logo (px) nhỏ gọn sát góc")
    logo_opacity: float = Field(default=0.90, description="Độ mờ của logo")
    channel_name: str = Field(default="@Mắt Thần Review", description="Tên kênh hiển thị watermark mờ")
    channel_opacity: float = Field(default=0.35, description="Độ mờ của tên kênh tránh clone (0.2 - 0.5)")
    karaoke_highlight_color: str = Field(default="&H0000D7FF", description="Màu highlight Karaoke ASS (&HAABBGGRR - Vàng kim: &H0000D7FF, Cyan: &H00FFFF00)")
    backdrop_opacity_hex: str = Field(default="80", description="Độ mờ hộp nền che phụ đề cũ (80 = 50% mờ)")
    has_mask: bool = Field(default=True, description="Có bật vùng hộp đen che phụ đề gốc tiếng Trung hay không")
    mask_top: float = Field(default=80.0, description="Vị trí top của vùng che (%)")
    mask_left: float = Field(default=15.0, description="Vị trí left của vùng che (%)")
    mask_width: float = Field(default=70.0, description="Chiều rộng vùng che (%)")
    mask_height: float = Field(default=12.0, description="Chiều cao vùng che (%)")
    margin_v: int = Field(default=65, description="Vị trí thanh phụ đề từ dưới đáy màn hình")

class VideoComposeResponse(BaseModel):
    task_id: str
    status: str
    message: str
