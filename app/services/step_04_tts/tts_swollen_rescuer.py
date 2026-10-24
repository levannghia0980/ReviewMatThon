# -*- coding: utf-8 -*-
"""
Module Cứu Hộ Câu Phình Quá Tải (> 1.6x) Sau Khi Sinh TTS (TTS Swollen Rescuer).
Triết lý thiết kế:
- Hoạt động theo cơ chế Vá Cục Bộ theo ID (tương tự module sửa sót Hán trong TranslationAuditor).
- Kích hoạt CHỈ KHI thời lượng âm thanh thực tế sau khi TTS đo được vượt quá 1.6x so với khung hình gốc (raw_dur / slot_dur > 1.60).
- Đánh số lại từ đầu (id: 1, 2, 3...) khi gửi cho LLM để đảm bảo model nhỏ (Lite/Mini) không bao giờ bị ảo giác hay nhảy cóc ID.
- Code giữ bảng ánh xạ mapping {1: dialogue_id} để cập nhật chuẩn xác 100% vào cơ sở dữ liệu.
- Giữ nguyên 100% timeline (start, end, src, speaker), chỉ chuốt lại văn bản tiếng Việt cô đọng và sinh lại audio cho đúng 1-2 câu bị phình.
"""

import json
import logging
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from pydub import AudioSegment

from app.core.llm_client import execute_llm_call, safe_json_loads
from app.services.step_03_translation.post_processor import PostProcessor

logger = logging.getLogger(__name__)


