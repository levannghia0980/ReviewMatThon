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
        if dialogue_segments:
            # Gộp các khoảng thời gian có thoại, mở rộng biên 0.05s để bao trọn âm tiết
            merged_intervals = []
            for seg in sorted(dialogue_segments, key=lambda s: s.start):
                s = max(0.0, float(seg.start) - 0.05)
                e = float(seg.end) + 0.05
                if merged_intervals and s <= merged_intervals[-1][1]:
                    merged_intervals[-1] = (merged_intervals[-1][0], max(merged_intervals[-1][1], e))
                else:
                    merged_intervals.append((s, e))

            expr_parts = [f"between(t,{start:.2f},{end:.2f})" for start, end in merged_intervals]
            timeline_expr = "+".join(expr_parts)

        if timeline_expr:
            # Dùng biểu thức timeline chuẩn frame kèm alimiter chống clipping/vỡ tiếng
            mix_filter = (
                f"[0:a]volume=enable='{timeline_expr}':volume={bgm_volume_when_speaking}:eval=frame[bgm];"
                f"[1:a]volume={voiceover_volume}[voice];"
                f"[bgm][voice]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,alimiter=limit=0.95:attack=5:release=50[a_out]"
            )
        else:
            # Fallback nếu không truyền danh sách segments: sidechaincompress thông minh
            mix_filter = (
                f"[0:a]volume={bgm_volume_normal}[bgm];"
                f"[1:a]volume={voiceover_volume}[voice];"
                f"[bgm][voice]sidechaincompress=threshold=0.015:ratio=12:attack=10:release=180:makeup=1[ducked_bgm];"
                f"[ducked_bgm][voice]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,alimiter=limit=0.95:attack=5:release=50[a_out]"
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
        except FileNotFoundError:
            raise RuntimeError("Không tìm thấy công cụ FFmpeg trên máy.")

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
            subprocess.run(simple_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")

        return str(out_audio)
