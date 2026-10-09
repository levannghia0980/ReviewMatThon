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
        backdrop_opacity_hex: str = "80",       # Hộp nền mờ
        margin_v: Optional[int] = None,         # Cao độ phụ đề từ đáy (px hoặc %)
        box_style: str = "white_box",           # "white_box", "dark_box", "outline_only"
        box_padding: int = 8,                   # Độ to theo chiều dọc / padding của hộp che chữ gốc (px)
        blur_height: Optional[float] = None,    # Chiều cao dải mờ (%)
        sub_bottom_offset: Optional[float] = None # Khoảng cách từ đáy khung hình lên đáy dải mờ (%)
    ) -> str:
        """
        Tạo file phụ đề ASS chuẩn Điện ảnh Review (Static Text Vàng Kim - Tối ưu Render Siêu Tốc):
        - Chữ Vàng kim nổi bật (&H0000E6FF: #FFE600) + Viền đen bóng mờ sắc nét.
        - Text tĩnh hiển thị trọn vẹn cả câu (không chạy Karaoke \kf để CPU/GPU render nhanh gấp 5 lần).
        - Phụ đề căn CHÍNH GIỮA 100% tâm dải mờ Blur/Frosted Glass của FFmpeg, không lệch lên trên hay xuống dưới.
        """
        # 1. Scale font_size theo độ phân giải màn hình ASS (PlayResY)
        ref_h = 360.0
        scale_factor = max(1.0, height / ref_h)

        if font_size <= 28:
            effective_font_size = max(16, int(round(font_size * scale_factor)))
        else:
            effective_font_size = int(round(font_size * (height / 1080.0))) if height != 1080 else font_size

        # 2. CĂN TÂM QUANG HỌC: ĐẶT DÒNG CHỮ NẰM CHÍNH GIỮA 100% TÂM DẢI MỜ
        # Alignment=2 (Bottom-Center): actual_margin_v là khoảng cách từ đáy khung hình lên ĐÁY dòng chữ.
        # Tâm dòng chữ = actual_margin_v + (effective_font_size / 2).
        # Tâm dải mờ = box_bottom_px + (box_height_px / 2).
        # => actual_margin_v = Tâm dải mờ - (effective_font_size / 2) - visual_offset.
        b_offset = float(sub_bottom_offset if sub_bottom_offset is not None else 0.0)
        b_height = float(blur_height if blur_height is not None else 13.0)

        box_bottom_px = height * (b_offset / 100.0)
        box_height_px = height * (b_height / 100.0)
        box_center_y = box_bottom_px + (box_height_px / 2.0)

        # Tính margin_v để tâm chữ trùng khít tâm dải mờ (bù 1-2px độ dày viền chữ outline cho cân đối)
        calculated_margin_v = int(round(box_center_y - (effective_font_size / 2.0) - (effective_font_size * 0.06)))

        if sub_bottom_offset is not None or blur_height is not None:
            actual_margin_v = max(2, calculated_margin_v)
        elif margin_v is not None:
            if margin_v <= 100:
                actual_margin_v = max(4, int(round(height * (margin_v / 100.0))))
            else:
                actual_margin_v = int(margin_v)
        else:
            actual_margin_v = max(2, calculated_margin_v)

        # Scale box_padding từ preview sang độ phân giải thực tế
        if box_padding is not None and box_padding <= 25:
            effective_box_padding = max(4.0, round(box_padding * scale_factor, 1))
        else:
            effective_box_padding = float(box_padding if box_padding is not None else 8.0)

        # Màu chữ Trắng tinh khiết (&HAABBGGRR: &H00FFFFFF -> #FFFFFF trắng chuẩn sắc nét)
        white_color = "&H00FFFFFF"
        primary_c = primary_color if (primary_color and primary_color not in ("&H00111111", "&H0000E6FF")) else white_color
        outline_c = "&H00000000"                        # Viền đen sắc nét giúp chữ trắng nổi bật tuyệt đối
        back_c = "&H80000000"                           # Bóng đổ đen
        outline_val = max(2.8, round(effective_font_size * 0.09, 1))
        shadow_val = max(1.2, round(effective_font_size * 0.04, 1))

        ass_header = f"""[Script Info]
; Script generated by ReviewMatThon Subtitle Engine
Title: {video_title}
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
PlayResX: {width}
PlayResY: {height}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
; Chữ Vàng Kim Viền Đen Nổi Bật Trên Nền Túi Bóng Trắng Mờ (BorderStyle=1)
Style: SubtitleStyle,{font_name},{effective_font_size},{primary_c},{primary_c},{outline_c},{back_c},1,0,0,0,100,100,0,0,1,{outline_val},{shadow_val},2,25,25,{actual_margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        events = []
        for s in segments:
            # 1. Lấy duy nhất translated_text đã dịch tiếng Việt, KHÔNG dùng fallback tiếng Trung gốc
            text = (getattr(s, 'translated_text', '') or "").strip()
            # Nếu text còn chứa ký tự tiếng Trung Hán tự chưa dịch, bỏ qua để không rò rỉ lên sub
            if not text or re.search(r'[\u4e00-\u9fff]', text):
                continue

            # Xóa sạch 100% số thứ tự đầu câu (1. , 02. , [1], Câu 1:...)
            text = re.sub(r'^(?:\[\s*\d+\s*\]|\(\s*\d+\s*\)|\{\s*\d+\s*\}|【\s*\d+\s*】)\s*[\.\:\-\–\—\s]*', '', text)
            text = re.sub(r'^(?:câu|thoại|đoạn|stt|dòng|line)\s*\d+\s*[\.\:\-\–\—\)\/\]\s]*\s*', '', text, flags=re.IGNORECASE)
            text = re.sub(r'^\d+\/\d+\s*[\.\:\-\–\—\s]*', '', text)
            text = re.sub(r'^[^\w\s\(\[\{]+', '', text).strip()
            # Dọn dẹp triệt để các dị tật dấu câu kép như ,. hoặc ., hoặc ,, ở đuôi phụ đề
            text = re.sub(r'[\,\.]+\,', ',', text)
            text = re.sub(r'[\,\.]+\.', '.', text)
            if not text:
                continue

            effective_end = max(s.end, s.start + 0.3)
            start_ts = format_ass_time(s.start)
            end_ts = format_ass_time(effective_end)

            # Xuất dạng text tĩnh (không dùng thẻ \kf) -> Render siêu tốc, chữ vàng kim nét căng
            line = f"Dialogue: 0,{start_ts},{end_ts},SubtitleStyle,,0,0,0,,{text}"
            events.append(line)

        full_ass_content = ass_header + "\n".join(events) + "\n"

        out_path = Path(output_ass_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(full_ass_content)

        return str(out_path)

    @classmethod
    def slice_ass_for_interval(
        cls,
        source_ass_path: str,
        output_slice_path: str,
        start_sec: float,
        dur_sec: float
    ) -> str:
        """
        Cắt lát file .ass tổng thành file .ass cục bộ độc lập cho 1 phân đoạn video [start_sec, start_sec + dur_sec].
        - Chỉ giữ lại những câu thoại có giao cắt với khoảng [start_sec, start_sec + dur_sec].
        - Chuyển đổi timestamp về hệ tọa độ tương đối của phân đoạn:
            local_start = max(0.0, orig_start - start_sec)
            local_end   = min(dur_sec, orig_end - start_sec)
        - Đảm bảo khi FFmpeg render phân đoạn độc lập, subtitle hiển thị chuẩn xác 100%,
          tuyệt đối không bị reset về 00:00:00 của toàn bộ video.
        """
        if not source_ass_path or not os.path.exists(source_ass_path) or os.path.getsize(source_ass_path) == 0:
            return ""

        end_sec = start_sec + dur_sec

        def _parse_ass_timestamp(ts: str) -> float:
            try:
                parts = ts.strip().split(":")
                hrs = float(parts[0])
                mins = float(parts[1])
                secs = float(parts[2])
                return hrs * 3600.0 + mins * 60.0 + secs
            except Exception:
                return 0.0

        header_lines = []
        dialogue_lines = []
        in_events = False

        with open(source_ass_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                stripped = line.strip()
                if stripped.lower() == "[events]":
                    in_events = True
                    header_lines.append(line)
                    continue

                if not in_events:
                    header_lines.append(line)
                else:
                    if stripped.startswith("Format:"):
                        header_lines.append(line)
                    elif stripped.startswith("Dialogue:"):
                        # Dialogue: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
                        parts = line.split(",", 9)
                        if len(parts) == 10:
                            s_time = _parse_ass_timestamp(parts[1])
                            e_time = _parse_ass_timestamp(parts[2])

                            # Kiểm tra xem câu có nằm trong khoảng phân đoạn không
                            if e_time > start_sec and s_time < end_sec:
                                local_start = max(0.0, s_time - start_sec)
                                local_end = min(dur_sec, e_time - start_sec)
                                if local_end > local_start:
                                    parts[1] = format_ass_time(local_start)
                                    parts[2] = format_ass_time(local_end)
                                    dialogue_lines.append(",".join(parts))
                    else:
                        header_lines.append(line)

        out_p = Path(output_slice_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        with open(out_p, "w", encoding="utf-8") as f:
            f.writelines(header_lines)
            f.writelines(dialogue_lines)

        return str(out_p)

    @classmethod
    def create_source_mask_ass(
        cls,
        segments: list,
        output_ass_path: str,
        video_title: str = "Mask",
        width: int = 1920,
        height: int = 1080,
        source_font_size: int = 32,        # Cỡ chữ ước tính sub gốc Hán
        mask_height_pct: float = 12.0,     # Chiều cao dải che
        margin_v_pct: float = 8.0,         # % từ đáy video
        blur_radius: int = 8,              # Độ mờ viền
        bg_alpha_hex: str = "40",          # Nền tối mờ (&H40 = ~75% opaque, &H20 = ~87% opaque)
    ) -> str:
        """
        Tạo file ASS chứa dải nền che mờ động (Dynamic Mask) ôm trọn từng câu sub cũ:
        - Mỗi câu thoại tiếng Trung tự sinh box có độ rộng co giãn vừa vặn theo chiều dài câu đó.
        - Tự động xuất hiện và biến mất theo đúng timing (start -> end) của câu sub cũ.
        - Không bị dải mờ kéo dài ra 2 mép video.
        """
        from pathlib import Path

        actual_margin_v = max(4, int(round(height * (margin_v_pct / 100.0))))
        ref_h = 360.0
        scale_f = max(1.0, height / ref_h)
        effective_font = max(18, int(round(source_font_size * scale_f)))

        # PrimaryColour: &HFF000000 (Alpha = FF: chữ trong suốt 100%, không hiện chữ Hán ra màn hình)
        primary_transparent = "&HFF000000"
        # BackColour: &H20000000 (Alpha = 20: nền đen mờ 87% che vừa đủ sub cũ)
        back_color = f"&H{bg_alpha_hex}000000"
        outline_color = f"&H{bg_alpha_hex}000000"

        box_pad = max(12, int(round(12 * scale_f)))
        blur_tag = f"\\blur{blur_radius}"

        ass_header = f"""[Script Info]
