import os
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional
from app.config import settings
from app.schemas.transcript import DialogueSegment
from app.utils.bin_helper import get_ffmpeg_cmd

class AudioMixerService:
    @staticmethod
    def mix_bgm_and_voiceover(
        original_video_or_audio: str,
        voiceover_mp3: str,
        output_mixed_audio: str,
        dialogue_segments: Optional[List[DialogueSegment]] = None,
        bgm_volume_when_speaking: float = 0.04,  # Khi có giọng thuyết minh Việt: dập tắt 96% tiếng gốc
        bgm_volume_normal: float = 0.23,          # Khi khoảng nghỉ / im lặng: giữ BGM và âm thanh môi trường
        voiceover_volume: float = 1.0,           # 1.0 (100%) chuẩn xác chống biến dạng
        use_flat_bgm_test: bool = False          # False = bật Ducking thông minh; True = ép phẳng BGM
    ) -> str:
        """
        Kỹ thuật Smart Audio Mixing (Tối ưu Song Song & Stream Copy .m4a):
        - Tự động nhận diện chế độ: Nếu use_flat_bgm_test=True hoặc 2 mức âm lượng bằng nhau -> Chạy BGM phẳng siêu tốc.
        - Khi use_flat_bgm_test=False -> Tự động bật Ducking thông minh:
          + Nới biên khoảng lặng (-0.25s đầu / +0.55s cuối) gộp các câu nghỉ ngắn < 0.80s thành 1 khối dập âm liên tục.
          + Tự động fallback sang Studio Auto Sidechain Ducking (sidechaincompress) khi danh sách câu dài.
        - Thêm cờ -vn trước video gốc để FFmpeg không giải mã hình ảnh, tăng tốc gấp đôi luồng Audio.
        - Hỗ trợ xuất trực tiếp .m4a (AAC 192k) để kích hoạt -c:a copy trong 0.5s ở bước mux final.
        """
        src_orig = Path(original_video_or_audio)
        src_voice = Path(voiceover_mp3)
        out_audio = Path(output_mixed_audio)
        out_audio.parent.mkdir(parents=True, exist_ok=True)

        ffmpeg_cmd = get_ffmpeg_cmd()

        if use_flat_bgm_test or abs(bgm_volume_when_speaking - bgm_volume_normal) < 0.001:
            # =========================================================================
            # [CHẾ ĐỘ BGM PHẲNG ĐỒNG ĐỀU SIÊU TỐC]
            # Giảm phẳng âm lượng track gốc theo bgm_volume_when_speaking trước khi hòa âm.
            # =========================================================================
            flat_bgm_volume = bgm_volume_when_speaking
            mix_filter = (
                f"[0:a]volume={flat_bgm_volume}[bgm];"
                f"[1:a]volume={voiceover_volume}[voice];"
                f"[bgm][voice]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,alimiter=limit=0.95:attack=5:release=50[a_out]"
            )
        else:
            # =========================================================================
            # [CHẾ ĐỘ DUCKING THÔNG MINH - NỚI BIÊN GỘP KHOẢNG NGHỈ]
            # =========================================================================
            timeline_expr = ""
            if dialogue_segments:
                merged_intervals = []
                def _extract_t(item, field_names, fallback=0.0):
                    for f in field_names:
                        if isinstance(item, dict) and f in item and item[f] is not None:
                            return float(item[f])
                        if hasattr(item, f) and getattr(item, f) is not None:
                            return float(getattr(item, f))
                    return fallback

                for seg in sorted(dialogue_segments, key=lambda s: _extract_t(s, ['start', 'start_time'])):
                    s_val = _extract_t(seg, ['start', 'start_time'])
                    e_val = _extract_t(seg, ['end', 'end_time'])
                    # Nới biên gộp khoảng nghỉ: -0.25s đầu / +0.55s cuối -> gộp mọi khoảng ngắt < 0.80s
                    s = max(0.0, s_val - 0.25)
                    e = e_val + 0.55
                    if merged_intervals and s <= merged_intervals[-1][1]:
                        merged_intervals[-1] = (merged_intervals[-1][0], max(merged_intervals[-1][1], e))
                    else:
                        merged_intervals.append((s, e))

                if len(merged_intervals) <= 120:
                    expr_parts = [f"between(t,{start:.2f},{end:.2f})" for start, end in merged_intervals]
                    candidate_expr = "+".join(expr_parts)
                    if len(candidate_expr) < 3500:
                        timeline_expr = candidate_expr

            if timeline_expr:
                mix_filter = (
                    f"[0:a]volume='if({timeline_expr},{bgm_volume_when_speaking},{bgm_volume_normal})':eval=frame[bgm];"
                    f"[1:a]volume={voiceover_volume}[voice];"
                    f"[bgm][voice]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,alimiter=limit=0.95:attack=5:release=50[a_out]"
                )
            else:
                # Thuật toán Auto Sidechain Ducking thông minh tiêu chuẩn phòng thu:
                mix_filter = (
                    f"[0:a]volume={bgm_volume_normal}[bgm];"
                    f"[1:a]volume={voiceover_volume},asplit=2[voice_sc][voice_mix];"
                    f"[bgm][voice_sc]sidechaincompress=threshold=0.015:ratio=16:attack=10:release=180[ducked_bgm];"
                    f"[ducked_bgm][voice_mix]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,alimiter=limit=0.95:attack=5:release=50[a_out]"
                )

        out_ext = out_audio.suffix.lower()
        if out_ext in (".m4a", ".aac"):
            codec_args = ["-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-threads", "0"]
        else:
            codec_args = ["-c:a", "libmp3lame", "-b:a", "192k", "-ar", "44100", "-threads", "0"]

        cmd = [
            *ffmpeg_cmd, "-y",
            "-vn", "-i", str(src_orig),
            "-i", str(src_voice),
            "-filter_complex", mix_filter,
            "-map", "[a_out]",
            *codec_args,
            str(out_audio)
        ]

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        except FileNotFoundError as e:
            if getattr(e, 'winerror', None) == 206 or "too long" in str(e).lower():
                fb_bgm_vol = bgm_volume_when_speaking
                fallback_cmd = [
                    *ffmpeg_cmd, "-y",
                    "-vn", "-i", str(src_orig),
                    "-i", str(src_voice),
                    "-filter_complex", f"[0:a]volume={fb_bgm_vol}[a0];[1:a]volume={voiceover_volume}[a1];[a0][a1]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,alimiter=limit=0.95:attack=5:release=50[a_out]",
                    "-map", "[a_out]",
                    *codec_args,
                    str(out_audio)
                ]
                res = subprocess.run(fallback_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            else:
                raise RuntimeError(f"Không thể khởi chạy công cụ FFmpeg ({ffmpeg_cmd}): {e}")

        if res.returncode != 0 or not out_audio.exists():
            fb_bgm_vol = bgm_volume_when_speaking
            fallback_codec_args = ["-c:a", "aac", "-b:a", "192k", "-ar", "44100"] if out_ext in (".m4a", ".aac") else ["-c:a", "libmp3lame", "-b:a", "192k", "-ar", "44100"]
            simple_cmd = [
                *ffmpeg_cmd, "-y",
                "-vn", "-i", str(src_orig),
                "-i", str(src_voice),
                "-filter_complex", f"[0:a]volume={fb_bgm_vol}[a0];[1:a]volume={voiceover_volume}[a1];[a0][a1]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,alimiter=limit=0.95:attack=5:release=50[a_out]",
                "-map", "[a_out]",
                *fallback_codec_args,
                str(out_audio)
            ]
            res_simple = subprocess.run(simple_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if res_simple.returncode != 0 or not out_audio.exists():
                raise RuntimeError(f"Lỗi khi hòa trộn âm thanh FFmpeg: {res.stderr or res_simple.stderr}")

        return str(out_audio)
