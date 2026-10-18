# -*- coding: utf-8 -*-
"""
app/services/tts/tts_script_service.py
==================================================
Dịch vụ Kịch bản Giọng đọc TTS (TTS Voiceover Service)
Chuyên trách độc lập 100%:
- Nhận danh sách câu dịch LLM gộp.
- Tự động gộp liên tục (Smart Continuous Chunk Merger) các câu cùng vai đọc và khoảng nghỉ hẹp.
- Bảo toàn câu văn tự nhiên, mượt mà, TUYỆT ĐỐI không chèn dấu chấm bậy bạ ở giữa từ.
- Lưu dữ liệu vào bảng tts_dialogue_segments trong Database SQLite.
- Xuất thành phẩm vào thư mục: output/projects/project_{id}/tts_voiceover/
    + script_tts.json
    + script_tts.txt
    + transcript_tts.srt
"""

import os
import re
import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session

from app.models.dialogue import TTSDialogueSegmentModel
from app.config import get_project_dirs

def format_timestamp(seconds: float) -> str:
    """Định dạng giây sang SRT timestamp 00:00:00,000."""
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis >= 1000:
        millis = 999
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{millis:03d}"

logger = logging.getLogger(__name__)


class TTSScriptService:
    ELLIPSIS_GAP_THRESHOLD = 0.85  # Ngưỡng khoảng nghỉ tối thiểu để đặt dấu "..." (giây)

    @staticmethod
    def clean_text_for_tts(text: str) -> str:
        """Làm sạch văn bản phục vụ TTS đọc chuẩn xác."""
        if not text:
            return ""
        t = text.strip()
        # Xóa các định dạng đánh số câu (1. , [1], Câu 1:...)
        t = re.sub(r'^(?:\[\s*\d+\s*\]|\(\s*\d+\s*\)|\{\s*\d+\s*\}|【\s*\d+\s*】)\s*[\.\:\-\–\—\s]*', '', t)
        t = re.sub(r'^(?:câu|thoại|đoạn|stt|dòng|line)\s*\d+\s*[\.\:\-\–\—\)\/\]\s]*\s*', '', t, flags=re.IGNORECASE)
        t = re.sub(r'^\d+\/\d+\s*[\.\:\-\–\—\s]*', '', t)
        t = re.sub(r'^\d+\s*[\.\:\-\–\—\)\/\]]+\s*', '', t)
        t = re.sub(r'^[^\w\s\(\[\{]+', '', t).strip()
        t = re.sub(r'\s+', ' ', t)

        # Xóa các dị tật dấu câu kép như ., ,. ,, nhưng BẢO TỒN dấu ba chấm "..."
        t = re.sub(r'\,[\,\.\s]*\,', ',', t)
        t = re.sub(r'\,\.+', '.', t)
        t = re.sub(r'\.\,+', ',', t)
        t = re.sub(r'\.{4,}', '...', t)
        t = re.sub(r'(?<!\.)\.\.(?!\.)', '.', t)
        return t

    @classmethod
    def merge_continuous_chunks(
        cls,
        raw_items: List[Dict[str, Any]],
        enable_merge: Optional[bool] = None
    ) -> List[Dict[str, Any]]:
        """
        Gộp thông minh các chunk liên tiếp của CÙNG MỘT NHÂN VẬT (Smart Continuous Chunk Merger):
        - Nếu enable_merge == False (Cờ TTS_MERGE_CHUNKS = False):
            + KHÔNG GỘP CÂU - Tôn trọng 100% từng câu LLM dịch (1 câu LLM = 1 chunk TTS).
            + Tránh tình trạng chunk quá dài khiến CapCut TTS nuốt chữ hoặc mất file âm thanh.
        - Nếu enable_merge == True:
            + Gộp các câu liên tiếp cùng nhân vật nếu gap < 0.85s để giọng đọc mượt mà.
        """
        if not raw_items:
            return []

        input_items = raw_items
        import copy
        copied_items = copy.deepcopy(raw_items)

        # 1. Thu thập và làm sạch danh sách mẩu thoại ban đầu
        cleaned_items = []
        for orig_idx, item in enumerate(copied_items):
            vi_text = cls.clean_text_for_tts(item.get("vi") or item.get("viet") or item.get("text") or "")
            if not vi_text:
                continue

            s_time = float(item.get("s", item.get("start", 0.0)))
            e_time = float(item.get("e", item.get("end", s_time + 1.0)))
            dur = max(0.1, round(e_time - s_time, 2))
            role = str(item.get("role") or item.get("speaker") or "NARRATOR").strip()
            src_list = item.get("src", [])
            if not isinstance(src_list, list):
                src_list = [int(src_list)] if str(src_list).isdigit() else []
            elif len(src_list) == 2 and isinstance(src_list[0], int) and isinstance(src_list[1], int) and src_list[1] > src_list[0]:
                src_list = list(range(src_list[0], src_list[1] + 1))
            zh_text = str(item.get("zh") or item.get("orig") or "").strip()
            batch_idx = int(item.get("batch_index", 1))

            cleaned_items.append({
                "orig_idx": orig_idx,
                "orig_indices": [orig_idx],
                "s": s_time,
                "e": e_time,
                "duration": dur,
                "vi": vi_text,
                "zh": zh_text,
                "role": role,
                "src": src_list,
                "batch_index": batch_idx
            })

        if not cleaned_items:
            return []

        # Luôn chạy chế độ 1 câu LLM = 1 chunk TTS (Bảo toàn 100% kết quả dịch, chống nuốt chữ)
        # Việc gộp audio cùng nhân vật sẽ được xử lý ở tầng audio sau TTS (merge_continuous_audio_dialogues)
        chunks: List[Dict[str, Any]] = []
        for new_idx, item in enumerate(cleaned_items, start=1):
            item_vi = item["vi"].strip()
            if not re.search(r'[\.\!\?\,\:\;\…\—\-]$', item_vi):
                item["vi"] = f"{item_vi}."
            item["tts_index"] = new_idx
            chunks.append(item)
            if 0 <= item["orig_idx"] < len(input_items):
                input_items[item["orig_idx"]]["tts_index"] = new_idx
        return chunks

    @classmethod
    def build_and_save(
        cls,
        project_id: int,
        db: Session,
        llm_items: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Xây dựng kịch bản TTS, lưu Database tts_dialogue_segments
        và xuất ra thư mục output/projects/project_{id}/tts_voiceover/
        """
        p_dirs = get_project_dirs(project_id)
        tts_dir = p_dirs["tts_voiceover"]
        tts_dir.mkdir(parents=True, exist_ok=True)

        from app.models.dialogue import DialogueSegmentModel
        orig_segs = db.query(DialogueSegmentModel).filter(
            DialogueSegmentModel.task_id == project_id
        ).order_by(DialogueSegmentModel.index.asc()).all()

        # Fallback an toàn: Nếu DB chưa có hoặc bị rỗng, khôi phục từ file raw_clean.json gốc
        if not orig_segs:
            from app.models.project import ProjectTask
            from app.config import settings
            proj = db.query(ProjectTask).filter(ProjectTask.id == project_id).first()
            if proj and proj.video_id:
                clean_json_p = settings.OUTPUT_TRANSCRIPTS_DIR / f"{proj.video_id}_raw_clean.json"
                if clean_json_p.exists():
                    try:
                        with open(clean_json_p, "r", encoding="utf-8") as f:
                            clean_data = json.load(f)
                        for s in clean_data:
                            db.add(DialogueSegmentModel(
                                task_id=project_id,
                                index=s["id"],
                                start_time=s["start"],
                                end_time=s["end"],
                                duration=s["duration"],
                                original_text=s.get("text", ""),
                                clean_text=s.get("clean_text") or s.get("text", ""),
                                confidence=s.get("confidence", 1.0),
                                status="RAW"
                            ))
                        db.commit()
                        orig_segs = db.query(DialogueSegmentModel).filter(
                            DialogueSegmentModel.task_id == project_id
                        ).order_by(DialogueSegmentModel.index.asc()).all()
                        logger.info(f"✔ [Fallback ASR] Đã khôi phục {len(orig_segs)} câu ASR gốc từ {clean_json_p.name} vào CSDL.")
                    except Exception as e:
                        logger.warning(f"Lỗi nạp fallback raw_clean.json: {e}")

        orig_by_idx = {s.index: s for s in orig_segs}
        orig_starts = {round(float(s.start_time or 0.0), 2): s.index for s in orig_segs}
        orig_ends = {round(float(s.end_time or 0.0), 2): s.index for s in orig_segs}
        orig_zh_map = {s.index: (s.clean_text or s.original_text or "").strip() for s in orig_segs}

        # 0. Đồng bộ mốc thời gian chuẩn xác từ ASR gốc cho llm_items trước khi gộp chunk
        for item in llm_items:
            src = item.get("src", [])
            if isinstance(src, list):
                if len(src) == 2 and isinstance(src[0], int) and isinstance(src[1], int) and src[1] > src[0]:
                    src = list(range(src[0], src[1] + 1))
                    item["src"] = src
                if src:
                    s_id = src[0]
                    e_id = src[-1]
                    if s_id in orig_by_idx and e_id in orig_by_idx:
                        item["s"] = round(float(orig_by_idx[s_id].start_time or 0.0), 2)
                        item["e"] = round(float(orig_by_idx[e_id].end_time or item["s"] + 1.0), 2)
            if not item.get("src"):
                raw_s = round(float(item.get("s", 0.0)), 2)
                raw_e = round(float(item.get("e", raw_s + 1.0)), 2)
                s_from_map = orig_starts.get(raw_s)
                e_from_map = orig_ends.get(raw_e)
                if s_from_map and e_from_map and s_from_map <= e_from_map:
                    item["src"] = list(range(s_from_map, e_from_map + 1))
                    item["s"] = round(float(orig_by_idx[s_from_map].start_time or 0.0), 2)
                    item["e"] = round(float(orig_by_idx[e_from_map].end_time or item["s"] + 1.0), 2)

        # 1. Gộp các chunk liên tục
        merged_chunks = cls.merge_continuous_chunks(llm_items)

        # 2. Xóa các dòng TTS cũ của project này trong DB để cập nhật chuẩn mới
        # Dọn sạch khỏi Identity Map TRƯỚC KHI delete để tránh StaleDataError khi commit
        for obj in list(db.identity_map.values()):
            if isinstance(obj, TTSDialogueSegmentModel):
                db.expunge(obj)

        db.query(TTSDialogueSegmentModel).filter(
            TTSDialogueSegmentModel.task_id == project_id
        ).delete(synchronize_session=False)
        db.commit()

        tts_entries_db = []
        json_dialogues = []
        srt_lines = []
        txt_lines = []

        prev_chunk_end = 0.0
        for new_idx, chunk in enumerate(merged_chunks, start=1):
            src_list = chunk.get("src", [])
            if len(src_list) == 2 and isinstance(src_list[0], int) and isinstance(src_list[1], int) and src_list[1] > src_list[0]:
                src_list = list(range(src_list[0], src_list[1] + 1))
                chunk["src"] = src_list

            src_start = src_list[0] if src_list else new_idx
            src_end = src_list[-1] if src_list else new_idx
            src_str = ",".join(str(x) for x in src_list) if src_list else str(new_idx)

            # Khóa chặt mốc thời gian theo ASR gốc
            if src_start in orig_by_idx and src_end in orig_by_idx:
                s_time = round(float(orig_by_idx[src_start].start_time or 0.0), 2)
                e_time = round(float(orig_by_idx[src_end].end_time or s_time + 1.0), 2)
                orig_cnt = max(1, src_end - src_start + 1)
            else:
                raw_s = round(float(chunk.get("s", 0.0)), 2)
                raw_e = round(float(chunk.get("e", raw_s + 1.0)), 2)
                s_from_map = orig_starts.get(raw_s)
                e_from_map = orig_ends.get(raw_e)
                if s_from_map and e_from_map and s_from_map <= e_from_map:
                    s_time = round(float(orig_by_idx[s_from_map].start_time or 0.0), 2)
                    e_time = round(float(orig_by_idx[e_from_map].end_time or s_time + 1.0), 2)
                    orig_cnt = max(1, e_from_map - s_from_map + 1)
                else:
                    s_time = raw_s
                    e_time = raw_e
                    orig_cnt = len(src_list) if src_list else 1

            # Bảo đảm tính đơn điệu không nhảy lùi, nhưng tôn trọng tối đa mốc ASR thực tế
            if s_time < prev_chunk_end:
                s_time = prev_chunk_end
            e_time = max(e_time, round(s_time + 0.1, 2))
            prev_chunk_end = e_time

            dur = max(0.1, round(e_time - s_time, 2))
            zh_text = (chunk.get("zh") or "").strip()
            if not zh_text and src_list:
                zh_text = " ".join([orig_zh_map[idx] for idx in src_list if idx in orig_zh_map and orig_zh_map[idx]]).strip()
            vi_text = chunk["vi"]
            from app.services.step_03_translation.dichhan.raw_text_cleaner import contains_chinese, CHINESE_CHAR_REGEX
            if contains_chinese(vi_text):
                vi_text = CHINESE_CHAR_REGEX.sub('', vi_text).strip()
            role = chunk["role"]

            # Lưu vào Database Model
            db_model = TTSDialogueSegmentModel(
                task_id=project_id,
                tts_index=new_idx,
                batch_index=chunk.get("batch_index", 1),
                src_start_index=src_start,
                src_end_index=src_end,
                src_indices=src_str,
                orig_count=orig_cnt,
                start_time=s_time,
                end_time=e_time,
                duration=dur,
                original_text=zh_text,
                translated_text=vi_text,
                speaker=role,
                status="PENDING"
            )
            db.add(db_model)
            tts_entries_db.append(db_model)

            # Cập nhật liên kết tts_index cho các câu con trong DialogueSegmentModel
            for src_idx in src_list:
                db.query(DialogueSegmentModel).filter(
                    DialogueSegmentModel.task_id == project_id,
                    DialogueSegmentModel.index == src_idx
                ).update({"tts_index": new_idx}, synchronize_session=False)

            # Thu thập dữ liệu file JSON
            json_dialogues.append({
                "tts_index": new_idx,
                "start": s_time,
                "end": e_time,
                "duration": dur,
                "vi": vi_text,
                "zh": zh_text,
                "role": role,
                "src_indices": src_list
            })

            # SRT lines
            srt_lines.append(f"{new_idx}\n{format_timestamp(s_time)} --> {format_timestamp(e_time)}\n{vi_text}\n")
            txt_lines.append(vi_text)

        db.commit()

        # 3. Xuất các file thành phẩm vào thư mục tts_voiceover/
        json_path = tts_dir / "script_tts.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(json_dialogues, f, ensure_ascii=False, indent=2)

        txt_path = tts_dir / "script_tts.txt"
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(txt_lines))

        srt_path = tts_dir / "transcript_tts.srt"
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(srt_lines))

        logger.info(f"✔ [TTSScriptService] Đã xuất kịch bản TTS Project #{project_id}: {len(merged_chunks)} câu -> {tts_dir}")
        return {
            "status": "SUCCESS",
            "total_chunks": len(merged_chunks),
            "tts_dir": str(tts_dir),
            "json_path": str(json_path),
            "txt_path": str(txt_path),
            "srt_path": str(srt_path)
        }
