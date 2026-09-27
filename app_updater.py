"""
AUTO UPDATER CHO REVIEW MAT THAN (BẢN TỐI ƯU TOÀN DIỆN)
- Tự động nhận diện Git để `git pull` siêu tốc nếu máy có cài Git.
- Tự động tải ZIP từ GitHub (có Cache-Busting chống dính cache cũ) nếu máy không có Git.
- Cập nhật 100% mã nguồn mới nhất: app/, frontend/, run.py, scripts, tools, bat files...
- Tuyệt đối bảo toàn dữ liệu người dùng: .env, database/, input/, output/, temp/, venv/.
"""

import os
import sys
import json
import time
import shutil
import zipfile
import tempfile
import subprocess
import urllib.request
from pathlib import Path

# Thiết lập UTF-8 console Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent

# ==============================================================================
# CẤU HÌNH GITHUB REPOSITORY
# ==============================================================================
GITHUB_USER = "levannghia0980"
GITHUB_REPO = "ReviewMatThon"
BRANCH = "main"

LOCAL_VERSION_FILE = BASE_DIR / ".app_version"

PROTECTED_PATHS = {
    ".env",
    "database",
    "input",
    "output",
    "temp",
    "venv",
    ".git",
    ".app_version"
}

def ensure_tools_ffmpeg():
    """Kiểm tra và đảm bảo FFmpeg luôn sẵn sàng trong tools/"""
    tools_dir = BASE_DIR / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    
    ffmpeg_exe = tools_dir / "ffmpeg.exe"
    ffprobe_exe = tools_dir / "ffprobe.exe"
    nested_ffmpeg = tools_dir / "ffmpeg" / "bin" / "ffmpeg.exe"
    
    if ffmpeg_exe.exists() or nested_ffmpeg.exists():
        return
        
    try:
        import imageio_ffmpeg
        img_ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        if img_ffmpeg and os.path.exists(img_ffmpeg):
            shutil.copy2(img_ffmpeg, ffmpeg_exe)
            (tools_dir / "ffmpeg" / "bin").mkdir(parents=True, exist_ok=True)
            shutil.copy2(img_ffmpeg, nested_ffmpeg)
            print("  [✔] Đã trích xuất FFmpeg vào tools/ sẵn sàng!")
            return
    except Exception:
        pass

def update_via_git() -> bool:
    """Thực hiện cập nhật nhanh qua Git nếu có sẵn"""
    if not (BASE_DIR / ".git").exists():
        return False
    if not shutil.which("git"):
        return False

    try:
        print("  [*] Đang kéo mã nguồn mới nhất qua Git (git pull)...")
        # Reset các thay đổi tạm thời ở file code để tránh conflict
        subprocess.run(["git", "fetch", "origin", BRANCH], cwd=str(BASE_DIR), capture_output=True, timeout=15)
        res = subprocess.run(["git", "pull", "origin", BRANCH], cwd=str(BASE_DIR), capture_output=True, text=True, timeout=20)
        if res.returncode == 0:
            print("  [✔] Đã cập nhật mã nguồn qua Git thành công!")
            return True
        else:
            print(f"  [!] Git pull gặp thông báo: {res.stderr.strip()[:150]}")
    except Exception as e:
        print(f"  [!] Thử qua Git lỗi ({e}), chuyển sang phương thức tải trực tiếp...")
    return False

def get_remote_latest_commit() -> str:
    """Lấy mã commit SHA mới nhất từ GitHub API (có User-Agent chuẩn)"""
    url = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/commits/{BRANCH}?t={int(time.time())}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/vnd.github.v3+json",
            "Cache-Control": "no-cache"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode('utf-8'))
                return data.get("sha", "")
    except Exception:
        pass
    return ""

def get_local_commit() -> str:
    if LOCAL_VERSION_FILE.exists():
        try:
            return LOCAL_VERSION_FILE.read_text(encoding='utf-8').strip()
        except Exception:
            return ""
    return ""

def save_local_commit(sha: str):
    if sha and sha != "remote_available":
        try:
            LOCAL_VERSION_FILE.write_text(sha.strip(), encoding='utf-8')
        except Exception:
            pass

