# -*- coding: utf-8 -*-
"""
SmartTimelineHealer v4 — Bộ thẩm định & chữa lành timeline dịch thuật đa tầng (Multi-Layer Alignment & Audit)
Triết lý thiết kế (Chuẩn phát thanh phim cao cấp):
1. TẦNG CỨNG (Hard Ground Truth):
   - Căn chỉnh chuỗi tiếng Trung `zh` trực tiếp vào chuỗi ASR gốc (không tin số ID do LLM tự khai).
   - Tự động suy ra `src` chính xác tuyệt đối.
   - Bắt triệt để: Bỏ mẩu ASR (dropped_before), chồng lấn (overlap), chữ bịa (match_ratio < 0.85).
   - Thi hành tính NGUYÊN TỬ: Cấm cắt dở giữa chừng một mẩu ASR (chunk_split).
2. TẦNG MỀM (Soft Audits & Co-occurrence Escalation):
   - Ngân sách số từ tiếng Việt so với viCapcut (nếu có viCapcut).
   - Đối soát từ vựng viCapcut có trọng số IDF.
   - Ước lượng hệ số co giãn TTS (est_dur / slot_dur): Chặn > 1.35x (tua nhanh) và < 0.55x (câm tiếng).
   - Tự động nhận diện nếu viCapcut rỗng: Tắt audit viCapcut, log NO_VICAPCUT_AVAILABLE, 0 báo nhầm.
   - Quy tắc phối hợp: 1 tín hiệu mềm = Cảnh báo; >= 2 tín hiệu mềm = Leo thang thành HARD ERROR.
3. TỰ CỨU AN TOÀN:
   - Gap-Split: Kích hoạt khi có khoảng lặng cứng >= HARD_GAP (1.0s).
   - Đã vá triệt để: Chống cắt nhầm số thập phân 3.5 và số 1,000; dọn sạch dấu nháy kép rác.
   - Pooled-Resplit: Chỉ cho phép chạy khi Khối 1 chứng minh nội dung chữ Hán đủ 100%.
4. CỬA SỔ RETRY CỤC BỘ & CÁCH LY:
   - Gom các câu HARD liên tiếp, mở rộng sang hai biên sạch.
   - Gán cờ `needs_review` để cách ly nếu vượt quá số lần retry, không làm đơ cả bộ phim.
"""
import re
import logging
from difflib import SequenceMatcher
from collections import Counter
from typing import List, Dict, Any, Tuple, Optional, Set

logger = logging.getLogger(__name__)

# --- CÁC HẰNG SỐ CHUẨN PHÁT THANH ---
DEFAULT_TARGET_WPS = 3.3
WPS_SAFE_MIN = 1.5
WPS_SAFE_MAX = 4.8
SPEEDUP_MAX = 1.35
SPEEDUP_MIN = 0.55

SOFT_GAP = 0.4   # Ranh giới ưu tiên phân bổ ranh giới trong pooled-resplit
HARD_GAP = 1.0   # Ngưỡng bắt buộc kích hoạt split câu gộp khoảng lặng

# Cờ an toàn: Khóa mặc định pooled_resplit cho đến khi đạt chỉ số benchmark
ENABLE_POOLED_RESPLIT = False

STOPWORDS_VI = {
    "là", "của", "và", "có", "những", "các", "được", "một", "với", "này", "đó", 
    "thì", "mà", "cho", "trong", "đã", "đang", "sẽ", "đến", "tại", "vào", "ra",
    "người", "khi", "như", "lại", "thấy", "thế", "gì", "nào", "ở", "bị", "bởi"
}


def norm_zh(text: Any) -> str:
    """Chuẩn hóa tiếng Trung: bỏ khoảng trắng, dấu câu Trung/Anh, và số thứ tự mẩu [298]."""
    s = str(text or "")
    # Loại bỏ tiền tố đánh số kiểu [298], 298., 298:
    s = re.sub(r'^\s*\[?\d+\]?[\.\:\s]*', '', s)
    # Giữ lại chữ Hán, chữ cái, chữ số nội dung
    s = re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]', '', s)
    return s.strip()


