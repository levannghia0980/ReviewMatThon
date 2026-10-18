"""
REALM DETECTOR — Phát hiện & Dịch tự động thuật ngữ cảnh giới tu luyện (Cultivation Realm)

Quét bản gốc tiếng Trung, nhận diện tất cả pattern cảnh giới phổ biến từ nhiều hệ thống
(tu tiên, linh sư, võ đạo, game...), tự động dịch sang Hán-Việt chuẩn, trả về list dict
để inject vào bảng thực thể cho LLM tham khảo.
"""

import re
from typing import Dict, List, Tuple

# ============================================================
# 1. BẢNG TÊN CẢNH GIỚI → HÁN-VIỆT
#    Key = Hán tự, Value = Hán-Việt chuẩn
# ============================================================

REALM_NAMES: Dict[str, str] = {
    # --- Hệ thống tu tiên chuẩn (仙侠) ---
    "炼气": "Luyện Khí",
    "练气": "Luyện Khí",      # dị thể
    "筑基": "Trúc Cơ",
    "开光": "Khai Quang",
    "融合": "Dung Hợp",
    "心动": "Tâm Động",
    "结丹": "Kết Đan",
    "金丹": "Kim Đan",
    "元婴": "Nguyên Anh",
    "出窍": "Xuất Khiếu",
    "分神": "Phân Thần",
    "化神": "Hóa Thần",
    "炼虚": "Luyện Hư",
    "合体": "Hợp Thể",
    "大乘": "Đại Thừa",
    "渡劫": "Độ Kiếp",
    "散仙": "Tán Tiên",
    "真仙": "Chân Tiên",
    "金仙": "Kim Tiên",
    "大罗金仙": "Đại La Kim Tiên",
    "太乙金仙": "Thái Ất Kim Tiên",
    "准圣": "Chuẩn Thánh",
    "混元": "Hỗn Nguyên",
    "天仙": "Thiên Tiên",
    "地仙": "Địa Tiên",
    "人仙": "Nhân Tiên",
    "仙王": "Tiên Vương",
    "仙帝": "Tiên Đế",
    "仙尊": "Tiên Tôn",
    "仙君": "Tiên Quân",
    "辟谷": "Tích Cốc",
    "胎息": "Thai Tức",
    "旋照": "Tuyền Chiếu",
    "结晶": "Kết Tinh",
    "具灵": "Cụ Linh",
    "元神": "Nguyên Thần",
    "假丹": "Giả Đan",
    "假婴": "Giả Anh",
    "凝神": "Ngưng Thần",
    "归一": "Quy Nhất",
    "法相": "Pháp Tướng",
    "天人": "Thiên Nhân",
    "生死": "Sinh Tử",
    "涅槃": "Niết Bàn",
    "轮回": "Luân Hồi",
    "斩道": "Trảm Đạo",
    "准帝": "Chuẩn Đế",
    "大帝": "Đại Đế",
    "古神": "Cổ Thần",
    "界主": "Giới Chủ",
    "不朽": "Bất Hủ",
    "永恒": "Vĩnh Hằng",
    "造化": "Tạo Hóa",
    "洞天": "Động Thiên",
    "纯阳": "Thuần Dương",
    "阴阳": "Âm Dương",
    "三花聚顶": "Tam Hoa Tụ Đỉnh",
    "五气朝元": "Ngũ Khí Triều Nguyên",
    "脱胎": "Thoát Thai",
    "换骨": "Hoán Cốt",
    "洗髓": "Tẩy Tủy",
    "易筋": "Dịch Cân",

    # --- Hệ thống linh sư / linh khí ---
    "炼灵": "Luyện Linh",
    "灵动": "Linh Động",
    "灵寂": "Linh Tịch",
    "灵修": "Linh Tu",
    "灵王": "Linh Vương",
    "灵皇": "Linh Hoàng",
    "灵帝": "Linh Đế",
    "灵尊": "Linh Tôn",

    # --- Hệ thống luyện thể / thể tu ---
    "炼体": "Luyện Thể",
    "淬体": "Tôi Thể",
    "锻体": "Đoạn Thể",
    "铸体": "Chú Thể",
    "体修": "Thể Tu",

    # --- Hệ thống luyện hồn / thần hồn ---
    "炼魂": "Luyện Hồn",
    "凝魂": "Ngưng Hồn",
    "铸魂": "Chú Hồn",
    "魂师": "Hồn Sư",
    "魂王": "Hồn Vương",
    "魂帝": "Hồn Đế",
    "魂尊": "Hồn Tôn",

    # --- Hệ thống Đấu Khí (斗气) - Đấu La Đại Lục style ---
    "斗之气": "Đấu Chi Khí",
    "斗者": "Đấu Giả",
    "斗师": "Đấu Sư",
    "大斗师": "Đại Đấu Sư",
    "斗灵": "Đấu Linh",
    "斗王": "Đấu Vương",
    "斗皇": "Đấu Hoàng",
    "斗宗": "Đấu Tông",
    "斗尊": "Đấu Tôn",
    "半圣": "Bán Thánh",
    "斗圣": "Đấu Thánh",
    "斗帝": "Đấu Đế",

    # --- Hệ thống võ đạo / võ giả ---
    "武者": "Võ Giả",
    "武徒": "Võ Đồ",
    "武士": "Võ Sĩ",
    "武师": "Võ Sư",
    "大武师": "Đại Võ Sư",
    "武灵": "Võ Linh",
    "武将": "Võ Tướng",
    "武王": "Võ Vương",
    "武皇": "Võ Hoàng",
    "武帝": "Võ Đế",
    "武神": "Võ Thần",
    "武圣": "Võ Thánh",
    "武尊": "Võ Tôn",

    # --- Hệ thống đan dược ---
    "炼丹": "Luyện Đan",
    "丹师": "Đan Sư",
    "丹王": "Đan Vương",
    "丹帝": "Đan Đế",
    "丹尊": "Đan Tôn",

    # --- Hệ thống luyện khí sư / phù sư ---
    "符师": "Phù Sư",
    "符王": "Phù Vương",
    "阵师": "Trận Sư",
    "阵王": "Trận Vương",
    "器师": "Khí Sư",
    "器王": "Khí Vương",

    # --- Hệ thống huyền huyễn / cấp bậc chung ---
    "先天": "Tiên Thiên",
    "后天": "Hậu Thiên",
    "後天": "Hậu Thiên",  # phồn thể
    "通脉": "Thông Mạch",
    "凝脉": "Ngưng Mạch",
    "通玄": "Thông Huyền",
    "凝气": "Ngưng Khí",
    "聚气": "Tụ Khí",
    "化境": "Hóa Cảnh",
    "返虚": "Phản Hư",
    "通神": "Thông Thần",
    "入圣": "Nhập Thánh",
    "破虚": "Phá Hư",
    "窥道": "Khuy Đạo",
    "问道": "Vấn Đạo",
    "悟道": "Ngộ Đạo",
    "证道": "Chứng Đạo",
    "入道": "Nhập Đạo",
    "天人合一": "Thiên Nhân Hợp Nhất",

    # --- Hệ thống giai cấp (天/地/玄/黄) ---
    "天阶": "Thiên giai",
    "地阶": "Địa giai",
    "玄阶": "Huyền giai",
    "黄阶": "Hoàng giai",
    "天级": "Thiên cấp",
    "地级": "Địa cấp",
    "玄级": "Huyền cấp",
    "黄级": "Hoàng cấp",
    "天品": "Thiên phẩm",
    "地品": "Địa phẩm",
    "玄品": "Huyền phẩm",
    "黄品": "Hoàng phẩm",

    # --- Hệ thống tinh cấp / tinh đẩu ---
    "星级": "Tinh cấp",
    "星斗": "Tinh Đẩu",

    # --- Ma đạo / Yêu tu ---
    "炼魔": "Luyện Ma",
    "魔尊": "Ma Tôn",
    "魔帝": "Ma Đế",
    "魔皇": "Ma Hoàng",
    "魔王": "Ma Vương",
    "魔神": "Ma Thần",
    "魔圣": "Ma Thánh",
    "妖修": "Yêu Tu",
    "妖王": "Yêu Vương",
    "妖皇": "Yêu Hoàng",
    "妖帝": "Yêu Đế",
    "妖尊": "Yêu Tôn",
    "妖圣": "Yêu Thánh",
    "妖神": "Yêu Thần",

    # --- Thần cấp ---
    "神王": "Thần Vương",
    "神皇": "Thần Hoàng",
    "神帝": "Thần Đế",
    "神尊": "Thần Tôn",
    "至尊": "Chí Tôn",
    "主神": "Chủ Thần",
    "圣人": "Thánh Nhân",
    "圣者": "Thánh Giả",
    "圣王": "Thánh Vương",
    "圣帝": "Thánh Đế",
}

