# -*- coding: utf-8 -*-
"""
SmartTimelineHealer v5.3 — Căn chỉnh zh -> ASR theo TỪNG MẢNH & Kẹp Bánh Mì Khoanh Vùng (Sandwich Containment).

NÂNG CẤP CỐT LÕI (v5.3):
1. Gỡ bỏ hoàn toàn MERGED_CHUNKS_IN_PIECE, MERGE_ACROSS_GAP, GAP_COLLISION khỏi HARD_FLAG_PREFIXES.
   LLM gộp câu ASR bị cắt ngang (Ca E) là hành vi thông minh hợp lệ 100%, chỉ ghi nhận vào soft_flags.
2. Kẹp Bánh Mì Khoanh Vùng (Sandwich Containment) giữa 2 câu Neo chuẩn vị trí gốc:
   - Nếu 1-2 mẩu ASR ngắn/rác bị LLM bỏ qua nhưng nằm kẹp giữa 2 câu liền kề đã khóa chuẩn vị trí với ASR gốc
     -> Khoanh vùng thành SANDWICH_SKIPPED_CHUNK (soft_flags), không đánh DROPPED_CHUNK bắt dịch lại.
   - Nếu 1 câu ở giữa bị LLM viết lại chữ Hán làm mất khớp (NO_SRC / PIECE_UNMATCHED), nhưng câu ngay trước (L)
     và câu ngay sau (R) đều khớp chuẩn vị trí với ASR gốc, và khe (L..R) có sẵn 1-2 mẩu ASR vừa vặn
     -> Kẹp thẳng các mẩu lọt khe đó cho câu ở giữa, xóa lỗi cứng, giữ nguyên timeline mượt mà.
3. Chỉ bắt dịch lại (HARD FAIL) khi:
   - Mất đoạn nội dung dài (thủng lưới >= 3 mẩu ASR liên tiếp hoặc tổng chữ > 10 chữ / thời lượng > 3.5s).
   - Hiệu ứng cánh bướm (Butterfly Drift): Trượt neo liên hoàn (UNSYNC_SPAN / NEEDS_REVIEW_UNSYNC / OUT_OF_ORDER).
   - Lỗi ở biên đầu/cuối lô không có vỏ bánh mì kẹp và vượt ngưỡng an toàn.

API TƯƠNG THÍCH HOÀN TOÀN:
- audit_and_heal, check_and_heal, check_and_heal_ex, align_sequential, align_zh_to_asr,
  align_one_to_one, audit_soft_signals, find_big_gap, try_rescue_split, build_retry_windows,
  apply_quarantine, closed_gate, verify_3_invariants, enforce_sequential_asr_locking.
"""

import bisect
import logging
import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# --- HẰNG SỐ THỜI GIAN / TTS ---
DEFAULT_TARGET_WPS = 3.3
WPS_SAFE_MIN = 1.5
WPS_SAFE_MAX = 4.8
SPEEDUP_MAX = 1.60
SPEEDUP_MIN = 0.55

SOFT_GAP = 0.4
HARD_GAP = 0.8          # Ngưỡng [KHÓA] trong prompt / pipeline
GAP_SPLIT_TH = 0.85     # Ngưỡng bắt buộc cắt câu gộp băng qua khoảng lặng (đồng bộ với gap_threshold gộp audio Bước 4)

BUFFER = 0.08           # đệm trước đầu mẩu kế tiếp (đo thực tế)
TAIL_CAP = 0.40         # nới tối đa sau đuôi khi gặp khoảng lặng >= HARD_GAP
JUNK_EXTEND_CAP = 1.0   # nới tối đa qua mẩu rác thuần túy (e_max_ext)

# --- THAM SỐ CĂN CHỈNH ---
W_CONTENT = 8           # số mẩu có nội dung trong vùng tìm (mở rộng để chịu được LLM gộp/lược mẩu ngắn)
MAX_WINDOW = 16
TH_NORMAL = 0.58
TH_SHORT = 0.58         # Hạ bằng TH_NORMAL để không phạt oan câu ngắn
SHORT_LEN = 3
SHORT_SEQ_MAX_LEN = 5   # Ngưỡng độ dài coi là câu ngắn khi xét tuần tự i+1
MARGIN_MIN = 0.15
CONFIRM_W = 3
CONFIRM_LOOKAHEAD = 2
FAIL_STREAK_MAX = 3
ANCHOR_MIN_LEN = 5
ANCHOR_LOOKAHEAD = 8
ANCHOR_WIN_CONTENT = 20
ANCHOR_WIN_TOTAL = 40
ANCHOR_TH = 0.70
ANCHOR_MARGIN = 0.20
GLOBAL_ANCHOR_MIN_LEN = 8
GLOBAL_ANCHOR_TH = 0.80
GLOBAL_ANCHOR_MARGIN = 0.25
MERGE_TH = 0.70
MERGE_GAIN = 0.15
LOW_CONF_BAND = 0.05
JUNK_MAX_LEN = 0  # CẤM coi câu ngắn là rác: 1-2 chữ (A!, Cút!, Dừng tay!) đều là thoại hợp lệ 100%

# --- NGƯỠNG KẸP BÁNH MÌ KHOANH VÙNG (SANDWICH CONTAINMENT) ---
SANDWICH_ANCHOR_MIN_RATIO = 0.45   # Độ tin cậy tối thiểu để 1 câu được công nhận là Vỏ Bánh Mì chuẩn gốc
SANDWICH_SKIP_MAX_CHUNKS = 15      # Tối đa số mẩu ASR ngắn bị LLM lược bỏ nằm giữa 2 vỏ bánh mì
SANDWICH_SKIP_MAX_CHARS = 150      # Tổng chữ Hán tối đa của khe bị lược bỏ (khoanh vùng câu cảm thán/câu phụ ngắn)
SANDWICH_SKIP_MAX_DUR = 35.0       # Tổng thời lượng tối đa (giây) của khe bị lược bỏ
SANDWICH_ORPHAN_MAX_CHUNKS = 25    # Tối đa số mẩu ASR lọt khe gán cho cụm câu mồ côi nằm giữa 2 vỏ bánh mì
SANDWICH_ORPHAN_MAX_CHARS = 300    # Tổng chữ Hán tối đa cho phép kẹp cứu cụm câu mồ côi
SANDWICH_ORPHAN_MAX_DUR = 60.0     # Tổng thời lượng tối đa cho phép kẹp cứu cụm câu mồ côi

JUNK_PHRASES = {'谢谢观看', '感谢观看', '谢谢收看', '请订阅', '字幕由'}

# Các hư từ tiếng Trung phổ biến dễ trùng ngẫu nhiên nếu chỉ xét 1 chữ đơn lẻ
COMMON_ZH_STOP_CHARS = set("我你他的了吗吧啊呢不是在有就都而及与着或一个")

STOPWORDS_VI = {
    "là", "của", "và", "có", "những", "các", "được", "một", "với", "này", "đó",
    "thì", "mà", "cho", "trong", "đã", "đang", "sẽ", "đến", "tại", "vào", "ra",
    "người", "khi", "như", "lại", "thấy", "thế", "gì", "nào", "ở", "bị", "bởi"
}

# --- CHUẨN HÓA CHỮ HÁN ---
try:  # tùy chọn: phồn thể -> giản thể
    from opencc import OpenCC  # type: ignore
    _T2S = OpenCC('t2s').convert
except Exception:  # pragma: no cover
    _T2S = None

_ZH_DIGIT = {
    '零': 0, '〇': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4,
    '五': 5, '六': 6, '七': 7, '八': 8, '九': 9
}
_ZH_UNITS = {'十': 10, '百': 100, '千': 1000}
_ZH_NUM_RE = re.compile(r'[零〇一二两三四五六七八九十百千万]+')


def _zh_run_to_arabic(run: str) -> str:
    """Đổi một cụm chữ số Hán liền nhau sang chữ số Ả Rập (二十三 -> 23, 二零二四 -> 2024)."""
    if not any(ch in _ZH_UNITS or ch == '万' for ch in run):
        return ''.join(str(_ZH_DIGIT[ch]) for ch in run)
    total, section, num = 0, 0, 0
    for ch in run:
        if ch in _ZH_DIGIT:
            num = _ZH_DIGIT[ch]
        elif ch in _ZH_UNITS:
            if num == 0:
                num = 1
            section += num * _ZH_UNITS[ch]
            num = 0
        else:  # 万
            section += num
            total += (section if section else 1) * 10000
            section, num = 0, 0
    return str(total + section + num)


def norm_zh(text: Any) -> str:
    """Chuẩn hóa chữ Hán để so khớp: bỏ dấu câu/khoảng trắng, bỏ tiền tố đánh số RÕ RÀNG ([298], '298. '),
    quy chữ số Hán về Ả Rập, (tùy chọn) phồn -> giản. KHÔNG cắt chữ số đầu nội dung."""
    s = unicodedata.normalize('NFKC', str(text or ""))
    s = re.sub(r'^\s*\[\d+\][\.\:\s]*', '', s)
    s = re.sub(r'^\s*\d+[\.\:\-]\s+', '', s)
    if _T2S is not None:
        try:
            s = _T2S(s)
        except Exception:
            pass
    s = _ZH_NUM_RE.sub(lambda m: _zh_run_to_arabic(m.group(0)), s)
    s = re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]', '', s)
    return s.lower().strip()


def is_junk_text(text: str) -> bool:
    """CẤM XÓA NỘI DUNG: Mọi câu thoại 1-2 chữ (A!, Cút!, Dừng tay!) đều là thoại hợp lệ 100%.
    Chỉ coi là rác nếu chuỗi rỗng HOẶC trùng đúng cụm watermark quảng cáo (JUNK_PHRASES)."""
    return (not text) or (text in JUNK_PHRASES)


def is_junk_segment(seg: Dict[str, Any]) -> bool:
    raw = seg.get('clean_text') or seg.get('text') or seg.get('zh')
    if raw is None:
        return False  # không có thông tin chữ -> không dám coi là rác
    return is_junk_text(norm_zh(raw))


def _lcs_length(a: str, b: str) -> int:
    """Đếm số ký tự trùng nhau theo ĐÚNG THỨ TỰ từ trái sang phải (Longest Common Subsequence)."""
    if not a or not b:
        return 0
    m, n = len(a), len(b)
    dp = [0] * (n + 1)
    for i in range(1, m + 1):
        prev = 0
        for j in range(1, n + 1):
            temp = dp[j]
            if a[i - 1] == b[j - 1]:
                dp[j] = prev + 1
            else:
                dp[j] = max(dp[j], dp[j - 1])
            prev = temp
    return dp[n]


def _longest_contiguous_match(a: str, b: str) -> int:
    """Độ dài chuỗi con LIỀN KỀ dài nhất trùng nhau giữa a và b."""
    if not a or not b:
        return 0
    match = SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b))
    return match.size


def calc_piece_similarity(s1: str, s2: str) -> float:
    """Tương đồng NHẠY THỨ TỰ (SequenceMatcher) đã bỏ phạt kép bất hợp lý đối với câu ngắn."""
    if not s1 or not s2:
        return 0.0
    if s1 == s2:
        return 1.0
    l1, l2 = len(s1), len(s2)
    min_l, max_l = min(l1, l2), max(l1, l2)

    # Nếu câu ngắn (<= 5 chữ) và một chuỗi là chuỗi con liền kề (>= 2 chữ) của chuỗi kia (VD: '先凡' trong '先凡有别')
    if max_l <= 6 and min_l >= 2 and (s1 in s2 or s2 in s1):
        return max(0.82, SequenceMatcher(None, s1, s2, autojunk=False).ratio())

    sm_ratio = SequenceMatcher(None, s1, s2, autojunk=False).ratio()
    diff = abs(l1 - l2)
    len_ratio = (diff / max_l) if max_l > 0 else 0.0

    if max_l <= 5:
        # Với câu ngắn: dùng thêm điểm LCS theo thứ tự để không bị tụt thê thảm khi mất 1-2 chữ
        lcs_cnt = _lcs_length(s1, s2)
        contig_cnt = _longest_contiguous_match(s1, s2)
        if contig_cnt >= 2:
            return max(sm_ratio, 0.75)
        if lcs_cnt >= 2:
            return max(sm_ratio, 0.68)
        if diff <= 1:
            return sm_ratio
        return sm_ratio * 0.85

    if len_ratio <= 0.25:  # mảnh dài: dung sai 25% độ dài
        return sm_ratio
    return sm_ratio * max(0.0, 1.0 - (len_ratio - 0.25) * 2.0)


