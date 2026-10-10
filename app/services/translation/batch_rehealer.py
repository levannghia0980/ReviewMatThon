import os
import json
import logging
from typing import List, Dict, Any, Optional, Tuple, Set
from sqlalchemy.orm import Session

from app.config import settings
from app.models import ProjectTask, DialogueSegmentModel
from app.services.task_manager import task_manager

logger = logging.getLogger(__name__)

class BatchRehealer:
    """
    Cơ chế Vá Lô Cục Bộ (Targeted Batch Rehealer).
    
    Khi phát hiện câu có tốc độ co giãn âm thanh bất thường (speed_ratio > 2.0x):
    - Thay vì ép tăng tốc 2.0x làm giọng đọc bị méo mó, biến dạng:
    - Hệ thống định vị chính xác câu đó thuộc Lô (Batch) thứ mấy dựa trên `TRANSLATION_BATCH_SIZE` trong cấu hình cài đặt.
    - Chỉ dịch lại và tạo lại TTS cho duy nhất Lô đó, giữ nguyên 100% các câu ở các lô khác.
    """

    @classmethod
    def get_configured_batch_size(cls, override_batch_size: Optional[int] = None) -> int:
        """
        Lấy số câu trong một lô từ cấu hình settings hoặc biến truyền vào.
        """
        if override_batch_size and override_batch_size > 0:
            return override_batch_size
        return getattr(settings, "TRANSLATION_BATCH_SIZE", 300) or 300

    @classmethod
    def get_batch_number_for_index(cls, sentence_index: int, batch_size: Optional[int] = None) -> int:
        """
        Tính toán số thứ tự Lô (1-based index) cho câu thoại.
        Ví dụ: batch_size = 300:
          - Câu 1 -> 300   : Lô 1
          - Câu 301 -> 600 : Lô 2
          - Câu 901 -> 1200: Lô 4
        """
        b_size = cls.get_configured_batch_size(batch_size)
        idx = max(1, sentence_index)
        return ((idx - 1) // b_size) + 1

    @classmethod
    def get_batch_bounds(cls, batch_num: int, batch_size: Optional[int] = None) -> Tuple[int, int]:
        """
        Trả về khoảng index [start_index, end_index] (1-based) của lô đó.
        """
        b_size = cls.get_configured_batch_size(batch_size)
        b_num = max(1, batch_num)
        start_idx = (b_num - 1) * b_size + 1
        end_idx = b_num * b_size
        return start_idx, end_idx

    @classmethod
    def detect_anomalies(
        cls,
        dialogues: List[DialogueSegmentModel],
        max_ratio_threshold: float = 2.0
    ) -> List[Dict[str, Any]]:
        """
        Quét và phát hiện các câu bị phình thời lượng vượt ngưỡng an toàn (mặc định > 2.0x).
        """
        anomalies = []
        for d in dialogues:
            target_dur = max(0.1, (d.end_time - d.start_time)) if (d.end_time and d.start_time) else 1.0
            voice_dur = d.voice_duration or 0.0
            speed_ratio = d.speed_ratio or 1.0

            calc_ratio = voice_dur / target_dur if (voice_dur > 0 and target_dur > 0) else speed_ratio

            if calc_ratio > max_ratio_threshold or speed_ratio > max_ratio_threshold:
                anomalies.append({
                    "dialogue_id": d.id,
                    "index": d.index,
                    "original_text": d.clean_text or d.original_text,
                    "translated_text": d.translated_text,
                    "target_dur": round(target_dur, 2),
                    "voice_dur": round(voice_dur, 2),
                    "speed_ratio": round(calc_ratio, 2),
                })
        return anomalies

    @classmethod
    def group_anomalies_by_batch(
        cls,
        anomalies: List[Dict[str, Any]],
        batch_size: Optional[int] = None
    ) -> Dict[int, List[Dict[str, Any]]]:
        """
        Gom nhóm các câu bị lỗi theo số thứ tự Lô (Batch number).
        """
        grouped: Dict[int, List[Dict[str, Any]]] = {}
        b_size = cls.get_configured_batch_size(batch_size)

        for item in anomalies:
            b_num = cls.get_batch_number_for_index(item["index"], b_size)
            if b_num not in grouped:
                grouped[b_num] = []
            grouped[b_num].append(item)
        return grouped

    @classmethod
    def format_batch_reheal_summary(
        cls,
        anomalies: List[Dict[str, Any]],
        batch_size: Optional[int] = None
    ) -> str:
        """
        Tạo báo cáo chi tiết về các lô cần vá để log ra giao diện người dùng.
        """
        b_size = cls.get_configured_batch_size(batch_size)
        grouped = cls.group_anomalies_by_batch(anomalies, b_size)
        if not grouped:
            return "✔ Không phát hiện câu nào bị phình vượt ngưỡng 2.0x."

        lines = [f"⚠️ Phát hiện {len(anomalies)} câu bị phình > 2.0x nằm trong {len(grouped)} lô (cỡ lô: {b_size} câu/lô):"]
        for b_num, items in sorted(grouped.items()):
            start_i, end_i = cls.get_batch_bounds(b_num, b_size)
            err_indices = [str(it["index"]) for it in items]
            lines.append(f"  • Lô #{b_num} (Câu #{start_i} -> #{end_i}): Có {len(items)} câu lỗi (Câu: {', '.join(err_indices[:5])}{'...' if len(err_indices) > 5 else ''})")
        return "\n".join(lines)

    @classmethod
    def _safe_print(cls, msg: str) -> None:
        try:
            print(msg, flush=True)
        except UnicodeEncodeError:
            import sys
            enc = getattr(sys.stdout, 'encoding', 'utf-8') or 'utf-8'
            print(msg.encode(enc, errors='replace').decode(enc, errors='replace'), flush=True)

    @classmethod
    def log_anomaly_to_terminal(
        cls,
        sentence_index: int,
        ratio: float,
        target_dur: float,
        trimmed_dur: float,
        batch_size: Optional[int] = None
    ) -> None:
        """
        In cảnh báo phát hiện câu phình > 2.0x lên Terminal.
        """
        b_size = cls.get_configured_batch_size(batch_size)
        b_num = cls.get_batch_number_for_index(sentence_index, b_size)
        start_i, end_i = cls.get_batch_bounds(b_num, b_size)
        msg = (
            f"[CANH BAO VA LO] Cau #{sentence_index:03d} phinh {ratio:.2f}x ({trimmed_dur:.2f}s/{target_dur:.2f}s > 2.0x) -> "
            f"Thuoc Lo #{b_num} (Pham vi cau #{start_i} -> #{end_i}, co lo: {b_size} cau) can dich lai!"
        )
        cls._safe_print(msg)

    @classmethod
    def log_reheal_start_to_terminal(
        cls,
        batch_num: int,
        start_idx: int,
        end_idx: int,
        total_in_batch: int
    ) -> None:
        """
        In thông báo bắt đầu tiến trình dịch lại Lô lỗi lên Terminal.
        """
        msg = (
            f"[TIEN TRINH VA LO] Dang dich lai Lo #{batch_num} (Cau #{start_idx} -> #{end_idx}, tong {total_in_batch} cau)... "
            f"Yeu cau: Dich co dong, suc tich, khop nhip dieu goc!"
        )
        cls._safe_print(msg)

    @classmethod
    def log_reheal_complete_to_terminal(
        cls,
        batch_num: int,
        start_idx: int,
        end_idx: int,
        total_in_batch: int
    ) -> None:
        """
        In thông báo hoàn tất vá lô và cập nhật âm thanh lên Terminal.
        """
        msg = (
            f"[TIEN TRINH VA LO] Hoan tat va Lo #{batch_num} (Cau #{start_idx} -> #{end_idx}, tong {total_in_batch} cau) -> "
            f"CSDL & Audio da dong bo khop hoan hao voi Timeline video!"
        )
        cls._safe_print(msg)
