# -*- coding: utf-8 -*-
"""
app/db/project_db.py
====================
QUẢN LÝ CƠ SỞ DỮ LIỆU ĐỘC LẬP TỪNG PHIM (PER-PROJECT DATABASE)
Mỗi phim sở hữu đúng 1 file SQLite riêng: output/projects/project_{id}/project.db
- Không chia sẻ bảng, không lo bị khóa CSDL (Database Locked).
- Xóa phim là xóa file, tự động dọn dẹp sạch sẽ.
- 1 Bảng câu duy nhất: `segments` (4 loại câu: stt, llm, tts, sub).
- 2 Cột chữ xuyên suốt: text_orig (Trung) và text_trans (Việt - ở stt chính là VI CapCut).
- sub: text_orig để trống (NULL), text_trans tách từ tts theo dấu câu.
"""

import sqlite3
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from contextlib import contextmanager

from app.config import get_project_dirs

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS segments (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    type                TEXT NOT NULL,          -- 'stt' | 'llm' | 'tts' | 'sub'
    order_idx           INTEGER NOT NULL,       -- Số thứ tự theo từng loại: 1, 2, 3...
    
    -- 2 CỘT CHỮ DUY NHẤT
    text_orig           TEXT,                   -- Trung: Có ở stt, llm, tts. ĐỂ TRỐNG ở sub!
    text_trans          TEXT NOT NULL,          -- Việt: stt = VI CapCut | llm = dịch 1:1 | tts = nối đọc | sub = ngắt dòng
    
    -- TIMECODE (Mili-giây số nguyên, chuẩn xác 100%, không sai số float)
    start_ms            INTEGER NOT NULL,       -- Thời điểm bắt đầu (ms)
    end_ms              INTEGER NOT NULL,       -- Thời điểm kết thúc (ms)
    duration_ms         INTEGER NOT NULL,       -- Thời lượng = end_ms - start_ms
    
    -- TRẠNG THÁI & KIỂM SOÁT LỖI
    status              TEXT DEFAULT 'pending', -- 'pending' | 'processing' | 'passed' | 'failed'
    error_message       TEXT,                   -- Ghi rõ lý do hỏng để xử lý riêng
    retry_count         INTEGER DEFAULT 0,      -- Số lần thử lại
    process_started_at  TIMESTAMP,              -- Thời điểm bắt đầu xử lý chống kẹt
    
    -- TRUY VẾT PHA HỆ
    parent_id           INTEGER,                -- llm trỏ về stt.id | sub trỏ về tts.id
    batch_id            INTEGER,                -- llm trỏ về lô dịch batches.id
    llm_start_idx       INTEGER,                -- tts trỏ về câu llm đầu
    llm_end_idx         INTEGER,                -- tts trỏ về câu llm cuối
    
    -- RIÊNG CHO TTS
    audio_path          TEXT,                   -- Đường dẫn file mp3 lồng tiếng
    voice_code          TEXT,                   -- Mã giọng đọc TTS
    speed_ratio         REAL DEFAULT 1.0,       -- Hệ số co giãn tốc độ đọc
    actual_duration_ms  INTEGER,                -- Thời lượng audio thực tế đo bằng FFprobe (ms)
    
    -- RIÊNG CHO SUB
    track               INTEGER DEFAULT 0,      -- Track phụ đề
    style               TEXT,                   -- Style hiển thị Karaoke/ASS
    
    UNIQUE(type, order_idx)
);

CREATE INDEX IF NOT EXISTS idx_seg_type_order ON segments(type, order_idx);
CREATE INDEX IF NOT EXISTS idx_seg_type_status ON segments(type, status);
CREATE INDEX IF NOT EXISTS idx_seg_parent ON segments(parent_id);

