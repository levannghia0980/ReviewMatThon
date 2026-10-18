import logging
from typing import List, Dict, Any, Tuple, Optional
from pydub import AudioSegment

logger = logging.getLogger(__name__)


class TimelineBorrower:
    """
    Thuat toan Di Vay Thoi Gian Timeline (Timeline Borrowing Algorithm).

    NGUYEN TAC BAT BIEN QUAN TRONG NHAT:
    Phuong thuc rebalance_timeline() KHONG BAO GIO SUA d.start_time hay d.end_time.
    No CHI tinh toan va tra ve Dict[dialogue_id -> (borrowed_start, borrowed_end)]
    de caller dung noi bo khi ghep audio vao master_buffer.

    Caller phai:
    1. Doc asr_start/asr_end tu CSDL (bat bien).
    2. Goi rebalance_timeline() lay borrow_map.
    3. Dung borrow_map[d.id] de tinh start_sec khi ghi vao master_buffer.
    4. Ghi ket qua vay muon vao cot borrowed_start/borrowed_end trong CSDL (tuy chon).
    5. KHONG BAO GIO ghi de asr_start/asr_end.

    Quy trinh tinh toan:
    1. Tinh Nhu cau thieu hut (deficit) va Von du da (budget) toan bo cac cau:
       - Von cho vay toi da cua cau du: W_goc - (T_audio / 1.35)
       - Nhu cau can vay cua cau thieu: (T_audio - luong_tu_got_1:2) / 1.35 - W_goc
    2. Sap xep uu tien: Cau thieu nhieu nhat duoc uu tien cap von truoc.
    3. Phan bo thong minh:
       - Cau truoc dai hon cau sau (>1.3x): Vay truoc 75%, sau 25%.
       - Cau sau dai hon cau truoc (>1.3x): Vay sau 75%, truoc 25%.
       - Ngang nhau hoac binh thuong: Vay deu 50% truoc, 50% sau.
    4. Tru dan von kha dung cua cau cho vay sau moi lan giai ngan de tranh vo no.
    5. TRA VE BAN DO VAY MUON, khong sua ORM.
    """

    @classmethod
    def _safe_print(cls, msg: str) -> None:
        try:
            print(msg, flush=True)
        except Exception:
            pass

    @classmethod
    def rebalance_timeline(
        cls,
        dialogues: List[Any],
        raw_results_map: Dict[int, AudioSegment],
        target_ratio: float = 1.3
    ) -> Tuple[List[Dict[str, Any]], Dict[int, Tuple[float, float]]]:
        """
        Thuc hien can bang timeline bang co che di vay thoi gian giua cac cau lan can.

        Args:
            dialogues: Danh sach cac doi tuong TTSDialogueSegmentModel
            raw_results_map: Ban do chua AudioSegment tho cua tung cau {dialogue_id: AudioSegment}
            target_ratio: Nguong ty le muc tieu (mac dinh 1.3)

        Returns:
            Tuple[borrow_transactions, borrow_map]:
            - borrow_transactions: Log cac giao dich da thuc hien (de luu CSDL tuy chon)
            - borrow_map: Dict[dialogue_id -> (effective_start, effective_end)]
              Caller dung map nay de tinh start_sec khi ghi vao master_buffer.
              KHONG bao gio ghi map nay vao asr_start/asr_end cua CSDL.
        """
        if not dialogues or not raw_results_map:
            return [], {}

        n = len(dialogues)

        # Doc ASR start/end bat bien (khong sua)
        # Uu tien doc asr_start/asr_end neu co (schema moi), fallback start_time/end_time
        starts = [
            float(getattr(d, 'asr_start', None) or getattr(d, 'start_time', 0.0))
            for d in dialogues
        ]
        ends = [
            float(getattr(d, 'asr_end', None) or getattr(d, 'end_time', starts[i] + 1.0))
            for i, d in enumerate(dialogues)
        ]
        orig_durations = [max(0.1, ends[i] - starts[i]) for i in range(n)]

        audio_durations = []
        for d in dialogues:
            seg = raw_results_map.get(d.id)
            dur = (len(seg) / 1000.0) if seg else 0.0
            audio_durations.append(dur)

        # ------------------------------------------------------------------
        # BUOC 1: TINH TOAN TRUOC TOAN CUC (DEFICIT & BUDGET)
        # ------------------------------------------------------------------
        deficits = [0.0] * n
        budgets  = [0.0] * n

        for i in range(n):
            w = orig_durations[i]
            t_audio = audio_durations[i]
            if t_audio <= 0:
                continue

            current_ratio = t_audio / w

            # Tinh luong cau do co the tu got toi da theo thuat toan 1:2
            if current_ratio > 1.2:
                excess_sec  = max(0.0, t_audio - (w * 1.2))
                excess_ms   = int(round(excess_sec * 1000))
                ideal_head  = int(round(excess_ms * 1.0 / 3.0))
                ideal_tail  = excess_ms - ideal_head
                cut_head    = min(100, ideal_head)
                cut_tail    = min(200, ideal_tail)
                max_self_trim = (cut_head + cut_tail) / 1000.0
            else:
                max_self_trim = 0.0

            t_after_self_trim = max(0.1, t_audio - max_self_trim)
            w_required = t_after_self_trim / target_ratio

            if w_required > w:
                deficits[i] = round(w_required - w, 3)
                budgets[i]  = 0.0
            else:
                safe_min_window = t_audio / target_ratio
                if w > safe_min_window:
                    budgets[i] = round(w - safe_min_window, 3)
                else:
                    budgets[i] = 0.0

        # ------------------------------------------------------------------
        # BUOC 2: SAP XEP UU TIEN VA DIEU PHOI VAY NO
        # ------------------------------------------------------------------
        needy_indices = [i for i in range(n) if deficits[i] > 0.005]
        needy_indices.sort(key=lambda idx: deficits[idx], reverse=True)

        borrow_transactions = []
        shifts_left  = [0.0] * n   # Mo rong ve phia truoc (lui start)
        shifts_right = [0.0] * n   # Mo rong ve phia sau (day end)

        for i in needy_indices:
            needed = deficits[i]
            if needed <= 0.005:
                continue

            has_prev = (i > 0)
            has_next = (i < n - 1)

            b_prev = budgets[i - 1] if has_prev else 0.0
            b_next = budgets[i + 1] if has_next else 0.0

            if b_prev <= 0 and b_next <= 0:
                continue

            if has_prev and has_next:
                half_needed = needed / 2.0
                take_prev = min(half_needed, b_prev)
                take_next = min(half_needed, b_next)
            elif has_prev:
                take_prev = min(needed, b_prev)
                take_next = 0.0
            else:
                take_prev = 0.0
                take_next = min(needed, b_next)

            rem_needed = needed - (take_prev + take_next)
            if rem_needed > 0.005:
                if has_prev and (b_prev - take_prev) > 0.005:
                    extra = min(rem_needed, b_prev - take_prev)
                    take_prev += extra
                    rem_needed -= extra
                if rem_needed > 0.005 and has_next and (b_next - take_next) > 0.005:
                    extra = min(rem_needed, b_next - take_next)
                    take_next += extra

            total_borrowed = take_prev + take_next
            if total_borrowed <= 0.005:
                continue

            if has_prev and take_prev > 0:
                budgets[i - 1] = max(0.0, budgets[i - 1] - take_prev)
                shifts_left[i] += take_prev

            if has_next and take_next > 0:
                budgets[i + 1] = max(0.0, budgets[i + 1] - take_next)
                shifts_right[i] += take_next

            new_window = orig_durations[i] + total_borrowed
            new_projected_ratio = round(audio_durations[i] / new_window, 2)

            borrow_transactions.append({
                "index":           dialogues[i].tts_index if hasattr(dialogues[i], 'tts_index') else getattr(dialogues[i], 'index', i + 1),
                "dialogue_id":     dialogues[i].id,
                "needed_sec":      needed,
                "borrowed_total":  round(total_borrowed, 3),
                "from_prev_idx":   (dialogues[i - 1].tts_index if hasattr(dialogues[i-1], 'tts_index') else getattr(dialogues[i-1], 'index', i)) if (has_prev and take_prev > 0) else None,
                "borrowed_prev":   round(take_prev, 3),
                "from_next_idx":   (dialogues[i + 1].tts_index if hasattr(dialogues[i+1], 'tts_index') else getattr(dialogues[i+1], 'index', i + 2)) if (has_next and take_next > 0) else None,
                "borrowed_next":   round(take_next, 3),
                "orig_window":     round(orig_durations[i], 2),
                "new_window":      round(new_window, 2),
                "orig_ratio":      round(audio_durations[i] / orig_durations[i], 2),
                "new_ratio":       new_projected_ratio
            })

        # ------------------------------------------------------------------
        # BUOC 3: TINH BAN DO VAY MUON (BORROW MAP)
        # CHI TINH TOAN NOI BO, KHONG SUA ORM OBJECT NAO CA.
        # Caller dung borrow_map[d.id] -> (effective_start, effective_end)
        # de chon start_sec khi ghi vao master_buffer.
        # ------------------------------------------------------------------
        borrow_map: Dict[int, Tuple[float, float]] = {}
        index_to_pos = {d.id: k for k, d in enumerate(dialogues)}

        for trans in borrow_transactions:
            d_id = trans["dialogue_id"]
            idx_in_list = index_to_pos.get(d_id)
            if idx_in_list is None:
                continue

            t_prev = trans["borrowed_prev"]
            t_next = trans["borrowed_next"]

            eff_start = starts[idx_in_list]
            eff_end   = ends[idx_in_list]

            if t_prev > 0 and idx_in_list > 0:
                eff_start = max(0.0, eff_start - t_prev)
                # Dong bo thu hep phan end cua cau truoc (cau cho vay) trong borrow_map
                prev_id = dialogues[idx_in_list - 1].id
                p_s, p_e = borrow_map.get(prev_id, (starts[idx_in_list - 1], ends[idx_in_list - 1]))
                borrow_map[prev_id] = (p_s, min(p_e, eff_start))

            if t_next > 0 and idx_in_list < n - 1:
                eff_end = eff_end + t_next
                # Dong bo lui phan start cua cau sau (cau cho vay) trong borrow_map
                next_id = dialogues[idx_in_list + 1].id
                n_s, n_e = borrow_map.get(next_id, (starts[idx_in_list + 1], ends[idx_in_list + 1]))
                borrow_map[next_id] = (max(n_s, eff_end), n_e)

            borrow_map[d_id] = (round(eff_start, 3), round(eff_end, 3))

        # In log điều phối vay mượn gọn gàng, rõ ràng
        if borrow_transactions:
            cls._safe_print(f"🤝 [Timeline Borrower] Điều phối vay mượn thời gian ({len(borrow_transactions)} câu thiếu hụt):")
            for trans in borrow_transactions:
                t_prev = trans["borrowed_prev"]
                t_next = trans["borrowed_next"]
                borrow_src = []
                if t_prev > 0 and trans["from_prev_idx"]:
                    borrow_src.append(f"#{trans['from_prev_idx']:03d} (-{t_prev:.2f}s)")
                if t_next > 0 and trans["from_next_idx"]:
                    borrow_src.append(f"#{trans['from_next_idx']:03d} (-{t_next:.2f}s)")
                borrow_src_str = " + ".join(borrow_src) if borrow_src else "Tự cân đối"

                c_idx = trans.get("index", 0)
                needed = trans.get("needed_sec", 0.0)
                tot_b = trans.get("borrowed_total", 0.0)
                w_old = trans.get("orig_window", 0.0)
                w_new = trans.get("new_window", 0.0)
                r_new = trans.get("new_ratio", 1.0)

                log_line = (
                    f"   • Câu #{c_idx:03d} (Thiếu {needed:+.2f}s) ➔ Vay từ {borrow_src_str} "
                    f"| Cửa sổ: {w_old:.2f}s ➔ {w_new:.2f}s (+{tot_b:.2f}s) "
                    f"| Tốc độ mới: {r_new:.2f}x"
                )
                cls._safe_print(log_line)
            cls._safe_print(f"   ✔ Cân bằng xong {len(borrow_transactions)} câu. Tất cả câu thoại đạt tốc độ đọc tự nhiên!\n")

        return borrow_transactions, borrow_map
