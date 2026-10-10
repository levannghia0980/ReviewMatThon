import os
import re
import json
import asyncio
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional
from sqlalchemy.orm import Session
from app.config import settings
from app.schemas.transcript import DialogueSegment
from app.models.project import ProjectTask
from app.models.dialogue import DialogueSegmentModel
from app.services.translation.batch_manager import BatchManager
from app.services.translation.llm_translator import extract_batch_entities_pass1_llm, translate_batch_pass2_llm
from app.services.postprocessing.post_processor import PostProcessor
from app.services.postprocessing.translation_auditor import TranslationAuditor
from app.services.dichhan.raw_text_cleaner import sanitize_chinese_raw_text
from app.services.task_manager import task_manager

def format_timestamp(seconds: float) -> str:
    millis = int((seconds - int(seconds)) * 1000)
    mins, secs = divmod(int(seconds), 60)
    hours, mins = divmod(mins, 60)
    return f"{hours:02d}:{mins:02d}:{secs:02d},{millis:03d}"

class TranslationPipelineService:
    @classmethod
    async def translate_project_dialogues(
        cls,
        task_id: str,
        project_id: int,
        db: Session,
        genre: str = "cophong",
        batch_size: int = 300,
        max_chars: int = 25000,
        provider: str = "gemini"
    ) -> Dict[str, Any]:
        """
        Quy trình Dịch Thuật 2 LLM Chuẩn AIRead:
        1. Làm sạch văn bản gốc (01_raw_cleaned).
        2. Từng lô:
           - LLM 1 (Pass 1 - Dịch Hán / Trích xuất & Đồng bộ thực thể xuyên suốt các lô).
           - LLM 2 (Pass 2 - Dịch văn phong hoàn chỉnh theo quy chuẩn thể loại & bảng thực thể khóa).
        3. Hậu xử lý & Chuẩn hóa dấu câu (04_post_processed).
        4. Auditor quét sót chữ Hán (05_auditor).
        5. Xuất SRT/TXT và lưu SQLite.
        """
        project = db.query(ProjectTask).filter(ProjectTask.id == project_id).first()
        if not project:
            raise ValueError(f"Không tìm thấy Project ID #{project_id}")

        dialogue_models = db.query(DialogueSegmentModel).filter(
            DialogueSegmentModel.task_id == project_id
        ).order_by(DialogueSegmentModel.index.asc()).all()

        if not dialogue_models:
            # TỰ ĐỘNG BÓC TÁCH STT NẾU CHƯA CÓ CÂU THOẠI (Không bắt người dùng bấm 2 lần)
            if not project.video_path or not os.path.exists(project.video_path):
                raise ValueError(f"Project #{project_id} chưa có câu thoại và không tìm thấy file video gốc để bóc tách thoại!")
            
            task_manager.update_task(task_id, step=1, progress=8)
            task_manager.add_log(task_id, "🎙️ Dự án chưa có câu thoại. Đang tự động kích hoạt bóc tách STT từ video gốc...", "cyan")
            
            from app.services.audio_extractor import AudioExtractorService
            from app.services.whisper_service import WhisperService
            
            audio_path = project.audio_path
            if not audio_path or not os.path.exists(audio_path):
                task_manager.add_log(task_id, "   🎵 Trích xuất Audio 16kHz PCM WAV...", "cyan")
                audio_path = AudioExtractorService.extract_audio_16k_wav(project.video_path)
                project.audio_path = audio_path
                db.commit()

            task_manager.add_log(task_id, "   🎙️ Khử ồn & làm sạch âm thanh (Vocal Denoise)...", "cyan")
            clean_audio_path = AudioExtractorService.get_clean_audio_for_asr(audio_path)

            engine = getattr(settings, "ASR_ENGINE", "capcut").lower()
            engine_name = "CapCut Cloud STT" if engine == "capcut" else "Groq Whisper"
            task_manager.add_log(task_id, f"   🤖 Đang nhận diện lời thoại bằng {engine_name}...", "cyan")

            extracted_dialogues, srt_p, txt_p, json_p = WhisperService.transcribe(
                audio_path=clean_audio_path,
                language=project.source_language or "zh",
                clean_text=True,
                task_id=task_id
            )

            if not extracted_dialogues:
                raise ValueError("Không nhận diện được câu thoại nào từ video!")

            project.srt_path = srt_p
            project.txt_path = txt_p
            project.json_path = json_p
            project.status = "TRANSCRIBED"
            db.commit()

            for d in extracted_dialogues:
                db.add(DialogueSegmentModel(
                    task_id=project.id,
                    index=d.id,
                    start_time=d.start,
                    end_time=d.end,
                    duration=d.duration,
                    original_text=d.text,
                    clean_text=d.clean_text,
                    confidence=d.confidence,
                    status="RAW"
                ))
            db.commit()
            task_manager.add_log(task_id, f"   ✔ Đã bóc tách thành công {len(extracted_dialogues)} câu thoại!", "emerald")

            # Nạp lại danh sách vừa bóc tách
            dialogue_models = db.query(DialogueSegmentModel).filter(
                DialogueSegmentModel.task_id == project_id
            ).order_by(DialogueSegmentModel.index.asc()).all()

        # Khử trùng lặp tuyệt đối theo index (phòng vệ chống dữ liệu rác x2)
        unique_dialogues = []
        seen_idx = set()
        for d in dialogue_models:
            if d.index not in seen_idx:
                seen_idx.add(d.index)
                unique_dialogues.append(d)
        dialogue_models = unique_dialogues

        safe_title = re_clean_name = "".join(c for c in project.title if c.isalnum() or c in (' ', '_', '-')).strip()
        if not re_clean_name:
            re_clean_name = f"project_{project.id}"

        task_manager.add_log(task_id, f"🚀 BẮT ĐẦU QUY TRÌNH DỊCH THUẬT 2 LLM AIREAD (Project #{project_id}: {project.title})", "purple")
        task_manager.add_log(task_id, f"   Tổng số câu: {len(dialogue_models)} câu | Thể loại: {genre} | Provider: {provider}", "cyan")

        # =====================================================================
        # GIAI ĐOẠN 1: TIỀN XỬ LÝ & LÀM SẠCH VĂN BẢN GỐC (01_raw_cleaned)
        # =====================================================================
        task_manager.update_task(task_id, step=1, progress=15)
        task_manager.add_log(task_id, "[1/5] 🧹 TIỀN XỬ LÝ: Làm sạch ký tự Hán & từ đệm...", "cyan")

        segments: List[DialogueSegment] = []
        cleaned_lines = []
        for d in dialogue_models:
            orig = d.clean_text or d.original_text or ""
            cleaned = sanitize_chinese_raw_text(orig) if orig else ""
            cleaned = cleaned if cleaned else orig
            
            seg = DialogueSegment(
                id=d.index,
                start=d.start_time,
                end=d.end_time,
                duration=d.duration,
                text=d.original_text,
                clean_text=cleaned,
                confidence=d.confidence
            )
            segments.append(seg)
            cleaned_lines.append(f"<s id=\"{seg.id}\">{cleaned}</s>")

        # Lưu vào output/01_raw_cleaned
        raw_cleaned_path = settings.OUTPUT_RAW_CLEANED_DIR / f"{project.video_id}_{re_clean_name}_cleaned.txt"
        with open(raw_cleaned_path, "w", encoding="utf-8") as f:
            f.write("\n".join(cleaned_lines))
        task_manager.add_log(task_id, f"   ✔ Đã lưu bản sạch: output/01_raw_cleaned/{raw_cleaned_path.name}", "emerald")

        # =====================================================================
        # GIAI ĐOẠN 2 & 3: CHIA LÔ & CHẠY 2 LLM CHO TỪNG LÔ (02_entities + 03_dich_ai_llm)
        # =====================================================================
        task_manager.update_task(task_id, step=2, progress=30)
        batches = BatchManager.create_batches(segments, batch_size=batch_size, max_chars=max_chars)
        batch_log_msg = f"[2/5] 🌐 TIẾN TRÌNH DỊCH 2 LLM: Tổng cộng {len(batches)} lô (~{batch_size} câu/lô)..."
        task_manager.add_log(task_id, batch_log_msg, "cyan")
        print(f"\n🌐 [TIẾN TRÌNH DỊCH] Tổng cộng {len(batches)} lô (~{batch_size} câu/lô) | Dự án #{project.id}: {project.title}", flush=True)

        accumulated_entities: Dict[str, Dict[str, Any]] = {}
        all_raw_llm_outputs = []
        translated_segments: List[DialogueSegment] = []
        prev_batch: List[DialogueSegment] = []

        entities_file = settings.OUTPUT_ENTITIES_DIR / f"{project.video_id}_{re_clean_name}_entities.json"

        for b_idx, batch in enumerate(batches):
            # CHỐT AN TOÀN HỦY TIẾN TRÌNH (Cancel Check): Nếu người dùng nhấn dừng thì dọn sạch những gì lô này đang làm
            if task_manager.is_cancelled(task_id):
                task_manager.add_log(task_id, f"🛑 Đã phát hiện yêu cầu DỪNG tại Lô #{b_idx + 1}. Hủy bỏ xử lý và dọn dẹp lô dở dang...", "amber")
                raise asyncio.CancelledError(f"Người dùng đã hủy tiến trình tại Lô #{b_idx + 1}")

            b_num = b_idx + 1
            clean_batch_text = BatchManager.pack_dialogues_to_clean_text(batch)
            tagged_batch_text = BatchManager.pack_dialogues_to_tagged_text(batch)
            context_text = BatchManager.get_context_from_previous_batch(prev_batch)

            start_bid = getattr(batch[0], 'index', batch[0].id)
            end_bid = getattr(batch[-1], 'index', batch[-1].id)
            batch_status_msg = f"   ⚡ [Lô #{b_num}/{len(batches)}] LLM: Đang dịch (Câu #{start_bid} ➔ #{end_bid}, tổng {len(batch)} câu)..."
            task_manager.add_log(task_id, batch_status_msg, "cyan")
            print(f"⚡ [LÔ #{b_num}/{len(batches)}] Bắt đầu dịch Câu #{start_bid} ➔ #{end_bid} ({len(batch)} câu)...", flush=True)

            # --- LLM 1: TRÍCH XUẤT & ĐỒNG BỘ THỰC THỂ THEO LÔ ---
            new_entities = await extract_batch_entities_pass1_llm(
                batch_raw_text=clean_batch_text,
                accumulated_entities=accumulated_entities,
                genre=genre,
                project_title=project.title,
                provider=provider
            )

            if task_manager.is_cancelled(task_id):
                task_manager.add_log(task_id, f"🛑 Đã dừng tiến trình dịch thuật ở Lô #{b_num}.", "amber")
                raise asyncio.CancelledError(f"Người dùng đã hủy tiến trình tại Lô #{b_num}")

            new_found_count = 0
            for ent in new_entities:
                raw = ent.get("raw", "").strip()
                if raw and raw not in accumulated_entities:
                    accumulated_entities[raw] = ent
                    new_found_count += 1
                elif raw and raw in accumulated_entities:
                    pass

            # Lưu entities tích lũy ra file json
            with open(entities_file, "w", encoding="utf-8") as f:
                json.dump(list(accumulated_entities.values()), f, ensure_ascii=False, indent=2)

            if new_found_count > 0:
                sample_new = [f"{e.get('raw','')}->{e.get('viet','')}" for e in new_entities[:4]]
                task_manager.add_log(task_id, f"      ✔ LLM 1 phát hiện thêm {new_found_count} thực thể mới: {', '.join(sample_new)}", "emerald")

            # Tạo bảng thực thể cho LLM 2
            entity_lines = []
            for raw, ent in accumulated_entities.items():
                if raw and (raw in clean_batch_text or raw in tagged_batch_text):
                    viet = ent.get("viet", "")
                    etype = ent.get("type", "NAME")
                    desc = ent.get("desc", "")
                    entity_lines.append(f"- {raw} ➔ {viet} ({etype}{': ' + desc if desc else ''})")
            entity_table_text = "\n".join(entity_lines)

            # Đợi nhẹ 3s giữa các call (tiết kiệm thời gian nhưng vẫn chống rate limit)
            await asyncio.sleep(3)
            if task_manager.is_cancelled(task_id):
                raise asyncio.CancelledError(f"Người dùng đã hủy tiến trình tại Lô #{b_num}")

            # --- LLM 2: DỊCH VĂN PHONG CHUẨN AIREAD ---
            task_manager.add_log(task_id, f"   ✨ [Lô #{b_num}/{len(batches)}] LLM 2: Dịch kịch bản văn phong AIRead với bảng {len(entity_lines)} thực thể xuất hiện trong lô...", "cyan")

            raw_llm_batch_output = await translate_batch_pass2_llm(
                tagged_text=tagged_batch_text,
                genre=genre,
                project_title=project.title,
                context_text=context_text,
                entity_table_text=entity_table_text,
                provider=provider
            )

            if task_manager.is_cancelled(task_id):
                raise asyncio.CancelledError(f"Người dùng đã hủy tiến trình tại Lô #{b_num}")

            # Lưu ngay bản thô LLM vào output/03_dich_ai_llm/
            batch_raw_file = settings.OUTPUT_DICH_AI_LLM_DIR / f"{project.video_id}_batch_{b_num:02d}_output.txt"
            with open(batch_raw_file, "w", encoding="utf-8") as f:
                f.write(raw_llm_batch_output)

            # Hậu xử lý sơ bộ cho lô
            batch_updated = PostProcessor.apply_post_processing(batch, raw_llm_batch_output)
            translated_segments.extend(batch_updated)
            prev_batch = batch_updated
            all_raw_llm_outputs.append(raw_llm_batch_output)

            # LƯU NGAY LẬP TỨC LÔ VỪA DỊCH XONG VÀO SQLITE DATABASE
            for s in batch_updated:
                diag_db = db.query(DialogueSegmentModel).filter(
                    DialogueSegmentModel.task_id == project.id,
                    DialogueSegmentModel.index == s.id
                ).first()
                if diag_db:
                    diag_db.translated_text = s.translated_text
                    diag_db.status = "TRANSLATED"
            db.commit()
            l_done = f"   💾 [Lô #{b_num}/{len(batches)}] Đã dịch xong {len(batch_updated)} câu & lưu vào SQLite."
            task_manager.add_log(task_id, l_done, "emerald")
            print(f"✔ [LÔ #{b_num}/{len(batches)}] Hoàn thành dịch {len(batch_updated)} câu ➔ Đã lưu SQLite.", flush=True)

            pct = int(30 + (b_num / len(batches)) * 45)
            task_manager.update_task(task_id, progress=pct)

            # Nghỉ nhẹ 3 giây giữa các lô
            if b_idx < len(batches) - 1:
                task_manager.add_log(task_id, f"      ⏳ [Lô #{b_num}] Đã xong. Đợi 3 giây trước khi xử lý Lô #{b_num + 1}...", "gray")
                await asyncio.sleep(3)

        done_all = f"✔ Hoàn tất dịch toàn bộ {len(batches)} lô với tổng {len(accumulated_entities)} thực thể xuyên suốt."
        task_manager.add_log(task_id, done_all, "emerald")
        print(f"\n🎉 {done_all}\n", flush=True)

        # =====================================================================
        # GIAI ĐOẠN 4: HẬU XỬ LÝ & CHUẨN HÓA (04_post_processed)
        # =====================================================================
        task_manager.update_task(task_id, step=4, progress=80)
        task_manager.add_log(task_id, "[4/5] 🛠️ HẬU XỬ LÝ: Chuẩn hóa dấu câu & kiểm tra rò rỉ...", "cyan")

        post_processed_lines = []
        for s in translated_segments:
            post_processed_lines.append(f"<s id=\"{s.id}\">{s.translated_text}</s>")

        post_file = settings.OUTPUT_POST_PROCESSED_DIR / f"{project.video_id}_{re_clean_name}_postprocessed.txt"
        with open(post_file, "w", encoding="utf-8") as f:
            f.write("\n".join(post_processed_lines))

        # =====================================================================
        # GIAI ĐOẠN 5: TRANSLATION AUDITOR (QUÉT SÓT CHỮ HÁN & VÁ LỖI AN TOÀN)
        # =====================================================================
        task_manager.update_task(task_id, step=5, progress=88)
        task_manager.add_log(task_id, "[5/5] 🔍 AUDITOR: Kiểm tra rò rỉ chữ Hán & soát câu sót...", "cyan")

        valid_segs, error_segs = TranslationAuditor.audit_dialogues(translated_segments)
        if error_segs:
            task_manager.add_log(task_id, f"   ⚠️ Phát hiện {len(error_segs)} câu cần kiểm tra/vá lỗi. Kích hoạt vá nhanh (tối đa 50 câu, 1 pass duy nhất)...", "amber")
            fixed_map = await TranslationAuditor.fix_errors_with_llm(error_segs, genre=genre, provider=provider, max_fix_limit=50)
            if fixed_map:
                for s in translated_segments:
                    if s.id in fixed_map:
                        s.translated_text = fixed_map[s.id]
                task_manager.add_log(task_id, f"   ✔ Đã tự động vá lỗi thành công {len(fixed_map)} câu thoại!", "emerald")
            else:
                task_manager.add_log(task_id, "   ℹ️ Bỏ qua vá lỗi tự động để tránh nghẽn luồng; tiếp tục hoàn tất bản dịch.", "gray")
        else:
            task_manager.add_log(task_id, "   ✔ Bản dịch sạch 100%, không rò rỉ bất kỳ chữ Hán nào!", "emerald")

        # =====================================================================
        # GIAI ĐOẠN 5.5: GỘP VẾ BỔ TRỢ THEO DẤU PHẨY (SAFE SMART COMMA MERGER)
        # Tự động gộp vế câu bổ trợ kết thúc bằng dấu phẩy ',' vào câu sau
        # Start lấy từ đầu câu trước, End lấy ở cuối câu sau, giảm tổng số câu
        # Có chốt an toàn: max 3 câu gộp, max duration 6.0s, gap <= 0.40s
        # =====================================================================
        INTRO_CONNECTIVES = {
            "lúc này", "đúng lúc này", "đúng lúc ấy", "nghe vậy", "nghe thấy vậy", "nghe được lời này",
            "nói đoạn", "nói xong", "hôm sau", "ngày hôm sau", "một lát sau", "vừa dứt lời", "trời ơi",
            "than ôi", "nhìn thấy cảnh này", "thấy vậy", "sau đó", "bỗng nhiên", "đột nhiên", "ngay sau đó",
            "khi này", "thời khắc này", "chỉ thấy", "nhưng đúng lúc này", "ngay lúc này", "vừa quay đầu",
            "chớp mắt một cái", "ngay tại lúc này"
        }

        def _is_short_clause(text: str) -> bool:
            """Chỉ xét câu ngắn dưới 6 từ mới được xem là vế trạng ngữ hoặc bổ trợ dở dang."""
            return len(text.strip().split()) <= 6

        def _is_forward_dependent(text: str) -> bool:
            """Câu hiện tại là vế mở đầu/trạng ngữ ngắn hướng về câu sau (kết thúc bằng dấu phẩy)."""
            t = text.strip()
            if not _is_short_clause(t):
                return False
            if t.endswith(",") or t.endswith("，"):
                return True
            clean_t = re.sub(r'[\.\,\!\?\…\s]+$', '', t).strip().lower()
            return clean_t in INTRO_CONNECTIVES

        def _is_backward_dependent(text: str) -> bool:
            """Câu sau là vế bổ ngữ ngắn hướng về câu trước (bắt đầu bằng dấu phẩy)."""
            t = text.strip()
            if not _is_short_clause(t):
                return False
            return t.startswith(",") or t.startswith("，")

        def _is_same_speaker_context(text_a: str, text_b: str) -> bool:
            """Kiểm tra xem 2 câu có cùng 1 ngôi nói hay không (chống gộp lẫn giữa dẫn truyện & thoại nhân vật)."""
            t_a = text_a.strip()
            t_b = text_b.strip()

            # 1. Nhận diện thoại trực tiếp (dấu ngoặc kép, ngoặc vuông thoại)
            is_dialogue_a = bool(re.search(r'[\"“”\'‘’「」『』]', t_a))
            is_dialogue_b = bool(re.search(r'[\"“”\'‘’「」『』]', t_b))
            if is_dialogue_a != is_dialogue_b:
                return False  # 1 câu là dẫn truyện, 1 câu là thoại nhân vật -> CẤM GỘP

            # 2. Nhận diện cấu trúc chuyển đổi ngôi dẫn sang thoại: 'hắn nói:', 'cười bảo:'
            if re.search(r'(?:nói|bảo|quát|hét|hỏi|than|đáp|lẩm bẩm|thì thầm|hô lên)\s*[\:\,\—\-]\s*$', t_a, re.IGNORECASE):
                return False

            # 3. Nhận diện chuyển đổi đại từ xưng hô đối đáp giữa 2 câu
            is_second_person_b = bool(re.match(r'^(?:ngươi|mày|các ngươi|cậu|bạn|anh|chị|chú|bác|đạo hữu|sư huynh|sư muội)\b', t_b, re.IGNORECASE))
            is_third_person_a = bool(re.search(r'^(?:hắn|y|nàng|gã|bọn họ|họ|tiểu tử)\b', t_a, re.IGNORECASE))
            if is_third_person_a and is_second_person_b:
                return False

            return True

        merged_segments = []
        i = 0
        n = len(translated_segments)

        while i < n:
            curr = translated_segments[i]
            curr_text = (curr.translated_text or "").strip()
            curr_orig = (curr.clean_text or curr.text or "").strip()
            curr_start = curr.start
            curr_end = curr.end
            chain_count = 1

            # Làm sạch nếu vô tình dính lỗi '.,'
            if curr_text.endswith(".,"):
                curr_text = curr_text[:-2].strip() + ","

            while i + 1 < n:
                next_seg = translated_segments[i + 1]
                next_text = (next_seg.translated_text or "").strip()
                next_orig = (next_seg.clean_text or next_seg.text or "").strip()
                gap = next_seg.start - curr_end

                # Điều kiện gộp an toàn:
                # 1. Có dấu phẩy chỉ định câu ngắn (câu trước kết thúc phẩy HOẶC câu sau bắt đầu phẩy)
                # 2. CÙNG 1 NGÔI NÓI (không đổi từ dẫn truyện sang thoại hoặc giữa 2 người)
                # 3. Khoảng cách thời gian gần khít (gap <= 0.40s)
                # 4. Chuỗi gộp không quá 3 câu, tổng thời lượng <= 6.0s
                can_merge = False
                if (_is_forward_dependent(curr_text) or _is_backward_dependent(next_text)):
                    if _is_same_speaker_context(curr_text, next_text):
                        if gap <= 0.40 and chain_count < 3 and (next_seg.end - curr_start) <= 6.0:
                            can_merge = True

                if can_merge:
                    # Nối 2 câu: chuẩn hóa dấu phẩy ở giữa
                    curr_text_clean = re.sub(r'[\.\,\s]+$', '', curr_text).strip()
                    next_text_clean = re.sub(r'^[\,\s]+', '', next_text).strip()
                    if next_text_clean:
                        clean_next_text = next_text_clean[0].lower() + next_text_clean[1:] if len(next_text_clean) > 1 else next_text_clean.lower()
                    else:
                        clean_next_text = ""

                    curr_text = f"{curr_text_clean}, {clean_next_text}".strip()
                    curr_orig = f"{curr_orig}，{next_orig}".strip()
                    curr_end = next_seg.end
                    chain_count += 1
                    i += 1
                else:
                    break

            # Chuẩn hóa dấu cuối: nếu còn dấu phẩy ở đuôi câu kết thì đổi thành dấu chấm
            if curr_text.endswith(",") or curr_text.endswith("，"):
                curr_text = curr_text.rstrip(",，").strip() + "."
            # Chuẩn hóa dấu đầu câu nếu còn sót
            if curr_text.startswith(",") or curr_text.startswith("，"):
                curr_text = curr_text.lstrip(",，").strip()
                if curr_text:
                    curr_text = curr_text[0].upper() + curr_text[1:]

            curr.translated_text = curr_text
            curr.start = curr_start
            curr.end = curr_end
            curr.duration = round(curr_end - curr_start, 3)
            curr.clean_text = curr_orig
            merged_segments.append(curr)
            i += 1

        if len(merged_segments) < len(translated_segments):
            merged_count = len(translated_segments) - len(merged_segments)
            task_manager.add_log(task_id, f"   ✨ [Smart Comma Merger] Đã tự động gộp {merged_count} vế câu bổ trợ kết thúc bằng dấu phẩy! (Tổng câu: {len(translated_segments)} ➔ {len(merged_segments)})", "emerald")
            for new_idx, s in enumerate(merged_segments, 1):
                s.id = new_idx
            translated_segments = merged_segments

        # =====================================================================
        # LƯU TRỮ VÀ XUẤT CÁC FILE THÀNH PHẨM (output/translations/)
        # =====================================================================
        task_manager.update_task(task_id, progress=95)
        task_manager.add_log(task_id, "💾 Đang lưu dữ liệu vào SQLite Database và xuất file kịch bản...", "cyan")

        # Cập nhật SQLite sạch sẽ với danh sách câu chuẩn
        proj_id = project.id
        proj_video_id = project.video_id
        db.query(DialogueSegmentModel).filter(DialogueSegmentModel.task_id == proj_id).delete(synchronize_session=False)
        db.commit()

        # Dọn sạch các DialogueSegmentModel cũ khỏi Identity Map của Session để tránh SAWarning xung đột khóa chính
        for obj in list(db.identity_map.values()):
            if isinstance(obj, DialogueSegmentModel):
                db.expunge(obj)

        seen_trans_ids = set()
        clean_final_segments = []
        for s in translated_segments:
            if s.id in seen_trans_ids:
                continue
            seen_trans_ids.add(s.id)
            clean_final_segments.append(s)
            db.add(DialogueSegmentModel(
                task_id=proj_id,
                index=s.id,
                start_time=s.start,
                end_time=s.end,
                duration=s.duration,
                original_text=s.text,
                clean_text=s.clean_text,
                translated_text=s.translated_text,
                confidence=s.confidence,
                status="TRANSLATED"
            ))

        db.query(ProjectTask).filter(ProjectTask.id == proj_id).update({"status": "TRANSLATED"})
        db.commit()

        # Xuất File SRT Tiếng Việt
        srt_lines = []
        for s in clean_final_segments:
            srt_lines.append(f"{s.id}\n{format_timestamp(s.start)} --> {format_timestamp(s.end)}\n{s.translated_text}\n")

        srt_path = settings.OUTPUT_TRANSCRIPTS_DIR / f"{proj_video_id}_{re_clean_name}_vi.srt"
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(srt_lines))

        # Xuất File TXT Tiếng Việt hoàn chỉnh
        txt_lines = [s.translated_text for s in translated_segments if s.translated_text]
        txt_path = settings.OUTPUT_TRANSCRIPTS_DIR / f"{proj_video_id}_{re_clean_name}_vi.txt"
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(txt_lines))

        task_manager.update_task(task_id, step=5, progress=100, status="DONE")
        task_manager.add_log(task_id, f"🎉 QUY TRÌNH DỊCH THUẬT HOÀN TẤT XUẤT SẮC! File SRT: {srt_path.name}", "emerald")

        return {
            "status": "SUCCESS",
            "project_id": proj_id,
            "total_segments": len(translated_segments),
            "total_entities": len(accumulated_entities),
            "srt_path": str(srt_path),
            "txt_path": str(txt_path)
        }
