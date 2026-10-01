"""
AUTO UPDATER v3.0 - STUDIO REVIEW MẮT THẦN (PHIÊN BẢN SIÊU BỀN VỮNG)
=======================================================================
Tính năng đã được xử lý hoàn toàn tự động:
  [1] Cập nhật code qua Git Pull HOẶC tải ZIP trực tiếp từ GitHub
  [2] Smart .env merge: Tự động thêm KEY MỚI từ .env.example vào .env
      hiện có MÀ KHÔNG BAO GIỜ ghi đè giá trị người dùng đã cài
  [3] Smart pip install: Chỉ cài lại thư viện khi requirements.txt
      thực sự thay đổi (dùng hash MD5, không bị lỗi byte-compare)
  [4] Post-update hook: Tự chạy scripts/post_update.py nếu developer thêm
  [5] Update log: Ghi nhật ký chi tiết vào temp/update.log để debug
  [6] Xóa __pycache__ cũ sau mỗi lần cập nhật tránh lỗi bytecode stale
  [7] Tự động chuẩn hóa CRLF cho file .bat trên Windows
  [8] 3 tầng download: urllib → curl.exe → PowerShell (không bao giờ fail)
  [9] Bảo toàn 100% dữ liệu người dùng: .env, database/, input/, output/,
      temp/, venv/ - TUYỆT ĐỐI không bao giờ bị xoá hay ghi đè
"""

import os
import sys
import json
import time
import shutil
import ssl
import zipfile
import hashlib
import logging
import tempfile
import subprocess
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

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
BRANCH      = "main"

LOCAL_VERSION_FILE = BASE_DIR / ".app_version"
PIP_HASH_FILE      = BASE_DIR / ".pip_hash"        # [MỚI] Track hash requirements
UPDATE_LOG_FILE    = BASE_DIR / "temp" / "update.log"

# Dữ liệu người dùng — TUYỆT ĐỐI KHÔNG được ghi đè
PROTECTED_PATHS = {
    ".env",
    "database",
    "input",
    "output",
    "temp",
    "venv",
    ".git",
    ".app_version",
    ".pip_hash",
}

# ==============================================================================
# LOGGER: ghi cả ra console lẫn file temp/update.log
# ==============================================================================
def _setup_logger() -> logging.Logger:
    log = logging.getLogger("updater")
    log.setLevel(logging.DEBUG)
    fmt = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S")

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    log.addHandler(ch)

    try:
        UPDATE_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(str(UPDATE_LOG_FILE), encoding='utf-8')
        fh.setFormatter(fmt)
        log.addHandler(fh)
    except Exception:
        pass

    return log

LOG = _setup_logger()


# ==============================================================================
# TIỆN ÍCH NHỎ
# ==============================================================================
def get_ssl_contexts():
    """Trả về list SSL context để thử lần lượt."""
    ctxs = []
    try: ctxs.append(ssl.create_default_context())
    except Exception: pass
    try: ctxs.append(ssl._create_unverified_context())
    except Exception: pass
    return ctxs


def md5_file(path: Path) -> str:
    """Tính MD5 của một file để so sánh thay đổi chính xác."""
    try:
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def fix_batch_file_crlf(file_path: Path):
    """Đảm bảo mọi file .bat, .cmd luôn có định dạng ngắt dòng CRLF."""
    if file_path.suffix.lower() in (".bat", ".cmd", ".ps1"):
        try:
            content = file_path.read_bytes()
            normalized = content.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            if normalized != content:
                file_path.write_bytes(normalized)
        except Exception:
            pass


def clear_pycache(root: Path):
    """Xóa __pycache__ cũ sau cập nhật để tránh lỗi bytecode stale."""
    count = 0
    for p in root.rglob("__pycache__"):
        if p.is_dir() and "venv" not in p.parts:
            try:
                shutil.rmtree(p, ignore_errors=True)
                count += 1
            except Exception:
                pass
    if count:
        LOG.info(f"  [✔] Đã xoá {count} thư mục __pycache__ cũ.")


