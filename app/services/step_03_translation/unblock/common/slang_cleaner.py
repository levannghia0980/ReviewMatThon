import re

def clean_duplicate_slang_words(text: str) -> str:
    """
    Khử trùng lặp từ ngữ và sửa các lỗi ghép từ nhân bản sau khi Unmask.
    Thiết kế an toàn tuyệt đối: Không can thiệp vào các từ đơn thuần Việt,
    không bổ não thay đổi ngữ nghĩa câu văn của truyện.
    """
    if not text:
        return ""
    
    t = text

    # 1. Sửa các từ ghép Hán-Việt bị dịch nhầm gốc 'thao' (操) thành 'địtt'
    t = re.sub(r'(?i)\bđịtt\s+tác\b', 'thao tác', t)
    t = re.sub(r'(?i)\bđịtt\s+túng\b', 'thao túng', t)
    t = re.sub(r'(?i)\bđịtt\s+lộng\b', 'thao lộng', t)
    t = re.sub(r'(?i)\bđịtt\s+tâm\b', 'thao tâm', t)
    t = re.sub(r'(?i)\bđịtt\s+diễn\b', 'thao diễn', t)
    t = re.sub(r'(?i)\bđịtt\s+trì\b', 'thao trì', t)
    t = re.sub(r'(?i)\bthể\s+địtt\b', 'thể thao', t)

    # 2. Sửa lặp từ nhân bản do unmask ghép nối (VD: 'đầu cặc cặc', 'cổ cổ tử cung')
    t = re.sub(r'(?i)\bđầu\s+cặc(?:\s+cặc)+\b', 'đầu cặc', t)
    t = re.sub(r'(?i)\b(?:đầu\s+cặc\s+)+đầu\s+cặc\b', 'đầu cặc', t)
    t = re.sub(r'(?i)\b(?:quy\s+đầu\s+)+quy\s+đầu\b', 'quy đầu', t)
    t = re.sub(r'(?i)\b(?:cổ\s+){2,}tử\s+cung\b', 'cổ tử cung', t)
    t = re.sub(r'(?i)\b(?:tử\s+cung\s+)+tử\s+cung\b', 'tử cung', t)
    t = re.sub(r'(?i)\bcổ\s+tử\s+cung(?:\s+tử\s+cung)+\b', 'cổ tử cung', t)

    # 3. Khử trùng lặp cho các cụm từ ghép lặp liên tiếp 2 lần trở lên
    compound_dedups = [
        (r'(?i)\b(chó\s+cái)(?:\s+dâm\s+đãng)+\b', 'chó cái dâm đãng'),
        (r'(?i)\b(chó\s+cái\s+dâm\s+đãng)(?:\s+dâm\s+đãng|\s+chó\s+cái)+\b', r'\1'),
        (r'(?i)\b(con\s+đĩ)(?:\s+dâm\s+đãng)+\b', 'con đĩ dâm đãng'),
        (r'(?i)\b(dâm\s+phụ)(?:\s+lẳng\s+lơ|\s+dâm\s+đãng)+\b', r'\1 lẳng lơ'),
        (r'(?i)\b(tiếng\s+rên)(?:\s+dâm\s+mị)+\b', 'tiếng rên dâm mị'),
        (r'(?i)\b(tiếng\s+rên\s+dâm\s+mị)(?:\s+dâm\s+mị|\s+tiếng\s+rên)+\b', r'\1'),
        (r'(?i)\b(rên\s+la)(?:\s+dâm\s+đãng)+\b', 'rên la dâm đãng'),
        (r'(?i)\b(rên\s+la\s+dâm\s+đãng)(?:\s+dâm\s+đãng)+\b', r'\1'),
        (r'(?i)\b(búp\s+bê\s+tình\s+dục)(?:\s+tình\s+dục|\s+búp\s+bê)+\b', r'\1'),
        (r'(?i)\b(con\s+cặc\s+bự)(?:\s+con\s+cặc|\s+cặc|\s+bự)+\b', r'\1'),
        (r'(?i)\b(con\s+cặc)(?:\s+con\s+cặc|\s+cặc)+\b', r'\1'),
        (r'(?i)\b(côn\s+thịt)(?:\s+côn\s+thịt|\s+con\s+cặc|\s+cặc)+\b', r'\1'),
        (r'(?i)\b(lỗ\s+lồn)(?:\s+lỗ\s+lồn|\s+lồn)+\b', r'\1'),
        (r'(?i)\b(khe\s+lồn)(?:\s+khe\s+lồn|\s+lồn)+\b', r'\1'),
        (r'(?i)\b(mép\s+lồn)(?:\s+mép\s+lồn|\s+lồn)+\b', r'\1'),
        (r'(?i)\b(hoa\s+huyệt)(?:\s+hoa\s+huyệt|\s+lỗ\s+lồn)+\b', r'\1'),
        (r'(?i)\b(mật\s+huyệt)(?:\s+mật\s+huyệt|\s+lỗ\s+lồn)+\b', r'\1'),
        (r'(?i)\b(bầu\s+vú)(?:\s+bầu\s+vú|\s+vú|\s+ngực)+\b', r'\1'),
        (r'(?i)\b(cặp\s+vú)(?:\s+cặp\s+vú|\s+vú|\s+ngực)+\b', r'\1'),
        (r'(?i)\b(đầu\s+vú)(?:\s+đầu\s+vú|\s+núm\s+vú|\s+vú)+\b', r'\1'),
        (r'(?i)\b(núm\s+vú)(?:\s+núm\s+vú|\s+đầu\s+vú)+\b', r'\1'),
        (r'(?i)\b(bờ\s+mông)(?:\s+bờ\s+mông|\s+mông)+\b', r'\1'),
        (r'(?i)\b(cặp\s+mông)(?:\s+cặp\s+mông|\s+mông)+\b', r'\1'),
        (r'(?i)\b(cặp\s+đùi)(?:\s+cặp\s+đùi|\s+đùi)+\b', r'\1'),
        (r'(?i)\b(lỗ\s+đít)(?:\s+lỗ\s+đít|\s+đít)+\b', r'\1'),
        (r'(?i)\b(hòn\s+dái)(?:\s+hòn\s+dái|\s+dái)+\b', r'\1'),
        (r'(?i)\b(đút|cắm|đâm)\s+cặc(?:\s+vào\s+lồn|\s+vào)?\s+(lỗ\s+lồn|hoa\s+huyệt|khe\s+lồn)\b', r'\1 cặc vào \2'),
        (r'(?i)\b(địtt\s+nhau)(?:\s+địtt\s+nhau|\s+làm\s+tình)+\b', r'\1'),
        (r'(?i)\b(hoan\s+ái)(?:\s+hoan\s+ái|\s+địtt\s+nhau)+\b', r'\1'),
        (r'(?i)\b(mây\s+mưa)(?:\s+mây\s+mưa|\s+địtt\s+nhau)+\b', r'\1'),
        (r'(?i)\b(luân\s+phiên\s+cưỡng\s+hiếp\s+tập\s+thể)(?:\s+tập\s+thể)+\b', r'\1'),
        (r'(?i)\b(cưỡng\s+hiếp\s+cuồng\s+bạo)(?:\s+cuồng\s+bạo)+\b', r'\1'),
        (r'(?i)\b(cưỡng\s+hiếp\s+thô\s+bạo)(?:\s+thô\s+bạo)+\b', r'\1'),
        (r'(?i)\b(quần\s+tất)(?:\s+quần\s+tất|\s+tất)+\b', r'\1'),
        (r'(?i)\b(quần\s+lót)(?:\s+quần\s+lót|\s+quần\s+trong)+\b', r'\1'),
        (r'(?i)\b(áo\s+ngực)(?:\s+áo\s+ngực|\s+áo\s+lót)+\b', r'\1'),
        (r'(?i)\b(đồ\s+lót)(?:\s+đồ\s+lót)+\b', r'\1'),
    ]

    for p, repl in compound_dedups:
        t = re.sub(p, repl, t)

    # 4. Khử trùng lặp từ đơn lẻ lặp liên tiếp 2 lần trở lên (như 'tử cung tử cung', 'dâm loạn dâm loạn')
    single_slangs = [
        r'quần\s+tất', r'quần\s+lót', r'quần\s+lọt\s+khe', r'áo\s+ngực', r'đồ\s+lót',
        r'dâm\s+đãng', r'dâm\s+dục', r'dâm\s+mị', r'dâm\s+loạn', r'dâm\s+mỹ', r'dâm\s+tình',
        r'gợi\s+tình', r'khiêu\s+dâm', r'khoái\s+cảm', r'thở\s+dốc', r'rên\s+rỉ',
        r'tiếng\s+rên', r'chó\s+cái', r'nô\s+lệ', r'búp\s+bê\s+tình\s+dục',
        r'nước\s+lồn', r'dâm\s+dịch', r'tinh\s+dịch', r'tinh\s+trùng', r'lỗ\s+lồn', r'khe\s+lồn',
        r'mép\s+lồn', r'hoa\s+huyệt', r'mật\s+huyệt', r'con\s+cặc', r'con\s+cặc\s+bự',
        r'côn\s+thịt', r'bầu\s+vú', r'cặp\s+vú', r'đầu\s+vú', r'núm\s+vú',
        r'bờ\s+mông', r'cặp\s+mông', r'cặp\s+đùi', r'lỗ\s+đít', r'hòn\s+dái',
        r'bắn\s+tinh', r'phun\s+tinh', r'đút\s+cặc', r'rút\s+cặc', r'địtt\s+nhau',
        r'làm\s+tình', r'hoan\s+ái', r'mây\s+mưa', r'cưỡng\s+hiếp', r'thôi\s+miên',
        r'đầu\s+cặc', r'quy\s+đầu', r'cổ\s+tử\s+cung', r'tử\s+cung', r'cặc', r'lồn', r'địtt', r'địt'
    ]

    for s in single_slangs:
        t = re.sub(rf'(?i)\b({s})(?:\s+(?:\1))+\b', r'\1', t)

    # 5. Dọn khoảng trắng thừa
    t = re.sub(r'[ \t]{2,}', ' ', t)
    return t
