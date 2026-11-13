import os
import io
import re
import json
import time
import math
import base64
import random
import logging
import asyncio
import requests
import subprocess
import numpy as np
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from sqlalchemy.orm import Session
from concurrent.futures import ThreadPoolExecutor, as_completed
from pydub import AudioSegment

from app.config import settings
from app.models import ProjectTask, DialogueSegmentModel, TTSDialogueSegmentModel, SubtitleSegmentModel, LLMMergedSegmentModel
from app.services.task_manager import task_manager
from app.utils.bin_helper import get_ffmpeg_cmd, get_ffprobe_cmd
from app.services.step_04_tts.proxy_manager import proxy_manager
from app.services.step_04_tts.adaptive_trimmer import AdaptiveSilenceTrimmer
from app.services.step_04_tts.timeline_borrower import TimelineBorrower
from app.services.step_04_tts.async_tts_engine import AsyncTTSEngine, split_text_strictly_by_punctuation

logger = logging.getLogger(__name__)

# Đọc Cookie CapCut từ settings (file .env)
DEFAULT_CAPCUT_COOKIE = getattr(settings, "CAPCUT_COOKIE", "") or getattr(settings, "TIKTOK_SESSION_ID", "")

import tempfile

def _load_audio_from_bytes(data: bytes, format: str = "mp3") -> AudioSegment:
    """
    Giải mã âm thanh từ buffer sang AudioSegment siêu tốc & an toàn 100% trên Windows:
    - Sử dụng trực tiếp ffmpeg pipe decode sang WAV thô (tránh lỗi pydub ffprobe JSONDecodeError và seekback pipe:0).
    - Tương thích 100% mọi phiên bản Windows và FFmpeg.
    """
    if not data or len(data) < 100:
        return AudioSegment.silent(duration=200)
    try:
        ffmpeg_bin = get_ffmpeg_cmd()[0]
        cmd = [ffmpeg_bin, "-y", "-i", "pipe:0", "-f", "wav", "pipe:1"]
        res = subprocess.run(cmd, input=data, capture_output=True)
        if res.returncode == 0 and len(res.stdout) > 44:
            return AudioSegment.from_wav(io.BytesIO(res.stdout))
    except Exception:
        pass

    temp_dir = settings.TEMP_TTS_DIR
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        fd, temp_path = tempfile.mkstemp(suffix=f".{format}", dir=str(temp_dir))
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
        return AudioSegment.from_file(temp_path)
    except Exception:
        return AudioSegment.silent(duration=300)
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


# ============================================================
def strip_leading_numbering(text: str) -> str:
    """
    Xóa sạch 100% số thứ tự đầu câu (ví dụ: '1. ', '02. ', '3: ', '4 - ', '[5] ', '(6) ', 'Câu 7: ', '1/100 '):
    - Đảm bảo khi gửi sang CapCut/TikTok TTS, giọng đọc KHÔNG đọc 'Một chấm...', 'Hai chấm...'
    - Giữ nguyên số nếu là số lượng nội dung thực tế (ví dụ: '10 vạn linh thạch', '2024 năm sau').
    """
    if not text:
        return ""
    t = text.strip()
    # 1. Bóc ngoặc vuông / ngoặc tròn / ngoặc nhọn chứa số ở đầu: [1], (1), {1}, 【1】
    t = re.sub(r'^(?:\[\s*\d+\s*\]|\(\s*\d+\s*\)|\{\s*\d+\s*\}|【\s*\d+\s*】)\s*[\.\:\-\–\—\s]*', '', t)
    # 2. Bóc các tiền tố dạng: Câu 1:, Thoại 1:, Đoạn 1:, STT 1:, Line 1:
    t = re.sub(r'^(?:câu|thoại|đoạn|stt|dòng|line)\s*\d+\s*[\.\:\-\–\—\)\/\]\s]*\s*', '', t, flags=re.IGNORECASE)
    # 3. Bóc dạng phân số/tổng số câu: 1/100 
    t = re.sub(r'^\d+\/\d+\s*[\.\:\-\–\—\s]*', '', t)
    # 4. Bóc số thứ tự đầu câu kèm dấu phân cách: 1. , 12. , 1: , 1 - , 1) 
    t = re.sub(r'^\d+\s*[\.\:\-\–\—\)\/\]]+\s*', '', t)
    # 5. Dọn dẹp dấu câu thừa còn sót lại ở đầu chuỗi sau khi bóc số
    t = re.sub(r'^[^\w\s\(\[\{]+', '', t).strip()
    return t


def normalize_tts_text(text: str) -> str:
    """Chuẩn hóa ký tự trước khi gửi lên API TTS."""
    if not text:
        return ""
    text = strip_leading_numbering(text)
    
    # 1. Chuyển đổi các biến thể của "vân vân" (v.v., v.v, v-v, v/v, vv...) để tránh đọc thành số
    text = re.sub(r'v\.v\.?', 'vân vân', text, flags=re.IGNORECASE)
    text = re.sub(r'\b(v[\-\/]v|vv)\b', 'vân vân', text, flags=re.IGNORECASE)
    
    # 2. Chuyển đổi "vi", "Vi", "VI" đứng độc lập thành "vy", "Vy" tránh TTS đọc thành số La Mã VI (sáu)
    text = re.sub(r'\bVI\b', 'Vy', text)
    text = re.sub(r'\bVi\b', 'Vy', text)
    text = re.sub(r'\bvi\b', 'vy', text)

    # 3. Chuẩn hóa phát âm chữ "e" trong từ ghép và thán từ (tránh ByteDance TTS đọc nhầm thành "Em"):
    text = re.sub(r'\b[eE]\s+ngại\b', 'è ngại', text)
    text = re.sub(r'\b[eE]\s+rằng\b', 'è rằng', text)
    text = re.sub(r'\b[eE]\s+là\b', 'è là', text)
    text = re.sub(r'\b[eE]\s+sợ\b', 'lo sợ', text)
    text = re.sub(r'\b[eE]\s+dè\b', 'dè dặt', text)
    text = re.sub(r'\b[eE]\s+thẹn\b', 'thẹn thùng', text)
    text = re.sub(r'\b[eE]\s*\!+', 'Ê!', text)
    text = re.sub(r'\b[eE]\s*\?+', 'Ể?', text)
    text = re.sub(r'\b[eE]\s*(?:\.{2,}|…)', 'Ơ...', text)

    # 4. Chuẩn hóa chuỗi "aa", "aaa", "Aa", "Aaa"... thành "a' a'", "a' a' a'" (theo yêu cầu ngữ âm)
    def _expand_repeated_a(m):
        chars = m.group(0)
        return " ".join([f"{c}'" for c in chars])
    text = re.sub(r'\b[aA]{2,}\b', _expand_repeated_a, text)

    # 5. Tên riêng tiền tố "A" (A Vân, A Cường, A Bảo, A Dũng...):
    # Dùng A' để tránh TTS đọc thành "Anh Vân", đồng thời giữ nguyên âm A không dấu
    text = re.sub(r'\bA\s+([A-ZÀ-Ỹ][a-zà-ỹ]*)', r"A' \1", text)

    # 6. Thán từ "A" độc lập (tránh đọc thành "Anh"):
    text = re.sub(r'\b[aA]\s+ha\b', 'À ha', text, flags=re.IGNORECASE)
    text = re.sub(r'\b[aA]\s*\!+', 'Á!', text)
    text = re.sub(r'\b[aA]\s*(?:\.{2,}|…)', 'À...', text)
    text = re.sub(r'\b[aA]\s*\,', 'À,', text)

    text = re.sub(r'[\r\n\t]+', ' ', text)
    text = re.sub(r'["“”„‟«»]', ' ', text)
    text = re.sub(r'[\(\)\[\]\{\}]', ' ', text)
    text = re.sub(r'[/／\\]+', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()

    # 3. Chuẩn hóa dấu câu ở GIỮA chunk: Chuyển toàn bộ các dấu kết câu (. ! ? : ... …) ở giữa câu thành dấu chấm phẩy ";"
    # để tránh làm hỏng âm/nghẹn giọng của TTS CapCut/TikTok giữa chừng.
    text = re.sub(r'(?:\.{3,}|…)', ';', text)
    text = re.sub(r'[\.\!\?\:\;]+(?=\s|$)', ';', text)
    text = re.sub(r'\s*;\s*', '; ', text)
    text = re.sub(r';+', ';', text).strip()

    # Dọn sạch dấu câu dở dang ở cuối
    text = re.sub(r'[\.\,\!\?\…\:\;\—\-\_\s]+$', '', text).strip()

    # Chuẩn hóa kết thúc: kết thúc bằng dấu chấm phẩy ";"
    return f"{text};" if text else ""


def sanitize_to_vietnamese(text: Optional[str], fallback_orig: Optional[str] = None) -> str:
    """Đảm bảo chuỗi đưa vào đọc là tiếng Việt chuẩn 100%, quét sạch 100% cú pháp JSON, tag rác."""
    t = (text or "").strip()
    if not t:
        t = (fallback_orig or "").strip()
    
    # Bóc tách nếu bị dính cú pháp JSON {"i": ..., "vi": "..."} hoặc {"vi": "..."}
    if "{" in t or '"vi"' in t or "'vi'" in t:
        m_vi = re.search(r'["\']vi["\']\s*:\s*["\']([^"\']+)["\']', t)
        if m_vi:
            t = m_vi.group(1).strip()
        else:
            try:
                import json
                json_m = re.search(r'\{.*\}', t)
                if json_m:
                    parsed = json.loads(json_m.group(0))
                    if isinstance(parsed, dict) and "vi" in parsed:
                        t = str(parsed["vi"]).strip()
            except Exception:
                pass
        t = re.sub(r'^\s*\{+\s*["\']?i["\']?\s*:\s*\d+\s*,\s*["\']?vi["\']?\s*:\s*["\']?', '', t)
        t = re.sub(r'["\']?\s*\}+\s*[\.\,\;]?\s*$', '', t)

    # Xóa các thẻ HTML/XML nếu có (vd: <s>, </s>, <p>...)
    t = re.sub(r'<[^>]+>', '', t).strip()
    return t



_GLOBAL_SESSION = requests.Session()
_GLOBAL_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
})