-- BẢNG LÔ DỊCH
CREATE TABLE IF NOT EXISTS translation_batches (
    id                  INTEGER PRIMARY KEY,
    model_name          TEXT,
    prompt_version      TEXT,
    input_tokens        INTEGER DEFAULT 0,
    output_tokens       INTEGER DEFAULT 0,
    status              TEXT DEFAULT 'pending',
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- BẢNG THUẬT NGỮ
CREATE TABLE IF NOT EXISTS glossary_terms (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    term_zh             TEXT NOT NULL UNIQUE,
    term_vi             TEXT NOT NULL,
    category            TEXT DEFAULT 'NAME'
);
"""

class ProjectDB:
    @staticmethod
    def get_db_path(project_id: int) -> Path:
        p_dirs = get_project_dirs(project_id)
        return p_dirs["root"] / "project.db"

    @classmethod
    @contextmanager
    def get_connection(cls, project_id: int):
        db_path = cls.get_db_path(project_id)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA busy_timeout=30000;")
            yield conn
        finally:
            conn.close()

    @classmethod
    def init_db(cls, project_id: int):
        """Khởi tạo cấu trúc bảng chuẩn chỉ cho phim."""
        with cls.get_connection(project_id) as conn:
            conn.executescript(SCHEMA_SQL)
            conn.commit()

    @classmethod
    def save_stt_batch(cls, project_id: int, items: List[Dict[str, Any]]):
        """
        Lưu toàn bộ câu nhận diện STT:
        - text_orig: Chữ Trung
        - text_trans: Bản dịch CapCut VI
        """
        cls.init_db(project_id)
        with cls.get_connection(project_id) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM segments WHERE type = 'stt';")
            
            records = []
            for it in items:
                o_idx = int(it.get("id") or it.get("order_idx") or 0)
                zh = it.get("text") or it.get("clean_text") or it.get("text_orig") or ""
                vi = it.get("translated_text") or it.get("reference_text") or it.get("text_trans") or ""
                s_ms = int(round(float(it.get("start", 0.0) or it.get("start_ms", 0) / 1000.0) * 1000))
                e_ms = int(round(float(it.get("end", 0.0) or it.get("end_ms", 0) / 1000.0) * 1000))
                d_ms = max(100, e_ms - s_ms)
                
                records.append((
                    'stt', o_idx, zh, vi, s_ms, e_ms, d_ms, 'passed'
                ))
                
            cursor.executemany("""
                INSERT OR REPLACE INTO segments 
                (type, order_idx, text_orig, text_trans, start_ms, end_ms, duration_ms, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """, records)
            conn.commit()
            logger.info(f"[ProjectDB #{project_id}] Đã lưu {len(records)} câu STT (kèm VI CapCut) vào project.db.")

    @classmethod
    def save_llm_batch(cls, project_id: int, batch_id: int, items: List[Dict[str, Any]]):
        """
        Lưu các câu dịch LLM 1:1 đã kiểm tra đạt chuẩn:
        - text_orig: Đúng chữ Trung của câu stt cha
        - text_trans: Bản dịch tiếng Việt của câu đó
        - parent_id: id của dòng stt cha
        """
        cls.init_db(project_id)
        with cls.get_connection(project_id) as conn:
            cursor = conn.cursor()
            
            # Đọc map stt order_idx -> stt id
            cursor.execute("SELECT id, order_idx, text_orig FROM segments WHERE type = 'stt';")
            stt_map = {row["order_idx"]: (row["id"], row["text_orig"]) for row in cursor.fetchall()}
            
            records = []
            for it in items:
                o_idx = int(it["order_idx"])
                stt_info = stt_map.get(o_idx)
                parent_id = stt_info[0] if stt_info else None
                zh = stt_info[1] if stt_info else (it.get("text_orig") or "")
                vi = it.get("text_trans") or it.get("vi") or ""
                s_ms = int(it.get("start_ms", 0))
                e_ms = int(it.get("end_ms", 0))
                d_ms = max(100, e_ms - s_ms)
                status = it.get("status", "passed")
                err = it.get("error_message")
                
                records.append((
                    'llm', o_idx, zh, vi, s_ms, e_ms, d_ms, status, err, parent_id, batch_id
                ))
                
            cursor.executemany("""
                INSERT OR REPLACE INTO segments
                (type, order_idx, text_orig, text_trans, start_ms, end_ms, duration_ms, status, error_message, parent_id, batch_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, records)
            conn.commit()

    @classmethod
    def get_segments(cls, project_id: int, seg_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """Truy vấn siêu tốc các câu theo loại."""
        if not cls.get_db_path(project_id).exists():
            return []
            
        with cls.get_connection(project_id) as conn:
            cursor = conn.cursor()
            if seg_type:
                cursor.execute("SELECT * FROM segments WHERE type = ? ORDER BY order_idx ASC;", (seg_type.lower(),))
            else:
                cursor.execute("SELECT * FROM segments ORDER BY type ASC, order_idx ASC;")
            rows = cursor.fetchall()
            return [dict(r) for r in rows]

    @classmethod
    def get_metrics(cls, project_id: int) -> Dict[str, Any]:
        """Thống kê tiến độ theo từng loại câu."""
        if not cls.get_db_path(project_id).exists():
            return {}
            
        with cls.get_connection(project_id) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT type, status, COUNT(*) as cnt 
                FROM segments 
                GROUP BY type, status;
            """)
            rows = cursor.fetchall()
            
            res = {"stt": 0, "llm": 0, "tts": 0, "sub": 0}
            for r in rows:
                t = r["type"]
                if t in res:
                    res[t] += r["cnt"]
            return res
