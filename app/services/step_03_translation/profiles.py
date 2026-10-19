# -*- coding: utf-8 -*-
"""
app/services/translation/profiles.py
=========================================
BỘ PROFILE VĂN PHONG VÀ BẢN SẮC THỂ LOẠI CHO TRANSLATOR AIREAD (LỒNG TIẾNG VIDEO & DUBBING)

CẤU TRÚC CHUẨN MỰC (v4 - TINH CHỈNH ĐỒNG BỘ CHO LỒNG TIẾNG PHIM VIDEO):
1. YÊU CẦU BẢN SẮC & LỜI DẪN THOẠI (Ngôi thứ 3 - CẤM TUYỆT ĐỐI DÙNG 'Y').
2. DANH SÁCH CẤM TUYỆT ĐỐI TOÀN BỘ XƯNG HÔ HIỆN ĐẠI (Không phân loại để tránh AI hiểu nhầm chỉ cấm trong nhà).
3. NGUYÊN TẮC THAY THẾ CHỐNG Ô NHIỄM (Bề trên/người già khó chọn từ thì bắt buộc dùng "Ta").
4. BẢNG XƯNG HÔ CỔ ĐẠI NÊN DÙNG / BẮT BUỘC DÙNG (Kèm ví dụ đối chiếu trực quan).
5. THUẬT NGỮ BẢN SẮC THỂ LOẠI & QUY CHUẨN ĐỊA DANH.
6. COMMON RULES ĐẶC THÙ LỒNG TIẾNG PHIM: Câu từ súc tích, gãy gọn, vừa vặn nhịp đọc (ôm khít Start - End, không xé lẻ câu, không cắt cụt đuôi).
"""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)


# =====================================================================
# KHỐI QUY TẮC XƯNG HÔ CỔ PHONG DÙNG CHUNG
# =====================================================================

def _co_phong_addressing_block(danh_xung_bo_sung: str = "") -> str:
    extra = f", danh xưng: {danh_xung_bo_sung.strip()}" if danh_xung_bo_sung and danh_xung_bo_sung.strip() else ""
    return f"XƯNG HÔ CỔ PHONG: Đại từ chính Ta - Ngươi - Hắn, cấm dùng từ 'y' và từ hiện đại (tôi, bạn, cậu, chú, bác...), bối phận thân tộc môn phái giữ nhất quán (Sư tôn - Đồ nhi, Huynh - Đệ, Phụ thân - Mẫu thân), chức danh ghép xuôi (Trần tiêu đầu, Lâm đạo trưởng){extra}."


# =====================================================================
# 1. TIÊN HIỆP, TU CHÂN, HUYỀN HUYỄN
# =====================================================================
XIANXIA_PROFILE = {
    "description": (
        "THỂ LOẠI TIÊN HIỆP: Giữ chuẩn thuật ngữ tu tiên (Luyện Khí, Trúc Cơ, Kim Đan, Nguyên Anh, thần thức, đan điền, linh căn, pháp bảo), địa danh giữ âm Hán-Việt; "
        + _co_phong_addressing_block("Đạo hữu, Chư vị, Tiền bối, Vãn bối, Đạo trưởng, Tiên tử, Tông chủ, Trưởng lão, Sư tôn, Đồ nhi")
    )
}


# =====================================================================
# 2. VÕ HIỆP, KIẾM HIỆP, GIANG HỒ, LỤC LÂM, THỦY HỬ, DÃ SỬ
# =====================================================================
WUXIA_PROFILE = {
    "description": (
        "THỂ LOẠI VÕ HIỆP: Giữ chuẩn thuật ngữ giang hồ (đan điền, kinh mạch, nội lực, kiếm khí, tiêu cục, trại chủ, bang chủ, tướng quân); "
        + _co_phong_addressing_block("Huynh đài, Các hạ, Hảo hán, Tráng sĩ, Đại hiệp, Thiếu hiệp, Tướng quân, Ca ca, Hiền đệ")
    )
}


# =====================================================================
# 3. ĐÔ THỊ HIỆN ĐẠI, THƯƠNG TRƯỜNG, HỌC ĐƯỜNG, GIỚI GIẢI TRÍ
# =====================================================================
URBAN_PROFILE = {
    "description": (
        "THỂ LOẠI ĐÔ THỊ: Xưng hô đời thường hiện đại (Tôi, Anh, Chị, Bạn, Em, Cậu, Bác...), cấm xưng hô cổ trang (huynh đài, tại hạ, bản tọa), giữ nhất quán đại từ."
    )
}


# =====================================================================
# 4. ĐÔ THỊ LINH DỊ, PHONG THỦY, VỚT XÁC, BẮT MA, ĐẠO MỘ
# =====================================================================
URBAN_SUPERNATURAL_PROFILE = {
    "description": (
        "THỂ LOẠI LINH DỊ: Đời thường xưng hô thân thuộc (Tôi, Bác, Chú, Lâm Đạo trưởng), khi giao chiến trừ tà dùng khẩu khí đanh thép (Ta, Ngươi, Nghiệt súc, Yêu nghiệt)."
    )
}


