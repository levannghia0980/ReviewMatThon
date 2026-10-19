import os
import asyncio
import json
import re
import time
import threading
import httpx
from typing import Dict, Any, Optional, List, Tuple

import logging
logger = logging.getLogger("llm_client")

def _safe_add_log(msg: str, level: str = "info"):
    """Ghi log an toàn ra logger hệ thống (tránh lỗi import không tồn tại)"""
    if level == "warning":
        logger.warning(msg)
    elif level == "error":
        logger.error(msg)
    else:
        logger.info(msg)

DEFAULT_SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_CIVIC_INTEGRITY", "threshold": "BLOCK_NONE"}
]

_PACING_LOCK = threading.Lock()
_LAST_GEMINI_REQUEST_TIME = 0.0

def _get_pacing_delay(min_interval: Optional[float] = None) -> float:
    """Điều tiết giãn cách request an toàn trên mọi luồng, mọi máy và mọi event loop."""
    global _LAST_GEMINI_REQUEST_TIME
    if min_interval is None:
        try:
            env_val = os.getenv("GEMINI_PACING_INTERVAL")
            min_interval = max(0.0, float(env_val)) if env_val is not None else 4.1
        except Exception:
            min_interval = 4.1

    with _PACING_LOCK:
        now = time.time()
        elapsed = now - _LAST_GEMINI_REQUEST_TIME
        if elapsed < min_interval:
            delay = min_interval - elapsed
            _LAST_GEMINI_REQUEST_TIME = now + delay
            return delay
        else:
            _LAST_GEMINI_REQUEST_TIME = now
            return 0.0

