import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, Text
from sqlalchemy.orm import relationship
from app.db.session import Base

class ProjectTask(Base):
    __tablename__ = "project_tasks"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    video_id = Column(String(100), index=True, nullable=False)
    title = Column(String(500), nullable=False)
    source_url = Column(String(1000), nullable=True)
    duration = Column(Float, default=0.0)
    
    # Trạng thái tiến trình: PENDING, DOWNLOADED, TRANSCRIBED, TRANSLATED, DUBBED, COMPLETED, FAILED
    status = Column(String(50), default="PENDING", index=True)
    
    source_language = Column(String(10), default="zh")
    target_language = Column(String(10), default="vi")
    
    # File Paths trong input & output
    video_path = Column(String(500), nullable=True)     # input/videos/
    audio_path = Column(String(500), nullable=True)     # input/audio_raw/
    srt_path = Column(String(500), nullable=True)       # output/transcripts/
    txt_path = Column(String(500), nullable=True)       # output/transcripts/
    json_path = Column(String(500), nullable=True)      # output/transcripts/
    final_video_path = Column(String(500), nullable=True) # output/final_videos/
    
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    # Quan hệ duy nhất với Bảng Hợp Nhất Pipeline
    pipeline_segments = relationship("PipelineSegmentModel", back_populates="project", cascade="all, delete-orphan", order_by="PipelineSegmentModel.index")

    @property
    def dialogues(self):
        """Danh sách câu ASR gốc (stage='RAW_ASR')"""
        return [s for s in self.pipeline_segments if s.stage == "RAW_ASR"]

    @property
    def tts_dialogues(self):
        """Danh sách câu kịch bản TTS (stage='TTS_AUDIO')"""
        return [s for s in self.pipeline_segments if s.stage == "TTS_AUDIO"]

    @property
    def subtitles(self):
        """Danh sách câu phụ đề Karaoke (stage='SUB_KARAOKE')"""
        return [s for s in self.pipeline_segments if s.stage == "SUB_KARAOKE"]

    @property
    def llm_dialogues(self):
        """Danh sách câu kịch bản dịch thuật (stage='LLM_MERGED')"""
        return [s for s in self.pipeline_segments if s.stage == "LLM_MERGED"]



