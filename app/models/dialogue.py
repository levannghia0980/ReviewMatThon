# -*- coding: utf-8 -*-
"""
app/models/dialogue.py
======================
BẢNG HỢP NHẤT DUY NHẤT (UNIFIED PIPELINE SEGMENT MODEL)
Quản trị toàn bộ các mẩu câu xuyên suốt 4 pha trong vòng đời Pipeline:
1. RAW_ASR    : Câu nhận diện âm thanh gốc (CapCut/Whisper), lưu vĩnh viễn bản dịch tham chiếu CapCut (reference_text).
2. LLM_MERGED : Câu kịch bản trau chuốt do AI LLM gộp và dịch thuật.
3. TTS_AUDIO  : Câu lồng tiếng TTS hoàn chỉnh (lưu actual_duration, speed_ratio, audio_path, is_anomaly).
4. SUB_KARAOKE: Phụ đề hiển thị màn hình Karaoke khớp chính xác theo sóng âm.
"""

from typing import Optional, List
from sqlalchemy import Column, Integer, String, Float, Text, Boolean, ForeignKey, UniqueConstraint, Index
from sqlalchemy.orm import relationship, synonym
from app.db.session import Base


class PipelineSegmentModel(Base):
    """
    BẢNG DUY NHẤT THỐNG NHẤT (UNIFIED PIPELINE SEGMENTS)
    Thay thế hoàn toàn 3 bảng rời rạc cũ (dialogue_segments, tts_dialogue_segments, video_subtitle_segments).
    Hỗ trợ Single Table Inheritance để các lớp DialogueSegmentModel, TTSDialogueSegmentModel,
    SubtitleSegmentModel tự động ánh xạ theo stage tương ứng.
    """
    __tablename__ = "pipeline_segments"
    __table_args__ = (
        UniqueConstraint("task_id", "stage", "index", name="uq_task_stage_index"),
        Index("idx_pipe_task_stage_idx", "task_id", "stage", "index"),
        Index("idx_pipe_task_stage_batch", "task_id", "stage", "batch_index"),
        Index("idx_pipe_task_parent", "task_id", "parent_index"),
        Index("idx_pipe_task_anomaly", "task_id", "is_anomaly"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("project_tasks.id", ondelete="CASCADE"), nullable=False, index=True)

    # 1. PHÂN CẤP TẦNG DỮ LIỆU (STAGE)
    # RAW_ASR | LLM_MERGED | TTS_AUDIO | SUB_KARAOKE
    stage = Column(String(30), nullable=False, index=True)

    # 2. SỐ THỨ TỰ & PHÂN LÔ
    index = Column(Integer, nullable=False, index=True)
    batch_index = Column(Integer, default=1, index=True)

    # 3. QUAN HỆ HỒI QUY (LINEAGE / TRACEABILITY)
    parent_index   = Column(Integer, nullable=True, index=True)   # Trỏ về câu TTS_AUDIO cha
    source_indices = Column(String(500), nullable=True)          # Chuỗi các câu con (vd: "1,2,3")
    sub_count      = Column(Integer, default=1)                  # Số lượng mẩu con gộp lại

    # 4. TIMECODE VẬT LÝ & ÂM THANH
    start_time = Column(Float, nullable=False)
    end_time   = Column(Float, nullable=False)
    duration   = Column(Float, nullable=False)

    # 5. NỘI DUNG VĂN BẢN
    original_text   = Column(Text, nullable=False)
    translated_text = Column(Text, nullable=True)
    reference_text  = Column(Text, nullable=True)  # Bản dịch tham chiếu CapCut bất biến

    # 6. NHÂN VẬT & GIỌNG ĐỌC
    role       = Column(String(100), default="NARRATOR")
    speaker    = Column(String(100), default="NARRATOR")
    voice_code = Column(String(100), nullable=True)

    # 7. THÔNG SỐ ÂM THANH THỰC TẾ
    audio_path      = Column(String(500), nullable=True)
    actual_duration = Column(Float, default=0.0)
    speed_ratio     = Column(Float, default=1.0)

    # 8. TRẠNG THÁI & CẢNH BÁO BẤT THƯỜNG
    confidence = Column(Float, default=0.98)
    status     = Column(String(30), default="PENDING")
    is_anomaly = Column(Boolean, default=False, index=True)
    is_locked  = Column(Boolean, default=False)

    # 9. THÔNG SỐ VAY MƯỢN TIMELINE & TTS (TƯƠNG THÍCH 100% NHÁNH TEST1)
    src_start_index        = Column(Integer, nullable=True)
    src_end_index          = Column(Integer, nullable=True)
    borrowed_start_time    = Column(Float, nullable=True)
    borrowed_end_time      = Column(Float, nullable=True)
    borrowed_duration      = Column(Float, nullable=True)
    borrow_prev_sec        = Column(Float, default=0.0)
    borrow_next_sec        = Column(Float, default=0.0)
    raw_audio_duration     = Column(Float, default=0.0)
    trimmed_audio_duration = Column(Float, default=0.0)
    cut_head_ms            = Column(Integer, default=0)
    cut_tail_ms            = Column(Integer, default=0)
    initial_ratio          = Column(Float, default=1.0)

    project = relationship("ProjectTask", back_populates="pipeline_segments")

    __mapper_args__ = {
        "polymorphic_on": stage,
        "polymorphic_identity": "PIPELINE_SEGMENT",
    }

    # =========================================================================
    # ALIASES & SYNONYMS TẦNG ORM (TƯƠNG THÍCH SQL QUERY CHO TEST1)
    # Cho phép gọi .asc(), .desc(), .filter() hoặc đọc/ghi bình thường
    # =========================================================================
    text_vi            = synonym("translated_text")
    text_zh            = synonym("original_text")
    clean_text         = synonym("original_text")
    final_speed_ratio  = synonym("speed_ratio")
    voice_duration     = synonym("actual_duration")
    voice_audio_path   = synonym("audio_path")
    src_indices        = synonym("source_indices")
    orig_count         = synonym("sub_count")
    parent_tts_index   = synonym("parent_index")
    tts_index          = synonym("index")
    sub_index          = synonym("index")

    asr_start          = synonym("start_time")
    asr_end            = synonym("end_time")
    asr_duration       = synonym("duration")
    borrowed_start     = synonym("borrowed_start_time")
    borrowed_end       = synonym("borrowed_end_time")

    def __init__(self, *args, **kwargs):
        if "tts_index" in kwargs and "index" not in kwargs:
            kwargs["index"] = kwargs.pop("tts_index")
        if "sub_index" in kwargs and "index" not in kwargs:
            kwargs["index"] = kwargs.pop("sub_index")
        if "text_vi" in kwargs and "translated_text" not in kwargs:
            kwargs["translated_text"] = kwargs.pop("text_vi")
        if "text_zh" in kwargs and "original_text" not in kwargs:
            kwargs["original_text"] = kwargs.pop("text_zh")
        if "asr_start" in kwargs and "start_time" not in kwargs:
            kwargs["start_time"] = kwargs.pop("asr_start")
        if "asr_end" in kwargs and "end_time" not in kwargs:
            kwargs["end_time"] = kwargs.pop("asr_end")
        if "asr_duration" in kwargs and "duration" not in kwargs:
            kwargs["duration"] = kwargs.pop("asr_duration")
        if "borrowed_start" in kwargs and "borrowed_start_time" not in kwargs:
            kwargs["borrowed_start_time"] = kwargs.pop("borrowed_start")
        if "borrowed_end" in kwargs and "borrowed_end_time" not in kwargs:
            kwargs["borrowed_end_time"] = kwargs.pop("borrowed_end")
        if "final_speed_ratio" in kwargs and "speed_ratio" not in kwargs:
            kwargs["speed_ratio"] = kwargs.pop("final_speed_ratio")
        if "src_indices" in kwargs and "source_indices" not in kwargs:
            kwargs["source_indices"] = kwargs.pop("src_indices")
        if "orig_count" in kwargs and "sub_count" not in kwargs:
            kwargs["sub_count"] = kwargs.pop("orig_count")
        if "voice_audio_path" in kwargs and "audio_path" not in kwargs:
            kwargs["audio_path"] = kwargs.pop("voice_audio_path")
        if "voice_duration" in kwargs and "actual_duration" not in kwargs:
            kwargs["actual_duration"] = kwargs.pop("voice_duration")
        super().__init__(*args, **kwargs)


# =========================================================================
# CÁC LỚP CON THỪA KẾ ĐA HÌNH (POLYMORPHIC SINGLE TABLE INHERITANCE)
# Tự động ánh xạ 100% với các bảng cũ của nhánh test1 mà vẫn dùng chung 1 bảng SQLite
# =========================================================================
class DialogueSegmentModel(PipelineSegmentModel):
    """Bảng câu thoại ASR gốc (stage='RAW_ASR')"""
    __mapper_args__ = {"polymorphic_identity": "RAW_ASR"}
    tts_index = synonym("parent_index")

    def __init__(self, *args, **kwargs):
        if "tts_index" in kwargs and "parent_index" not in kwargs:
            kwargs["parent_index"] = kwargs.pop("tts_index")
        super().__init__(*args, **kwargs)


class TTSDialogueSegmentModel(PipelineSegmentModel):
    """Bảng câu thoại TTS (stage='TTS_AUDIO')"""
    __mapper_args__ = {"polymorphic_identity": "TTS_AUDIO"}
    tts_index = synonym("index")

    def __init__(self, *args, **kwargs):
        if "tts_index" in kwargs and "index" not in kwargs:
            kwargs["index"] = kwargs.pop("tts_index")
        super().__init__(*args, **kwargs)


class SubtitleSegmentModel(PipelineSegmentModel):
    """Bảng câu phụ đề Karaoke (stage='SUB_KARAOKE')"""
    __mapper_args__ = {"polymorphic_identity": "SUB_KARAOKE"}
    sub_index = synonym("index")
    tts_index = synonym("parent_index")

    def __init__(self, *args, **kwargs):
        if "sub_index" in kwargs and "index" not in kwargs:
            kwargs["index"] = kwargs.pop("sub_index")
        if "tts_index" in kwargs and "parent_index" not in kwargs:
            kwargs["parent_index"] = kwargs.pop("tts_index")
        super().__init__(*args, **kwargs)


class LLMMergedSegmentModel(PipelineSegmentModel):
    """Bảng câu kịch bản dịch thuật LLM (stage='LLM_MERGED')"""
    __mapper_args__ = {"polymorphic_identity": "LLM_MERGED"}
    tts_index = synonym("index")

    def __init__(self, *args, **kwargs):
        if "tts_index" in kwargs and "index" not in kwargs:
            kwargs["index"] = kwargs.pop("tts_index")
        super().__init__(*args, **kwargs)