def is_sequential_short_match(llm_txt: str, asr_i1_txt: str, asr_i2_txt: Optional[str] = None) -> bool:
    """
    Kiểm tra đặc quyền cho CÂU NGẮN tại đúng vị trí tuần tự i+1:
    So sánh dựa trên SỐ ĐẾM TỪ TRÙNG + THỨ TỰ TỪ, kèm liếc nhẹ sang i+2 để chống nhảy cóc.
    """
    if not llm_txt or not asr_i1_txt:
        return False
    l_llm, l_i1 = len(llm_txt), len(asr_i1_txt)
    # Chỉ áp dụng cơ chế câu ngắn khi câu ASR gốc <= 5 chữ hoặc mảnh LLM <= 4 chữ (và câu gốc <= 7 chữ)
    if not (l_i1 <= SHORT_SEQ_MAX_LEN or (l_llm <= 4 and l_i1 <= 7)):
        return False

    lcs_i1 = _lcs_length(llm_txt, asr_i1_txt)
    contig_i1 = _longest_contiguous_match(llm_txt, asr_i1_txt)
    lcs_i2 = _lcs_length(llm_txt, asr_i2_txt) if asr_i2_txt else 0

    # Nếu nó khớp với câu i+2 nhiều hơn hẳn câu i+1 (chứng tỏ LLM bỏ sót i+1 và đang dịch i+2) -> Từ chối chốt vào i+1
    if asr_i2_txt and lcs_i2 > lcs_i1 and lcs_i2 >= 2:
        if calc_piece_similarity(llm_txt, asr_i2_txt) > calc_piece_similarity(llm_txt, asr_i1_txt) + 0.20:
            return False

    # Mức 1: Trùng >= 2 chữ liền kề đúng thứ tự (VD: '先凡' trong '先凡有别') -> Chốt cứng
    if contig_i1 >= 2:
        return True

    # Mức 2: Trùng >= 2 chữ theo đúng thứ tự từ trái sang phải (LCS >= 2)
    if lcs_i1 >= 2:
        common_chars = set(llm_txt) & set(asr_i1_txt)
        # Không phải toàn bộ chữ trùng đều là hư từ vô nghĩa, hoặc độ dài rất ngắn
        if (common_chars - COMMON_ZH_STOP_CHARS) or max(l_llm, l_i1) <= 3:
            return True

    # Mức 3: Câu cực ngắn (<= 3 chữ) và trùng đúng 1 chữ
    if max(l_llm, l_i1) <= 3 and lcs_i1 == 1:
        common_chars = set(llm_txt) & set(asr_i1_txt)
        meaningful = common_chars - COMMON_ZH_STOP_CHARS
        same_edge_pos = (llm_txt[0] == asr_i1_txt[0]) or (llm_txt[-1] == asr_i1_txt[-1])
        # Chấp nhận nếu chữ trùng là thực từ có nghĩa HOẶC nằm cùng vị trí đầu/cuối câu
        if meaningful or same_edge_pos:
            # Đảm bảo không phải câu của i+2
            if not asr_i2_txt or lcs_i2 <= 1:
                return True

    return False


def _wc(text: str) -> int:
    return len(re.findall(r'\b\w+\b', str(text or "")))


def _words(text: str) -> List[str]:
    return [w.lower() for w in re.findall(r'\b\w+\b', str(text or ""))]


def _seg_text(seg: Dict[str, Any]) -> str:
    return norm_zh(seg.get('clean_text') or seg.get('text') or seg.get('zh') or '')


