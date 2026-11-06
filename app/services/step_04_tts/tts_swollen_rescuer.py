# -*- coding: utf-8 -*-
"""
Module Cứu Hộ Câu Phình Quá Tải (> 1.30x) Sau Khi TTS & Gộp Vai (TTS Swollen Rescuer).
Triết lý thiết kế:
- Kích hoạt SAU KHI đã tạo âm thanh TTS và Gộp Vai liên tiếp (Continuous Audio Merger).
- Cân bằng tốc độ đọc nghiêm ngặt trong dải [1.10x - 1.30x]. Các câu có tốc độ thô > 1.30x được LLM cô đọng văn bản.
- Chia nhỏ thành từng lô (batch) theo cấu hình TRANSLATION_BATCH_SIZE trong settings để chống tràn token LLM.
- Đánh số lại từ đầu (id: 1, 2, 3...) theo từng lô để đảm bảo model nhỏ (Lite/Mini) không bao giờ bị ảo giác hay nhảy cóc ID.
- Code giữ bảng ánh xạ mapping {temp_id: item} để cập nhật chuẩn xác 100% vào cơ sở dữ liệu và file phụ đề.
- Giữ nguyên 100% timeline (start, end, src, speaker), chỉ chuốt lại văn bản tiếng Việt cô đọng và sinh lại audio cho đúng các câu bị phình.
"""

import json
import logging
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from pydub import AudioSegment

from app.config import settings
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
    """Bộ cứu hộ văn bản và âm thanh cho các câu thoại bị phình quá tải (> 1.30x)."""

    RESCUE_RATIO_THRESHOLD = 1.30  # Ngưỡng bắt buộc kích hoạt vá câu (cân bằng tốc độ [1.10x - 1.30x])

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
        Thực thi cứu hộ chia theo lô (Batching) theo cài đặt để chống tràn token:
        1. Phân chia swollen_items thành các lô nhỏ (mặc định theo TRANSLATION_BATCH_SIZE, tối đa 50 câu/lô).
        2. Gửi từng lô lên LLM Tổng Biên Tập để cô đọng câu chữ.
        3. Sinh lại audio CapCut TTS cho câu đã được viết lại.
        4. Cập nhật lại raw_results_map và database.
        """
        if not swollen_items:
            return 0

        # Import trễ tránh circular import
        from app.services.step_04_tts.capcut_tts_service import CapCutTTSService

        # Phân chia theo lô theo cài đặt setting
        setting_batch = getattr(settings, "TRANSLATION_BATCH_SIZE", 500)
        rescuer_batch_size = max(10, min(setting_batch, 50))

        total_swollen = len(swollen_items)
        num_batches = (total_swollen + rescuer_batch_size - 1) // rescuer_batch_size
        _safe_log(
            f"\n⚡ [TTS Swollen Rescuer] Phát hiện {total_swollen} câu bị phình (> {cls.RESCUE_RATIO_THRESHOLD}x). "
            f"Đang chia thành {num_batches} lô (mỗi lô ~{rescuer_batch_size} câu theo setting) để tránh tràn token..."
        )

        provider = provider or "gemini"
        total_rescued = 0

        for b_idx in range(num_batches):
            batch_items = swollen_items[b_idx * rescuer_batch_size : (b_idx + 1) * rescuer_batch_size]
            _safe_log(f"   ▶ [Lô #{b_idx + 1}/{num_batches}] Đang gửi {len(batch_items)} câu phình lên LLM cô đọng...")

            # 1. Đánh số lại từ đầu (1, 2, 3...) theo từng lô và lưu mapping {temp_id: item}
            mapping: Dict[int, Dict[str, Any]] = {}
            prompt_payload = []

            for i, item in enumerate(batch_items, 1):
                mapping[i] = item
                target_dur = float(item.get("target_dur", 2.0))
                orig_text = str(item.get("text_vi", "")).strip()
                orig_word_cnt = len(orig_text.split()) if orig_text else 0

                # Tốc độ lồng tiếng tự nhiên chuẩn: ~3.8 từ/giây (vừa khít tốc độ ~1.15x, nói sống động không bị cụt câu)
                calc_words = int(round(target_dur * 3.8))
                
                # Không ép số từ xuống quá thấp nếu câu gốc vốn dĩ đã ngắn, tránh biến thành câu cộc lốc vô hồn
                if orig_word_cnt <= 4:
                    target_words = max(2, min(orig_word_cnt, calc_words))
                else:
                    target_words = max(3, min(orig_word_cnt - 1, calc_words))

                payload_entry = {
                    "id": i,
                    "text_vi": orig_text,
                    "target_words": target_words
                }
                zh_val = item.get("text_zh") or item.get("original_text")
                if zh_val:
                    payload_entry["text_zh"] = str(zh_val).strip()
                speaker_val = item.get("speaker") or item.get("role")
                if speaker_val:
                    payload_entry["speaker"] = str(speaker_val).strip()

                prompt_payload.append(payload_entry)

            prompt_str = json.dumps(prompt_payload, ensure_ascii=False, indent=2)

            prompt = f"""Bạn là bậc thầy biên kịch và biên tập lồng tiếng phim chuyên nghiệp (Dubbing Script Doctor).
