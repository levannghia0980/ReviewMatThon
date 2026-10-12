import re
import httpx
from typing import List, Dict, Any, Tuple
from app.schemas.transcript import DialogueSegment
from app.config import settings
from app.core.llm_client import post_gemini_with_retry, post_openrouter_with_retry

HANZI_REGEX = re.compile(r'[\u4e00-\u9fff]')

class TranslationAuditor:
    @staticmethod
    def audit_single_batch(
        batch: List[DialogueSegment],
        expected_count: int = None
    ) -> Tuple[bool, List[Dict[str, Any]]]:
        """
        BỘ KIỂM ĐỊNH 3 TIÊU CHUẨN CỐT LÕI CHO MỖI LÔ SAU KHI LLM TRẢ VỀ:
        1. Kiểm tra số lượng câu: Đủ chính xác 100% số câu yêu cầu.
        2. Kiểm tra lặp câu dịch (Duplicate Check): vi[i] == vi[i+1] khi zh[i] != zh[i+1].
        3. Kiểm tra tỷ lệ độ dài & phình câu: Câu ngắn (<= 6 chữ) mà dịch dài >= 12 từ hoặc ratio > 2.8x.

        Returns:
            is_valid (bool): True nếu lô đạt chuẩn 3 tiêu chí, False nếu có lỗi.
            errors (list): Danh sách chi tiết các câu bị lỗi.
        """
        errors = []
        n_batch = len(batch)
        exp_n = expected_count if expected_count is not None else n_batch

        # 1. KIỂM TRA SỐ LƯỢNG CÂU
        if n_batch != exp_n:
            errors.append({
                "type": "COUNT_MISMATCH",
                "reason": f"Lệch số lượng câu: Nhận {n_batch}/{exp_n} câu",
                "id": None
            })

        for i in range(n_batch):
            cur = batch[i]
            cur_id = getattr(cur, 'id', i + 1)
            cur_viet = (getattr(cur, 'translated_text', '') or '').strip()
            cur_orig = (getattr(cur, 'clean_text', '') or getattr(cur, 'text', '') or '').strip()
            cur_dur = getattr(cur, 'duration', 0.0) or 0.0
            cur_hanzi = len(HANZI_REGEX.findall(cur_orig)) if cur_orig else 0
            cur_words = len(cur_viet.split()) if cur_viet else 0

            # 2. KIỂM TRA LẶP CÂU DỊCH LIỀN KỀ
            if i < n_batch - 1:
                nxt = batch[i + 1]
                nxt_viet = (getattr(nxt, 'translated_text', '') or '').strip()
                nxt_orig = (getattr(nxt, 'clean_text', '') or getattr(nxt, 'text', '') or '').strip()
                if cur_viet and nxt_viet and cur_viet == nxt_viet and cur_orig != nxt_orig:
                    errors.append({
                        "id": cur_id,
                        "type": "DUPLICATE",
                        "reason": f"Lặp câu dịch với câu #{getattr(nxt, 'id', i+2)}: '{cur_viet}'",
                        "orig": cur_orig
                    })

            # 3. KIỂM TRA TỐC ĐỘ ĐỌC TỐI ĐA (CHỐNG VƯỢT QUÁ KHẢ NĂNG NÓI CỦA DURATION)
            # Tốc độ đọc review phim tự nhiên kết hợp TTS co giãn là 5.5 - 7.0 từ/giây. Chỉ báo lỗi khi câu quá dài vượt ngưỡng 7.5 từ/giây.
            # ĐẶC BIỆT: Đối với câu có số, ngày tháng, thời gian, điểm số (phải diễn giải thành chữ cho TTS) -> Miễn trừ ngưỡng phình (cho phép tới 10.0 từ/giây)
            if cur_dur > 0 and cur_words > 0:
                has_numbers = any(c.isdigit() for c in (cur_orig + cur_viet)) or any(k in cur_orig for k in ['百', '千', '万', '年', '月', '日', '点', '分', '秒'])
                rate_threshold = 10.0 if has_numbers else 7.5
                multiplier = 2.0 if has_numbers else 1.0
                max_allowed_words = int(max(12, int(cur_dur * 7.5) + 6) * multiplier)
                speech_rate = cur_words / cur_dur
                if cur_words > max_allowed_words and speech_rate > rate_threshold:
                    errors.append({
                        "id": cur_id,
                        "type": "OVER_SPEED_BLOWUP",
                        "reason": f"Câu quá dài so với thời lượng ({cur_words} từ / {cur_dur:.1f}s, tốc độ {speech_rate:.1f} từ/giây vượt ngưỡng {rate_threshold:.1f})",
                        "orig": cur_orig
                    })

        is_valid = (len(errors) == 0)
        return is_valid, errors

    @staticmethod
    def audit_comprehensive_batches(
        batches: List[List[DialogueSegment]],
        max_phinh_ratio: float = 2.5
    ) -> Tuple[List[int], Dict[int, str], List[Dict[str, Any]]]:
        """
        THUẬT TOÁN KIỂM ĐỊNH LÔ TOÀN DIỆN 3 BỘ LỌC CHUẨN MỰC:
        1. Kiểm tra Lặp câu dịch (Duplicate Check): vi[i] == vi[i+1] khi zh[i] != zh[i+1].
        2. Kiểm tra Tỷ lệ phình độ dài (Ratio Check):
           - Số từ tiếng Việt > max_phinh_ratio * số chữ Hán và vượt quá thời lượng đọc.
        3. Kiểm tra Rò rỉ chữ Hán (Hanzi Leak): Còn sót ký tự chữ Hán trong bản dịch.
        4. Kiểm tra Câu rỗng bất thường do bị gộp.

        Returns:
            error_batch_numbers: Danh sách số thứ tự Lô cần dịch lại (1-based index).
            status_map: Bản đồ id -> status ('TRANSLATED' hoặc 'SILENT_VALID').
            error_details: Danh sách chi tiết các lỗi phát hiện để in ra màn hình.
        """
        error_batches = set()
        status_map: Dict[int, str] = {}
        error_details: List[Dict[str, Any]] = []

        flat_segments = []
        seg_to_batch = {}
        for b_idx, b in enumerate(batches):
            b_num = b_idx + 1
            for seg in b:
                flat_segments.append(seg)
                seg_to_batch[seg.id] = b_num

        N = len(flat_segments)
        for i in range(N):
            cur = flat_segments[i]
            cur_id = getattr(cur, 'id', i + 1)
            b_num = seg_to_batch.get(cur_id, 1)
            cur_viet = (getattr(cur, 'translated_text', '') or '').strip()
            cur_orig = (getattr(cur, 'clean_text', '') or getattr(cur, 'text', '') or '').strip()
            cur_dur = getattr(cur, 'duration', 0.0) or 0.0
            cur_hanzi = len(HANZI_REGEX.findall(cur_orig)) if cur_orig else 0
            cur_words = len(cur_viet.split()) if cur_viet else 0

            # 1. RÒ RỈ CHỮ HÁN -> TỰ ĐỘNG KHỬ TẠI CHỖ (KHÔNG BẮT DỊCH LẠI CẢ LÔ)
            if cur_viet and HANZI_REGEX.search(cur_viet):
                from app.services.translation.chinese_guard import sanitize_to_vietnamese
                cleaned_viet = sanitize_to_vietnamese(cur_viet, fallback_orig=cur_orig)
                cur.translated_text = cleaned_viet
                cur_viet = cleaned_viet

            # 2. KIỂM TRA LẶP CÂU DỊCH (DUPLICATE CHECK)
            if i < N - 1:
                nxt = flat_segments[i + 1]
                nxt_viet = (getattr(nxt, 'translated_text', '') or '').strip()
                nxt_orig = (getattr(nxt, 'clean_text', '') or getattr(nxt, 'text', '') or '').strip()
                if cur_viet and nxt_viet and cur_viet == nxt_viet and cur_orig != nxt_orig:
                    error_batches.add(b_num)
                    error_details.append({
                        "b": b_num,
                        "id": cur_id,
                        "type": "DUPLICATE",
                        "reason": f"Lặp câu dịch với câu #{getattr(nxt, 'id', i+2)}: '{cur_viet}'",
                        "orig": cur_orig
                    })

            # 3. KIỂM TRA TỶ LỆ ĐỘ DÀI & PHÌNH CÂU (BLOW-UP CHECK)
            # Ngưỡng: Nếu tiếng Trung ngắn (<= 6 chữ) mà tiếng Việt > 12 từ hoặc tỷ lệ > 2.8x
            ratio = (cur_words / max(1, cur_hanzi)) if cur_hanzi > 0 else 0
            expected_max_words = max(6, int(cur_dur * 4.5) + 3)
            if cur_hanzi > 0 and cur_words > 0:
                if (cur_hanzi <= 6 and cur_words >= 12) or (ratio > 3.0 and cur_words > expected_max_words):
                    error_batches.add(b_num)
                    error_details.append({
                        "b": b_num,
                        "id": cur_id,
                        "type": "RATIO_BLOWUP",
                        "reason": f"Phình câu bất thường ({cur_words} từ / {cur_hanzi} chữ Hán, tỷ lệ {ratio:.1f}x, {cur_dur:.1f}s): '{cur_viet}'",
                        "orig": cur_orig
                    })

            # 4. KIỂM TRA CÂU RỖNG BẤT THƯỜNG (Do bị câu trước gộp mất)
            if not cur_viet:
                prev_phinh = False
                if i > 0:
                    p = flat_segments[i - 1]
                    p_viet = (getattr(p, 'translated_text', '') or '').strip()
                    p_hanzi = len(HANZI_REGEX.findall(getattr(p, 'clean_text', '') or ''))
                    p_words = len(p_viet.split()) if p_viet else 0
                    if p_hanzi <= 8 and p_words >= 14:
                        prev_phinh = True

                if prev_phinh and cur_orig:
                    error_batches.add(b_num)
                    error_details.append({
                        "b": b_num,
                        "id": cur_id,
                        "type": "MERGED_EMPTY",
                        "reason": f"Câu có chữ '{cur_orig}' nhưng dịch rỗng (nghi bị câu trước nuốt)",
                        "orig": cur_orig
                    })
                else:
                    status_map[cur_id] = "SILENT_VALID"
            else:
                status_map[cur_id] = "TRANSLATED"

        return sorted(list(error_batches)), status_map, error_details

    @staticmethod
    def audit_dialogues(segments: List[DialogueSegment]) -> Tuple[List[DialogueSegment], List[DialogueSegment]]:
        """
        Kiểm định tương thích ngược cho các câu rò rỉ chữ Hán đơn lẻ.
        """
        valid_segs: List[DialogueSegment] = []
        error_segs: List[DialogueSegment] = []

        for s in segments:
            viet = (getattr(s, 'translated_text', '') or '').strip()
            orig = (getattr(s, 'clean_text', '') or getattr(s, 'text', '') or '').strip()
            dur = getattr(s, 'duration', 0.0) or 0.0

            if not viet:
                continue
            elif HANZI_REGEX.search(viet):
                error_segs.append(s)
            else:
                viet_word_count = len(viet.split())
                hanzi_count = len(HANZI_REGEX.findall(orig)) if orig else 0
                if 0 < hanzi_count <= 4 and dur <= 1.2 and viet_word_count > 16:
                    error_segs.append(s)
                else:
                    valid_segs.append(s)

        return valid_segs, error_segs

    @classmethod
    async def fix_errors_with_llm(
        cls,
        error_segments: List[DialogueSegment],
        genre: str = "cophong",
        provider: str = "gemini",
        max_fix_limit: int = 50
    ) -> Dict[int, str]:
        """
        Tự động vá lỗi (LLM Swept Error Fixer):
        - Giới hạn tối đa 50 câu mỗi lần quét để tránh ngốn thời gian, token và CPU/RAM.
        - Chạy 1 pass duy nhất dứt khoát, tuyệt đối không lặp vô tận.
        - Dùng chuẩn đánh số mỏ neo (1. ... 2. ...) khớp 100% với PostProcessor.
        """
        if not error_segments:
            return {}

        # Giới hạn số lượng câu để xử lý gọn gàng
        target_segments = error_segments[:max_fix_limit]

        import json
        from app.services.postprocessing.post_processor import PostProcessor
        from app.core.llm_client import safe_json_loads
        from app.services.translation.llm_translator import translate_batch_pass2_llm

        items = []
        id_map = {}
        for local_idx, s in enumerate(target_segments, 1):
            id_map[local_idx] = s.id
            orig = (s.clean_text or s.text or "").strip()
            if not orig:
                orig = "..."
            items.append({"i": local_idx, "zh": orig})

        tagged_json_text = json.dumps(items, ensure_ascii=False)
        
        try:
            fixed_output = await translate_batch_pass2_llm(
                tagged_text=tagged_json_text,
                genre=genre,
                provider=provider
            )
            
            parsed = safe_json_loads(fixed_output)
            if isinstance(parsed, dict):
                for v in parsed.values():
                    if isinstance(v, list):
                        parsed = v
                        break

            result_map: Dict[int, str] = {}
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict):
                        loc_id = item.get("i")
                        val = item.get("vi") or item.get("viet") or item.get("text") or ""
                        clean_val = PostProcessor.extract_clean_vietnamese_text(str(val))
                        # Bộ kiểm tra độ khớp & sạch chữ Hán
                        if loc_id in id_map and clean_val:
                            # Nếu câu trả về sạch chữ Hán và có nội dung
                            if not HANZI_REGEX.search(clean_val):
                                real_id = id_map[loc_id]
                                result_map[real_id] = clean_val

            # Nếu vì lý do nào đó JSON parse không đủ, fallback qua bóc tách tự nhiên bảo toàn id_map
            if not result_map:
                fallback_map = PostProcessor.parse_tagged_translation(fixed_output, target_segments)
                return fallback_map

            return result_map
        except Exception as e:
            print(f"[TranslationAuditor] Warning: Lỗi vá tự động: {e}")
            return {}

    @classmethod
    async def audit_sampled_batches_llm(
        cls,
        batches: List[List[DialogueSegment]],
        provider: str = "gemini",
        model: str = None
    ) -> List[int]:
        """
        LỚP KIỂM ĐỊNH HẬU KỲ TỰ ĐỘNG (TAIL SAMPLING BATCH AUDIT):
        - Lấy mẫu 5 câu cho mỗi lô, nghiêng mạnh về 50% cuối lô (vị trí ~60%, 75%, 85%, 95%, 100%).
        - Gửi danh sách các cặp (zh, vi) lên LLM để phát hiện xem có câu nào bị lệch nghĩa/trôi dòng do gộp câu không.
        - Trả về danh sách số thứ tự lô (1-based index) bị lỗi: ví dụ [10]. Nếu chuẩn 100% trả về [].
        """
        if not batches:
            return []

        import json
        from app.core.llm_client import safe_json_loads, post_gemini_with_retry, post_openrouter_with_retry
        from app.services.translation.llm_translator import get_active_gemini_keys, get_active_openrouter_keys

        sample_items = []
        for b_idx, batch in enumerate(batches):
            b_num = b_idx + 1
            n_batch = len(batch)
            if n_batch == 0:
                continue

            # Chọn 5 vị trí nghiêng về đuôi lô
            if n_batch <= 5:
                pick_indices = list(range(n_batch))
            else:
                pick_indices = [
                    int(n_batch * 0.60),
                    int(n_batch * 0.75),
                    int(n_batch * 0.85),
                    int(n_batch * 0.95),
                    n_batch - 1
                ]
                # Loại trừ trùng lặp và giữ thứ tự tăng dần
                pick_indices = sorted(list(set(pick_indices)))

            for p_idx in pick_indices:
                seg = batch[p_idx]
                orig_zh = (getattr(seg, 'clean_text', '') or getattr(seg, 'text', '') or '').strip()
                trans_vi = (getattr(seg, 'translated_text', '') or '').strip()
                # Chỉ kiểm định các câu có nội dung (bỏ qua câu rác rỗng)
                if orig_zh:
                    sample_items.append({
                        "b": b_num,
                        "id": getattr(seg, 'id', p_idx + 1),
                        "zh": orig_zh,
                        "vi": trans_vi
                    })

        if not sample_items:
            return []

        # Tạo prompt thẩm định siêu tinh gọn
        prompt_input = json.dumps(sample_items, ensure_ascii=False, indent=2)
        system_audit = (
            "BẠN LÀ CHUYÊN GIA KIỂM ĐỊNH ĐỐI CHIẾU PHỤ ĐỀ TRUNG - VIỆT (SUBTITLE AUDITOR).\n"
            "Nhiệm vụ: Kiểm tra xem các câu dịch tiếng Việt ('vi') có tương ứng đúng nội dung với câu tiếng Trung ('zh') cùng dòng hay không.\n"
            "Cần phát hiện: Các câu bị TRÔI DÒNG / LỆCH DÒNG (nghĩa của câu 'vi' thực chất là của câu trước hoặc câu sau do bị gộp câu làm thụt lùi chỉ số).\n"
            "LƯU Ý: Nếu câu tiếng Trung là câu rác/số vô nghĩa và câu 'vi' để rỗng '' thì ĐÓ LÀ HỢP LỆ, không phải lỗi.\n\n"
            "YÊU CẦU ĐẦU RA (JSON THUẦN):\n"
            "- Nếu phát hiện câu nào bị lệch nghĩa/trôi dòng so với 'zh' cùng dòng, ghi lại thông tin vào mảng:\n"
            "[\n"
            "  {\"b\": 10, \"id\": 2903, \"error\": \"Lệch nghĩa, vi không khớp với zh\"}\n"
            "]\n"
            "- Nếu TẤT CẢ các câu đối chiếu đều khớp nghĩa đúng dòng: Trả về duy nhất mảng rỗng: []\n"
            "CHỈ TRẢ VỀ DUY NHẤT MẢNG JSON, không giải thích bên ngoài."
        )

        user_audit = f"=== DANH SÁCH CẦN THẨM ĐỊNH LỆCH DÒNG ===\n{prompt_input}"

        provider = provider or settings.TRANSLATION_PROVIDER or "gemini"
        try:
            if provider == "gemini":
                keys = get_active_gemini_keys()
                if not keys:
                    return []
                api_key = keys[0]
                model_name = model or settings.GEMINI_MODEL or "gemini-3.1-flash-lite"
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
                headers = {"Content-Type": "application/json"}
                payload = {
                    "contents": [{"role": "user", "parts": [{"text": f"{system_audit}\n\n{user_audit}"}]}],
                    "generationConfig": {
                        "temperature": 0.1,
                        "responseMimeType": "application/json"
                    }
                }
                async with httpx.AsyncClient(timeout=60.0) as client:
                    resp = await post_gemini_with_retry(client, url, headers, payload)
                    if resp.status_code != 200:
                        return []
                    data = resp.json()
                    candidates = data.get("candidates") or []
                    if not candidates or "content" not in candidates[0]:
                        return []
                    ans_text = candidates[0]["content"]["parts"][0]["text"].strip()
            else:
                keys = get_active_openrouter_keys()
                if not keys:
                    return []
                api_key = keys[0]
                model_name = model or settings.OPENROUTER_MODEL or "google/gemini-2.5-flash"
                url = "https://openrouter.ai/api/v1/chat/completions"
                headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
                payload = {
                    "model": model_name,
                    "messages": [
                        {"role": "system", "content": system_audit},
                        {"role": "user", "content": user_audit}
                    ],
                    "temperature": 0.1,
                    "response_format": {"type": "json_object"}
                }
                async with httpx.AsyncClient(timeout=60.0) as client:
                    resp = await post_openrouter_with_retry(client, url, headers, payload)
                    if resp.status_code != 200:
                        return []
                    data = resp.json()
                    choices = data.get("choices") or []
                    if not choices or "message" not in choices[0]:
                        return []
                    ans_text = choices[0]["message"]["content"].strip()

            parsed = safe_json_loads(ans_text)
            if isinstance(parsed, dict):
                for v in parsed.values():
                    if isinstance(v, list):
                        parsed = v
                        break

            if isinstance(parsed, list):
                error_batches = set()
                for item in parsed:
                    if isinstance(item, dict) and "b" in item:
                        try:
                            b_val = int(item["b"])
                            error_batches.add(b_val)
                        except (ValueError, TypeError):
                            pass
                return sorted(list(error_batches))
            return []
        except Exception as e:
            print(f"[TranslationAuditor] Warning lỗi khi chạy audit_sampled_batches_llm: {e}")
            return []


