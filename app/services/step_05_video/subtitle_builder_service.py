# -*- coding: utf-8 -*-
"""
app/services/video/subtitle_builder_service.py
==================================================
Dịch vụ Phụ đề Video & Karaoke (Video Subtitle Builder Service)
Chuyên trách độc lập 100%:
- Nhận danh sách 100% mẩu ASR CapCut gốc (ví dụ 183 câu) và danh sách câu dịch LLM.
- Phân rã câu thông minh theo dấu câu tự nhiên (Punctuation-Aware De-merging):
    + Tách câu lớn theo dấu chấm, dấu than, dấu hỏi.
    + Căn chỉnh từng câu vào nhóm mẩu ASR con tương ứng.
    + Trong từng câu: tách vế theo dấu phẩy, bảo toàn cụm từ ngữ nghĩa,
      tuyệt đối không chèn dấu chấm bừa bãi vào giữa từ ghép ("nhân loại", "cấm địa").
- Cập nhật Database bảng dialogue_segments (BẢO TOÀN 100% SỐ DÒNG GỐC, KHÔNG BAO GIỜ XÓA).
- Gán liên kết tts_index trỏ về câu TTS tương ứng.
- Xuất thành phẩm vào thư mục: output/projects/project_{id}/video_subtitles/
    + subtitles_karaoke.json
    + subtitles_karaoke.srt
    + subtitles_karaoke.ass
"""

import os
import re
import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session

from app.models.dialogue import DialogueSegmentModel
from app.schemas.transcript import DialogueSegment
from app.config import get_project_dirs
from app.services.step_05_video.karaoke_subtitle_service import KaraokeSubtitleService

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

# Danh sách từ nối / liên từ tự nhiên để căn chỉnh điểm cắt từ không bị gãy từ ghép
NATURAL_SPLIT_HEADS = {
    "trong", "khi", "và", "đã", "để", "mà", "thì", "ở", "tại", "với", "từ",
    "nhưng", "bởi", "vì", "do", "nên", "nếu", "sẽ", "đang", "vừa", "cũng",
    "liền", "bèn", "lại", "rồi", "qua", "về"
}


