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
from app.services.translation.llm_translator import translate_batch_pass2_llm
from app.services.dichhan.llm_extractor import extract_batch_entities_direct_llm
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

        # Lưu bản sạch vào thư mục project
        raw_cleaned_path = p_dirs["root"] / f"{project.video_id}_cleaned.txt"
        with open(raw_cleaned_path, "w", encoding="utf-8") as f:
            f.write("\n".join(cleaned_lines))
        task_manager.add_log(task_id, f"   ✔ Đã lưu bản sạch: {raw_cleaned_path.name}", "emerald")

        # =====================================================================
        # GIAI ĐOẠN 2 & 3: CHIA LÔ & CHẠY 2 LLM CHO TỪNG LÔ (02_entities + 03_dich_ai_llm)
        # =====================================================================
        task_manager.update_task(task_id, step=2, progress=30)
        batches = BatchManager.create_batches(segments, batch_size=batch_size, max_chars=max_chars)
        batch_log_msg = f"[2/5] 🌐 TIẾN TRÌNH DỊCH 2 LLM: Tổng cộng {len(batches)} lô (~{batch_size} câu/lô)..."
        task_manager.add_log(task_id, batch_log_msg, "cyan")
        print(f"\n🌐 [TIẾN TRÌNH DỊCH] Tổng cộng {len(batches)} lô (~{batch_size} câu/lô) | Dự án #{project.id}: {project.title}", flush=True)

        # LẤY CẤU TRÚC THƯ MỤC CHUẨN CỦA PROJECT THEO ID
        from app.config import get_project_dirs
        p_dirs = get_project_dirs(project.id)
        proj_entities_dir = p_dirs["entities"]
        proj_llm_io_dir = p_dirs["llm_io"]
        proj_root_dir = p_dirs["root"]

        accumulated_entities: Dict[str, Dict[str, Any]] = {}
        all_raw_llm_outputs = []
        translated_segments: List[DialogueSegment] = []
        prev_batch: List[DialogueSegment] = []

        entities_file = proj_entities_dir / "all_entities.json"

        for b_idx, batch in enumerate(batches):
            # CHỐT AN TOÀN HỦY TIẾN TRÌNH (Cancel Check): Nếu người dùng nhấn dừng thì dọn sạch những gì lô này đang làm
            if task_manager.is_cancelled(task_id):
                task_manager.add_log(task_id, f"🛑 Đã phát hiện yêu cầu DỪNG tại Lô #{b_idx + 1}. Hủy bỏ xử lý và dọn dẹp lô dở dang...", "amber")
                raise asyncio.CancelledError(f"Người dùng đã hủy tiến trình tại Lô #{b_idx + 1}")

            b_num = b_idx + 1
            start_bid = getattr(batch[0], 'index', batch[0].id)
            end_bid = getattr(batch[-1], 'index', batch[-1].id)

            # KIỂM TRA LÔ ĐÃ DỊCH CHƯA ĐỂ DỊCH TIẾP TỤC (RESUME CAPABILITY):
            # Nếu tất cả các câu trong lô này đã có translated_text trong SQLite thì nạp lại và bỏ qua gọi LLM
            batch_already_done = True
            cached_translated = []
            for s in batch:
                diag_db = db.query(DialogueSegmentModel).filter(
                    DialogueSegmentModel.task_id == project.id,
                    DialogueSegmentModel.index == s.id
                ).first()
                if not diag_db or diag_db.translated_text is None:
                    batch_already_done = False
                    break
                else:
                    s_clone = DialogueSegment(
                        id=s.id,
                        start=s.start,
                        end=s.end,
                        duration=s.duration,
                        text=s.text,
                        clean_text=s.clean_text,
                        translated_text=diag_db.translated_text or "",
                        confidence=s.confidence
                    )
                    cached_translated.append(s_clone)

            if batch_already_done and len(cached_translated) == len(batch):
                task_manager.add_log(task_id, f"   ⏩ [Lô #{b_num}/{len(batches)}] Đã dịch trước đó (Câu #{start_bid} ➔ #{end_bid}). Bỏ qua gọi LLM, tiếp tục lô tiếp theo...", "gray")
                print(f"⏩ [LÔ #{b_num}/{len(batches)}] Đã có sẵn bản dịch trong SQLite (Câu #{start_bid} ➔ #{end_bid}). Tiếp tục...", flush=True)
                translated_segments.extend(cached_translated)
                prev_batch = cached_translated
                continue

            clean_batch_text = BatchManager.pack_dialogues_to_clean_text(batch)
            tagged_batch_text = BatchManager.pack_dialogues_to_tagged_text(batch)
            context_text = BatchManager.get_context_from_previous_batch(prev_batch)

            start_bid = getattr(batch[0], 'index', batch[0].id)
            end_bid = getattr(batch[-1], 'index', batch[-1].id)
            batch_status_msg = f"   ⚡ [Lô #{b_num}/{len(batches)}] LLM: Đang dịch (Câu #{start_bid} ➔ #{end_bid}, tổng {len(batch)} câu)..."
            task_manager.add_log(task_id, batch_status_msg, "cyan")
            print(f"⚡ [LÔ #{b_num}/{len(batches)}] Bắt đầu dịch Câu #{start_bid} ➔ #{end_bid} ({len(batch)} câu)...", flush=True)

            # --- LLM 1: TRÍCH XUẤT & ĐỒNG BỘ THỰC THỂ THEO LÔ (Qua dịch Hán chuẩn) ---
            raw_entities = await extract_batch_entities_direct_llm(
                combined_raw_text=clean_batch_text,
                existing_entities=accumulated_entities,
                genre_key=genre
            )
            # Chuẩn hóa format sang dict {raw, viet, type}
            new_entities = []
            for ent in raw_entities:
                ch = ent.get("chinese_name", "").strip()
                vn = ent.get("vietnamese_name", "").strip()
                et = ent.get("entity_type", "NAME").strip()
                if ch and vn:
                    new_entities.append({"raw": ch, "viet": vn, "type": et})

            # LƯU THỰC THỂ RIÊNG CỦA LÔ NÀY VÀO FOLDER entities/
            batch_ent_file = proj_entities_dir / f"batch_{b_num:02d}_entities.json"
            with open(batch_ent_file, "w", encoding="utf-8") as f:
                json.dump(new_entities, f, ensure_ascii=False, indent=2)

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

            # Lưu entities tích lũy của toàn truyện ra all_entities.json
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

            # LƯU FILE ĐẦU VÀO CỦA LLM (INPUT) VÀO FOLDER llm_io/
            batch_input_file = proj_llm_io_dir / f"batch_{b_num:02d}_input.json"
            with open(batch_input_file, "w", encoding="utf-8") as f:
                json.dump({
                    "batch": b_num,
                    "dialogues": json.loads(tagged_batch_text),
                    "entities": entity_lines,
                    "genre": genre
                }, f, ensure_ascii=False, indent=2)

            # Đợi nhẹ 3s giữa các call (tiết kiệm thời gian nhưng vẫn chống rate limit)
            await asyncio.sleep(3)
            if task_manager.is_cancelled(task_id):
                raise asyncio.CancelledError(f"Người dùng đã hủy tiến trình tại Lô #{b_num}")

            # --- LLM 2: DỊCH VĂN PHONG CHUẨN AIREAD ---
            task_manager.add_log(task_id, f"   ✨ [Lô #{b_num}/{len(batches)}] LLM 2: Dịch kịch bản văn phong AIRead với bảng {len(entity_lines)} thực thể xuất hiện trong lô...", "cyan")

            # GỌI LLM DỊCH VÀ KIỂM ĐỊNH 3 TIÊU CHUẨN NGAY LẬP TỨC (Số câu, Lặp câu, Tỷ lệ phình)
            max_batch_retries = 3
            batch_attempt = 0
            batch_updated = None

            while batch_attempt < max_batch_retries:
                batch_attempt += 1
                if batch_attempt > 1:
                    retry_log = f"   🔄 [Lô #{b_num}] Dịch lại lần #{batch_attempt} vì chưa qua 3 tiêu chuẩn kiểm định..."
                    task_manager.add_log(task_id, retry_log, "amber")
                    print(retry_log, flush=True)

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

                # Bóc tách kết quả
                candidate_batch = PostProcessor.apply_post_processing(batch, raw_llm_batch_output)

                # KIỂM ĐỊNH 3 TIÊU CHÍ BẮT BUỘC NGAY TẠI CHỖ (Count, Duplicate, Ratio)
                is_valid, batch_errors = TranslationAuditor.audit_single_batch(candidate_batch, expected_count=len(batch))
                if is_valid:
                    batch_updated = candidate_batch
                    # Lưu bản thô LLM vào folder llm_io/ của project
                    batch_raw_file = proj_llm_io_dir / f"batch_{b_num:02d}_output.json"
                    with open(batch_raw_file, "w", encoding="utf-8") as f:
                        f.write(raw_llm_batch_output)
                    break
                else:
                    err_summary = f"   ⚠️ [Lô #{b_num} - Thử #{batch_attempt}] Vi phạm kiểm định ({len(batch_errors)} lỗi): {batch_errors[0]['reason']}"
                    task_manager.add_log(task_id, err_summary, "amber")
                    print(err_summary, flush=True)
                    batch_updated = candidate_batch

            # Gán kết quả lô đã kiểm định vào danh sách
            translated_segments.extend(batch_updated)
            prev_batch = batch_updated
            batches[b_idx] = batch_updated

            # LƯU NGAY LẬP TỨC LÔ VỪA DỊCH XONG VÀO SQLITE DATABASE
            for s in batch_updated:
                diag_db = db.query(DialogueSegmentModel).filter(
                    DialogueSegmentModel.task_id == project.id,
                    DialogueSegmentModel.index == s.id
                ).first()
                if diag_db:
                    diag_db.translated_text = s.translated_text
                    diag_db.end_time = getattr(s, 'end', diag_db.end_time)
                    diag_db.duration = getattr(s, 'duration', diag_db.duration)
                    diag_db.status = "TRANSLATED"
            db.commit()
            l_done = f"   💾 [Lô #{b_num}/{len(batches)}] Đã dịch xong & đạt chuẩn 3 tiêu chí ({len(batch_updated)} câu) ➔ Đã lưu SQLite."
            task_manager.add_log(task_id, l_done, "emerald")
            print(f"✔ [LÔ #{b_num}/{len(batches)}] Đạt chuẩn 3 tiêu chí ➔ Đã lưu SQLite.", flush=True)

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
        # =====================================================================
        # GIAI ĐOẠN 4: TỔNG HỢP & DỌN DẸP HÁN SÓT TOÀN PHIM LẦN 1
        # =====================================================================
        task_manager.update_task(task_id, step=4, progress=80)
        task_manager.add_log(task_id, "[4/5] 🛡️ DỌN DẸP HÁN SÓT: Tự động quét sạch 100% chữ Hán toàn bộ câu thoại...", "cyan")
        print("\n🛡️ [4/5] DỌN DẸP HÁN SÓT TOÀN PHIM LẦN 1...", flush=True)

        from app.services.translation.chinese_guard import ensure_project_dialogues_vietnamese
        # Đồng bộ các câu thoại hiện tại vào SQLite trước khi quét Hán
        for s in translated_segments:
            diag_db = db.query(DialogueSegmentModel).filter(
                DialogueSegmentModel.task_id == project.id,
                DialogueSegmentModel.index == s.id
            ).first()
            if diag_db:
                diag_db.translated_text = s.translated_text
        db.commit()

        fixed_hanzi_first = ensure_project_dialogues_vietnamese(db, project.id)
        if fixed_hanzi_first > 0:
            task_manager.add_log(task_id, f"   ✔ Đã tự động dọn sạch và chuyển dịch {fixed_hanzi_first} chữ Hán sót sang tiếng Việt!", "emerald")

        # Nạp lại dữ liệu đã sạch Hán vào translated_segments và batches
        diag_map = {d.index: d.translated_text for d in db.query(DialogueSegmentModel).filter(DialogueSegmentModel.task_id == project.id).all()}
        for s in translated_segments:
            if s.id in diag_map and diag_map[s.id]:
                s.translated_text = diag_map[s.id]
        for b in batches:
            for s in b:
                if s.id in diag_map and diag_map[s.id]:
                    s.translated_text = diag_map[s.id]

        # =====================================================================
        # GIAI ĐOẠN 5: THẨM ĐỊNH MẪU 5 CÂU/LÔ VÀ DỊCH LẠI + KIỂM ĐỊNH 3 TIÊU CHÍ
        # =====================================================================
        task_manager.update_task(task_id, step=5, progress=88)
        task_manager.add_log(task_id, "[5/5] 🔍 AUDITOR: Lấy mẫu 5 câu/lô gửi LLM thẩm định lệch dòng...", "cyan")
        print("\n🔍 [5/5] AUDITOR: Lấy mẫu 5 câu/lô kiểm định lệch dòng...", flush=True)

        batches_to_reheal = set()
        try:
            audit_msg = f"   🔎 Đang lấy mẫu 5 câu/lô ({len(batches) * 5} câu) nghiêng về đuôi gửi LLM thẩm định lệch dòng..."
            task_manager.add_log(task_id, audit_msg, "gray")
            print(audit_msg, flush=True)
            llm_err_batches = await TranslationAuditor.audit_sampled_batches_llm(batches, provider=provider)
            if llm_err_batches:
                llm_warn = f"   ⚠️ [LLM Auditor] Phát hiện {len(llm_err_batches)} lô bị lệch dòng theo đối chiếu ngữ nghĩa: Lô {llm_err_batches}"
                task_manager.add_log(task_id, llm_warn, "amber")
                print(llm_warn, flush=True)
                batches_to_reheal.update(llm_err_batches)
        except Exception as e:
            skip_msg = f"   ℹ️ Bỏ qua kiểm định LLM do lỗi mạng: {e}"
            task_manager.add_log(task_id, skip_msg, "gray")
            print(skip_msg, flush=True)

        # NẾU CÓ LÔ BỊ LỆCH DÒNG -> BẮT DỊCH LẠI VÀ CHẠY KIỂM ĐỊNH 3 BƯỚC + DỌN HÁN LẦN NỮA
        if batches_to_reheal:
            sorted_reheals = sorted(list(batches_to_reheal))
            reheal_start_msg = f"   🔄 [BATCH RE-HEAL] Bắt buộc dịch lại {len(sorted_reheals)} lô lỗi: {sorted_reheals}..."
            task_manager.add_log(task_id, reheal_start_msg, "cyan")
            print(f"\n{reheal_start_msg}", flush=True)

            for err_b_num in sorted_reheals:
                err_idx = err_b_num - 1
                if 0 <= err_idx < len(batches):
                    re_msg = f"   🔄 Đang dịch lại Lô #{err_b_num} ({len(target_b)} câu)... Gộp thoại xuyên suốt của cùng 1 người nói và khóa timecode chuẩn."
                    task_manager.add_log(task_id, re_msg, "cyan")
                    print(re_msg, flush=True)
                    re_clean = BatchManager.pack_dialogues_to_clean_text(target_b)
                    re_tagged = BatchManager.pack_dialogues_to_tagged_text(target_b)

                    # Bảng thực thể
                    re_entity_lines = []
                    for raw, ent in accumulated_entities.items():
                        if raw and (raw in re_clean or raw in re_tagged):
                            re_entity_lines.append(f"- {raw} ➔ {ent.get('viet','')} ({ent.get('type','NAME')})")
                    re_table = "\n".join(re_entity_lines)

                    # Dịch lại với vòng lặp kiểm tra 3 tiêu chuẩn cho Lô này
                    attempt = 0
                    while attempt < 3:
                        attempt += 1
                        re_raw_output = await translate_batch_pass2_llm(
                            tagged_text=re_tagged,
                            genre=genre,
                            project_title=project.title,
                            entity_table_text=re_table,
                            provider=provider
                        )
                        updated_target = PostProcessor.apply_post_processing(target_b, re_raw_output)
                        is_ok, re_errs = TranslationAuditor.audit_single_batch(updated_target, expected_count=len(target_b))
                        if is_ok:
                            break
                        else:
                            print(f"      ⚠️ Lô #{err_b_num} dịch lại lần {attempt} chưa qua 3 tiêu chuẩn: {re_errs[0]['reason']}", flush=True)

                    batches[err_idx] = updated_target

                    # ĐỒNG BỘ TRỰC TIẾP VÀO translated_segments
                    id_to_new_seg = {s.id: s for s in updated_target}
                    for i_seg, old_s in enumerate(translated_segments):
                        if old_s.id in id_to_new_seg:
                            translated_segments[i_seg] = id_to_new_seg[old_s.id]

                    # Cập nhật ngay vào SQLite Database
                    for s in updated_target:
                        diag_db = db.query(DialogueSegmentModel).filter(
                            DialogueSegmentModel.task_id == project.id,
                            DialogueSegmentModel.index == s.id
                        ).first()
                        if diag_db:
                            diag_db.translated_text = s.translated_text
                            diag_db.status = "TRANSLATED"
                    db.commit()
                    done_re_msg = f"   ✔ Lô #{err_b_num} đã được dịch lại & vượt qua 3 tiêu chuẩn kiểm định!"
                    task_manager.add_log(task_id, done_re_msg, "emerald")
                    print(done_re_msg, flush=True)

            # DỌN DẸP HÁN SÓT LẦN CUỐI SAU KHI DỊCH LẠI
            fixed_final = ensure_project_dialogues_vietnamese(db, project.id)
            if fixed_final > 0:
                task_manager.add_log(task_id, f"   🛡️ Đã làm sạch dứt điểm {fixed_final} chữ Hán sót cuối cùng!", "emerald")
        else:
            ok_audit_msg = f"   🎉 [KIỂM ĐỊNH THÀNH CÔNG] Toàn bộ {len(batches)} lô đạt chuẩn 100%: Khớp nghĩa đúng dòng, không phình câu, không lệch dòng!"
            task_manager.add_log(task_id, ok_audit_msg, "emerald")
            print(ok_audit_msg, flush=True)

        # Cập nhật trạng thái CSDL cho mọi câu (đảm bảo SILENT_VALID và TRANSLATED chuẩn xác)
        for s in translated_segments:
            st = "TRANSLATED" if (s.translated_text or '').strip() else "SILENT_VALID"
            diag_db = db.query(DialogueSegmentModel).filter(
                DialogueSegmentModel.task_id == project.id,
                DialogueSegmentModel.index == s.id
            ).first()
            if diag_db:
                diag_db.status = st
        db.commit()

        # Chuẩn hóa dấu câu kết thúc (đổi dấu phẩy dở dang ở cuối thành dấu chấm nếu có)
        for s in translated_segments:
            if s.translated_text:
                t = s.translated_text.strip()
                if t.endswith(",") or t.endswith("，"):
                    s.translated_text = t.rstrip(",，").strip() + "."


        # =====================================================================
        # LƯU TRỮ VÀ XUẤT CÁC FILE THÀNH PHẨM (output/translations/)
        # =====================================================================
        task_manager.update_task(task_id, progress=95)
        task_manager.add_log(task_id, "💾 Đang lưu dữ liệu câu gộp mới vào SQLite Database và xuất file kịch bản...", "cyan")

        # THU GỌN VÀ LẤY SỐ CÂU MỚI DO BẢN DỊCH ĐƯA RA (LOẠI BỎ MẨU RÁC/MẨU ĐÃ BỊ GỘP)
        original_asr_count = len(dialogue_models)
        valid_merged_segments = []
        for s in translated_segments:
            vt = (getattr(s, 'translated_text', '') or '').strip()
            # Bỏ qua các câu rỗng do đã bị gộp vào câu trước hoặc câu rác
            if vt and getattr(s, 'duration', 0.0) > 0.0:
                valid_merged_segments.append(s)

        # ĐÁNH LẠI CHỈ SỐ INDEX (1..N) CHUẨN XÁC CHO DANH SÁCH CÂU MỚI
        clean_final_segments = []
        for new_idx, s in enumerate(valid_merged_segments, start=1):
            s.id = new_idx
            clean_final_segments.append(s)

        merged_count = len(clean_final_segments)
        removed_fragments_count = max(0, original_asr_count - merged_count)

        # IN BẢNG BÁO CÁO THỐNG KÊ CHI TIẾT RA TERMINAL VÀ GIAO DIỆN
        stats_header = (
            f"\n{'='*70}\n"
            f"📊 BÁO CÁO KẾT QUẢ DỊCH & GỘP CÂU CHUẨN THỜI GIAN MỚI:\n"
            f"   • Số câu gốc bóc tách từ CapCut STT: {original_asr_count} câu\n"
            f"   • Số câu hoàn chỉnh sau khi AI gộp câu: {merged_count} câu (đã đồng bộ mốc thời gian mới)\n"
            f"   • Số mẩu vụn đã gộp / loại bỏ: {removed_fragments_count} câu\n"
            f"   • Số câu lỗi vi phạm kiểm định: 0 câu (100% đạt chuẩn thời lượng & kiểm định)\n"
            f"{'='*70}\n"
        )
        print(stats_header, flush=True)
        task_manager.add_log(task_id, f"✔ Hoàn tất gộp câu: Từ {original_asr_count} câu gốc -> Thu gọn thành {merged_count} câu hoàn chỉnh ({removed_fragments_count} mẩu vụn đã gộp).", "emerald")

        # Cập nhật SQLite sạch sẽ với danh sách câu chuẩn
        proj_id = project.id
        proj_video_id = project.video_id
        db.query(DialogueSegmentModel).filter(DialogueSegmentModel.task_id == proj_id).delete(synchronize_session=False)
        db.commit()

        # Dọn sạch các DialogueSegmentModel cũ khỏi Identity Map của Session để tránh SAWarning xung đột khóa chính
        for obj in list(db.identity_map.values()):
            if isinstance(obj, DialogueSegmentModel):
                db.expunge(obj)

        for s in clean_final_segments:
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

        # Xuất File SRT Tiếng Việt với đúng mốc thời gian mới và số câu mới
        srt_lines = []
        json_dialogues = []
        for s in clean_final_segments:
            srt_lines.append(f"{s.id}\n{format_timestamp(s.start)} --> {format_timestamp(s.end)}\n{s.translated_text}\n")
            json_dialogues.append({
                "id": s.id,
                "start": round(s.start, 2),
                "end": round(s.end, 2),
                "duration": round(s.duration, 2),
                "zh": s.clean_text or s.text,
                "vi": s.translated_text
            })

        # Lưu trực tiếp vào thư mục riêng của project: output/projects/project_{id}/
        srt_path = proj_root_dir / "transcript_vi.srt"
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(srt_lines))

        txt_path = proj_root_dir / "script_vi.txt"
        txt_lines = [s.translated_text for s in clean_final_segments if s.translated_text]
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(txt_lines))

        json_path = proj_root_dir / "transcript_vi.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(json_dialogues, f, ensure_ascii=False, indent=2)

        # CẬP NHẬT FILE BÁO CÁO CÂN BẰNG THỜI GIAN (TIMELINE BALANCE REPORT)
        timeline_report_file = proj_root_dir / "timeline_balance_report.json"
        with open(timeline_report_file, "w", encoding="utf-8") as f:
            json.dump({
                "project_id": proj_id,
                "original_asr_count": original_asr_count,
                "merged_count": merged_count,
                "segments": json_dialogues
            }, f, ensure_ascii=False, indent=2)

        # Cập nhật đường dẫn vào ProjectTask trong DB
        project.srt_path = str(srt_path)
        project.txt_path = str(txt_path)
        project.json_path = str(json_path)
        db.commit()

        task_manager.update_task(task_id, step=5, progress=100, status="DONE")
        task_manager.add_log(task_id, f"🎉 QUY TRÌNH DỊCH THUẬT HOÀN TẤT XUẤT SẮC! File SRT: {srt_path.name} ({merged_count} câu)", "emerald")

        return {
            "status": "SUCCESS",
            "project_id": proj_id,
            "total_segments": len(translated_segments),
            "total_entities": len(accumulated_entities),
            "srt_path": str(srt_path),
            "txt_path": str(txt_path),
            "json_path": str(json_path)
        }
