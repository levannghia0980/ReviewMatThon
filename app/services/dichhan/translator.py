import gc
import re
from typing import Dict, List, Optional
from functools import lru_cache
from sqlalchemy import select
from app.db.session import SessionLocal
from app.models.dictionary import PhraseDictionary, NamesDictionary
from app.services.dichhan.hanviet_data import get_hanviet, HanVietContext


# Cache từ điển từ ghép và tên nhân vật trong RAM (LRU Cache tối đa 10k mục)
# Key: (chinese_text, novel_id)
# Tránh truy vấn DB liên tục cho từng chữ
_PHRASE_CACHE: Dict[str, str] = {}
_NAME_CACHE: Dict[tuple, str] = {}

async def load_phrase_dict_to_cache(session):
    """Nạp nhanh cụm từ ghép hot vào Cache nếu Cache trống"""
    global _PHRASE_CACHE
    if not _PHRASE_CACHE and session is not None:
        try:
            stmt = select(PhraseDictionary).limit(5000)
            res = session.execute(stmt)
            for item in res.scalars().all():
                _PHRASE_CACHE[item.chinese_phrase] = item.vietnamese_phrase
        except Exception:
            pass

async def load_name_dict_to_cache(session, novel_id: Optional[int] = None):
    """Nạp tên nhân vật của bộ truyện cụ thể vào cache"""
    global _NAME_CACHE
    if session is not None:
        try:
            stmt = select(NamesDictionary).where(
                (NamesDictionary.novel_id == novel_id) | (NamesDictionary.novel_id == None)
            )
            res = session.execute(stmt)
            for item in res.scalars().all():
                _NAME_CACHE[(item.chinese_name, novel_id)] = item.vietnamese_name
        except Exception:
            pass

def clear_translator_caches():
    """Xóa cache khi người dùng thay đổi/cập nhật từ điển"""
    global _PHRASE_CACHE, _NAME_CACHE
    _PHRASE_CACHE.clear()
    _NAME_CACHE.clear()

async def lookup_phrase(chinese_phrase: str, session) -> Optional[str]:
    """Tìm cụm từ ghép (Offline Dict) trong Cache -> DB"""
    # 1. Check RAM Cache
    if chinese_phrase in _PHRASE_CACHE:
        return _PHRASE_CACHE[chinese_phrase]

    # 2. Check DB
    if session is not None:
        try:
            stmt = select(PhraseDictionary.vietnamese_phrase).where(PhraseDictionary.chinese_phrase == chinese_phrase)
            res = session.execute(stmt)
            val = res.scalar_one_or_none()
            if val:
                # Cache lại
                if len(_PHRASE_CACHE) < 15000:
                    _PHRASE_CACHE[chinese_phrase] = val
                return val
        except Exception:
            pass
    return None

async def lookup_name(chinese_name: str, novel_id: Optional[int], session) -> Optional[str]:
    """Tìm tên riêng/tên nhân vật trong Cache -> DB"""
    cache_key = (chinese_name, novel_id)
    if cache_key in _NAME_CACHE:
        return _NAME_CACHE[cache_key]

    if session is not None:
        try:
            # Check cụ thể cho truyện
            stmt = select(NamesDictionary.vietnamese_name).where(
                NamesDictionary.chinese_name == chinese_name,
                NamesDictionary.novel_id == novel_id
            )
            res = session.execute(stmt)
            val = res.scalar_one_or_none()

            if not val:
                # Check global (novel_id IS NULL)
                stmt_glob = select(NamesDictionary.vietnamese_name).where(
                    NamesDictionary.chinese_name == chinese_name,
                    NamesDictionary.novel_id == None
                )
                res_glob = session.execute(stmt_glob)
                val = res_glob.scalar_one_or_none()

            if val:
                if len(_NAME_CACHE) < 10000:
                    _NAME_CACHE[cache_key] = val
                return val
        except Exception:
            pass
    return None

async def translate_chinese_to_vietnamese_rough(text: str, novel_id: Optional[int] = None) -> str:
    """
    Dịch thô Hán-Việt tối ưu đa tầng (Greedy Maximum Matching):
    1. Ưu tiên Tên nhân vật (Names Dict)
    2. Từ điển từ ghép (Phrases Dict)
    3. Hán-Việt âm đọc (offline/online)
    """
    if not text or not text.strip():
        return text

    context = HanVietContext()
    session = None
    try:
        try:
            session = SessionLocal()
            await load_phrase_dict_to_cache(session)
        except Exception:
            session = None

        # Biểu thức chính quy tách khối chữ Hán và các ký tự thường (số, tiếng Anh, dấu câu)
        blocks = re.split(r'([\u4e00-\u9fff]+)', text)
        translated_blocks = []

        for block in blocks:
            if not block:
                continue
            
            # Nếu không phải chữ Hán, giữ nguyên
            if not re.match(r'^[\u4e00-\u9fff]+$', block):
                translated_blocks.append(block)
                continue

            # Áp dụng thuật toán Tách từ tham lam (Maximum Matching)
            idx = 0
            n = len(block)
            max_word_len = 5 # Độ dài cụm từ tối đa cần quét

            while idx < n:
                matched = False
                
                # Quét từ độ dài lớn về 1
                for length in range(max_word_len, 0, -1):
                    if idx + length <= n:
                        word = block[idx : idx + length]
                        
                        # 1. Tra tên nhân vật
                        if session is not None:
                            name_val = await lookup_name(word, novel_id, session)
                            if name_val:
                                translated_blocks.append(f" {name_val} ")
                                idx += length
                                matched = True
                                break
                        
                        # 2. Tra cụm từ ghép nghĩa Việt
                        if session is not None:
                            phrase_val = await lookup_phrase(word, session)
                            if phrase_val:
                                # Viết hoa chữ cái đầu cho tự nhiên
                                translated_blocks.append(f" {phrase_val.strip()} ")
                                idx += length
                                matched = True
                                break

                # 3. Fallback: Nếu không khớp từ ghép nào -> Dịch âm Hán Việt từng chữ đơn lẻ
                if not matched:
                    single_char = block[idx]
                    hv_val = await get_hanviet(single_char, online=False, context=context)
                    translated_blocks.append(f" {hv_val.strip()} ")
                    idx += 1

        # Ghép chuỗi và dọn khoảng trắng thừa
        res_str = "".join(translated_blocks)
        res_str = re.sub(r'\s+', ' ', res_str).strip()
        
        # Giải phóng bộ nhớ tạm thời
        del blocks
        del translated_blocks
        gc.collect()

        return res_str
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
        context.close()