# ============================================================
# 2. BẢNG SỐ HÁN-VIỆT
# ============================================================

NUM_HANVIET: Dict[str, str] = {
    "一": "nhất", "二": "nhị", "三": "tam", "四": "tứ", "五": "ngũ",
    "六": "lục", "七": "thất", "八": "bát", "九": "cửu", "十": "thập",
    "百": "bách", "千": "thiên", "万": "vạn",
}

# ============================================================
# 3. BẢNG HẬU TỐ CẢNH GIỚI & PHÂN KỲ / THỜI KỲ
# ============================================================

# Hậu tố đơn (1 chữ)
REALM_SUFFIXES: Dict[str, str] = {
    "期": "kỳ",
    "境": "cảnh",
    "重": "trọng",
    "层": "tầng",
    "阶": "giai",
    "级": "cấp",
    "段": "đoạn",
    "品": "phẩm",
    "星": "tinh",
    "环": "hoàn",
    "转": "chuyển",
    "天": "thiên",
}

# Hậu tố phân kỳ, thời kỳ & trạng thái tu vi (2+ chữ)
REALM_PHASES: Dict[str, str] = {
    "初期": "sơ kỳ",
    "中期": "trung kỳ",
    "后期": "hậu kỳ",
    "後期": "hậu kỳ",   # phồn thể
    "末期": "mạt kỳ",
    "前期": "tiền kỳ",
    "巅峰": "đỉnh phong",
    "顶峰": "đỉnh phong",
    "巅峰期": "đỉnh phong kỳ",
    "顶峰期": "đỉnh phong kỳ",
    "圆满": "viên mãn",
    "圆满期": "viên mãn kỳ",
    "大圆满": "đại viên mãn",
    "大成": "đại thành",
    "小成": "tiểu thành",
    "入门": "nhập môn",
    "初": "sơ",
    "中": "trung",
    "末": "mạt",
    # Các thời kỳ, giai đoạn, trạng thái sinh vật & tu vi
    "时期": "thời kỳ",
    "時期": "thời kỳ",   # phồn thể
    "期间": "thời kỳ",
    "阶段": "giai đoạn",
    "瓶颈期": "bình cảnh kỳ",
    "瓶颈": "bình cảnh",
    "幼年期": "ấu niên kỳ",
    "成长期": "trưởng thành kỳ",
    "成熟期": "thành thục kỳ",
    "全盛期": "toàn thịnh kỳ",
    "衰弱期": "suy nhược kỳ",
    "衰退期": "suy thoái kỳ",
    "虚弱期": "hư nhược kỳ",
    "爆发期": "bộc phát kỳ",
    "过度期": "quá độ kỳ",
    "过渡期": "quá độ kỳ",
    "蜕变期": "thoái biến kỳ",
    "潜伏期": "tiềm phục kỳ",
    "休眠期": "hưu miên kỳ",
    "发情期": "phát tình kỳ",
    "觉醒期": "thức tỉnh kỳ",
    "变异期": "biến dị kỳ",
    "假死期": "giả tử kỳ",
    "上升期": "thời kỳ đi lên",
    "化形期": "hóa hình kỳ",
    "飞升期": "phi thăng kỳ",
    "渡劫期": "độ kiếp kỳ",
    "初阶": "sơ giai",
    "中阶": "trung giai",
    "高阶": "cao giai",
    "顶阶": "đỉnh giai",
    "初级": "sơ cấp",
    "中级": "trung cấp",
    "高级": "cao cấp",
    "顶级": "đỉnh cấp",
    "极境": "cực cảnh",
    "极限": "cực hạn",
}

