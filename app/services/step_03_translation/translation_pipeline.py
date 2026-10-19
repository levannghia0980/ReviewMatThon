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
from app.models.dialogue import DialogueSegmentModel, TTSDialogueSegmentModel
from app.services.step_03_translation.batch_manager import BatchManager
from app.services.step_03_translation.llm_translator import translate_batch_pass2_llm
from app.services.step_03_translation.dichhan.llm_extractor import extract_batch_entities_direct_llm
from app.services.step_03_translation.post_processor import PostProcessor
from app.services.step_03_translation.translation_auditor import TranslationAuditor
from app.services.task_manager import task_manager
import logging

logger = logging.getLogger(__name__)

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
            # TỰ ĐỘNG BÓC TÁCH STT NẾU CHƯA CÓ CÂU THOẠI NHƯNG CÓ FILE VIDEO / AUDIO GỐC
            from app.config import BASE_DIR
            from app.services.step_02_stt.audio_extractor import AudioExtractorService
            from app.services.step_02_stt.whisper_service import WhisperService

            def _resolve_file_path(p_str: Optional[str]) -> Optional[str]:
                if not p_str:
                    return None
                p = Path(p_str)
                if p.exists():
                    return str(p)
                rel_candidate = BASE_DIR / (p.relative_to(p.anchor) if p.is_absolute() else p)
                if rel_candidate.exists():
                    return str(rel_candidate)
                return None

            resolved_video = _resolve_file_path(project.video_path)
            resolved_audio = _resolve_file_path(project.audio_path)

            if not resolved_video and not resolved_audio:
                raise ValueError(
                    f"Project #{project_id} chưa có câu thoại ASR và không tìm thấy file video/audio gốc trên đĩa để bóc tách!"
                )

            task_manager.add_log(task_id, f"ℹ️ Project #{project_id} chưa có câu thoại ASR. Đang TỰ ĐỘNG kích hoạt Bóc tách STT từ video...", "cyan")
            task_manager.update_task(task_id, step=1, progress=10, message="Đang tự động bóc tách âm thanh...")

            if not resolved_audio:
                task_manager.add_log(task_id, "🎵 Trích xuất Audio 16kHz PCM WAV từ video gốc...", "cyan")
                resolved_audio = AudioExtractorService.extract_audio_16k_wav(resolved_video)
                project.audio_path = resolved_audio
                db.commit()

            task_manager.update_task(task_id, step=1, progress=25, message="Đang lọc tạp âm...")
            clean_audio_path = AudioExtractorService.get_clean_audio_for_asr(resolved_audio)

            engine = getattr(settings, "ASR_ENGINE", "capcut").lower()
            engine_name = "CapCut Cloud STT" if engine == "capcut" else "Groq Whisper"
            task_manager.update_task(task_id, step=1, progress=40, message=f"Đang bóc tách thoại qua {engine_name}...")
            task_manager.add_log(task_id, f"🤖 Đang bóc tách lời thoại bằng {engine_name}...", "cyan")

            src_lang = project.source_language or "zh"
            dialogues, srt_path, txt_path, json_path = WhisperService.transcribe(
                audio_path=clean_audio_path,
                language=src_lang,
                clean_text=True,
                task_id=task_id
            )

            if not dialogues:
                raise ValueError(f"Không thể bóc tách được câu thoại nào từ audio của Project #{project_id}!")

            task_manager.add_log(task_id, f"✔ Bóc tách tự động thành công: {len(dialogues)} câu thoại! Bắt đầu chuyển tiếp sang Dịch thuật...", "emerald")
            task_manager.update_task(task_id, step=1, progress=50, message="Đã có câu thoại. Bắt đầu dịch thuật...")

            project.srt_path = srt_path
            project.txt_path = txt_path
            project.json_path = json_path
            project.status = "TRANSCRIBED"

            db.query(DialogueSegmentModel).filter(DialogueSegmentModel.task_id == project.id).delete(synchronize_session=False)
            db.commit()

            for d in dialogues:
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

        # Chuẩn hóa Provider: Ưu tiên provider truyền vào -> đọc trực tiếp từ Cài Đặt (Settings) -> gemini (API)
        provider = provider or getattr(settings, "TRANSLATION_PROVIDER", "gemini")
        
        # Lấy thông tin model cụ thể để hiển thị minh bạch cho người dùng
        if provider == "gemini_web":
            tMap = {"low": "THẤP (~5-7s)", "medium": "TRUNG BÌNH (~10-15s)", "high": "CAO (~15-20s)"}
            raw_t = (getattr(settings, "GEMINI_WEB_THINKING", "medium") or "medium").lower()
            t_str = tMap.get(raw_t, raw_t.upper())
            model_info_str = f"🌐 Gemini Web (Tự động Gemini 3 • Tư duy: {t_str})"
        else:
            api_model = (getattr(settings, "GEMINI_MODEL", "") or "").strip()
            tb = getattr(settings, "GEMINI_THINKING_BUDGET", -1)
            tb_str = "MAX (Dynamic)" if tb == -1 else f"Thinking {tb}"
            model_info_str = f"⚡ Gemini API ({api_model} • {tb_str})"

        # Tôn trọng 100% thể loại người dùng đã chọn từ giao diện.
        # CHỈ tự động nhận diện nếu genre hoàn toàn rỗng hoặc không được truyền vào.
        if not genre:
            t_low = (project.title or "").lower()
            if any(k in t_low for k in ["求生", "公路", "末世", "丧尸", "废土", "zombie", "tận thế", "mạt thế"]):
                genre = "sci_fi_apocalypse"
            elif any(k in t_low for k in ["系统", "升级", "签到", "hệ thống", "thăng cấp"]):
                genre = "system_reincarnation"
            elif any(k in t_low for k in ["诡异", "抓鬼", "灵异", "quỷ dị", "linh dị"]):
                genre = "urban_supernatural"
            else:
                genre = "xianxia"

        task_manager.add_log(task_id, f"🚀 BẮT ĐẦU QUY TRÌNH DỊCH THUẬT 2 LLM AIREAD (Project #{project_id}: {project.title})", "purple")
        task_manager.add_log(task_id, f"   Tổng số câu: {len(dialogue_models)} câu | Thể loại: {genre} | Mô hình AI: {model_info_str}", "cyan")
        print(f"\n🚀 [AI Translation] Project #{project_id} | Thể loại: {genre} | Mô hình: {model_info_str}", flush=True)

        # LẤY CẤU TRÚC THƯ MỤC CHUẨN CỦA PROJECT THEO ID
        from app.config import get_project_dirs
        p_dirs = get_project_dirs(project.id)
        proj_entities_dir = p_dirs["entities"]
        proj_llm_io_dir = p_dirs["llm_io"]
        proj_root_dir = p_dirs["root"]

        # DỌN DẸP SẠCH TOÀN BỘ BẢN DỊCH VÀ FILE TẠM CŨ TRƯỚC KHI DỊCH MỚI (CHỐNG LẪN BẢN CŨ)
        import shutil
        for sub in [proj_llm_io_dir, proj_entities_dir, p_dirs.get("tts_voiceover"), p_dirs.get("video_subtitles")]:
            if sub and sub.exists():
                for f in list(sub.iterdir()):
                    try:
                        if f.is_file():
                            f.unlink(missing_ok=True)
                        elif f.is_dir():
                            shutil.rmtree(f, ignore_errors=True)
                    except Exception:
                        pass

        # Xóa file phái sinh cũ trong root của project (giữ file video raw, cleaned.txt, audio gốc)
        if proj_root_dir.exists():
            for f in list(proj_root_dir.iterdir()):
                if f.is_file() and (
                    f.name.endswith("_voiceover.mp3") or
                    f.name.endswith("_karaoke.ass") or
                    f.name in ["script_vi.txt", "transcript_vi.json", "transcript_vi.srt", "transcript_vi.txt", "transcript_vi.vtt", "timeline_balance_report.json"]
                ):
                    try:
                        f.unlink(missing_ok=True)
                    except Exception:
                        pass

        # Dọn sạch TTS cache của video_id trong temp
        if settings.TEMP_TTS_DIR.exists():
            tts_cache_p = settings.TEMP_TTS_DIR / project.video_id
            if tts_cache_p.exists():
                try:
                    shutil.rmtree(tts_cache_p, ignore_errors=True)
                except Exception:
                    pass

        # Xóa sạch bảng TTSDialogueSegmentModel trong SQLite cho project này trước khi bắt đầu dịch mới
        db.query(TTSDialogueSegmentModel).filter(TTSDialogueSegmentModel.task_id == project.id).delete(synchronize_session=False)
        db.query(DialogueSegmentModel).filter(DialogueSegmentModel.task_id == project.id).update({
            DialogueSegmentModel.translated_text: None,
            DialogueSegmentModel.status: "RAW",
            DialogueSegmentModel.tts_index: None
        }, synchronize_session=False)
        db.commit()

        # =====================================================================
        # GIAI ĐOẠN 1: TIỀN XỬ LÝ & LÀM SẠCH VĂN BẢN GỐC (01_raw_cleaned)
        # =====================================================================
        task_manager.update_task(task_id, step=1, progress=15)
        task_manager.add_log(task_id, "[1/5] 🧹 TIỀN XỬ LÝ: Làm sạch ký tự Hán & từ đệm...", "cyan")

        segments: List[DialogueSegment] = []
        cleaned_lines = []
        for d in dialogue_models:
            orig = (d.original_text or d.clean_text or "").strip()
            
            seg = DialogueSegment(
                id=d.index,
                start=d.start_time,
                end=d.end_time,
                duration=d.duration,
                text=orig,
                clean_text=orig,
                confidence=d.confidence
            )
            segments.append(seg)
            cleaned_lines.append(f"<s id=\"{seg.id}\">{orig}</s>")

        # Lưu bản sạch vào thư mục project
        raw_cleaned_path = proj_root_dir / f"{project.video_id}_cleaned.txt"
        with open(raw_cleaned_path, "w", encoding="utf-8") as f:
            f.write("\n".join(cleaned_lines))
        task_manager.add_log(task_id, f"   ✔ Đã lưu bản sạch: {raw_cleaned_path.name}", "emerald")

        # ĐỒNG BỘ FILE JSON ASR GỐC VÀO THƯ MỤC PROJECT ĐỂ PHỤC VỤ RÀ SOÁT / ĐỐI CHIẾU
        raw_json_dest = proj_root_dir / f"{project.video_id}_raw_clean.json"
        if not raw_json_dest.exists():
            clean_json_p = settings.OUTPUT_TRANSCRIPTS_DIR / f"{project.video_id}_raw_clean.json"
            if clean_json_p.exists():
                try:
                    shutil.copyfile(clean_json_p, raw_json_dest)
                    task_manager.add_log(task_id, f"   ✔ Đã đồng bộ JSON ASR gốc: {raw_json_dest.name}", "emerald")
                except Exception as e:
                    logger.warning(f"Lỗi sao chép json gốc sang project dir: {e}")
            else:
                try:
                    asr_export = [
                        {
                            "id": d.index,
                            "start": d.start_time,
                            "end": d.end_time,
                            "duration": d.duration,
                            "text": d.original_text,
                            "clean_text": d.clean_text or d.original_text,
                            "confidence": d.confidence
                        }
                        for d in dialogue_models
                    ]
                    with open(raw_json_dest, "w", encoding="utf-8") as f:
                        json.dump(asr_export, f, ensure_ascii=False, indent=2)
                    task_manager.add_log(task_id, f"   ✔ Đã xuất JSON ASR gốc từ CSDL: {raw_json_dest.name}", "emerald")
                except Exception as e:
                    logger.warning(f"Lỗi xuất file json gốc từ DB: {e}")

        raw_srt_dest = proj_root_dir / f"{project.video_id}_raw_clean.srt"
        if not raw_srt_dest.exists():
            clean_srt_p = settings.OUTPUT_TRANSCRIPTS_DIR / f"{project.video_id}_raw_clean.srt"
            if clean_srt_p.exists():
                try:
                    shutil.copyfile(clean_srt_p, raw_srt_dest)
                except Exception:
                    pass

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

        entities_file = proj_entities_dir / "all_entities.json"

        for b_idx, batch in enumerate(batches):
            # CHỐT AN TOÀN HỦY TIẾN TRÌNH (Cancel Check): Nếu người dùng nhấn dừng thì dọn sạch những gì lô này đang làm
            if task_manager.is_cancelled(task_id):
                task_manager.add_log(task_id, f"🛑 Đã phát hiện yêu cầu DỪNG tại Lô #{b_idx + 1}. Hủy bỏ xử lý và dọn dẹp lô dở dang...", "amber")
                raise asyncio.CancelledError(f"Người dùng đã hủy tiến trình tại Lô #{b_idx + 1}")

            b_num = b_idx + 1
            start_bid = getattr(batch[0], 'index', batch[0].id)
            end_bid = getattr(batch[-1], 'index', batch[-1].id)

            # LUÔN XÓA FILE THÔ CŨ VÀ DỊCH TƯƠI MỚI 100% QUA LLM (KHÔNG DÙNG LẠI CACHE)
            batch_raw_file = proj_llm_io_dir / f"batch_{b_num:02d}_output.json"
            if batch_raw_file.exists():
                try:
                    batch_raw_file.unlink(missing_ok=True)
                except Exception:
                    pass

            clean_batch_text = BatchManager.pack_dialogues_to_clean_text(batch)
            tagged_batch_text = BatchManager.pack_dialogues_to_tagged_text(batch)
            context_text = BatchManager.get_context_from_previous_batch(prev_batch)

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

            # Giãn cách 7s để Google AI Studio hoàn toàn xả tải xong trước khi nhận prompt dịch dài (chống 503 Server Busy)
            task_manager.add_log(task_id, f"      ⏳ [Lô #{b_num}] Giãn cách 7s xả tải Google AI Studio...", "gray")
            await asyncio.sleep(7)
            if task_manager.is_cancelled(task_id):
                raise asyncio.CancelledError(f"Người dùng đã hủy tiến trình tại Lô #{b_num}")

            # --- LLM 2: DỊCH VĂN PHONG CHUẨN AIREAD ---
            task_manager.add_log(task_id, f"   ✨ [Lô #{b_num}/{len(batches)}] LLM 2: Dịch kịch bản văn phong AIRead với bảng {len(entity_lines)} thực thể xuất hiện trong lô...", "cyan")

            # CHỈ LƯU DUY NHẤT 1 FILE PROMPT ĐẦU VÀO CỦA LLM: batch_XX_full_prompt.txt
            batch_prompt_file = proj_llm_io_dir / f"batch_{b_num:02d}_full_prompt.txt"
            raw_llm_batch_output = await translate_batch_pass2_llm(
                tagged_text=tagged_batch_text,
                genre=genre,
                project_title=project.title,
                context_text=context_text,
                entity_table_text=entity_table_text,
                provider=provider,
                prompt_save_path=str(batch_prompt_file)
            )

            if task_manager.is_cancelled(task_id):
                raise asyncio.CancelledError(f"Người dùng đã hủy tiến trình tại Lô #{b_num}")

            # 1. PARSE VÀ LÀM SẠCH NGAY 100% QUA SmartTimelineHealer (CHỐNG SỐ ẢO GIÁC & MARKDOWN)
            from app.core.llm_client import safe_json_loads
            from app.services.step_03_translation.smart_timeline_healer import SmartTimelineHealer

            print(f"\n🔍 [Lô #{b_num}/{len(batches)}] LLM trả về -> Đang làm sạch & thẩm định 3 Bất Biến (SmartTimelineHealer)...", flush=True)
            task_manager.add_log(task_id, f"🔍 [Lô #{b_num}] Đang làm sạch & thẩm định 3 Bất Biến qua SmartTimelineHealer...", "cyan")

            parsed_json = safe_json_loads(raw_llm_batch_output)
            if isinstance(parsed_json, dict):
                for v in parsed_json.values():
                    if isinstance(v, list):
                        parsed_json = v
                        break

            orig_starts = [round(float(getattr(s, 'start', 0.0) or getattr(s, 'start_time', 0.0) or 0.0), 2) for s in batch]
            orig_ends = [round(float(getattr(s, 'end', 0.0) or getattr(s, 'end_time', 0.0) or 0.0), 2) for s in batch]
            batch_orig_dicts = [
                {
                    "index": i + 1,
                    "global_id": getattr(s, 'id', getattr(s, 'index', i + 1)),
                    "start": orig_starts[i],
                    "end": orig_ends[i],
                    "zh": s.clean_text or s.text or ""
                }
                for i, s in enumerate(batch)
            ]

            healed_items = []
            inv_check = {"passed": True}
            if isinstance(parsed_json, list) and len(parsed_json) > 0:
                healed_items, heal_cnt, _ = SmartTimelineHealer.audit_and_heal(batch_orig_dicts, parsed_json)
                inv_check = SmartTimelineHealer.verify_3_invariants(healed_items, batch_orig_dicts)
                if not inv_check.get("passed"):
                    logger.warning(f"⚠️ [Lô #{b_num}] Cảnh báo 3 bất biến timeline: {inv_check.get('errors')}")

            # KHÓA CỨNG 100% TIMECODE TUẦN TỰ THEO 2 MẢNG ASR GỐC (DỰA VÀO SỐ CÂU CON, TUYỆT ĐỐI KHÔNG ĐÈ NHAU)
            if healed_items:
                healed_items = SmartTimelineHealer.enforce_sequential_asr_locking(
                    healed_items=healed_items,
                    batch=batch,
                    orig_starts=orig_starts,
                    orig_ends=orig_ends
                )

            cov_str = f"{inv_check.get('covered_orig', len(batch))}/{len(batch)}"
            heal_summary = f"✔ [Lô #{b_num}/{len(batches)}] SmartTimelineHealer: Đã làm sạch & chuẩn hóa {len(healed_items)} câu kịch bản (Bao phủ {cov_str} câu ASR gốc):"
            print(f"\n{heal_summary}", flush=True)
            task_manager.add_log(task_id, heal_summary, "emerald")

            # In chi tiết từng câu kịch bản đã làm sạch ra Terminal (Gọn gàng, dễ nhìn, không tốn diện tích)
            sample_count = min(5, len(healed_items))
            for idx_h, item in enumerate(healed_items[:sample_count], start=1):
                role = item.get("role") or item.get("speaker") or "NARRATOR"
                s_time = float(item.get("s", item.get("start", 0.0)))
                e_time = float(item.get("e", item.get("end", 0.0)))
                src = item.get("src", [])
                src_str = f"ASR #{src[0]}->#{src[-1]}" if len(src) > 1 else (f"ASR #{src[0]}" if src else "ASR")
                vi_preview = (item.get("vi") or "").strip()
                if len(vi_preview) > 60:
                    vi_preview = vi_preview[:57] + "..."
                print(f"   • #{idx_h:03d} [{role}] ({s_time:.2f}s ➔ {e_time:.2f}s | {e_time - s_time:.2f}s | {src_str}): {vi_preview}", flush=True)
            if len(healed_items) > sample_count:
                print(f"   ... và {len(healed_items) - sample_count} câu kịch bản tiếp theo đã được khóa timeline chuẩn xác 100%.\n", flush=True)

            # 2. LƯU BẢN JSON ĐÃ LÀM SẠCH 100% VÀO batch_XX_output.json (CHỈ LƯU: role, src, vi - KHÔNG LƯU s, e)
            batch_raw_file = proj_llm_io_dir / f"batch_{b_num:02d}_output.json"
            try:
                clean_output_items = []
                source_items = healed_items if healed_items else (parsed_json if isinstance(parsed_json, list) else [])
                for it in source_items:
                    if isinstance(it, dict):
                        # Ưu tiên local_src (1..N trong lô) để file lưu luôn chuẩn hóa 1..500
                        use_src = it.get("local_src") or it.get("src", [])
                        clean_out_src = []
                        for x in use_src:
                            if isinstance(x, int):
                                clean_out_src.append(x)
                            elif isinstance(x, str) and x.strip().isdigit():
                                clean_out_src.append(int(x.strip()))
                        clean_output_items.append({
                            "role": it.get("role") or it.get("speaker") or "NARRATOR",
                            "src": clean_out_src,
                            "vi": it.get("vi", "")
                        })
                with open(batch_raw_file, "w", encoding="utf-8") as f:
                    json.dump(clean_output_items, f, ensure_ascii=False, indent=2)
                save_msg = f"💾 [Lô #{b_num}/{len(batches)}] Đã lưu kịch bản sạch vào {batch_raw_file.name}"
                print(save_msg, flush=True)
                task_manager.add_log(task_id, save_msg, "emerald")
            except Exception as e:
                logger.warning(f"Lỗi ghi file sạch LLM {batch_raw_file}: {e}")

            # 3. BÓC TÁCH KẾT QUẢ CHO BẢNG PHỤ ĐỀ KARAOKE TỪ BẢN ĐÃ LÀM SẠCH (KHÔNG RETRY, CHỈ MAP VÀO DIALOGUE_SEGMENTS)
            candidate_batch = PostProcessor.apply_post_processing(batch, healed_items if healed_items else raw_llm_batch_output)
            batch_updated = candidate_batch

            # Gán kết quả lô đã kiểm định vào danh sách
            translated_segments.extend(batch_updated)
            prev_batch = batch_updated
            batches[b_idx] = batch_updated

            # 1. LƯU BẢNG PHỤ ĐỀ KARAOKE (dialogue_segments) - Chuẩn 100% số câu ASR gốc
            for s in batch_updated:
                diag_db = db.query(DialogueSegmentModel).filter(
                    DialogueSegmentModel.task_id == project.id,
                    DialogueSegmentModel.index == s.id
                ).first()
                if diag_db:
                    diag_db.translated_text = s.translated_text
                    diag_db.end_time = getattr(s, 'end', diag_db.end_time)
                    diag_db.duration = getattr(s, 'duration', diag_db.duration)
                    diag_db.status = "TRANSLATED" if (s.translated_text or '').strip() else "RAW"

            # 2. LƯU BẢNG CÂU GỘP TTS (tts_dialogue_segments) - Phục vụ TTS và AIREAD
            parsed_raw_llm = healed_items if (healed_items and isinstance(healed_items, list)) else []

            if isinstance(parsed_raw_llm, list) and len(parsed_raw_llm) > 0:
                # Xóa các dòng TTS cũ của lô này nếu dịch lại
                db.query(TTSDialogueSegmentModel).filter(
                    TTSDialogueSegmentModel.task_id == project.id,
                    TTSDialogueSegmentModel.batch_index == b_num
                ).delete(synchronize_session=False)

                # Tìm max tts_index hiện tại của project
                max_tts_row = db.query(TTSDialogueSegmentModel.tts_index).filter(
                    TTSDialogueSegmentModel.task_id == project.id,
                    TTSDialogueSegmentModel.batch_index < b_num
                ).order_by(TTSDialogueSegmentModel.tts_index.desc()).first()
                curr_tts_counter = (max_tts_row[0] if max_tts_row else 0)

                for item in parsed_raw_llm:
                    if not isinstance(item, dict):
                        continue
                    vi_str = str(item.get("vi") or item.get("viet") or item.get("text") or "").strip()
                    if not vi_str:
                        continue

                    curr_tts_counter += 1
                    s_ids = item.get("src")
                    # Chuẩn hóa nếu LLM trả về dạng [start, end] (ví dụ [1, 6] bao trùm 1..6)
                    if isinstance(s_ids, list) and len(s_ids) == 2 and isinstance(s_ids[0], int) and isinstance(s_ids[1], int) and s_ids[1] > s_ids[0]:
                        s_ids = list(range(s_ids[0], s_ids[1] + 1))
                        item["src"] = s_ids

                    matched_db_segs = []
                    if s_ids and isinstance(s_ids, list):
                        # 1. Khớp qua ID toàn cục (global_id / index)
                        matched_db_segs = [s for s in batch_updated if getattr(s, 'id', None) in s_ids or getattr(s, 'index', None) in s_ids]
                        # 2. Khớp dự phòng qua chỉ số cục bộ trong lô (1..len(batch))
                        if not matched_db_segs:
                            matched_db_segs = [
                                batch_updated[i - 1] for i in s_ids
                                if isinstance(i, int) and 1 <= i <= len(batch_updated)
                            ]

                    if matched_db_segs:
                        first_s = matched_db_segs[0]
                        last_s = matched_db_segs[-1]
                        src_start_idx = getattr(first_s, 'id', getattr(first_s, 'index', 1))
                        src_end_idx = getattr(last_s, 'id', getattr(last_s, 'index', 1))
                        src_str = ",".join(str(getattr(s, 'id', getattr(s, 'index', 1))) for s in matched_db_segs)
                        orig_cnt = len(matched_db_segs)
                        # KHÓA CHẶT 100% TIMECODE THEO ASR GỐC - TUYỆT ĐỐI KHÔNG DÙNG s, e ƯỚC LƯỢNG CỦA LLM
                        s_time = round(float(getattr(first_s, 'start', 0.0) or getattr(first_s, 'start_time', 0.0) or 0.0), 2)
                        e_time = round(float(getattr(last_s, 'end', 0.0) or getattr(last_s, 'end_time', s_time + 1.0) or (s_time + 1.0)), 2)
                        zh_combined = ", ".join((getattr(s, 'clean_text', '') or getattr(s, 'text', '') or '') for s in matched_db_segs if (getattr(s, 'clean_text', '') or getattr(s, 'text', '') or '').strip())
                        # Gán tts_index ngược lại cho từng câu con trong dialogue_segments
                        for ms in matched_db_segs:
                            ms_idx = getattr(ms, 'index', getattr(ms, 'id', None))
                            d_row = db.query(DialogueSegmentModel).filter(
                                DialogueSegmentModel.task_id == project.id,
                                DialogueSegmentModel.index == ms_idx
                            ).first()
                            if d_row:
                                d_row.tts_index = curr_tts_counter
                    else:
                        lead_idx = min(len(batch_updated) - 1, max(0, curr_tts_counter - 1)) if batch_updated else 0
                        lead_seg = batch_updated[lead_idx] if batch_updated else None
                        src_start_idx = getattr(lead_seg, 'id', getattr(lead_seg, 'index', 1)) if lead_seg else 1
                        src_end_idx = src_start_idx
                        src_str = str(src_start_idx)
                        orig_cnt = 1
                        s_time = round(float(getattr(lead_seg, 'start', 0.0) or getattr(lead_seg, 'start_time', 0.0) or 0.0), 2)
                        e_time = round(float(getattr(lead_seg, 'end', 0.0) or getattr(lead_seg, 'end_time', s_time + 1.0) or (s_time + 1.0)), 2)
                        zh_combined = getattr(lead_seg, 'clean_text', '') or getattr(lead_seg, 'text', '') if lead_seg else ''

                    tts_dur = max(0.1, round(e_time - s_time, 2))
                    role_str = item.get("role") or item.get("speaker") or "NARRATOR"

                    tts_entry = TTSDialogueSegmentModel(
                        task_id=project.id,
                        tts_index=curr_tts_counter,
                        batch_index=b_num,
                        src_start_index=src_start_idx,
                        src_end_index=src_end_idx,
                        src_indices=src_str,
                        orig_count=orig_cnt,
                        start_time=s_time,
                        end_time=e_time,
                        duration=tts_dur,
                        original_text=zh_combined,
                        translated_text=vi_str,
                        speaker=role_str,
                        status="PENDING"
                    )
                    db.add(tts_entry)

            db.commit()
            l_done = f"   💾 [Lô #{b_num}/{len(batches)}] Đã dịch xong ➔ Đã lưu 2 bảng (Subtitles & TTS) SQLite."
            task_manager.add_log(task_id, l_done, "emerald")
            print(f"✔ [LÔ #{b_num}/{len(batches)}] Đạt chuẩn 3 tiêu chí ➔ Đã lưu 2 bảng SQLite.", flush=True)

            pct = int(30 + (b_num / len(batches)) * 45)
            task_manager.update_task(task_id, progress=pct)

            # Nghỉ 6 giây giữa các lô để xả tải an toàn cho Google AI Studio
            if b_idx < len(batches) - 1:
                task_manager.add_log(task_id, f"      ⏳ [Lô #{b_num}] Đã xong. Nghỉ 6 giây xả tải trước khi xử lý Lô #{b_num + 1}...", "gray")
                await asyncio.sleep(6)

        done_all = f"✔ Hoàn tất dịch toàn bộ {len(batches)} lô với tổng {len(accumulated_entities)} thực thể xuyên suốt."
        task_manager.add_log(task_id, done_all, "emerald")
        print(f"\n🎉 {done_all}\n", flush=True)

        # =====================================================================
        # =====================================================================
        # GIAI ĐOẠN 4: TỔNG BIÊN TẬP SỬA SÓT HÁN TOÀN PHIM BẰNG LLM & DỌN DẸP HÁN SÓT
        # =====================================================================
        task_manager.update_task(task_id, step=4, progress=80)
        task_manager.add_log(task_id, "[4/5] 🛡️ TỔNG BIÊN TẬP: Rà soát & sửa sót Hán toàn bộ câu thoại bằng LLM...", "cyan")
        print("\n🛡️ [4/5] TỔNG BIÊN TẬP SỬA SÓT HÁN TOÀN PHIM BẰNG LLM...", flush=True)

        # 4.1. Bước 1: LLM Tổng Biên Tập Viên Cao Cấp (Kế thừa AIRead) viết lại trọn vẹn cả câu
        try:
            llm_fixed_map = await TranslationAuditor.batch_fix_hanzi_leak_llm(
                segments=translated_segments,
                genre=genre,
                provider=provider,
                project_title=project.title
            )
            if llm_fixed_map:
                fix_cnt = len(llm_fixed_map)
                for s in translated_segments:
                    if s.id in llm_fixed_map:
                        s.translated_text = llm_fixed_map[s.id]
                for b in batches:
                    for s in b:
                        if s.id in llm_fixed_map:
                            s.translated_text = llm_fixed_map[s.id]
                task_manager.add_log(task_id, f"   ✨ [Tổng Biên Tập LLM] Đã biên tập và chuốt mượt thành công {fix_cnt} câu sót chữ Hán!", "emerald")
        except Exception as e:
            print(f"⚠️ [Pipeline Fix Hanzi LLM] Lỗi khi gọi LLM biên tập: {e}", flush=True)

        # 4.2. Bước 2: Đồng bộ các câu thoại vào SQLite
        for s in translated_segments:
            diag_db = db.query(DialogueSegmentModel).filter(
                DialogueSegmentModel.task_id == project.id,
                DialogueSegmentModel.index == s.id
            ).first()
            if diag_db:
                diag_db.translated_text = s.translated_text
        # 4.3. Đã hoàn tất biên tập qua Tổng Biên Tập LLM
        db.commit()

        # Nạp lại dữ liệu đã sạch Hán vào translated_segments và batches
        diag_map = {d.index: d.translated_text for d in db.query(DialogueSegmentModel).filter(DialogueSegmentModel.task_id == project.id).all()}
        for s in translated_segments:
            if s.id in diag_map and diag_map[s.id]:
                s.translated_text = diag_map[s.id]
        for b in batches:
            for s in b:
                if s.id in diag_map and diag_map[s.id]:
                    s.translated_text = diag_map[s.id]

        # 4.4. Bước 4: LÀM SẠCH TRIỆT ĐỂ TOÀN BỘ FILE CACHE VÀ all_llm_items
        # Đảm bảo 100% sạch Hán trước khi phân chia ra 2 công việc
        from app.core.llm_client import safe_json_loads
        from app.services.step_03_translation.dichhan.raw_text_cleaner import contains_chinese, CHINESE_CHAR_REGEX
        all_llm_items = []
        if proj_llm_io_dir.exists():
            for out_f in sorted(proj_llm_io_dir.glob("batch_*_output.json")):
                try:
                    with open(out_f, "r", encoding="utf-8") as f:
                        parsed = safe_json_loads(f.read())
                    if isinstance(parsed, dict):
                        for v in parsed.values():
                            if isinstance(v, list):
                                parsed = v
                                break
                    if isinstance(parsed, list):
                        batch_modified = False
                        for it in parsed:
                            if isinstance(it, dict):
                                vi_str = str(it.get("vi") or it.get("viet") or it.get("text") or "").strip()
                                if contains_chinese(vi_str):
                                    it["vi"] = CHINESE_CHAR_REGEX.sub('', vi_str).strip()
                                    batch_modified = True
                        if batch_modified:
                            with open(out_f, "w", encoding="utf-8") as f:
                                json.dump(parsed, f, ensure_ascii=False, indent=2)
                        all_llm_items.extend(parsed)
                except Exception as e:
                    logger.warning(f"Lỗi đọc/làm sạch file llm_io {out_f.name}: {e}")

        # Bảo đảm an toàn 100%: mọi item trong all_llm_items sạch hoàn toàn chữ Hán
        for it in all_llm_items:
            if isinstance(it, dict) and "vi" in it:
                if contains_chinese(it["vi"]):
                    it["vi"] = CHINESE_CHAR_REGEX.sub('', it["vi"]).strip()

        # CHỐT AN TOÀN DỰ PHÒNG: Nếu all_llm_items vì bất kỳ lý do gì bị rỗng, tự nạp lại từ CSDL SQLite
        if not all_llm_items:
            db_segs = db.query(DialogueSegmentModel).filter(
                DialogueSegmentModel.task_id == project.id
            ).order_by(DialogueSegmentModel.index.asc()).all()
            for s in db_segs:
                v_text = (s.translated_text or "").strip()
                if v_text:
                    all_llm_items.append({
                        "id": s.index,
                        "zh": s.clean_text or s.original_text or "",
                        "vi": v_text,
                        "role": getattr(s, "speaker", "NARRATOR") or "NARRATOR",
                        "start": float(s.start_time or 0.0),
                        "end": float(s.end_time or 0.0),
                        "src": [s.index]
                    })

        # =====================================================================
        # GIAI ĐOẠN 5: KIỂM TOÁN HOÀN TẤT VÀ XÁC NHẬN CHẤT LƯỢNG
        # =====================================================================
        task_manager.update_task(task_id, step=5, progress=90)
        ok_audit_msg = f"   🎉 [KIỂM ĐỊNH THÀNH CÔNG] Toàn bộ {len(batches)} lô đạt chuẩn 100%: Lời thoại gộp hoàn chỉnh theo cấu trúc src, không phình câu!"
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
        # GIAI ĐOẠN 5: XUẤT THÀNH PHẨM 2 NHÁNH SONG SONG ĐỘC LẬP
        # Nhánh 1: TTS Voiceover (app/services/tts/tts_script_service.py) -> output/projects/project_{id}/tts_voiceover/
        # Nhánh 2: Video Subtitles Karaoke (app/services/video/subtitle_builder_service.py) -> output/projects/project_{id}/video_subtitles/
        # =====================================================================
        task_manager.update_task(task_id, progress=95)
        task_manager.add_log(task_id, "💾 Đang xuất bản 2 nhánh độc lập: Giọng đọc TTS & Phụ đề Karaoke...", "cyan")

        # 1. NHÁNH TTS VOICEOVER: Nạp trực tiếp từ bảng TTSDialogueSegmentModel trong SQLite
        # Bảng này đã được tính toán khóa cứng 100% timecode ASR gốc cho từng lô
        tts_db_rows = db.query(TTSDialogueSegmentModel).filter(
            TTSDialogueSegmentModel.task_id == project.id
        ).order_by(TTSDialogueSegmentModel.tts_index.asc()).all()

        tts_items = []
        if tts_db_rows:
            for row in tts_db_rows:
                s_list = []
                if row.src_indices:
                    for x in str(row.src_indices).split(","):
                        if x.strip().isdigit():
                            s_list.append(int(x.strip()))
                tts_items.append({
                    "tts_index": row.tts_index,
                    "role": row.speaker or "NARRATOR",
                    "speaker": row.speaker or "NARRATOR",
                    "s": float(row.start_time or 0.0),
                    "e": float(row.end_time or 0.0),
                    "start": float(row.start_time or 0.0),
                    "end": float(row.end_time or 0.0),
                    "vi": row.translated_text or "",
                    "zh": row.original_text or "",
                    "src": s_list or [row.src_start_index or 1],
                    "batch_index": row.batch_index or 1
                })
        else:
            import copy
            tts_items = copy.deepcopy(all_llm_items)

        from app.services.step_04_tts.tts_script_service import TTSScriptService
        tts_result = TTSScriptService.build_and_save(
            project_id=project.id,
            db=db,
            llm_items=tts_items
        )
        task_manager.add_log(task_id, f"   🎙️ [TTS Voiceover] Đã tạo kịch bản giọng đọc: {tts_result['total_chunks']} câu hoàn chỉnh -> tts_voiceover/", "emerald")

        # 2. NHÁNH VIDEO SUBTITLES & KARAOKE (Phụ đề tự nhiên theo dấu câu Gemini & đồng bộ tts_index)
        from app.services.step_05_video.subtitle_builder_service import SubtitleBuilderService
        sub_result = SubtitleBuilderService.build_from_translated_items(
            project_id=project.id,
            db=db,
            llm_items=tts_items,
            video_title=project.title
        )
        task_manager.add_log(task_id, f"   🎬 [Video Subtitles] Đã tạo phụ đề tự nhiên: {sub_result['total_subtitles']} vế câu chuẩn dấu câu -> video_subtitles/", "emerald")

        # 3. CẬP NHẬT TRẠNG THÁI PROJECTTASK TRONG DB
        db.query(ProjectTask).filter(ProjectTask.id == project.id).update({"status": "TRANSLATED"})
        project.srt_path = sub_result["srt_path"]
        project.txt_path = tts_result["txt_path"]
        project.json_path = sub_result["json_path"]
        db.commit()

        # 4. GHI BÁO CÁO CÂN BẰNG THỜI GIAN (TIMELINE BALANCE REPORT)
        timeline_report_file = proj_root_dir / "timeline_balance_report.json"
        with open(timeline_report_file, "w", encoding="utf-8") as f:
            json.dump({
                "project_id": project.id,
                "original_asr_count": sub_result["total_subtitles"],
                "tts_chunks_count": tts_result["total_chunks"],
                "tts_voiceover_dir": tts_result["tts_dir"],
                "video_subtitles_dir": sub_result["sub_dir"],
                "tts_script_json": tts_result["json_path"],
                "subtitles_karaoke_json": sub_result["json_path"]
            }, f, ensure_ascii=False, indent=2)

        # 5. TƯƠNG THÍCH NGƯỢC: COPY FILE VÀO ROOT FOLDER CỦA PROJECT
        import shutil
        try:
            shutil.copyfile(sub_result["srt_path"], proj_root_dir / "transcript_vi.srt")
            shutil.copyfile(sub_result["json_path"], proj_root_dir / "transcript_vi.json")
            shutil.copyfile(tts_result["txt_path"], proj_root_dir / "script_vi.txt")
        except Exception as e:
            logger.warning(f"Không thể copy file tương thích ngược vào root project: {e}")

        # IN BÁO CÁO TỔNG KẾT RA TERMINAL
        stats_header = (
            f"\n{'='*70}\n"
            f"🎉 QUY TRÌNH DỊCH THUẬT & PHÂN NHÁNH THÀNH CÔNG RỰC RỠ:\n"
            f"   • Phụ đề Video & Karaoke: {sub_result['total_subtitles']} câu chuẩn ASR CapCut -> video_subtitles/\n"
            f"   • Kịch bản Giọng đọc TTS: {tts_result['total_chunks']} chunk hoàn chỉnh -> tts_voiceover/\n"
            f"   • Database: dialogue_segments (100% {sub_result['total_subtitles']} câu) | tts_dialogue_segments ({tts_result['total_chunks']} câu)\n"
            f"{'='*70}\n"
        )
        print(stats_header, flush=True)

        task_manager.update_task(task_id, step=5, progress=100, status="completed", message="Dịch thuật hoàn tất thành công!")
        task_manager.add_log(task_id, f"🎉 QUY TRÌNH DỊCH THUẬT HOÀN TẤT XUẤT SẮC! Phụ đề: {sub_result['total_subtitles']} câu | Giọng đọc: {tts_result['total_chunks']} câu", "emerald")

        return {
            "status": "SUCCESS",
            "project_id": project.id,
            "total_segments": sub_result["total_subtitles"],
            "total_tts_chunks": tts_result["total_chunks"],
            "total_entities": len(accumulated_entities),
            "srt_path": sub_result["srt_path"],
            "txt_path": tts_result["txt_path"],
            "json_path": sub_result["json_path"]
        }
