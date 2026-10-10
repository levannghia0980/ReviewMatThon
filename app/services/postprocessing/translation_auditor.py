import re
import httpx
from typing import List, Dict, Any, Tuple
from app.schemas.transcript import DialogueSegment
from app.config import settings
from app.core.llm_client import post_gemini_with_retry, post_openrouter_with_retry

HANZI_REGEX = re.compile(r'[\u4e00-\u9fff]')

class TranslationAuditor:
    @staticmethod
    def audit_dialogues(segments: List[DialogueSegment]) -> Tuple[List[DialogueSegment], List[DialogueSegment]]:
        """
        Kiểm định chất lượng bản dịch:
        - Phát hiện các câu bị thiếu bản dịch (rỗng).
        - Phát hiện các câu bị rò rỉ chữ Hán (Hanzi Leak).
        - Phát hiện câu bị LỆCH DÒNG / PHÌNH TO BẤT THƯỜNG (Length Ratio Sanity Guard):
          Nếu câu tiếng Trung siêu ngắn (<= 4 chữ Hán, < 1.2s) mà câu tiếng Việt dài bất thường (> 16 từ).
        Trả về (danh_sách_hợp_lệ, danh_sách_lỗi_cần_vá).
        """
        valid_segs: List[DialogueSegment] = []
        error_segs: List[DialogueSegment] = []

        for s in segments:
            viet = (getattr(s, 'translated_text', '') or '').strip()
            orig = (getattr(s, 'clean_text', '') or getattr(s, 'text', '') or '').strip()
            dur = getattr(s, 'duration', 0.0) or 0.0

            if not viet:
                error_segs.append(s)
            elif HANZI_REGEX.search(viet):
                error_segs.append(s)
            else:
                # Kiểm tra tỷ lệ lệch dòng bất thường:
                # Câu Trung <= 4 ký tự Hán, thời lượng <= 1.2s nhưng tiếng Việt > 16 từ
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

