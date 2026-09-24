import asyncio
import json
import re
import time
import httpx
from typing import Dict, Any, Optional

DEFAULT_SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_CIVIC_INTEGRITY", "threshold": "BLOCK_NONE"}
]

_LAST_GEMINI_REQUEST_TIME = 0.0
_GEMINI_REQUEST_LOCK = asyncio.Lock()

async def post_gemini_with_retry(
    client: httpx.AsyncClient,
    url: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
    max_retries: int = 6
) -> httpx.Response:
    """
    Tự động gửi request đến Gemini API và xử lý thông minh:
    - Chủ động giãn cách tối thiểu 4.1s giữa các request (Adaptive Pacing) để 100% không chạm mốc 15 RPM.
    - Bắt và thử lại tự động các lỗi mạng/ngắt kết nối.
    - Xử lý thông minh lỗi HTTP 429 (Rate Limit / Quota Exceeded) với delay từ API.
    - Tự động gắn bộ lọc safetySettings=BLOCK_NONE để loại bỏ hoàn toàn vi phạm chặn văn bản.
    """
    global _LAST_GEMINI_REQUEST_TIME

    if payload is not None and "safetySettings" not in payload:
        payload["safetySettings"] = DEFAULT_SAFETY_SETTINGS

    def _safe_add_log(msg: str, level: str = "info"):
        try:
            from app.api.translation_router import add_system_log
            add_system_log(msg, level)
        except Exception:
            pass

    last_exception = None
    for attempt in range(1, max_retries + 1):
        # Tự động điều tiết tần suất request (Pacing): Giãn cách tối thiểu 4.1s giữa các request
        # để đảm bảo 100% không vượt quá ngưỡng 15 RPM của Gemini Free Tier, tránh tối đa việc bị ngắt 60s.
        async with _GEMINI_REQUEST_LOCK:
            now = time.time()
            elapsed = now - _LAST_GEMINI_REQUEST_TIME
            min_interval = 4.1  # 60s / 15 RPM = 4.0s -> 4.1s safe margin
            if elapsed < min_interval:
                await asyncio.sleep(min_interval - elapsed)
            _LAST_GEMINI_REQUEST_TIME = time.time()

        try:
            resp = await client.post(url, headers=headers, json=payload)
        except Exception as e:
            last_exception = e
            wait_seconds = min(3.0 * attempt, 15.0)
            err_name = type(e).__name__
            err_msg = str(e) or err_name
            log_net = f"⚠️ [LLM MẠNG/DISCONNECT] Gặp lỗi kết nối ({err_name}: {err_msg}). Đang chờ {wait_seconds:.1f}s để thử lại (Lần {attempt}/{max_retries})..."
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
            wait_s = min(4.0 * attempt + 3.0, 25.0)
            m_match = re.search(r"/models/([^:]+):", url)
            model_name = m_match.group(1) if m_match else "Gemini"
            log_503 = f"⚠️ [LLM 503 Server Busy] Google AI Studio đang quá tải ({model_name} High Demand). Đang chờ {wait_s:.0f}s để thử lại ({attempt}/{max_retries})..."
            try:
                print(log_503)
            except Exception:
                pass
            _safe_add_log(log_503, "warning")
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
    def _safe_add_log(msg: str, level: str = "info"):
        try:
            from app.api.translation_router import add_system_log
            add_system_log(msg, level)
        except Exception:
            pass

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
    def _safe_add_log(msg: str, level: str = "info"):
        try:
            from app.api.translation_router import add_system_log
            add_system_log(msg, level)
        except Exception:
            pass

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

    cleaned = text.strip()

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
        raise e