# =====================================================================
# 5. NGÔN TÌNH, CỔ ĐẠI, ĐIỀN VĂN, CUNG ĐẤU, TRẠCH ĐẤU
# =====================================================================
ROMANCE_PROFILE = {
    "description": (
        "THỂ LOẠI CỔ ĐẠI CUNG ĐẤU: Chuẩn xưng hô triều đình phủ đệ (Hoàng thượng, Nương nương, Bệ hạ, Thần thiếp, Di nương, Huynh trưởng, Tỷ tỷ); "
        + _co_phong_addressing_block("Phu quân, Thê tử, Nương tử, Bệ hạ, Thần thiếp, Huynh trưởng, Tỷ tỷ, Muội muội")
    )
}


# =====================================================================
# 6. HỆ THỐNG, TRỌNG SINH, XUYÊN KHÔNG, KHOÁI XUYÊN, VÔ ĐỊCH LƯU
# =====================================================================
SYSTEM_REINCARNATION_PROFILE = {
    "description": (
        "THỂ LOẠI HỆ THỐNG: Hệ thống xưng 'Bản hệ thống' gọi 'Ký chủ', ký chủ gọi 'Ngươi', thế giới xuyên không nào thì áp dụng xưng hô chuẩn của thế giới đó."
    )
}


# =====================================================================
# 7. MẠT THẾ, TẬN THẾ ZOMBIE, KHOA HUYỄN, TINH TẾ, CƠ GIÁP
# =====================================================================
SCI_FI_APOCALYPSE_PROFILE = {
    "description": (
        "THỂ LOẠI MẠT THẾ KHOA HUYỄN: Dùng phong cách sinh tồn quân đội hiện đại (Tôi, Cậu, Anh, Em, Chỉ huy, Đội trưởng), cấm xưng hô cổ trang kiếm hiệp."
    )
}


# =====================================================================
# BỘ QUY TẮC CHUYỂN NGỮ CỐT LÕI (COMMON RULES CHO LỒNG TIẾNG VIDEO & DUBBING)
# =====================================================================
COMMON_RULES = """PHÂN VAI ('role'): 'NARRATOR' cho lời kể dẫn chuyện ngôi 3, 'HỆ THỐNG' cho thông báo máy móc cảnh báo hệ thống, [Tên Nhân Vật] cho lời thoại nhân vật (nếu không rõ tên thì đặt 'Nhân vật A', 'Nhân vật B'... tịnh tiến).
GỘP CÂU: Gộp các câu thoại liên tiếp của cùng một người nói thành câu văn tiếng Việt tự nhiên mượt mà.
CẤM GỘP: Bắt buộc tách dòng độc lập khi đổi người nói hoặc khi mẩu có "no_merge": true (đây là điểm ngắt cảnh phim, tuyệt đối không gộp qua câu tiếp theo).
VĂN PHONG DỊCH: Dịch thoát ý tự nhiên, câu văn thuần Việt xuôi tai, không ghi tên nhân vật vào trường 'vi', mỗi câu kết thúc bằng dấu kết câu (. ! ?).
QUY CHUẨN ĐẦU RA: Trả về DUY NHẤT một mảng JSON [{"role": "...", "src": [start, end], "vi": "..."}], trường 'src' bao phủ liên tục tuần tự từ 1 đến hết danh sách không sót không trùng."""




CONTEXT_PROFILES = {
    "xianxia": XIANXIA_PROFILE,
    "wuxia": WUXIA_PROFILE,
    "urban": URBAN_PROFILE,
    "modern_urban": URBAN_PROFILE,
    "urban_supernatural": URBAN_SUPERNATURAL_PROFILE,
    "romance": ROMANCE_PROFILE,
    "system_reincarnation": SYSTEM_REINCARNATION_PROFILE,
    "sci_fi_apocalypse": SCI_FI_APOCALYPSE_PROFILE,
}