_AUDIO_CACHE: Dict[str, AudioSegment] = {}


class CapCutTTSService:
    VOICE_MAP = {
        "BV074_streaming": "Nữ: Cô Gái Hoạt Ngôn (Trẻ trung, Review Trend - Mặc định)",
        "vi-VN-HoaiMyNeural": "Nữ: Hoài My (Truyền cảm, Ngọt ngào - Mới)",
        "hoaimy": "Nữ: Hoài My (Truyền cảm, Ngọt ngào - Mới)",
        "BV075_streaming": "Nam: Hào Sảng (Trầm ấm, Thuyết minh phim)",
        "BV076_streaming": "Nam Miền Nam (Trầm Ấm)",
        "BV077_streaming": "Nữ Miền Nam (Ngọt Ngào)",
        "vi_male_standard": "Nam Tiêu Chuẩn",
        "vi_female_standard": "Nữ Tiêu Chuẩn",
    }

    @classmethod
    def get_supported_voices(cls) -> List[Dict[str, str]]:
        return [{"code": k, "name": v} for k, v in cls.VOICE_MAP.items()]

    @classmethod
    def trim_audio_silence(cls, audio_segment: AudioSegment, silence_thresh: int = -40, chunk_size: int = 10) -> AudioSegment:
        if len(audio_segment) < 100:
            return audio_segment
        try:
            from pydub.silence import detect_leading_silence
            start_trim = detect_leading_silence(audio_segment, silence_threshold=silence_thresh, chunk_size=chunk_size)
            end_trim = detect_leading_silence(audio_segment.reverse(), silence_threshold=silence_thresh, chunk_size=chunk_size)
            duration = len(audio_segment)
            trimmed = audio_segment[start_trim:duration - end_trim]
            return trimmed if len(trimmed) > 50 else audio_segment
        except Exception:
            return audio_segment

    @classmethod
    def synthesize_sentence(
        cls,
        text: str,
        voice_code: str = "BV074_streaming",
        cookie: str = None,
        apply_mastering: bool = True,
        playback_speed: float = 1.0,
        max_retries: int = 3
    ) -> Optional[AudioSegment]:
        clean_text = normalize_tts_text(text)
        if not clean_text:
            return None

        cache_key = f"{voice_code}_{clean_text}_{playback_speed}"
        if cache_key in _AUDIO_CACHE:
            return _AUDIO_CACHE[cache_key]

        actual_cookie = cookie or DEFAULT_CAPCUT_COOKIE

        # =====================================================================
        # GHI CHÚ LỊCH SỬ (OLD TQ DRIVER):
        # Endpoint cũ TQ (faceu.mobi) thường xuyên bị timeout/lỗi DNS tại VN:
        # endpoint = "https://lf16-capcut.faceu.mobi/api/v1/tts/invoke"
        # =====================================================================
        # HIỆN TẠI: Sử dụng CapCut Global Singapore (editor-api-sg.capcutapi.com)
        # Tốc độ cao qua Akamai CDN Đông Nam Á, hỗ trợ 100% tài khoản & vùng Việt Nam
        from app.services.step_04_tts.capcut_api import CapCutClient
        from app.services.step_04_tts.capcut_api.models import DeviceConfig

        actual_cookie = cookie or DEFAULT_CAPCUT_COOKIE
        dev = DeviceConfig.create_random()
        client = CapCutClient(device=dev, cookie=actual_cookie)

        for attempt in range(1, max_retries + 1):
            try:
                url, headers, body_text = client.build_tts_new_request([clean_text], voice=voice_code)
                res = _GLOBAL_SESSION.post(url, headers=headers, data=body_text.encode("utf-8"), timeout=15)
                if res.status_code == 200:
                    data = res.json()
                    tasks = (data.get("data") or {}).get("tasks") or []
                    if tasks:
                        task_id = tasks[0].get("id")
                        token = tasks[0].get("token")
                        if task_id and token:
                            # Polling lấy speech_url từ CapCut Global SAMI
                            for _ in range(12):
                                time.sleep(0.4)
                                q_url, q_headers, q_body = client.build_query_request(task_id, token, mode="tts")
                                q_res = _GLOBAL_SESSION.post(q_url, headers=q_headers, data=q_body.encode("utf-8"), timeout=10)
                                if q_res.status_code == 200:
                                    q_data = q_res.json()
                                    q_tasks = (q_data.get("data") or {}).get("tasks") or []
                                    if q_tasks and q_tasks[0].get("status") in ("success", "succeed"):
                                        payload_raw = q_tasks[0].get("payload") or "{}"
                                        payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
                                        subs = payload.get("audio_subtitles") or []
                                        if subs and subs[0].get("speech_url"):
                                            dl_res = _GLOBAL_SESSION.get(subs[0]["speech_url"], timeout=12)
                                            if dl_res.status_code == 200 and len(dl_res.content) > 100:
                                                seg = _load_audio_from_bytes(dl_res.content, format="mp3")
                                                seg = cls.trim_audio_silence(seg)
                                                if len(seg) > 100:
                                                    _AUDIO_CACHE[cache_key] = seg
                                                    return seg
                                        break
                                    elif q_tasks and q_tasks[0].get("status") == "failed":
                                        break
                time.sleep(0.15)
            except Exception as exc:
                err_str = str(exc)
                if attempt == max_retries:
                    logger.error(f"[CapCut Global TTS] Thất bại câu: {clean_text[:40]} -> {err_str}")

        # Fallback đồng giọng sang TikTok TTS API nếu là mã giọng BV* (giữ nguyên 100% giọng Cô Gái Hoạt Ngôn BV074_streaming)
        if "multi_" not in voice_code and "Neural" not in voice_code:
            try:
                from app.services.step_04_tts.tiktok_tts_service import TikTokTTSService
                tt_seg = TikTokTTSService.synthesize_sentence(
                    text=clean_text,
                    voice_code=voice_code,
                    apply_mastering=False,
                    playback_speed=1.0,
                    max_retries=2
                )
                if tt_seg and len(tt_seg) > 100:
                    _AUDIO_CACHE[cache_key] = tt_seg
                    return tt_seg
            except Exception:
                pass
        return None

    @classmethod
    def synthesize_chunk(cls, text: str, voice_code: str = "BV074_streaming", cookie: str = None) -> Optional[AudioSegment]:
        return cls.synthesize_sentence(text=text, voice_code=voice_code, cookie=cookie)

    @classmethod
    def time_stretch_by_factor(cls, audio_seg: AudioSegment, speed_factor: float) -> AudioSegment:
        if abs(speed_factor - 1.0) < 0.02:
            return audio_seg
        try:
            ffmpeg_bin = get_ffmpeg_cmd()[0]
            filters = []
            rem = float(speed_factor)
            while rem > 2.0:
                filters.append("atempo=2.0")
                rem /= 2.0
            while rem < 0.5:
                filters.append("atempo=0.5")
                rem /= 0.5
            filters.append(f"atempo={rem:.4f}")

            cmd = [
                ffmpeg_bin, "-y",
                "-f", "s16le", "-ar", str(audio_seg.frame_rate), "-ac", str(audio_seg.channels),
                "-i", "pipe:0",
                "-filter:a", ",".join(filters),
                "-f", "s16le", "-ar", str(audio_seg.frame_rate), "-ac", str(audio_seg.channels),
                "pipe:1"
            ]
            res = subprocess.run(cmd, input=audio_seg.raw_data, capture_output=True, timeout=10)
            if res.returncode == 0 and len(res.stdout) > 0:
                return AudioSegment(
                    data=res.stdout,
                    sample_width=audio_seg.sample_width,
                    frame_rate=audio_seg.frame_rate,
                    channels=audio_seg.channels
                )
        except Exception:
            pass

        # Fallback sang pydub speedup
        try:
            return audio_seg.speedup(playback_speed=speed_factor, chunk_size=50, crossfade=15)
        except Exception:
            return audio_seg

    @classmethod
    def prepare_unmerged_chunk_offsets(
        cls,
        dialogues: List[Any],
        raw_results_map: Dict[int, AudioSegment],
        engine_sub_timings: Optional[Dict[int, List[Dict[str, Any]]]] = None,
    ) -> Dict[int, List[Dict[str, Any]]]:
        """Tạo chunk_sub_offsets 1-1 cho từng câu TTS không gộp, giữ old_tts_index chuẩn xác."""
        sub_timings_map = engine_sub_timings or {}
        offsets: Dict[int, List[Dict[str, Any]]] = {}
        for d in dialogues:
            tts_idx = getattr(d, "tts_index", getattr(d, "index", None))
            if tts_idx is None:
                continue
            d_id = getattr(d, "id", None)
            eng_t = sub_timings_map.get(d_id) if d_id is not None else None
            if eng_t:
                offsets[tts_idx] = [
                    {**dict(item), "old_tts_index": item.get("old_tts_index", tts_idx)}
                    for item in eng_t
                ]
            else:
                seg = raw_results_map.get(d_id) if d_id is not None else None
                offsets[tts_idx] = [{
                    "old_tts_index": tts_idx,
                    "raw_start_ms": 0,
                    "raw_end_ms": len(seg) if seg else 0,
                }]
        return offsets

    @classmethod
    def ensure_unmerged_tts_script(
        cls,
        project_id: int,
        db: Session,
        task_id: Optional[str] = None,
    ) -> bool:
        """
        Khi tắt gộp audio sau TTS (TTS_MERGE_CHUNKS=False):
        - Nếu TTS_AUDIO trong DB từng bị gộp gộp nhỏ từ lần chạy trước (số câu < LLM_MERGED),
          tự động khôi phục 100% kịch bản TTS_AUDIO & SUB_KARAOKE sạch từ bảng LLM_MERGED.
        - Nếu SUB_KARAOKE bị thiếu hoặc thiếu source_indices (cũ), tái lập phụ đề sạch theo dấu câu.
        """
        llm_rows = db.query(LLMMergedSegmentModel).filter(
            LLMMergedSegmentModel.task_id == project_id
        ).order_by(LLMMergedSegmentModel.index.asc()).all()
        tts_rows = db.query(TTSDialogueSegmentModel).filter(
            TTSDialogueSegmentModel.task_id == project_id
        ).order_by(TTSDialogueSegmentModel.tts_index.asc()).all()
        sub_rows = db.query(SubtitleSegmentModel).filter(
            SubtitleSegmentModel.task_id == project_id
        ).order_by(SubtitleSegmentModel.index.asc()).all()

        need_restore_tts = bool(llm_rows) and (not tts_rows or len(tts_rows) != len(llm_rows))
        if need_restore_tts:
            for obj in list(db.identity_map.values()):
                if isinstance(obj, TTSDialogueSegmentModel):
                    db.expunge(obj)
            db.query(TTSDialogueSegmentModel).filter(
                TTSDialogueSegmentModel.task_id == project_id
            ).delete(synchronize_session=False)
            db.commit()

            asr_rows = db.query(DialogueSegmentModel).filter(
                DialogueSegmentModel.task_id == project_id
            ).all()
            asr_by_idx = {a.index: a for a in asr_rows}

            restored_tts = []
            for idx_num, lm in enumerate(llm_rows, start=1):
                src_str = (lm.source_indices or str(lm.index)).strip()
                src_ids = [int(x) for x in src_str.split(",") if x.strip().isdigit()]
                for s_id in src_ids:
                    if s_id in asr_by_idx:
                        asr_by_idx[s_id].tts_index = idx_num

                entry = TTSDialogueSegmentModel(
                    task_id=project_id,
                    tts_index=idx_num,
                    batch_index=getattr(lm, "batch_index", 1) or 1,
                    src_start_index=getattr(lm, "src_start_index", None) or (src_ids[0] if src_ids else idx_num),
                    src_end_index=getattr(lm, "src_end_index", None) or (src_ids[-1] if src_ids else idx_num),
                    src_indices=src_str,
                    orig_count=getattr(lm, "sub_count", None) or max(1, len(src_ids)),
                    start_time=round(float(lm.start_time), 2),
                    end_time=round(float(lm.end_time), 2),
                    duration=round(max(0.1, float(lm.end_time) - float(lm.start_time)), 2),
                    original_text=lm.original_text or "",
                    translated_text=lm.translated_text or "",
                    speaker=lm.speaker or "DẪN CHUYỆN",
                    status="TRANSLATED",
                )
                db.add(entry)
                restored_tts.append(entry)
            db.commit()
            tts_rows = restored_tts
            msg = f"🔄 [TTS Restore] Đã khôi phục {len(tts_rows)} câu gốc từ LLM_MERGED (thay cho bản gộp cũ) vì TTS_MERGE_CHUNKS=False."
            if task_id:
                task_manager.add_log(task_id, msg, "cyan")
            logger.info(msg)

        need_rebuild_subs = need_restore_tts or (
            bool(tts_rows) and (not sub_rows or any(not getattr(sr, "source_indices", None) for sr in sub_rows))
        )
        if need_rebuild_subs and tts_rows:
            for obj in list(db.identity_map.values()):
                if isinstance(obj, SubtitleSegmentModel):
                    db.expunge(obj)
            db.query(SubtitleSegmentModel).filter(
                SubtitleSegmentModel.task_id == project_id
            ).delete(synchronize_session=False)
            db.commit()

            sub_counter = 0
            for t in tts_rows:
                t_idx = getattr(t, "tts_index", getattr(t, "index", 1))
                vi_str = (t.translated_text or "").strip()
                sub_pieces = split_text_strictly_by_punctuation(
                    vi_str,
                    max_words=22,
                    max_chars=180,
                    allow_hard_word_cut=False,
                )
                if not sub_pieces and vi_str:
                    sub_pieces = [vi_str]
                if not sub_pieces:
                    continue

                s_time = float(t.start_time or 0.0)
                e_time = float(t.end_time or (s_time + 1.0))
                tts_dur = max(0.1, e_time - s_time)
                tot_words = sum(max(1, len(sp.split())) for sp in sub_pieces)
                curr_sub_s = s_time
                for sp_idx, sp in enumerate(sub_pieces):
                    sub_counter += 1
                    w_cnt = max(1, len(sp.split()))
                    dur_sp = round(tts_dur * (w_cnt / tot_words), 2)
                    sp_start = round(curr_sub_s, 2)
                    sp_end = round(e_time if sp_idx == len(sub_pieces) - 1 else curr_sub_s + dur_sp, 2)
                    curr_sub_s = sp_end
                    db.add(SubtitleSegmentModel(
                        task_id=project_id,
                        sub_index=sub_counter,
                        tts_index=t_idx,
                        source_indices=str(t_idx),
                        batch_index=getattr(t, "batch_index", 1) or 1,
                        start_time=sp_start,
                        end_time=sp_end,
                        duration=round(max(0.1, sp_end - sp_start), 2),
                        text_vi=sp,
                        text_zh=t.original_text or "",
                        speaker=t.speaker or "DẪN CHUYỆN",
                        status="TRANSLATED",
                    ))
            db.commit()

        return need_restore_tts

    @classmethod
    def merge_continuous_audio_dialogues(
        cls,
        dialogues: List[Any],
        raw_results_map: Dict[int, AudioSegment],
        db: Session,
        project_id: int,
        task_id: str,
        gap_threshold: float = 0.85,
        enable_merge: Optional[bool] = None,
    ) -> Tuple[List[Any], Dict[int, AudioSegment], Dict[int, List[Dict[str, Any]]]]:
        """
        Gộp các file âm thanh của các câu liên tiếp CÙNG MỘT NGƯỜI NÓI (Audio Continuous Chunk Merger):
        - Tôn trọng cấu hình settings.TTS_MERGE_CHUNKS: khi False, giữ nguyên từng câu khớp 100% khung hình.
        - Khi bật True: quét gộp các câu liên tiếp cùng vai đọc (gap < gap_threshold) và giữ map offset.
        """
        should_merge = getattr(settings, "TTS_MERGE_CHUNKS", False) if enable_merge is None else bool(enable_merge)
        engine_sub_timings = AsyncTTSEngine.get_last_sub_timings() or {}

        if not should_merge:
            offsets = cls.prepare_unmerged_chunk_offsets(dialogues, raw_results_map, engine_sub_timings)
            if task_id and dialogues:
                task_manager.add_log(
                    task_id,
                    f"⏹️ [TTS Audio Merger] Đã tắt gộp sau TTS (TTS_MERGE_CHUNKS=False) — giữ nguyên {len(dialogues)} câu khớp hình.",
                    "cyan",
                )
            return dialogues, raw_results_map, offsets

        if not dialogues or not raw_results_map or len(dialogues) <= 1:
            return dialogues, raw_results_map, cls.prepare_unmerged_chunk_offsets(dialogues, raw_results_map, engine_sub_timings)

        sorted_dialogues = sorted(dialogues, key=lambda x: (float(x.start_time or 0.0), getattr(x, 'index', 0)))
        merged_dialogues = []
        merged_results_map = {}
        tts_index_remap = {}  # old_tts_index -> new_tts_index
        chunk_sub_offsets = {}  # target_chunk_idx -> list of {old_tts_index, raw_start_ms, raw_end_ms}
        merged_count = 0
        engine_sub_timings = AsyncTTSEngine.get_last_sub_timings() or {}

        curr = sorted_dialogues[0]
        curr_audio = raw_results_map.get(curr.id)
        curr_sub_tts_indices = [curr.tts_index]
        curr_sub_offsets = [{
            "old_tts_index": curr.tts_index,
            "raw_start_ms": 0,
            "raw_end_ms": len(curr_audio) if curr_audio else 0
        }]

        for i in range(1, len(sorted_dialogues)):
            next_d = sorted_dialogues[i]
            next_audio = raw_results_map.get(next_d.id)
            if not next_audio:
                continue

            role_curr = (curr.speaker or "").strip().lower()
            role_next = (next_d.speaker or "").strip().lower()
            same_role = (role_curr == role_next) and bool(role_curr)
            gap = round(float(next_d.start_time) - float(curr.end_time), 3)

            if same_role and gap < gap_threshold:
                merged_count += 1
                # 1. Nối mảng sóng âm thanh pydub
                if gap > 0:
                    pause_ms = min(int(gap * 1000), 850)
                    curr_audio = curr_audio + AudioSegment.silent(duration=pause_ms) + next_audio
                    start_ms = curr_sub_offsets[-1]["raw_end_ms"] + pause_ms
                else:
                    curr_audio = curr_audio + next_audio
                    start_ms = curr_sub_offsets[-1]["raw_end_ms"]

                end_ms = start_ms + len(next_audio)
                curr_sub_offsets.append({
                    "old_tts_index": next_d.tts_index,
                    "raw_start_ms": start_ms,
                    "raw_end_ms": end_ms
                })

                # 2. Cập nhật văn bản
                text_a = (curr.translated_text or "").strip()
                text_b = (next_d.translated_text or "").strip()
                if not re.search(r'[\.\!\?\,\:\;\…\—\-]$', text_a):
                    text_a = f"{text_a}."
                curr.translated_text = f"{text_a} {text_b}"

                # 3. Tính time gộp tổng theo số câu gộp và cập nhật lại start, end y như code gộp chữ trước
                curr.end_time = max(float(curr.end_time), float(next_d.end_time))
                curr.duration = round(float(curr.end_time) - float(curr.start_time), 2)

                # 4. Gộp src_indices & orig_count
                if getattr(next_d, 'src_indices', None):
                    curr.src_indices = f"{curr.src_indices or ''},{next_d.src_indices}".strip(",")
                curr.orig_count = (getattr(curr, 'orig_count', 1) or 1) + (getattr(next_d, 'orig_count', 1) or 1)

                curr_sub_tts_indices.append(next_d.tts_index)
            else:
                curr_vi = (curr.translated_text or "").strip()
                if not re.search(r'[\.\!\?\,\:\;\…\—\-]$', curr_vi):
                    curr.translated_text = f"{curr_vi}."

                target_chunk_idx = len(merged_dialogues) + 1
                for old_idx in curr_sub_tts_indices:
                    tts_index_remap[old_idx] = target_chunk_idx
                if len(curr_sub_tts_indices) == 1 and curr.id in engine_sub_timings and len(engine_sub_timings[curr.id]) > 1:
                    chunk_sub_offsets[target_chunk_idx] = engine_sub_timings[curr.id]
                else:
                    chunk_sub_offsets[target_chunk_idx] = curr_sub_offsets

                merged_dialogues.append(curr)
                merged_results_map[curr.id] = curr_audio

                curr = next_d
                curr_audio = next_audio
                curr_sub_tts_indices = [curr.tts_index]
                curr_sub_offsets = [{
                    "old_tts_index": curr.tts_index,
                    "raw_start_ms": 0,
                    "raw_end_ms": len(curr_audio) if curr_audio else 0
                }]

        # Chốt chunk cuối cùng
        curr_vi = (curr.translated_text or "").strip()
        if not re.search(r'[\.\!\?\,\:\;\…\—\-]$', curr_vi):
            curr.translated_text = f"{curr_vi}."
        target_chunk_idx = len(merged_dialogues) + 1
        for old_idx in curr_sub_tts_indices:
            tts_index_remap[old_idx] = target_chunk_idx
        if len(curr_sub_tts_indices) == 1 and curr.id in engine_sub_timings and len(engine_sub_timings[curr.id]) > 1:
            chunk_sub_offsets[target_chunk_idx] = engine_sub_timings[curr.id]
        else:
            chunk_sub_offsets[target_chunk_idx] = curr_sub_offsets
        merged_dialogues.append(curr)
        merged_results_map[curr.id] = curr_audio

        if merged_count > 0:
            # 1. Dọn sạch khỏi Identity Map và Xóa các dòng cũ trong DB để tránh xung đột UniqueConstraint
            for obj in list(db.identity_map.values()):
                if isinstance(obj, TTSDialogueSegmentModel):
                    db.expunge(obj)

            try:
                db.query(TTSDialogueSegmentModel).filter(
                    TTSDialogueSegmentModel.task_id == project_id
                ).delete(synchronize_session=False)
                db.commit()
            except Exception as e:
                db.rollback()
                logger.warning(f"Lỗi dọn sạch tts_dialogue_segments cũ: {e}")

            # 2. Lưu lại danh sách 100% chunk mới đã gộp vào Database (tts_index từ 1 đến N)
            fresh_entries = []
            fresh_results_map = {}
            for new_idx, chunk in enumerate(merged_dialogues, start=1):
                entry = TTSDialogueSegmentModel(
                    task_id=project_id,
                    tts_index=new_idx,
                    batch_index=getattr(chunk, 'batch_index', 1) or 1,
                    src_start_index=getattr(chunk, 'src_start_index', new_idx) or new_idx,
                    src_end_index=getattr(chunk, 'src_end_index', new_idx) or new_idx,
                    src_indices=getattr(chunk, 'src_indices', str(new_idx)) or str(new_idx),
                    orig_count=getattr(chunk, 'orig_count', 1) or 1,
                    start_time=round(float(chunk.start_time), 2),
                    end_time=round(float(chunk.end_time), 2),
                    duration=round(float(chunk.duration), 2),
                    original_text=getattr(chunk, 'original_text', '') or '',
                    translated_text=chunk.translated_text,
                    speaker=chunk.speaker,
                    status="PENDING"
                )
                db.add(entry)
                fresh_entries.append(entry)

            try:
                db.commit()
                # Sau khi commit, SQLite đã gán id tự tăng mới cho từng entry
                for new_idx, entry in enumerate(fresh_entries, start=1):
                    entry.index = entry.tts_index
                    old_chunk = merged_dialogues[new_idx - 1]
                    fresh_results_map[entry.id] = merged_results_map.get(old_chunk.id)
                merged_dialogues = fresh_entries
                merged_results_map = fresh_results_map
            except Exception as e:
                db.rollback()
                logger.warning(f"Lỗi commit fresh_entries cho TTSDialogueSegmentModel: {e}")

            # 3. Cập nhật liên kết tts_index cho DialogueSegmentModel (ASR) và SubtitleSegmentModel (phụ đề Karaoke)
            try:
                db_asrs = db.query(DialogueSegmentModel).filter(
                    DialogueSegmentModel.task_id == project_id
                ).all()
                for asr in db_asrs:
                    if asr.tts_index in tts_index_remap:
                        asr.tts_index = tts_index_remap[asr.tts_index]

                db_subs = db.query(SubtitleSegmentModel).filter(
                    SubtitleSegmentModel.task_id == project_id
                ).all()
                for sub in db_subs:
                    if sub.tts_index in tts_index_remap:
                        sub.tts_index = tts_index_remap[sub.tts_index]

                db.commit()
            except Exception as e:
                db.rollback()
                logger.warning(f"Lỗi cập nhật tts_index cho DialogueSegmentModel / SubtitleSegmentModel: {e}")

            # 4. Cập nhật file script_tts.json
            try:
                from app.config import get_project_dirs
                p_dirs = get_project_dirs(project_id)
                script_tts_path = p_dirs["tts_voiceover"] / "script_tts.json"
                if script_tts_path.parent.exists():
                    json_dialogues = []
                    for d in merged_dialogues:
                        json_dialogues.append({
                            "tts_index": d.tts_index,
                            "start": round(float(d.start_time), 2),
                            "end": round(float(d.end_time), 2),
                            "duration": round(float(d.duration), 2),
                            "text": d.translated_text,
                            "speaker": d.speaker,
                            "orig_count": getattr(d, 'orig_count', 1),
                            "src_indices": getattr(d, 'src_indices', str(d.tts_index))
                        })
                    with open(script_tts_path, "w", encoding="utf-8") as f:
                        json.dump(json_dialogues, f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.warning(f"Lỗi cập nhật script_tts.json sau khi gộp audio: {e}")

            orig_cnt = len(dialogues)
            new_cnt = len(merged_dialogues)
            log_msg = f"🤝 [Continuous Audio Merger] Đã gộp thành công {merged_count} câu thoại liên tiếp cùng người nói ({orig_cnt} câu lẻ ➔ {new_cnt} chunk âm thanh liền mạch)!"
            task_manager.add_log(task_id, log_msg, "emerald")
            try:
                print(f"\n{log_msg}\n", flush=True)
            except Exception:
                safe_msg = f"[Continuous Audio Merger] Da gop thanh cong {merged_count} cau thoai lien tiep ({orig_cnt} cau le -> {new_cnt} chunk lien mach)!"
                print(f"\n{safe_msg}\n", flush=True)
        else:
            for new_idx, d in enumerate(merged_dialogues, start=1):
                d.tts_index = new_idx
                d.index = new_idx
            try:
                print(f"ℹ️ [Continuous Audio Merger] Không có câu liên tiếp nào cùng người nói cần gộp ({len(dialogues)} câu giữ nguyên).", flush=True)
            except Exception:
                pass

        return merged_dialogues, merged_results_map, chunk_sub_offsets

    @classmethod
    def produce_project_voiceover(
        cls,
        task_id: str,
        project_id: int,
        db: Session,
        voice_code: str = "BV074_streaming",
        session_id: str = None,
        apply_mastering: bool = True,
        playback_speed: float = 1.0,
        auto_fit_timeline: bool = True,
        max_workers: int = None,
        force_regenerate: bool = True,
        use_exact_timeline_fit: bool = True  # [NOTE TEST PHƯƠNG ÁN 1]: Khớp 100% timecode gốc, tắt cân bằng & cắt xén
    ) -> Dict[str, Any]:
        """
        Quy trình sản xuất âm thanh CapCut TTS ĐỒNG TỐC VỚI HÀNG ĐỢI ƯU TIÊN ĐỘNG:
        - Sử dụng AsyncTTSEngine Priority Queue chạy song song đa luồng.
        - Tự động điều phối 64-96 luồng và ưu tiên vá lỗi tức thì.
        - Ghép Master Audio Track bằng NumPy Buffer siêu tốc.
        """
        project = db.query(ProjectTask).filter(ProjectTask.id == project_id).first()
        if not project:
            raise ValueError(f"Không tìm thấy Project ID #{project_id}")

        # Đảm bảo nếu TTS_MERGE_CHUNKS=False mà TTS_AUDIO trong DB từng bị gộp cũ thì khôi phục từ LLM_MERGED
        if not getattr(settings, "TTS_MERGE_CHUNKS", False):
            cls.ensure_unmerged_tts_script(project_id=project_id, db=db, task_id=task_id)

        # BẮT BUỘC: Đọc từ bảng câu TTS (tts_dialogue_segments)
        tts_dialogues_db = db.query(TTSDialogueSegmentModel).filter(
            TTSDialogueSegmentModel.task_id == project_id
        ).order_by(TTSDialogueSegmentModel.tts_index.asc()).all()

        if not tts_dialogues_db:
            raise ValueError(f"Project #{project_id} chưa có kịch bản thoại gộp TTS (TTSDialogueSegmentModel)! Vui lòng chạy Dịch thuật trước để tạo kịch bản lồng tiếng hoàn chỉnh.")

        dialogues = tts_dialogues_db

        raw_workers = max_workers or getattr(settings, 'TTS_MAX_WORKERS', 128)
        try:
            num_workers = int(raw_workers)
        except Exception:
            num_workers = 128
        num_workers = max(1, min(128, num_workers))

        safe_title = "".join(c for c in project.title if c.isalnum() or c in (' ', '_', '-')).strip() or f"project_{project.id}"
        from app.config import get_project_dirs
        p_dirs = get_project_dirs(project.id)
        master_voice_file = p_dirs["root"] / f"{project.video_id}_voiceover.mp3"
        master_voice_file.parent.mkdir(parents=True, exist_ok=True)

        # Luôn xóa file voiceover cũ nếu có để tạo mới 100%, tuyệt đối không lẫn bản cũ
        if master_voice_file.exists():
            try:
                master_voice_file.unlink(missing_ok=True)
            except Exception:
                pass

        task_manager.add_log(task_id, f"🎙️ BẮT ĐẦU TẠO ÂM THANH CAPCUT CLOUD TTS (Project #{project_id}: {project.title})", "purple")
        task_manager.add_log(task_id, f"   Động cơ: CapCut Cloud TTS (ByteDance SAMI) | Giọng: {voice_code} | Luồng xử lý: {num_workers} Async Workers", "cyan")

        project_audio_dir = settings.TEMP_TTS_DIR / project.video_id
        segments_dir = project_audio_dir / "segments"
        segments_dir.mkdir(parents=True, exist_ok=True)

        total_video_ms = int((project.duration or 0.0) * 1000)
        if total_video_ms == 0 and dialogues:
            total_video_ms = int(dialogues[-1].end_time * 1000) + 1000

        total_count = len(dialogues)
        raw_results_map = {}

        # =====================================================================
        # PHA 1: TẢI TOÀN BỘ ÂM THANH QUA DYNAMIC IN-FLIGHT PRIORITY QUEUE
        # =====================================================================
        proxy_count = proxy_manager.count
        proxy_info = f" | Proxy: {proxy_count} Proxies xoay vòng" if proxy_count > 0 else " | Proxy: Direct (Không dùng proxy)"
        task_manager.add_log(task_id, f"⚡ [Priority Queue] Đang tải {total_count} câu thoại CapCut ({num_workers} luồng xử lý ưu tiên{proxy_info})...", "cyan")

        # Chuẩn bị dữ liệu thoại
        dialogues_data = []
        silent_dialogue_ids = set()
        for d in dialogues:
            raw_text = sanitize_to_vietnamese(
                d.translated_text,
                fallback_orig=(getattr(d, 'clean_text', None) or d.original_text)
            ).strip()
            # Chuẩn hóa toàn diện: đổi v.v, vv thành vân vân; vi, Vi, VI thành vy, Vy tránh đọc thành số 6; xóa số thứ tự và dấu thừa
            text_to_speak = normalize_tts_text(raw_text)

            # Phải có ít nhất 1 ký tự chữ cái hoặc số để API TTS có thể đọc
            if text_to_speak and any(c.isalnum() for c in text_to_speak):
                dialogues_data.append((d.id, text_to_speak))
            else:
                # Câu chỉ là dấu chấm '.' hoặc khoảng lặng không lời -> tạo đệm im lặng, không gửi lên API
                silent_dialogue_ids.add(d.id)

        if silent_dialogue_ids:
            task_manager.add_log(task_id, f"ℹ️ Phát hiện {len(silent_dialogue_ids)} câu là khoảng lặng/dấu chấm lẻ, tự động tạo đệm âm thanh im lặng chuẩn xác.", "cyan")

        def _update_progress(completed_count, total_target):
            progress_pct = int((completed_count / total_target) * 70)
            task_manager.update_task(task_id, progress=progress_pct, stage=f"TTS Priority Queue: {completed_count}/{total_target}")

        def _is_cancelled():
            return task_manager.is_cancelled(task_id)

        def _log_callback(msg: str, color: str = "cyan"):
            task_manager.add_log(task_id, msg, color)

        from app.services.step_04_tts.async_tts_engine import AsyncTTSEngine
        loop = asyncio.new_event_loop()
        raw_utterances_map = {}
        try:
            raw_audio_bytes_map, raw_utterances_map = loop.run_until_complete(
                AsyncTTSEngine.run_batch_priority_queue(
                    dialogues_data=dialogues_data,
                    engine="capcut",
                    voice_code=voice_code,
                    session_id=session_id,
                    concurrency_limit=num_workers,
                    max_retries_per_item=5,
                    progress_callback=_update_progress,
                    log_callback=_log_callback,
                    is_cancelled_callback=_is_cancelled,
                    return_utterances=True,
                )
            )
        finally:
            loop.close()

        if task_manager.is_cancelled(task_id):
            raise RuntimeError("Tiến trình đã bị người dùng hủy bỏ!")

        # Bản đồ số từ từng câu để gọt đuôi khoảng lặng thích ứng (<10 từ: -140ms, 10-19 từ: -80ms, >=20 từ: 0ms)
        dialogue_word_counts = {}
        for d in dialogues:
            txt = (d.translated_text or getattr(d, 'clean_text', None) or d.original_text or "").strip()
            w_count = len(txt.split()) if txt else 0
            dialogue_word_counts[d.id] = w_count

        # Giải mã In-Memory Buffer sang AudioSegment nguyên bản đa luồng siêu tốc
        decode_workers = min(16, max(4, (os.cpu_count() or 4)))
        with ThreadPoolExecutor(max_workers=decode_workers) as decode_pool:
            future_to_id = {
                decode_pool.submit(_load_audio_from_bytes, a_bytes, "mp3"): d_id
                for d_id, a_bytes in raw_audio_bytes_map.items()
                if a_bytes and len(a_bytes) > 100
            }
            for fut in as_completed(future_to_id):
                d_id = future_to_id[fut]
                try:
                    seg = fut.result()
                    if seg:
                        raw_results_map[d_id] = seg
                except Exception as e:
                    logger.warning(f"Lỗi giải mã audio cho dialogue #{d_id}: {e}")

        # Bổ sung đệm im lặng cho các câu chỉ có dấu chấm / khoảng lặng
        for s_id in silent_dialogue_ids:
            raw_results_map[s_id] = AudioSegment.silent(duration=200)

        # ── VÉT CẠN CÂU THIẾU: Đảm bảo 100% câu thoại đều có âm thanh trước khi đóng gói ──
        missing_dialogues = [d for d in dialogues if d.id not in raw_results_map]
        if missing_dialogues:
            task_manager.add_log(task_id, f"⚠️ Phát hiện {len(missing_dialogues)} câu còn thiếu. Bắt đầu vét cạn cứu hộ để đạt 100%...", "amber")
            for m in missing_dialogues:
                raw_text = sanitize_to_vietnamese(
                    m.translated_text,
                    fallback_orig=(getattr(m, 'clean_text', None) or m.original_text)
                ).strip()
                clean_text = strip_leading_numbering(raw_text)
                clean_text = re.sub(r'^[\.\,\!\?\…\:\;\—\-\s]+|[\.\,\!\?\…\:\;\—\-\s]+$', '', clean_text).strip()
                duration_ms = max(200, int((m.end_time - m.start_time) * 1000)) if (m.end_time and m.start_time) else 400

                if not clean_text or not any(c.isalnum() for c in clean_text):
                    # Câu chỉ là dấu chấm / khoảng lặng -> cấp đệm im lặng chuẩn
                    raw_results_map[m.id] = AudioSegment.silent(duration=min(1000, duration_ms))
                    task_manager.add_log(task_id, f"✔ Câu #{m.index} (Khoảng lặng/dấu chấm) -> Đã cấp đệm im lặng {duration_ms}ms.", "cyan")
                else:
                    try:
                        rescued_seg = cls.synthesize_chunk(clean_text, voice_code=voice_code)
                        if rescued_seg and len(rescued_seg) > 100:
                            raw_results_map[m.id] = rescued_seg
                            task_manager.add_log(task_id, f"✔ Cứu hộ thành công câu #{m.index}!", "emerald")
                    except Exception as e:
                        logger.error(f"Lỗi cứu hộ câu #{m.index}: {e}")

        valid_count = len(raw_results_map)
        task_manager.add_log(task_id, f"✔ Hoàn tất tải âm thanh tiếng Việt: {valid_count}/{total_count} câu thoại hợp lệ.", "emerald")

        if valid_count < total_count:
            still_missing = [d.index for d in dialogues if d.id not in raw_results_map]
            raise RuntimeError(f"Chưa hoàn thành đủ 100% số câu! Còn thiếu {len(still_missing)} câu: {still_missing[:10]}... Dừng đóng gói để bảo toàn chất lượng phim!")

        # =========================================================================
        # 0. GỘP CÁC FILE ÂM THANH CÙNG MỘT NGƯỜI NÓI (Continuous Audio Chunk Merger)
        # TTS xong mới gộp thành câu to cả text nội dung và âm thanh để cùng 1 nhân vật
        # có thể đồng bộ tốc độ nói, vừa in vào start và end khi đã gộp xong.
        # =========================================================================
        dialogues, raw_results_map, chunk_sub_offsets = cls.merge_continuous_audio_dialogues(
            dialogues=dialogues,
            raw_results_map=raw_results_map,
            db=db,
            project_id=project_id,
            task_id=task_id,
            gap_threshold=0.85
        )
        total_count = len(dialogues)

        # Hợp nhất các mốc sóng âm từ các sub-chunks tạo bởi AsyncTTSEngine cho từng câu
        engine_sub_timings = AsyncTTSEngine.get_last_sub_timings()
        if engine_sub_timings:
            for d in dialogues:
                d_id = getattr(d, 'id', None)
                tts_idx = getattr(d, 'tts_index', getattr(d, 'index', None))
                if d_id in engine_sub_timings and (tts_idx not in chunk_sub_offsets or len(chunk_sub_offsets.get(tts_idx, [])) <= 1):
                    chunk_sub_offsets[tts_idx] = [
                        {**dict(item), "old_tts_index": item.get("old_tts_index", tts_idx)}
                        for item in engine_sub_timings[d_id]
                    ]

        # =========================================================================
        # 0.5. CỨU HỘ CÂU PHÌNH QUÁ TẢI (> 1.30x) (TTS Swollen Rescuer)
        # Quét các câu có tốc độ thực tế > 1.30x (SAU KHI đã tính khoảng nghỉ khả dụng phía sau gap_after),
        # gọi LLM cô đọng và sinh lại TTS đa luồng siêu tốc.
        # =========================================================================
        from app.services.step_04_tts.tts_swollen_rescuer import TTSSwollenRescuer
        swollen_items = []
        total_v_sec_est = total_video_ms / 1000.0
        for idx_d, d in enumerate(dialogues):
            audio_seg = raw_results_map.get(d.id)
            if not audio_seg or len(audio_seg) < 100:
                continue
            start_sec = float(d.start_time)
            orig_end_sec = float(d.end_time) if (d.end_time and d.end_time > start_sec) else (start_sec + len(audio_seg) / 1000.0)
            base_dur = max(0.1, orig_end_sec - start_sec)
            raw_dur_sec = len(audio_seg) / 1000.0

            if idx_d < len(dialogues) - 1:
                next_start = float(dialogues[idx_d + 1].start_time)
                gap_after = max(0.0, next_start - orig_end_sec)
            else:
                gap_after = max(0.0, total_v_sec_est - orig_end_sec)

            # Tính thời lượng đích thực tế sau khi mượn khoảng nghỉ phía sau (đồng bộ 100% với Bước 1)
            target_dur = base_dur
            if (raw_dur_sec / base_dur) > 1.10 and gap_after > 0.001:
                needed_for_1_1 = raw_dur_sec / 1.10
                borrow_after = min(max(0.0, needed_for_1_1 - base_dur), gap_after)
                target_dur = base_dur + borrow_after

            ratio = raw_dur_sec / target_dur
            # Ước lượng thời lượng nói thực tế sau khi trừ khoảng im lặng mặc định (~250ms)
            effective_dur = max(0.1, raw_dur_sec - 0.25)
            effective_ratio = effective_dur / target_dur

            # Chỉ kích hoạt nếu tỷ lệ sau khi mượn khoảng nghỉ vẫn > ngưỡng (1.30x) VÀ sau khi gọt im lặng vẫn vượt 1.15x
            if ratio > TTSSwollenRescuer.RESCUE_RATIO_THRESHOLD and effective_ratio > 1.15:
                vi_text = str(d.translated_text or "").strip()
                # Đếm số câu bằng các dấu kết câu (. ! ? … ; :)
                sub_sentences = [s for s in re.split(r'[\.!\?…;:]+', vi_text) if s.strip()]
                num_sub_sentences = len(sub_sentences)
                char_count = len(vi_text)

                # BỘ LỌC THÔNG MINH ĐA TIÊU CHÍ (TIME + SỐ CÂU KẾT + KÝ TỰ):
                # - Chỉ vá những câu ngắn/vừa: target_dur <= 10.0s, tối đa <= 4 câu nhỏ, tối đa <= 200 ký tự.
                # - Các câu trường thiên đại cảnh (> 10s hoặc > 4 câu hoặc > 200 ký tự): TUYỆT ĐỐI KHÔNG GỌI LLM CẮT XÉN,
                #   để bảo toàn 100% tình tiết cốt truyện, và nhường cho bước TRIM + Co Giãn Tốc Độ xử lý vừa vặn.
                if target_dur <= 10.0 and num_sub_sentences <= 4 and char_count <= 200:
                    zh_text = getattr(d, 'text_zh', None) or getattr(d, 'original_text', '') or ""
                    speaker_name = getattr(d, 'speaker', None) or getattr(d, 'role', '') or "DẪN CHUYỆN"
                    swollen_items.append({
                        "dialogue_id": d.id,
                        "dialogue_obj": d,
                        "index": getattr(d, 'tts_index', getattr(d, 'index', d.id)),
                        "text_vi": vi_text,
                        "text_zh": zh_text,
                        "speaker": speaker_name,
                        "target_dur": target_dur,
                        "ratio": ratio,
                        "voice_code": voice_code
                    })

        if swollen_items:
            task_manager.add_log(
                task_id,
                f"⚡ [TTS Swollen Rescuer] Phát hiện {len(swollen_items)} câu bị phình (> {TTSSwollenRescuer.RESCUE_RATIO_THRESHOLD}x). Đang kích hoạt vá cục bộ...",
                "amber"
            )
            rescued_cnt = TTSSwollenRescuer.rescue_swollen_dialogues_sync(
                swollen_items=swollen_items,
                raw_results_map=raw_results_map,
                db=db,
                chunk_sub_offsets=chunk_sub_offsets,
            )
            if rescued_cnt > 0:
                task_manager.add_log(
                    task_id,
                    f"✔ [TTS Swollen Rescuer] Đã vá và sinh lại âm thanh thành công cho {rescued_cnt}/{len(swollen_items)} câu phình!",
                    "emerald"
                )
                # Cập nhật lại script_tts.json nếu có
                try:
                    script_tts_path = p_dirs["tts_voiceover"] / "script_tts.json"
                    if script_tts_path.exists():
                        json_dialogues = []
                        for d in dialogues:
                            json_dialogues.append({
                                "tts_index": d.tts_index,
                                "start": round(float(d.start_time), 2),
                                "end": round(float(d.end_time), 2),
                                "duration": round(float(d.duration), 2),
                                "text": d.translated_text,
                                "speaker": d.speaker,
                                "orig_count": getattr(d, 'orig_count', 1),
                                "src_indices": getattr(d, 'src_indices', str(d.tts_index))
                            })
                        with open(script_tts_path, "w", encoding="utf-8") as f:
                            json.dump(json_dialogues, f, ensure_ascii=False, indent=2)
                except Exception as e:
                    logger.warning(f"Lỗi cập nhật script_tts.json sau khi cứu hộ câu phình: {e}")

        task_manager.add_log(task_id, f"🎯 Ghép nối {total_count} chunk thoại vào Master Audio Track (Engine: NumPy Siêu Tốc)...", "cyan")

        fs = 24000
        total_samples = int((total_video_ms / 1000.0) * fs) + fs
        # MONO int16: Giọng đọc chỉ cần mono 1 kênh, giảm dung lượng RAM từ 7GB xuống chỉ 1.7GB cho video 10 tiếng
        master_buffer = np.zeros(total_samples, dtype=np.int16)
        current_timeline_sec = 0.0
        trimmed_sentences_count = 0
        count_natural_1x = 0
        count_stretched = 0
        high_speed_anomalies = []
        timeline_audit_records = []

        # =========================================================================
        # 1. BẢO TOÀN TIMELINE KHỚP GỐC & VAY NGHỈ SAU (BỎ HẲN MƯỢN 2 BÊN)
        # =========================================================================
        # Tuyệt đối không dùng TimelineBorrower (mượn chéo 2 bên gây lỗi timeline)
        borrow_transactions = []
        borrow_map = {}

        for idx_d, d in enumerate(dialogues):
            raw_seg = raw_results_map.get(d.id)
            if not raw_seg:
                continue

            raw_dur_sec = len(raw_seg) / 1000.0

            # 1. Bảo toàn 100% mốc bắt đầu của câu thoại gốc (tuyệt đối không vay nghỉ ở trước để khớp khẩu hình)
            start_sec = float(d.start_time)
            orig_end_sec = float(d.end_time) if (d.end_time and d.end_time > start_sec) else (start_sec + raw_dur_sec)
            base_dur = max(0.1, orig_end_sec - start_sec)

            # 2. Tính khoảng nghỉ phía sau (gap_after)
            if idx_d < len(dialogues) - 1:
                next_d = dialogues[idx_d + 1]
                next_start = float(next_d.start_time)
                gap_after = max(0.0, next_start - orig_end_sec)
            else:
                total_v_sec = total_video_ms / 1000.0
                gap_after = max(0.0, total_v_sec - orig_end_sec)

            # 3. BƯỚC 1: MƯỢN KHOẢNG NGHỈ PHÍA SAU (TIẾN VỀ TỐC ĐỘ 1.10X)
            # Mở khóa 100% khoảng nghỉ phía sau (next_start - orig_end_sec > 0), không tự ý trừ hao 0.25s
            target_dur = base_dur
            available_gap = gap_after
            raw_ratio = raw_dur_sec / base_dur

            # Nếu tốc độ thô > 1.10x và phía sau có khoảng nghỉ khả dụng:
            if raw_ratio > 1.10 and available_gap > 0.001:
                needed_for_1_1 = raw_dur_sec / 1.10
                deficit = max(0.0, needed_for_1_1 - base_dur)
                borrow_after = min(deficit, available_gap)
                if borrow_after > 0:
                    target_dur = base_dur + borrow_after
                    d.borrowed_end_time = round(start_sec + target_dur, 2)
                    d.borrowed_duration = round(target_dur, 2)

            cur_ratio = raw_dur_sec / target_dur
            seg_to_stretch = raw_seg
            trimmed_dur_sec = raw_dur_sec
            trim_meta = {"trimmed": False, "cut_head_ms": 0, "cut_tail_ms": 0}

            # 4. BƯỚC 2: NẾU MƯỢN XONG MÀ VẪN > 1.20X -> MỚI TIẾN HÀNH GỌT ĐẦU ĐUÔI
            if cur_ratio > 1.20:
                seg_to_stretch, trim_meta = AdaptiveSilenceTrimmer.trim_audio_segment(
                    raw_seg,
                    target_dur_sec=target_dur,
                    target_ratio=1.20,
                    max_head_ms=140,
                    max_tail_ms=140
                )
                if trim_meta.get("trimmed"):
                    trimmed_sentences_count += 1
                    AdaptiveSilenceTrimmer.log_trim_to_terminal(d.index, raw_dur_sec, target_dur, trim_meta)
                trimmed_dur_sec = len(seg_to_stretch) / 1000.0

            # 5. BƯỚC 3: CO GIÃN ÂM THANH (TIME-STRETCH) CÂN BẰNG TỐC ĐỘ [1.10x - 1.30x]
            speed_factor = trimmed_dur_sec / target_dur
            if speed_factor < 1.10:
                # Những câu có tỷ lệ nhỏ hơn 1.10 sẽ để đều là 1.10x để tạo nhịp nói gọn gàng, chừa khoảng lặng
                fitted_seg = cls.time_stretch_by_factor(seg_to_stretch, 1.10)
                actual_speed = 1.10
                count_natural_1x += 1
            else:
                # Những câu trong dải [1.10x - 1.30x] (hoặc lớn hơn sau vá): co giãn đúng speed_factor để vừa khít target_dur
                fitted_seg = cls.time_stretch_by_factor(seg_to_stretch, speed_factor)
                actual_speed = speed_factor
                count_stretched += 1

            seg_dur_sec = len(fitted_seg) / 1000.0

            # Cập nhật thông số timeline chuẩn xác vừa in vào start và end
            max_allowed_end = next_start if idx_d < len(dialogues) - 1 else (total_video_ms / 1000.0)
            d.start_time = round(start_sec, 3)
            d.end_time = min(round(start_sec + seg_dur_sec, 3), round(max_allowed_end, 3))
            d.duration = round(d.end_time - d.start_time, 3)
            d.voice_duration = round(seg_dur_sec, 3)
            d.speed_ratio = round(actual_speed, 2)
            d.status = "DUBBED"
            # Ghi nhận dữ liệu kiểm toán timeline cho từng câu
            timeline_audit_records.append({
                "tts_index": d.tts_index,
                "start_time": round(float(d.start_time), 2),
                "end_time": round(float(d.end_time), 2),
                "target_dur": round(target_dur, 2),
                "raw_dur": round(raw_dur_sec, 2),
                "trimmed_dur": round(trimmed_dur_sec, 2),
                "voice_dur": round(seg_dur_sec, 2),
                "speed_ratio": round(actual_speed, 2),
                "trimmed": trim_meta.get("trimmed", False),
                "cut_head_ms": trim_meta.get("cut_head_ms", 0),
                "cut_tail_ms": trim_meta.get("cut_tail_ms", 0)
            })

            # Dán trực tiếp vào NumPy Master Buffer (chuẩn Mono 24kHz int16)
            try:
                norm_seg = fitted_seg.set_frame_rate(fs).set_channels(1)
                seg_samples = np.array(norm_seg.get_array_of_samples(), dtype=np.int16)

                start_idx = int(start_sec * fs)
                seg_len = len(seg_samples)
                if use_exact_timeline_fit:
                    # Giới hạn không cho âm thanh lấn sang khung của câu kế tiếp
                    max_allowed_len = int(max(0.1, max_allowed_end - start_sec) * fs)
                    fit_len = min(seg_len, max_allowed_len)
                else:
                    fit_len = seg_len
                end_idx = min(start_idx + fit_len, total_samples)

                if end_idx > start_idx:
                    master_buffer[start_idx:end_idx] = seg_samples[:end_idx - start_idx]
            except Exception as e:
                logger.error(f"Lỗi ghép câu #{d.index} vào timeline: {e}")

            # ── ĐỒNG BỘ MỐC TIMELINE CÂU CON THEO SÓNG ÂM THANH THỰC TẾ (AUDIO WAVEFORM SYNC) ──
            from app.services.step_04_tts.waveform_sub_syncer import WaveformSubSyncer
            WaveformSubSyncer.sync_subtitles_to_waveform(
                db=db,
                project_id=project_id,
                tts_index=d.tts_index,
                start_sec=start_sec,
                seg_dur_sec=seg_dur_sec,
                actual_speed=actual_speed,
                trimmed_dur_sec=trimmed_dur_sec,
                trim_meta=trim_meta,
                chunk_sub_offsets=chunk_sub_offsets,
                max_allowed_end=max_allowed_end
            )

            # In log tiến độ định kỳ thông minh mỗi 100 câu
            if (idx_d + 1) % 100 == 0 or (idx_d + 1) == total_count:
                pct = int(((idx_d + 1) / total_count) * 100)
                p_msg = f"[Timeline] Đã ghép {idx_d + 1:,}/{total_count:,} câu ({pct}%) | 1.0x: {count_natural_1x} | Co giãn (>1.05x): {count_stretched} | Gọt thích ứng: {trimmed_sentences_count}"
                task_manager.add_log(task_id, f"   • [Pha 2/2] {p_msg}", "cyan")
                if (idx_d + 1) % 500 == 0 or (idx_d + 1) == total_count:
                    print(f"   • {p_msg}", flush=True)

        # Lưu thay đổi thông số voice_duration vào CSDL
        db.commit()

        # Lưu file kiểm toán timeline_audit.json vào thư mục tts_voiceover
        try:
            from app.config import get_project_dirs
            p_dirs = get_project_dirs(project_id)
            audit_path = p_dirs["tts_voiceover"] / "timeline_audit.json"
            with open(audit_path, "w", encoding="utf-8") as f:
                json.dump(timeline_audit_records, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Lỗi lưu timeline_audit.json: {e}")

        # Báo cáo tổng kết thông minh ra Terminal
        avg_speed = round(sum(r["speed_ratio"] for r in timeline_audit_records) / max(1, len(timeline_audit_records)), 2)
        summary_table = (
            f"\n{'='*65}\n"
            f"📊 TỔNG KẾT GHÉP TIMELINE AUDIO (CAPCUT TTS):\n"
            f"   • Tổng số câu: {total_count:,} câu\n"
            f"   • Tốc độ cân bằng (1.10x): {count_natural_1x:,} câu ({int(count_natural_1x * 100 / max(1, total_count))} %)\n"
            f"   • Co giãn vừa khung [1.10x - 1.30x]: {count_stretched:,} câu ({int(count_stretched * 100 / max(1, total_count))} %)\n"
            f"   • Gọt khoảng lặng thích ứng: {trimmed_sentences_count:,} câu\n"
            f"   • Tốc độ đọc trung bình: {avg_speed}x\n"
            f"   • File kiểm toán chi tiết: {audit_path.name}\n"
            f"{'='*65}\n"
        )
        print(summary_table, flush=True)
        task_manager.add_log(task_id, f"📊 Tổng kết Timeline: {count_natural_1x} câu 1.10x, {count_stretched} câu [1.10x-1.30x], tốc độ TB {avg_speed}x", "emerald")

        if high_speed_anomalies:
            task_manager.add_log(task_id, f"⚠️ Có {len(high_speed_anomalies)} câu thoại phải co giãn âm thanh > 1.8x để khớp timeline.", "amber")

        # Xuất file âm thanh siêu tốc sang FFmpeg qua Streaming Pipe
        # KHÔNG tobytes() toàn bộ và KHÔNG bọc qua AudioSegment để tránh nhân bản thêm 7GB RAM
        settings.OUTPUT_VOICEOVER_DIR.mkdir(parents=True, exist_ok=True)
        task_manager.add_log(task_id, f"💾 Đang xuất file Master Audio MP3 192kbps (Streaming Pipe siêu tốc)...", "cyan")

        ffmpeg_cmd = get_ffmpeg_cmd()
        cmd = [
            *ffmpeg_cmd, "-y",
            "-f", "s16le",
            "-ar", str(fs),
            "-ac", "1",
            "-i", "pipe:0",
            "-c:a", "libmp3lame",
            "-b:a", "192k",
            str(master_voice_file)
        ]

        pipe_proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        chunk_samples = fs * 60  # Mỗi khối 60 giây (~120KB)
        for i in range(0, total_samples, chunk_samples):
            chunk = master_buffer[i:i + chunk_samples]
            pipe_proc.stdin.write(chunk.tobytes())

        pipe_proc.stdin.close()
        pipe_proc.wait()
        del master_buffer

        task_manager.add_log(task_id, f"🎉 ĐÃ XUẤT MASTER VOICEOVER THÀNH CÔNG: {master_voice_file.name}", "emerald")

        project.status = "DUBBED"
        db.commit()

        # ── TÁI SINH BỘ 3 FILE PHỤ ĐỀ KARAOKE ĐỒNG BỘ 100% VỚI GIỌNG ĐỌC MASTER ──
        try:
            from app.services.step_05_video.karaoke_subtitle_service import KaraokeSubtitleService
            from app.services.step_05_video.subtitle_builder_service import format_timestamp
            from app.schemas.transcript import DialogueSegment
            import shutil

            from app.models.dialogue import SubtitleSegmentModel
            all_db_subs = db.query(SubtitleSegmentModel).filter(
                SubtitleSegmentModel.task_id == project_id
            ).order_by(SubtitleSegmentModel.index.asc()).all()

            if not all_db_subs:
                all_db_subs = db.query(DialogueSegmentModel).filter(
                    DialogueSegmentModel.task_id == project_id
                ).order_by(DialogueSegmentModel.index.asc()).all()

            if all_db_subs:
                from app.config import get_project_dirs
                p_dirs = get_project_dirs(project_id)
                sub_dir = p_dirs["video_subtitles"]
                sub_dir.mkdir(parents=True, exist_ok=True)

                # 1. subtitles_karaoke.json
                all_recon = [
                    {
                        "id": getattr(d, 'index', getattr(d, 'id', idx)),
                        "start": float(d.start_time),
                        "end": float(d.end_time),
                        "duration": float(d.duration),
                        "zh": getattr(d, 'clean_text', '') or getattr(d, 'original_text', ''),
                        "vi": d.translated_text,
                        "role": getattr(d, 'speaker', 'NARRATOR') or 'NARRATOR',
                        "tts_index": getattr(d, 'parent_index', getattr(d, 'tts_index', idx))
                    }
                    for idx, d in enumerate(all_db_subs, 1)
                ]
                json_path = sub_dir / "subtitles_karaoke.json"
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(all_recon, f, ensure_ascii=False, indent=2)

                # 2. subtitles_karaoke.srt
                srt_path = sub_dir / "subtitles_karaoke.srt"
                with open(srt_path, "w", encoding="utf-8") as f:
                    for s in all_recon:
                        f.write(f"{s['id']}\n{format_timestamp(s['start'])} --> {format_timestamp(s['end'])}\n{s['vi']}\n\n")

                # 3. subtitles_karaoke.ass
                ass_path = sub_dir / "subtitles_karaoke.ass"
                segments_for_ass = [
                    DialogueSegment(
                        id=getattr(d, 'index', getattr(d, 'id', idx)),
                        start=float(d.start_time),
                        end=float(d.end_time),
                        duration=float(d.duration),
                        text=getattr(d, 'original_text', '') or '',
                        clean_text=getattr(d, 'clean_text', '') or '',
                        translated_text=d.translated_text,
                        role=getattr(d, 'speaker', 'NARRATOR') or 'NARRATOR',
                        speaker=getattr(d, 'speaker', 'NARRATOR') or 'NARRATOR'
                    )
                    for idx, d in enumerate(all_db_subs, 1)
                ]
                KaraokeSubtitleService.create_karaoke_ass_file(
                    segments=segments_for_ass,
                    output_ass_path=str(ass_path),
                    video_title=project.title
                )

                # Đồng bộ file ASS ra root và transcripts dir
                root_ass = p_dirs["root"] / f"{project.video_id}_karaoke.ass"
                transcripts_ass = settings.OUTPUT_TRANSCRIPTS_DIR / f"{project.video_id}_karaoke.ass"
                if ass_path.exists():
                    shutil.copyfile(ass_path, root_ass)
                    shutil.copyfile(ass_path, transcripts_ass)
                task_manager.add_log(task_id, f"✔ Đã tái sinh phụ đề Karaoke (JSON, SRT, ASS) khớp 100% với giọng đọc Master!", "emerald")
        except Exception as e:
            logger.warning(f"Lỗi xuất phụ đề karaoke sau TTS: {e}")

        return {
            "status": "success",
            "project_id": project.id,
            "audio_path": str(master_voice_file),
            "master_voice_path": str(master_voice_file),
            "total_dialogues": len(dialogues),
            "engine": "capcut",
            "global_speed_ratio": 1.0
        }