async def post_gemini_with_retry(
    client: httpx.AsyncClient,
    url: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
    max_retries: int = 8
) -> httpx.Response:
    """
    Tự động gửi request đến Gemini API và xử lý thông minh:
    - Chủ động giãn cách tối thiểu 4.1s giữa các request (Adaptive Pacing) để 100% không chạm mốc 15 RPM.
    - Bắt và thử lại tự động các lỗi mạng/ngắt kết nối.
    - Xử lý thông minh lỗi HTTP 429 (Rate Limit / Quota Exceeded) với delay từ API.
    - Xử lý lỗi 503 Server Busy: Chờ giãn cách tăng dần từ 20s - 80s và tự động fallback sang gemini-2.5-flash nếu 3.1 quá tải.
    - Tự động gắn bộ lọc safetySettings=BLOCK_NONE để loại bỏ hoàn toàn vi phạm chặn văn bản.
    """
    if payload is not None and "safetySettings" not in payload:
        payload["safetySettings"] = DEFAULT_SAFETY_SETTINGS

    last_exception = None
    for attempt in range(1, max_retries + 1):
        # Tự động điều tiết tần suất request (Pacing): Giãn cách tối thiểu 4.1s giữa các request
        # để đảm bảo 100% không vượt quá ngưỡng 15 RPM của Gemini Free Tier, an toàn tuyệt đối trên mọi event loop.
        delay = _get_pacing_delay(min_interval=4.1)
        if delay > 0:
            await asyncio.sleep(delay)

        try:
            resp = await client.post(url, headers=headers, json=payload)
        except Exception as e:
            last_exception = e
            wait_seconds = min(5.0 * attempt, 30.0)
            err_name = type(e).__name__
            err_msg = str(e) or err_name
            log_net = f"⚠️ [LLM MẠNG/TIMEOUT] Gặp lỗi kết nối ({err_name}: {err_msg}). Đang chờ {wait_seconds:.1f}s để thử lại (Lần {attempt}/{max_retries})..."
            print(log_net)
            _safe_add_log(log_net, "warning")

            if attempt < max_retries:
                await asyncio.sleep(wait_seconds)
                continue
            else:
                raise Exception(f"Lỗi kết nối Gemini API sau {max_retries} lần thử: {err_msg}") from e

        if resp.status_code == 200:
            return resp
            
        # Kiểm tra nếu dính lỗi 429 Quota Exceeded / Rate Limit
        if resp.status_code == 429 or "RESOURCE_EXHAUSTED" in resp.text or "Quota exceeded" in resp.text:
            wait_seconds = 8.0  # Chờ ngắn hơn để nhanh thử lại hoặc xoay key
            
            try:
                err_data = resp.json()
                details = err_data.get("error", {}).get("details", [])
                for d in details:
                    if "retryDelay" in d:
                        delay_str = d["retryDelay"]
                        num = float(re.sub(r"[^\d.]", "", delay_str))
                        if num > 0:
                            wait_seconds = min(num + 1.0, 15.0)  # Cắt tối đa 15s chờ
                            break
            except Exception:
                pass
                
            m_match = re.search(r"/models/([^:]+):", url)
            model_name = m_match.group(1) if m_match else "Gemini"
            log_429 = f"⚠️ [LLM 429 Rate Limit] Google API báo chạm hạn mức request/token ({model_name}). Đang tự động thử lại sau {wait_seconds:.1f}s (Lần {attempt}/{max_retries})..."
            try:
                print(log_429)
            except Exception:
                pass
            _safe_add_log(log_429, "warning")
            if attempt < max_retries:
                await asyncio.sleep(wait_seconds)
            else:
                return resp
        elif resp.status_code == 503:
            # Đợi từ từ: Lần 1: 20s, Lần 2: 35s, Lần 3: 50s, Lần 4: 65s (tối đa 80s)
            wait_s = min(20.0 + (attempt - 1) * 15.0, 80.0)
            m_match = re.search(r"/models/([^:]+):", url)
            model_name = m_match.group(1) if m_match else "Gemini"

            log_503 = f"⚠️ [LLM 503 Server Busy] Google AI Studio đang quá tải ({model_name} High Demand). Đang chờ từ từ {wait_s:.0f}s để xả tải và thử lại ({attempt}/{max_retries})..."
            try:
                print(log_503)
            except Exception:
                pass
            _safe_add_log(log_503, "warning")

            # Fallback tự động thông minh: Nếu model 3.1 bị quá tải 503 từ lần 3 trở đi, tự động đổi URL sang gemini-2.5-flash
            if attempt >= 3 and "3.1" in url:
                fallback_url = re.sub(r"gemini-[^:]+", "gemini-2.5-flash", url)
                if fallback_url != url:
                    url = fallback_url
                    log_fb = "🔄 [LLM Fallback] gemini-3.1-flash-lite quá tải liên tục ➔ Tự động chuyển sang gemini-2.5-flash ổn định để hoàn thành lô!"
                    print(log_fb, flush=True)
                    _safe_add_log(log_fb, "emerald")

            if attempt < max_retries:
                await asyncio.sleep(wait_s)
            else:
                return resp
        else:
            # Lỗi khác (400, 500...), thử lại với exponential backoff ngắn
            log_err = f"⚠️ [LLM HTTP {resp.status_code}] Gặp lỗi API: {resp.text[:150]}... Đang chờ 5s để thử lại ({attempt}/{max_retries})."
            print(log_err)
            _safe_add_log(log_err, "warning")
            await asyncio.sleep(5.0)
            
    return resp


async def post_openrouter_with_retry(
    client: httpx.AsyncClient,
    url: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
    max_retries: int = 6
) -> httpx.Response:
    """
    Gửi request đến OpenRouter API với auto-retry thông minh:
    - Tự đọc retry_after_seconds từ response 429 và chờ đúng thời gian yêu cầu.
    - Retry tối đa max_retries lần cho các lỗi mạng và 429.
    """
    for attempt in range(1, max_retries + 1):
        try:
            resp = await client.post(url, headers=headers, json=payload)
        except Exception as e:
            wait_s = min(5.0 * attempt, 30.0)
            log_net = f"⚠️ [OpenRouter MẠNG] Lỗi kết nối: {e}. Chờ {wait_s:.0f}s thử lại ({attempt}/{max_retries})..."
            print(log_net)
            _safe_add_log(log_net, "warning")
            if attempt < max_retries:
                await asyncio.sleep(wait_s)
                continue
            raise

        if resp.status_code == 200:
            return resp

        if resp.status_code == 429:
            # Đọc retry_after_seconds từ response OpenRouter
            wait_s = 10.0
            try:
                err_data = resp.json()
                retry_s = err_data.get("error", {}).get("metadata", {}).get("retry_after_seconds")
                if retry_s and float(retry_s) > 0:
                    wait_s = float(retry_s) + 2.0  # Thêm 2s buffer
            except Exception:
                pass
            wait_s = min(wait_s, 60.0)
            log_429 = f"⚠️ [OpenRouter 429] Rate Limit Free Tier. Chờ {wait_s:.0f}s thử lại ({attempt}/{max_retries})..."
            print(log_429)
            _safe_add_log(log_429, "warning")
            if attempt < max_retries:
                await asyncio.sleep(wait_s)
                continue
            return resp
        else:
            # Lỗi khác
            log_err = f"⚠️ [OpenRouter HTTP {resp.status_code}] {resp.text[:200]}... ({attempt}/{max_retries})"
            print(log_err)
            _safe_add_log(log_err, "warning")
            if attempt < max_retries:
                await asyncio.sleep(5.0)
                continue
            return resp

    return resp


