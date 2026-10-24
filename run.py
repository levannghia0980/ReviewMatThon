import os
import sys
import time
import socket
import webbrowser
import threading
from pathlib import Path

def setup_windows_console():
    """Cau hinh stdout/stderr an toan cho Windows Console."""
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

setup_windows_console()

BASE_DIR = Path(__file__).resolve().parent

def cleanup_zombie_browsers():
    """Diệt sạch file lock cũ của profile nếu có."""
    try:
        profile_dir = BASE_DIR / "database" / "gemini_profile"
        for lock_name in ["SingletonLock", "SingletonCookie", "SingletonSocket"]:
            l_path = profile_dir / lock_name
            if l_path.exists():
                try:
                    l_path.unlink(missing_ok=True)
                except Exception:
                    pass
    except Exception:
        pass

# 0. Dọn sạch file khóa cũ nếu có trước khi khởi động
cleanup_zombie_browsers()

# Nạp hệ thống PATH và các công cụ nhúng
try:
    from app.utils.bin_helper import setup_system_path
    setup_system_path()
except Exception:
    pass

def find_free_port(preferred_port: int = 8686, max_tries: int = 50) -> int:
    """Tìm cổng mạng (port) còn trống tự động để không bị xung đột với ứng dụng khác"""
    for p in range(preferred_port, preferred_port + max_tries):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('127.0.0.1', p))
                return p
        except OSError:
            continue
    return preferred_port

def check_environment():
    """Kiem tra moi truong va cac thu muc can thiet"""
    print("=" * 72)
    print("  [*] MAT THON REVIEW - STUDIO DICH THUAT & THUYET MINH VIDEO AI")
    print("      Khoi chay he thong tich hop Backend FastAPI + React Studio UI")
    print("=" * 72)
    
    # 1. Dam bao cac thu muc ton tai
    for folder in ["input/videos", "input/audio_raw", "output/transcripts", "output/translations", "output/voiceover", "output/final_videos", "database"]:
        (BASE_DIR / folder).mkdir(parents=True, exist_ok=True)
    print("[OK] Cau truc thu muc input/output/database: San sang")

    # 2. Kiem tra ban build cua React Frontend
    dist_index = BASE_DIR / "frontend" / "dist" / "index.html"
    if not dist_index.exists():
        print("[!] Dang build React Frontend lan dau...")
        os.system(f'cd /d "{BASE_DIR / "frontend"}" && npm run build')
    print("[OK] React Studio UI (Production Build): San sang")

    # 3. Thông tin chế độ dịch thuật (Tôn trọng 100% cấu hình người dùng từ .env)
    try:
        from app.config import settings
        current_provider = getattr(settings, "TRANSLATION_PROVIDER", "gemini")
        if current_provider == "gemini_web":
            web_thinking = getattr(settings, "GEMINI_WEB_THINKING", "medium")
            print(f"[OK] Che do dich: Google Gemini Web (Chromium On-Demand • Tu duy: {web_thinking.upper()})")
        else:
            api_model = getattr(settings, "GEMINI_MODEL", "gemini-3.1-flash-lite")
            print(f"[OK] Che do dich: Google Gemini API Key ({api_model})")
    except Exception:
        pass

def open_browser_when_ready(url: str, port: int, max_wait: float = 10.0):
    """Tự động mở trình duyệt Studio ngay khi cổng server sẵn sàng."""
    def _wait_and_open():
        start_time = time.time()
        while time.time() - start_time < max_wait:
            time.sleep(0.15)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.2)
                if s.connect_ex(('127.0.0.1', port)) == 0:
                    target_url = f"{url}?v={int(time.time())}"
                    print(f"\n[>>] Server da khoi dong thanh cong tai: {target_url}")
                    try:
                        webbrowser.open(target_url)
                    except Exception:
                        pass
                    return
        try:
            webbrowser.open(f"{url}?v={int(time.time())}")
        except Exception:
            pass
    threading.Thread(target=_wait_and_open, daemon=True).start()

def main():
    import atexit
    import signal

    # 2. Đăng ký dọn rác khi tiến trình thoát
    def _on_exit():
        try:
            from app.services.step_03_translation.gemini_web_service import GeminiWebService
            if getattr(GeminiWebService, "_warm_page", None) is not None or getattr(GeminiWebService, "_context", None) is not None:
                import asyncio
                asyncio.run(GeminiWebService.shutdown_worker())
        except Exception:
            pass

    atexit.register(_on_exit)

    def _fast_exit():
        try:
            _on_exit()
        finally:
            os._exit(0)

    signal.signal(signal.SIGINT, lambda sig, frame: _fast_exit())
    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, lambda sig, frame: _fast_exit())

    # Bộ bắt sự kiện bấm dấu [X] tắt nhanh Console trên Windows (thoát tức thì 0.01s, không bao giờ bị Not Responding)
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            PHANDLER_ROUTINE = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
            def _console_ctrl_handler(ctrl_type):
                os._exit(0)
                return True
            _handler_func = PHANDLER_ROUTINE(_console_ctrl_handler)
            ctypes.windll.kernel32.SetConsoleCtrlHandler(_handler_func, True)
        except Exception:
            pass

    # 3. Khởi tạo môi trường và phiên Gemini Web
    check_environment()
    
    # Tự động tìm port riêng biệt không bị chiếm (Mặc định bắt đầu từ 8686)
    port = find_free_port(preferred_port=8686)
    app_url = f"http://127.0.0.1:{port}"
    
    open_browser_when_ready(app_url, port=port)
    
    print("\n" + "-" * 72)
    print(f"  * Giao Dien Studio React:   {app_url}")
    print(f"  * Tai Lieu API Swagger:     {app_url}/docs")
    print(f"  * Cong Port Rieng Biet:     {port} (Da tranh xung dot cong 8000)")
    print("  * Nhan Ctrl + C de dung chuong trinh.")
    print("-" * 72 + "\n")

    try:
        import uvicorn
        import logging

        class CleanConsoleFilter(logging.Filter):
            """Lọc bỏ các request polling liên tục để console luôn hiển thị rõ log nghiệp vụ."""
            def filter(self, record: logging.LogRecord) -> bool:
                msg = record.getMessage()
                if any(k in msg for k in ["/api/v1/pipeline/status/", "/assets/", "/favicon.ico", "/api/v1/settings", "/api/health"]):
                    return False
                return True

        logging.getLogger("uvicorn.access").addFilter(CleanConsoleFilter())
    except ImportError:
        print("[!] Đang cài đặt thư viện uvicorn còn thiếu...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "uvicorn[standard]"])
        import uvicorn

    try:
        uvicorn.run(
            "app.main:app",
            host="127.0.0.1",
            port=port,
            reload=False,
            log_level="info",
            access_log=False,
            timeout_graceful_shutdown=2
        )
    finally:
        _on_exit()

if __name__ == "__main__":
    main()