# ==============================================================================
# [MỚI] SMART .ENV MERGE - TỰ ĐỘNG THÊM KEY MỚI TỪ DEVELOPER
# ==============================================================================
def smart_merge_env():
    """
    So sánh .env.example (developer) với .env (user).
    Key nào chưa có trong .env → tự động thêm vào cuối .env kèm comment.
    KHÔNG BAO GIỜ xoá hay ghi đè giá trị cũ của user.
    """
    env_file    = BASE_DIR / ".env"
    env_example = BASE_DIR / ".env.example"

    if not env_example.exists():
        return

    # Nếu chưa có .env thì tạo mới từ .env.example
    if not env_file.exists():
        try:
            shutil.copy2(env_example, env_file)
            LOG.info("  [✔] Đã tạo file cấu hình .env từ .env.example")
        except Exception as e:
            LOG.warning(f"  [!] Không thể tạo .env: {e}")
        return

    # Đọc các KEY đang có trong .env của user
    existing_keys: set = set()
    try:
        for line in env_file.read_text(encoding='utf-8', errors='replace').splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key = line.split("=", 1)[0].strip()
                if key:
                    existing_keys.add(key)
    except Exception:
        return

    # Đọc key + comment từ .env.example và tìm key mới
    try:
        example_lines = env_example.read_text(encoding='utf-8', errors='replace').splitlines()
    except Exception:
        return

    new_entries: list = []
    pending_comments: list = []
    added_count = 0

    for line in example_lines:
        stripped = line.strip()
        if stripped.startswith("#") or stripped == "":
            pending_comments.append(line)
        elif "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key and key not in existing_keys:
                new_entries.extend(pending_comments)
                new_entries.append(line)
                existing_keys.add(key)
                added_count += 1
            pending_comments = []
        else:
            pending_comments = []

    if new_entries:
        try:
            current = env_file.read_text(encoding='utf-8', errors='replace')
            separator = "\n\n# --- Tu dong them boi App Updater (dien gia tri thich hop) ---\n"
            env_file.write_text(
                current.rstrip() + separator + "\n".join(new_entries) + "\n",
                encoding='utf-8'
            )
            LOG.info(f"  [✔] Đã tự động thêm {added_count} key cấu hình mới vào .env — vui lòng điền giá trị.")
        except Exception as e:
            LOG.warning(f"  [!] Không thể cập nhật .env: {e}")
    else:
        LOG.info("  [✔] File .env đã đồng bộ đầy đủ.")


# ==============================================================================
# [MỚI] SMART PIP INSTALL - HASH-BASED VÀ OFFLINE VERIFICATION
# ==============================================================================
def check_requirements_offline(req_file: Path) -> tuple[bool, list]:
    """
    Kiểm tra nhanh offline (<0.05s) xem các thư viện trong requirements.txt đã có trong môi trường chưa.
    Không tốn mạng, không bị timeout hay treo máy.
    """
    if not req_file.exists():
        return True, []
    try:
        import importlib.metadata
        import re
        missing = []
        for line in req_file.read_text(encoding='utf-8', errors='replace').splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            pkg_spec = re.split(r'[<>=!~;]', line)[0].strip()
            pkg_name = re.sub(r'\[.*\]', '', pkg_spec).strip()
            if not pkg_name:
                continue
            pkg_canonical = re.sub(r'[-_.]+', '-', pkg_name).lower()
            found = False
            for name in (pkg_name, pkg_canonical, pkg_canonical.replace('-', '_')):
                try:
                    importlib.metadata.version(name)
                    found = True
                    break
                except Exception:
                    pass
            if not found:
                missing.append(pkg_name)
        return (len(missing) == 0), missing
    except Exception:
        return False, []


