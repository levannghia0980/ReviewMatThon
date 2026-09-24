import os
import sys
import time
import socket
import webbrowser
import threading
from pathlib import Path

# Đảm bảo UTF-8 cho console Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent

# Nạp hệ thống PATH và các công cụ nhúng
try:
    from app.utils.bin_helper import setup_system_path
    setup_system_path()
except Exception:
    pass

def find_free_port(preferred_port: int = 8686, max_tries: int = 50) -> int:
    """Tìm cổng mạng (port) còn trống tự động để không bị xung đột với ứng dụng khác"""
    for p in range(preferred_port, preferred_port + max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('127.0.0.1', p)) != 0:
                return p
    return preferred_port

def check_environment():
    """Kiểm tra môi trường và các thư mục cần thiết"""
    print("=" * 70)
    print("  ⚡ REVIEW MẮT THẦN - STUDIO DỊCH THUẬT & THUYẾT MINH VIDEO AI")
    print("  Khởi chạy hệ thống tích hợp Backend FastAPI + React Studio UI")
    print("=" * 70)
    
    # 1. Đảm bảo các thư mục tồn tại
    for folder in ["input/videos", "input/audio_raw", "output/transcripts", "output/translations", "output/voiceover", "output/final_videos", "database"]:
        (BASE_DIR / folder).mkdir(parents=True, exist_ok=True)
    print("[✔] Cấu trúc thư mục input/output/database: Sẵn sàng")

    # 2. Kiểm tra bản build của React Frontend
    dist_index = BASE_DIR / "frontend" / "dist" / "index.html"
    if not dist_index.exists():
        print("[!] Đang build React Frontend lần đầu...")
        os.system(f'cd /d "{BASE_DIR / "frontend"}" && npm run build')
    print("[✔] React Studio UI (Production Build): Sẵn sàng")

def open_browser_when_ready(url: str, port: int, max_wait: float = 10.0):
    """Mở trình duyệt ngay lập tức khi cổng server vừa mở, không gây lỗi Connection Refused"""
    def _wait_and_open():
        start_time = time.time()
        while time.time() - start_time < max_wait:
            time.sleep(0.15)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.2)
                if s.connect_ex(('127.0.0.1', port)) == 0:
                    print(f"\n[🌐] Server đã khởi động thành công! Đang mở trình duyệt tại: {url}")
                    webbrowser.open(url)
                    return
        # Fallback nếu quá thời gian
        webbrowser.open(url)
    threading.Thread(target=_wait_and_open, daemon=True).start()

def main():
    check_environment()
    
    # Tự động tìm port riêng biệt không bị chiếm (Mặc định bắt đầu từ 8686)
    port = find_free_port(preferred_port=8686)
    app_url = f"http://127.0.0.1:{port}"
    
    open_browser_when_ready(app_url, port=port)
    
    print("\n" + "-" * 70)
    print(f"  👉 Giao Diện Studio React:  {app_url}")
    print(f"  👉 Tài Liệu API Swagger:   {app_url}/docs")
    print(f"  👉 Cổng Port Riêng Biệt:    {port} (Đã tránh xung đột cổng 8000)")
    print("  Nhấn Ctrl + C để dừng chương trình.")
    print("-" * 70 + "\n")

    try:
        import uvicorn
    except ImportError:
        print("[!] Đang cài đặt thư viện uvicorn còn thiếu...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "uvicorn[standard]"])
        import uvicorn

    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=port,
        reload=False,
        log_level="info"
    )

if __name__ == "__main__":
    main()
