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
        [{"i": 1, "max_words": 4, "zh": "全球诡异爆发"}, ...]
        Truyền hạn mức số từ max_words (tính theo dur * 3.3 từ/s) để LLM kiểm soát độ dài.
        Nếu khoảng cách giữa 2 câu liên tiếp >= 1.0s: đánh dấu "no_merge": true.
        """
        items = []
        for idx, s in enumerate(segments):
            text = (s.clean_text or s.text or "").strip()
            text = re.sub(r'[\r\n\t]+', ' ', text).strip()

            raw_start = float(getattr(s, 'start', 0.0) or getattr(s, 'start_time', 0.0) or 0.0)
            raw_end = float(getattr(s, 'end', 0.0) or getattr(s, 'end_time', 0.0) or 0.0)
            raw_dur = float(getattr(s, 'duration', 0.0) or (raw_end - raw_start) or 0.1)
            dur = max(0.1, round(raw_dur, 2))
            max_words = max(1, int(round(dur * 3.3)))

            # Kiểm tra khoảng cách với câu trước: nếu gap >= 0.4s thì câu trước đó bắt buộc ngắt, cấm gộp
            if idx > 0:
                prev_end = float(getattr(segments[idx - 1], 'end', 0.0) or getattr(segments[idx - 1], 'end_time', 0.0) or 0.0)
                gap_prev = max(0.0, raw_start - prev_end)
                if gap_prev >= 0.4 and items:
                    items[-1]["no_merge"] = True

            item_dict: Dict[str, Any] = {
                "i": idx + 1,
                "max_words": max_words,
                "zh": text
            }
            items.append(item_dict)
        return items

    @classmethod
    def pack_dialogues_to_tagged_text(cls, segments: List[DialogueSegment]) -> str:
        """
        Đóng gói danh sách câu thoại thành định dạng dòng kịch bản siêu tinh gọn (tiết kiệm 50% token):
        1. 全球诡异爆发 (max 4 từ)
        2. 诡异降临 (max 6 từ) [KHÓA]
        3. 快跑！ (max 3 từ)
        - Đánh số thứ tự 1..N trực quan cho LLM bám sát, chống mất dấu câu.
        - Hiển thị hạn mức số từ tối đa (max X từ) để LLM kiểm soát chặt độ dài câu, tốc độ luôn dưới 1.3x.
        - Gắn tag [KHÓA] ở cuối mẩu nếu khoảng cách nghỉ trước mẩu sau >= 0.4s (ranh giới ngắt cứng).
        """
        lines = []
        for idx, s in enumerate(segments):
            text = (s.clean_text or s.text or "").strip()
            text = re.sub(r'[\r\n\t]+', ' ', text).strip()
            if not text:
                text = "..."

            raw_start = float(getattr(s, 'start', 0.0) or getattr(s, 'start_time', 0.0) or 0.0)
            raw_end = float(getattr(s, 'end', 0.0) or getattr(s, 'end_time', 0.0) or 0.0)
            raw_dur = float(getattr(s, 'duration', 0.0) or (raw_end - raw_start) or 0.1)
            dur = max(0.1, round(raw_dur, 2))
            max_words = max(1, int(round(dur * 3.3)))

            lock_tag = ""
            if idx + 1 < len(segments):
                next_s = segments[idx + 1]
                next_start = float(getattr(next_s, 'start', 0.0) or getattr(next_s, 'start_time', 0.0) or 0.0)
                if (next_start - raw_end) >= 0.4:
                    lock_tag = " [KHÓA]"

            lines.append(f"{idx + 1}. {text} (max {max_words} từ){lock_tag}")

        return "\n".join(lines)

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