async def post_grok_local_with_retry(
    client: httpx.AsyncClient,
    url: str,
    payload: Dict[str, Any],
    max_retries: int = 3,
    timeout: float = 300.0
) -> Dict[str, Any]:
    """
    Gửi request tới Grok Web Automation Server (cổng 8020) với auto-retry và xử lý thân thiện.
    Trả về dict kết quả hoặc ném ngoại lệ rõ ràng nếu server chưa bật.
    """
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = await client.post(url, json=payload, timeout=timeout)
        except httpx.ConnectError as e:
            err_msg = f"❌ [Grok Local 8020] Không thể kết nối tới Grok Web Server tại {url}. Vui lòng mở file 'Run_Grok_Server.bat' hoặc chạy 'python tools/grok_server/server.py' để khởi động server Grok trước!"
            print(err_msg)
            _safe_add_log(err_msg, "error")
            raise Exception(err_msg) from e
        except Exception as e:
            last_err = e
            wait_s = min(3.0 * attempt, 10.0)
            log_net = f"⚠️ [Grok Local MẠNG] Gặp lỗi kết nối ({e}). Đang chờ {wait_s:.0f}s thử lại ({attempt}/{max_retries})..."
            print(log_net)
            _safe_add_log(log_net, "warning")
            if attempt < max_retries:
                await asyncio.sleep(wait_s)
                continue
            raise Exception(f"Lỗi kết nối tới Grok Local sau {max_retries} lần thử: {e}") from e

        if resp.status_code == 200:
            try:
                data = resp.json()
                return data
            except Exception as e:
                raise Exception(f"Grok Local Server trả về định dạng JSON không hợp lệ: {resp.text}") from e
        else:
            err_text = resp.text[:250]
            log_err = f"⚠️ [Grok Local HTTP {resp.status_code}] {err_text} (Lần {attempt}/{max_retries})"
            print(log_err)
            _safe_add_log(log_err, "warning")
            if attempt < max_retries:
                await asyncio.sleep(4.0)
                continue
            raise Exception(f"Grok Local Server trả về lỗi HTTP {resp.status_code}: {err_text}")

    raise Exception(f"Grok Local request thất bại: {last_err}")


def get_active_gemini_keys() -> List[str]:
    """Lấy danh sách Gemini API Key từ cấu hình settings hoặc biến môi trường."""
    from app.config import settings
    keys = []
    if getattr(settings, "GEMINI_API_KEY", None):
        keys = [k.strip() for k in settings.GEMINI_API_KEY.split(",") if k.strip()]
    if not keys:
        raw_env = os.getenv("GEMINI_API_KEY", "")
        keys = [k.strip() for k in raw_env.split(",") if k.strip()]
    return keys


def get_active_openrouter_keys() -> List[str]:
    """Lấy danh sách OpenRouter API Key từ cấu hình settings hoặc biến môi trường."""
    from app.config import settings
    keys = []
    if getattr(settings, "OPENROUTER_API_KEY", None):
        keys = [k.strip() for k in settings.OPENROUTER_API_KEY.split(",") if k.strip()]
    if not keys:
        raw_env = os.getenv("OPENROUTER_API_KEY", "")
        keys = [k.strip() for k in raw_env.split(",") if k.strip()]
    return keys


