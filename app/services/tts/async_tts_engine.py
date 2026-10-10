import asyncio
import aiohttp
import base64
import json
import logging
import os
import random
import re
import time
import threading
import uuid
from typing import Dict, List, Optional, Tuple, Any, Callable
from urllib.parse import urlencode

from app.services.tts.proxy_manager import proxy_manager
from app.config import settings
from app.services.capcut_tts_api.models import DeviceConfig
from app.services.capcut_tts_api.signer import (
    base_headers,
    compact_json,
    common_query,
    make_sign_header,
    make_tts_payload_sign,
)
from app.services.capcut_tts_api.config import BASE_URL as CAPCUT_BASE_URL

logger = logging.getLogger(__name__)

from dataclasses import dataclass, field
import secrets

# ─── Cấu hình TTS đọc từ .env (qua settings - 1 nguồn sự thật duy nhất) ──────
# Để thay đổi: chỉnh sửa file .env, KHÔNG hardcode trực tiếp vào đây.
def _get_tiktok_session() -> str:
    """Luôn đọc runtime từ settings (phản ánh .env hiện tại ngay cả khi reload)."""
    return settings.TIKTOK_SESSION_ID or "410bfa37bdc185e1c6da82e1afb48409"

def _get_capcut_cookie() -> str:
    """Luôn đọc runtime từ settings, fallback về TIKTOK_SESSION_ID nếu chưa set."""
    return settings.CAPCUT_COOKIE or settings.TIKTOK_SESSION_ID or "410bfa37bdc185e1c6da82e1afb48409"

# Alias lấy giá trị runtime chuẩn xác
DEFAULT_TIKTOK_SESSION_ID: str = _get_tiktok_session()
DEFAULT_CAPCUT_COOKIE: str = _get_capcut_cookie()

# Endpoint TikTok TTS API (cố định theo giao thức ByteDance)
TIKTOK_API_ENDPOINT = "https://api16-normal-v4.tiktokv.com/media/api/text/speech/invoke/"

TIKTOK_USER_AGENTS = [
    "com.zhiliaoapp.musically/2022600030 (Linux; U; Android 7.1.2; es_ES; SM-G988N; Build/NRD90M;tt-ok/3.12.13.1)",
    "com.zhiliaoapp.musically/2022500020 (Linux; U; Android 10; en_US; Pixel 4 XL; Build/QQ3A.200805.001;tt-ok/3.12.13.1)",
    "com.zhiliaoapp.musically/2022400010 (Linux; U; Android 9; vi_VN; Redmi Note 8; Build/PKQ1.190616.001;tt-ok/3.12.13.1)",
    "com.ss.android.ugc.trill/260103 (Linux; U; Android 11; en_US; SM-G998B; Build/RP1A.200720.012;tt-ok/3.12.13.1)",
]

# AID 1233 và 1180 hoạt động 100% ổn định (AID 1340 bị ByteDance chặn lỗi 1)
TIKTOK_AIDS = ["1233", "1180"]

def generate_fake_vietnam_ip() -> str:
    """Tạo IP ngụy trang từ các dải ISP lớn của Việt Nam (Viettel, FPT, VNPT) chuẩn AIRead."""
    isp_ranges = [
        ("115.72.", 1, 254, 1, 254),     # Viettel
        ("171.224.", 1, 254, 1, 254),    # Viettel
        ("118.69.", 1, 254, 1, 254),     # FPT Telecom
        ("1.52.", 1, 254, 1, 254),       # FPT Telecom
        ("14.160.", 1, 254, 1, 254),     # VNPT
        ("123.24.", 1, 254, 1, 254),     # VNPT
        ("42.112.", 1, 254, 1, 254),     # Mobifone
    ]
    prefix, a_min, a_max, b_min, b_max = random.choice(isp_ranges)
    return f"{prefix}{random.randint(a_min, a_max)}.{random.randint(b_min, b_max)}"


# ─────────────────────────────────────────────────────────────────────────────
# DirectIPManager  — Xẻng kim cương (Gate-Lock Pattern chuẩn AIRead)
# ─────────────────────────────────────────────────────────────────────────────