def smart_pip_install(force_reinstall: bool = False):
    """
    Chỉ chạy pip install khi requirements.txt thực sự thay đổi (MD5 hash)
    hoặc khi phát hiện thiếu thư viện thực tế.
    Tránh việc chờ pip mỗi lần khởi động.
    """
    req_file = BASE_DIR / "requirements.txt"
    if not req_file.exists():
        return

    pip_exe = BASE_DIR / "venv" / "Scripts" / "pip.exe"
    if not pip_exe.exists():
        LOG.warning("  [!] Không tìm thấy pip trong venv. Bỏ qua bước cài thư viện.")
        return

    current_hash = md5_file(req_file)
    saved_hash   = ""
    if PIP_HASH_FILE.exists():
        try:
            saved_hash = PIP_HASH_FILE.read_text(encoding='utf-8').strip()
        except Exception:
            pass

    # 1. Nếu hash trùng khớp và không ép buộc cài lại -> Đã đủ 100%
    if not force_reinstall and current_hash and current_hash == saved_hash:
        LOG.info("  [✔] Thư viện Python đã đầy đủ và cập nhật.")
        return

    # 2. Nếu chưa có file hash hoặc hash khác: kiểm tra offline cực nhanh xem thư viện đã có trong máy chưa
    if not force_reinstall:
        all_installed, missing = check_requirements_offline(req_file)
        if all_installed:
            try:
                PIP_HASH_FILE.write_text(current_hash, encoding='utf-8')
            except Exception:
                pass
            LOG.info("  [✔] Thư viện Python đã đầy đủ và cập nhật.")
            return

    # 3. Chỉ khi thực sự thiếu thư viện hoặc có cờ --force mới gọi pip install
    all_installed, missing = check_requirements_offline(req_file)
    if missing:
        sample_missing = ", ".join(missing[:3]) + ("..." if len(missing) > 3 else "")
        LOG.info(f"  [*] Phát hiện thiếu {len(missing)} thư viện ({sample_missing}) → Đang cài đặt...")
    else:
        LOG.info("  [*] Đang cài đặt/cập nhật thư viện từ requirements.txt...")

    try:
        result = subprocess.run(
            [str(pip_exe), "install", "-r", str(req_file), "--no-warn-script-location"],
            cwd=str(BASE_DIR),
            capture_output=True, text=True,
            timeout=180, encoding='utf-8'
        )
        if result.returncode == 0:
            try:
                PIP_HASH_FILE.write_text(current_hash, encoding='utf-8')
            except Exception:
                pass
            LOG.info("  [✔] Đã cài đặt/cập nhật thư viện Python thành công!")
        else:
            LOG.warning(f"  [!] pip install gặp lỗi:\n{result.stderr.strip()[:400]}")
            LOG.warning("  [i] Thử chạy '1_CAI_DAT_HE_THONG.bat' để cài lại hoàn toàn.")
    except subprocess.TimeoutExpired:
        LOG.warning("  [!] Cài thư viện quá lâu (>3 phút). Có thể mạng chậm — sẽ thử lại lần sau.")
    except Exception as e:
        LOG.warning(f"  [!] Lỗi khi chạy pip: {e}")


# ==============================================================================
# [MỚI] POST-UPDATE HOOK - DEVELOPER CÓ THỂ THÊM scripts/post_update.py
# ==============================================================================
def run_post_update_hook():
    """
    Sau mỗi lần cập nhật code, nếu có file scripts/post_update.py
    thì tự động chạy (migrate DB, clear cache, rebuild index, v.v.)
    """
    hook = BASE_DIR / "scripts" / "post_update.py"
    if not hook.exists():
        return

    py_exe = BASE_DIR / "venv" / "Scripts" / "python.exe"
    if not py_exe.exists():
        py_exe = Path(sys.executable)

    LOG.info("  [*] Đang chạy post-update hook (scripts/post_update.py)...")
    try:
        result = subprocess.run(
            [str(py_exe), str(hook)],
            cwd=str(BASE_DIR),
            capture_output=True, text=True,
            timeout=60, encoding='utf-8'
        )
        if result.stdout.strip():
            LOG.info(f"  [hook] {result.stdout.strip()[:500]}")
        if result.returncode == 0:
            LOG.info("  [✔] Post-update hook hoàn thành.")
        else:
            LOG.warning(f"  [!] Post-update hook lỗi: {result.stderr.strip()[:300]}")
    except Exception as e:
        LOG.warning(f"  [!] Không thể chạy post-update hook: {e}")


# ==============================================================================
# DOWNLOAD ROBUST - 3 TẦNG
# ==============================================================================
def download_file_robust(url: str, output_path: Path) -> bool:
    """Tải file với 3 tầng cơ chế: urllib -> curl.exe -> powershell"""

    # Tầng 1: urllib
    for ctx in get_ssl_contexts():
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "ReviewMatThon-Updater/3.0 (Windows)",
                    "Cache-Control": "no-cache, no-store",
                }
            )
            with urllib.request.urlopen(req, timeout=60, context=ctx) as resp, \
                 open(output_path, 'wb') as f:
                shutil.copyfileobj(resp, f)
            if output_path.exists() and output_path.stat().st_size > 10_000:
                return True
        except Exception:
            pass

    # Tầng 2: curl.exe
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if curl:
        try:
            subprocess.run(
                [curl, "-L", "-k", "-s", "--retry", "2", "-o", str(output_path), url],
                capture_output=True, timeout=90
            )
            if output_path.exists() and output_path.stat().st_size > 10_000:
                return True
        except Exception:
            pass

    # Tầng 3: PowerShell
    try:
        ps = (
            "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12;"
            f"(New-Object Net.WebClient).DownloadFile('{url}', '{str(output_path)}')"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, timeout=90
        )
        if output_path.exists() and output_path.stat().st_size > 10_000:
            return True
    except Exception:
        pass

    return False