async def execute_llm_call(
    prompt: str,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    temperature: float = 0.2,
    response_mime_type: Optional[str] = None,
    thinking_budget: Optional[int] = None,
    prompt_file_path: Optional[str] = None,
    timeout: float = 300.0,
    system_instruction: Optional[str] = None
) -> str:
    """
    CỔNG ĐIỀU PHỐI LLM DUY NHẤT (UNIFIED LLM GATEWAY):
    Tập trung 100% logic gọi AI cho toàn bộ hệ thống:
    - Pass 1: Bóc tách thực thể (llm_extractor)
    - Pass 2: Dịch thoại kịch bản (llm_translator)
    - Pass 3: Hậu kiểm & Chuốt mượt chữ Hán (translation_auditor)

    Hỗ trợ chuẩn hóa các Provider:
    1. 'gemini_web': Tự động điều phối qua Browser Worker (chia 2 tin nhắn, New Chat, Thinking UI).
    2. 'gemini': Google Generative Language API (Pacing 4.1s chống 15 RPM, xoay key, auto-retry, unblock safety).
    3. 'openrouter': OpenRouter OpenAI-compatible API.
    4. 'grok_local': Grok Web Automation Server (cổng 8020).
    """
    from app.config import settings

    if thinking_budget is None:
        thinking_budget = getattr(settings, "GEMINI_THINKING_BUDGET", -1)

    prov = (provider or getattr(settings, "TRANSLATION_PROVIDER", "gemini")).lower().strip()

    # 1. NHÁNH GEMINI WEB (Playwright Chromium Worker)
    if prov == "gemini_web":
        from app.services.step_03_translation.gemini_web_service import GeminiWebService
        full_p = f"{system_instruction}\n\n{prompt}" if system_instruction else prompt
        return await GeminiWebService.generate_content(full_p, prompt_file_path=prompt_file_path)

    # 2. NHÁNH GROK LOCAL
    is_grok_local = (prov in ["grok_local", "grok", "grok_web"]) or ("grok" in str(model).lower())
    if is_grok_local:
        grok_url = os.environ.get("AIREAD_GROK_URL") or "http://127.0.0.1:8020/translate-text"
        full_p = f"{system_instruction}\n\n{prompt}" if system_instruction else prompt
        async with httpx.AsyncClient(timeout=timeout) as client:
            res_data = await post_grok_local_with_retry(client, grok_url, {"text": full_p, "timeout": timeout})
            return (res_data.get("translated_text") or "").strip()

    # 3. NHÁNH OPENROUTER
    is_openrouter = (prov == "openrouter") or (model and ("/" in model or "openrouter" in model.lower()))
    if is_openrouter:
        keys = get_active_openrouter_keys()
        if not keys:
            raise ValueError("Chưa cấu hình OPENROUTER_API_KEY trong file .env hoặc Cài đặt!")
        api_key = keys[0]
        model_name = (model or getattr(settings, "OPENROUTER_MODEL", "google/gemini-2.5-flash") or "google/gemini-2.5-flash").strip()
        url = "https://openrouter.ai/api/v1/chat/completions"
        or_headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:8000",
            "X-Title": "AiRead"
        }
        or_messages = []
        if system_instruction:
            or_messages.append({"role": "system", "content": system_instruction})
        or_messages.append({"role": "user", "content": prompt})

        or_body: Dict[str, Any] = {
            "model": model_name,
            "messages": or_messages,
            "temperature": temperature
        }
        if response_mime_type == "application/json":
            or_body["response_format"] = {"type": "json_object"}

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await post_openrouter_with_retry(client, url, or_headers, or_body)
            if resp.status_code != 200:
                raise Exception(f"OpenRouter API Error (HTTP {resp.status_code}): {resp.text}")
            res_json = resp.json()
            choices = res_json.get("choices") or []
            if choices and "message" in choices[0]:
                return (choices[0]["message"].get("content") or "").strip()
            raise Exception("OpenRouter không trả về nội dung hợp lệ!")

    # 4. NHÁNH GEMINI API (GOOGLE AI STUDIO)
    keys = get_active_gemini_keys()
    if not keys:
        raise ValueError("Chưa cấu hình GEMINI_API_KEY trong file .env hoặc Cài đặt!")
    api_key = keys[0]
    model_name = (model or getattr(settings, "GEMINI_MODEL", "gemini-2.5-flash") or "gemini-2.5-flash").strip()

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    headers = {"Content-Type": "application/json"}
    payload: Dict[str, Any] = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "topP": 0.95,
            "topK": 40
        }
    }
    if system_instruction:
        payload["systemInstruction"] = {
            "parts": [{"text": system_instruction}]
        }
    if thinking_budget is not None and (thinking_budget > 0 or thinking_budget == -1):
        payload["generationConfig"]["thinkingConfig"] = {
            "thinkingBudget": thinking_budget
        }
    if response_mime_type:
        payload["generationConfig"]["responseMimeType"] = response_mime_type

    async with httpx.AsyncClient(timeout=timeout) as client:
        # Giãn cách 2.5s để nạp từ từ, chống nghẽn và giảm tải cho Google AI Studio
        await asyncio.sleep(2.5)
        resp = await post_gemini_with_retry(client, url, headers, payload)
        if resp.status_code != 200:
            raise Exception(f"Lỗi gọi Gemini API (HTTP {resp.status_code}): {resp.text}")
        res_json = resp.json()

        candidates = res_json.get("candidates") or []
        if candidates and "content" in candidates[0]:
            parts = candidates[0]["content"].get("parts") or []
            if parts and "text" in parts[0]:
                return parts[0]["text"].strip()

        # Kiểm tra nếu bị Safety Filter chặn
        from app.services.step_03_translation.unblock.unblock_pipeline import is_response_safety_blocked
        if is_response_safety_blocked(res_json):
            raise Exception("Phản hồi bị Google chặn bởi Safety Policy (HARM_CATEGORY).")

        raise Exception(f"Gemini API không trả về nội dung: {res_json}")