class DirectIPManager:
    """
    Xẻng kim cương (Direct IP) - Gate-Lock pattern chuẩn AIRead:
    • Khi hỏng (Timeout/RateLimit):
        - Nếu có Proxy: đánh dấu nghỉ 45s để ép chuyển sang Proxy bảo vệ IP máy.
        - Nếu KHÔNG có Proxy: chỉ cooldown ngắn 2s để xả tải, không làm tê liệt hệ thống.
    • Trong cooldown: is_in_cooldown()=True → workers BỎ QUA ngay, đi lấy proxy (nếu có).
    • Cooldown hết → CHỈ 1 worker vào kiểm tra qua _gate_lock:
        ✔ Dùng được → mark_success() → _verified_ok=True → các luồng sau tự đến lấy slot bình thường.
        ✘ Hỏng     → mark_failed() → gia hạn cooldown → các luồng kia KHÔNG cần check thêm.
    • _verified_ok=True: xẻng đang tốt, workers lấy slot semaphore tự do.
    """

    def __init__(self, max_concurrent: int = 96, cooldown_seconds: float = 45.0):
        self.max_concurrent = max_concurrent
        self.cooldown_seconds = cooldown_seconds
        self.cooldown_until: float = 0.0
        self._verified_ok: bool = True     # Mặc định ban đầu IP máy hoạt động tốt
        self._semaphores: Dict[Any, asyncio.Semaphore] = {}
        self._gate_locks: Dict[Any, asyncio.Lock] = {}
        self._lock = threading.Lock()

    def get_semaphore(self) -> asyncio.Semaphore:
        """Lấy Semaphore đồng bộ đúng event loop đang chạy của luồng hiện tại."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        with self._lock:
            if loop not in self._semaphores:
                self._semaphores[loop] = asyncio.Semaphore(self.max_concurrent)
            return self._semaphores[loop]

    def get_gate_lock(self) -> asyncio.Lock:
        """Lấy Gate Lock đồng bộ đúng event loop đang chạy của luồng hiện tại."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        with self._lock:
            if loop not in self._gate_locks:
                self._gate_locks[loop] = asyncio.Lock()
            return self._gate_locks[loop]

    def is_in_cooldown(self) -> bool:
        return time.time() < self.cooldown_until

    def mark_failed(self, reason: str = "timeout"):
        """Xẻng hỏng: nếu không có proxy thì chỉ giãn 2s tránh tê liệt, có proxy thì nghỉ 45s."""
        cd = 2.0 if not proxy_manager.has_proxies else self.cooldown_seconds
        self.cooldown_until = time.time() + cd
        self._verified_ok = False

    def mark_success(self):
        """Xẻng hoạt động tốt: reset cooldown, báo bọn sau có thể vào dùng."""
        self.cooldown_until = 0.0
        self._verified_ok = True

    async def try_acquire(self) -> bool:
        if self.is_in_cooldown():
            return False

        sem = self.get_semaphore()
        gate_lock = self.get_gate_lock()

        if self._verified_ok:
            try:
                await asyncio.wait_for(sem.acquire(), timeout=0.05)
                return True
            except (asyncio.TimeoutError, Exception):
                return False

        if gate_lock.locked():
            return False

        try:
            async with gate_lock:
                if self.is_in_cooldown():
                    return False
                if self._verified_ok:
                    try:
                        await asyncio.wait_for(sem.acquire(), timeout=0.05)
                        return True
                    except Exception:
                        return False
                try:
                    await asyncio.wait_for(sem.acquire(), timeout=0.1)
                    self._verified_ok = True
                    return True
                except (asyncio.TimeoutError, Exception):
                    return False
        except Exception:
            return False

    def release(self):
        try:
            sem = self.get_semaphore()
            sem.release()
        except Exception:
            pass


GLOBAL_DIRECT_IP_MANAGER = DirectIPManager(
    max_concurrent=max(32, getattr(settings, "TTS_MAX_WORKERS", 96)),
    cooldown_seconds=45.0
)


