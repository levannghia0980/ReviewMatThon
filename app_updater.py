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
    ".app_version",
    "1_CAI_DAT_HE_THONG.bat",
    "2_KHOI_DONG.bat",
    "TAO_SHORTCUT_DESKTOP.bat"
}

def get_remote_latest_commit() -> str:
    """Lay ma SHA cua commit moi nhat tu GitHub API (Timeout 3s)"""
    url = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/commits/{BRANCH}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "ReviewMatThon-AutoUpdater",
            "Accept": "application/vnd.github.v3+json"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode('utf-8'))
                return data.get("sha", "")
    except Exception:
        pass
    return ""

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

def check_and_update():
    """Ham tong kiem tra va tu dong cap nhat - Duoc goi dau tien khi khoi dong"""
    print("-" * 65)
    print("  ⚡ [AUTO-UPDATE] Dang kiem tra ban cap nhat code tu GitHub...")
    print("-" * 65)

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