def try_repair_truncated_json(text: str) -> Any:
    """
    Tự động khôi phục JSON bị cắt cụt do LLM chạm giới hạn output token hoặc gián đoạn mạng:
    Quét ngược từ các dấu ngoặc đóng '}' hoặc ']' cuối cùng về trước,
    tự động cân bằng và đóng các ngoặc mở còn thiếu, parse cấu trúc hợp lệ lớn nhất có thể.
    """
    if not text:
        return None
    close_indices = [i for i, ch in enumerate(text) if ch in ('}', ']')]
    if not close_indices:
        return None

    # Thử từ vị trí đóng ngoặc cuối cùng lùi dần về trước (tối đa 40 vị trí gần nhất)
    for pos in reversed(close_indices[-40:]):
        sub = text[:pos + 1].rstrip().rstrip(',')
        stack = []
        in_str = False
        esc = False
        valid_syntax = True
        
        for c in sub:
            if esc:
                esc = False
                continue
            if c == '\\':
                if in_str:
                    esc = True
                continue
            if c == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if c in ('{', '['):
                stack.append(c)
            elif c == '}':
                if stack and stack[-1] == '{':
                    stack.pop()
                else:
                    valid_syntax = False
                    break
            elif c == ']':
                if stack and stack[-1] == '[':
                    stack.pop()
                else:
                    valid_syntax = False
                    break

        if not valid_syntax or in_str:
            continue

        closers = {'{': '}', '[': ']'}
        closing_str = ''.join(closers[b] for b in reversed(stack))
        repaired = sub + "\n" + closing_str
        try:
            parsed = json.loads(repaired)
            if parsed is not None:
                return parsed
        except Exception:
            continue

    return None


