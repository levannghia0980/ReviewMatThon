import re
from typing import Dict, List

class Validator:
    @staticmethod
    def check_placeholders(text: str, mapping_table: Dict[str, Dict[str, str]]) -> Dict[str, any]:
        if not mapping_table:
            return {"total": 0, "found": 0, "missing": [], "is_valid": True}
        
        total = len(mapping_table)
        found = 0
        missing = []
        
        for token in mapping_table.keys():
            clean_tok = token.strip(" §[]⟦⟧")
            parts = clean_tok.split("_")
            flex_clean = r"[\s_-]*".join([re.escape(p) for p in parts])
            pattern = r"(?:§|{|\[|⟦|\()?[\s_-]*" + flex_clean + r"[\s_-]*(?:§|}|\]|⟧|\))?"
            
            if re.search(pattern, text, re.IGNORECASE):
                found += 1
            else:
                missing.append(token)
                
        missing_count = len(missing)
        pct = (found / total) if total > 0 else 1.0

        # PHÂN TẦNG THÔNG MINH KẾT HỢP SỐ THẺ MẤT VÀ TỔNG SỐ THẺ:
        # 1. Tầng cực ít (total <= 3): Mất 1-3 thẻ do AI dịch tự nhiên sang tiếng Việt -> 100% HỢP LỆ (Không hủy lô)
        if total <= 3:
            is_valid = True
            tier_desc = f"Tầng cực ít (<=3 thẻ): Giữ {found}/{total} thẻ (Mất {missing_count} thẻ) -> Tự động đạt chuẩn"
        # 2. Tầng ít (4 <= total <= 7): Chỉ cần giữ được >= 1 thẻ hoặc số thẻ mất <= 4 -> HỢP LỆ
        elif total <= 7:
            is_valid = (found >= 1) or (missing_count <= 4)
            tier_desc = f"Tầng ít (4-7 thẻ): Giữ {found}/{total} thẻ (Mất {missing_count} thẻ, {round(pct*100)}%)"
        # 3. Tầng vừa (8 <= total <= 15): Cho phép mất <= 6 thẻ hoặc giữ >= 35% -> HỢP LỆ
        elif total <= 15:
            is_valid = (missing_count <= 6) or (pct >= 0.35)
            tier_desc = f"Tầng vừa (8-15 thẻ): Giữ {found}/{total} thẻ (Mất {missing_count} thẻ, {round(pct*100)}%)"
        # 4. Tầng nhiều (> 15 thẻ): Cho phép mất <= 10 thẻ hoặc giữ >= 35% -> Chỉ hủy khi mất hàng loạt
        else:
            is_valid = (missing_count <= 10) or (pct >= 0.35)
            tier_desc = f"Tầng nhiều (>15 thẻ): Giữ {found}/{total} thẻ (Mất {missing_count} thẻ, {round(pct*100)}%)"
            
        return {
            "total": total,
            "found": found,
            "missing": missing,
            "missing_count": missing_count,
            "is_valid": is_valid,
            "tier_desc": tier_desc
        }
