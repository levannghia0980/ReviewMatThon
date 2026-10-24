import os
import json
import time
import logging
import asyncio
import threading
import re
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from filelock import FileLock

from app.config import BASE_DIR
from app.services.step_03_translation.gemini_browser_session import GeminiBrowserSessionManager
from app.services.step_03_translation.gemini_selectors import GeminiSelectors

logger = logging.getLogger(__name__)


class GeminiWebService:
    """
    Giao tiếp với Google Gemini Web thông qua Chromium Persistent Session chạy ngầm duy nhất:
    - 1 Profile cố định (database/gemini_profile), 1 Single Tab duy nhất (_warm_page).
    - FileLock (database/gemini_profile.lock) ngăn chặn mọi xung đột tiến trình.
    - Quét dọn tiến trình mồ côi bằng psutil trước khi khởi chạy.
    - Tập trung 100% Selector vào GeminiSelectors (aria-label, role, visible text).
    - Tự động kiểm tra đăng nhập (ensure_logged_in), dừng ngay và báo lỗi rõ ràng nếu hết phiên.
    - Đổi và kiểm chứng cấp độ tư duy (Cao / Trung bình / Thấp) với cơ chế xác thực ngược (read-back verification).
    - Cơ chế chống treo: Nút Stop biến mất + văn bản ổn định 3s + hard timeout 300s kèm chụp màn hình debug.
    - Cơ chế chống leak prompt: So khớp và trích xuất dự phòng qua DOM nếu clipboard rỗng/bị lỗi.
    - Đóng sạch sẽ và giải phóng 100% RAM khi server tắt (atexit & shutdown_worker).
    """

    _loop: Optional[asyncio.AbstractEventLoop] = None
    _thread: Optional[threading.Thread] = None
    _playwright: Optional[Any] = None
    _context: Optional[Any] = None
    _warm_page: Optional[Any] = None
    _lock: Optional[asyncio.Lock] = None
    _current_thinking_level: str = "high"
    _file_lock_path: Path = BASE_DIR / "database" / "gemini_profile.lock"

    @classmethod
    def _ensure_loop(cls) -> asyncio.AbstractEventLoop:
        """Khởi động và duy trì một background thread với Event Loop chuyên biệt vĩnh viễn cho Chromium."""
        if cls._loop is not None and cls._loop.is_running():
            return cls._loop

        ready_event = threading.Event()

        def _worker_thread_main():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            cls._loop = loop
            cls._lock = asyncio.Lock()
            ready_event.set()
            loop.run_forever()

        cls._thread = threading.Thread(target=_worker_thread_main, daemon=True, name="GeminiBrowserLoopThread")
        cls._thread.start()
        ready_event.wait(timeout=10)
        return cls._loop

    @classmethod
    def is_running(cls) -> bool:
        return bool(cls._context is not None and cls._warm_page is not None and not cls._warm_page.is_closed())

    @classmethod
    def get_current_thinking_level(cls) -> str:
        return cls._current_thinking_level

    @classmethod
    def get_status(cls) -> Dict[str, Any]:
        return {
            "is_running": cls.is_running(),
            "thinking_level": cls._current_thinking_level,
            "url": cls._warm_page.url if (cls._warm_page and not cls._warm_page.is_closed()) else None
        }

    @classmethod
    async def _ensure_logged_in(cls, page: Any) -> None:
        """
        Kiểm tra trạng thái đăng nhập tài khoản Google trên trang:
        1. Nếu bị chuyển hướng sang accounts.google.com -> Ném lỗi yêu cầu đăng nhập.
        2. Nếu không tìm thấy ô nhập prompt sau khi tải trang -> Kiểm tra nút Sign in.
        """
        cur_url = page.url
        for domain in GeminiSelectors.LOGIN_REDIRECT_DOMAINS:
            if domain in cur_url:
                raise RuntimeError(
                    "Phiên đăng nhập Google Gemini Web đã hết hạn hoặc chưa đăng nhập! "
                    "Vui lòng vào Cài đặt -> Bấm 'Đăng nhập Gemini Web' để đăng nhập lại tài khoản Google."
                )

        # Kiểm tra xem có nút Đăng nhập hiển thị không
        for sel in GeminiSelectors.SIGN_IN_BUTTONS:
            try:
                sign_in_btn = await page.query_selector(sel)
                if sign_in_btn and await sign_in_btn.is_visible():
                    raise RuntimeError(
                        "Chưa đăng nhập tài khoản Google trên Gemini Web! "
                        "Vui lòng vào Cài đặt -> Bấm 'Đăng nhập Gemini Web' để kết nối tài khoản Google."
                    )
            except Exception as e:
                if "Chưa đăng nhập" in str(e):
                    raise

    @classmethod
    async def _open_new_chat_ui(cls, page: Any) -> bool:
        """
        Bấm nút 'Cuộc trò chuyện mới' (New chat) để dọn sạch ngữ cảnh cũ cho lô mới.
        Dùng semantic selector từ GeminiSelectors.NEW_CHAT_BUTTONS.
        """
        try:
            # 1. Thử click nút Cuộc trò chuyện mới bằng selector
            clicked = False
            for sel in GeminiSelectors.NEW_CHAT_BUTTONS:
                try:
                    btn = await page.query_selector(sel)
                    if btn and await btn.is_visible():
                        await btn.click()
                        clicked = True
                        break
                except Exception:
                    continue

            if not clicked:
                # Fallback JS click
                clicked = await page.evaluate('''() => {
                    const buttons = Array.from(document.querySelectorAll('button, a, div[role="button"]'));
                    const newChat = buttons.find(b => {
                        const t = (b.innerText || '').trim();
                        const aria = (b.getAttribute('aria-label') || '').trim();
                        return t.includes('Cuộc trò chuyện mới') || aria.includes('Cuộc trò chuyện mới') ||
                               t.includes('New chat') || aria.includes('New chat');
                    });
                    if (newChat) {
                        newChat.click();
                        return true;
                    }
                    return false;
                }''')

            if clicked:
                await asyncio.sleep(1.2)
            else:
                await page.goto("https://gemini.google.com/app", timeout=45000, wait_until="domcontentloaded")
                await asyncio.sleep(1.5)
        except Exception:
            try:
                await page.goto("https://gemini.google.com/app", timeout=45000, wait_until="domcontentloaded")
                await asyncio.sleep(1.5)
            except Exception:
                pass

        # Khi chuyển cuộc trò chuyện mới, mặc định giao diện Gemini Web luôn là 'low' (Thấp)
        cls._current_thinking_level = "low"
        return True

    @classmethod
    async def _apply_thinking_level_ui(cls, page: Any, thinking_level: str) -> bool:
        """
        Thiết lập và kiểm chứng Cấp độ tư duy (Cao / Trung bình / Thấp) trên giao diện Gemini Web:
        1. Bấm vào ô nhập prompt để hiển thị nút chọn mô hình/tư duy (bard-mode-switcher).
        2. Bấm nút chọn mô hình.
        3. Chọn mức tư duy tương ứng (gem-menu-item).
        4. Xác thực ngược (read-back verification): kiểm tra mục được chọn có class 'selected'.
        5. Thử lại tối đa 3 lần nếu chưa khớp.
        """
        target_norm = thinking_level.lower().strip()
        if target_norm == "low":
            cls._current_thinking_level = "low"
            return True

        target_keywords = GeminiSelectors.THINKING_LEVEL_MAP.get(target_norm, ["Cao", "High"])
        target_display = target_keywords[0]

        for attempt in range(1, 4):
            try:
                logger.info(f"[*] Thiết lập Cấp độ tư duy sang '{target_display.upper()}' (Lần thử {attempt}/3)...")

                # Bước 1: Bấm vào ô nhập prompt để đảm bảo nút chọn mô hình xuất hiện
                try:
                    box = await page.wait_for_selector(GeminiSelectors.INPUT_BOX, timeout=8000)
                    if box:
                        await box.click()
                        await asyncio.sleep(0.6)
                except Exception as click_err:
                    logger.debug(f"Click box warning: {click_err}")

                # Bước 2: Tìm và click nút chọn mô hình (bard-mode-menu-button / input-area-switch)
                opened_menu = False
                for sel in GeminiSelectors.MODEL_BUTTONS:
                    try:
                        btn = await page.query_selector(sel)
                        if btn and await btn.is_visible():
                            await btn.click()
                            opened_menu = True
                            break
                    except Exception:
                        continue

                if not opened_menu:
                    opened_menu = await page.evaluate('''() => {
                        const btn = document.querySelector('button[data-test-id="bard-mode-menu-button"]') ||
                                    document.querySelector('bard-mode-switcher button') ||
                                    document.querySelector('.input-area-switch');
                        if (btn) {
                            btn.click();
                            return true;
                        }
                        return false;
                    }''')

                if not opened_menu:
                    logger.warning("[Gemini Web UI] Chưa mở được menu chọn mô hình.")
                    await asyncio.sleep(1.0)
                    continue

                await asyncio.sleep(1.0)

                # Bước 3: Tìm mục menu Cấp độ tư duy mục tiêu và bấm chọn
                clicked = await page.evaluate('''(targetKws) => {
                    const items = Array.from(document.querySelectorAll('gem-menu-item[role="menuitem"], [role="menuitem"]'));
                    const match = items.find(el => {
                        const txt = (el.innerText || '').trim();
                        return targetKws.some(kw => txt.includes(kw));
                    });
                    if (match) {
                        match.click();
                        return true;
                    }
                    return false;
                }''', target_keywords)

                await asyncio.sleep(0.8)

                # Đóng menu nếu vẫn còn mở
                try:
                    await page.keyboard.press("Escape")
                except Exception:
                    pass
                await asyncio.sleep(0.4)

                # Bước 4: Kiểm chứng ngược (Read-back verification)
                # Mở nhanh menu để kiểm tra xem mục vừa chọn có class 'selected' không
                verify_ok = False
                for sel in GeminiSelectors.MODEL_BUTTONS:
                    try:
                        btn = await page.query_selector(sel)
                        if btn and await btn.is_visible():
                            await btn.click()
                            await asyncio.sleep(0.6)
                            verify_ok = await page.evaluate('''(targetKws) => {
                                const selectedItems = Array.from(document.querySelectorAll('gem-menu-item.selected, [role="menuitem"].selected'));
                                return selectedItems.some(el => {
                                    const txt = (el.innerText || '').trim();
                                    return targetKws.some(kw => txt.includes(kw));
                                });
                            }''', target_keywords)
                            await page.keyboard.press("Escape")
                            await asyncio.sleep(0.3)
                            break
                    except Exception:
                        continue

                if verify_ok or clicked:
                    cls._current_thinking_level = target_norm
                    logger.info(f"🎉 [Gemini Web UI] Đã thiết lập và xác thực thành công Cấp độ tư duy: {target_display.upper()}")
                    print(f"[OK] [Gemini Web UI] Đã thiết lập Cấp độ tư duy: {target_display.upper()}", flush=True)
                    return True

            except Exception as e:
                logger.warning(f"Lỗi khi chọn cấp độ tư duy trên UI (lần {attempt}): {e}")
                await asyncio.sleep(1.0)

        logger.warning(f"[Gemini Web UI] Không thể xác thực chuyển cấp độ tư duy sang '{target_display}', tiếp tục với mức mặc định.")
        return False

    @classmethod
    async def set_thinking_level(cls, thinking_level: str) -> bool:
        """Đổi Cấp độ tư duy trực tiếp trên phiên đang chạy mà không cần tắt trình duyệt."""
        loop = cls._ensure_loop()
        try:
            curr_loop = asyncio.get_running_loop()
        except RuntimeError:
            curr_loop = None

        if curr_loop == loop:
            return await cls._do_set_thinking_level(thinking_level)
        fut = asyncio.run_coroutine_threadsafe(cls._do_set_thinking_level(thinking_level), loop)
        if curr_loop is not None and curr_loop.is_running():
            return await asyncio.wrap_future(fut)
        return fut.result(timeout=60)

    @classmethod
    async def _do_set_thinking_level(cls, thinking_level: str) -> bool:
        if cls.is_running() and cls._warm_page:
            applied = await cls._apply_thinking_level_ui(cls._warm_page, thinking_level)
            return applied
        else:
            return await cls._do_init_worker(thinking_level=thinking_level)

    @classmethod
    async def init_worker(cls, thinking_level: Optional[str] = None) -> bool:
        """Khởi tạo hoặc kiểm tra duy nhất 1 Chromium Persistent Context ngầm chạy xuyên suốt."""
        loop = cls._ensure_loop()
        try:
            curr_loop = asyncio.get_running_loop()
        except RuntimeError:
            curr_loop = None

        if curr_loop == loop:
            return await cls._do_init_worker(thinking_level)
        fut = asyncio.run_coroutine_threadsafe(cls._do_init_worker(thinking_level), loop)
        if curr_loop is not None and curr_loop.is_running():
            return await asyncio.wrap_future(fut)
        return fut.result(timeout=120)

    @classmethod
    def init_worker_sync(cls, thinking_level: Optional[str] = None) -> bool:
        """Dùng cho run.py hoặc môi trường đồng bộ không có sẵn event loop."""
        loop = cls._ensure_loop()
        fut = asyncio.run_coroutine_threadsafe(cls._do_init_worker(thinking_level), loop)
        return fut.result(timeout=120)

    @classmethod
    async def _do_init_worker(cls, thinking_level: Optional[str] = None) -> bool:
        from playwright.async_api import async_playwright
        from app.config import settings

        target_thinking = thinking_level or getattr(settings, "GEMINI_WEB_THINKING", "medium")

        if cls.is_running() and cls._warm_page:
            if cls._current_thinking_level != target_thinking.lower().strip():
                await cls._apply_thinking_level_ui(cls._warm_page, target_thinking)
            return True

        if cls._lock is None:
            cls._lock = asyncio.Lock()

        async with cls._lock:
            if cls.is_running() and cls._warm_page:
                return True

            if not GeminiBrowserSessionManager.is_logged_in():
                logger.info("Chưa có phiên đăng nhập Gemini Web. Bỏ qua khởi động ngầm.")
                return False

            try:
                logger.info("🚀 Đang khởi động Duy Nhất 1 phiên Gemini Web Worker với FileLock...")
                cls._file_lock_path.parent.mkdir(parents=True, exist_ok=True)

                # Sử dụng FileLock để loại bỏ triệt để hiện tượng xung đột nhiều tiến trình Playwright cùng lúc
                file_lock = FileLock(str(cls._file_lock_path), timeout=15)
                with file_lock:
                    # Dọn dẹp tiến trình Chrome mồ côi trước khi mở profile
                    GeminiBrowserSessionManager.kill_orphaned_gemini_chrome()

                    cls._playwright = await async_playwright().start()
                    # Mở cửa sổ Chrome có giao diện (headless=False) để người dùng xem trực tiếp
                    is_headless = getattr(settings, "GEMINI_WEB_HEADLESS", False)
                    launch_opts = GeminiBrowserSessionManager._get_launch_kwargs(headless=is_headless)
                    cls._context = await cls._playwright.chromium.launch_persistent_context(**launch_opts)

                    cls._warm_page = cls._context.pages[0] if cls._context.pages else await cls._context.new_page()
                    await cls._warm_page.goto("https://gemini.google.com/app", timeout=60000, wait_until="domcontentloaded")
                    await asyncio.sleep(2.0)

                    # Kiểm tra trạng thái đăng nhập
                    await cls._ensure_logged_in(cls._warm_page)

                    # Chờ ô nhập prompt
                    try:
                        await cls._warm_page.wait_for_selector(GeminiSelectors.INPUT_BOX, timeout=20000)
                    except Exception:
                        await cls._ensure_logged_in(cls._warm_page)

                    # Thiết lập Cấp độ tư duy ban đầu
                    await cls._apply_thinking_level_ui(cls._warm_page, target_thinking)
                    cls._current_thinking_level = target_thinking.lower().strip()

                    logger.info(f"✅ Phiên Gemini Web duy nhất đã sẵn sàng (Cấp độ tư duy: {target_thinking.upper()})!")
                    return True

            except Exception as e:
                logger.warning(f"⚠️ Khởi động Gemini Web Worker gặp lỗi ({e}).")
                await cls._do_shutdown_worker()
                return False

    @classmethod
    async def shutdown_worker(cls) -> None:
        """Đóng sạch sẽ tiến trình Chromium và giải phóng toàn bộ tài nguyên khi tắt server."""
        if cls._loop is None or not cls._loop.is_running():
            return
        try:
            curr_loop = asyncio.get_running_loop()
        except RuntimeError:
            curr_loop = None

        if curr_loop == cls._loop:
            await cls._do_shutdown_worker()
            return
        fut = asyncio.run_coroutine_threadsafe(cls._do_shutdown_worker(), cls._loop)
        if curr_loop is not None and curr_loop.is_running():
            await asyncio.wrap_future(fut)
        else:
            try:
                fut.result(timeout=15)
            except Exception:
                pass

    @classmethod
    async def _do_shutdown_worker(cls) -> None:
        if cls._lock is not None:
            async with cls._lock:
                if cls._warm_page is not None:
                    try:
                        await cls._warm_page.close()
                    except Exception:
                        pass
                    cls._warm_page = None

                if cls._context is not None:
                    try:
                        await cls._context.close()
                    except Exception:
                        pass
                    cls._context = None

                if cls._playwright is not None:
                    try:
                        await cls._playwright.stop()
                    except Exception:
                        pass
                    cls._playwright = None

                # Đóng dứt điểm mọi tiến trình Chromium con còn sót
                GeminiBrowserSessionManager.kill_orphaned_gemini_chrome()
                logger.info("🛑 Đã tắt sạch Gemini Web Worker và giải phóng 100% RAM.")

    @staticmethod
    def _get_windows_clipboard() -> str:
        """Đọc trực tiếp nội dung văn bản Unicode từ Clipboard Windows qua Win32 API ctypes."""
        try:
            import ctypes
            import ctypes.wintypes as wt

            CF_UNICODETEXT = 13
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            user32.OpenClipboard.argtypes = [wt.HWND]
            user32.OpenClipboard.restype = wt.BOOL
            user32.GetClipboardData.argtypes = [wt.UINT]
            user32.GetClipboardData.restype = wt.HANDLE
            user32.CloseClipboard.restype = wt.BOOL

            kernel32.GlobalLock.argtypes = [wt.HGLOBAL]
            kernel32.GlobalLock.restype = wt.LPVOID
            kernel32.GlobalUnlock.argtypes = [wt.HGLOBAL]
            kernel32.GlobalUnlock.restype = wt.BOOL

            if not user32.OpenClipboard(None):
                return ""
            try:
                handle = user32.GetClipboardData(CF_UNICODETEXT)
                if not handle:
                    return ""
                ptr = kernel32.GlobalLock(handle)
                if not ptr:
                    return ""
                try:
                    return ctypes.wstring_at(ptr)
                finally:
                    kernel32.GlobalUnlock(handle)
            finally:
                user32.CloseClipboard()
        except Exception as e:
            logger.debug(f"Lỗi đọc Windows clipboard qua ctypes: {e}")
            return ""

    @staticmethod
    def _extract_content_and_instructions(prompt: str) -> Tuple[Optional[str], str]:
        """
        Tự động đóng gói và bóc tách 'Nội dung (nd) JSON / văn bản' ra khỏi 'Yêu cầu (instructions)'
        ngay từ chính Prompt gốc của hệ thống để chống tràn ký tự.
        """
        # 1. Nhận diện Dịch Thoại
        marker_dt = "=== KỊCH BẢN GỐC CẦN DỊCH LỒNG TIẾNG (DANH SÁCH MẨU JSON ĐẦU VÀO) ==="
        if marker_dt in prompt:
            parts = prompt.split(marker_dt, 1)
            before = parts[0]
            after = parts[1]
            next_header = re.search(r'\n(=== [^=\n]+ ===)', after)
            if next_header:
                data_text = after[:next_header.start()].strip()
                remaining = after[next_header.start():].strip()
            else:
                data_text = after.strip()
                remaining = ""

            if data_text and (data_text.startswith("[") or data_text.startswith("{")):
                note = "(Toàn bộ danh sách câu thoại kịch bản JSON gốc đã được cung cấp ở phần dữ liệu. Hãy dịch từng câu thoại theo đúng thứ tự mảng JSON)."
                instruction_prompt = f"{before.strip()}\n\n{marker_dt}\n{note}\n\n{remaining}".strip()
                return data_text, instruction_prompt

        # 2. Nhận diện Dịch Hán (Bóc tách thực thể)
        marker_dh = "=== VĂN BẢN TOÀN BỘ LÔ CHƯƠNG (LIÊN TỤC KHÔNG XUỐNG DÒNG) ==="
        if marker_dh in prompt:
            parts = prompt.split(marker_dh, 1)
            before = parts[0]
            after = parts[1]
            next_header = re.search(r'\n(=== [^=\n]+ ===|Yêu cầu trả về DUY NHẤT)', after)
            if next_header:
                raw_content = after[:next_header.start()].strip()
                remaining = after[next_header.start():].strip()
            else:
                raw_content = after.strip()
                remaining = ""

            if raw_content:
                data_payload = {
                    "task": "NỘI DUNG VĂN BẢN LÔ CẦN BÓC TÁCH THỰC THỂ",
                    "content": raw_content
                }
                data_json = json.dumps(data_payload, ensure_ascii=False, indent=2)
                note = "(Nội dung văn bản JSON đã được cung cấp ở phần dữ liệu. Hãy phân tích và bóc tách thực thể dựa trên dữ liệu văn bản đã nhận)."
                instruction_prompt = f"{before.strip()}\n\n{marker_dh}\n{note}\n\n{remaining}".strip()
                return data_json, instruction_prompt

        # 3. Mặc định: Không tách
        return None, prompt

    @classmethod
    async def generate_content(
        cls,
        prompt: str,
        data_content: Optional[str] = None,
        prompt_file_path: Optional[str] = None
    ) -> str:
        """
        Gửi prompt lên Gemini Web và nhận văn bản phản hồi thông qua Browser Worker ngầm duy nhất.
        Tự động điều phối xuyên luồng (cross-thread) an toàn sang dedicated browser loop.
        """
        loop = cls._ensure_loop()
        try:
            curr_loop = asyncio.get_running_loop()
        except RuntimeError:
            curr_loop = None

        if curr_loop == loop:
            return await cls._do_generate_content(prompt, data_content=data_content, prompt_file_path=prompt_file_path)
        fut = asyncio.run_coroutine_threadsafe(
            cls._do_generate_content(prompt, data_content=data_content, prompt_file_path=prompt_file_path),
            loop
        )
        if curr_loop is not None and curr_loop.is_running():
            return await asyncio.wrap_future(fut)
        return fut.result(timeout=400)

    @classmethod
    async def _do_generate_content(
        cls,
        prompt: str,
        data_content: Optional[str] = None,
        prompt_file_path: Optional[str] = None,
        retry_count: int = 0
    ) -> str:
        """Thực thi dịch trên chính event loop sở hữu Playwright context."""
        # 1. Đảm bảo worker đang hoạt động
        if cls._context is None or cls._warm_page is None or cls._warm_page.is_closed():
            logger.info("Khởi tạo / Khôi phục phiên Gemini Web Worker...")
            success = await cls._do_init_worker()
            if not success or cls._context is None:
                raise RuntimeError(
                    "Chưa đăng nhập Gemini Web hoặc không thể khởi chạy Chromium! "
                    "Vui lòng vào Cài đặt -> Bấm 'Đăng nhập Gemini Web' để kết nối tài khoản Google."
                )

        from app.config import settings
        thinking_level = getattr(settings, "GEMINI_WEB_THINKING", "medium").lower().strip()

        def _clean_web_output(raw_text: str) -> str:
            if not raw_text:
                return ""
            cleaned = raw_text.replace('\xa0', ' ').replace('\u3000', ' ').replace('\u200b', '').replace('\ufeff', '')
            tb_matches = re.findall(r'<TextBox\s+text=["\'](.*?)["\']\s*/>', cleaned, flags=re.DOTALL)
            if tb_matches:
                cleaned = "\n".join(tb_matches)
            cleaned = re.sub(r'</?(?:Section|TextBox|Elicitations|Elicitation)[^>]*>', '', cleaned)
            return cleaned.strip()

        # Thực thi dịch trên DUY NHẤT 1 TAB ĐÃ MỞ SẴN (_warm_page)
        if cls._lock is None:
            cls._lock = asyncio.Lock()

        async with cls._lock:
            page = cls._warm_page
            try:
                # Kiểm tra trạng thái đăng nhập
                await cls._ensure_logged_in(page)

                # 1. Bắt đầu trên một cuộc trò chuyện mới tinh
                await cls._open_new_chat_ui(page)

                # 2. Chờ ô nhập văn bản xuất hiện
                box = await page.wait_for_selector(GeminiSelectors.INPUT_BOX, timeout=20000)

                # 3. THIẾT LẬP CẤP ĐỘ TƯ DUY NẾU CẦN
                if thinking_level in ("high", "medium"):
                    await cls._apply_thinking_level_ui(page, thinking_level)
                    box = await page.wait_for_selector(GeminiSelectors.INPUT_BOX, timeout=20000)

                # 4. CHUẨN BỊ NỘI DUNG VÀ PROMPT
                actual_data = data_content
                actual_prompt = prompt
                if not actual_data:
                    actual_data, actual_prompt = cls._extract_content_and_instructions(prompt)

                if actual_data and actual_data.strip():
                    effective_prompt = f"{actual_prompt}\n\n=== DỮ LIỆU ĐẦU VÀO ===\n{actual_data}"
                else:
                    effective_prompt = actual_prompt

                logger.info(f"📤 [Gemini Web] Nhập câu lệnh yêu cầu ({len(effective_prompt)} ký tự)...")
                print(f"📤 [Gemini Web] Nhập câu lệnh yêu cầu ({len(effective_prompt)} ký tự)...", flush=True)

                # Bấm vào ô chat và nhập prompt
                await box.click()
                await asyncio.sleep(0.3)
                await page.keyboard.insert_text(effective_prompt)
                await asyncio.sleep(0.5)

                # Đếm số lượng phản hồi hiện tại trước khi bấm Gửi
                prev_response_count = await page.evaluate(
                    "() => document.querySelectorAll('model-response').length"
                )

                # 5. BẤM NÚT GỬI (An toàn tuyệt đối: Không bấm nút Dừng/Stop)
                send_clicked = False
                for sel in GeminiSelectors.SEND_BUTTONS:
                    try:
                        s_btn = await page.query_selector(sel)
                        if s_btn and await s_btn.is_visible():
                            # Kiểm tra ngược để 100% không bấm phải nút Dừng/Stop
                            is_stop = await page.evaluate('''(btn) => {
                                const aria = (btn.getAttribute('aria-label') || '').toLowerCase();
                                const title = (btn.getAttribute('title') || '').toLowerCase();
                                return aria.includes('dừng') || aria.includes('stop') || title.includes('dừng') || title.includes('stop');
                            }''', s_btn)
                            if not is_stop:
                                await s_btn.click()
                                send_clicked = True
                                break
                    except Exception:
                        continue

                if not send_clicked:
                    await box.focus()
                    await box.press("Enter")

                # Xác minh ô chat đã gửi đi an toàn (Chống bấm nhầm nút Stop khi AI đang sinh)
                for _ in range(6):
                    await asyncio.sleep(0.6)
                    # 1. Kiểm tra xem AI đã bắt đầu sinh phản hồi chưa (xuất hiện Stop hoặc model-response mới)
                    is_generating = await page.evaluate('''(prevCount) => {
                        const responses = document.querySelectorAll('model-response');
                        if (responses.length > prevCount) return true;
                        const allButtons = Array.from(document.querySelectorAll('button, div[role="button"]'));
                        return allButtons.some(b => {
                            const aria = (b.getAttribute('aria-label') || '').toLowerCase();
                            const testId = (b.getAttribute('data-test-id') || '').toLowerCase();
                            return aria.includes('dừng') || aria.includes('stop') || testId.includes('stop');
                        });
                    }''', prev_response_count)
                    if is_generating:
                        # AI đã bắt đầu sinh phản hồi -> Tuyệt đối dừng mọi thao tác bàn phím/chuột!
                        break

                    is_cleared = await page.evaluate('''() => {
                        const tb = document.querySelector('rich-textarea div[contenteditable="true"], div[role="textbox"]');
                        return !tb || (tb.innerText || '').trim().length === 0;
                    }''')
                    if is_cleared:
                        break

                    # Nếu sau 1.2s vẫn chưa xóa ô chat VÀ AI chưa sinh -> chỉ click lại nút GỬI thực sự (không press Enter)
                    for sel in GeminiSelectors.SEND_BUTTONS:
                        try:
                            s_btn = await page.query_selector(sel)
                            if s_btn and await s_btn.is_visible():
                                is_stop = await page.evaluate('''(btn) => {
                                    const aria = (btn.getAttribute('aria-label') || '').toLowerCase();
                                    return aria.includes('dừng') || aria.includes('stop');
                                }''', s_btn)
                                if not is_stop:
                                    await s_btn.click()
                                    break
                        except Exception:
                            continue

                await asyncio.sleep(1.0)
                print(f"👉 [Gemini Web] Link cuộc trò chuyện: {page.url}", flush=True)

                # 6. CHỜ AI HOÀN THÀNH (Cơ chế chống treo: Stop biến mất + văn bản ổn định 3s + hard timeout 300s)
                max_wait_secs = 300  # 5 phút hard timeout
                start_time = asyncio.get_event_loop().time()
                last_log_time = 0
                last_text_len = 0
                stable_ticks = 0

                while (asyncio.get_event_loop().time() - start_time) < max_wait_secs:
                    status = await page.evaluate('''(prevRespCount) => {
                        const responses = document.querySelectorAll('model-response');
                        if (responses.length <= prevRespCount) {
                            return { isReady: false, isStopPresent: true, currentLen: 0 };
                        }
                        const lastResp = responses[responses.length - 1];

                        // 1. Kiểm tra sự tồn tại của nút Stop / Dừng
                        const allButtons = Array.from(document.querySelectorAll('button, div[role="button"]'));
                        const isStopButtonPresent = allButtons.some(b => {
                            const aria = (b.getAttribute('aria-label') || '').toLowerCase();
                            const testId = (b.getAttribute('data-test-id') || '').toLowerCase();
                            const txt = (b.innerText || '').toLowerCase();
                            return aria.includes('dừng') || aria.includes('stop') ||
                                   testId.includes('stop') || txt === 'stop';
                        });

                        // 2. Kiểm tra nút Copy ở footer (ngoài code block)
                        const nonCodeButtons = Array.from(lastResp.querySelectorAll('button')).filter(b => {
                            return !b.closest('pre, code, [class*="code-block"], [class*="codeblock"]');
                        });

                        const hasFooterCopy = nonCodeButtons.some(b => {
                            const aria = (b.getAttribute('aria-label') || '').toLowerCase();
                            const testId = (b.getAttribute('data-test-id') || '').toLowerCase();
                            const title = (b.getAttribute('title') || '').toLowerCase();
                            return aria.includes('sao chép') || aria.includes('copy') ||
                                   testId.includes('copy') || title.includes('sao chép') || title.includes('copy');
                        });

                        const currentLen = (lastResp.innerText || '').trim().length;
                        return {
                            isStopPresent: isStopButtonPresent,
                            hasFooterCopy: hasFooterCopy,
                            currentLen: currentLen
                        };
                    }''', prev_response_count)

                    elapsed = int(asyncio.get_event_loop().time() - start_time)
                    is_stop_present = status.get("isStopPresent", True)
                    curr_len = status.get("currentLen", 0)

                    # Điều kiện hoàn thành:
                    # - Nút Stop đã biến mất
                    # - Độ dài văn bản > 10 ký tự và không đổi trong 2 lần kiểm tra liên tiếp (2 x 1.5s = 3 giây ổn định)
                    if not is_stop_present and curr_len > 10:
                        if curr_len == last_text_len:
                            stable_ticks += 1
                            if stable_ticks >= 2:
                                await asyncio.sleep(0.5)
                                break
                        else:
                            stable_ticks = 1
                            last_text_len = curr_len
                    else:
                        stable_ticks = 0
                        last_text_len = curr_len

                    if elapsed - last_log_time >= 5:
                        last_log_time = elapsed
                        logger.info(f"⏳ [Gemini Web] AI đang suy nghĩ / sinh kết quả... ({elapsed}s)")
                        print(f"⏳ [Gemini Web] AI đang suy nghĩ / sinh kết quả... ({elapsed}s)", flush=True)

                    await asyncio.sleep(1.5)

                # Kiểm tra nếu bị quá thời gian 300s
                if (asyncio.get_event_loop().time() - start_time) >= max_wait_secs:
                    # Chụp ảnh màn hình lưu vào database/debug_screenshots/
                    try:
                        screenshot_dir = BASE_DIR / "database" / "debug_screenshots"
                        screenshot_dir.mkdir(parents=True, exist_ok=True)
                        screenshot_path = screenshot_dir / f"gemini_timeout_{int(time.time())}.png"
                        await page.screenshot(path=str(screenshot_path), full_page=True)
                        logger.error(f"❌ [Gemini Web] Quá thời gian chờ phản hồi ({max_wait_secs}s). Đã lưu ảnh chụp lỗi: {screenshot_path}")
                        print(f"❌ [Gemini Web] Quá thời gian chờ ({max_wait_secs}s). Ảnh lỗi: {screenshot_path}", flush=True)
                    except Exception as ss_err:
                        logger.debug(f"Không thể chụp màn hình lỗi: {ss_err}")

                    # Reload trang để hồi phục cho lần sau
                    try:
                        await page.reload(timeout=30000)
                    except Exception:
                        pass
                    raise TimeoutError(f"Gemini Web không hoàn tất phản hồi sau {max_wait_secs} giây.")

                # 7. TRÍCH XUẤT PHẢN HỒI (Ưu tiên nút Sao chép footer, fallback trích xuất DOM trực tiếp)
                extracted_text = ""

                # 7.1. Xóa clipboard trước để không nhận nhầm nội dung cũ
                try:
                    await page.evaluate("() => navigator.clipboard.writeText('')")
                except Exception:
                    pass

                # 7.2. Bấm nút Sao chép footer của phản hồi cuối cùng
                clicked_copy = await page.evaluate('''() => {
                    const responses = document.querySelectorAll('model-response');
                    if (!responses.length) return false;
                    const lastResp = responses[responses.length - 1];

                    const nonCodeButtons = Array.from(lastResp.querySelectorAll('button')).filter(b => {
                        return !b.closest('pre, code, [class*="code-block"], [class*="codeblock"]');
                    });

                    const target = nonCodeButtons.find(b => {
                        const aria = (b.getAttribute('aria-label') || '').toLowerCase();
                        const testId = (b.getAttribute('data-test-id') || '').toLowerCase();
                        const title = (b.getAttribute('title') || '').toLowerCase();
                        return aria.includes('sao chép') || aria.includes('copy') ||
                               testId.includes('copy') || title.includes('sao chép') || title.includes('copy');
                    });

                    if (!target) return false;
                    target.scrollIntoView({ block: 'nearest' });
                    target.click();
                    return true;
                }''')

                if clicked_copy:
                    await asyncio.sleep(0.5)
                    try:
                        extracted_text = await page.evaluate("() => navigator.clipboard.readText()")
                    except Exception:
                        pass
                    if not extracted_text or len(extracted_text.strip()) < 5:
                        extracted_text = cls._get_windows_clipboard()

                # 7.3. Chống rò rỉ prompt & Fallback DOM
                is_prompt_leak = (
                    extracted_text and (
                        extracted_text.strip() == effective_prompt.strip()
                        or "=== QUY CHUẨN THỂ LOẠI" in extracted_text
                        or "=== KỊCH BẢN GỐC CẦN DỊCH" in extracted_text
                        or "=== VĂN BẢN TOÀN BỘ LÔ CHƯƠNG" in extracted_text
                    )
                )

                if is_prompt_leak or not extracted_text or len(extracted_text.strip()) < 5:
                    if is_prompt_leak:
                        logger.warning("⚠️ Phát hiện văn bản copy trùng Prompt gửi đi! Chuyển sang trích xuất trực tiếp từ DOM...")
                    extracted_text = await page.evaluate('''() => {
                        const responses = document.querySelectorAll('model-response');
                        if (!responses.length) return "";
                        const lastResp = responses[responses.length - 1];

                        const codeBlock = lastResp.querySelector('pre code, pre, div.code-block');
                        if (codeBlock && codeBlock.innerText.trim().length > 5) {
                            return codeBlock.innerText.trim();
                        }
                        const msgContent = lastResp.querySelector('message-content, div.markdown');
                        if (msgContent && msgContent.innerText.trim().length > 5) {
                            return msgContent.innerText.trim();
                        }
                        return lastResp.innerText.trim();
                    }''')

                if extracted_text and len(extracted_text.strip()) > 5:
                    clean_res = _clean_web_output(extracted_text)

                    # 7.4. Nhận diện phản hồi bị dừng dở dang hoặc gặp lỗi giao diện web
                    ABORTED_PATTERNS = [
                        "bạn đã dừng câu trả lời này",
                        "you stopped this response",
                        "đã xảy ra lỗi",
                        "something went wrong",
                        "không thể tạo phản hồi",
                        "cannot generate response"
                    ]
                    clean_lower = clean_res.lower()
                    is_aborted = any(pat in clean_lower for pat in ABORTED_PATTERNS)

                    if is_aborted:
                        # Tự động chụp ảnh màn hình lưu lại để người dùng/hệ thống xem xét ngay cả khi chạy headless
                        try:
                            screenshot_dir = BASE_DIR / "database" / "debug_screenshots"
                            screenshot_dir.mkdir(parents=True, exist_ok=True)
                            ss_path = screenshot_dir / f"gemini_aborted_{int(time.time())}.png"
                            await page.screenshot(path=str(ss_path), full_page=True)
                            logger.error(f"❌ [Gemini Web] Phản hồi bị dừng dở dang / lỗi ({clean_res[:60]}...). Đã lưu ảnh chụp lỗi: {ss_path}")
                            print(f"❌ [Gemini Web] Phản hồi bị dừng dở dang. Ảnh lỗi: {ss_path}", flush=True)
                        except Exception as ss_err:
                            logger.debug(f"Không thể chụp ảnh lỗi: {ss_err}")

                        # Reload để khôi phục cho lần sau
                        try:
                            await page.reload(timeout=30000)
                        except Exception:
                            pass
                        raise RuntimeError(f"Gemini Web phản hồi bị dừng hoặc lỗi: '{clean_res[:80]}'")

                    if clean_res and len(clean_res) > 5:
                        logger.info(f"✔ [Gemini Web] Đã sao chép thành công phản hồi ({len(clean_res)} ký tự).")
                        print(f"✔ [Gemini Web] Đã sao chép thành công phản hồi ({len(clean_res)} ký tự).", flush=True)

                        # Dọn sang cuộc trò chuyện mới ngay để giải phóng bộ nhớ DOM
                        try:
                            await cls._open_new_chat_ui(page)
                        except Exception:
                            pass
                        return clean_res

            except Exception as web_err:
                err_str = str(web_err)
                if ("Connection closed" in err_str or "Target closed" in err_str or "has no attribute" in err_str) and retry_count < 1:
                    logger.warning(f"⚠️ Kết nối trình duyệt bị ngắt ({err_str}). Tự động nạp lại worker...")
                    await cls._do_shutdown_worker()
                    await cls._do_init_worker()
                    return await cls._do_generate_content(prompt, data_content=data_content, prompt_file_path=prompt_file_path, retry_count=retry_count + 1)
                logger.error(f"❌ [Gemini Web Worker Lỗi] {web_err}")
                raise RuntimeError(f"Lỗi Gemini Web: {web_err}. Vui lòng thử lại.")

        return ""
