import re
from typing import Dict, List, Any, Optional, Tuple
from app.schemas.transcript import DialogueSegment

class PostProcessor:
    @staticmethod
    def parse_tagged_translation(
        raw_llm_output: str,
        original_segments: List[DialogueSegment]
    ) -> Dict[int, str]:
        """
        Phân tích cú pháp bóc tách kết quả dịch từ LLM và tự động chống lệch dòng (Anti-Shift Engine):
        1. Bóc tách danh sách các cặp (tag_id, text) theo thứ tự xuất hiện.
        2. Đối soát với danh sách ID gốc của lô (original_segments).
        3. Tự động khắc phục khi LLM đánh lại ID từ 1..N hoặc thiếu/thừa thẻ.
        """
        if not original_segments:
            return {}

        expected_ids = [s.id for s in original_segments]
        expected_id_set = set(expected_ids)
        min_expected_id = expected_ids[0]
        max_expected_id = expected_ids[-1]

        # 1. Trích xuất toàn bộ các thẻ theo thứ tự xuất hiện
        extracted_pairs: List[Tuple[int, str]] = []
        
        # Mẫu 0 (Ưu tiên số 1): Định dạng đánh số mỏ neo: ^(\d+)\.\s*(.+)$
        pattern_numbered = re.compile(r'^\s*(\d+)\.\s*(.+)$', re.MULTILINE)
        pairs_num = pattern_numbered.findall(raw_llm_output)
        if len(pairs_num) >= len(original_segments) * 0.7:
            for seg_id_str, text in pairs_num:
                clean_t = text.strip().strip('`').strip()
                if clean_t:
                    extracted_pairs.append((int(seg_id_str), clean_t))

        # Mẫu 1 Fallback: Thẻ XML chuẩn <s id="X">...</s> hoặc <s id='X'>...</s> hoặc <s id=X>...</s>
        if not extracted_pairs:
            pattern_xml = re.compile(r'<\s*s\s+id=[\'"]?(\d+)[\'"]?\s*>\s*(.*?)\s*<\s*/\s*s\s*>', re.DOTALL | re.IGNORECASE)
            for seg_id_str, text in pattern_xml.findall(raw_llm_output):
                clean_t = text.strip().strip('`').strip()
                if clean_t:
                    extracted_pairs.append((int(seg_id_str), clean_t))

        # Mẫu 2 Fallback: [#X] ... [#X]
        if len(extracted_pairs) < len(original_segments):
            pattern_pair = re.compile(r'\[#(\d+)\]\s*(.*?)\s*\[#\1\]', re.DOTALL)
            pairs_2 = pattern_pair.findall(raw_llm_output)
            if len(pairs_2) > len(extracted_pairs):
                extracted_pairs = [(int(sid), txt.strip().strip('`').strip()) for sid, txt in pairs_2 if txt.strip()]

        # Mẫu 3 Fallback: <s id="X"> ... (không đóng thẻ)
        if len(extracted_pairs) < len(original_segments):
            pattern_xml_open = re.compile(r'<\s*s\s+id=[\'"]?(\d+)[\'"]?\s*>\s*([^<\n]+)', re.IGNORECASE)
            pairs_3 = pattern_xml_open.findall(raw_llm_output)
            if len(pairs_3) > len(extracted_pairs):
                extracted_pairs = [(int(sid), txt.strip().strip('`').strip()) for sid, txt in pairs_3 if txt.strip()]

        translations: Dict[int, str] = {}

        if not extracted_pairs:
            return translations

        # --- CHIẾN LƯỢC ĐỐI SOÁT & CĂN CHỈNH ID THÔNG MINH ---
        extracted_ids = [p[0] for p in extracted_pairs]

        # TH1: Khớp số lượng 100% nhưng LLM tự đánh số lại từ 1..N (ví dụ Lô bắt đầu từ 301 nhưng LLM trả về 1..300)
        if len(extracted_pairs) == len(original_segments) and set(extracted_ids) != expected_id_set:
            # Map trực tiếp 1-1 theo chỉ số thứ tự tuần tự
            for idx, orig_seg in enumerate(original_segments):
                translations[orig_seg.id] = extracted_pairs[idx][1]
            return translations

        # TH2: Ưu tiên gán đúng 100% theo ID đích danh
        for seg_id, text in extracted_pairs:
            if seg_id in expected_id_set and seg_id not in translations:
                translations[seg_id] = text

        # Nếu đã có bản dịch theo đúng ID, trả về ngay (chống tuyệt đối việc dồn dòng)
        if translations:
            return translations

        # TH3: Fallback trường hợp đặc biệt LLM tự ý đánh lại ID từ 1..N
        if len(extracted_pairs) == len(original_segments):
            for idx, orig_seg in enumerate(original_segments):
                translations[orig_seg.id] = extracted_pairs[idx][1]

        return translations

    @staticmethod
    def clean_and_normalize_sentence(
        viet_text: str,
        orig_text: str
    ) -> str:
        """
        Chuẩn hóa dấu câu, sửa lỗi dính chữ, chống lặp từ và bảo toàn dấu kết câu gốc.
        """
        if not viet_text:
            return ""
            
        text = viet_text.strip()
        # Loại bỏ các thẻ XML hoặc dấu ngoặc thẻ câu còn sót
        text = re.sub(r'<\s*/?\s*s(?:\s+id=[\'"]?\d+[\'"]?)?\s*>', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\[#\d+\]', '', text).strip()
        # Chuẩn hóa khoảng trắng
        text = re.sub(r'\s+', ' ', text)
        
        # Bảo toàn dấu kết câu gốc nếu câu dịch bị mất dấu
        orig_tail = orig_text.strip()
        if orig_tail.endswith("？") or orig_tail.endswith("?"):
            if not text.endswith("?"):
                text = text.rstrip(".!?,") + "?"
        elif orig_tail.endswith("！") or orig_tail.endswith("!"):
            if not text.endswith("!"):
                text = text.rstrip(".!?,") + "!"
        elif orig_tail.endswith("……") or orig_tail.endswith("..."):
            if not text.endswith("..."):
                text = text.rstrip(".!?,") + "..."
                
        # Viết hoa chữ cái đầu câu
        if text and text[0].islower():
            text = text[0].upper() + text[1:]
            
        return text

    @classmethod
    def apply_post_processing(
        cls,
        segments: List[DialogueSegment],
        raw_llm_output: str
    ) -> List[DialogueSegment]:
        """
        Áp dụng toàn bộ quy trình hậu xử lý và gán bản dịch chuẩn vào từng câu thoại.
        """
        trans_map = cls.parse_tagged_translation(raw_llm_output, segments)
        
        updated_segments = []
        for s in segments:
            viet = trans_map.get(s.id, "")
            orig = s.clean_text or s.text or ""
            clean_viet = cls.clean_and_normalize_sentence(viet, orig)
            
            s.translated_text = clean_viet
            updated_segments.append(s)
            
        return updated_segments
