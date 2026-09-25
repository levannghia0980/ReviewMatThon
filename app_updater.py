"""
AUTO UPDATER CHO REVIEW MAT THAN
- Thuc thi doc lap, chi dung thu vien chuan (standard library) cua Python: urllib, zipfile, json, shutil...
- Khong can cai Git, khong can pip install bat ky thu vien nao.
- Tu dong kiem tra commit moi nhat tu GitHub Repository.
- Chi cap nhat file code (app/, frontend/dist, run.py, requirements.txt...), tuyet doi khong ghi de .env, database, input/output.
- Neu mang yeu, loi mang hoac GitHub khong truy cap duoc -> Tu dong bo qua trong 1.5s va tiep tuc chay app binh thuong, khong bao gio gay dung chuong trinh.
"""

import os
import sys
import json
import shutil
import zipfile
import tempfile
import urllib.request
from pathlib import Path

# Thiet lap UTF-8 console Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent

# ==============================================================================
# CAU HINH REPOSITORY GITHUB
# (Ban co the thay doi ten User / Repo hoac Branch tai day)
# ==============================================================================
GITHUB_USER = "levannghia0980"
GITHUB_REPO = "ReviewMatThon"
BRANCH = "main"

# File luu sha commit hien tai tren may nguoi dung
LOCAL_VERSION_FILE = BASE_DIR / ".app_version"

# Danh sach cac duong dan / file TUYET DOI KHONG GHI DE de bao toan du lieu nguoi dung
PROTECTED_PATHS = {
    ".env",
    "database",
    "input",
    "output",
    "temp",
    "venv",
    ".app_version"
}

def ensure_tools_ffmpeg():
    """Tu dong kiem tra va tai FFmpeg vao tools/ neu may nguoi dung chua co"""
    tools_dir = BASE_DIR / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    
    ffmpeg_exe = tools_dir / "ffmpeg.exe"
    ffprobe_exe = tools_dir / "ffprobe.exe"
    nested_ffmpeg = tools_dir / "ffmpeg" / "bin" / "ffmpeg.exe"
    
    # 1. Kiem tra neu da co ffmpeg trong tools hoac trong he thong
    if ffmpeg_exe.exists() or nested_ffmpeg.exists():
        return
        
    # 2. Uu tien trich xuat ngay tu imageio-ffmpeg co san trong venv (toc do 0.01s, khong can mang)
    try:
        import imageio_ffmpeg
        img_ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        if img_ffmpeg and os.path.exists(img_ffmpeg):
            shutil.copy2(img_ffmpeg, ffmpeg_exe)
            (tools_dir / "ffmpeg" / "bin").mkdir(parents=True, exist_ok=True)
            shutil.copy2(img_ffmpeg, nested_ffmpeg)
            print("  [✔] Da trich xuat FFmpeg tu imageio-ffmpeg sang tools/ thanh cong!")
            return
    except Exception:
        pass
        
    if shutil.which("ffmpeg"):
        return

    urls = [
        "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
        "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
    ]
    temp_zip = Path(tempfile.gettempdir()) / "ffmpeg_dl.zip"
    unpack_dir = Path(tempfile.gettempdir()) / "ffmpeg_unpack"
    downloaded = False

    print("  [*] Dang tu dong cau hinh bo cong cu FFmpeg...")
    for u in urls:
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=40) as resp, open(temp_zip, 'wb') as out:
                shutil.copyfileobj(resp, out)
            if temp_zip.exists() and temp_zip.stat().st_size > 1000000:
                downloaded = True
                break
        except Exception:
            continue
            
    if downloaded:
        try:
            if unpack_dir.exists():
                shutil.rmtree(unpack_dir, ignore_errors=True)
            unpack_dir.mkdir(parents=True, exist_ok=True)
            
            with zipfile.ZipFile(temp_zip, 'r') as z:
                z.extractall(unpack_dir)
                
            for root, dirs, files in os.walk(unpack_dir):
                for f in files:
                    if f.lower() in ("ffmpeg.exe", "ffprobe.exe"):
                        src = Path(root) / f
                        shutil.copy2(src, tools_dir / f)
                        (tools_dir / "ffmpeg" / "bin").mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src, tools_dir / "ffmpeg" / "bin" / f)
            print("  [✔] Da tu dong cai dat FFmpeg vao tools/ thanh cong!")
        except Exception as err:
            print(f"  [!] Khong the giai nen FFmpeg: {err}")
        finally:
            if temp_zip.exists():
                try: temp_zip.unlink()
                except Exception: pass
            if unpack_dir.exists():
                try: shutil.rmtree(unpack_dir, ignore_errors=True)
                except Exception: pass

