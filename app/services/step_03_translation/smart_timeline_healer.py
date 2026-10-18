# -*- coding: utf-8 -*-
"""
app/services/translation/smart_timeline_healer.py
==================================================
Hệ thống Kiểm toán 3 Lớp & Khôi phục Mốc Thời Gian (Smart Timeline Healer)
Bảo vệ thời lượng chính xác tuyệt đối:
- Lớp 1: Khai thác 'src' nếu LLM trả về danh sách mẩu gốc hợp lệ.
- Lớp 2: Kiểm toán mốc 's' và 'e' trực tiếp từ ASR gốc, phát hiện câu bị ép/ưu ái thời gian.
- Lớp 3: Bóc tách số vế câu con tương quan vị trí gốc (lọc dấu phẩy bổ trợ ở đầu/cuối),
         truy thu thời gian bị lấn chiếm và tái phân bổ khít 100% theo mốc CapCut ASR.
"""

import re
import logging
from typing import List, Dict, Any, Tuple

logger = logging.getLogger(__name__)


class SmartTimelineHealer:
    @staticmethod
    def _count_effective_clauses(vi_text: str, corresponding_origs: List[Dict[str, Any]]) -> int:
        """
        Đếm số câu con thực tế trong câu tiếng Việt.
        So sánh vị trí mẩu con với mẩu gốc tiếng Trung tương ứng:
        - Dấu phẩy ở đầu (trạng ngữ, tên gọi như 'Hạ Phạn,', 'Hôm nay,') hoặc ở cuối trước dấu kết câu
          nếu mẩu con quá ngắn trong khi mẩu gốc dài -> Dấu phẩy bổ trợ, KHÔNG tính là câu con gốc.
        - Chỉ tính khi mẩu con tương xứng với 1 mẩu gốc hoặc mẩu gốc thực sự cũng ngắn.
        """
        raw_clauses = [c.strip() for c in re.split(r'[,.!?;]+', vi_text) if c.strip()]
        if not raw_clauses:
            return 1

        if len(raw_clauses) == 1 or not corresponding_origs:
            return len(raw_clauses)

        effective_clauses = []
        for c_idx, clause in enumerate(raw_clauses):
            words = clause.split()
            # Mẩu con ở vị trí đầu câu hoặc cuối câu có ít từ (<= 3 từ)
            if (c_idx == 0 or c_idx == len(raw_clauses) - 1) and len(words) <= 3:
                # Kiểm tra mẩu gốc tương ứng
                orig_idx = min(c_idx, len(corresponding_origs) - 1)
                orig_zh = str(corresponding_origs[orig_idx].get("zh", "")).strip()
                # Nếu mẩu gốc dài (>= 6 chữ Hán) mà mẩu con tiếng Việt lại quá cộc lốc (<= 3 từ)
                # -> Đây chắc chắn chỉ là dấu phẩy bổ trợ ngắt nhịp (vd: "Hôm nay,", "Hạ Phạn,")
                if len(orig_zh) >= 6:
                    continue
            effective_clauses.append(clause)

        return max(1, len(effective_clauses))

    @classmethod
    def audit_and_heal(
        cls,
        orig_segments: List[Dict[str, Any]], 
        ai_merged_items: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], int, List[str]]:
        """
        Đầu vào:
            orig_segments: Danh sách mẩu gốc [{'index': 1, 'start': 0.08, 'end': 1.08, 'zh': '...'}, ...]
            ai_merged_items: Danh sách câu gộp do LLM trả về [{'src': [1,2], 's': 0.08, 'e': 4.76, 'vi': '...'}, ...]
        Đầu ra:
            (healed_items, fixes_count, error_logs)
        """
        if not orig_segments or not ai_merged_items:
            return ai_merged_items, 0, []

        orig_starts = {round(float(s["start"]), 2): idx for idx, s in enumerate(orig_segments)}
        orig_ends = {round(float(s["end"]), 2): idx for idx, s in enumerate(orig_segments)}
        total_orig = len(orig_segments)
        total_ai = len(ai_merged_items)

        healed_items = [dict(it) for it in ai_merged_items]
        fixes_count = 0
        error_logs = []

        # -------------------------------------------------------------
        # LỚP 1: KIỂM SOÁT VÀ GỘP CÂU TRÙNG 'src' (DEDUPLICATE & MERGE FRAGMENTS)
        # -------------------------------------------------------------
        # Khôi phục và làm sạch trường 'src' cho từng câu
        for it in healed_items:
            clean_src = []
            for x in it.get("src", []):
                if isinstance(x, int):
                    clean_src.append(x)
                elif isinstance(x, str) and x.strip().isdigit():
                    clean_src.append(int(x.strip()))
            # Chỉ giữ các chỉ số hợp lệ trong phạm vi 1..total_orig
            clean_src = [v for v in clean_src if 1 <= v <= total_orig]

            # Hỗ trợ định dạng 2 số [start_idx, end_idx] (ví dụ [1, 3] bao trùm 1, 2, 3)
            if len(clean_src) == 2 and clean_src[1] > clean_src[0]:
                clean_src = list(range(clean_src[0], clean_src[1] + 1))

            # Nếu src rỗng hoặc bị mất do LLM trả về số thực (vd: [158.5, 158.9]),
            # tự động khôi phục theo mốc thời gian s và e từ orig_segments nếu có
            if not clean_src:
                it_s = round(float(it.get("s", 0)), 2)
                it_e = round(float(it.get("e", 0)), 2)
                if it_s > 0 and it_e > it_s:
                    s_idx = orig_starts.get(it_s)
                    e_idx = orig_ends.get(it_e)
                    if s_idx is not None and e_idx is not None and s_idx <= e_idx:
                        clean_src = [orig_segments[i]["index"] for i in range(s_idx, e_idx + 1)]
                    else:
                        matching = [
                            s["index"] for s in orig_segments
                            if round(float(s["start"]), 2) >= it_s - 0.05 and round(float(s["end"]), 2) <= it_e + 0.05
                        ]
                        if matching:
                            clean_src = matching
            it["src"] = sorted(list(set(clean_src)))

        # Fallback phân bổ tuần tự nếu bất kỳ câu nào thiếu src
        curr_alloc_ptr = 1
        for it in healed_items:
            c_src = it.get("src", [])
            if not c_src:
                if curr_alloc_ptr <= total_orig:
                    it["src"] = [curr_alloc_ptr]
                    curr_alloc_ptr += 1
                else:
                    it["src"] = [total_orig]
            else:
                curr_alloc_ptr = max(c_src) + 1

        has_src = all("src" in it and isinstance(it["src"], list) and len(it["src"]) > 0 for it in healed_items)
        if has_src:
            deduped_items = []
            for it in healed_items:
                if not deduped_items:
                    deduped_items.append(it)
                    continue

                prev_it = deduped_items[-1]
                prev_src_set = set(prev_it.get("src", []))
                curr_src_set = set(it.get("src", []))

                # Kiểm tra giao nhau của src giữa 2 câu liền kề
                overlap_src = prev_src_set.intersection(curr_src_set)
                if overlap_src:
                    prev_role = (prev_it.get("role") or prev_it.get("speaker") or "").strip().lower()
                    curr_role = (it.get("role") or it.get("speaker") or "").strip().lower()

                    # Nếu 2 câu khác vai nhân vật (ví dụ: Nhân vật A vs Thăng Cấp Hệ Thống):
                    # Không gộp làm một mà giữ nguyên 2 dòng thoại độc lập, loại bỏ mẩu trùng khỏi câu trước
                    if prev_role and curr_role and prev_role != curr_role:
                        rem = sorted(list(prev_src_set - overlap_src))
                        if rem:
                            prev_it["src"] = rem
                            fixes_count += 1
                            error_logs.append(f"Tách mẩu trùng {list(overlap_src)} khỏi câu trước vì khác vai nhân vật ({prev_role} vs {curr_role}).")
                        deduped_items.append(it)
                    else:
                        # Cùng vai hoặc narrator: Gộp câu sau vào câu trước: nối text tiếng Việt
                        prev_vi = prev_it.get("vi", "").strip()
                        curr_vi = it.get("vi", "").strip()
                        if curr_vi:
                            if not prev_vi.endswith((".", "!", "?", "…")):
                                prev_it["vi"] = f"{prev_vi}, {curr_vi}"
                            else:
                                prev_it["vi"] = f"{prev_vi} {curr_vi}"

                        # Gộp mảng src
                        combined_src = sorted(list(prev_src_set.union(curr_src_set)))
                        prev_it["src"] = combined_src
                        fixes_count += 1
                        error_logs.append(f"Gộp câu vụn trùng src {list(overlap_src)} về câu dài liền trước.")
                else:
                    deduped_items.append(it)

            healed_items = deduped_items
            total_ai = len(healed_items)

            # LỚP 1.1: BÙ LẤP KHE HỞ MẨU GỐC BỊ BỎ SÓT TRONG MẢNG SRC (GAP HEALING CHO PHỤ ĐỀ)
            if len(healed_items) > 1:
                for k in range(len(healed_items) - 1):
                    prev_it = healed_items[k]
                    next_it = healed_items[k + 1]
                    p_src = prev_it.get("src", [])
                    n_src = next_it.get("src", [])
                    if not p_src or not n_src:
                        continue
                    prev_max = max(p_src)
                    next_min = min(n_src)
                    if next_min > prev_max + 1:
                        gap_indices = list(range(prev_max + 1, next_min))
                        if all(1 <= g <= total_orig for g in gap_indices):
                            first_gap_s = round(float(orig_segments[gap_indices[0] - 1]["start"]), 2)
                            last_gap_e = round(float(orig_segments[gap_indices[-1] - 1]["end"]), 2)
                            prev_last_e = round(float(orig_segments[prev_max - 1]["end"]), 2)
                            next_first_s = round(float(orig_segments[next_min - 1]["start"]), 2)
                            # Đóng khít 100% khe hở: Gắn mẩu khuyết vào chunk lân cận gần nhất
                            dist_prev = max(0.0, first_gap_s - prev_last_e)
                            dist_next = max(0.0, next_first_s - last_gap_e)
                            if dist_prev <= dist_next:
                                prev_it["src"] = sorted(p_src + gap_indices)
                                fixes_count += 1
                                error_logs.append(f"Tự động bù mẩu khuyết {gap_indices} vào câu thoại #{k+1} liền trước.")
                            else:
                                next_it["src"] = sorted(gap_indices + n_src)
                                fixes_count += 1
                                error_logs.append(f"Tự động bù mẩu khuyết {gap_indices} vào câu thoại #{k+2} liền sau.")

                # Xử lý mẩu khuyết ở cuối danh sách (nếu câu cuối chưa chạm mốc total_orig)
                last_it = healed_items[-1]
                last_src = last_it.get("src", [])
                if last_src:
                    last_max = max(last_src)
                    if last_max < total_orig:
                        tail_gap = list(range(last_max + 1, total_orig + 1))
                        last_it["src"] = sorted(last_src + tail_gap)
                        fixes_count += 1
                        error_logs.append(f"Tự động gộp mẩu khuyết cuối danh sách {tail_gap} vào câu cuối.")

                # Xử lý mẩu khuyết ở đầu danh sách (nếu câu đầu không bắt đầu từ 1)
                first_it = healed_items[0]
                first_src = first_it.get("src", [])
                if first_src:
                    first_min = min(first_src)
                    if first_min > 1:
                        head_gap = list(range(1, first_min))
                        first_it["src"] = sorted(head_gap + first_src)
                        fixes_count += 1
                        error_logs.append(f"Tự động gộp mẩu khuyết đầu danh sách {head_gap} vào câu đầu tiên.")

            # Cập nhật mốc s, e, orig_count chuẩn xác 100% theo ASR gốc sau khi đã gộp và bù lấp
            all_indices = [idx for it in healed_items for idx in it.get("src", [])]
            if all_indices and min(all_indices, default=1) >= 1 and max(all_indices, default=total_orig) <= total_orig:
                for it in healed_items:
                    it_src = it.get("src", [])
                    if it_src:
                        first_i = it_src[0] - 1
                        last_i = it_src[-1] - 1
                        if 0 <= first_i < total_orig and 0 <= last_i < total_orig:
                            first_seg = orig_segments[first_i]
                            last_seg = orig_segments[last_i]
                            it["s"] = round(float(first_seg["start"]), 2)
                            it["e"] = round(float(last_seg["end"]), 2)
                            it["orig_count"] = len(it_src)
                            it["src_start_index"] = it_src[0]
                            it["src_end_index"] = it_src[-1]

                healed_items = cls._enforce_monotonic_timeline(healed_items, orig_segments)
                # Kiểm toán 3 tiêu chuẩn bất biến và in log chứng nhận
                audit_summary = cls.verify_3_invariants(healed_items, orig_segments)
                if audit_summary.get("passed"):
                    error_logs.append("✔ Đã kiểm toán đạt 100% Bộ 3 Tiêu Chí Bất Biến (Timecode, Tuần tự, Bao phủ ASR).")
                else:
                    error_logs.extend(audit_summary.get("errors", []))
                return healed_items, fixes_count, error_logs

        # -------------------------------------------------------------
        # LỚP 2: KIỂM TOÁN TIME 's' VÀ 'e' THEO LUẬT CỨNG
        # -------------------------------------------------------------
        status_list = []
        prev_end = None

        for idx, item in enumerate(healed_items):
            s = round(float(item.get("s", 0)), 2)
            e = round(float(item.get("e", 0)), 2)
            
            errs = []
            if s not in orig_starts:
                errs.append("START_NOT_IN_ORIG")
            if e not in orig_ends:
                errs.append("END_NOT_IN_ORIG")
            if s >= e:
                errs.append("INVERTED_TIMECODE")
            if prev_end is not None:
                if s < prev_end - 0.04:
                    errs.append("OVERLAP_WITH_PREV")
                
            status = "OK" if not errs else "|".join(errs)
            status_list.append(status)
            if status != "OK":
                error_logs.append(f"Khối câu #{idx+1} [s={s}, e={e}]: {status}")
            prev_end = e

        error_indices = [i for i, st in enumerate(status_list) if st != "OK"]
        if not error_indices:
            # Timecode của AI trả về đã chuẩn 100%
            return healed_items, fixes_count, error_logs

        # -------------------------------------------------------------
        # LỚP 3: KHẤU TRỪ VÙNG ĐÚNG & TÁI PHÂN BỔ BẰNG ĐẾM CÂU CON TƯƠNG QUAN VỊ TRÍ
        # -------------------------------------------------------------
        clusters = []
        current_cluster = [error_indices[0]]
        for idx in error_indices[1:]:
            if idx == current_cluster[-1] + 1 or idx == current_cluster[-1] + 2:
                current_cluster.append(idx)
            else:
                clusters.append(current_cluster)
                current_cluster = [idx]
        if current_cluster:
            clusters.append(current_cluster)

        for cluster in clusters:
            left_valid_ai_idx = cluster[0] - 1
            right_valid_ai_idx = cluster[-1] + 1

            if left_valid_ai_idx >= 0:
                left_end_time = round(float(healed_items[left_valid_ai_idx]["e"]), 2)
                orig_start_idx = orig_ends.get(left_end_time, -1) + 1
            else:
                orig_start_idx = 0

            if right_valid_ai_idx < total_ai:
                right_start_time = round(float(healed_items[right_valid_ai_idx]["s"]), 2)
                orig_end_idx = orig_starts.get(right_start_time, total_orig) - 1
            else:
                orig_end_idx = total_orig - 1

            if orig_start_idx > orig_end_idx or orig_start_idx >= total_orig:
                continue

            residual_orig_count = (orig_end_idx - orig_start_idx) + 1
            cluster_orig_segments = orig_segments[orig_start_idx:orig_end_idx + 1]

            # Đếm số câu con thực tế bằng cách lọc dấu phẩy bổ trợ tương quan độ dài gốc
            cluster_ai_clauses = []
            for ai_idx in cluster:
                vi_text = healed_items[ai_idx].get("vi", "")
                clause_cnt = cls._count_effective_clauses(vi_text, cluster_orig_segments)
                cluster_ai_clauses.append((ai_idx, clause_cnt))

            total_ai_clauses = sum(cnt for _, cnt in cluster_ai_clauses)
            current_orig_pointer = orig_start_idx

            if total_ai_clauses == residual_orig_count:
                # Khớp hoàn hảo: gán thẳng mốc gốc vào từng khối
                for ai_idx, cnt in cluster_ai_clauses:
                    start_seg = orig_segments[current_orig_pointer]
                    end_seg = orig_segments[current_orig_pointer + cnt - 1]
                    healed_items[ai_idx]["s"] = round(float(start_seg["start"]), 2)
                    healed_items[ai_idx]["e"] = round(float(end_seg["end"]), 2)
                    current_orig_pointer += cnt
                    fixes_count += 1
            else:
                # Phân bổ tỷ lệ theo số câu con thực tế
                for ai_idx, cnt in cluster_ai_clauses:
                    allocated = max(1, round(cnt * residual_orig_count / max(1, total_ai_clauses)))
                    allocated = min(allocated, (orig_end_idx - current_orig_pointer) + 1)
                    start_seg = orig_segments[current_orig_pointer]
                    end_seg = orig_segments[min(orig_end_idx, current_orig_pointer + allocated - 1)]
                    healed_items[ai_idx]["s"] = round(float(start_seg["start"]), 2)
                    healed_items[ai_idx]["e"] = round(float(end_seg["end"]), 2)
                    current_orig_pointer += allocated
                    fixes_count += 1

        healed_items = cls._enforce_monotonic_timeline(healed_items, orig_segments)
        return healed_items, fixes_count, error_logs

    @classmethod
    def _enforce_monotonic_timeline(cls, items: List[Dict[str, Any]], orig_segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Đảm bảo tuyệt đối thời gian tăng tiến đơn điệu (Monotonic Non-Decreasing), cấm nhảy lùi làm ghi đè audio."""
        if not items:
            return items
        prev_e = 0.0
        for it in items:
            it_s = round(float(it.get("s", 0.0)), 2)
            it_e = round(float(it.get("e", it_s + 1.0)), 2)
            if it_s < prev_e:
                it["s"] = prev_e
                if it_e <= prev_e:
                    it["e"] = round(prev_e + max(0.5, it_e - it_s), 2)
            prev_e = round(float(it["e"]), 2)
        return items

    @classmethod
    def verify_3_invariants(
        cls,
        items: List[Dict[str, Any]],
        orig_segments: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Kiểm định và chứng nhận 3 Tiêu chí Bất biến (3 Hard Invariants):
        1. Độ khớp Timecode & src: item.s == ASR[src_start].start và item.e == ASR[src_end].end
        2. Tính tuần tự liên tục: Chunk[n+1].src_start == Chunk[n].src_end + 1 (không hở gap, không đè overlap)
        3. Số lượng câu gốc và độ bao phủ: orig_count == src_end - src_start + 1 và sum(orig_count) == total_orig
        """
        errors = []
        if not items or not orig_segments:
            return {"passed": False, "errors": ["Danh sách rỗng"]}

        total_orig = len(orig_segments)
        
        # 1. TIÊU CHÍ 1: KHỚP NỐI TIMECODE VÀ SRC
        for idx, it in enumerate(items):
            src = it.get("src", [])
            if not src:
                errors.append(f"Tiêu chí 1: Câu #{idx+1} thiếu mảng src!")
                continue
            first_i = src[0] - 1
            last_i = src[-1] - 1
            if 0 <= first_i < total_orig and 0 <= last_i < total_orig:
                exp_s = round(float(orig_segments[first_i]["start"]), 2)
                exp_e = round(float(orig_segments[last_i]["end"]), 2)
                act_s = round(float(it.get("s", 0.0)), 2)
                act_e = round(float(it.get("e", 0.0)), 2)
                if abs(act_s - exp_s) > 0.05 or abs(act_e - exp_e) > 0.05:
                    errors.append(f"Tiêu chí 1: Câu #{idx+1} lệch timecode vs ASR: s={act_s} (ASR: {exp_s}), e={act_e} (ASR: {exp_e})")

        # 2. TIÊU CHÍ 2: TÍNH TUẦN TỰ LIÊN TỤC GIỮA CÁC CHUNK (S_{n+1} == E_n + 1)
        for idx in range(len(items) - 1):
            curr_src = items[idx].get("src", [])
            next_src = items[idx + 1].get("src", [])
            if curr_src and next_src:
                curr_end = curr_src[-1]
                next_start = next_src[0]
                if next_start != curr_end + 1:
                    errors.append(f"Tiêu chí 2: Mất tuần tự giữa câu #{idx+1} (kết thúc ở ASR #{curr_end}) và câu #{idx+2} (bắt đầu ở ASR #{next_start})")

        # 3. TIÊU CHÍ 3: ĐẾM SỐ LƯỢNG VÀ ĐỘ BAO PHỦ 100% CÂU GỐC
        all_covered = [x for it in items for x in it.get("src", [])]
        unique_covered = set(all_covered)
        expected_set = set(range(1, total_orig + 1))
        missing_segs = sorted(list(expected_set - unique_covered))
        duplicate_segs = len(all_covered) - len(unique_covered)

        if missing_segs:
            errors.append(f"Tiêu chí 3: Bỏ sót {len(missing_segs)} câu ASR gốc: {missing_segs[:5]}...")
        if duplicate_segs > 0:
            errors.append(f"Tiêu chí 3: Trùng lặp {duplicate_segs} mẩu câu ASR giữa các chunk!")

        return {
            "passed": len(errors) == 0,
            "total_chunks": len(items),
            "total_orig": total_orig,
            "covered_orig": len(unique_covered),
            "errors": errors
        }

    @classmethod
    def enforce_sequential_asr_locking(
        cls,
        healed_items: List[Dict[str, Any]],
        batch: List[Any],
        orig_starts: List[float],
        orig_ends: List[float]
    ) -> List[Dict[str, Any]]:
        """
        Khóa cứng 100% timecode tuần tự theo 2 mảng ASR gốc (dựa vào số câu con, tuyệt đối không đè nhau).
        Đảm bảo:
        - Mỗi item ôm một khoảng liên tục [curr_orig_ptr .. end_orig_ptr]
        - it["s"] = orig_starts[first_i]
        - it["e"] = orig_ends[last_i]
        - global_src, local_src, src_start_index, src_end_index, orig_count
        - Con trỏ tiếp theo curr_orig_ptr = end_orig_ptr + 1
        """
        if not healed_items or not batch:
            return healed_items

        total_orig = len(batch)
        curr_orig_ptr = 1  # 1-based index từ 1 đến total_orig

        for it_idx, it in enumerate(healed_items):
            it_src = it.get("local_src") or it.get("src", [])
            clean_cand = []
            for x in it_src:
                if isinstance(x, int) and 1 <= x <= total_orig:
                    clean_cand.append(x)
                elif isinstance(x, str) and x.strip().isdigit():
                    ix = int(x.strip())
                    if 1 <= ix <= total_orig:
                        clean_cand.append(ix)

            clause_len = max(1, len(set(clean_cand))) if clean_cand else 1

            is_last = (it_idx == len(healed_items) - 1)
            if is_last:
                end_orig_ptr = total_orig
            else:
                remaining_items = len(healed_items) - 1 - it_idx
                max_available = total_orig - remaining_items
                end_orig_ptr = min(max_available, curr_orig_ptr + clause_len - 1)

            end_orig_ptr = max(curr_orig_ptr, end_orig_ptr)

            clean_local_src = list(range(curr_orig_ptr, end_orig_ptr + 1))
            first_i = clean_local_src[0] - 1
            last_i = clean_local_src[-1] - 1

            it["s"] = round(float(orig_starts[first_i]), 2)
            it["e"] = round(float(orig_ends[last_i]), 2)

            global_src = [getattr(batch[k - 1], 'id', getattr(batch[k - 1], 'index', k)) for k in clean_local_src]
            it["local_src"] = clean_local_src
            it["src"] = global_src
            it["src_start_index"] = global_src[0]
            it["src_end_index"] = global_src[-1]
            it["orig_count"] = len(global_src)

            curr_orig_ptr = end_orig_ptr + 1

        return healed_items
