"""
Bộ tinh chỉnh phụ đề CapCut ASR dựa trên phụ đề cứng (Hardsub) trên video — 100% Offline (Zero-LLM).

Kiến trúc tối ưu hiệu năng & bộ nhớ:
1. Giải mã phần cứng thích ứng đa máy (NVIDIA CUDA/NVDEC -> Intel QSV -> D3D11VA -> CPU), đặt `fps=2.5` trước
   `hwdownload,format=nv12|p010le` để chỉ tải tối thiểu frame cần thiết sang RAM dưới dạng kênh grayscale (`gray`).
2. Chia Micro-Chunk thích ứng phần cứng + RAM/VRAM, chạy song song đa luồng (`ocr_workers` phiên RapidOCR PP-OCRv4 độc lập)
   theo cơ chế Producer-Consumer gối đầu và nhảy cóc theo mốc CapCut ASR (`gap >= 10s`, Lookahead).
3. Chỉ gọi Multi-Witness (`PP-OCRv5_MOBILE` + frame phụ thứ 2, dừng sớm khi khớp ASR) trên các câu bất đồng.
4. Trọng tài Offline 4 Tín hiệu (Pinyin nhiều chữ số, Hình dạng nét chữ 32x32, `jieba/wordfreq` + Thành ngữ 4 chữ,
   Tần suất thực thể lặp xuyên phim) + Guard chống Bleed & chống lặp đuôi (`SAFE_SUPERSET`).
5. Cache JSON theo file video và giải phóng triệt để FFmpeg / ONNX trong khối `finally`.
"""

import concurrent.futures
import difflib
import gc
import hashlib
import json
import logging
import os
import queue
import re
import subprocess
import threading
import time
from collections import Counter, deque
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2
import jieba
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from pypinyin import lazy_pinyin
from rapidfuzz import fuzz
from wordfreq import zipf_frequency
import zhconv

from app.config import TEMP_DIR, settings
from app.schemas.transcript import DialogueSegment
from app.utils.bin_helper import get_ffmpeg_cmd, get_ffprobe_cmd
from app.utils.hardware_profile import (
    detect_nvidia_gpus,
    get_ffmpeg_capabilities,
    recommend_ocr_profile,
)

jieba.setLogLevel(20)
logger = logging.getLogger(__name__)

# Ánh xạ Kanji Tân tự thể / Phồn thể hiếm gặp về chữ Giản thể chuẩn
_SHINJITAI_MAP = str.maketrans(
    "眞塡銭戸転発変広応団圧検証読訳鉄鉱駅気歩毎歳渋湾沢浄残砕竜仮仏伝剣労効勧庁拝払挙抜捜摂撃暁歴権歯渇獣献猟畳痩禅稲穀絵継続総繊縄縦罰聴脳臓舗薬薫蛍衛覚観諸譲豊賛軽遅違還酔釈録鎮鏡関陥険隠雑離霊響頼顔顕類飲館駆騒驚髪黙齢",
    "真填钱户转发变广应团压检证读译铁矿站气步每岁涩湾泽净残碎龙假佛传剑劳效劝厅拜拂举拔搜摄击晓历权齿渴兽献猎叠瘦禅稻谷绘继续总纤绳纵罚听脑脏铺药薰萤卫觉观诸让丰赞轻迟违还醉释录镇镜关陷险隐杂离灵响赖颜显类饮馆驱骚惊发默龄",
)

_DIG_PINYIN = {
    "0": "ling", "1": "yi", "2": "er", "3": "san", "4": "si",
    "5": "wu", "6": "liu", "7": "qi", "8": "ba", "9": "jiu",
}

_CACHE_VERSION = "v9.0"

_WIDTH_BUCKETS = (256, 384, 512, 640, 768, 960, 1152)


