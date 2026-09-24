import logging
from typing import Tuple, Dict, Any
from app.services.unblock.common.trie_matcher import LongestMatchTrie
from app.services.unblock.rawt.rawt_encoder import RawtEncoder
from app.services.unblock.rawt.rawt_decoder import RawtDecoder, ZH_TO_EROTIC_VN_MAP

logger = logging.getLogger(__name__)

_RAW_TRIE_EROTIC = None
_RAW_TRIE_SOFT = None

async def get_rawt_trie(enable_erotic: bool = False) -> LongestMatchTrie:
    """
    Tải cây Trie cho Luồng RAWT tách biệt theo nút Phong Cách:
    - Khi Phong Cách BẬT (enable_erotic=True): Dùng danh sách từ nặng/sắc văn (zh_erotic_dictionary.json).
    - Khi Phong Cách TẮT (enable_erotic=False): CHỈ dùng danh sách từ nhẹ/thoát nghĩa (zh_soft_dictionary.json).
    """
    global _RAW_TRIE_EROTIC, _RAW_TRIE_SOFT
    if enable_erotic:
        if _RAW_TRIE_EROTIC is None:
            _RAW_TRIE_EROTIC = LongestMatchTrie()
            from app.services.unblock.common.dictionary_loader import load_zh_erotic_map
            erotic_words = [w.strip() for w in load_zh_erotic_map().keys() if w.strip()]
            _RAW_TRIE_EROTIC.load_dictionary(erotic_words, "sensitive_context")
            logger.info(f"Loaded {len(_RAW_TRIE_EROTIC.words)} terms into RAWT Erotic Trie.")
        return _RAW_TRIE_EROTIC
    else:
        if _RAW_TRIE_SOFT is None:
            _RAW_TRIE_SOFT = LongestMatchTrie()
            from app.services.unblock.common.dictionary_loader import load_zh_soft_map
            soft_words = [w.strip() for w in load_zh_soft_map().keys() if w.strip()]
            _RAW_TRIE_SOFT.load_dictionary(soft_words, "sensitive_context")
            logger.info(f"Loaded {len(_RAW_TRIE_SOFT.words)} terms into RAWT Soft Trie.")
        return _RAW_TRIE_SOFT

async def mask_rawt_text(text: str, mask_level: str = "word", enable_erotic: bool = False) -> Tuple[str, Dict[str, Dict[str, str]], bool]:
    if not text:
        return text, {}, False
    trie = await get_rawt_trie(enable_erotic=enable_erotic)
    encoder = RawtEncoder(trie)
    masked_text, mapping_table = encoder.encode(text, mask_level=mask_level)
    is_sensitive = len(mapping_table) > 0
    return masked_text, mapping_table, is_sensitive

def clear_rawt_trie_cache():
    global _RAW_TRIE_EROTIC, _RAW_TRIE_SOFT
    _RAW_TRIE_EROTIC = None
    _RAW_TRIE_SOFT = None
    from app.services.unblock.common.dictionary_loader import clear_dictionary_cache
    clear_dictionary_cache()

def unmask_rawt_text(text: str, mapping_table: Dict[str, Dict[str, str]], highlight: bool = False, enable_erotic: bool = False) -> str:
    return RawtDecoder.decode(text, mapping_table, highlight=highlight, enable_erotic=enable_erotic)
