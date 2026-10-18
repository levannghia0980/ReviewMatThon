import os
import json
import sqlite3
import httpx
import logging
import asyncio
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# URL nguồn từ điển mở VietPhrase nổi tiếng từ GitHub & jsDelivr CDN
VIETPHRASE_URLS = [
    "https://fastly.jsdelivr.net/gh/truyencuatui/VietPhrase@master/VietPhrase.txt",
    "https://raw.githubusercontent.com/truyencuatui/VietPhrase/master/VietPhrase.txt"
]

NAMES_URLS = [
    "https://fastly.jsdelivr.net/gh/truyencuatui/VietPhrase@master/LuatNhan.txt",
    "https://raw.githubusercontent.com/truyencuatui/VietPhrase/master/LuatNhan.txt"
]

from app.config import settings
DB_PATH = settings.DB_PATH
DICT_DIR = settings.BASE_DIR / "assets" / "dictionaries"

async def download_file_with_fallback(urls: list, filename: str) -> Path:
    """Tải file từ danh sách URL dự phòng"""
    DICT_DIR.mkdir(parents=True, exist_ok=True)
    local_path = DICT_DIR / filename

    for url in urls:
        logger.info(f"🔄 Đang tải {filename} từ {url}...")
        try:
            async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
                response = await client.get(url)
                if response.status_code == 200:
                    with open(local_path, "wb") as f:
                        f.write(response.content)
                    logger.info(f"✅ Tải thành công {filename} về {local_path} ({os.path.getsize(local_path)} bytes)")
                    return local_path
        except Exception as e:
            logger.warning(f"❌ Tải thất bại từ {url}: {e}")
            
    raise Exception(f"Không thể tải {filename} từ bất kỳ nguồn nào.")

def parse_vietphrase_file(file_path: Path) -> dict:
    """
    Parse định dạng của VietPhrase: Chinese=Vietnamese1/Vietnamese2
    Tự động nhận diện bảng mã UTF-8 hoặc UTF-16 để tránh lỗi đọc ký tự null \x00.
    """
    logger.info(f"🧩 Đang parse dữ liệu từ {file_path.name}...")
    
    # Đọc bytes đầu tiên để phát hiện BOM
    encoding = "utf-8"
    try:
        with open(file_path, "rb") as f:
            header = f.read(4)
            if header.startswith(b"\xff\xfe") or header.startswith(b"\xfe\xff"):
                encoding = "utf-16"
            elif b"\x00" in header:
                # Nếu chứa ký tự null trong header -> khả năng cao là UTF-16 LE không BOM
                encoding = "utf-16"
    except Exception as e:
        logger.warning(f"Không thể tự động nhận diện encoding, mặc định dùng utf-8: {e}")

    logger.info(f"🔍 Bảng mã nhận diện được cho {file_path.name}: {encoding}")
    
    result = {}
    with open(file_path, "r", encoding=encoding, errors="ignore") as f:
        for line in f:
            # Loại bỏ ký tự BOM ẩn (\ufeff) nếu có
            line = line.replace('\ufeff', '').strip()
            if not line or "=" not in line:
                continue
            parts = line.split("=", 1)
            chinese = parts[0].strip()
            vietnamese_options = parts[1].strip().split("/")
            if chinese and vietnamese_options:
                # Loại bỏ ký tự null thừa nếu có do lỗi đọc
                clean_chinese = chinese.replace('\x00', '')
                clean_vietnamese = vietnamese_options[0].replace('\x00', '').strip()
                if clean_chinese and clean_vietnamese:
                    result[clean_chinese] = clean_vietnamese
                    
    logger.info(f"✅ Đã parse thành công {len(result)} từ từ {file_path.name}")
    return result

def import_to_sqlite(phrases: dict, names: dict):
    """Import hàng loạt (Bulk Insert) dữ liệu từ điển vào SQLite nhanh chóng & an toàn"""
    logger.info("💾 Đang chuẩn bị ghi dữ liệu vào SQLite...")
    
    # Kết nối trực tiếp qua SQLite3 để có hiệu năng bulk-insert cao nhất
    conn = sqlite3.connect(DB_PATH, timeout=60.0)
    cursor = conn.cursor()

    try:
        # Bật chế độ tối ưu SQLite
        cursor.execute("PRAGMA synchronous = OFF;")
        cursor.execute("PRAGMA journal_mode = MEMORY;")

        # Tạo bảng nếu chưa tồn tại
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS phrase_dictionary (
                chinese_phrase TEXT PRIMARY KEY,
                vietnamese_phrase TEXT
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS names_dictionary (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                novel_id INTEGER,
                chinese_name TEXT,
                vietnamese_name TEXT
            );
        """)

        # 1. Thêm từ điển từ ghép (PhraseDictionary)
        phrase_data = [(k, v) for k, v in phrases.items()]
        logger.info(f"🔄 Bulk inserting {len(phrase_data)} từ ghép vào phrase_dictionary...")
        
        # INSERT OR REPLACE để tránh trùng lặp PRIMARY KEY
        cursor.executemany(
            "INSERT OR REPLACE INTO phrase_dictionary (chinese_phrase, vietnamese_phrase) VALUES (?, ?);",
            phrase_data
        )

        # 2. Thêm từ điển tên nhân vật (NamesDictionary)
        name_data = [(None, k, v) for k, v in names.items()]
        logger.info(f"🔄 Bulk inserting {len(name_data)} tên nhân vật vào names_dictionary...")
        cursor.executemany(
            "INSERT OR REPLACE INTO names_dictionary (novel_id, chinese_name, vietnamese_name) VALUES (?, ?, ?);",
            name_data
        )

        conn.commit()
        logger.info("🎉 Đã lưu toàn bộ dữ liệu vào SQLite Database thành công!")
    except Exception as e:
        conn.rollback()
        logger.error(f"❌ Lỗi ghi Database: {e}")
        raise e
    finally:
        conn.close()

async def run_import():
    try:
        # 1. Tải file từ CDN
        vp_file = await download_file_with_fallback(VIETPHRASE_URLS, "VietPhrase.txt")
        names_file = await download_file_with_fallback(NAMES_URLS, "LuatNhan.txt")

        # 2. Parse file thành dict
        phrases = parse_vietphrase_file(vp_file)
        names = parse_vietphrase_file(names_file)

        # 3. Save JSON để lưu trữ dự phòng như yêu cầu
        vp_json_path = DICT_DIR / "VietPhrase_Clean.json"
        names_json_path = DICT_DIR / "Names_Clean.json"
        
        logger.info(f"💾 Đang lưu dữ liệu dự phòng ra file JSON...")
        with open(vp_json_path, "w", encoding="utf-8") as f:
            json.dump(phrases, f, ensure_ascii=False, indent=2)
        with open(names_json_path, "w", encoding="utf-8") as f:
            json.dump(names, f, ensure_ascii=False, indent=2)
        
        logger.info(f"✅ Đã lưu JSON dự phòng tại {vp_json_path} và {names_json_path}")

        # 4. Ghi trực tiếp lên DB SQLite để tra cứu tối ưu
        import_to_sqlite(phrases, names)

        # 5. Xóa file TXT gốc để giải phóng đĩa cứng nếu cần
        if vp_file.exists():
            os.remove(vp_file)
        if names_file.exists():
            os.remove(names_file)
            
        logger.info("🚀 Hoàn thành toàn bộ quy trình import dữ liệu sạch!")
    except Exception as e:
        logger.error(f"❌ Quy trình thất bại: {e}")

if __name__ == "__main__":
    asyncio.run(run_import())
