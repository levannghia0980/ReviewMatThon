import re
import json
import os
import logging
from typing import Dict
from app.services.unblock.common.slang_cleaner import clean_duplicate_slang_words

logger = logging.getLogger(__name__)

from app.services.unblock.common.dictionary_loader import (
    load_zh_erotic_map,
    load_zh_soft_map,
    save_zh_erotic_word,
    clear_dictionary_cache
)

def get_zh_to_vn_map(enable_erotic: bool = False) -> Dict[str, str]:
    if enable_erotic:
        return load_zh_erotic_map()
    else:
        return load_zh_soft_map()

class _ZHMapProxy(dict):
    def __getitem__(self, key):
        return load_zh_erotic_map().get(key, "")
    def get(self, key, default=None):
        return load_zh_erotic_map().get(key, default)
    def keys(self):
        return load_zh_erotic_map().keys()
    def values(self):
        return load_zh_erotic_map().values()
    def items(self):
        return load_zh_erotic_map().items()
    def __contains__(self, key):
        return key in load_zh_erotic_map()
    def __len__(self):
        return len(load_zh_erotic_map())
    def update(self, other):
        for k, v in other.items():
            save_zh_erotic_word(k, v)

ZH_TO_EROTIC_VN_MAP = _ZHMapProxy()
ZH_TO_SOFT_VN_MAP = load_zh_soft_map()


