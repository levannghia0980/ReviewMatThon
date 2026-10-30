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
            # 1. Ưu tiên kiểm tra các câu phụ đề SubtitleSegmentModel đã được lưu trước từ Bước 3
            sub_models = db.query(SubtitleSegmentModel).filter(
                SubtitleSegmentModel.task_id == project_id,
                SubtitleSegmentModel.tts_index == tts_index
            ).order_by(SubtitleSegmentModel.index.asc()).all()

            sub_timings = chunk_sub_offsets.get(tts_index, [])
            cut_h = trim_meta.get("cut_head_ms", 0) if trim_meta.get("trimmed") else 0
            speed = max(0.1, actual_speed)
            total_seg_end = round(start_sec + seg_dur_sec, 2)

            if sub_models:
                n_subs = len(sub_models)

                # Trường hợp câu đơn (1 câu sub con)
                if n_subs == 1:
                    sub_m = sub_models[0]
                    sub_m.start_time = round(start_sec, 2)
                    sub_m.end_time = total_seg_end
                    sub_m.duration = round(seg_dur_sec, 2)
                else:
                    # Trường hợp đa câu con: Tính mốc cất tiếng của từng câu con
                    calc_starts: List[float] = []

                    if sub_timings and len(sub_timings) >= n_subs:
                        # Khớp 100% theo mốc sóng âm thanh thực tế của từng sub-chunk
                        for s_idx in range(n_subs):
                            raw_s_ms = sub_timings[s_idx].get("raw_start_ms", 0)
                            t_s_ms = max(0.0, raw_s_ms - cut_h)
                            offset_s_sec = (t_s_ms / 1000.0) / speed
                            calc_starts.append(round(start_sec + offset_s_sec, 2))
                    else:
                        # Fallback: Trải đều theo tỷ lệ số từ
                        tot_words = sum(max(1, len((m.text_vi or '').split())) for m in sub_models)
                        curr_t = start_sec
                        for s_idx, m in enumerate(sub_models):
                            calc_starts.append(round(curr_t, 2))
                            w_cnt = max(1, len((m.text_vi or '').split()))
                            curr_t += seg_dur_sec * (w_cnt / tot_words)

                    # Áp dụng cơ chế CONTINUOUS HOLD SUBTITLE:
                    # - Mốc start: Bắt đầu chuẩn xác lúc cất tiếng.
                    # - Mốc end: Kéo dài giữ chữ liên tục đến đúng lúc câu sau nói (không chớp tắt khi câu quá ngắn).
                    # - Câu cuối cùng: Kéo dài đến hết khung thời gian của câu to đã gộp.
                    for s_idx, sub_m in enumerate(sub_models):
                        s_start = calc_starts[s_idx]
                        if s_idx < n_subs - 1:
                            next_start = calc_starts[s_idx + 1]
                            s_end = max(round(s_start + 0.3, 2), next_start)
                        else:
                            s_end = max(round(s_start + 0.4, 2), total_seg_end)

                        sub_m.start_time = s_start
                        sub_m.end_time = s_end
                        sub_m.duration = round(max(0.1, s_end - s_start), 2)

                # Đồng thời cập nhật actual_duration cho các dòng RAW_ASR tương ứng để bảo toàn dữ liệu
                raw_subs = db.query(DialogueSegmentModel).filter(
                    DialogueSegmentModel.task_id == project_id,
                    DialogueSegmentModel.tts_index == tts_index
                ).all()
                for rs in raw_subs:
                    rs.actual_duration = round(seg_dur_sec, 2)
                return

            # 2. Fallback cho dữ liệu cũ (chưa có SubtitleSegmentModel từ Bước 3)
            sub_dialogues = db.query(DialogueSegmentModel).filter(
                DialogueSegmentModel.task_id == project_id,
                DialogueSegmentModel.tts_index == tts_index
            ).order_by(DialogueSegmentModel.index.asc()).all()

            if not sub_dialogues:
                return

            n_subs = len(sub_dialogues)

            if n_subs == 1:
                sub = sub_dialogues[0]
                sub.actual_duration = round(seg_dur_sec, 2)
                s_start = round(start_sec, 2)
                s_end = total_seg_end

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
                calc_starts = []
                if sub_timings and len(sub_timings) >= n_subs:
                    for s_idx in range(n_subs):
                        raw_s_ms = sub_timings[s_idx].get("raw_start_ms", 0)
                        t_s_ms = max(0.0, raw_s_ms - cut_h)
                        offset_s_sec = (t_s_ms / 1000.0) / speed
                        calc_starts.append(round(start_sec + offset_s_sec, 2))
                else:
                    tot_words = sum(max(1, len((s.translated_text or '').split())) for s in sub_dialogues)
                    curr_t = start_sec
                    for sub in sub_dialogues:
                        calc_starts.append(round(curr_t, 2))
                        w_cnt = max(1, len((sub.translated_text or '').split()))
                        curr_t += seg_dur_sec * (w_cnt / tot_words)

                for s_idx, sub in enumerate(sub_dialogues):
                    s_start = calc_starts[s_idx]
                    if s_idx < n_subs - 1:
                        next_start = calc_starts[s_idx + 1]
                        s_end = max(round(s_start + 0.3, 2), next_start)
                    else:
                        s_end = max(round(s_start + 0.4, 2), total_seg_end)

                    sub.actual_duration = round(max(0.1, s_end - s_start), 2)

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
