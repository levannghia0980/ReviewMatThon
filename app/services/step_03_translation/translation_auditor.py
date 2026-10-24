import re
import httpx
from typing import List, Dict, Any, Tuple
from app.schemas.transcript import DialogueSegment
from app.config import settings
from app.core.llm_client import execute_llm_call, safe_json_loads

HANZI_REGEX = re.compile(r'[\u4e00-\u9fff]')

class TranslationAuditor:
    @staticmethod
    def audit_single_batch(
        batch: List[DialogueSegment],
        expected_count: int = None
    ) -> Tuple[bool, List[Dict[str, Any]]]:
        """
        BỘ KIỂM ĐỊNH 3 TIÊU CHUẨN CỐT LÕI CHO MỖI LÔ SAU KHI LLM TRẢ VỀ:
        1. Kiểm tra bao phủ mảng src: Nếu có câu gộp, kiểm tra các câu mới có bao phủ hợp lý mảng src không.
        2. Kiểm tra lặp câu dịch (Duplicate Check): vi[i] == vi[i+1] khi zh[i] != zh[i+1].
        3. Kiểm tra tỷ lệ độ dài & phình câu: Câu ngắn (<= 6 chữ) mà dịch dài >= 12 từ hoặc ratio > 2.8x.

        Returns:
            is_valid (bool): True nếu lô đạt chuẩn 3 tiêu chí, False nếu có lỗi.
            errors (list): Danh sách chi tiết các câu bị lỗi.
        """
        errors = []
        n_batch = len(batch)
        exp_n = expected_count if expected_count is not None else n_batch

        # 1. KIỂM TRA SỐ LƯỢNG CÂU & ĐỘ BAO PHỦ CỦA LÔ (COVERAGE AUDIT):
        # Nếu n_batch == 0 báo lỗi rỗng.
        if n_batch == 0:
            errors.append({
                "type": "EMPTY_BATCH",
                "reason": "Lô không nhận được bất kỳ câu thoại nào",
                "id": None
            })
        else:
            # Kiểm tra xem bản dịch có bị cụt giữa chừng hay không (Truncated Batch Detection):
            # Tìm câu cuối cùng có nội dung dịch tiếng Việt
            translated_indices = [idx for idx, s in enumerate(batch) if (getattr(s, 'translated_text', '') or '').strip()]
            if translated_indices:
                last_trans_idx = max(translated_indices)
                last_seg = batch[last_trans_idx]
                batch_end_time = getattr(batch[-1], 'end', 0.0) or getattr(batch[-1], 'end_time', 0.0) or 0.0
                last_seg_end = getattr(last_seg, 'end', 0.0) or getattr(last_seg, 'end_time', 0.0) or 0.0

                # Nếu câu dịch cuối cùng đã bao phủ tới cuối lô (hoặc chênh lệch <= 0.25s) thì không bị cụt đuôi
                is_tail_covered = (batch_end_time > 0 and last_seg_end >= batch_end_time - 0.25)

                tail_missing = n_batch - 1 - last_trans_idx
                if not is_tail_covered and n_batch >= 5 and tail_missing >= 4 and (tail_missing / n_batch) >= 0.25:
                    errors.append({
                        "type": "TRUNCATED_BATCH_TAIL",
                        "reason": f"Bản dịch bị cụt giữa chừng: chỉ dịch đến câu #{getattr(batch[last_trans_idx], 'id', last_trans_idx + 1)}, bỏ sót {tail_missing}/{n_batch} câu ở đuôi lô!",
                        "id": getattr(batch[last_trans_idx], 'id', last_trans_idx + 1)
                    })

        for i in range(n_batch):
            cur = batch[i]
            cur_id = getattr(cur, 'id', i + 1)
            cur_viet = (getattr(cur, 'translated_text', '') or '').strip()
            cur_orig = (getattr(cur, 'clean_text', '') or getattr(cur, 'text', '') or '').strip()
            cur_dur = getattr(cur, 'duration', 0.0) or 0.0
            cur_hanzi = len(HANZI_REGEX.findall(cur_orig)) if cur_orig else 0
            cur_words = len(cur_viet.split()) if cur_viet else 0

            # Bỏ qua câu rỗng do đã được gộp hoặc do là câu rác/nói nhảm
            if not cur_viet or cur_dur <= 0:
                continue

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

            # 3. KIỂM TRA TỐC ĐỘ ĐỌC TỐI ĐA (CHỐNG VƯỢT QUÁ KHẢ NĂNG NÓI CỦA DURATION - GIỮ TỐC ĐỘ DƯỚI 1.3x)
            # Tốc độ đọc chuẩn tự nhiên ~3.0 - 3.2 từ/giây. Để tốc độ co giãn âm thanh luôn dưới 1.3x,
            # tốc độ đọc an toàn không được vượt quá 4.0 - 4.5 từ/giây.
            if cur_dur >= 1.5 and cur_words > 0:
                has_numbers = any(c.isdigit() for c in (cur_orig + cur_viet)) or any(k in cur_orig for k in ['百', '千', '万', '年', '月', '日', '点', '分', '秒'])
                rate_threshold = 4.5 if has_numbers else 4.0
                max_allowed_words = max(6, int(round(cur_dur * rate_threshold)))

                speech_rate = cur_words / cur_dur
                if cur_words > max_allowed_words and speech_rate > rate_threshold:
                    errors.append({
                        "id": cur_id,
                        "type": "OVER_SPEED_BLOWUP",
                        "reason": f"Câu quá dài so với thời lượng ({cur_words} từ / {cur_dur:.1f}s, tốc độ {speech_rate:.1f} từ/giây vượt ngưỡng an toàn {rate_threshold:.1f})",
                        "orig": cur_orig
                    })

        is_valid = (len(errors) == 0)
        return is_valid, errors



    @classmethod
    async def batch_fix_hanzi_leak_llm(
        cls,
        segments: List[DialogueSegment],
        genre: str = "cophong",
        provider: str = None,
        model: str = None,
        project_title: str = ""
    ) -> Dict[int, str]:
        """
        MODULE SỬA SÓT HÁN TOÀN BỘ PHIM BẰNG LLM (KẾ THỪA TRỰC TIẾP TỪ AIREAD):
        - Quét toàn bộ danh sách câu thoại sau khi dịch xong tất cả các lô.
        - Phát hiện mọi câu thoại bị rò rỉ hoặc sót chữ Hán (HANZI_REGEX).
        - Gửi cả câu tiếng Trung gốc + câu tiếng Việt có đánh dấu [CHỮ HÁN CẦN DỊCH: ...] lên LLM.
        - Yêu cầu LLM đóng vai TỔNG BIÊN TẬP VIÊN VĂN HỌC & LỒNG TIẾNG PHIM CAO CẤP,
          chịu trách nhiệm viết lại trọn vẹn cả câu văn (fixed_sentence) hoàn chỉnh 100% tiếng Việt,
          văn phong trôi chảy, giàu hình ảnh, khớp với thời lượng (duration) và ngữ cảnh phim.
        - Ánh xạ chuẩn xác 100% theo ID câu thoại (id_map) -> Tuyệt đối không bao giờ bị lệch câu hay trôi dòng!
        """
        if not segments:
            return {}

        import json
        import re
        from app.core.llm_client import safe_json_loads
        from app.services.step_03_translation.post_processor import PostProcessor

        # 1. Quét tìm tất cả các câu thoại bị sót chữ Hán
        error_items = []
        for s in segments:
            viet = (getattr(s, 'translated_text', '') or '').strip()
            orig = (getattr(s, 'clean_text', '') or getattr(s, 'text', '') or '').strip()
            dur = getattr(s, 'duration', 0.0) or 0.0

            # Bỏ qua các câu rỗng (câu đã gộp hoặc khoảng lặng)
            if not viet or dur <= 0:
                continue

            # Kiểm tra xem có chứa chữ Hán CJK không
            han_matches = HANZI_REGEX.findall(viet)
            if han_matches:
                # Đánh dấu các vị trí chữ Hán trong câu để LLM dễ nhận diện ngữ cảnh
                def _mask_han(m):
                    return f" [CHỮ HÁN CẦN DỊCH: {m.group(0)}] "

                masked_context = HANZI_REGEX.sub(_mask_han, viet)
                masked_context = re.sub(r'\s+', ' ', masked_context).strip()

                error_items.append({
                    "id": getattr(s, 'id', 0),
                    "zh_orig": orig,
                    "vi_context": masked_context,
                    "duration": round(dur, 2)
                })

        if not error_items:
            print("✔ [Batch Fix Hanzi] Toàn bộ kịch bản phim sạch 100% chữ Hán, không cần biên tập lại.", flush=True)
            return {}

        total_err = len(error_items)
        print(f"🔍 [Batch Fix Hanzi] Phát hiện {total_err} câu bị sót chữ Hán. Đang gửi Tổng Biên Tập Viên LLM xử lý...", flush=True)

        # 2. Xây dựng Prompt Tổng Biên Tập Viên Cao Cấp (Kế thừa nguyên bản AIRead)
        provider = provider or getattr(settings, "TRANSLATION_PROVIDER", "gemini")
        
        prompt_input = json.dumps(error_items, ensure_ascii=False, indent=2)
        genre_upper = genre.upper() if genre else "CỔ PHONG"

        prompt = f"""Bạn là TỔNG BIÊN TẬP VIÊN VĂN HỌC & LỒNG TIẾNG PHIM CAO CẤP (thể loại: {genre_upper}).
Dưới đây là danh sách các câu thoại bị sót chữ Hán trong kịch bản phim. Mỗi câu được đánh dấu rõ vị trí [CHỮ HÁN CẦN DỊCH: ...] cùng câu gốc tiếng Trung (zh_orig) và thời lượng (duration).

=== DANH SÁCH CÁC CÂU CẦN BIÊN TẬP ===
{prompt_input}

=== NGUYÊN TẮC BIÊN TẬP & SỬA LỖI (CHỊU TRÁCH NHIỆM TOÀN BỘ CÂU VĂN) ===
1. 🛑 CẤM TUYỆT ĐỐI CHỮ HÁN & TỪ LẶP:
   - `fixed_sentence` BẮT BUỘC 100% LÀ TIẾNG VIỆT HOÀN CHỈNH, TUYỆT ĐỐI CẤM để sót bất kỳ chữ Hán nào.
   - TUYỆT ĐỐI KHÔNG lặp lại các từ ngữ liền kề do lỗi ghép từ trước đó.
   - Xóa bỏ triệt để các ký tự rác, mảnh từ bị cắt dở hoặc lỗi dịch máy thô.

2. TRÁCH NHIỆM CHUỐT MƯỢT CẢ CÂU VĂN LỒNG TIẾNG (fixed_sentence):
   - Bạn được cung cấp cả câu tiếng Trung gốc (zh_orig) và ngữ cảnh câu thoại tiếng Việt đang dở dang (vi_context).
   - Hãy dịch vị trí chữ Hán đó chuẩn xác theo ngữ cảnh phim, hòa nhập hoàn toàn vào câu để tạo thành một câu thoại tiếng Việt hoàn chỉnh, tự nhiên, trôi chảy, giàu cảm xúc và đậm chất điện ảnh.
   - TUYỆT ĐỐI CẤM dịch bẻ âm thô từng chữ (convert máy móc). Phải dùng văn phong lồng tiếng phim chuẩn mực.
   - Cân đối độ dài câu văn cho phù hợp với thời lượng `duration` để diễn viên lồng tiếng (TTS) đọc trôi chảy, không bị nói quá nhanh hoặc quá chậm.

=== CẤU TRÚC JSON BẮT BUỘC TRẢ VỀ ===
[
  {{
    "id": 1,
    "fixed_sentence": "Câu thoại tiếng Việt hoàn chỉnh trôi chảy đã làm sạch 100% chữ Hán"
  }}
]
Chỉ trả về JSON thuần hợp lệ (mảng JSON các object chứa "id" và "fixed_sentence"), không bọc thêm bất kỳ lời dẫn hay giải thích nào bên ngoài."""

        # 3. Thực thi gọi LLM (Hỗ trợ cả Gemini Web và API)
        result_map: Dict[int, str] = {}
        try:
            ans_text = await execute_llm_call(
                prompt=prompt,
                provider=provider,
                model=model,
                temperature=0.2,
                response_mime_type="application/json",
                timeout=120.0
            )

            if ans_text:
                parsed = safe_json_loads(ans_text)
                if isinstance(parsed, dict):
                    for v in parsed.values():
                        if isinstance(v, list):
                            parsed = v
                            break

                if isinstance(parsed, list):
                    for item in parsed:
                        if isinstance(item, dict) and "id" in item:
                            try:
                                seg_id = int(item["id"])
                                fixed = item.get("fixed_sentence") or item.get("vi") or item.get("text") or ""
                                clean_fixed = PostProcessor.extract_clean_vietnamese_text(str(fixed))
                                # Làm sạch hoàn toàn nếu còn thẻ [CHỮ HÁN...] do LLM nhại lại
                                clean_fixed = re.sub(r'\[CHỮ HÁN CẦN DỊCH:[^\]]+\]', '', clean_fixed).strip()
                                clean_fixed = re.sub(r'\s+', ' ', clean_fixed).strip()
                                if clean_fixed and not HANZI_REGEX.search(clean_fixed):
                                    result_map[seg_id] = clean_fixed
                            except (ValueError, TypeError):
                                pass

            print(f"✔ [Batch Fix Hanzi] LLM đã biên tập và viết lại thành công {len(result_map)}/{total_err} câu!", flush=True)
            return result_map
        except Exception as e:
            print(f"⚠️ [Batch Fix Hanzi] Lỗi trong quá trình gọi LLM biên tập: {e}", flush=True)
            return {}


