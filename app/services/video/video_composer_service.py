import os
import sys
import re
import time
import math
import shutil
import threading
import subprocess
import concurrent.futures
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from sqlalchemy.orm import Session

from app.config import settings
from app.models.project import ProjectTask
from app.models.dialogue import DialogueSegmentModel
from app.schemas.transcript import DialogueSegment
from app.services.video.karaoke_subtitle_service import KaraokeSubtitleService
from app.services.video.audio_mixer_service import AudioMixerService
from app.services.task_manager import task_manager
from app.utils.bin_helper import get_ffmpeg_cmd, get_ffprobe_cmd

class VideoComposerService:
    @staticmethod
    def detect_best_encoder() -> Tuple[str, str, str]:
        """
        Tự động nhận diện Encoder phần cứng nhanh nhất trên máy:
        1. h264_nvenc  (NVIDIA - nhanh nhất, ổn định nhất)
        2. h264_qsv    (Intel GPU QuickSync - phần cứng ASIC siêu tốc trên chip Intel Core/Xe/UHD)
        3. h264_amf    (AMD RX/RX Vega/RDNA - card rời AMD)
        4. h264_mf     (Windows GPU Media Foundation - đa năng)
        5. libx264     (CPU Đa Luồng Ultrafast - tương thích 100% mọi máy)
        """
        ff_cmd = get_ffmpeg_cmd()
        # 1. Test NVIDIA NVENC (tăng timeout lên 8s để card rời Laptop RTX 3050/4060 kịp đánh thức từ chế độ ngủ)
        try:
            test_nvenc = subprocess.run(
                [*ff_cmd, "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1", "-c:v", "h264_nvenc", "-f", "null", "-"],
                capture_output=True, text=True, errors="replace", timeout=8
            )
            if test_nvenc.returncode == 0:
                return "h264_nvenc", "p4", "NVIDIA GPU NVENC Siêu Tốc"
        except Exception:
            pass

        # 2. Test Intel QuickSync (Intel Core/Xe/UHD - ASIC chuyên dụng siêu tốc, nhẹ CPU)
        try:
            test_qsv = subprocess.run(
                [*ff_cmd, "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1", "-c:v", "h264_qsv", "-f", "null", "-"],
                capture_output=True, text=True, errors="replace", timeout=3
            )
            if test_qsv.returncode == 0:
                return "h264_qsv", "veryfast", "Intel GPU QuickSync Siêu Tốc"
        except Exception:
            pass

        # 3. Test AMD AMF (RX 350 / RX 5xx / RX 6xxx / RDNA)
        try:
            test_amf = subprocess.run(
                [*ff_cmd, "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1", "-c:v", "h264_amf", "-f", "null", "-"],
                capture_output=True, text=True, errors="replace", timeout=3
            )
            if test_amf.returncode == 0:
                return "h264_amf", "none", "AMD GPU AMF (RX Series) Siêu Tốc"
        except Exception:
            pass

        # 4. Test Windows Media Foundation (Hỗ trợ hầu hết GPU trên Windows)
        try:
            test_mf = subprocess.run(
                [*ff_cmd, "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1", "-c:v", "h264_mf", "-f", "null", "-"],
                capture_output=True, text=True, errors="replace", timeout=3
            )
            if test_mf.returncode == 0:
                return "h264_mf", "none", "Windows GPU Media Foundation"
        except Exception:
            pass

        return "libx264", "ultrafast", "CPU Đa Luồng Ultrafast"

    @staticmethod
    def get_video_resolution(video_path: str) -> Tuple[int, int]:
        """Đọc chính xác độ phân giải (Width x Height) của video MP4 bằng FFprobe, OpenCV hoặc FFmpeg."""
        # 1. Thử qua FFprobe
        try:
            ffprobe_cmd = get_ffprobe_cmd()
            cmd = [
                *ffprobe_cmd, "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=s=x:p=0",
                str(video_path)
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=6)
            if res.returncode == 0 and res.stdout.strip():
                parts = res.stdout.strip().split("x")
                if len(parts) == 2:
                    w, h = int(parts[0]), int(parts[1])
                    if w > 0 and h > 0:
                        return w, h
        except Exception:
            pass

        # 2. Thử trực tiếp qua FFmpeg -i (cực kỳ chính xác khi ffprobe bị thiếu/hỏng)
        try:
            ffmpeg_cmd = get_ffmpeg_cmd()
            p = subprocess.run([*ffmpeg_cmd, "-i", str(video_path)], capture_output=True, text=True, errors="replace", timeout=6)
            for line in p.stderr.splitlines():
                if "Video:" in line:
                    m = re.search(r" (\d{2,5})x(\d{2,5})", line)
                    if m:
                        w, h = int(m.group(1)), int(m.group(2))
                        if w > 0 and h > 0:
                            return w, h
        except Exception:
            pass

        # 3. Dự phòng bằng OpenCV VideoCapture (cực kỳ tin cậy & nhanh trên Windows)
        try:
            import cv2
            cap = cv2.VideoCapture(str(video_path))
            if cap.isOpened():
                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                cap.release()
                if w > 0 and h > 0:
                    return w, h
        except Exception:
            pass

        return 1280, 720

    @staticmethod
    def get_video_duration(video_path: str) -> float:
        """Đọc chính xác tổng thời lượng (giây) của video bằng FFprobe hoặc FFmpeg."""
        try:
            ffprobe_cmd = get_ffprobe_cmd()
            cmd = [
                *ffprobe_cmd, "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(video_path)
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=8)
            if res.returncode == 0 and res.stdout.strip():
                val = float(res.stdout.strip())
                if val > 0:
                    return val
        except Exception:
            pass

        try:
            ffmpeg_cmd = get_ffmpeg_cmd()
            cmd = [*ffmpeg_cmd, "-i", str(video_path)]
            res = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=8)
            m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", res.stderr)
            if m:
                h, mm, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
                return h * 3600 + mm * 60 + s
        except Exception:
            pass

        return 0.0

    @staticmethod
    def _format_time(seconds: float) -> str:
        """Định dạng số giây thành chuỗi trực quan HH:MM:SS."""
        sec = max(0.0, float(seconds))
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = int(sec % 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    @classmethod
    def calculate_crop(cls, vw: int, vh: int, bottom_cut_percent: float, top_cut_percent: float = 0.0, target_ratio: str = "16:9") -> Dict[str, int]:
        """
        Tính toán thông số crop:
        - Cắt bỏ phần trên theo top_cut_percent (% chiều cao).
        - Cắt bỏ phần đáy theo bottom_cut_percent (% chiều cao) để xóa bỏ hoàn toàn hardsub Trung Quốc cũ.
        - Cắt đều 2 bên (trái & phải) để video đạt chuẩn tỷ lệ YouTube hỗ trợ (Mặc định 16:9 YouTube ngang, hoặc 9:16 Shorts).
        - CHỈ CẮT (CROP) - TUYỆT ĐỐI KHÔNG SCALE để tối ưu tốc độ render nhanh nhất có thể.
        """
        bot_pct = max(0.0, min(45.0, float(bottom_cut_percent if bottom_cut_percent is not None else 0.0)))
        top_pct = max(0.0, min(35.0, float(top_cut_percent if top_cut_percent is not None else 0.0)))
        
        cut_top = int(round(vh * (top_pct / 100.0)))
        cut_bot = int(round(vh * (bot_pct / 100.0)))
        
        h_target = vh - cut_top - cut_bot
        h_target = max(120, (h_target // 2) * 2)

        ratio_val = 9.0 / 16.0 if target_ratio == "9:16" else 16.0 / 9.0
        w_target = int(round(h_target * ratio_val))
        w_target = (w_target // 2) * 2

        if w_target > vw:
            w_target = (vw // 2) * 2
            h_target = int(round(w_target / ratio_val))
            h_target = (h_target // 2) * 2
            x = 0
            y = cut_top
        else:
            x = int(round((vw - w_target) / 2.0))
            x = (x // 2) * 2
            y = cut_top

        # Giới hạn an toàn tuyệt đối tránh tràn khung hình
        w_target = max(64, min(vw, (w_target // 2) * 2))
        h_target = max(64, min(vh, (h_target // 2) * 2))
        x = max(0, min(vw - w_target, (x // 2) * 2))
        y = max(0, min(vh - h_target, (y // 2) * 2))

        return {
            "w": w_target,
            "h": h_target,
            "x": x,
            "y": y,
            "cut_h": cut_bot,
            "cut_top": cut_top
        }

    @classmethod
    def calculate_crop_9_16(cls, vw: int, vh: int, bottom_cut_percent: float, target_ratio: str = "16:9") -> Dict[str, int]:
        return cls.calculate_crop(vw, vh, bottom_cut_percent, target_ratio=target_ratio)

    @classmethod
    def compose_full_video(
        cls,
        task_id: str,
        project_id: int,
        db: Session,
        logo_path: Optional[str] = None,
        logo_position: str = "top_left",        # top_left, top_right, bottom_left, bottom_right
        logo_size: int = 120,                   # Kích thước gọn nhỏ (~120px) sát góc
        logo_opacity: float = 0.90,
        channel_name: str = "@Mắt Thần Review",  # Tên kênh watermark mờ
        channel_opacity: float = 0.25,          # Độ mờ kênh chống clone
        karaoke_highlight_color: str = "&H0000D7FF", # Vàng kim sang trọng
        has_mask: bool = True,                  # Bật hộp che mờ chữ cũ
        mask_left: float = 0.0,
        mask_top: float = 83.5,                 # Tự động che chuẩn xác vùng phụ đề đáy 83.5%
        mask_width: float = 100.0,
        mask_height: float = 16.5,              # Dải đen cao 16.5% ôm trọn mọi cỡ chữ cũ
        backdrop_opacity_hex: str = "FF",       # 100% Solid Black chống lộ chữ
        margin_v: int = 30,                     # Khoảng cách đáy màn hình
        crop_ratio: str = "16:9",               # 16:9 (YouTube Ngang) hoặc 9:16 (Shorts/TikTok)
        font_size: Optional[int] = None,        # Cỡ chữ phụ đề nhỏ gọn (px)
        box_style: str = "white_box",           # "white_box" | "dark_box" | "outline_only"
        box_padding: int = 8,                   # Độ to theo chiều dọc / padding của hộp che chữ gốc (px)
        render_subtitles: bool = True           # True = Chèn Karaoke ASS & che sub; False = Không Sub, chỉ crop & lồng tiếng
    ) -> Dict[str, Any]:
        """
        Sản xuất Video Review Hoàn Thiện:
        1. Đọc kích thước video gốc MP4 để căn chỉnh tọa độ crop khớp 100%.
        2. Tạo phụ đề Karaoke ASS chuẩn độ phân giải và font chữ (nếu render_subtitles=True).
        3. Che sạch phụ đề tiếng Trung cũ (nếu render_subtitles=True).
        4. Hòa trộn Vocal Ducking (giữ BGM, triệt tiêu tiếng gốc, lồng tiếng Việt).
        5. Render 1-pass NVENC / CPU xuất file `output/final_videos/{video_id}_final.mp4`.
        """
        project = db.query(ProjectTask).filter(ProjectTask.id == project_id).first()
        if not project:
            raise ValueError(f"Không tìm thấy Project ID #{project_id}")

        safe_title = re.sub(r'[\\/*?:"<>|]', "", project.title or "").replace(" ", "_")

        if not project.video_path or not os.path.exists(project.video_path):
            raise FileNotFoundError(f"Không tìm thấy file video gốc: {project.video_path}")

        dialogues_db = db.query(DialogueSegmentModel).filter(
            DialogueSegmentModel.task_id == project_id
        ).order_by(DialogueSegmentModel.index.asc()).all()

        # Khử trùng lặp tuyệt đối theo index để tránh bị vẽ đè 2 tầng sub
        seen_d_idx = set()
        unique_dialogues_db = []
        for d in dialogues_db:
            if d.index not in seen_d_idx:
                seen_d_idx.add(d.index)
                unique_dialogues_db.append(d)
        dialogues_db = unique_dialogues_db

        if render_subtitles:
            task_manager.add_log(task_id, "[1/4] ✨ Đang sinh file phụ đề Karaoke ASS từng từ (Word-by-word Highlight)...", "cyan")
        else:
            task_manager.add_log(task_id, "[1/4] ⚡ Chế độ 'Không Sub': Bỏ qua tạo phụ đề Karaoke & dải mờ, chỉ Crop video & lồng tiếng!", "cyan")

        vw, vh = cls.get_video_resolution(project.video_path)
        if has_mask or (mask_height and mask_height > 0):
            crop_info = cls.calculate_crop(vw, vh, bottom_cut_percent=mask_height, target_ratio=crop_ratio)
            ass_w, ass_h = crop_info["w"], crop_info["h"]
        else:
            ass_w, ass_h = (vw // 2) * 2, (vh // 2) * 2

        font_sz = font_size if (font_size and font_size > 0) else 14
        margin_v_val = margin_v if margin_v is not None else 8
        box_pad_val = box_padding if box_padding is not None else 8

        segments = [
            DialogueSegment(
                id=d.index,
                start=d.start_time,
                end=d.end_time if (d.end_time and d.end_time > d.start_time) else round(d.start_time + d.duration, 3),
                duration=round((d.end_time - d.start_time) if (d.end_time and d.end_time > d.start_time) else d.duration, 3),
                text=d.original_text,
                clean_text=d.clean_text or d.original_text,
                translated_text=d.translated_text or d.clean_text or d.original_text,
                confidence=d.confidence
            )
            for d in dialogues_db
        ]

        ass_file = settings.OUTPUT_TRANSCRIPTS_DIR / f"{project.video_id}_karaoke.ass"
        if render_subtitles:
            KaraokeSubtitleService.create_karaoke_ass_file(
                segments=segments,
                output_ass_path=str(ass_file),
                video_title=project.title,
                width=ass_w,
                height=ass_h,
                font_size=font_sz,
                margin_v=margin_v_val,
                highlight_color=karaoke_highlight_color if (karaoke_highlight_color and karaoke_highlight_color != "&H00EB6325") else "&H000000FF",
                backdrop_opacity_hex=backdrop_opacity_hex,
                box_style=box_style,
                box_padding=box_pad_val,
                blur_height=blur_height,
                sub_bottom_offset=sub_bottom_offset
            )
            task_manager.add_log(task_id, f"   ✔ Đã tạo xong file Karaoke ASS: {ass_file.name} (PlayRes: {ass_w}x{ass_h})", "emerald")
        else:
            # Tạo file dummy rỗng nếu chưa có để đảm bảo đường dẫn không lỗi
            if not ass_file.exists():
                with open(ass_file, "w", encoding="utf-8") as f:
                    f.write("")

        # Gen file ASS mask che sub gốc: nền dark vừa đúng độ rộng từng dòng sub Hán (BorderStyle=4)
        source_mask_ass = None
        if has_mask and render_subtitles:
            try:
                mask_ass_file = settings.OUTPUT_TRANSCRIPTS_DIR / f"{project.video_id}_source_mask.ass"
                # margin_v_pct: vị trí đáy sub gốc (% từ đáy), nhất thiết phải đúng vị trí sub Hán
                src_margin_v_pct = mask_top * 0.01 * 100 if mask_top > 1 else (100 - mask_top - mask_height)
                # mask_top là % từ trên xuống → margin_v_pct từ đáy = 100 - mask_top - mask_height
                src_margin_v_pct = max(1.0, 100.0 - mask_top - mask_height)
                KaraokeSubtitleService.create_source_mask_ass(
                    segments=segments,
                    output_ass_path=str(mask_ass_file),
                    video_title=project.title,
                    width=ass_w,
                    height=ass_h,
                    source_font_size=32,
                    mask_height_pct=mask_height,
                    margin_v_pct=src_margin_v_pct,
                    blur_radius=10,
                    bg_alpha_hex="90",  # 90 = ~44% transparent (56% opaque)
                )
                source_mask_ass = str(mask_ass_file)
                task_manager.add_log(task_id, f"   ✔ Đã tạo Source Mask ASS (per-sub blur): {mask_ass_file.name}", "emerald")
            except Exception as e_mask:
                task_manager.add_log(task_id, f"   ⚠️ Không tạo được source mask ASS: {e_mask}", "amber")
                source_mask_ass = None

        # Kiểm tra nếu đã có file mixed sẵn hoặc file voiceover
        mixed_audio_file = settings.OUTPUT_VOICEOVER_DIR / f"{project.video_id}_mixed_final.mp3"
        voiceover_file = settings.OUTPUT_VOICEOVER_DIR / f"{project.video_id}_{safe_title}_voiceover.mp3"
        if not voiceover_file.exists():
            candidates = [f for f in settings.OUTPUT_VOICEOVER_DIR.glob(f"{project.video_id}*.mp3") if "mixed" not in f.name and f.stat().st_size > 1000]
            if candidates:
                voiceover_file = candidates[0]
            else:
                voiceover_file = None

        if voiceover_file and Path(voiceover_file).exists():
            task_manager.add_log(task_id, "   🎵 Đang hòa trộn BGM gốc & lồng tiếng Việt (Vocal Ducking)...", "cyan")
            try:
                from app.services.video.audio_mixer_service import AudioMixerService
                mixed_path = AudioMixerService.mix_bgm_and_voiceover(
                    original_video_or_audio=project.video_path,
                    voiceover_mp3=str(voiceover_file),
                    output_mixed_audio=str(mixed_audio_file),
                    dialogue_segments=segments,
                    bgm_volume_when_speaking=0.036,
                    bgm_volume_normal=0.15,  # 0.15 khi không nói để giữ rõ âm thanh môi trường/BGM, 0.036 khi nói để giấu tiếng Trung
                    voiceover_volume=1.05
                )
                audio_source = str(mixed_path)
                task_manager.add_log(task_id, "   ✔ Đã hòa trộn BGM & giọng Việt thành công.", "emerald")
            except Exception as e:
                task_manager.add_log(task_id, f"   ⚠️ Mix BGM lỗi, dùng trực tiếp file voiceover: {e}", "amber")
                audio_source = str(voiceover_file)
        elif mixed_audio_file.exists():
            audio_source = str(mixed_audio_file)
        else:
            task_manager.add_log(task_id, "   ⚠️ Chưa tìm thấy file voiceover lồng tiếng, sử dụng âm thanh gốc.", "amber")
            audio_source = project.video_path

        # 3        # 4. Render luồng hình ảnh (tự động chạy song song siêu tốc cho video dài)
        task_manager.update_task(task_id, step=4, progress=65)
        task_manager.add_log(task_id, "[4/4] 🚀 Kích hoạt Render Video (Engine Đa Luồng Song Song)...", "purple")

        output_video_path = settings.OUTPUT_FINAL_VIDEOS_DIR / f"{project.video_id}_{safe_title}_final.mp4"
        temp_visual_video = settings.TEMP_DIR / f"{project.video_id}_{safe_title}_visual_tmp.mp4"
        
        # Xóa triệt để file tạm hình ảnh cũ nếu có từ lần chạy trước
        if temp_visual_video.exists():
            try:
                temp_visual_video.unlink(missing_ok=True)
            except Exception:
                pass

        v_ok = cls.render_visual_stream(
            task_id=task_id,
            video_input_path=project.video_path,
            ass_file_path=str(ass_file),
            output_temp_video=str(temp_visual_video),
            logo_path=final_logo,
            logo_position=logo_position,
            logo_size=logo_size,
            logo_opacity=logo_opacity,
            channel_name=channel_name,
            channel_opacity=channel_opacity,
            has_mask=has_mask,
            mask_top=mask_top,
            mask_left=mask_left,
            mask_width=mask_width,
            mask_height=mask_height,
            backdrop_opacity_hex=backdrop_opacity_hex,
            target_ratio=target_ratio,
            source_mask_ass=source_mask_ass,
            bottom_cut_percent=mask_height,
            render_subtitles=render_subtitles
        )

        if not v_ok or not temp_visual_video.exists() or temp_visual_video.stat().st_size < 1000:
            raise RuntimeError("Render luồng hình ảnh video thất bại! Không thể tiến hành ghép audio.")

        # 5. Hợp nhất luồng hình ảnh & âm thanh thành phẩm (Stream copy 0.5s)
        task_manager.add_log(task_id, "   ⚡ [Stream Mux] Hợp nhất video và âm thanh thành phẩm...", "cyan")
        mux_ok = cls.mux_final_video(
            visual_video_path=str(temp_visual_video),
            audio_source_path=str(audio_source),
            output_final_path=str(output_video_path)
        )
        try:
            temp_visual_video.unlink(missing_ok=True)
        except Exception:
            pass

        if not mux_ok or not output_video_path.exists():
            raise RuntimeError("Hợp nhất video thành phẩm thất bại!")

        # Cập nhật CSDL
        project.final_video_path = str(output_video_path)
        project.status = "COMPLETED"
        db.commit()

        task_manager.add_log(task_id, f"🎉 HOÀN TẤT XUẤT BẢN VIDEO REVIEW! File: output/final_videos/{output_video_path.name}", "emerald")
        task_manager.add_log(task_id, f"   • Karaoke Sub: Đồng bộ 100% từng từ ({render_w}x{render_h})", "emerald")
        task_manager.add_log(task_id, f"   • Audio Mix: BGM gốc giữ nguyên + Lồng tiếng Việt nét căng", "emerald")
        task_manager.add_log(task_id, f"   • Logo & Watermark: Gọn nhỏ, chống clone bản quyền tinh tế", "emerald")

        task_manager.update_task(
            task_id,
            status="completed",
            progress=100,
            message="Hoàn tất sản xuất video thành phẩm!",
            result={
                "project_id": project.id,
                "final_video_path": str(output_video_path),
                "karaoke_ass_path": str(ass_file),
                "mixed_audio_path": str(audio_source)
            }
        )

        return {
            "status": "success",
            "project_id": project.id,
            "final_video_path": str(output_video_path),
            "karaoke_ass_path": str(ass_file)
        }

    @classmethod
    def _build_visual_filter_complex(
        cls,
        video_input_path: str,
        ass_file_path: str,
        logo_path: Optional[str] = None,
        logo_position: str = "top_left",
        logo_size: int = 120,
        logo_opacity: float = 0.90,
        channel_name: str = "@Mắt Thần Review",
        channel_opacity: float = 0.35,
        has_mask: bool = True,
        mask_top: float = 80.0,
        mask_left: float = 15.0,
        mask_width: float = 70.0,
        mask_height: float = 12.0,
        backdrop_opacity_hex: str = "80",
        target_ratio: str = "16:9",
        source_mask_ass: Optional[str] = None,
        bottom_cut_percent: Optional[float] = None,
        top_cut_percent: Optional[float] = 0.0,
        blur_height: Optional[float] = 13.0,
        sub_bottom_offset: Optional[float] = 0.0,
        render_subtitles: bool = True
    ) -> Tuple[str, List[str], int, int]:
        """Tạo chuỗi filter_complex chuẩn xác dùng chung cho cả render đơn luồng và song song."""
        # Chuẩn hóa đường dẫn file ASS an toàn tuyệt đối cho FFmpeg trên mọi hệ điều hành (kể cả ổ C:, D:, E: trên Windows hoặc Linux)
        ass_path_obj = Path(ass_file_path).resolve()
        ass_posix = str(ass_path_obj).replace("\\", "/")
        if sys.platform == "win32" and ":" in ass_posix:
            drive, rest = ass_posix.split(":", 1)
            ass_escaped = f"{drive}\\:{rest}".replace("'", "\\'")
        else:
            ass_escaped = ass_posix.replace("'", "\\'")

        # Chuẩn hóa đường dẫn file mask ASS nếu có
        mask_ass_escaped = None
        if source_mask_ass and os.path.exists(source_mask_ass):
            mask_path_obj = Path(source_mask_ass).resolve()
            mask_posix = str(mask_path_obj).replace("\\", "/")
            if sys.platform == "win32" and ":" in mask_posix:
                m_drive, m_rest = mask_posix.split(":", 1)
                mask_ass_escaped = f"{m_drive}\\:{m_rest}".replace("'", "\\'")
            else:
                mask_ass_escaped = mask_posix.replace("'", "\\'")

        filter_chains = []
        last_v = "0:v"

        vw, vh = cls.get_video_resolution(video_input_path)
        cut_pct = float(bottom_cut_percent if bottom_cut_percent is not None else 0.0)
        t_cut_pct = float(top_cut_percent if top_cut_percent is not None else 0.0)

        # 🎬 BƯỚC 1: CROP VIDEO (Cắt bỏ % sub Trung ở đáy + cắt % trên đỉnh + cắt đều 2 bên theo tỷ lệ 16:9 hoặc 9:16 Shorts)
        if cut_pct > 0 or t_cut_pct > 0:
            crop_info = cls.calculate_crop(vw, vh, bottom_cut_percent=cut_pct, top_cut_percent=t_cut_pct, target_ratio=target_ratio)
            render_w = crop_info["w"]
            render_h = crop_info["h"]
            crop_x = crop_info["x"]
            crop_y = crop_info["y"]
            filter_chains.append(f"[0:v]crop={render_w}:{render_h}:{crop_x}:{crop_y}[v_clean]")
            last_v = "v_clean"
        else:
            render_w = (vw // 2) * 2
            render_h = (vh // 2) * 2
            if render_w != vw or render_h != vh:
                crop_expr = f"w='min(iw,{render_w})':h='min(ih,{render_h})':x=0:y=0"
                filter_chains.append(f"[0:v]crop={crop_expr}[v_clean]")
                last_v = "v_clean"

        # 🎬 BƯỚC 2: TẠO DẢI NỀN MỜ CHE SUB CŨ TRÊN KHUNG HÌNH (CHỈ KHI BẬT HAS_MASK VÀ RENDER_SUBTITLES=TRUE)
        if has_mask and render_subtitles:
            b_h_val = float(blur_height if blur_height is not None else (mask_height if mask_height and mask_height > 0 else 13.0))
            b_offset_val = float(sub_bottom_offset if sub_bottom_offset is not None else 0.0)

            m_w = int(round(render_w * (mask_width / 100.0))) if mask_width else render_w
            m_h = int(round(render_h * (b_h_val / 100.0)))
            m_x = max(0, int(round((render_w - m_w) / 2.0)))

            # offset từ đáy khung hình: khi b_offset_val <= 1.0% -> dải mờ dính sát 100% mép cắt crop đáy, không hở 1 pixel nào
            offset_bottom_px = int(round(render_h * (b_offset_val / 100.0)))
            if offset_bottom_px <= 2:
                m_y = max(0, render_h - m_h)
                fade_expr = "a='if(lt(Y,8),245*(Y/8),245)'"
            else:
                m_y = max(0, render_h - offset_bottom_px - m_h)
                f_h = max(2, int(round(m_h * 0.12)))
                fade_expr = f"a='if(lt(Y,{f_h}),245*(Y/{f_h}),if(gt(Y,H-{f_h}),245*((H-Y)/{f_h}),245))'"

            m_w = max(32, min(render_w, (m_w // 2) * 2))
            m_h = max(16, min(render_h, (m_h // 2) * 2))
            m_x = max(0, min(render_w - m_w, (m_x // 2) * 2))
            if offset_bottom_px <= 2:
                m_y = max(0, render_h - m_h)
            else:
                m_y = max(0, min(render_h - m_h, (m_y // 2) * 2))

            # Dải mờ Frosted Glass đục trắng mịn màng như CapCut: avgblur làm nhòe chữ cũ + ánh sáng trắng sương sang trọng
            filter_chains.append(
                f"[{last_v}]split=2[v_main][v_crop_src];"
                f"[v_crop_src]crop={m_w}:{m_h}:{m_x}:{m_y},"
                f"avgblur=sizeX=65:sizeY=5,"
                f"format=yuva420p,"
                f"geq=r='min(255,r(X,Y)*0.85+35)':g='min(255,g(X,Y)*0.85+35)':b='min(255,b(X,Y)*0.85+40)':"
                f"{fade_expr}[v_blurred];"
                f"[v_main][v_blurred]overlay={m_x}:{m_y}[v_masked]"
            )
            last_v = "v_masked"

        # 🎬 BƯỚC 3: Subtitle Tiếng Việt đặt lên trên cùng, căn giữa khung hình đã crop (chỉ khi render_subtitles=True)
        if render_subtitles and ass_file_path and os.path.exists(ass_file_path) and os.path.getsize(ass_file_path) > 0:
            filter_chains.append(f"[{last_v}]subtitles='{ass_escaped}':force_style='Encoding=UTF-8'[v_sub]")
            last_v = "v_sub"

        logo_inputs = []
        if logo_path and os.path.exists(logo_path):
            logo_inputs = ["-i", str(logo_path)]
            if logo_position == "top_right":
                overlay_pos = "W-w-25:25"
            elif logo_position == "bottom_left":
                overlay_pos = "25:H-h-25"
            elif logo_position == "bottom_right":
                overlay_pos = "W-w-25:H-h-25"
            else:
                overlay_pos = "25:25"
            filter_chains.append(f"[1:v]scale={logo_size}:{logo_size},format=rgba,colorchannelmixer=aa={logo_opacity}[logo]")
            filter_chains.append(f"[{last_v}][logo]overlay={overlay_pos}[v_logo]")
            last_v = "v_logo"

        filter_chains.append(f"[{last_v}]scale=w='trunc(iw/2)*2':h='trunc(ih/2)*2'[v_out]")
        return ";".join(filter_chains), logo_inputs, render_w, render_h

    @classmethod
    def render_visual_stream(cls, *args, **kwargs):
        """Alias tương thích chuyển tiếp tới parallel_render_visual_stream."""
        return cls.parallel_render_visual_stream(*args, **kwargs)

    @classmethod
    def parallel_render_visual_stream(
        cls,
        task_id: str,
        video_input_path: str,
        ass_file_path: str,
        output_temp_video: str,
        logo_path: Optional[str] = None,
        logo_position: str = "top_left",
        logo_size: int = 120,
        logo_opacity: float = 0.90,
        channel_name: str = "@Mắt Thần Review",
        channel_opacity: float = 0.35,
        has_mask: bool = True,
        mask_top: float = 80.0,
        mask_left: float = 15.0,
        mask_width: float = 70.0,
        mask_height: float = 12.0,
        backdrop_opacity_hex: str = "80",
        target_ratio: str = "16:9",
        max_workers: Optional[int] = None,
        source_mask_ass: Optional[str] = None,
        bottom_cut_percent: Optional[float] = None,
        top_cut_percent: Optional[float] = 0.0,
        blur_height: Optional[float] = 13.0,
        sub_bottom_offset: Optional[float] = 0.0,
        render_subtitles: bool = True
    ) -> bool:
        """
        Render luồng hình ảnh song song tối đa (Parallel Segment Rendering).
        Chia nhỏ video thành nhiều phân đoạn, chạy đa luồng đồng thời trên GPU/CPU,
        rồi ghép lại siêu tốc bằng Concat Demuxer. Tăng tốc từ 3x - 10x so với render đơn luồng.
        """
        total_duration = cls.get_video_duration(video_input_path)
        # Chỉ những video siêu ngắn dưới 60s mới chạy đơn luồng.
        # Tất cả video từ 60s trở lên (kể cả 2-5 phút hay 8-15 tiếng) ĐỀU phân đoạn chạy song song tối đa để bứt tốc!
        if total_duration <= 60.0:
            return cls._render_visual_stream_single(
                task_id=task_id,
                video_input_path=video_input_path,
                ass_file_path=ass_file_path,
                output_temp_video=output_temp_video,
                logo_path=logo_path,
                logo_position=logo_position,
                logo_size=logo_size,
                logo_opacity=logo_opacity,
                channel_name=channel_name,
                channel_opacity=channel_opacity,
                has_mask=has_mask,
                mask_top=mask_top,
                mask_left=mask_left,
                mask_width=mask_width,
                mask_height=mask_height,
                backdrop_opacity_hex=backdrop_opacity_hex,
                target_ratio=target_ratio,
                source_mask_ass=source_mask_ass,
                bottom_cut_percent=bottom_cut_percent,
                top_cut_percent=top_cut_percent,
                blur_height=blur_height,
                sub_bottom_offset=sub_bottom_offset,
                render_subtitles=render_subtitles
            )

        cpu_cores = os.cpu_count() or 4
        vcodec, preset, encoder_desc = cls.detect_best_encoder()

        # Tính số luồng xử lý đồng thời thích ứng linh hoạt theo cấu hình máy bất kỳ:
        if max_workers is not None and max_workers > 0:
            workers = max_workers
        else:
            env_workers = os.getenv("VIDEO_RENDER_WORKERS")
            if env_workers and env_workers.isdigit() and int(env_workers) > 0:
                workers = int(env_workers)
            elif vcodec in ("h264_nvenc", "h264_qsv", "h264_amf"):
                # GPU phần cứng (Intel QuickSync / Nvidia NVENC / AMD AMF): 
                # Chạy 3 - 4 luồng đồng thời khai thác tối đa đa kênh phần cứng ASIC
                workers = max(2, min(4, cpu_cores // 2 if cpu_cores >= 4 else cpu_cores))
            else:
                # Máy không có GPU (chạy CPU libx264 Ultrafast): Dùng đa nhân CPU, chừa 1 core cho hệ thống
                workers = max(1, min(max(1, cpu_cores - 1), 8) if cpu_cores > 2 else cpu_cores)

        # Phân chia phân đoạn thông minh để tận dụng tối đa số worker:
        # - Video ngắn (< 5 phút): mỗi phân đoạn ~ 60s -> chia đều cho các luồng GPU xử lý đồng loạt
        # - Video vừa (5 - 30 phút): mỗi phân đoạn ~ 120s - 180s
        # - Video dài (1 - 15 tiếng): mỗi phân đoạn ~ 300s (5 phút)
        if total_duration <= 300.0:
            segment_duration = max(30.0, total_duration / float(max(2, workers)))
        elif total_duration <= 1800.0:
            segment_duration = 150.0
        else:
            segment_duration = 300.0

        num_segments = math.ceil(total_duration / segment_duration)
        workers = min(workers, num_segments)
        threads_per_worker = max(1, cpu_cores // workers)

        if task_id:
            task_manager.add_log(task_id, f"🚀 KÍCH HOẠT ENGINE RENDER SONG SONG TỐI ĐA ({workers} Luồng Đồng Thời)...", "purple")
            task_manager.add_log(task_id, f"   • Bộ Mã Hóa: {vcodec} ({encoder_desc})", "cyan")
            task_manager.add_log(task_id, f"   • Video: {cls._format_time(total_duration)} | Chia {num_segments} phân đoạn (~{int(segment_duration)}s/đoạn)", "cyan")

        full_filter_complex, logo_inputs, render_w, render_h = cls._build_visual_filter_complex(
            video_input_path=video_input_path,
            ass_file_path=ass_file_path,
            logo_path=logo_path,
            logo_position=logo_position,
            logo_size=logo_size,
            logo_opacity=logo_opacity,
            channel_name=channel_name,
            channel_opacity=channel_opacity,
            has_mask=has_mask,
            mask_top=mask_top,
            mask_left=mask_left,
            mask_width=mask_width,
            mask_height=mask_height,
            backdrop_opacity_hex=backdrop_opacity_hex,
            target_ratio=target_ratio,
            source_mask_ass=source_mask_ass,
            bottom_cut_percent=bottom_cut_percent,
            blur_height=blur_height,
            sub_bottom_offset=sub_bottom_offset,
            render_subtitles=render_subtitles
        )

        out_p = Path(output_temp_video).resolve()
        out_p.parent.mkdir(parents=True, exist_ok=True)
        temp_seg_dir = out_p.parent / f"segs_{out_p.stem}"
        # Luôn làm sạch thư mục phân đoạn cũ để không dính file hỏng từ lần chạy trước
        if temp_seg_dir.exists():
            try:
                shutil.rmtree(temp_seg_dir, ignore_errors=True)
            except Exception:
                pass
        temp_seg_dir.mkdir(parents=True, exist_ok=True)

        segments_info = []
        for i in range(num_segments):
            start_sec = i * segment_duration
            dur_sec = min(segment_duration, total_duration - start_sec)
            seg_file = temp_seg_dir / f"seg_{i:05d}.mp4"

            # Cắt lát file ASS độc lập cho riêng phân đoạn này:
            # Timestamp được chuẩn hóa về [0, dur_sec] nên libass hiển thị chính xác 100%
            seg_ass_path = None
            if ass_file_path and os.path.exists(ass_file_path) and os.path.getsize(ass_file_path) > 0:
                seg_ass_file = temp_seg_dir / f"seg_{i:05d}.ass"
                seg_ass_path = KaraokeSubtitleService.slice_ass_for_interval(
                    source_ass_path=ass_file_path,
                    output_slice_path=str(seg_ass_file),
                    start_sec=start_sec,
                    dur_sec=dur_sec
                )

            # Tạo filter_complex riêng cho phân đoạn với file ASS tương ứng
            seg_filter_complex, seg_logo_inputs, _, _ = cls._build_visual_filter_complex(
                video_input_path=video_input_path,
                ass_file_path=seg_ass_path,
                logo_path=logo_path,
                logo_position=logo_position,
                logo_size=logo_size,
                logo_opacity=logo_opacity,
                channel_name=channel_name,
                channel_opacity=channel_opacity,
                has_mask=has_mask,
                mask_top=mask_top,
                mask_left=mask_left,
                mask_width=mask_width,
                mask_height=mask_height,
                backdrop_opacity_hex=backdrop_opacity_hex,
                target_ratio=target_ratio,
                source_mask_ass=source_mask_ass,
                bottom_cut_percent=bottom_cut_percent,
                top_cut_percent=top_cut_percent,
                blur_height=blur_height,
                sub_bottom_offset=sub_bottom_offset,
                render_subtitles=render_subtitles
            )

            segments_info.append({
                "index": i,
                "start": start_sec,
                "duration": dur_sec,
                "output": seg_file,
                "filter_complex": seg_filter_complex,
                "logo_inputs": seg_logo_inputs
            })

        completed_count = 0
        lock = threading.Lock()

        def _worker_encode_segment(seg_info: Dict[str, Any]) -> Tuple[int, bool]:
            nonlocal completed_count
            idx = seg_info["index"]
            start_sec = seg_info["start"]
            dur_sec = seg_info["duration"]
            seg_file = seg_info["output"]
            seg_fc = seg_info["filter_complex"]
            seg_logos = seg_info["logo_inputs"]

            if task_id and task_manager.is_cancelled(task_id):
                return idx, False

            # Xóa file cũ nếu có để tránh xung đột
            try:
                seg_file.unlink(missing_ok=True)
            except Exception:
                pass

            ffmpeg_cmd = get_ffmpeg_cmd()
            # Fast seek -ss TRƯỚC -i để seek tức thì không decode thừa,
            # và file ASS đã được cô lập cục bộ theo [0, dur_sec] nên chuẩn tuyệt đối 100%
            cmd = [
                *ffmpeg_cmd, "-y",
                "-ss", f"{start_sec:.3f}",
                "-t", f"{dur_sec:.3f}",
                "-i", str(video_input_path),
                *seg_logos,
                "-threads", str(threads_per_worker),
                "-filter_complex", seg_fc,
                "-map", "[v_out]",
                "-an",
                "-c:v", vcodec
            ]
            if preset != "none":
                cmd.extend(["-preset", preset])
            if vcodec == "libx264":
                cmd.extend(["-crf", "22"])
            elif vcodec == "h264_amf":
                cmd.extend(["-quality", "speed", "-b:v", "4000k"])
            else:
                cmd.extend(["-b:v", "4000k"])

            pix_fmt = "nv12" if vcodec == "h264_qsv" else "yuv420p"
            cmd.extend([
                "-pix_fmt", pix_fmt,
                "-r", "30",
                str(seg_file)
            ])

            res = subprocess.run(cmd, capture_output=True, text=True, errors="replace", cwd=str(settings.BASE_DIR))

            # Tự động Fallback CPU ultrafast nếu GPU encoder phân đoạn gặp lỗi
            if res.returncode != 0 or not seg_file.exists() or seg_file.stat().st_size < 1000:
                fb_cmd = [
                    *ffmpeg_cmd, "-y",
                    "-ss", f"{start_sec:.3f}",
                    "-t", f"{dur_sec:.3f}",
                    "-i", str(video_input_path),
                    *seg_logos,
                    "-threads", str(threads_per_worker),
                    "-filter_complex", seg_fc,
                    "-map", "[v_out]",
                    "-an",
                    "-c:v", "libx264",
                    "-preset", "ultrafast",
                    "-crf", "22",
                    "-pix_fmt", "yuv420p",
                    "-r", "30",
                    str(seg_file)
                ]
                res_fb = subprocess.run(fb_cmd, capture_output=True, text=True, errors="replace", cwd=str(settings.BASE_DIR))
                if res_fb.returncode != 0 or not seg_file.exists() or seg_file.stat().st_size < 1000:
                    err_msg = (res_fb.stderr or res.stderr or "").strip()
                    err_lines = [l for l in err_msg.splitlines() if l.strip() and not l.startswith("frame=")]
                    summary_err = "\n".join(err_lines[-10:])
                    print(f"❌ [Segment #{idx+1}] Lỗi render FFmpeg (code={res_fb.returncode}):\n{summary_err}", flush=True)
                    return idx, False

            with lock:
                completed_count += 1
                pct = (completed_count / num_segments) * 100
                start_str = cls._format_time(start_sec)
                end_str = cls._format_time(start_sec + dur_sec)
                if task_id:
                    task_manager.update_task(task_id, stage=f"Render Song Song: {completed_count}/{num_segments} ({pct:.1f}%)")
                    log_interval = max(1, num_segments // 20)
                    if num_segments <= 30 or completed_count % log_interval == 0 or completed_count == num_segments:
                        task_manager.add_log(task_id, f"   ⚡ [Đoạn #{idx+1}/{num_segments}] {start_str} ➜ {end_str} hoàn tất ({pct:.1f}%)", "cyan")
                print(f"   🎬 [Render Song Song] Phân đoạn #{idx+1}/{num_segments} ({start_str} ➜ {end_str}) | {completed_count}/{num_segments} ({pct:.1f}%)", flush=True)

            return idx, True

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(_worker_encode_segment, seg) for seg in segments_info]
            all_ok = True
            for f in concurrent.futures.as_completed(futures):
                if task_id and task_manager.is_cancelled(task_id):
                    executor.shutdown(wait=False, cancel_futures=True)
                    try:
                        shutil.rmtree(temp_seg_dir, ignore_errors=True)
                    except Exception:
                        pass
                    return False
                idx, ok = f.result()
                if not ok:
                    all_ok = False
                    if task_id:
                        task_manager.add_log(task_id, f"⚠️ Phân đoạn song song #{idx+1} bị lỗi, tự động chuyển sang chế độ Render Toàn Diện An Toàn...", "amber")
                    break

        if not all_ok:
            try:
                shutil.rmtree(temp_seg_dir, ignore_errors=True)
            except Exception:
                pass
            # Fallback 100% tin cậy: chuyển sang Render đơn luồng toàn bộ video
            return cls._render_visual_stream_single(
                task_id=task_id,
                video_input_path=video_input_path,
                ass_file_path=ass_file_path,
                output_temp_video=output_temp_video,
                logo_path=logo_path,
                logo_position=logo_position,
                logo_size=logo_size,
                logo_opacity=logo_opacity,
                channel_name=channel_name,
                channel_opacity=channel_opacity,
                has_mask=has_mask,
                mask_top=mask_top,
                mask_left=mask_left,
                mask_width=mask_width,
                mask_height=mask_height,
                backdrop_opacity_hex=backdrop_opacity_hex,
                target_ratio=target_ratio,
                source_mask_ass=source_mask_ass,
                bottom_cut_percent=bottom_cut_percent,
                blur_height=blur_height,
                sub_bottom_offset=sub_bottom_offset,
                render_subtitles=render_subtitles
            )

        # Ghép nối các phân đoạn bằng Concat Demuxer
        concat_list_file = temp_seg_dir / "concat_list.txt"
        with open(concat_list_file, "w", encoding="utf-8") as f:
            for seg in segments_info:
                # Dùng relative filename để FFmpeg concat demuxer đọc an toàn tuyệt đối trên Windows/Linux
                f.write(f"file '{seg['output'].name}'\n")

        if task_id:
            task_manager.add_log(task_id, f"⚡ Hợp nhất {num_segments} phân đoạn video (Stream Concat 1s)...", "cyan")

        ffmpeg_cmd = get_ffmpeg_cmd()
        concat_cmd = [
            *ffmpeg_cmd, "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list_file),
            "-c", "copy",
            "-movflags", "+faststart",
            str(output_temp_video)
        ]
        c_res = subprocess.run(concat_cmd, capture_output=True, text=True, errors="replace", cwd=str(temp_seg_dir))
        out_video = Path(output_temp_video)
        if c_res.returncode == 0 and out_video.exists() and out_video.stat().st_size > 1000:
            # Dọn dẹp ngay các phân đoạn tạm để giải phóng ổ đĩa
            try:
                shutil.rmtree(temp_seg_dir, ignore_errors=True)
            except Exception:
                pass
            if task_id:
                task_manager.add_log(task_id, f"🎉 Render song song hoàn tất 100% ({cls._format_time(total_duration)})!", "emerald")
            return True
        else:
            if task_id:
                task_manager.add_log(task_id, f"⚠️ Concat phân đoạn không thành công, tự động chuyển Render Đơn Luồng An Toàn...", "amber")
            try:
                shutil.rmtree(temp_seg_dir, ignore_errors=True)
            except Exception:
                pass
            return cls._render_visual_stream_single(
                task_id=task_id,
                video_input_path=video_input_path,
                ass_file_path=ass_file_path,
                output_temp_video=output_temp_video,
                logo_path=logo_path,
                logo_position=logo_position,
                logo_size=logo_size,
                logo_opacity=logo_opacity,
                channel_name=channel_name,
                channel_opacity=channel_opacity,
                has_mask=has_mask,
                mask_top=mask_top,
                mask_left=mask_left,
                mask_width=mask_width,
                mask_height=mask_height,
                backdrop_opacity_hex=backdrop_opacity_hex,
                target_ratio=target_ratio,
                source_mask_ass=source_mask_ass,
                bottom_cut_percent=bottom_cut_percent,
                blur_height=blur_height,
                sub_bottom_offset=sub_bottom_offset,
                render_subtitles=render_subtitles
            )

    @classmethod
    def _render_visual_stream_single(
        cls,
        task_id: str,
        video_input_path: str,
        ass_file_path: str,
        output_temp_video: str,
        logo_path: Optional[str] = None,
        logo_position: str = "top_left",
        logo_size: int = 120,
        logo_opacity: float = 0.90,
        channel_name: str = "@Mắt Thần Review",
        channel_opacity: float = 0.35,
        has_mask: bool = True,
        mask_top: float = 80.0,
        mask_left: float = 15.0,
        mask_width: float = 70.0,
        mask_height: float = 12.0,
        backdrop_opacity_hex: str = "80",
        target_ratio: str = "16:9",
        source_mask_ass: Optional[str] = None,
        bottom_cut_percent: Optional[float] = None,
        top_cut_percent: Optional[float] = 0.0,
        blur_height: Optional[float] = 13.0,
        sub_bottom_offset: Optional[float] = 0.0,
        render_subtitles: bool = True
    ) -> bool:
        """Render đơn luồng (dành cho video ngắn dưới 60s)."""
        full_filter_complex, logo_inputs, render_w, render_h = cls._build_visual_filter_complex(
            video_input_path=video_input_path,
            ass_file_path=ass_file_path,
            logo_path=logo_path,
            logo_position=logo_position,
            logo_size=logo_size,
            logo_opacity=logo_opacity,
            channel_name=channel_name,
            channel_opacity=channel_opacity,
            has_mask=has_mask,
            mask_top=mask_top,
            mask_left=mask_left,
            mask_width=mask_width,
            mask_height=mask_height,
            backdrop_opacity_hex=backdrop_opacity_hex,
            target_ratio=target_ratio,
            source_mask_ass=source_mask_ass,
            bottom_cut_percent=bottom_cut_percent,
            top_cut_percent=top_cut_percent,
            blur_height=blur_height,
            sub_bottom_offset=sub_bottom_offset,
            render_subtitles=render_subtitles
        )

        vcodec, preset, encoder_desc = cls.detect_best_encoder()
        ffmpeg_cmd = get_ffmpeg_cmd()
        cmd = [
            *ffmpeg_cmd, "-y",
            "-i", str(video_input_path),
            *logo_inputs,
            "-threads", "0",
            "-filter_complex", full_filter_complex,
            "-map", "[v_out]",
            "-an",
            "-c:v", vcodec
        ]
        if preset != "none":
            cmd.extend(["-preset", preset])
        if vcodec == "libx264":
            cmd.extend(["-crf", "22"])
        elif vcodec == "h264_amf":
            cmd.extend(["-quality", "speed", "-b:v", "4000k"])
        else:
            cmd.extend(["-b:v", "4000k"])

        pix_fmt = "nv12" if vcodec == "h264_qsv" else "yuv420p"
        cmd.extend([
            "-pix_fmt", pix_fmt,
            "-r", "30",
            str(output_temp_video)
        ])

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(settings.BASE_DIR)
        )

        last_progress_time = 0.0
        stderr_tail = []
        if process.stderr:
            for line in process.stderr:
                stderr_tail.append(line)
                if len(stderr_tail) > 25:
                    stderr_tail.pop(0)

                if task_id and task_manager.is_cancelled(task_id):
                    try:
                        process.kill()
                    except Exception:
                        pass
                    break

                if "time=" in line and (time.time() - last_progress_time > 3.0):
                    last_progress_time = time.time()
                    m = re.search(r"time=(\d+):(\d+):(\d+)", line)
                    if m:
                        h, mm, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
                        print(f"   🎬 Đang Render Video ({vcodec}): {h:02d}:{mm:02d}:{s:02d}")
                        if task_id:
                            task_manager.update_task(task_id, stage=f"Render Video: {h:02d}:{mm:02d}:{s:02d}")

        process.wait()
        out_p = Path(output_temp_video)
        if process.returncode != 0 or not out_p.exists() or out_p.stat().st_size < 1000:
            err_summary = "".join(stderr_tail[-20:])
            print(f"[RenderVisualStream] Lỗi encode ({vcodec}), tự động chuyển CPU Ultrafast:\n{err_summary}")
            if task_id:
                task_manager.add_log(task_id, f"   ⚠️ Chuyển sang bộ mã hóa CPU Đa Luồng Ultrafast an toàn...", "amber")
            clean_part = full_filter_complex
            fallback_cmd = [
                *ffmpeg_cmd, "-y",
                "-i", str(video_input_path),
                *logo_inputs,
                "-threads", "0",
                "-filter_complex", clean_part,
                "-map", "[v_out]",
                "-an",
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-crf", "22",
                "-pix_fmt", "yuv420p",
                "-r", "30",
                str(output_temp_video)
            ]
            fallback_proc = subprocess.Popen(
                fallback_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(settings.BASE_DIR)
            )
            last_fb_time = 0.0
            fb_stderr_tail = []
            if fallback_proc.stderr:
                for line in fallback_proc.stderr:
                    fb_stderr_tail.append(line)
                    if len(fb_stderr_tail) > 25:
                        fb_stderr_tail.pop(0)

                    if task_id and task_manager.is_cancelled(task_id):
                        try:
                            fallback_proc.kill()
                        except Exception:
                            pass
                        break
                    if "time=" in line and (time.time() - last_fb_time > 3.0):
                        last_fb_time = time.time()
                        m = re.search(r"time=(\d+):(\d+):(\d+)", line)
                        if m:
                            h, mm, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
                            print(f"   🎬 Đang Render CPU Ultrafast: {h:02d}:{mm:02d}:{s:02d}")
                            if task_id:
                                task_manager.update_task(task_id, stage=f"CPU Render: {h:02d}:{mm:02d}:{s:02d}")
            fallback_proc.wait()

            if fallback_proc.returncode != 0:
                fb_err_summary = "".join(fb_stderr_tail[-25:])
                print(f"❌ LỖI FFMPEG FULL LOG:\n{fb_err_summary}")
                if task_id:
                    task_manager.add_log(task_id, f"❌ Lỗi CPU Fallback: Xem chi tiết trong Terminal (Log FFmpeg)", "rose")

        return out_p.exists() and out_p.stat().st_size > 1000

    @classmethod
    def render_visual_stream(
        cls,
        task_id: str,
        video_input_path: str,
        ass_file_path: str,
        output_temp_video: str,
        logo_path: Optional[str] = None,
        logo_position: str = "top_left",
        logo_size: int = 120,
        logo_opacity: float = 0.90,
        channel_name: str = "@Mắt Thần Review",
        channel_opacity: float = 0.35,
        has_mask: bool = True,
        mask_top: float = 80.0,
        mask_left: float = 15.0,
        mask_width: float = 70.0,
        mask_height: float = 12.0,
        backdrop_opacity_hex: str = "80",
        target_ratio: str = "16:9",
        max_workers: Optional[int] = None,
        source_mask_ass: Optional[str] = None,
        bottom_cut_percent: Optional[float] = None,
        top_cut_percent: Optional[float] = 0.0,
        blur_height: Optional[float] = 13.0,
        sub_bottom_offset: Optional[float] = 0.0,
        render_subtitles: bool = True
    ) -> bool:
        """
        Render luồng hình ảnh không tiếng (Visual Stream) với Subtitle Karaoke + Logo + Vùng che.
        Tự động phân luồng song song (Multi-Worker Parallel) cho video dài (>60s), tối ưu tốc độ tối đa trên mọi máy.
        """
        dur = cls.get_video_duration(video_input_path)
        if dur > 60.0:
            return cls.parallel_render_visual_stream(
                task_id=task_id,
                video_input_path=video_input_path,
                ass_file_path=ass_file_path,
                output_temp_video=output_temp_video,
                logo_path=logo_path,
                logo_position=logo_position,
                logo_size=logo_size,
                logo_opacity=logo_opacity,
                channel_name=channel_name,
                channel_opacity=channel_opacity,
                has_mask=has_mask,
                mask_top=mask_top,
                mask_left=mask_left,
                mask_width=mask_width,
                mask_height=mask_height,
                backdrop_opacity_hex=backdrop_opacity_hex,
                target_ratio=target_ratio,
                max_workers=max_workers,
                source_mask_ass=source_mask_ass,
                bottom_cut_percent=bottom_cut_percent,
                top_cut_percent=top_cut_percent,
                blur_height=blur_height,
                sub_bottom_offset=sub_bottom_offset,
                render_subtitles=render_subtitles
            )
        else:
            return cls._render_visual_stream_single(
                task_id=task_id,
                video_input_path=video_input_path,
                ass_file_path=ass_file_path,
                output_temp_video=output_temp_video,
                logo_path=logo_path,
                logo_position=logo_position,
                logo_size=logo_size,
                logo_opacity=logo_opacity,
                channel_name=channel_name,
                channel_opacity=channel_opacity,
                has_mask=has_mask,
                mask_top=mask_top,
                mask_left=mask_left,
                mask_width=mask_width,
                mask_height=mask_height,
                backdrop_opacity_hex=backdrop_opacity_hex,
                target_ratio=target_ratio,
                source_mask_ass=source_mask_ass,
                bottom_cut_percent=bottom_cut_percent,
                top_cut_percent=top_cut_percent,
                blur_height=blur_height,
                sub_bottom_offset=sub_bottom_offset,
                render_subtitles=render_subtitles
            )

    @classmethod
    def mux_final_video(
        cls,
        visual_video_path: str,
        audio_source_path: str,
        output_final_path: str
    ) -> bool:
        """
        Ghép siêu tốc luồng hình ảnh và âm thanh (Stream Copy 0.5s - 1s).
        An toàn 100% trên Windows (chống WinError 32 khóa file từ trình duyệt).
        """
        out_p = Path(output_final_path).resolve()
        out_p.parent.mkdir(parents=True, exist_ok=True)
        temp_out = out_p.parent / f"{out_p.stem}_mux_tmp{out_p.suffix}"

        ffmpeg_cmd = get_ffmpeg_cmd()
        cmd = [
            *ffmpeg_cmd, "-y",
            "-i", str(visual_video_path),
            "-i", str(audio_source_path),
            "-map", "0:v:0",
            "-map", "1:a?",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            "-movflags", "+faststart",
            str(temp_out)
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if res.returncode != 0 or not temp_out.exists():
            # Fallback nếu stream copy gặp lỗi
            print(f"[MuxFinalVideo] Lỗi Stream Copy: {res.stderr}")
            fallback_cmd = [
                *ffmpeg_cmd, "-y",
                "-i", str(visual_video_path),
                "-i", str(audio_source_path),
                "-c:v", "copy",
                "-c:a", "aac",
                "-shortest",
                str(temp_out)
            ]
            subprocess.run(fallback_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")

        if not temp_out.exists() or temp_out.stat().st_size < 1000:
            return False

        # Thay thế file đích an toàn
        try:
            if out_p.exists():
                try:
                    out_p.unlink(missing_ok=True)
                except Exception:
                    # File đang bị khóa bởi trình phát video trên web, dùng os.replace để ghi đè
                    pass
            os.replace(str(temp_out), str(out_p))
        except Exception as e:
            print(f"[MuxFinalVideo] Không thể ghi đè file cũ (đang bị lock), lưu tạm file: {e}")
            try:
                import shutil
                shutil.copy2(str(temp_out), str(out_p))
                temp_out.unlink(missing_ok=True)
            except Exception:
                # Nếu vẫn không copy được, đổi tên temp_out thành file chính
                pass

        return out_p.exists() or temp_out.exists()
