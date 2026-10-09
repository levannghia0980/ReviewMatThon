# ANTIGRAVITY MANDATORY RULE: PHÂN TÍCH VÀ HỎI Ý KIẾN TRƯỚC KHI SỬA CODE

## 1. NGUYÊN TẮC BẮT BUỘC (TUYỆT ĐỐI TUÂN THỦ 100%)
Với BẤT CỨ câu hỏi, phản ánh lỗi, hoặc yêu cầu tính năng nào từ người dùng, áp dụng cho TẤT CẢ các mô hình AI:

1. **BƯỚC 1 - PHÂN TÍCH & ĐỀ XUẤT PHƯƠNG ÁN (KHÔNG CHẠM VÀO CODE)**:
   - Đọc, kiểm tra và phân tích cặn kẽ nguyên nhân gốc rễ của vấn đề.
   - Trình bày giải thích rõ ràng, súc tích và đề xuất phương án kỹ thuật cụ thể (sẽ sửa file nào, sửa logic gì, vì sao làm như vậy, ưu và nhược điểm).

2. **BƯỚC 2 - HỎI Ý KIẾN VÀ CHỜ NGƯỜI DÙNG PHÊ DUYỆT**:
   - DỪNG LẠI và hỏi rõ người dùng: **"Bạn có đồng ý với phương án phân tích và kế hoạch sửa đổi này không?"**.
   - Cung cấp các phương án lựa chọn (nếu có) để người dùng quyết định.

3. **BƯỚC 3 - TUYỆT ĐỐI CẤM SỬA CODE TRƯỚC KHI ĐƯỢC ĐỒNG Ý**:
   - **CẤM TUYỆT ĐỐI** việc gọi các công cụ sửa file (`replace_file_content`, `write_to_file`, `multi_replace_file_content`...) để sửa code khi người dùng CHƯA bấm đồng ý / chưa gửi phản hồi chấp thuận phương án!
   - Kể cả khi thấy giải pháp rất hiển nhiên, VẪN BẮT BUỘC phải hỏi ý kiến trước. Người dùng chấp nhận tốn thêm token cho bước trao đổi này.

---

## 2. LƯU Ý PHẦN CỨNG & RENDER VIDEO THÍCH ỨNG (KHI CHUYỂN MÁY)
Khi chuyển mã nguồn dự án này sang máy tính khác (hoặc khi Agent phân tích hiệu năng render):
- **Nguyên tắc Render**: Mọi video từ **> 60s** đều phải kích hoạt chế độ **Render Phân Đoạn Song Song (Parallel Segment Rendering)** trong [`app/services/video/video_composer_service.py`](file:///d:/NENGHIA0980/ReviewMatThon/app/services/video/video_composer_service.py). Tuyệt đối không hardcode chạy đơn luồng cho video dài.
- **Thích ứng bộ mã hóa (Hardware Adaptive)**:
  - Máy hiện tại của User có **Intel GPU (QuickSync - `h264_qsv`)**: Hệ thống tự detect và dùng `workers = 3-4 luồng song song`, mỗi luồng mã hóa phần cứng cực nhanh.
  - Khi chuyển sang máy có **Nvidia GPU**: Code sẽ tự detect `h264_nvenc` và dùng 3-4 luồng.
  - Khi chuyển sang **máy chỉ có CPU (không GPU rời/iGPU yếu)**: Code tự động fallback sang `libx264` preset `ultrafast` và dùng số workers = `max(1, cpu_cores - 1)` để vắt kiệt tất cả nhân CPU mà không làm đơ máy.
  - Người dùng có thể chủ động ép số luồng bằng biến môi trường `VIDEO_RENDER_WORKERS` (ví dụ: `VIDEO_RENDER_WORKERS=6`).
- **Phân đoạn video thông minh**:
  - Video ngắn (< 5p): Tự chia nhỏ thành `total_duration / workers` (~60s/đoạn) để tất cả các luồng render đồng thời.
  - Video vừa (5p - 30p): Chia mỗi đoạn ~ 150s.
  - Video dài (1h - 15h): Chia mỗi đoạn ~ 300s (5 phút), sau đó ghép lại bằng FFmpeg Concat Demuxer không re-encode.