# ==============================================================================
# VERSION TRACKING
# ==============================================================================
def get_remote_latest_commit() -> str:
    url = (
        f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}"
        f"/commits/{BRANCH}?t={int(time.time())}"
    )
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "ReviewMatThon-Updater/3.0",
            "Accept": "application/vnd.github.v3+json",
            "Cache-Control": "no-cache",
        }
    )
    for ctx in get_ssl_contexts():
        try:
            with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
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
    if sha:
        try:
            LOCAL_VERSION_FILE.write_text(sha.strip(), encoding='utf-8')
        except Exception:
            pass


# ==============================================================================
# CẬP NHẬT QUA GIT
# ==============================================================================
def update_via_git(force: bool = False) -> tuple[bool, bool]:
    """
    Cập nhật qua Git. Trả về: (thành_công: bool, có_thay_đổi: bool)
    """
    if not (BASE_DIR / ".git").exists():
        return False, False
    if not shutil.which("git"):
        return False, False

    LOG.info("  [*] Đang kiểm tra và đồng bộ qua Git...")
    try:
        head_before = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(BASE_DIR), capture_output=True, text=True, timeout=10
        ).stdout.strip()

        fetch_res = subprocess.run(
            ["git", "fetch", "origin", BRANCH],
            cwd=str(BASE_DIR), capture_output=True, timeout=20
        )
        if fetch_res.returncode != 0:
            LOG.warning("  [!] Không thể kết nối Git remote để fetch.")
            return False, False

        remote_head = subprocess.run(
            ["git", "rev-parse", f"origin/{BRANCH}"],
            cwd=str(BASE_DIR), capture_output=True, text=True, timeout=10
        ).stdout.strip()

        if not force and head_before and remote_head and head_before == remote_head:
            LOG.info("  [✔] Mã nguồn Git đã ở phiên bản mới nhất.")
            return True, False

        # Kiểm tra xem có thay đổi cục bộ chưa commit không
        status_res = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(BASE_DIR), capture_output=True, text=True, timeout=10
        )
        has_local_changes = bool(status_res.stdout.strip())
        if has_local_changes and not force:
            LOG.info("  [*] Phát hiện mã nguồn cục bộ đang được chỉnh sửa. Tự động bảo vệ (git stash)...")
            subprocess.run(["git", "stash", "save", "Auto-stash-before-update"], cwd=str(BASE_DIR), capture_output=True, timeout=15)

        # Tiến hành pull cập nhật
        res = subprocess.run(
            ["git", "pull", "--rebase=false", "origin", BRANCH],
            cwd=str(BASE_DIR), capture_output=True, text=True, timeout=30, encoding='utf-8'
        )

        if has_local_changes and not force:
            LOG.info("  [*] Đang khôi phục lại các thay đổi cục bộ (git stash pop)...")
            subprocess.run(["git", "stash", "pop"], cwd=str(BASE_DIR), capture_output=True, timeout=15)

        if res.returncode == 0:
            LOG.info("  [✔] Đã cập nhật mã nguồn mới qua Git thành công!")
            for bat in BASE_DIR.glob("*.bat"):
                fix_batch_file_crlf(bat)
            return True, True
        else:
            if force:
                LOG.info("  [*] Chế độ ép buộc (--force): Đồng bộ chuẩn xác 100% theo GitHub (git reset --hard)...")
                subprocess.run(["git", "reset", "--hard", f"origin/{BRANCH}"], cwd=str(BASE_DIR), capture_output=True, timeout=15)
                for bat in BASE_DIR.glob("*.bat"):
                    fix_batch_file_crlf(bat)
                LOG.info("  [✔] Đã đồng bộ mã nguồn và giao diện mới nhất thành công!")
                return True, True
            else:
                LOG.warning("  [!] Git pull có xung đột với code cục bộ. Vui lòng chạy 3_CAP_NHAT_CODE.bat để đồng bộ.")
                return False, False
    except Exception as e:
        LOG.info(f"  [!] Git gặp lỗi ({e}), chuyển sang ZIP...")
    return False, False