def _pad_crop_to_bucket(crop_bgr: np.ndarray) -> np.ndarray:
    """
    Chuẩn hóa chiều cao về 48px và đệm chiều rộng theo nấc cố định (bucket)
    để ONNXRuntime tái sử dụng 100% bộ đệm BFCArena, tránh cấp phát lại RAM và tránh spike CPU.
    """
    if crop_bgr is None or crop_bgr.size == 0:
        return crop_bgr
    h, w = crop_bgr.shape[:2]
    if h <= 0 or w <= 0:
        return crop_bgr
    target_h = 48
    if h != target_h:
        new_w = max(32, int(round(w * (target_h / float(h)))))
        interp = cv2.INTER_AREA if target_h < h else cv2.INTER_LINEAR
        crop_bgr = cv2.resize(crop_bgr, (new_w, target_h), interpolation=interp)
        h, w = target_h, new_w
    bucket_w = w
    for bw in _WIDTH_BUCKETS:
        if w <= bw:
            bucket_w = bw
            break
    else:
        bucket_w = ((w + 127) // 128) * 128
    if bucket_w > w:
        border_val = int(np.median(crop_bgr[:, -min(4, w):])) if w >= 2 else 0
        crop_bgr = cv2.copyMakeBorder(
            crop_bgr, 0, 0, 0, bucket_w - w, cv2.BORDER_CONSTANT, value=(border_val, border_val, border_val)
        )
    return crop_bgr


def to_simplified(text: str) -> str:
    """Chuẩn hóa Kanji Tân tự thể / Phồn thể về Tiếng Trung Giản thể."""
    if not text:
        return ""
    return zhconv.convert(text.translate(_SHINJITAI_MAP), "zh-cn").translate(_SHINJITAI_MAP)


def normalize_cjk(text: str) -> str:
    """Chuẩn hóa text giữ lại chữ Hán + chữ Latinh + chữ số (bỏ dấu câu & khoảng trắng)."""
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", to_simplified(text or ""))


def _find_cjk_font() -> Optional[ImageFont.FreeTypeFont]:
    """Tìm font chữ CJK hệ điều hành theo đường dẫn động (không hardcode ổ đĩa cá nhân)."""
    candidates: List[Path] = []
    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot")
    if windir:
        fdir = Path(windir) / "Fonts"
        candidates.extend([fdir / "msyh.ttc", fdir / "simhei.ttf", fdir / "simsun.ttc"])
    # Fallback tiêu chuẩn trên Linux / macOS
    candidates.extend([
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
        Path("/System/Library/Fonts/PingFang.ttc"),
    ])
    for fp in candidates:
        try:
            if fp.is_file():
                return ImageFont.truetype(str(fp), 28)
        except Exception:
            continue
    return None


class _ShapeComparator:
    """Bộ đệm so sánh độ giống hình dạng nét chữ 32x32 cục bộ theo vòng đời xử lý."""

    def __init__(self) -> None:
        self._font = _find_cjk_font()
        self._cache: Dict[str, Tuple[np.ndarray, float]] = {}

    def _render(self, ch: str) -> Tuple[np.ndarray, float]:
        if ch in self._cache:
            return self._cache[ch]
        img = Image.new("L", (32, 32), 0)
        if self._font:
            ImageDraw.Draw(img).text((2, 0), ch, fill=255, font=self._font)
        arr = np.asarray(img, dtype=np.float32) / 255.0
        val = (arr, float(np.linalg.norm(arr)))
        self._cache[ch] = val
        return val

    def span_similarity(self, s1: str, s2: str) -> float:
        """Tính điểm giống nét giữa 2 đoạn ngắn cùng độ dài (+/-1 ký tự)."""
        if not s1 or not s2 or not self._font or abs(len(s1) - len(s2)) > 1:
            return 0.0
        sims: List[float] = []
        for k in range(min(len(s1), len(s2))):
            c1, c2 = s1[k], s2[k]
            if c1 == c2:
                sims.append(1.0)
                continue
            a1, n1 = self._render(c1)
            a2, n2 = self._render(c2)
            if n1 == 0.0 or n2 == 0.0:
                sims.append(0.0)
                continue
            best = max(
                float((a1 * np.roll(np.roll(a2, dx, axis=1), dy, axis=0)).sum() / (n1 * n2))
                for dx in (-2, 0, 2)
                for dy in (-2, 0, 2)
            )
            sims.append(best)
        return sum(sims) / len(sims) if sims else 0.0

    def clear(self) -> None:
        self._cache.clear()
        self._font = None


def _num_to_pinyin(num_str: str) -> List[str]:
    if len(num_str) == 2 and num_str[0] != "0":
        res = ([] if num_str[0] == "1" else [_DIG_PINYIN[num_str[0]]]) + ["shi"]
        if num_str[1] != "0":
            res.append(_DIG_PINYIN[num_str[1]])
        return res
    return [_DIG_PINYIN[c] for c in num_str if c in _DIG_PINYIN]


def to_pinyin_list(s: str) -> List[str]:
    out: List[str] = []
    i = 0
    while i < len(s):
        if s[i].isdigit():
            j = i
            while j < len(s) and s[j].isdigit():
                j += 1
            out.extend(_num_to_pinyin(s[i:j]))
            i = j
        else:
            out.extend(lazy_pinyin(s[i]))
            i += 1
    return out


def _norm_pinyin(py: str) -> str:
    p = py.lower()
    for a, b in (("zh", "z"), ("ch", "c"), ("sh", "s"), ("ang", "an"), ("eng", "en"), ("ing", "in"), ("uo", "o")):
        p = p.replace(a, b)
    if p.startswith("n"):
        p = "l" + p[1:]
    if p.startswith("r"):
        p = "s" + p[1:]
    return p


def pinyin_similarity(a: str, b: str) -> float:
    la = [_norm_pinyin(x) for x in to_pinyin_list(a)]
    lb = [_norm_pinyin(x) for x in to_pinyin_list(b)]
    if not la or not lb:
        return 0.0
    r1 = difflib.SequenceMatcher(None, "".join(la), "".join(lb)).ratio()
    r2 = difflib.SequenceMatcher(
        None,
        "".join(x[0] for x in la if x),
        "".join(x[0] for x in lb if x),
    ).ratio()
    return 0.7 * r1 + 0.3 * r2


_HW_DECODE_CANDIDATES: Dict[str, List[str]] = {
    "cuda": ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"],
    "qsv": ["-hwaccel", "qsv", "-hwaccel_output_format", "qsv"],
    "d3d11va": ["-hwaccel", "d3d11va", "-hwaccel_output_format", "d3d11"],
}
_HW_DECODE_CACHE: Dict[Tuple[str, str], Tuple[str, List[str]]] = {}
_HW_DECODE_CACHE_LOCK = threading.Lock()


def _create_rapidocr(
    ocr_version: Any,
    intra_op_threads: int = 2,
    use_det: bool = False,
):
    """Khởi tạo một phiên RapidOCR PP-OCRv4/v5 Mobile với số thread ONNX thích ứng phần cứng."""
    from rapidocr import ModelType, RapidOCR

    params: Dict[str, Any] = {
        "Global.use_cls": False,
        "Rec.ocr_version": ocr_version,
        "Rec.model_type": ModelType.MOBILE,
        "EngineConfig.onnxruntime.intra_op_num_threads": max(1, int(intra_op_threads)),
        "EngineConfig.onnxruntime.inter_op_num_threads": 1,
    }
    if not use_det:
        params["Global.use_det"] = False
    return RapidOCR(params=params)


def _probe_video_info(video_path: str) -> Tuple[int, int, float, str]:
    """Đọc (width, height, duration_sec, codec_name) qua FFprobe trong một lần gọi."""
    fp_cmd = get_ffprobe_cmd()
    try:
        out = subprocess.check_output(
            [
                *fp_cmd, "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height,codec_name:format=duration",
                "-of", "json",
                video_path,
            ],
            stderr=subprocess.DEVNULL,
            timeout=10,
        ).decode("utf-8", errors="ignore")
        info = json.loads(out)
        stream = (info.get("streams") or [{}])[0]
        fmt = info.get("format") or {}
        w = int(stream.get("width") or 1920)
        h = int(stream.get("height") or 1080)
        dur = float(fmt.get("duration") or 0.0)
        codec = str(stream.get("codec_name") or "").strip().lower()
        return max(16, w), max(16, h), dur, codec
    except Exception:
        return 1920, 1080, 0.0, ""


def _probe_video_geometry(video_path: str) -> Tuple[int, int, float]:
    """Đọc chiều rộng, chiều cao và thời lượng video qua FFprobe."""
    w, h, dur, _ = _probe_video_info(video_path)
    return w, h, dur


def _compute_subtitle_band(
    src_w: int,
    src_h: int,
    roi: Optional[Dict[str, Any]] = None,
) -> Tuple[str, int, int]:
    """
    Tính vùng crop phụ đề chuẩn hóa theo bố cục thực tế đo được từ video (hoặc fallback biên rộng).
    Tự co giãn chiều cao băng chuẩn hóa theo cỡ chữ để giữ nét chữ ổn định từ 360p đến 4K, cả 16:9 và 9:16.
    """
    if roi and roi.get("y1_pct", 0.0) > roi.get("y0_pct", 0.0):
        x0_pct = max(0.0, min(0.80, float(roi.get("x0_pct", 0.06))))
        x1_pct = min(1.0, max(x0_pct + 0.20, float(roi.get("x1_pct", 0.94))))
        y0_pct = max(0.0, min(0.92, float(roi.get("y0_pct", 0.74))))
        y1_pct = min(1.0, max(y0_pct + 0.08, float(roi.get("y1_pct", 1.0))))
        med_h_pct = max(0.03, float(roi.get("med_h_pct", 0.08)))
    elif src_w >= src_h:
        x0_pct, x1_pct, y0_pct, y1_pct = 0.08, 0.92, 0.74, 0.996
        med_h_pct = 0.08
    else:
        # Video dọc 9:16 (Shorts / Douyin): sub thường nằm cao hơn thanh UI dưới
        x0_pct, x1_pct, y0_pct, y1_pct = 0.04, 0.96, 0.68, 0.96
        med_h_pct = 0.065

    cx = max(0, (int(src_w * x0_pct) // 2) * 2)
    cy = max(0, (int(src_h * y0_pct) // 2) * 2)
    cw = max(64, (int(src_w * (x1_pct - x0_pct)) // 2) * 2)
    ch = max(32, (int(src_h * (y1_pct - y0_pct)) // 2) * 2)
    if cx + cw > src_w:
        cw = max(64, ((src_w - cx) // 2) * 2)
    if cy + ch > src_h:
        ch = max(32, ((src_h - cy) // 2) * 2)

    if roi and roi.get("has_hardsub") and roi.get("med_h_pct"):
        # Co giãn để 1 dòng chữ trong băng cao chuẩn hóa vào khoảng ~54px (không phụ thuộc 360p hay 1080p)
        sub_ratio_in_band = max(0.20, min(0.70, med_h_pct / max(0.05, y1_pct - y0_pct)))
        band_h = max(140, min(240, (int(round(54.0 / sub_ratio_in_band)) // 2) * 2))
        band_w = max(480, min(1600, (int(round(band_h * (cw / max(1, ch)))) // 2) * 2))
    elif src_w >= src_h:
        band_w, band_h = 1440, 180
    else:
        band_w, band_h = 1080, 180

    crop_expr = f"crop={cw}:{ch}:{cx}:{cy}"
    if cw != band_w or ch != band_h:
        crop_expr += f",scale={band_w}:{band_h}"
    return crop_expr, band_w, band_h


def _probe_subtitle_roi(
    video_path: str,
    src_w: int,
    src_h: int,
    vid_dur: float,
    max_duration_sec: Optional[float] = None,
    sample_timestamps: Optional[List[float]] = None,
) -> Dict[str, Any]:
    """
    Pha 1: Thăm dò nhanh các khung hình rải đều bằng PyAV (libdav1d/H264 Y-plane) + RapidOCR
    để tự khóa vùng phụ đề thực tế, loại bỏ logo/chữ tĩnh góc màn hình, đo kích thước chữ & độ sáng
    và thoát sớm nếu phim không có hardsub. Hoàn toàn không dùng libaom-av1 của OpenCV.
    """
    from rapidocr import OCRVersion

    eff_dur = float(vid_dur or 0.0)
    if max_duration_sec and max_duration_sec > 0:
        eff_dur = min(eff_dur, float(max_duration_sec)) if eff_dur > 0 else float(max_duration_sec)
    if eff_dur <= 1.0:
        eff_dur = 60.0

    n_samples = 12
    if sample_timestamps and len(sample_timestamps) >= 4:
        valid_ts = [float(t) for t in sample_timestamps if 0.5 <= float(t) <= eff_dur - 0.2]
        if len(valid_ts) >= n_samples:
            step = (len(valid_ts) - 1) / float(n_samples - 1)
            sample_ts = [round(valid_ts[int(round(i * step))], 2) for i in range(n_samples)]
        elif len(valid_ts) >= 4:
            sample_ts = [round(t, 2) for t in valid_ts]
        else:
            start_t = min(4.0, eff_dur * 0.08)
            end_t = max(start_t + 1.0, eff_dur * 0.90)
            sample_ts = [round(start_t + (end_t - start_t) * (i / max(1, n_samples - 1)), 2) for i in range(n_samples)]
    else:
        start_t = min(4.0, eff_dur * 0.08)
        end_t = max(start_t + 1.0, eff_dur * 0.90)
        sample_ts = [round(start_t + (end_t - start_t) * (i / max(1, n_samples - 1)), 2) for i in range(n_samples)]

    det_ocr = None
    av_container = None
    cap = None
    decoded_frames = 0
    raw_boxes: List[Dict[str, Any]] = []
    try:
        probe_threads = max(1, min(3, (os.cpu_count() or 4) // 2))
        det_ocr = _create_rapidocr(OCRVersion.PPOCRV4, intra_op_threads=probe_threads, use_det=True)

        def _iter_probe_gray_frames():
            nonlocal av_container, cap
            yielded = 0
            try:
                import av

                av_container = av.open(video_path)
                vstream = av_container.streams.video[0]
                vstream.thread_type = "AUTO"
                vstream.thread_count = 2
                tb = float(vstream.time_base) if vstream.time_base else (1.0 / 1000.0)
                for f_idx, ts in enumerate(sample_ts):
                    try:
                        av_container.seek(int(max(0.0, ts - 0.05) / tb), stream=vstream, backward=True, any_frame=False)
                        for frame in av_container.decode(vstream):
                            ft = float(frame.pts * tb) if frame.pts is not None else ts
                            if ft + 0.05 < ts and (ft < 1.5 or (ts - ft) <= 4.5):
                                continue
                            plane = frame.planes[0]
                            arr = np.frombuffer(plane, dtype=np.uint8).reshape(frame.height, plane.line_size)[:, :frame.width].copy()
                            yielded += 1
                            yield (f_idx, arr)
                            break
                    except Exception:
                        continue
                if yielded > 0:
                    return
            except GeneratorExit:
                return
            except Exception:
                pass
            finally:
                if av_container is not None:
                    try:
                        av_container.close()
                    except Exception:
                        pass
                    av_container = None

            if yielded > 0:
                return

            try:
                cap = cv2.VideoCapture(video_path)
                if not cap.isOpened():
                    return
                for f_idx, ts in enumerate(sample_ts):
                    cap.set(cv2.CAP_PROP_POS_MSEC, ts * 1000.0)
                    ok, img = cap.read()
                    if not ok or img is None:
                        continue
                    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
                    yield (f_idx, g)
            finally:
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass
                    cap = None

        for f_idx, gray_full in _iter_probe_gray_frames():
            decoded_frames += 1
            ih, iw = gray_full.shape[:2]
            scale = min(1.0, 720.0 / float(max(1, iw)))
            if scale < 0.95:
                pw = max(64, int(round(iw * scale)))
                ph = max(64, int(round(ih * scale)))
                gray_small = cv2.resize(gray_full, (pw, ph), interpolation=cv2.INTER_AREA)
            else:
                pw, ph = iw, ih
                gray_small = gray_full
            img_det = cv2.cvtColor(gray_small, cv2.COLOR_GRAY2BGR)
            res = det_ocr(img_det)
            if not res or res.boxes is None or res.txts is None:
                continue
            inv_scale_x = float(iw) / float(max(1, pw))
            inv_scale_y = float(ih) / float(max(1, ph))
            for bx, txt in zip(res.boxes, res.txts):
                n_txt = normalize_cjk(str(txt))
                if not n_txt or not re.search(r"[\u4e00-\u9fff]", n_txt):
                    continue
                xs = [float(p[0]) * inv_scale_x for p in bx]
                ys = [float(p[1]) * inv_scale_y for p in bx]
                x0_px, x1_px = max(0, int(min(xs))), min(iw, int(max(xs)))
                y0_px, y1_px = max(0, int(min(ys))), min(ih, int(max(ys)))
                bw_px, bh_px = x1_px - x0_px, y1_px - y0_px
                if bw_px < 8 or bh_px < 8:
                    continue
                x0_r, x1_r = x0_px / float(iw), x1_px / float(iw)
                y0_r, y1_r = y0_px / float(ih), y1_px / float(ih)
                cx_r = (x0_r + x1_r) * 0.5
                cy_r = (y0_r + y1_r) * 0.5

                # Bỏ chữ dọc xoay 90 độ sát mép trái/phải (ví dụ cảnh báo "剧情虚构")
                if bw_px < bh_px * 0.85 and (cx_r < 0.12 or cx_r > 0.88):
                    continue
                # Bỏ watermark góc trên hoặc logo góc dưới cùng hai bên
                if cy_r < 0.14 and (cx_r < 0.28 or cx_r > 0.72):
                    continue
                if cy_r > 0.86 and (cx_r < 0.18 or cx_r > 0.82):
                    continue

                patch = gray_full[y0_px:y1_px, x0_px:x1_px]
                p90 = float(np.percentile(patch, 92)) if patch.size else 235.0
                p30 = float(np.percentile(patch, 30)) if patch.size else 60.0
                p10 = float(np.percentile(patch, 10)) if patch.size else 30.0
                raw_boxes.append({
                    "frame_idx": f_idx,
                    "n": n_txt,
                    "x0_r": x0_r,
                    "x1_r": x1_r,
                    "y0_r": y0_r,
                    "y1_r": y1_r,
                    "cx_r": cx_r,
                    "cy_r": cy_r,
                    "bh_r": y1_r - y0_r,
                    "p90": p90,
                    "p30": p30,
                    "p10": p10,
                })

            # Thoát sớm nếu đã khóa được cụm phụ đề trung tâm ổn định trên >= 5 khung hình
            if decoded_frames >= 6:
                center_frames = {b["frame_idx"] for b in raw_boxes if 0.25 <= b["cx_r"] <= 0.75 and b["cy_r"] >= 0.55}
                if len(center_frames) >= 5 and len(raw_boxes) >= 6:
                    break
    except Exception as e_probe:
        logger.warning(f"[VisualSubRefiner] Lỗi thăm dò ROI, dùng vùng fallback: {e_probe}")
        return {"has_hardsub": True, "mode": "probe_err_fallback"}
    finally:
        if av_container is not None:
            try:
                av_container.close()
            except Exception:
                pass
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass
        det_ocr = None
        gc.collect()

    if decoded_frames < 2:
        return {"has_hardsub": True, "mode": "decode_fallback", "probe_frames": decoded_frames}

    # Lọc bỏ banner/chú thích tĩnh xuất hiện lặp lại y hệt ở >= 3 frame hoặc cố định cùng tọa độ
    txt_counts = Counter(b["n"] for b in raw_boxes)
    by_txt: Dict[str, List[Dict[str, Any]]] = {}
    for b in raw_boxes:
        by_txt.setdefault(b["n"], []).append(b)

    static_texts = set()
    for t_str, grp in by_txt.items():
        if len(grp) >= 3:
            static_texts.add(t_str)
        elif len(grp) == 2 and len({b["frame_idx"] for b in raw_boxes}) <= 5:
            if abs(grp[0]["cx_r"] - grp[1]["cx_r"]) < 0.03 and abs(grp[0]["cy_r"] - grp[1]["cy_r"]) < 0.03:
                static_texts.add(t_str)

    dyn_boxes = [b for b in raw_boxes if b["n"] not in static_texts]
    # Ưu tiên các dòng thoại căn tương đối gần tâm ngang màn hình (loại hộp chú thích lệch hẳn bên trái/phải)
    centered_boxes = [b for b in dyn_boxes if 0.22 <= b["cx_r"] <= 0.78]
    cands = centered_boxes if len(centered_boxes) >= 2 else dyn_boxes

    cand_frames = {b["frame_idx"] for b in cands}
    if len(cand_frames) < 2:
        return {
            "has_hardsub": False,
            "mode": "no_hardsub",
            "probe_frames": decoded_frames,
            "raw_cjk": len(raw_boxes),
        }

    # Phân cụm theo trục Y để khóa đúng băng phụ đề chính (ưu tiên nửa dưới nếu cả trên và dưới cùng có chữ)
    clusters: List[List[Dict[str, Any]]] = []
    for b in sorted(cands, key=lambda x: x["cy_r"]):
        placed = False
        for cl in clusters:
            mean_cy = sum(x["cy_r"] for x in cl) / len(cl)
            if abs(b["cy_r"] - mean_cy) <= 0.085:
                cl.append(b)
                placed = True
                break
        if not placed:
            clusters.append([b])

    def _cluster_score(cl: List[Dict[str, Any]]) -> float:
        mean_cy = sum(x["cy_r"] for x in cl) / len(cl)
        uniq_frames = len({x["frame_idx"] for x in cl})
        center_bonus = sum(1.0 for x in cl if 0.30 <= x["cx_r"] <= 0.70)
        lower_mult = 1.6 if mean_cy >= 0.50 else 0.75
        return (uniq_frames * 2.0 + center_bonus * 0.8) * lower_mult

    best_cl = max(clusters, key=_cluster_score)
    med_h_pct = float(np.median([b["bh_r"] for b in best_cl]))
    raw_y0 = min(b["y0_r"] for b in best_cl)
    raw_y1 = max(b["y1_r"] for b in best_cl)
    raw_x0 = min(b["x0_r"] for b in best_cl)
    raw_x1 = max(b["x1_r"] for b in best_cl)

    pad_top = max(0.032, med_h_pct * 0.60)
    pad_bot = max(0.025, med_h_pct * 0.45)
    y0_pct = max(0.0, raw_y0 - pad_top)
    y1_pct = 1.0 if raw_y1 >= 0.94 else min(1.0, raw_y1 + pad_bot)
    x0_pct = max(0.03, min(0.14, raw_x0 - 0.14))
    x1_pct = min(0.97, max(0.86, raw_x1 + 0.14))

    p90_med = float(np.median([b["p90"] for b in best_cl]))
    p30_med = float(np.median([b["p30"] for b in best_cl]))
    p10_med = float(np.median([b["p10"] for b in best_cl]))
    polarity = "dark" if (p90_med < 175 and p10_med < 85 and (p90_med - p10_med) > 45) else "light"

    return {
        "has_hardsub": True,
        "mode": "auto_calibrated",
        "probe_frames": decoded_frames,
        "cluster_hits": len(best_cl),
        "x0_pct": round(x0_pct, 4),
        "x1_pct": round(x1_pct, 4),
        "y0_pct": round(y0_pct, 4),
        "y1_pct": round(y1_pct, 4),
        "med_h_pct": round(med_h_pct, 4),
        "p90": round(p90_med, 1),
        "p30": round(p30_med, 1),
        "p10": round(p10_med, 1),
        "polarity": polarity,
    }


def _build_decode_vf(hw_name: str, fps_val: float, crop_filter: str) -> str:
    """Tạo chuỗi -vf chuẩn hóa pixel format 8-bit/10-bit khi dùng hwdownload GPU."""
    if hw_name in _HW_DECODE_CANDIDATES:
        return f"fps={fps_val},hwdownload,format=nv12|p010le,{crop_filter}"
    return f"fps={fps_val},{crop_filter}"


def _detect_hwaccel_decode(
    video_path: str,
    codec_name: str = "",
    preferred: Optional[str] = None,
) -> Tuple[str, List[str]]:
    """
    Tự động phát hiện bộ giải mã phần cứng nhanh nhất hỗ trợ file/codec đầu vào:
    Ưu tiên NVIDIA CUDA (RTX 3050+...) -> Intel QSV -> D3D11VA -> CPU.
    Dùng `-xerror` + `-frames:v 1` và kiểm tra 256 byte raw gray thực tế để ngắt nhanh khi codec không hỗ trợ.
    """
    pref = (preferred or getattr(settings, "OCR_HWACCEL", "auto") or "auto").strip().lower()
    if pref == "cpu":
        return "cpu", []

    codec_key = (codec_name or "").strip().lower()
    if not codec_key and video_path:
        _, _, _, codec_key = _probe_video_info(video_path)

    cache_key = (codec_key or "unknown", pref)
    with _HW_DECODE_CACHE_LOCK:
        cached = _HW_DECODE_CACHE.get(cache_key)
        if cached is not None:
            return cached[0], list(cached[1])

    order = ["cuda", "qsv", "d3d11va"]
    if pref in _HW_DECODE_CANDIDATES:
        order = [pref] + [name for name in order if name != pref]

    caps = get_ffmpeg_capabilities()
    avail_hw = caps.get("hwaccels", set())
    has_nvidia = bool(detect_nvidia_gpus())
    ff_cmd = get_ffmpeg_cmd()

    selected: Tuple[str, List[str]] = ("cpu", [])
    for name in order:
        if avail_hw and name not in avail_hw:
            continue
        if name == "cuda" and not has_nvidia:
            continue
        extra = list(_HW_DECODE_CANDIDATES[name])
        try:
            proc = subprocess.run(
                [
                    *ff_cmd, "-v", "error", "-xerror", *extra,
                    "-i", video_path,
                    "-vf", "fps=2,hwdownload,format=nv12|p010le,crop=16:16:0:0",
                    "-frames:v", "1",
                    "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=6,
            )
            if proc.returncode == 0 and len(proc.stdout or b"") >= 256:
                selected = (name, extra)
                break
        except Exception:
            continue

    with _HW_DECODE_CACHE_LOCK:
        _HW_DECODE_CACHE[cache_key] = (selected[0], list(selected[1]))
    return selected[0], list(selected[1])


def _crop_from_mask(
    mask: np.ndarray,
    gray: np.ndarray,
    band_w: int,
    band_h: int,
    sub_win: Tuple[float, float, float, float] = (0.0, 1.0, 0.0, 1.0),
) -> Optional[np.ndarray]:
    """Tách đúng khung bao quanh 1 dòng phụ đề chính, thích ứng kích thước mask & chữ."""
    m_h, m_w = mask.shape
    m_u8 = mask.astype(np.uint8) * 255
    dil_w = max(9, min(19, int(round(m_w * 0.022)) | 1))
    dil = cv2.dilate(m_u8, np.ones((3, dil_w), np.uint8))
    num_lbl, _, stats, centroids = cv2.connectedComponentsWithStats(dil)
    if num_lbl <= 1:
        return None

    wx0, wx1, wy0, wy1 = sub_win
    scale_x = (band_w * max(0.01, wx1 - wx0)) / float(m_w)
    scale_y = (band_h * max(0.01, wy1 - wy0)) / float(m_h)
    off_x = int(band_w * wx0)
    off_y = int(band_h * wy0)

    min_area = max(24, int(m_h * 0.55))
    min_bw = max(8, int(m_w * 0.014))
    min_bh = max(5, int(m_h * 0.09))

    cands_cc = []
    for idx in range(1, num_lbl):
        x, y, bw, bh, area = stats[idx]
        cx, cy = centroids[idx]
        if area >= min_area and bw >= min_bw and bh >= min_bh and (m_h * 0.04) <= cy <= (m_h * 0.96):
            score = area * (1.0 if (m_w * 0.14) <= cx <= (m_w * 0.86) else 0.35)
            cands_cc.append((score, x, y, bw, bh, cy))
    if not cands_cc:
        return None

    cands_cc.sort(reverse=True)
    main_cy = cands_cc[0][5]
    dy_tol = max(10, int(round(m_h * 0.22)))
    min_piece_score = max(20, min(40, int(min_area * 0.7)))
    kept = [c for c in cands_cc if abs(c[5] - main_cy) <= dy_tol and c[0] >= min_piece_score]
    if not kept:
        kept = [cands_cc[0]]
    x0_m = min(c[1] for c in kept)
    x1_m = max(c[1] + c[3] for c in kept)
    y0_m = min(c[2] for c in kept)
    y1_m = max(c[2] + c[4] for c in kept)

    pad_x = max(14, int(band_w * 0.015))
    pad_y = max(8, int(band_h * 0.055))
    x0 = max(0, int(off_x + x0_m * scale_x) - pad_x)
    x1 = min(band_w, int(off_x + x1_m * scale_x) + pad_x + 2)
    y0 = max(0, int(off_y + y0_m * scale_y) - pad_y)
    y1 = min(band_h, int(off_y + y1_m * scale_y) + pad_y + 2)
    if x1 <= x0 + 8 or y1 <= y0 + 6:
        return None
    return cv2.cvtColor(gray[y0:y1, x0:x1], cv2.COLOR_GRAY2BGR)


def _get_cache_path(video_path: str, max_duration_sec: Optional[float] = None) -> Path:
    vp = Path(video_path).resolve()
    dur_tag = f"clip{int(round(max_duration_sec))}" if (max_duration_sec and max_duration_sec > 0) else "full"
    try:
        st = vp.stat()
        sig = f"{vp.name}:{st.st_size}:{int(st.st_mtime)}:{dur_tag}:{_CACHE_VERSION}"
    except Exception:
        sig = f"{vp.name}:{dur_tag}:{_CACHE_VERSION}"
    h = hashlib.md5(sig.encode("utf-8")).hexdigest()[:10]
    cache_dir = TEMP_DIR / "visual_sub_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{vp.stem}_{h}.json"


def clear_visual_cache_for_video(video_path: Optional[str] = None, video_id: Optional[str] = None) -> int:
    """Xóa cache OCR trên đĩa của một video cụ thể để tránh phình ổ đĩa hoặc dùng nhầm cache cũ khi chạy lại."""
    cache_dir = TEMP_DIR / "visual_sub_cache"
    if not cache_dir.exists():
        return 0
    targets = set()
    if video_path:
        try:
            stem = Path(video_path).resolve().stem
            if stem:
                targets.update(cache_dir.glob(f"{stem}_*.json"))
        except Exception:
            pass
    if video_id:
        targets.update(cache_dir.glob(f"*{video_id}*.json"))
    removed = 0
    for f in targets:
        try:
            if f.is_file():
                f.unlink(missing_ok=True)
                removed += 1
        except Exception:
            pass
    return removed


class VisualSubRefiner:
    """
    Bộ gộp câu bị cắt vụn và sửa lỗi đồng âm CapCut ASR dựa trên phụ đề cứng trên video.
    An toàn luồng, tự hủy tài nguyên FFmpeg & ONNX sau khi chạy xong.
    """

    @staticmethod
    def _split_segments_into_chunks(
        segments: List[DialogueSegment],
        max_chunks: int = 3,
        target_chunk_sec: Optional[float] = None,
        target_chunk_segs: Optional[int] = None,
        min_chunk_segs: int = 16,
    ) -> List[Tuple[int, int]]:
        """
        Chia danh sách câu ASR thành các khúc độc lập tại khoảng nghỉ tự nhiên (`gap >= 0.85s`)
        để không cắt ngang câu phụ đề đang hiển thị liên tục.
        - Khi truyền `target_chunk_sec` hoặc `target_chunk_segs`: tự chia thành nhiều micro-chunk
          cân bằng tải (tính theo thời lượng hội thoại thực tế, bỏ qua khoảng lặng >= 10s).
        - Khi không truyền: giữ tương thích chế độ chia theo `max_chunks`.
        """
        n = len(segments)
        if n <= 0:
            return []

        if (target_chunk_sec and target_chunk_sec > 0) or (target_chunk_segs and target_chunk_segs > 0):
            min_s = max(10, int(min_chunk_segs))
            tail_min = max(10, min_s - 4)
            if n < max(24, min_s + tail_min):
                return [(0, n)]

            t_goal = float(target_chunk_sec or 180.0)
            s_goal = int(target_chunk_segs or 140)
            chunks: List[Tuple[int, int]] = []
            start_idx = 0

            while start_idx < n:
                if (n - start_idx) < (min_s + tail_min):
                    chunks.append((start_idx, n))
                    break

                active_sec = max(0.2, float(segments[start_idx].end) - float(segments[start_idx].start))
                cut_idea = n
                for k in range(start_idx + 1, n):
                    gap = max(0.0, float(segments[k].start) - float(segments[k - 1].end))
                    # Khoảng lặng >= 10s được _build_dialogue_windows nhảy cóc bằng -ss, chỉ tính padding ~0.8s
                    active_sec += (gap if gap < 10.0 else 0.8) + max(0.1, float(segments[k].end) - float(segments[k].start))
                    seg_cnt = k - start_idx
                    if seg_cnt >= min_s and (active_sec >= t_goal or seg_cnt >= s_goal):
                        if (n - k) >= tail_min:
                            cut_idea = k
                        break

                if cut_idea >= n:
                    chunks.append((start_idx, n))
                    break

                lo = max(start_idx + min_s, cut_idea - 12)
                hi = min(n - tail_min, cut_idea + 18)
                if lo >= hi:
                    best_pos = max(start_idx + min_s, min(n - tail_min, cut_idea))
                else:
                    best_pos = cut_idea
                    best_score = -1e9
                    for pos in range(lo, hi + 1):
                        gap = float(segments[pos].start) - float(segments[pos - 1].end)
                        dist_pen = abs(pos - cut_idea) * 0.015
                        score = gap - dist_pen + (2.0 if gap >= 0.85 else 0.0)
                        if score > best_score:
                            best_score = score
                            best_pos = pos

                chunks.append((start_idx, best_pos))
                start_idx = best_pos

            return chunks

        if n < 36 or max_chunks <= 1:
            return [(0, n)]

        k_chunks = min(max_chunks, 2 if n < 120 else 3)
        target_step = n / float(k_chunks)
        bounds = [0]
        for c_idx in range(1, k_chunks):
            ideal = int(round(c_idx * target_step))
            lo = max(bounds[-1] + 10, ideal - 25)
            hi = min(n - 10, ideal + 25)
            if lo >= hi:
                continue
            best_pos = ideal
            best_score = -1e9
            for pos in range(lo, hi + 1):
                gap = float(segments[pos].start) - float(segments[pos - 1].end)
                dist_pen = abs(pos - ideal) * 0.015
                score = gap - dist_pen
                if gap >= 0.85:
                    score += 2.0
                if score > best_score:
                    best_score = score
                    best_pos = pos
            if best_pos > bounds[-1]:
                bounds.append(best_pos)
        bounds.append(n)
        return [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1) if bounds[i] < bounds[i + 1]]

    @staticmethod
    def _build_dialogue_windows(
        segments: List[DialogueSegment],
        gap_split: float = 10.0,
        pad_before: float = 0.35,
        pad_after: float = 0.45,
        vid_dur: float = 0.0,
    ) -> List[Tuple[float, float, int, int]]:
        """
        Gom các câu ASR thành các cửa sổ hội thoại liên tục, cắt tại khoảng lặng >= gap_split (mặc định 10s)
        để FFmpeg dùng `-ss` nhảy cóc qua các đoạn nhạc/cảnh tĩnh kéo dài mà không tốn công decode.
        Trả về [(win_start, win_end, rel_start_idx, rel_end_idx), ...].
        """
        n = len(segments)
        if n <= 0:
            return []

        windows: List[Tuple[float, float, int, int]] = []
        w_s_idx = 0
        w_start = max(0.0, float(segments[0].start) - pad_before)
        prev_end = float(segments[0].end)

        for idx in range(1, n):
            cur_start = float(segments[idx].start)
            cur_end = float(segments[idx].end)
            if cur_start - prev_end >= gap_split:
                w_end = prev_end + pad_after
                if vid_dur > 0:
                    w_end = min(vid_dur, w_end)
                windows.append((round(w_start, 3), round(max(w_start + 0.1, w_end), 3), w_s_idx, idx))
                w_s_idx = idx
                w_start = max(0.0, cur_start - pad_before)
            prev_end = max(prev_end, cur_end)

        w_end = prev_end + pad_after
        if vid_dur > 0:
            w_end = min(vid_dur, w_end)
        windows.append((round(w_start, 3), round(max(w_start + 0.1, w_end), 3), w_s_idx, n))
        return windows

    @classmethod
    def _extract_blocks_asr_guided_parallel(
        cls,
        video_path: str,
        segments: List[DialogueSegment],
        roi: Dict[str, Any],
        src_w: int,
        src_h: int,
        vid_dur: float,
        max_duration_sec: Optional[float] = None,
        progress_cb: Optional[Callable[[str], None]] = None,
        video_codec: str = "",
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Quét phụ đề cứng định hướng theo mốc CapCut ASR:
        - Hardware-First Streaming (NVIDIA CUDA/NVDEC -> Intel QSV -> D3D11VA -> CPU) đa worker song song,
          mỗi worker sở hữu một phiên RapidOCR PP-OCRv4 độc lập (không nghẽn khóa ocr_lock dùng chung).
        - Chia Micro-Chunk cân bằng tải thích ứng theo phần cứng + RAM/VRAM thực tế + độ dài phim.
        - Nhảy cóc qua khoảng lặng dài >= 10s bằng `_build_dialogue_windows` & Lookahead (n -> n + best_k).
        - Multi-Probe 2 nửa (K4) cho câu ASR dài chứa 2 phụ đề nối tiếp & Multi-Witness bắt buộc (K3).
        - Zero-Crop-RAM: giải phóng toàn bộ crop & session ONNX ngay sau OCR.
        """
        t_all = time.time()
        crop_filter, band_w, band_h = _compute_subtitle_band(src_w, src_h, roi=roi)
        crop_only = crop_filter.split(",")[0].replace("crop=", "")
        cf_parts = crop_only.split(":")
        cw = int(cf_parts[0]) if len(cf_parts) >= 4 else band_w
        ch = int(cf_parts[1]) if len(cf_parts) >= 4 else band_h
        bx1 = int(cf_parts[2]) if len(cf_parts) >= 4 else 0
        by1 = int(cf_parts[3]) if len(cf_parts) >= 4 else max(0, src_h - ch)

        if max_duration_sec and max_duration_sec > 0:
            active_segments = [s for s in segments if float(s.start) < max_duration_sec]
        else:
            active_segments = list(segments)

        if not active_segments:
            return [], {
                "frames": 0,
                "raw_blocks": 0,
                "t_decode": 0.0,
                "hwaccel": "asr_guided",
                "has_hardsub": True,
                "roi": roi,
                "subs_count": 0,
                "duration_sec": vid_dur,
                "t_extract_wall": round(time.time() - t_all, 2),
                "mode": "asr_guided_lookahead",
            }

        hw_name, hw_args = _detect_hwaccel_decode(video_path, codec_name=video_codec)
        use_hw_pipe = hw_name in _HW_DECODE_CANDIDATES
        scan_fps = 2.5
        ff_cmd = get_ffmpeg_cmd()

        profile = recommend_ocr_profile(
            hw_decode_name=hw_name,
            video_duration_sec=vid_dur,
            segment_count=len(active_segments),
            vid_width=src_w,
            vid_height=src_h,
            env_workers=getattr(settings, "OCR_WORKERS", 0),
            env_chunk_sec=getattr(settings, "OCR_CHUNK_SECONDS", 0),
        )
        chunks = cls._split_segments_into_chunks(
            active_segments,
            target_chunk_sec=profile["chunk_target_sec"],
            target_chunk_segs=profile["chunk_target_segs"],
            min_chunk_segs=profile["chunk_min_segs"],
        )
        ocr_workers = max(1, min(len(chunks), int(profile["ocr_workers"])))
        if ocr_workers < int(profile["ocr_workers"]):
            eff_onnx_threads = max(
                int(profile["onnx_threads"]),
                min(3, max(1, (int(profile["cpu_cores"]) - 1) // ocr_workers)),
            )
        else:
            eff_onnx_threads = int(profile["onnx_threads"])
        cpu_fallback_threads = max(1, min(2, int(profile["cpu_cores"]) // max(1, ocr_workers)))

        small_h = max(56, min(80, int(round(band_h * 0.35))))
        small_w = max(160, int(round(band_w * (small_h / float(band_h)))))
        k_sz = max(7, min(13, int(round(small_h * 0.14)) | 1))
        k_morph = cv2.getStructuringElement(cv2.MORPH_RECT, (k_sz, k_sz))
        is_dark_sub = roi.get("polarity") == "dark"
        p90_v = float(roi.get("p90", 240.0))
        p30_v = float(roi.get("p30", 60.0))
        p10_v = float(roi.get("p10", 30.0))
        lum_thr = max(150, min(205, int(p90_v - 48))) if not is_dark_sub else max(45, min(145, int(p10_v + 45)))
        th_thr = (
            max(22, int((p90_v - p10_v) * 0.25))
            if is_dark_sub
            else max(24, min(42, int((p90_v - p30_v) * 0.22)))
        )
        min_mask_px = max(70, int(small_w * small_h * 0.0022))

        def _extract_mask(gray_band: Optional[np.ndarray]) -> Optional[np.ndarray]:
            if gray_band is None or gray_band.size == 0:
                return None
            small = cv2.resize(gray_band, (small_w, small_h), interpolation=cv2.INTER_AREA)
            if is_dark_sub:
                bh_m = cv2.morphologyEx(small, cv2.MORPH_BLACKHAT, k_morph)
                mask = (small < lum_thr) & (bh_m > th_thr)
            else:
                th_m = cv2.morphologyEx(small, cv2.MORPH_TOPHAT, k_morph)
                mask = (small > lum_thr) & (th_m > th_thr)
            if int(mask.sum()) < min_mask_px:
                return None
            return mask

        def _full_align_score(a_str: str, o_str: str) -> float:
            if not a_str or not o_str:
                return 0.0
            fz = fuzz.ratio(a_str, o_str) / 100.0
            py = pinyin_similarity(a_str, o_str)
            len_r = min(len(a_str), len(o_str)) / float(max(len(a_str), len(o_str)))
            return 0.45 * fz + 0.40 * py + 0.15 * len_r

        from rapidocr import OCRVersion

        v4_free: List[Any] = []
        v4_all: List[Any] = []
        v5_free: List[Any] = []
        v5_all: List[Any] = []
        pool_lock = threading.Lock()
        v5_init_lock = threading.Lock()
        v4_sem = threading.Semaphore(ocr_workers)
        v5_pool_cap = max(1, min(ocr_workers, int(profile["v5_pool_size"])))
        v5_sem = threading.Semaphore(v5_pool_cap)
        tls = threading.local()

        try:
            # Nạp trước 1 phiên PP-OCRv4 trên luồng chính để tải/kiểm tra model 1 lần an toàn
            first_v4 = _create_rapidocr(OCRVersion.PPOCRV4, intra_op_threads=eff_onnx_threads, use_det=False)
            v4_all.append(first_v4)
            v4_free.append(first_v4)

            def _acquire_v4() -> Any:
                v4_sem.acquire()
                try:
                    with pool_lock:
                        if v4_free:
                            return v4_free.pop()
                    eng = _create_rapidocr(OCRVersion.PPOCRV4, intra_op_threads=eff_onnx_threads, use_det=False)
                    with pool_lock:
                        v4_all.append(eng)
                    return eng
                except Exception:
                    v4_sem.release()
                    raise

            def _release_v4(eng: Any) -> None:
                try:
                    if eng is not None:
                        with pool_lock:
                            v4_free.append(eng)
                finally:
                    v4_sem.release()

            def _run_v4(crop_img: np.ndarray) -> Tuple[str, float]:
                padded = _pad_crop_to_bucket(crop_img)
                eng = getattr(tls, "eng_v4", None)
                if eng is not None:
                    res = eng(padded)
                else:
                    eng_tmp = _acquire_v4()
                    try:
                        res = eng_tmp(padded)
                    finally:
                        _release_v4(eng_tmp)
                if not res or not res.txts:
                    return "", 0.0
                txt = normalize_cjk("".join(res.txts))
                sc = min((float(s) for s in res.scores), default=0.85) if getattr(res, "scores", None) else 0.85
                return txt, sc

            def _run_v5(crop_img: np.ndarray) -> str:
                padded = _pad_crop_to_bucket(crop_img)
                eng = None
                v5_sem.acquire()
                try:
                    with pool_lock:
                        if v5_free:
                            eng = v5_free.pop()
                    if eng is None:
                        with v5_init_lock:
                            eng = _create_rapidocr(
                                OCRVersion.PPOCRV5,
                                intra_op_threads=eff_onnx_threads,
                                use_det=False,
                            )
                            with pool_lock:
                                v5_all.append(eng)
                    res = eng(padded)
                finally:
                    if eng is not None:
                        with pool_lock:
                            v5_free.append(eng)
                    v5_sem.release()
                if not res or not res.txts:
                    return ""
                return normalize_cjk("".join(res.txts))

            gpu_tag = f" | GPU={profile['gpu_name']}" if profile["gpu_name"] else ""
            logger.info(
                f"[VisualSubRefiner] ASR-Guided ({hw_name} | tier={profile['tier']}{gpu_tag}): "
                f"{len(active_segments)} câu ASR -> {len(chunks)} chunk (~{int(profile['chunk_target_sec'])}s/chunk) "
                f"trên {ocr_workers} worker x {eff_onnx_threads}T ONNX | fps={scan_fps} | "
                f"RAM={profile['free_ram_gb']}/{profile['total_ram_gb']}GB | ROI={crop_filter}"
            )
            if progress_cb:
                progress_cb(
                    f"Đang quét phụ đề ({hw_name.upper()} • {ocr_workers} luồng x {len(chunks)} chunk) "
                    f"theo {len(active_segments)} mốc ASR..."
                )

            def _process_window(
                win_start: float,
                win_end: float,
                g_start: int,
                g_end: int,
                c_subs: List[Dict[str, Any]],
                c_stats: Dict[str, int],
            ) -> None:
                win_dur = max(0.2, win_end - win_start)
                fb = band_w * band_h
                frame_q: "queue.Queue[Optional[Tuple[float, np.ndarray]]]" = queue.Queue(
                    maxsize=int(profile["frame_queue_size"])
                )
                stop_event = threading.Event()
                step_sec = 1.0 / scan_fps

                def _put_frame(item: Optional[Tuple[float, np.ndarray]]) -> bool:
                    while not stop_event.is_set():
                        try:
                            frame_q.put(item, timeout=0.25)
                            return True
                        except queue.Full:
                            continue
                    return False

                def _pyav_tail_produce(tail_start: float, tail_end: float) -> None:
                    container = None
                    try:
                        import av

                        container = av.open(video_path)
                        vstream = container.streams.video[0]
                        vstream.thread_type = "AUTO"
                        vstream.thread_count = max(1, cpu_fallback_threads)
                        tb = float(vstream.time_base) if vstream.time_base else (1.0 / 1000.0)
                        seek_ts = int(max(0.0, tail_start - 0.08) / tb)
                        container.seek(seek_ts, stream=vstream, backward=True, any_frame=False)
                        next_emit = tail_start
                        for packet in container.demux(vstream):
                            if stop_event.is_set():
                                break
                            try:
                                frames = packet.decode()
                            except Exception:
                                continue
                            for frame in frames:
                                if stop_event.is_set():
                                    break
                                ft = float(frame.pts * tb) if frame.pts is not None else next_emit
                                if ft + 0.03 < next_emit:
                                    continue
                                if ft > tail_end + 0.25:
                                    return
                                plane = frame.planes[0]
                                arr = np.frombuffer(plane, dtype=np.uint8).reshape(
                                    frame.height, plane.line_size
                                )[:, : frame.width]
                                if frame.width != src_w or frame.height != src_h:
                                    arr = cv2.resize(arr, (src_w, src_h), interpolation=cv2.INTER_AREA)
                                raw_band = arr[by1 : by1 + ch, bx1 : bx1 + cw]
                                if raw_band.shape[1] != band_w or raw_band.shape[0] != band_h:
                                    band = cv2.resize(raw_band, (band_w, band_h), interpolation=cv2.INTER_LINEAR)
                                else:
                                    band = raw_band.copy()
                                if not _put_frame((round(ft, 3), band)):
                                    return
                                c_stats["frames"] += 1
                                next_emit = ft + step_sec - 0.02
                    except Exception as e_av:
                        logger.debug(f"[VisualSubRefiner] PyAV window fallback lỗi ({tail_start}..{tail_end}): {e_av}")
                    finally:
                        if container is not None:
                            try:
                                container.close()
                            except Exception:
                                pass

                def _stream_ffmpeg(
                    cmd_hw_args: List[str],
                    vf_str: str,
                    s_from: float,
                    s_dur: float,
                    dec_threads: int = 0,
                ) -> Tuple[int, float]:
                    proc = None
                    emitted = 0
                    last_t = s_from - step_sec
                    cmd = [*ff_cmd, "-v", "error", *cmd_hw_args]
                    if dec_threads > 0 and not cmd_hw_args:
                        cmd.extend(["-threads", str(dec_threads)])
                    if s_from > 0.02:
                        cmd.extend(["-ss", f"{s_from:.3f}"])
                    cmd.extend(["-t", f"{s_dur:.3f}", "-i", video_path, "-vf", vf_str, "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"])
                    try:
                        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=fb * 8)
                        assert proc.stdout is not None
                        k = 0
                        while not stop_event.is_set():
                            buf = proc.stdout.read(fb)
                            if len(buf) < fb:
                                break
                            ft = round(s_from + k * step_sec, 3)
                            k += 1
                            gray = np.frombuffer(buf, dtype=np.uint8).reshape((band_h, band_w)).copy()
                            if not _put_frame((ft, gray)):
                                break
                            emitted += 1
                            last_t = ft
                            c_stats["frames"] += 1
                    except Exception:
                        pass
                    finally:
                        if proc is not None:
                            try:
                                if proc.stdout:
                                    proc.stdout.close()
                            except Exception:
                                pass
                            if proc.poll() is None:
                                try:
                                    proc.terminate()
                                    proc.wait(timeout=2)
                                except Exception:
                                    try:
                                        proc.kill()
                                        proc.wait(timeout=2)
                                    except Exception:
                                        pass
                    return emitted, last_t

                def _producer() -> None:
                    try:
                        vf_main = _build_decode_vf(hw_name if use_hw_pipe else "cpu", scan_fps, crop_filter)
                        args_main = hw_args if use_hw_pipe else []
                        main_threads = int(profile["ffmpeg_decode_threads"])
                        emitted, last_t = _stream_ffmpeg(
                            args_main,
                            vf_main,
                            win_start,
                            win_dur,
                            dec_threads=main_threads,
                        )
                        if not stop_event.is_set() and last_t < win_end - 1.5:
                            tail_s = win_start if emitted == 0 else max(win_start, last_t + step_sec)
                            tail_d = win_end - tail_s
                            if tail_d >= 0.35:
                                if use_hw_pipe:
                                    e2, last_t2 = _stream_ffmpeg(
                                        [],
                                        f"fps={scan_fps},{crop_filter}",
                                        tail_s,
                                        tail_d,
                                        dec_threads=cpu_fallback_threads,
                                    )
                                    last_t = last_t2 if e2 > 0 else last_t
                                if not stop_event.is_set() and last_t < win_end - 1.5:
                                    tail_s2 = tail_s if (not use_hw_pipe or last_t < tail_s) else (last_t + step_sec)
                                    if win_end - tail_s2 >= 0.35:
                                        _pyav_tail_produce(tail_s2, win_end)
                    finally:
                        try:
                            frame_q.put(None, timeout=1.0)
                        except Exception:
                            pass

                prod_thr = threading.Thread(target=_producer, daemon=True)
                prod_thr.start()

                buf: deque = deque()
                eof = False

                def _read_band_at(
                    t_target: float,
                    pref_range: Optional[Tuple[float, float]] = None,
                    min_keep_t: float = 0.0,
                ) -> Tuple[Optional[float], Optional[np.ndarray]]:
                    nonlocal eof
                    while not eof and (not buf or buf[-1][0] < t_target + 0.35):
                        try:
                            item = frame_q.get(timeout=60.0)
                        except queue.Empty:
                            eof = True
                            break
                        if item is None:
                            eof = True
                            break
                        buf.append(item)

                    while len(buf) > 16 and buf[0][0] < min_keep_t:
                        buf.popleft()

                    if not buf:
                        return None, None

                    if pref_range is not None:
                        p_lo, p_hi = pref_range
                        in_range = [(ft, b) for ft, b in buf if (p_lo + 0.01) <= ft <= (p_hi - 0.01)]
                        if in_range:
                            best_ft, best_b = min(in_range, key=lambda x: abs(x[0] - t_target))
                            return best_ft, best_b

                    best_ft, best_b = min(buf, key=lambda x: abs(x[0] - t_target))
                    if abs(best_ft - t_target) <= 0.38:
                        return best_ft, best_b
                    return None, None

                def _collect_witnesses(
                    base_txt: str,
                    base_mask: np.ndarray,
                    base_gray: np.ndarray,
                    base_crop: Optional[np.ndarray],
                    base_ft: float,
                    target_asr: str,
                    t_lo: float,
                    t_hi: float,
                ) -> List[str]:
                    wits = [base_txt]
                    if not target_asr or base_txt == target_asr:
                        return wits
                    loc_crop = base_crop if base_crop is not None else _crop_from_mask(base_mask, base_gray, band_w, band_h)
                    if loc_crop is None:
                        return wits
                    t5 = _run_v5(loc_crop)
                    c_stats["witness_calls"] += 1
                    if t5:
                        wits.append(t5)
                    if t5 == target_asr or (
                        t5
                        and t5 == base_txt
                        and len(t5) >= max(3, int(len(target_asr) * 0.72))
                        and fuzz.partial_ratio(t5, target_asr) >= 50
                    ):
                        return wits

                    cand_alts: List[float] = []
                    if base_ft + 0.32 <= t_hi:
                        cand_alts.append(min(t_hi - 0.04, max(base_ft + 0.42, (base_ft + t_hi) * 0.5)))
                    if base_ft - 0.32 >= t_lo:
                        cand_alts.append(max(t_lo + 0.04, min(base_ft - 0.42, (t_lo + base_ft) * 0.5)))
                    min_k_t = max(0.0, t_lo - 1.2)
                    for t_alt in cand_alts:
                        ft_alt, g_alt = _read_band_at(t_alt, (t_lo, t_hi), min_keep_t=min_k_t)
                        if ft_alt is None or g_alt is None or abs(ft_alt - base_ft) < 0.22:
                            continue
                        m_alt = _extract_mask(g_alt)
                        if m_alt is None:
                            continue
                        c_alt = _crop_from_mask(m_alt, g_alt, band_w, band_h)
                        if c_alt is None:
                            continue
                        t4_2, _ = _run_v4(c_alt)
                        c_stats["witness_calls"] += 1
                        if t4_2:
                            wits.append(t4_2)
                        if t4_2 != target_asr:
                            t5_2 = _run_v5(c_alt)
                            c_stats["witness_calls"] += 1
                            if t5_2:
                                wits.append(t5_2)
                        break
                    return wits

                def _pick_witness_text(wits_list: List[str], hint_asr: str) -> str:
                    clean = [w for w in wits_list if w]
                    if not clean:
                        return ""
                    if hint_asr:
                        exact = [w for w in clean if w == hint_asr]
                        if exact:
                            return exact[0]
                    cnt = Counter(clean)
                    top_val, top_cnt = cnt.most_common(1)[0]
                    if top_cnt >= 2:
                        return top_val
                    if hint_asr:
                        return max(clean, key=lambda w: _full_align_score(hint_asr, w))
                    return clean[0]

                prev_mask: Optional[np.ndarray] = None
                prev_n_txt: str = ""
                prev_probe_t: float = -999.0

                try:
                    i = g_start
                    while i < g_end:
                        seg = active_segments[i]
                        st = float(seg.start)
                        en = float(seg.end)
                        dur = max(0.05, en - st)
                        min_keep_t = max(0.0, st - 1.2)

                        t_probe = st + min(0.42, max(0.12, dur * 0.45))
                        if t_probe >= en:
                            t_probe = (st + en) * 0.5

                        ft1, gray = _read_band_at(t_probe, (st, en), min_keep_t=min_keep_t)
                        mask = _extract_mask(gray)
                        if mask is None and dur >= 0.45:
                            t_b = st + dur * 0.72
                            ft_b, gray_b = _read_band_at(t_b, (st, en), min_keep_t=min_keep_t)
                            mask_b = _extract_mask(gray_b)
                            if mask_b is not None and ft_b is not None:
                                ft1 = ft_b
                                gray = gray_b
                                mask = mask_b
                                t_probe = t_b

                        if mask is None or gray is None or ft1 is None:
                            prev_mask = None
                            prev_n_txt = ""
                            i += 1
                            continue

                        asr_norm = normalize_cjk(seg.clean_text or seg.text)
                        n_txt = ""
                        crop: Optional[np.ndarray] = None

                        if prev_mask is not None and prev_n_txt and (t_probe - prev_probe_t) <= 3.5:
                            inter = int(np.logical_and(mask, prev_mask).sum())
                            union = int(np.logical_or(mask, prev_mask).sum())
                            if (inter / max(1, union)) >= 0.68:
                                n_txt = prev_n_txt
                                c_stats["reused_iou"] += 1

                        if not n_txt:
                            crop = _crop_from_mask(mask, gray, band_w, band_h)
                            if crop is None:
                                i += 1
                                continue
                            n_txt, _ = _run_v4(crop)
                            c_stats["ocr_calls"] += 1

                            # Nếu đầu câu dính đuôi phụ đề câu trước (chuyển cảnh trễ), lấy mẫu lại ở 74% thời lượng câu
                            if (
                                n_txt
                                and prev_n_txt
                                and fuzz.ratio(n_txt, prev_n_txt) >= 88
                                and dur >= 0.42
                                and asr_norm
                                and fuzz.partial_ratio(asr_norm, n_txt) < 55
                                and pinyin_similarity(asr_norm, n_txt[: len(asr_norm) + 2]) < 0.52
                            ):
                                t_late = st + dur * 0.74
                                ft2_late, g2 = _read_band_at(t_late, (st, en), min_keep_t=min_keep_t)
                                if ft2_late is not None and g2 is not None and abs(ft2_late - ft1) >= 0.22:
                                    m2 = _extract_mask(g2)
                                    if m2 is not None:
                                        c2 = _crop_from_mask(m2, g2, band_w, band_h)
                                        if c2 is not None:
                                            txt2_l, _ = _run_v4(c2)
                                            c_stats["ocr_calls"] += 1
                                            if txt2_l:
                                                n_txt = txt2_l
                                                mask = m2
                                                gray = g2
                                                crop = c2
                                                ft1 = ft2_late
                                                t_probe = t_late

                        if not n_txt:
                            i += 1
                            continue

                        last_mask = mask
                        last_n_txt = n_txt
                        last_probe_t = ft1

                        # Nhảy cóc Lookahead (n -> n + best_k) thắt chặt căn lề phần đuôi còn lại
                        comb_asr = asr_norm
                        best_k = 0
                        if comb_asr and len(comb_asr) < len(n_txt):
                            prefix_o = n_txt[: min(len(n_txt), len(comb_asr) + 2)]
                            is_prefix_ok = (
                                fuzz.partial_ratio(comb_asr, prefix_o) >= 68
                                or pinyin_similarity(comb_asr, prefix_o) >= 0.62
                            )
                            if is_prefix_ok:
                                cur_score = _full_align_score(comb_asr, n_txt)
                                max_k = min(6, g_end - i - 1)
                                for k in range(1, max_k + 1):
                                    gap_k = float(active_segments[i + k].start) - float(
                                        active_segments[i + k - 1].end
                                    )
                                    if gap_k > 1.15:
                                        break
                                    next_norm = normalize_cjk(
                                        active_segments[i + k].clean_text or active_segments[i + k].text
                                    )
                                    if not next_norm or len(next_norm) < 2:
                                        break
                                    cand_comb = comb_asr + next_norm
                                    if len(cand_comb) > len(n_txt) + 3:
                                        break
                                    rem_o = n_txt[max(0, len(comb_asr) - 2) : min(len(n_txt), len(cand_comb) + 3)]
                                    if not rem_o:
                                        break
                                    next_fits = (
                                        fuzz.partial_ratio(next_norm, rem_o) >= 72
                                        or pinyin_similarity(next_norm, rem_o) >= 0.65
                                    )
                                    cand_score = _full_align_score(cand_comb, n_txt)
                                    if next_fits and cand_score >= cur_score + 0.04:
                                        best_k = k
                                        comb_asr = cand_comb
                                        cur_score = cand_score
                                        if len(comb_asr) >= len(n_txt):
                                            break
                                    else:
                                        break

                        sub_start = round(float(active_segments[i].start), 2)
                        sub_end = round(float(active_segments[i + best_k].end), 2)
                        c_stats["skipped_lookahead"] += best_k

                        # K4: Multi-Probe cho câu ASR dài (>= 1.45s hoặc OCR < 72% ASR) có thể chứa 2 phụ đề nối tiếp
                        k4_merged = False
                        wits: List[str] = []
                        if (
                            best_k == 0
                            and len(asr_norm) >= 8
                            and (dur >= 1.65 or len(n_txt) < len(asr_norm) * 0.68)
                            and len(n_txt) <= len(asr_norm) - 4
                        ):
                            t_second = st + dur * 0.74
                            ft2, g2 = _read_band_at(t_second, (st, en), min_keep_t=min_keep_t)
                            if ft2 is not None and g2 is not None and abs(ft2 - ft1) >= 0.25:
                                m2 = _extract_mask(g2)
                                if m2 is not None:
                                    inter12 = int(np.logical_and(mask, m2).sum())
                                    union12 = int(np.logical_or(mask, m2).sum())
                                    if (inter12 / max(1, union12)) < 0.60:
                                        c2 = _crop_from_mask(m2, g2, band_w, band_h)
                                        if c2 is not None:
                                            txt2, _ = _run_v4(c2)
                                            c_stats["ocr_calls"] += 1
                                            next_norm = (
                                                normalize_cjk(
                                                    active_segments[i + 1].clean_text or active_segments[i + 1].text
                                                )
                                                if (i + 1) < g_end
                                                else ""
                                            )
                                            belongs_to_next = bool(
                                                next_norm
                                                and len(txt2) >= 2
                                                and (fuzz.ratio(txt2, next_norm) >= 75 or fuzz.partial_ratio(next_norm, txt2) >= 85)
                                                and fuzz.partial_ratio(txt2, asr_norm) < 60
                                            )
                                            asr_tail = asr_norm[max(0, len(n_txt) - 2) :]
                                            improves_fit = bool(
                                                txt2
                                                and len(n_txt) + len(txt2) <= len(asr_norm) + 5
                                                and fuzz.ratio(txt2, n_txt) < 70
                                                and fuzz.partial_ratio(txt2, n_txt) < 72
                                                and txt2 not in n_txt
                                                and n_txt not in txt2
                                                and not belongs_to_next
                                                and (
                                                    _full_align_score(asr_norm, n_txt + txt2)
                                                    >= _full_align_score(asr_norm, n_txt) + 0.02
                                                    or fuzz.partial_ratio(txt2, asr_tail) >= 62
                                                    or pinyin_similarity(txt2, asr_tail) >= 0.58
                                                )
                                            )
                                            if improves_fit:
                                                c_stats["multi_probe_splits"] += 1
                                                split_idx = min(len(asr_norm), len(n_txt))
                                                asr_h1 = asr_norm[: min(len(asr_norm), split_idx + 2)]
                                                asr_h2 = asr_norm[max(0, split_idx - 2) :]
                                                mid_t = (st + en) * 0.5
                                                wits1 = _collect_witnesses(
                                                    n_txt, mask, gray, crop, ft1, asr_h1, st, mid_t
                                                )
                                                wits2 = _collect_witnesses(
                                                    txt2, m2, g2, c2, ft2, asr_h2, mid_t, en
                                                )
                                                best1 = _pick_witness_text(wits1, asr_h1) or n_txt
                                                best2 = _pick_witness_text(wits2, asr_h2) or txt2
                                                same_sub = (
                                                    fuzz.ratio(best1, best2) >= 55
                                                    or fuzz.partial_ratio(best1, best2) >= 75
                                                    or best1 in best2
                                                    or best2 in best1
                                                    or (len(best1) >= 4 and len(best2) >= 4 and best1[-4:] == best2[-4:])
                                                )
                                                if same_sub:
                                                    wits_merged = wits1 + wits2
                                                    n_txt = _pick_witness_text(wits_merged, asr_norm) or best2
                                                    seen_w = set()
                                                    for w_item in [n_txt] + wits_merged:
                                                        if w_item and w_item not in seen_w:
                                                            seen_w.add(w_item)
                                                            wits.append(w_item)
                                                    last_n_txt = n_txt
                                                else:
                                                    n_txt = f"{best1}{best2}"
                                                    wits_raw = (
                                                        [n_txt]
                                                        + [f"{w1}{best2}" for w1 in wits1]
                                                        + [f"{best1}{w2}" for w2 in wits2]
                                                    )
                                                    seen_w = set()
                                                    for w_item in wits_raw:
                                                        if w_item and w_item not in seen_w:
                                                            seen_w.add(w_item)
                                                            wits.append(w_item)
                                                    last_n_txt = txt2
                                                last_mask = m2
                                                last_probe_t = ft2
                                                k4_merged = True

                        prev_mask = last_mask
                        prev_n_txt = last_n_txt
                        prev_probe_t = last_probe_t

                        # Nếu trùng với sub ngay trước đó trong cùng chunk -> mở rộng mốc kết thúc
                        if (
                            not k4_merged
                            and c_subs
                            and fuzz.ratio(n_txt, c_subs[-1]["n"]) >= 88
                            and (sub_start - c_subs[-1]["end"]) <= 0.85
                        ):
                            c_subs[-1]["end"] = max(c_subs[-1]["end"], sub_end)
                            i += best_k + 1
                            continue

                        # K3: Multi-Witness bắt buộc mọi câu bất đồng n_txt != comb_asr (không tin mù quáng điểm v4)
                        if not k4_merged:
                            wits = _collect_witnesses(
                                n_txt, mask, gray, crop, ft1, comb_asr, sub_start, sub_end
                            )

                        c_subs.append({
                            "id": 0,
                            "start": sub_start,
                            "end": sub_end,
                            "n": n_txt,
                            "wits": wits,
                            "crop": None,
                            "alt_crop": None,
                        })
                        i += best_k + 1
                finally:
                    stop_event.set()
                    try:
                        while True:
                            frame_q.get_nowait()
                    except queue.Empty:
                        pass
                    prod_thr.join(timeout=5.0)

            def _process_chunk(chunk_idx: int, start_idx: int, end_idx: int) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
                c_subs: List[Dict[str, Any]] = []
                c_stats = {
                    "frames": 0,
                    "ocr_calls": 0,
                    "skipped_lookahead": 0,
                    "reused_iou": 0,
                    "witness_calls": 0,
                    "multi_probe_splits": 0,
                    "windows": 0,
                }
                windows = cls._build_dialogue_windows(
                    active_segments[start_idx:end_idx],
                    gap_split=10.0,
                    pad_before=0.35,
                    pad_after=0.45,
                    vid_dur=vid_dur,
                )
                c_stats["windows"] = len(windows)
                chunk_v4 = _acquire_v4()
                tls.eng_v4 = chunk_v4
                try:
                    for w_start, w_end, rel_s, rel_e in windows:
                        _process_window(w_start, w_end, start_idx + rel_s, start_idx + rel_e, c_subs, c_stats)
                finally:
                    tls.eng_v4 = None
                    _release_v4(chunk_v4)
                return c_subs, c_stats

            chunk_results: List[Tuple[int, List[Dict[str, Any]], Dict[str, int]]] = []
            if len(chunks) == 1:
                c_subs, c_st = _process_chunk(0, chunks[0][0], chunks[0][1])
                chunk_results.append((0, c_subs, c_st))
            else:
                with concurrent.futures.ThreadPoolExecutor(max_workers=ocr_workers) as pool:
                    futs = {
                        pool.submit(_process_chunk, idx, s_i, e_i): idx
                        for idx, (s_i, e_i) in enumerate(chunks)
                    }
                    for fut in concurrent.futures.as_completed(futs):
                        idx = futs[fut]
                        c_subs, c_st = fut.result()
                        chunk_results.append((idx, c_subs, c_st))

            chunk_results.sort(key=lambda x: x[0])
            merged_subs: List[Dict[str, Any]] = []
            tot_frames = 0
            tot_ocr = 0
            tot_skipped = 0
            tot_iou = 0
            tot_wit = 0
            tot_mp = 0
            tot_win = 0

            for _, c_subs, c_st in chunk_results:
                tot_frames += c_st["frames"]
                tot_ocr += c_st["ocr_calls"]
                tot_skipped += c_st["skipped_lookahead"]
                tot_iou += c_st["reused_iou"]
                tot_wit += c_st["witness_calls"]
                tot_mp += c_st["multi_probe_splits"]
                tot_win += c_st["windows"]
                for s in c_subs:
                    if (
                        merged_subs
                        and fuzz.ratio(s["n"], merged_subs[-1]["n"]) >= 88
                        and (s["start"] - merged_subs[-1]["end"]) <= 0.85
                    ):
                        merged_subs[-1]["end"] = max(merged_subs[-1]["end"], s["end"])
                        for w in s.get("wits") or []:
                            if w and w not in merged_subs[-1]["wits"]:
                                merged_subs[-1]["wits"].append(w)
                    else:
                        s["id"] = len(merged_subs) + 1
                        merged_subs.append(s)
        finally:
            tls.eng_v4 = None
            v4_free.clear()
            v4_all.clear()
            v5_free.clear()
            v5_all.clear()
            gc.collect()

        t_wall = round(time.time() - t_all, 2)
        logger.info(
            f"[VisualSubRefiner] Hoàn tất ASR-Guided ({hw_name}, {ocr_workers}w/{len(chunks)}chunks, {t_wall}s): "
            f"{len(active_segments)} câu ASR -> {tot_win} cửa sổ, decode {tot_frames} frames, OCR {tot_ocr} lần, "
            f"Multi-Witness {tot_wit} lần, Multi-Probe {tot_mp} câu 2-nửa, nhảy cóc {tot_skipped} câu, IoU {tot_iou}, thu {len(merged_subs)} block sub."
        )
        stats = {
            "frames": tot_frames,
            "raw_blocks": len(merged_subs),
            "t_decode": t_wall,
            "hwaccel": f"{hw_name}_asr_guided",
            "has_hardsub": True,
            "roi": roi,
            "subs_count": len(merged_subs),
            "duration_sec": vid_dur,
            "t_extract_wall": t_wall,
            "mode": "asr_guided_lookahead",
            "ocr_calls": tot_ocr,
            "skipped_lookahead": tot_skipped,
            "reused_iou": tot_iou,
            "witness_calls": tot_wit,
            "multi_probe_splits": tot_mp,
            "windows": tot_win,
            "chunks": len(chunks),
            "ocr_workers": ocr_workers,
            "onnx_threads": eff_onnx_threads,
            "machine_tier": profile["tier"],
            "chunk_target_sec": profile["chunk_target_sec"],
        }
        return merged_subs, stats

    @staticmethod
    def extract_visual_blocks(
        video_path: str,
        fps: float = 2.0,
        max_duration_sec: Optional[float] = None,
        progress_cb: Optional[Callable[[str], None]] = None,
        segments: Optional[List[DialogueSegment]] = None,
        asr_segments_future: Optional[Any] = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Quét phụ đề cứng từ video:
        - Khi có `segments` hoặc `asr_segments_future`: Chạy chế độ ASR-Guided Lookahead Step-Skipping (n -> n+k)
          chia Chunk song song qua PyAV, chỉ lấy đúng các frame tại mốc câu ASR.
        - Khi gọi độc lập không truyền `segments`: Quét theo `fps` (tương thích ngược với các bài test độc lập).
        """
        if not video_path or not os.path.exists(video_path):
            return [], {"skipped": "missing_video"}

        t_all = time.time()
        src_w, src_h, vid_dur, video_codec = _probe_video_info(video_path)
        if progress_cb:
            progress_cb("Đang thăm dò bố cục & kích thước phụ đề riêng của phim...")

        sample_ts: Optional[List[float]] = None
        if segments:
            step = max(1, len(segments) // 12)
            sample_ts = [
                round((float(s.start) + float(s.end)) * 0.5, 2)
                for s in segments[::step][:12]
            ]

        roi = _probe_subtitle_roi(
            video_path=video_path,
            src_w=src_w,
            src_h=src_h,
            vid_dur=vid_dur,
            max_duration_sec=max_duration_sec,
            sample_timestamps=sample_ts,
        )
        if not roi.get("has_hardsub", True):
            logger.info(f"[VisualSubRefiner] Phát hiện video không có hardsub tiếng Trung ({roi.get('mode')}) -> Bỏ qua quét ảnh.")
            return [], {
                "frames": 0,
                "raw_blocks": 0,
                "t_decode": 0.0,
                "hwaccel": "skipped_no_hardsub",
                "has_hardsub": False,
                "roi": roi,
                "subs_count": 0,
                "duration_sec": vid_dur,
                "t_extract_wall": round(time.time() - t_all, 2),
            }

        if segments is None and asr_segments_future is not None:
            try:
                fut_timeout = max(900.0, float(vid_dur or 0.0) * 0.25)
                segments = asr_segments_future.result(timeout=fut_timeout)
            except Exception as e_fut:
                logger.warning(f"[VisualSubRefiner] Không nhận được segments từ asr_segments_future: {e_fut}")
                segments = None

        if segments is not None:
            subs, st = VisualSubRefiner._extract_blocks_asr_guided_parallel(
                video_path=video_path,
                segments=segments,
                roi=roi,
                src_w=src_w,
                src_h=src_h,
                vid_dur=vid_dur,
                max_duration_sec=max_duration_sec,
                progress_cb=progress_cb,
                video_codec=video_codec,
            )
            st["t_extract_wall"] = round(time.time() - t_all, 2)
            return subs, st

        crop_filter, band_w, band_h = _compute_subtitle_band(src_w, src_h, roi=roi)
        hw_name, hw_args = _detect_hwaccel_decode(video_path, codec_name=video_codec)
        logger.info(
            f"[VisualSubRefiner] ROI ({roi.get('mode')}): {src_w}x{src_h} -> {crop_filter} | HW={hw_name} | polarity={roi.get('polarity', 'light')}"
        )

        from rapidocr import OCRVersion

        ff_cmd = get_ffmpeg_cmd()
        eng_v4 = None
        proc = None
        block_q: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue(maxsize=32)
        decode_stats = {
            "frames": 0,
            "raw_blocks": 0,
            "t_decode": 0.0,
            "hwaccel": hw_name,
            "has_hardsub": True,
            "roi": roi,
        }

        small_h = max(56, min(80, int(round(band_h * 0.35))))
        small_w = max(160, int(round(band_w * (small_h / float(band_h)))))
        k_sz = max(7, min(13, int(round(small_h * 0.14)) | 1))
        k_morph = cv2.getStructuringElement(cv2.MORPH_RECT, (k_sz, k_sz))
        is_dark_sub = roi.get("polarity") == "dark"
        p90_v = float(roi.get("p90", 240.0))
        p30_v = float(roi.get("p30", 60.0))
        p10_v = float(roi.get("p10", 30.0))
        lum_thr = max(150, min(205, int(p90_v - 48))) if not is_dark_sub else max(45, min(145, int(p10_v + 45)))
        th_thr = (
            max(22, int((p90_v - p10_v) * 0.25))
            if is_dark_sub
            else max(24, min(42, int((p90_v - p30_v) * 0.22)))
        )
        min_mask_px = max(70, int(small_w * small_h * 0.0022))

        def _compact_block(b: Dict[str, Any]) -> Dict[str, Any]:
            c = b["cands"]
            if len(c) > 3:
                b["cands"] = [c[0], c[len(c) // 2], c[-1]]
            return b

        def _producer() -> None:
            nonlocal proc
            td0 = time.time()
            vf = _build_decode_vf(hw_name, fps, crop_filter)
            cmd = [*ff_cmd, "-v", "error", *hw_args]
            if hw_name == "cpu":
                cmd.extend(["-threads", str(max(1, min(4, (os.cpu_count() or 4) // 2)))])
            if max_duration_sec and max_duration_sec > 0:
                cmd.extend(["-t", str(max_duration_sec)])
            cmd.extend([
                "-i", video_path,
                "-vf", vf,
                "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
            ])

            fb = band_w * band_h
            cur: Optional[Dict[str, Any]] = None
            prev_mask: Optional[np.ndarray] = None
            k = 0
            try:
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=fb * 8
                )
                assert proc.stdout is not None
                while True:
                    buf = proc.stdout.read(fb)
                    if len(buf) < fb:
                        break
                    t = round(k / fps, 3)
                    k += 1
                    gray = np.frombuffer(buf, dtype=np.uint8).reshape((band_h, band_w)).copy()
                    small = cv2.resize(gray, (small_w, small_h), interpolation=cv2.INTER_AREA)
                    if is_dark_sub:
                        bh_m = cv2.morphologyEx(small, cv2.MORPH_BLACKHAT, k_morph)
                        mask = (small < lum_thr) & (bh_m > th_thr)
                    else:
                        th_m = cv2.morphologyEx(small, cv2.MORPH_TOPHAT, k_morph)
                        mask = (small > lum_thr) & (th_m > th_thr)
                    if int(mask.sum()) < min_mask_px:
                        if cur:
                            decode_stats["raw_blocks"] += 1
                            block_q.put(_compact_block(cur))
                            cur = None
                        prev_mask = None
                        continue
                    if cur is None:
                        cur = {"start": t, "end": t + 1.0 / fps, "cands": [(t, mask, gray)]}
                        prev_mask = mask
                        continue
                    inter = int(np.logical_and(mask, prev_mask).sum())
                    union = int(np.logical_or(mask, prev_mask).sum())
                    if (inter / max(1, union)) >= 0.65:
                        cur["end"] = t + 1.0 / fps
                        cur["cands"].append((t, mask, gray))
                        prev_mask = mask
                    else:
                        decode_stats["raw_blocks"] += 1
                        block_q.put(_compact_block(cur))
                        cur = {"start": t, "end": t + 1.0 / fps, "cands": [(t, mask, gray)]}
                        prev_mask = mask
                if cur:
                    decode_stats["raw_blocks"] += 1
                    block_q.put(_compact_block(cur))
            except Exception as e_prod:
                logger.warning(f"[VisualSubRefiner] Lỗi luồng giải mã FFmpeg: {e_prod}")
            finally:
                if proc is not None:
                    try:
                        if proc.stdout:
                            proc.stdout.close()
                    except Exception:
                        pass
                    try:
                        if proc.poll() is None:
                            proc.kill()
                        proc.wait(timeout=3)
                    except Exception:
                        pass
                decode_stats["frames"] = k
                decode_stats["t_decode"] = round(time.time() - td0, 2)
                block_q.put(None)

        subs: List[Dict[str, Any]] = []
        try:
            legacy_threads = max(1, min(3, (os.cpu_count() or 4) - 1))
            eng_v4 = _create_rapidocr(OCRVersion.PPOCRV4, intra_op_threads=legacy_threads, use_det=False)
            if progress_cb:
                progress_cb(f"Đang quét phụ đề ảnh ({hw_name.upper()} + PP-OCRv4 song song)...")

            pt = threading.Thread(target=_producer, daemon=True)
            pt.start()

            while True:
                b = block_q.get()
                if b is None:
                    break
                mid_idx = len(b["cands"]) // 2
                _, mask, gray = b["cands"][mid_idx]
                crop = _crop_from_mask(mask, gray, band_w, band_h)
                if crop is None:
                    continue
                res = eng_v4(_pad_crop_to_bucket(crop))
                if not res or not res.txts:
                    continue
                n_txt = normalize_cjk("".join(res.txts))
                if not n_txt:
                    continue

                alt_crop = None
                if len(b["cands"]) >= 2:
                    alt_idx = 0 if mid_idx > 0 else len(b["cands"]) - 1
                    _, m2, g2 = b["cands"][alt_idx]
                    alt_crop = _crop_from_mask(m2, g2, band_w, band_h)

                if subs and fuzz.ratio(n_txt, subs[-1]["n"]) >= 86 and b["start"] - subs[-1]["end"] <= 0.6:
                    subs[-1]["end"] = round(b["end"], 2)
                    if alt_crop is not None and subs[-1]["alt_crop"] is None:
                        subs[-1]["alt_crop"] = alt_crop
                    continue

                subs.append({
                    "id": len(subs) + 1,
                    "start": round(b["start"], 2),
                    "end": round(b["end"], 2),
                    "n": n_txt,
                    "crop": crop,
                    "alt_crop": alt_crop,
                })
            pt.join()
        finally:
            if proc is not None and proc.poll() is None:
                try:
                    proc.kill()
                    proc.wait(timeout=2)
                except Exception:
                    pass
            eng_v4 = None
            gc.collect()

        stats = {
            **decode_stats,
            "subs_count": len(subs),
            "duration_sec": vid_dur,
            "t_extract_wall": round(time.time() - t_all, 2),
        }
        return subs, stats

    @staticmethod
    def _span_word_strength(sent: str, s: int, e: int) -> Tuple[float, float, List[Tuple[str, int, int]]]:
        tks_pos = list(jieba.tokenize(sent))
        tks = [tk for tk, a, b in tks_pos if b > s and a < e]
        mz = [
            zipf_frequency(tk, "zh") + (1.5 if len(tk) >= 3 else 0.0)
            for tk in tks
            if len(tk) >= 2 and (zipf_frequency(tk, "zh") > 2.2 or (len(tk) >= 3 and zipf_frequency(tk, "zh") > 0.5))
        ]
        idiom_zf = 0.0
        for L in (4, 3):
            for p in range(max(0, s - L + 1), min(len(sent) - L + 1, e)):
                if p + L > s and p < e:
                    zf = zipf_frequency(sent[p:p + L], "zh")
                    if zf > idiom_zf:
                        idiom_zf = zf
        bgs = []
        for p in range(max(0, s - 1), min(len(sent) - 1, e)):
            if p + 2 > s and p < e:
                bg = sent[p:p + 2]
                if not (p == 0 and sent[0] == "一" and tks_pos and tks_pos[0][0] == "一"):
                    bgs.append(zipf_frequency(bg, "zh"))
        sz = zipf_frequency(sent[s:e], "zh") if (e - s) == 1 else 0.0
        best_m = max(max(mz, default=0.0), max(bgs, default=0.0), (idiom_zf + 2.5 if idiom_zf >= 1.0 else 0.0))
        return best_m, sz, tks_pos

    @staticmethod
    def _trim_adjacent_bleed(final_s: str, a_s: str, prev_a: str, next_a: str) -> str:
        """Cắt phần chữ tràn từ câu liền trước/liền sau do phụ đề OCR hiện gộp 2 câu."""
        if not final_s or not a_s:
            return final_s

        min_rem = max(2, len(a_s) // 2)

        def _find_next_bleed(allow_fuzzy: bool) -> int:
            max_l = min(len(final_s) - min_rem, len(next_a) + (2 if allow_fuzzy else 0))
            for l in range(max_l, 2, -1):
                suf = final_s[-l:]
                if a_s.endswith(suf):
                    continue
                if len(a_s) >= l and pinyin_similarity(suf, a_s[-l:]) >= 0.75:
                    continue
                if len(final_s) - l < len(a_s) and pinyin_similarity(suf[0], a_s[-1]) >= 0.75:
                    continue
                if next_a.startswith(suf):
                    return l
                if allow_fuzzy and len(next_a) >= l:
                    if (
                        pinyin_similarity(suf[0], next_a[0]) >= 0.6
                        and pinyin_similarity(suf[-1], next_a[l - 1]) >= 0.6
                        and pinyin_similarity(suf, next_a[:l]) >= 0.85
                    ):
                        return l
            return 0

        def _find_prev_bleed(allow_fuzzy: bool) -> int:
            max_l = min(len(final_s) - min_rem, len(prev_a) + (2 if allow_fuzzy else 0))
            for l in range(max_l, 2, -1):
                pref = final_s[:l]
                if a_s.startswith(pref):
                    continue
                if len(a_s) >= l and pinyin_similarity(pref, a_s[:l]) >= 0.75:
                    continue
                if len(final_s) - l < len(a_s) and pinyin_similarity(pref[-1], a_s[0]) >= 0.75:
                    continue
                if prev_a.endswith(pref):
                    return l
                if allow_fuzzy and len(prev_a) >= l:
                    if (
                        pinyin_similarity(pref[0], prev_a[-l]) >= 0.6
                        and pinyin_similarity(pref[-1], prev_a[-1]) >= 0.6
                        and pinyin_similarity(pref, prev_a[-l:]) >= 0.85
                    ):
                        return l
            return 0

        if next_a and len(next_a) >= 3 and len(final_s) > min_rem + 2:
            cut_n = _find_next_bleed(False) or _find_next_bleed(True)
            if cut_n:
                final_s = final_s[:-cut_n]

        if prev_a and len(prev_a) >= 3 and len(final_s) > min_rem + 2:
            cut_p = _find_prev_bleed(False) or _find_prev_bleed(True)
            if cut_p:
                final_s = final_s[cut_p:]

        return final_s

    @classmethod
    def _choose_span(
        cls,
        asr_s: str,
        ocr_s: str,
        wits_flat: List[str],
        ngram_counts: Counter,
        shape_cmp: _ShapeComparator,
    ) -> Tuple[str, bool]:
        non_empty_wits = [w for w in wits_flat if w]
        ocr_unanimous = len(non_empty_wits) >= 2 and len(set(non_empty_wits)) == 1
        sm = difflib.SequenceMatcher(None, asr_s, ocr_s)
        out: List[str] = []
        unc = False

        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            a_seg, o_seg = asr_s[i1:i2], ocr_s[j1:j2]
            if tag == "equal":
                out.append(a_seg)
                continue
            if ("7" in o_seg and "了" in a_seg and "7" not in a_seg) or (tag == "insert" and o_seg in ("6", "7")):
                out.append(a_seg)
                continue
            if tag == "delete" and len(a_seg) == 1 and ((i1 > 0 and asr_s[i1 - 1] == a_seg) or (i2 < len(asr_s) and asr_s[i2] == a_seg)):
                continue
            ctx_a = asr_s[max(0, i1 - 1):min(len(asr_s), i2 + 1)]
            if len(ctx_a) >= 2 and any(ctx_a in w for w in non_empty_wits):
                out.append(a_seg)
                continue

            # Khóa bảo vệ sườn ASR (K1): Tuyệt đối không xóa cả mảng chữ ASR dài bằng mảnh OCR cụt ngắn hơn
            if tag == "replace" and (len(a_seg) >= len(o_seg) + 2 or (len(a_seg) >= 2 and len(o_seg) <= 1)):
                out.append(a_seg)
                continue

            # Chặn ký tự Latinh/rác đơn lẻ đè lên chữ Hán của ASR
            if tag == "replace" and len(o_seg) == 1 and o_seg.isascii() and o_seg.isalpha() and a_seg and not a_seg.isascii():
                out.append(a_seg)
                continue

            psim = pinyin_similarity(a_seg, o_seg) if (a_seg and o_seg) else 0.0
            vsim = shape_cmp.span_similarity(a_seg, o_seg) if (a_seg and o_seg) else 0.0
            wA, sA, a_tks = cls._span_word_strength(asr_s, i1, i2) if a_seg else (0.0, 0.0, [])
            wO, sO, o_tks = cls._span_word_strength(ocr_s, j1, j2) if o_seg else (0.0, 0.0, [])
            a_4char = any(len(tk) >= 4 for tk, a, b in a_tks if b > i1 and a < i2)
            o_4char = any(len(tk) >= 4 for tk, a, b in o_tks if b > j1 and a < j2)
            mzA = max(
                (zipf_frequency(tk, "zh") for tk, a, b in a_tks if b > i1 and a < i2 and len(tk) >= 2),
                default=0.0,
            )
            mzO = max(
                (zipf_frequency(tk, "zh") for tk, a, b in o_tks if b > j1 and a < j2 and len(tk) >= 2),
                default=0.0,
            )
            o_ent = (
                ngram_counts.get(o_seg, 0)
                if len(o_seg) >= 2
                else max(
                    [ngram_counts.get(bg, 0) for bg in (ocr_s[max(0, j1 - 1):j1 + 1], ocr_s[j1:j1 + 2]) if len(bg) == 2],
                    default=0,
                )
            )
            span_verified = ocr_unanimous or (
                len(non_empty_wits) >= 2 and sum(1 for w in non_empty_wits if o_seg and o_seg in w) >= 2
            )

            if tag == "replace" and o_4char and not a_4char and vsim < 0.65:
                out.append(o_seg)
            elif o_ent >= 2 and psim >= 0.45 and vsim < 0.72 and not (wA >= 3.5 and wO == 0):
                out.append(o_seg)
            elif tag == "replace" and vsim < 0.49 and mzO >= 3.2 and mzA == 0.0:
                out.append(o_seg)
            elif psim >= 0.70 and vsim < 0.75:
                if wA >= 3.2 and wO == 0 and o_ent == 0 and vsim >= 0.48:
                    out.append(a_seg)
                elif wA > 2.8 and wO == 0 and len(a_seg) > len(o_seg) and o_ent == 0:
                    out.append(a_seg)
                elif not span_verified and o_ent == 0 and (wA + 0.5 * sA) >= (wO + 0.5 * sO) + 0.6:
                    # ASR đọc đúng & tự nhiên hơn hẳn bản OCR đơn lẻ chưa có Multi-Witness đồng thuận -> giữ ASR
                    out.append(a_seg)
                else:
                    out.append(o_seg)
            elif vsim >= 0.49 and psim < 0.65:
                # Khác âm đọc (không phải đồng âm ASR) + gần nét: OCR rất dễ nhìn nhầm -> giữ ASR trừ khi có đồng thuận + nghĩa vượt trội
                if not span_verified and o_ent < 2 and wO < wA + 1.2:
                    out.append(a_seg)
                elif (wA >= 2.5 and wO < wA - 0.3) or (sA >= 4.2 and sO < sA - 0.8) or (wO == 0 and wA > 0):
                    out.append(a_seg)
                elif wO >= 3.2 and wA == 0:
                    out.append(o_seg)
                else:
                    out.append(a_seg if (wA + 0.5 * sA) >= (wO + 0.5 * sO) else o_seg)
            elif wO >= wA + 0.6 and tag == "replace" and wO >= 2.4 and (span_verified or psim >= 0.60 or o_ent >= 2):
                out.append(o_seg)
            elif wA >= 3.0 and wO == 0 and o_ent == 0 and tag == "replace":
                out.append(a_seg)
            elif tag == "replace":
                # Khi không đồng âm lẫn không rõ hơn về từ vựng -> ưu tiên giữ nguyên ASR, không tự ý đè
                if (span_verified or o_ent >= 2) and (wO + 0.4 * len(o_seg)) > (wA + 0.4 * len(a_seg)) + 1.0:
                    out.append(o_seg)
                else:
                    out.append(a_seg)
            elif tag == "insert":
                if (o_seg == "一" and j1 == 0 and o_tks and o_tks[0][0] == "一") or (
                    len(o_seg) == 1 and o_seg.isascii() and o_seg.isalpha()
                ):
                    continue
                elif o_seg == "一" and mzO < 3.5:
                    continue
                elif len(o_seg) >= 1 and (wO >= 2.2 or len(o_seg) >= 2):
                    out.append(o_seg)
                else:
                    out.append(a_seg)
            elif tag == "delete":
                fixed_ent = None
                if len(a_seg) == 1:
                    for cand_bg in (asr_s[max(0, i1 - 1):i1 + 1], asr_s[i1:min(len(asr_s), i1 + 2)]):
                        if len(cand_bg) == 2:
                            anchor_pos = 0 if cand_bg == asr_s[max(0, i1 - 1):i1 + 1] else 1
                            anchor_ch = cand_bg[anchor_pos]
                            for ent, cnt in ngram_counts.items():
                                if cnt >= 3 and len(ent) == 2 and ent[anchor_pos] == anchor_ch and pinyin_similarity(a_seg, ent[1 - anchor_pos]) >= 0.70:
                                    fixed_ent = ent[1 - anchor_pos]
                                    break
                        if fixed_ent:
                            break
                if fixed_ent:
                    out.append(fixed_ent)
                elif len(a_seg) >= 3:
                    # Không bao giờ xóa mảng dài (>=3 chữ) của ASR chỉ vì OCR đọc sót
                    out.append(a_seg)
                elif ocr_unanimous and len(a_seg) == 1 and wA < 2.6:
                    continue
                elif len(a_seg) == 1 and wA < 2.2:
                    continue
                else:
                    out.append(a_seg)
            else:
                out.append(a_seg)
                unc = True

        return "".join(out), unc

    @classmethod
    def refine_asr_segments(
        cls,
        segments: List[DialogueSegment],
        subs: List[Dict[str, Any]],
        video_path: Optional[str] = None,
        use_cache: bool = True,
    ) -> Tuple[List[DialogueSegment], Dict[str, Any]]:
        """
        Gom câu CapCut ASR bị chặt lẻ nằm cùng 1 phụ đề ảnh và sửa lỗi đồng âm 100% Offline.
        Luôn bảo toàn trình tự & mốc thời gian gốc của CapCut ASR.
        """
        if not segments:
            return segments, {"total_in": 0, "total_out": 0}

        # Đọc từ cache multi-witness nếu cùng video và có file cache hợp lệ
        cached_wits: Dict[int, List[str]] = {}
        cache_file = _get_cache_path(video_path) if (video_path and use_cache) else None
        if cache_file and cache_file.exists():
            try:
                cdata = json.loads(cache_file.read_text(encoding="utf-8"))
                if not subs:
                    subs = cdata.get("subs", [])
                cached_wits = {int(k): v for k, v in (cdata.get("dispute_cache") or {}).items()}
            except Exception as e_c:
                logger.warning(f"[VisualSubRefiner] Lỗi đọc cache: {e_c}")

        if not subs and video_path and os.path.exists(video_path):
            subs, _ = cls.extract_visual_blocks(video_path=video_path, segments=segments)

        if not subs:
            return segments, {"total_in": len(segments), "total_out": len(segments), "visual_subs": 0}

        t0 = time.time()
        PAD = 0.04
        subs_sorted = sorted(subs, key=lambda x: (float(x["start"]), float(x["end"])))
        asr_subs: List[List[int]] = []
        sub_ptr = 0
        prev_seg_start = -1.0
        n_subs_sorted = len(subs_sorted)
        for seg in segments:
            an = normalize_cjk(seg.clean_text or seg.text)
            seg_start = float(seg.start)
            seg_end = float(seg.end)
            dur_a = max(0.1, seg_end - seg_start)
            if seg_start < prev_seg_start:
                sub_ptr = 0
            prev_seg_start = seg_start
            while sub_ptr < n_subs_sorted and (subs_sorted[sub_ptr]["end"] + PAD) <= seg_start + 0.12:
                sub_ptr += 1
            cands = []
            for k in range(sub_ptr, n_subs_sorted):
                s = subs_sorted[k]
                if (s["start"] - PAD) >= seg_end - 0.12:
                    break
                ov = min(seg_end, s["end"] + PAD) - max(seg_start, s["start"] - PAD)
                if ov > 0.12:
                    ov_r = ov / dur_a
                    sim_p = fuzz.partial_ratio(an, s["n"]) / 100.0 if an else 0.0
                    cands.append((s, ov, ov_r, sim_p))
            cands.sort(key=lambda x: x[1], reverse=True)
            matched = []
            for idx, (s, ov, ov_r, sim_p) in enumerate(cands):
                if ov_r >= 0.58 or (ov >= 0.35 and sim_p >= 0.60) or (idx == 0 and ov_r >= 0.45):
                    matched.append(s["id"])
            matched.sort()
            asr_subs.append(matched)

        sub_by_id = {s["id"]: s for s in subs}
        units: List[Dict[str, Any]] = []
        i = 0
        n_seg = len(segments)
        while i < n_seg:
            m = asr_subs[i]
            if len(m) == 1:
                sid = m[0]
                j = i + 1
                while j < n_seg:
                    gap = float(segments[j].start) - float(segments[j - 1].end)
                    same_sub = (asr_subs[j] == [sid]) or (sid in asr_subs[j] and gap <= 0.75)
                    if same_sub and gap <= 0.85:
                        j += 1
                    elif gap <= 1.15 and sid in (asr_subs[j] or []):
                        comb = "".join(normalize_cjk(x.clean_text or x.text) for x in segments[i:j + 1])
                        if fuzz.partial_ratio(comb, sub_by_id[sid]["n"]) >= 82:
                            j += 1
                        else:
                            break
                    else:
                        break
                grp = segments[i:j]
                units.append({
                    "grp": grp,
                    "asr_txt": "".join(normalize_cjk(x.clean_text or x.text) for x in grp),
                    "sub_ids": [sid],
                    "sub_txt": sub_by_id[sid]["n"],
                })
                i = j
            else:
                seg = segments[i]
                units.append({
                    "grp": [seg],
                    "asr_txt": normalize_cjk(seg.clean_text or seg.text),
                    "sub_ids": m,
                    "sub_txt": "".join(sub_by_id[sid]["n"] for sid in m),
                })
                i += 1

        # Bước Multi-Witness (nạp sẵn wits từ ASR-Guided mode hoặc đọc bổ sung PP-OCRv5 & frame phụ trên câu bất đồng)
        dispute_cache: Dict[int, List[str]] = dict(cached_wits)
        for s in subs:
            if s.get("wits") and s["id"] not in dispute_cache:
                dispute_cache[s["id"]] = list(s["wits"])

        disputed_map: Dict[int, set] = {}
        for u in units:
            if u["sub_txt"] and u["asr_txt"] != u["sub_txt"]:
                for sid in u["sub_ids"]:
                    if sid not in dispute_cache and sub_by_id[sid].get("crop") is not None:
                        disputed_map.setdefault(sid, set()).add(u["asr_txt"])

        t_mw = 0.0
        if disputed_map:
            t_mw0 = time.time()
            from rapidocr import OCRVersion

            eng_v4 = None
            eng_v5 = None
            mw_threads = max(1, min(3, (os.cpu_count() or 4) // 2))
            try:
                eng_v4 = _create_rapidocr(OCRVersion.PPOCRV4, intra_op_threads=mw_threads, use_det=False)
                eng_v5 = _create_rapidocr(OCRVersion.PPOCRV5, intra_op_threads=mw_threads, use_det=False)

                def _matched_asr(txt: str, cands: set) -> bool:
                    return any(txt == ac or (len(txt) >= 2 and txt in ac) for ac in cands)

                for sid, asr_cands in sorted(disputed_map.items()):
                    s = sub_by_id[sid]
                    readings = [s["n"]]
                    r5 = eng_v5(_pad_crop_to_bucket(s["crop"]))
                    t5 = normalize_cjk("".join(r5.txts)) if r5 and r5.txts else ""
                    if t5:
                        readings.append(t5)
                    if t5 and _matched_asr(t5, asr_cands):
                        dispute_cache[sid] = readings
                        continue
                    alt_crop = s.get("alt_crop")
                    if alt_crop is not None:
                        r4_2 = eng_v4(_pad_crop_to_bucket(alt_crop))
                        t4_2 = normalize_cjk("".join(r4_2.txts)) if r4_2 and r4_2.txts else ""
                        if t4_2:
                            readings.append(t4_2)
                        if t4_2 and _matched_asr(t4_2, asr_cands):
                            dispute_cache[sid] = readings
                            continue
                        r5_2 = eng_v5(_pad_crop_to_bucket(alt_crop))
                        t5_2 = normalize_cjk("".join(r5_2.txts)) if r5_2 and r5_2.txts else ""
                        if t5_2:
                            readings.append(t5_2)
                    dispute_cache[sid] = readings
            finally:
                eng_v4 = None
                eng_v5 = None
                gc.collect()
            t_mw = round(time.time() - t_mw0, 2)

        # Cập nhật lại câu OCR tốt nhất cho từng unit & giải phóng ảnh crop khỏi RAM
        for u in units:
            if u["sub_txt"] and u["asr_txt"] != u["sub_txt"]:
                best_per_sid: List[str] = []
                all_wits: List[List[str]] = []
                for sid in u["sub_ids"]:
                    wits = dispute_cache.get(sid, [sub_by_id[sid]["n"]])
                    all_wits.append(wits)
                    exact = [w for w in wits if w and (w == u["asr_txt"] or w in u["asr_txt"])]
                    if exact:
                        best_per_sid.append(exact[0])
                    else:
                        cnt = Counter(w for w in wits if w)
                        best_per_sid.append(cnt.most_common(1)[0][0] if cnt else sub_by_id[sid]["n"])
                u["sub_txt"] = "".join(best_per_sid)
                u["wits"] = all_wits
            else:
                u["wits"] = []

        if cache_file and use_cache and not cache_file.exists():
            try:
                serializable_subs = [
                    {"id": s["id"], "start": s["start"], "end": s["end"], "n": s["n"]}
                    for s in subs
                ]
                cache_file.write_text(
                    json.dumps(
                        {"subs": serializable_subs, "dispute_cache": {str(k): v for k, v in dispute_cache.items()}},
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
            except Exception as e_save:
                logger.warning(f"[VisualSubRefiner] Không thể ghi cache: {e_save}")

        for s in subs:
            s.pop("crop", None)
            s.pop("alt_crop", None)

        ngram_counts: Counter = Counter()
        for u in units:
            st = u["sub_txt"]
            for n_gram in (2, 3):
                for k in range(max(0, len(st) - n_gram + 1)):
                    ngram_counts[st[k:k + n_gram]] += 1

        shape_cmp = _ShapeComparator()
        stats = {
            "total_in": len(segments),
            "total_out": len(units),
            "visual_subs": len(subs),
            "exact": 0,
            "multi_witness_saved": 0,
            "bleed_guarded": 0,
            "safe_superset": 0,
            "auto_fused": 0,
            "unmatched": 0,
            "alias_polished": 0,
            "t_multi_witness": t_mw,
        }

        try:
            for idx_u, u in enumerate(units):
                a_s, o_s = u["asr_txt"], u["sub_txt"]
                prev_a = units[idx_u - 1]["asr_txt"] if idx_u > 0 else ""
                next_a = units[idx_u + 1]["asr_txt"] if idx_u + 1 < len(units) else ""
                prev_o = units[idx_u - 1]["sub_txt"] if idx_u > 0 else ""
                next_o = units[idx_u + 1]["sub_txt"] if idx_u + 1 < len(units) else ""

                if not o_s:
                    u["final"] = a_s or (u["grp"][0].clean_text or u["grp"][0].text)
                    u["status"] = "UNMATCHED"
                    stats["unmatched"] += 1
                    continue

                if a_s == o_s or a_s.replace("10", "十") == o_s.replace("10", "十"):
                    u["final"] = a_s
                    u["status"] = "EXACT"
                    stats["exact"] += 1
                    if u["wits"]:
                        stats["multi_witness_saved"] += 1
                    continue

                # Guard 1: Chặn bleed sang câu thoại khác hoặc mismatch hoàn toàn (<= 1 ký tự chung + < 25% text/pinyin)
                common_chars = set(a_s) & set(o_s)
                if (len(common_chars) == 0 and len(set(to_pinyin_list(a_s)) & set(to_pinyin_list(o_s))) == 0) or (
                    len(common_chars) <= 1 and fuzz.ratio(a_s, o_s) < 25 and pinyin_similarity(a_s, o_s) < 0.35
                ):
                    u["final"] = a_s
                    u["status"] = "TURN_BLEED_KEEP_ASR"
                    stats["bleed_guarded"] += 1
                    continue

                # Guard 2: Superset không lặp đuôi / không dính nhiễu biên 1 ký tự qua biên câu kề
                if a_s in o_s or o_s in a_s:
                    if len(o_s) > len(a_s) and a_s in o_s:
                        pos = o_s.find(a_s)
                        pref, suff = o_s[:pos], o_s[pos + len(a_s):]
                        if pref.startswith("一") and len(pref) > 1 and not any(t[0].startswith(pref) for t in jieba.tokenize(o_s)):
                            pref = pref[1:]
                        if (pref == "一" and a_s) or (
                            len(pref) == 1 and pref.isascii() and pref.isalpha() and not (prev_a and pref in prev_a[-3:])
                        ):
                            pref = ""
                        elif pref and prev_a and (pref in prev_a[-len(pref) - 3:] or prev_a[-1:] in pref):
                            if not (len(pref) == 1 and prev_o and prev_a.endswith(pref) and not prev_o.endswith(pref)):
                                pref = ""
                        if (len(suff) == 1 and suff.isascii() and suff.isalpha() and not (next_a and suff in next_a[:3])) or (
                            suff == "一" and a_s and not any(len(t[0]) >= 2 and t[2] == len(a_s[-2:]) + 1 for t in jieba.tokenize(a_s[-2:] + suff))
                        ):
                            suff = ""
                        elif suff and next_a and (suff in next_a[:len(suff) + 3] or next_a[:2] in suff or suff[:2] in next_a):
                            suff = ""
                        u["final"] = pref + a_s + suff
                    else:
                        res_s = a_s
                        if o_s in a_s and a_s.startswith(o_s):
                            tail = a_s[len(o_s):]
                            if len(tail) == 1 and (next_a.startswith(tail) or next_o.startswith(tail)):
                                res_s = o_s
                        u["final"] = res_s
                    u["status"] = "SAFE_SUPERSET"
                    stats["safe_superset"] += 1
                    continue

                # Guard 3 (K2): Bảo vệ ASR khi OCR bị cụt nặng (< 75% độ dài ASR và hụt >= 3 ký tự)
                trimmed_asr = cls._trim_adjacent_bleed(a_s, a_s, prev_a, next_a)
                if len(a_s) >= len(o_s) + 3 and len(o_s) < len(a_s) * 0.75 and fuzz.partial_ratio(o_s, a_s) < 60:
                    u["final"] = trimmed_asr
                    u["status"] = "TRUNCATED_OCR_KEEP_ASR"
                    stats["asr_kept_longer"] = stats.get("asr_kept_longer", 0) + 1
                    continue

                wits_flat = [w for wl in u["wits"] for w in wl]
                final_s, _ = cls._choose_span(a_s, o_s, wits_flat, ngram_counts, shape_cmp)
                final_s = cls._trim_adjacent_bleed(final_s, a_s, prev_a, next_a)
                if len(trimmed_asr) - len(final_s) >= 2:
                    final_s = trimmed_asr
                u["final"] = final_s
                u["status"] = "AUTO_FUSED"
                stats["auto_fused"] += 1

            # Quét nốt các câu UNMATCHED có biến thể đồng âm của thực thể lặp >= 3 lần ở bản sub đọc được
            canon_ents: Counter = Counter()
            for u in units:
                if u["status"] != "UNMATCHED":
                    f = u["final"]
                    for k in range(len(f) - 1):
                        bg = f[k:k + 2]
                        if ngram_counts.get(bg, 0) >= 3:
                            canon_ents[bg] += 1

            for u in units:
                if u["status"] == "UNMATCHED" and len(u["final"]) >= 2:
                    f = u["final"]
                    tks_f = list(jieba.tokenize(f))
                    for k in range(len(f) - 1):
                        bg = f[k:k + 2]
                        if ngram_counts.get(bg, 0) == 0:
                            protected_bg = any(
                                len(tk) >= 2
                                and b > k
                                and a < (k + 2)
                                and zipf_frequency(tk, "zh") >= 3.0
                                for tk, a, b in tks_f
                            )
                            if protected_bg:
                                continue
                            for ent, cnt in canon_ents.most_common():
                                if cnt >= 3:
                                    diffs = [(c1, c2) for c1, c2 in zip(bg, ent) if c1 != c2]
                                    if len(diffs) == 1:
                                        dc1, dc2 = diffs[0]
                                        if (
                                            pinyin_similarity(bg, ent) >= 0.72
                                            and pinyin_similarity(dc1, dc2) >= 0.72
                                            and shape_cmp.span_similarity(dc1, dc2) < 0.70
                                        ):
                                            u["final"] = f.replace(bg, ent, 1)
                                            u["status"] = "ALIAS_POLISHED"
                                            stats["alias_polished"] += 1
                                            stats["unmatched"] -= 1
                                            break
                        if u["status"] == "ALIAS_POLISHED":
                            break
        finally:
            shape_cmp.clear()

        refined_segments: List[DialogueSegment] = []
        for idx, u in enumerate(units, 1):
            grp = u["grp"]
            final_text = u["final"].strip() or (grp[0].clean_text or grp[0].text)
            start_t = round(float(grp[0].start), 3)
            end_t = round(max(start_t + 0.08, float(grp[-1].end)), 3)
            confs = [float(g.confidence) for g in grp if getattr(g, "confidence", -1.0) is not None and float(g.confidence) >= 0.0]
            conf = round(sum(confs) / len(confs), 4) if confs else -1.0
            refined_segments.append(
                DialogueSegment(
                    id=idx,
                    start=start_t,
                    end=end_t,
                    duration=round(end_t - start_t, 3),
                    text=final_text,
                    clean_text=final_text,
                    translated_text="",
                    confidence=conf,
                )
            )

        # Cầu chì an toàn toàn phim: nếu OCR làm hụt lượng chữ >22% hoặc tỷ lệ khớp thực tế <25% -> trả nguyên ASR gốc
        orig_chars = sum(len(normalize_cjk(s.clean_text or s.text)) for s in segments)
        ref_chars = sum(len(normalize_cjk(s.clean_text or s.text)) for s in refined_segments)
        confirmed = stats["exact"] + stats["auto_fused"] + stats["safe_superset"] + stats["alias_polished"]
        confirm_ratio = confirmed / max(1, len(units))
        if len(segments) >= 25 and orig_chars > 0 and (
            ref_chars < orig_chars * 0.78 or confirm_ratio < 0.25
        ):
            logger.warning(
                f"[VisualSubRefiner] Safety Circuit Breaker kích hoạt (chars={ref_chars}/{orig_chars}, "
                f"confirm={confirm_ratio:.1%}) -> Hoàn nguyên 100% CapCut ASR gốc."
            )
            stats["circuit_breaker_triggered"] = True
            stats["total_out"] = len(segments)
            stats["t_arbiter"] = round(time.time() - t0, 2)
            return segments, stats

        stats["circuit_breaker_triggered"] = False
        stats["t_arbiter"] = round(time.time() - t0, 2)
        return refined_segments, stats

    @classmethod
    def try_load_cached_subs(cls, video_path: str) -> List[Dict[str, Any]]:
        """Tải danh sách sub ảnh từ cache nếu video đã từng quét trước đó."""
        if not video_path or not os.path.exists(video_path):
            return []
        cf = _get_cache_path(video_path)
        if not cf.exists():
            return []
        try:
            data = json.loads(cf.read_text(encoding="utf-8"))
            return data.get("subs", [])
        except Exception:
            return []
