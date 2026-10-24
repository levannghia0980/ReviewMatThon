# -*- coding: utf-8 -*-
"""
app/services/tts/waveform_sub_syncer.py
======================================
Module chuyên trách đồng bộ mốc thời gian phụ đề Karaoke (DialogueSegmentModel)
theo sóng âm thanh thực tế sau khi tạo giọng đọc TTS.

Nguyên tắc:
- Với câu đơn (1 câu con): Start = start_sec, End = start_sec + seg_dur_sec
- Với câu gộp (nhiều câu con): Căn chỉnh mốc theo sub_timings sóng âm thực tế,
  tính toán qua hệ số gọt khoảng lặng (cut_head_ms) và hệ số co giãn tốc độ (speed).
- Thêm đệm đọc tự nhiên 120ms giữa các câu con liên tiếp.
"""

import logging
from typing import Dict, Any, List
from sqlalchemy.orm import Session
from app.models.dialogue import DialogueSegmentModel, SubtitleSegmentModel

logger = logging.getLogger(__name__)


class WaveformSubSyncer:
    @staticmethod
    def sync_subtitles_to_waveform(
        db: Session,
        project_id: int,
        tts_index: int,
        start_sec: float,
        seg_dur_sec: float,
        actual_speed: float,
        trimmed_dur_sec: float,
        trim_meta: Dict[str, Any],
        chunk_sub_offsets: Dict[int, List[Dict[str, Any]]]
    ) -> None:
        """
        Đồng bộ mốc thời gian của từng mẩu câu con (DialogueSegmentModel)
        với đoạn audio thực tế vừa dán vào timeline.
        """
        try:
            sub_dialogues = db.query(DialogueSegmentModel).filter(
                DialogueSegmentModel.task_id == project_id,
                DialogueSegmentModel.tts_index == tts_index
            ).order_by(DialogueSegmentModel.index.asc()).all()

            if not sub_dialogues:
                return

            n_subs = len(sub_dialogues)
            sub_timings = chunk_sub_offsets.get(tts_index, [])
            cut_h = trim_meta.get("cut_head_ms", 0) if trim_meta.get("trimmed") else 0

            if n_subs == 1:
                sub = sub_dialogues[0]
                # BẢO TỒN NGUYÊN VẸN RAW_ASR: Không ghi đè start_time, end_time, duration gốc!
                sub.actual_duration = round(seg_dur_sec, 2)

                # Lưu / cập nhật vào BẢNG 3: SubtitleSegmentModel cho câu đơn
                s_start = round(start_sec, 2)
                s_end = round(start_sec + seg_dur_sec, 2)
                sub_model = db.query(SubtitleSegmentModel).filter(
                    SubtitleSegmentModel.task_id == project_id,
                    SubtitleSegmentModel.sub_index == sub.index
                ).first()
                if not sub_model:
                    sub_model = SubtitleSegmentModel(
                        task_id=project_id,
                        sub_index=sub.index,
                        tts_index=tts_index,
                        batch_index=getattr(sub, 'batch_index', 1) or 1,
                        start_time=s_start,
                        end_time=s_end,
                        duration=round(max(0.1, s_end - s_start), 2),
                        text_vi=sub.translated_text or "",
                        text_zh=sub.clean_text or sub.original_text or "",
                        speaker=sub.speaker or "NARRATOR"
                    )
                    db.add(sub_model)
                else:
                    sub_model.tts_index = tts_index
                    sub_model.start_time = s_start
                    sub_model.end_time = s_end
                    sub_model.duration = round(max(0.1, s_end - s_start), 2)
                    sub_model.text_vi = sub.translated_text or ""
            elif sub_timings and len(sub_timings) == n_subs:
                # Khớp 100% theo sóng âm thanh thực tế của từng câu con
                speed = max(0.1, actual_speed)
                tot_trimmed_ms = trimmed_dur_sec * 1000.0

                for s_idx, (sub, timing) in enumerate(zip(sub_dialogues, sub_timings)):
                    raw_s_ms = timing["raw_start_ms"]
                    raw_e_ms = timing["raw_end_ms"]

                    # Ánh xạ qua bộ gọt thích ứng (cut_head_ms)
                    t_s_ms = max(0.0, raw_s_ms - cut_h)
                    t_e_ms = max(t_s_ms + 100.0, min(tot_trimmed_ms, raw_e_ms - cut_h))

                    # Ánh xạ qua co giãn tốc độ (speed)
                    offset_start_sec = (t_s_ms / 1000.0) / speed
                    offset_end_sec = (t_e_ms / 1000.0) / speed

                    s_start = round(start_sec + offset_start_sec, 2)
                    s_end = round(start_sec + offset_end_sec, 2)

                    # Đệm đọc tự nhiên 120ms: không bao giờ đè sang câu sau
                    if s_idx < n_subs - 1:
                        next_raw_s = sub_timings[s_idx + 1]["raw_start_ms"]
                        next_t_s = max(0.0, next_raw_s - cut_h)
                        next_s_start = round(start_sec + ((next_t_s / 1000.0) / speed), 2)
                        s_end = min(round(s_end + 0.12, 2), max(round(s_start + 0.3, 2), round(next_s_start - 0.05, 2)))
                    else:
                        s_end = min(round(s_end + 0.12, 2), round(start_sec + seg_dur_sec, 2))

                    s_end = max(round(s_start + 0.3, 2), s_end)
                    # BẢO TỒN NGUYÊN VẸN RAW_ASR: Lưu thời lượng đọc giọng vào actual_duration
                    sub.actual_duration = round(max(0.1, s_end - s_start), 2)

                    # Lưu / cập nhật vào BẢNG 3: SubtitleSegmentModel
                    sub_model = db.query(SubtitleSegmentModel).filter(
                        SubtitleSegmentModel.task_id == project_id,
                        SubtitleSegmentModel.sub_index == sub.index
                    ).first()
                    if not sub_model:
                        sub_model = SubtitleSegmentModel(
                            task_id=project_id,
                            sub_index=sub.index,
                            tts_index=tts_index,
                            batch_index=getattr(sub, 'batch_index', 1) or 1,
                            start_time=s_start,
                            end_time=s_end,
                            duration=round(max(0.1, s_end - s_start), 2),
                            text_vi=sub.translated_text or "",
                            text_zh=sub.clean_text or sub.original_text or "",
                            speaker=sub.speaker or "NARRATOR"
                        )
                        db.add(sub_model)
                    else:
                        sub_model.tts_index = tts_index
                        sub_model.start_time = s_start
                        sub_model.end_time = s_end
                        sub_model.duration = round(max(0.1, s_end - s_start), 2)
                        sub_model.text_vi = sub.translated_text or ""
            else:
                # Fallback: chia đều theo tỉ lệ số từ
                tot_words = sum(max(1, len((s.translated_text or '').split())) for s in sub_dialogues)
                curr_s = start_sec
                for s_idx, sub in enumerate(sub_dialogues):
                    w_cnt = max(1, len((sub.translated_text or '').split()))
                    dur_i = seg_dur_sec * (w_cnt / tot_words)
                    s_start = round(curr_s, 2)
                    s_end = round(start_sec + seg_dur_sec if s_idx == n_subs - 1 else curr_s + dur_i, 2)
                    # BẢO TỒN NGUYÊN VẸN RAW_ASR: Lưu thời lượng đọc vào actual_duration
                    sub.actual_duration = round(max(0.1, s_end - s_start), 2)
                    curr_s = s_end

                    # Lưu / cập nhật vào BẢNG 3: SubtitleSegmentModel
                    sub_model = db.query(SubtitleSegmentModel).filter(
                        SubtitleSegmentModel.task_id == project_id,
                        SubtitleSegmentModel.sub_index == sub.index
                    ).first()
                    if not sub_model:
                        sub_model = SubtitleSegmentModel(
                            task_id=project_id,
                            sub_index=sub.index,
                            tts_index=tts_index,
                            batch_index=getattr(sub, 'batch_index', 1) or 1,
                            start_time=s_start,
                            end_time=s_end,
                            duration=round(max(0.1, s_end - s_start), 2),
                            text_vi=sub.translated_text or "",
                            text_zh=sub.clean_text or sub.original_text or "",
                            speaker=sub.speaker or "NARRATOR"
                        )
                        db.add(sub_model)
                    else:
                        sub_model.tts_index = tts_index
                        sub_model.start_time = s_start
                        sub_model.end_time = s_end
                        sub_model.duration = round(max(0.1, s_end - s_start), 2)
                        sub_model.text_vi = sub.translated_text or ""
        except Exception as e:
            logger.error(f"Lỗi đồng bộ mốc sóng âm sub cho chunk #{tts_index}: {e}")
