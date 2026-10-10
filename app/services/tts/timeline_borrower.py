import logging
from typing import List, Dict, Any, Tuple, Optional
from pydub import AudioSegment

logger = logging.getLogger(__name__)

class TimelineBorrower:
    """
    Thuật toán Đi Vay Thời Gian Timeline (Timeline Borrowing Algorithm).
    
    Quy trình hoạt động:
    1. Tính trước Nhu cầu thiếu hụt (deficit) và Vốn dư dả (budget) cho toàn bộ các câu:
       - Vốn cho vay tối đa của câu dư: W_gốc - (T_audio / 1.35)
       - Nhu cầu cần vay của câu thiếu: (T_audio - lượng_tự_gọt_1:2) / 1.35 - W_gốc
    2. Sắp xếp ưu tiên: Câu thiếu nhiều nhất được ưu tiên cấp vốn trước.
    3. Phân bổ thông minh:
       - Câu trước dài hơn câu sau (>1.3x): Vay trước 75%, sau 25%.
       - Câu sau dài hơn câu trước (>1.3x): Vay sau 75%, trước 25%.
       - Ngang nhau hoặc bình thường: Vay đều 50% trước, 50% sau.
    4. Trừ dần vốn khả dụng của câu cho vay sau mỗi lần giải ngân để tránh vỡ nợ.
    5. Cập nhật mốc timeline trực tiếp nhưng bảo toàn nguyên vẹn khoảng nghỉ (silence gap).
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
        target_ratio: float = 1.35
    ) -> List[Dict[str, Any]]:
        """
        Thực hiện cân bằng timeline bằng cơ chế đi vay thời gian giữa các câu lân cận.
        
        Args:
            dialogues: Danh sách các đối tượng DialogueSegmentModel
            raw_results_map: Bản đồ chứa AudioSegment thô của từng câu {dialogue_id: AudioSegment}
            target_ratio: Ngưỡng tỷ lệ mục tiêu (mặc định 1.35)
            
        Returns:
            List[Dict[str, Any]]: Báo cáo các giao dịch vay mượn đã thực hiện.
        """
        if not dialogues or not raw_results_map:
            return []

        n = len(dialogues)
        # Lưu trữ mốc thời gian làm việc
        starts = [float(d.start_time) for d in dialogues]
        ends = [float(d.end_time) if d.end_time else float(d.start_time) + 1.0 for d in dialogues]
        orig_durations = [max(0.1, ends[i] - starts[i]) for i in range(n)]

        audio_durations = []
        for d in dialogues:
            seg = raw_results_map.get(d.id)
            dur = (len(seg) / 1000.0) if seg else 0.0
            audio_durations.append(dur)

        # -------------------------------------------------------------
        # BƯỚC 1: TÍNH TOÁN TRƯỚC TOÀN CỤC (DEFICIT & BUDGET)
        # -------------------------------------------------------------
        deficits = [0.0] * n
        budgets = [0.0] * n

        for i in range(n):
            w = orig_durations[i]
            t_audio = audio_durations[i]
            if t_audio <= 0:
                continue

            current_ratio = t_audio / w

            # Tính lượng câu đó có thể tự gọt tối đa theo thuật toán 1:2 (đầu max 100ms, đuôi max 200ms)
            if current_ratio > 1.25:
                excess_sec = max(0.0, t_audio - (w * 1.25))
                excess_ms = int(round(excess_sec * 1000))
                ideal_head = int(round(excess_ms * 1.0 / 3.0))
                ideal_tail = excess_ms - ideal_head
                cut_head = min(100, ideal_head)
                cut_tail = min(200, ideal_tail)
                max_self_trim = (cut_head + cut_tail) / 1000.0
            else:
                max_self_trim = 0.0

            # Thời lượng audio sau khi câu tự cứu
            t_after_self_trim = max(0.1, t_audio - max_self_trim)

            # Cửa sổ timeline cần thiết để đạt đúng target_ratio (1.35x)
            w_required = t_after_self_trim / target_ratio

            if w_required > w:
                # Câu bị thiếu cửa sổ -> Cần đi vay
                deficits[i] = round(w_required - w, 3)
                budgets[i] = 0.0
            else:
                # Câu dư giả cửa sổ -> Có vốn cho vay
                # Vốn tối đa đảm bảo sau khi cho vay tỷ lệ audio nguyên bản vẫn <= 1.35x
                safe_min_window = t_audio / target_ratio
                if w > safe_min_window:
                    budgets[i] = round(w - safe_min_window, 3)
                else:
                    budgets[i] = 0.0

        # -------------------------------------------------------------
        # BƯỚC 2: SẮP XẾP ƯU TIÊN VÀ ĐIỀU PHỐI VAY NỢ
        # -------------------------------------------------------------
        # Gom các câu thiếu hụt và sắp xếp giảm dần theo mức độ thiếu (lâm nguy nhất giải ngân trước)
        needy_indices = [i for i in range(n) if deficits[i] > 0.005]
        needy_indices.sort(key=lambda idx: deficits[idx], reverse=True)

        borrow_transactions = []
        shifts_left = [0.0] * n   # Mở rộng về phía trước (lùi start_time)
        shifts_right = [0.0] * n  # Mở rộng về phía sau (đẩy end_time)

        for i in needy_indices:
            needed = deficits[i]
            if needed <= 0.005:
                continue

            has_prev = (i > 0)
            has_next = (i < n - 1)

            w_prev = orig_durations[i - 1] if has_prev else 0.0
            w_next = orig_durations[i + 1] if has_next else 0.0

            b_prev = budgets[i - 1] if has_prev else 0.0
            b_next = budgets[i + 1] if has_next else 0.0

            if b_prev <= 0 and b_next <= 0:
                # Cả hai bên đều không có vốn -> Câu này chấp nhận giữ nguyên
                continue

            # Phân bổ tỷ lệ theo độ dài cửa sổ của 2 câu hàng xóm
            if has_prev and has_next:
                if w_prev > 1.3 * w_next:
                    ratio_prev, ratio_next = 0.75, 0.25
                elif w_next > 1.3 * w_prev:
                    ratio_prev, ratio_next = 0.25, 0.75
                else:
                    ratio_prev, ratio_next = 0.50, 0.50
            elif has_prev:
                ratio_prev, ratio_next = 1.0, 0.0
            else:
                ratio_prev, ratio_next = 0.0, 1.0

            ideal_take_prev = needed * ratio_prev
            ideal_take_next = needed * ratio_next

            # Vay lần 1 theo tỷ lệ
            take_prev = min(ideal_take_prev, b_prev) if has_prev else 0.0
            take_next = min(ideal_take_next, b_next) if has_next else 0.0

            rem_needed = needed - (take_prev + take_next)

            # Nếu một bên hết vốn mà bên kia còn dư vốn -> chuyển sang bên còn vốn vay nốt
            if rem_needed > 0.005:
                if has_prev and (b_prev - take_prev) > 0.005:
                    extra = min(rem_needed, b_prev - take_prev)
                    take_prev += extra
                    rem_needed -= extra

                if rem_needed > 0.005 and has_next and (b_next - take_next) > 0.005:
                    extra = min(rem_needed, b_next - take_next)
                    take_next += extra
                    rem_needed -= extra

            total_borrowed = take_prev + take_next
            if total_borrowed <= 0.005:
                continue

            # Trừ dần vốn của hàng xóm để tránh bị câu khác vay trùng
            if has_prev and take_prev > 0:
                budgets[i - 1] = max(0.0, budgets[i - 1] - take_prev)
                shifts_left[i] += take_prev

            if has_next and take_next > 0:
                budgets[i + 1] = max(0.0, budgets[i + 1] - take_next)
                shifts_right[i] += take_next

            # Tính tỷ lệ dự kiến mới sau khi vay
            new_window = orig_durations[i] + total_borrowed
            new_projected_ratio = round(audio_durations[i] / new_window, 2)

            borrow_transactions.append({
                "index": dialogues[i].index,
                "needed_sec": needed,
                "borrowed_total": round(total_borrowed, 3),
                "from_prev_idx": dialogues[i - 1].index if (has_prev and take_prev > 0) else None,
                "borrowed_prev": round(take_prev, 3),
                "from_next_idx": dialogues[i + 1].index if (has_next and take_next > 0) else None,
                "borrowed_next": round(take_next, 3),
                "orig_window": round(orig_durations[i], 2),
                "new_window": round(new_window, 2),
                "orig_ratio": round(audio_durations[i] / orig_durations[i], 2),
                "new_ratio": new_projected_ratio
            })

        # -------------------------------------------------------------
        # BƯỚC 3: CẬP NHẬT MỐC START/END VÀ BẢO TOÀN KHOẢNG NGHỈ
        # -------------------------------------------------------------
        for trans in borrow_transactions:
            c_idx = trans["index"]
            # Tìm chỉ số mảng
            idx_in_list = next((k for k, d in enumerate(dialogues) if d.index == c_idx), None)
            if idx_in_list is None:
                continue

            t_prev = trans["borrowed_prev"]
            t_next = trans["borrowed_next"]

            if t_prev > 0 and idx_in_list > 0:
                # Nới lùi start của câu hiện tại, thu ngắn end của câu trước
                dialogues[idx_in_list].start_time = max(0.0, float(dialogues[idx_in_list].start_time) - t_prev)
                dialogues[idx_in_list - 1].end_time = max(float(dialogues[idx_in_list - 1].start_time) + 0.1, float(dialogues[idx_in_list - 1].end_time) - t_prev)

            if t_next > 0 and idx_in_list < n - 1:
                # Nới đẩy end của câu hiện tại, đẩy lùi start của câu sau
                dialogues[idx_in_list].end_time = float(dialogues[idx_in_list].end_time) + t_next
                dialogues[idx_in_list + 1].start_time = min(float(dialogues[idx_in_list + 1].end_time) - 0.1, float(dialogues[idx_in_list + 1].start_time) + t_next)

            # In thông báo giao dịch vay thời gian ra Terminal
            p_str = f"Vay #{trans['from_prev_idx']:03d}: {t_prev:.2f}s" if trans['from_prev_idx'] else "Khong vay truoc"
            n_str = f"Vay #{trans['from_next_idx']:03d}: {t_next:.2f}s" if trans['from_next_idx'] else "Khong vay sau"
            log_msg = (
                f"[VAY TIME] Cau #{c_idx:03d} (Thieu {trans['needed_sec']:.2f}s) -> "
                f"{p_str} | {n_str} (Tong {trans['borrowed_total']:.2f}s) -> "
                f"Cua so: {trans['orig_window']:.2f}s -> {trans['new_window']:.2f}s -> "
                f"Ty le moi: {trans['new_ratio']:.2f}x"
            )
            cls._safe_print(log_msg)

        if borrow_transactions:
            cls._safe_print(f"✔ [TimelineBorrower] Da hoan tat dieu phoi cho vay {len(borrow_transactions)} cau thanh cong!\n")

        return borrow_transactions
