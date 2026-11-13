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
from typing import Dict, Any, List, Optional
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
        chunk_sub_offsets: Dict[int, List[Dict[str, Any]]],
        max_allowed_end: Optional[float] = None
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
            if max_allowed_end is not None:
                total_seg_end = min(total_seg_end, round(max_allowed_end, 2))

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

                    # 1. Nhóm sub_timings theo old_tts_index (nếu có) để khóa chặt biên WAV của từng câu gốc,
                    # tránh hiện tượng một câu chia nhiều sub làm lệch toàn bộ các câu sau trong chunk.
                    timings_by_old_idx = {}
                    for item in (sub_timings or []):
                        old_idx = item.get("old_tts_index")
                        if old_idx is not None:
                            timings_by_old_idx.setdefault(str(old_idx), []).append(item)

                    grouped_subs = []
                    all_subs_have_src = bool(timings_by_old_idx)
                    for m in sub_models:
                        src_key = (getattr(m, "source_indices", None) or "").strip()
                        if not src_key or src_key not in timings_by_old_idx:
                            all_subs_have_src = False
                            break
                        if grouped_subs and grouped_subs[-1][0] == src_key:
                            grouped_subs[-1][1].append(m)
                        else:
                            grouped_subs.append((src_key, [m]))

                    def _append_start(val: float) -> None:
                        v = round(val, 2)
                        if calc_starts and v < calc_starts[-1]:
                            v = calc_starts[-1]
                        calc_starts.append(v)

                    if all_subs_have_src and grouped_subs:
                        def _ms_to_timeline_sec(raw_ms: float) -> float:
                            t_ms = max(0.0, float(raw_ms) - cut_h)
                            return min(total_seg_end, start_sec + (t_ms / 1000.0) / speed)

                        for g_i, (src_key, grp_subs) in enumerate(grouped_subs):
                            grp_timings = timings_by_old_idx[src_key]
                            if len(grp_timings) == len(grp_subs):
                                for t_item in grp_timings:
                                    _append_start(_ms_to_timeline_sec(t_item.get("raw_start_ms", 0)))
                            else:
                                grp_start = _ms_to_timeline_sec(grp_timings[0].get("raw_start_ms", 0))
                                grp_raw_end = max(
                                    (float(t.get("raw_end_ms", 0) or 0) for t in grp_timings),
                                    default=0.0,
                                )
                                grp_end = _ms_to_timeline_sec(grp_raw_end) if grp_raw_end > 0 else grp_start
                                if grp_end <= grp_start:
                                    if g_i + 1 < len(grouped_subs):
                                        next_key = grouped_subs[g_i + 1][0]
                                        next_raw_s = timings_by_old_idx[next_key][0].get("raw_start_ms", 0)
                                        grp_end = _ms_to_timeline_sec(next_raw_s)
                                    else:
                                        grp_end = total_seg_end
                                grp_dur = max(0.0, grp_end - grp_start)
                                tot_w = sum(max(1, len((m.text_vi or '').split())) for m in grp_subs)
                                curr_t = grp_start
                                for m in grp_subs:
                                    _append_start(curr_t)
                                    w_cnt = max(1, len((m.text_vi or '').split()))
                                    curr_t += grp_dur * (w_cnt / tot_w)
                    elif sub_timings and len(sub_timings) >= n_subs:
                        # Fallback tương thích ngược khi chưa có source_indices/old_tts_index
                        for s_idx in range(n_subs):
                            raw_s_ms = sub_timings[s_idx].get("raw_start_ms", 0)
                            t_s_ms = max(0.0, raw_s_ms - cut_h)
                            offset_s_sec = (t_s_ms / 1000.0) / speed
                            _append_start(start_sec + offset_s_sec)
                    else:
                        # Fallback: Trải đều theo tỷ lệ số từ
                        tot_words = sum(max(1, len((m.text_vi or '').split())) for m in sub_models)
                        curr_t = start_sec
                        for s_idx, m in enumerate(sub_models):
                            _append_start(curr_t)
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
                            s_end = max(round(s_start + 0.3, 2), min(next_start, total_seg_end))
                        else:
                            s_end = max(round(s_start + 0.4, 2), total_seg_end)


                        if max_allowed_end is not None:
                            s_end = min(s_end, round(max_allowed_end, 2))

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
                        s_end = max(round(s_start + 0.3, 2), min(next_start, total_seg_end))
                    else:
                        s_end = max(round(s_start + 0.4, 2), total_seg_end)



                    if max_allowed_end is not None:
                        s_end = min(s_end, round(max_allowed_end, 2))
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