# Bảng ánh xạ thời kỳ lịch sử / bối cảnh thế giới quan
ERA_PERIOD_MAP: Dict[str, str] = {
    "上古": "Thượng Cổ",
    "太古": "Thái Cổ",
    "远古": "Viễn Cổ",
    "近古": "Cận Cổ",
    "乱古": "Loạn Cổ",
    "荒古": "Hoang Cổ",
    "洪荒": "Hồng Hoang",
    "末法": "Mạt Pháp",
    "神魔": "Thần Ma",
    "修仙": "Tu Tiên",
    "修真": "Tu Chân",
    "灵气复苏": "Linh Khí Khôi Phục",
    "大灾变": "Đại Tai Biến",
    "末世": "Mạt Thế",
    "黄金": "Hoàng Kim",
    "变异": "Biến Dị",
    "觉醒": "Thức Tỉnh",
    "幼年": "Ấu Niên",
    "鼎盛": "Đỉnh Thịnh",
    "全盛": "Toàn Thịnh",
    "衰落": "Suy Lạc",
    "战乱": "Chiến Loạn",
    "和平": "Hòa Bình",
}


# ============================================================
# 4. REGEX PATTERNS — Compile 1 lần duy nhất
# ============================================================

def _build_realm_patterns():
    """Xây dựng các regex pattern để quét cảnh giới & thời kỳ."""
    # Sắp xếp realm names theo độ dài giảm dần (ưu tiên match dài nhất)
    sorted_names = sorted(REALM_NAMES.keys(), key=len, reverse=True)
    realm_group = "|".join(re.escape(n) for n in sorted_names)

    # Số đếm Hán tự
    num_chars = "".join(NUM_HANVIET.keys())
    num_group = f"[{re.escape(num_chars)}]"

    # Hậu tố đơn
    suffix_chars = "".join(REALM_SUFFIXES.keys())
    suffix_group = f"[{re.escape(suffix_chars)}]"

    # Phân kỳ (sắp xếp dài trước)
    sorted_phases = sorted(REALM_PHASES.keys(), key=len, reverse=True)
    phase_group = "|".join(re.escape(p) for p in sorted_phases)

    # Phân kỳ / thời kỳ độc lập (lọc các từ >= 2 ký tự, không lấy từ đơn như 初, 中, 末)
    standalone_phases_list = [p for p in sorted_phases if len(p) >= 2 and p not in ("初", "中", "末")]
    standalone_phase_group = "|".join(re.escape(p) for p in standalone_phases_list)

    # Thời kỳ lịch sử / bối cảnh thế giới quan
    era_group = "|".join(re.escape(e) for e in sorted(ERA_PERIOD_MAP.keys(), key=len, reverse=True))

    patterns = []

    # Pattern 0: TÊN + SỐ + HẬU_TỐ — Ví dụ: 炼灵三境, 炼气九重, 金丹三层
    patterns.append(re.compile(
        rf"(?P<name>{realm_group})(?P<num>{num_group})(?P<suffix>{suffix_group})"
    ))

    # Pattern 1: TÊN + PHÂN_KỲ — Ví dụ: 筑基初期, 金丹巅峰, 元婴大圆满, 筑基时期
    patterns.append(re.compile(
        rf"(?P<name>{realm_group})(?P<phase>{phase_group})"
    ))

    # Pattern 2: SỐ + HẬU_TỐ + TÊN (đảo) — Ví dụ: 十境炼灵, 九重炼气
    patterns.append(re.compile(
        rf"(?P<num>{num_group})(?P<suffix>{suffix_group})(?P<name>{realm_group})"
    ))

    # Pattern 3: TÊN + HẬU_TỐ (không số) — Ví dụ: 炼气期, 筑基期, 结丹期
    patterns.append(re.compile(
        rf"(?P<name>{realm_group})(?P<suffix>{suffix_group})"
    ))

    # Pattern 4: SỐ + HẬU_TỐ (đứng độc lập) — Ví dụ: 三境 -> tam cảnh, 四境 -> tứ cảnh, 五境 -> ngũ cảnh, 一重 -> nhất trọng
    patterns.append(re.compile(
        rf"(?P<num>{num_group})(?P<suffix>境|重|层|阶|品|段|转)"
    ))

    # Pattern 5: BÁN BỘ / CHUẨN + TÊN — Ví dụ: 半步筑基, 半步金丹, 半步元婴, 准圣, 准帝
    patterns.append(re.compile(
        rf"(?P<half>半步|准)(?P<name>{realm_group})"
    ))

    # Pattern 6: THỜI KỲ / THỜI ĐẠI LỊCH SỬ THẾ GIỚI QUAN — Ví dụ: 上古时期, 洪荒时期, 末法时期, 黄金时期
    patterns.append(re.compile(
        rf"(?P<era>{era_group})(?P<period>时期|時代|时代|岁月|纪元)"
    ))

    # Pattern 7: CÁC THỜI KỲ / PHÂN KỲ ĐỨNG ĐỘC LẬP — Ví dụ: 初期, 中期, 后期, 巅峰期, 瓶颈期, 幼年期, 成熟期, 衰弱期, 化形期...
    patterns.append(re.compile(
        rf"(?P<standalone_phase>{standalone_phase_group})"
    ))

    return patterns


