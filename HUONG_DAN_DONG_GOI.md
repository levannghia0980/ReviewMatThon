# 📦 HƯỚNG DẪN ĐÓNG GÓI & CHUYỂN GIAO STUDIO REVIEW MẮT THẦN

Tài liệu này tổng hợp toàn bộ các lưu ý quan trọng nhất khi bạn nén file (.zip / .rar) gửi cho người khác sử dụng trên máy tính mới (kể cả máy chưa có Python hoặc không biết cấu hình GPU).

---

## 1. Danh Sách Những Thứ TUYỆT ĐỐI KHÔNG NÉN VÀO FILE ZIP

Khi chuẩn bị nén thư mục, hãy kiểm tra và **loại bỏ các thư mục/file sau** để file zip nhẹ, sạch và không bị lỗi đường dẫn:

| Tên thư mục / File | Lý do không nén |
| :--- | :--- |
| **`venv/`** | ⚠️ **Cực kỳ quan trọng:** Thư mục môi trường ảo nặng từ 3 - 5 GB và **chứa đường dẫn tuyệt đối (hardcoded path)** của máy bạn (`D:\NENGHIA0980\...`). Nếu nén gửi sang máy khác, `venv` sẽ bị hỏng do không khớp đường dẫn. Người nhận chỉ cần chạy `1_CAI_DAT_HE_THONG.bat` là máy tự tạo `venv` mới chuẩn 100%. |
| **`__pycache__/`** | Các file biên dịch nháp (`.pyc`) của Python, không cần nén để tránh xung đột bytecode giữa các phiên bản. |
| **`input/` và `output/`** | Đã được dọn sạch về 0 MB. Hãy để các thư mục này rỗng để người nhận tự thêm video mới của họ vào. |
| **`scratch/`** | Thư mục chứa các file nháp / benchmark lúc phát triển. |

---

## 2. Lưu Ý Quan Trọng Về File Cấu Hình `.env` (API Keys)

Trong file `.env` có các cấu hình và API Key:
- `GEMINI_API_KEY=AIzaSy...`
- `OPENROUTER_API_KEY=...`

👉 **Lựa chọn khi gửi:**
1. **Gửi cho người thân / nội bộ dùng chung:** Có thể giữ nguyên file `.env` để người nhận mở lên là dùng được ngay mà không cần lấy key.
2. **Gửi cho khách hàng / người ngoài:** Hãy mở file `.env` và xóa chuỗi key đi (để `GEMINI_API_KEY=` trống). Người nhận khi mở giao diện web lên chỉ cần vào tab **Cài Đặt** và dán key của họ vào.

---

## 3. Các Thành Phần BẮT BUỘC Phải Có Trong Gói ZIP

Hãy đảm bảo gói gửi đi có đầy đủ các file sau:
- [x] **`1_CAI_DAT_HE_THONG.bat`**: File cài đặt tự động 1-click cho máy mới (tự tải Python 3.11, nhận diện GPU NVIDIA, cài PyTorch, tạo venv, tạo shortcut).
- [x] **`2_KHOI_DONG.bat`**: File chạy ứng dụng 1-click (tự mở trình duyệt web `http://127.0.0.1:8686`).
- [x] **`TAO_SHORTCUT_DESKTOP.bat`**: File công cụ tạo lại icon ngoài Desktop khi cần.
- [x] **`requirements.txt`**: Danh mục toàn bộ thư viện phụ thuộc.
- [x] **`run.py`**: Trình điều phối khởi động server và UI.
- [x] **`app/`**: Toàn bộ mã nguồn backend, AI pipeline, unblock, TTS, video processing.
- [x] **`frontend/dist/`**: ⚠️ **BẮT BUỘC GIỮ:** Đây là bản build Production đã đóng gói sẵn của giao diện React. Nhờ có thư mục này, **máy người nhận KHÔNG CẦN cài đặt Node.js hay npm gì cả!**
- [x] **`database/`**: Chứa file CSDL SQLite sạch.

---

## 4. Hướng Dẫn Dành Cho Người Nhận (Chỉ Gồm 3 Bước Đơn Giản)

Bạn có thể copy đoạn hướng dẫn ngắn gọn này gửi kèm cho người nhận:

```text
HƯỚNG DẪN SỬ DỤNG STUDIO REVIEW MẮT THẦN TRÊN MÁY MỚI:

Bước 1: Giải nén file zip ra ổ đĩa máy tính (Ví dụ: D:\ReviewMatThon hoặc C:\ReviewMatThon).
Bước 2: Nhấn đúp chuột vào file "1_CAI_DAT_HE_THONG.bat".
        -> Hệ thống sẽ tự động làm hết từ A-Z (tải Python, nhận diện card đồ họa GPU, 
           cài đặt thư viện, tạo icon ngoài Desktop). Quá trình mất khoảng 2 - 3 phút.
Bước 3: Nhấn vào biểu tượng "Review Mắt Thần" ngoài màn hình Desktop (hoặc chạy "2_KHOI_DONG.bat")
        để mở Studio trên trình duyệt web và bắt đầu làm video!

* Lưu ý: Khi muốn tắt chương trình, bạn chỉ cần đóng cửa sổ dòng lệnh (terminal) lại là xong.
```