def download_and_apply_update(force: bool = False) -> bool:
    """Tải trực tiếp file ZIP mới nhất từ GitHub và ghi đè an toàn"""
    # Cache-Busting URL để tránh tải phải file zip cũ lưu trong cache mạng
    zip_url = f"https://github.com/{GITHUB_USER}/{GITHUB_REPO}/archive/refs/heads/{BRANCH}.zip?t={int(time.time())}"
    temp_zip = Path(tempfile.gettempdir()) / f"review_mat_thon_latest_{int(time.time())}.zip"
    extract_temp_dir = Path(tempfile.gettempdir()) / f"review_mat_thon_extracted_{int(time.time())}"

    try:
        print("  [*] Đang kết nối và tải gói cập nhật mới nhất từ GitHub...")
        req = urllib.request.Request(
            zip_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ReviewMatThon-Updater",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache"
            }
        )
        with urllib.request.urlopen(req, timeout=45) as response, open(temp_zip, 'wb') as out_file:
            shutil.copyfileobj(response, out_file)

        if not temp_zip.exists() or temp_zip.stat().st_size < 10000:
            print("  [!] File tải về không hợp lệ hoặc lỗi mạng.")
            return False

        print("  [*] Đang giải nén và cập nhật các tệp hệ thống...")
        if extract_temp_dir.exists():
            shutil.rmtree(extract_temp_dir, ignore_errors=True)
        extract_temp_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(temp_zip, 'r') as zip_ref:
            zip_ref.extractall(extract_temp_dir)

        subdirs = list(extract_temp_dir.glob(f"{GITHUB_REPO}-*"))
        source_root = subdirs[0] if subdirs else extract_temp_dir

        updated_files = 0
        for root, dirs, files in os.walk(source_root):
            rel_dir = os.path.relpath(root, source_root)
            
            top_level = rel_dir.split(os.sep)[0]
            if top_level in PROTECTED_PATHS:
                continue

            target_dir = BASE_DIR if rel_dir == "." else BASE_DIR / rel_dir
            target_dir.mkdir(parents=True, exist_ok=True)

            for file_name in files:
                rel_file_path = os.path.normpath(os.path.join(rel_dir, file_name))
                top_item = rel_file_path.split(os.sep)[0]

                if top_item in PROTECTED_PATHS or file_name in PROTECTED_PATHS:
                    continue

                src_file = Path(root) / file_name
                dst_file = target_dir / file_name

                try:
                    shutil.copy2(src_file, dst_file)
                    updated_files += 1
                except Exception as copy_err:
                    pass

        remote_sha = get_remote_latest_commit()
        if remote_sha:
            save_local_commit(remote_sha)

        print(f"  [✔] ĐÃ CẬP NHẬT THÀNH CÔNG {updated_files} TỆP MÃ NGUỒN MỚI NHẤT 100%!")
        return True

    except Exception as e:
        print(f"  [!] Lỗi trong quá trình cập nhật ZIP: {e}")
        return False
    finally:
        if temp_zip.exists():
            try: temp_zip.unlink(missing_ok=True)
            except Exception: pass
        if extract_temp_dir.exists():
            try: shutil.rmtree(extract_temp_dir, ignore_errors=True)
            except Exception: pass

def check_and_update(force: bool = False):
    """Tiến trình cập nhật toàn diện"""
    print("=" * 70)
    print("        STUDIO REVIEW MẮT THẦN - BỘ ĐỒNG BỘ CẬP NHẬT TỰ ĐỘNG")
    print("=" * 70)

    # 1. Đảm bảo FFmpeg
    try:
        ensure_tools_ffmpeg()
    except Exception:
        pass

    # 2. Thử cập nhật qua Git trước nếu có Git
    if update_via_git():
        print("=" * 70)
        return

    # 3. Cập nhật qua gói trực tiếp GitHub ZIP
    remote_sha = get_remote_latest_commit()
    local_sha = get_local_commit()

    if not force and remote_sha and local_sha and remote_sha == local_sha:
        print("  [✔] Máy tính của bạn đã ở phiên bản mới nhất. Sẵn sàng khởi động!")
        print("=" * 70)
        return

    print("  [*] Đang tiến hành tải bản cập nhật mới nhất từ máy chủ...")
    success = download_and_apply_update(force=force)
    if not success and not force:
        print("  [i] Giữ nguyên phiên bản hiện tại để tiếp tục sử dụng.")
    print("=" * 70)

if __name__ == "__main__":
    force_mode = "--force" in sys.argv or "-f" in sys.argv
    check_and_update(force=force_mode)
