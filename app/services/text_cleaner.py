import re
from typing import List
from app.schemas.transcript import DialogueSegment

# Các từ đệm / thán từ tiếng Trung và tiếng Việt phổ biến cần lọc
CHINESE_FILLERS = [
    "呃", "啊", "嗯", "吧", "呢", "啦", "哎", "哇", "呀", "哦", "呐", "哈",
    "那个", "这个", "就是说"
]

VIETNAMESE_FILLERS = [
    "ừm", "à", "ừ", "hả", "nè", "nha", "đấy", "thì", "vậy", "á", "ơ", "ê"
]

class TextCleanerService:
    @staticmethod
    def clean_text_string(text: str, is_chinese: bool = True) -> str:
        if not text:
            return ""
            
        t = text.strip()
        
        # 1. Loại bỏ các ký tự rác / nhãn AI hallucination kiểu: [Music], (Laughter), [Applause]
        t = re.sub(r"\[.*?\]|\(.*?\)|【.*?】|（.*?）", "", t).strip()
        
        # 2. Xóa từ đệm đầu câu / lặp từ vô nghĩa
        if is_chinese:
            # Xóa từ đệm tiếng Trung đầu câu: "呃，今天..." -> "今天..."
            t = re.sub(r"^[呃啊嗯哎哇呀哦呐哈\s，,。]+", "", t)
            # Chuẩn hóa khoảng trắng tiếng Trung
            t = re.sub(r"\s+", "", t)
        else:
            t = re.sub(r"\s+", " ", t)

        return t.strip()

    @staticmethod
    def clean_segments(segments: List[DialogueSegment], is_chinese: bool = True) -> List[DialogueSegment]:
        cleaned_list: List[DialogueSegment] = []
        new_id = 1
        last_clean = None
        consecutive_repeats = 0
        
        for seg in segments:
            raw_text = seg.text.strip()
            clean_txt = TextCleanerService.clean_text_string(raw_text, is_chinese=is_chinese)
            
            # Chỉ bỏ qua nếu thực sự không có chữ nào
            if not clean_txt or len(clean_txt) == 0:
                continue

            # Chặn đứng vòng lặp ảo giác (Anti-Hallucination Loop Breaker)
            norm_check = re.sub(r"[^\w\u4e00-\u9fff]", "", clean_txt)
            if norm_check and norm_check == last_clean:
                consecutive_repeats += 1
                if consecutive_repeats >= 2:
                    # Bỏ qua không cho lặp từ lần thứ 3 trở đi
                    continue
            else:
                last_clean = norm_check
                consecutive_repeats = 0

            cleaned_list.append(
                DialogueSegment(
                    id=new_id,
                    start=round(seg.start, 3),
                    end=round(seg.end, 3),
                    duration=round(seg.end - seg.start, 3),
                    text=raw_text,
                    clean_text=clean_txt,
                    confidence=seg.confidence
                )
            )
            new_id += 1
            
        return cleaned_list
