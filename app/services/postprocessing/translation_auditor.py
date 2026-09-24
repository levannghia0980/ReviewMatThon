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
        Trả về (danh_sách_hợp_lệ, danh_sách_lỗi_cần_vá).
        """
        valid_segs: List[DialogueSegment] = []
        error_segs: List[DialogueSegment] = []

        for s in segments:
            viet = (getattr(s, 'translated_text', '') or '').strip()
            if not viet:
                error_segs.append(s)
            elif HANZI_REGEX.search(viet):
                error_segs.append(s)
            else:
                valid_segs.append(s)

        return valid_segs, error_segs

    @classmethod
    async def fix_errors_with_llm(
        cls,
        error_segments: List[DialogueSegment],
        genre: str = "cophong",
        provider: str = "gemini"
    ) -> Dict[int, str]:
        """
        Tự động vá lỗi (LLM Swept Error Fixer):
        Gửi danh sách các câu bị sót/lỗi chữ Hán để AI dịch lại chính xác từng câu đơn lẻ.
        """
        if not error_segments:
            return {}

        prompt_lines = [
            "Các câu dưới đây bị thiếu bản dịch hoặc bị sót chữ Hán.",
            "Hãy dịch lại từng câu sang tiếng Việt chuẩn theo đúng thể loại cổ phong:",
            ""
        ]
        for s in error_segments:
            orig = s.clean_text or s.text or ""
            prompt_lines.append(f'<s id="{s.id}">{orig}</s>')

        tagged_prompt = "\n".join(prompt_lines)
        
        from app.services.translation.llm_translator import translate_batch_pass2_llm
        try:
            fixed_output = await translate_batch_pass2_llm(
                tagged_text=tagged_prompt,
                genre=genre,
                provider=provider
            )
            from app.services.postprocessing.post_processor import PostProcessor
            return PostProcessor.parse_tagged_translation(fixed_output, error_segments)
        except Exception:
            return {}
