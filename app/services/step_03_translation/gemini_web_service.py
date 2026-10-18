import os
import json
import time
import logging
import asyncio
import threading
import re
from typing import Optional, Dict, Any, Tuple
from app.services.step_03_translation.gemini_browser_session import GeminiBrowserSessionManager

logger = logging.getLogger(__name__)


class GeminiWebService:
    """
    Giao tiếp với Google Gemini Web (Gehihi) thông qua Chromium Persistent Session chạy ngầm liên tục.
    - Giữ Browser Context luôn ấm trong RAM suốt thời gian chạy server (không bật tắt liên tục).
    - Phản hồi cực nhanh (~5-7s) vì Google nhận diện 100% người dùng trình duyệt thật, không bị bóp băng thông.
    - Mỗi request mượn một tab riêng và đóng ngay sau khi nhận phản hồi để giải phóng DOM.
    - Tự động đóng hoàn toàn tiến trình Chromium khi ứng dụng tắt (lifespan shutdown).
    """

    _loop: Optional[asyncio.AbstractEventLoop] = None
    _thread: Optional[threading.Thread] = None
    _playwright: Optional[Any] = None
    _context: Optional[Any] = None
    _warm_page: Optional[Any] = None
    _lock: Optional[asyncio.Lock] = None
    _current_thinking_level: str = "high"

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
    async def _apply_thinking_level_ui(cls, page: Any, thinking_level: str) -> bool:
        """
        Thực hiện mở menu 2 lớp để chọn Cấp độ tư duy (Cao / Trung bình / Thấp)
        theo đúng quy trình UI tương tác:
        1. Bấm vào khung chat để bung nút 'Gemini Tự động'
        2. Bấm vào nút 'Gemini Tự động', đợi 1.2s hiện menu
        3. Bấm vào dòng dưới cùng 'Cấp độ tư duy', đợi 1.5s hiện 3 mức
        4. Bấm trực tiếp vào chữ mục tiêu (Thấp / Trung bình / Cao)
        """
        try:
            if thinking_level.lower().strip() == "low":
                cls._current_thinking_level = "low"
                return True

            target_level_map = {
                "high": "Cao",
                "medium": "Trung bình",
                "low": "Thấp"
            }
            target_label = target_level_map.get(thinking_level.lower().strip(), "Cao")

            # Bước 1: Bắt buộc bấm vào khung chat trước để bung nút chọn mô hình ở góc phải
            try:
                await page.click(
                    'rich-textarea div[contenteditable="true"], div[role="textbox"], textarea',
                    timeout=15000
                )
                await asyncio.sleep(1.2)
            except Exception as click_box_err:
                logger.debug(f"Click box: {click_box_err}")

            # Bước 2: Tìm và click nút 'Gemini Tự động' hoặc 'Tự động'
            opened_menu = await page.evaluate('''() => {
                const all = Array.from(document.querySelectorAll('button, div[role="button"], span, div'));
                for (let i = all.length - 1; i >= 0; i--) {
                    const t = (all[i].innerText || '').trim();
                    if (t.includes('Gemini Tự động') || t === 'Tự động' || t.includes('Gemini 2.5') || t.includes('Gemini 3')) {
                        const target = all[i].closest('button, div[role="button"]') || all[i];
                        target.click();
                        return true;
                    }
                }
                const btns = Array.from(document.querySelectorAll('button'));
                for (const b of btns) {
                    if ((b.getAttribute('aria-haspopup') === 'menu' || b.getAttribute('aria-expanded')) && b.innerText) {
                        b.click();
                        return true;
                    }
                }
                return false;
            }''')

            if not opened_menu:
                logger.warning("[Gemini Web UI] Chưa tìm thấy nút 'Gemini Tự động' trong khung chat.")
                return False

            # Đợi 1.2s để menu xổ xuống hoàn toàn
            await asyncio.sleep(1.2)

            # Bước 3: Tìm dòng dưới cùng có chữ 'Cấp độ tư duy' và bấm vào
            opened_sub = await page.evaluate('''() => {
                const candidates = Array.from(document.querySelectorAll('*')).filter(el => {
                    const t = (el.innerText || '').trim();
                    return t.includes('Cấp độ tư duy') && t.length < 50;
                });
                if (!candidates.length) return false;
                candidates.sort((a, b) => (a.innerText || '').trim().length - (b.innerText || '').trim().length);
                const leaf = candidates[0];
                const clickable = leaf.closest('button, [role="menuitem"], [role="button"], li') || leaf;
                clickable.scrollIntoView({ block: 'nearest' });
                clickable.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true, cancelable: true }));
                clickable.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, cancelable: true }));
                clickable.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
                clickable.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
                clickable.click();
                return true;
            }''')

            if not opened_sub:
                logger.warning("[Gemini Web UI] Không tìm thấy dòng 'Cấp độ tư duy' trong menu.")
                return False

            # Đợi 1.5s để menu con bên phải bung ra đủ 3 mức: Thấp, Trung bình, Cao
            await asyncio.sleep(1.5)

            # Bước 4: Tìm chính xác theo chữ (Thấp / Trung bình / Cao) và bấm chọn
            clicked = await page.evaluate('''(targetText) => {
                const candidates = Array.from(document.querySelectorAll('*')).filter(el => {
                    const t = (el.innerText || '').trim();
                    return (t === targetText || t.startsWith(targetText) || t.includes(targetText)) && t.length < 60;
                });
                if (!candidates.length) return false;
                candidates.sort((a, b) => (a.innerText || '').trim().length - (b.innerText || '').trim().length);
                const leaf = candidates[0];
                const clickable = leaf.closest('button, [role="menuitem"], [role="menuitemradio"], [role="button"], li') || leaf;
                clickable.scrollIntoView({ block: 'nearest' });
                clickable.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
                clickable.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
                clickable.click();
                return true;
            }''', target_label)

            # Đóng menu bằng phím Escape
            await asyncio.sleep(0.5)
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.3)

            if clicked:
                cls._current_thinking_level = thinking_level.lower().strip()
                logger.info(f"🎉 [Gemini Web UI] Đã thiết lập thành công Cấp độ tư duy: {target_label.upper()}")
                print(f"[OK] [Gemini Web UI] Đã thiết lập Cấp độ tư duy: {target_label.upper()}", flush=True)
                return True
            else:
                logger.warning(f"[Gemini Web UI] Chưa click được vào mục '{target_label}'.")
                return False
        except Exception as e:
            logger.warning(f"Lỗi khi chọn cấp độ tư duy trên UI: {e}")
            return False

    @classmethod
    async def _open_new_chat_ui(cls, page: Any) -> bool:
        """
        Bấm nút 'Cuộc trò chuyện mới' (New chat) để dọn sạch ngữ cảnh cũ.
        Mỗi khi mở cuộc trò chuyện mới, Gemini Web luôn tự động reset cấp độ tư duy về mặc định 'Thấp'.
        """
        try:
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
        # Reset biến cấp độ tư duy về 'low' vì cuộc trò chuyện mới luôn mặc định là Thấp
        cls._current_thinking_level = "low"
        return True

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
        """
        Khởi tạo hoặc kiểm tra duy nhất 1 Chromium Persistent Context ngầm chạy xuyên suốt.
        Đảm bảo giữ 1 phiên duy nhất (Singleton), không tắt giữa chừng để không bao giờ bị mất cấp độ tư duy.
        """
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

        # Nếu đã đang chạy ổn định, chỉ cần đồng bộ mức tư duy nếu có thay đổi
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
                logger.info("🚀 Đang khởi động Duy Nhất 1 phiên Gemini Web Worker trên dedicated loop...")
                # Quét dọn tiến trình con bị kẹt để không bao giờ bị xung đột khóa profile
                GeminiBrowserSessionManager.kill_orphaned_gemini_chrome()

                cls._playwright = await async_playwright().start()
                launch_opts = GeminiBrowserSessionManager._get_launch_kwargs(headless=False)
                cls._context = await cls._playwright.chromium.launch_persistent_context(**launch_opts)

                cls._warm_page = cls._context.pages[0] if cls._context.pages else await cls._context.new_page()
                await cls._warm_page.goto("https://gemini.google.com/app", timeout=60000, wait_until="domcontentloaded")
                await asyncio.sleep(1.5)
                
                # Chờ khung chat và click vào mở rộng bằng page.click để tránh lỗi element detached
                try:
                    await cls._warm_page.click(
                        'rich-textarea div[contenteditable="true"], div[role="textbox"], textarea',
                        timeout=15000
                    )
                    await asyncio.sleep(0.5)
                except Exception:
                    pass

                # Thiết lập ngay Cấp độ tư duy lúc vừa bật
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
            fut.result(timeout=15)

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
                logger.info("🛑 Đã tắt sạch Gemini Web Worker và giải phóng RAM.")

    @staticmethod
    def _get_windows_clipboard() -> str:
        """Đọc trực tiếp nội dung văn bản Unicode từ Clipboard Windows qua Win32 API ctypes (hỗ trợ 64-bit an toàn)."""
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
    def _set_windows_clipboard(text: str) -> bool:
        """Ghi trực tiếp nội dung Unicode vào Windows OS Clipboard qua Win32 API ctypes (an toàn, không phụ thuộc focus trình duyệt)."""
        try:
            import ctypes
            import ctypes.wintypes as wt

            CF_UNICODETEXT = 13
            GMEM_MOVEABLE = 0x0002
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            user32.OpenClipboard.argtypes = [wt.HWND]
            user32.OpenClipboard.restype = wt.BOOL
            user32.EmptyClipboard.argtypes = []
            user32.EmptyClipboard.restype = wt.BOOL
            user32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
            user32.SetClipboardData.restype = wt.HANDLE
            user32.CloseClipboard.restype = wt.BOOL

            kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
            kernel32.GlobalAlloc.restype = wt.HGLOBAL
            kernel32.GlobalLock.argtypes = [wt.HGLOBAL]
            kernel32.GlobalLock.restype = wt.LPVOID
            kernel32.GlobalUnlock.argtypes = [wt.HGLOBAL]
            kernel32.GlobalUnlock.restype = wt.BOOL

            if not user32.OpenClipboard(None):
                return False
            try:
                user32.EmptyClipboard()
                data_bytes = (text + '\0').encode('utf-16le')
                h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data_bytes))
                if not h_mem:
                    return False
                ptr = kernel32.GlobalLock(h_mem)
                if not ptr:
                    return False
                try:
                    ctypes.memmove(ptr, data_bytes, len(data_bytes))
                finally:
                    kernel32.GlobalUnlock(h_mem)
                user32.SetClipboardData(CF_UNICODETEXT, h_mem)
                return True
            finally:
                user32.CloseClipboard()
        except Exception as e:
            logger.debug(f"Lỗi ghi Windows clipboard qua ctypes: {e}")
            return False

    @staticmethod
    def _copy_file_to_windows_clipboard(file_path: str) -> bool:
        """Sao chép tệp vào Clipboard Windows dưới định dạng CF_HDROP (tương đương Ctrl+C file trong Explorer)."""
        try:
            import ctypes
            import ctypes.wintypes as wt

            class DROPFILES(ctypes.Structure):
                _fields_ = [
                    ("pFiles", wt.DWORD),
                    ("pt", wt.POINT),
                    ("fNC", wt.BOOL),
                    ("fWide", wt.BOOL),
                ]

            CF_HDROP = 15
            GMEM_MOVEABLE = 0x0002
            GMEM_ZEROINIT = 0x0040
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
            kernel32.GlobalAlloc.restype = wt.HGLOBAL
            kernel32.GlobalLock.argtypes = [wt.HGLOBAL]
            kernel32.GlobalLock.restype = wt.LPVOID
            kernel32.GlobalUnlock.argtypes = [wt.HGLOBAL]
            kernel32.GlobalUnlock.restype = wt.BOOL

            user32.OpenClipboard.argtypes = [wt.HWND]
            user32.OpenClipboard.restype = wt.BOOL
            user32.EmptyClipboard.argtypes = []
            user32.EmptyClipboard.restype = wt.BOOL
            user32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
            user32.SetClipboardData.restype = wt.HANDLE
            user32.CloseClipboard.argtypes = []
            user32.CloseClipboard.restype = wt.BOOL

            abs_path = os.path.abspath(file_path)
            if not os.path.exists(abs_path):
                return False

            path_bytes = (abs_path + '\0\0').encode('utf-16le')

            df = DROPFILES()
            df.pFiles = ctypes.sizeof(DROPFILES)
            df.fWide = True

            total_size = ctypes.sizeof(DROPFILES) + len(path_bytes)
            h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, total_size)
            if not h_mem:
                return False
            ptr = kernel32.GlobalLock(h_mem)
            if not ptr:
                return False
            try:
                ctypes.memmove(ptr, ctypes.byref(df), ctypes.sizeof(DROPFILES))
                ctypes.memmove(ptr + ctypes.sizeof(DROPFILES), path_bytes, len(path_bytes))
            finally:
                kernel32.GlobalUnlock(h_mem)

            opened = False
            for _ in range(15):
                if user32.OpenClipboard(None):
                    opened = True
                    break
                time.sleep(0.05)

            if not opened:
                return False

            try:
                user32.EmptyClipboard()
                res = user32.SetClipboardData(CF_HDROP, h_mem)
                return bool(res)
            finally:
                user32.CloseClipboard()
        except Exception as e:
            logger.debug(f"Lỗi sao chép tệp vào Windows clipboard: {e}")
            return False

    @staticmethod
    def _extract_content_and_instructions(prompt: str) -> Tuple[Optional[str], str]:
        """
        Tự động đóng gói và bóc tách 'Nội dung (nd) JSON / văn bản' ra khỏi 'Yêu cầu (instructions)'
        ngay từ chính Prompt gốc của hệ thống:
        - Trường hợp 1: Dịch kịch bản thoại (LLM 2)
          Có khối: === KỊCH BẢN GỐC CẦN DỊCH LỒNG TIẾNG (DANH SÁCH MẨU JSON ĐẦU VÀO) ===
        - Trường hợp 2: Dịch Hán bóc tách thực thể (LLM 1)
          Có khối: === VĂN BẢN TOÀN BỘ LÔ CHƯƠNG (LIÊN TỤC KHÔNG XUỐNG DÒNG) ===
        - Trường hợp khác (Auditor, Prompt ngắn):
          Gửi nguyên vẹn 1 lần (data_content = None).
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
                note = "(Toàn bộ danh sách câu thoại kịch bản JSON gốc đã được cung cấp ở Tin nhắn 1. Hãy dịch từng câu thoại theo đúng thứ tự mảng JSON đã gửi)."
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
                note = "(Nội dung văn bản JSON đã được cung cấp ở Tin nhắn 1 trước đó. Hãy phân tích và bóc tách thực thể dựa trên dữ liệu văn bản đã nhận)."
                instruction_prompt = f"{before.strip()}\n\n{marker_dh}\n{note}\n\n{remaining}".strip()
                return data_json, instruction_prompt

        # 3. Mặc định: Không tách nếu không có marker hoặc prompt ngắn
        return None, prompt

    @classmethod
    async def generate_content(
        cls,
        prompt: str,
        data_content: Optional[str] = None,
        prompt_file_path: Optional[str] = None
    ) -> str:
        """
        Gửi prompt lên Gemini Web và nhận văn bản phản hồi thông qua Browser Worker ngầm.
        Tự động điều phối xuyên luồng (cross-thread) an toàn sang dedicated browser loop.
        Tự động đóng gói tách nội dung JSON ra gửi Lần 1 và yêu cầu/thực thể gửi Lần 2 chống tràn ký tự.
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
        return fut.result(timeout=360)

    @classmethod
    async def _do_generate_content(
        cls,
        prompt: str,
        data_content: Optional[str] = None,
        prompt_file_path: Optional[str] = None,
        retry_count: int = 0
    ) -> str:
        """Thực thi dịch trên chính event loop sở hữu Playwright context."""
        # Đảm bảo worker đã được khởi chạy
        if cls._context is None or cls._warm_page is None or cls._warm_page.is_closed():
            logger.info("Khởi tạo / Khôi phục phiên Gemini Web Worker...")
            success = await cls._do_init_worker()
            if not success or cls._context is None:
                raise RuntimeError(
                    "Chưa đăng nhập Gemini Web hoặc không thể khởi chạy Chromium! "
                    "Vui lòng vào Cài đặt -> Bấm 'Đăng nhập Gemini Web' để kết nối tài khoản Google."
                )

        from pathlib import Path
        from app.config import settings
        selected_model = getattr(settings, "GEMINI_WEB_MODEL", "flash").lower().strip()
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

        # Thực thi dịch trên DUY NHẤT 1 TAB ĐÃ MỞ SẴN (_warm_page), không tạo tab mới
        if cls._lock is None:
            cls._lock = asyncio.Lock()
        async with cls._lock:
            page = cls._warm_page
            temp_created_file = None
            try:
                # 1. Luôn đảm bảo bắt đầu trên một đoạn chat mới tinh bằng cách bấm nút 'Cuộc trò chuyện mới'
                await cls._open_new_chat_ui(page)

                # 2. Chờ ô nhập văn bản xuất hiện
                box = await page.wait_for_selector(
                    'rich-textarea div[contenteditable="true"], div[role="textbox"]',
                    timeout=20000
                )

                # 3. THIẾT LẬP CẤP ĐỘ TƯ DUY (THINKING LEVEL):
                # Google Gemini Web tự động đặt mức mặc định là "Thấp" (low) khi sang cuộc trò chuyện mới.
                # Nếu cấu hình là "Cao" (high) hoặc "Trung bình" (medium): BẮT BUỘC thao tác UI chọn lại mức tương ứng!
                # Nếu cấu hình là "Thấp" (low): bỏ qua hoàn toàn, không thao tác UI vì đã là mặc định sẵn.
                if thinking_level in ("high", "medium"):
                    logger.info(f"[*] Thiết lập Cấp độ tư duy sang '{thinking_level.upper()}' cho cuộc trò chuyện mới...")
                    applied = await cls._apply_thinking_level_ui(page, thinking_level)
                    if applied:
                        cls._current_thinking_level = thinking_level
                    await asyncio.sleep(0.5)
                    box = await page.wait_for_selector(
                        'rich-textarea div[contenteditable="true"], div[role="textbox"]',
                        timeout=20000
                    )

                # 4. TỰ ĐỘNG BÓC TÁCH NỘI DUNG (ND) JSON RA KHỎI YÊU CẦU NẾU CHƯA ĐƯỢC TÁCH SẴN
                actual_data = data_content
                actual_prompt = prompt
                if not actual_data:
                    actual_data, actual_prompt = cls._extract_content_and_instructions(prompt)

                has_attached_file = False
                if actual_data and actual_data.strip():
                    from app.config import BASE_DIR
                    temp_dir = BASE_DIR / "database"
                    temp_dir.mkdir(parents=True, exist_ok=True)
                    temp_json_path = temp_dir / f"gemini_input_{int(time.time()*1000)}.json"

                    try:
                        # 4.1. Đóng gói dữ liệu thành tệp JSON
                        with open(temp_json_path, "w", encoding="utf-8") as f:
                            try:
                                parsed_obj = json.loads(actual_data)
                                json.dump(parsed_obj, f, ensure_ascii=False, indent=2)
                            except Exception:
                                f.write(actual_data)

                        # Sao chép file vào Clipboard Windows (CF_HDROP)
                        copied_ok = cls._copy_file_to_windows_clipboard(str(temp_json_path))
                        logger.info(f"📁 [Gemini Web] Copy tệp JSON vào Clipboard (CF_HDROP): {copied_ok}...")
                        print(f"📁 [Gemini Web] Copy tệp JSON vào Clipboard (CF_HDROP): {copied_ok}...", flush=True)

                        # Thoát mọi menu/popup nếu có bằng phím Escape
                        await page.keyboard.press("Escape")
                        await asyncio.sleep(0.2)

                        # Click trực tiếp vào ô chat để kích hoạt editor
                        box = await page.wait_for_selector(
                            'rich-textarea div[contenteditable="true"], div[role="textbox"]',
                            timeout=15000
                        )
                        await box.click()
                        await asyncio.sleep(0.4)

                        # Nhấn Control+v để dán file vào ô chat
                        await page.keyboard.press("Control+v")
                        logger.info("📋 [Gemini Web] Đã nhấn Ctrl+V dán tệp JSON vào ô chat!")
                        print("📋 [Gemini Web] Đã nhấn Ctrl+V dán tệp JSON vào ô chat!", flush=True)
                        await asyncio.sleep(2.5)
                        has_attached_file = True
                    except Exception as attach_err:
                        logger.warning(f"⚠️ [Gemini Web] Không thể đính kèm file ({attach_err}), tự động chuyển sang dán văn bản trực tiếp...")
                        print(f"⚠️ [Gemini Web] Không thể đính kèm file, chuyển sang dán văn bản...", flush=True)
                    finally:
                        try:
                            temp_json_path.unlink(missing_ok=True)
                        except Exception:
                            pass

                # 4.2. NHẬP CÂU LỆNH YÊU CẦU / PROMPT VÀO KHUNG CHAT
                if actual_data and not has_attached_file:
                    effective_prompt = f"{actual_prompt}\n\n=== DỮ LIỆU ĐẦU VÀO ===\n{actual_data}"
                else:
                    effective_prompt = actual_prompt

                logger.info(f"📤 [Gemini Web] Nhập câu lệnh yêu cầu ({len(effective_prompt)} ký tự)...")
                print(f"📤 [Gemini Web] Nhập câu lệnh yêu cầu ({len(effective_prompt)} ký tự)...", flush=True)

                box = await page.wait_for_selector(
                    'rich-textarea div[contenteditable="true"], div[role="textbox"]',
                    timeout=15000
                )
                await box.click()
                await asyncio.sleep(0.3)

                # Nhập prompt chỉ thị trực tiếp bằng CDP injection (không đè lên file đính kèm)
                await page.keyboard.insert_text(effective_prompt)
                await asyncio.sleep(0.5)

                # Đếm số lượng phản hồi hiện tại trước khi bấm Gửi
                prev_response_count = await page.evaluate(
                    "() => document.querySelectorAll('model-response').length"
                )

                # 4.3. BẤM NÚT GỬI
                send_btn = await page.query_selector('button[aria-label*="Gửi"], button[aria-label*="Send"]')
                if send_btn:
                    await send_btn.click()
                else:
                    await box.focus()
                    await box.press("Enter")

                for _ in range(5):
                    await asyncio.sleep(0.8)
                    is_cleared = await page.evaluate('''() => {
                        const tb = document.querySelector('rich-textarea div[contenteditable="true"], div[role="textbox"]');
                        return !tb || (tb.innerText || '').trim().length === 0;
                    }''')
                    if is_cleared:
                        break
                    send_retry = await page.query_selector('button[aria-label*="Gửi"], button[aria-label*="Send"]')
                    if send_retry:
                        await send_retry.click()
                    else:
                        await box.focus()
                        await box.press("Enter")

                await asyncio.sleep(1.0)
                chat_url = page.url
                cls._thinking_synced_url = chat_url
                print(f"👉 [Gemini Web] Link cuộc trò chuyện: {chat_url}", flush=True)

                # BƯỚC 3: CHỜ AI SINH XONG HOÀN TOÀN
                # Kiểm tra trực tiếp vào KHUNG CHAT (nút Stop hình vuông [■] biến mất, nút Micro mic hoặc Gửi quay lại)
                # VÀ thanh công cụ chân trang của model-response (ngoài code block) có nút Sao chép
                await asyncio.sleep(3.0)

                max_wait_secs = 600  # Cho phép chờ AI hoàn thành trọn vẹn, không bốc non
                start_time = asyncio.get_event_loop().time()
                last_log_time = 0
                last_text_len = 0
                stable_ticks = 0

                while (asyncio.get_event_loop().time() - start_time) < max_wait_secs:
                    status = await page.evaluate('''(prevRespCount) => {
                        const responses = document.querySelectorAll('model-response');
                        if (responses.length <= prevRespCount) {
                            return { isReady: false, isGenerating: true };
                        }
                        const lastResp = responses[responses.length - 1];

                        // 1. KIỂM TRA TRỰC TIẾP TẠI KHUNG CHAT:
                        // Khi đang render: bên phải khung chat có nút Dừng (Stop [■]).
                        // Quét tất cả button xem có nút Stop/Dừng hoặc SVG chứa hình vuông [■] không:
                        const allButtons = Array.from(document.querySelectorAll('button, div[role="button"]'));
                        const isStopButtonPresent = allButtons.some(b => {
                            const aria = (b.getAttribute('aria-label') || '').toLowerCase();
                            const testId = (b.getAttribute('data-test-id') || '').toLowerCase();
                            const html = b.innerHTML.toLowerCase();
                            const txt = (b.innerText || '').toLowerCase();
                            return aria.includes('dừng') || aria.includes('stop') ||
                                   testId.includes('stop') ||
                                   html.includes('<rect') || html.includes('rect ') ||
                                   txt === 'stop';
                        });

                        // 2. KIỂM TRA NÚT COPY Ở CHÂN TRANG PHẢN HỒI (NGOÀI CODE BLOCK):
                        // Nút Copy của code block nằm trong pre / code-block -> loại trừ hoàn toàn!
                        const nonCodeButtons = Array.from(lastResp.querySelectorAll('button')).filter(b => {
                            return !b.closest('pre, code, [class*="code-block"], [class*="codeblock"]');
                        });

                        const hasFooterCopy = nonCodeButtons.some(b => {
                            const aria = (b.getAttribute('aria-label') || '').toLowerCase();
                            const testId = (b.getAttribute('data-test-id') || '').toLowerCase();
                            const title = (b.getAttribute('title') || '').toLowerCase();
                            const mat = b.querySelector('mat-icon, span, svg, i');
                            const matText = (mat ? (mat.innerText || mat.textContent || '') : '').toLowerCase();
                            return aria.includes('sao chép') || aria.includes('copy') ||
                                   testId.includes('copy') || title.includes('sao chép') || title.includes('copy') ||
                                   matText.includes('content_copy');
                        });

                        const currentLen = (lastResp.innerText || '').trim().length;

                        // AI CHỈ HOÀN THÀNH KHI:
                        // - Khung chat KHÔNG còn nút Stop [■]
                        // - Thanh công cụ chân trang ĐÃ CÓ nút Sao chép
                        const isReady = (!isStopButtonPresent) && hasFooterCopy;

                        return {
                            isReady: isReady,
                            isStopPresent: isStopButtonPresent,
                            hasFooterCopy: hasFooterCopy,
                            currentLen: currentLen
                        };
                    }''', prev_response_count)

                    elapsed = int(asyncio.get_event_loop().time() - start_time)

                    # Kiểm tra độ ổn định văn bản: nếu isReady thỏa mãn và độ dài văn bản không đổi trong 2 lần kiểm tra liên tiếp
                    if status.get("isReady"):
                        curr_len = status.get("currentLen", 0)
                        if curr_len > 10 and curr_len == last_text_len:
                            stable_ticks += 1
                            if stable_ticks >= 2:
                                await asyncio.sleep(1.0)
                                break
                        else:
                            stable_ticks = 1
                            last_text_len = curr_len
                    else:
                        stable_ticks = 0
                        last_text_len = status.get("currentLen", 0)

                    if elapsed - last_log_time >= 5:
                        last_log_time = elapsed
                        logger.info(f"⏳ [Gemini Web] AI đang suy nghĩ / sinh kết quả... ({elapsed}s)")
                        print(f"⏳ [Gemini Web] AI đang suy nghĩ / sinh kết quả... ({elapsed}s)", flush=True)

                    await asyncio.sleep(1.5)

                # BƯỚC 4: BẤM NÚT SAO CHÉP CHÂN TRANG CỦA CÂU TRẢ LỜI
                extracted_text = ""

                # 4.1. Xóa clipboard trước để đảm bảo không nhận nhầm nội dung cũ
                try:
                    await page.evaluate("() => navigator.clipboard.writeText('')")
                except Exception:
                    pass

                # 4.2. Bấm nút Sao chép ở thanh công cụ dưới cùng (NGOÀI CODE BLOCK)
                clicked_copy = await page.evaluate('''() => {
                    const responses = document.querySelectorAll('model-response');
                    if (!responses.length) return false;
                    const lastResp = responses[responses.length - 1];

                    // Lọc các nút nằm NGOÀI khối code (pre, code-block)
                    const nonCodeButtons = Array.from(lastResp.querySelectorAll('button')).filter(b => {
                        return !b.closest('pre, code, [class*="code-block"], [class*="codeblock"]');
                    });

                    const target = nonCodeButtons.find(b => {
                        const aria = (b.getAttribute('aria-label') || '').toLowerCase();
                        const testId = (b.getAttribute('data-test-id') || '').toLowerCase();
                        const title = (b.getAttribute('title') || '').toLowerCase();
                        const mat = b.querySelector('mat-icon, span, svg, i');
                        const matText = (mat ? (mat.innerText || mat.textContent || '') : '').toLowerCase();
                        return aria.includes('sao chép') || aria.includes('copy') ||
                               testId.includes('copy') || title.includes('sao chép') || title.includes('copy') ||
                               matText.includes('content_copy');
                    });

                    if (!target) return false;
                    target.scrollIntoView({ block: 'nearest' });
                    target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
                    target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
                    target.click();
                    return true;
                }''')

                if clicked_copy:
                    await asyncio.sleep(0.6)
                    # Lấy văn bản từ clipboard của Chromium
                    try:
                        extracted_text = await page.evaluate("() => navigator.clipboard.readText()")
                    except Exception as clip_err:
                        logger.debug(f"Không đọc được qua navigator.clipboard: {clip_err}")

                    # Fallback lấy qua Windows Clipboard (ctypes API Win32 chuẩn 64-bit)
                    if not extracted_text or len(extracted_text.strip()) < 5:
                        extracted_text = cls._get_windows_clipboard()

                # CHỐT CHẶN AN TOÀN: Nếu văn bản vô tình bị rỗng hoặc dính Prompt, lấy trực tiếp từ DOM của lastResp
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
                        logger.warning("⚠️ Phát hiện văn bản copy là Prompt gửi đi! Chuyển sang trích xuất trực tiếp từ model-response...")
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
                    if clean_res and len(clean_res) > 5:
                        logger.info(f"✔ [Gemini Web] Đã sao chép thành công phản hồi ({len(clean_res)} ký tự).")
                        print(f"✔ [Gemini Web] Đã sao chép thành công phản hồi ({len(clean_res)} ký tự).", flush=True)
                        # ĐỔI SANG CUỘC TRÒ CHUYỆN MỚI NGAY KHI XONG REQUEST ĐỂ DỌN SẠCH PHIÊN
                        try:
                            logger.info("[Gemini Web] Hoàn tất request, đang chuyển sang Cuộc trò chuyện mới...")
                            await cls._open_new_chat_ui(page)
                        except Exception:
                            pass
                        return clean_res

                raise RuntimeError("Gemini Web không trả về nội dung nào hoặc không thể lấy kết quả phản hồi qua nút Sao chép!")

            except Exception as web_err:
                err_str = str(web_err)
                if ("Connection closed" in err_str or "Target closed" in err_str or "has no attribute" in err_str) and retry_count < 1:
                    logger.warning(f"⚠️ Phát hiện kết nối trình duyệt bị ngắt ({err_str}). Đang tự động nạp lại worker...")
                    await cls._do_shutdown_worker()
                    await cls._do_init_worker()
                    return await cls._do_generate_content(prompt, data_content=data_content, prompt_file_path=prompt_file_path, retry_count=retry_count + 1)
                logger.error(f"❌ [Gemini Web Worker Lỗi] {web_err}")
                raise RuntimeError(f"Lỗi Gemini Web: {web_err}. Vui lòng thử lại.")
            finally:
                if temp_created_file and temp_created_file.exists():
                    try:
                        temp_created_file.unlink(missing_ok=True)
                    except Exception:
                        pass

        return ""
