import re
from typing import List, Dict, Any, Tuple
from app.schemas.transcript import DialogueSegment

class BatchManager:
    CYCLIC_MARKS = ["。", "！", "？"]

    CYCLE_TAGS = ["@", "#", "$"]

    @classmethod
    def pack_dialogues_to_json_items(cls, segments: List[DialogueSegment]) -> List[Dict[str, Any]]:
        """
        Đóng gói danh sách câu thoại thành mảng Object JSON tinh gọn cho LLM:
        [{"i": 1, "zh": "全球诡异爆发。"}, ...]
        Dùng key ngắn gọn 'i' và 'zh' để tiết kiệm token tối đa cho Gemini Lite.
        Đặc biệt: Gắn thêm 'hint': 'nghi_van_chieu_thuc' cho các câu ngắn rơi vào nhịp khoảng lặng/hành động.
        """
        items = []
        for idx, s in enumerate(segments):
            text = (s.clean_text or s.text or "").strip()
            text = re.sub(r'[\r\n\t]+', ' ', text).strip()
            
            # Tính gap khoảng lặng trước và sau câu
            start_t = getattr(s, 'start', 0.0) or getattr(s, 'start_time', 0.0) or 0.0
            end_t = getattr(s, 'end', 0.0) or getattr(s, 'end_time', 0.0) or 0.0
            gap_prev = 0.0
            gap_next = 0.0
            if idx > 0:
                prev_end = getattr(segments[idx - 1], 'end', 0.0) or getattr(segments[idx - 1], 'end_time', 0.0) or 0.0
                gap_prev = max(0.0, start_t - prev_end)
            if idx < len(segments) - 1:
                next_start = getattr(segments[idx + 1], 'start', 0.0) or getattr(segments[idx + 1], 'start_time', 0.0) or 0.0
                gap_next = max(0.0, next_start - end_t)

            is_action_candidate = len(text) <= 5 and (gap_prev >= 1.0 or gap_next >= 1.0)

            start_t = round(float(getattr(s, 'start', 0.0) or getattr(s, 'start_time', 0.0) or 0.0), 2)
            end_t = round(float(getattr(s, 'end', 0.0) or getattr(s, 'end_time', 0.0) or 0.0), 2)
            
            item_dict = {
                "i": idx + 1,
                "s": start_t,
                "e": end_t,
                "zh": text
            }
            if is_action_candidate:
                item_dict["hint"] = "nghi_van_chieu_thuc"
            items.append(item_dict)
        return items

    @classmethod
    def pack_dialogues_to_tagged_text(cls, segments: List[DialogueSegment]) -> str:
        """
        Đóng gói danh sách câu thoại thành chuỗi JSON nạp thẳng vào LLM.
        """
        import json
        items = cls.pack_dialogues_to_json_items(segments)
        return json.dumps(items, ensure_ascii=False, indent=None)

    @staticmethod
    def pack_dialogues_to_clean_text(segments: List[DialogueSegment]) -> str:
        """
        Gom toàn bộ nội dung chữ Hán của lô thành văn bản mạch lạc phục vụ LLM 1 trích xuất thực thể.
        Nếu câu là nghi vấn chiêu thức, thêm nhãn nhẹ [nghi_van_chieu_thuc] để LLM 1 chú ý bóc tách.
        """
        lines = []
        for idx, s in enumerate(segments):
            text = (s.clean_text or s.text or "").strip()
            if text:
                start_t = getattr(s, 'start', 0.0) or getattr(s, 'start_time', 0.0) or 0.0
                end_t = getattr(s, 'end', 0.0) or getattr(s, 'end_time', 0.0) or 0.0
                gap_prev = 0.0
                gap_next = 0.0
                if idx > 0:
                    prev_end = getattr(segments[idx - 1], 'end', 0.0) or getattr(segments[idx - 1], 'end_time', 0.0) or 0.0
                    gap_prev = max(0.0, start_t - prev_end)
                if idx < len(segments) - 1:
                    next_start = getattr(segments[idx + 1], 'start', 0.0) or getattr(segments[idx + 1], 'start_time', 0.0) or 0.0
                    gap_next = max(0.0, next_start - end_t)

                if len(text) <= 5 and (gap_prev >= 1.0 or gap_next >= 1.0):
                    lines.append(f"{text} [nghi_van_chieu_thuc]")
                else:
                    lines.append(text)
        return "\n".join(lines)

    @staticmethod
    def create_batches(
        segments: List[DialogueSegment],
        batch_size: int = 300,
        max_chars: int = 50000
    ) -> List[List[DialogueSegment]]:
        """
        Chia danh sách câu thoại thành các lô (batches) linh hoạt dựa trên số câu hoặc ngưỡng ký tự Hán.
        Hỗ trợ lô lớn lên tới 15k - 20k ký tự Hán.
        """
        batches: List[List[DialogueSegment]] = []
        current_batch: List[DialogueSegment] = []
        current_chars = 0

        for seg in segments:
            text_len = len(seg.clean_text or seg.text or "")
            # Nếu vượt quá số câu hoặc vượt quá giới hạn ký tự thì ngắt lô
            if current_batch and (len(current_batch) >= batch_size or (current_chars + text_len > max_chars)):
                batches.append(current_batch)
                current_batch = []
                current_chars = 0

            current_batch.append(seg)
            current_chars += text_len

        if current_batch:
            batches.append(current_batch)

        return batches

    @staticmethod
    def get_context_from_previous_batch(prev_batch: List[DialogueSegment], max_sentences: int = 4) -> str:
        """
        Lấy 3-5 câu thoại cuối cùng của lô trước để làm ngữ cảnh nối tiếp (Seamless Continuity).
        """
        if not prev_batch:
            return ""
        
        last_segs = prev_batch[-max_sentences:]
        ctx_lines = []
        for s in last_segs:
            orig = (s.clean_text or s.text or "").strip()
            trans = (getattr(s, 'translated_text', '') or '').strip()
            if trans:
                ctx_lines.append(f"- Gốc: {orig} -> Dịch: {trans}")
            else:
                ctx_lines.append(f"- Gốc: {orig}")
        return "\n".join(ctx_lines)
