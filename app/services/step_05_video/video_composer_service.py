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
from app.models.dialogue import DialogueSegmentModel, SubtitleSegmentModel
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
        Tự động nhận diện Encoder phần cứng nhanh nhất trên máy (Lưu Cache 1 lần duy nhất):
        1. h264_nvenc  (NVIDIA - preset p2 siêu tốc + spatial_aq giữ nét chữ)
        2. h264_qsv    (Intel GPU QuickSync - phần cứng ASIC siêu tốc)
        3. h264_amf    (AMD RX/RX Vega/RDNA - card rời AMD)
        4. h264_mf     (Windows GPU Media Foundation)
        5. libx264     (CPU Đa Luồng Ultrafast)
        """
        if cls._CACHED_ENCODER is not None:
            return cls._CACHED_ENCODER

        ff_cmd = get_ffmpeg_cmd()
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
        """Đọc gộp (width, height, fps_float, fps_str, duration) trong 1 lần gọi FFprobe và lưu Cache."""
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
            try:
                ffmpeg_cmd = get_ffmpeg_cmd()
                p = subprocess.run([*ffmpeg_cmd, "-i", str(video_path)], capture_output=True, text=True, errors="replace", timeout=6)
                for line in p.stderr.splitlines():
                    if "Video:" in line:
                        m_res = re.search(r" (\d{2,5})x(\d{2,5})", line)
                        if m_res:
                            w, h = int(m_res.group(1)), int(m_res.group(2))
                        m_fps = re.search(r"(\d+(?:\.\d+)?)\s*fps", line)
                        if m_fps:
                            f_v = float(m_fps.group(1))
                            if 10.0 <= f_v <= 120.0:
                                fps_val, fps_str = round(f_v, 3), str(round(f_v, 3))
                    if "Duration:" in line and dur <= 0.0:
                        m_dur = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", line)
                        if m_dur:
                            hh, mm, ss = int(m_dur.group(1)), int(m_dur.group(2)), float(m_dur.group(3))
                            dur = hh * 3600 + mm * 60 + ss
            except Exception:
                pass

        if dur <= 0.0 or w <= 0 or h <= 0:
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
        p = Path(file_path)
        if not p.exists() or p.stat().st_size < 1000:
            return False
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
                    if f.suffix.lower() in (".m4a", ".aac", ".mp3", ".wav") and not f.stem.endswith("_tmp") and cls._is_file_readable_audio(f):
                        return f
        except Exception:
            pass
        return None

    @staticmethod
    def _format_time(seconds: float) -> str:
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
        top_cut_percent: float = 0.0,
        target_ratio: str = "16:9"
    ) -> Dict[str, int]:
        """
        [KHÔI PHỤC NGUYÊN BẢN 100% CROP FULL MÀN HÌNH]:
        - Cắt bỏ phần trên theo top_cut_percent (% chiều cao).
        - Cắt bỏ phần đáy theo bottom_cut_percent (% chiều cao) để xóa bỏ hardsub Trung Quốc cũ.
        - Cắt đều 2 bên (trái & phải) để video đạt chuẩn tỷ lệ 16:9 hoặc 9:16.
        - CHỈ CẮT (CROP) FULL MÀN HÌNH - TUYỆT ĐỐI KHÔNG ĐÓNG KHUNG VIỀN BO MỜ PIP.
        """
        bot_pct = max(4.0, min(45.0, float(bottom_cut_percent if bottom_cut_percent is not None else 4.0)))
        top_pct = max(0.0, min(35.0, float(top_cut_percent if top_cut_percent is not None else 0.0)))

        cut_top = int(round(vh * (top_pct / 100.0)))
        cut_bot = int(round(vh * (bot_pct / 100.0)))

        pan_y = 0
        h_clean = vh - cut_top - cut_bot
        h_target = max(120, (h_clean // 2) * 2)

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

        w_target = max(64, min(vw, (w_target // 2) * 2))
        h_target = max(64, min(vh, (h_target // 2) * 2))
        x = max(0, min(vw - w_target, (x // 2) * 2))
        y = max(0, min(vh - h_target, (y // 2) * 2))

        return {
            "w": w_target,
            "h": h_target,
            "x": x,
            "y": y,
            "pan_y": pan_y,
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
        logo_position: str = "top_left",
        logo_size: int = 120,
        logo_opacity: float = 0.90,
        channel_name: str = "@Mắt Thần Review",
        channel_opacity: float = 0.25,
        karaoke_highlight_color: str = "&H0000D7FF",
        has_mask: bool = True,
        mask_left: float = 0.0,
        mask_top: float = 83.5,
        mask_width: float = 100.0,
        mask_height: float = 16.5,
        backdrop_opacity_hex: str = "FF",
        margin_v: int = 30,
        crop_ratio: str = "16:9",
        font_size: Optional[int] = None,
        box_style: str = "white_box",
        box_padding: int = 8,
        blur_height: Optional[float] = None,
        sub_bottom_offset: Optional[float] = None,
        render_subtitles: bool = True
    ) -> Dict[str, Any]:
        """
        Sản xuất Video Review Hoàn Thiện:
        - Khôi phục 100% giao diện Crop Full Khung + Dải mờ Frosted Glass che sub Trung chuẩn CapCut.
        - Bỏ noise & sin(t) gây nhòe/sôi viền, giữ tốc độ Render GPU Turbo & trộn Audio .m4a song song.
        """
        project = db.query(ProjectTask).filter(ProjectTask.id == project_id).first()
        if not project:
            raise ValueError(f"Không tìm thấy Project ID #{project_id}")

        safe_title = re.sub(r'[\\/*?:"<>|]', "", project.title or "").replace(" ", "_")

        if not project.video_path or not os.path.exists(project.video_path):
            raise FileNotFoundError(f"Không tìm thấy file video gốc: {project.video_path}")

        from app.models.dialogue import SubtitleSegmentModel
        sub_rows = db.query(SubtitleSegmentModel).filter(
            SubtitleSegmentModel.task_id == project_id
        ).order_by(SubtitleSegmentModel.index.asc()).all()

        tts_merged_rows = db.query(DialogueSegmentModel).filter(
            DialogueSegmentModel.task_id == project_id
        ).order_by(DialogueSegmentModel.index.asc()).all()

        dialogues_db = sub_rows if sub_rows else tts_merged_rows

        def _dedup_to_segments(rows) -> List[DialogueSegment]:
            seen_idx = set()
            out_list = []
            for d in rows:
                if d.index not in seen_idx:
                    seen_idx.add(d.index)
                    out_list.append(
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
                    )
            return out_list

        segments = _dedup_to_segments(dialogues_db)
        karaoke_render_segments = segments
        tts_audio_segments = _dedup_to_segments(tts_merged_rows) if tts_merged_rows else segments

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
                from app.config import get_project_dirs
                p_dirs = get_project_dirs(project.id)
                shutil.copyfile(ass_file, p_dirs["root"] / f"{project.video_id}_karaoke.ass")
                shutil.copyfile(ass_file, p_dirs["video_subtitles"] / "subtitles_karaoke.ass")
            except Exception:
                pass
        else:
            if not ass_file.exists():
                with open(ass_file, "w", encoding="utf-8") as f:
                    f.write("")

        source_mask_ass = None
        if has_mask and render_subtitles:
            try:
                mask_ass_file = settings.OUTPUT_TRANSCRIPTS_DIR / f"{project.video_id}_source_mask.ass"
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
                    bg_alpha_hex="90",
                )
                source_mask_ass = str(mask_ass_file)
                task_manager.add_log(task_id, f"   ✔ Đã tạo Source Mask ASS (per-sub blur): {mask_ass_file.name}", "emerald")
            except Exception as e_mask:
                task_manager.add_log(task_id, f"   ⚠️ Không tạo được source mask ASS: {e_mask}", "amber")
                source_mask_ass = None

        from app.config import get_project_dirs
        p_dirs = get_project_dirs(project.id)
        mixed_audio_file = settings.OUTPUT_VOICEOVER_DIR / f"{project.video_id}_mixed_final.m4a"
        proj_root_vo = p_dirs["root"] / f"{project.video_id}_voiceover.mp3"
        voiceover_file = settings.OUTPUT_VOICEOVER_DIR / f"{project.video_id}_{safe_title}_voiceover.mp3"
        if proj_root_vo.exists():
            voiceover_file = proj_root_vo
        elif not voiceover_file.exists():
            candidates = [
                f for f in settings.OUTPUT_VOICEOVER_DIR.glob(f"{project.video_id}*.*")
                if "mixed" not in f.name and f.suffix.lower() in (".mp3", ".m4a", ".wav", ".aac") and f.stat().st_size > 1000
            ]
            voiceover_file = candidates[0] if candidates else None

        audio_thread: Optional[threading.Thread] = None
        audio_source = project.video_path

        if voiceover_file and Path(voiceover_file).exists():
            try:
                mixed_audio_file.unlink(missing_ok=True)
            except Exception:
                pass

            audio_source = str(mixed_audio_file)
            task_manager.add_log(task_id, "   🎵 Đang hòa trộn BGM gốc & lồng tiếng Việt song song (Vocal Ducking)...", "cyan")

            def _async_mix_audio():
                nonlocal audio_source
                try:
                    mixed_path = AudioMixerService.mix_bgm_and_voiceover(
                        original_video_or_audio=project.video_path,
                        voiceover_mp3=str(voiceover_file),
                        output_mixed_audio=str(mixed_audio_file),
                        dialogue_segments=tts_audio_segments,
                        bgm_volume_when_speaking=0.04,
                        bgm_volume_normal=0.23,
                        voiceover_volume=1.0,
                        use_flat_bgm_test=False
                    )
                    audio_source = str(mixed_path)
                    task_manager.add_log(task_id, "   ✔ Đã hòa trộn BGM & giọng Việt thành công.", "emerald")
                except Exception as e:
                    task_manager.add_log(task_id, f"   ⚠️ Mix BGM lỗi, dùng trực tiếp file voiceover: {e}", "amber")
                    audio_source = str(voiceover_file)

            audio_thread = threading.Thread(target=_async_mix_audio, daemon=True)
            audio_thread.start()
        elif mixed_audio_file.exists():
            audio_source = str(mixed_audio_file)
        else:
            task_manager.add_log(task_id, "   ⚠️ Chưa tìm thấy file voiceover lồng tiếng, sử dụng âm thanh gốc.", "amber")
            audio_source = project.video_path

        task_manager.update_task(task_id, step=4, progress=65)
        task_manager.add_log(task_id, "[4/4] 🚀 Kích hoạt Render Video (Engine Single-Pass Siêu Tốc)...", "purple")

        output_video_path = settings.OUTPUT_FINAL_VIDEOS_DIR / f"{project.video_id}_{safe_title}_final.mp4"
        temp_visual_video = settings.TEMP_DIR / f"{project.video_id}_{safe_title}_visual_tmp.mp4"

        for old_f in (output_video_path, temp_visual_video):
            if old_f.exists():
                try:
                    old_f.unlink(missing_ok=True)
                except Exception:
                    pass

        # Với video ngắn <= 60s, chờ audio_thread hoàn tất để mux trực tiếp trong 1 pass
        if audio_thread is not None and cls.get_video_duration(project.video_path) <= 60.0:
            audio_thread.join(timeout=60.0)

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

        if audio_thread is not None and audio_thread.is_alive():
            audio_thread.join(timeout=30.0)

        if not v_ok or not output_video_path.exists() or output_video_path.stat().st_size < 1000:
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

        project.final_video_path = str(output_video_path)
        project.status = "COMPLETED"
        db.commit()

        task_manager.add_log(task_id, f"🎉 HOÀN TẤT XUẤT BẢN VIDEO REVIEW! File: output/final_videos/{output_video_path.name}", "emerald")
        task_manager.add_log(task_id, f"   • Karaoke Sub & Dải mờ: Đồng bộ 100% ({ass_w}x{ass_h})", "emerald")
        task_manager.add_log(task_id, f"   • Audio Mix: BGM gốc giữ nguyên + Lồng tiếng Việt nét căng", "emerald")

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
        top_cut_percent: Optional[float] = 0.0,
        blur_height: Optional[float] = 13.0,
        sub_bottom_offset: Optional[float] = 0.0,
        render_subtitles: bool = True,
        start_sec: float = 0.0,
        cached_res: Optional[Tuple[int, int]] = None
    ) -> Tuple[str, List[str], int, int]:
        """
        [KHÔI PHỤC GIAO DIỆN GỐC + LÁCH QUÉT KHÔNG NOISE]:
        1. Bước 1: Crop cắt thẳng (Full màn hình, không bo viền PIP, khóa tĩnh chẵn pixel chống sôi viền).
        2. Bước 1.5: Lá chắn lách quét (hflip + unsharp + eq tách gamma R/B, xóa bỏ noise làm nhòe hình).
        3. Bước 2: Khôi phục 100% Dải nền mờ Frosted Glass che sạch sub Trung cũ.
        4. Bước 3: Đốt phụ đề Karaoke ASS Tiếng Việt và Logo kênh lên trên cùng.
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

        filter_chains = []
        last_v = "0:v"

        vw, vh = cached_res if cached_res else cls.get_video_resolution(video_input_path)
        cut_pct = max(4.0, float(bottom_cut_percent if bottom_cut_percent is not None else 4.0))
        t_cut_pct = max(0.0, float(top_cut_percent if top_cut_percent is not None else 0.0))

        # 🎬 BƯỚC 1: CROP CẮT THẲNG FULL MÀN HÌNH (Khóa tọa độ chẵn tĩnh -> 0% sôi viền, 0% bo khung thừa)
        crop_info = cls.calculate_crop(vw, vh, bottom_cut_percent=cut_pct, top_cut_percent=t_cut_pct, target_ratio=target_ratio)
        render_w = crop_info["w"]
        render_h = crop_info["h"]
        crop_x = crop_info["x"]
        crop_y = crop_info["y"]

        filter_chains.append(f"[0:v]crop=w={render_w}:h={render_h}:x={crop_x}:y={crop_y}[v_clean]")
        last_v = "v_clean"

        # 🛡️ BƯỚC 1.5: LÁ CHẮN CHỐNG QUÉT DEEP CNN (SẠCH 100% NOISE & KHÔNG RUNG LẮC)
        # Giữ nguyên hflip + unsharp + eq lệch pha gamma R/B để phá mã băm CNN mà hình ảnh trong vắt, siêu tốc
        filter_chains.append(
            f"[{last_v}]hflip,"
            f"unsharp=lx=3:ly=3:la=0.20,"
            f"eq=contrast=1.04:saturation=1.06:gamma=1.03:gamma_r=1.03:gamma_b=0.97:brightness=0.01[v_shield]"
        )
        last_v = "v_shield"

        # 🎬 BƯỚC 2: KHÔI PHỤC 100% DẢI NỀN MỜ FROSTED GLASS CHE SUB CŨ TRÊN KHUNG HÌNH
        if has_mask and render_subtitles:
            b_h_val = float(blur_height if blur_height is not None else (mask_height if mask_height and mask_height > 0 else 13.0))
            b_offset_val = float(sub_bottom_offset if sub_bottom_offset is not None else 0.0)

            m_w = int(round(render_w * (mask_width / 100.0))) if mask_width else render_w
            m_h = int(round(render_h * (b_h_val / 100.0)))
            m_x = max(0, int(round((render_w - m_w) / 2.0)))

            offset_bottom_px = int(round(render_h * (b_offset_val / 100.0)))
            if offset_bottom_px <= 2:
                m_y = max(0, render_h - m_h)
                mask_expr = "if(lt(Y,8),245*(Y/8),245)"
            else:
                m_y = max(0, render_h - offset_bottom_px - m_h)
                f_h = max(2, int(round(m_h * 0.12)))
                mask_expr = f"if(lt(Y,{f_h}),245*(Y/{f_h}),if(gt(Y,H-{f_h}),245*((H-Y)/{f_h}),245))"

            m_w = max(32, min(render_w, (m_w // 2) * 2))
            m_h = max(16, min(render_h, (m_h // 2) * 2))
            m_x = max(0, min(render_w - m_w, (m_x // 2) * 2))
            m_y = max(0, min(render_h - m_h, (m_y // 2) * 2))

            filter_chains.append(
                f"color=s={m_w}x{m_h}:c=black,format=gray,geq=lum='{mask_expr}',loop=loop=-1:size=1:start=0[mask_alpha];"
                f"[{last_v}]split=2[v_main][v_crop_src];"
                f"[v_crop_src]crop={m_w}:{m_h}:{m_x}:{m_y},"
                f"avgblur=sizeX=65:sizeY=5,"
                f"colorchannelmixer=rr=0.85:gg=0.85:bb=0.85,"
                f"eq=brightness=0.14,"
                f"format=rgba[v_blurred];"
                f"[v_blurred][mask_alpha]alphamerge[v_feathered];"
                f"[v_main][v_feathered]overlay={m_x}:{m_y}:format=yuv420:shortest=1[v_masked]"
            )
            last_v = "v_masked"

        # 🎬 BƯỚC 3: Phụ đề Tiếng Việt Karaoke ASS đặt đè lên dải mờ
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
            filter_chains.append(f"[1:v]scale={logo_size}:{logo_size}:flags=bicubic,format=rgba,colorchannelmixer=aa={logo_opacity}[logo]")
            filter_chains.append(f"[{last_v}][logo]overlay={overlay_pos}:format=yuv420[v_logo]")
            last_v = "v_logo"

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
        cached_w, cached_h, native_fps_val, native_fps_arg, total_duration = cls.get_video_metadata(video_input_path)
        cached_video_res = (cached_w, cached_h)

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

        if max_workers is not None and max_workers > 0:
            workers = max_workers
        else:
            env_workers = os.getenv("VIDEO_RENDER_WORKERS")
            if env_workers and env_workers.isdigit() and int(env_workers) > 0:
                workers = int(env_workers)
            elif vcodec == "h264_nvenc":
                workers = max(3, min(8, cpu_cores // 2 if cpu_cores >= 4 else cpu_cores))
            elif vcodec in ("h264_qsv", "h264_amf"):
                workers = max(2, min(4, cpu_cores // 2 if cpu_cores >= 4 else cpu_cores))
            else:
                workers = max(1, min(max(1, cpu_cores - 1), 8) if cpu_cores > 2 else cpu_cores)

        if total_duration <= 300.0:
            segment_duration = max(30.0, total_duration / float(max(2, workers)))
        elif total_duration <= 1800.0:
            segment_duration = 150.0
        else:
            segment_duration = 300.0

        num_segments = math.ceil(total_duration / segment_duration)
        workers = min(workers, num_segments)
        threads_per_worker = max(1, cpu_cores // workers)

        _, _, render_w, render_h = cls._build_visual_filter_complex(
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

        if render_h >= 1080:
            target_bv, max_bv, crf_val = "8000k", "12000k", "19"
        elif render_h >= 720:
            target_bv, max_bv, crf_val = "5000k", "8000k", "21"
        else:
            target_bv, max_bv, crf_val = "3500k", "5000k", "23"

        if task_id:
            task_manager.add_log(task_id, f"🚀 KÍCH HOẠT ENGINE RENDER SONG SONG TỐI ĐA ({workers} Luồng Đồng Thời)...", "purple")
            task_manager.add_log(task_id, f"   • Bộ Mã Hóa: {vcodec} ({encoder_desc})", "cyan")
            task_manager.add_log(task_id, f"   • Khung Hình Chuẩn: {render_w}x{render_h} @ {native_fps_val} FPS (Bitrate: {target_bv})", "cyan")
            task_manager.add_log(task_id, f"   • Video: {cls._format_time(total_duration)} | Chia {num_segments} phân đoạn (~{int(segment_duration)}s/đoạn)", "cyan")

        out_p = Path(output_temp_video).resolve()
        out_p.parent.mkdir(parents=True, exist_ok=True)
        temp_seg_dir = out_p.parent / f"segs_{out_p.stem}"
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

            seg_ass_path = None
            if render_subtitles and ass_file_path and os.path.exists(ass_file_path) and os.path.getsize(ass_file_path) > 0:
                seg_ass_file = temp_seg_dir / f"seg_{i:05d}.ass"
                seg_ass_path = KaraokeSubtitleService.slice_ass_for_interval(
                    source_ass_path=ass_file_path,
                    output_slice_path=str(seg_ass_file),
                    start_sec=start_sec,
                    dur_sec=dur_sec
                )

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

            try:
                seg_file.unlink(missing_ok=True)
            except Exception:
                pass

            ffmpeg_cmd = get_ffmpeg_cmd()
            cmd = [
                *ffmpeg_cmd, "-y",
                "-hwaccel", "auto",
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
                cmd.extend(["-crf", crf_val, "-tune", "fastdecode"])
            elif vcodec == "h264_nvenc":
                cmd.extend([
                    "-tune", "hq",
                    "-rc:v", "vbr",
                    "-cq:v", "21",
                    "-b:v", target_bv,
                    "-maxrate", max_bv,
                    "-spatial_aq", "1",
                    "-bf", "2"
                ])
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

            seg_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(settings.BASE_DIR)
            )
            task_manager.register_subprocess(task_id, seg_proc)
            stderr_out = ""
            try:
                while seg_proc.poll() is None:
                    if task_id and task_manager.is_cancelled(task_id):
                        try:
                            seg_proc.kill()
                        except Exception:
                            pass
                        return idx, False
                    time.sleep(0.3)
                _, stderr_out = seg_proc.communicate()
            finally:
                task_manager.unregister_subprocess(task_id, seg_proc)

            if seg_proc.returncode != 0 or not seg_file.exists() or seg_file.stat().st_size < 1000:
                if task_id and task_manager.is_cancelled(task_id):
                    return idx, False
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
                fb_proc = subprocess.Popen(
                    fb_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                    encoding="utf-8",
                    errors="replace",
                    cwd=str(settings.BASE_DIR)
                )
                task_manager.register_subprocess(task_id, fb_proc)
                fb_stderr_out = ""
                try:
                    while fb_proc.poll() is None:
                        if task_id and task_manager.is_cancelled(task_id):
                            try:
                                fb_proc.kill()
                            except Exception:
                                pass
                            return idx, False
                        time.sleep(0.3)
                    _, fb_stderr_out = fb_proc.communicate()
                finally:
                    task_manager.unregister_subprocess(task_id, fb_proc)

                if fb_proc.returncode != 0 or not seg_file.exists() or seg_file.stat().st_size < 1000:
                    if task_id and task_manager.is_cancelled(task_id):
                        return idx, False
                    err_msg = (fb_stderr_out or stderr_out or "").strip()
                    err_lines = [l for l in err_msg.splitlines() if l.strip() and not l.startswith("frame=")]
                    summary_err = "\n".join(err_lines[-10:])
                    print(f"❌ [Segment #{idx+1}] Lỗi render FFmpeg (code={fb_proc.returncode}):\n{summary_err}", flush=True)
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

        concat_list_file = temp_seg_dir / "concat_list.txt"
        with open(concat_list_file, "w", encoding="utf-8") as f:
            for seg in segments_info:
                f.write(f"file '{seg['output'].name}'\n")

        has_audio = False
        valid_audio_path = None
        if audio_source_path:
            wait_start = time.time()
            while time.time() - wait_start < 60.0:
                valid_audio_path = cls._find_valid_audio(audio_source_path)
                if valid_audio_path:
                    has_audio = True
                    break
                if task_id and task_manager.is_cancelled(task_id):
                    break
                time.sleep(0.3)

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

        if (c_res.returncode != 0 or not out_video.exists() or out_video.stat().st_size < 1000) and has_audio:
            if task_id:
                task_manager.add_log(task_id, "⚠️ Single-Pass Concat kèm Audio chưa sẵn sàng, chuyển sang Concat Video thuần túy...", "amber")
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
        cached_w, cached_h, _, native_fps_arg, total_duration = cls.get_video_metadata(video_input_path)
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
            cached_res=(cached_w, cached_h)
        )

        if render_h >= 1080:
            target_bv, max_bv, crf_val = "8000k", "12000k", "19"
        elif render_h >= 720:
            target_bv, max_bv, crf_val = "5000k", "8000k", "21"
        else:
            target_bv, max_bv, crf_val = "3500k", "5000k", "23"

        has_audio = False
        audio_inputs = []
        audio_maps = ["-an"]
        valid_audio = None
        if audio_source_path:
            wait_start = time.time()
            while time.time() - wait_start < 60.0:
                valid_audio = cls._find_valid_audio(audio_source_path)
                if valid_audio:
                    break
                if task_id and task_manager.is_cancelled(task_id):
                    break
                time.sleep(0.3)

        if valid_audio:
            has_audio = True
            audio_inputs = ["-i", str(valid_audio.resolve())]
            a_idx = 1 + (len(logo_inputs) // 2)
            audio_codec_args = ["-c:a", "copy"] if valid_audio.suffix.lower() in (".m4a", ".aac") else ["-c:a", "aac", "-b:a", "192k"]
            audio_maps = ["-map", f"{a_idx}:a?", *audio_codec_args, "-shortest"]

        vcodec, preset, _ = cls.detect_best_encoder()
        ffmpeg_cmd = get_ffmpeg_cmd()
        cmd = [
            *ffmpeg_cmd, "-y",
            "-hwaccel", "auto",
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
            cmd.extend(["-crf", crf_val, "-tune", "fastdecode"])
        elif vcodec == "h264_nvenc":
            cmd.extend(["-tune", "hq", "-rc:v", "vbr", "-cq:v", "21", "-b:v", target_bv, "-maxrate", max_bv, "-spatial_aq", "1", "-bf", "2"])
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
        task_manager.register_subprocess(task_id, process)

        last_progress_time = 0.0
        stderr_tail = []
        try:
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
                        return False

                    if "time=" in line and (time.time() - last_progress_time > 3.0):
                        last_progress_time = time.time()
                        m = re.search(r"time=(\d+):(\d+):(\d+)", line)
                        if m:
                            h, mm, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
                            print(f"   🎬 Đang Render Video ({vcodec}): {h:02d}:{mm:02d}:{s:02d}")
                            if task_id:
                                task_manager.update_task(task_id, stage=f"Render Video: {h:02d}:{mm:02d}:{s:02d}")

            process.wait()
        finally:
            task_manager.unregister_subprocess(task_id, process)

        out_p = Path(output_temp_video)
        if process.returncode != 0 or not out_p.exists() or out_p.stat().st_size < 1000:
            if task_id and task_manager.is_cancelled(task_id):
                return False
            err_summary = "".join(stderr_tail[-20:])
            print(f"[RenderVisualStream] Lỗi encode ({vcodec}), tự động chuyển CPU Ultrafast:\n{err_summary}")
            if task_id:
                task_manager.add_log(task_id, f"   ⚠️ Chuyển sang bộ mã hóa CPU Đa Luồng Ultrafast an toàn...", "amber")
            fallback_cmd = [
                *ffmpeg_cmd, "-y",
                "-i", str(video_input_path),
                *logo_inputs,
                *audio_inputs,
                "-threads", "0",
                "-filter_complex", full_filter_complex,
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
            task_manager.register_subprocess(task_id, fallback_proc)
            last_fb_time = 0.0
            fb_stderr_tail = []
            try:
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
                            return False
                        if "time=" in line and (time.time() - last_fb_time > 3.0):
                            last_fb_time = time.time()
                            m = re.search(r"time=(\d+):(\d+):(\d+)", line)
                            if m:
                                h, mm, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
                                print(f"   🎬 Đang Render CPU Ultrafast: {h:02d}:{mm:02d}:{s:02d}")
                                if task_id:
                                    task_manager.update_task(task_id, stage=f"CPU Render: {h:02d}:{mm:02d}:{s:02d}")
                fallback_proc.wait()
            finally:
                task_manager.unregister_subprocess(task_id, fallback_proc)

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
        out_p = Path(output_final_path).resolve()
        out_p.parent.mkdir(parents=True, exist_ok=True)
        temp_out = out_p.parent / f"{out_p.stem}_mux_tmp{out_p.suffix}"

        ffmpeg_cmd = get_ffmpeg_cmd()
        total_duration = cls.get_video_duration(visual_video_path)
        actual_audio = cls._find_valid_audio(audio_source_path) or Path(audio_source_path)
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

        try:
            if out_p.exists():
                try:
                    out_p.unlink(missing_ok=True)
                except Exception:
                    pass
            os.replace(str(temp_out), str(out_p))
        except Exception as e:
            print(f"[MuxFinalVideo] Không thể ghi đè file cũ (đang bị lock), lưu tạm file: {e}")
            try:
                shutil.copy2(str(temp_out), str(out_p))
                temp_out.unlink(missing_ok=True)
            except Exception:
                pass

        return out_p.exists() or temp_out.exists()