class TTSSwollenRescuer:
    """Bộ cứu hộ văn bản và âm thanh cho các câu thoại bị phình quá tải (> 1.6x)."""

    RESCUE_RATIO_THRESHOLD = 1.60  # Ngưỡng bắt buộc kích hoạt vá câu

    @classmethod
    async def rescue_swollen_dialogues(
        cls,
        swollen_items: List[Dict[str, Any]],
        raw_results_map: Dict[int, AudioSegment],
        db: Optional[Session] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None
    ) -> int:
        """
        Thực thi cứu hộ:
        1. Gửi danh sách câu bị phình lên LLM Tổng Biên Tập để cô đọng câu chữ.
        2. Sinh lại audio CapCut TTS cho câu đã được viết lại.
        3. Cập nhật lại raw_results_map và database.
        """
        if not swollen_items:
            return 0

        # Import trễ tránh circular import
        from app.services.step_04_tts.capcut_tts_service import CapCutTTSService

        # 1. Đánh số lại từ đầu (1, 2, 3...) và lưu bảng ánh xạ mapping {temp_id: item}
        mapping: Dict[int, Dict[str, Any]] = {}
        prompt_payload = []

        for i, item in enumerate(swollen_items, 1):
            mapping[i] = item
            target_dur = float(item.get("target_dur", 2.0))
            # Ngân sách số từ: tốc độ tự nhiên ~ 3.0 từ/giây
            max_words = max(3, int(target_dur * 3.0))

            prompt_payload.append({
                "id": i,
                "vi_current": item.get("text_vi", ""),
                "target_duration_sec": round(target_dur, 2),
                "max_allowed_words": max_words
            })

        print(f"\n⚡ [TTS Swollen Rescuer] Phát hiện {len(prompt_payload)} câu bị phình (> {cls.RESCUE_RATIO_THRESHOLD}x). Đang kích hoạt vá cục bộ...", flush=True)

        prompt_str = json.dumps(prompt_payload, ensure_ascii=False, indent=2)

        prompt = f"""Bạn là TỔNG BIÊN TẬP VIÊN LỒNG TIẾNG PHIM CAO CẤP.
Dưới đây là danh sách các câu thoại tiếng Việt đang bị DÀI QUÁ MỨC so với thời lượng nói của nhân vật (target_duration_sec), khiến âm thanh bị nói quá nhanh / bắn rap (> 1.6x).

=== DANH SÁCH CÂU CẦN CÔ ĐỌNG ===
{prompt_str}

=== QUY TẮC RÚT GỌN (BẮT BUỘC) ===
1. Viết lại câu thoại ngắn gọn, súc tích, nằm trọn trong ngân sách từ (max_allowed_words).
2. Lược bỏ các từ đệm, liên từ phụ, từ thừa ("thế nhỉ", "cái", "thì", "lại thấy"...) nhưng BẢO TOÀN 100% Ý NGHĨA và SẮC THÁI NHÂN VẬT.
3. Câu viết lại phải đọc xuôi tai, tự nhiên, đúng ngữ cảnh lồng tiếng.
4. Chỉ trả về duy nhất mảng JSON thuần hợp lệ theo định dạng sau:
[
  {{
    "id": 1,
    "shortened_sentence": "Câu tiếng Việt rút gọn cô đọng"
  }}
]
Tuyệt đối KHÔNG kèm bất kỳ lời dẫn hay giải thích nào bên ngoài JSON!"""

        # 2. Gọi LLM cô đọng câu chữ
        provider = provider or "gemini"
        rescued_count = 0

        try:
            ans_text = await execute_llm_call(
                prompt=prompt,
                provider=provider,
                model=model,
                temperature=0.2,
                response_mime_type="application/json",
                timeout=30.0
            )

            if not ans_text:
                return 0

            parsed = safe_json_loads(ans_text)
            if isinstance(parsed, dict):
                for v in parsed.values():
                    if isinstance(v, list):
                        parsed = v
                        break

            if not isinstance(parsed, list):
                return 0

            # 3. Ánh xạ và sinh lại audio TTS
            for resp_item in parsed:
                if not isinstance(resp_item, dict):
                    continue
                temp_id = resp_item.get("id")
                # Hỗ trợ cả temp_id (1, 2) hoặc dialogue_id cũ
                orig_item = mapping.get(temp_id)
                if not orig_item:
                    # Thử tìm theo dialogue_id
                    for it in swollen_items:
                        if it.get("dialogue_id") == temp_id:
                            orig_item = it
                            break
                if not orig_item:
                    continue

                shortened_text = str(resp_item.get("shortened_sentence") or resp_item.get("vi") or "").strip()
                clean_text = PostProcessor.extract_clean_vietnamese_text(shortened_text)
                if not clean_text:
                    continue

                d_id = orig_item["dialogue_id"]
                d_obj = orig_item.get("dialogue_obj")
                voice_code = orig_item.get("voice_code", "BV074_streaming")
                target_dur = orig_item.get("target_dur", 2.0)

                # Sinh lại âm thanh CapCut TTS riêng cho câu đã rút gọn
                new_audio = CapCutTTSService.synthesize_sentence(text=clean_text, voice_code=voice_code)
                if new_audio and len(new_audio) > 100:
                    new_dur_sec = len(new_audio) / 1000.0
                    new_ratio = new_dur_sec / target_dur
                    raw_results_map[d_id] = new_audio

                    # Cập nhật văn bản mới vào object dialogue và DB
                    if d_obj:
                        if hasattr(d_obj, 'text_vietnamese'):
                            d_obj.text_vietnamese = clean_text
                        if hasattr(d_obj, 'translated_text'):
                            d_obj.translated_text = clean_text

                    rescued_count += 1
                    print(
                        f"  ✔ [Vá Thành Công Câu #{orig_item.get('index', d_id)}]:\n"
                        f"     • Cũ ({orig_item.get('ratio'):.2f}x): \"{orig_item.get('text_vi')}\"\n"
                        f"     • Mới ({new_ratio:.2f}x): \"{clean_text}\"",
                        flush=True
                    )

            if db and rescued_count > 0:
                try:
                    db.commit()
                except Exception as e:
                    logger.warning(f"Lỗi commit DB sau khi cứu hộ câu phình: {e}")

            return rescued_count

        except Exception as e:
            print(f"⚠️ [TTS Swollen Rescuer] Lỗi khi gọi LLM cứu hộ câu phình: {e}", flush=True)
            return 0
