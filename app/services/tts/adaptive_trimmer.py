import logging
from typing import Tuple, Dict, Any, Optional
from pydub import AudioSegment

logger = logging.getLogger(__name__)

class AdaptiveSilenceTrimmer:
    """
    Thuật toán Gọt Thích Ứng Tỷ Lệ 1:2 (Adaptive 1:2 Silence Trimmer).
    
    Mục đích:
    - Khi tỷ lệ thời lượng TTS so với phụ đề gốc (ratio = raw_dur / target_dur) > 1.25,
      tính chính xác lượng thời gian dư thừa cần cắt để đưa tỷ lệ về đúng ngưỡng tự nhiên 1.25x.
    - Chia thời gian cần cắt theo tỷ lệ 1:2:
        + Đầu gánh 1 phần (~33.33%), tối đa trần max_head_ms (mặc định 100ms) để bảo vệ phụ âm đầu.
        + Đuôi gánh 2 phần (~66.67%), tối đa trần max_tail_ms (mặc định 200ms) để dọn sạch khoảng lặng đuôi.
    - Tổng trần tối đa gọt bỏ là 300ms.
    - Nếu câu chỉ hơi dài (cần cắt < 300ms), thuật toán sẽ cắt vừa đủ để câu về đúng 1.25x
      mà không làm lẹm thêm bất kỳ mili-giây âm thanh nào.
    """

    @classmethod
    def calculate_trim_params(
        cls,
        raw_dur_sec: float,
        target_dur_sec: float,
        target_ratio: float = 1.25,
        max_head_ms: int = 100,
        max_tail_ms: int = 200
    ) -> Dict[str, Any]:
        """
        Tính toán số mili-giây cần cắt ở đầu và đuôi mà không làm thay đổi trực tiếp audio.
        """
        if target_dur_sec <= 0 or raw_dur_sec <= 0:
            return {
                "needs_trim": False,
                "initial_ratio": 1.0,
                "target_ratio": target_ratio,
                "needed_cut_ms": 0,
                "cut_head_ms": 0,
                "cut_tail_ms": 0,
                "total_cut_ms": 0,
                "projected_dur_sec": raw_dur_sec,
                "projected_ratio": 1.0
            }

        initial_ratio = raw_dur_sec / target_dur_sec

        # Nếu tỷ lệ đã nằm trong ngưỡng cho phép (<= 1.25), không cần gọt
        if initial_ratio <= target_ratio:
            return {
                "needs_trim": False,
                "initial_ratio": round(initial_ratio, 3),
                "target_ratio": target_ratio,
                "needed_cut_ms": 0,
                "cut_head_ms": 0,
                "cut_tail_ms": 0,
                "total_cut_ms": 0,
                "projected_dur_sec": raw_dur_sec,
                "projected_ratio": round(initial_ratio, 3)
            }

        # Thời lượng âm thanh cho phép để đạt đúng target_ratio (1.25x)
        allowed_dur_sec = target_dur_sec * target_ratio
        excess_dur_sec = raw_dur_sec - allowed_dur_sec
        needed_cut_ms = max(0, int(round(excess_dur_sec * 1000)))

        # Phân bổ tỷ lệ 1:2 (Đầu 1/3 ~ 33.3%, Đuôi 2/3 ~ 66.7%)
        ideal_head_ms = int(round(needed_cut_ms * 1.0 / 3.0))
        ideal_tail_ms = needed_cut_ms - ideal_head_ms

        # Khóa trần an toàn (Đầu max 100ms, Đuôi max 200ms)
        cut_head_ms = min(max_head_ms, ideal_head_ms)
        cut_tail_ms = min(max_tail_ms, ideal_tail_ms)

        total_cut_ms = cut_head_ms + cut_tail_ms
        projected_dur_sec = max(0.05, raw_dur_sec - (total_cut_ms / 1000.0))
        projected_ratio = projected_dur_sec / target_dur_sec

        return {
            "needs_trim": total_cut_ms > 0,
            "initial_ratio": round(initial_ratio, 3),
            "target_ratio": target_ratio,
            "needed_cut_ms": needed_cut_ms,
            "cut_head_ms": cut_head_ms,
            "cut_tail_ms": cut_tail_ms,
            "total_cut_ms": total_cut_ms,
            "projected_dur_sec": round(projected_dur_sec, 3),
            "projected_ratio": round(projected_ratio, 3)
        }

    @classmethod
    def trim_audio_segment(
        cls,
        audio_seg: AudioSegment,
        target_dur_sec: float,
        target_ratio: float = 1.25,
        max_head_ms: int = 100,
        max_tail_ms: int = 200
    ) -> Tuple[AudioSegment, Dict[str, Any]]:
        """
        Thực hiện gọt âm thanh thích ứng theo tỷ lệ 1:2.
        
        Args:
            audio_seg: Đoạn âm thanh pydub.AudioSegment
            target_dur_sec: Thời lượng câu gốc cho phép (giây)
            target_ratio: Ngưỡng tỷ lệ mục tiêu (mặc định 1.25)
            max_head_ms: Giới hạn trần cắt đầu (mặc định 100ms)
            max_tail_ms: Giới hạn trần cắt đuôi (mặc định 200ms)
            
        Returns:
            Tuple[AudioSegment, Dict[str, Any]]: (audio đã gọt, thông số gọt chi tiết)
        """
        if not audio_seg or len(audio_seg) == 0:
            return audio_seg, {"needs_trim": False, "trimmed": False}

        raw_dur_sec = len(audio_seg) / 1000.0
        params = cls.calculate_trim_params(
            raw_dur_sec=raw_dur_sec,
            target_dur_sec=target_dur_sec,
            target_ratio=target_ratio,
            max_head_ms=max_head_ms,
            max_tail_ms=max_tail_ms
        )

        if not params["needs_trim"]:
            params["trimmed"] = False
            return audio_seg, params

        cut_head = params["cut_head_ms"]
        cut_tail = params["cut_tail_ms"]
        total_len = len(audio_seg)

        # Chốt phòng thủ an toàn: Phải còn ít nhất 120ms âm thanh thực tế
        if (cut_head + cut_tail) >= (total_len - 120):
            # Nếu câu quá ngắn, thu nhỏ tỷ lệ gọt tương ứng
            max_allowable_cut = max(0, total_len - 120)
            if max_allowable_cut <= 0:
                params["trimmed"] = False
                params["cut_head_ms"] = 0
                params["cut_tail_ms"] = 0
                return audio_seg, params
            scale = max_allowable_cut / float(cut_head + cut_tail)
            cut_head = int(cut_head * scale)
            cut_tail = max_allowable_cut - cut_head
            params["cut_head_ms"] = cut_head
            params["cut_tail_ms"] = cut_tail

        end_pos = max(cut_head + 50, total_len - cut_tail)
        trimmed_seg = audio_seg[cut_head:end_pos]
        
        actual_new_dur = len(trimmed_seg) / 1000.0
        params["trimmed"] = True
        params["actual_dur_sec"] = round(actual_new_dur, 3)
        params["actual_ratio"] = round(actual_new_dur / target_dur_sec, 3) if target_dur_sec > 0 else 1.0

        return trimmed_seg, params

    @classmethod
    def log_trim_to_terminal(
        cls,
        sentence_index: int,
        raw_dur_sec: float,
        target_dur_sec: float,
        trim_meta: Dict[str, Any]
    ) -> None:
        """
        In chi tiết kết quả gọt thích ứng 2:3 trực tiếp lên Terminal để người dùng theo dõi.
        """
        init_r = trim_meta.get("initial_ratio", round(raw_dur_sec / target_dur_sec, 2) if target_dur_sec > 0 else 1.0)
        c_head = trim_meta.get("cut_head_ms", 0)
        c_tail = trim_meta.get("cut_tail_ms", 0)
        c_tot = trim_meta.get("total_cut_ms", c_head + c_tail)
        new_r = trim_meta.get("actual_ratio", trim_meta.get("projected_ratio", 1.25))

        msg = (
            f"[TRIM 1:2] Cau #{sentence_index:03d}: "
            f"Ty le {init_r:.2f}x ({raw_dur_sec:.2f}s/{target_dur_sec:.2f}s) -> "
            f"Cat Dau: {c_head}ms | Duoi: {c_tail}ms (Tong {c_tot}ms) -> "
            f"Ty le moi: {new_r:.2f}x"
        )
        try:
            print(msg, flush=True)
        except Exception:
            pass
