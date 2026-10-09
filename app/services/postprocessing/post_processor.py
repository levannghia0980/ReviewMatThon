import re
from typing import Dict, List, Any, Optional, Tuple
from app.schemas.transcript import DialogueSegment

class PostProcessor:
    CYCLIC_MARKS = [".", "!", "?"]

    @staticmethod
    def get_line_key(line: str) -> int:
        t = line.strip()
        if t.endswith("?"):
            return 3
        elif t.endswith("!"):
            return 2
        elif t.endswith("."):
            return 1
        return 0

    @classmethod
    def align_cyclic_modulo_3(
        cls,
        original_segments: List[DialogueSegment],
        translated_lines: List[str]
    ) -> Dict[int, str]:
        """
        Thuật toán Ma trận Tra cứu & Khôi phục chu kỳ Modulo 3:
        Mỗi chu kỳ gồm 3 khóa tuần hoàn:
        1: Dấu chấm (.)
        2: Dấu than (!)
        3: Dấu hỏi (?)
        Dựa trên Needleman-Wunsch DP để cô lập triệt để lỗi mất câu/thừa câu,
        tuyệt đối không làm trôi lệch bất kỳ câu nào ở các chu kỳ tiếp theo.
        """
        N = len(original_segments)
        M = len(translated_lines)
        if N == 0 or M == 0:
            return {}

        # Nếu số dòng khớp 100%: Map 1-1 trực tiếp theo thứ tự tuần tự
        if N == M:
            return {original_segments[i].id: translated_lines[i] for i in range(N)}

        # Khởi tạo bảng quy hoạch động DP
        dp = [[-1e9] * (M + 1) for _ in range(N + 1)]
        backtrack = [[(0, 0)] * (M + 1) for _ in range(N + 1)]
        
        dp[0][0] = 0.0
        for i in range(1, N + 1):
            dp[i][0] = dp[i-1][0] - 5.0
            backtrack[i][0] = (i - 1, 0)
        for j in range(1, M + 1):
            dp[0][j] = dp[0][j-1] - 5.0
            backtrack[0][j] = (0, j - 1)

        for i in range(1, N + 1):
            orig_seg = original_segments[i - 1]
            orig_t = (orig_seg.clean_text or orig_seg.text or "").strip()
            exp_key = ((i - 1) % 3) + 1  # 1 for '.', 2 for '!', 3 for '?'
            zh_len = max(1, len(orig_t))

            for j in range(1, M + 1):
                trans_t = translated_lines[j - 1]
                obs_key = cls.get_line_key(trans_t)
                vi_words = max(1, len(trans_t.split()))

                score = 0.0
                if exp_key == obs_key:
                    score += 8.0  # Chìa khóa trùng khớp
                elif obs_key == 0:
                    score += 1.0  # Không có dấu
                else:
                    score -= 6.0  # Lệch chìa khóa

                ratio = vi_words / zh_len
                if 0.3 <= ratio <= 3.0:
                    score += 2.0
                else:
                    score -= 3.0

                val_match = dp[i - 1][j - 1] + score
                val_skip_orig = dp[i - 1][j] - 4.0
                val_skip_trans = dp[i][j - 1] - 4.0

                best_val = val_match
                best_prev = (i - 1, j - 1)

                if val_skip_orig > best_val:
                    best_val = val_skip_orig
                    best_prev = (i - 1, j)

                if val_skip_trans > best_val:
                    best_val = val_skip_trans
                    best_prev = (i, j - 1)

                dp[i][j] = best_val
                backtrack[i][j] = best_prev

        # Truy vết kết quả
        curr_i, curr_j = N, M
        result_map = {}
        while curr_i > 0 or curr_j > 0:
            prev_i, prev_j = backtrack[curr_i][curr_j]
            if prev_i == curr_i - 1 and prev_j == curr_j - 1:
                result_map[original_segments[curr_i - 1].id] = translated_lines[curr_j - 1]
            elif prev_i == curr_i - 1 and prev_j == curr_j:
                result_map[original_segments[curr_i - 1].id] = ""
            curr_i, curr_j = prev_i, prev_j

        return result_map

    @classmethod
    def parse_tagged_translation(
        cls,
        raw_llm_output: str,
        original_segments: List[DialogueSegment]
    ) -> Dict[int, str]:
        """
        Bóc tách kết quả dịch từ LLM bằng định dạng đánh số tự nhiên: 1. 2. 3. ... N.
        1. Quét mọi dòng bắt đầu bằng số thứ tự: ^\s*(\d+)[\.\:\-\)\s]+(.*)$
        2. Dùng chính xác số thứ tự (1-based index) để gán trực tiếp vào câu gốc:
           result_map[original_segments[idx - 1].id] = text
        3. Nếu câu nào bị thiếu số: Câu đó tự động để trống, CÁC CÂU KHÁC CÓ SỐ VẪN ĐỨNG ĐÚNG CHỖ,
           TUYỆT ĐỐI KHÔNG BAO GIỜ BỊ TRÔI LỆCH TIMECODE DÂY CHUYỀN!
        4. Vẫn hỗ trợ fallback các định dạng khác nếu có.
        """
        if not original_segments:
            return {}

        result_map: Dict[int, str] = {}
        n_orig = len(original_segments)

        # TH1: Bóc tách theo số thứ tự tự nhiên (1., 2., 3. ...)
        lines = raw_llm_output.splitlines()
        num_matched = 0

        for line in lines:
            line_str = line.strip()
            if not line_str:
                continue
            m = re.match(r'^\s*(\d+)[\.\:\-\)\s]+(.*)$', line_str)
            if m:
                num = int(m.group(1))
                content = m.group(2).strip()
                # Xóa sạch các ký tự rác nếu có
                clean_content = re.sub(r'[\r\n\t]+', ' ', content).strip()
                if 1 <= num <= n_orig:
                    result_map[original_segments[num - 1].id] = clean_content
                    num_matched += 1

        # Nếu tìm thấy định dạng số thứ tự hợp lệ
        if num_matched > 0:
            return result_map

        # TH2: Fallback bóc tách theo bộ thẻ chu kỳ [0] -> [9]
        cycle_matches = re.findall(r'\[(\d)\]\s*([^\[\n\r]+)', raw_llm_output)
        if cycle_matches:
            i = 0
            for tag_str, raw_text in cycle_matches:
                clean_text = re.sub(r'[\r\n\t]+', ' ', raw_text).strip()
                if i < n_orig:
                    result_map[original_segments[i].id] = clean_text
                    i += 1
            return result_map

        # TH3: Fallback parse theo dòng thường
        raw_lines = [l.strip() for l in raw_llm_output.splitlines() if l.strip()]
        for idx, text in enumerate(raw_lines):
            if idx < n_orig:
                result_map[original_segments[idx].id] = text
        return result_map

        # TH2: Fallback bóc tách theo dấu '#' ở đầu dòng
        hash_lines = []
        for line in raw_llm_output.splitlines():
            line_str = line.strip()
            if line_str.startswith("#"):
                content = re.sub(r'^#+\s*', '', line_str).strip()
                content = re.sub(r'§[^§]+§', '', content).strip()
                content = re.sub(r'^(?:#+|\*+|-+)?\s*(?:\d+[\.\:\-\)\/\]\s]+)+', '', content).strip()
                if content:
                    hash_lines.append(content)

        if hash_lines:
            for idx, text in enumerate(hash_lines):
                if idx < n_orig:
                    result_map[original_segments[idx].id] = text
                else:
                    last_id = original_segments[-1].id
                    result_map[last_id] = (result_map.get(last_id, "") + " " + text).strip()
            return result_map

        # TH2: Fallback bóc tách theo thẻ §...§ (nếu LLM quen tay trả về dạng §#§)
        cycle_pattern = re.compile(r'§[^§]+§\s*([^§]*)', re.MULTILINE)
        cycle_matches = cycle_pattern.findall(raw_llm_output)
        if cycle_matches:
            for idx, raw_text in enumerate(cycle_matches):
                clean_text = re.sub(r'[\r\n\t]+', ' ', raw_text).strip()
                clean_text = re.sub(r'^(?:#+|\*+|-+)?\s*(?:\d+[\.\:\-\)\/\]\s]+)+', '', clean_text).strip()
                if idx < n_orig:
                    result_map[original_segments[idx].id] = clean_text
                elif n_orig > 0:
                    last_id = original_segments[-1].id
                    result_map[last_id] = (result_map.get(last_id, "") + " " + clean_text).strip()
            return result_map

        # TH3: Fallback bóc tách thẻ số §(\d+)§ cũ (tương thích ngược)
        pattern = re.compile(r'§(\d+)§\s*([\s\S]*?)(?=(?:§\d+§|$))', re.MULTILINE)
        matches = pattern.findall(raw_llm_output)
        if matches:
            for str_id, content in matches:
                try:
                    seg_id = int(str_id)
                    clean_content = re.sub(r'[\r\n\t]+', ' ', content).strip()
                    clean_content = re.sub(r'^(?:#+|\*+|-+)?\s*(?:\d+[\.\:\-\)\/\]\s]+)+', '', clean_content)
                    if clean_content:
                        result_map[seg_id] = clean_content
                except Exception:
                    pass
            return result_map

        # TH4: Fallback parse theo dòng thường
        raw_lines = [l.strip() for l in raw_llm_output.splitlines() if l.strip()]
        clean_lines = []
        for l in raw_lines:
            t = re.sub(r'§[@#$\d]+§', '', l)
            t = re.sub(r'^(?:#+|\*+|-+)?\s*(?:\d+[\.\:\-\)\/\]\s]+)+', '', t).strip()
            if t:
                clean_lines.append(t)
        return cls.align_cyclic_modulo_3(original_segments, clean_lines)

    @staticmethod
    def clean_and_normalize_sentence(
        viet_text: str,
        orig_text: str
    ) -> str:
        """
        Chuẩn hóa câu sau khi tra cứu ma trận:
        - Xóa bỏ dấu neo xoay vòng giả lập (. ! ? modulo 3)
        - Khôi phục dấu câu ngữ pháp tự nhiên chuẩn cho TTS
        """
        if not viet_text:
            return ""
            
        text = viet_text.strip()
        # Loại bỏ các thẻ XML hoặc số thứ tự đầu câu còn sót
        text = re.sub(r'<\s*/?\s*s(?:\s+id=[\'"]?\d+[\'"]?)?\s*>', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\[#\d+\]', '', text).strip()
        text = re.sub(r'^(?:\[\s*\d+\s*\]|\(\s*\d+\s*\)|\{\s*\d+\s*\}|【\s*\d+\s*】)\s*[\.\:\-\–\—\s]*', '', text)
        text = re.sub(r'^(?:câu|thoại|đoạn|stt|dòng|line)\s*\d+\s*[\.\:\-\–\—\)\/\]\s]*\s*', '', text, flags=re.IGNORECASE)
        text = re.sub(r'^\d+\/\d+\s*[\.\:\-\–\—\s]*', '', text)
        text = re.sub(r'^\d+\s*[\.\:\-\–\—\)\/\]]+\s*', '', text)
        text = re.sub(r'^[^\w\s\(\[\{]+', '', text).strip()
        text = re.sub(r'\s+', ' ', text)
        
        # Xóa các cụm dấu dị tật kép hoặc dấu khóa xoay vòng ở đuôi chuỗi
        text = re.sub(r'[\.\,\!\?\…\:\;\—\-\_\s]+$', lambda m: '', text).strip()

        # Khôi phục dấu câu tự nhiên dựa trên câu gốc thực sự:
        orig_tail = (orig_text or "").strip()
        if orig_tail.endswith("？") or orig_tail.endswith("?"):
            text = text + "?"
        elif orig_tail.endswith("！") or orig_tail.endswith("!"):
            text = text + "!"
        elif orig_tail.endswith("……") or orig_tail.endswith("..."):
            text = text + "..."
        elif orig_tail.endswith("，") or orig_tail.endswith(","):
            text = text + ","
        else:
            text = text + "."

        # Dọn dẹp triệt để nếu vẫn còn dị tật dấu câu kép như ., ,. ,, ..
        text = re.sub(r'[\,\.]+\,', ',', text)
        text = re.sub(r'[\,\.]+\.', '.', text)

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
