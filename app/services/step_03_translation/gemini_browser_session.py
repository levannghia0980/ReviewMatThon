import os
import json
import logging
import asyncio
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from app.config import BASE_DIR

logger = logging.getLogger(__name__)

PROFILE_DIR = BASE_DIR / "database" / "gemini_profile"
SESSION_FILE = BASE_DIR / "database" / "gemini_session.json"


class GeminiBrowserSessionManager:
    """
    Quản lý phiên đăng nhập Google Gemini Web thông qua Chromium profile độc lập:
    1. open_login_window(): Mở cửa sổ trình duyệt cho người dùng đăng nhập lần đầu.
    2. get_fresh_session(): Mở ngầm (headless) 2-3s để lấy Cookie & SNlM0e token mới nhất, sau đó ĐÓNG NGAY để giải phóng RAM.
    """

    @classmethod
    def get_profile_dir(cls) -> Path:
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        return PROFILE_DIR

    @classmethod
    def is_logged_in(cls) -> bool:
        """Kiểm tra xem đã có cookie xác thực Google hợp lệ chưa (__Secure-1PSID hoặc SID)."""
        cached = cls.load_cached_cookies()
        return bool(cached.get("__Secure-1PSID") or cached.get("SID"))

    @classmethod
    def kill_orphaned_gemini_chrome(cls) -> None:
        """
        Quét và đóng triệt để mọi tiến trình Chromium hoặc Node bị kẹt
        đang chiếm giữ profile thư mục database/gemini_profile để tránh xung đột khóa tệp.
        Sử dụng psutil để quét tức thì (<0.1s) và tuyệt đối không ảnh hưởng đến Chrome cá nhân của người dùng.
        """
        profile_dir = cls.get_profile_dir()
        try:
            import psutil
            my_pid = os.getpid()
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    if proc.info['pid'] == my_pid:
                        continue
                    pname = (proc.info['name'] or '').lower()
                    if 'chrome' not in pname and 'node' not in pname:
                        continue
                    cmdline = " ".join(proc.info['cmdline'] or [])
                    # Chỉ diệt tiến trình có flag trỏ vào database/gemini_profile
                    if "gemini_profile" in cmdline:
                        proc.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception as e:
            logger.debug(f"Không thể quét tiến trình Chrome con qua psutil: {e}")

        # Xóa các file lock của Chromium
        for lock_name in ["SingletonLock", "SingletonCookie", "SingletonSocket"]:
            l_path = profile_dir / lock_name
            if l_path.exists():
                try:
                    l_path.unlink(missing_ok=True)
                except Exception:
                    pass

    @classmethod
    def _get_launch_kwargs(cls, headless: bool) -> Dict[str, Any]:
        """Khởi chạy Google Chrome chính hãng, sử dụng profile riêng biệt trong database/gemini_profile."""
        args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-session-crashed-bubble",
            "--disable-infobars"
        ]
        if not headless:
            args.append("--start-maximized")
        else:
            args.extend(["--disable-gpu", "--headless=new"])

        kwargs = {
            "user_data_dir": str(cls.get_profile_dir()),
            "channel": "chrome",
            "headless": headless,
            "args": args,
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "viewport": None if not headless else {"width": 1280, "height": 800},
            "no_viewport": not headless,
            "permissions": ["clipboard-read", "clipboard-write"],
            "ignore_default_args": ["--enable-automation"]
        }
        return kwargs

    @classmethod
    async def open_login_window(cls) -> Dict[str, Any]:
        """
        Mở cửa sổ Chromium cho người dùng đăng nhập tài khoản Google 1 lần duy nhất.
        Tự động đóng lại khi phát hiện người dùng đã đăng nhập thành công vào gemini.google.com.
        """
        from playwright.async_api import async_playwright

        profile_dir = cls.get_profile_dir()
        logger.info(f"Đang mở cửa sổ đăng nhập Gemini tại: {profile_dir}")

        # 1. Tạm giải phóng Web Worker ngầm nếu đang chạy để nhường quyền mở profile
        try:
            from app.services.step_03_translation.gemini_web_service import GeminiWebService
            await GeminiWebService.shutdown_worker()
        except Exception:
            pass

        # 2. Dọn dẹp tiến trình con và file khóa cũ để tránh lỗi crash exitCode=21
        cls.kill_orphaned_gemini_chrome()

        async with async_playwright() as p:
            launch_opts = cls._get_launch_kwargs(headless=False)
            context = await p.chromium.launch_persistent_context(**launch_opts)
            page = await context.new_page()

            # Mở thẳng trang Gemini Web App
            await page.goto("https://gemini.google.com/app", timeout=60000)

            logger.info("Vui lòng đăng nhập tài khoản Google trên cửa sổ Chrome vừa mở...")
            
            # Chờ người dùng đăng nhập xong (hoặc nếu đã có sẵn phiên trước đó thì tự nhận ngay)
            logged_in = False
            for loop_i in range(150): # Đợi tối đa 300 giây (5 phút)
                await asyncio.sleep(2)
                try:
                    cookies = await context.cookies()
                    cookie_names = [c["name"] for c in cookies]
                    # Google đăng nhập thành công sẽ có ít nhất __Secure-1PSID hoặc SID
                    if "__Secure-1PSID" in cookie_names or "SID" in cookie_names:
                        cur_url = page.url
                        if "gemini.google.com" in cur_url:
                            logged_in = True
                            # Cho thêm 2s để gemini nạp đủ cookies
                            await asyncio.sleep(2)
                            break
                    else:
                        # Nếu người dùng đã vào thẳng giao diện chat Gemini
                        cur_url = page.url
                        if "gemini.google.com/app" in cur_url and loop_i > 2:
                            if any("SID" in n for n in cookie_names):
                                logged_in = True
                                break
                except Exception:
                    # Trình duyệt bị người dùng tắt thủ công
                    break

            # Lấy toàn bộ cookies của session
            fresh_cookies = await context.cookies()
            await context.close()

            # 3. Lưu phiên đăng nhập và đóng sạch sẽ trình duyệt (TUYỆT ĐỐI KHÔNG tự ý khởi động ngầm)
            if logged_in:
                cls._save_session_cache(fresh_cookies)
                return {"success": True, "message": "Đăng nhập Google Gemini Web thành công!"}
            else:
                return {"success": False, "message": "Chưa hoàn tất đăng nhập hoặc đã quá thời gian chờ."}

    @classmethod
    async def sync_thinking_level_headless(cls, target_level: str = "high") -> Tuple[bool, str]:
        """
        Đồng bộ Cấp độ tư duy thông qua phiên làm việc Gemini duy nhất (GeminiWebService).
        Không mở thêm tiến trình riêng biệt để tránh xung đột khóa tệp và mất phiên.
        """
        if not cls.is_logged_in():
            return False, "Chưa đăng nhập Gemini Web"
        try:
            from app.services.step_03_translation.gemini_web_service import GeminiWebService
            ok = await GeminiWebService.set_thinking_level(target_level)
            if ok:
                return True, f"Đã chuyển cấp độ tư duy sang {target_level.upper()}"
            else:
                return False, f"Không thể thiết lập cấp độ tư duy {target_level.upper()}"
        except Exception as e:
            return False, str(e)

    @classmethod
    async def extract_fresh_cookies_headless(cls) -> Optional[Dict[str, str]]:
        """
        MỞ NGẦM CHROME 2-3 GIÂY ĐỂ LẤY COOKIE VÀ ĐÓNG NGAY ĐỂ GIẢI PHÓNG 100% RAM.
        Trả về dict cookies định dạng key-value.
        """
        from playwright.async_api import async_playwright

        profile_dir = cls.get_profile_dir()
        if not PROFILE_DIR.exists():
            logger.warning("Chưa có thư mục profile Gemini Web.")
            return None

        try:
            async with async_playwright() as p:
                launch_opts = cls._get_launch_kwargs(headless=True)
                context = await p.chromium.launch_persistent_context(**launch_opts)
                page = await context.new_page()
                # Vào gemini để browser tự refresh cookie nếu cần
                await page.goto("https://gemini.google.com/app", timeout=20000, wait_until="domcontentloaded")
                await asyncio.sleep(1.5)

                raw_cookies = await context.cookies()
                await context.close()

                cookie_dict = {c["name"]: c["value"] for c in raw_cookies}
                cls._save_session_cache(raw_cookies)
                logger.info(f"Đã trích xuất thành công {len(cookie_dict)} cookie từ Gemini Web (Đã đóng Chrome giải phóng RAM).")
                return cookie_dict
        except Exception as e:
            logger.error(f"Lỗi khi trích xuất cookie ngầm: {e}")
            return None

    @classmethod
    def _save_session_cache(cls, cookies_list):
        try:
            cookie_dict = {c["name"]: c["value"] for c in cookies_list}
            with open(SESSION_FILE, "w", encoding="utf-8") as f:
                json.dump(cookie_dict, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Không thể lưu session cache: {e}")

    @classmethod
    def load_cached_cookies(cls) -> Dict[str, str]:
        if SESSION_FILE.exists():
            try:
                with open(SESSION_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}
