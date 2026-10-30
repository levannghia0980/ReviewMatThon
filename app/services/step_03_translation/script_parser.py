import re
import json
from typing import List, Dict, Any
from app.core.llm_client import safe_json_loads


def clean_system_tags_from_text(text: str) -> str:
    """
    Khử đúng cấu trúc nhãn kỹ thuật hệ thống từ đầu vào, tránh làm mất nội dung thoại thật:
    - Nhãn khóa: [KHÓA], [KHOA], [LOCK] đặt trong ngoặc vuông [...]
    - Nhãn thời lượng: (X.Xs) hoặc (Xs) đặt trong ngoặc tròn (...)
    - Nhãn hạn mức từ: [max X từ] hoặc (max X từ)
    Tuyệt đối KHÔNG xóa các từ ngữ thông thường của câu thoại như:
    'khóa cửa', 'chìa khóa', 'mở khóa', 'khóa chặt', 'Đại Ung Thần Khóa'...
    """
    if not text:
        return ""
    s = str(text).strip()
    # 1. Khử cấu trúc [KHÓA] trong ngoặc vuông (kèm dấu câu bám sau nếu có, ví dụ [KHÓA]. hoặc [KHÓA],)
    s = re.sub(r'\s*\[\s*(?:KHÓA|KHOA|LOCK)\s*\][\.\,\;\:\s]*', ' ', s, flags=re.IGNORECASE)
    # 2. Khử cấu trúc thời lượng giây trong ngoặc tròn như (1.2s), (0.5s)
    s = re.sub(r'\s*\(\s*\d+(?:\.\d+)?s\s*\)[\.\,\;\:\s]*', ' ', s, flags=re.IGNORECASE)
    # 3. Khử cấu trúc nhãn hạn mức từ [max X từ] hoặc (max X từ)
    s = re.sub(r'\s*[\[\(]\s*max\s*\d+\s*(?:từ|words?)\s*[\]\)][\.\,\;\:\s]*', ' ', s, flags=re.IGNORECASE)
    # 4. Thu gọn khoảng trắng thừa và làm sạch hai đầu
    s = re.sub(r'\s{2,}', ' ', s).strip()
    return s


