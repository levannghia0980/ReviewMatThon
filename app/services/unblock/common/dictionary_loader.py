import os
import json
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
ZH_EROTIC_FILE = os.path.join(DATA_DIR, "zh_erotic_dictionary.json")
ZH_SOFT_FILE = os.path.join(DATA_DIR, "zh_soft_dictionary.json")
VN_EROTIC_FILE = os.path.join(DATA_DIR, "vn_erotic_dictionary.json")

_CACHED_ZH_EROTIC: Optional[Dict[str, str]] = None
_CACHED_ZH_SOFT: Optional[Dict[str, str]] = None
_CACHED_VN_EROTIC: Optional[Dict[str, str]] = None

def _flatten_dict(data: dict) -> Dict[str, str]:
    """Hỗ trợ nạp cả JSON dạng phẳng và JSON dạng phân nhóm theo danh mục."""
    flat = {}
    for k, v in data.items():
        if isinstance(v, dict):
            flat.update(_flatten_dict(v))
        elif isinstance(v, str):
            flat[k.strip()] = v.strip()
    return flat

def load_zh_erotic_map() -> Dict[str, str]:
    """Luôn trả về từ điển chuẩn mực/lịch sự để không làm tục câu văn."""
    return load_zh_soft_map()

def load_zh_soft_map() -> Dict[str, str]:
    global _CACHED_ZH_SOFT
    if _CACHED_ZH_SOFT is None:
        if os.path.exists(ZH_SOFT_FILE):
            try:
                with open(ZH_SOFT_FILE, "r", encoding="utf-8") as f:
                    raw_data = json.load(f)
                    _CACHED_ZH_SOFT = _flatten_dict(raw_data)
            except Exception as e:
                logger.error(f"Error reading {ZH_SOFT_FILE}: {e}")
                _CACHED_ZH_SOFT = {}
        else:
            _CACHED_ZH_SOFT = {}
    return _CACHED_ZH_SOFT

def load_vn_erotic_map() -> Dict[str, str]:
    return {}

def save_zh_erotic_word(zh_word: str, vn_meaning: str) -> None:
    """Thêm hoặc cập nhật một từ tiếng Trung vào file JSON từ điển chuẩn."""
    global _CACHED_ZH_SOFT
    zh_map = load_zh_soft_map()
    zh_map[zh_word.strip()] = vn_meaning.strip()
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(ZH_SOFT_FILE, "w", encoding="utf-8") as f:
            json.dump(zh_map, f, ensure_ascii=False, indent=2)
        _CACHED_ZH_SOFT = zh_map
    except Exception as e:
        logger.error(f"Error saving to {ZH_SOFT_FILE}: {e}")

def save_vn_erotic_word(vn_word: str, erotic_meaning: str) -> None:
    pass

def clear_dictionary_cache() -> None:
    global _CACHED_ZH_EROTIC, _CACHED_ZH_SOFT, _CACHED_VN_EROTIC
    _CACHED_ZH_EROTIC = None
    _CACHED_ZH_SOFT = None
    _CACHED_VN_EROTIC = None
