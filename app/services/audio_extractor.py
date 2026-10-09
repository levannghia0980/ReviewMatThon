import os
import logging
import subprocess
from pathlib import Path
from app.config import settings
from app.utils.bin_helper import get_ffmpeg_cmd

logger = logging.getLogger(__name__)

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

    @staticmethod
    def get_audio_duration(audio_path: str) -> float:
        try:
            ffprobe_cmd = get_ffmpeg_cmd()[0].replace("ffmpeg", "ffprobe")
            cmd = [ffprobe_cmd, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)]
            res = subprocess.run(cmd, capture_output=True, text=True)
            return float(res.stdout.strip())
        except Exception:
            return 0.0

    @staticmethod
    def split_audio_chunks_with_overlap(audio_path: str, chunk_length_sec: int = 2700, overlap_sec: int = 45) -> list:
        """
        Chia nhỏ audio thành các đoạn có khoảng chồng (overlap 45s) để gửi song song cho CapCut Cloud.
        QUY TẮC TỐI ƯU KHỚP NỐI:
        - Mặc định: Mỗi chunk cắt theo DUNG LƯỢNG TỐI ĐA 45 phút (2700s) nén MP3 64kbps 16kHz (~20MB).
        - Nếu video <= 45 phút: GIỮ NGUYÊN 1 CHUNK DUY NHẤT 100%, tuyệt đối không cắt chia nhỏ.
        - Nếu video > 45 phút: Cắt chia theo đúng lượng tối đa 45 phút/chunk (ít mối nối nhất có thể, tránh chia vụn gây khó ghép).
        Trả về danh sách dictionary: [{"path": "...", "offset": 0}, {"path": "...", "offset": 2655}, ...]
        """
        src = Path(audio_path)
        out_dir = src.parent / f"{src.stem}_chunks"
        out_dir.mkdir(parents=True, exist_ok=True)
        
        duration = AudioExtractorService.get_audio_duration(str(src))
        # Nếu video ngắn hơn hoặc bằng 1 chunk (<= 45 phút), giữ nguyên 100% không cắt chia chunk
        if duration <= chunk_length_sec and duration > 0:
            logger.info(f"[Audio Splitter] Audio dài {duration:.1f}s ({duration/60:.1f} phút) <= {chunk_length_sec}s (1 chunk tối đa) -> Giữ nguyên 1 file duy nhất, không chia nhỏ.")
            return [{"path": str(src), "offset": 0.0}]
            
        ffmpeg_cmd = get_ffmpeg_cmd()
        stride = chunk_length_sec - overlap_sec
        chunks_info = []
        
        current_start = 0.0
        idx = 0
        
        # Nếu đã chia rồi thì đọc lại (hỗ trợ cả .mp3 và .wav)
        existing = sorted(list(out_dir.glob("chunk_*.mp3")) or list(out_dir.glob("chunk_*.wav")))
        if existing and duration > 0:
            expected_chunks = int(duration // stride) + 1
            if len(existing) >= expected_chunks - 1:
                for c in existing:
                    offset_val = float(c.stem.split("_")[2]) if len(c.stem.split("_")) > 2 else idx * stride
                    chunks_info.append({"path": str(c), "offset": offset_val})
                    idx += 1
                return chunks_info

        chunks_info = []
        idx = 0
        while True:
            out_file = out_dir / f"chunk_{idx:03d}_{int(current_start)}.wav"
            cmd = [
                *ffmpeg_cmd,
                "-y",
                "-ss", str(current_start),
                "-t", str(chunk_length_sec),
                "-i", str(src),
                "-vn",
                "-acodec", "pcm_s16le",
                "-ar", "16000",
                "-ac", "1",
                str(out_file)
            ]
            subprocess.run(cmd, capture_output=True)
            
            # Kiểm tra nếu file tạo thành công và có dung lượng hợp lệ
            if not out_file.exists() or out_file.stat().st_size < 1000:
                if out_file.exists(): out_file.unlink()
                break
                
            chunks_info.append({"path": str(out_file), "offset": current_start})
            idx += 1
            current_start += stride
            
            if duration > 0 and current_start >= duration:
                break
                
        return chunks_info
