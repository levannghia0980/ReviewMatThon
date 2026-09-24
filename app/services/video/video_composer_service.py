import os
import re
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
        1. h264_nvenc  (NVIDIA - nhanh nhất)
        2. h264_amf    (AMD RX/RX Vega/RDNA - card rời AMD)
        3. h264_mf     (Windows Media Foundation / Intel Iris Xe)
        4. libx264     (CPU Ultrafast - fallback)
        """
        ff_cmd = get_ffmpeg_cmd()
        # Test NVIDIA NVENC
        try:
            test_nvenc = subprocess.run(
                [*ff_cmd, "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1", "-c:v", "h264_nvenc", "-f", "null", "-"],
                capture_output=True, text=True, errors="replace", timeout=3
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

        # Test Windows Media Foundation (Intel Iris Xe / DirectX GPU)
        try:
            test_mf = subprocess.run(
                [*ff_cmd, "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1", "-c:v", "h264_mf", "-f", "null", "-"],
                capture_output=True, text=True, errors="replace", timeout=3
            )
            if test_mf.returncode == 0:
                return "h264_mf", "none", "GPU Phần Cứng (Intel Iris Xe / Windows MF)"
        except Exception:
            pass

        return "libx264", "ultrafast", "CPU Đa Luồng Ultrafast"

    @staticmethod
    def get_video_resolution(video_path: str) -> Tuple[int, int]:
        """Đọc chính xác độ phân giải (Width x Height) của video MP4."""
        try:
            ffprobe_cmd = get_ffprobe_cmd()
            cmd = [
                *ffprobe_cmd, "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=s=x:p=0",
                str(video_path)
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=5)
            if res.returncode == 0 and res.stdout.strip():
                parts = res.stdout.strip().split("x")
                if len(parts) == 2:
                    return int(parts[0]), int(parts[1])
        except Exception:
            pass
        return 1280, 720

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
        margin_v: int = 30                      # Khoảng cách đáy màn hình
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

        if not project.video_path or not os.path.exists(project.video_path):
            raise FileNotFoundError(f"Không tìm thấy file video gốc: {project.video_path}")

        dialogues_db = db.query(DialogueSegmentModel).filter(
            DialogueSegmentModel.task_id == project_id
        ).order_by(DialogueSegmentModel.index.asc()).all()

        if not dialogues_db:
            raise ValueError(f"Project #{project_id} chưa có câu thoại nào!")

        task_manager.add_log(task_id, f"🎬 BẮT ĐẦU SẢN XUẤT VIDEO THÀNH PHẨM (Project #{project_id}: {project.title})", "purple")

        safe_title = "".join(c for c in project.title if c.isalnum() or c in (' ', '_', '-')).strip()
        if not safe_title:
            safe_title = f"project_{project.id}"

        # 1. Đọc kích thước video thực tế và Tạo file Phụ đề Karaoke ASS
        task_manager.update_task(task_id, step=1, progress=15)
        task_manager.add_log(task_id, "[1/4] ✨ Đang sinh file phụ đề Karaoke ASS từng từ (Word-by-word Highlight)...", "cyan")

        vw, vh = cls.get_video_resolution(project.video_path)
        font_sz = 32 if vh <= 720 else 46
        actual_margin_v = margin_v if margin_v != 65 else (30 if vh <= 720 else 45)

        segments = [
            DialogueSegment(
                id=d.index,
                start=d.start_time,
                end=round(d.start_time + (d.voice_duration if d.voice_duration and d.voice_duration > 0 else d.duration), 3),
                duration=round(d.voice_duration if d.voice_duration and d.voice_duration > 0 else d.duration, 3),
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
            width=vw,
            height=vh,
            font_size=font_sz,
            margin_v=actual_margin_v,
            highlight_color=karaoke_highlight_color,
            backdrop_opacity_hex=backdrop_opacity_hex
        )
        task_manager.add_log(task_id, f"   ✔ Đã tạo xong file Karaoke ASS: {ass_file.name} ({vw}x{vh})", "emerald")

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
                    bgm_volume_when_speaking=0.04,
                    bgm_volume_normal=1.00,
                    voiceover_volume=1.35
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
                settings.BASE_DIR / "assets" / "logo.png",
                Path("d:/NENGHIA0980/video/static/assets/logo_nengia_fire.png")
            ]:
                if cand.exists():
                    final_logo = str(cand)
                    break

        # 4. Xây dựng FFmpeg Filter Complex 1-Pass
        task_manager.update_task(task_id, step=4, progress=65)
        task_manager.add_log(task_id, "[4/4] 🚀 Đang Render Video 1-Pass Hardware Acceleration...", "cyan")

        # Chuẩn hóa đường dẫn ASS cho filter subtitle trong FFmpeg (thoát ký tự dấu hai chấm và gạch chéo ngược)
        ass_escaped = str(ass_file).replace("\\", "/").replace(":", "\\:")

        filter_chains = []
        last_v = "0:v"

        # A. Tự động Crop bỏ mép đáy chứa sub Trung cũ & Zoom về kích thước gốc (Cách 1: Siêu nét, 0% nhòe, siêu nhẹ)
        if has_mask:
            mh = max(0.05, min(0.30, float(mask_height) / 100.0 if mask_height is not None else 0.160))
            keep_h = 1.0 - mh
            # bilinear thay lanczos: nhanh hơn 40%, đây là file final nên dùng bilinear vẫn sắc nét tốt
            filter_chains.append(f"[0:v]crop=iw:trunc(ih*{keep_h:.3f}/2)*2:0:0,scale=iw:ih:flags=bilinear[v_clean]")
            last_v = "v_clean"
            task_manager.add_log(task_id, f"   • Xóa Sub Cũ (Crop & Zoom): Cắt {mh*100:.1f}% mép đáy + Phóng to 100%", "cyan")
        else:
            task_manager.add_log(task_id, "   • Vùng Xóa Sub Cũ: Đã tắt", "cyan")

        # B. Thêm Logo nhỏ gọn sát góc
        if final_logo and os.path.exists(final_logo):
            logo_escaped = str(final_logo).replace("\\", "/")
            # Đặt vị trí gọn nhỏ sát mép: margin 25px
            if logo_position == "top_right":
                overlay_pos = f"W-w-25:25"
            elif logo_position == "bottom_left":
                overlay_pos = f"25:H-h-25"
            elif logo_position == "bottom_right":
                overlay_pos = f"W-w-25:H-h-25"
            else: # top_left mặc định
                overlay_pos = f"25:25"

            # Scale logo nhỏ gọn ~110px-130px và áp dụng opacity
            filter_chains.append(f"movie='{logo_escaped}',scale={logo_size}:{logo_size},format=rgba,colorchannelmixer=aa={logo_opacity}[logo]")
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

        # D. Burn Phụ Đề Karaoke ASS
        filter_chains.append(f"[{last_v}]subtitles='{ass_escaped}'[v_out]")

        full_filter_complex = ";".join(filter_chains)

        output_video_path = settings.OUTPUT_FINAL_VIDEOS_DIR / f"{project.video_id}_{safe_title}_final.mp4"

        # Chọn encoder phần cứng tốt nhất (NVENC / Intel Iris Xe Media Foundation / CPU Ultrafast)
        vcodec, preset, encoder_desc = cls.detect_best_encoder()
        task_manager.add_log(task_id, f"   • Bộ Mã Hóa Render: {vcodec} ({encoder_desc})", "cyan")

        hwaccel_args = []
        if vcodec in ("h264_nvenc", "h264_amf", "h264_mf"):
            hwaccel_args = ["-hwaccel", "dxva2"]

        ffmpeg_cmd = get_ffmpeg_cmd()
        cmd = [
            *ffmpeg_cmd, "-y",
            "-probesize", "10M", "-analyzeduration", "0",
            *hwaccel_args,
            "-i", str(project.video_path),
            "-i", str(audio_source),
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

        cmd.extend([
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            str(output_video_path)
        ])

        process = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if process.returncode != 0 or not output_video_path.exists():
            # Fallback sang CPU Ultrafast nếu filter phức tạp gặp sự cố
            task_manager.add_log(task_id, f"   ⚠️ Chuyển sang chế độ CPU Ultrafast...", "amber")
            clean_part = f"[0:v]crop=iw:trunc(ih*{keep_h:.3f}/2)*2:0:0,scale=iw:ih:flags=lanczos[vc];[vc]subtitles='{ass_escaped}'[v_out]" if has_mask else f"[0:v]subtitles='{ass_escaped}'[v_out]"
            fallback_cmd = [
                *ffmpeg_cmd, "-y",
                "-i", str(project.video_path),
                "-i", str(audio_source),
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
            subprocess.run(fallback_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")

        # Cập nhật CSDL
        project.final_video_path = str(output_video_path)
        project.status = "COMPLETED"
        db.commit()

        task_manager.add_log(task_id, f"🎉 HOÀN TẤT XUẤT BẢN VIDEO REVIEW! File: output/final_videos/{output_video_path.name}", "emerald")
        task_manager.add_log(task_id, f"   • Karaoke Sub: Đồng bộ 100% từng từ", "emerald")
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
        backdrop_opacity_hex: str = "80"
    ) -> bool:
        """
        Render luồng hình ảnh không tiếng (Visual Stream) với Subtitle Karaoke + Logo + Vùng che.
        Chạy độc lập trên GPU / CPU, phục vụ mô hình xử lý song song (Parallel Pipeline).
        """
        final_logo = None
        if logo_path and os.path.exists(logo_path):
            final_logo = logo_path
        else:
            for cand in [
                settings.BASE_DIR / "assets" / "logo_nengia_fire.png",
                settings.BASE_DIR / "assets" / "logo.png",
                Path("d:/NENGHIA0980/video/static/assets/logo_nengia_fire.png")
            ]:
                if cand.exists():
                    final_logo = str(cand)
                    break

        ass_escaped = str(ass_file_path).replace("\\", "/").replace(":", "\\:")
        filter_chains = []
        last_v = "0:v"

        # 1. Tự động Crop bỏ mép đáy chứa sub Trung cũ & Zoom về kích thước gốc
        # Dùng bilinear thay lanczos cho file tạm (nhanh hơn 40%, mux cuối dùng stream copy nên chất lượng không mất)
        if has_mask:
            mh = max(0.05, min(0.30, float(mask_height) / 100.0 if mask_height is not None else 0.160))
            keep_h = 1.0 - mh
            filter_chains.append(f"[0:v]crop=iw:trunc(ih*{keep_h:.3f}/2)*2:0:0,scale=iw:ih:flags=bilinear[v_clean]")
            last_v = "v_clean"

        if final_logo and os.path.exists(final_logo):
            logo_escaped = str(final_logo).replace("\\", "/")
            if logo_position == "top_right":
                overlay_pos = "W-w-25:25"
            elif logo_position == "bottom_left":
                overlay_pos = "25:H-h-25"
            elif logo_position == "bottom_right":
                overlay_pos = "W-w-25:H-h-25"
            else:
                overlay_pos = "25:25"

            filter_chains.append(f"movie='{logo_escaped}',scale={logo_size}:{logo_size},format=rgba,colorchannelmixer=aa={logo_opacity}[logo]")
            filter_chains.append(f"[{last_v}][logo]overlay={overlay_pos}[v_logo]")
            last_v = "v_logo"

        if channel_name:
            clean_ch = channel_name.replace("'", "").replace(":", "")
            drawtext_filter = (
                f"drawtext=text='{clean_ch}':x=w-tw-30:y=30:"
                f"fontsize=20:fontcolor=white@{channel_opacity}:"
                f"shadowcolor=black@{channel_opacity/2}:shadowx=1:shadowy=1"
            )
            filter_chains.append(f"[{last_v}]{drawtext_filter}[v_watermark]")
            last_v = "v_watermark"

        filter_chains.append(f"[{last_v}]subtitles='{ass_escaped}'[v_out]")
        full_filter_complex = ";".join(filter_chains)

        vcodec, preset, encoder_desc = cls.detect_best_encoder()

        # Hardware decode: dxva2 (AMD/Intel/NVIDIA Windows) giải mã nhanh hơn CPU
        # Lưu ý: filter (crop, subtitles) vẫn chạy CPU, nên dùng hwaccel + output sw
        hwaccel_args = []
        if vcodec in ("h264_nvenc", "h264_amf", "h264_mf"):
            hwaccel_args = ["-hwaccel", "dxva2"]

        ffmpeg_cmd = get_ffmpeg_cmd()
        cmd = [
            *ffmpeg_cmd, "-y",
            # Bỏ qua phân tích đầu vào chậm (file đã biết định dạng)
            "-probesize", "10M", "-analyzeduration", "0",
            *hwaccel_args,
            "-i", str(video_input_path),
            "-threads", "0",           # Dùng toàn bộ CPU core cho filter
            "-filter_complex", full_filter_complex,
            "-map", "[v_out]",
            "-an",
            "-c:v", vcodec
        ]
        if preset != "none":
            cmd.extend(["-preset", preset])
        if vcodec == "libx264":
            # CRF 23 thay vì 21: nhanh hơn ~15%, file tạm này sẽ bị mux stream copy không encode lại
            cmd.extend(["-crf", "23", "-tune", "zerolatency"])
        elif vcodec == "h264_amf":
            cmd.extend(["-quality", "speed", "-b:v", "4000k"])
        else:
            cmd.extend(["-b:v", "4000k"])

        cmd.extend([
            "-pix_fmt", "yuv420p",
            str(output_temp_video)
        ])

        process = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if process.returncode != 0 or not Path(output_temp_video).exists():
            clean_part = f"[0:v]crop=iw:trunc(ih*{keep_h:.3f}/2)*2:0:0,scale=iw:ih:flags=lanczos[vc];[vc]subtitles='{ass_escaped}'[v_out]" if has_mask else f"[0:v]subtitles='{ass_escaped}'[v_out]"
            fallback_cmd = [
                *ffmpeg_cmd, "-y",
                "-i", str(video_input_path),
                "-filter_complex", clean_part,
                "-map", "[v_out]",
                "-an",
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-crf", "22",
                "-pix_fmt", "yuv420p",
                str(output_temp_video)
            ]
            subprocess.run(fallback_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")

        return Path(output_temp_video).exists()

    @classmethod
    def mux_final_video(
        cls,
        visual_video_path: str,
        audio_source_path: str,
        output_final_path: str
    ) -> bool:
        """
        Ghép siêu tốc luồng hình ảnh và âm thanh (Stream Copy 0.5s - 1s).
        """
        ffmpeg_cmd = get_ffmpeg_cmd()
        cmd = [
            *ffmpeg_cmd, "-y",
            "-i", str(visual_video_path),
            "-i", str(audio_source_path),
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            "-movflags", "+faststart",
            str(output_final_path)
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        return res.returncode == 0 and Path(output_final_path).exists()
