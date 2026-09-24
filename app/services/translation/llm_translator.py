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


async def extract_batch_entities_pass1_llm(
    batch_raw_text: str,
    accumulated_entities: Dict[str, Dict[str, Any]] = None,
    genre: str = "cophong",
    project_title: str = "",
    provider: str = "gemini",
    model: str = None
) -> List[Dict[str, Any]]:
    """
    PHA 1 (LLM 1): Dịch Hán / Bóc tách thực thể & Kiểm soát tính nhất quán xuyên suốt các lô.
    - Nhận diện toàn bộ thực thể mới xuất hiện trong lô (Tên nhân vật, Địa danh, Tông môn, Võ học, Sủng vật, Bảo vật, Cảnh giới).
    - Đối chiếu với các thực thể đã tích lũy từ các lô trước để đảm bảo 100% nhất quán tên gọi từ đầu đến cuối video.
    """
    accumulated_entities = accumulated_entities or {}
    
    existing_entities_preview = ""
    if accumulated_entities:
        sample_list = []
        for raw, item in list(accumulated_entities.items())[-40:]: # Lấy 40 thực thể gần nhất
            viet = item.get("viet", "")
            etype = item.get("type", "NAME")
            sample_list.append(f"- {raw} ➔ {viet} ({etype})")
        existing_entities_preview = "\n".join(sample_list)

    genre_rules = get_profile_description(genre)
    norm_genre = normalize_profile_key(genre)

    system_prompt = f"""BẠN LÀ CHUYÊN GIA DỊCH HÁN - BÓC TÁCH THỰC THỂ & TÊN RIÊNG TIỂU THUYẾT / KỊCH BẢN TRUNG QUỐC.
Nhiệm vụ của bạn là phân tích đoạn văn bản tiếng Trung của lô kịch bản hiện tại, phát hiện toàn bộ thực thể và quy chuẩn tên gọi Hán-Việt chuẩn xác.

=== QUY CHUẨN THỂ LOẠI ({norm_genre.upper()}) ===
{genre_rules}

=== NGUYÊN TẮC BÓC TÁCH VÀ ĐỒNG BỘ THỰC THỂ (QUAN TRỌNG NHẤT) ===
1. VÉT CẠN 6 NHÓM THỰC THỂ BẮT BUỘC:
   - 'NAME': Tên nhân vật, họ tên, danh hiệu, ngoại hiệu, tên cúng cơm/biệt danh (Đại Tráng, Nhị Cẩu, Thạch Đầu - BẮT BUỘC dịch âm Hán-Việt, CẤM dịch nghĩa thuần).
   - 'CREATURE': Tên thú cưng, linh thú, sủng vật, yêu thú (Hắc Ngưu, Ô Vân, Tuyết Cầu, Thái Cổ Thần Long - CẤM dịch thành người hầu/nha đầu).
   - 'PLACE': Địa danh, núi non, sông biển, thành trì, tông môn (Thái Hòa Sơn, Tử Cấm Thành, Trường An - giữ nguyên trật tự Hán-Việt).
   - 'SECT': Tông môn, môn phái, gia tộc, thế lực, bang hội.
   - 'ITEM': Pháp bảo, thần binh, đan dược, linh thảo, bảo vật.
   - 'SKILL': Võ công, tuyệt kỹ, công pháp, thần thông, trận pháp.
   - 'OTHER': Cảnh giới tu vi (Trúc Cơ, Kim Đan, Luyện Khí tam trọng), thuật ngữ bản sắc (Đan điền, Thức hải, Đạo lữ...).

2. TÍNH NHẤT QUÁN XUYÊN SUỐT:
   - Nếu thực thể ĐÃ XUẤT HIỆN trong danh sách đã có từ các lô trước, BẮT BUỘC GIỮ NGUYÊN bản dịch tiếng Việt đã khóa.
   - Nếu là thực thể MỚI, hãy chuyển ngữ sang âm Hán-Việt chuẩn xác, thanh thoát, đúng bản sắc thể loại.

3. ĐỊNH DẠNG ĐẦU RA:
   Trả về JSON thuần túy (Array of Objects):
   [
     {{"raw": "李逍遥", "viet": "Lý Tiêu Dao", "type": "NAME", "desc": "Nhân vật chính"}},
     {{"raw": "长青诀", "viet": "Trường Thanh Quyết", "type": "SKILL", "desc": "Công pháp tu tiên"}},
     {{"raw": "灵田", "viet": "Linh điền", "type": "OTHER", "desc": "Ruộng trồng linh thảo"}}
   ]
"""

    title_part = f"--- TIÊU ĐỀ VIDEO / PHIM (ĐỐI CHIẾU XÁC ĐỊNH TÊN NHÂN VẬT & TỪ KHÓA) ---\n{project_title.strip()}\n\n" if project_title else ""
    user_prompt = f"""{title_part}--- THỰC THỂ ĐÃ TÍCH LŨY TỪ CÁC LÔ TRƯỚC (BẮT BUỘC KHÓA CỨNG NẾU GẶP LẠI) ---
{existing_entities_preview or "(Chưa có thực thể từ lô trước - Đây là lô đầu tiên)"}

--- VĂN BẢN LÔ HIỆN TẠI CẦN BÓC TÁCH THỰC THỂ ---
{batch_raw_text[:9000]}
"""

    provider = provider or settings.TRANSLATION_PROVIDER or "gemini"

    if provider == "gemini":
        keys = get_active_gemini_keys()
        if not keys:
            raise ValueError("Chưa nhập Google Gemini API Key trong Tab Cài Đặt!")
        
        api_key = keys[0]
        if api_key.startswith("gsk_"):
            raise ValueError("LỖI API KEY: Bạn đang nhập nhầm Groq Key (gsk_...) vào ô Gemini! Vui lòng lấy Google Gemini API Key (bắt đầu bằng AIzaSy...) miễn phí tại https://aistudio.google.com/app/apikey và dán vào ô Gemini trong Tab Cài Đặt.")
            
        model_name = model or settings.GEMINI_MODEL or "gemini-3.1-flash-lite"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        headers = {"Content-Type": "application/json"}
        payload = {
            "contents": [
                {"role": "user", "parts": [{"text": f"{system_prompt}\n\n{user_prompt}"}]}
            ],
            "generationConfig": {
                "temperature": 0.1,
                "responseMimeType": "application/json"
            }
        }
        
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await post_gemini_with_retry(client, url, headers, payload)
            if resp.status_code != 200:
                raise RuntimeError(f"Google Gemini API Lỗi HTTP {resp.status_code}: {resp.text}")
            data = resp.json()
            from app.services.unblock.unblock_pipeline import is_response_safety_blocked
            if is_response_safety_blocked(data):
                import logging
                logging.getLogger(__name__).warning("⚠️ [Unblock] Phát hiện LLM chặn bóc tách thực thể (Safety)! Tạm thời bật Unblock...")
                from app.services.unblock.unblock_pipeline import mask_text_with_dictionary, unmask_text_with_dictionary
                masked_batch_text, mapping_table, _ = await mask_text_with_dictionary(batch_raw_text[:9000])
                retry_user_prompt = user_prompt.replace(batch_raw_text[:9000], masked_batch_text)
                retry_payload = {
                    "contents": [
                        {"role": "user", "parts": [{"text": f"{system_prompt}\n\n{retry_user_prompt}"}]}
                    ],
                    "generationConfig": payload["generationConfig"]
                }
                resp_retry = await post_gemini_with_retry(client, url, headers, retry_payload)
                if resp_retry.status_code == 200:
                    retry_data = resp_retry.json()
                    candidates = retry_data.get("candidates") or []
                    if candidates and "content" in candidates[0]:
                        raw_text = candidates[0]["content"]["parts"][0]["text"]
                        parsed = safe_json_loads(raw_text)
                        ent_list = parsed if isinstance(parsed, list) else (parsed.get("entities", []) if isinstance(parsed, dict) else [])
                        for item in ent_list:
                            if "viet" in item and item["viet"]:
                                item["viet"] = unmask_text_with_dictionary(item["viet"], mapping_table)
                        return ent_list

            candidates = data.get("candidates") or []
            if candidates and "content" in candidates[0]:
                text = candidates[0]["content"]["parts"][0]["text"]
                parsed = safe_json_loads(text)
                if isinstance(parsed, list):
                    return parsed
                elif isinstance(parsed, dict) and "entities" in parsed:
                    return parsed["entities"]
            return []
    else:
        keys = get_active_openrouter_keys()
        if not keys:
            return []
        
        api_key = keys[0]
        model_name = model or settings.OPENROUTER_MODEL or "google/gemini-2.5-flash"
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"}
        }
        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                resp = await post_openrouter_with_retry(client, url, headers, payload)
                if resp.status_code == 200:
                    data = resp.json()
                    text = data["choices"][0]["message"]["content"]
                    parsed = safe_json_loads(text)
                    if isinstance(parsed, list):
                        return parsed
                    elif isinstance(parsed, dict) and "entities" in parsed:
                        return parsed["entities"]
        except Exception as e:
            print(f"[LLM Extractor] Warning: Lỗi trích xuất thực thể OpenRouter: {e}")
            return []

    return []


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
    - Sử dụng 100% Profile thể loại chuẩn AIRead (bản sắc, xưng hô, thuật ngữ, cảnh giới...).
    - Khống chế độ dài ký tự: không vượt quá 1.5 lần đến tối đa dưới 2 lần so với câu gốc tiếng Trung.
    - Cho phép và khuyến khích rút gọn câu ngắn gọn súc tích mà vẫn giữ nguyên trọn vẹn nghĩa gốc.
    - Sát nghĩa, dễ hiểu, viết theo đúng ngữ pháp tiếng Việt nói tự nhiên, dùng từ phổ thông của thể loại.
    - Tuyệt đối cấm phình câu, cấm thêm từ đệm/từ nối rườm rà.
    - Đặt mệnh lệnh bảo toàn thẻ XML 1:1 ở CUỐI CÙNG prompt để chống quên thẻ.
    """
    genre_rules = get_profile_description(genre)
    norm_genre = normalize_profile_key(genre)
    common_rules = get_common_rules()

    system_prompt = f"""BẠN LÀ BIÊN TẬP VIÊN DỊCH THUẬT LỒNG TIẾNG VIDEO CAO CẤP (DUBBING & VOICEOVER).
