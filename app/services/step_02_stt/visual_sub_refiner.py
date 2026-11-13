"""
Bộ tinh chỉnh phụ đề CapCut ASR dựa trên phụ đề cứng (Hardsub) trên video — 100% Offline (Zero-LLM).

Kiến trúc tối ưu hiệu năng & bộ nhớ:
1. Giải mã phần cứng thích ứng (Intel QSV -> NVIDIA CUDA -> CPU), đặt `fps=2` trước `hwdownload`
   để chỉ tải tối thiểu frame cần thiết sang RAM dưới dạng kênh grayscale (`gray`).
2. Producer-Consumer gối đầu: FFmpeg vừa xuất xong block phụ đề là `PP-OCRv4 rec-only` đọc ngay.
3. Chỉ gọi Multi-Witness (`PP-OCRv5_MOBILE` + frame phụ thứ 2, dừng sớm khi khớp ASR) trên các câu bất đồng.
4. Trọng tài Offline 4 Tín hiệu (Pinyin nhiều chữ số, Hình dạng nét chữ 32x32, `jieba/wordfreq` + Thành ngữ 4 chữ,
   Tần suất thực thể lặp xuyên phim) + Guard chống Bleed & chống lặp đuôi (`SAFE_SUPERSET`).
5. Cache JSON theo file video và giải phóng triệt để FFmpeg / ONNX trong khối `finally`.
"""

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
from collections import Counter
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

from app.config import TEMP_DIR
from app.schemas.transcript import DialogueSegment
from app.utils.bin_helper import get_ffmpeg_cmd, get_ffprobe_cmd

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

_CACHE_VERSION = "v7.0"


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


