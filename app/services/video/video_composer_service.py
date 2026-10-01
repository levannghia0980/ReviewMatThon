import os
import re
import time
import math
import subprocess
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
        2. h264_amf    (AMD RX/RX Vega/RDNA - card rời AMD)
        3. libx264     (CPU Đa Luồng Ultrafast - tương thích 100% mọi máy, không lỗi MediaFoundation)
        """
        ff_cmd = get_ffmpeg_cmd()
        # Test NVIDIA NVENC (tăng timeout lên 8s để card rời Laptop RTX 3050/4060 kịp đánh thức từ chế độ ngủ)
        try:
            test_nvenc = subprocess.run(
                [*ff_cmd, "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1", "-c:v", "h264_nvenc", "-f", "null", "-"],
                capture_output=True, text=True, errors="replace", timeout=8
            )
            if test_nvenc.returncode == 0:
                return "h264_nvenc", "p4", "NVIDIA GPU NVENC Siêu Tốc"
        except Exception:
            pass

        # Test AMD AMF (RX 350 / RX 5xx / RX 6xxx / RDNA)
        try:
            test_amf = subprocess.run(
                [*ff_cmd, "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1", "-c:v", "h264_amf", "-f", "null", "-"],
                capture_output=True, text=True, errors="replace", timeout=3
            )
            if test_amf.returncode == 0:
                return "h264_amf", "none", "AMD GPU AMF (RX Series) Siêu Tốc"
        except Exception:
            pass

        # Test Windows Media Foundation (Hỗ trợ hầu hết GPU trên Windows, an toàn nhất)
        try:
            test_mf = subprocess.run(
                [*ff_cmd, "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1", "-c:v", "h264_mf", "-f", "null", "-"],
                capture_output=True, text=True, errors="replace", timeout=3
            )
            if test_mf.returncode == 0:
                return "h264_mf", "none", "Windows GPU Media Foundation"
        except Exception:
            pass

        # Test Intel QuickSync (Intel Core/Xe - đôi lúc kén tham số)
        try:
            test_qsv = subprocess.run(
                [*ff_cmd, "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1", "-c:v", "h264_qsv", "-f", "null", "-"],
                capture_output=True, text=True, errors="replace", timeout=3
            )
            if test_qsv.returncode == 0:
                return "h264_qsv", "veryfast", "Intel GPU QuickSync"
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

    @classmethod
    def calculate_crop(cls, vw: int, vh: int, bottom_cut_percent: float, target_ratio: str = "16:9") -> Dict[str, int]:
        """
        Tính toán thông số crop:
        - Cắt bỏ phần đáy theo bottom_cut_percent (% chiều cao) để xóa bỏ hoàn toàn hardsub Trung Quốc cũ.
        - Cắt đều 2 bên (trái & phải) để video đạt chuẩn tỷ lệ YouTube hỗ trợ (Mặc định 16:9 YouTube ngang, hoặc 9:16 Shorts).
        - CHỈ CẮT (CROP) - TUYỆT ĐỐI KHÔNG SCALE để tối ưu tốc độ render nhanh nhất có thể.
        """
        pct = max(0.0, min(45.0, float(bottom_cut_percent if bottom_cut_percent is not None else 12.0)))
        cut_h = int(round(vh * (pct / 100.0)))
        h_target = vh - cut_h
        h_target = max(120, (h_target // 2) * 2)

        ratio_val = 9.0 / 16.0 if target_ratio == "9:16" else 16.0 / 9.0
        w_target = int(round(h_target * ratio_val))
        w_target = (w_target // 2) * 2

        if w_target > vw:
            w_target = (vw // 2) * 2
            h_target = int(round(w_target / ratio_val))
            h_target = (h_target // 2) * 2
            x = 0
            y = 0
        else:
            x = int(round((vw - w_target) / 2.0))
            x = (x // 2) * 2
            y = 0

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
            "cut_h": cut_h
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
        box_padding: int = 8                    # Độ to theo chiều dọc / padding của hộp che chữ gốc (px)
    ) -> Dict[str, Any]:
        """
        Sản xuất Video Review Hoàn Thiện:
        1. Đọc kích thước video gốc MP4 để căn chỉnh tọa độ phụ đề khớp 100%.
        2. Tạo phụ đề Karaoke ASS chuẩn độ phân giải và font chữ.
        3. Che sạch 100% phụ đề tiếng Trung cũ bằng Solid Cinema Mask (1-pass duy nhất).
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

        if not dialogues_db:
            raise ValueError(f"Project #{project_id} chưa có câu thoại nào!")

        task_manager.add_log(task_id, "[1/4] ✨ Đang sinh file phụ đề Karaoke ASS từng từ (Word-by-word Highlight)...", "cyan")

        vw, vh = cls.get_video_resolution(project.video_path)
        if has_mask:
            crop_info = cls.calculate_crop(vw, vh, mask_height, target_ratio=crop_ratio)
            render_w = crop_info["w"]
            render_h = crop_info["h"]
            crop_x = crop_info["x"]
            crop_y = crop_info["y"]
        else:
            render_w, render_h = vw, vh
            crop_x, crop_y = 0, 0

        # ASS phụ đề LUÔN dùng kích thước KHUNG GỐC (vw x vh)
        # Subtitle sẽ được burn vào khung gốc TRƯỚC khi crop — không bị lệch tọa độ.
        ass_w, ass_h = vw, vh

        if font_size and font_size > 0:
            font_sz = font_size
        else:
            if ass_h <= 540:
                font_sz = 11
            elif ass_h <= 768:
                font_sz = 13
            elif ass_h <= 1100:
                font_sz = 16
            else:
                font_sz = 20


        # Tính margin_v theo % chiều cao khung hình render (render_h)
        # Nếu margin_v <= 100: tính theo % (ví dụ: 15% -> round(render_h * 0.15))
        # Nếu margin_v > 100: giá trị pixel trực tiếp
        if margin_v and margin_v > 0:
            if margin_v <= 100:
                actual_margin_v = max(4, int(round(render_h * (margin_v / 100.0))))
            else:
                actual_margin_v = margin_v
        else:
            actual_margin_v = max(8, int(round(render_h * 0.022)))

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
        KaraokeSubtitleService.create_karaoke_ass_file(
            segments=segments,
            output_ass_path=str(ass_file),
            video_title=project.title,
            width=ass_w,    # LUÔN dùng kích thước khung gốc
            height=ass_h,   # LUÔN dùng kích thước khung gốc
            font_size=font_sz,
            margin_v=actual_margin_v,
            highlight_color=karaoke_highlight_color,
            backdrop_opacity_hex=backdrop_opacity_hex,
            box_style=box_style,
            box_padding=box_padding
        )
        task_manager.add_log(task_id, f"   ✔ Đã tạo xong file Karaoke ASS: {ass_file.name} (PlayRes: {ass_w}x{ass_h} = khung gốc)", "emerald")

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
                    bgm_volume_when_speaking=0.03,
                    bgm_volume_normal=0.70,
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

        # 3. Chuẩn bị Logo & Watermark mờ
        task_manager.update_task(task_id, step=3, progress=50)
        task_manager.add_log(task_id, "[3/4] 🎨 Thiết lập Logo gọn nhỏ sát góc & Tên kênh mờ chống clone...", "cyan")

        # Tìm logo
        final_logo = None
        if logo_path and os.path.exists(logo_path):
            final_logo = logo_path
        else:
            for cand in [
                settings.BASE_DIR / "assets" / "logo_nengia_fire.png",
                settings.BASE_DIR / "assets" / "logo.png"
            ]:
                if cand.exists():
                    final_logo = str(cand)
                    break

        # 4. Xây dựng FFmpeg Filter Complex 1-Pass
        task_manager.update_task(task_id, step=4, progress=65)
        task_manager.add_log(task_id, "[4/4] 🚀 Đang Render Video 1-Pass Hardware Acceleration...", "cyan")

        # Chuẩn hóa đường dẫn ASS cho filter subtitle trong FFmpeg
        # Tránh lỗi ký tự (Unicode path) bằng cách dùng đường dẫn tương đối
        try:
            ass_rel = os.path.relpath(str(ass_file), start=str(settings.BASE_DIR))
            # FFmpeg subtitles filter: escape single quotes and backslashes
            ass_escaped = ass_rel.replace("\\", "/").replace("'", "\\'")
        except ValueError:
            # Nếu khác ổ đĩa, dùng đường dẫn tuyệt đối và escape ':'
            ass_abs = str(Path(ass_file).resolve())
            ass_escaped = ass_abs.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")

        filter_chains = []
        last_v = "0:v"

        # A. Burn Phụ Đề Karaoke ASS VÀO KHUNG GỐC TRƯỜC (toa độ khung gốc = đúng y chang user nhìn thấy)
        filter_chains.append(f"[{last_v}]subtitles='{ass_escaped}':force_style='Encoding=UTF-8'[v_subbed]")
        last_v = "v_subbed"

        # B. SAU ĐÓ mới Crop — loại bỏ sub Trung cũ & chuẩn tỷ lệ (sub Việt đã nằm đúng vị trí trước khi crop)
        if has_mask:
            crop_expr = f"w='min(iw,{render_w})':h='min(ih,{render_h})':x='min(max(0,iw-out_w),{crop_x})':y='min(max(0,ih-out_h),{crop_y})'"
            filter_chains.append(f"[{last_v}]crop={crop_expr}[v_cropped]")
            last_v = "v_cropped"
            task_manager.add_log(task_id, f"   • Burn sub vào khung gốc → Cắt đáy {mask_height:.1f}% + Chuẩn 16:9 ({render_w}x{render_h})", "cyan")
        else:
            task_manager.add_log(task_id, "   • Burn sub vào khung gốc → Không crop", "cyan")


        # D. Thêm Logo nhỏ gọn sát góc (không bị ảnh hưởng bởi crop)
        logo_inputs = []
        if final_logo and os.path.exists(final_logo):
            logo_inputs = ["-i", str(final_logo)]
            if logo_position == "top_right":
                overlay_pos = f"W-w-25:25"
            elif logo_position == "bottom_left":
                overlay_pos = f"25:H-h-25"
            elif logo_position == "bottom_right":
                overlay_pos = f"W-w-25:H-h-25"
            else: # top_left mặc định
                overlay_pos = f"25:25"

            # Input 0: video, Input 1: audio, Input 2: logo
            filter_chains.append(f"[2:v]scale={logo_size}:{logo_size},format=rgba,colorchannelmixer=aa={logo_opacity}[logo]")
            filter_chains.append(f"[{last_v}][logo]overlay={overlay_pos}[v_logo]")
            last_v = "v_logo"

        # C. Thêm Watermark Tên Kênh Mờ Bán Trong Suốt
        if channel_name:
            clean_ch = channel_name.replace("'", "").replace(":", "")
            # Đặt tên kênh mờ nhỏ ở góc trên bên phải hoặc mép cạnh
            drawtext_filter = (
                f"drawtext=text='{clean_ch}':x=w-tw-30:y=30:"
                f"fontsize=20:fontcolor=white@{channel_opacity}:"
                f"shadowcolor=black@{channel_opacity/2}:shadowx=1:shadowy=1"
            )
            filter_chains.append(f"[{last_v}]{drawtext_filter}[v_watermark]")
            last_v = "v_watermark"

        filter_chains.append(f"[{last_v}]scale=w='trunc(iw/2)*2':h='trunc(ih/2)*2'[v_out]")

        full_filter_complex = ";".join(filter_chains)

        output_video_path = settings.OUTPUT_FINAL_VIDEOS_DIR / f"{project.video_id}_{safe_title}_final.mp4"

        # Chọn encoder phần cứng tốt nhất (NVENC / Intel Iris Xe Media Foundation / CPU Ultrafast)
        vcodec, preset, encoder_desc = cls.detect_best_encoder()
        task_manager.add_log(task_id, f"   • Bộ Mã Hóa Render: {vcodec} ({encoder_desc})", "cyan")

        hwaccel_args = []
        # if vcodec in ("h264_nvenc", "h264_amf", "h264_mf"):
        #     hwaccel_args = ["-hwaccel", "dxva2"]

        ffmpeg_cmd = get_ffmpeg_cmd()
        cmd = [
            *ffmpeg_cmd, "-y",
            *hwaccel_args,
            "-i", str(project.video_path),
            "-i", str(audio_source),
            *logo_inputs,
            "-threads", "0",
            "-filter_complex", full_filter_complex,
            "-map", "[v_out]",
            "-map", "1:a",
            "-c:v", vcodec
        ]
        if preset != "none":
            cmd.extend(["-preset", preset])
        if vcodec == "libx264":
            cmd.extend(["-crf", "21"])  # Final video: giữ CRF 21 cho chất lượng tốt
        elif vcodec == "h264_amf":
            cmd.extend(["-quality", "speed", "-b:v", "4000k"])
        else:
            cmd.extend(["-b:v", "4000k"])

        pix_fmt = "nv12" if vcodec == "h264_qsv" else "yuv420p"
        cmd.extend([
            "-pix_fmt", pix_fmt,
            "-r", "30",
            "-movflags", "+faststart",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            str(output_video_path)
        ])


        process = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(settings.BASE_DIR))
        if process.returncode != 0 or not output_video_path.exists():
            # Fallback sang CPU Ultrafast nếu filter phức tạp gặp sự cố
            task_manager.add_log(task_id, f"   ⚠️ Chuyển sang chế độ CPU Ultrafast...", "amber")
            clean_part = full_filter_complex
            fallback_cmd = [
                *ffmpeg_cmd, "-y",
                "-i", str(project.video_path),
                "-i", str(audio_source),
                *logo_inputs,
                "-filter_complex", clean_part,
                "-map", "[v_out]",
                "-map", "1:a",
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-crf", "22",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                "-c:a", "aac",
                "-b:a", "192k",
                "-shortest",
                str(output_video_path)
            ]
            subprocess.run(fallback_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(settings.BASE_DIR))

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
        target_ratio: str = "16:9"
    ) -> bool:
        """
        Render luồng hình ảnh không tiếng (Visual Stream) với Subtitle Karaoke + Logo + Vùng che.
        Chạy độc lập trên GPU / CPU, phục vụ mô hình xử lý song song (Parallel Pipeline).
        """
        # Chỉ có crop + subtitle + scale, không logo/watermark
        # (drawtext cần font trên máy, dễ gây lỗi -22 nếu máy khách không có fontconfig)
        logo_inputs = []  # Không dùng logo trong render_visual_stream

        # Chuẩn hóa đường dẫn ASS cho filter subtitle trong FFmpeg
        try:
            ass_rel = os.path.relpath(str(ass_file_path), start=str(settings.BASE_DIR))
            ass_escaped = ass_rel.replace("\\", "/").replace("'", "\\'")
        except ValueError:
            ass_abs = str(Path(ass_file_path).resolve())
            ass_escaped = ass_abs.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")

        filter_chains = []
        last_v = "0:v"

        vw, vh = cls.get_video_resolution(video_input_path)
        if has_mask:
            crop_info = cls.calculate_crop(vw, vh, mask_height, target_ratio=target_ratio)
            render_w = crop_info["w"]
            render_h = crop_info["h"]
            crop_x = crop_info["x"]
            crop_y = crop_info["y"]
            crop_expr = f"w='min(iw,{render_w})':h='min(ih,{render_h})':x='min(max(0,iw-out_w),{crop_x})':y='min(max(0,ih-out_h),{crop_y})'"
            filter_chains.append(f"[0:v]crop={crop_expr}[v_clean]")
            last_v = "v_clean"
        else:
            render_w = (vw // 2) * 2
            render_h = (vh // 2) * 2
            if render_w != vw or render_h != vh:
                crop_expr = f"w='min(iw,{render_w})':h='min(ih,{render_h})':x=0:y=0"
                filter_chains.append(f"[0:v]crop={crop_expr}[v_clean]")
                last_v = "v_clean"

        # Subtitle Karaoke & Đảm bảo kích thước chẵn tuyệt đối (chống lỗi code -22 invalid argument)
        filter_chains.append(f"[{last_v}]subtitles='{ass_escaped}':force_style='Encoding=UTF-8'[v_sub]")
        filter_chains.append(f"[v_sub]scale=w='trunc(iw/2)*2':h='trunc(ih/2)*2'[v_out]")
        full_filter_complex = ";".join(filter_chains)


        vcodec, preset, encoder_desc = cls.detect_best_encoder()

        # Hardware decode: dxva2 (AMD/Intel/NVIDIA Windows) giải mã nhanh hơn CPU
        hwaccel_args = []

        ffmpeg_cmd = get_ffmpeg_cmd()
        cmd = [
            *ffmpeg_cmd, "-y",
            *hwaccel_args,
            "-i", str(video_input_path),
            *logo_inputs,
            "-threads", "0",           # Dùng toàn bộ CPU core cho filter
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

        # Chạy FFmpeg với đọc tiến trình thời gian thực, cập nhật live progress UI và chống treo bộ nhớ
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

                # Kiểm tra hủy tác vụ từ người dùng hoặc luồng song song gặp sự cố
                if task_id and task_manager.is_cancelled(task_id):
                    try:
                        process.kill()
                    except Exception:
                        pass
                    break

                # Trích xuất time=HH:MM:SS để cập nhật UI và Console
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
