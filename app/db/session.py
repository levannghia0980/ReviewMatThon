from sqlalchemy import create_engine, event
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
    """Tự động tạo bảng khi Server khởi động"""
    Base.metadata.create_all(bind=engine)

