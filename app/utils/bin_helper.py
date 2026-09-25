import os
import sys
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent

def _find_winget_package_binary(binary_name: str) -> Optional[str]:
    """Tìm binary trong thư mục Microsoft WinGet Packages nếu có"""
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if not local_app_data:
        return None
    
    winget_dir = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
    if not winget_dir.exists():
        return None
        
    try:
        matches = list(winget_dir.rglob(binary_name))
        if matches:
            for m in matches:
                if m.is_file() and os.access(str(m), os.X_OK):
                    return str(m)
    except Exception:
        pass
    return None

def setup_system_path():
    """Tự động thêm tất cả các thư mục chứa công cụ nhị phân vào os.environ['PATH']"""
    candidate_dirs = [
        BASE_DIR / "tools",
        BASE_DIR / "tools" / "ffmpeg",
        BASE_DIR / "tools" / "ffmpeg" / "bin",
        BASE_DIR / "tools" / "aria2",
        BASE_DIR / "tools" / "bin",
        BASE_DIR / "venv" / "Scripts",
        Path(sys.executable).parent,
        Path(sys.executable).parent / "Scripts"
    ]
    
    # 1. Thử lấy FFmpeg từ thư viện Python imageio-ffmpeg nếu có
    try:
        import imageio_ffmpeg
        img_ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        if img_ffmpeg and os.path.exists(img_ffmpeg):
            candidate_dirs.append(Path(img_ffmpeg).parent)
            
            # Tự động copy vào tools/ffmpeg.exe nếu tools chưa có
            tools_ffmpeg = BASE_DIR / "tools" / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
            if not tools_ffmpeg.exists():
                try:
                    (BASE_DIR / "tools").mkdir(parents=True, exist_ok=True)
                    shutil.copy2(img_ffmpeg, tools_ffmpeg)
                except Exception:
                    pass
                    
            # Cấu hình trực tiếp cho pydub nếu có
            try:
                from pydub import AudioSegment
                AudioSegment.converter = img_ffmpeg
            except Exception:
                pass
    except Exception:
        pass

    # Thêm winget package dirs nếu có
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        winget_dir = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
        if winget_dir.exists():
            try:
                for sub in winget_dir.iterdir():
                    if sub.is_dir():
                        candidate_dirs.append(sub)
            except Exception:
                pass

    current_path = os.environ.get("PATH", "")
    paths = current_path.split(os.pathsep) if current_path else []
    
    for c in candidate_dirs:
        if c.exists() and str(c) not in paths:
            paths.insert(0, str(c))
            
    os.environ["PATH"] = os.pathsep.join(paths)

# Chạy cấu hình PATH ngay khi load module
setup_system_path()

def get_ffmpeg_cmd() -> List[str]:
    """Trả về lệnh gọi FFmpeg chắc chắn hoạt động"""
    setup_system_path()
    bin_name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    
    # 1. Kiểm tra tools folder trong project
    for cand in [
        BASE_DIR / "tools" / bin_name,
        BASE_DIR / "tools" / "ffmpeg" / bin_name,
        BASE_DIR / "tools" / "ffmpeg" / "bin" / bin_name,
        BASE_DIR / "tools" / "bin" / bin_name
    ]:
        if cand.exists():
            return [str(cand)]
            
    # 2. Kiểm tra imageio-ffmpeg
    try:
        import imageio_ffmpeg
        img_ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        if img_ffmpeg and os.path.exists(img_ffmpeg):
            return [img_ffmpeg]
    except Exception:
        pass

    # 3. Kiểm tra shutil.which (PATH)
    found = shutil.which("ffmpeg")
    if found:
        return [found]
        
    # 3. Kiểm tra WinGet packages
    winget_bin = _find_winget_package_binary(bin_name)
    if winget_bin:
        return [winget_bin]
        
    # Fallback mặc định
    return ["ffmpeg"]

def get_ffprobe_cmd() -> List[str]:
    """Trả về lệnh gọi FFprobe chắc chắn hoạt động"""
    setup_system_path()
    bin_name = "ffprobe.exe" if sys.platform == "win32" else "ffprobe"
    
    for cand in [
        BASE_DIR / "tools" / bin_name,
        BASE_DIR / "tools" / "ffmpeg" / bin_name,
        BASE_DIR / "tools" / "ffmpeg" / "bin" / bin_name,
        BASE_DIR / "tools" / "bin" / bin_name
    ]:
        if cand.exists():
            return [str(cand)]
            
    found = shutil.which("ffprobe")
    if found:
        return [found]
        
    winget_bin = _find_winget_package_binary(bin_name)
    if winget_bin:
        return [winget_bin]
        
    return ["ffprobe"]

def get_ytdlp_cmd() -> List[str]:
    """Trả về lệnh gọi yt-dlp chắc chắn hoạt động (kể cả qua module python)"""
    setup_system_path()
    bin_name = "yt-dlp.exe" if sys.platform == "win32" else "yt-dlp"
    
    # 1. Kiểm tra trong tools/
    for cand in [
        BASE_DIR / "tools" / bin_name,
        BASE_DIR / "tools" / "bin" / bin_name,
        BASE_DIR / "venv" / "Scripts" / bin_name,
        Path(sys.executable).parent / bin_name,
        Path(sys.executable).parent / "Scripts" / bin_name
    ]:
        if cand.exists():
            return [str(cand)]
            
    # 2. Kiểm tra PATH
    found = shutil.which("yt-dlp")
    if found:
        return [found]
        
    # 3. Kiểm tra WinGet packages
    winget_bin = _find_winget_package_binary(bin_name)
    if winget_bin:
        return [winget_bin]
        
    # 4. Fallback qua Python module (cực kỳ an toàn nếu yt-dlp nằm trong venv/pip)
    return [sys.executable, "-m", "yt_dlp"]

def get_aria2c_cmd() -> Optional[List[str]]:
    """Trả về lệnh gọi aria2c nếu có, hoặc None nếu chưa cài"""
    setup_system_path()
    bin_name = "aria2c.exe" if sys.platform == "win32" else "aria2c"
    
    for cand in [
        BASE_DIR / "tools" / bin_name,
        BASE_DIR / "tools" / "aria2" / bin_name,
        BASE_DIR / "tools" / "bin" / bin_name
    ]:
        if cand.exists():
            return [str(cand)]
            
    found = shutil.which("aria2c")
    if found:
        return [found]
        
    winget_bin = _find_winget_package_binary(bin_name)
    if winget_bin:
        return [winget_bin]
        
    return None