Nhiệm vụ: Chuyển ngữ kịch bản lời thoại video tiếng Trung sang tiếng Việt để LỒNG TIẾNG CHO VIDEO.
Văn phong: Thoát ý, thuần Việt phổ thông, gãy gọn, sát nghĩa, dễ hiểu, viết theo đúng ngữ pháp tiếng Việt nói tự nhiên.
TUYỆT ĐỐI CẤM dịch bám chữ convert máy móc hoặc dùng từ Hán-Việt tối nghĩa khó hiểu.
TUYỆT ĐỐI KHÔNG ĐƯỢC CHO PHÌNH TỪ (tổng số từ tiếng Việt không vượt quá 2.0 lần số từ gốc tiếng Trung). ĐƯỢC PHÉP rút gọn câu súc tích mà vẫn giữ nguyên trọn vẹn nghĩa gốc.
Tuyệt đối không để sót chữ Hán hay tiếng Anh.

=== QUY CHUẨN THỂ LOẠI CHUẨN AIREAD ({norm_genre.upper()}) ===
{genre_rules}

=== QUY TẮC CỐT LÕI DỊCH THUẬT CHUẨN AIREAD CHO LỒNG TIẾNG VIDEO ===
{common_rules}"""

    prompt_parts = []

    # 0. Tiêu đề video và quy tắc tự động sửa lỗi từ đồng âm ASR cho mọi video
    title_section = f"=== TIÊU ĐỀ VIDEO / TÁC PHẨM GỐC ===\n{project_title.strip()}\n\n" if (project_title and project_title.strip()) else ""
    prompt_parts.append(f"""{title_section}=== NGUYÊN TẮC TỰ ĐỘNG PHÁT HIỆN & PHỤC HỒI LỖI TỪ ĐỒNG ÂM ASR (SPEECH-TO-TEXT AUTO-CORRECTION) ===