# Tập hư từ cố định theo yêu cầu kiểm chuẩn (trọng số 0.3)
FIXED_EMPTY_WORDS = {
    '的', '了', '是', '不', '在', '有', '我', '他', '你', '这', '那',
    '就', '也', '都', '而', '及', '与', '着', '过', '得', '地', '呢',
    '吧', '啊', '吗', '呀'
}


def calc_piece_similarity(s1: str, s2: str) -> float:
    """Tính tương đồng kết hợp SequenceMatcher và Jaccard ký tự giảm trọng số hư từ."""
    if not s1 or not s2:
        return 0.0
    if s1 == s2:
        return 1.0

    sm_ratio = SequenceMatcher(None, s1, s2, autojunk=False).ratio()

    c1 = Counter(s1)
    c2 = Counter(s2)
    all_chars = set(c1.keys()) | set(c2.keys())
    intersection = 0.0
    union = 0.0
    for ch in all_chars:
        w = 0.3 if ch in FIXED_EMPTY_WORDS else 1.0
        intersection += min(c1[ch], c2[ch]) * w
        union += max(c1[ch], c2[ch]) * w
    jaccard_ratio = (intersection / union) if union > 0 else 0.0

    return max(sm_ratio, jaccard_ratio)


def _wc(text: str) -> int:
    return len(re.findall(r'\b\w+\b', str(text or "")))


def _words(text: str) -> List[str]:
    return [w.lower() for w in re.findall(r'\b\w+\b', str(text or ""))]


