import os
import random
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
        bgm_volume_normal: float = 0.23,          # Khi khoảng nghỉ / im lặng: giữ BGM và âm thanh đánh nhau
        voiceover_volume: float = 1.0,           # 1.0 (100%) chuẩn xác chống biến dạng
        use_flat_bgm_test: bool = False          # False = phân 2 loại Lúc nói (0.04) & Không nói (0.23); True = làm phẳng đều BGM song song
    ) -> str:
        """
        Kỹ thuật Smart Audio Mixing & Phá Vỡ Phổ Tần Số Bài Hát (Phương án A - NAFP Breaker):
        - Tích hợp lá chắn âm thanh trên luồng [0:a] (Pitch-Shift vượt ngưỡng NAFP + Tempo-Restore khớp 100% thời gian
          + Sub-Second Vibrato uốn tần số trong cửa sổ 1s + ExtraStereo giữ nguyên 100% độ lực tiếng đánh nhau).
        - Khi use_flat_bgm_test=True: Nhánh tổng hợp ép phẳng đều BGM theo bgm_volume_when_speaking phát song song giọng đọc.
        - Khi use_flat_bgm_test=False (Mặc định): Phân 2 trạng thái Lúc nói (bgm_volume_when_speaking) và Lúc không nói (bgm_volume_normal)
          dựa trên mốc thời gian đã gộp trọn vẹn sau khi tạo TTS xong (hỗ trợ không giới hạn số câu thoại qua chuỗi volume phân tầng).
        """
        src_orig = Path(original_video_or_audio)
        src_voice = Path(voiceover_mp3)
        out_audio = Path(output_mixed_audio)
        out_audio.parent.mkdir(parents=True, exist_ok=True)

        ffmpeg_cmd = get_ffmpeg_cmd()

        # Lá chắn Phương án A trên luồng [0:a]: Phá vỡ Mel-Spectrogram bài hát trong cửa sổ 1s nhưng giữ 100% âm lượng & tiếng đánh nhau
        sys_rng = random.SystemRandom()
        pitch_r = sys_rng.uniform(0.848, 0.865)
        tempo_r = 1.0 / pitch_r
        vib_f = sys_rng.uniform(2.65, 3.35)
        vib_d = sys_rng.uniform(0.30, 0.38)
        stereo_m = sys_rng.uniform(1.30, 1.45)
        shield_chain = (
            f"aresample=44100:filter_size=16:phase_shift=6,asetrate=44100*{pitch_r:.5f},"
            f"atempo={tempo_r:.6f},aresample=44100:filter_size=16:phase_shift=6,"
            f"vibrato=f={vib_f:.2f}:d={vib_d:.2f},extrastereo=m={stereo_m:.2f}"
        )

        if use_flat_bgm_test or abs(bgm_volume_when_speaking - bgm_volume_normal) < 0.001:
            # =========================================================================
            # [NHÁNH TỔNG HỢP BGM PHẲNG ĐỀU PHÁT SONG SONG]
            # Chỉ cần đổi use_flat_bgm_test=True để chạy phẳng đồng đều toàn video.
            # =========================================================================
            flat_bgm_volume = bgm_volume_when_speaking
            mix_filter = (
                f"[0:a]{shield_chain},volume={flat_bgm_volume}[bgm];"
                f"[1:a]volume={voiceover_volume}[voice];"
                f"[bgm][voice]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,alimiter=limit=0.95:attack=5:release=50[a_out]"
            )
        else:
            # =========================================================================
            # [CHẾ ĐỘ PHÂN 2 LOẠI: LÚC NÓI (0.04) VÀ LÚC KHÔNG NÓI (0.23) THEO TIME TTS]
            # =========================================================================
            merged_intervals = []
            if dialogue_segments:
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

            if merged_intervals:
                # Chia thành các bậc tối đa 80 khoảng/bậc để FFmpeg thực thi 100% danh sách time TTS dù phim dài hàng giờ
                safe_normal = max(0.0001, float(bgm_volume_normal))
                duck_ratio = float(bgm_volume_when_speaking) / safe_normal
                vol_stages = [f"volume={bgm_volume_normal}"]
                chunk_size = 80
                for idx in range(0, len(merged_intervals), chunk_size):
                    chunk = merged_intervals[idx:idx + chunk_size]
                    expr_str = "+".join([f"between(t,{start:.2f},{end:.2f})" for start, end in chunk])
                    vol_stages.append(f"volume='if({expr_str},{duck_ratio:.6f},1.0)':eval=frame")

                vol_chain_str = ",".join(vol_stages)
                mix_filter = (
                    f"[0:a]{ vol_chain_str },{shield_chain}[bgm];"
                    f"[1:a]volume={voiceover_volume}[voice];"
                    f"[bgm][voice]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,alimiter=limit=0.95:attack=5:release=50[a_out]"
                )
            else:
                # Dự phòng khi không truyền danh sách câu thoại: Auto Sidechain Ducking
                mix_filter = (
                    f"[0:a]{shield_chain},volume={bgm_volume_normal}[bgm];"
                    f"[1:a]volume={voiceover_volume},asplit=2[voice_sc][voice_mix];"
                    f"[bgm][voice_sc]sidechaincompress=threshold=0.015:ratio=16:attack=10:release=350[ducked_bgm];"
                    f"[ducked_bgm][voice_mix]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,alimiter=limit=0.95:attack=5:release=50[a_out]"
                )

        out_ext = out_audio.suffix.lower()
        if out_ext in (".m4a", ".aac"):
            codec_args = ["-c:a", "aac", "-aac_coder", "fast", "-b:a", "192k", "-ar", "44100", "-threads", "0"]
        else:
            codec_args = ["-c:a", "libmp3lame", "-b:a", "192k", "-ar", "44100", "-threads", "0"]

        tmp_out_audio = out_audio.parent / f"{out_audio.stem}_tmp{out_audio.suffix}"
        try:
            tmp_out_audio.unlink(missing_ok=True)
        except Exception:
            pass

        fc_script_file: Optional[Path] = None
        try:
            if len(mix_filter) > 24000:
                fc_script_file = out_audio.parent / f"{out_audio.stem}_fc.txt"
                fc_script_file.write_text(mix_filter, encoding="utf-8")
                fc_args = ["-/filter_complex", str(fc_script_file)]
            else:
                fc_args = ["-filter_complex", mix_filter]

            cmd = [
                *ffmpeg_cmd, "-y",
                "-threads", "0", "-vn", "-sn", "-dn", "-i", str(src_orig),
                "-threads", "0", "-vn", "-sn", "-dn", "-i", str(src_voice),
                "-filter_complex_threads", "0",
                *fc_args,
                "-map", "[a_out]",
                *codec_args,
                str(tmp_out_audio)
            ]

            try:
                res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            except FileNotFoundError as e:
                if getattr(e, 'winerror', None) == 206 or "too long" in str(e).lower():
                    fb_bgm_vol = bgm_volume_when_speaking
                    fallback_cmd = [
                        *ffmpeg_cmd, "-y",
                        "-threads", "0", "-vn", "-sn", "-dn", "-i", str(src_orig),
                        "-threads", "0", "-vn", "-sn", "-dn", "-i", str(src_voice),
                        "-filter_complex_threads", "0",
                        "-filter_complex", f"[0:a]{shield_chain},volume={fb_bgm_vol}[a0];[1:a]volume={voiceover_volume}[a1];[a0][a1]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,alimiter=limit=0.95:attack=5:release=50[a_out]",
                        "-map", "[a_out]",
                        *codec_args,
                        str(tmp_out_audio)
                    ]
                    res = subprocess.run(fallback_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
                else:
                    raise RuntimeError(f"Không thể khởi chạy công cụ FFmpeg ({ffmpeg_cmd}): {e}")

            if res.returncode != 0 or not tmp_out_audio.exists():
                fb_bgm_vol = bgm_volume_when_speaking
                fallback_codec_args = ["-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-threads", "0"] if out_ext in (".m4a", ".aac") else ["-c:a", "libmp3lame", "-b:a", "192k", "-ar", "44100", "-threads", "0"]
                simple_cmd = [
                    *ffmpeg_cmd, "-y",
                    "-threads", "0", "-vn", "-sn", "-dn", "-i", str(src_orig),
                    "-threads", "0", "-vn", "-sn", "-dn", "-i", str(src_voice),
                    "-filter_complex", f"[0:a]{shield_chain},volume={fb_bgm_vol}[a0];[1:a]volume={voiceover_volume}[a1];[a0][a1]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,alimiter=limit=0.95:attack=5:release=50[a_out]",
                    "-map", "[a_out]",
                    *fallback_codec_args,
                    str(tmp_out_audio)
                ]
                res_simple = subprocess.run(simple_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
                if res_simple.returncode != 0 or not tmp_out_audio.exists():
                    raise RuntimeError(f"Lỗi khi hòa trộn âm thanh FFmpeg: {res.stderr or res_simple.stderr}")

            try:
                if out_audio.exists():
                    out_audio.unlink(missing_ok=True)
                os.replace(str(tmp_out_audio), str(out_audio))
            except Exception:
                import shutil
                shutil.copy2(str(tmp_out_audio), str(out_audio))
                tmp_out_audio.unlink(missing_ok=True)
        finally:
            if fc_script_file and fc_script_file.exists():
                try:
                    fc_script_file.unlink(missing_ok=True)
                except Exception:
                    pass
            if tmp_out_audio.exists():
                try:
                    tmp_out_audio.unlink(missing_ok=True)
                except Exception:
                    pass

        return str(out_audio)

