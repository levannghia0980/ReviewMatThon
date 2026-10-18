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
        Bảo tồn 100% nội dung chữ Hán nguyên bản và thời gian xuất hiện.
        """
        items = []
        batch_base_s = 0.0
        if segments:
            first_s = getattr(segments[0], 'start', 0.0) or getattr(segments[0], 'start_time', 0.0) or 0.0
            batch_base_s = float(first_s)

        for idx, s in enumerate(segments):
            text = (s.clean_text or s.text or "").strip()
            text = re.sub(r'[\r\n\t]+', ' ', text).strip()
            
            # Tính mốc thời gian tương đối chuẩn hóa về 0.0s của lô (dễ nhìn, dễ tính khoảng cách)
            raw_start = float(getattr(s, 'start', 0.0) or getattr(s, 'start_time', 0.0) or 0.0)
            raw_end = float(getattr(s, 'end', 0.0) or getattr(s, 'end_time', 0.0) or 0.0)
            rel_start = max(0.0, round(raw_start - batch_base_s, 2))
            rel_end = max(rel_start, round(raw_end - batch_base_s, 2))

            gap_prev = 0.0
            if idx > 0:
                prev_end = float(getattr(segments[idx - 1], 'end', 0.0) or getattr(segments[idx - 1], 'end_time', 0.0) or 0.0)
                gap_prev = max(0.0, raw_start - prev_end)

            item_dict = {
                "i": idx + 1,
                "s": rel_start,
                "e": rel_end,
                "zh": text
            }
            # Mốc cứng 0.85s: Nếu khoảng cách nghỉ trước câu >= 0.85s, đánh dấu điểm ngắt cứng
            if gap_prev >= 0.85:
                item_dict["hard_break"] = True
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
        Gom toàn bộ nội dung chữ Hán nguyên bản của lô phục vụ LLM 1 trích xuất thực thể.
        Tuyệt đối không can thiệp, biến đổi hay chèn thêm nhãn vào nội dung gốc.
        """
        lines = []
        for s in segments:
            text = (s.clean_text or s.text or "").strip()
            if text:
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