Kịch bản tiếng Trung đầu vào được bóc tách từ giọng nói video bằng AI thính giác (ASR), do đó có thể xuất hiện các chữ Hán bị nghe nhầm sang chữ đồng âm hoặc gần âm Pinyin (homophones):
- ĐỐI CHIẾU MẠCH TRUYỆN & THỂ LOẠI: Luôn kết hợp ngữ cảnh đối thoại, bối cảnh phân cảnh và tiêu đề tác phẩm (nếu có).
- PHÁT HIỆN TỪ LỆCH CẢNH: Khi một từ ngữ xuất hiện phi lý, ngô nghê, hoặc không ăn nhập với mạch câu chuyện (đặc biệt: tên nhân vật, chức vị, môn phái, chiêu thức võ công, cảnh giới tu luyện bị AI nghe nhầm thành từ sinh hoạt đời thường), bạn BẮT BUỘC tự động suy luận chữ Hán đồng âm chuẩn xác trong tiếng Trung theo đúng ngữ cảnh đó và dịch thẳng sang Tiếng Việt chuẩn xác.
- TUYỆT ĐỐI KHÔNG dịch máy móc theo mặt chữ bị nghe nhầm; bản dịch phải thông suốt, tự nhiên, đúng 100% tinh thần tác phẩm.""")
    
    # 1. Bảng thực thể khóa tên riêng (nếu có)
    if entity_table_text and entity_table_text.strip():
        prompt_parts.append(f"=== BẢNG TÊN RIÊNG & THỰC THỂ KHÓA CỨNG (DÙNG ĐÚNG 100%) ===\n{entity_table_text.strip()}")

    # 2. Ngữ cảnh nối tiếp từ trước (nếu có)
    if context_text and context_text.strip():
        prompt_parts.append(f"=== NGỮ CẢNH NỐI TIẾP TRƯỚC ĐÓ ===\n{context_text.strip()}")

    # 3. Kịch bản đầu vào cần dịch
    prompt_parts.append(f"""=== KỊCH BẢN ĐẦU VÀO CẦN DỊCH LỒNG TIẾNG ===
{tagged_text.strip()}""")

    # 4. MỆNH LỆNH BẢO TOÀN THỨ TỰ ĐÁNH SỐ & ÉP TỶ LỆ SỐ TỪ LỒNG TIẾNG ĐẶT Ở CUỐI CÙNG (Recency Effect)
    prompt_parts.append("""=== MỆNH LỆNH BẢO TOÀN THỨ TỰ ĐÁNH SỐ & ÉP TỶ LỆ SỐ TỪ LỒNG TIẾNG (BẮT BUỘC TUÂN THỦ 100%) ===
