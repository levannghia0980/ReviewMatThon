import os
import subprocess
from pathlib import Path
from app.config import settings
from app.utils.bin_helper import get_ffmpeg_cmd

class AudioExtractorService:
    @staticmethod
    def extract_audio_16k_wav(video_or_audio_path: str, output_name: str = None) -> str:
        """
        Trích xuất âm thanh 16kHz Mono PCM WAV và lưu tại input/audio_raw/
        """
        src = Path(video_or_audio_path)
        if not src.exists():
            raise FileNotFoundError(f"Không tìm thấy file nguồn: {video_or_audio_path}")
            
        base_name = output_name if output_name else src.stem
        out_wav = settings.INPUT_AUDIO_DIR / f"{base_name}_16k.wav"
        
        ffmpeg_cmd = get_ffmpeg_cmd()
        cmd = [
            *ffmpeg_cmd,
            "-y",
            "-i", str(src),
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            str(out_wav)
        ]
        
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        except FileNotFoundError:
            raise RuntimeError("Không tìm thấy công cụ FFmpeg trên máy. Vui lòng kiểm tra lại cấu hình công cụ.")
            
        if res.returncode != 0:
            raise RuntimeError(f"Lỗi khi trích xuất âm thanh qua FFmpeg: {res.stderr}")
            
        return str(out_wav)

    @staticmethod
    def get_clean_audio_for_asr(raw_wav_path: str) -> str:
        """
        Lọc sạch tạp âm bằng DeepFilterNet3 CHỈ DÀNH RIÊNG cho CapCut ASR nhận diện lời thoại.
        TUYỆT ĐỐI không dùng file này để mix BGM vào video (để không mất tiếng động, BGM gốc).
        """
        try:
            from app.services.audio.vocal_cleaner_service import VocalCleanerService
            src = Path(raw_wav_path)
            clean_name = src.stem.replace("_clean", "")
            cleaned_wav = src.parent / f"{clean_name}_clean.wav"
            return VocalCleanerService.clean_audio(str(src), str(cleaned_wav))
        except Exception:
            return str(raw_wav_path)