def safe_json_loads(text: str) -> Any:
    """
    Phân tích JSON an toàn và thông minh từ phản hồi của LLM (Gemini, ChatGPT...):
    - Tự động bóc tách markdown codeblock (```json ... ```)
    - Tự động cắt bỏ text thừa / rác ở đầu và đuôi
    - Khôi phục từ lỗi 'Extra data' do LLM lặp ngoặc đóng (vd: }\\n})
    - Tự động sửa lỗi ngoặc thừa hoặc phẩy thừa (trailing comma)
    - Tự động khôi phục dữ liệu khi JSON bị cắt cụt do chạm giới hạn token (Unterminated string...)
    """
    if not text:
        raise ValueError("Văn bản phản hồi từ LLM rỗng.")

    # Chuẩn hóa khoảng trắng Unicode đặc thù của giao diện Web (NBSP \xa0, IDEOGRAPHIC SPACE \u3000, ZERO-WIDTH \u200b, BOM \ufeff)
    cleaned = text.replace('\xa0', ' ').replace('\u3000', ' ').replace('\u200b', '').replace('\ufeff', '').strip()

    # 1. Bóc tách markdown backticks
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

    # 2. Tìm vị trí dấu ngoặc mở đầu tiên ({ hoặc [) và dấu ngoặc đóng tương ứng cuối cùng (} hoặc ])
    first_brace = min([i for i in [cleaned.find('{'), cleaned.find('[')] if i != -1], default=-1)
    last_brace = max([cleaned.rfind('}'), cleaned.rfind(']')], default=-1)

    if first_brace != -1 and last_brace != -1 and last_brace >= first_brace:
        candidate = cleaned[first_brace:last_brace + 1]
    else:
        candidate = cleaned

    # Tự động sửa lỗi phổ biến của LLM: thiếu dấu đóng ngoặc mảng trước khóa tiếp theo (ví dụ: "src": [76, 77, 78, "s": -> "src": [76, 77, 78], "s":)
    candidate = re.sub(r'("src"\s*:\s*\[[\d\s,]+),\s*("s"|"e"|"zh"|"vi"|"text")', r'\1], \2', candidate)
    # Tự động sửa lỗi LLM bỏ quên key "role": (ví dụ: {"Nhân vật A", "src": ...} -> {"role": "Nhân vật A", "src": ...})
    candidate = re.sub(r'\{\s*"([^":\n\r]+)"\s*,\s*"(src|s|e|vi|zh|index|i)"', r'{"role": "\1", "\2"', candidate)

    # Lần 1: Thử parse trực tiếp candidate
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        # Lần 2: Xử lý lỗi Extra data (LLM in thêm ngoặc } hoặc text sau object JSON hoàn chỉnh)
        if "Extra data" in str(e) and e.pos > 0:
            try:
                return json.loads(candidate[:e.pos].strip())
            except Exception:
                pass

        # Lần 3: Loại bỏ lặp ngoặc đóng ở đuôi
        fixed = re.sub(r'\}\s*\}$', '}', candidate.strip())
        fixed = re.sub(r'\]\s*\]$', ']', fixed)
        try:
            return json.loads(fixed)
        except Exception:
            pass

        # Lần 4: Loại bỏ dấu phẩy thừa trước ngoặc đóng (vd: {"a": 1,})
        fixed_comma = re.sub(r',\s*([\}\]])', r'\1', candidate)
        try:
            return json.loads(fixed_comma)
        except Exception:
            pass

        # Lần 5: Cứu vớt JSON bị cắt cụt (Truncated JSON Auto-Recovery) do tràn token / đứt chuỗi
        repaired = try_repair_truncated_json(candidate)
        if repaired is not None:
            return repaired

        # Nâng cao: Thử parse toàn bộ cleaned text nếu candidate cắt bị thiếu
        if candidate != cleaned:
            try:
                return json.loads(cleaned)
            except Exception:
                pass
            repaired_cleaned = try_repair_truncated_json(cleaned)
            if repaired_cleaned is not None:
                return repaired_cleaned

        # Lần 6: CỨU VỚT TỪNG PHẦN TỬ OBJECT BẰNG REGEX (LINE-BY-LINE RECOVERY)
        # Nếu chỉ bị lỗi cú pháp ở 1 dòng (ví dụ thiếu nháy kép), vẫn bóc tách được tất cả các câu hợp lệ còn lại!
        rescued_items = []
        for obj_match in re.finditer(r'\{[^{}]*\}', text):
            obj_str = obj_match.group(0)
            try:
                item = json.loads(obj_str)
                if isinstance(item, dict) and ("i" in item or "vi" in item or "zh" in item):
                    rescued_items.append(item)
            except Exception:
                # Thử cứu vớt nhẹ nhàng nếu thiếu nháy kép quanh key hoặc phẩy thừa hoặc thiếu key role
                try:
                    fixed_obj = re.sub(r'\{\s*"([^":\n\r]+)"\s*,\s*"(src|s|e|vi|zh|index|i)"', r'{"role": "\1", "\2"', obj_str)
                    fixed_obj = re.sub(r'([{,])\s*([a-zA-Z0-9_]+)\s*:', r'\1"\2":', fixed_obj)
                    fixed_obj = re.sub(r',\s*}', '}', fixed_obj)
                    item = json.loads(fixed_obj)
                    if isinstance(item, dict):
                        rescued_items.append(item)
                except Exception:
                    pass

        if rescued_items:
            return rescued_items

        raise e