def normalize_profile_key(profile_key: str) -> str:
    """Chuẩn hóa key thể loại từ bất kỳ chuỗi đầu vào nào."""
    if not profile_key:
        return "xianxia"

    pk = profile_key.lower().strip()

    if any(k in pk for k in [
        "linh dị", "linh di", "vớt xác", "vot xac", "trộm mộ", "trom mo",
        "đạo mộ", "dao mo", "phong thủy", "phong thuy", "urban_supernatural",
        "bắt ma", "bat ma", "cương thi", "cuong thi", "supernatural", "dị năng", "di nang",
        "灵异", "悬疑", "盗墓", "风水", "捉鬼", "僵尸"
    ]):
        return "urban_supernatural"

    if any(k in pk for k in [
        "system_reincarnation", "system", "hệ thống", "he thong", "trọng sinh", "trong sinh",
        "xuyên không", "xuyen khong", "khoái xuyên", "khoai xuyen", "vô địch", "vo dich",
        "dị giới", "di gioi", "ngón tay vàng", "bàn tay vàng",
        "系统", "重生", "穿越", "快穿", "无敌"
    ]):
        return "system_reincarnation"

    if any(k in pk for k in [
        "sci_fi_apocalypse", "sci-fi", "scifi", "khoa huyễn", "khoa huyen",
        "mạt thế", "mat the", "tận thế", "tan the", "zombie", "tang thi",
        "tinh tế", "tinh te", "cơ giáp", "co giap", "viễn tưởng", "vien tuong",
        "科幻", "末世", "机甲", "星际", "丧尸"
    ]):
        return "sci_fi_apocalypse"

    if any(k in pk for k in [
        "romance", "ngôn tình", "ngon tinh", "cung đấu", "cung dau", "trạch đấu", "trach dau",
        "điền văn", "dien van", "nữ cường", "nu cuong", "hậu cung", "hau cung", "gia đấu", "gia dau",
        "言情", "宫斗", "宅斗", "种田", "女强"
    ]):
        return "romance"

    if any(k in pk for k in [
        "wuxia", "võ hiệp", "vo hiep", "kiếm hiệp", "kiem hiep", "giang hồ", "giang ho",
        "lục lâm", "luc lam", "hảo hán", "hao han", "thủy hử", "thuy hu", "dã sử", "da su", "sa trường",
        "武侠", "江湖", "水浒", "传统武侠"
    ]):
        return "wuxia"

    if any(k in pk for k in [
        "urban", "modern_urban", "đô thị", "do thi", "hiện đại", "hien dai",
        "học đường", "hoc duong", "thương trường", "thuong truong", "thương chiến",
        "giới giải trí", "gioi giai tri", "vườn trường", "vuon truong",
        "都市", "现言", "现代", "商战", "娱乐"
    ]):
        return "urban"

    return "xianxia"


def get_profile_by_genre(genre_name: Optional[str]) -> dict:
    """Lấy profile theo thể loại truyện."""
    key = normalize_profile_key(genre_name or "xianxia")
    return CONTEXT_PROFILES.get(key, XIANXIA_PROFILE)


def build_standard_system_prompt(genre: Optional[str] = None, author_notes_block: str = "") -> str:
    """Tạo System Prompt tối giản 1 dòng cho toàn bộ hệ thống dịch lồng tiếng."""
    prof = get_profile_by_genre(genre)
    genre_rules = prof.get("description", "")
    author_section = f"\nLƯU Ý TÁC GIẢ: {author_notes_block.strip()}" if author_notes_block and author_notes_block.strip() else ""
    return (
        f"Bạn là BIÊN TẬP VIÊN DỊCH THUẬT LỒNG TIẾNG PHIM CAO CẤP (DUBBING) TRUNG - VIỆT.\n"
        f"{genre_rules}\n"
        f"{COMMON_RULES}"
        f"{author_section}"
    )


def build_user_translation_prompt(
    raw_text: str,
    entity_table_text: str,
    genre: Optional[str] = None,
    author_notes_block: str = ""
) -> str:
    """Tạo User Prompt tối giản: Kịch bản JSON ở đầu, thực thể ở giữa, common rules & yêu cầu output quan trọng nhất ở cuối."""
    prof = get_profile_by_genre(genre)
    genre_rules = prof.get("description", "")

    entity_clean = entity_table_text.strip()
    if entity_clean:
        # Làm sạch bảng thực thể thành danh sách gọn gàng không gạch rác
        ent_items = []
        for line in entity_clean.splitlines():
            line_str = re.sub(r'^[-\s*•]+', '', line).strip()
            if line_str:
                ent_items.append(line_str)
        entity_line = ", ".join(ent_items)
    else:
        entity_line = "Không có"

    return (
        f"KỊCH BẢN GỐC (JSON):\n"
        f"{raw_text.strip()}\n\n"
        f"BẢNG THỰC THỂ KHÓA TÊN RIÊNG:\n"
        f"{entity_line}\n\n"
        f"QUY CHUẨN XƯNG HÔ & THỂ LOẠI:\n"
        f"{genre_rules}\n\n"
        f"QUY TẮC CỐT LÕI & YÊU CẦU ĐẦU RA:\n"
        f"{COMMON_RULES}\n"
        f"Trả về DUY NHẤT một mảng JSON thuần túy, định dạng: [{{\"role\": \"...\", \"src\": [start, end], \"vi\": \"...\"}}]."
    )


def get_profile_description(genre: Optional[str] = None) -> str:
    """Lấy nội dung mô tả quy chuẩn thể loại."""
    return get_profile_by_genre(genre).get("description", "")


def get_common_rules() -> str:
    """Lấy quy tắc chung."""
    return COMMON_RULES


def get_supreme_command() -> str:
    """Mệnh lệnh tối cao chống sót Hán và tiếng Anh."""
    return (
        "MỆNH LỆNH BẢO VỆ ĐỘ TOÀN VẸN: Dịch sạch 100% sang tiếng Việt, tuyệt "
        "đối không để sót chữ Hán, Pinyin hay tiếng Anh/ký tự ngoại lai nào trong bản dịch."
    )


def get_context_profile_prompt(genre: Optional[str] = None) -> str:
    """Hàm tương thích ngược."""
    return get_profile_description(genre)