def parse_ai_script_output(raw_text: str) -> List[Dict[str, Any]]:
    """
    Bóc tách kết quả kịch bản dịch từ LLM.
    Hỗ trợ 2 định dạng:
    1. Định dạng dòng kịch bản siêu tinh gọn (tiết kiệm 50% token đầu ra):
       [Role|start-end] Lời thoại tiếng Việt.
       Ví dụ:
       [DẪN CHUYỆN|1-2] Toàn cầu quỷ dị bùng phát, quỷ dị giáng lâm.
       [Trần Bưu|3-3] Mau chạy đi!
       Hoặc các biến thể linh hoạt [Role|1], [1-2|Role], [Role] [1-2], [1-2] [Role]...
    2. Fallback định dạng JSON: [{"role": "...", "src": [start, end], "vi": "..."}]
    """
    if not raw_text or not str(raw_text).strip():
        return []

    text = str(raw_text).strip()

    # TH1: Thử bóc tách JSON an toàn trước (nếu LLM trả về mảng JSON)
    try:
        parsed_json = safe_json_loads(text)
        if isinstance(parsed_json, dict):
            for v in parsed_json.values():
                if isinstance(v, list):
                    parsed_json = v
                    break
        if isinstance(parsed_json, list) and len(parsed_json) > 0:
            valid_json_items = []
            for it in parsed_json:
                if isinstance(it, dict) and any(k in it for k in ["vi", "text", "viet", "translation"]):
                    role = it.get("role") or it.get("speaker") or "DẪN CHUYỆN"
                    if str(role).upper() in ["NARRATOR", "DẪN CHUYỆN", "DAN CHUYEN", "DẪN", "KỂ", "KỂ CHUYỆN", "NGƯỜI KỂ", "THUYẾT MINH"]:
                        role = "DẪN CHUYỆN"
                    src = it.get("src") or []
                    if isinstance(src, int):
                        src = [src, src]
                    elif isinstance(src, list) and len(src) == 1:
                        src = [src[0], src[0]]
                    vi = clean_system_tags_from_text(it.get("vi") or it.get("text") or it.get("viet") or "")
                    zh = str(it.get("zh") or it.get("original") or it.get("chinese") or "").strip()
                    item_dict = {"role": role, "src": src, "vi": vi}
                    if zh:
                        item_dict["zh_raw"] = zh
                        item_dict["zh_pieces"] = [p.strip() for p in zh.split('/') if p.strip()]
                        item_dict["zh"] = zh.replace('/', '').strip()
                    else:
                        item_dict["zh_pieces"] = []
                    valid_json_items.append(item_dict)
            if len(valid_json_items) > 0:
                return valid_json_items
    except Exception:
        pass

    # TH2: Bóc tách từng dòng kịch bản (Script Line Format)
    results: List[Dict[str, Any]] = []
    lines = text.splitlines()

    # Pattern 1: [Role|start-end] or [Role|num] (hỗ trợ thêm phần đuôi tùy chọn như |max X từ)
    p1 = re.compile(
        r'^\s*(?:#?\d+[\.\:\-\)]\s*)?(?:\*{1,2})?\[\s*([^\|\]]+?)\s*\|\s*#?(\d+)(?:\s*(?:[\-\–\—\~]|->|\.{2,3})\s*#?(\d+))?(?:\s*\|[^\]]*)?\s*\](?:\*{1,2})?[:\s]*(.*)$'
    )
    # Pattern 2: [start-end|Role] or [num|Role]
    p2 = re.compile(
        r'^\s*(?:#?\d+[\.\:\-\)]\s*)?(?:\*{1,2})?\[\s*#?(\d+)(?:\s*(?:[\-\–\—\~]|->|\.{2,3})\s*#?(\d+))?\s*\|\s*([^\|\]]+?)(?:\s*\|[^\]]*)?\s*\](?:\*{1,2})?[:\s]*(.*)$'
    )
    # Pattern 3: [Role] [start-end]
    p3 = re.compile(
        r'^\s*(?:#?\d+[\.\:\-\)]\s*)?(?:\*{1,2})?\[\s*([^\]]+?)\s*\]\s*\[\s*#?(\d+)(?:\s*(?:[\-\–\—\~]|->|\.{2,3})\s*#?(\d+))?(?:\s*\|[^\]]*)?\s*\](?:\*{1,2})?[:\s]*(.*)$'
    )
    # Pattern 4: [start-end] [Role]
    p4 = re.compile(
        r'^\s*(?:#?\d+[\.\:\-\)]\s*)?(?:\*{1,2})?\[\s*#?(\d+)(?:\s*(?:[\-\–\—\~]|->|\.{2,3})\s*#?(\d+))?(?:\s*\|[^\]]*)?\s*\]\s*\[\s*([^\]]+?)\s*\](?:\*{1,2})?[:\s]*(.*)$'
    )

    for line in lines:
        line_clean = line.strip()
        if not line_clean or line_clean.startswith("```"):
            continue

        # 1. Thử khớp định dạng chuẩn v5 (Chốt vai ở cuối): [zh] Lời dịch -> [Role]
        # Phòng thủ: Xử lý trường hợp LLM chèn lặp ngoặc vai ở đầu dòng: [Tag1] [Tag2] Lời dịch -> [Role]
        double_bracket_m = re.match(
            r'^\s*(?:#?\d+[\.\:\-\)]\s*)?(?:\*{1,2})?\[\s*([^\]]+?)\s*\]\s*\[\s*([^\]]+?)\s*\](?:\*{1,2})?[:\s]*(.*)$',
            line_clean
        )
        if double_bracket_m:
            g1 = double_bracket_m.group(1).strip()
            g2 = double_bracket_m.group(2).strip()
            content = double_bracket_m.group(3).strip()
            if re.search(r'[\u4e00-\u9fff]', g2) and not re.search(r'[\u4e00-\u9fff]', g1):
                tag_content = g2
            elif re.search(r'[\u4e00-\u9fff]', g1):
                tag_content = g1
            else:
                tag_content = g2
            has_bracket = True
        else:
            bracket_m = re.match(r'^\s*(?:#?\d+[\.\:\-\)]\s*)?(?:\*{1,2})?\[\s*([^\]]+?)\s*\](?:\*{1,2})?[:\s]*(.*)$', line_clean)
            if bracket_m:
                tag_content = bracket_m.group(1).strip()
                content = bracket_m.group(2).strip()
                has_bracket = True
            else:
                has_bracket = False

        if has_bracket:

            def _normalize_role(r_str: str) -> str:
                r_clean = r_str.strip()
                if re.match(r'^\d+(?:[\-\–\—\~]|->|\.{2,3})?\d*$', r_clean):
                    return "DẪN CHUYỆN"
                elif re.match(r'^(?:NV\s+|Nhân\s*vật\s+|Vai\s+)?([A-Za-z])$', r_clean, re.IGNORECASE):
                    char_code = re.match(r'^(?:NV\s+|Nhân\s*vật\s+|Vai\s+)?([A-Za-z])$', r_clean, re.IGNORECASE).group(1).upper()
                    return f"Vai {char_code}"
                else:
                    role_upper = r_clean.upper()
                    if role_upper in ["NARRATOR", "DẪN CHUYỆN", "DAN CHUYEN", "DẪN", "KỂ", "KỂ CHUYỆN", "NGƯỜI KỂ", "THUYẾT MINH"]:
                        return "DẪN CHUYỆN"
                return r_clean

            # TH 1A: Định dạng mới: [zh] Lời dịch -> [Role] hoặc @[Role]
            tail_role_m = re.search(r'(?:->|–>|—>|@)\s*\[?\s*([^\]\n]+?)\s*\]?\s*$', content)
            if tail_role_m:
                role = _normalize_role(tail_role_m.group(1))
                vi_raw = content[:tail_role_m.start()].strip()
                vi = clean_system_tags_from_text(vi_raw)
                vi = re.sub(r'^\*+\s*', '', vi)
                vi = re.sub(r'\s*\*+$', '', vi)

                zh = tag_content
                item_dict = {"role": role, "src": [], "vi": vi}
                if zh:
                    item_dict["zh_raw"] = zh
                    item_dict["zh_pieces"] = [p.strip() for p in zh.split('/') if p.strip()]
                    item_dict["zh"] = zh.replace('/', '').strip()
                else:
                    item_dict["zh_pieces"] = []
                results.append(item_dict)
                continue

            # TH 1B: Định dạng cũ: [Role|zh] hoặc [Role|start-end|zh]
            parts = [p.strip() for p in tag_content.split('|') if p.strip()]
            if len(parts) >= 2:
                role = "DẪN CHUYỆN"
                s, e = 0, 0
                zh = ""
                for p_str in parts:
                    num_m = re.match(r'^#?(\d+)(?:\s*(?:[\-\–\—\~]|->|\.{2,3})\s*#?(\d+))?$', p_str)
                    if num_m:
                        s = int(num_m.group(1))
                        e = int(num_m.group(2)) if num_m.group(2) else s
                    elif re.search(r'[\u4e00-\u9fff]', p_str):
                        zh = p_str
                    else:
                        role = p_str
                vi = clean_system_tags_from_text(content)
                vi = re.sub(r'^\*+\s*', '', vi)
                vi = re.sub(r'\s*\*+$', '', vi)

                role = _normalize_role(role)

                item_dict = {"role": role, "src": [], "vi": vi}
                if zh:
                    item_dict["zh_raw"] = zh
                    item_dict["zh_pieces"] = [p.strip() for p in zh.split('/') if p.strip()]
                    item_dict["zh"] = zh.replace('/', '').strip()
                else:
                    item_dict["zh_pieces"] = []
                if s > 0:
                    item_dict["llm_claimed_src"] = [s, e]
                results.append(item_dict)
                continue

        # 2. Dự phòng các định dạng cũ (Legacy patterns với 2 cặp ngoặc [Role] [start-end]...)
        matched = False
        for p, role_first in [(p1, True), (p2, False), (p3, True), (p4, False)]:
            m = p.match(line_clean)
            if m:
                if role_first:
                    role_str, s_str, e_str, content = m.groups()
                else:
                    s_str, e_str, role_str, content = m.groups()

                s = int(s_str)
                e = int(e_str) if e_str else s
                role = role_str.strip()
                vi = clean_system_tags_from_text(content)
                vi = re.sub(r'^\*+\s*', '', vi)
                vi = re.sub(r'\s*\*+$', '', vi)

                if re.match(r'^\d+(?:[\-\–\—\~]|->|\.{2,3})?\d*$', role):
                    role = "DẪN CHUYỆN"
                elif re.match(r'^(?:NV\s+|Nhân\s*vật\s+|Vai\s+)?([A-Za-z])$', role, re.IGNORECASE):
                    char_code = re.match(r'^(?:NV\s+|Nhân\s*vật\s+|Vai\s+)?([A-Za-z])$', role, re.IGNORECASE).group(1).upper()
                    role = f"Vai {char_code}"
                else:
                    role_upper = role.upper()
                    if role_upper in ["NARRATOR", "DẪN CHUYỆN", "DAN CHUYEN", "DẪN", "KỂ", "KỂ CHUYỆN", "NGƯỜI KỂ", "THUYẾT MINH"]:
                        role = "DẪN CHUYỆN"

                bracket_inside = re.search(r'\[([^\]]+)\]', line_clean)
                zh = ""
                if bracket_inside:
                    for p_part in bracket_inside.group(1).split('|'):
                        if re.search(r'[\u4e00-\u9fff]', p_part):
                            zh = p_part.strip()
                            break

                item_res = {
                    "role": role,
                    "src": [],
                    "llm_claimed_src": [s, e],
                    "vi": vi
                }
                if zh:
                    item_res["zh_raw"] = zh
                    item_res["zh_pieces"] = [p.strip() for p in zh.split('/') if p.strip()]
                    item_res["zh"] = zh.replace('/', '').strip()
                else:
                    item_res["zh_pieces"] = []
                results.append(item_res)
                matched = True
                break

    return results
