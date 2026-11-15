import os
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def detect_optimal_workers() -> tuple[int, str, str]:
    """
    Tự động nhận diện phần cứng hiện tại (GPU NVIDIA / Intel QSV / AMD AMF / CPU + RAM)
    và trả về (optimal_workers, vcodec, encoder_desc) tương thích đa máy.
    """
    from app.utils.hardware_profile import recommend_render_workers

    cpu_cores = os.cpu_count() or 4
    vcodec = "libx264"
    preset = "ultrafast"
    encoder_desc = "CPU Đa Luồng Ultrafast"

    try:
        from app.services.step_05_video.video_composer_service import VideoComposerService
        vcodec, preset, encoder_desc = VideoComposerService.detect_best_encoder()
    except Exception:
        pass

    workers = recommend_render_workers(vcodec=vcodec, cpu_cores=cpu_cores)
    return workers, vcodec, f"{encoder_desc} (preset={preset})"


def _upsert_env_key(content: str, key: str, value: str) -> str:
    pattern = re.compile(rf"^{re.escape(key)}\s*=.*$", re.MULTILINE)
    target_line = f"{key}={value}"
    if pattern.search(content):
        return pattern.sub(target_line, content)
    sep = "" if (not content or content.endswith("\n")) else "\n"
    return f"{content}{sep}{target_line}\n"


def apply_env_optimizations() -> int:
    from app.utils.hardware_profile import get_hardware_summary, recommend_ocr_profile

    env_path = BASE_DIR / ".env"
    optimal_workers, vcodec, desc = detect_optimal_workers()
    hw_summary = get_hardware_summary()
    hw_ocr_hint = "cuda" if hw_summary["nvidia_gpus"] else (
        "qsv" if "qsv" in hw_summary["ffmpeg_caps"].get("hwaccels", []) else "cpu"
    )
    ocr_preview = recommend_ocr_profile(hw_decode_name=hw_ocr_hint, video_duration_sec=3600.0, segment_count=500)

    # Giữ OCR ở chế độ tự thích ứng ("0"/"auto") để an toàn tuyệt đối khi đồng bộ/copy dự án sang máy khác
    desired_ocr_defaults = {
        "OCR_WORKERS": "0",
        "OCR_CHUNK_SECONDS": "0",
        "OCR_HWACCEL": "auto",
    }

    if not env_path.exists():
        lines = [f"VIDEO_RENDER_WORKERS={optimal_workers}"] + [
            f"{k}={v}" for k, v in desired_ocr_defaults.items()
        ]
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(
            f"[MachineOptim] Đã khởi tạo .env | VIDEO_RENDER_WORKERS={optimal_workers} ({vcodec} - {desc}) | "
            f"OCR Auto ({ocr_preview['tier']}: {ocr_preview['ocr_workers']} workers, "
            f"chunk~{int(ocr_preview['chunk_target_sec'])}s, RAM={hw_summary['total_ram_gb']}GB)"
        )
        return optimal_workers

    content = env_path.read_text(encoding="utf-8", errors="replace")
    new_content = _upsert_env_key(content, "VIDEO_RENDER_WORKERS", str(optimal_workers))
    for k, v in desired_ocr_defaults.items():
        if not re.search(rf"^{re.escape(k)}\s*=", new_content, re.MULTILINE):
            new_content = _upsert_env_key(new_content, k, v)

    if new_content != content:
        env_path.write_text(new_content, encoding="utf-8")
        print(
            f"[MachineOptim] Đã cập nhật .env: VIDEO_RENDER_WORKERS={optimal_workers} ({vcodec} - {desc}) | "
            f"OCR Auto ({ocr_preview['tier']}: {ocr_preview['ocr_workers']} workers, chunk~{int(ocr_preview['chunk_target_sec'])}s)"
        )
    else:
        print(
            f"[MachineOptim] Cấu hình .env đã tối ưu sẵn: VIDEO_RENDER_WORKERS={optimal_workers} ({vcodec} - {desc}) | "
            f"OCR Auto ({ocr_preview['tier']}: {ocr_preview['ocr_workers']} workers, chunk~{int(ocr_preview['chunk_target_sec'])}s)"
        )

    return optimal_workers


def main():
    apply_env_optimizations()


if __name__ == "__main__":
    main()