1. MỤC ĐÍCH LỒNG TIẾNG VIDEO (DUBBING):
   - Bản dịch dùng để lồng tiếng cho video, khớp với khẩu hình và nhịp nói của nhân vật.
   - Câu dịch phải THOÁT Ý, THUẦN VIỆT PHỔ THÔNG, SÁT NGHĨA, DỄ HIỂU, viết theo đúng ngữ pháp tiếng Việt nói tự nhiên.
   - TUYỆT ĐỐI CẤM dịch bẻ âm thô từng chữ (convert máy móc) hoặc dùng từ Hán-Việt tối nghĩa khó hiểu.
2. KHỐNG CHẾ SỐ TỪ — TUYỆT ĐỐI KHÔNG ĐƯỢC CHO PHÌNH TỪ:
   - TỶ LỆ SỐ TỪ BẮT BUỘC: Tổng số từ của câu dịch tiếng Việt KHÔNG ĐƯỢC VƯỢT QUÁ 1.8 LẦN số từ của câu gốc tiếng Trung.
   - ĐƯỢC PHÉP và KHUYẾN KHÍCH RÚT GỌN CÂU: Linh hoạt cô đọng câu văn ngắn gọn, súc tích mà vẫn giữ nguyên trọn vẹn nghĩa gốc để vừa vặn nhịp lồng tiếng.
   - TUYỆT ĐỐI CẤM THÊM TỪ RƯỜM RÀ, cấm từ nối lan man, cấm bôi dài câu làm phình số từ nói không kịp video.
3. BẢO TOÀN THỨ TỰ ĐÁNH SỐ 1:1:
   - MỖI dòng đầu vào "X. [Nội dung]" ➔ BẮT BUỘC trả về ĐÚNG 1 dòng đầu ra "X. [Bản dịch tiếng Việt]" với CHÍNH XÁC số thứ tự "X".
   - TUYỆT ĐỐI CẤM gộp số, CẤM bỏ sót số (kể cả câu ngắn 1-2 từ), CẤM tự ý đổi số thứ tự.
   - Dòng nào chỉ có dấu chấm (ví dụ "56. .") thì đầu ra cũng giữ nguyên là số thứ tự và dấu chấm ("56. .").
   - Với các câu thoại đối thoại hoặc vế câu nối tiếp, kết thúc tự nhiên bằng dấu phẩy ',' để lời đọc liền mạch mềm mại, chỉ dùng '?' khi hỏi hoặc '!' khi cảm thán.