_REALM_PATTERNS = _build_realm_patterns()


# ============================================================
# 5. HÀM PHÁT HIỆN & DỊCH CẢNH GIỚI & THỜI KỲ
# ============================================================

def _translate_realm_match(match: re.Match, pattern_idx: int) -> Tuple[str, str]:
    """
    Dịch một match cảnh giới / thời kỳ sang Hán-Việt chuẩn.
    Returns: (han_original, viet_translation)
    """
    han = match.group(0)
    groups = match.groupdict()

    name_han = groups.get("name", "")
    num_han = groups.get("num", "")
    suffix_han = groups.get("suffix", "")
    phase_han = groups.get("phase", "")

    name_viet = REALM_NAMES.get(name_han, name_han)
    num_viet = NUM_HANVIET.get(num_han, num_han) if num_han else ""
    suffix_viet = REALM_SUFFIXES.get(suffix_han, suffix_han) if suffix_han else ""
    phase_viet = REALM_PHASES.get(phase_han, phase_han) if phase_han else ""

    if pattern_idx == 0:
        # Pattern 0: TÊN + SỐ + HẬU_TỐ → "Luyện Linh tam cảnh"
        viet = f"{name_viet} {num_viet} {suffix_viet}"
    elif pattern_idx == 1:
        # Pattern 1: TÊN + PHÂN_KỲ → "Trúc Cơ sơ kỳ", "Trúc Cơ thời kỳ"
        viet = f"{name_viet} {phase_viet}"
    elif pattern_idx == 2:
        # Pattern 2: SỐ + HẬU_TỐ + TÊN (đảo) → "thập cảnh Luyện Linh"
        viet = f"{num_viet} {suffix_viet} {name_viet}"
    elif pattern_idx == 3:
        # Pattern 3: TÊN + HẬU_TỐ → "Luyện Khí kỳ"
        viet = f"{name_viet} {suffix_viet}"
    elif pattern_idx == 4:
        # Pattern 4: SỐ + HẬU_TỐ (độc lập) → "tam cảnh", "tứ cảnh", "nhất trọng"
        viet = f"{num_viet} {suffix_viet}"
    elif pattern_idx == 5:
        # Pattern 5: BÁN BỘ / CHUẨN + TÊN → "Bán bộ Trúc Cơ", "Chuẩn Thánh"
        half_han = groups.get("half", "")
        half_viet = "Bán bộ" if half_han == "半步" else "Chuẩn"
        viet = f"{half_viet} {name_viet}"
    elif pattern_idx == 6:
        # Pattern 6: THỜI KỲ THẾ GIỚI QUAN → "thời kỳ Thượng Cổ", "kỷ nguyên Hồng Hoang"
        era_han = groups.get("era", "")
        period_han = groups.get("period", "")
        era_viet = ERA_PERIOD_MAP.get(era_han, era_han)
        period_viet = "kỷ nguyên" if period_han == "纪元" else "thời kỳ"
        viet = f"{period_viet} {era_viet}"
    elif pattern_idx == 7:
        # Pattern 7: THỜI KỲ / PHÂN KỲ ĐỘC LẬP → "sơ kỳ", "hậu kỳ", "đỉnh phong kỳ", "bình cảnh kỳ"
        phase_standalone = groups.get("standalone_phase", "")
        viet = REALM_PHASES.get(phase_standalone, phase_standalone)
    else:
        viet = name_viet

    return han, re.sub(r'\s+', ' ', viet).strip()


