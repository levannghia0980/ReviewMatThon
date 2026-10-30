# -*- coding: utf-8 -*-
"""
Module Cứu Hộ Câu Phình Quá Tải (> 1.6x) Sau Khi TTS & Gộp Vai (TTS Swollen Rescuer).
Triết lý thiết kế:
- Kích hoạt SAU KHI đã tạo âm thanh TTS và Gộp Vai liên tiếp (Continuous Audio Merger).
  Nhờ gộp vai, 98% câu ngắn tự động cân bằng thời gian tự nhiên; chỉ 1-2 câu thực sự phình (> 1.6x) mới cần cứu hộ.
- Hoạt động theo cơ chế Vá Cục Bộ theo ID (tương tự module sửa sót Hán trong TranslationAuditor).
- Đánh số lại từ đầu (id: 1, 2, 3...) khi gửi cho LLM để đảm bảo model nhỏ (Lite/Mini) không bao giờ bị ảo giác hay nhảy cóc ID.
- Code giữ bảng ánh xạ mapping {1: dialogue_id} để cập nhật chuẩn xác 100% vào cơ sở dữ liệu và file phụ đề.
- Giữ nguyên 100% timeline (start, end, src, speaker), chỉ chuốt lại văn bản tiếng Việt cô đọng và sinh lại audio cho đúng các câu bị phình.
"""

import json
import logging
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from pydub import AudioSegment

from app.core.llm_client import execute_llm_call, safe_json_loads
from app.services.step_03_translation.post_processor import PostProcessor

logger = logging.getLogger(__name__)


