
#  🛑 TUYỆT ĐỐI CẤM VIẾT / CHỈNH SỬA / THÊM BỚT PROMPT TRONG FILE NÀY!                             #
#  🛑 MỌI PROMPT DỊCH THUẬT, VĂN PHONG, GỘP THOẠI PHẢI NẰM TẠI:                                    #
#     -> app/services/translation/profiles.py                                                      #
#  🛑 FILE NÀY CHỈ LÀ HTTP API DRIVER ĐỂ GỬI YÊU CẦU LÊN GEMINI / OPENROUTER!                      #
####################################################################################################

import json
import os
import re
import logging
import httpx
from typing import Dict, Any, List, Optional, Tuple
from app.config import settings
from app.core.llm_client import (
    execute_llm_call,
    safe_json_loads,
    get_active_gemini_keys,
    get_active_openrouter_keys
)
from app.services.step_03_translation.profiles import build_user_translation_prompt, build_standard_system_prompt
from app.services.step_03_translation.script_parser import parse_ai_script_output

logger = logging.getLogger(__name__)


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
    from app.services.step_03_translation.dichhan.llm_extractor import extract_batch_entities_direct_llm
    accumulated_entities = accumulated_entities or {}
    raw_ents = await extract_batch_entities_direct_llm(
        combined_raw_text=batch_raw_text,
        existing_entities=accumulated_entities,
        genre_key=genre,
        provider=provider
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
    model: str = None,
    prompt_save_path: Optional[str] = None
) -> str:
    """
    PHA 2 (LLM 2): Dịch thuật kịch bản LỒNG TIẾNG VIDEO sang Tiếng Việt.
    Toàn bộ System Prompt và Quy tắc xưng hô, văn phong được nạp 100% từ app/services/translation/profiles.py.
    # =========================================================================================
    # 🛑 CẢNH BÁO BẮT BUỘC: TUYỆT ĐỐI CẤM VIẾT HOẶC SỬA PROMPT TRONG FILE NÀY!
    # Mọi System Prompt, User Prompt, Quy chuẩn văn phong, Quy tắc xưng hô & Chỉ thị gộp câu
    # BẮT BUỘC ĐƯỢC QUẢN LÝ TẬP TRUNG TẠI DUY NHẤT 1 NƠI: app/services/translation/profiles.py
    # File llm_translator.py CHỈ ĐÓNG VAI TRÒ GỌI API MẠNG THUẦN TÚY (HTTP Client / Driver).
    # =========================================================================================
    """
    user_prompt = build_user_translation_prompt(
        raw_text=tagged_text,
        entity_table_text=entity_table_text,
        genre=genre,
        author_notes_block=context_text
    )
    system_prompt = build_standard_system_prompt(
        genre=genre,
        author_notes_block=context_text
    )

    try:
        input_items = json.loads(tagged_text) if isinstance(tagged_text, str) else tagged_text
        if not isinstance(input_items, list):
            input_items = []
    except Exception:
        input_items = []

    if prompt_save_path:
        try:
            full_saved = f"=== SYSTEM INSTRUCTION ===\n{system_prompt}\n\n=== USER PROMPT ===\n{user_prompt}"
            with open(prompt_save_path, "w", encoding="utf-8") as f:
                f.write(full_saved)
        except Exception as e:
            logger.warning(f"Không thể lưu prompt ra {prompt_save_path}: {e}")

    provider = provider or getattr(settings, "TRANSLATION_PROVIDER", "gemini")
    trans_temperature = float(getattr(settings, "TRANSLATION_TEMPERATURE", 0.85))

    # Hàm nội bộ gọi LLM thông qua Cổng tập trung
    async def _execute_single_call(u_prompt: str) -> str:
        return await execute_llm_call(
            prompt=u_prompt,
            provider=provider,
            model=model,
            temperature=trans_temperature,
            response_mime_type=None,
            prompt_file_path=prompt_save_path,
            timeout=180.0,
            system_instruction=system_prompt
        )

    # VÒNG LẶP AUTO-RETRY KIỂM TRA SỐ LƯỢNG ITEM ĐẾN KHI ĐẠT CHUẨN MỚI THÔI
    current_prompt = user_prompt
    attempt = 0
    while True:
        attempt += 1
        ans = await _execute_single_call(current_prompt)
        
        # Bóc tách kịch bản an toàn (hỗ trợ cả dòng [Role|start-end] và JSON)
        parsed_items = parse_ai_script_output(ans)

        # KIỂM TRA TÍNH TOÀN VẸN CỦA KỊCH BẢN TRẢ VỀ:
        is_valid = False
        duplicate_detected = False
        duplicate_detail = ""

        if isinstance(parsed_items, list) and len(parsed_items) > 0:
            valid_items = [
                it for it in parsed_items 
                if isinstance(it, dict) and any(k in it for k in ["vi", "viet", "text", "translation"]) and str(it.get("vi") or it.get("text") or "").strip()
            ]
            if len(valid_items) > 0 and len(valid_items) >= max(1, int(len(parsed_items) * 0.70)):
                is_valid = True

            # KIỂM TRA LẶP NỘI DUNG LIỀN KỀ:
            if is_valid and len(parsed_items) > 1 and isinstance(input_items, list):
                def _pure_text(t: str) -> str:
                    return re.sub(r'[^\w\s]', '', str(t).lower()).strip()

                zh_map = {}
                for inp in input_items:
                    if isinstance(inp, dict) and "i" in inp:
                        zh_map[inp["i"]] = str(inp.get("zh", "")).strip()

                def _get_zh_span_by_src(item_obj: dict) -> str:
                    s_list = item_obj.get("src", [])
                    if isinstance(s_list, list):
                        if len(s_list) == 2 and isinstance(s_list[0], int) and isinstance(s_list[1], int) and s_list[1] > s_list[0]:
                            idx_list = list(range(s_list[0], s_list[1] + 1))
                        else:
                            idx_list = [x for x in s_list if isinstance(x, int)]
                    elif isinstance(s_list, int):
                        idx_list = [s_list]
                    else:
                        idx_list = []
                    return "".join(zh_map.get(idx, "") for idx in idx_list)

                for k in range(1, len(parsed_items)):
                    item_prev = parsed_items[k-1]
                    item_curr = parsed_items[k]
                    vi_prev_pure = _pure_text(item_prev.get("vi", ""))
                    vi_curr_pure = _pure_text(item_curr.get("vi", ""))
                    
                    if vi_prev_pure and vi_curr_pure and vi_prev_pure == vi_curr_pure and len(vi_curr_pure) >= 4:
                        zh_prev_span = _pure_text(item_prev.get("zh") or _get_zh_span_by_src(item_prev))
                        zh_curr_span = _pure_text(item_curr.get("zh") or _get_zh_span_by_src(item_curr))
                        
                        # Nếu nội dung gốc tiếng Trung từ các mẩu src cũng giống hệt nhau (như nhân vật hét lặp lại) -> CHO QUA 100%!
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
                logger.info(
                    f"✔ [Smart Script Validation] Đã dịch lại thành công ở lần {attempt}! Nhận {len(parsed_items)} câu hợp lệ."
                )
            return ans

        # NẾU ĐÃ THỬ 3 LẦN MÀ PARSE ĐƯỢC MẢNG: CHẤP NHẬN ĐỂ HEALER XỬ LÝ TIẾP
        if attempt >= 3 and isinstance(parsed_items, list) and len(parsed_items) > 0:
            return ans

        # NẾU CHƯA ĐẠT: GỬI LỆNH YÊU CẦU DỊCH LẠI
        actual_len = len(parsed_items) if isinstance(parsed_items, list) else 0
        if duplicate_detected:
            logger.warning(
                f"⚠️ [Duplicate Check] Lần {attempt}: Phát hiện lặp câu ({duplicate_detail})! Đang yêu cầu dịch lại..."
            )
            current_prompt = user_prompt + (
                f"\n\n[LƯU Ý]: Bản dịch trước bị lặp câu ({duplicate_detail}). "
                f"Hãy dịch chuẩn xác từng câu theo đúng ngữ cảnh và giữ nguyên cấu trúc định dạng chuẩn: [mẩu ASR gốc] Lời dịch -> [Tên vai]."
            )
        else:
            logger.warning(
                f"⚠️ [Script Format Validation] Lần {attempt}: Định dạng kịch bản chưa hợp lệ! Đang yêu cầu LLM dịch lại..."
            )
            current_prompt = user_prompt

# Backward compatibility aliases
translate_dialogue_batch_llm = translate_batch_pass2_llm
extract_entities_and_context_llm = extract_batch_entities_pass1_llm