class RawtDecoder:
    """
    Decoder chuyên biệt cho Luồng RAWT:
    Giải mã trực tiếp từ Placeholder gốc Tiếng Trung sang Tiếng Việt Sắc Văn 1 bước duy nhất.
    Khử trùng lặp từ lóng & không chạy đè biến đổi của luồng CONTEXTT.
    """
    @staticmethod
    def decode(text: str, mapping_table: Dict[str, Dict[str, str]], highlight: bool = False, enable_erotic: bool = False) -> str:
        if not text:
            return ""
        if not mapping_table:
            return clean_duplicate_slang_words(text)

        zh_map = get_zh_to_vn_map(enable_erotic=enable_erotic)
        from app.services.dichhan.hanviet_data import build_hanviet_name

        restored_text = text
        for token, data in mapping_table.items():
            clean_token = token.strip(" §[]⟦⟧")
            parts = clean_token.split("_")
            flex_clean = r"[\s_-]*".join([re.escape(p) for p in parts])
            token_pattern = r"(?:§|{|\[|⟦|\()?[\s_-]*" + flex_clean + r"[\s_-]*(?:§|}|\]|⟧|\))?"
            
            matched_pattern = None
            if re.search(token_pattern, restored_text, re.IGNORECASE):
                matched_pattern = token_pattern
            elif len(parts) == 2 and len(parts[1]) >= 3 and re.search(rf"(?i)\b{re.escape(parts[1])}\b", restored_text):
                matched_pattern = rf"(?i)\b{re.escape(parts[1])}\b"

            if matched_pattern:
                orig_term = data.get("text", "")
                is_chinese = bool(re.search(r"[\u4e00-\u9fff]", orig_term))
                
                if is_chinese:
                    target_replacement = zh_map.get(orig_term) or zh_map.get(orig_term.lower())
                    if not target_replacement:
                        from app.services.unblock.common.trie_matcher import SAFE_COMPOUNDS
                        sorted_zh_keys = sorted(zh_map.keys(), key=lambda x: len(x), reverse=True)
                        translated_parts = orig_term
                        for zh_key in sorted_zh_keys:
                            if zh_key in translated_parts:
                                # BẢO VỆ TỪ AN TOÀN: Kiểm tra xem zh_key có nằm bên trong
                                # một từ ghép an toàn (safe compound) trong orig_term không
                                is_safe = False
                                for safe_word in SAFE_COMPOUNDS:
                                    if zh_key in safe_word and safe_word in translated_parts:
                                        is_safe = True
                                        break
                                if not is_safe:
                                    vn_val = zh_map[zh_key]
                                    translated_parts = translated_parts.replace(zh_key, f" {vn_val} ")

                        if re.search(r"[\u4e00-\u9fff]", translated_parts):
                            translated_parts = build_hanviet_name(translated_parts) or translated_parts

                        target_replacement = re.sub(r"\s+", " ", translated_parts).strip()
                else:
                    target_replacement = orig_term

                if highlight:
                    replacement = f'<span class="unblock-sensitive" title="Đã khôi phục: {orig_term} → {target_replacement}">{target_replacement}</span>'
                else:
                    replacement = target_replacement
                    
                def _sub_matched(m):
                    st, en = m.start(), m.end()
                    pre = " " if st > 0 and (restored_text[st-1].isalnum() or '\u00c0' <= restored_text[st-1] <= '\u1eff') else ""
                    suf = " " if en < len(restored_text) and (restored_text[en].isalnum() or '\u00c0' <= restored_text[en] <= '\u1eff') else ""
                    return f"{pre}{replacement}{suf}"

                restored_text = re.sub(matched_pattern, _sub_matched, restored_text, flags=re.IGNORECASE)

        for token, data in mapping_table.items():
            orig_term = data.get("text", "")
            is_chinese = bool(re.search(r"[\u4e00-\u9fff]", orig_term))
            if is_chinese:
                target_rep = zh_map.get(orig_term) or zh_map.get(orig_term.lower())
                if not target_rep:
                    target_rep = build_hanviet_name(orig_term) or orig_term
            if highlight:
                target_rep_display = f'<span class="unblock-sensitive" title="Đã khôi phục: {orig_term} → {target_rep}">{target_rep}</span>'
            else:
                target_rep_display = target_rep

            if token in restored_text:
                def _sub_tok(m):
                    st, en = m.start(), m.end()
                    pre = " " if st > 0 and (restored_text[st-1].isalnum() or '\u00c0' <= restored_text[st-1] <= '\u1eff') else ""
                    suf = " " if en < len(restored_text) and (restored_text[en].isalnum() or '\u00c0' <= restored_text[en] <= '\u1eff') else ""
                    return f"{pre}{target_rep_display}{suf}"
                restored_text = re.sub(re.escape(token), _sub_tok, restored_text)

        # Dọn sạch triệt để mọi biến thể thẻ markup rò rỉ (thay bằng khoảng trắng để tránh dính chữ)
        restored_text = re.sub(r'§[A-Z]+(?:_[A-Z0-9]+)?§?', ' ', restored_text)
        restored_text = re.sub(r'§[A-Za-z0-9_]*§', ' ', restored_text)
        restored_text = re.sub(r'\b(?:STRICT|ZH|ACT|OBJ|BDY|SCN|PREFIX)_[A-Z0-9]+\b', ' ', restored_text)
        restored_text = re.sub(r'\b(?:STRICT|ZH|ACT|OBJ|BDY|SCN|PREFIX)\b', ' ', restored_text)

        # Áp dụng bộ lọc khử trùng lặp từ lóng, tách dính chữ & sửa các từ bị sai ngữ nghĩa
        restored_text = clean_duplicate_slang_words(restored_text)
        restored_text = re.sub(r'[ \t]+', ' ', restored_text)
        restored_text = re.sub(r' *\n *', '\n', restored_text)

        # Quét sạch triệt để mọi chữ Hán còn sót lại (chuyển sang âm Hán-Việt)
        if re.search(r"[\u4e00-\u9fff]", restored_text):
            def _sub_remaining_han(m):
                raw = m.group(0)
                hv = build_hanviet_name(raw)
                return f" {hv} " if hv else raw
            restored_text = re.sub(r"[\u4e00-\u9fff]+", _sub_remaining_han, restored_text)
            restored_text = re.sub(r"[^\S\r\n]{2,}", " ", restored_text)

        return restored_text.strip()
