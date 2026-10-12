import json
import os
import re
import httpx
from typing import Dict, Any, List, Optional, Tuple
from app.config import settings
from app.core.llm_client import post_gemini_with_retry, post_openrouter_with_retry, safe_json_loads
from app.services.translation.profiles import (
    get_profile_description,
    get_common_rules,
    get_supreme_command,
    build_standard_system_prompt,
    normalize_profile_key
)

def get_active_gemini_keys() -> List[str]:
    keys_str = settings.GEMINI_API_KEY
    if not keys_str:
        return []
    return [k.strip() for k in keys_str.split(",") if k.strip()]

def get_active_openrouter_keys() -> List[str]:
    keys_str = settings.OPENROUTER_API_KEY
    if not keys_str:
        return []
    return [k.strip() for k in keys_str.split(",") if k.strip()]


# ==============================================================================
# QUY TẮC KIẾN TRÚC HỆ THỐNG (MANDATORY ARCHITECTURE RULE):
# 1. BÓC TÁCH THỰC THỂ / DỊCH HÁN -> 100% nằm tại: app/services/dichhan/llm_extractor.py
# 2. TOÀN BỘ QUY CHUẨN PROMPT, XƯNG HÔ, THỂ LOẠI -> 100% nằm tại: app/services/translation/profiles.py
# 3. FILE NÀY (llm_translator.py) CHỈ LÀ THỰC THI GỌI API DỊCH THUẬT LỒNG TIẾNG, CẤM VIẾT PROMPT LẶP LẠI!
# ==============================================================================

async def extract_batch_entities_pass1_llm(
    batch_raw_text: str,
    accumulated_entities: Dict[str, Dict[str, Any]] = None,
    genre: str = "cophong",
    project_title: str = "",
    provider: str = "gemini",
    model: str = None
) -> List[Dict[str, Any]]:
    """Hàm chuyển tiếp sạch sang module bóc tách thực thể chuẩn của Dịch Hán."""
    from app.services.dichhan.llm_extractor import extract_batch_entities_direct_llm
    accumulated_entities = accumulated_entities or {}
    raw_ents = await extract_batch_entities_direct_llm(
        combined_raw_text=batch_raw_text,
        existing_entities=accumulated_entities,
        genre_key=genre
    )
    result = []
    for ent in raw_ents:
        ch = ent.get("chinese_name", "").strip()
        vn = ent.get("vietnamese_name", "").strip()
        et = ent.get("entity_type", "NAME").strip()
        if ch and vn:
            result.append({"raw": ch, "viet": vn, "type": et})
    return result


