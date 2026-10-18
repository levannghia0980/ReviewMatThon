"""
Module Unblock Preprocessor (Multi-Stage Obfuscation Engine)
Hệ thống mở khóa bảo vệ nội dung nhạy cảm cho AiRead.
"""

from app.services.step_03_translation.unblock.common.normalize import normalize_text
from app.services.step_03_translation.unblock.common.trie_matcher import LongestMatchTrie
from app.services.step_03_translation.unblock.unblock_pipeline import (
    is_sensitive_text,
    is_exact_sensitive_word,
    mask_text_with_dictionary,
    unmask_text_with_dictionary
)

__all__ = [
    "normalize_text",
    "LongestMatchTrie",
    "is_sensitive_text",
    "is_exact_sensitive_word",
    "mask_text_with_dictionary",
    "unmask_text_with_dictionary"
]
