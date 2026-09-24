import hashlib
from typing import Dict, Tuple
from app.services.unblock.common.trie_matcher import LongestMatchTrie

class RawtEncoder:
    def __init__(self, trie: LongestMatchTrie):
        self.trie = trie

    def encode(self, text: str, mask_level: str = "word") -> Tuple[str, Dict[str, Dict[str, str]]]:
        if not text:
            return text, {}

        matches = self.trie.find_all_matches(text)
        if not matches:
            return text, {}

        matches.sort(key=lambda x: x[0], reverse=True)

        mapping_table = {}
        masked_text = text

        for start, end, matched_str, categories in matches:
            hash_suffix = hashlib.md5(matched_str.encode('utf-8')).hexdigest()[:4].upper()
            
            category_prefix_map = {
                "body": "BDY", "action": "ACT", "state": "ST",
                "scene": "SCN", "sensitive_context": "STRICT"
            }
            primary_cat = next(iter(categories)) if categories else "sensitive_context"
            prefix = category_prefix_map.get(primary_cat, "ZH")

            token = f"§{prefix}_{hash_suffix}§"
            mapping_table[token] = {
                "text": matched_str,
                "type": primary_cat,
                "flow": "rawt"
            }
            masked_text = masked_text[:start] + f" {token} " + masked_text[end:]

        return masked_text, mapping_table
