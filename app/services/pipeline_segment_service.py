# -*- coding: utf-8 -*-
"""
app/services/pipeline_segment_service.py
========================================
Dịch vụ Quản lý Dữ liệu Hợp nhất Pipeline (Pipeline Segment Service)
Quản trị Bảng hợp nhất duy nhất `pipeline_segments` (PipelineSegmentModel):
- Đồng bộ đa pha: RAW_ASR -> LLM_MERGED -> TTS_AUDIO -> SUB_KARAOKE
- Hỗ trợ Backfill tự động từ các bảng cũ sang bảng mới
- Hỗ trợ truy vấn Phả hệ 2 chiều (Lineage: Con <-> Cha <-> Nhóm gộp)
- Báo cáo kiểm toán & Dị thường (Anomaly Detection: co giãn tốc độ > 1.8x, độ trôi timecode)
"""

import logging
from typing import Dict, List, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.dialogue import (
    PipelineSegmentModel,
    DialogueSegmentModel,
    TTSDialogueSegmentModel,
    SubtitleSegmentModel
)

logger = logging.getLogger(__name__)

class PipelineSegmentService:

    @classmethod
    def backfill_project(cls, project_id: int, db: Session) -> Dict[str, int]:
        """
        Đồng bộ dữ liệu của một Project từ các bảng cũ sang bảng hợp nhất `pipeline_segments`.
        Đảm bảo an toàn không xóa đè dữ liệu đang có nếu đã có trong `pipeline_segments`.
        """
        counts = {"RAW_ASR": 0, "TTS_AUDIO": 0, "SUB_KARAOKE": 0}

        # 1. Backfill RAW_ASR từ DialogueSegmentModel
        existing_raw = db.query(PipelineSegmentModel).filter(
            PipelineSegmentModel.task_id == project_id,
            PipelineSegmentModel.stage == "RAW_ASR"
        ).count()

        if existing_raw == 0:
            asr_rows = db.query(DialogueSegmentModel).filter(
                DialogueSegmentModel.task_id == project_id
            ).order_by(DialogueSegmentModel.index.asc()).all()

            for r in asr_rows:
                b_idx = getattr(r, 'batch_index', None) or (((r.index - 1) // 50) + 1)
                seg = PipelineSegmentModel(
                    task_id=project_id,
                    stage="RAW_ASR",
                    index=r.index,
                    batch_index=b_idx,
                    parent_index=r.tts_index,
                    source_indices=str(r.index),
                    sub_count=1,
                    start_time=r.start_time,
                    end_time=r.end_time,
                    duration=r.duration,
                    original_text=r.original_text,
                    translated_text=r.translated_text,
                    reference_text=r.translated_text,  # Bản dịch mẫu CapCut
                    role=r.role,
                    speaker=r.speaker,
                    status=r.status or "TRANSCRIBED",
                    is_locked=getattr(r, 'is_locked', False)
                )
                db.add(seg)
                counts["RAW_ASR"] += 1

        # 2. Backfill TTS_AUDIO từ TTSDialogueSegmentModel
        existing_tts = db.query(PipelineSegmentModel).filter(
            PipelineSegmentModel.task_id == project_id,
            PipelineSegmentModel.stage == "TTS_AUDIO"
        ).count()

        if existing_tts == 0:
            tts_rows = db.query(TTSDialogueSegmentModel).filter(
                TTSDialogueSegmentModel.task_id == project_id
            ).order_by(TTSDialogueSegmentModel.tts_index.asc()).all()

            for t in tts_rows:
                seg = PipelineSegmentModel(
                    task_id=project_id,
                    stage="TTS_AUDIO",
                    index=t.tts_index,
                    batch_index=t.batch_index or 1,
                    source_indices=t.src_indices or str(t.tts_index),
                    sub_count=t.orig_count or 1,
                    start_time=t.start_time,
                    end_time=t.end_time,
                    duration=t.duration,
                    original_text=t.original_text,
                    translated_text=t.translated_text,
                    speaker=t.speaker,
                    voice_code=t.voice_code,
                    audio_path=t.voice_audio_path,
                    actual_duration=t.voice_duration,
                    speed_ratio=t.final_speed_ratio or 1.0,
                    status=t.status or "PENDING",
                    is_anomaly=bool(t.final_speed_ratio and t.final_speed_ratio > 1.8),
                    is_locked=t.is_locked or False
                )
                db.add(seg)
                counts["TTS_AUDIO"] += 1

        # 3. Backfill SUB_KARAOKE từ SubtitleSegmentModel
        existing_sub = db.query(PipelineSegmentModel).filter(
            PipelineSegmentModel.task_id == project_id,
            PipelineSegmentModel.stage == "SUB_KARAOKE"
        ).count()

        if existing_sub == 0:
            sub_rows = db.query(SubtitleSegmentModel).filter(
                SubtitleSegmentModel.task_id == project_id
            ).order_by(SubtitleSegmentModel.sub_index.asc()).all()

            for s in sub_rows:
                seg = PipelineSegmentModel(
                    task_id=project_id,
                    stage="SUB_KARAOKE",
                    index=s.sub_index,
                    batch_index=getattr(s, 'batch_index', 1) or 1,
                    parent_index=s.tts_index,
                    source_indices=str(s.sub_index),
                    sub_count=1,
                    start_time=s.start_time,
                    end_time=s.end_time,
                    duration=s.duration,
                    original_text=getattr(s, 'text_zh', '') or '',
                    translated_text=getattr(s, 'text_vi', '') or '',
                    speaker=s.speaker,
                    status="DUBBED",
                    is_locked=False
                )
                db.add(seg)
                counts["SUB_KARAOKE"] += 1

        if sum(counts.values()) > 0:
            db.commit()
            logger.info(f"[PipelineSegmentService] Đã backfill cho Project #{project_id}: {counts}")

        return counts

    @classmethod
    def get_stage_metrics(cls, project_id: int, db: Session) -> Dict[str, Any]:
        """Lấy số liệu tổng hợp siêu tốc theo từng stage từ bảng hợp nhất."""
        metrics = {}
        for stg in ["RAW_ASR", "LLM_MERGED", "TTS_AUDIO", "SUB_KARAOKE"]:
            cnt = db.query(func.count(PipelineSegmentModel.id)).filter(
                PipelineSegmentModel.task_id == project_id,
                PipelineSegmentModel.stage == stg
            ).scalar() or 0

            anomalies = db.query(func.count(PipelineSegmentModel.id)).filter(
                PipelineSegmentModel.task_id == project_id,
                PipelineSegmentModel.stage == stg,
                PipelineSegmentModel.is_anomaly == True
            ).scalar() or 0

            metrics[stg] = {
                "total": cnt,
                "anomalies": anomalies
            }
        return metrics

    @classmethod
    def get_lineage(cls, project_id: int, stage: str, index: int, db: Session) -> Dict[str, Any]:
        """
        Truy vết phả hệ 2 chiều:
        - Nếu hỏi RAW_ASR câu 5: Trả về câu ASR đó + câu LLM_MERGED / TTS_AUDIO cha chứa nó + câu SUB_KARAOKE con tương ứng.
        - Nếu hỏi TTS_AUDIO câu 2: Trả về toàn bộ danh sách các câu RAW_ASR gốc hợp thành nó + các mẩu SUB_KARAOKE sinh ra từ nó.
        """
        curr = db.query(PipelineSegmentModel).filter(
            PipelineSegmentModel.task_id == project_id,
            PipelineSegmentModel.stage == stage,
            PipelineSegmentModel.index == index
        ).first()

        if not curr:
            return {"found": False, "error": f"Không tìm thấy đoạn stage={stage}, index={index}"}

        parents = []
        children = []

        if stage in ["RAW_ASR", "SUB_KARAOKE"]:
            # Tìm cha: Ưu tiên tìm cha trực tiếp theo thứ tự phân tầng
            if curr.parent_index:
                target_stage = "TTS_AUDIO" if stage == "SUB_KARAOKE" else None
                if target_stage:
                    p = db.query(PipelineSegmentModel).filter(
                        PipelineSegmentModel.task_id == project_id,
                        PipelineSegmentModel.stage == target_stage,
                        PipelineSegmentModel.index == curr.parent_index
                    ).all()
                else:
                    # RAW_ASR: Ưu tiên TTS_AUDIO, nếu không có thì tìm LLM_MERGED (tránh đụng độ index 2 tầng)
                    p = db.query(PipelineSegmentModel).filter(
                        PipelineSegmentModel.task_id == project_id,
                        PipelineSegmentModel.stage == "TTS_AUDIO",
                        PipelineSegmentModel.index == curr.parent_index
                    ).all()
                    if not p:
                        p = db.query(PipelineSegmentModel).filter(
                            PipelineSegmentModel.task_id == project_id,
                            PipelineSegmentModel.stage == "LLM_MERGED",
                            PipelineSegmentModel.index == curr.parent_index
                        ).all()
                parents = [cls._to_dict(x) for x in p]
        elif stage == "TTS_AUDIO":
            # 1. Tìm cha LLM_MERGED (nếu có liên kết)
            if curr.parent_index:
                p = db.query(PipelineSegmentModel).filter(
                    PipelineSegmentModel.task_id == project_id,
                    PipelineSegmentModel.stage == "LLM_MERGED",
                    PipelineSegmentModel.index == curr.parent_index
                ).all()
                parents = [cls._to_dict(x) for x in p]
            # 2. Tìm các con RAW_ASR và SUB_KARAOKE
            c_subs = db.query(PipelineSegmentModel).filter(
                PipelineSegmentModel.task_id == project_id,
                PipelineSegmentModel.stage.in_(["RAW_ASR", "SUB_KARAOKE"]),
                PipelineSegmentModel.parent_index == curr.index
            ).all()
            children = [cls._to_dict(x) for x in c_subs]
        elif stage == "LLM_MERGED":
            # Tìm con TTS_AUDIO hoặc con RAW_ASR
            c_subs = db.query(PipelineSegmentModel).filter(
                PipelineSegmentModel.task_id == project_id,
                PipelineSegmentModel.stage.in_(["TTS_AUDIO", "RAW_ASR"]),
                PipelineSegmentModel.parent_index == curr.index
            ).all()
            children = [cls._to_dict(x) for x in c_subs]

        return {
            "found": True,
            "target": cls._to_dict(curr),
            "parents": parents,
            "children": children
        }

    @staticmethod
    def _to_dict(m: PipelineSegmentModel) -> Dict[str, Any]:
        return {
            "id": m.id,
            "stage": m.stage,
            "index": m.index,
            "batch_index": m.batch_index,
            "parent_index": m.parent_index,
            "source_indices": m.source_indices,
            "start_time": m.start_time,
            "end_time": m.end_time,
            "duration": m.duration,
            "original_text": m.original_text,
            "translated_text": m.translated_text,
            "reference_text": m.reference_text,
            "speaker": m.speaker,
            "speed_ratio": m.speed_ratio,
            "status": m.status,
            "is_anomaly": m.is_anomaly
        }