def _safe_log(msg: str):
    """In log an toàn trên mọi hệ điều hành (chống UnicodeEncodeError trên Windows cp1252)."""
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        try:
            safe_msg = msg.encode("ascii", "backslashreplace").decode("ascii")
            print(safe_msg, flush=True)
        except Exception:
            pass


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
            # Tốc độ lồng tiếng chuẩn tự nhiên ~3.8 từ/giây (vừa khít tỷ lệ 1.05x - 1.25x)
            # Min words: chặn dưới để câu không bao giờ bị cắt quá cụt làm tỷ lệ tụt < 1.0
            min_words = max(2, int(round(target_dur * 3.2)))
            # Max words: chặn trên an toàn để tốc độ đọc không vượt quá 1.30x
            max_words = max(min_words + 1, int(round(target_dur * 4.4)))

            prompt_payload.append({
                "id": i,
                "vi_current": item.get("text_vi", ""),
                "target_duration_sec": round(target_dur, 2),
                "min_words": min_words,
                "max_words": max_words
            })

        _safe_log(f"\n⚡ [TTS Swollen Rescuer] Phát hiện {len(prompt_payload)} câu bị phình (> {cls.RESCUE_RATIO_THRESHOLD}x). Đang kích hoạt vá cục bộ...")

        prompt_str = json.dumps(prompt_payload, ensure_ascii=False, indent=2)

        prompt = f"""Bạn là TỔNG BIÊN TẬP VIÊN LỒNG TIẾNG PHIM CAO CẤP.
Dưới đây là danh sách các câu thoại tiếng Việt đang bị DÀI QUÁ MỨC so với thời lượng nói của nhân vật (target_duration_sec), khiến âm thanh bị nói quá nhanh (> 1.6x).

=== DANH SÁCH CÂU CẦN ĐIỀU CHỈNH ĐỘ DÀI ===
{prompt_str}

=== QUY TẮC BIÊN TẬP & ĐIỀU CHỈNH (BẮT BUỘC) ===
1. MỤC TIÊU CỐT LÕI - VỪA VẶN NHỊP NÓI:
   - Viết lại câu thoại có số từ NẰM TRONG KHOẢNG [min_words - max_words] để giọng đọc khớp tự nhiên với thời lượng cảnh phim.
   - 🛑 CẤM CẮT XÉN QUÁ CỤT LỦN: Tuyệt đối KHÔNG viết câu ngắn hơn min_words, không được làm câu cộc lốc, vô nghĩa hoặc tụt tỷ lệ dưới 1.0x.
   - 🛑 CẤM VƯỢT QUÁ TRẦN: Tuyệt đối KHÔNG vượt quá max_words để tránh tràn âm thanh sang cảnh sau.
2. VĂN PHONG & NỘI DUNG:
   - Bảo toàn 100% ý nghĩa, sắc thái cảm xúc và bối phận nhân vật trong phim.
   - Lược bỏ các từ đệm rườm rà, chọn lọc động từ mạnh, súc tích, xuôi tai, đậm chất điện ảnh.
3. ĐỊNH DẠNG ĐẦU RA:
   - Chỉ trả về duy nhất mảng JSON thuần hợp lệ theo định dạng sau:
[
  {{
    "id": 1,
    "shortened_sentence": "Câu tiếng Việt vừa vặn nhịp nói"
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
                    orig_ratio = float(orig_item.get("ratio", 1.6))

                    # GUARDRAIL KIỂM SOÁT TỶ LỆ ÂM THANH:
                    # - Nếu new_ratio < 0.85x: Bị cắt quá ngắn/teo tóp. Nếu câu gốc <= 1.85x thì giữ bản gốc để không bị cộc lốc.
                    # - Nếu new_ratio >= orig_ratio: Không cải thiện được độ dài.
                    # - Chỉ chấp nhận khi câu mới thực sự đưa tỷ lệ về vùng tối ưu hoặc giảm thời lượng rõ rệt.
                    is_acceptable = True
                    reject_reason = ""
                    if new_ratio < 0.85:
                        if orig_ratio <= 1.85:
                            is_acceptable = False
                            reject_reason = f"Bị cắt quá ngắn ({new_ratio:.2f}x < 0.85x), giữ bản cũ ({orig_ratio:.2f}x) để bảo toàn nghĩa"
                    elif new_ratio >= orig_ratio:
                        is_acceptable = False
                        reject_reason = f"Không giảm được thời lượng ({new_ratio:.2f}x >= cũ {orig_ratio:.2f}x)"

                    if not is_acceptable:
                        _safe_log(
                            f"  ⚠️ [Bỏ Qua Vá Câu #{orig_item.get('index', d_id)}]: {reject_reason}\n"
                            f"     • Cũ: \"{orig_item.get('text_vi')}\"\n"
                            f"     • Bị loại: \"{clean_text}\""
                        )
                        continue

                    raw_results_map[d_id] = new_audio

                    # Cập nhật văn bản mới vào object dialogue và DB
                    if d_obj:
                        if hasattr(d_obj, 'text_vietnamese'):
                            d_obj.text_vietnamese = clean_text
                        if hasattr(d_obj, 'translated_text'):
                            d_obj.translated_text = clean_text

                        # Đồng bộ luôn vào bảng phụ đề DialogueSegmentModel nếu có
                        if db and getattr(d_obj, 'task_id', None) and getattr(d_obj, 'tts_index', None):
                            try:
                                from app.models import DialogueSegmentModel
                                sub_segs = db.query(DialogueSegmentModel).filter(
                                    DialogueSegmentModel.task_id == d_obj.task_id,
                                    DialogueSegmentModel.tts_index == d_obj.tts_index
                                ).all()
                                for sub_s in sub_segs:
                                    sub_s.translated_text = clean_text
                            except Exception:
                                pass

                    rescued_count += 1
                    _safe_log(
                        f"  ✔ [Vá Thành Công Câu #{orig_item.get('index', d_id)}]:\n"
                        f"     • Cũ ({orig_ratio:.2f}x): \"{orig_item.get('text_vi')}\"\n"
                        f"     • Mới ({new_ratio:.2f}x): \"{clean_text}\""
                    )

            if db and rescued_count > 0:
                try:
                    db.commit()
                except Exception as e:
                    logger.warning(f"Lỗi commit DB sau khi cứu hộ câu phình: {e}")

            return rescued_count

        except Exception as e:
            _safe_log(f"⚠️ [TTS Swollen Rescuer] Lỗi khi gọi LLM cứu hộ câu phình: {e}")
            return 0

    @classmethod
    def rescue_swollen_dialogues_sync(
        cls,
        swollen_items: List[Dict[str, Any]],
        raw_results_map: Dict[int, AudioSegment],
        db: Optional[Session] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None
    ) -> int:
        """Wrapper đồng bộ để gọi từ hàm sync không có event loop (như produce_project_voiceover)."""
        import asyncio
        import concurrent.futures

        def _run_in_new_loop():
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            try:
                return new_loop.run_until_complete(
                    cls.rescue_swollen_dialogues(swollen_items, raw_results_map, db, provider, model)
                )
            finally:
                new_loop.close()

        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop and running_loop.is_running():
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(_run_in_new_loop).result()
        else:
            return _run_in_new_loop()