def _probe_video_geometry(video_path: str) -> Tuple[int, int, float]:
    """Đọc chiều rộng, chiều cao và thời lượng video qua FFprobe."""
    fp_cmd = get_ffprobe_cmd()
    try:
        out = subprocess.check_output(
            [
                *fp_cmd, "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height:format=duration",
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
        return max(16, w), max(16, h), dur
    except Exception:
        return 1920, 1080, 0.0


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
) -> Dict[str, Any]:
    """
    Pha 1: Thăm dò nhanh 12 khung hình rải đều bằng RapidOCR để tự khóa vùng phụ đề thực tế,
    loại bỏ logo/chữ tĩnh góc màn hình, đo kích thước chữ & độ sáng và thoát sớm nếu phim không có hardsub.
    """
    from rapidocr import ModelType, OCRVersion, RapidOCR

    eff_dur = float(vid_dur or 0.0)
    if max_duration_sec and max_duration_sec > 0:
        eff_dur = min(eff_dur, float(max_duration_sec)) if eff_dur > 0 else float(max_duration_sec)
    if eff_dur <= 1.0:
        eff_dur = 60.0

    start_t = min(4.0, eff_dur * 0.08)
    end_t = max(start_t + 1.0, eff_dur * 0.90)
    n_samples = 12
    sample_ts = [round(start_t + (end_t - start_t) * (i / max(1, n_samples - 1)), 2) for i in range(n_samples)]

    det_ocr = None
    cap = None
    decoded_frames = 0
    raw_boxes: List[Dict[str, Any]] = []
    try:
        det_ocr = RapidOCR(params={
            "Global.use_cls": False,
            "Rec.ocr_version": OCRVersion.PPOCRV4,
            "Rec.model_type": ModelType.MOBILE,
        })
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return {"has_hardsub": True, "mode": "cap_open_fallback"}

        for f_idx, ts in enumerate(sample_ts):
            cap.set(cv2.CAP_PROP_POS_MSEC, ts * 1000.0)
            ok, img = cap.read()
            if not ok or img is None:
                continue
            decoded_frames += 1
            ih, iw = img.shape[:2]
            res = det_ocr(img)
            if not res or res.boxes is None or res.txts is None:
                continue
            gray_full = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
            for bx, txt in zip(res.boxes, res.txts):
                n_txt = normalize_cjk(str(txt))
                if not n_txt or not re.search(r"[\u4e00-\u9fff]", n_txt):
                    continue
                xs = [float(p[0]) for p in bx]
                ys = [float(p[1]) for p in bx]
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
    except Exception as e_probe:
        logger.warning(f"[VisualSubRefiner] Lỗi thăm dò ROI, dùng vùng fallback: {e_probe}")
        return {"has_hardsub": True, "mode": "probe_err_fallback"}
    finally:
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


def _detect_hwaccel_decode(video_path: str) -> Tuple[str, List[str]]:
    """Tự động phát hiện bộ giải mã phần cứng nhanh nhất hỗ trợ file đầu vào."""
    ff_cmd = get_ffmpeg_cmd()
    for name, extra in (("qsv", ["-hwaccel", "qsv"]), ("cuda", ["-hwaccel", "cuda"])):
        try:
            proc = subprocess.run(
                [
                    *ff_cmd, "-v", "error", *extra,
                    "-t", "0.4", "-i", video_path,
                    "-vf", "fps=2,hwdownload,format=nv12,crop=16:16:0:0",
                    "-f", "null", "-",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=8,
            )
            if proc.returncode == 0:
                return name, extra
        except Exception:
            continue
    return "cpu", []


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


class VisualSubRefiner:
    """
    Bộ gộp câu bị cắt vụn và sửa lỗi đồng âm CapCut ASR dựa trên phụ đề cứng trên video.
    An toàn luồng, tự hủy tài nguyên FFmpeg & ONNX sau khi chạy xong.
    """

    @staticmethod
    def extract_visual_blocks(
        video_path: str,
        fps: float = 2.0,
        max_duration_sec: Optional[float] = None,
        progress_cb: Optional[Callable[[str], None]] = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Quét video song song GPU (decode) + CPU (`PP-OCRv4 rec-only`) để thu thập danh sách
        block phụ đề cứng + crop ảnh dự phòng phục vụ Multi-Witness.
        """
        if not video_path or not os.path.exists(video_path):
            return [], {"skipped": "missing_video"}

        t_all = time.time()
        src_w, src_h, vid_dur = _probe_video_geometry(video_path)
        if progress_cb:
            progress_cb("Đang thăm dò bố cục & kích thước phụ đề riêng của phim...")
        roi = _probe_subtitle_roi(
            video_path=video_path,
            src_w=src_w,
            src_h=src_h,
            vid_dur=vid_dur,
            max_duration_sec=max_duration_sec,
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

        crop_filter, band_w, band_h = _compute_subtitle_band(src_w, src_h, roi=roi)
        hw_name, hw_args = _detect_hwaccel_decode(video_path)
        logger.info(
            f"[VisualSubRefiner] ROI ({roi.get('mode')}): {src_w}x{src_h} -> {crop_filter} | HW={hw_name} | polarity={roi.get('polarity', 'light')}"
        )

        from rapidocr import ModelType, OCRVersion, RapidOCR

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
            vf = (
                f"fps={fps},hwdownload,format=nv12,{crop_filter}"
                if hw_name != "cpu"
                else f"fps={fps},{crop_filter}"
            )
            cmd = [*ff_cmd, "-v", "error", *hw_args]
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
            eng_v4 = RapidOCR(params={
                "Global.use_det": False,
                "Global.use_cls": False,
                "Rec.ocr_version": OCRVersion.PPOCRV4,
                "Rec.model_type": ModelType.MOBILE,
            })
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
                res = eng_v4(crop)
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
        ocr_unanimous = len(wits_flat) >= 2 and len({w for w in wits_flat if w}) == 1
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
            if len(ctx_a) >= 2 and any(ctx_a in w for w in wits_flat):
                out.append(a_seg)
                continue

            psim = pinyin_similarity(a_seg, o_seg) if (a_seg and o_seg) else 0.0
            vsim = shape_cmp.span_similarity(a_seg, o_seg) if (a_seg and o_seg) else 0.0
            wA, sA, a_tks = cls._span_word_strength(asr_s, i1, i2) if a_seg else (0.0, 0.0, [])
            wO, sO, o_tks = cls._span_word_strength(ocr_s, j1, j2) if o_seg else (0.0, 0.0, [])
            a_4char = any(len(tk) >= 4 for tk, a, b in a_tks if b > i1 and a < i2)
            o_4char = any(len(tk) >= 4 for tk, a, b in o_tks if b > j1 and a < j2)
            o_ent = (
                ngram_counts.get(o_seg, 0)
                if len(o_seg) >= 2
                else max(
                    [ngram_counts.get(bg, 0) for bg in (ocr_s[max(0, j1 - 1):j1 + 1], ocr_s[j1:j1 + 2]) if len(bg) == 2],
                    default=0,
                )
            )

            if tag == "replace" and o_4char and not a_4char and vsim < 0.65:
                out.append(o_seg)
            elif o_ent >= 2 and psim >= 0.45 and vsim < 0.72 and not (wA >= 3.5 and wO == 0):
                out.append(o_seg)
            elif psim >= 0.70 and vsim < 0.75:
                if wA >= 3.2 and wO == 0 and o_ent == 0 and vsim >= 0.48:
                    out.append(a_seg)
                elif wA > 2.8 and wO == 0 and len(a_seg) > len(o_seg) and o_ent == 0:
                    out.append(a_seg)
                else:
                    out.append(o_seg)
            elif vsim >= 0.49 and psim < 0.65:
                if (wA >= 2.5 and wO < wA - 0.3) or (sA >= 4.2 and sO < sA - 0.8) or (wO == 0 and wA > 0):
                    out.append(a_seg)
                elif wO >= 3.2 and wA == 0:
                    out.append(o_seg)
                else:
                    out.append(a_seg if (wA + 0.5 * sA) >= (wO + 0.5 * sO) else o_seg)
            elif wO >= wA + 0.6 and tag == "replace" and wO >= 2.4:
                out.append(o_seg)
            elif wA >= 3.0 and wO == 0 and o_ent == 0 and tag == "replace":
                out.append(a_seg)
            elif tag == "replace" and psim < 0.50 and vsim < 0.49 and (wO > 0 or len(o_seg) >= 2):
                out.append(o_seg)
            elif tag == "insert":
                if o_seg == "一" and j1 == 0 and o_tks and o_tks[0][0] == "一":
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
                elif ocr_unanimous and len(a_seg) == 1:
                    continue
                elif len(a_seg) == 1 and wA < 2.8:
                    continue
                elif len(a_seg) >= 1 and wA >= 2.8:
                    out.append(a_seg)
                else:
                    out.append(o_seg)
            else:
                out.append(o_seg if (wO + 0.3 * len(o_seg)) >= (wA + 0.3 * len(a_seg)) else a_seg)
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

        if not subs:
            return segments, {"total_in": len(segments), "total_out": len(segments), "visual_subs": 0}

        t0 = time.time()
        PAD = 0.04
        asr_subs: List[List[int]] = []
        for seg in segments:
            an = normalize_cjk(seg.clean_text or seg.text)
            dur_a = max(0.1, float(seg.end) - float(seg.start))
            cands = []
            for s in subs:
                ov = min(float(seg.end), s["end"] + PAD) - max(float(seg.start), s["start"] - PAD)
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

        # Bước Multi-Witness (chỉ đọc bổ sung PP-OCRv5 & frame phụ trên câu bất đồng với ASR)
        dispute_cache: Dict[int, List[str]] = dict(cached_wits)
        disputed_map: Dict[int, set] = {}
        for u in units:
            if u["sub_txt"] and u["asr_txt"] != u["sub_txt"]:
                for sid in u["sub_ids"]:
                    if sid not in dispute_cache and sub_by_id[sid].get("crop") is not None:
                        disputed_map.setdefault(sid, set()).add(u["asr_txt"])

        t_mw = 0.0
        if disputed_map:
            t_mw0 = time.time()
            from rapidocr import ModelType, OCRVersion, RapidOCR

            eng_v4 = None
            eng_v5 = None
            try:
                eng_v4 = RapidOCR(params={
                    "Global.use_det": False, "Global.use_cls": False,
                    "Rec.ocr_version": OCRVersion.PPOCRV4, "Rec.model_type": ModelType.MOBILE,
                })
                eng_v5 = RapidOCR(params={
                    "Global.use_det": False, "Global.use_cls": False,
                    "Rec.ocr_version": OCRVersion.PPOCRV5, "Rec.model_type": ModelType.MOBILE,
                })

                def _matched_asr(txt: str, cands: set) -> bool:
                    return any(txt == ac or (len(txt) >= 2 and txt in ac) for ac in cands)

                for sid, asr_cands in sorted(disputed_map.items()):
                    s = sub_by_id[sid]
                    readings = [s["n"]]
                    r5 = eng_v5(s["crop"])
                    t5 = normalize_cjk("".join(r5.txts)) if r5 and r5.txts else ""
                    if t5:
                        readings.append(t5)
                    if t5 and _matched_asr(t5, asr_cands):
                        dispute_cache[sid] = readings
                        continue
                    alt_crop = s.get("alt_crop")
                    if alt_crop is not None:
                        r4_2 = eng_v4(alt_crop)
                        t4_2 = normalize_cjk("".join(r4_2.txts)) if r4_2 and r4_2.txts else ""
                        if t4_2:
                            readings.append(t4_2)
                        if t4_2 and _matched_asr(t4_2, asr_cands):
                            dispute_cache[sid] = readings
                            continue
                        r5_2 = eng_v5(alt_crop)
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

                # Guard 1: Chặn bleed sang câu thoại khác (Pinyin gốc chưa gộp n/l & 0 ký tự chung)
                if len(set(a_s) & set(o_s)) == 0 and len(set(to_pinyin_list(a_s)) & set(to_pinyin_list(o_s))) == 0:
                    u["final"] = a_s
                    u["status"] = "TURN_BLEED_KEEP_ASR"
                    stats["bleed_guarded"] += 1
                    continue

                # Guard 2: Superset không lặp đuôi qua biên câu kề
                if a_s in o_s or o_s in a_s:
                    if len(o_s) > len(a_s) and a_s in o_s:
                        pos = o_s.find(a_s)
                        pref, suff = o_s[:pos], o_s[pos + len(a_s):]
                        if pref.startswith("一") and len(pref) > 1 and not any(t[0].startswith(pref) for t in jieba.tokenize(o_s)):
                            pref = pref[1:]
                        if pref == "一" and a_s:
                            pref = ""
                        elif pref and prev_a and (pref in prev_a[-len(pref) - 3:] or prev_a[-1:] in pref):
                            if not (len(pref) == 1 and prev_o and prev_a.endswith(pref) and not prev_o.endswith(pref)):
                                pref = ""
                        if suff and next_a and (suff in next_a[:len(suff) + 3] or next_a[:2] in suff or suff[:2] in next_a):
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

                wits_flat = [w for wl in u["wits"] for w in wl]
                final_s, _ = cls._choose_span(a_s, o_s, wits_flat, ngram_counts, shape_cmp)
                final_s = cls._trim_adjacent_bleed(final_s, a_s, prev_a, next_a)
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
                    for k in range(len(f) - 1):
                        bg = f[k:k + 2]
                        if ngram_counts.get(bg, 0) == 0:
                            for ent, cnt in canon_ents.most_common():
                                if cnt >= 3:
                                    diffs = [(c1, c2) for c1, c2 in zip(bg, ent) if c1 != c2]
                                    if len(diffs) == 1:
                                        dc1, dc2 = diffs[0]
                                        if pinyin_similarity(bg, ent) >= 0.72 and shape_cmp.span_similarity(dc1, dc2) < 0.70:
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