; Dynamic Source Mask - Auto-fit subtitle length & timing
Title: {video_title} Source Mask
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
PlayResX: {width}
PlayResY: {height}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
; BorderStyle=3 (opaque box) phủ kín chữ Hán gốc, co giãn đúng độ dài text, Primary transparent chỉ để lại nền
Style: MaskBoxStyle,Arial,{effective_font},{primary_transparent},{primary_transparent},{outline_color},{back_color},1,0,0,0,100,100,0,0,3,{box_pad},0,2,20,20,{actual_margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        events = []
        for s in segments:
            raw_text = (getattr(s, 'text', '') or getattr(s, 'clean_text', '') or '').strip()
            if not raw_text:
                continue

            clean = re.sub(r'\{[^}]*\}', '', raw_text).strip()
            if not clean:
                continue

            t_start = format_ass_time(s.start)
            t_end = format_ass_time(max(s.end, s.start + 0.2))

            # Dialogue vẽ box che phủ vừa khớp từng ký tự gốc Hán + blur viền mềm mại
            line = f"Dialogue: 0,{t_start},{t_end},MaskBoxStyle,,0,0,0,,{{{blur_tag}}}{clean}"
            events.append(line)

        full_content = ass_header + "\n".join(events) + "\n"
        out_path = Path(output_ass_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(full_content)
        return str(out_path)

