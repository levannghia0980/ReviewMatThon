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
    import app.models  # Nạp đầy đủ metadata của tất cả các bảng
    Base.metadata.create_all(bind=engine)

    
    # Dọn dẹp triệt để 3 bảng cũ đã được di trú 100% sang pipeline_segments
    # và tự động migrate các cột TTS/Timeline cần thiết cho pipeline_segments
    try:
        with engine.connect() as conn:
            inspector = inspect(engine)
            existing_tables = inspector.get_table_names()
            for legacy_tbl in ["dialogue_segments", "tts_dialogue_segments", "video_subtitle_segments"]:
                if legacy_tbl in existing_tables:
                    conn.execute(text(f"DROP TABLE IF EXISTS {legacy_tbl}"))

            if "pipeline_segments" in existing_tables:
                pipe_cols = {col["name"] for col in inspector.get_columns("pipeline_segments")}
                needed_cols = [
                    ("src_start_index", "INTEGER NULL"),
                    ("src_end_index", "INTEGER NULL"),
                    ("borrowed_start_time", "FLOAT NULL"),
                    ("borrowed_end_time", "FLOAT NULL"),
                    ("borrowed_duration", "FLOAT NULL"),
                    ("borrow_prev_sec", "FLOAT DEFAULT 0.0"),
                    ("borrow_next_sec", "FLOAT DEFAULT 0.0"),
                    ("raw_audio_duration", "FLOAT DEFAULT 0.0"),
                    ("trimmed_audio_duration", "FLOAT DEFAULT 0.0"),
                    ("cut_head_ms", "INTEGER DEFAULT 0"),
                    ("cut_tail_ms", "INTEGER DEFAULT 0"),
                    ("initial_ratio", "FLOAT DEFAULT 1.0"),
                ]
                for col_name, col_type in needed_cols:
                    if col_name not in pipe_cols:
                        conn.execute(text(f"ALTER TABLE pipeline_segments ADD COLUMN {col_name} {col_type}"))

            conn.commit()
    except Exception as e:
        pass

