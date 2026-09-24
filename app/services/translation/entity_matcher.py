"""
Entity Matcher — Hàm khớp thực thể thông minh dùng chung cho RAWT & CONTEXTT.

Chiến lược khớp (từ chính xác nhất → mở rộng):
1. Exact match chinese_name trong văn bản
2. Exact match rough_translation (bản dịch Việt) trong bản dịch GG
3. Substring match: Tìm phần tên riêng (bỏ họ) hoặc phần cốt lõi thực thể
4. Corrections match: Kiểm tra nếu corrections chứa bản dịch sai → entity này
"""
from typing import Dict, List, Any, Optional

from app.services.dichhan.common_lists import CHINESE_SURNAMES

COMPOUND_SURNAMES = tuple(s for s in CHINESE_SURNAMES if len(s) >= 2)
NAME_PREFIXES = ("小", "老", "阿", "大")


def entity_appears_in_text(
    chinese_name: str,
    rough_translation: str,
    _entity_type: str = "NAME",
    combined_text: str = "",
    combined_text_lower: str = "",
    corrections: Optional[Dict[str, str]] = None
) -> bool:
    """
    Kiểm tra thực thể có xuất hiện trong văn bản hay không.
    Áp dụng cho TẤT CẢ loại thực thể (NAME, PLACE, ITEM, SKILL, SECT, OTHER).
    """
    cn = chinese_name.strip()
    rt = rough_translation.strip()

    # 1. Exact match tên Hán gốc
    if cn in combined_text:
        return True

    # 2. Exact match bản dịch Việt (trong bản GG / bản dịch thô)
    if rt and len(rt) >= 2 and rt.lower() in combined_text_lower:
        return True

    # 3. Substring match — tìm phần tên riêng / phần cốt lõi
    if len(cn) >= 3:
        # Xác định phần cốt lõi: bỏ chữ đầu (thường là họ hoặc tiền tố)
        first_char = cn[0]
        # Kiểm tra chữ đầu có phải họ đơn hoặc tiền tố thông dụng
        is_surname_or_prefix = (
            first_char in CHINESE_SURNAMES
            or first_char in NAME_PREFIXES
        )
        if is_surname_or_prefix:
            core_part = cn[1:]  # phần tên riêng (bỏ họ/tiền tố)
        else:
            core_part = cn[1:]  # vẫn thử bỏ chữ đầu

        if len(core_part) >= 2 and core_part in combined_text:
            return True

        # Thử bỏ chữ cuối (hậu tố loại bảo vật/địa danh/môn phái: 简, 剑, 刀, 宗, 门, 山, 峰, 阁, 殿...)
        prefix_core = cn[:-1]
        if len(prefix_core) >= 2 and prefix_core in combined_text:
            return True

    # Với họ kép (2 chữ), thử bỏ 2 chữ đầu
    if len(cn) >= 4:
        first_two = cn[:2]
        if first_two in COMPOUND_SURNAMES:
            core_part_2 = cn[2:]
            if len(core_part_2) >= 2 and core_part_2 in combined_text:
                return True

    # 4. Corrections match: kiểm tra lỗi GG nào correct → bản dịch Việt này
    if corrections and rt:
        rt_lower = rt.lower()
        for wrong_text, correct_text in corrections.items():
            if correct_text == rt or rt_lower in correct_text.lower():
                if wrong_text.lower() in combined_text_lower:
                    return True

    return False


def build_entity_dict(
    entity_list: List[Dict[str, Any]],
    combined_text: str,
    corrections: Optional[Dict[str, str]] = None,
    include_details: bool = False
) -> Dict[str, str]:
    """
    Xây dựng dict thực thể từ danh sách entities.
    Khớp TẤT CẢ loại thực thể (NAME, PLACE, ITEM, SKILL, SECT, OTHER).

    Args:
        entity_list: Danh sách entity dicts (từ metadata cache hoặc DB).
        combined_text: Văn bản gộp của lô chương (RAW hoặc GG tùy luồng).
        corrections: Dict sửa lỗi GG {wrong_text: correct_text} (optional).
        include_details: True để thêm [TYPE], gender, role vào mô tả (dùng cho CONTEXTT).
                         False để chỉ giữ tên Việt + role (dùng cho RAWT).

    Returns:
        Dict[chinese_name, description_string]
    """
    mapping: Dict[str, str] = {}
    combined_text_lower = combined_text.lower()

    for e in entity_list:
        cn = str(e.get("chinese_name") or "").strip()
        rt = str(e.get("rough_translation") or "").strip()
        etype = str(e.get("entity_type") or "NAME").strip()

        if not cn or not rt or etype == "CORRECTION":
            continue
        if cn in mapping:
            continue

        if entity_appears_in_text(cn, rt, etype, combined_text, combined_text_lower, corrections):
            if include_details:
                # Định dạng chi tiết cho CONTEXTT
                desc = f"{rt} [{etype}]"
                details = []
                gender = e.get("gender")
                if gender:
                    gender_vi = "Nam" if gender == "male" else ("Nữ" if gender == "female" else str(gender))
                    details.append(f"Giới tính: {gender_vi}")
                role = e.get("role")
                if role:
                    details.append(f"Vai trò: {role}")
                if details:
                    desc += f" - ({', '.join(details)})"
                mapping[cn] = desc
            else:
                # Định dạng gọn cho RAWT
                role = e.get("role")
                role_str = f" ({role})" if role else ""
                mapping[cn] = f"{rt}{role_str}"

    return mapping
