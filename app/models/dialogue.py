# -*- coding: utf-8 -*-
from typing import Optional
from sqlalchemy import Column, Integer, String, Float, Text, Boolean, ForeignKey, UniqueConstraint, Index
from sqlalchemy.orm import relationship
from app.db.session import Base

# =============================================================================
# NGUYÊN TẮC BẤT BIẾN TUYỆT ĐỐI (IMMUTABILITY INVARIANTS):
#
# BẢO VỆ TIMECODE GỐC:
# 1. dialogue_segments.start_time / end_time
#    -> Ghi 1 LẦN DUY NHẤT bởi ASR (CapCut/Whisper), KHÔNG BAO GIỜ thay đổi.
#    -> Đây là "chân lý vật lý" của video gốc. Mọi thuật toán phải đọc từ đây.
#
# 2. tts_dialogue_segments.start_time / end_time
#    -> Ghi 1 LẦN DUY NHẤT bởi SmartTimelineHealer sau bước dịch thuật.
#    -> KHÔNG BAO GIỜ bị TimelineBorrower, ChunkMerger hay bất kỳ bước nào đè hỏng.
#
# 3. tts_dialogue_segments.borrowed_start_time / borrowed_end_time (NULLABLE)
#    -> CHỈ được ghi bởi TimelineBorrower để lưu kết quả vay mượn TTS.
#    -> Đây là dữ liệu tham khảo, KHÔNG dùng làm "gốc" cho lần chạy tiếp theo.
#    -> Khi chạy TTS lại: Reset về NULL, tính lại từ start_time/end_time.
# =============================================================================