class SmartTimelineHealer:
    """SmartTimelineHealer v5.3 — Kẹp Bánh Mì Khoanh Vùng & Miễn Phạt Gộp Câu Hợp Lệ."""

    # Đã loại bỏ hoàn toàn MERGED_CHUNKS_IN_PIECE, MERGE_ACROSS_GAP, GAP_COLLISION,
    # MERGE_ACROSS_KHOA và INTERNAL_GAP khỏi HARD_FLAG_PREFIXES.
    HARD_FLAG_PREFIXES = (
        "DROPPED_CHUNK", "UNSYNC_SPAN", "PIECE_UNMATCHED", "PIECE_UNASSIGNED", "AMBIGUOUS",
        "OUT_OF_ORDER", "NO_SRC", "INVALID_SRC", "ZH_MISSING", "NEEDS_REVIEW", "ONE2ONE",
    )

    # =========================================================================
    # API CHÍNH
    # =========================================================================
    @classmethod
    def audit_and_heal(cls, orig_segments, ai_items, entity_table_text: str = ''):
        return cls.check_and_heal(orig_segments, ai_items, entity_table_text)

    @classmethod
    def check_and_heal(cls, orig_segments, ai_items, entity_table_text: str = ''):
        items, fixes, logs, _windows = cls.check_and_heal_ex(orig_segments, ai_items, entity_table_text)
        return items, fixes, logs

    @classmethod
    def check_and_heal_ex(cls, orig_segments: List[Dict[str, Any]], ai_items: List[Dict[str, Any]],
                          entity_table_text: str = '', verbose: bool = True):
        """Như check_and_heal nhưng trả thêm danh sách cửa sổ retry cục bộ."""
        if not orig_segments or not ai_items:
            return ai_items, 0, [], []

        N = len(orig_segments)
        seg_map = {s.get("index", i + 1): s for i, s in enumerate(orig_segments)}
        logs: List[str] = []
        fixes = 0

        # BƯỚC 1: căn chỉnh từng mảnh zh vào ASR
        has_zh = any(bool(str(it.get('zh', '')).strip()) or bool(it.get('zh_pieces')) for it in ai_items)
        if has_zh:
            aligned, tele = cls.align_sequential(ai_items, orig_segments)
            fixes = (
                int(tele.get('junk_skipped_count', 0))
                + int(tele.get('ambig_resolved', 0))
                + int(tele.get('short_seq_rescued', 0))
                + int(tele.get('sandwich_rescued', 0))
                + int(tele.get('internal_gaps_healed', 0))
            )
            logs.append(
                f"[Align v5.3] {tele['matched_pieces']}/{tele['total_pieces']} mảnh khớp | "
                f"cứu câu ngắn={tele.get('short_seq_rescued', 0)} | kẹp bánh mì={tele.get('sandwich_rescued', 0)} | "
                f"lấp khe={tele.get('internal_gaps_healed', 0)} | rác bỏ qua={tele.get('junk_skipped_count', 0)} | "
                f"mẩu nội dung rơi={len(tele.get('unassigned_real_dropped', []))} | cờ={dict(tele['flags'])}")
        else:
            aligned = []
            for it in ai_items:
                c = dict(it)
                c['src'] = cls._norm_src(c.get('src', []), seg_map)
                c['align_ok'] = False
                c['hard_flags'] = ['ZH_MISSING_ENTIRE_BATCH']
                c['soft_flags'] = []
                c['_ok'] = False
                aligned.append(c)
            logs.append("[Align v5.3] Lô không có trường zh -> ZH_MISSING_ENTIRE_BATCH, yêu cầu dịch lại.")

        # BƯỚC 1.5: CẮT TUẦN TỰ câu gộp băng qua khoảng lặng >= GAP_SPLIT_TH (chỉ câu thỏa điều kiện mới vào nhánh cắt)
        aligned, gap_split_cnt, gap_split_logs = cls.split_items_at_hard_gaps(aligned, seg_map)
        if gap_split_cnt:
            fixes += gap_split_cnt
            logs.extend(gap_split_logs)

        # BƯỚC 2: ghi chú mềm cho câu gộp băng qua khoảng lặng cứng ([KHÓA]) — KHÔNG đánh lỗi cứng
        cls.flag_gap_collisions(aligned, seg_map)

        # BƯỚC 3: mốc thời gian + trần (kẹp đơn điệu thời gian trước khi audit tốc độ đọc)
        rebuilt = cls._rebuild(aligned, orig_segments)

        # BƯỚC 4: audit mềm (tính dur, wps, speedup theo mốc chuẩn xác đã kẹp)
        audited = cls.audit_soft_signals(rebuilt, seg_map)

        # BƯỚC 5: cửa sổ retry cục bộ
        windows = cls.build_retry_windows(audited, orig_segments)
        for w in windows:
            for idx in w['item_indices']:
                audited[idx]['retry_window'] = w['window_index']

        healed = audited

        # BƯỚC 6: cờ retry CHỈ cho câu lỗi cứng thực sự (không thể khoanh vùng bằng bánh mì kẹp thịt)
        for it in healed:
            bad = (not it.get('_ok', True)) or bool(it.get('hard_flags')) or not it.get('src')
            it['_retry_needed'] = bad
            it.setdefault('is_quarantined', False)

        fails = sum(1 for it in healed if it['_retry_needed'])
        passed = len(healed) - fails
        sep = '=' * 58
        summary = (f"\n{sep}\nSmartTimelineHealer v5.3 | {len(healed)} câu | {N} mẩu ASR\n"
                   f"  PASSED={passed} CẦN_RETRY={fails} TỰ_CỨU={fixes} | CỬA_SỔ_RETRY={len(windows)}\n"
                   f"  KẾT QUẢ: {'CAN DICH LAI (cục bộ)' if windows else 'DAT'}\n{sep}")
        if verbose:
            try:
                print(summary, flush=True)
            except Exception:
                pass
        return healed, fixes, [summary] + logs, windows

    # =========================================================================
    # KHỐI 1: CĂN CHỈNH TỪNG MẢNH ZH VÀO ASR & KẸP BÁNH MÌ KHOANH VÙNG
    # =========================================================================
    @staticmethod
    def _extract_pieces(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        pieces: List[Dict[str, Any]] = []
        for it_idx, it in enumerate(items):
            raw_pieces = it.get('zh_pieces')
            if not raw_pieces:
                zh_val = str(it.get('zh', '') or '').strip()
                raw_pieces = re.split(r'[/／]', zh_val) if zh_val else []
            for raw in raw_pieces:
                norm = norm_zh(raw)
                if not norm:
                    continue
                pieces.append({
                    'item_idx': it_idx,
                    'norm': norm,
                    'raw': str(raw),
                    'chunks': [],
                    'flags': [],
                    'best': None,
                    'margin': None
                })
        return pieces

    @classmethod
    def _is_trusted_anchor_item(cls, it: Dict[str, Any]) -> bool:
        """
        Kiểm tra một câu kịch bản có đủ chuẩn làm 'Vỏ Bánh Mì' (Anchor) không:
        - Có src hợp lệ.
        - Không mang lỗi trượt cấu trúc nghiêm trọng (OUT_OF_ORDER, NO_SRC, INVALID_SRC, ZH_MISSING).
        - Khớp với ASR gốc ở mức tin cậy hoặc đã được cứu qua các cơ chế căn chỉnh tuần tự.
        """
        src = it.get('src') or []
        if not src:
            return False
        h_flags = it.get('hard_flags') or []
        if any(f.startswith(('OUT_OF_ORDER', 'NO_SRC', 'INVALID_SRC', 'ZH_MISSING')) for f in h_flags):
            return False
        ratio = float(it.get('match_ratio', 0.0) or 0.0)
        if ratio >= SANDWICH_ANCHOR_MIN_RATIO or any(
            f.startswith(('SHORT_SEQ_', 'SANDWICH_', 'SPAN_LOCKED_'))
            for f in it.get('soft_flags', [])
        ):
            return True
        return False

    @classmethod
    def _apply_sandwich_containment(
        cls,
        out: List[Dict[str, Any]],
        order: List[int],
        pos_of: Dict[int, int],
        seg_map: Dict[int, Dict[str, Any]],
        texts: Dict[int, str],
        junk: Set[int],
        telemetry: Dict[str, Any],
    ) -> None:
        """
        KẸP BÁNH MÌ KHOANH VÙNG LỖI CỤC BỘ (Sandwich Containment v2):
        Nếu câu trước (L) và câu sau (R) đều là các Neo chuẩn vị trí gốc (Trusted Anchors),
        mọi biến động nhỏ nằm gọn trong khe (L..R) sẽ được khoanh vùng tại chỗ mà không bắt dịch lại:
          1. Cụm 1..4 câu mồ côi (NO_SRC / PIECE_UNMATCHED / UNSYNC_SPAN) nằm kẹp giữa L và R:
             Phân bổ đơn điệu các mẩu ASR chưa ai nhận nằm giữa L và R cho các câu mồ côi.
          2. Câu ảo tự sinh (Phantom Injection) khi giữa L và R không còn mẩu ASR nào trống:
             Tự động gọt bỏ để không làm gãy timeline.
          3. Câu đã có src chuẩn và nằm khít tuần tự giữa L và R nhưng còn vướng cờ phụ
             (PIECE_UNMATCHED / PIECE_UNASSIGNED / AMBIGUOUS / UNSYNC_SPAN / NEEDS_REVIEW_UNSYNC):
             Hạ cờ cứng xuống cờ mềm (CONTAINED_*).
        """
        num_items = len(out)
        if num_items == 0 or not order:
            return

        all_assigned = {c for o in out for c in (o.get('src') or [])}

        # --- Tầng A: Cứu cụm câu mồ côi (1..4 câu liên tiếp rỗng src) bị kẹp giữa 2 Vỏ Bánh Mì chuẩn ---
        idx = 0
        while idx < num_items:
            if out[idx].get('src'):
                idx += 1
                continue
            if any(f.startswith('OUT_OF_ORDER') for f in out[idx].get('hard_flags', [])):
                idx += 1
                continue

            run_start = idx
            while idx < num_items and not out[idx].get('src') and not any(
                f.startswith('OUT_OF_ORDER') for f in out[idx].get('hard_flags', [])
            ):
                idx += 1
            run_end = idx - 1
            run_len = run_end - run_start + 1
            if run_len > 10:
                continue

            left_item = out[run_start - 1] if run_start > 0 else None
            right_item = out[run_end + 1] if run_end + 1 < num_items else None

            if left_item is not None and not cls._is_trusted_anchor_item(left_item):
                continue
            if right_item is not None and not cls._is_trusted_anchor_item(right_item):
                continue
            if left_item is None and right_item is None:
                continue

            l_pos = (pos_of[left_item['src'][-1]] + 1) if left_item is not None else 0
            r_pos = pos_of[right_item['src'][0]] if right_item is not None else len(order)
            if l_pos > r_pos:
                continue

            avail_between = [
                order[p]
                for p in range(l_pos, r_pos)
                if order[p] not in all_assigned and order[p] not in junk
            ]
            if not avail_between:
                avail_between = [
                    order[p]
                    for p in range(l_pos, r_pos)
                    if order[p] not in all_assigned
                ]
            if not avail_between or len(avail_between) > SANDWICH_ORPHAN_MAX_CHUNKS * run_len:
                continue

            gap_chars = sum(len(texts.get(c, '')) for c in avail_between)
            gap_dur = sum(
                max(0.0, float(seg_map[c].get('end', 0.0) or 0.0) - float(seg_map[c].get('start', 0.0) or 0.0))
                for c in avail_between
            )
            if gap_chars <= SANDWICH_ORPHAN_MAX_CHARS * run_len and gap_dur <= SANDWICH_ORPHAN_MAX_DUR * run_len:
                m_cnt = len(avail_between)
                for offset in range(run_len):
                    it_orph = out[run_start + offset]
                    if m_cnt >= run_len:
                        c_lo = (offset * m_cnt) // run_len
                        c_hi = ((offset + 1) * m_cnt) // run_len
                        assigned_slice = sorted(avail_between[c_lo:max(c_lo + 1, c_hi)])
                    else:
                        if offset < m_cnt:
                            assigned_slice = [avail_between[offset]]
                        else:
                            assigned_slice = []

                    if assigned_slice:
                        it_orph['src'] = assigned_slice
                        it_orph['n_t1'] = len(assigned_slice)
                        it_orph['match_ratio'] = max(float(it_orph.get('match_ratio', 0.0) or 0.0), 0.70)
                        rescued_flags = [
                            f for f in it_orph.get('hard_flags', [])
                            if not f.startswith((
                                'NO_SRC', 'PIECE_UNMATCHED', 'PIECE_UNASSIGNED',
                                'AMBIGUOUS', 'UNSYNC_SPAN', 'NEEDS_REVIEW_UNSYNC'
                            ))
                        ]
                        demoted = [
                            f for f in it_orph.get('hard_flags', [])
                            if f.startswith((
                                'NO_SRC', 'PIECE_UNMATCHED', 'PIECE_UNASSIGNED',
                                'AMBIGUOUS', 'UNSYNC_SPAN', 'NEEDS_REVIEW_UNSYNC'
                            ))
                        ]
                        it_orph['hard_flags'] = rescued_flags
                        it_orph['soft_flags'] = list(it_orph.get('soft_flags', [])) + [
                            f'SANDWICH_CONTAINED_ORPHAN_{assigned_slice}'
                        ] + [f'CONTAINED_{d}' for d in demoted]
                        it_orph['align_ok'] = len(it_orph['hard_flags']) == 0
                        it_orph['_ok'] = it_orph['align_ok']
                        for c in assigned_slice:
                            all_assigned.add(c)
                        telemetry['sandwich_rescued'] += 1

        # --- Tầng A2: Khử câu rác do LLM tự bịa/lặp lại (Sandwich Phantom Purge) ---
        # Nếu câu rỗng src (NO_SRC) kẹp giữa 2 Neo chuẩn (hoặc biên lô) mà không còn mẩu ASR nào trống ở giữa,
        # thì đây là câu ảo do LLM tự chèn (phantom injection). Loại bỏ để bảo toàn 100% câu thật.
        indices_to_purge = []
        for idx in range(num_items):
            it = out[idx]
            if it.get('src'):
                continue
            left_item = next((out[p] for p in range(idx - 1, -1, -1) if out[p].get('src')), None)
            right_item = next((out[p] for p in range(idx + 1, num_items) if out[p].get('src')), None)
            if left_item is None and right_item is None:
                continue
            if left_item is not None and not cls._is_trusted_anchor_item(left_item):
                continue
            if right_item is not None and not cls._is_trusted_anchor_item(right_item):
                continue

            l_pos = (pos_of[left_item['src'][-1]] + 1) if left_item is not None else 0
            r_pos = pos_of[right_item['src'][0]] if right_item is not None else len(order)
            avail = [
                order[p]
                for p in range(l_pos, min(len(order), max(l_pos, r_pos)))
                if order[p] not in all_assigned and order[p] not in junk
            ]
            if not avail:
                indices_to_purge.append(idx)

        if indices_to_purge:
            for purge_idx in sorted(indices_to_purge, reverse=True):
                purged_it = out.pop(purge_idx)
                telemetry['sandwich_rescued'] += 1
                logger.info(
                    f"🧹 [Sandwich Phantom Purge] Đã loại bỏ câu ảo tự sinh không có trong ASR: "
                    f"'{purged_it.get('zh', '')}' -> '{purged_it.get('vi', '')}'"
                )
            num_items = len(out)

        # --- Tầng B: Câu đã có src hợp lệ, nằm gọn đơn điệu giữa 2 Neo nhưng thừa mảnh phụ / UNSYNC cục bộ ---
        for idx in range(num_items):
            it = out[idx]
            src = it.get('src') or []
            h_flags = it.get('hard_flags') or []
            if not src or not h_flags:
                continue
            if any(f.startswith(('OUT_OF_ORDER', 'DROPPED_CHUNK', 'ZH_MISSING', 'INVALID_SRC')) for f in h_flags):
                continue

            left_ok = False
            if idx == 0 and pos_of[src[0]] <= SANDWICH_SKIP_MAX_CHUNKS:
                left_ok = True
            elif idx > 0 and out[idx - 1].get('src'):
                l_end = out[idx - 1]['src'][-1]
                if 0 <= pos_of[src[0]] - pos_of[l_end] <= SANDWICH_SKIP_MAX_CHUNKS + 1:
                    left_ok = True

            right_ok = False
            if idx == num_items - 1 and (len(order) - 1 - pos_of[src[-1]]) <= SANDWICH_SKIP_MAX_CHUNKS:
                right_ok = True
            elif idx + 1 < num_items and out[idx + 1].get('src'):
                r_start = out[idx + 1]['src'][0]
                if 0 <= pos_of[r_start] - pos_of[src[-1]] <= SANDWICH_SKIP_MAX_CHUNKS + 1:
                    right_ok = True

            if left_ok and right_ok:
                kept_hard = [
                    f for f in h_flags
                    if not f.startswith((
                        'PIECE_UNMATCHED', 'PIECE_UNASSIGNED', 'AMBIGUOUS',
                        'UNSYNC_SPAN', 'NEEDS_REVIEW_UNSYNC'
                    ))
                ]
                demoted = [
                    f for f in h_flags
                    if f.startswith((
                        'PIECE_UNMATCHED', 'PIECE_UNASSIGNED', 'AMBIGUOUS',
                        'UNSYNC_SPAN', 'NEEDS_REVIEW_UNSYNC'
                    ))
                ]
                if demoted:
                    it['hard_flags'] = kept_hard
                    it['soft_flags'] = list(it.get('soft_flags', [])) + [
                        f'SANDWICH_CONTAINED_PARTIAL_{src}'
                    ] + [f'CONTAINED_{d}' for d in demoted]
                    it['align_ok'] = len(it['hard_flags']) == 0
                    it['_ok'] = it['align_ok']
                    telemetry['sandwich_rescued'] += 1

    @classmethod
    def align_sequential(cls, items: List[Dict[str, Any]], orig_segs: List[Dict[str, Any]]
                         ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        seg_map = {s.get('index', i + 1): s for i, s in enumerate(orig_segs)}
        order = sorted(seg_map.keys())
        pos_of = {c: i for i, c in enumerate(order)}
        texts = {c: _seg_text(seg_map[c]) for c in order}
        junk: Set[int] = {c for c in order if is_junk_segment(seg_map[c])}

        telemetry: Dict[str, Any] = {
            'total_pieces': 0, 'matched_pieces': 0, 'junk_skipped_count': 0, 'ambig_resolved': 0,
            'short_seq_rescued': 0, 'sandwich_rescued': 0, 'internal_gaps_healed': 0,
            'best_scores': [], 'margins': [], 'flags': Counter(),
            'dropped_by_confirm': [], 'unsync_chunks': [],
        }

        pieces = cls._extract_pieces(items)
        n = len(pieces)
        telemetry['total_pieces'] = n

        assigned: Set[int] = set()
        junk_covered: Set[int] = set()
        st = {'ptr': 0, 'fails': 0, 'fail_start': None}

        def window(start_ptr: int, w_content: int = W_CONTENT, max_total: int = MAX_WINDOW) -> List[int]:
            cands: List[int] = []
            content = 0
            i = start_ptr
            L = len(order)
            while i < L and content < w_content and len(cands) < max_total:
                c = order[i]
                if c not in assigned:
                    cands.append(c)
                    if c not in junk:
                        content += 1
                i += 1
            return cands

        def first_content_cand(cands: List[int]) -> Tuple[Optional[int], List[int], Optional[int]]:
            """Tìm ứng viên nội dung đầu tiên (vị trí tuần tự i+1), danh sách rác đứng trước nó, và ứng viên i+2."""
            junk_before: List[int] = []
            c_i1: Optional[int] = None
            c_i2: Optional[int] = None
            for c in cands:
                if c_i1 is None:
                    if c in junk:
                        junk_before.append(c)
                    else:
                        c_i1 = c
                else:
                    if c not in junk:
                        c_i2 = c
                        break
            # Nếu toàn bộ cands chỉ là junk thì lấy cands[0] làm i+1
            if c_i1 is None and cands:
                c_i1 = cands[0]
                junk_before = []
                c_i2 = cands[1] if len(cands) > 1 else None
            return c_i1, junk_before, c_i2

        def score(txt: str, cands: List[int]) -> List[Tuple[int, float]]:
            res = [(c, calc_piece_similarity(txt, texts[c])) for c in cands]
            res.sort(key=lambda x: (-x[1], x[0]))
            return res

        def th_of(txt: str) -> float:
            return TH_SHORT if len(txt) <= SHORT_LEN else TH_NORMAL

        def judge(txt: str, scores: List[Tuple[int, float]]):
            """-> (best_chunk, best_score, margin, verdict) với verdict in {none, low, clear, tie}"""
            if not scores:
                return None, 0.0, 0.0, 'none'
            best_c, best_sc = scores[0]
            second = scores[1][1] if len(scores) > 1 else 0.0
            margin = best_sc - second
            if best_sc < th_of(txt):
                return best_c, best_sc, margin, 'low'
            if len(scores) == 1 or margin >= MARGIN_MIN:
                return best_c, best_sc, margin, 'clear'
            return best_c, best_sc, margin, 'tie'

        def confirm(k: int, after_ptr: int) -> bool:
            """Xác nhận bằng mảnh kế tiếp: một trong CONFIRM_LOOKAHEAD mảnh sau khớp RÕ ngay sau vị trí đang xét."""
            for j in range(k + 1, min(n, k + 1 + CONFIRM_LOOKAHEAD)):
                txt = pieces[j]['norm']
                cands = window(after_ptr, CONFIRM_W)
                if not cands:
                    return False
                _, _, _, verdict = judge(txt, score(txt, cands))
                if verdict == 'clear':
                    return True
            return False

        def confirm_sandwich_1to1(k: int, target_i1: int) -> bool:
            """
            Kẹp bánh mì tuần tự 1-1: Kiểm tra xem mảnh kế tiếp (k+1) có khớp trực tiếp
            với ngay mẩu nội dung kế tiếp (i+2) sau target_i1 mà không bỏ qua mẩu nội dung nào không.
            """
            if k + 1 >= n:
                return False
            next_txt = pieces[k + 1]['norm']
            next_cands = window(pos_of[target_i1] + 1, CONFIRM_W)
            if not next_cands:
                return False
            c_i2, _, c_i3 = first_content_cand(next_cands)
            if c_i2 is None:
                return False
            sc_next = score(next_txt, next_cands)
            best_c, best_sc, _, verdict = judge(next_txt, sc_next)
            if best_c == c_i2 and verdict in ('clear', 'tie') and best_sc >= th_of(next_txt):
                return True
            if is_sequential_short_match(next_txt, texts[c_i2], texts[c_i3] if c_i3 else None):
                return True
            return False

        def confirm_box_sandwich(k: int, cands: List[int]) -> Optional[Tuple[int, List[int], List[int], float]]:
            """
            Kẹp bánh mì Hộp Kín Đa Tỷ Lệ (M mảnh LLM : N mẩu ASR, với M, N in 1..3).
            Chỉ kích hoạt khi:
              - Đầu trái đã khóa cứng tại st['ptr'].
              - Đầu phải (mảnh k + M) khóa cứng vào đúng mẩu nội dung thứ N+1 (c_right), hoặc chạm cuối lô.
              - Tổng chữ Hán bên trong hộp khớp chặt với nhau (không cho phép trượt vị trí).
            Trả về: (m_span, box_content_chunks, box_junk_chunks, box_sim) hoặc None.
            """
            content_cands = [c for c in cands if c not in junk]
            if not content_cands:
                return None

            curr_item_idx = pieces[k]['item_idx']

            for m_span in (1, 2, 3):
                if k + m_span > n:
                    break
                # Chốt chặn 1: Các mảnh trong hộp kín BẮT BUỘC phải thuộc cùng 1 câu kịch bản
                if any(pieces[k + idx]['item_idx'] != curr_item_idx for idx in range(m_span)):
                    break

                box_llm_txt = ''.join(pieces[k + idx]['norm'] for idx in range(m_span))
                if not box_llm_txt:
                    continue

                for n_span in (1, 2, 3):
                    if n_span > len(content_cands):
                        break
                    box_chunks = content_cands[:n_span]
                    last_box_c = box_chunks[-1]
                    # Mọi mẩu nằm từ đầu cands đến last_box_c nhưng thuộc junk sẽ được gom vào junk_skipped
                    box_junk = [c for c in cands if c < last_box_c and c in junk]

                    # Kiểm tra khóa Neo Phải (Right Anchor Lock)
                    right_locked = False
                    if k + m_span == n:
                        right_locked = (n_span == len(content_cands))
                    elif n_span < len(content_cands):
                        c_right = content_cands[n_span]
                        right_txt = pieces[k + m_span]['norm']
                        after_cands = window(pos_of[last_box_c] + 1, CONFIRM_W)
                        if after_cands:
                            sc_r = score(right_txt, after_cands)
                            best_rc, best_rsc, _, r_verdict = judge(right_txt, sc_r)
                            if best_rc == c_right and r_verdict in ('clear', 'tie') and best_rsc >= th_of(right_txt):
                                right_locked = True
                            elif is_sequential_short_match(right_txt, texts[c_right], None):
                                right_locked = True

                    if not right_locked:
                        continue

                    box_asr_txt = ''.join(texts[c] for c in box_chunks)
                    box_sim = calc_piece_similarity(box_llm_txt, box_asr_txt)
                    lcs_cnt = _lcs_length(box_llm_txt, box_asr_txt)

                    # Tỷ lệ 1:1 có Neo Trái + Neo Phải khóa khít tuyệt đối
                    if m_span == 1 and n_span == 1:
                        if box_sim >= 0.45 or lcs_cnt >= 2 or (lcs_cnt >= 1 and box_llm_txt in box_asr_txt):
                            return m_span, box_chunks, box_junk, max(box_sim, 0.72)
                    else:
                        # Tỷ lệ M:N (2:1, 3:1, 1:2, 2:2...) đòi hỏi tổng chữ Hán trong hộp khớp cao (>= 0.72)
                        if box_sim >= 0.72 or (lcs_cnt >= max(3, int(len(box_asr_txt) * 0.75))):
                            return m_span, box_chunks, box_junk, box_sim
            return None

        def commit(k: int, chunks: List[int], junk_skipped=()):
            pieces[k]['chunks'] = list(chunks)
            for c in chunks:
                assigned.add(c)
            for c in junk_skipped:
                junk_covered.add(c)
            telemetry['junk_skipped_count'] += len(list(junk_skipped))
            st['ptr'] = pos_of[max(chunks)] + 1
            st['fails'] = 0
            st['fail_start'] = None

        def note_fail(k: int):
            if st['fails'] == 0:
                st['fail_start'] = k
            st['fails'] += 1

        def find_anchor(k_from: int):
            for j in range(k_from, min(n, k_from + ANCHOR_LOOKAHEAD)):
                t = pieces[j]['norm']
                if len(t) < ANCHOR_MIN_LEN:
                    continue
                sc = score(t, window(st['ptr'], ANCHOR_WIN_CONTENT, ANCHOR_WIN_TOTAL))
                if sc and sc[0][1] >= ANCHOR_TH and (len(sc) == 1 or sc[0][1] - sc[1][1] >= ANCHOR_MARGIN):
                    return j, sc[0][0]
            for j in range(k_from, min(n, k_from + ANCHOR_LOOKAHEAD)):
                t = pieces[j]['norm']
                if len(t) < GLOBAL_ANCHOR_MIN_LEN:
                    continue
                sc = score(t, window(st['ptr'], 10 ** 9, 10 ** 9))
                if sc and sc[0][1] >= GLOBAL_ANCHOR_TH and (len(sc) == 1 or sc[0][1] - sc[1][1] >= GLOBAL_ANCHOR_MARGIN):
                    return j, sc[0][0]
            return None

        def resync(k: int) -> str:
            """Gọi khi thất bại liên tiếp. Trả 'jump' (đã neo lại, k mới ở st['k']) hoặc 'stop'."""
            found = find_anchor(k + 1)
            fs = st['fail_start'] if st['fail_start'] is not None else k
            if found is None:
                unmatched_tail = [r for r in range(fs, n) if not pieces[r]['chunks']]
                avail_tail = [c for c in order[st['ptr']:] if c not in assigned and c not in junk]
                if unmatched_tail and avail_tail and len(unmatched_tail) <= 8 and len(avail_tail) <= 35:
                    m_cnt = len(avail_tail)
                    u_cnt = len(unmatched_tail)
                    for idx_u, r in enumerate(unmatched_tail):
                        if m_cnt >= u_cnt:
                            c_lo = (idx_u * m_cnt) // u_cnt
                            c_hi = ((idx_u + 1) * m_cnt) // u_cnt
                            c_slice = avail_tail[c_lo:max(c_lo + 1, c_hi)]
                        else:
                            c_slice = [avail_tail[min(idx_u, m_cnt - 1)]]
                        pieces[r]['chunks'] = list(c_slice)
                        pieces[r]['best'] = max(pieces[r].get('best') or 0.0, 0.70)
                        pieces[r]['flags'] = [
                            f for f in pieces[r]['flags']
                            if not f.startswith(('PIECE_UNMATCHED', 'PIECE_UNASSIGNED', 'AMBIGUOUS'))
                        ]
                        pieces[r]['flags'].append(f'SANDWICH_TAIL_HEALED_{c_slice}')
                        for c_x in c_slice:
                            assigned.add(c_x)
                    telemetry['matched_pieces'] += u_cnt
                    telemetry['sandwich_rescued'] += u_cnt
                else:
                    for r in range(fs, n):
                        if not pieces[r]['chunks']:
                            pieces[r]['flags'].append('NEEDS_REVIEW_UNSYNC')
                return 'stop'
            j, a = found
            unmatched_span = [r for r in range(fs, j) if not pieces[r]['chunks']]
            avail_span = [c for c in order[st['ptr']:pos_of[a]] if c not in assigned and c not in junk]
            if unmatched_span and avail_span and len(avail_span) <= SANDWICH_ORPHAN_MAX_CHUNKS * max(1, len(unmatched_span)):
                m_cnt = len(avail_span)
                u_cnt = len(unmatched_span)
                for idx_u, r in enumerate(unmatched_span):
                    if m_cnt >= u_cnt:
                        c_lo = (idx_u * m_cnt) // u_cnt
                        c_hi = ((idx_u + 1) * m_cnt) // u_cnt
                        c_slice = avail_span[c_lo:max(c_lo + 1, c_hi)]
                    else:
                        c_slice = [avail_span[min(idx_u, m_cnt - 1)]]
                    pieces[r]['chunks'] = list(c_slice)
                    pieces[r]['best'] = max(pieces[r].get('best') or 0.0, 0.70)
                    pieces[r]['flags'] = [
                        f for f in pieces[r]['flags']
                        if not f.startswith(('PIECE_UNMATCHED', 'PIECE_UNASSIGNED', 'AMBIGUOUS'))
                    ]
                    pieces[r]['flags'].append(f'SANDWICH_RESYNC_HEALED_{c_slice}')
                    for c_x in c_slice:
                        assigned.add(c_x)
                telemetry['matched_pieces'] += u_cnt
                telemetry['sandwich_rescued'] += u_cnt
            else:
                for r in range(fs, j):
                    if not pieces[r]['chunks']:
                        pieces[r]['flags'].append('UNSYNC_SPAN')
            for c in order[st['ptr']:pos_of[a]]:
                if c not in assigned and c not in junk:
                    telemetry['unsync_chunks'].append(c)
            st['ptr'] = pos_of[a]
            st['fails'] = 0
            st['fail_start'] = None
            st['k'] = j
            return 'jump'

        k = 0
        while k < n:
            pc = pieces[k]
            txt = pc['norm']
            cands = window(st['ptr'])
            if not cands:
                pc['flags'].append('PIECE_UNMATCHED_NO_CANDS')
                note_fail(k)
                k += 1
                continue

            c_i1, junk_before_i1, c_i2 = first_content_cand(cands)
            if junk_before_i1:
                promoted_jc = False
                for idx_j, jc in enumerate(list(junk_before_i1)):
                    next_after_jc = junk_before_i1[idx_j + 1] if idx_j + 1 < len(junk_before_i1) else c_i1
                    s_jc = calc_piece_similarity(txt, texts[jc])
                    s_i1 = calc_piece_similarity(txt, texts[c_i1]) if c_i1 is not None else 0.0
                    # TH1: Mảnh hiện tại khớp trực tiếp hoặc khớp tuần tự câu ngắn với jc (<= 2 chữ)
                    cond_direct = (
                        txt == texts[jc]
                        or (s_jc >= th_of(txt) and s_jc >= s_i1)
                        or (
                            len(txt) <= max(3, len(texts[jc]) + 1)
                            and is_sequential_short_match(txt, texts[jc], texts[next_after_jc] if next_after_jc is not None else None)
                        )
                    )
                    # TH2: Kẹp bánh mì 1-1 (Neo trái st['ptr'] -> jc -> Neo phải khớp mảnh k+1)
                    cond_sandwich = False
                    if not cond_direct and k + 1 < n and next_after_jc is not None and len(txt) <= max(4, len(texts[jc]) + 2):
                        nxt_txt = pieces[k + 1]['norm']
                        s_nxt_after = calc_piece_similarity(nxt_txt, texts[next_after_jc])
                        s_curr_after = calc_piece_similarity(txt, texts[next_after_jc])
                        if (
                            (s_nxt_after >= th_of(nxt_txt) or is_sequential_short_match(nxt_txt, texts[next_after_jc], None))
                            and s_curr_after < s_nxt_after
                        ):
                            cond_sandwich = True
                    # TH3: Mảnh hiện tại gộp cả jc + c_i1 thành 1 khối không có dấu '/'
                    cond_merged_prefix = False
                    if not cond_direct and not cond_sandwich and c_i1 is not None and len(txt) > len(texts[c_i1]):
                        comb_jc = texts[jc] + texts[c_i1]
                        if (
                            _lcs_length(txt, comb_jc) > _lcs_length(txt, texts[c_i1])
                            and calc_piece_similarity(txt, comb_jc) > s_i1 + 0.02
                        ):
                            cond_merged_prefix = True

                    if cond_direct or cond_sandwich or cond_merged_prefix:
                        junk.discard(jc)
                        promoted_jc = True
                if promoted_jc:
                    c_i1, junk_before_i1, c_i2 = first_content_cand(cands)

            # Ca E (Nâng cấp): 1 mảnh LLM bao trùm 2 mẩu ASR nội dung liền kề (c_i1 + c_i2)
            # Đây là hành vi gộp câu đứt ngang hợp lệ 100%, gắn cờ mềm MERGED_CHUNKS_IN_PIECE
            if len(txt) >= 4 and c_i1 is not None and c_i2 is not None:
                between = [order[p] for p in range(pos_of[c_i1] + 1, pos_of[c_i2])]
                if all(b in junk for b in between):
                    combined_asr = texts[c_i1] + texts[c_i2]
                    s_merged = calc_piece_similarity(txt, combined_asr)
                    s_single = max(calc_piece_similarity(txt, texts[c_i1]), calc_piece_similarity(txt, texts[c_i2]))
                    lcs_gain = _lcs_length(txt, combined_asr) - max(_lcs_length(txt, texts[c_i1]), _lcs_length(txt, texts[c_i2]))
                    min_side_len = min(len(texts[c_i1]), len(texts[c_i2]))
                    if (s_merged >= MERGE_TH and s_merged > s_single + MERGE_GAIN) or (
                        s_merged >= 0.85 and lcs_gain >= min(2, min_side_len) and s_merged > s_single + 0.02
                    ):
                        pc['best'], pc['margin'] = s_merged, s_merged - s_single
                        commit(k, [c1 for c1 in (c_i1, c_i2)], junk_before_i1 + between)
                        pc['flags'].append(f'MERGED_CHUNKS_IN_PIECE_[{c_i1},{c_i2}]')
                        telemetry['best_scores'].append(round(s_merged, 3))
                        telemetry['margins'].append(round(s_merged - s_single, 3))
                        k += 1
                        continue

            # Ca F1 (Đối ngẫu Ca E): 2 hoặc 3 mảnh LLM liên tiếp trong cùng 1 câu ghép lại khớp trọn vẹn 1 mẩu ASR (c_i1)
            if c_i1 is not None and k + 1 < n and len(texts[c_i1]) >= 4:
                s_p1_i1 = calc_piece_similarity(txt, texts[c_i1])
                matched_span = 0
                best_span_sc = s_p1_i1
                acc_txt = txt
                for span in (2, 3):
                    if k + span - 1 >= n:
                        break
                    nxt_p = pieces[k + span - 1]
                    if nxt_p['item_idx'] != pc['item_idx']:
                        break
                    nxt_txt = nxt_p['norm']
                    # Chốt chặn: mảnh tiếp theo không được khớp rõ ràng hơn với mẩu ASR kế tiếp (c_i2)
                    if c_i2 is not None and calc_piece_similarity(nxt_txt, texts[c_i2]) >= max(th_of(nxt_txt), 0.68):
                        if nxt_txt not in texts[c_i1]:
                            break
                    acc_txt += nxt_txt
                    if len(acc_txt) > len(texts[c_i1]) + 3:
                        break
                    s_comb = calc_piece_similarity(acc_txt, texts[c_i1])
                    lcs_improve = _lcs_length(acc_txt, texts[c_i1]) - _lcs_length(acc_txt[:-len(nxt_txt)], texts[c_i1])
                    if s_comb >= 0.80 and s_comb > best_span_sc + 0.05 and lcs_improve >= min(2, len(nxt_txt)):
                        best_span_sc = s_comb
                        matched_span = span

                if matched_span >= 2:
                    for idx_s in range(k, k + matched_span):
                        pieces[idx_s]['chunks'] = [c_i1]
                        pieces[idx_s]['best'] = best_span_sc
                        pieces[idx_s]['margin'] = round(best_span_sc - s_p1_i1, 3)
                        pieces[idx_s]['flags'].append(f'SPLIT_PIECE_MERGED_TO_[{c_i1}]')
                        telemetry['best_scores'].append(round(best_span_sc, 3))
                        telemetry['margins'].append(round(best_span_sc - s_p1_i1, 3))
                    assigned.add(c_i1)
                    for jc in junk_before_i1:
                        junk_covered.add(jc)
                    telemetry['junk_skipped_count'] += len(junk_before_i1)
                    telemetry['sandwich_rescued'] += matched_span
                    st['ptr'] = pos_of[c_i1] + 1
                    st['fails'] = 0
                    st['fail_start'] = None
                    k += matched_span
                    continue

            scores = score(txt, cands)
            best_c, best_sc, margin, verdict = judge(txt, scores)
            pc['best'], pc['margin'] = best_sc, margin
            telemetry['best_scores'].append(round(best_sc, 3))
            telemetry['margins'].append(round(margin, 3))
            th = th_of(txt)

            if verdict == 'clear':
                skipped = [c for c in cands if c < best_c]
                content_skipped = [c for c in skipped if c not in junk]
                junk_skipped = [c for c in skipped if c in junk]
                if not content_skipped:
                    commit(k, [best_c], junk_skipped)
                    if best_sc < th + LOW_CONF_BAND:
                        pc['flags'].append(f'PIECE_LOW_CONF_{best_sc:.0%}')
                    if junk_skipped:
                        pc['flags'].append(f'JUNK_SKIPPED_{junk_skipped}')
                    k += 1
                    continue

                if c_i1 is not None and is_sequential_short_match(txt, texts[c_i1], texts[c_i2] if c_i2 else None):
                    if confirm_sandwich_1to1(k, c_i1):
                        commit(k, [c_i1], junk_before_i1)
                        pc['flags'].append(f'SHORT_SEQ_MATCH_[{c_i1}]')
                        telemetry['short_seq_rescued'] += 1
                        k += 1
                        continue

                if confirm(k, pos_of[best_c] + 1):
                    commit(k, [best_c], junk_skipped)
                    telemetry['dropped_by_confirm'].extend(content_skipped)
                    if best_sc < th + LOW_CONF_BAND:
                        pc['flags'].append(f'PIECE_LOW_CONF_{best_sc:.0%}')
                    k += 1
                    continue
                pc['flags'].append(f'AMBIGUOUS_SKIP_{content_skipped}')
                note_fail(k)

            elif verdict == 'tie':
                plausible = sorted(c for c, sc in scores if sc >= th and best_sc - sc < MARGIN_MIN)
                tentative = None
                for c in plausible:
                    if not [x for x in cands if x < c and x not in junk]:
                        tentative = c
                        break
                if tentative is not None:
                    commit(k, [tentative], [x for x in cands if x < tentative])
                    pc['flags'].append('AMBIG_RESOLVED_MONOTONIC')
                    telemetry['ambig_resolved'] += 1
                    k += 1
                    continue
                if c_i1 is not None and is_sequential_short_match(txt, texts[c_i1], texts[c_i2] if c_i2 else None):
                    if confirm_sandwich_1to1(k, c_i1):
                        commit(k, [c_i1], junk_before_i1)
                        pc['flags'].append(f'SHORT_SEQ_MATCH_[{c_i1}]')
                        telemetry['short_seq_rescued'] += 1
                        k += 1
                        continue
                for c in plausible:
                    if confirm(k, pos_of[c] + 1) or confirm_sandwich_1to1(k, c):
                        tentative = c
                        break
                if tentative is not None:
                    skipped_t = [x for x in cands if x < tentative]
                    content_skipped_t = [x for x in skipped_t if x not in junk]
                    junk_skipped_t = [x for x in skipped_t if x in junk]
                    commit(k, [tentative], junk_skipped_t)
                    telemetry['dropped_by_confirm'].extend(content_skipped_t)
                    pc['flags'].append('AMBIG_RESOLVED_BY_CONFIRM')
                    telemetry['ambig_resolved'] += 1
                    k += 1
                    continue
                pc['flags'].append(f'AMBIGUOUS_{txt}')
                note_fail(k)

            else:
                # Ca F2 (Hấp thụ đuôi lùi): Mảnh k-1 vừa khóa c_prev và mảnh k là phần đuôi còn lại của chính c_prev
                if k > 0 and pieces[k - 1]['chunks']:
                    c_prev = pieces[k - 1]['chunks'][-1]
                    prev_norms = ''.join(
                        pieces[m]['norm'] for m in range(max(0, k - 3), k)
                        if c_prev in pieces[m]['chunks']
                    )
                    s_before = calc_piece_similarity(prev_norms, texts[c_prev])
                    s_after = calc_piece_similarity(prev_norms + txt, texts[c_prev])
                    if (txt in texts[c_prev] or s_after >= 0.80) and s_after > s_before + 0.04:
                        pc['chunks'] = [c_prev]
                        pc['best'] = s_after
                        pc['margin'] = round(s_after - s_before, 3)
                        pc['flags'].append(f'TAIL_PIECE_ABSORBED_TO_[{c_prev}]')
                        telemetry['sandwich_rescued'] += 1
                        st['fails'] = 0
                        st['fail_start'] = None
                        k += 1
                        continue

                if c_i1 is not None:
                    asr_i1_txt = texts[c_i1]
                    asr_i2_txt = texts[c_i2] if c_i2 is not None else None

                    # Vũ khí 1: Khớp tuần tự câu ngắn theo thứ tự từ
                    if is_sequential_short_match(txt, asr_i1_txt, asr_i2_txt):
                        commit(k, [c_i1], junk_before_i1)
                        pc['best'] = max(best_sc, 0.75)
                        pc['flags'].append(f'SHORT_SEQ_MATCH_[{c_i1}]')
                        telemetry['short_seq_rescued'] += 1
                        k += 1
                        continue

                    # Vũ khí 1.5: Kẹp bánh mì 1-1 cho câu ngắn bị sửa đồng âm cả cụm (Neo trái st['ptr'] + Neo phải c_i2)
                    if (
                        len(txt) <= SHORT_SEQ_MAX_LEN
                        and len(asr_i1_txt) <= SHORT_SEQ_MAX_LEN
                        and c_i2 is not None
                        and confirm_sandwich_1to1(k, c_i1)
                        and calc_piece_similarity(txt, asr_i2_txt) < calc_piece_similarity(pieces[k + 1]['norm'], asr_i2_txt)
                    ):
                        commit(k, [c_i1], junk_before_i1)
                        pc['best'] = max(best_sc, 0.72)
                        pc['flags'].append(f'SANDWICH_1TO1_SHORT_[{c_i1}]')
                        telemetry['sandwich_rescued'] += 1
                        k += 1
                        continue

                    # Vũ khí 2: Kẹp bánh mì Hộp Kín Đa Tỷ Lệ (1:1 mọi độ dài, 2:1, 3:1, 1:2, 2:2)
                    box_res = confirm_box_sandwich(k, cands)
                    if box_res is not None:
                        m_span, box_chunks, box_junk, box_sim = box_res
                        for idx_s in range(k, k + m_span):
                            pieces[idx_s]['chunks'] = list(box_chunks)
                            pieces[idx_s]['best'] = max(pieces[idx_s].get('best') or 0.0, round(box_sim, 2))
                            pieces[idx_s]['flags'].append(f'SANDWICH_BOX_{m_span}x{len(box_chunks)}_{box_chunks}')
                        for bc in box_chunks:
                            assigned.add(bc)
                        for jc in box_junk:
                            junk_covered.add(jc)
                        telemetry['junk_skipped_count'] += len(box_junk)
                        telemetry['sandwich_rescued'] += m_span
                        st['ptr'] = pos_of[max(box_chunks)] + 1
                        st['fails'] = 0
                        st['fail_start'] = None
                        k += m_span
                        continue

                next_good = False
                for j in range(k + 1, min(n, k + 1 + CONFIRM_LOOKAHEAD)):
                    t2 = pieces[j]['norm']
                    _, _, _, v2 = judge(t2, score(t2, cands))
                    if v2 == 'clear':
                        next_good = True
                        break
                pc['flags'].append(f'PIECE_UNMATCHED_{txt}')
                if not next_good:
                    note_fail(k)

            if st['fails'] >= FAIL_STREAK_MAX:
                action = resync(k)
                if action == 'stop':
                    break
                k = st['k']
                continue
            k += 1

        # ---- gom mảnh thành câu, Auto-heal Internal Gap & Khóa Kẹp Toàn Câu (Item-Level Span Lock) ----
        by_item: Dict[int, List[Dict[str, Any]]] = {}
        for p in pieces:
            by_item.setdefault(p['item_idx'], []).append(p)
            if p['chunks']:
                telemetry['matched_pieces'] += 1

        out: List[Dict[str, Any]] = []
        prev_max_src = 0
        for it_idx, it in enumerate(items):
            pcs = by_item.get(it_idx, [])
            chunks = sorted({c for p in pcs for c in p['chunks']})
            item_flags: List[str] = []
            for p in pcs:
                item_flags.extend(p['flags'])
                if not p['chunks'] and not any(f.startswith(cls.HARD_FLAG_PREFIXES) for f in p['flags']):
                    item_flags.append('PIECE_UNASSIGNED')
            if not pcs:
                item_flags.append('ZH_MISSING')
            if not chunks:
                item_flags.append('NO_SRC')

            # Auto-heal Internal Gap: Tự động hấp thụ mẩu bị lọt khe ở giữa câu gộp
            if len(chunks) >= 2:
                healed_gaps: List[int] = []
                for pos in range(pos_of[chunks[0]], pos_of[chunks[-1]] + 1):
                    c = order[pos]
                    if c not in chunks and c not in assigned:
                        chunks.append(c)
                        assigned.add(c)
                        if c not in junk:
                            healed_gaps.append(c)
                        else:
                            junk_covered.add(c)
                chunks.sort()
                if healed_gaps:
                    telemetry['internal_gaps_healed'] += len(healed_gaps)
                    item_flags.append(f'INTERNAL_GAP_HEALED_{healed_gaps}')

            # Tầng 2 — Khóa Kẹp Toàn Câu (Item-Level Span Lock):
            # Nếu câu đã khóa được dải chunks[0]..chunks[-1] mà bên trong có mảnh lẻ bị PIECE_UNMATCHED/UNASSIGNED
            # do LLM đặt dấu '/' lệch vị trí, kiểm tra tổng chữ Hán toàn câu so với tổng chữ Hán của dải chunks.
            if chunks and any(f.startswith(('PIECE_UNMATCHED', 'PIECE_UNASSIGNED')) for f in item_flags):
                item_zh_norm = ''.join(p['norm'] for p in pcs)
                span_asr_norm = ''.join(texts[c] for c in chunks)
                span_sim = calc_piece_similarity(item_zh_norm, span_asr_norm)
                if span_sim >= 0.80:
                    unmatched_pcs = sum(1 for p in pcs if not p['chunks'])
                    telemetry['matched_pieces'] += unmatched_pcs
                    telemetry['sandwich_rescued'] += max(1, unmatched_pcs)
                    item_flags = [
                        (f'SPAN_LOCKED_{f}' if f.startswith(('PIECE_UNMATCHED', 'PIECE_UNASSIGNED')) else f)
                        for f in item_flags
                    ]
                    item_flags.append(f'ITEM_SPAN_LOCKED_{span_sim:.0%}')

            if chunks:
                if len(chunks) > 1 and chunks[0] == prev_max_src:
                    chunks = chunks[1:]
                elif len(chunks) == 1 and chunks[0] == prev_max_src and out and len(out[-1].get('src') or []) > 1 and out[-1]['src'][-1] == prev_max_src:
                    out[-1]['src'] = out[-1]['src'][:-1]
                    out[-1]['n_t1'] = len(out[-1]['src'])
                if chunks[0] < prev_max_src:
                    item_flags.append('OUT_OF_ORDER')
                prev_max_src = max(prev_max_src, chunks[-1])

            hard = list(dict.fromkeys(f for f in item_flags if f.startswith(cls.HARD_FLAG_PREFIXES)))
            soft = list(dict.fromkeys(f for f in item_flags if f not in hard))

            bests = [p['best'] for p in pcs if p['best'] is not None]
            margins = [p['margin'] for p in pcs if p['margin'] is not None]
            align_ok = (not hard) and bool(chunks)
            out.append({
                **it,
                'src': chunks,
                'junk_covered': [],
                'dropped_chunks': [],
                'match_ratio': round(sum(bests) / len(bests), 2) if bests else 0.0,
                'min_margin': round(min(margins), 2) if margins else 0.0,
                'n_t1': len(chunks),
                'k_t2': len(pcs),
                'align_ok': align_ok,
                'hard_flags': hard,
                'soft_flags': soft,
                '_ok': align_ok,
            })

        # ---- KẸP BÁNH MÌ KHOANH VÙNG LỖI CỤC BỘ CHO CÁC CÂU NẰM GIỮA 2 ĐẦU NEO CHUẨN ----
        cls._apply_sandwich_containment(out, order, pos_of, seg_map, texts, junk, telemetry)

        # ---- Xử lý các mẩu ASR chưa được gán sau khi đã kẹp câu ----
        all_assigned = {c for o in out for c in o['src']}
        unassigned = [c for c in order if c not in all_assigned]
        real_dropped = [c for c in unassigned if c not in junk]
        junk_un = [c for c in unassigned if c in junk]

        with_src = [(o['src'][0], o['src'][-1], o) for o in out if o['src']]
        by_start = sorted(with_src, key=lambda w: w[0])
        starts = [w[0] for w in by_start]
        by_end = sorted(with_src, key=lambda w: w[1])
        ends = [w[1] for w in by_end]

        for j in junk_un:
            i = bisect.bisect_right(ends, j)
            tgt = by_end[i][2] if i < len(by_end) else (by_end[-1][2] if by_end else (out[-1] if out else None))
            if tgt is not None:
                tgt['junk_covered'].append(j)

        # Gom các mẩu real_dropped liên tiếp thành từng cụm (contiguous runs) để xét kẹp bánh mì
        dropped_runs: List[List[int]] = []
        for u in real_dropped:
            if dropped_runs and pos_of[u] == pos_of[dropped_runs[-1][-1]] + 1:
                dropped_runs[-1].append(u)
            else:
                dropped_runs.append([u])

        for run in dropped_runs:
            u_first, u_last = run[0], run[-1]
            i = bisect.bisect_right(starts, u_first)

            # 1. Nằm lọt lòng bên trong 1 câu gộp
            if len(run) <= SANDWICH_SKIP_MAX_CHUNKS and i > 0 and by_start[i - 1][0] < u_first and u_last < by_start[i - 1][1]:
                tgt = by_start[i - 1][2]
                for u_x in run:
                    if u_x not in tgt['src']:
                        tgt['src'].append(u_x)
                tgt['src'].sort()
                tgt['n_t1'] = len(tgt['src'])
                telemetry['internal_gaps_healed'] += len(run)
                tgt['soft_flags'].append(f'INTERNAL_GAP_HEALED_{run}')
                continue

            # 2. Nằm ở biên câu: kiểm tra xem chữ Hán của mẩu đơn lẻ đã được dịch gộp trong câu láng giềng chưa
            if len(run) == 1:
                u_txt = texts.get(u_first, '')
                rescued_boundary = False
                if u_txt:
                    cand_neighbors = []
                    if i > 0 and pos_of[u_first] == pos_of[by_start[i - 1][1]] + 1:
                        cand_neighbors.append(by_start[i - 1][2])
                    if i < len(by_start) and pos_of[by_start[i][0]] == pos_of[u_first] + 1:
                        cand_neighbors.append(by_start[i][2])
                    for nb_item in cand_neighbors:
                        nb_zh = norm_zh(nb_item.get('zh', '') or ''.join(nb_item.get('zh_pieces', [])))
                        if u_txt and (u_txt in nb_zh or _longest_contiguous_match(u_txt, nb_zh) >= max(2, len(u_txt) - 1)):
                            nb_item['src'].append(u_first)
                            nb_item['src'].sort()
                            nb_item['n_t1'] = len(nb_item['src'])
                            telemetry['internal_gaps_healed'] += 1
                            nb_item['soft_flags'].append(f'BOUNDARY_CHUNK_ABSORBED_[{u_first}]')
                            rescued_boundary = True
                            break
                if rescued_boundary:
                    continue

            # 3. KẸP BÁNH MÌ CHO MẨU ASR BỊ LLM LƯỢC BỎ (Sanctioned Sandwich Skip):
            # Nếu cụm mẩu bị bỏ qua nằm kẹp giữa 2 câu có src hợp lệ (left_anchor và right_anchor)
            # và có kích thước nhỏ (<= SANDWICH_SKIP_MAX_CHUNKS, <= SANDWICH_SKIP_MAX_CHARS, <= SANDWICH_SKIP_MAX_DUR),
            # đây là từ đệm/câu cảm thán/câu thừa được LLM chủ động lược bỏ.
            # Khoanh vùng vào junk_covered để giữ timeline an toàn, KHÔNG đánh DROPPED_CHUNK bắt dịch lại!
            run_chars = sum(len(texts.get(x, '')) for x in run)
            run_dur = sum(
                max(0.0, float(seg_map[x].get('end', 0.0) or 0.0) - float(seg_map[x].get('start', 0.0) or 0.0))
                for x in run
            )
            if 0 < i < len(by_start):
                left_item = by_start[i - 1][2]
                right_item = by_start[i][2]
                is_tightly_bounded = (
                    pos_of[u_first] >= pos_of[by_start[i - 1][1]] + 1
                    and pos_of[by_start[i][0]] >= pos_of[u_last] + 1
                )
                if (
                    is_tightly_bounded
                    and cls._is_trusted_anchor_item(left_item)
                    and cls._is_trusted_anchor_item(right_item)
                    and len(run) <= SANDWICH_SKIP_MAX_CHUNKS
                    and run_chars <= SANDWICH_SKIP_MAX_CHARS
                    and run_dur <= SANDWICH_SKIP_MAX_DUR
                ):
                    for x in run:
                        left_item['junk_covered'].append(x)
                        junk_covered.add(x)
                    left_item['soft_flags'].append(f'SANDWICH_SKIPPED_CHUNK_{run}')
                    telemetry['sandwich_rescued'] += len(run)
                    telemetry['junk_skipped_count'] += len(run)
                    continue

            # 3b. Kẹp biên đầu/cuối lô (Intro/Outro ngắn bị LLM lược bỏ <= 10 mẩu)
            if (i == 0 or i == len(by_start)) and by_start and len(run) <= 10 and run_chars <= 150 and run_dur <= 35.0:
                edge_item = by_start[0][2] if i == 0 else by_start[-1][2]
                if cls._is_trusted_anchor_item(edge_item):
                    for x in run:
                        edge_item['junk_covered'].append(x)
                        junk_covered.add(x)
                    edge_item['soft_flags'].append(f'BOUNDARY_SKIPPED_CHUNK_{run}')
                    telemetry['sandwich_rescued'] += len(run)
                    telemetry['junk_skipped_count'] += len(run)
                    continue

            # 3c. Khe nhỏ lọt giữa 2 câu (khe <= 4 mẩu, <= 12s): Tự động khoanh vùng vào junk_covered thay vì đánh lỗi cứng
            if len(run) <= 4 and run_dur <= 12.0:
                tgt_absorb = by_start[i - 1][2] if i > 0 else (by_start[0][2] if by_start else (out[-1] if out else None))
                if tgt_absorb is not None:
                    for x in run:
                        tgt_absorb['junk_covered'].append(x)
                        junk_covered.add(x)
                    tgt_absorb['soft_flags'].append(f'SANDWICH_GAP_ABSORBED_{run}')
                    telemetry['sandwich_rescued'] += len(run)
                    telemetry['junk_skipped_count'] += len(run)
                    continue

            # 4. Thực sự mất đoạn nội dung dài (Thủng lưới lớn / Không có neo kẹp) -> Bắt buộc đánh DROPPED_CHUNK để dịch lại
            tgt = by_start[i][2] if i < len(by_start) else (by_start[-1][2] if by_start else (out[-1] if out else None))
            if tgt is None:
                continue
            for x in run:
                tgt['dropped_chunks'].append(x)
                flag = f'DROPPED_CHUNK_[{x}]'
                if flag not in tgt['hard_flags']:
                    tgt['hard_flags'].append(flag)
                telemetry['flags']['DROPPED_CHUNK'] += 1
            tgt['align_ok'] = False
            tgt['_ok'] = False

        # Thống kê lại telemetry flags từ toàn bộ các câu sau khi đã qua bước kẹp bánh mì
        for o in out:
            for f in o.get('hard_flags', []) + o.get('soft_flags', []):
                if f.startswith('DROPPED_CHUNK'):
                    continue  # đã cộng ở trên
                prefix_key = f.split('_[')[0].split('_')[0]
                if f.startswith(('PIECE_', 'MERGED_', 'SHORT_', 'SANDWICH_', 'INTERNAL_', 'ITEM_')):
                    parts = f.split('_')
                    if len(parts) > 1:
                        prefix_key += '_' + parts[1]
                telemetry['flags'][prefix_key] += 1

        # Cập nhật lại tập đã gán sau khi lấp khe & kẹp bánh mì
        all_assigned = {c for o in out for c in o['src']}
        all_junk_cov = junk_covered | set(junk_un) | {c for o in out for c in o.get('junk_covered', [])}
        unassigned = [c for c in order if c not in all_assigned and c not in all_junk_cov]
        real_dropped = [c for c in unassigned if c not in junk]

        telemetry['unassigned_all'] = sorted(unassigned)
        telemetry['unassigned_real_dropped'] = real_dropped
        telemetry['junk_covered'] = sorted(all_junk_cov)
        telemetry['total_items'] = len(out)
        telemetry['total_segs'] = len(order)
        return out, telemetry

    @classmethod
    def align_zh_to_asr(cls, items, segs, slack: int = 6, drop_tol: int = 2):
        """Wrapper tương thích ngược."""
        aligned, tele = cls.align_sequential(items, segs)
        return aligned, len(tele.get('unassigned_all', []))

    @classmethod
    def align_one_to_one(cls, chunk_ids: List[int], lines: List[Dict[str, Any]],
                         orig_segs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Cấp chữa 1 mẩu = 1 câu. Mỗi dòng PHẢI mang chữ Hán 'zh' của mẩu để đối chiếu (không tin vị trí dòng)."""
        seg_map = {s.get('index', i + 1): s for i, s in enumerate(orig_segs)}
        texts = {c: _seg_text(seg_map[c]) for c in chunk_ids if c in seg_map}
        out: List[Dict[str, Any]] = []
        if len(lines) != len(chunk_ids):
            for ln in lines:
                it = dict(ln)
                it.update(src=[], hard_flags=['ONE2ONE_COUNT_MISMATCH'], soft_flags=[], _ok=False, align_ok=False)
                out.append(it)
            return out
        for i, (cid, ln) in enumerate(zip(chunk_ids, lines)):
            zh = norm_zh(ln.get('zh', ''))
            own = calc_piece_similarity(zh, texts.get(cid, ''))
            nb = 0.0
            for j in (i - 1, i + 1):
                if 0 <= j < len(chunk_ids):
                    nb = max(nb, calc_piece_similarity(zh, texts.get(chunk_ids[j], '')))
            th = TH_SHORT if len(zh) <= SHORT_LEN else TH_NORMAL
            flags: List[str] = []
            if cid not in seg_map:
                flags.append('ONE2ONE_BAD_CHUNK')
            if own < th and not is_sequential_short_match(zh, texts.get(cid, '')):
                flags.append(f'ONE2ONE_LOW_MATCH_{own:.0%}')
            if nb > own + 0.15:
                flags.append('ONE2ONE_SHIFTED')
            it = dict(ln)
            it.update(src=[cid] if cid in seg_map else [], hard_flags=flags, soft_flags=[],
                      _ok=not flags, align_ok=not flags, match_ratio=round(own, 2))
            out.append(it)
        return out

    # =========================================================================
    # KHỐI 2: KHOẢNG LẶNG CỨNG / [KHÓA] — chỉ gắn cờ mềm (soft_flags), không ép retry
    # =========================================================================
    @classmethod
    def flag_gap_collisions(cls, items: List[Dict[str, Any]], seg_map: Dict[int, Dict[str, Any]]):
        order = sorted(seg_map.keys())
        pos_of = {c: i for i, c in enumerate(order)}
        for it in items:
            src = it.get('src') or []
            if len(src) < 2 or src[0] not in pos_of or src[-1] not in pos_of:
                continue
            for pos in range(pos_of[src[0]], pos_of[src[-1]]):
                a, b = order[pos], order[pos + 1]
                gap = float(seg_map[b].get('start', 0)) - float(seg_map[a].get('end', 0))
                if gap >= HARD_GAP:
                    flag = f'MERGE_ACROSS_KHOA_GAP_{a}-{b}_{gap:.2f}s'
                    it['soft_flags'] = list(it.get('soft_flags', [])) + [flag]
                    break

    # =========================================================================
    # KHỐI 2.5: CẮT TUẦN TỰ CÂU GỘP BĂNG QUA KHOẢNG LẶNG (GAP SPLIT - SEQUENTIAL ANCHOR)
    # =========================================================================
    @staticmethod
    def _gap_split_seg_zh(seg: Dict[str, Any]) -> str:
        return str(seg.get('zh') or seg.get('clean_text') or seg.get('text') or '').strip()

    @classmethod
    def _find_gap_cut_text(cls, text: str, left_ids: List[int], right_ids: List[int],
                           seg_map: Dict[int, Dict[str, Any]]) -> Optional[Tuple[str, str]]:
        """CHỈ dùng để TÌM VỊ TRÍ cắt chữ tiếng Việt giữa nhóm mẩu trái / nhóm mẩu phải.
        DÙNG CHUNG toàn bộ dấu ngắt (kết câu .?!… và giữa câu ,;:—) trên trục tích lũy số từ:
        - Neo theo dung lượng chữ Hán gốc cụm trái (ZH_L).
        - Điểm cắt chuẩn là dấu câu nằm sát dung lượng kỳ vọng nhất (có cộng điểm ưu tiên cho dấu kết câu .?!…).
        - Cấm chẻ đôi từ hoặc số (3.5, 10:30, 1,000). Không tìm được ranh giới an toàn -> trả None (không cắt)."""
        text = str(text or '').strip()
        if not text or not left_ids or not right_ids:
            return None

        words = text.split()
        total_words = len(words)
        if total_words < 2:
            return None

        zh_l = "".join(cls._gap_split_seg_zh(seg_map[i]) for i in left_ids)
        zh_r = "".join(cls._gap_split_seg_zh(seg_map[i]) for i in right_ids)
        zl = len(norm_zh(zh_l))
        zr = len(norm_zh(zh_r))
        if zl + zr == 0:
            return None

        expected_wl = total_words * (zl / (zl + zr))

        punct_pattern = re.compile(r'([.!?…;:,，；：—–]+)')
        cands = []
        for m in punct_pattern.finditer(text):
            en_pos = m.end()
            st_pos = m.start()
            punct = m.group()

            # Bỏ qua các cấu trúc số: 3.5, 10:30, 1,000
            if st_pos > 0 and en_pos < len(text) and text[st_pos - 1].isdigit() and text[en_pos].isdigit():
                continue

            left = text[:en_pos].strip()
            right = text[en_pos:].strip()

            if not left or not right:
                continue
            if not any(c.isalnum() for c in left) or not any(c.isalnum() for c in right):
                continue

            wl = len(left.split())
            is_end = bool(re.search(r'[.!?…]', punct))
            word_diff = abs(wl - expected_wl)

            # Điểm phạt: càng sát số từ kỳ vọng càng tốt.
            # Dấu kết câu (.!?) được ưu tiên tự nhiên (tương đương dung sai ~3.5 từ so với dấu phẩy)
            score = word_diff
            if is_end:
                score -= 3.5

            cands.append({
                'left': left,
                'right': right,
                'diff': word_diff,
                'score': score
            })

        if not cands:
            return None

        cands.sort(key=lambda x: x['score'])
        best = cands[0]
        return best['left'], best['right']

    @classmethod
    def split_items_at_hard_gaps(cls, items: List[Dict[str, Any]], seg_map: Dict[int, Dict[str, Any]],
                                 threshold: float = GAP_SPLIT_TH) -> Tuple[List[Dict[str, Any]], int, List[str]]:
        """Nhánh IF cắt câu gộp băng qua khoảng lặng >= threshold.
        - Câu không có khoảng lặng nội bộ >= threshold, câu mang lỗi cứng, câu thiếu src: GIỮ NGUYÊN 100%.
        - Câu thỏa điều kiện: cắt TUẦN TỰ từng điểm gap từ trái sang phải; phần còn lại làm neo cho lần cắt kế.
        - Không tìm được ranh giới dấu câu an toàn: dừng cắt, giữ phần còn lại nguyên vẹn + cờ mềm GAP_SPLIT_SKIPPED."""
        out: List[Dict[str, Any]] = []
        logs: List[str] = []
        split_cnt = 0

        for it in items:
            src = [s for s in (it.get('src') or []) if s in seg_map]
            if len(src) < 2 or it.get('hard_flags') or not it.get('_ok', True):
                out.append(it)
                continue

            cut_positions = []
            for j in range(len(src) - 1):
                gap = float(seg_map[src[j + 1]].get('start', 0.0) or 0.0) - float(seg_map[src[j]].get('end', 0.0) or 0.0)
                if gap >= threshold:
                    cut_positions.append((j, round(gap, 2)))
            if not cut_positions:
                out.append(it)
                continue

            pieces_out: List[Dict[str, Any]] = []
            ptr = 0
            remain_txt = str(it.get('vi', '') or '').strip()
            skipped_flag = None

            for j, gap in cut_positions:
                left_ids, right_ids = src[ptr:j + 1], src[j + 1:]
                res = cls._find_gap_cut_text(remain_txt, left_ids, right_ids, seg_map)
                if not res:
                    skipped_flag = f'GAP_SPLIT_SKIPPED_{src[j]}-{src[j + 1]}_{gap:.2f}s'
                    break
                l_txt, r_txt = res
                pieces_out.append({'src': left_ids, 'vi': l_txt, 'gap_flag': f'GAP_SPLIT_{src[j]}-{src[j + 1]}_{gap:.2f}s'})
                ptr = j + 1
                remain_txt = r_txt

            if not pieces_out:
                if skipped_flag:
                    it['soft_flags'] = list(it.get('soft_flags', [])) + [skipped_flag]
                out.append(it)
                continue

            pieces_out.append({'src': src[ptr:], 'vi': remain_txt, 'gap_flag': skipped_flag})

            junk_cov = list(it.get('junk_covered') or [])
            dropped = list(it.get('dropped_chunks') or [])
            for p_idx, p in enumerate(pieces_out):
                p_src = p['src']
                lo = p_src[0] if p_idx > 0 else float('-inf')
                hi = pieces_out[p_idx + 1]['src'][0] if p_idx + 1 < len(pieces_out) else float('inf')
                child = dict(it)
                child['src'] = list(p_src)
                child['vi'] = p['vi']
                child['zh_pieces'] = [cls._gap_split_seg_zh(seg_map[i]) for i in p_src]
                child['zh'] = ''.join(child['zh_pieces'])
                child['zh_raw'] = '/'.join(child['zh_pieces'])
                child['junk_covered'] = [c for c in junk_cov if lo <= c < hi]
                child['dropped_chunks'] = [c for c in dropped if lo <= c < hi]
                child['hard_flags'] = list(it.get('hard_flags') or [])
                child['soft_flags'] = list(it.get('soft_flags') or []) + ([p['gap_flag']] if p['gap_flag'] else [])
                out.append(child)

            split_cnt += len(pieces_out) - 1
            logs.append(f"  ✔ [GapSplit] Cắt câu src={src} thành {len(pieces_out)} câu: "
                        + " | ".join(f"{p['src']}: {p['vi']}" for p in pieces_out))

        return out, split_cnt, logs

    # =========================================================================
    # KHỐI 3: AUDIT & CHỐT CHẶN CỰC ĐOAN (>= 2.0x)
    # =========================================================================
    @classmethod
    def audit_soft_signals(cls, items: List[Dict[str, Any]], seg_map: Dict[int, Dict[str, Any]],
                           target_wps: float = DEFAULT_TARGET_WPS) -> List[Dict[str, Any]]:
        audited = []
        for it in items:
            soft_flags = list(it.get('soft_flags', []))
            hard_flags = list(it.get('hard_flags', []))
            src = it.get('src', [])
            vi = str(it.get('vi', '')).strip()
            wc_vi = _wc(vi)

            if not src:
                if 'NO_SRC' not in hard_flags:
                    hard_flags.append('NO_SRC')
                it['hard_flags'] = hard_flags
                it['soft_flags'] = soft_flags
                it['_ok'] = False
                audited.append(it)
                continue
            segs = [seg_map[s] for s in src if s in seg_map]
            if not segs:
                hard_flags.append('INVALID_SRC')
                it['hard_flags'] = hard_flags
                it['soft_flags'] = soft_flags
                it['_ok'] = False
                audited.append(it)
                continue

            dur = max(0.1, float(it.get('e', segs[-1].get('end', 0))) - float(it.get('s', segs[0].get('start', 0))))
            wps = wc_vi / dur
            speedup = (wc_vi / target_wps) / dur

            if speedup > SPEEDUP_MAX:
                soft_flags.append(f"TTS_FAST_{speedup:.2f}x")

            is_hard = len(hard_flags) > 0
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
    # TIỆN ÍCH TỰ CỨU CŨ (giữ để tương thích)
    # =========================================================================
    @staticmethod
    def find_big_gap(src: List[int], seg_map: Dict[int, Dict[str, Any]], threshold: float = HARD_GAP) -> Optional[int]:
        for j in range(len(src) - 1):
            a, b = src[j], src[j + 1]
            if a in seg_map and b in seg_map:
                if float(seg_map[b].get('start', 0)) - float(seg_map[a].get('end', 0)) >= threshold:
                    return a
        return None

    @classmethod
    def try_rescue_split(cls, text, src_left, src_right, seg_map, target_wps: float = DEFAULT_TARGET_WPS):
        if not text or not src_left or not src_right:
            return None
        segs_l = [seg_map[s] for s in src_left if s in seg_map]
        segs_r = [seg_map[s] for s in src_right if s in seg_map]
        if not segs_l or not segs_r:
            return None
        dur_l = max(0.1, float(segs_l[-1].get('end', 0)) - float(segs_l[0].get('start', 0)))
        dur_r = max(0.1, float(segs_r[-1].get('end', 0)) - float(segs_r[0].get('start', 0)))
        w_tot = _wc(text)
        ideal_wl = int(round(w_tot * (dur_l / (dur_l + dur_r))))
        clamped = max(2.6, min(3.8, target_wps))
        cands = []
        for m in re.finditer(r'([,;:\.!?…—]+|["”\'])', text):
            st_pos, en_pos, punct = m.start(), m.end(), m.group()
            if punct in ('.', ',') and st_pos > 0 and en_pos < len(text):
                if text[st_pos - 1].isdigit() and text[en_pos].isdigit():
                    continue
            left = text[:en_pos].strip()
            right = re.sub(r'^[,\.\s"”\'—\-]+', '', text[en_pos:]).strip()
            wl, wr = _wc(left), _wc(right)
            if wl < 2 or wr < 2:
                continue
            wps_l, wps_r = wl / dur_l, wr / dur_r
            score = 10.0 - abs(wps_l - clamped) * 1.5 - abs(wps_r - clamped) * 1.5 - abs(wl - ideal_wl) * 0.4
            cands.append({"left": left, "right": right, "score": score, "wps_l": wps_l, "wps_r": wps_r})
        if cands:
            cands.sort(key=lambda x: x["score"], reverse=True)
            b = cands[0]
            if b["score"] >= 4.0 and WPS_SAFE_MIN <= b["wps_l"] <= WPS_SAFE_MAX and WPS_SAFE_MIN <= b["wps_r"] <= WPS_SAFE_MAX:
                return b["left"], b["right"]
        return None

    # =========================================================================
    # KHỐI 4: CỬA SỔ RETRY CỤC BỘ, CÁCH LY, CỔNG ĐÓNG KÍN
    # =========================================================================
    @staticmethod
    def build_retry_windows(items: List[Dict[str, Any]], segs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Gom các câu lỗi liền kề, mở rộng 1 câu sạch mỗi biên, GỘP các cửa sổ chồng nhau."""
        N = len(items)
        seg_idx = sorted(s.get('index', i + 1) for i, s in enumerate(segs))
        bad = [i for i, it in enumerate(items) if not it.get('_ok', True)]
        runs: List[List[int]] = []
        for i in bad:
            if runs and i == runs[-1][-1] + 1:
                runs[-1].append(i)
            else:
                runs.append([i])
        intervals: List[List[Any]] = []
        for r in runs:
            lo, hi = max(0, r[0] - 1), min(N - 1, r[-1] + 1)
            if intervals and lo <= intervals[-1][1]:
                intervals[-1][1] = max(intervals[-1][1], hi)
                intervals[-1][2].extend(r)
            else:
                intervals.append([lo, hi, list(r)])
        windows = []
        for lo, hi, errs in intervals:
            win_items = items[lo:hi + 1]
            chunks_all: List[int] = []
            reasons: List[str] = []
            for it in win_items:
                chunks_all.extend(it.get('src') or [])
                chunks_all.extend(it.get('dropped_chunks') or [])
                reasons.extend(it.get('hard_flags', []))
                reasons.extend(it.get('soft_flags', []))
            if chunks_all:
                src_range = (min(chunks_all), max(chunks_all))
            else:
                src_range = (seg_idx[0], seg_idx[-1]) if seg_idx else (0, 0)
            windows.append({
                'window_index': len(windows) + 1,
                'item_indices': list(range(lo, hi + 1)),
                'error_item_indices': sorted(set(errs)),
                'src_range': src_range,
                'reasons': sorted(set(reasons)),
                'retry_count': 0,
                'needs_review': False,
            })
        return windows

    @staticmethod
    def apply_quarantine(items: List[Dict[str, Any]], windows: List[Dict[str, Any]], max_retries: int = 2):
        """Cửa sổ đã retry >= max_retries mà vẫn lỗi -> cách ly (needs_review). Phía gọi tự tăng retry_count."""
        for w in windows:
            if w.get('retry_count', 0) >= max_retries:
                w['needs_review'] = True
                for idx in w.get('error_item_indices', []):
                    if 0 <= idx < len(items):
                        items[idx]['needs_review'] = True
                        items[idx]['is_quarantined'] = True
        return items, windows

    @classmethod
    def closed_gate(cls, items: List[Dict[str, Any]], orig_segments: List[Dict[str, Any]],
                    max_quarantine_ratio: float = 0.015, max_cluster: int = 3) -> Dict[str, Any]:
        """Cổng đóng kín: chỉ câu 'allowed' mới được vào TTS. Mọi ngoại lệ = KHÔNG đạt (fail-closed)."""
        try:
            inv = cls.verify_3_invariants(items, orig_segments)
            quarantined, allowed = [], []
            for i, it in enumerate(items):
                reasons: List[str] = []
                if not it.get('_ok', True):
                    reasons.append('NOT_OK')
                reasons.extend(it.get('hard_flags') or [])
                if not it.get('src'):
                    reasons.append('NO_SRC')
                if it.get('needs_review') or it.get('is_quarantined'):
                    reasons.append('NEEDS_REVIEW')
                if reasons:
                    quarantined.append({'index': i, 'reasons': list(dict.fromkeys(reasons))[:6]})
                else:
                    allowed.append(i)
            total = len(items)
            qset = {q['index'] for q in quarantined}
            longest = cur = 0
            for i in range(total):
                cur = cur + 1 if i in qset else 0
                longest = max(longest, cur)
            ratio = (len(quarantined) / total) if total else 1.0
            stop, reason = False, ''
            if total == 0:
                stop, reason = True, 'EMPTY'
            elif inv['structural_errors']:
                stop, reason = True, 'STRUCTURAL_INVARIANT: ' + '; '.join(inv['structural_errors'][:3])
            elif ratio > max_quarantine_ratio:
                stop, reason = True, f'QUARANTINE_RATIO_{ratio:.1%}>{max_quarantine_ratio:.1%}'
            elif longest >= max_cluster:
                stop, reason = True, f'DRIFT_CLUSTER_{longest}'
            return {
                'allowed': allowed,
                'quarantined': quarantined,
                'ratio': ratio,
                'longest_cluster': longest,
                'stop': stop,
                'stop_reason': reason,
                'invariants': inv
            }
        except Exception as e:  # fail-closed
            logger.exception("closed_gate lỗi")
            return {
                'allowed': [],
                'quarantined': [],
                'ratio': 1.0,
                'longest_cluster': 0,
                'stop': True,
                'stop_reason': f'EXCEPTION: {e!r}',
                'invariants': {}
            }

    # =========================================================================
    # TIỆN ÍCH CHUẨN HÓA VÀ TÁI TẠO MỐC THỜI GIAN
    # =========================================================================
    @staticmethod
    def _norm_src(raw: Any, seg_map: Dict[int, Dict[str, Any]]) -> List[int]:
        clean = []
        for x in (raw or []):
            try:
                v = int(float(str(x).strip()))
                if v in seg_map:
                    clean.append(v)
            except Exception:
                pass
        return sorted(set(clean))

    @staticmethod
    def _norm_role(role: Any) -> str:
        r = str(role or '').strip()
        if not r or r.upper() in ["NARRATOR", "DẪN CHUYỆN", "DAN CHUYEN", "DẪN", "KỂ", "KỂ CHUYỆN", "NGƯỜI KỂ", "THUYẾT MINH"]:
            return "DẪN CHUYỆN"
        return r

    @staticmethod
    def _rebuild(items: List[Dict[str, Any]], segs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Mốc thời gian: s, e, e_content (chuẩn xác tuyệt đối từ ASR gốc),
        e_max (trần an toàn = đầu mẩu ASR kế tiếp thật - đệm),
        e_max_ext (nới qua mẩu rác thuần túy, có giới hạn chặt chẽ <= 1.0s).
        Phân chia thời lượng theo tỷ lệ chữ Hán khi ASR bị cắt dính 2 câu đối thoại (Shared Chunk)."""
        seg_map = {s.get("index", i + 1): s for i, s in enumerate(segs)}
        order = sorted(seg_map.keys())
        pos_of = {c: i for i, c in enumerate(order)}
        junk = {c for c in order if is_junk_segment(seg_map[c])}
        assigned_all = {s for it in items for s in (it.get('src') or []) if s in seg_map}

        # Chống lỗi khi ASR bị cắt dính 2 câu thoại vào 1 mẩu ASR (Shared Chunk Between Dialogue Turns):
        shared_groups: Dict[int, List[int]] = {}
        for idx, it in enumerate(items):
            src = it.get('src') or []
            if len(src) == 1:
                shared_groups.setdefault(src[0], []).append(idx)

        shared_splits: Dict[int, Tuple[float, float]] = {}
        for cid, item_indices in shared_groups.items():
            if len(item_indices) > 1 and cid in seg_map:
                seg_s = round(float(seg_map[cid].get('start', 0.0) or 0.0), 2)
                seg_e = round(float(seg_map[cid].get('end', 0.0) or 0.0), 2)
                seg_dur = max(0.2, seg_e - seg_s)

                weights = []
                for idx in item_indices:
                    zh_len = len(norm_zh(items[idx].get('zh', '')))
                    if zh_len == 0:
                        zh_len = max(1, len(str(items[idx].get('vi', '')).split()))
                    weights.append(zh_len)
                total_w = sum(weights) or len(weights)

                cur_s = seg_s
                for i_pos, idx in enumerate(item_indices):
                    w_ratio = weights[i_pos] / total_w
                    item_dur = max(0.1, round(seg_dur * w_ratio, 2))
                    cur_e = seg_e if i_pos == len(item_indices) - 1 else round(cur_s + item_dur, 2)
                    shared_splits[idx] = (cur_s, cur_e)
                    cur_s = cur_e

        prev_e: Optional[float] = None
        for idx, it in enumerate(items):
            it['role'] = SmartTimelineHealer._norm_role(it.get('role'))
            src = sorted({s for s in (it.get('src') or []) if s in seg_map})
            it['src'] = src
            if not src:
                it['timing_valid'] = False
                continue

            if idx in shared_splits:
                s_val, e_val = shared_splits[idx]
            else:
                # Mốc thực tế từ ASR kèm kẹp đơn điệu (Monotonic Clamp) khi ASR gốc bị đè mốc thời gian
                s_val = round(float(seg_map[src[0]].get('start', 0.0) or 0.0), 2)
                e_val = round(float(seg_map[src[-1]].get('end', 0.0) or 0.0), 2)

            if prev_e is not None and s_val < prev_e:
                s_val = prev_e
            if e_val < s_val:
                e_val = s_val
            it['s'] = s_val
            it['e'] = e_val
            it['e_content'] = e_val
            it['timing_valid'] = True
            prev_e = e_val

            last_pos = pos_of[src[-1]]
            if last_pos + 1 < len(order):
                next_start = float(seg_map[order[last_pos + 1]].get('start', 0.0))
                if next_start - e_val >= HARD_GAP:
                    e_max = min(e_val + TAIL_CAP, next_start - BUFFER)
                else:
                    e_max = next_start - BUFFER
            else:
                e_max = e_val + TAIL_CAP
            it['e_max'] = round(max(e_val, e_max), 2)

            limit = e_val
            cur_end = e_val
            i = last_pos + 1
            reached_end = True
            while i < len(order):
                c = order[i]
                ch_start = float(seg_map[c].get('start', 0.0))
                if ch_start - cur_end >= HARD_GAP:
                    limit = min(cur_end + TAIL_CAP, ch_start - BUFFER)
                    reached_end = False
                    break
                if c not in junk or c in assigned_all:
                    limit = ch_start - BUFFER
                    reached_end = False
                    break
                cur_end = float(seg_map[c].get('end', cur_end))
                i += 1
            if reached_end:
                limit = cur_end + TAIL_CAP
            limit = min(limit, e_val + JUNK_EXTEND_CAP)
            it['e_max_ext'] = round(max(it['e_max'], limit), 2)

            it['orig_count'] = len(src)
            it['src_start_index'] = src[0]
            it['src_end_index'] = src[-1]
            it['local_src'] = src
        return items

    @classmethod
    def verify_3_invariants(cls, items: List[Dict[str, Any]], orig_segments: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Bất biến. structural_errors = lỗi cấu trúc (phủ lặng, trùng, thứ tự, trần, chồng thời gian);
        quality_errors = câu còn cờ cứng / src rỗng. 'errors' = cả hai (tương thích bản cũ).
        NÂNG CẤP v5.3: Kẹp Bánh Mì Sandwich (Sanctioned Gaps) cho mẩu ASR ngắn/rác lọt giữa 2 mẩu hợp lệ."""
        N = len(orig_segments)
        seg_map = {s.get("index", i + 1): s for i, s in enumerate(orig_segments)}
        order = sorted(seg_map.keys())
        pos_of = {c: i for i, c in enumerate(order)}
        valid = set(order)

        all_src_list = [x for it in items for x in (it.get('src') or []) if x in valid]
        assigned_set = set(all_src_list)
        junk_cov: Set[int] = set()
        flagged_dropped: Set[int] = set()
        for it in items:
            junk_cov.update(it.get('junk_covered', []))
            flagged_dropped.update(it.get('dropped_chunks', []))

        covered = assigned_set | junk_cov
        missing = sorted(valid - covered)
        real_missing = [m for m in missing if not is_junk_segment(seg_map[m])]

        # Phân loại mẩu missing: Nếu được Kẹp Bánh Mì an toàn giữa 2 mẩu hợp lệ (Sandwich Clamping)
        # và là mẩu ngắn (<= 3.5s hoặc text ngắn <= 10 chữ), đây là mẩu rác/nghỉ hợp lệ (Sanctioned Gap)
        silent_missing_strict: List[int] = []
        for m in real_missing:
            if m in flagged_dropped:
                continue
            p = pos_of.get(m, -1)
            is_sandwiched = False
            if p > 0 and p + 1 < len(order):
                left_c = order[p - 1]
                right_c = order[p + 1]
                if (left_c in assigned_set or left_c in junk_cov) and (right_c in assigned_set or right_c in junk_cov):
                    seg_m = seg_map.get(m, {})
                    dur_m = float(seg_m.get('end', 0.0) or 0.0) - float(seg_m.get('start', 0.0) or 0.0)
                    txt_m = _seg_text(seg_m)
                    if dur_m <= SANDWICH_SKIP_MAX_DUR or len(txt_m) <= SANDWICH_SKIP_MAX_CHARS:
                        is_sandwiched = True

            if not is_sandwiched:
                silent_missing_strict.append(m)

        structural: List[str] = []
        quality: List[str] = []
        if silent_missing_strict:
            structural.append(f"Bỏ sót LẶNG {len(silent_missing_strict)} mẩu nội dung (không có cờ): {silent_missing_strict[:10]}")

        # Chồng lấn SRC: Chỉ tính nếu khác câu kịch bản VÀ không phải là trường hợp tách câu đối thoại (Shared Chunk)
        counts = Counter(all_src_list)
        dups = [k for k, v in counts.items() if v > 1]
        real_inter_item_dups = []
        if dups:
            for d in dups:
                claiming_items = [idx for idx, it in enumerate(items) if d in (it.get('src') or [])]
                if len(claiming_items) > 1:
                    is_consecutive_shared = (
                        all(len(items[idx].get('src') or []) == 1 for idx in claiming_items)
                        and claiming_items == list(range(claiming_items[0], claiming_items[0] + len(claiming_items)))
                    )
                    if not is_consecutive_shared:
                        real_inter_item_dups.append(d)
        if real_inter_item_dups:
            structural.append(f"Chồng lấn SRC {len(real_inter_item_dups)} mẩu giữa các câu: {real_inter_item_dups[:10]}")

        prev_max = 0
        for it in items:
            s = it.get('src', [])
            if s:
                if s[0] < prev_max:
                    structural.append(f"Lệch thứ tự: {s} đi sau {prev_max}")
                prev_max = max(prev_max, s[-1])

        prev_e = None
        for idx, it in enumerate(items):
            if not it.get('timing_valid', bool(it.get('src'))):
                continue
            e_c = it.get('e_content', it.get('e', 0.0))
            e_m = it.get('e_max', e_c)
            if e_c > e_m + 0.01:
                structural.append(f"Câu #{idx + 1} tràn trần: e_content={e_c} > e_max={e_m}")
            if prev_e is not None and it.get('s', 0.0) + 0.01 < prev_e:
                structural.append(f"Câu #{idx + 1} chồng thời gian với câu trước")
            prev_e = e_c

        empty_src = [i + 1 for i, it in enumerate(items) if not it.get('src')]
        if empty_src:
            quality.append(f"{len(empty_src)} câu rỗng src: #{empty_src[:10]}")

        # Failed: Chỉ tính câu lỗi cứng thật sự
        failed = [i + 1 for i, it in enumerate(items)
                  if not it.get('_ok', True) or it.get('hard_flags') or it.get('is_quarantined')]
        if failed:
            quality.append(f"{len(failed)} câu lỗi HARD / cách ly: #{failed[:10]}")

        errs = structural + quality
        timeline_passed = (len(structural) == 0) and (len(empty_src) == 0) and (len(failed) == 0)
        return {
            'passed': timeline_passed,
            'errors': errs,
            'structural_errors': structural,
            'quality_errors': quality,
            'covered_orig': len(assigned_set),
            'covered_total': len(covered),
            'missing_indices': real_missing,
            'silent_missing_indices': silent_missing_strict,
            'flagged_dropped_indices': sorted(flagged_dropped),
            'empty_src_items': empty_src,
            'failed_items': failed,
            'total_chunks': len(items),
            'total_orig': N,
        }

    @classmethod
    def enforce_sequential_asr_locking(cls, healed_items, batch, orig_starts, orig_ends):
        segs = [{'index': i + 1, 'start': orig_starts[i], 'end': orig_ends[i]} for i in range(len(orig_starts))]
        return cls._rebuild(healed_items, segs)
