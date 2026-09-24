import logging
from typing import Tuple, Dict, Any
from app.services.unblock.rawt.rawt_pipeline import mask_rawt_text, unmask_rawt_text, get_rawt_trie
from app.services.unblock.common.validator import Validator

logger = logging.getLogger(__name__)

async def mask_text_with_dictionary(text: str, mask_level: str = "word", flow: str = "rawt", enable_erotic: bool = False, **kwargs) -> Tuple[str, Dict[str, Dict[str, str]], bool]:
    """
    Bọc từ nhạy cảm tiếng Trung (RAW Masking) trước khi gửi sang LLM.
    """
    if not text:
        return text, {}, False
        
    return await mask_rawt_text(text, mask_level=mask_level, enable_erotic=enable_erotic)

def unmask_text_with_dictionary(
    translated_text: str, 
    mapping_table: Dict[str, Dict[str, str]], 
    highlight: bool = False, 
    is_draft_only: bool = False, 
    enable_erotic: bool = False,
    flow: str = "rawt",
    **kwargs
) -> str:
    """
    Giải mã Placeholder tiếng Trung sang Tiếng Việt (18+ Từ Nặng hoặc Uyển chuyển).
    """
    if not translated_text and not mapping_table:
        return translated_text or ""

    use_erotic = bool(enable_erotic)
    return unmask_rawt_text(translated_text, mapping_table, highlight=highlight, enable_erotic=use_erotic)

def validate_placeholders(output_text: str, mapping_table: dict) -> dict:
    return Validator.check_placeholders(output_text, mapping_table)

def build_placeholder_reminder(missing_tokens: list, mapping_table: dict) -> str:
    if not missing_tokens:
        return ""
    lines = [f"⚠️ CHÚ Ý: BẢN DỊCH THIẾU {len(missing_tokens)} MÃ MARKUP BẮT BUỘC SAU ĐÂY:"]
    for token in missing_tokens:
        lines.append(f"  - {token}")
    lines.append("Hãy giữ nguyên các mã trên đúng vị trí ngữ pháp!")
    return "\n".join(lines)

def get_unblock_prompt_enforcer() -> str:
    return """
[QUY TẮC BẢO TOÀN THẺ MARKUP §PREFIX_XXXX§ - BẮT BUỘC TUÂN THỦ 100%]
1. GIỮ NGUYÊN 100% CÁC MÃ §PREFIX_XXXX§: Đặt đúng vị trí ngữ pháp trong câu tiếng Việt. Tuyệt đối không xóa, không sửa mã.
2. DỊCH 100% TOÀN BỘ CHỮ HÁN SANG TIẾNG VIỆT: Tuyệt đối CẤM copy giữ lại bất kỳ chữ Hán nào trong câu tiếng Việt. Toàn bộ câu chữ xung quanh mã thẻ BẮT BUỘC PHẢI dịch sang tiếng Việt thuần túy 100%!
3. TUYỆT ĐỐI KHÔNG TỰ CHÊM TỪ ĐỒNG NGHĨA SÁT CẠNH THẺ: Thẻ §PREFIX_XXXX§ đã đại diện trọn vẹn cho cụm từ. Không tự ý thêm danh từ/tính từ đồng nghĩa (như 'người vợ', 'thân xác', 'đôi/cặp'...) ngay sát cạnh thẻ để tránh gây lặp từ luộm thuộm khi giải mã.
"""

async def is_sensitive_text(text: str) -> bool:
    """Kiểm tra xem đoạn văn bản có chứa bất kỳ từ nhạy cảm nào không."""
    if not text:
        return False
    raw_trie = await get_rawt_trie()
    return len(raw_trie.find_all_matches(text)) > 0

async def is_exact_sensitive_word(text: str) -> bool:
    """
    Kiểm tra xem toàn bộ cụm từ (nguyên cụm) có phải là từ nhạy cảm chính xác hay không.
    Sử dụng tra cứu tập hợp O(1) in-memory, không so khớp chuỗi con ngẫu nhiên.
    Tránh chặn oan các tên nhân vật/thực thể hợp lệ (như 'Lưu Chấn', 'Vương Uy').
    Chỉ chặn khi toàn bộ chuỗi khớp 100% với một từ nhạy cảm trong từ điển Unblock.
    """
    if not text or not text.strip():
        return False
    clean = text.strip().lower()
    raw_trie = await get_rawt_trie()
    
    if clean in raw_trie.words:
        return True
        
    try:
        from app.services.unblock.rawt.rawt_decoder import load_zh_erotic_map
        zh_map = load_zh_erotic_map()
        if clean in {w.lower() for w in zh_map}:
            return True
    except Exception:
        pass
        
    return False

def clear_unblock_trie_cache():
    """
    Xóa cache Trie của luồng RAWT để tải lại từ điển mới từ DB.
    """
    import app.services.unblock.rawt.rawt_pipeline as raw_mod
    raw_mod._RAW_TRIE_EROTIC = None
    raw_mod._RAW_TRIE_SOFT = None
    logger.info("Cleared unblock Trie cache for RAWT.")

def is_response_safety_blocked(response_data: Any) -> bool:
    """
    Kiểm tra xem phản hồi của LLM (Gemini, OpenRouter) có bị chặn bởi bộ lọc nội dung (Safety Policy) hay không.
    Chỉ khi bị chặn thật sự, hệ thống mới kích hoạt cơ chế Unblock.
    """
    if not response_data:
        return False
    if isinstance(response_data, dict):
        # 1. Gemini promptFeedback
        pf = response_data.get("promptFeedback") or {}
        if pf.get("blockReason"):
            return True
        # 2. Gemini candidates
        candidates = response_data.get("candidates") or []
        if candidates:
            c0 = candidates[0]
            fr = str(c0.get("finishReason", "")).upper()
            if fr in ("SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"):
                return True
            if "content" not in c0 or not (c0.get("content") or {}).get("parts"):
                if fr and fr not in ("STOP", "MAX_TOKENS"):
                    return True
        elif "candidates" in response_data and len(candidates) == 0:
            return True

        # 3. OpenRouter / OpenAI error or finish_reason
        err = response_data.get("error") or {}
        err_msg = str(err.get("message", "")).lower()
        if any(w in err_msg for w in ("safety", "content filter", "moderation", "policy violation")):
            return True
        choices = response_data.get("choices") or []
        if choices:
            ch0 = choices[0]
            if ch0.get("finish_reason") in ("content_filter", "safety"):
                return True
    elif isinstance(response_data, str):
        low = response_data.lower()
        if any(w in low for w in ("safety policy", "content filter", "blocked by safety")):
            return True
    return False


