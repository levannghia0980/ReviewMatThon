import os
import sys
import logging
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

class VocalCleanerService:
    """
    Dịch vụ khử nhiễu & tách lọc giọng nói chuyên sâu sử dụng DeepFilterNet3:
    - Cơ chế On-Demand qua Subprocess độc lập: Chỉ nạp model vào GPU trong tiến trình con ngắn hạn.
    - Tự động giải phóng 100% VRAM GPU về 0: Khi tiến trình con kết thúc, hệ điều hành & driver GPU thu hồi toàn bộ bộ nhớ ngay lập tức.
    - Server chính (Uvicorn / FastAPI) hoàn toàn không ngậm GPU, không nóng máy, không ảnh hưởng card đồ họa.
    """

    @classmethod
    def clean_audio(cls, input_audio_path: str, output_audio_path: Optional[str] = None) -> str:
        """
        Khử nhiễu một file audio và xuất ra file WAV mới đã được lọc sạch.
        Chạy trong tiến trình con riêng biệt để GPU tắt ngay sau khi xong.
        """
        src = Path(input_audio_path).resolve()
        if not src.exists():
            raise FileNotFoundError(f"Không tìm thấy file audio: {input_audio_path}")

        if output_audio_path:
            dst = Path(output_audio_path).resolve()
        else:
            dst = (src.parent / f"{src.stem}_clean{src.suffix}").resolve()

        dst.parent.mkdir(parents=True, exist_ok=True)

        worker_code = """
import sys
import torch
from pathlib import Path
from df.enhance import init_df, enhance, load_audio, save_audio

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
model, df_state, _ = init_df()
model.eval()
audio, _ = load_audio(str(src), sr=df_state.sr())
with torch.no_grad():
    enhanced = enhance(model, df_state, audio)
save_audio(str(dst), enhanced, df_state.sr())
"""
        try:
            logger.info(f"[VocalCleaner] Đang nạp DeepFilterNet3 trên GPU để khử nhiễu: {src.name}...")
            res = subprocess.run(
                [sys.executable, "-c", worker_code, str(src), str(dst)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=600
            )
            if res.returncode == 0 and dst.exists() and dst.stat().st_size > 1000:
                logger.info(f"[VocalCleaner] Khử nhiễu thành công -> {dst.name}. Đã giải phóng 100% GPU VRAM.")
                return str(dst)
            else:
                logger.warning(f"[VocalCleaner] Subprocess lỗi ({res.stderr[:200]}), dùng lại file gốc.")
                return str(src)
        except Exception as e:
            logger.warning(f"[VocalCleaner] Không thể chạy subprocess ({e}), dùng lại file gốc: {src.name}")
            return str(src)