def detect_realms(raw_text: str) -> List[dict]:
    """
    Quét bản RAW tiếng Trung, phát hiện tất cả cụm cảnh giới tu luyện & các thời kỳ và dịch Hán-Việt.

    Args:
        raw_text: Văn bản gốc tiếng Trung (1 hoặc nhiều chương).

    Returns:
        List[dict] — mỗi dict chứa:
            - "han": cụm Hán gốc (ví dụ: "炼灵三境", "筑基初期", "上古时期", "后期")
            - "viet": bản dịch Hán-Việt (ví dụ: "Luyện Linh tam cảnh", "Trúc Cơ sơ kỳ", "thời kỳ Thượng Cổ", "hậu kỳ")
            - "entity_type": "REALM"
            - "count": số lần xuất hiện trong raw_text
    """
    if not raw_text:
        return []

    found: Dict[str, dict] = {}  # key = han, value = {viet, count}

    for pattern_idx, pattern in enumerate(_REALM_PATTERNS):
        for match in pattern.finditer(raw_text):
            han, viet = _translate_realm_match(match, pattern_idx)

            if han in found:
                found[han]["count"] += 1
            else:
                found[han] = {
                    "han": han,
                    "viet": viet,
                    "entity_type": "REALM",
                    "count": 1,
                }

    # Giữ lại các cảnh giới & thời kỳ hợp lệ:
    # - Độ dài >= 3 ký tự (炼气期, 炼灵三境, 筑基初期, 半步筑基, 上古时期...)
    # - Độ dài 2 ký tự có hậu tố cảnh giới/thời kỳ chuẩn (初期, 中期, 后期, 末期, 前期, 时期, 三境, 四境, 一重...)
    # - Hoặc xuất hiện >= 2 lần
    results = []
    for item in found.values():
        h = item["han"]
        if len(h) >= 3 or (len(h) == 2 and (h[1] in ("境", "重", "阶", "品", "段", "转", "层", "期", "步", "段") or h in REALM_PHASES or h in REALM_NAMES)):
            results.append(item)
        elif item["count"] >= 2:
            results.append(item)

    # Sắp xếp theo count giảm dần, rồi theo độ dài han giảm dần
    results.sort(key=lambda x: (-x["count"], -len(x["han"])))

    return results


def detect_realms_as_entities(raw_text: str) -> Dict[str, str]:
    """
    Tiện ích: Trả về dict {han: viet} có thể merge trực tiếp vào bảng thực thể.
    Dùng cho luồng entity_extractor.
    """
    realms = detect_realms(raw_text)
    return {r["han"]: r["viet"] for r in realms}

