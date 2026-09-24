import re
from typing import List

# Các mẫu rác quảng cáo TOÀN DÒNG (chỉ xóa khi cả dòng là quảng cáo, không xóa giữa câu)
FULL_LINE_AD_PATTERNS = [
    re.compile(r'^\s*https?://\S+\s*$'),
    re.compile(r'^\s*www\.\S+\s*$'),
    re.compile(r'^\s*m\.[a-zA-Z0-9.-]+\.[a-zA-Z]+\s*$'),
    re.compile(r'^\s*请记住本书首发域名：.*$'),
    re.compile(r'^\s*最新章节txt下载：.*$'),
    re.compile(r'^\s*本书域名：.*$'),
    re.compile(r'^\s*手机用户请浏览.*阅读.*$'),
    re.compile(r'^\s*一秒记住【.*】.*$'),
    re.compile(r'^\s*百度搜索【.*】.*$'),
    re.compile(r'^\s*【\s*推荐下.*换源.*】\s*$'),
    re.compile(r'^\s*p\s*s\s*[：:].*求.*$', re.IGNORECASE),
    re.compile(r'^\s*\(?笔趣阁\)?\s*$'),
    re.compile(r'^\s*\(?UU看书\)?\s*$'),
    re.compile(r'^\s*\(?全本小说网\)?\s*$'),
    re.compile(r'^\s*\(?起点中文网\)?\s*$'),
    re.compile(r'^\s*\(?看书神站\)?\s*$'),
    re.compile(r'^\s*\(?飞卢小说网\)?\s*$'),
]


def sanitize_chinese_raw_text(text: str) -> str:
    """
    Chuẩn hóa văn bản gốc tiếng Trung (RAW) trước khi gửi LLM.
    Lọc bỏ rác crawler metadata (icon web, số từ, thời gian cào web, tên tác giả crawler rác) để AI không dịch nhầm vào truyện.
    """
    if not text:
        return text

    t = text.replace('\r\n', '\n').replace('\r', '\n')
    
    # Loại bỏ các dòng rác crawler website Trung Quốc
    t = re.sub(r'(?m)^\s*[]\s*\n?', '', t)
    t = re.sub(r'(?m)^\s*(?:蘑菇面要加蛋|Mì\s+nấm\s+.*)\s*\n?', '', t)
    t = re.sub(r'(?m)^\s*\d+\s*(?:字|Chữ|từ)\s*\n?', '', t)
    t = re.sub(r'(?m)^\s*\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\s*\n?', '', t)
    # Loại bỏ tiền tố "Chương XXX:" ở đầu văn bản (do crawler tự gắn, thường bị lệch số chương so với thực tế truyện)
    t = re.sub(r'^\s*Chương\s*\d+\s*[:\-–]\s*', '', t, flags=re.IGNORECASE)
    
    # Loại bỏ các nhãn kết thúc chương và lời xin phiếu của tác giả làm LLM hiểu lầm ngắt chương sớm
    t = re.sub(r'[（\(]\s*(?:求追读|求月票|求推荐|求收藏|求花|求鲜花|求订|求打赏|求订阅|本章完|全书完|全剧终)[!！]?\s*[）\)]', '', t)
    t = re.sub(r'(?m)^\s*(?:本章完|全书完|全剧终)\s*$', '', t)
    # Chuẩn hóa dòng chỉ có dấu chấm lửng cô lập thành dấu phân đoạn nhẹ nhàng
    t = re.sub(r'(?m)^\s*……\s*$', '……', t)

    # Chuẩn hóa các chữ Hán phồn thể phổ biến rò rỉ từ web lậu sang giản thể chuẩn
    t = re.sub(r'(?<=[^\w\s])著|(?<=[\u4e00-\u9fff])著', '着', t)  # 摸著天 -> 摸着天, 看著 -> 看着, 拿著 -> 拿着
    t = t.replace('摸著天', '摸着天')
    t = t.replace('杓子', '勺子').replace('握杓', '握勺')
    t = t.replace('裏面', '里面').replace('這裏', '这里').replace('那裏', '那里')
    t = t.replace('麼', '么')

    # Chuẩn hóa các từ lóng mạng Trung Quốc dễ kích hoạt nhầm bộ lọc Gemini (PROHIBITED_CONTENT / HARASSMENT)
    # Ví dụ: '舔狗' (simp/kẻ lụy tình) bị Google Perspective API / Gemini hiểu nhầm là từ ngữ nhục mạ cực đoan
    t = t.replace('做舔狗', '做深情之人')
    t = t.replace('当舔狗', '当深情之人')
    t = t.replace('成了舔狗', '成了深情之人')
    t = t.replace('十年的舔狗', '十年的深情')
    t = t.replace('十年舔狗', '十年深情')
    t = t.replace('舔狗', '深情者')
    t = t.replace('我舔了那个女人十年', 'ta si tình vì nàng mười năm')
    t = t.replace('我舔了', 'ta si tình vì')
    
    # LẤY CÁC DÒNG SẠCH
    raw_lines = [line.strip() for line in t.split('\n') if line.strip()]
    if not raw_lines:
        return ""
        
    # Dòng 1: Tiêu đề chương
    title_line = raw_lines[0]
    
    # Tất cả các dòng thân bài tiếp theo: Nối liền 100% không có ký tự xuống dòng (\n) nào!
    body_lines = raw_lines[1:]
    if body_lines:
        body_str = "".join(body_lines)
        return f"{title_line}\n{body_str}"
    else:
        return title_line

