import re
from typing import Dict, List, Any, Optional, Tuple
from app.schemas.transcript import DialogueSegment

class PostProcessor:

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

        # TH0: BÓC TÁCH MẢNG JSON HOẶC DANH SÁCH HEALED ĐÃ QUA LÀM SẠCH (ƯU TIÊN SỐ 1 - CHÍNH XÁC TUYỆT ĐỐI)
        import json
        from app.core.llm_client import safe_json_loads
        
        if isinstance(raw_llm_output, list):
            parsed_json = raw_llm_output
        else:
            parsed_json = safe_json_loads(raw_llm_output)
            if isinstance(parsed_json, dict):
                for v in parsed_json.values():
                    if isinstance(v, list):
                        parsed_json = v
                        break

        if isinstance(parsed_json, list) and len(parsed_json) > 0:
            # Kiểm tra xem có phải định dạng câu gộp (có 'src' hoặc có 's' và 'e')
            is_merged_format = any(isinstance(it, dict) and ("src" in it or ("s" in it and "e" in it)) for it in parsed_json)
            if is_merged_format:
                # Nếu đã là list đã được làm sạch trước (từ SmartTimelineHealer) thì tái sử dụng trực tiếp
                if isinstance(raw_llm_output, list):
                    healed_items = raw_llm_output
                else:
                    from app.services.step_03_translation.smart_timeline_healer import SmartTimelineHealer
                    orig_dicts = [
                        {"index": i + 1, "global_id": getattr(s, 'id', getattr(s, 'index', i + 1)), "start": float(getattr(s, 'start', 0.0) or getattr(s, 'start_time', 0.0) or 0.0), "end": float(getattr(s, 'end', 0.0) or getattr(s, 'end_time', 0.0) or 0.0), "zh": s.clean_text or s.text or ""}
                        for i, s in enumerate(original_segments)
                    ]
                    healed_items, _, _ = SmartTimelineHealer.audit_and_heal(orig_dicts, parsed_json)
                
                # Ánh xạ câu gộp vào original_segments
                # Mỗi câu gốc nằm trong khoảng [s, e] của câu gộp sẽ nhận nội dung hoặc câu đầu nhận câu dịch, các câu sau rỗng
                # Để tương thích ngược tối đa: gán câu dịch trọn vẹn vào câu đầu tiên trong nhóm gộp
                for item in healed_items:
                    it_s = round(float(item.get("s", 0)), 2)
                    it_e = round(float(item.get("e", 0)), 2)
                    vi_val = str(item.get("vi", "")).strip()

                    # KHỬ SẠCH TIỀN TỐ TÊN NHÂN VẬT NẾU LLM LỠ IN THỪA (Ví dụ: "Giai Giai: Thôi quên đi..." -> "Thôi quên đi...")
                    role_val = (item.get("role") or item.get("speaker") or "").strip()
                    if role_val and vi_val.lower().startswith(f"{role_val.lower()}:"):
                        vi_val = vi_val[len(role_val) + 1:].strip()
                    # Khử cả pattern chung như "Nhân vật A: ..." hay "Hạ Phạn: ..."
                    vi_val = re.sub(r'^(?:[A-ZÀ-Ỹa-zà-ỹ0-9_\s]{2,15}|Nhân\s*vật\s*[A-Z0-9]):\s*', '', vi_val).strip()

                    # Ưu tiên tìm mẩu gốc theo mảng src (Chính xác tuyệt đối)
                    src_indices = item.get("src")
                    matched_segs = []
                    if src_indices and isinstance(src_indices, list) and len(src_indices) > 0:
                        # 1. Thử tìm theo ID tuyệt đối hoặc index của segment
                        matched_segs = [s for s in original_segments if getattr(s, 'id', None) in src_indices or getattr(s, 'index', None) in src_indices]
                        # 2. Nếu không khớp (ví dụ LLM đánh số tương đối 1..N trong lô), map theo 1-based index trong original_segments
                        if not matched_segs:
                            for idx in src_indices:
                                if isinstance(idx, int) and 1 <= idx <= len(original_segments):
                                    matched_segs.append(original_segments[idx - 1])
                    if not matched_segs:
                        # Fallback: Tìm các mẩu gốc nằm trong khoảng [it_s, it_e]
                        matched_segs = [
                            s for s in original_segments 
                            if round(float(getattr(s, 'start', 0.0) or getattr(s, 'start_time', 0.0) or 0.0), 2) >= it_s - 0.05
                            and round(float(getattr(s, 'end', 0.0) or getattr(s, 'end_time', 0.0) or 0.0), 2) <= it_e + 0.05
                        ]
                    # Lọc sạch tiền tố tên nhân vật nếu LLM vô tình chèn vào text
                    vi_val = re.sub(r'^(?:[A-Za-z0-9_À-ỹ\s]{1,25}|Nhân\s*vật\s*[A-Za-z0-9]+)\s*[:：]\s*', '', vi_val).strip()

                    if matched_segs:
                        role_val = item.get("role") or item.get("speaker") or "NARRATOR"
                        if len(matched_segs) == 1:
                            lead_seg = matched_segs[0]
                            result_map[lead_seg.id] = vi_val
                            lead_seg.role = role_val
                            lead_seg.speaker = role_val
                        else:
                            # Phân rã câu thông minh theo dấu câu tự nhiên cho từng mẩu ASR con
                            from app.services.step_05_video.subtitle_builder_service import SubtitleBuilderService
                            aligned = SubtitleBuilderService.demerge_sentence_aware(matched_segs, vi_val, role_val, int(item.get("batch_index", 1)))
                            for piece in aligned:
                                p_id = piece["id"]
                                result_map[p_id] = piece["vi"]
                            for s in matched_segs:
                                s.role = role_val
                                s.speaker = role_val
                                s_id = getattr(s, 'index', getattr(s, 'id', None))
                                if s_id not in result_map and getattr(s, 'id', None) not in result_map:
                                    result_map[s.id] = ""
                    elif original_segments:
                        # Fallback nếu không khớp chính xác
                        result_map[original_segments[0].id] = vi_val
                return result_map

            json_matched = 0
            for item in parsed_json:
                if isinstance(item, dict):
                    idx = item.get("i") or item.get("id") or item.get("index")
                    val = item.get("vi") or item.get("viet") or item.get("text") or item.get("translation") or ""
                    if idx is not None and isinstance(idx, int) and 1 <= idx <= n_orig:
                        clean_content = re.sub(r'[\r\n\t]+', ' ', str(val)).strip()
                        result_map[original_segments[idx - 1].id] = clean_content
                        json_matched += 1
            if json_matched > 0:
                return result_map

        # TH1: Fallback bóc tách theo số thứ tự tự nhiên (1., 2., 3. ...)
        lines = raw_llm_output.splitlines()
        num_matched = 0

        for line in lines:
            line_str = line.strip()
            if not line_str:
                continue
            m = re.match(r'^\s*(\d+)[\.\:\-\)\s]+(.*)$', line_str)
            if m:
                num = int(m.group(1))
                # Xóa sạch các ký tự rác và cú pháp JSON nếu có
                clean_content = cls.extract_clean_vietnamese_text(content)
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
        return {
            original_segments[i].id: clean_lines[i]
            for i in range(min(len(original_segments), len(clean_lines)))
        }

    @staticmethod
    def extract_clean_vietnamese_text(text: str) -> str:
        """
        Bóc tách và làm sạch triệt để nội dung tiếng Việt:
        - Nếu chuỗi bị dính JSON: {"i": 1, "vi": "..."} hoặc {"vi": "..."} -> trích xuất nội dung 'vi'
        - Bỏ ngoặc nhọn, nháy thừa, các tag rác
        """
        if not text:
            return ""
        t = text.strip()
        # 1. Thử parse nếu là JSON object hoặc chứa regex "vi": "..."
        if "{" in t or '"vi"' in t or "'vi'" in t:
            m_vi = re.search(r'["\']vi["\']\s*:\s*["\']([^"\']+)["\']', t)
            if m_vi:
                t = m_vi.group(1).strip()
            else:
                try:
                    import json
                    json_str = re.search(r'\{.*\}', t)
                    if json_str:
                        obj = json.loads(json_str.group(0))
                        if isinstance(obj, dict) and "vi" in obj:
                            t = str(obj["vi"]).strip()
                except Exception:
                    pass
            # Xóa các ký tự cú pháp JSON rác còn sót nếu có
            t = re.sub(r'^\s*\{+\s*["\']?i["\']?\s*:\s*\d+\s*,\s*["\']?vi["\']?\s*:\s*["\']?', '', t)
            t = re.sub(r'["\']?\s*\}+\s*[\.\,\;]?\s*$', '', t)
        t = re.sub(r'[\r\n\t]+', ' ', t).strip()
        return t

    @staticmethod
    def clean_and_normalize_sentence(
        viet_text: str,
        orig_text: str
    ) -> str:
        """
        Chuẩn hóa câu sau khi tra cứu ma trận:
        - Bóc tách sạch 100% cú pháp JSON {"i": ..., "vi": "..."}
        - Chuẩn hóa dấu câu cho mẩu phụ đề Karaoke (dialogue_segments), không áp dụng cho kịch bản TTS.
        """
        if not viet_text:
            return ""
            
        text = PostProcessor.extract_clean_vietnamese_text(viet_text)
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
        if orig_tail.endswith(("？", "?")):
            text = text + "?"
        elif orig_tail.endswith(("！", "!")):
            text = text + "!"
        elif orig_tail.endswith(("……", "...")):
            text = text + "..."
        elif orig_tail.endswith(("，", ",")):
            text = text + ","
        elif orig_tail.endswith(("。", ".")):
            text = text + "."
        # TUYỆT ĐỐI KHÔNG tự ý chèn dấu chấm "." nếu mẩu gốc không có dấu ngắt câu!

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
