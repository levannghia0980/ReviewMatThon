from sqlalchemy import Column, Integer, String, Float, Text, ForeignKey
from sqlalchemy.orm import relationship
from app.db.session import Base

class DialogueSegmentModel(Base):
    __tablename__ = "dialogue_segments"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("project_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    index = Column(Integer, nullable=False, index=True) # Thứ tự câu 1, 2, 3...
    
    # Mốc thời gian (giây)
    start_time = Column(Float, nullable=False)
    end_time = Column(Float, nullable=False)
    duration = Column(Float, nullable=False) # end_time - start_time
    
    # Nội dung Text
    original_text = Column(Text, nullable=False)        # Tiếng Trung gốc
    clean_text = Column(Text, nullable=True)           # Tiếng Trung sạch (bỏ 呃, 啊...)
    translated_text = Column(Text, nullable=True)      # Dịch tiếng Việt
    
    # Lồng tiếng TTS
    voice_audio_path = Column(String(500), nullable=True) # File mp3/wav câu thoại tiếng Việt
    voice_duration = Column(Float, default=0.0)         # Thời lượng audio TTS thực tế
    speed_ratio = Column(Float, default=1.0)            # Hệ số tăng/giảm tốc độ (ví dụ 1.15x) để khớp duration gốc
    
    confidence = Column(Float, default=0.0)
    status = Column(String(30), default="RAW") # RAW, TRANSLATED, DUBBED, SYNCED

    project = relationship("ProjectTask", back_populates="dialogues")