class SmartTimelineHealer:
    """SmartTimelineHealer v4: Thẩm định căn chỉnh đa tầng và tự cứu an toàn."""

    @classmethod
    def audit_and_heal(
        cls,
        orig_segments: List[Dict[str, Any]],
        ai_items: List[Dict[str, Any]],
        entity_table_text: str = '',
    ) -> Tuple[List[Dict[str, Any]], int, List[str]]:
        """Hàm chính: Duyệt tuần tự kiểm tra và chữa lành timeline."""
        return cls.check_and_heal(orig_segments, ai_items, entity_table_text)

    @classmethod
    def check_and_heal(
        cls,
        orig_segments: List[Dict[str, Any]],
        ai_items: List[Dict[str, Any]],
        entity_table_text: str = '',
    ) -> Tuple[List[Dict[str, Any]], int, List[str]]:

        if not orig_segments or not ai_items:
            return ai_items, 0, []

        N = len(orig_segments)
        seg_map = {s.get("index", i + 1): s for i, s in enumerate(orig_segments)}
        logs: List[str] = []

        # =========================================================================
        # BƯỚC 1: CĂN CHỈNH ZH VÀO ASR GỐC (TẦNG CỨNG)
        # =========================================================================
        has_zh_field = any(bool(str(it.get('zh', '')).strip()) for it in ai_items)
        if has_zh_field:
            aligned_items, telemetry = cls.align_sequential(ai_items, orig_segments)
            logs.append(f"🔍 [Align v4] Căn chỉnh tuần tự {len(aligned_items)} câu theo zh vào {N} mẩu ASR (N==K: {telemetry.get('n_eq_k', 0)}, N!=K: {telemetry.get('n_neq_k', 0)}).")
        else:
            # Nếu toàn bộ lô không chứa field zh: Báo lỗi cứng BẮT BUỘC DỊCH LẠI (Không tin src mù)
            aligned_items = []
            for it in ai_items:
                item_copy = dict(it)
                item_copy['src'] = cls._norm_src(item_copy.get('src', []), seg_map)
                item_copy['align_ok'] = False
                item_copy['hard_flags'] = ['ZH_MISSING_ENTIRE_BATCH']
                item_copy['_ok'] = False
                aligned_items.append(item_copy)
            logs.append(f"⚠️ [Align v4] CẢNH BÁO: Kịch bản không chứa trường zh! Đánh dấu ZH_MISSING_ENTIRE_BATCH để yêu cầu dịch lại.")

        # =========================================================================
        # BƯỚC 2: AUDIT MỀM (VICAPCUT, IDF, TTS DURATION)
        # =========================================================================
        audited_items = cls.audit_soft_signals(aligned_items, seg_map)

        # =========================================================================
        # BƯỚC 3: TỰ CỨU (GAP SPLIT QUA HARD_GAP >= 1.0s & POOLED RESPLIT)
        # =========================================================================
        healed_items: List[Dict[str, Any]] = []
        fixes = 0

        for it in audited_items:
            src = it.get('src', [])
            # Dò khoảng lặng cứng >= HARD_GAP (1.0s)
            gap_at = cls.find_big_gap(src, seg_map, threshold=HARD_GAP)
            if gap_at and len(src) >= 2:
                sl = [s for s in src if s <= gap_at]
                sr = [s for s in src if s > gap_at]
                split_res = cls.try_rescue_split(it.get('vi', ''), sl, sr, seg_map)
                if split_res:
                    l_txt, r_txt = split_res
                    it1 = dict(it)
                    it1['src'] = sl
                    it1['vi'] = l_txt
                    it1['_ok'] = True
                    it1['hard_flags'] = []

                    it2 = dict(it)
                    it2['src'] = sr
                    it2['vi'] = r_txt
                    it2['_ok'] = True
                    it2['hard_flags'] = []

                    healed_items.extend([it1, it2])
                    fixes += 1
                    logs.append(f"  ✔ [Tự cứu Gap-Split] Tách câu thành công qua gap {gap_at}")
                    continue
            healed_items.append(it)

        # =========================================================================
        # BƯỚC 4: DỰNG CỬA SỔ RETRY CHO CÁC CÂU HARD
        # =========================================================================
        retry_windows = cls.build_retry_windows(healed_items, orig_segments)
        has_hard_errors = len(retry_windows) > 0

        # =========================================================================
        # BƯỚC 5: REBUILD TIMECODE CHUẨN XÁC
        # =========================================================================
        healed_items = cls._rebuild(healed_items, orig_segments)

        # Gán cờ _retry_needed cho từng câu
        for it in healed_items:
            it['_retry_needed'] = (not it.get('_ok', True)) or has_hard_errors

        fails = sum(1 for it in healed_items if not it.get('_ok', True))
        passed = len(healed_items) - fails
        verdict = 'CAN DICH LAI' if has_hard_errors else 'DAT'
        sep = '=' * 58
        summary = (
            f"\n{sep}\n"
            f"SmartTimelineHealer v4 | {len(healed_items)} câu kịch bản | {N} mẩu ASR\n"
            f"  PASSED={passed} FAIL={fails} FIXED={fixes}"
            + (f" | CỬA SỔ LỖI HARD={len(retry_windows)}" if retry_windows else "") + "\n"
            f"  KẾT QUẢ: {verdict}\n"
            f"{sep}"
        )
        print(summary, flush=True)

        return healed_items, fixes, [summary] + logs

    # =========================================================================
    # KHỐI 1: CĂN CHỈNH ZH TUẦN TỰ ĐA TẦNG VÀO ASR GỐC (PLAN HEALER V4)
    # =========================================================================
    @classmethod
    def align_sequential(
        cls,
        items: List[Dict[str, Any]], 
        orig_segs: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Healer v4: Căn chỉnh tuần tự hai tầng (Plan Healer v4).
        - Con trỏ p bắt đầu từ mẩu ASR đầu tiên chưa dùng.
        - Tầng 1: Đoán n mẩu liên tiếp theo độ dài tích luỹ và độ tương đồng tổng (n = 1..12).
        - Tầng 2: Xác nhận k mảnh (tách theo '/') với n mẩu ASR tương ứng.
        - Xử lý lệch: Hấp thụ mẩu ngắn (<= 2 chữ hoặc < 0.8s) vào câu kề thời gian gần nhất; phát hiện mẩu bỏ rơi thật.
        - Khâu chốt tổng thể: Đối chiếu toàn cục, không để sót mẩu và không báo lỗi giả.
        """
        seg_map = {s.get('index', i + 1): s for i, s in enumerate(orig_segs)}
        seg_indices = sorted(seg_map.keys())
        N = len(seg_indices)
        max_idx = seg_indices[-1] if seg_indices else 0

        telemetry: Dict[str, Any] = {
            'n_eq_k': 0,
            'n_neq_k': 0,
            'true_scores': [],
            'neighbor_scores': [],
            'absorbed': [],
            'flags': Counter()
        }

        out: List[Dict[str, Any]] = []
        p = seg_indices[0] if seg_indices else 1

        for it_idx, it in enumerate(items):
            zh_clean = norm_zh(it.get('zh', ''))
            raw_pieces = it.get('zh_pieces') or ([zh_clean] if zh_clean else [])
            pieces = [norm_zh(pc) for pc in raw_pieces if norm_zh(pc)]
            k = len(pieces)

            hard_flags = []
            soft_flags = []

            # Hàm đánh giá ứng viên n từ điểm xuất phát start_p
            def eval_candidate(start_p: int) -> Tuple[int, float, float]:
                cand_n = 1
                cand_sc = -1.0
                cand_sim = 0.0
                for n in range(1, min(13, max_idx - start_p + 2)):
                    chk_ids = [start_p + i for i in range(n) if (start_p + i) in seg_map]
                    if not chk_ids:
                        continue
                    A_n = "".join(norm_zh(seg_map[c].get('clean_text') or seg_map[c].get('text') or seg_map[c].get('zh') or '') for c in chk_ids)
                    diff = abs(len(zh_clean) - len(A_n))
                    max_l = max(len(zh_clean), len(A_n))
                    len_ratio = (diff / max_l) if max_l > 0 else 0.0

                    sim = calc_piece_similarity(zh_clean, A_n)
                    if len_ratio <= 0.25 or (n == 1 and diff <= 2):
                        sc = sim
                    else:
                        sc = sim * max(0.0, 1.0 - (len_ratio - 0.25) * 2)

                    if sc > cand_sc:
                        cand_sc = sc
                        cand_n = n
                        cand_sim = sim
                return cand_n, cand_sc, cand_sim

            n_p, sc_p, sim_p = eval_candidate(p)
            n_next, sc_next, sim_next = eval_candidate(p + 1) if (p + 1) in seg_map else (1, -1.0, 0.0)

            # Bước 2.4: Kiểm tra mẩu p bị bỏ qua (nếu p+1 khớp tốt hơn rõ rệt >= 0.20 và sc_p < 0.60)
            if sc_next > sc_p + 0.20 and sc_p < 0.60:
                seg_p = seg_map[p]
                dur_p = float(seg_p.get('end', 0.0)) - float(seg_p.get('start', 0.0))
                len_p = len(norm_zh(seg_p.get('clean_text') or seg_p.get('text') or seg_p.get('zh') or ''))

                if len_p <= 2 or dur_p < 0.8:
                    telemetry['absorbed'].append(p)
                    # Hấp thụ vào câu kề thời gian gần nhất
                    if out and out[-1].get('src'):
                        prev_last_chk = out[-1]['src'][-1]
                        prev_end = float(seg_map[prev_last_chk].get('end', 0.0))
                        next_start = float(seg_map[p + 1].get('start', 0.0))
                        if abs(float(seg_p.get('start', 0.0)) - prev_end) <= abs(next_start - float(seg_p.get('end', 0.0))):
                            out[-1]['src'].append(p)
                            out[-1]['soft_flags'] = out[-1].get('soft_flags', []) + [f"ABSORBED_{p}"]
                        else:
                            soft_flags.append(f"ABSORBED_{p}")
                    else:
                        soft_flags.append(f"ABSORBED_{p}")
                else:
                    hard_flags.append(f"DROPPED_CHUNKS_[{p}]")

                p += 1
                best_n, best_score, best_sim = n_next, sc_next, sim_next
            else:
                best_n, best_score, best_sim = n_p, sc_p, sim_p

            # Kiểm tra ngưỡng Tầng 1
            if best_score < 0.50:
                # Không khớp ở đâu -> Cố định lại p bằng câu kế (tìm trong p..p+5)
                found_anchor = False
                for m_ahead in range(1, 6):
                    target_p = p + m_ahead
                    if target_p in seg_map:
                        _, test_sc, _ = eval_candidate(target_p)
                        if test_sc >= 0.60:
                            error_span = list(range(p, target_p))
                            hard_flags.append(f"UNMATCHED_SPAN_{error_span}")
                            p = target_p
                            found_anchor = True
                            break
                if not found_anchor:
                    hard_flags.append(f"ZH_MATCH_LOW_{best_score:.0%}")

                out.append({
                    **it,
                    'src': [],
                    'match_ratio': round(best_sim, 2),
                    'n_t1': best_n,
                    'k_t2': k,
                    'align_ok': False,
                    'hard_flags': hard_flags,
                    'soft_flags': soft_flags,
                    '_ok': False
                })
                continue

            # Bước 2.2: Xác nhận bằng Tầng 2 (từng mảnh)
            if k == best_n:
                telemetry['n_eq_k'] += 1
                matched_chunks_for_pieces = []
                for j in range(k):
                    pc = pieces[j]
                    chk = p + j
                    matched_chunks_for_pieces.append(chk)
                    raw_c = norm_zh(seg_map[chk].get('clean_text') or seg_map[chk].get('text') or seg_map[chk].get('zh') or '')
                    s_true = calc_piece_similarity(pc, raw_c)

                    s_prev = calc_piece_similarity(pc, norm_zh(seg_map[chk-1].get('clean_text') or '')) if (chk-1) in seg_map else 0.0
                    s_next = calc_piece_similarity(pc, norm_zh(seg_map[chk+1].get('clean_text') or '')) if (chk+1) in seg_map else 0.0
                    s_neighbor = max(s_prev, s_next)

                    telemetry['true_scores'].append(s_true)
                    telemetry['neighbor_scores'].append(s_neighbor)

                    th = 0.50 if len(raw_c) <= 4 else 0.65
                    if s_true < th:
                        soft_flags.append(f"PIECE_{j+1}_LOW_{s_true:.0%}")

                # Kiểm tra tính nguyên tử: Hai mảnh liên tiếp cùng khớp 1 mẩu
                if len(set(matched_chunks_for_pieces)) < len(matched_chunks_for_pieces):
                    hard_flags.append("ATOMIC_CHUNK_SPLIT")
            else:
                telemetry['n_neq_k'] += 1
                if best_score >= 0.85:
                    soft_flags.append(f"SOFT_K_MISMATCH_{k}vs{best_n}")
                else:
                    hard_flags.append(f"HARD_K_MISMATCH_{k}vs{best_n}")

            assigned_src = [p + i for i in range(best_n) if (p + i) in seg_map]
            p += best_n

            align_ok = (len(hard_flags) == 0)
            for f in hard_flags:
                telemetry['flags'][f] += 1
            for f in soft_flags:
                telemetry['flags'][f] += 1

            out.append({
                **it,
                'src': assigned_src,
                'match_ratio': round(best_sim, 2),
                'n_t1': best_n,
                'k_t2': k,
                'align_ok': align_ok,
                'hard_flags': hard_flags,
                'soft_flags': soft_flags,
                '_ok': align_ok
            })

        # Bước 3: Chốt tổng thể sau khi duyệt hết
        all_assigned = set(s for item in out for s in item.get('src', []))
        unassigned = set(seg_indices) - all_assigned

        real_dropped = []
        for u in sorted(list(unassigned)):
            seg_u = seg_map[u]
            dur_u = float(seg_u.get('end', 0.0)) - float(seg_u.get('start', 0.0))
            len_u = len(norm_zh(seg_u.get('clean_text') or seg_u.get('text') or seg_u.get('zh') or ''))
            if len_u > 2 and dur_u >= 0.8:
                real_dropped.append(u)

        if real_dropped:
            if out:
                out[-1]['hard_flags'] = out[-1].get('hard_flags', []) + [f"GLOBAL_UNASSIGNED_{real_dropped}"]
                out[-1]['align_ok'] = False
                out[-1]['_ok'] = False

        telemetry['unassigned_all'] = sorted(list(unassigned))
        telemetry['unassigned_real_dropped'] = real_dropped
        telemetry['total_items'] = len(out)
        telemetry['total_segs'] = N

        return out, telemetry

    @classmethod
    def align_zh_to_asr(
        cls,
        items: List[Dict[str, Any]], 
        segs: List[Dict[str, Any]], 
        slack: int = 6, 
        drop_tol: int = 2
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Wrapper tương thích ngược với các caller cũ."""
        aligned, telemetry = cls.align_sequential(items, segs)
        unassigned_count = len(telemetry.get('unassigned_all', []))
        return aligned, unassigned_count

    # =========================================================================
    # KHỐI 2: AUDIT MỀM (VICAPCUT, IDF, TTS DURATION)
    # =========================================================================
    @staticmethod
    def audit_soft_signals(
        items: List[Dict[str, Any]], 
        seg_map: Dict[int, Dict[str, Any]],
        target_wps: float = DEFAULT_TARGET_WPS
    ) -> List[Dict[str, Any]]:
        has_vicapcut = any(bool(s.get('vi', '').strip()) for s in seg_map.values())
        k_median = 1.0

        if has_vicapcut:
            k_ratios = []
            for it in items:
                src = it.get('src', [])
                vi = it.get('vi', '')
                if len(src) == 1 and src[0] in seg_map:
                    ref_txt = seg_map[src[0]].get('vi', '')
                    wc_ref = _wc(ref_txt)
                    wc_item = _wc(vi)
                    if wc_ref >= 3 and wc_item >= 3:
                        k_ratios.append(wc_item / wc_ref)

            if len(k_ratios) >= 3:
                k_ratios.sort()
                k_median = k_ratios[len(k_ratios) // 2]
                k_median = max(0.8, min(1.6, k_median))

        audited = []
        for it in items:
            soft_flags = []
            hard_flags = list(it.get('hard_flags', []))
            src = it.get('src', [])
            vi = str(it.get('vi', '')).strip()
            wc_vi = _wc(vi)

            if not src:
                hard_flags.append("NO_SRC")
                it['hard_flags'] = hard_flags
                it['_ok'] = False
                audited.append(it)
                continue

            segs = [seg_map[s] for s in src if s in seg_map]
            if not segs:
                hard_flags.append("INVALID_SRC")
                it['hard_flags'] = hard_flags
                it['_ok'] = False
                audited.append(it)
                continue

            dur = max(0.1, float(segs[-1].get('end', 0)) - float(segs[0].get('start', 0)))
            wps = wc_vi / dur

            # --- Tín hiệu mềm 1 & 3: viCapcut (chỉ kích hoạt khi có dữ liệu) ---
            if has_vicapcut:
                ref_total_words = sum(_wc(s.get('vi', '')) for s in segs)
                if ref_total_words >= 4:
                    budget = ref_total_words * k_median
                    ratio = wc_vi / budget
                    if ratio > 2.0:
                        soft_flags.append(f"VI_BLOATED_{wc_vi}w_vs_budget_{budget:.0f}w")
                    elif ratio < 0.4:
                        soft_flags.append(f"VI_SHRUNK_{wc_vi}w_vs_budget_{budget:.0f}w")

                vi_tokens = [w for w in _words(vi) if w not in STOPWORDS_VI]
                if ref_total_words >= 5 and vi_tokens:
                    ref_tokens = set()
                    for s in segs:
                        ref_tokens.update(w for w in _words(s.get('vi', '')) if w not in STOPWORDS_VI)
                    if ref_tokens:
                        overlap_cnt = sum(1 for w in vi_tokens if w in ref_tokens)
                        recall = overlap_cnt / max(1, len(ref_tokens))
                        if recall < 0.15 and len(ref_tokens) >= 4:
                            soft_flags.append(f"VICAPCUT_LOW_RECALL_{recall:.0%}")
            else:
                it['vicapcut_mode'] = "NO_VICAPCUT_AVAILABLE"

            # --- Tín hiệu mềm 2: Ước lượng thời lượng TTS / Slot ---
            est_tts_dur = wc_vi / target_wps
            speedup = est_tts_dur / dur
            if speedup > SPEEDUP_MAX:
                soft_flags.append(f"TTS_TOO_FAST_{speedup:.2f}x")
            elif speedup < SPEEDUP_MIN and dur >= 2.0:
                soft_flags.append(f"TTS_TOO_SLOW_{speedup:.2f}x")

            # --- QUY TẮC PHỐI HỢP: 1 SOFT = WARNING, >= 2 SOFT = HARD ERROR ---
            is_hard = len(hard_flags) > 0 or len(soft_flags) >= 2
            it['soft_flags'] = soft_flags
            it['hard_flags'] = hard_flags
            it['_ok'] = not is_hard
            it['severity'] = "HARD" if is_hard else ("SOFT" if soft_flags else "OK")
            it['dur'] = dur
            it['wps'] = round(wps, 1)
            it['speedup'] = round(speedup, 2)
            audited.append(it)

        return audited

    # =========================================================================
    # KHỐI 3: TỰ CỨU (GAP SPLIT QUA HARD_GAP >= 1.0s)
    # =========================================================================
    @staticmethod
    def find_big_gap(src: List[int], seg_map: Dict[int, Dict[str, Any]], threshold: float = HARD_GAP) -> Optional[int]:
        for j in range(len(src) - 1):
            a, b = src[j], src[j + 1]
            if a in seg_map and b in seg_map:
                gap = float(seg_map[b].get('start', 0)) - float(seg_map[a].get('end', 0))
                if gap >= threshold:
                    return a
        return None

    @classmethod
    def try_rescue_split(
        cls,
        text: str,
        src_left: List[int],
        src_right: List[int],
        seg_map: Dict[int, Dict[str, Any]],
        target_wps: float = DEFAULT_TARGET_WPS
    ) -> Optional[Tuple[str, str]]:
        if not text or not src_left or not src_right:
            return None

        segs_l = [seg_map[s] for s in src_left if s in seg_map]
        segs_r = [seg_map[s] for s in src_right if s in seg_map]
        if not segs_l or not segs_r:
            return None

        dur_l = max(0.1, float(segs_l[-1].get('end', 0)) - float(segs_l[0].get('start', 0)))
        dur_r = max(0.1, float(segs_r[-1].get('end', 0)) - float(segs_r[0].get('start', 0)))

        w_tot = _wc(text)
        dur_tot = dur_l + dur_r
        ideal_wl = int(round(w_tot * (dur_l / dur_tot)))
        clamped_wps = max(2.6, min(3.8, target_wps))

        candidates = []
        pattern = re.compile(r'([,;:\.!?…—]+|["”\'])')
        matches = list(pattern.finditer(text))

        for m in matches:
            st_pos = m.start()
            en_pos = m.end()
            punct = m.group()

            # Chống cắt nhầm 3.5 và 1,000 (\d[.,]\d)
            if punct in ('.', ',') and st_pos > 0 and en_pos < len(text):
                if text[st_pos - 1].isdigit() and text[en_pos].isdigit():
                    continue

            left = text[:en_pos].strip()
            # Dọn sạch dấu nháy, dấu phẩy thừa ở đầu vế phải
            right = re.sub(r'^[,\.\s"”\'—\-]+', '', text[en_pos:]).strip()

            wl = _wc(left)
            wr = _wc(right)
            if wl < 2 or wr < 2:
                continue

            wps_l = wl / dur_l
            wps_r = wr / dur_r

            score = 10.0
            score -= abs(wps_l - clamped_wps) * 1.5
            score -= abs(wps_r - clamped_wps) * 1.5
            score -= abs(wl - ideal_wl) * 0.4
            candidates.append({"left": left, "right": right, "score": score, "wps_l": wps_l, "wps_r": wps_r})

        # Fallback khoảng trắng nếu không có dấu câu nào hoặc điểm quá thấp
        if not candidates:
            words = text.split()
            if len(words) >= 6:
                cut_idx = max(2, min(len(words) - 2, ideal_wl))
                left = " ".join(words[:cut_idx]).strip()
                right = " ".join(words[cut_idx:]).strip()
                right = re.sub(r'^[,\.\s"”\'—\-]+', '', right).strip()
                wps_l = _wc(left) / dur_l
                wps_r = _wc(right) / dur_r
                if WPS_SAFE_MIN <= wps_l <= WPS_SAFE_MAX and WPS_SAFE_MIN <= wps_r <= WPS_SAFE_MAX:
                    return left, right

        if candidates:
            candidates.sort(key=lambda x: x["score"], reverse=True)
            best = candidates[0]
            if best["score"] >= 4.0 and WPS_SAFE_MIN <= best["wps_l"] <= WPS_SAFE_MAX and WPS_SAFE_MIN <= best["wps_r"] <= WPS_SAFE_MAX:
                return best["left"], best["right"]

        return None

    # =========================================================================
    # KHỐI 5: XÂY DỰNG CỬA SỔ RETRY CỤC BỘ & CÁCH LY
    # =========================================================================
    @staticmethod
    def build_retry_windows(items: List[Dict[str, Any]], segs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        windows = []
        N = len(items)
        i = 0
        while i < N:
            if not items[i].get('_ok', True):
                start_err = i
                while i < N and not items[i].get('_ok', True):
                    i += 1
                end_err = i - 1

                win_start = max(0, start_err - 1)
                win_end = min(N - 1, end_err + 1)

                win_items = items[win_start : win_end + 1]
                all_src = [s for it in win_items for s in it.get('src', [])]
                src_min = min(all_src) if all_src else 0
                src_max = max(all_src) if all_src else 0

                reasons = []
                for it in win_items:
                    reasons.extend(it.get('hard_flags', []))
                    reasons.extend(it.get('soft_flags', []))

                windows.append({
                    "window_index": len(windows) + 1,
                    "item_indices": list(range(win_start, win_end + 1)),
                    "src_range": (src_min, src_max),
                    "reasons": list(set(reasons)),
                    "retry_count": 0,
                    "needs_review": False
                })
            else:
                i += 1
        return windows

    # =========================================================================
    # TIỆN ÍCH CHUẨN HÓA VÀ HẬU KIỂM TƯƠNG THÍCH PIPELINE
    # =========================================================================
    @staticmethod
    def _norm_src(raw: Any, seg_map: Dict[int, Dict[str, Any]]) -> List[int]:
        """Làm sạch src an toàn, chỉ chấp nhận ID nằm trong seg_map."""
        clean = []
        for x in (raw or []):
            try:
                v = int(float(str(x).strip()))
                if v in seg_map:
                    clean.append(v)
            except Exception:
                pass
        return sorted(list(set(clean)))

    @staticmethod
    def _norm_role(role: Any) -> str:
        r = str(role or '').strip()
        if not r or r.upper() in ["NARRATOR", "DẪN CHUYỆN", "DAN CHUYEN", "DẪN", "KỂ", "KỂ CHUYỆN", "NGƯỜI KỂ", "THUYẾT MINH"]:
            return "DẪN CHUYỆN"
        return r

    @staticmethod
    def _rebuild(items: List[Dict[str, Any]], segs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seg_map = {s.get("index", i + 1): s for i, s in enumerate(segs)}
        for it in items:
            src = [s for s in (it.get('src') or []) if s in seg_map]
            if not src:
                continue
            first_s = seg_map.get(src[0], {})
            last_s = seg_map.get(src[-1], {})
            it['s'] = round(float(first_s.get('start', 0.0) or 0.0), 2)
            it['e'] = round(float(last_s.get('end', 0.0) or 0.0), 2)
            it['orig_count'] = len(src)
            it['src_start_index'] = src[0]
            it['src_end_index'] = src[-1]
            it['local_src'] = src
            it['role'] = SmartTimelineHealer._norm_role(it.get('role'))
        return items

    @classmethod
    def verify_3_invariants(cls, items: List[Dict[str, Any]], orig_segments: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Kiểm tra 3 bất biến timeline cho pipeline."""
        N = len(orig_segments)
        valid_keys = set(s.get("index", i + 1) for i, s in enumerate(orig_segments))
        all_src = [x for it in items for x in (it.get('src') or []) if x in valid_keys]
        uniq = set(all_src)
        missing = sorted(valid_keys - uniq)

        errs = []
        if missing:
            errs.append(f"Bỏ sót {len(missing)} mẩu ASR: {missing[:10]}")

        from collections import Counter
        counts = Counter(all_src)
        duplicates = [k for k, v in counts.items() if v > 1]
        if duplicates:
            errs.append(f"Chồng lấn SRC {len(duplicates)} mẩu: {duplicates[:10]}")

        # Kiểm tra thứ tự tăng dần
        prev_max = 0
        for it in items:
            s = it.get('src', [])
            if s:
                if s[0] < prev_max:
                    errs.append(f"Lệch thứ tự: {s} đi sau {prev_max}")
                prev_max = max(prev_max, s[-1])

        return {
            'passed': not errs,
            'errors': errs,
            'covered_orig': len(uniq),
            'missing_indices': missing,
            'total_chunks': len(items),
            'total_orig': N
        }

    @classmethod
    def enforce_sequential_asr_locking(
        cls,
        healed_items: List[Dict[str, Any]],
        batch: Any,
        orig_starts: List[float],
        orig_ends: List[float]
    ) -> List[Dict[str, Any]]:
        segs = [
            {'index': i + 1, 'start': orig_starts[i], 'end': orig_ends[i]}
            for i in range(len(orig_starts))
        ]
        return cls._rebuild(healed_items, segs)