def get_remote_latest_commit() -> str:
    """Lay ma SHA cua commit moi nhat tu GitHub API (Tang timeout 15s)"""
    url = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/commits/{BRANCH}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/vnd.github.v3+json"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode('utf-8'))
                return data.get("sha", "")
    except Exception:
        pass
    
    # Fallback: Thu lay tu raw commit sha hoac tra ve "force_update"
    return "remote_available"

def get_local_commit() -> str:
    """Doc ma commit hien tai da luu tren may"""
    if LOCAL_VERSION_FILE.exists():
        try:
            return LOCAL_VERSION_FILE.read_text(encoding='utf-8').strip()
        except Exception:
            return ""
    return ""

def save_local_commit(sha: str):
    """Luu ma commit sau khi cap nhat thanh cong"""
    try:
        LOCAL_VERSION_FILE.write_text(sha.strip(), encoding='utf-8')
    except Exception:
        pass

def download_and_apply_update(latest_sha: str) -> bool:
    """Tai zip tu GitHub va ghi de thong minh cac file code moi"""
    zip_url = f"https://github.com/{GITHUB_USER}/{GITHUB_REPO}/archive/refs/heads/{BRANCH}.zip"
    temp_zip = Path(tempfile.gettempdir()) / "review_mat_thon_latest.zip"
    extract_temp_dir = Path(tempfile.gettempdir()) / "review_mat_thon_extracted"

    try:
        # 1. Tai file zip
        print("[*] Dang tai ban cap nhat tu GitHub...")
        req = urllib.request.Request(zip_url, headers={"User-Agent": "ReviewMatThon-AutoUpdater"})
        with urllib.request.urlopen(req, timeout=30) as response, open(temp_zip, 'wb') as out_file:
            shutil.copyfileobj(response, out_file)

        # 2. Giai nen vao thu muc tam
        if extract_temp_dir.exists():
            shutil.rmtree(extract_temp_dir, ignore_errors=True)
        extract_temp_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(temp_zip, 'r') as zip_ref:
            zip_ref.extractall(extract_temp_dir)

        # Tim thu muc goc ben trong zip (vi du: ReviewMatThon-main)
        subdirs = list(extract_temp_dir.glob(f"{GITHUB_REPO}-*"))
        source_root = subdirs[0] if subdirs else extract_temp_dir

        # 3. Sao chep thong minh vao BASE_DIR, giu nguyen data cua nguoi dung
        updated_count = 0
        for root, dirs, files in os.walk(source_root):
            rel_dir = os.path.relpath(root, source_root)
            
            # Bo qua thu muc bi cam
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

                # Ghi de file code
                try:
                    shutil.copy2(src_file, dst_file)
                    updated_count += 1
                except Exception:
                    pass

        # 4. Luu version moi
        save_local_commit(latest_sha)
        print(f"[OK] Da cap nhat thanh cong {updated_count} tep ma nguon moi!")
        return True

    except Exception as e:
        print(f"[!] Khong the ap dung ban cap nhat: {e}")
        return False
    finally:
        # Don dep tep tam
        if temp_zip.exists():
            try: temp_zip.unlink()
            except Exception: pass
        if extract_temp_dir.exists():
            try: shutil.rmtree(extract_temp_dir, ignore_errors=True)
            except Exception: pass