class AsyncTTSEngine:
    """
    Động cơ tổng hợp giọng nói Bất Đồng Bộ Chuẩn 100% AIRead:
    - Dedicated Workers Pool (Luồng xử lý không nghỉ).
    - Hàng đợi ưu tiên Priority Queue: Câu lỗi bị trừ 100.000.000 điểm ưu tiên để nhảy thẳng lên ĐẦU hàng đợi.
    - Xẻng kim cương (Direct IP) kết hợp Gate-Lock: Bảo vệ IP máy khi bị rate-limit, tự động chuyển proxy và tự phục hồi khi hết cooldown.
    - Fast-Track Rescue: Câu bị lỗi >= 2 lần tự động ưu tiên dùng Direct IP máy để giải quyết dứt điểm.
    """

    @classmethod
    async def fetch_tiktok_chunk_async(
        cls,
        session: aiohttp.ClientSession,
        text: str,
        voice_code: str = "BV074_streaming",
        session_id: Optional[str] = None,
        proxy: Optional[str] = None,
        timeout_sec: float = 15.0,
    ) -> Optional[bytes]:
        """Tải 1 chunk âm thanh TikTok TTS với timeout 15s và tự động xoay AID an toàn."""
        base_sess_id = session_id or _get_tiktok_session()
        ua = random.choice(TIKTOK_USER_AGENTS)
        aid = random.choice(TIKTOK_AIDS)
        fake_ip = generate_fake_vietnam_ip()

        headers = {
            "User-Agent": ua,
            "Cookie": f"sessionid={base_sess_id}",
            "X-Forwarded-For": fake_ip,
            "Client-IP": fake_ip,
            "X-Real-IP": fake_ip,
            "X-Client-IP": fake_ip,
            "X-Remote-IP": fake_ip,
        }
        params = {
            "text_speaker": voice_code,
            "req_text": text,
            "speaker_map_type": "0",
            "aid": aid,
        }
        try:
            async with session.post(
                TIKTOK_API_ENDPOINT,
                params=params,
                headers=headers,
                proxy=proxy,
                timeout=aiohttp.ClientTimeout(total=timeout_sec),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    code = data.get("status_code", -1)
                    if code == 0 and "data" in data and "v_str" in data["data"]:
                        audio_bytes = base64.b64decode(data["data"]["v_str"])
                        if len(audio_bytes) > 100:
                            return audio_bytes
                    elif code == 1:
                        # Thử lại nhanh với AID thay thế
                        alt_aid = "1233" if aid != "1233" else "1180"
                        params["aid"] = alt_aid
                        async with session.post(
                            TIKTOK_API_ENDPOINT,
                            params=params,
                            headers=headers,
                            proxy=proxy,
                            timeout=aiohttp.ClientTimeout(total=timeout_sec),
                        ) as resp_retry:
                            if resp_retry.status == 200:
                                retry_data = await resp_retry.json(content_type=None)
                                if retry_data.get("status_code") == 0 and "data" in retry_data and "v_str" in retry_data["data"]:
                                    audio_bytes = base64.b64decode(retry_data["data"]["v_str"])
                                    if len(audio_bytes) > 100:
                                        return audio_bytes
        except Exception as e:
            logger.debug(f"fetch_tiktok_chunk_async exception: {e}")

        return None

    @classmethod
    async def fetch_edge_chunk_async(
        cls,
        text: str,
        voice_code: str = "vi-VN-HoaiMyNeural",
        max_retries: int = 3,
    ) -> Optional[bytes]:
        """Tải 1 chunk âm thanh Edge TTS (Hoài My, Nam Minh...) siêu tốc qua luồng in-memory (Auto-Retry 3 lần)."""
        import io, edge_tts
        actual_voice = "vi-VN-HoaiMyNeural" if "hoaimy" in voice_code.lower() else voice_code

        for attempt in range(1, max_retries + 1):
            try:
                comm = edge_tts.Communicate(text, actual_voice)
                buf = io.BytesIO()
                async for chunk in comm.stream():
                    if chunk.get("type") == "audio":
                        buf.write(chunk.get("data", b""))
                val = buf.getvalue()
                if len(val) > 100:
                    return val
            except Exception as e:
                if attempt == max_retries:
                    logger.error(f"[Edge TTS] Thất bại sau {max_retries} lần thử ({voice_code}): {e}")
                else:
                    await asyncio.sleep(0.3)
        return None

    @classmethod
    async def fetch_capcut_chunk_async(
        cls,
        session: aiohttp.ClientSession,
        text: str,
        voice_code: str = "BV074_streaming",
        cookie: Optional[str] = None,
        proxy: Optional[str] = None,
        timeout_sec: float = 18.0,
    ) -> Optional[bytes]:
        """Tải 1 chunk âm thanh CapCut Cloud SAMI (Create -> Query Polling -> Download CDN)."""
        from app.services.capcut_tts_api import CapCutClient
        user_cookie = cookie or _get_capcut_cookie()
        dev = DeviceConfig.create_random()
        client = CapCutClient(device=dev, cookie=user_cookie)

        try:
            url, headers, body_text = client.build_tts_new_request([text], voice=voice_code)
            async with session.post(
                url,
                headers=headers,
                data=body_text.encode("utf-8"),
                proxy=proxy,
                timeout=aiohttp.ClientTimeout(total=8.0),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
                tasks = (data.get("data") or {}).get("tasks") or []
                if not tasks:
                    return None
                task_id = tasks[0].get("id")
                token = tasks[0].get("token")
                if not task_id or not token:
                    return None

            # Polling query task status cho đến khi có speech_url
            start_poll = time.time()
            max_poll_time = timeout_sec - 4.0
            while (time.time() - start_poll) < max_poll_time:
                await asyncio.sleep(0.5)
                q_url, q_headers, q_body = client.build_query_request(task_id, token, mode="tts")
                async with session.post(
                    q_url,
                    headers=q_headers,
                    data=q_body.encode("utf-8"),
                    proxy=proxy,
                    timeout=aiohttp.ClientTimeout(total=6.0),
                ) as q_resp:
                    if q_resp.status != 200:
                        continue
                    q_data = await q_resp.json(content_type=None)
                    q_tasks = (q_data.get("data") or {}).get("tasks") or []
                    if not q_tasks:
                        continue
                    q_task = q_tasks[0]
                    status = q_task.get("status")
                    if status in ("success", "succeed"):
                        payload_raw = q_task.get("payload") or "{}"
                        payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
                        subs = payload.get("audio_subtitles") or []
                        if subs and subs[0].get("speech_url"):
                            speech_url = subs[0]["speech_url"]
                            async with session.get(
                                speech_url,
                                proxy=proxy,
                                timeout=aiohttp.ClientTimeout(total=10.0),
                            ) as dl_resp:
                                if dl_resp.status == 200:
                                    content = await dl_resp.read()
                                    if len(content) > 100:
                                        return content
                        return None
                    elif status == "failed":
                        return None
        except Exception as e:
            logger.debug(f"fetch_capcut_chunk_async exception: {e}")

        return None

    @classmethod
    async def batch_synthesize_async(
        cls,
        dialogues_data: List[Tuple[int, str]],  # [(dialogue_id, text_to_speak), ...]
        engine: str = "tiktok",
        voice_code: str = "BV074_streaming",
        session_id: Optional[str] = None,
        concurrency_limit: int = -1,  # -1 = đọc từ settings.TTS_MAX_WORKERS (.env)
        max_retries_per_item: int = 5,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        log_callback: Optional[Callable[[str, str], None]] = None,
        is_cancelled_callback: Optional[Callable[[], bool]] = None,
    ) -> Dict[int, bytes]:
        """
        Quy trình xử lý Batch TTS chuẩn 100% Rotating Engine AIRead:
        - Task Queue dạng Tuple: (priority, attempts, dialogue_id, text_to_speak, current_voice)
        - Dedicated Workers xử lý liên tục theo nguyên tắc:
            1. Ưu tiên kênh Direct IP (Xẻng kim cương) nếu khả dụng hoặc task kẹt attempts >= 2.
            2. Nếu Direct IP bận/cooldown -> Tự động chuyển qua Proxy xoay vòng.
            3. Nếu task lỗi -> Trừ 100.000.000 điểm Priority để nhảy ngay lên ĐẦU hàng đợi cho luồng kế tiếp cứu ngay!
        """
        # Đọc concurrency từ .env nếu không truyền vào
        if concurrency_limit <= 0:
            concurrency_limit = max(4, settings.TTS_MAX_WORKERS)

        # Đọc session từ .env nếu không truyền
        if not session_id:
            session_id = _get_tiktok_session() if engine.lower() == "tiktok" else _get_capcut_cookie()

        results_map: Dict[int, bytes] = {}
        total = len(dialogues_data)
        if total == 0:
            return results_map

        task_queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        completed_count = 0

        # Nạp toàn bộ danh sách câu thoại vào Hàng đợi ưu tiên chuẩn AIRead
        # Base priority = 100_000_000 + index
        for idx, (d_id, text) in enumerate(dialogues_data):
            base_prio = 100_000_000 + idx
            await task_queue.put((base_prio, 0, idx, d_id, text, voice_code))

        connector = aiohttp.TCPConnector(
            limit=concurrency_limit * 2,
            limit_per_host=concurrency_limit,
            ssl=False,
            keepalive_timeout=30.0,
            enable_cleanup_closed=True,
        )

        async with aiohttp.ClientSession(connector=connector) as session:
            async def _dedicated_worker(worker_id: int):
                nonlocal completed_count
                current_proxy: Optional[str] = None

                while True:
                    if is_cancelled_callback and is_cancelled_callback():
                        break

                    try:
                        item = await task_queue.get()
                    except asyncio.CancelledError:
                        break

                    priority, attempts, chunk_idx, d_id, text, cur_voice = item

                    try:
                        if is_cancelled_callback and is_cancelled_callback():
                            task_queue.task_done()
                            break

                        # Kiểm tra nếu câu rỗng hoặc chỉ là dấu câu lẻ loi (không có chữ để đọc)
                        if not text or not text.strip() or not any(c.isalnum() for c in text):
                            completed_count += 1
                            try:
                                if progress_callback:
                                    progress_callback(completed_count, total)
                            except Exception:
                                pass
                            continue
                        used_direct = False
                        direct_acquired = False

                        # Fast-Track Rescue: Nếu câu bị lỗi >= 2 lần hoặc proxy không có -> Cứu bằng Direct IP
                        if attempts >= 2 and not GLOBAL_DIRECT_IP_MANAGER.is_in_cooldown():
                            direct_acquired = await GLOBAL_DIRECT_IP_MANAGER.try_acquire()
                            used_direct = direct_acquired

                        if not used_direct:
                            # 1. Thử Direct IP máy trước nếu không cooldown
                            if not GLOBAL_DIRECT_IP_MANAGER.is_in_cooldown():
                                direct_acquired = await GLOBAL_DIRECT_IP_MANAGER.try_acquire()
                                used_direct = direct_acquired

                            # 2. Nếu Direct IP bận/cooldown -> Lấy Proxy xoay vòng
                            if not used_direct:
                                current_proxy = proxy_manager.get_proxy(randomize=True)

                        # Thực hiện gọi TTS
                        t0 = time.time()
                        target_proxy = None if used_direct else current_proxy

                        if "Neural" in cur_voice or "hoaimy" in cur_voice.lower():
                            actual_voice = "vi-VN-HoaiMyNeural" if "hoaimy" in cur_voice.lower() else cur_voice
                            audio_data = await cls.fetch_edge_chunk_async(text=text, voice_code=actual_voice)
                        elif engine.lower() == "capcut" or "multi_" in cur_voice:
                            audio_data = await cls.fetch_capcut_chunk_async(
                                session=session,
                                text=text,
                                voice_code=cur_voice,
                                cookie=session_id or _get_capcut_cookie(),
                                proxy=target_proxy,
                            )
                        else:
                            audio_data = await cls.fetch_tiktok_chunk_async(
                                session=session,
                                text=text,
                                voice_code=cur_voice,
                                session_id=session_id or _get_tiktok_session(),
                                proxy=target_proxy,
                            )

                        elapsed = time.time() - t0

                        # Giải phóng slot Direct IP nếu có mượn
                        if used_direct and direct_acquired:
                            if audio_data and len(audio_data) > 100:
                                GLOBAL_DIRECT_IP_MANAGER.mark_success()
                            else:
                                GLOBAL_DIRECT_IP_MANAGER.mark_failed(reason="timeout/reject")
                            GLOBAL_DIRECT_IP_MANAGER.release()

                        # ── XỬ LÝ KẾT QUẢ ─────────────────────────────────
                        if audio_data and len(audio_data) > 100:
                            results_map[d_id] = audio_data
                            completed_count += 1

                            channel_info = "DIRECT-IP (Xẻng Kim Cương)" if used_direct else f"Proxy [{current_proxy}]"
                            retry_tag = f" (Vá Lần #{attempts})" if attempts > 0 else ""
                            log_msg = f"⚡ [W#{worker_id:02d}] Câu #{chunk_idx + 1:03d}{retry_tag} | {channel_info} | {elapsed:.2f}s | ✔ Hoàn tất"
                            try:
                                if log_callback:
                                    log_callback(log_msg, "emerald" if attempts == 0 else "amber")
                            except Exception:
                                pass
                            try:
                                if progress_callback:
                                    progress_callback(completed_count, total)
                            except Exception:
                                pass
                        else:
                            # 💥 GẶP LỖI: CHÍNH SÁCH ZERO-DROP (Tuyệt đối không bỏ rơi câu thoại)
                            next_retry = attempts + 1
                            high_prio = priority - 100_000_000

                            # Dọn dẹp nhẹ ký tự điều khiển vô hình (nếu có), TUYỆT ĐỐI giữ nguyên 100% từ ngữ và dấu câu tự nhiên
                            clean_text = re.sub(r'[\u200b\u200c\u200d\ufeff\x00-\x08\x0b\x0c\x0e-\x1f]+', '', text).strip()
                            if not clean_text:
                                clean_text = text

                            # Tiếp tục nạp lại vào hàng đợi ưu tiên cao để các luồng sau thử lại qua Proxy/IP khác
                            # TUYỆT ĐỐI giữ đúng Engine và Voice được chỉ định, không bao giờ bỏ rơi câu thoại!
                            try:
                                if log_callback:
                                    log_callback(
                                        f"⚠️ [W#{worker_id:02d}] Câu #{chunk_idx + 1:03d} lỗi lần #{next_retry} -> ĐẨY LÊN ĐẦU QUEUE (Ưu tiên {high_prio}) để luồng kế tiếp thử lại!",
                                        "amber"
                                    )
                            except Exception:
                                pass

                            # Giãn cách nhẹ 0.3s để tránh bão request cho câu kẹt
                            await asyncio.sleep(0.3)
                            await task_queue.put((high_prio, next_retry, chunk_idx, d_id, clean_text, cur_voice))
                    except Exception as e:
                        logger.error(f"Worker #{worker_id} exception on item #{chunk_idx + 1}: {e}")
                    finally:
                        task_queue.task_done()

            # Khởi chạy Worker Pool bất tử
            actual_workers = min(concurrency_limit, total if total > 0 else 1)
            workers = [asyncio.create_task(_dedicated_worker(i + 1)) for i in range(actual_workers)]

            queue_join_task = asyncio.create_task(task_queue.join())
            while not queue_join_task.done():
                if is_cancelled_callback and is_cancelled_callback():
                    queue_join_task.cancel()
                    break
                await asyncio.sleep(0.1)

            for w in workers:
                w.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

        return results_map

    # Alias tương thích ngược cho cả hai cách gọi
    run_batch_priority_queue = batch_synthesize_async



