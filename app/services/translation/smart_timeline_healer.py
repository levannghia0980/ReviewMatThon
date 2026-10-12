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
        # Nếu LLM xé câu làm 2 khiến 1 mẩu gốc xuất hiện ở cả 2 câu liền kề:
        # Tự động gộp câu vụn ở sau về lại câu dài phía trước, nối text tiếng Việt liền mạch!
        has_src = all("src" in it and isinstance(it["src"], list) and len(it["src"]) > 0 for it in healed_items)
        if has_src:
            for it in healed_items:
                it["src"] = sorted([int(x) for x in it["src"] if isinstance(x, (int, str)) and str(x).isdigit()])

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
                    # Gộp câu sau vào câu trước: nối text tiếng Việt
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

            # Cập nhật mốc s và e chuẩn xác theo src sau khi đã gộp
            all_indices = [idx for it in healed_items for idx in it["src"]]
            if all_indices and min(all_indices) >= 1 and max(all_indices) <= total_orig:
                for it in healed_items:
                    first_i = it["src"][0] - 1
                    last_i = it["src"][-1] - 1
                    first_seg = orig_segments[first_i]
                    last_seg = orig_segments[last_i]
                    it["s"] = round(float(first_seg["start"]), 2)
                    it["e"] = round(float(last_seg["end"]), 2)

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

        return healed_items, fixes_count, error_logs