def is_server_host() -> bool:
    """Kiem tra xem may hien tai co phai la May Chu Dev (Server Host) hay khong"""
    env_file = BASE_DIR / ".env"
    if env_file.exists():
        try:
            for line in env_file.read_text(encoding='utf-8').splitlines():
                if line.strip().startswith("IS_SERVER_HOST"):
                    val = line.split("=", 1)[1].strip().lower()
                    if val in ("true", "1", "yes"):
                        return True
        except Exception:
            pass
    return (BASE_DIR / ".git").exists() and os.getenv("IS_SERVER_HOST", "").lower() in ("true", "1", "yes")

def server_auto_push():
    """May chu DEV: Tu dong build frontend va push code moi nhat len GitHub cho nguoi dung"""
    import subprocess
    print("  ⚡ [MAY CHU DEV] Phat hien day la MAY CHU PHAT TRIEN (IS_SERVER_HOST=true).")
    print("  [*] Dang tu dong kiem tra va dong bo (PUSH) code moi len GitHub...")
    try:
        # 1. Kiem tra xem co lenh git khong
        if not shutil.which("git"):
            print("  [!] Khong tim thay git tren may chu, bo qua tu dong day code.")
            return

        # 2. Build React Frontend neu co thay doi
        node_modules = BASE_DIR / "frontend" / "node_modules"
        if node_modules.exists() and shutil.which("npm"):
            print("  [*] Dang build React Frontend...")
            subprocess.run(["npm", "run", "build"], cwd=str(BASE_DIR / "frontend"), shell=True, capture_output=True)

        # 3. Kiem tra git status
        st = subprocess.run(["git", "status", "--porcelain"], cwd=str(BASE_DIR), capture_output=True, text=True)
        if st.stdout.strip():
            print("  [*] Phat hien thay doi ma nguon moi, dang commit & push...")
            subprocess.run(["git", "add", "-A"], cwd=str(BASE_DIR), capture_output=True)
            subprocess.run(["git", "commit", "-m", "Auto-update latest code from Dev Host"], cwd=str(BASE_DIR), capture_output=True)
            push_res = subprocess.run(["git", "push", "origin", "main"], cwd=str(BASE_DIR), capture_output=True, text=True)
            if push_res.returncode == 0:
                print("  [✔] DA DAY TOAN BO CODE MOI LEN GITHUB THANH CONG!")
            else:
                print(f"  [!] Push chua thanh cong: {push_res.stderr.strip()[:100]}")
        else:
            print("  [✔] Code tren May Chu va GitHub da dong bo 100%.")
    except Exception as e:
        print(f"  [!] Loi khi tu dong day code: {e}")

def check_and_update():
    """Ham tong kiem tra va dong bo - 1 chieu chuan: May chu chi DAY, May khach chi NHAN"""
    print("-" * 65)
    print("  ⚡ [DONG BO MA NGUON] Kiem tra he thong...")
    print("-" * 65)

    # 1. Kiem tra va dam bao co du FFmpeg trong tools/
    try:
        ensure_tools_ffmpeg()
    except Exception:
        pass

    # 2. Neu day la MAY CHU: TU DONG PUSH LEN GITHUB, KHONG BAO GIO KEO VE
    if is_server_host():
        server_auto_push()
        return

    # 3. DOI VOI MAY KHACH (NGUOI DUNG): 100% CHI KEO CODE MOI VE (1 CHIEU)
    latest_sha = get_remote_latest_commit()
    if not latest_sha:
        print("  [i] Khong the ket noi GitHub hoac chua cau hinh Repo. Bo qua cap nhat, tiep tuc chay app.")
        return

    local_sha = get_local_commit()

    if local_sha == latest_sha:
        print("  [✔] Code dang o phien ban moi nhat. Khoi dong Studio ngay...")
        return

    print(f"  [*] Phat hien ban code moi (Commit: {latest_sha[:7]})!")
    success = download_and_apply_update(latest_sha)
    if success:
        print("  [✔] Hoan tat dong bo code moi nhat!")
    else:
        print("  [!] Cap nhat that bai, van se tiep tuc chay phien ban hien tai.")

if __name__ == "__main__":
    check_and_update()