Nhiệm vụ của bạn là viết lại các câu thoại tiếng Việt đang bị dài so với thời lượng cảnh quay sao cho câu ngắn gọn hơn (khoảng `target_words` từ), nhưng VẪN PHẢI GIỮ NGUYÊN 100% ngữ khí, cảm xúc và chất phim.

CÁC NGUYÊN TẮC CỐT LÕI (TUYỆT ĐỐI KHÔNG VI PHẠM):
1. GIỮ NGUYÊN TRỌN VẸN CÁC TỪ NGỮ KHÍ VÀ BIỂU CẢM CUỐI CÂU:
   - Các từ cảm thán, câu hỏi, phản vấn, khẳng định, mỉa mai bắt buộc phải giữ hoặc thay bằng từ biểu cảm tương đương (ví dụ: "sao", "hả", "chứ", "kìa", "đâu", "nào", "nhé", "đấy", "thế", "ư").
   - CẤM TUYỆT ĐỐI cắt xén làm mất đuôi biểu cảm khiến câu nói thành cụt ngủn, ngơ ngác!
2. KHÔNG CẮT BỎ ĐẠI TỪ VÀ ĐỐI TƯỢNG XƯNG HÔ:
   - Giữ rõ ai đang nói với ai ("anh", "tôi", "ta", "mình", "ngươi", "hắn"). Không biến câu hội thoại thành cụm danh từ vô tri.
3. KỸ THUẬT RÚT GỌN LỒNG TIẾNG CHUẨN:
   - Thay thế từ ngữ dài/văn viết rườm rà bằng từ văn nói khẩu ngữ ngắn gọn, đắt giá hơn.
   - Lược bỏ từ đệm vô nghĩa hoặc trợ từ liên kết dư thừa, KHÔNG lược bỏ vị ngữ hay thái độ nhân vật.

VÍ DỤ ĐỐI CHIẾU CHUẨN:
- Cũ: "Nâng cấp. Chẳng lẽ ta đã thức tỉnh năng lực hệ siêu phàm rồi sao?" -> Mới: "Nâng cấp. Lẽ nào ta thức tỉnh siêu năng lực rồi sao?" (Tuyệt đối không cắt thành "Mình thức tỉnh năng lực rồi?")
- Cũ: "Còn có thể cho vay sao?" -> Mới: "Vẫn cho vay được sao?" (Tuyệt đối không cắt thành "Còn cho vay?")
- Cũ: "Xe này không kém gì mấy chiếc xe nhỏ kia đâu." -> Mới: "Xe này chẳng kém gì mấy xe nhỏ đâu." (Tuyệt đối không cắt thành "Xe này không kém xe nhỏ.")
- Cũ: "Hừ, nhìn bộ dạng nhát gan của anh kìa." -> Mới: "Hừ, trông anh nhát gan chưa kìa." (Tuyệt đối không cắt thành "Hừ, nhìn bộ dạng nhát gan.")
- Cũ: "Mặc kệ có Quỷ Dị hay không." -> Mới: "Kệ nó có Quỷ Dị hay không." (Tuyệt đối không cắt thành "Mặc kệ Quỷ Dị.")
- Cũ: "Đứt ruột." (câu vốn ngắn) -> Giữ nguyên: "Đứt ruột." (Không tự ý thêm bớt từ thừa).

Danh sách câu cần tối ưu:
{prompt_str}

