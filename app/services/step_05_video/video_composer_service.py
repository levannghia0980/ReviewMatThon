import os
import sys
import re
import json
import time
import math
import shutil
import threading
import subprocess
import concurrent.futures
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple, Union
from sqlalchemy.orm import Session

from app.config import settings
from app.models.project import ProjectTask
from app.models.dialogue import DialogueSegmentModel
from app.schemas.transcript import DialogueSegment
from app.services.step_05_video.karaoke_subtitle_service import KaraokeSubtitleService
from app.services.step_05_video.audio_mixer_service import AudioMixerService
from app.services.task_manager import task_manager
from app.utils.bin_helper import get_ffmpeg_cmd, get_ffprobe_cmd

class VideoComposerService:
    # Bộ nhớ đệm phần cứng GPU & Metadata giúp triệt tiêu thời gian chờ subprocess lặp lại
    _CACHED_ENCODER: Optional[Tuple[str, str, str]] = None
    _METADATA_CACHE: Dict[str, Tuple[float, int, int, float, str, float]] = {}

    @classmethod
    def detect_best_encoder(cls) -> Tuple[str, str, str]:
        """
        Tự động nhận diện Encoder phần cứng nhanh nhất trên máy (Chỉ dò 1 lần duy nhất và lưu Cache):
        1. h264_nvenc  (NVIDIA - preset p2 siêu tốc + spatial_aq giữ nét chữ)
        2. h264_qsv    (Intel GPU QuickSync - phần cứng ASIC siêu tốc)
        3. h264_amf    (AMD RX/RX Vega/RDNA - card rời AMD)
        4. h264_mf     (Windows GPU Media Foundation)
        5. libx264     (CPU Đa Luồng Ultrafast)
        """
        if cls._CACHED_ENCODER is not None:
            return cls._CACHED_ENCODER

        ff_cmd = get_ffmpeg_cmd()
        # 1. Test NVIDIA NVENC (preset p2 tăng 40% tốc độ so với p4 mà vẫn giữ nguyên độ nét hoạt hình)
        try:
            test_nvenc = subprocess.run(
                [*ff_cmd, "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1", "-pix_fmt", "yuv420p", "-c:v", "h264_nvenc", "-f", "null", "-"],
                capture_output=True, text=True, errors="replace", timeout=8
            )
            if test_nvenc.returncode == 0:
                cls._CACHED_ENCODER = ("h264_nvenc", "p2", "NVIDIA GPU NVENC Siêu Tốc (Turbo P2)")
                return cls._CACHED_ENCODER
        except Exception:
            pass

        # 2. Test Intel QuickSync
        try:
            test_qsv = subprocess.run(
                [*ff_cmd, "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1", "-c:v", "h264_qsv", "-f", "null", "-"],
                capture_output=True, text=True, errors="replace", timeout=3
            )
            if test_qsv.returncode == 0:
                cls._CACHED_ENCODER = ("h264_qsv", "veryfast", "Intel GPU QuickSync Siêu Tốc")
                return cls._CACHED_ENCODER
        except Exception:
            pass

        # 3. Test AMD AMF
        try:
            test_amf = subprocess.run(
                [*ff_cmd, "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1", "-c:v", "h264_amf", "-f", "null", "-"],
                capture_output=True, text=True, errors="replace", timeout=3
            )
            if test_amf.returncode == 0:
                cls._CACHED_ENCODER = ("h264_amf", "none", "AMD GPU AMF (RX Series) Siêu Tốc")
                return cls._CACHED_ENCODER
        except Exception:
            pass

        # 4. Test Windows Media Foundation
        try:
            test_mf = subprocess.run(
                [*ff_cmd, "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1", "-c:v", "h264_mf", "-f", "null", "-"],
                capture_output=True, text=True, errors="replace", timeout=3
            )
            if test_mf.returncode == 0:
                cls._CACHED_ENCODER = ("h264_mf", "none", "Windows GPU Media Foundation")
                return cls._CACHED_ENCODER
        except Exception:
            pass

        cls._CACHED_ENCODER = ("libx264", "ultrafast", "CPU Đa Luồng Ultrafast")
        return cls._CACHED_ENCODER

    @classmethod
    def get_video_metadata(cls, video_path: str) -> Tuple[int, int, float, str, float]:
        """
        Đọc gộp (width, height, fps_float, fps_str, duration) chỉ trong 1 lần gọi FFprobe duy nhất
        và lưu Cache theo mtime của file để tăng tốc khởi tạo tiến trình.
        """
        p_str = str(Path(video_path).resolve()) if video_path else ""
        mtime = 0.0
        try:
            if p_str and os.path.exists(p_str):
                mtime = os.path.getmtime(p_str)
                cached = cls._METADATA_CACHE.get(p_str)
                if cached and cached[0] == mtime:
                    return cached[1], cached[2], cached[3], cached[4], cached[5]
        except Exception:
            pass

        w, h, fps_val, fps_str, dur = 1280, 720, 30.0, "30", 0.0
        try:
            ffprobe_cmd = get_ffprobe_cmd()
            cmd = [
                *ffprobe_cmd, "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height,r_frame_rate,avg_frame_rate:format=duration",
                "-of", "json",
                str(video_path)
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=6)
            if res.returncode == 0 and res.stdout.strip():
                data = json.loads(res.stdout)
                streams = data.get("streams", [])
                if streams:
                    s0 = streams[0]
                    sw, sh = int(s0.get("width", 0) or 0), int(s0.get("height", 0) or 0)
                    if sw > 0 and sh > 0:
                        w, h = sw, sh
                    for rate_key in ("r_frame_rate", "avg_frame_rate"):
                        r_str = s0.get(rate_key, "")
                        if "/" in r_str:
                            num, den = r_str.split("/", 1)
                            n_v, d_v = float(num), float(den)
                            if d_v > 0 and 10.0 <= (n_v / d_v) <= 120.0:
                                fps_val, fps_str = round(n_v / d_v, 3), r_str
                                break
                fmt = data.get("format", {})
                if fmt.get("duration"):
                    d_val = float(fmt["duration"])
                    if d_val > 0:
                        dur = d_val
        except Exception:
            pass

        if dur <= 0.0 or w <= 0 or h <= 0:
            # Fallback về các hàm đọc lẻ nếu file đặc biệt
            try:
                import cv2
                cap = cv2.VideoCapture(str(video_path))
                if cap.isOpened():
                    cw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                    ch = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    cfps = float(cap.get(cv2.CAP_PROP_FPS))
                    cframes = float(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                    cap.release()
                    if cw > 0 and ch > 0:
                        w, h = cw, ch
                    if 10.0 <= cfps <= 120.0:
                        fps_val, fps_str = round(cfps, 3), str(round(cfps, 3))
                    if dur <= 0.0 and cfps > 0 and cframes > 0:
                        dur = cframes / cfps
            except Exception:
                pass

        if p_str and dur > 0:
            cls._METADATA_CACHE[p_str] = (mtime, w, h, fps_val, fps_str, dur)
        return w, h, fps_val, fps_str, dur

    @classmethod
    def get_video_resolution(cls, video_path: str) -> Tuple[int, int]:
        w, h, _, _, _ = cls.get_video_metadata(video_path)
        return w, h

    @classmethod
    def get_video_fps(cls, video_path: str) -> Tuple[float, str]:
        _, _, fps_val, fps_str, _ = cls.get_video_metadata(video_path)
        return fps_val, fps_str

    @classmethod
    def get_video_duration(cls, video_path: str) -> float:
        _, _, _, _, dur = cls.get_video_metadata(video_path)
        return dur

    @staticmethod
    def has_audio_stream(file_path: Union[str, Path]) -> bool:
        """Kiểm tra xem file video có chứa luồng âm thanh (audio stream) hợp lệ hay không."""
        p = Path(file_path)
        if not p.exists() or p.stat().st_size < 1000:
            return False
        # 1. Kiểm tra qua FFprobe
        try:
            ffprobe_cmd = get_ffprobe_cmd()
            cmd = [
                *ffprobe_cmd, "-v", "error",
                "-select_streams", "a:0",
                "-show_entries", "stream=codec_type",
                "-of", "csv=p=0",
                str(p)
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=6)
            if res.returncode == 0 and "audio" in res.stdout.lower():
                return True
        except Exception:
            pass

        # 2. Kiểm tra dự phòng qua FFmpeg -i
        try:
            ffmpeg_cmd = get_ffmpeg_cmd()
            p_res = subprocess.run([*ffmpeg_cmd, "-i", str(p)], capture_output=True, text=True, errors="replace", timeout=6)
            if "Audio:" in p_res.stderr:
                return True
        except Exception:
            pass

        return False

    @classmethod
    def _is_file_readable_audio(cls, file_path: Path) -> bool:
        """Kiểm tra file âm thanh có đọc được và không bị thiếu moov atom (đang ghi dở)."""
        if not file_path or not file_path.exists() or file_path.stat().st_size < 1000:
            return False
        try:
            ffprobe_bin = get_ffprobe_cmd()[0]
            cmd = [
                ffprobe_bin, "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(file_path.resolve())
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if res.returncode == 0 and res.stdout.strip():
                try:
                    dur = float(res.stdout.strip().split("\n")[0].strip())
                    return dur > 0.1
                except Exception:
                    return False
        except Exception:
            return False
        return False

    @classmethod
    def _find_valid_audio(cls, audio_source_path: Optional[Union[str, Path]]) -> Optional[Path]:
        """Tìm file âm thanh hợp lệ trên đĩa, tự động thích ứng các đuôi mở rộng (.m4a, .aac, .mp3, .wav) và kiểm tra tính toàn vẹn."""
        if not audio_source_path:
            return None
        p = Path(audio_source_path)
        candidates = [
            p,
            p.with_suffix(".m4a"),
            p.with_suffix(".aac"),
            p.with_suffix(".mp3"),
            p.with_suffix(".wav")
        ]
        for cand in candidates:
            if cls._is_file_readable_audio(cand):
                return cand
        try:
            if p.parent.exists():
                for f in p.parent.glob(f"{p.stem}.*"):
                    if f.suffix.lower() in (".m4a", ".aac", ".mp3", ".wav") and cls._is_file_readable_audio(f):
                        return f
        except Exception:
            pass
        return None

    @staticmethod
    def _format_time(seconds: float) -> str:
        """Định dạng số giây thành chuỗi trực quan HH:MM:SS."""
        sec = max(0.0, float(seconds))
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = int(sec % 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    @classmethod
    def calculate_crop(
        cls,
        vw: int,
        vh: int,
        bottom_cut_percent: float,
        top_cut_percent: float = 2.5,
        target_ratio: str = "16:9"
    ) -> Dict[str, int]:
        """
        Tính toán bố cục Khung Kính Điện Ảnh 88% Bất Đối Xứng (Asymmetric Cinema Glass PIP):
        - Giữ nguyên độ phân giải khung nền chuẩn (1280x720 / 1920x1080 cho 16:9 hoặc 9:16 cho Shorts).
        - Cắt sạch 100% hardsub tiếng Trung ở đáy (tối thiểu 9.0%) và mép logo ở đỉnh (2.5%).
        - Cắt lẹm bất đối xứng Trái (4.0%) / Phải (1.5%) để phá vỡ tâm quang học của khung hình gốc.
        - Thu phóng lớp phim chính về 88% và đặt LỆCH TÂM DỌC lên trên (cách đỉnh 3.2%, chừa đáy 8.8%)
          để phụ đề Karaoke tiếng Việt đè cắt ngang qua biên dưới (Semantic Occlusion + Phá biên hộp).
        - Mọi tọa độ đều khóa số chẵn (`// 2 * 2`) giúp không gian màu yuv420p sắc nét tuyệt đối, 0% sôi viền.
        """
        ratio_val = 9.0 / 16.0 if target_ratio == "9:16" else 16.0 / 9.0
        if target_ratio == "9:16":
            canvas_h = (vh // 2) * 2
            canvas_w = (int(round(canvas_h * ratio_val)) // 2) * 2
            if canvas_w > vw:
                canvas_w = (vw // 2) * 2
                canvas_h = (int(round(canvas_w / ratio_val)) // 2) * 2
        else:
            canvas_w = (vw // 2) * 2
            canvas_h = (int(round(canvas_w / ratio_val)) // 2) * 2
            if canvas_h > vh:
                canvas_h = (vh // 2) * 2
                canvas_w = (int(round(canvas_h * ratio_val)) // 2) * 2

        canvas_w = max(128, canvas_w)
        canvas_h = max(128, canvas_h)

        # Cắt sạch đáy (tối thiểu 9.0% để nuốt trọn hardsub Trung), cắt đỉnh 2.5%, cắt lẹm trái 4.0% & phải 1.5%
        bot_pct = max(9.0, min(40.0, float(bottom_cut_percent if bottom_cut_percent is not None else 9.5)))
        top_pct = max(2.2, min(25.0, float(top_cut_percent if top_cut_percent and top_cut_percent > 0 else 2.5)))
        left_pct = 4.0
        right_pct = 1.5

        cut_top = (int(round(vh * (top_pct / 100.0))) // 2) * 2
        cut_bot = (int(round(vh * (bot_pct / 100.0))) // 2) * 2
        cut_left = (int(round(vw * (left_pct / 100.0))) // 2) * 2
        cut_right = (int(round(vw * (right_pct / 100.0))) // 2) * 2

        fg_crop_w = max(64, ((vw - cut_left - cut_right) // 2) * 2)
        fg_crop_h = max(64, ((vh - cut_top - cut_bot) // 2) * 2)
        fg_crop_x = max(0, min(vw - fg_crop_w, cut_left))
        fg_crop_y = max(0, min(vh - fg_crop_h, cut_top))

        # Khung Kính 88% đặt lệch tâm dọc (cách mép trên 3.2%, chừa phần đáy 8.8% cho phụ đề đè phá biên)
        fg_scale = 0.88
        fg_w = max(64, (int(round(canvas_w * fg_scale)) // 2) * 2)
        fg_h = max(64, (int(round(canvas_h * fg_scale)) // 2) * 2)

        margin_x_center = (canvas_w - fg_w) // 2
        fg_x = max(0, min(canvas_w - fg_w, ((margin_x_center + 6) // 2) * 2))
        fg_y = max(0, min(canvas_h - fg_h, (int(round(canvas_h * 0.032)) // 2) * 2))

        return {
            "w": canvas_w,
            "h": canvas_h,
            "x": fg_crop_x,
            "y": fg_crop_y,
            "fg_crop_w": fg_crop_w,
            "fg_crop_h": fg_crop_h,
            "fg_crop_x": fg_crop_x,
            "fg_crop_y": fg_crop_y,
            "fg_w": fg_w,
            "fg_h": fg_h,
            "fg_x": fg_x,
            "fg_y": fg_y,
            "pan_y": 0,
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
        blur_height: Optional[float] = None,    # Chiều cao dải mờ (%)
        sub_bottom_offset: Optional[float] = None, # Khoảng cách dải mờ từ đáy (%)
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

        from app.models.dialogue import SubtitleSegmentModel
        dialogues_db = db.query(SubtitleSegmentModel).filter(
            SubtitleSegmentModel.task_id == project_id
        ).order_by(SubtitleSegmentModel.index.asc()).all()

        if not dialogues_db:
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

        # Phụ đề Video & Karaoke được nạp trực tiếp chuẩn 100% từ bảng dialogue_segments
        karaoke_render_segments = segments

        ass_file = settings.OUTPUT_TRANSCRIPTS_DIR / f"{project.video_id}_karaoke.ass"
        if render_subtitles:
            KaraokeSubtitleService.create_karaoke_ass_file(
                segments=karaoke_render_segments,
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
            try:
                import shutil
                from app.config import get_project_dirs
                p_dirs = get_project_dirs(project.id)
                shutil.copyfile(ass_file, p_dirs["root"] / f"{project.video_id}_karaoke.ass")
                shutil.copyfile(ass_file, p_dirs["video_subtitles"] / "subtitles_karaoke.ass")
            except Exception:
                pass
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
        from app.config import get_project_dirs
        p_dirs = get_project_dirs(project.id)
        mixed_audio_file = settings.OUTPUT_VOICEOVER_DIR / f"{project.video_id}_mixed_final.mp3"
        proj_root_vo = p_dirs["root"] / f"{project.video_id}_voiceover.mp3"
        voiceover_file = settings.OUTPUT_VOICEOVER_DIR / f"{project.video_id}_{safe_title}_voiceover.mp3"
        if proj_root_vo.exists():
            voiceover_file = proj_root_vo
        elif not voiceover_file.exists():
            candidates = [f for f in settings.OUTPUT_VOICEOVER_DIR.glob(f"{project.video_id}*.mp3") if "mixed" not in f.name and f.stat().st_size > 1000]
            if candidates:
                voiceover_file = candidates[0]
            else:
                voiceover_file = None

        if voiceover_file and Path(voiceover_file).exists():
            task_manager.add_log(task_id, "   🎵 Đang hòa trộn BGM gốc & lồng tiếng Việt (Vocal Ducking)...", "cyan")
            try:
                from app.services.step_05_video.audio_mixer_service import AudioMixerService
                mixed_path = AudioMixerService.mix_bgm_and_voiceover(
                    original_video_or_audio=project.video_path,
                    voiceover_mp3=str(voiceover_file),
                    output_mixed_audio=str(mixed_audio_file),
                    dialogue_segments=segments,
                    bgm_volume_when_speaking=0.23,  # 0.23 (23%) đồng đều xuyên suốt
                    bgm_volume_normal=0.23,  # 0.23 (23%) khi không nói để giữ rõ âm thanh môi trường và BGM sống động
                    voiceover_volume=1.0
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

        # 4. Render luồng hình ảnh & âm thanh thành phẩm (Single-Pass Siêu Tốc)
        task_manager.update_task(task_id, step=4, progress=65)
        task_manager.add_log(task_id, "[4/4] 🚀 Kích hoạt Render Video (Engine Single-Pass Siêu Tốc)...", "purple")

        output_video_path = settings.OUTPUT_FINAL_VIDEOS_DIR / f"{project.video_id}_{safe_title}_final.mp4"
        temp_visual_video = settings.TEMP_DIR / f"{project.video_id}_{safe_title}_visual_tmp.mp4"
        
        # Xóa triệt để file video thành phẩm cũ và file tạm hình ảnh cũ nếu có từ lần chạy trước
        if output_video_path.exists():
            try:
                output_video_path.unlink(missing_ok=True)
            except Exception:
                pass

        if temp_visual_video.exists():
            try:
                temp_visual_video.unlink(missing_ok=True)
            except Exception:
                pass

        # Single-Pass Render: Ghép video phân đoạn + Mux âm thanh trực tiếp vào output_video_path
        # Tiết kiệm hoàn toàn 1 lần ghi 20GB và 1 lần đọc 20GB file trung gian!
        v_ok = cls.render_visual_stream(
            task_id=task_id,
            video_input_path=project.video_path,
            ass_file_path=str(ass_file),
            output_temp_video=str(output_video_path),
            audio_source_path=str(audio_source),
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
            target_ratio=crop_ratio,
            source_mask_ass=source_mask_ass,
            bottom_cut_percent=mask_height,
            blur_height=blur_height,
            sub_bottom_offset=sub_bottom_offset,
            render_subtitles=render_subtitles
        )

        if not v_ok or not output_video_path.exists() or output_video_path.stat().st_size < 1000:
            # Fallback an toàn nếu cần hợp nhất lại qua file tạm
            if temp_visual_video.exists() and temp_visual_video.stat().st_size > 1000:
                task_manager.add_log(task_id, "   ⚡ [Stream Mux Fallback] Hợp nhất video và âm thanh thành phẩm...", "cyan")
                cls.mux_final_video(
                    visual_video_path=str(temp_visual_video),
                    audio_source_path=str(audio_source),
                    output_final_path=str(output_video_path)
                )
                try:
                    temp_visual_video.unlink(missing_ok=True)
                except Exception:
                    pass

        if not output_video_path.exists() or output_video_path.stat().st_size < 1000:
            raise RuntimeError("Render & hợp nhất video thành phẩm thất bại!")

        # Cập nhật CSDL
        project.final_video_path = str(output_video_path)
        project.status = "COMPLETED"
        db.commit()

        task_manager.add_log(task_id, f"🎉 HOÀN TẤT XUẤT BẢN VIDEO REVIEW! File: output/final_videos/{output_video_path.name}", "emerald")
        task_manager.add_log(task_id, f"   • Karaoke Sub: Đồng bộ 100% từng từ ({ass_w}x{ass_h})", "emerald")
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
        ass_file_path: Optional[str] = None,
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
        top_cut_percent: Optional[float] = 2.5,
        blur_height: Optional[float] = 13.0,
        sub_bottom_offset: Optional[float] = 0.0,
        render_subtitles: bool = True,
        start_sec: float = 0.0,
        cached_res: Optional[Tuple[int, int]] = None
    ) -> Tuple[str, List[str], int, int]:
        """
        Chuỗi Filter Khung Kính Điện Ảnh 88% Bất Đối Xứng (Turbo C-SIMD Pure YUV420P):
        1. Xóa vĩnh viễn noise, hue động và crop sin/cos exact=1 -> Hình ảnh trong vắt, 0% vỡ khối, 0% sôi viền.
        2. Nhánh nền [v_bg] thu nhỏ về 160x90 trước khi làm mờ avgblur và hạ sáng eq -> Giảm 98% tải CPU nhánh nền.
        3. Nhánh chính [v_fg] cắt bất đối xứng sạch hardsub Trung, lật gương hflip, chỉnh màu ấm tĩnh + nét viền unsharp,
           KHÔNG vẽ khung drawbox, đặt lệch tâm để phụ đề Karaoke ASS đè cắt ngang biên dưới.
        """
        ass_escaped = None
        if ass_file_path:
            ass_path_obj = Path(ass_file_path).resolve()
            ass_posix = str(ass_path_obj).replace("\\", "/")
            if sys.platform == "win32" and ":" in ass_posix:
                drive, rest = ass_posix.split(":", 1)
                ass_escaped = f"{drive}\\:{rest}".replace("'", "\\'")
            else:
                ass_escaped = ass_posix.replace("'", "\\'")

        vw, vh = cached_res if cached_res else cls.get_video_resolution(video_input_path)
        cut_pct = max(9.0, float(bottom_cut_percent if bottom_cut_percent is not None else (mask_height if mask_height else 9.5)))
        t_cut_pct = max(2.2, float(top_cut_percent if top_cut_percent and top_cut_percent > 0 else 2.5))

        crop_info = cls.calculate_crop(
            vw, vh,
            bottom_cut_percent=cut_pct,
            top_cut_percent=t_cut_pct,
            target_ratio=target_ratio
        )
        render_w = crop_info["w"]
        render_h = crop_info["h"]
        fg_crop_w = crop_info["fg_crop_w"]
        fg_crop_h = crop_info["fg_crop_h"]
        fg_crop_x = crop_info["fg_crop_x"]
        fg_crop_y = crop_info["fg_crop_y"]
        fg_w = crop_info["fg_w"]
        fg_h = crop_info["fg_h"]
        fg_x = crop_info["fg_x"]
        fg_y = crop_info["fg_y"]

        # Kích thước thu nhỏ siêu tốc cho lớp nền mờ (chẵn pixel, nhỏ hơn ~64 lần so với 720p)
        bg_tiny_w = 90 if target_ratio == "9:16" else 160
        bg_tiny_h = 160 if target_ratio == "9:16" else 90

        filter_chains = [
            # Tách 2 luồng thuần planar yuv420p
            f"[0:v]format=yuv420p,split=2[v_bg][v_fg]",
            # Nhánh nền mờ: Scale nhỏ 160x90 -> avgblur C-SIMD + hạ tối eq ngay trên ảnh nhỏ -> phóng fast_bilinear
            f"[v_bg]scale={bg_tiny_w}:{bg_tiny_h}:flags=fast_bilinear,"
            f"avgblur=sizeX=8:sizeY=8,"
            f"eq=brightness=-0.14:saturation=0.78,"
            f"scale={render_w}:{render_h}:flags=fast_bilinear[bg]",
            # Nhánh phim chính 88%: Crop bất đối xứng chẵn pixel -> hflip -> scale bicubic -> eq màu ấm điện ảnh -> unsharp
            f"[v_fg]crop=w={fg_crop_w}:h={fg_crop_h}:x={fg_crop_x}:y={fg_crop_y},"
            f"hflip,"
            f"scale={fg_w}:{fg_h}:flags=bicubic,"
            f"eq=contrast=1.03:saturation=1.05:gamma=1.02:gamma_r=1.03:gamma_b=0.97,"
            f"unsharp=lx=3:ly=3:la=0.22[fg]",
            # Đặt lệch tâm trên nền mờ (Không dùng drawbox để chống AI Auto-Crop dò khung)
            f"[bg][fg]overlay=x={fg_x}:y={fg_y}:format=yuv420:shortest=1[v_pip]"
        ]
        last_v = "v_pip"

        # Chỉ kích hoạt dải mờ bổ sung nếu người dùng đẩy sub_bottom_offset lên cao (> 4.0%)
        # Còn mặc định (< 4.0%) hardsub Trung ở đáy đã bị cắt vật lý sạch 100% bởi fg_crop_h, giúp tiết kiệm 35% CPU!
        b_offset_val = float(sub_bottom_offset if sub_bottom_offset is not None else 0.0)
        if has_mask and render_subtitles and b_offset_val > 4.0:
            b_h_val = float(blur_height if blur_height is not None else 12.0)
            m_w = max(32, min(fg_w, (int(round(fg_w * (mask_width / 100.0))) // 2) * 2))
            m_h = max(16, min(fg_h, (int(round(fg_h * (b_h_val / 100.0))) // 2) * 2))
            m_x = ((fg_x + (fg_w - m_w) // 2) // 2) * 2
            offset_px = int(round(render_h * (b_offset_val / 100.0)))
            m_y = max(0, min(render_h - m_h, ((render_h - offset_px - m_h) // 2) * 2))
            filter_chains.append(
                f"[{last_v}]split=2[v_main][v_crop_src];"
                f"[v_crop_src]crop={m_w}:{m_h}:{m_x}:{m_y},avgblur=sizeX=45:sizeY=6,eq=brightness=-0.08[v_blurred];"
                f"[v_main][v_blurred]overlay={m_x}:{m_y}:format=yuv420:shortest=1[v_masked]"
            )
            last_v = "v_masked"

        # Phụ đề Tiếng Việt Karaoke ASS đè trực tiếp lên ranh giới cạnh dưới của Khung Kính (Phá biên + Semantic Occlusion)
        if render_subtitles and ass_file_path and os.path.exists(ass_file_path) and os.path.getsize(ass_file_path) > 0:
            filter_chains.append(f"[{last_v}]subtitles='{ass_escaped}':force_style='Encoding=UTF-8'[v_sub]")
            last_v = "v_sub"

        logo_inputs = []
        if logo_path and os.path.exists(logo_path):
            logo_inputs = ["-i", str(logo_path)]
            if logo_position == "top_right":
                overlay_pos = f"W-w-{fg_x // 2}:20"
            elif logo_position == "bottom_left":
                overlay_pos = f"{fg_x // 2}:H-h-20"
            elif logo_position == "bottom_right":
                overlay_pos = f"W-w-{fg_x // 2}:H-h-20"
            else:
                overlay_pos = f"{fg_x // 2}:20"
            filter_chains.append(f"[1:v]scale={logo_size}:{logo_size}:flags=bicubic,format=rgba,colorchannelmixer=aa={logo_opacity}[logo]")
            filter_chains.append(f"[{last_v}][logo]overlay={overlay_pos}:format=yuv420[v_logo]")
            last_v = "v_logo"

        # Đổi trực tiếp nhãn cuối cùng thành [v_out] (không cần filter scale trunc(iw/2)*2 dư thừa vì render_w/render_h đã chẵn 100%)
        filter_chains[-1] = re.sub(r"\[[^\]]+\]$", "[v_out]", filter_chains[-1])
        return ";".join(filter_chains), logo_inputs, render_w, render_h

    @classmethod
    def parallel_render_visual_stream(
        cls,
        task_id: str,
        video_input_path: str,
        ass_file_path: Optional[str],
        output_temp_video: str,
        audio_source_path: Optional[str] = None,
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
        Hỗ trợ Single-Pass Concat + Audio Mux trực tiếp, không sinh file trung gian 20GB.
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
                audio_source_path=audio_source_path,
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
            elif vcodec == "h264_nvenc":
                # Card rời NVIDIA (NVENC): Hỗ trợ tối đa tới 8 phiên mã hóa phần cứng song song
                workers = max(3, min(8, cpu_cores // 2 if cpu_cores >= 4 else cpu_cores))
            elif vcodec in ("h264_qsv", "h264_amf"):
                # GPU phần cứng (Intel QuickSync / AMD AMF): Chạy 3 - 4 luồng đồng thời tối ưu nhất
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

        # Đo độ phân giải 1 lần duy nhất cho toàn bộ tiến trình phân đoạn (tránh gọi ffprobe lặp lại N lần)
        cached_video_res = cls.get_video_resolution(video_input_path)

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
            render_subtitles=render_subtitles,
            start_sec=0.0,
            cached_res=cached_video_res
        )

        # Tự động nhận diện FPS gốc và cấu hình bitrate thích ứng độ phân giải để chất lượng video y nguyên bản gốc
        native_fps_val, native_fps_arg = cls.get_video_fps(video_input_path)
        if render_h >= 1080:
            target_bv = "8000k"
            max_bv = "12000k"
            crf_val = "19"
        elif render_h >= 720:
            target_bv = "5000k"
            max_bv = "8000k"
            crf_val = "21"
        else:
            target_bv = "3500k"
            max_bv = "5000k"
            crf_val = "23"

        if task_id:
            task_manager.add_log(task_id, f"🚀 KÍCH HOẠT ENGINE RENDER SONG SONG TỐI ĐA ({workers} Luồng Đồng Thời)...", "purple")
            task_manager.add_log(task_id, f"   • Bộ Mã Hóa: {vcodec} ({encoder_desc})", "cyan")
            task_manager.add_log(task_id, f"   • Khung Hình Chuẩn: {render_w}x{render_h} @ {native_fps_val} FPS (Bitrate: {target_bv})", "cyan")
            task_manager.add_log(task_id, f"   • Video: {cls._format_time(total_duration)} | Chia {num_segments} phân đoạn (~{int(segment_duration)}s/đoạn)", "cyan")

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

            # Tạo filter_complex riêng cho phân đoạn với file ASS tương ứng + khớp pha sóng (t + start_sec)
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
                render_subtitles=render_subtitles,
                start_sec=start_sec,
                cached_res=cached_video_res
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
                cmd.extend(["-crf", crf_val])
            elif vcodec == "h264_nvenc":
                # Tối ưu chuyên biệt cho NVIDIA GeForce/RTX/GTX (Laptop RTX 3050, 4060, v.v.):
                # -spatial_aq 1: Giữ viền nét chữ phụ đề sắc sảo, chống vỡ hạt macroblock
                # -rc:v vbr: Variable Bitrate thích ứng khung hình, xuất video siêu tốc
                cmd.extend(["-rc:v", "vbr", "-cq:v", "20", "-b:v", target_bv, "-maxrate", max_bv, "-spatial_aq", "1"])
            elif vcodec == "h264_amf":
                cmd.extend(["-quality", "speed", "-b:v", target_bv, "-maxrate", max_bv])
            else:
                cmd.extend(["-b:v", target_bv, "-maxrate", max_bv])

            pix_fmt = "nv12" if vcodec == "h264_qsv" else "yuv420p"
            cmd.extend([
                "-pix_fmt", pix_fmt,
                "-r", native_fps_arg,
                "-t", f"{dur_sec:.3f}",
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
                    "-crf", crf_val,
                    "-pix_fmt", "yuv420p",
                    "-r", native_fps_arg,
                    "-t", f"{dur_sec:.3f}",
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
                audio_source_path=audio_source_path,
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

        # Ghép nối các phân đoạn bằng Concat Demuxer
        concat_list_file = temp_seg_dir / "concat_list.txt"
        with open(concat_list_file, "w", encoding="utf-8") as f:
            for seg in segments_info:
                # Dùng relative filename để FFmpeg concat demuxer đọc an toàn tuyệt đối trên Windows/Linux
                f.write(f"file '{seg['output'].name}'\n")

        # Kiểm tra luồng audio: Nếu có audio_source_path và file hợp lệ -> Single-Pass Concat + Audio Mux
        has_audio = False
        valid_audio_path = None
        if audio_source_path:
            # Chờ tối đa 60 giây nếu luồng audio mixer chạy song song chưa kịp hoàn tất
            wait_start = time.time()
            while time.time() - wait_start < 60.0:
                valid_audio_path = cls._find_valid_audio(audio_source_path)
                if valid_audio_path:
                    has_audio = True
                    break
                if task_id and task_manager.is_cancelled(task_id):
                    break
                time.sleep(0.5)

        ffmpeg_cmd = get_ffmpeg_cmd()
        out_dest = Path(output_temp_video).resolve()

        if has_audio and valid_audio_path:
            if task_id:
                task_manager.add_log(task_id, f"⚡ [Single-Pass Concat] Hợp nhất {num_segments} phân đoạn & luồng âm thanh trong 1 lệnh duy nhất...", "cyan")
            audio_codec_args = ["-c:a", "copy"] if valid_audio_path.suffix.lower() in (".m4a", ".aac") else ["-c:a", "aac", "-b:a", "192k"]
            concat_cmd = [
                *ffmpeg_cmd, "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", concat_list_file.name,
                "-i", str(valid_audio_path.resolve()),
                "-map", "0:v:0",
                "-map", "1:a?",
                "-c:v", "copy",
                *audio_codec_args,
                "-map_metadata", "-1",
                "-shortest"
            ]
            # Chỉ thêm +faststart với video <= 30 phút; video dài bỏ qua để tránh ghi lại toàn bộ file lần 2
            if total_duration <= 1800.0:
                concat_cmd.extend(["-movflags", "+faststart"])
            concat_cmd.append(str(out_dest))
        else:
            if task_id:
                task_manager.add_log(task_id, f"⚡ Hợp nhất {num_segments} phân đoạn video (Stream Concat 1s)...", "cyan")
            concat_cmd = [
                *ffmpeg_cmd, "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", concat_list_file.name,
                "-c", "copy",
                "-map_metadata", "-1"
            ]
            if total_duration <= 1800.0:
                concat_cmd.extend(["-movflags", "+faststart"])
            concat_cmd.append(str(out_dest))

        c_res = subprocess.run(concat_cmd, capture_output=True, text=True, errors="replace", cwd=str(temp_seg_dir.resolve()))
        out_video = Path(output_temp_video)

        # Nếu Concat kèm Audio thất bại (do audio chưa sẵn sàng hoặc lỗi mux), tự động fallback sang Concat Video thuần túy
        # để bảo toàn 100% các phân đoạn video đã render cực nhọc, tuyệt đối không hủy bỏ phân đoạn!
        if (c_res.returncode != 0 or not out_video.exists() or out_video.stat().st_size < 1000) and has_audio:
            if task_id:
                task_manager.add_log(task_id, "⚠️ Single-Pass Concat kèm Audio chưa sẵn sàng, chuyển sang Concat Video thuần túy (Bảo toàn 100% phân đoạn)...", "amber")
            fallback_concat_cmd = [
                *ffmpeg_cmd, "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", concat_list_file.name,
                "-c", "copy"
            ]
            if total_duration <= 1800.0:
                fallback_concat_cmd.extend(["-movflags", "+faststart"])
            fallback_concat_cmd.append(str(out_dest))
            c_res = subprocess.run(fallback_concat_cmd, capture_output=True, text=True, errors="replace", cwd=str(temp_seg_dir.resolve()))

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
                audio_source_path=audio_source_path,
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
    def _render_visual_stream_single(
        cls,
        task_id: str,
        video_input_path: str,
        ass_file_path: str,
        output_temp_video: str,
        audio_source_path: Optional[str] = None,
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
        """Render đơn luồng (dành cho video ngắn dưới 60s hoặc fallback). Hỗ trợ Single-Pass muxing."""
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

        total_duration = cls.get_video_duration(video_input_path)
        native_fps_val, native_fps_arg = cls.get_video_fps(video_input_path)
        if render_h >= 1080:
            target_bv = "8000k"
            max_bv = "12000k"
            crf_val = "19"
        elif render_h >= 720:
            target_bv = "5000k"
            max_bv = "8000k"
            crf_val = "21"
        else:
            target_bv = "3500k"
            max_bv = "5000k"
            crf_val = "23"

        has_audio = False
        audio_inputs = []
        audio_maps = ["-an"]
        valid_audio = cls._find_valid_audio(audio_source_path) if audio_source_path else None
        if valid_audio:
            has_audio = True
            audio_inputs = ["-i", str(valid_audio.resolve())]
            a_idx = 1 + (len(logo_inputs) // 2)
            audio_codec_args = ["-c:a", "copy"] if valid_audio.suffix.lower() in (".m4a", ".aac") else ["-c:a", "aac", "-b:a", "192k"]
            audio_maps = ["-map", f"{a_idx}:a?", *audio_codec_args, "-shortest"]

        vcodec, preset, encoder_desc = cls.detect_best_encoder()
        ffmpeg_cmd = get_ffmpeg_cmd()
        cmd = [
            *ffmpeg_cmd, "-y",
            "-i", str(video_input_path),
            *logo_inputs,
            *audio_inputs,
            "-threads", "0",
            "-filter_complex", full_filter_complex,
            "-map", "[v_out]",
            *audio_maps,
            "-c:v", vcodec
        ]
        if preset != "none":
            cmd.extend(["-preset", preset])
        if vcodec == "libx264":
            cmd.extend(["-crf", crf_val])
        elif vcodec == "h264_nvenc":
            cmd.extend(["-rc:v", "vbr", "-cq:v", "20", "-b:v", target_bv, "-maxrate", max_bv, "-spatial_aq", "1"])
        elif vcodec == "h264_amf":
            cmd.extend(["-quality", "speed", "-b:v", target_bv, "-maxrate", max_bv])
        else:
            cmd.extend(["-b:v", target_bv, "-maxrate", max_bv])

        pix_fmt = "nv12" if vcodec == "h264_qsv" else "yuv420p"
        cmd.extend([
            "-pix_fmt", pix_fmt,
            "-r", native_fps_arg,
            "-map_metadata", "-1"
        ])
        if total_duration <= 1800.0 and has_audio:
            cmd.extend(["-movflags", "+faststart"])
        cmd.append(str(output_temp_video))

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
                *audio_inputs,
                "-threads", "0",
                "-filter_complex", clean_part,
                "-map", "[v_out]",
                *audio_maps,
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-crf", crf_val,
                "-pix_fmt", "yuv420p",
                "-r", native_fps_arg
            ]
            if total_duration <= 1800.0 and has_audio:
                fallback_cmd.extend(["-movflags", "+faststart"])
            fallback_cmd.append(str(output_temp_video))

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
        audio_source_path: Optional[str] = None,
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
        Hỗ trợ Single-Pass Concat + Audio Mux trực tiếp khi có audio_source_path.
        """
        dur = cls.get_video_duration(video_input_path)
        if dur > 60.0:
            return cls.parallel_render_visual_stream(
                task_id=task_id,
                video_input_path=video_input_path,
                ass_file_path=ass_file_path,
                output_temp_video=output_temp_video,
                audio_source_path=audio_source_path,
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
                audio_source_path=audio_source_path,
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
        output_final_path: str,
        task_id: Optional[str] = None
    ) -> bool:
        """
        Ghép siêu tốc luồng hình ảnh và âm thanh (Stream Copy 0.5s - 1s).
        Hiển thị tiến độ Stream Copy & di chuyển Moov Atom thời gian thực lên Terminal và Web UI.
        An toàn 100% trên Windows (chống WinError 32 khóa file từ trình duyệt).
        """
        out_p = Path(output_final_path).resolve()
        out_p.parent.mkdir(parents=True, exist_ok=True)
        temp_out = out_p.parent / f"{out_p.stem}_mux_tmp{out_p.suffix}"

        ffmpeg_cmd = get_ffmpeg_cmd()
        total_duration = cls.get_video_duration(visual_video_path)
        actual_audio = cls._find_valid_audio(audio_source_path) or Path(audio_source_path)
        # Kiểm tra nếu audio_source_path đã là AAC/M4A -> Stream Copy 100% không re-encode
        src_audio_ext = actual_audio.suffix.lower()
        if src_audio_ext in (".m4a", ".aac"):
            audio_codec_args = ["-c:a", "copy"]
        else:
            audio_codec_args = ["-c:a", "aac", "-b:a", "192k"]

        cmd = [
            *ffmpeg_cmd, "-y",
            "-i", str(visual_video_path),
            "-i", str(actual_audio.resolve()),
            "-map", "0:v:0",
            "-map", "1:a?",
            "-c:v", "copy",
            *audio_codec_args,
            "-map_metadata", "-1",
            "-shortest"
        ]
        if total_duration <= 1800.0:
            cmd.extend(["-movflags", "+faststart"])
        cmd.append(str(temp_out))

        print(f"   ⚡ Đang hợp nhất luồng Stream Copy & tối ưu FastStart Web...", flush=True)
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            encoding="utf-8",
            errors="replace"
        )

        last_log_t = 0.0
        if proc.stderr:
            for line in proc.stderr:
                if "time=" in line and (time.time() - last_log_t > 3.0):
                    last_log_t = time.time()
                    m = re.search(r"time=(\d+):(\d+):(\d+)", line)
                    if m:
                        h, mm, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
                        mux_msg = f"   📦 Đang ghi video thành phẩm: {h:02d}:{mm:02d}:{s:02d}"
                        print(mux_msg, flush=True)
                        if task_id:
                            task_manager.update_task(task_id, stage=mux_msg)

        proc.wait()

        if proc.returncode != 0 or not temp_out.exists():
            # Fallback nếu stream copy gặp lỗi
            fallback_cmd = [
                *ffmpeg_cmd, "-y",
                "-i", str(visual_video_path),
                "-i", str(audio_source_path),
                "-c:v", "copy",
                "-c:a", "aac",
                "-shortest",
                str(temp_out)
            ]
            fb_proc = subprocess.Popen(
                fallback_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            fb_proc.wait()

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
