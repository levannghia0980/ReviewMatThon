import os
import re
import math
from pathlib import Path
from typing import List, Dict, Any, Optional
from app.schemas.transcript import DialogueSegment
from app.config import settings

def format_ass_time(seconds: float) -> str:
    """Chuyển đổi giây sang định dạng ASS H:MM:SS.CC."""
    if seconds < 0:
        seconds = 0.0
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centis = int(round((seconds - int(seconds)) * 100))
    if centis >= 100:
        centis = 99
    return f"{hrs}:{mins:02d}:{secs:02d}.{centis:02d}"

class KaraokeSubtitleService:
    @staticmethod
    def generate_word_karaoke_tags(viet_text: str, duration_sec: float) -> str:
        """
        Phân bổ thời lượng phát âm từng từ dựa trên số lượng ký tự và âm tiết,
        tạo thẻ karaoke {\\kf<centiseconds>} sáng chữ mượt mà chuẩn từng mili-giây.
        """
        words = viet_text.strip().split()
        if not words:
            return ""

        total_chars = sum(len(w) for w in words)
        total_cs = max(int(duration_sec * 100), len(words) * 10)  # 1s = 100 centiseconds

        karaoke_parts = []
        accumulated_cs = 0

        for i, w in enumerate(words):
            if i == len(words) - 1:
                w_cs = max(total_cs - accumulated_cs, 10)
            else:
                weight = len(w) / max(total_chars, 1)
                w_cs = max(int(round(total_cs * weight)), 10)
                accumulated_cs += w_cs

            karaoke_parts.append(f"{{\\kf{w_cs}}}{w}")

        return " ".join(karaoke_parts)

    @classmethod
    def create_karaoke_ass_file(
        cls,
        segments: List[DialogueSegment],
        output_ass_path: str,
        video_title: str = "Video Review",
        width: int = 1920,
        height: int = 1080,
        font_name: str = "Arial",
        font_size: int = 14,
        primary_color: Optional[str] = None,
        highlight_color: Optional[str] = None,
        outline_color: Optional[str] = None,
        backdrop_opacity_hex: str = "80",       # Hộp nền mờ 50% opacity
        margin_v: int = 10,                     # Cao độ phụ đề từ đáy (px hoặc %)
        box_style: str = "white_box",           # "white_box" (Khung Trắng bo tròn chữ Đen), "dark_box" (Hộp Đen mờ), "outline_only" (Chữ viền nổi)
        box_padding: int = 8                    # Độ to theo chiều dọc / padding của hộp che chữ gốc (px)
    ) -> str:
        """
        Tạo file phụ đề ASS Karaoke chuyên nghiệp:
        - Kiểu A (Mặc định: 'white_box'): Khung viền Trắng bo tròn che sạch chữ gốc, chữ Đen đậm + Karaoke Xanh hoàng gia.
        - Kiểu B ('dark_box'): Hộp Đen mờ bo góc sang trọng + Chữ Trắng / Highlight Vàng kim.
        - Kiểu C ('outline_only'): Chữ viền hairline trong suốt không nền.
        - Tự động scale font_size & box_padding theo độ phân giải màn hình ASS (PlayResY)
          để đảm bảo tỷ lệ hộp che trên video thành phẩm khớp 1:1 với giao diện preview trên web.
        """
        style_mode = (box_style or "white_box").lower()

        # 1. Chuẩn hóa margin_v: Nếu margin_v <= 100, hiểu là % khoảng cách từ đáy (chuẩn giao diện Web)
        if margin_v <= 100:
            actual_margin_v = max(4, int(round(height * (margin_v / 100.0))))
        else:
            actual_margin_v = int(margin_v)

        # 2. Tự động scale font_size & box_padding từ hệ quy chiếu preview web (~360px) sang độ phân giải thực của video
        ref_h = 360.0
        scale_factor = max(1.0, height / ref_h)

        if font_size <= 28:
            effective_font_size = max(16, int(round(font_size * scale_factor)))
        else:
            effective_font_size = font_size

        if box_padding is not None and box_padding <= 25:
            effective_box_padding = max(5.0, round(box_padding * scale_factor, 1))
        else:
            effective_box_padding = float(box_padding if box_padding is not None else 8.0)

        # Màu Karaoke chữ chạy: Chuyển sang Đỏ cờ rực rỡ (&HAABBGGRR: &H000000FF) theo yêu cầu người dùng
        red_highlight = "&H000000FF"
        active_highlight = highlight_color if (highlight_color and highlight_color != "&H00EB6325") else red_highlight

        if style_mode == "white_box":
            # ⚪ KIỂU A: KHUNG TRẮNG BO TRÒN - CHỮ ĐEN ĐẬM / HIGHLIGHT ĐỎ RỰC RỠ (Che chữ gốc 100%)
            border_style = 3  # 3 = Opaque Box (Hộp nền bao quanh chữ)
            outline_val = max(4.0, effective_box_padding)
            shadow_val = 1.0  # Bóng nhẹ cho hộp nền nổi khối
            primary_c = primary_color or "&H00111111"       # Chữ Đen than chì siêu đậm & sắc nét
            highlight_c = active_highlight                  # Karaoke Chữ chạy MÀU ĐỎ RỰC RỠ (&HAABBGGRR: #FF0000)
            outline_c = outline_color or "&H00D0D5DD"       # Viền bo xám nhẹ tinh tế viền quanh hộp trắng
            back_c = "&H00FFFFFF"                           # Nền trắng tinh khiết che phủ hoàn toàn chữ gốc
            bold_val = 1                                    # Chữ in đậm rõ ràng

        elif style_mode == "dark_box":
            # ⚫ KIỂU B: HỘP ĐEN MỜ BO GÓC - CHỮ TRẮNG / HIGHLIGHT ĐỎ RỰC RỠ
            border_style = 3
            outline_val = max(4.0, effective_box_padding)
            shadow_val = 1.0
            primary_c = primary_color or "&H00FFFFFF"
            highlight_c = active_highlight
            outline_c = outline_color or "&H00333333"
            back_c = f"&H{backdrop_opacity_hex}000000"
            bold_val = 1

        else:
            # 🔤 KIỂU C: CHỮ VIỀN NỔI HAIRLINE TRONG SUỐT (Không hộp che)
            border_style = 1
            outline_val = max(0.8, round(effective_font_size * 0.075, 1))
            shadow_val = max(0.4, round(effective_font_size * 0.035, 1))
            primary_c = primary_color or "&H00FFFFFF"
            highlight_c = active_highlight
            outline_c = outline_color or "&H00000000"
            back_c = "&H00000000"
            bold_val = 0

        # Cập nhật lại ass_margin_v sau khi outline_val đã được xác định chắc chắn
        if border_style == 3:
            ass_margin_v = int(round(actual_margin_v + outline_val + (effective_font_size * 0.12)))
        else:
            ass_margin_v = actual_margin_v

        ass_header = f"""[Script Info]
; Script generated by ReviewMatThon Ultra Karaoke Engine
Title: {video_title}
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
PlayResX: {width}
PlayResY: {height}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
; Style Karaoke: BorderStyle={border_style}, Outline={outline_val}, Bold={bold_val}, MarginV={ass_margin_v}
Style: KaraokeSub,{font_name},{effective_font_size},{primary_c},{highlight_c},{outline_c},{back_c},{bold_val},0,0,0,100,100,0,0,{border_style},{outline_val},{shadow_val},2,15,15,{ass_margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        events = []
        for s in segments:
            text = (getattr(s, 'translated_text', '') or s.clean_text or s.text or "").strip()
            # Xóa sạch 100% số thứ tự đầu câu (1. , 02. , [1], Câu 1:...)
            text = re.sub(r'^(?:\[\s*\d+\s*\]|\(\s*\d+\s*\)|\{\s*\d+\s*\}|【\s*\d+\s*】)\s*[\.\:\-\–\—\s]*', '', text)
            text = re.sub(r'^(?:câu|thoại|đoạn|stt|dòng|line)\s*\d+\s*[\.\:\-\–\—\)\/\]\s]*\s*', '', text, flags=re.IGNORECASE)
            text = re.sub(r'^\d+\/\d+\s*[\.\:\-\–\—\s]*', '', text)
            text = re.sub(r'^\d+\s*[\.\:\-\–\—\)\/\]]+\s*', '', text)
            text = re.sub(r'^[^\w\s\(\[\{]+', '', text).strip()
            if not text:
                continue

            effective_end = max(s.end, s.start + 0.3)
            start_ts = format_ass_time(s.start)
            end_ts = format_ass_time(effective_end)
            dur = max(effective_end - s.start, 0.5)

            karaoke_text = cls.generate_word_karaoke_tags(text, dur)
            line = f"Dialogue: 0,{start_ts},{end_ts},KaraokeSub,,0,0,0,,{karaoke_text}"
            events.append(line)

        full_ass_content = ass_header + "\n".join(events) + "\n"

        out_path = Path(output_ass_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(full_ass_content)

        return str(out_path)
