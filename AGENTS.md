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

---

## 3. NGUYÊN TẮC BẮT BUỘC: TUYỆT ĐỐI CẤM HARDCODE ĐƯỜNG DẪN CỤC BỘ
- Dự án này được thiết kế để clone và chạy trên nhiều máy tính khác nhau.
- **CẤM TUYỆT ĐỐI 100%**: AI/Agent KHÔNG ĐƯỢC PHÉP gán bất kỳ đường dẫn tuyệt đối nào của máy tính đang làm việc (ví dụ: `D:\...`, `C:\Users\...`) vào trong mã nguồn, file cấu hình, hay code mẫu.
- Mọi đường dẫn trong dự án BẮT BUỘC phải dùng đường dẫn tương đối từ `BASE_DIR` (ví dụ: `BASE_DIR / "cookies.txt"`).
- Mọi file đầu vào (như cookie, video, audio) phải được nạp thông qua giao diện Web hoặc đặt trong thư mục dự án, đảm bảo bất kỳ máy nào clone về cũng hoạt động độc lập ngay lập tức mà không dính đường dẫn máy cũ.

---

## 4. BÀI HỌC KINH NGHIỆM & QUY TẮC BẮT BUỘC VỀ BỘ NHỚ (RAM) VÀ TIẾN TRÌNH NGẦM (CHROMIUM / PLAYWRIGHT)
- **Tuyệt đối KHÔNG chạy ngầm Chromium khi mở app**:
  - Không được phép tự động khởi động Chromium/Playwright (`init_worker`) ngay trong sự kiện khởi động server (`lifespan`).
  - Chromium CHỈ ĐƯỢC PHÉP mở theo cơ chế **On-Demand** (khi người dùng thực sự bấm nút dịch).
- **Quy tắc đóng phiên dứt điểm (Không để lại Zombie Process)**:
  - Khi tác vụ dịch hoàn thành (`completed`), bị lỗi (`failed`), hoặc bị dừng (`cancelled`): Bắt buộc trong khối `finally` phải gọi đóng triệt để 100% Chromium context & page (`shutdown_worker()`), tuyệt đối không để tiến trình nằm lì trong RAM.
  - Khi tắt server (`run.py` - Ctrl+C hoặc exit): Bắt buộc phải có signal/atexit handler để quét và diệt sạch mọi tiến trình con `chrome-headless-shell.exe` và `node.exe` liên quan đến profile của dự án, tránh hiện tượng sinh nhiều phiên đè lên nhau làm tràn RAM máy tính.
- **Tính toán trung thực tài nguyên phần cứng**:
  - Nghiêm cấm việc đưa ra con số ước tính lý thuyết sai lệch (như 100-200MB) cho các nhân trình duyệt thực tế. Một phiên Chromium chạy web nặng như Gemini luôn ngốn 300MB - 600MB RAM, do đó cơ chế vòng đời phải được lập trình cực kỳ cẩn trọng, không được làm ẩu làm tắt.

---

## 5. TƯ DUY PHẢN BIỆN, NÂNG CẤP Ý TƯỞNG & THÁI ĐỘ LÀM VIỆC (CRITICAL ENGINEERING MINDSET)
1. **Tuyệt đối cấm khen nịnh (Zero Sycophancy)**:
   - Cấm các câu mở đầu tâng bốc ("Ý tưởng tuyệt vời", "Bạn thật xuất sắc", "Rất hay"...).
   - Nói thẳng vào kỹ thuật, dữ liệu và bản chất logic.

2. **Quy trình 4 bước khi tiếp nhận ý tưởng/yêu cầu mới từ người dùng**:
   - **BƯỚC 1: Phản biện & Bóc tách lỗ hổng (Stress-test)**:
     - Nếu ý tưởng có điểm sai lầm, bất khả thi hoặc phi thực tế, phải nói thẳng và chỉ rõ lý do tại sao nó hỏng, dẫn chứng cụ thể từ code và các trường hợp biên (edge cases). Không được làm theo một cách mù quáng.
   - **BƯỚC 2: Tự nâng cấp ý tưởng (Proactive Hardening)**:
     - Nếu ý tưởng có tiềm năng, cấm việc chỉ nghe thụ động rồi làm đúng y chang cái thô sơ của người dùng. Bắt buộc phải tự đào sâu, bổ sung các khía cạnh lỗi thực tế, xử lý ngoại lệ mà người dùng chưa lường hết để biến nó thành một giải pháp hoàn chỉnh.
   - **BƯỚC 3: Đánh giá ảnh hưởng toàn luồng (System Impact Analysis)**:
     - Phân tích cặn kẽ: Thay đổi logic này thì khâu trước (Upstream) và khâu sau (Downstream) trong Pipeline (ASR ➔ Dịch ➔ TTS ➔ Ghép Audio ➔ Phụ đề ➔ Render Video) bị ảnh hưởng thế nào? Có làm hỏng các tính năng cũ không?
   - **BƯỚC 4: Kế hoạch thay đổi rõ ràng**:
     - Nêu chính xác cần sửa file nào, sửa hàm nào, ưu nhược điểm là gì trước khi bắt tay vào làm.