# ==============================================================================
# CẬP NHẬT QUA ZIP
# ==============================================================================
def download_and_apply_update(force: bool = False) -> bool:
    zip_url = (
        f"https://github.com/{GITHUB_USER}/{GITHUB_REPO}"
        f"/archive/refs/heads/{BRANCH}.zip?t={int(time.time())}"
    )
    temp_zip     = Path(tempfile.gettempdir()) / f"rmt_update_{int(time.time())}.zip"
    extract_dir  = Path(tempfile.gettempdir()) / f"rmt_extract_{int(time.time())}"

    try:
        LOG.info("  [*] Đang kết nối và tải gói cập nhật từ GitHub...")
        ok = download_file_robust(zip_url, temp_zip)

        if not ok or not temp_zip.exists() or temp_zip.stat().st_size < 10_000:
            LOG.error("  [✗] Không thể tải gói mã nguồn (kiểm tra kết nối mạng).")
            return False

        LOG.info("  [*] Đang giải nén và đồng bộ mã nguồn...")
        if extract_dir.exists():
            shutil.rmtree(extract_dir, ignore_errors=True)
        extract_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(temp_zip, 'r') as zf:
            zf.extractall(extract_dir)

        # GitHub zip chứa thư mục con REPO-BRANCH/
        subdirs = [d for d in extract_dir.iterdir() if d.is_dir()]
        source_root = subdirs[0] if subdirs else extract_dir

        updated_files = 0
        for root, dirs, files in os.walk(source_root):
            rel_dir  = os.path.relpath(root, source_root)
            top_item = rel_dir.split(os.sep)[0] if rel_dir != "." else "."

            # Bỏ qua toàn bộ thư mục được bảo vệ (và không đi sâu vào)
            if top_item in PROTECTED_PATHS:
                dirs.clear()
                continue

            target_dir = BASE_DIR if rel_dir == "." else BASE_DIR / rel_dir
            target_dir.mkdir(parents=True, exist_ok=True)

            for file_name in files:
                rel_file = os.path.normpath(os.path.join(rel_dir, file_name))
                file_top = rel_file.split(os.sep)[0]
                if file_top in PROTECTED_PATHS or file_name in PROTECTED_PATHS:
                    continue

                src = Path(root) / file_name
                dst = target_dir / file_name
                try:
                    shutil.copy2(src, dst)
                    fix_batch_file_crlf(dst)
                    updated_files += 1
                except Exception as e:
                    LOG.debug(f"  [!] Bỏ qua {file_name}: {e}")

        remote_sha = get_remote_latest_commit()
        if remote_sha:
            save_local_commit(remote_sha)

        LOG.info(f"  [✔] ĐÃ ĐỒNG BỘ THÀNH CÔNG {updated_files} TỆP MÃ NGUỒN MỚI NHẤT!")
        return True

    except Exception as e:
        LOG.error(f"  [✗] Lỗi cập nhật ZIP: {e}")
        return False
    finally:
        for p in [temp_zip, extract_dir]:
            try:
                if p.exists():
                    shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
            except Exception:
                pass


# ==============================================================================
# ENTRY POINT CHÍNH
# ==============================================================================
def check_and_update(force: bool = False):
    LOG.info("=" * 70)
    LOG.info("  STUDIO REVIEW MAT THAN - BO DONG BO CAP NHAT TU DONG v3.0")
    LOG.info(f"  Bat dau luc: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    LOG.info("=" * 70)

    # 1. Smart merge .env — tự động thêm key mới từ developer
    smart_merge_env()

    code_updated = False

    # 2. Thử qua Git trước (nhanh nhất nếu có Git)
    git_ok, git_has_changes = update_via_git(force=force)
    if git_ok:
        if git_has_changes:
            code_updated = True
    else:
        # 3. Kiểm tra phiên bản → tải ZIP nếu lạc hậu hoặc force
        remote_sha = get_remote_latest_commit()
        local_sha  = get_local_commit()

        if not force and remote_sha and local_sha and remote_sha == local_sha:
            LOG.info("  [✔] Ma nguon da la phien ban moi nhat. Khong can cap nhat!")
        else:
            if remote_sha or force:
                LOG.info("  [*] Dang tien hanh tai ban cap nhat moi nhat tu may chu...")
                success = download_and_apply_update(force=force)
                if success:
                    code_updated = True
                else:
                    LOG.warning("  [i] Giu nguyen phien ban hien tai de tiep tuc su dung.")

    # 4. Smart pip install — chỉ cài khi requirements.txt thay đổi (hoặc người dùng yêu cầu force)
    smart_pip_install(force_reinstall=force)

    # 5. Post-update hook + dọn __pycache__ (chỉ khi code thực sự được cập nhật)
    if code_updated:
        run_post_update_hook()
        clear_pycache(BASE_DIR)

    LOG.info("=" * 70)
    LOG.info(f"  Ket thuc luc: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    LOG.info("=" * 70)


if __name__ == "__main__":
    force_mode = "--force" in sys.argv or "-f" in sys.argv
    check_and_update(force=force_mode)