Chỉ trả về DUY NHẤT một mảng JSON thuần hợp lệ (không kèm markdown, không giải thích):
[
  {{
    "id": 1,
    "text": "câu thoại đã tối ưu"
  }}
]"""

            # 2. Gọi LLM cô đọng câu chữ cho lô này
            try:
                ans_text = await execute_llm_call(
                    prompt=prompt,
                    provider=provider,
                    model=model,
                    temperature=0.2,
                    response_mime_type="application/json",
                    timeout=120.0
                )

                if not ans_text:
                    continue

                parsed = safe_json_loads(ans_text)
                if isinstance(parsed, dict):
                    for v in parsed.values():
                        if isinstance(v, list):
                            parsed = v
                            break

                if not isinstance(parsed, list):
                    continue

                # 3. Ánh xạ và sinh lại audio TTS ĐA LUỒNG SIÊU TỐC (Parallel Batch TTS)
                from app.services.step_04_tts.async_tts_engine import AsyncTTSEngine, _load_audio_from_bytes
                candidates_map: Dict[int, Dict[str, Any]] = {}
                tts_batch_data = []
                batch_voice_code = "BV074_streaming"

                for resp_item in parsed:
                    if not isinstance(resp_item, dict):
                        continue
                    temp_id = resp_item.get("id")
                    orig_item = mapping.get(temp_id)
                    if not orig_item:
                        for it in batch_items:
                            if it.get("dialogue_id") == temp_id:
                                orig_item = it
                                break
                    if not orig_item:
                        continue

                    shortened_text = str(resp_item.get("text") or resp_item.get("shortened_sentence") or resp_item.get("vi") or "").strip()
                    clean_text = PostProcessor.extract_clean_vietnamese_text(shortened_text)
                    if not clean_text or len(clean_text.split()) < 2:
                        continue

                    d_id = orig_item["dialogue_id"]
                    batch_voice_code = orig_item.get("voice_code", "BV074_streaming") or "BV074_streaming"
                    candidates_map[d_id] = {
                        "orig_item": orig_item,
                        "clean_text": clean_text,
                    }
                    tts_batch_data.append((d_id, clean_text))

                if not tts_batch_data:
                    continue

                _safe_log(f"   ⚡ [TTS Swollen Rescuer] Đang tổng hợp song song {len(tts_batch_data)} câu đã cô đọng bằng giọng {batch_voice_code}...")
                saved_sub_timings = dict(getattr(AsyncTTSEngine, "_last_sub_timings_map", {}))
                synth_bytes_map = await AsyncTTSEngine.batch_synthesize_async(
                    dialogues_data=tts_batch_data,
                    engine=getattr(settings, "TTS_ENGINE", "capcut"),
                    voice_code=batch_voice_code,
                    concurrency_limit=min(32, max(8, len(tts_batch_data))),
                    max_retries_per_item=3,
                    return_utterances=False,
                )
                AsyncTTSEngine._last_sub_timings_map = saved_sub_timings

                for d_id, cand in candidates_map.items():
                    orig_item = cand["orig_item"]
                    clean_text = cand["clean_text"]
                    d_obj = orig_item.get("dialogue_obj")
                    target_dur = orig_item.get("target_dur", 2.0)
                    voice_code = orig_item.get("voice_code", batch_voice_code)

                    new_audio = None
                    a_bytes = synth_bytes_map.get(d_id)
                    if a_bytes and len(a_bytes) > 100:
                        try:
                            seg = _load_audio_from_bytes(a_bytes, "mp3")
                            new_audio = CapCutTTSService.trim_audio_silence(seg)
                        except Exception:
                            new_audio = None

                    if not new_audio or len(new_audio) <= 100:
                        new_audio = CapCutTTSService.synthesize_sentence(text=clean_text, voice_code=voice_code)

                    if new_audio and len(new_audio) > 100:
                        new_dur_sec = len(new_audio) / 1000.0
                        new_ratio = new_dur_sec / target_dur
                        orig_ratio = float(orig_item.get("ratio", 1.3))

                        # QUY TẮC CHẤP NHẬN:
                        # Đã bỏ hoàn toàn chặn sàn 0.65x.
                        # Miễn là câu mới giảm được tốc độ so với bản cũ (new_ratio < orig_ratio) thì lấy 100%.
                        if new_ratio >= orig_ratio:
                            _safe_log(
                                f"  ⚠️ [Bỏ Qua Vá Câu #{orig_item.get('index', d_id)}]: Không giảm được thời lượng ({new_ratio:.2f}x >= cũ {orig_ratio:.2f}x)\n"
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

                        total_rescued += 1
                        _safe_log(
                            f"  ✔ [Vá Thành Công Câu #{orig_item.get('index', d_id)}]:\n"
                            f"     • Cũ ({orig_ratio:.2f}x): \"{orig_item.get('text_vi')}\"\n"
                            f"     • Mới ({new_ratio:.2f}x): \"{clean_text}\""
                        )

                if db and total_rescued > 0:
                    try:
                        db.commit()
                    except Exception as e:
                        logger.warning(f"Lỗi commit DB sau khi cứu hộ lô #{b_idx + 1}: {e}")

            except Exception as e:
                _safe_log(f"⚠️ [TTS Swollen Rescuer] Lỗi khi gọi LLM cứu hộ lô #{b_idx + 1}: {e}")

        return total_rescued

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
