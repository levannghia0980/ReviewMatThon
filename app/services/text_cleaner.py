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
            
        # Tự động gộp các mẩu câu ngắn vụn bị ASR ngắt giữa chừng trong lời dẫn thoại
        merged_list = TextCleanerService.merge_short_adjacent_segments(cleaned_list, is_chinese=is_chinese)
        return merged_list

    @staticmethod
    def merge_short_adjacent_segments(
        segments: List[DialogueSegment],
        is_chinese: bool = True,
        max_gap_sec: float = 0.35,
        short_dur_sec: float = 1.25,
        short_char_count: int = 5,
        max_merged_dur: float = 5.5
    ) -> List[DialogueSegment]:
        """
        Tự động phát hiện và gộp các mẩu câu ngắn vụn bị ASR (CapCut/Whisper) ngắt giữa chừng
        khi người dẫn chuyện / nhân vật nói liên tục.
        - Tránh tình trạng sinh ra câu chỉ có 0.5s - 0.9s (như '末日之后', '大巴车的', '甚至能将...')
        làm sai lệch timecode start-end và ép TTS phải nói dồn dập giật cục.
        - Giúp câu văn hoàn chỉnh về ngữ nghĩa trước khi đưa vào LLM dịch thuật và TTS.
        """
        if not segments or len(segments) <= 1:
            return segments

        merged: List[DialogueSegment] = []
        i = 0
        n = len(segments)

        while i < n:
            curr = segments[i]
            curr_start = curr.start
            curr_end = curr.end
            curr_text = curr.text.strip()
            curr_clean = curr.clean_text.strip() if curr.clean_text else curr_text
            curr_confidence = curr.confidence

            while i + 1 < n:
                next_seg = segments[i + 1]
                gap = round(next_seg.start - curr_end, 3)
                combined_dur = round(next_seg.end - curr_start, 3)

                # Điều kiện gộp an toàn:
                # 1. Khoảng cách nghỉ giữa 2 câu cực nhỏ (gap <= max_gap_sec)
                # 2. Câu hiện tại hoặc câu kế tiếp là mẩu vụn ngắn
                # 3. Tổng thời lượng sau khi gộp không vượt quá giới hạn một câu tự nhiên
                curr_dur = round(curr_end - curr_start, 3)
                next_dur = round(next_seg.end - next_seg.start, 3)
                next_clean = next_seg.clean_text.strip() if next_seg.clean_text else next_seg.text.strip()

                is_curr_short = (curr_dur < short_dur_sec or len(curr_clean) <= short_char_count)
                is_next_short = (next_dur < short_dur_sec or len(next_clean) <= short_char_count)

                # Kiểm tra dấu kết câu hoàn chỉnh
                ends_complete = any(curr_clean.endswith(p) for p in ("。", "！", "？", "!", "?"))

                should_merge = False
                if gap <= max_gap_sec and combined_dur <= max_merged_dur:
                    if is_curr_short or is_next_short:
                        # Nếu câu trước chưa có dấu kết thúc, hoặc câu sau quá ngắn (< 0.8s hoặc <= 3 chữ)
                        if not ends_complete or (next_dur < 0.85 or len(next_clean) <= 4):
                            should_merge = True

                if should_merge:
                    curr_end = next_seg.end
                    sep = "，" if is_chinese else ", "
                    
                    if curr_text and not curr_text.endswith(("，", "。", "！", "？", ",", ".", "!", "?")):
                        curr_text = curr_text + sep + next_seg.text.strip()
                    else:
                        curr_text = curr_text + next_seg.text.strip()

                    if curr_clean and not curr_clean.endswith(("，", "。", "！", "？", ",", ".", "!", "?")):
                        curr_clean = curr_clean + sep + next_clean
                    else:
                        curr_clean = curr_clean + next_clean

                    curr_confidence = min(curr_confidence, next_seg.confidence)
                    i += 1
                else:
                    break

            merged.append(
                DialogueSegment(
                    id=len(merged) + 1,
                    start=round(curr_start, 3),
                    end=round(curr_end, 3),
                    duration=round(curr_end - curr_start, 3),
                    text=curr_text,
                    clean_text=curr_clean,
                    confidence=curr_confidence
                )
            )
            i += 1

        return merged