class SubtitleBuilderService:
    @staticmethod
    def find_best_split_index(words: List[str], target_idx: int) -> int:
        """
        Tìm vị trí cắt tự nhiên nhất trong phạm vi [-2, +2] từ:
        Ưu tiên đứng trước từ nối/giới từ hoặc sau từ có dấu phẩy.
        """
        n = len(words)
        if target_idx <= 1:
            return 1
        if target_idx >= n - 1:
            return n - 1

        best_idx = target_idx
        best_score = -100

        for offset in [0, 1, -1, 2, -2]:
            cand = target_idx + offset
            if 1 <= cand <= n - 1:
                score = -abs(offset) * 2
                next_word = words[cand].lower().rstrip(",.;!?")
                prev_word = words[cand - 1]

                if next_word in NATURAL_SPLIT_HEADS:
                    score += 15
                if prev_word.endswith((",", ";", "，", "；")):
                    score += 20

                if score > best_score:
                    best_score = score
                    best_idx = cand

        return best_idx

    @classmethod
    def demerge_sentence_aware(
        cls,
        matched_segs: List[DialogueSegment],
        vi_text: str,
        role: str,
        tts_idx: int
    ) -> List[Dict[str, Any]]:
        """
        Phân rã câu dịch tiếng Việt vào nhóm mẩu ASR con tương ứng.
        Bảo toàn 100% số lượng câu, dấu câu tự nhiên và mốc thời gian.
        """
        n_segs = len(matched_segs)
        if not matched_segs:
            return []

        # Chuẩn hóa timecode: câu trước không đè qua start_time của câu kế tiếp (khắc phục lỗi phình end_time do gộp cũ)
        seg_times = []
        for k, s in enumerate(matched_segs):
            s_s = round(float(getattr(s, 'start', 0.0) or getattr(s, 'start_time', 0.0) or 0.0), 2)
            if k < n_segs - 1:
                next_s = round(float(getattr(matched_segs[k+1], 'start', 0.0) or getattr(matched_segs[k+1], 'start_time', 0.0) or s_s + 1.0), 2)
                raw_e = round(float(getattr(s, 'end', 0.0) or getattr(s, 'end_time', 0.0) or s_s + 1.0), 2)
                s_e = min(raw_e, next_s) if raw_e > s_s else next_s
                if s_e <= s_s:
                    s_e = next_s
            else:
                s_e = round(float(getattr(s, 'end', 0.0) or getattr(s, 'end_time', 0.0) or s_s + 1.0), 2)
                if s_e <= s_s:
                    s_e = round(s_s + 1.5, 2)
            seg_times.append((s_s, s_e))

        if n_segs == 1:
            s = matched_segs[0]
            s_start, s_end = seg_times[0]
            return [{
                "id": getattr(s, 'index', getattr(s, 'id', 1)),
                "start": s_start,
                "end": s_end,
                "duration": round(max(0.1, s_end - s_start), 2),
                "zh": getattr(s, 'clean_text', '') or getattr(s, 'text', '') or getattr(s, 'original_text', ''),
                "vi": vi_text.strip(),
                "role": role,
                "tts_index": tts_idx
            }]

        # 1. Tách vi_text thành các mẩu dấu câu tự nhiên (Punctuation-First Splitting)
        # Level 1: Kết thúc câu (. ! ? … ...) và Dấu hai chấm (: ：)
        # Dấu hai chấm (:) có độ tin cậy và ưu tiên ngắt tương đương 100% như dấu kết câu
        # (Không ngắt khi nằm giữa các chữ số như 10:23 hay 3.14)
        # Level 2: Dấu phẩy (,), chấm phẩy (;), gạch ngang (—, –)
        pattern = re.compile(
            r'(?P<l1>(?:\.{3,}|…|[!?]+|(?<!\d)\.(?!\d)|(?<!\d)[:：]|[:：](?!\d)|\.(?=\s|$)|[!?…:：](?=\s|$)))'
            r'|(?P<l2>(?:(?<!\d)[,，;；](?!\d)|[,，;；](?=\s|$)|[—–]))'
        )

        chunks = []
        last_pos = 0
        for m in pattern.finditer(vi_text):
            end_pos = m.end()
            chunk = vi_text[last_pos:end_pos].strip()
            lvl = 1 if m.group('l1') else 2
            if chunk:
                chunks.append({'text': chunk, 'lvl': lvl})
            last_pos = end_pos
        rem = vi_text[last_pos:].strip()
        if rem:
            chunks.append({'text': rem, 'lvl': 0})

        if not chunks:
            chunks = [{'text': vi_text.strip(), 'lvl': 1}]

        # 2. Phân bổ các mẩu dấu câu vào các segment:
        m_chunks = len(chunks)
        if m_chunks <= n_segs:
            # Nếu số mẩu dấu câu <= số segment: Tuyệt đối KHÔNG chẻ đôi từ ngữ.
            # Gán lần lượt từng mẩu câu tự nhiên vào các segment tương ứng, các segment còn lại để trống ""
            res_texts = []
            for j in range(n_segs):
                if j < m_chunks:
                    res_texts.append(chunks[j]['text'])
                else:
                    res_texts.append('')
            res = []
            for sub_idx, (s, piece) in enumerate(zip(matched_segs, res_texts)):
                s_start, s_end = seg_times[sub_idx]
                clean_piece = re.sub(r'[\,\.]+\,', ',', piece)
                clean_piece = re.sub(r'[\,\.]+\.', '.', clean_piece)
                res.append({
                    "id": getattr(s, 'index', getattr(s, 'id', sub_idx + 1)),
                    "start": s_start,
                    "end": s_end,
                    "duration": round(max(0.1, s_end - s_start), 2),
                    "zh": getattr(s, 'clean_text', '') or getattr(s, 'text', '') or getattr(s, 'original_text', ''),
                    "vi": clean_piece,
                    "role": role,
                    "tts_index": tts_idx
                })
            return res

        seg_durs = [max(0.1, round(e - s, 2)) for s, e in seg_times]
        tot_dur = max(0.1, sum(seg_durs))
        tot_words = sum(max(1, len(c['text'].split())) for c in chunks)

        # 2. Quy hoạch động (DP) phân bổ tối ưu m mẩu câu vào n_segs segment:
        # - Tuyệt đối cấm gộp vượt qua dấu kết câu hoặc dấu hai chấm (Level 1: phạt nặng 1000)
        # - Cho phép gộp các vế dấu phẩy trong cùng câu (Level 2: phạt nhẹ 2.0)
        # - Khớp thời lượng nói tỷ lệ thuận với số từ của từng segment
        def group_penalty(k, i):
            pen = 0.0
            for idx in range(k, i - 1):
                if chunks[idx]['lvl'] == 1:
                    pen += 1000.0
                elif chunks[idx]['lvl'] == 2:
                    pen += 2.0
                else:
                    pen += 20.0
            return pen

        dp = {(0, 0): (0.0, -1)}
        for j in range(1, n_segs + 1):
            target_dur = seg_durs[j - 1]
            for i in range(j, m_chunks - (n_segs - j) + 1):
                best_cost = float('inf')
                best_k = -1
                for k in range(j - 1, i):
                    if (k, j - 1) not in dp:
                        continue
                    prev_cost = dp[(k, j - 1)][0]
                    g_words = sum(max(1, len(chunks[idx]['text'].split())) for idx in range(k, i))
                    expected_words = (target_dur / tot_dur) * tot_words
                    fit_err = ((g_words - expected_words) / max(1.0, expected_words)) ** 2
                    pen = group_penalty(k, i)
                    total_step_cost = prev_cost + fit_err * 10.0 + pen
                    if total_step_cost < best_cost:
                        best_cost = total_step_cost
                        best_k = k
                if best_k != -1:
                    dp[(i, j)] = (best_cost, best_k)

        curr_i = m_chunks
        res_texts = []
        for j in range(n_segs, 0, -1):
            k = dp.get((curr_i, j), (0, curr_i - 1))[1]
            if k == -1 or k >= curr_i:
                k = curr_i - 1
            group_text = ' '.join(chunks[idx]['text'] for idx in range(k, curr_i)).strip()
            res_texts.append(group_text)
            curr_i = k
        res_texts.reverse()

        res = []
        for sub_idx, (s, piece) in enumerate(zip(matched_segs, res_texts)):
            s_start, s_end = seg_times[sub_idx]
            clean_piece = re.sub(r'[\,\.]+\,', ',', piece)
            clean_piece = re.sub(r'[\,\.]+\.', '.', clean_piece)

            res.append({
                "id": getattr(s, 'index', getattr(s, 'id', sub_idx + 1)),
                "start": s_start,
                "end": s_end,
                "duration": round(max(0.1, s_end - s_start), 2),
                "zh": getattr(s, 'clean_text', '') or getattr(s, 'text', '') or getattr(s, 'original_text', ''),
                "vi": clean_piece,
                "role": role,
                "tts_index": tts_idx
            })

        return res

    @classmethod
    def split_into_natural_clauses(cls, text: str, max_words: int = 14) -> List[str]:
        """
        Tách câu dịch tiếng Việt thành các vế câu tự nhiên dựa theo dấu câu:
        - Dấu kết câu: . ! ? …
        - Dấu ngắt vế / liên kết: , ; : — –
        Bảo toàn 100% nội dung chữ, không nuốt chữ, không làm mất dấu câu,
        TUYỆT ĐỐI không chẻ đôi động từ ghép (bước vào, đi ra) và không tự ý chèn dấu phẩy bậy bạ.
        """
        text = text.strip()
        if not text:
            return []

        # Tách vế CHUẨN XÁC theo dấu câu tự nhiên: . ! ? … : ; , — –
        pattern = re.compile(r'[^,;:.!?…—–]+(?:[,;:.!?…—–]+|$)', re.UNICODE)
        raw_pieces = [p.strip() for p in pattern.findall(text) if p.strip()]
        if not raw_pieces:
            raw_pieces = [text]

        return raw_pieces

    @classmethod
    def build_from_translated_items(
        cls,
        project_id: int,
        db: Session,
        llm_items: List[Dict[str, Any]],
        video_title: str = "Video Review"
    ) -> Dict[str, Any]:
        """
        Tạo phụ đề chuẩn xác dựa trực tiếp trên các câu dịch của Gemini/LLM:
        - Tách câu con tự nhiên dựa theo dấu câu (. ! ? , : ; ...)
        - Phân bổ mốc thời gian start / end tỷ lệ thuận theo số từ
        - Cập nhật trực tiếp vào bảng dialogue_segments trong SQLite
        - Xuất trọn bộ 3 file: subtitles_karaoke.json, subtitles_karaoke.srt, subtitles_karaoke.ass
        - Đồng bộ file .ass ra thư mục root và transcripts
        """
        import copy
        p_dirs = get_project_dirs(project_id)
        sub_dir = p_dirs["video_subtitles"]
        sub_dir.mkdir(parents=True, exist_ok=True)

        if not llm_items:
            logger.warning(f"Project #{project_id} không có câu dịch LLM nào!")
            srt_path = sub_dir / "subtitles_karaoke.srt"
            json_path = sub_dir / "subtitles_karaoke.json"
            ass_path = sub_dir / "subtitles_karaoke.ass"
            return {"status": "EMPTY", "total_subtitles": 0, "srt_path": str(srt_path), "json_path": str(json_path), "ass_path": str(ass_path)}

        items_copy = copy.deepcopy(llm_items)
        all_reconstructed: List[Dict[str, Any]] = []
        sub_id = 0

        # Lấy bản đồ tiếng Trung nếu có từ CSDL
        db_orig = db.query(DialogueSegmentModel).filter(DialogueSegmentModel.task_id == project_id).all()
        orig_zh_map = {d.index: (d.clean_text or d.original_text or "").strip() for d in db_orig}

        for item_idx, item in enumerate(items_copy, start=1):
            vi_full = str(item.get("vi") or item.get("viet") or item.get("text") or "").strip()
            if not vi_full:
                continue

            from app.services.step_03_translation.dichhan.raw_text_cleaner import contains_chinese, CHINESE_CHAR_REGEX
            if contains_chinese(vi_full):
                vi_full = CHINESE_CHAR_REGEX.sub('', vi_full).strip()

            role = item.get("role") or item.get("speaker") or "NARRATOR"
            tts_idx = item.get("tts_index", item_idx)

            s_time = float(item.get("s", item.get("start", 0.0)))
            e_time = float(item.get("e", item.get("end", s_time + 1.0)))
            if e_time <= s_time:
                e_time = round(s_time + 1.5, 2)
            dur = max(0.1, round(e_time - s_time, 2))

            zh_orig = str(item.get("zh") or "").strip()
            src_list = item.get("src", [])
            if not zh_orig and isinstance(src_list, list) and src_list:
                zh_orig = " ".join([orig_zh_map[idx] for idx in src_list if idx in orig_zh_map and orig_zh_map[idx]]).strip()

            clauses = cls.split_into_natural_clauses(vi_full)
            if not clauses:
                clauses = [vi_full]

            tot_words = sum(max(1, len(c.split())) for c in clauses)
            curr_s = s_time

            for c_idx, c_text in enumerate(clauses):
                sub_id += 1
                w_cnt = max(1, len(c_text.split()))
                if c_idx == len(clauses) - 1:
                    c_e = e_time
                else:
                    c_dur = dur * (w_cnt / tot_words)
                    c_e = round(curr_s + c_dur, 2)

                c_s = round(curr_s, 2)
                if c_e <= c_s:
                    c_e = round(c_s + 0.5, 2)

                all_reconstructed.append({
                    "id": sub_id,
                    "start": c_s,
                    "end": c_e,
                    "duration": round(max(0.1, c_e - c_s), 2),
                    "zh": zh_orig,
                    "vi": c_text,
                    "role": role,
                    "tts_index": tts_idx
                })
                curr_s = c_e

        # 2. BẢO TOÀN 100% CSDL GỐC: dialogue_segments lưu trữ mẩu ASR gốc (không bao giờ xóa hay ghi đè)
        # Các vế câu phụ đề con đã được xuất trọn vẹn vào subtitles_karaoke.json / .srt / .ass bên dưới
        logger.info(f"✔ Đã tái tạo {len(all_reconstructed)} mẩu phụ đề Karaoke tự nhiên chuẩn dấu câu.")

        # 3. Xuất file JSON, SRT, ASS vào thư mục video_subtitles/
        json_path = sub_dir / "subtitles_karaoke.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(all_reconstructed, f, ensure_ascii=False, indent=2)

        srt_lines = []
        dialogue_segments_for_ass = []
        for s in all_reconstructed:
            srt_lines.append(f"{s['id']}\n{format_timestamp(s['start'])} --> {format_timestamp(s['end'])}\n{s['vi']}\n")
            dialogue_segments_for_ass.append(DialogueSegment(
                id=s["id"],
                start=s["start"],
                end=s["end"],
                duration=s["duration"],
                text=s["zh"],
                clean_text=s["zh"],
                translated_text=s["vi"],
                role=s["role"],
                speaker=s["role"]
            ))

        srt_path = sub_dir / "subtitles_karaoke.srt"
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(srt_lines))

        ass_path = sub_dir / "subtitles_karaoke.ass"
        KaraokeSubtitleService.create_karaoke_ass_file(
            segments=dialogue_segments_for_ass,
            output_ass_path=str(ass_path),
            video_title=video_title
        )

        # Đồng bộ file ASS và tương thích ngược
        try:
            import shutil
            from app.models import ProjectTask
            from app.config import settings
            proj_obj = db.query(ProjectTask).filter(ProjectTask.id == project_id).first()
            if proj_obj and proj_obj.video_id:
                root_ass = p_dirs["root"] / f"{proj_obj.video_id}_karaoke.ass"
                transcripts_ass = settings.OUTPUT_TRANSCRIPTS_DIR / f"{proj_obj.video_id}_karaoke.ass"
                shutil.copyfile(ass_path, root_ass)
                shutil.copyfile(ass_path, transcripts_ass)
            shutil.copyfile(json_path, p_dirs["root"] / "transcript_vi.json")
            shutil.copyfile(srt_path, p_dirs["root"] / "transcript_vi.srt")
        except Exception as e:
            logger.warning(f"Lỗi đồng bộ file phụ đề sang root/transcripts: {e}")

        logger.info(f"✔ [SubtitleBuilderService] Đã tạo xong phụ đề Video Project #{project_id}: {len(all_reconstructed)} câu -> {sub_dir}")

        return {
            "status": "SUCCESS",
            "total_subtitles": len(all_reconstructed),
            "sub_dir": str(sub_dir),
            "json_path": str(json_path),
            "srt_path": str(srt_path),
            "ass_path": str(ass_path)
        }

    @classmethod
    def build_and_save(
        cls,
        project_id: int,
        db: Session,
        original_asr_segments: List[Any],
        llm_items: List[Dict[str, Any]],
        video_title: str = "Video Review"
    ) -> Dict[str, Any]:
        """
        Tương thích ngược: Tự động chuyển giao sang quy trình phân rã câu tự nhiên chuẩn Gemini.
        """
        return cls.build_from_translated_items(
            project_id=project_id,
            db=db,
            llm_items=llm_items,
            video_title=video_title
        )

