from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker
from app.config import settings

# Cấu hình SQLite Engine với WAL mode và busy timeout chống khóa CSDL khi nhiều luồng ghi đồng thời
engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False}
)

@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")  # Chờ 30s nếu có luồng khác đang ghi
    except Exception:
        pass
    finally:
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    """Dependency cung cấp DB session cho từng API request"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    """Tự động tạo bảng và migrate các cột còn thiếu trong SQLite khi Server khởi động"""
    Base.metadata.create_all(bind=engine)
    
    # Auto-migration nhẹ nhàng cho SQLite nếu database cũ còn thiếu cột mới
    try:
        with engine.connect() as conn:
            inspector = inspect(engine)
            existing_tables = inspector.get_table_names()

            # 1. Kiểm tra bảng dialogue_segments
            if "dialogue_segments" in existing_tables:
                diag_cols = {col["name"] for col in inspector.get_columns("dialogue_segments")}
                if "role" not in diag_cols:
                    conn.execute(text("ALTER TABLE dialogue_segments ADD COLUMN role VARCHAR(100) DEFAULT 'NARRATOR'"))
                if "speaker" not in diag_cols:
                    conn.execute(text("ALTER TABLE dialogue_segments ADD COLUMN speaker VARCHAR(100) DEFAULT 'NARRATOR'"))
                if "voice_audio_path" not in diag_cols:
                    conn.execute(text("ALTER TABLE dialogue_segments ADD COLUMN voice_audio_path VARCHAR(500) NULL"))
                if "voice_duration" not in diag_cols:
                    conn.execute(text("ALTER TABLE dialogue_segments ADD COLUMN voice_duration FLOAT DEFAULT 0.0"))
                if "speed_ratio" not in diag_cols:
                    conn.execute(text("ALTER TABLE dialogue_segments ADD COLUMN speed_ratio FLOAT DEFAULT 1.0"))

            # 2. Kiểm tra bảng tts_dialogue_segments
            if "tts_dialogue_segments" in existing_tables:
                tts_cols = {col["name"] for col in inspector.get_columns("tts_dialogue_segments")}
                if "borrowed_start_time" not in tts_cols:
                    conn.execute(text("ALTER TABLE tts_dialogue_segments ADD COLUMN borrowed_start_time FLOAT NULL"))
                if "borrowed_end_time" not in tts_cols:
                    conn.execute(text("ALTER TABLE tts_dialogue_segments ADD COLUMN borrowed_end_time FLOAT NULL"))
                if "borrowed_duration" not in tts_cols:
                    conn.execute(text("ALTER TABLE tts_dialogue_segments ADD COLUMN borrowed_duration FLOAT NULL"))
                if "borrow_prev_sec" not in tts_cols:
                    conn.execute(text("ALTER TABLE tts_dialogue_segments ADD COLUMN borrow_prev_sec FLOAT DEFAULT 0.0"))
                if "borrow_next_sec" not in tts_cols:
                    conn.execute(text("ALTER TABLE tts_dialogue_segments ADD COLUMN borrow_next_sec FLOAT DEFAULT 0.0"))
                if "final_speed_ratio" not in tts_cols:
                    conn.execute(text("ALTER TABLE tts_dialogue_segments ADD COLUMN final_speed_ratio FLOAT DEFAULT 1.0"))
                if "initial_ratio" not in tts_cols:
                    conn.execute(text("ALTER TABLE tts_dialogue_segments ADD COLUMN initial_ratio FLOAT DEFAULT 1.0"))
                if "cut_head_ms" not in tts_cols:
                    conn.execute(text("ALTER TABLE tts_dialogue_segments ADD COLUMN cut_head_ms INTEGER DEFAULT 0"))
                if "cut_tail_ms" not in tts_cols:
                    conn.execute(text("ALTER TABLE tts_dialogue_segments ADD COLUMN cut_tail_ms INTEGER DEFAULT 0"))
                if "is_anomaly" not in tts_cols:
                    conn.execute(text("ALTER TABLE tts_dialogue_segments ADD COLUMN is_anomaly BOOLEAN DEFAULT 0"))
            conn.commit()
    except Exception as e:
        # Ghi nhận nhưng không làm crash app nếu đã tồn tại
        pass
