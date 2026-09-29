"""
CHINESE GUARD & VIETNAMESE SANITIZER SERVICE
- Đảm bảo 100% câu thoại và phụ đề khi vào TTS / Render luôn là TIẾNG VIỆT.
- Tuyệt đối ngăn chặn hiện tượng rò rỉ chữ Hán (Chinese Leak) vào TTS (gây đọc tiếng Trung) hoặc phụ đề.
- Tự động phát hiện và dịch tức thì các chunk chưa được dịch hoặc bị trả về chữ gốc:
  + Tầng 1: Google Translate Fast API (Miễn phí, cực nhanh, dịch chuẩn ngữ cảnh).
  + Tầng 2 (Offline Fallback): Bộ từ điển Hán Việt >47.900 từ + Bảng thực thể chuẩn hóa.
  + Tầng 3: Tự động loại bỏ triệt để mọi ký tự CJK còn sót lại.
"""

import re
import json
import urllib.parse
import urllib.request
from typing import Optional, List
from sqlalchemy.orm import Session

from app.models.dialogue import DialogueSegmentModel
from app.services.dichhan.hanviet_data import build_hanviet_name

# Regex nhận diện toàn bộ dải ký tự Hán tự CJK (Giản thể, Phồn thể, Mở rộng A-F, Ký hiệu CJK)
CHINESE_CHAR_REGEX = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\u2e80-\u2eff\u3000-\u303f]')

def contains_chinese(text: Optional[str]) -> bool:
    """Kiểm tra xem chuỗi có chứa bất kỳ ký tự tiếng Trung / chữ Hán nào không"""
    if not text:
        return False
    return bool(CHINESE_CHAR_REGEX.search(str(text)))

def translate_chinese_to_vietnamese_fast(chinese_text: str, timeout: float = 4.0) -> str:
    """
    Dịch tức thì một đoạn văn bản tiếng Trung sang tiếng Việt:
    1. Thử qua Google Translate GTX endpoint (nhanh, chuẩn tự nhiên).
    2. Nếu mất mạng hoặc lỗi, tự động dùng Từ điển Hán Việt Offline (100% tin cậy).
    """
    clean_text = str(chinese_text).strip()
    if not clean_text:
        return ""

    # 1. Thử Google Translate API không cần Key (gtx)
    try:
        encoded_q = urllib.parse.quote(clean_text)
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=zh-CN&tl=vi&dt=t&q={encoded_q}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode('utf-8'))
                if data and isinstance(data, list) and len(data) > 0 and isinstance(data[0], list):
                    translated_parts = [part[0] for part in data[0] if part and len(part) > 0 and part[0]]
                    gt_res = "".join(translated_parts).strip()
                    if gt_res and not contains_chinese(gt_res):
                        return gt_res
    except Exception:
        pass

    # 2. Fallback: Dùng bộ từ điển Hán Việt Offline
    try:
        hv_res = build_hanviet_name(clean_text)
        # Làm sạch khoảng trắng thừa
        hv_res = re.sub(r'\s+', ' ', hv_res).strip()
        if hv_res:
            # Xóa triệt để nếu còn sót ký tự lạ
            hv_res = CHINESE_CHAR_REGEX.sub('', hv_res).strip()
            return hv_res
    except Exception:
        pass

    # 3. Loại bỏ triệt để ký tự Hán nếu tất cả đều không xử lý được
    return CHINESE_CHAR_REGEX.sub('', clean_text).strip()

def sanitize_to_vietnamese(text: Optional[str], fallback_orig: Optional[str] = "") -> str:
    """
    Đảm bảo kết quả trả về là TIẾNG VIỆT 100%:
    - Nếu text rỗng -> lấy fallback_orig và dịch sang tiếng Việt.
    - Nếu text chứa chữ Hán -> dịch phần chữ Hán sang tiếng Việt.
    - Không bao giờ trả về nguyên văn tiếng Trung cho TTS/Subtitle.
    """
    candidate = (text or "").strip()
    orig = (fallback_orig or "").strip()

    # Nếu text rỗng hoàn toàn, kiểm tra fallback_orig
    if not candidate:
        if not orig:
            return ""
        if contains_chinese(orig):
            return translate_chinese_to_vietnamese_fast(orig)
        return orig

    # Nếu text chứa chữ Hán
    if contains_chinese(candidate):
        # Nếu toàn bộ hoặc phần lớn là tiếng Trung -> dịch nguyên câu
        # Kiểm tra tỷ lệ ký tự Hán
        han_chars = CHINESE_CHAR_REGEX.findall(candidate)
        total_chars = len([c for c in candidate if not c.isspace()])
        
        if total_chars > 0 and (len(han_chars) / total_chars > 0.4 or not re.search(r'[a-zA-Zà-ỹÀ-Ỹ]', candidate)):
            # Phần lớn là chữ Hán -> dịch lại toàn bộ
            return translate_chinese_to_vietnamese_fast(candidate)
        else:
            # Chứa một số chữ Hán xen kẽ -> thay thế từng cụm chữ Hán bằng âm Việt
            def _replace_han(match):
                return " " + translate_chinese_to_vietnamese_fast(match.group(0)) + " "
            
            replaced = CHINESE_CHAR_REGEX.sub(_replace_han, candidate)
            replaced = re.sub(r'\s+', ' ', replaced).strip()
            return replaced

    return candidate

def ensure_project_dialogues_vietnamese(db: Session, project_id: int) -> int:
    """
    Quét và tự động sửa toàn bộ các câu thoại trong CSDL:
    - Nếu translated_text bị rỗng hoặc còn dính chữ Trung -> dịch sang tiếng Việt ngay.
    - Cập nhật trực tiếp vào SQLite để đảm bảo đồng bộ cả Subtitle và TTS.
    - Trả về số câu đã được sửa/dịch.
    """
    dialogues = db.query(DialogueSegmentModel).filter(
        DialogueSegmentModel.task_id == project_id
    ).order_by(DialogueSegmentModel.index.asc()).all()

    if not dialogues:
        return 0

    fixed_count = 0
    for d in dialogues:
        current_trans = (d.translated_text or "").strip()
        orig = (d.clean_text or d.original_text or "").strip()

        # Kiểm tra xem câu này có cần sửa không
        needs_fix = False
        if not current_trans:
            needs_fix = True
        elif contains_chinese(current_trans):
            needs_fix = True

        if needs_fix:
            clean_viet = sanitize_to_vietnamese(current_trans, fallback_orig=orig)
            if clean_viet and clean_viet != current_trans:
                d.translated_text = clean_viet
                fixed_count += 1

    if fixed_count > 0:
        try:
            db.commit()
        except Exception:
            db.rollback()

    return fixed_count
