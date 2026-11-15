import ctypes
import logging
import os
import shutil
import subprocess
import sys
import threading
from typing import Any, Dict, List, Optional, Set, Tuple

from app.utils.bin_helper import get_ffmpeg_cmd

logger = logging.getLogger(__name__)

_CACHE_LOCK = threading.Lock()
_FFMPEG_CAPS_CACHE: Optional[Dict[str, Set[str]]] = None
_NVIDIA_STATIC_CACHE: Optional[List[Dict[str, Any]]] = None


def get_system_memory_gb() -> Tuple[float, float]:
    """
    Trả về (total_ram_gb, available_ram_gb) của máy hiện tại mà không phụ thuộc psutil.
    Hoạt động trên Windows, Linux và macOS.
    """
    try:
        import psutil

        vm = psutil.virtual_memory()
        return (
            round(vm.total / (1024**3), 2),
            round(vm.available / (1024**3), 2),
        )
    except ImportError:
        pass
    except Exception:
        pass

    if sys.platform == "win32":
        try:
            class _MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = _MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return (
                    round(stat.ullTotalPhys / (1024**3), 2),
                    round(stat.ullAvailPhys / (1024**3), 2),
                )
        except Exception:
            pass
    else:
        try:
            if os.path.exists("/proc/meminfo"):
                total_kb = 0
                avail_kb = 0
                with open("/proc/meminfo", "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        if line.startswith("MemTotal:"):
                            total_kb = int(line.split()[1])
                        elif line.startswith("MemAvailable:"):
                            avail_kb = int(line.split()[1])
                if total_kb > 0:
                    if avail_kb <= 0:
                        avail_kb = total_kb // 2
                    return (
                        round(total_kb / (1024**2), 2),
                        round(avail_kb / (1024**2), 2),
                    )
        except Exception:
            pass

    return 8.0, 4.0


def detect_nvidia_gpus(refresh: bool = False) -> List[Dict[str, Any]]:
    """
    Dò GPU NVIDIA qua nvidia-smi (tên card, tổng VRAM MiB, VRAM trống MiB).
    """
    global _NVIDIA_STATIC_CACHE
    with _CACHE_LOCK:
        if not refresh and _NVIDIA_STATIC_CACHE is not None:
            return [dict(item) for item in _NVIDIA_STATIC_CACHE]

    smi = shutil.which("nvidia-smi")
    if not smi and sys.platform == "win32":
        sys_smi = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvidia-smi.exe")
        if os.path.isfile(sys_smi):
            smi = sys_smi

    gpus: List[Dict[str, Any]] = []
    if smi:
        try:
            proc = subprocess.run(
                [
                    smi,
                    "--query-gpu=index,name,memory.total,memory.free",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=3,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                for raw_line in proc.stdout.strip().splitlines():
                    parts = [p.strip() for p in raw_line.split(",")]
                    if len(parts) < 4:
                        continue
                    try:
                        idx = int(parts[0])
                    except Exception:
                        idx = len(gpus)
                    try:
                        vram_total = float(parts[2])
                    except Exception:
                        vram_total = 0.0
                    try:
                        vram_free = float(parts[3])
                    except Exception:
                        vram_free = vram_total
                    gpus.append({
                        "index": idx,
                        "name": parts[1],
                        "vram_total_mb": int(round(vram_total)),
                        "vram_free_mb": int(round(vram_free)),
                    })
        except Exception as e:
            logger.debug(f"[HardwareProfile] Không đọc được nvidia-smi: {e}")

    with _CACHE_LOCK:
        _NVIDIA_STATIC_CACHE = [dict(item) for item in gpus]
    return gpus


def get_ffmpeg_capabilities() -> Dict[str, Set[str]]:
    """Đọc và cache danh sách hwaccel và decoder phần cứng có sẵn trong ffmpeg."""
    global _FFMPEG_CAPS_CACHE
    with _CACHE_LOCK:
        if _FFMPEG_CAPS_CACHE is not None:
            return {
                "hwaccels": set(_FFMPEG_CAPS_CACHE["hwaccels"]),
                "decoders": set(_FFMPEG_CAPS_CACHE["decoders"]),
            }

    ff_cmd = get_ffmpeg_cmd()
    hwaccels: Set[str] = set()
    decoders: Set[str] = set()

    try:
        res_hw = subprocess.run(
            [*ff_cmd, "-hide_banner", "-hwaccels"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=4,
        )
        if res_hw.returncode == 0:
            for line in res_hw.stdout.splitlines():
                tok = line.strip().lower()
                if tok and not tok.startswith("hardware acceleration"):
                    hwaccels.add(tok)
    except Exception:
        pass

    try:
        res_dec = subprocess.run(
            [*ff_cmd, "-hide_banner", "-decoders"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=4,
        )
        if res_dec.returncode == 0:
            for line in res_dec.stdout.splitlines():
                parts = line.strip().split()
                if len(parts) >= 2 and parts[0].startswith("V"):
                    dec = parts[1].lower()
                    if any(tag in dec for tag in ("cuvid", "qsv", "amf", "mf", "dav1d")):
                        decoders.add(dec)
    except Exception:
        pass

    caps = {"hwaccels": hwaccels, "decoders": decoders}
    with _CACHE_LOCK:
        _FFMPEG_CAPS_CACHE = caps
    return {"hwaccels": set(hwaccels), "decoders": set(decoders)}


def recommend_render_workers(vcodec: str, cpu_cores: Optional[int] = None) -> int:
    """Tính số luồng render video song song tối ưu theo encoder và phần cứng máy."""
    cores = max(1, int(cpu_cores or os.cpu_count() or 4))
    total_ram_gb, _ = get_system_memory_gb()

    if vcodec == "h264_nvenc":
        workers = 4 if cores >= 8 else max(3, min(4, cores // 2 if cores >= 4 else cores))
    elif vcodec in ("h264_qsv", "h264_amf"):
        workers = 4 if cores >= 8 else max(2, min(4, cores // 2 if cores >= 4 else cores))
    else:
        workers = max(1, cores - 1)

    if total_ram_gb < 9.0 and workers > 3:
        workers = 3 if vcodec in ("h264_nvenc", "h264_qsv", "h264_amf") else max(2, workers - 1)
    return max(1, workers)


def recommend_ocr_profile(
    hw_decode_name: str,
    video_duration_sec: float = 0.0,
    segment_count: int = 0,
    vid_width: int = 1920,
    vid_height: int = 1080,
    env_workers: int = 0,
    env_chunk_sec: int = 0,
) -> Dict[str, Any]:
    """
    Tính cấu hình OCR thích ứng (số luồng song song, thread ONNX/FFmpeg mỗi worker,
    và dung lượng chunk) theo phần cứng thực tế (RTX NVDEC / Intel QSV / D3D11VA / CPU)
    + RAM/VRAM còn trống + độ dài phim.
    """
    cpu_cores = max(1, int(os.cpu_count() or 4))
    total_ram_gb, free_ram_gb = get_system_memory_gb()
    gpus = detect_nvidia_gpus(refresh=True)
    best_gpu = max(gpus, key=lambda g: g.get("vram_total_mb", 0), default={})
    vram_total_mb = int(best_gpu.get("vram_total_mb", 0) or 0)
    vram_free_mb = int(best_gpu.get("vram_free_mb", 0) or vram_total_mb)
    gpu_name = str(best_gpu.get("name", "") or "")

    # Ngân sách RAM cho mỗi worker: FFmpeg decode + 1 phiên RapidOCR PP-OCRv4 + queue
    is_4k = max(int(vid_width or 0), int(vid_height or 0)) >= 2160
    is_1080 = max(int(vid_width or 0), int(vid_height or 0)) >= 1080
    per_worker_ram_gb = 0.50 if is_4k else (0.38 if is_1080 else 0.30)
    ram_headroom_gb = 1.15 if total_ram_gb <= 9.0 else 1.80
    ram_cap = max(1, int(max(0.4, free_ram_gb - ram_headroom_gb) // per_worker_ram_gb))
    if total_ram_gb <= 9.0:
        ram_cap = min(ram_cap, 3)

    hw_norm = (hw_decode_name or "cpu").lower()

    if hw_norm == "cuda":
        vram_per_decode_mb = 350 if is_4k else (220 if is_1080 else 150)
        eff_vram_mb = vram_free_mb if vram_free_mb >= 512 else vram_total_mb
        vram_cap = (
            max(1, int(max(250, eff_vram_mb - 550) // vram_per_decode_mb))
            if eff_vram_mb > 0
            else 3
        )
        if vram_total_mb >= 5500 and total_ram_gb >= 14.0:
            tier = "nvidia_nvdec_workstation"
            base_workers = 5 if cpu_cores >= 12 else (4 if cpu_cores >= 6 else 3)
        elif vram_total_mb >= 3500:
            tier = "nvidia_nvdec_mid"
            base_workers = 4 if (cpu_cores >= 8 and total_ram_gb >= 12.0) else 3
        else:
            tier = "nvidia_nvdec_compact"
            base_workers = 2
        auto_workers = max(1, min(base_workers, vram_cap, ram_cap, max(1, cpu_cores - 1)))
    elif hw_norm in ("qsv", "d3d11va"):
        if cpu_cores >= 12 and total_ram_gb >= 14.0:
            tier = f"{hw_norm}_high"
            base_workers = 4
        elif cpu_cores >= 8 and total_ram_gb >= 11.0:
            tier = f"{hw_norm}_mid"
            base_workers = 3
        elif cpu_cores >= 8 and free_ram_gb >= 3.0:
            tier = f"{hw_norm}_compact"
            base_workers = 3
        else:
            tier = f"{hw_norm}_compact"
            base_workers = 2 if cpu_cores >= 4 else 1
        auto_workers = max(1, min(base_workers, ram_cap, max(1, cpu_cores - 1)))
    else:
        if cpu_cores >= 8 and total_ram_gb >= 14.0:
            tier = "cpu_multicore"
            base_workers = min(5, max(2, cpu_cores // 2))
        elif cpu_cores >= 4 and total_ram_gb >= 6.5:
            tier = "cpu_standard"
            base_workers = 2
        else:
            tier = "low_spec"
            base_workers = 1
        auto_workers = max(1, min(base_workers, ram_cap))

    if env_workers and int(env_workers) > 0:
        workers = max(1, min(12, int(env_workers)))
        tier = f"{tier}+env"
    else:
        workers = auto_workers

    # Chia budget thread CPU hợp lý giữa các worker để tránh tranh chấp nhân (oversubscription)
    if workers == 1:
        onnx_threads = max(2, min(4, cpu_cores - 1)) if cpu_cores >= 4 else 2
        ffmpeg_decode_threads = 2
    else:
        if hw_norm in ("cuda", "qsv", "d3d11va"):
            # GPU gánh giải mã pixel; CPU dồn cho các phiên RapidOCR
            cpu_ocr_budget = max(workers, cpu_cores - 1)
            ffmpeg_decode_threads = 1
        else:
            # CPU vừa decode vừa OCR: chừa lại ngân sách cho FFmpeg decoder mỗi worker
            reserve_dec = max(1, workers // 2)
            cpu_ocr_budget = max(workers, cpu_cores - reserve_dec)
            ffmpeg_decode_threads = max(1, min(2, (cpu_cores - 1) // workers))
        onnx_threads = max(1, min(3, cpu_ocr_budget // workers))

    # Thiết lập dung lượng micro-chunk thích ứng theo độ dài video + hạng máy
    dur = max(0.0, float(video_duration_sec or 0.0))
    is_fast_tier = hw_norm == "cuda" or workers >= 4
    if env_chunk_sec and int(env_chunk_sec) > 0:
        chunk_target_sec = float(max(30, int(env_chunk_sec)))
        chunk_target_segs = max(40, int(round(chunk_target_sec * 0.85)))
    elif dur > 0 and dur <= 600.0:
        # Phim ngắn <= 10p: chia vừa để các luồng xong đều trong 1-2 lượt
        chunk_target_sec = max(45.0, min(90.0, round(dur / max(1.5, workers * 1.5), 1)))
        chunk_target_segs = 55 if is_fast_tier else 70
    elif dur <= 3600.0:
        chunk_target_sec = 120.0 if is_fast_tier else 150.0
        chunk_target_segs = 110 if is_fast_tier else 130
    elif dur <= 10800.0:
        chunk_target_sec = 180.0 if is_fast_tier else 210.0
        chunk_target_segs = 160 if is_fast_tier else 180
    else:
        # Phim dài nhiều giờ (3h-15h): micro-chunk ~4 phút chạy cuốn chiếu cân bằng tải
        chunk_target_sec = 240.0 if is_fast_tier else 270.0
        chunk_target_segs = 210 if is_fast_tier else 240

    chunk_min_segs = 16 if int(segment_count or 0) >= 60 else 12
    frame_queue_size = 16 if total_ram_gb <= 9.0 else 24
    v5_pool_size = min(2, workers) if (workers >= 3 and total_ram_gb >= 14.0) else 1

    return {
        "tier": tier,
        "hw_decode": hw_norm,
        "ocr_workers": workers,
        "onnx_threads": onnx_threads,
        "ffmpeg_decode_threads": ffmpeg_decode_threads,
        "chunk_target_sec": chunk_target_sec,
        "chunk_target_segs": chunk_target_segs,
        "chunk_min_segs": chunk_min_segs,
        "frame_queue_size": frame_queue_size,
        "v5_pool_size": v5_pool_size,
        "cpu_cores": cpu_cores,
        "total_ram_gb": total_ram_gb,
        "free_ram_gb": free_ram_gb,
        "gpu_name": gpu_name,
        "vram_total_mb": vram_total_mb,
        "vram_free_mb": vram_free_mb,
    }


def get_hardware_summary() -> Dict[str, Any]:
    """Tổng hợp thông tin phần cứng máy hiện tại phục vụ log chẩn đoán & script tối ưu."""
    total_ram_gb, free_ram_gb = get_system_memory_gb()
    return {
        "cpu_cores": max(1, int(os.cpu_count() or 4)),
        "total_ram_gb": total_ram_gb,
        "free_ram_gb": free_ram_gb,
        "nvidia_gpus": detect_nvidia_gpus(refresh=True),
        "ffmpeg_caps": {k: sorted(v) for k, v in get_ffmpeg_capabilities().items()},
    }