async def translate_batch_pass2_llm(
    tagged_text: str,
    genre: str = "cophong",
    project_title: str = "",
    context_text: str = "",
    entity_table_text: str = "",
    provider: str = "gemini",
    model: str = None
) -> str:
    """
    PHA 2 (LLM 2): Dịch thuật kịch bản LỒNG TIẾNG VIDEO sang Tiếng Việt.
    Toàn bộ System Prompt và Quy tắc xưng hô, văn phong được nạp 100% từ app/services/translation/profiles.py.
    """
    norm_genre = normalize_profile_key(genre)
    genre_rules = get_profile_description(norm_genre)
    common_rules = get_common_rules()

    try:
        input_items = json.loads(tagged_text)
        expected_count = len(input_items)
    except Exception:
        expected_count = len([l for l in tagged_text.splitlines() if l.strip()])

    prompt_parts = []

    # 1. TẦNG 1: KỊCH BẢN ĐẦU VÀO CẦN DỊCH (JSON INPUT)
    prompt_parts.append(f"""=== PHẦN 1: KỊCH BẢN ĐẦU VÀO ({expected_count} CÂU JSON CẦN DỊCH) ===
{tagged_text.strip()}""")

    # 2. TẦNG 2: BẢNG THỰC THỂ KHÓA TÊN RIÊNG (TỪ KHÂU DỊCH HÁN)
    if entity_table_text and entity_table_text.strip():
        prompt_parts.append(f"""=== PHẦN 2: BẢNG TÊN RIÊNG & THỰC THỂ KHÓA CỨNG (DÙNG ĐÚNG 100%) ===
{entity_table_text.strip()}""")

    # 3. TẦNG 3: QUY CHUẨN THỂ LOẠI & XƯNG HÔ
    title_info = f"Tiêu đề tác phẩm: {project_title.strip()}\n" if project_title else ""
    prompt_parts.append(f"""=== PHẦN 3: QUY CHUẨN THỂ LOẠI & XƯNG HÔ ({norm_genre.upper()}) ===
{title_info}{genre_rules}""")

    # 4. TẦNG 4: COMMON RULES & CẤU TRÚC JSON ĐẦU RA (MỆNH LỆNH Ở CUỐI)
    prompt_parts.append(f"""=== PHẦN 4: CÔNG THỨC DỊCH THUẬT LỒNG TIẾNG & CẤU TRÚC ĐẦU RA ===
{common_rules}

MỤC TIÊU & NGUYÊN TẮC GỘP THOẠI BẮT BUỘC:
1. ĐỊNH DẠNG ĐẦU RA: Trả về duy nhất mảng JSON thuần túy theo cấu trúc (bổ sung 'src' là danh sách số 'i' mẩu câu gốc được gộp):
[
  {{"src": [1, 2, 3], "s": 0.08, "e": 4.76, "vi": "..."}},
  {{"src": [4], "s": 4.76, "e": 9.30, "vi": "..."}}
]
2. NGUYÊN TẮC GỘP CÂU & TIMECODE TOÁN HỌC (QUY TẮC BẮT BUỘC SỐNG CÒN):
   - Khi một người nói một tràng dài gồm nhiều câu/mẩu vụn liên tiếp (ví dụ gộp mẩu #1, #2, #3 thành 1 câu mới):
     ➔ 'src': BẮT BUỘC khai báo đầy đủ danh sách [1, 2, 3].
     ➔ 's' (bắt đầu): BẮT BUỘC lấy chính xác giá trị 's' của CÂU ĐẦU TIÊN trong cụm câu gộp.
     ➔ 'e' (kết thúc): BẮT BUỘC lấy chính xác giá trị 'e' của CÂU CUỐI CÙNG trong cụm câu gộp.
   - VÍ DỤ MINH HỌA:
     Đầu vào có 3 mẩu của cùng 1 người nói liên tiếp:
       - Mẩu 1: {{"i": 1, "s": 175.0, "e": 178.52, "zh": "小贩你这车是从哪弄的"}}
       - Mẩu 2: {{"i": 2, "s": 178.52, "e": 179.88, "zh": "你该不会晚上出去搜集物资了吧"}}
       - Mẩu 3: {{"i": 3, "s": 179.88, "e": 181.7, "zh": "你胆子也太大了"}}
     ➔ Khi gộp thành 1 câu tiếng Việt hoàn chỉnh, BẮT BUỘC trả về:
       {{"src": [1, 2, 3], "s": 175.0, "e": 181.7, "vi": "Hạ Phạn, xe này của ngươi kiếm đâu ra vậy? Chẳng lẽ ban đêm ngươi dám ra ngoài thu thập vật tư sao? Gan ngươi lớn thật đấy!"}}
     ➔ TUYỆT ĐỐI CẤM lấy 's' hay 'e' lơ lửng ở giữa cụm (như s=178.52, e=179.88) làm câu dài bị co rút thiếu thời lượng đọc!
3. CẤM GỘP ĐỐI THOẠI KHÁC NHÂN VẬT:
   - TUYỆT ĐỐI CẤM gộp câu đối thoại giữa 2 nhân vật khác nhau (khi đổi vai nói từ Nhân vật A ➔ Nhân vật B bắt buộc tách riêng thành 2 câu độc lập).
4. CÂU RÁC & TẠP ÂM: Mẩu câu nào vô nghĩa, chỉ là con số rác, tạp âm hoặc tiếng thở ASR nhận diện sai mà không có nội dung thì BẮT BUỘC ĐỂ 'vi': '' (chuỗi rỗng), TUYỆT ĐỐI CẤM BỊA CHỮ!""")

    user_prompt = "\n\n".join(prompt_parts)

    provider = provider or settings.TRANSLATION_PROVIDER or "gemini"

    # Hàm nội bộ gọi LLM
    async def _execute_single_call(u_prompt: str) -> str:
        if provider == "gemini":
            keys = get_active_gemini_keys()
            if not keys:
                raise ValueError("Chưa cấu hình GEMINI_API_KEY trong file .env!")
            api_key = keys[0]
            model_name = model or settings.GEMINI_MODEL or "gemini-3.1-flash-lite"
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
            headers = {"Content-Type": "application/json"}
            payload = {
                "contents": [{"role": "user", "parts": [{"text": u_prompt}]}],
                "generationConfig": {
                    "temperature": 0.05,
                    "topP": 0.95,
                    "responseMimeType": "application/json"
                }
            }
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await post_gemini_with_retry(client, url, headers, payload)
                if resp.status_code != 200:
                    raise RuntimeError(f"Gemini API Lỗi HTTP {resp.status_code}: {resp.text}")
                data = resp.json()
                candidates = data.get("candidates") or []
                if candidates and "content" in candidates[0]:
                    return candidates[0]["content"]["parts"][0]["text"].strip()
                raise RuntimeError(f"Gemini API không trả về nội dung: {data}")
        else:
            keys = get_active_openrouter_keys()
            if not keys:
                raise ValueError("Chưa cấu hình OPENROUTER_API_KEY trong file .env!")
            api_key = keys[0]
            model_name = model or settings.OPENROUTER_MODEL or "google/gemini-2.5-flash"
            url = "https://openrouter.ai/api/v1/chat/completions"
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            payload = {
                "model": model_name,
                "messages": [
                    {"role": "user", "content": u_prompt}
                ],
                "temperature": 0.05,
                "response_format": {"type": "json_object"}
            }
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await post_openrouter_with_retry(client, url, headers, payload)
                if resp.status_code != 200:
                    raise RuntimeError(f"OpenRouter API Lỗi HTTP {resp.status_code}: {resp.text}")
                data = resp.json()
                choices = data.get("choices") or []
                if choices and "message" in choices[0]:
                    return choices[0]["message"]["content"].strip()
                raise RuntimeError(f"OpenRouter API không trả về nội dung: {data}")

    # VÒNG LẶP AUTO-RETRY KIỂM TRA SỐ LƯỢNG ITEM JSON ĐẾN KHI ĐẠT CHUẨN MỚI THÔI
    current_prompt = user_prompt
    attempt = 0
    while True:
        attempt += 1
        ans = await _execute_single_call(current_prompt)
        
        # Bóc tách JSON an toàn
        parsed_items = None
        try:
            parsed_items = safe_json_loads(ans)
            if isinstance(parsed_items, dict):
                for v in parsed_items.values():
                    if isinstance(v, list):
                        parsed_items = v
                        break
        except Exception as json_parse_err:
            import logging
            logging.getLogger(__name__).warning(f"⚠️ [JSON Syntax Error] Lỗi cú pháp JSON ở lần {attempt}: {json_parse_err}")

        # KIỂM TRA TÍNH TOÀN VẸN CỦA MẢNG JSON TRẢ VỀ (HỖ TRỢ CẢ MẢNG GỘP VÀ MẢNG ĐỘC LẬP)
        is_valid = False
        duplicate_detected = False
        duplicate_detail = ""

        if isinstance(parsed_items, list) and len(parsed_items) > 0:
            # Kiểm tra xem các phần tử có chứa trường nội dung dịch "vi" hay không
            valid_items = [it for it in parsed_items if isinstance(it, dict) and "vi" in it]
            if len(valid_items) >= max(1, int(len(parsed_items) * 0.85)):
                is_valid = True

            # KIỂM TRA LẶP NỘI DUNG LIỀN KỀ:
            # CHỈ BÁO LỖI KHI CÂU GỐC TIẾNG TRUNG KHÁC NHAU!
            # 1. Chỉ so sánh thuần chữ cái/số, gọt sạch 100% dấu câu (. , ! ? … - :) để không bị bắt nhầm.
            # 2. Gom toàn bộ câu gốc tiếng Trung từ mốc trùng với 'start' đến mốc trùng với 'end' của câu gộp để so sánh.
            if is_valid and len(parsed_items) > 1 and isinstance(input_items, list):
                def _pure_text(t: str) -> str:
                    return re.sub(r'[^\w\s]', '', str(t).lower()).strip()

                def _get_zh_full_span(s_time: float, e_time: float) -> str:
                    matched = []
                    for it in input_items:
                        if isinstance(it, dict):
                            it_s = round(float(it.get("s", 0)), 2)
                            it_e = round(float(it.get("e", 0)), 2)
                            # Mẩu gốc nằm trong khoảng từ start đến end
                            if it_s >= s_time - 0.05 and it_e <= e_time + 0.05:
                                matched.append(str(it.get("zh", "")).strip())
                    return "".join(matched)

                for k in range(1, len(parsed_items)):
                    item_prev = parsed_items[k-1]
                    item_curr = parsed_items[k]
                    vi_prev_pure = _pure_text(item_prev.get("vi", ""))
                    vi_curr_pure = _pure_text(item_curr.get("vi", ""))
                    
                    if vi_prev_pure and vi_curr_pure and vi_prev_pure == vi_curr_pure and len(vi_curr_pure) >= 4:
                        s_prev = round(float(item_prev.get("s", 0)), 2)
                        e_prev = round(float(item_prev.get("e", 0)), 2)
                        s_curr = round(float(item_curr.get("s", 0)), 2)
                        e_curr = round(float(item_curr.get("e", 0)), 2)

                        zh_prev_span = _pure_text(_get_zh_full_span(s_prev, e_prev))
                        zh_curr_span = _pure_text(_get_zh_full_span(s_curr, e_curr))
                        
                        # Nếu nội dung gốc tiếng Trung từ start đến end cũng giống hệt nhau (như nhân vật hét lặp lại) -> CHO QUA 100%!
                        if zh_prev_span and zh_curr_span and zh_prev_span == zh_curr_span:
                            continue
                        
                        # Chỉ khi tiếng Trung gốc khác nhau mà tiếng Việt lại dịch lặp giống nhau -> mới báo lỗi gian lận
                        duplicate_detected = True
                        duplicate_detail = f"Câu #{k} và #{k+1} bị lặp cùng nội dung '{item_curr.get('vi', '')}'!"
                        is_valid = False
                        break

        # NẾU ĐẠT CHUẨN: TRẢ VỀ KẾT QUẢ NGAY
        if is_valid:
            if attempt > 1:
                import logging
                logging.getLogger(__name__).info(
                    f"✔ [Smart JSON Validation] Đã dịch lại thành công ở lần {attempt}! Nhận {len(parsed_items)} câu hợp lệ."
                )
            return ans

        # NẾU ĐÃ THỬ 3 LẦN MÀ PARSE ĐƯỢC MẢNG: CHẤP NHẬN ĐỂ HEALER XỬ LÝ TIẾP
        if attempt >= 3 and isinstance(parsed_items, list) and len(parsed_items) > 0:
            return json.dumps(parsed_items, ensure_ascii=False)

        # NẾU CHƯA ĐẠT: GỬI LỆNH YÊU CẦU DỊCH LẠI
        actual_len = len(parsed_items) if isinstance(parsed_items, list) else 0
        import logging
        if duplicate_detected:
            logging.getLogger(__name__).warning(
                f"⚠️ [Duplicate Check] Lần {attempt}: Phát hiện lặp câu ({duplicate_detail})! Đang yêu cầu dịch lại..."
            )
            retry_note = (
                f"\n\n[CẢNH BÁO]: Nội dung dịch bị lặp lại ({duplicate_detail})! "
                f"TUYỆT ĐỐI CẤM lặp lại nguyên văn câu trước, mỗi lời thoại phải mang nội dung chuẩn xác theo ngữ cảnh."
            )
        else:
            logging.getLogger(__name__).warning(
                f"⚠️ [JSON Validation] Lần {attempt}: Lỗi cấu trúc JSON! Đang yêu cầu LLM dịch lại..."
            )
            retry_note = (
                f"\n\n[CẢNH BÁO LỖI CÚ PHÁP]: Mảng JSON bạn vừa trả về không đúng định dạng chuẩn! "
                f"BẮT BUỘC trả về mảng JSON thuần túy: [ {{\"role\": \"NARRATOR\", \"s\": 0.08, \"e\": 4.76, \"vi\": \"...\"}} ]."
            )
        current_prompt = user_prompt + retry_note

# Backward compatibility aliases
translate_dialogue_batch_llm = translate_batch_pass2_llm
extract_entities_and_context_llm = extract_batch_entities_pass1_llm