4. CHỈ TRẢ VỀ DANH SÁCH ĐƯỢC ĐÁNH SỐ (1. ... \n 2. ...), TUYỆT ĐỐI KHÔNG KÈM LỜI CHÀO, LỜI MỞ ĐẦU HAY LỜI GIẢI THÍCH NÀO KHÁC.""")

    user_prompt = "\n\n".join(prompt_parts)

    provider = provider or settings.TRANSLATION_PROVIDER or "gemini"

    if provider == "gemini":
        keys = get_active_gemini_keys()
        if not keys:
            raise ValueError("Chưa cấu hình GEMINI_API_KEY trong file .env!")
        
        api_key = keys[0]
        if api_key.startswith("gsk_"):
            raise ValueError("LỖI API KEY: Bạn đang nhập nhầm Groq Key (gsk_...) vào ô Gemini! Vui lòng lấy Google Gemini API Key (bắt đầu bằng AIzaSy...) miễn phí tại https://aistudio.google.com/app/apikey và dán vào ô Gemini trong Tab Cài Đặt.")
        model_name = model or settings.GEMINI_MODEL or "gemini-3.1-flash-lite"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        headers = {"Content-Type": "application/json"}
        payload = {
            "contents": [
                {"role": "user", "parts": [{"text": f"{system_prompt}\n\n{user_prompt}"}]}
            ],
            "generationConfig": {
                "temperature": 0.3,
                "topP": 0.95
            }
        }
        
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await post_gemini_with_retry(client, url, headers, payload)
            if resp.status_code != 200:
                raise RuntimeError(f"Gemini Translation API Lỗi HTTP {resp.status_code}: {resp.text}")
            data = resp.json()

            # NẾU VÀ CHỈ NẾU BỊ LLM CHẶN (SAFETY BLOCK): Tạm thời bật Unblock rồi tự tắt ngay
            from app.services.unblock.unblock_pipeline import is_response_safety_blocked
            if is_response_safety_blocked(data):
                import logging
                logging.getLogger(__name__).warning("⚠️ [Unblock] Phát hiện LLM kích hoạt Safety Policy! Tạm thời bật Unblock che từ nhạy cảm...")
                from app.services.unblock.unblock_pipeline import mask_text_with_dictionary, unmask_text_with_dictionary
                masked_tagged_text, mapping_table, _ = await mask_text_with_dictionary(tagged_text)
                retry_user_prompt = user_prompt.replace(tagged_text.strip(), masked_tagged_text.strip())
                retry_payload = {
                    "contents": [
                        {"role": "user", "parts": [{"text": f"{system_prompt}\n\n{retry_user_prompt}"}]}
                    ],
                    "generationConfig": payload["generationConfig"]
                }
                resp_retry = await post_gemini_with_retry(client, url, headers, retry_payload)
                if resp_retry.status_code == 200:
                    retry_data = resp_retry.json()
                    candidates = retry_data.get("candidates") or []
                    if candidates and "content" in candidates[0]:
                        raw_ans = candidates[0]["content"]["parts"][0]["text"].strip()
                        unmasked_ans = unmask_text_with_dictionary(raw_ans, mapping_table)
                        logging.getLogger(__name__).info("✔ [Unblock] Đã dịch an toàn thành công và tự động tắt Unblock.")
                        return unmasked_ans

            candidates = data.get("candidates") or []
            if candidates and "content" in candidates[0]:
                return candidates[0]["content"]["parts"][0]["text"].strip()
            raise RuntimeError(f"Gemini API không trả về nội dung (Safety/Empty): {data}")
            
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
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.3
        }
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await post_openrouter_with_retry(client, url, headers, payload)
            if resp.status_code != 200:
                raise RuntimeError(f"OpenRouter Translation API Lỗi HTTP {resp.status_code}: {resp.text}")
            data = resp.json()

            from app.services.unblock.unblock_pipeline import is_response_safety_blocked
            if is_response_safety_blocked(data):
                import logging
                logging.getLogger(__name__).warning("⚠️ [Unblock] Phát hiện OpenRouter chặn nội dung (Safety)! Tạm thời bật Unblock...")
                from app.services.unblock.unblock_pipeline import mask_text_with_dictionary, unmask_text_with_dictionary
                masked_tagged_text, mapping_table, _ = await mask_text_with_dictionary(tagged_text)
                retry_user_prompt = user_prompt.replace(tagged_text.strip(), masked_tagged_text.strip())
                retry_payload = {
                    "model": payload["model"],
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": retry_user_prompt}
                    ],
                    "temperature": 0.3
                }
                resp_retry = await post_openrouter_with_retry(client, url, headers, retry_payload)
                if resp_retry.status_code == 200:
                    retry_data = resp_retry.json()
                    choices = retry_data.get("choices") or []
                    if choices and "message" in choices[0]:
                        raw_ans = choices[0]["message"]["content"].strip()
                        unmasked_ans = unmask_text_with_dictionary(raw_ans, mapping_table)
                        logging.getLogger(__name__).info("✔ [Unblock] Đã dịch an toàn thành công và tự động tắt Unblock.")
                        return unmasked_ans

            choices = data.get("choices") or []
            if choices and "message" in choices[0]:
                return choices[0]["message"]["content"].strip()
            raise RuntimeError(f"OpenRouter API không trả về nội dung: {data}")

# Backward compatibility aliases
translate_dialogue_batch_llm = translate_batch_pass2_llm
extract_entities_and_context_llm = extract_batch_entities_pass1_llm