class DialogueSegmentModel(Base):
    """
    Bảng câu thoại ASR gốc – Bất biến sau bước STT.

    Lưu từng mẩu câu nhỏ do ASR nhận diện.
    start_time / end_time = mốc thời gian gốc của video, KHÔNG BAO GIỜ thay đổi.
    """
    __tablename__ = "dialogue_segments"
    __table_args__ = (
        UniqueConstraint("task_id", "index", name="uq_task_dialogue_index"),
        Index("idx_diag_task_tts", "task_id", "tts_index"),
        Index("idx_diag_task_status", "task_id", "status"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("project_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    index = Column(Integer, nullable=False, index=True)         # Thứ tự câu ASR gốc: 1, 2, 3...
    tts_index = Column(Integer, nullable=True, index=True)      # Liên kết tới câu gộp TTS

    # -- TIMELINE ASR GỐC – BẤT BIẾN TUYỆT ĐỐI --
    # Chỉ được ghi bởi WhisperService/CapCutASR, KHÔNG AI ĐƯỢC SỬA SAU ĐÓ.
    start_time = Column(Float, nullable=False)   # Giây bắt đầu ASR gốc
    end_time   = Column(Float, nullable=False)   # Giây kết thúc ASR gốc
    duration   = Column(Float, nullable=False)   # end_time - start_time (giây)

    # -- NỘI DUNG VĂN BẢN --
    original_text   = Column(Text, nullable=False)  # Tiếng nguồn gốc (Trung/Anh...)
    clean_text      = Column(Text, nullable=True)   # Tiếng nguồn đã lọc từ đệm
    translated_text = Column(Text, nullable=True)   # Bản dịch tiếng Việt (Karaoke Subtitle)

    # -- NHÂN VẬT --
    role    = Column(String(100), default="NARRATOR")   # NARRATOR | tên nhân vật
    speaker = Column(String(100), default="NARRATOR")   # Alias của role (tương thích TTS)

    # -- ÂM THANH RIÊNG (NẾU CÓ) --
    voice_audio_path = Column(String(500), nullable=True)
    voice_duration   = Column(Float, default=0.0)
    speed_ratio      = Column(Float, default=1.0)

    # -- CHẤT LƯỢNG & TRẠNG THÁI --
    confidence = Column(Float, default=0.0)
    # RAW: Chưa dịch | TRANSLATED: Đã dịch | DUBBED: Đã lồng tiếng | SYNCED: Đã đồng bộ sub
    status = Column(String(30), default="RAW")

    project = relationship("ProjectTask", back_populates="dialogues")


class TTSDialogueSegmentModel(Base):
    """
    Bảng câu gộp TTS – Được xây dựng từ SmartTimelineHealer sau bước dịch.

    Mỗi hàng = 1 chunk âm thanh đọc liên tục (có thể gộp nhiều mẩu ASR con).

    NGUYÊN TẮC BẤT BIẾN:
    - start_time / end_time: Bất biến từ SmartTimelineHealer, KHÔNG AI SỬA SAU ĐÓ.
    - borrowed_start_time / borrowed_end_time: CHỈ TimelineBorrower ghi, dùng tham khảo.
    - Toàn bộ xử lý audio (ghép master_buffer) đọc start_time/end_time.
      TimelineBorrower chỉ trả về Dict[tts_index -> (borrow_start, borrow_end)]
      để dùng NỘI BỘ khi ghép audio, KHÔNG ghi đè start_time/end_time.
    """
    __tablename__ = "tts_dialogue_segments"
    __table_args__ = (
        UniqueConstraint("task_id", "tts_index", name="uq_task_tts_dialogue_index"),
        Index("idx_tts_task_batch", "task_id", "batch_index"),
        Index("idx_tts_task_speaker", "task_id", "speaker"),
        Index("idx_tts_task_status", "task_id", "status"),
    )

    id          = Column(Integer, primary_key=True, autoincrement=True)
    task_id     = Column(Integer, ForeignKey("project_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    tts_index   = Column(Integer, nullable=False, index=True)   # Thứ tự chunk TTS: 1, 2, 3...
    batch_index = Column(Integer, default=1)                    # Thuộc lô dịch nào

    # -- 1. LIÊN KẾT VỀ CÂU ASR GỐC --
    src_start_index = Column(Integer, nullable=False)    # Mẩu ASR đầu tiên được gộp (vd: 35)
    src_end_index   = Column(Integer, nullable=False)    # Mẩu ASR cuối cùng được gộp (vd: 37)
    src_indices     = Column(String(500), nullable=False)   # Chuỗi trực quan: "35,36,37"
    orig_count      = Column(Integer, default=1)            # Số mẩu ASR gộp lại

    # -- 2. TIMELINE ASR GỐC – BẤT BIẾN TUYỆT ĐỐI --
    # Ghi 1 lần bởi SmartTimelineHealer, KHÔNG AI ĐƯỢC SỬA SAU ĐÓ.
    start_time = Column(Float, nullable=False)   # Giây bắt đầu theo ASR gốc
    end_time   = Column(Float, nullable=False)   # Giây kết thúc theo ASR gốc
    duration   = Column(Float, nullable=False)   # end_time - start_time

    # -- 3. TIMELINE SAU VAY MƯỢN (TimelineBorrower) – CHỈ THAM KHẢO --
    borrowed_start_time = Column(Float, nullable=True)   # Start sau vay mượn
    borrowed_end_time   = Column(Float, nullable=True)   # End sau vay mượn
    borrowed_duration   = Column(Float, nullable=True)   # Tổng cửa sổ sau vay mượn
    borrow_prev_sec     = Column(Float, default=0.0)     # Đã vay của câu trước (giây)
    borrow_next_sec     = Column(Float, default=0.0)     # Đã vay của câu sau (giây)

    # -- 4. KẾT QUẢ ÂM THANH THỰC TẾ (Sau khi ghép master_buffer) --
    raw_audio_duration     = Column(Float, default=0.0)   # Độ dài TTS thô ban đầu
    trimmed_audio_duration = Column(Float, default=0.0)   # Sau khi gọt đầu/đuôi silence
    cut_head_ms            = Column(Integer, default=0)   # ms đã gọt đầu
    cut_tail_ms            = Column(Integer, default=0)   # ms đã gọt đuôi
    initial_ratio          = Column(Float, default=1.0)
    final_speed_ratio      = Column(Float, default=1.0)   # Tỉ lệ co giãn cuối cùng
    voice_duration         = Column(Float, default=0.0)   # Thời lượng audio dán vào phim
    is_anomaly             = Column(Boolean, default=False) # True nếu final_speed_ratio > 2.0x

    # -- 5. NỘI DUNG VĂN BẢN --
    original_text   = Column(Text, nullable=False)   # Tiếng nguồn gộp
    translated_text = Column(Text, nullable=False)   # Bản dịch tiếng Việt (chunk TTS)

    # -- 6. NHÂN VẬT & GIỌNG ĐỌC --
    speaker          = Column(String(100), default="NARRATOR")
    voice_code       = Column(String(100), nullable=True)
    voice_audio_path = Column(String(500), nullable=True)   # File audio riêng của chunk

    # -- 7. TRẠNG THÁI --
    status    = Column(String(30), default="PENDING")
    is_locked = Column(Boolean, default=False)   # Người dùng khóa thủ công

    project = relationship("ProjectTask", back_populates="tts_dialogues")

    def __init__(self, *args, **kwargs):
        # Hỗ trợ cả tên biến asr_start/asr_end và start_time/end_time
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
        if "speed_ratio" in kwargs and "final_speed_ratio" not in kwargs:
            kwargs["final_speed_ratio"] = kwargs.pop("speed_ratio")
        super().__init__(*args, **kwargs)

    # -- ALIAS PROPERTIES --
    @property
    def asr_start(self) -> float:
        return self.start_time

    @property
    def asr_end(self) -> float:
        return self.end_time

    @property
    def asr_duration(self) -> float:
        return self.duration

    @property
    def borrowed_start(self) -> Optional[float]:
        return self.borrowed_start_time

    @borrowed_start.setter
    def borrowed_start(self, val: Optional[float]):
        self.borrowed_start_time = val

    @property
    def borrowed_end(self) -> Optional[float]:
        return self.borrowed_end_time

    @borrowed_end.setter
    def borrowed_end(self, val: Optional[float]):
        self.borrowed_end_time = val

    @property
    def speed_ratio(self) -> float:
        return self.final_speed_ratio

    @speed_ratio.setter
    def speed_ratio(self, val: float):
        self.final_speed_ratio = val

    @property
    def clean_text(self) -> str:
        return self.original_text or ""

    @property
    def role(self) -> str:
        return self.speaker or "NARRATOR"

    @property
    def index(self) -> int:
        return self.tts_index or 1

    @index.setter
    def index(self, value: int):
        self.tts_index = value


class SubtitleSegmentModel(Base):
    """
    BẢNG 3: Phụ đề hiển thị màn hình Karaoke (Karaoke Subtitles).
    Được sinh ra từ kịch bản dịch và đồng bộ 100% theo sóng âm của chunk TTS cha.
    Dùng trực tiếp để render phụ đề Karaoke ASS lên video.
    """
    __tablename__ = "video_subtitle_segments"
    __table_args__ = (
        UniqueConstraint("task_id", "sub_index", name="uq_task_sub_index"),
        Index("idx_sub_task_tts", "task_id", "tts_index"),
        Index("idx_sub_task_batch", "task_id", "batch_index"),
    )

    id          = Column(Integer, primary_key=True, autoincrement=True)
    task_id     = Column(Integer, ForeignKey("project_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    sub_index   = Column(Integer, nullable=False, index=True)   # Thứ tự phụ đề hiển thị: 1, 2, 3...
    tts_index   = Column(Integer, nullable=False, index=True)   # Trỏ về chunk cha trong tts_dialogue_segments
    batch_index = Column(Integer, default=1)

    # -- TIMELINE PHỤ ĐỀ HIỂN THỊ (Đã căn theo sóng âm & lồng tiếng) --
    start_time = Column(Float, nullable=False)   # Giây bắt đầu hiển thị sub
    end_time   = Column(Float, nullable=False)   # Giây kết thúc hiển thị sub
    duration   = Column(Float, nullable=False)   # end_time - start_time

    # -- NỘI DUNG PHỤ ĐỀ --
    text_vi = Column(Text, nullable=False)       # Chữ tiếng Việt hiển thị trên màn hình
    text_zh = Column(Text, nullable=True)        # Câu gốc tiếng Trung tương ứng (nếu có)
    speaker = Column(String(100), default="NARRATOR")

    project = relationship("ProjectTask", back_populates="subtitles")
