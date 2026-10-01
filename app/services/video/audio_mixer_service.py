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
        bgm_volume_when_speaking: float = 0.03,
        bgm_volume_normal: float = 0.70,
        voiceover_volume: float = 1.05
    ) -> str:
        """
        Kỹ thuật Smart Audio Mixing & Vocal Suppression:
        - Giữ trọn vẹn nhạc nền (BGM) và hiệu ứng âm thanh (SFX) từ video gốc.
        - Khi có giọng lồng tiếng tiếng Việt: Tự động giảm (ducking) âm lượng track gốc xuống 3% để triệt tiêu tiếng Trung gốc.
        - Khi không có lời thoại (khoảng lặng): Nhạc nền tự động đẩy về mức 70% tự nhiên.
        - Giọng lồng tiếng tiếng Việt to rõ, áp dụng bộ lọc Alimiter chống vỡ tiếng 100%.
        """
        src_orig = Path(original_video_or_audio)
        src_voice = Path(voiceover_mp3)
        out_audio = Path(output_mixed_audio)
        out_audio.parent.mkdir(parents=True, exist_ok=True)

        ffmpeg_cmd = get_ffmpeg_cmd()

        timeline_expr = ""
        # An toàn trên Windows: Biểu thức timeline dùng khi số khoảng thoại sau khi gộp hợp lý
        # Giúp triệt tiêu hoàn toàn tiếng Trung gốc (xuống 3%) khi có thoại và giữ nguyên BGM khi im lặng.
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
                s = max(0.0, s_val - 0.05)
                e = e_val + 0.05
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
            # Khi có lời thoại Việt: Ép âm thanh gốc xuống 3% để triệt tiêu tiếng Trung
            # Khi hết thoại: Trả nhạc nền BGM về mức 70% tự nhiên
            mix_filter = (
                f"[0:a]volume='if({timeline_expr},{bgm_volume_when_speaking},{bgm_volume_normal})':eval=frame[bgm];"
                f"[1:a]volume={voiceover_volume}[voice];"
                f"[bgm][voice]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,alimiter=limit=0.95:attack=5:release=50[a_out]"
            )
        else:
            # Thuật toán Auto Sidechain Ducking thông minh tiêu chuẩn phòng thu:
            # BẮT BUỘC dùng asplit=2 để [voice] vừa làm tín hiệu kích hoạt Sidechain Ducking,
            # vừa giữ lại luồng sạch để trộn vào amix (tránh bị FFmpeg nuốt mất luồng tiếng Việt).
            mix_filter = (
                f"[0:a]volume={bgm_volume_normal}[bgm];"
                f"[1:a]volume={voiceover_volume},asplit=2[voice_sc][voice_mix];"
                f"[bgm][voice_sc]sidechaincompress=threshold=0.015:ratio=16:attack=10:release=180[ducked_bgm];"
                f"[ducked_bgm][voice_mix]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,alimiter=limit=0.95:attack=5:release=50[a_out]"
            )

        cmd = [
            *ffmpeg_cmd, "-y",
            "-i", str(src_orig),
            "-i", str(src_voice),
            "-filter_complex", mix_filter,
            "-map", "[a_out]",
            "-c:a", "libmp3lame",
            "-b:a", "192k",
            "-ar", "44100",
            str(out_audio)
        ]

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        except FileNotFoundError as e:
            # Kiểm tra nếu do lệnh quá dài trên Windows (WinError 206)
            if getattr(e, 'winerror', None) == 206 or "too long" in str(e).lower():
                fallback_cmd = [
                    *ffmpeg_cmd, "-y",
                    "-i", str(src_orig),
                    "-i", str(src_voice),
                    "-filter_complex", f"[0:a]volume={bgm_volume_when_speaking}[a0];[1:a]volume={voiceover_volume}[a1];[a0][a1]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,alimiter=limit=0.95:attack=5:release=50[a_out]",
                    "-map", "[a_out]",
                    "-c:a", "libmp3lame",
                    "-b:a", "192k",
                    "-ar", "44100",
                    str(out_audio)
                ]
                res = subprocess.run(fallback_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            else:
                raise RuntimeError(f"Không thể khởi chạy công cụ FFmpeg ({ffmpeg_cmd}): {e}")

        if res.returncode != 0 or not out_audio.exists():
            # Fallback an toàn với mức âm lượng thấp khi thoại
            simple_cmd = [
                *ffmpeg_cmd, "-y",
                "-i", str(src_orig),
                "-i", str(src_voice),
                "-filter_complex", f"[0:a]volume={bgm_volume_when_speaking}[a0];[1:a]volume={voiceover_volume}[a1];[a0][a1]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,alimiter=limit=0.95:attack=5:release=50[a_out]",
                "-map", "[a_out]",
                "-c:a", "libmp3lame",
                "-b:a", "192k",
                "-ar", "44100",
                str(out_audio)
            ]
            res_simple = subprocess.run(simple_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if res_simple.returncode != 0 or not out_audio.exists():
                raise RuntimeError(f"Lỗi khi hòa trộn âm thanh FFmpeg: {res.stderr or res_simple.stderr}")

        return str(out_audio)
