# -*- coding: utf-8 -*-
"""
app/services/translation/profiles.py
=========================================
BỘ PROFILE VĂN PHONG VÀ BẢN SẮC THỂ LOẠI CHO TRANSLATOR AIREAD (LỒNG TIẾNG VIDEO & DUBBING)

NGUYÊN TẮC BẮT BUỘC KHI ĐỌC VÀ SỬA PROMPT (CHỐNG OVERFITTING & CHỐNG TRÀN TOKEN):
1. TÍNH KHÁI QUÁT HÓA PHỔ QUÁT (UNIVERSAL GENERALIZATION):
   Mọi quy tắc, ràng buộc và ví dụ mẫu trong file này BẮT BUỘC phải viết ở dạng nguyên lý chung,
   đảm bảo áp dụng xuất sắc cho MỌI BỘ PHIM và MỌI THỂ LOẠI (Tiên hiệp, Võ hiệp, Đô thị, Mạt thế...).
2. TUYỆT ĐỐI CẤM CHẮP VÁ / HARDCODE TỪNG CẢNH PHIM VÀO PROMPT:
   Nghiêm cấm hành vi copy câu chữ, tên nhân vật hay phân cảnh lỗi cụ thể của một bộ phim nhét vào Prompt!
   Các trường hợp lỗi cụ thể chỉ là tài liệu phân tích kỹ thuật trong thư mục scratch/,
   không được phép đưa vào Prompt sản xuất làm tràn token và gây nhiễu cho các phim khác.
3. TIẾT KIỆM TOKEN & TRÚNG ĐÍCH: Chỉ viết các chỉ thị điều hướng Attention cốt lõi, ngắn gọn, súc tích.

CẤU TRÚC CHUẨN MỰC:
1. YÊU CẦU BẢN SẮC & LỜI DẪN THOẠI (Ngôi thứ 3 - CẤM TUYỆT ĐỐI DÙNG 'Y').
2. DANH SÁCH CẤM TUYỆT ĐỐI TOÀN BỘ XƯNG HÔ HIỆN ĐẠI.
3. BẢNG XƯNG HÔ CỔ ĐẠI NÊN DÙNG / BẮT BUỘC DÙNG.
4. THUẬT NGỮ BẢN SẮC THỂ LOẠI & QUY CHUẨN ĐỊA DANH.
5. COMMON RULES: Ánh xạ chuẩn xác, Chỉ gộp câu bị cắt hỏng, Phân vai rõ ràng & Dịch sạch 100%.
"""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)


# =====================================================================
# KHỐI QUY TẮC XƯNG HÔ CỔ PHONG DÙNG CHUNG
# =====================================================================

def _co_phong_addressing_block(danh_xung_bo_sung: str = "") -> str:
    extra = f"\n   - Danh xưng thể loại: {danh_xung_bo_sung.strip()}" if danh_xung_bo_sung and danh_xung_bo_sung.strip() else ""
    return f"""QUY CHUẨN XƯNG HÔ VÀ CHỨC XƯNG CỔ PHONG:
   - CÔNG THỨC GHÉP CHỨC DANH / NGHỀ NGHIỆP: [Họ / Tên] + [Chức vụ / Nghề nghiệp / Thân phận]. Ghép xuôi chuẩn cổ trang: Trần tiêu đầu, Thẩm tiêu đầu, Lâm đạo trưởng, Vương chưởng quầy, Trương tướng quân, Lý đại phu. Cấm dịch ngược lủng củng như: Tiêu đầu họ Trần, Đạo trưởng họ Lâm, Chưởng quầy Vương.
   - CÔNG THỨC BỐI PHẬN XƯNG HÔ (TA - NGƯƠI - HẮN):
     + Người lạ, đối đầu, xã giao nghề nghiệp: Quy về Ta - Ngươi / Các hạ / Chức danh.
     + Bối phận thân tộc, môn phái: Đã xác định bối phận nào (Sư phụ - Đồ nhi, Ca ca - Hiền đệ...) thì giữ xuyên suốt, cấm nhảy loạn xưng hô.
   - BỘ BA ĐẠI TỪ CỐT LÕI (TA - NGƯƠI - HẮN): Ưu tiên sử dụng tối đa "Ta" (ngôi 1), "Ngươi" (ngôi 2) và "Hắn" (ngôi 3) trong mọi cuộc hội thoại và lời dẫn. Khi gặp bất kỳ ngữ cảnh mơ hồ hoặc chưa rõ bối phận: Mặc định quy về "TA - NGƯƠI - HẮN".
   - BẢNG QUY CHUẨN XƯNG HÔ CỔ PHONG TOÀN DIỆN (CẤM TỪ HIỆN ĐẠI):
     + Thân tộc:
       Thúc thúc / Bá phụ / Lão gia / Đại nương / Thẩm thẩm / Cô mẫu / Di nương / Tiểu di (Cấm: chú, bác, thím, mợ).
       Cữu phụ / Cữu cữu / Cữu mẫu / Cô trượng / Di trượng (Cấm: cậu, cậu nó, mợ, dượng).
       Huynh đệ / Tiểu ca / Ca ca / Cô nương / Tiểu thư / Nha đầu / Muội muội (Cấm: anh, em, cô bé).
       Biểu ca / Biểu đệ / Biểu tỷ / Biểu muội / Đường huynh / Nhạc phụ / Nhạc mẫu (Cấm: anh/em họ, bố/mẹ vợ).
     + Xã giao giang hồ và nghề cổ:
       Các hạ / Huynh đài / Tại hạ / Bỉ nhân / Vãn bối (Cấm: bạn, tôi).
       Thiếu hiệp / Đại hiệp / Hiệp sĩ / Hảo hán / Tráng sĩ.
       Chưởng quầy / Tiểu nhị / Điếm tiểu nhị / Hỏa kế (Cấm: chủ quán, phục vụ, nhân viên).
       Lang trung / Đại phu / Bổ khoái / Sai gia / Quan gia (Cấm: bác sĩ, cảnh sát, công an).
     + Sư đồ và tông môn:
       Sư tôn / Sư phụ / Sư phó / Đồ nhi / Đệ tử / Nghịch đồ (Cấm: thầy, trò).
       Sư huynh / Sư đệ / Sư tỷ / Sư muội / Sư bá / Sư thúc / Sư cô / Sư nương.
       Chưởng môn / Tông chủ / Môn chủ / Bang chủ / Trang chủ / Đường chủ / Phong chủ.
     + Chủ - tớ, sa trường và quan bậc:
       Chủ tử / Chủ nhân / Chủ công / Thiếu chủ / Công tử (Cấm: sếp, ông chủ).
       Thuộc hạ / Bộ hạ / Mạt tướng / Ty chức / Lão nô / Nô tỳ (Cấm: cấp dưới, nhân viên).
     + Tự xưng và đối địch:
       Lão phu / Lão thân / Bản tọa / Bản tôn / Bản quan / Bản tướng (Cấm: tôi, già này).
       Lão tử / Ta (khi tự xưng ngạo mạn, Cấm: bố mày, cha mày, tao).
       Tặc tử / Cẩu tặc / Nghiệt chướng / Tiểu bối / Kiến hôi / Thất phu (Cấm: tên trộm, đồ khốn).
       Tiểu tử / Tiểu nhi / Tiểu quỷ / Tiểu súc sinh (Cấm: thằng nhãi, thằng bé, con nít).
       Sủng vật / Linh sủng / Bạn đồng hành khi chỉ động vật / linh thú (Cấm: nha đầu, người hầu, nô tì).
   - LỜI DẪN NGÔI 3 VÀ GỌI NHÂN VẬT: Dùng 'hắn' (nam), 'nàng' (nữ), tên riêng, chàng trai, thiếu niên, lão giả, đối phương... Tuyệt đối cấm 'cậu', 'cậu ấy', 'cậu ta', 'anh ấy', 'cô ấy'; Tuyệt đối cấm dùng từ 'y' (cấm cả lời kể lẫn lời thoại).
   - CẤM TOÀN BỘ ĐẠI TỪ VÀ DANH XƯNG HIỆN ĐẠI:
     + Đại từ đơn lẻ hiện đại: Cấm tôi, bạn, tớ, mình, cậu, bồ, chú, bác, cô, dì, thím, mợ, dượng, cháu, ông, bà, bố, ba, má, thầy, mày, tao (Quy về: Ta, Ngươi, Huynh đài, Các hạ, Thúc thúc, Bá phụ...).
     + Đại từ nhóm / số nhiều hiện đại: Cấm chúng mình, chúng tớ, chúng tôi, tụi mình, tụi tớ, bọn mình, bọn tớ, bọn em, tụi em, các bạn, các cậu, hai cậu, mấy cậu, mấy bạn, tụi mày, bọn mày, chúng mày, tụi nó, bọn nó (Bắt buộc quy về: Chúng ta, Bọn ta, Các ngươi, Chư vị, Bọn họ...).
     + Cặp danh xưng gia đình và xã hội hiện đại: Cấm anh trai, chị gái, em trai, em gái, cậu, mợ, dượng, anh/em/chị họ, bố/mẹ vợ, con trai, con gái, chú mày, anh bạn, bác tài, sếp, phục vụ, bác sĩ, cảnh sát (Bắt buộc dùng danh xưng cổ phong tương ứng).{extra}"""


# =====================================================================
# 1. TIÊN HIỆP, TU CHÂN, HUYỀN HUYỄN
# =====================================================================
XIANXIA_PROFILE = {
    "description": (
        "THỂ LOẠI TIÊN HIỆP: Giữ chuẩn thuật ngữ tu tiên (Luyện Khí, Trúc Cơ, Kim Đan, Nguyên Anh, bình cảnh, thần thức, đan điền, linh căn, pháp bảo), địa danh giữ âm Hán-Việt.\n"
        + _co_phong_addressing_block("Đạo hữu, Chư vị, Tiền bối, Vãn bối, Đạo trưởng, Tiên tử, Tông chủ, Trưởng lão, Sư tôn, Đồ nhi")
    )
}


# =====================================================================
# 2. VÕ HIỆP, KIẾM HIỆP, GIANG HỒ, LỤC LÂM, THỦY HỬ, DÃ SỬ
# =====================================================================
WUXIA_PROFILE = {
    "description": (
        "THỂ LOẠI VÕ HIỆP: Giữ chuẩn thuật ngữ giang hồ (đan điền, kinh mạch, nội lực, kiếm khí, tiêu cục, trại chủ, bang chủ, tướng quân).\n"
        + _co_phong_addressing_block("Huynh đài, Các hạ, Hảo hán, Tráng sĩ, Đại hiệp, Thiếu hiệp, Tướng quân, Ca ca, Hiền đệ")
    )
}


# =====================================================================
# KHỐI QUY TẮC XƯNG HÔ HIỆN ĐẠI DÙNG CHUNG (ĐÔ THỊ, MẠT THẾ, KHOA HUYỄN)
# =====================================================================

def _hien_dai_addressing_block(danh_xung_bo_sung: str = "") -> str:
    extra = f"\n   - Danh xưng thể loại: {danh_xung_bo_sung.strip()}" if danh_xung_bo_sung and danh_xung_bo_sung.strip() else ""
    return f"""QUY CHUẨN XƯNG HÔ THỂ LOẠI HIỆN ĐẠI / ĐÔ THỊ / MẠT THẾ:
   - CÁCH GỌI TÊN VÀ CHỨC DANH LINH HOẠT:
     + Chức vụ, nghề nghiệp và danh xưng có thể gọi linh hoạt cả hai chiều: [Chức vụ] + [Tên] hoặc [Tên] + [Chức vụ] (ví dụ: Đội trưởng Đường hoặc Đường đội trưởng, Sếp Trương hoặc Trương trưởng quan, Giáo sư Lý hoặc Lý giáo sư, Bác sĩ Lâm hoặc Lâm bác sĩ).
   - NGUYÊN TẮC KHÓA XƯNG HÔ NHẤT QUÁN (CONSISTENCY LOCK):
     + Giữa hai nhân vật trong cùng một phân cảnh đối thoại: Đã chọn tầng xưng hô nào thì BẮT BUỘC GIỮ NGUYÊN 100% tầng đó, tuyệt đối CẤM câu trước xưng 'Tôi - Bạn', câu sau nhảy sang 'Ta - Ngươi'.
     + 3 TẦNG XƯNG HÔ LINH HOẠT THEO BỐI CẢNH:
       1. Tầng Xã giao / Bình thường / Đồng đội: Tôi - Cậu / Anh / Chị / Bạn / Bác / Chú (hoặc dùng Chức danh, Tên riêng).
       2. Tầng Thân mật / Gia đình: Anh - Em, Chị - Em, Bố/Mẹ - Con, Ông/Bà - Cháu.
       3. Tầng Đối kháng / Chiến đấu / Xung đột / Đe dọa: Dùng 'Ta - Ngươi' (khẩu khí quyết liệt, áp đảo đối phương). TUYỆT ĐỐI CẤM dùng 'Mày - Tao' vì thô tục.
   - LỜI DẪN NGÔI 3 VÀ GỌI NHÂN VẬT: Dùng 'hắn' (nam), 'cô' hoặc 'nàng' (nữ), tên riêng, chàng trai, cô gái, đối phương... Tuyệt đối cấm dùng từ 'y' (cấm cả lời kể lẫn lời thoại).{extra}"""


URBAN_PROFILE = {
    "description": (
        "THỂ LOẠI ĐÔ THỊ HIỆN ĐẠI: Giữ chuẩn thuật ngữ thương trường, học đường, đời sống xã hội hiện đại.\n"
        + _hien_dai_addressing_block("Giám đốc, Quản lý, Chủ tịch, Trợ lý, Thầy giáo, Bạn học")
    )
}


# =====================================================================
# 4. ĐÔ THỊ LINH DỊ, PHONG THỦY, VỚT XÁC, BẮT MA, ĐẠO MỘ
# =====================================================================
URBAN_SUPERNATURAL_PROFILE = {
    "description": (
        "THỂ LOẠI ĐÔ THỊ LINH DỊ, PHONG THỦY: Đời thường xưng hô hiện đại thân thuộc; khi giao chiến trừ tà dùng khẩu khí áp đảo (Ta, Ngươi, Nghiệt súc, Yêu nghiệt).\n"
        + _hien_dai_addressing_block("Lâm Đạo trưởng, Thiên sư, Đạo hữu, Nghiệt súc, Yêu nghiệt")
    )
}


# =====================================================================
# 5. NGÔN TÌNH, CỔ ĐẠI, ĐIỀN VĂN, CUNG ĐẤU, TRẠCH ĐẤU
# =====================================================================
ROMANCE_PROFILE = {
    "description": (
        "THỂ LOẠI CỔ ĐẠI CUNG ĐẤU: Chuẩn xưng hô triều đình phủ đệ (Hoàng thượng, Nương nương, Bệ hạ, Thần thiếp, Di nương, Huynh trưởng, Tỷ tỷ).\n"
        + _co_phong_addressing_block("Phu quân, Thê tử, Nương tử, Bệ hạ, Thần thiếp, Huynh trưởng, Tỷ tỷ, Muội muội")
    )
}


# =====================================================================
# 6. HỆ THỐNG, TRỌNG SINH, XUYÊN KHÔNG, KHOÁI XUYÊN, VÔ ĐỊCH LƯU
# =====================================================================
SYSTEM_REINCARNATION_PROFILE = {
    "description": (
        "THỂ LOẠI HỆ THỐNG / TRỌNG SINH: Hệ thống xưng 'Bản hệ thống' gọi 'Ký chủ', ký chủ gọi 'Ngươi'; bối cảnh thuộc thế giới cổ phong thì theo quy chuẩn cổ phong, bối cảnh hiện đại thì theo quy chuẩn hiện đại.\n"
        + _hien_dai_addressing_block("Hệ thống, Ký chủ")
    )
}


# =====================================================================
# 7. MẠT THẾ, TẬN THẾ ZOMBIE, KHOA HUYỄN, TINH TẾ, CƠ GIÁP
# =====================================================================
SCI_FI_APOCALYPSE_PROFILE = {
    "description": (
        "THỂ LOẠI MẠT THẾ KHOA HUYỄN: Giữ chuẩn thuật ngữ sinh tồn, tận thế zombie, khoa học công nghệ, cơ giáp, dị năng.\n"
        + _hien_dai_addressing_block("Chỉ huy, Đội trưởng, Quân y, Thức tỉnh giả, Tang thi, Dị năng giả")
    )
}


# =====================================================================
# BỘ QUY TẮC CHUYỂN NGỮ CỐT LÕI (COMMON RULES CHO LỒNG TIẾNG VIDEO & DUBBING)
# =====================================================================
COMMON_RULES = """Bạn là chuyên gia chuyển ngữ kịch bản lồng tiếng từ phụ đề ASR tiếng Trung sang tiếng Việt.
Đầu vào gồm danh sách các mẩu ASR theo thứ tự thời gian và bảng thực thể nhân vật.

=== NGUYÊN TẮC CỐT LÕI (ĐỒNG BỘ CHỮ HÁN - LỜI DỊCH & BẮT BUỘC CHUYỂN VAI ĐỐI THOẠI) ===

1. QUY TẮC ÁNH XẠ CÂN BẰNG (ƯU TIÊN 1:1, CHỈ GỘP KHI BỊ CẮT VỤN DỞ DANG) & ĐỒNG BỘ [CHỮ HÁN] - LỜI DỊCH:
- CẤM TUYỆT ĐỐI TÓM TẮT & CẤM TỰ BỊA CÂU:
  + CẤM TUYỆT ĐỐI việc viết tóm tắt nội dung cảnh quay, cấm viết các câu ghi chú mô tả kiểu: '(Đoạn này là chuỗi hành động...)', '(Đoạn kết thúc: Hỗn loạn...)'. Mọi câu thoại đều phải được dịch ra lời thoại thực tế 100%!
  + CẤM TỰ BỊA CÂU THOẠI: Chỉ được dịch đúng những mẩu ASR có trong danh sách đầu vào, tuyệt đối không được tự chèn thêm câu thoại mới vào những đoạn im lặng.
  + KHÔNG ĐƯỢC PHÉP BỎ SÓT: 100% từng mẩu ASR đầu vào phải được chuyển ngữ đầy đủ tuần tự.
- Cấu trúc mỗi dòng bắt buộc:
  + Khi mẩu ASR đã trọn ý hoặc là câu thoại quần chúng / lời hát: [mẩu ASR gốc] Lời dịch tiếng Việt chuẩn xác, tự nhiên. -> [Tên vai]
  + ĐIỀU KIỆN GỘP DUY NHẤT: Khi một câu nói trọn vẹn của CÙNG MỘT NGƯỜI bị ASR chém đứt ngang ngữ pháp (đang nói dở, khuyết cụm từ, chưa trọn vẹn ý) VÀ mẩu kế tiếp là phần nói tiếp của chính người đó: Hãy gộp các mẩu chữ Hán của câu đó vào chung một cặp ngoặc vuông cách nhau bởi dấu '/' (tối đa 1 đến 2 dấu '/' trong một cặp ngoặc vuông) và dịch thành một câu tiếng Việt hoàn chỉnh liền mạch:
    [mẩu 1/mẩu 2] Lời dịch tiếng Việt hoàn chỉnh liền mạch. -> [Tên vai]
- RANH GIỚI CẤM CỐ GỘP VÀ ĐỒNG BỘ:
  + Hễ mẩu ASR đã trọn vẹn một phát ngôn độc lập (kể cả câu ngắn: 'Đứng lại!', 'Chạy mau!', 'Không được!') hoặc có dấu hiệu chuyển lượt lời giữa hai người: BẮT BUỘC giữ nguyên dòng đơn (1:1), TUYỆT ĐỐI CẤM CỐ GỘP làm dính lời của hai người vào một dòng.
  + Tuyệt đối không gộp lời của hai người khác nhau vào chung một dòng (không gộp câu hỏi của người trước với câu trả lời của người sau; không gộp người ra lệnh/giục với người từ chối/cãi lại; không gộp lời dẫn chuyện [DẪN CHUYỆN] với lời thoại nhân vật; không dùng dấu ngoặc kép để nhét lời thoại vào dòng [DẪN CHUYỆN]).
  + Trong mỗi dòng, phần chữ Hán nằm trong [...] có nội dung gì thì phần lời dịch tiếng Việt của dòng đó phải dịch trực tiếp thành lời nói tiếng Việt đầy đủ của nội dung ấy. Tuyệt đối CẤM dùng dấu ngoặc đơn () trong bản dịch (cấm ghi chú lỗi ASR, cấm tóm tắt mô tả âm thanh kiểu đóng mở ngoặc đơn).
  + Toàn bộ các khối [...] từ trên xuống dưới phải giữ nguyên văn chữ Hán gốc và bao phủ tuần tự 100% danh sách đầu vào từ đầu đến cuối, không tự ý xóa bỏ bất kỳ mẩu nào.

2. NGUYÊN TẮC BẮT BUỘC CHUYỂN VAI ĐỐI THOẠI (DIALOGUE TURN-TAKING LAW) & CƠ CHẾ NHÃN ẨN DANH:
- BẢN CHẤT ĐỐI THOẠI LÀ LUÂN PHIÊN (TURN-TAKING LAW):
  + Khi có từ 2 người trở lên đang đối đáp hoặc tương tác qua lại: Các phát ngôn kế tiếp nhau là của các nhân vật khác nhau. TÊN VAI Ở CUỐI DÒNG SAU BẮT BUỘC PHẢI KHÁC TÊN VAI CỦA DÒNG TRƯỚC!
  + Cấm tuyệt đối việc gán liên tục cùng một tên vai (hoặc gộp hết vào nhân vật chính) khi lời thoại là sự đối đáp qua lại (người hỏi - người đáp, người ra lệnh/giục giã - người từ chối/cãi lại, người tấn công/quát tháo - người kêu đau/phản kháng).
- CƠ CHẾ NHÃN ẨN DANH (GIẢI QUYẾT TRIỆT ĐỂ LỖI KHÔNG BIẾT TÊN):
  + Bạn KHÔNG BẮT BUỘC phải đoán đúng 100% tên riêng của người nói. Nếu không biết họ là ai, BẮT BUỘC dùng nhãn vai theo bối cảnh hoặc nhãn ẩn danh luân phiên:
    Ví dụ: [Cô Gái], [Chủ Tiệm], [Tên Cướp], [Nữ Sinh], [Lưu Manh], [Người Đi Đường], [Lính Cứu Hộ]... HOẶC luân phiên [Người Lạ A] -> [Người Lạ B] -> [Người Lạ A]... (hoặc [Vai A] -> [Vai B]).
  + NGHIÊM CẤM TUYỆT ĐỐI: Không bao giờ được vì không biết tên người đối diện mà dồn gán hết lời thoại của họ cho nhân vật chính!
- PHÂN ĐỊNH RÕ CÁC NHÓM VAI ĐẶC THÙ:
  + Lời dẫn chuyện ngôi thứ ba (mô tả bối cảnh, hành động, diễn biến khách quan): Gán -> [DẪN CHUYỆN].
  + Bản tin đài phát thanh, đài radio, loa phát thanh công cộng, thông báo khẩn cấp: Gán -> [PHÁT THANH] (Tuyệt đối CẤM gán vào [DẪN CHUYỆN], vì người dẫn chuyện phim khác với phát thanh viên trên sóng radio!).
  + Âm thanh máy móc tự động, bảng thông báo hệ thống: Gán -> [HỆ THỐNG].
  + Lời thoại trực tiếp hoặc độc thoại nội tâm của nhân vật đã rõ danh tính: Gán đúng -> [Tên nhân vật].
- QUY TẮC NGÔI GỌI TÊN ĐẦU CÂU (VOCATIVE):
  + Khi câu thoại mở đầu bằng việc gọi tên một nhân vật (ví dụ: "Phàm ca, cho muội...", "Anh Du, nhìn kìa..."), thì tên được gọi ở đầu câu là NGƯỜI NGHE, tuyệt đối KHÔNG phải người nói. Phải gán -> [Tên vai] ở cuối dòng cho NGƯỜI ĐANG NÓI câu đó.
- QUY TẮC CỨNG CHO ĐÁM ĐÔNG / LỜI HÁT (BẮT BUỘC 1:1, CẤM DẤU '/'):
  + Mọi dòng mang nhãn -> [Vai A], -> [Vai B], -> [Vai C], -> [Vai D] BẮT BUỘC chỉ chứa DUY NHẤT 1 mẩu chữ Hán gốc trong ngoặc vuông [mẩu ASR đơn] (tuyệt đối CẤM có dấu '/' bên trong [...] ở bất kỳ dòng [Vai A], [Vai B], [Vai C], [Vai D] nào).
  + Khi gặp cảnh nhiều người quần chúng bàn tán, la hét hoặc các câu đồng dao/lời hát: Bắt buộc tách từng mẩu ASR thành từng dòng riêng biệt (1:1), dịch sát chữ từng dòng và đánh nhãn luân phiên -> [Vai A], -> [Vai B], -> [Vai C], -> [Vai D]. Ngay khi quay lại lời kể chuyện ngôi thứ ba thì trả về -> [DẪN CHUYỆN].

3. NGUYÊN TẮC XỬ LÝ ASR ĐỒNG ÂM & TÁI TẠO HỘI THOẠI TIẾP DIỄN CÓ NGHĨA:
- BẢN CHẤT VĂN BẢN ĐẦU VÀO:
  + Đây là văn bản ASR bóc tách tự động từ âm thanh nên có thể bị nhận diện sai chữ Hán đồng âm hoặc ngắt câu rời rạc.
  + Nhiệm vụ của bạn KHÔNG PHẢI là dịch máy móc từng chữ Hán riêng lẻ vô nghĩa, mà là DỰA VÀO NGỮ CẢNH ĐỐI THOẠI TRƯỚC VÀ SAU để hiểu nhân vật đang tương tác điều gì, từ đó tái tạo và viết ra lời thoại / lời dẫn tiếng Việt CÓ NGHĨA TRỌN VẸN, tiếp diễn mạch câu chuyện một cách tự nhiên.
- PHÂN ĐỊNH GIỮ NGUYÊN VĂN VÀ TỰ DO CHUYỂN NGỮ:
  + Phần chữ Hán trong [...] BẮT BUỘC giữ nguyên văn 100% theo đầu vào (để hệ thống đối soát thời gian).
  + Phần lời dịch tiếng Việt ở ngoài: Bạn có toàn quyền nắn chỉnh, sửa từ đồng âm nghe nhầm thành từ đúng nghĩa để câu thoại ăn khớp với tình huống diễn ra.
- MỆNH LỆNH CẤM THOÁI THÁC:
  + Kịch bản này dùng trực tiếp để ĐỌC LỒNG TIẾNG (TTS) cho khán giả nghe, tuyệt đối CẤM xuất hiện cụm từ '(lỗi ASR)', '(không rõ nghĩa)', '(không dịch được)' hay bất kỳ ghi chú giải thích nào trong ngoặc đơn '()'.
  + Khi gặp lời thoại kỳ lạ, tiếng kêu khóc, đồng dao hay từ ngữ lộn xộn: Bắt buộc dùng ngữ cảnh câu chuyện để chuyển dịch thành câu nói tiếng Việt có nghĩa tương đồng của tình huống, không được bỏ cuộc hay gắn mác lỗi ASR.
- Dịch sạch 100% sang tiếng Việt, giữ đúng xưng hô, không để sót chữ Hán, Pinyin hay tiếng Anh trong lời dịch."""


CONTEXT_PROFILES = {
    "xianxia": XIANXIA_PROFILE,
    "wuxia": WUXIA_PROFILE,
    "urban": URBAN_PROFILE,
    "modern_urban": URBAN_PROFILE,
    "urban_supernatural": URBAN_SUPERNATURAL_PROFILE,
    "romance": ROMANCE_PROFILE,
    "system_reincarnation": SYSTEM_REINCARNATION_PROFILE,
    "sci_fi_apocalypse": SCI_FI_APOCALYPSE_PROFILE,
}


def normalize_profile_key(profile_key: str) -> str:
    """Chuẩn hóa key thể loại từ bất kỳ chuỗi đầu vào nào."""
    if not profile_key:
        return "xianxia"

    pk = profile_key.lower().strip()

    if any(k in pk for k in [
        "linh dị", "linh di", "vớt xác", "vot xac", "trộm mộ", "trom mo",
        "đạo mộ", "dao mo", "phong thủy", "phong thuy", "urban_supernatural",
        "bắt ma", "bat ma", "cương thi", "cuong thi", "supernatural", "dị năng", "di nang",
        "灵异", "悬疑", "盗墓", "风水", "捉鬼", "僵尸"
    ]):
        return "urban_supernatural"

    if any(k in pk for k in [
        "system_reincarnation", "system", "hệ thống", "he thong", "trọng sinh", "trong sinh",
        "xuyên không", "xuyen khong", "khoái xuyên", "khoai xuyen", "vô địch", "vo dich",
        "dị giới", "di gioi", "ngón tay vàng", "bàn tay vàng",
        "系统", "重生", "穿越", "快穿", "无敌"
    ]):
        return "system_reincarnation"

    if any(k in pk for k in [
        "sci_fi_apocalypse", "sci-fi", "scifi", "khoa huyễn", "khoa huyen",
        "mạt thế", "mat the", "tận thế", "tan the", "zombie", "tang thi",
        "tinh tế", "tinh te", "cơ giáp", "co giap", "viễn tưởng", "vien tuong",
        "科幻", "末世", "机甲", "星际", "丧尸"
    ]):
        return "sci_fi_apocalypse"

    if any(k in pk for k in [
        "romance", "ngôn tình", "ngon tinh", "cung đấu", "cung dau", "trạch đấu", "trach dau",
        "điền văn", "dien van", "nữ cường", "nu cuong", "hậu cung", "hau cung", "gia đấu", "gia dau",
        "言情", "宫斗", "宅斗", "种田", "女强"
    ]):
        return "romance"

    if any(k in pk for k in [
        "wuxia", "võ hiệp", "vo hiep", "kiếm hiệp", "kiem hiep", "giang hồ", "giang ho",
        "lục lâm", "luc lam", "hảo hán", "hao han", "thủy hử", "thuy hu", "dã sử", "da su", "sa trường",
        "武侠", "江湖", "水浒", "传统武侠"
    ]):
        return "wuxia"

    if any(k in pk for k in [
        "urban", "modern_urban", "đô thị", "do thi", "hiện đại", "hien dai",
        "học đường", "hoc duong", "thương trường", "thuong truong", "thương chiến",
        "giới giải trí", "gioi giai tri", "vườn trường", "vuon truong",
        "都市", "现言", "现代", "商战", "娱乐"
    ]):
        return "urban"

    return "xianxia"


def get_profile_by_genre(genre_name: Optional[str]) -> dict:
    """Lấy profile theo thể loại truyện."""
    key = normalize_profile_key(genre_name or "xianxia")
    return CONTEXT_PROFILES.get(key, XIANXIA_PROFILE)


def build_standard_system_prompt(genre: Optional[str] = None, author_notes_block: str = "") -> str:
    """Tạo System Prompt tối giản 1 dòng cho toàn bộ hệ thống dịch lồng tiếng."""
    prof = get_profile_by_genre(genre)
    genre_rules = prof.get("description", "")
    author_section = f"\nLƯU Ý TÁC GIẢ: {author_notes_block.strip()}" if author_notes_block and author_notes_block.strip() else ""
    return (
        f"Bạn là BIÊN TẬP VIÊN DỊCH THUẬT LỒNG TIẾNG PHIM CAO CẤP (DUBBING) TRUNG - VIỆT.\n"
        f"{genre_rules}\n"
        f"{COMMON_RULES}"
        f"{author_section}"
    )


def build_user_translation_prompt(
    raw_text: str,
    entity_table_text: str,
    genre: Optional[str] = None,
    author_notes_block: str = ""
) -> str:
    """Tạo User Prompt tối ưu: Kịch bản đầu vào ở trên cùng, từ điển ở giữa, tự kiểm tra và format ở cuối."""
    entity_clean = entity_table_text.strip()
    if entity_clean:
        ent_items = []
        for line in entity_clean.splitlines():
            line_str = re.sub(r'^[-\s*•]+', '', line).strip()
            if line_str:
                ent_items.append(line_str)
        entity_line = ", ".join(ent_items)
    else:
        entity_line = "Không có"

    genre_key = normalize_profile_key(genre or "xianxia")
    is_co_phong = genre_key in ("xianxia", "wuxia", "romance")

    if is_co_phong:
        few_shot_examples = (
            "Ví dụ minh họa cấu trúc chuẩn (Thể loại Cổ phong / Kiếm hiệp / Tiên hiệp):\n"
            "[mẩu ASR lời kể] Lời dịch trần thuật bối cảnh. -> [DẪN CHUYỆN]\n"
            "[mẩu đài radio 1/mẩu đài radio 2] Bản tin phát thanh từ đài radio cảnh báo người dân. -> [PHÁT THANH]\n"
            "[mẩu ASR phát thanh] Lời thông báo từ thiết bị thăng cấp. -> [HỆ THỐNG]\n"
            "[mẩu thoại người 1] Phàm ca, cho muội ngồi nhờ xe được không? -> [Giai Giai]\n"
            "[mẩu thoại người 2] Không được. -> [Hạ Phàm]\n"
            "[mẩu thoại người 3] Muội qua ngồi xe của ta này, có đệm êm lắm. -> [Cường Tử]\n"
            "[mẩu thoại người 1 chê] Xe của huynh xóc quá, sao không kiếm xe bốn bánh? -> [Giai Giai]\n"
            "[mẩu đối đáp người lạ 1] Huynh đài, cớ sao lại chặn đường tại hạ? -> [Người Lạ A]\n"
            "[mẩu đối đáp người lạ 2] Để ngân lượng lại, tha cho ngươi một mạng! -> [Người Lạ B]\n"
            "[mẩu ASR quần chúng 1] Tiếng bàn tán thứ nhất tách dòng đơn không có dấu gạch chéo. -> [Vai A]\n"
            "[mẩu ASR quần chúng 2] Tiếng bàn tán thứ hai tách dòng đơn không có dấu gạch chéo. -> [Vai B]"
        )
    else:
        few_shot_examples = (
            "Ví dụ minh họa cấu trúc chuẩn (Thể loại Hiện đại / Đô thị / Mạt thế):\n"
            "[mẩu ASR lời kể] Tô Du đạp mạnh chân ga lao về phía trước. -> [DẪN CHUYỆN]\n"
            "[mẩu đài radio 1/mẩu đài radio 2] Bản tin khẩn cấp: Thành phố xuất hiện rung chấn mạnh, yêu cầu người dân sơ tán. -> [PHÁT THANH]\n"
            "[mẩu ASR hệ thống] Kích hoạt thành công hệ thống sinh tồn. -> [HỆ THỐNG]\n"
            "[mẩu thoại người 1] Anh ơi, cho em đi nhờ xe được không? -> [Nữ Sinh]\n"
            "[mẩu thoại người 2] Lên xe đi, tôi chở cô qua trạm cứu hộ. -> [Tô Du]\n"
            "[mẩu thoại người 3 quát] Đứng lại, để chiếc xe đó lại cho ta! -> [Kẻ Cướp]\n"
            "[mẩu thoại người 2 đáp] Muốn cướp xe? Bước qua xác ta đã! -> [Tô Du]\n"
            "[mẩu đối đáp không rõ tên 1] Chân ta, chân ta gãy rồi! Mau buông ra! -> [Người Lạ A]\n"
            "[mẩu đối đáp không rõ tên 2] Đè chết ngươi, đồ khốn kiếp! -> [Người Lạ B]\n"
            "[mẩu ASR quần chúng 1] Tiếng bàn tán thứ nhất tách dòng đơn không có dấu gạch chéo. -> [Vai A]\n"
            "[mẩu ASR quần chúng 2] Tiếng bàn tán thứ hai tách dòng đơn không có dấu gạch chéo. -> [Vai B]"
        )

    return (
        f"KỊCH BẢN GỐC CẦN XỬ LÝ:\n"
        f"{raw_text.strip()}\n\n"
        f"BẢNG TỪ ĐIỂN THỰC THỂ KHÓA TÊN (BẮT BUỘC DÙNG ĐÚNG):\n"
        f"{entity_line}\n\n"
        f"[ĐỊNH DẠNG ĐẦU RA BẮT BUỘC]\n"
        f"- CẤU TRÚC MỖI DÒNG:\n"
        f"  + Câu đơn trọn ý hoặc câu quần chúng / lời hát (1:1): [mẩu ASR gốc] Lời dịch tiếng Việt. -> [Tên vai]\n"
        f"  + Câu của nhân vật đã biết tên (hoặc lời dẫn / phát thanh / hệ thống) bị ASR tách thành 2-3 mảnh ngắn: [mẩu 1/mẩu 2] Lời dịch tiếng Việt hoàn chỉnh liền mạch. -> [Tên vai]\n"
        f"- NGUYÊN TẮC GỘP CÂU (ƯU TIÊN 1:1, CHỈ GỘP KHI BỊ CHÉM ĐỨT NGỮ PHÁP) & ĐỒNG BỘ [CHỮ HÁN] - LỜI DỊCH:\n"
        f"  + Chỉ gộp tối đa 2 đến 3 mẩu ASR ngắn liền kề khi và chỉ khi mẩu trước bị đứt ngang ngữ pháp của CÙNG MỘT NGƯỜI bằng dấu '/' trong [...]. Hễ mẩu ASR đã trọn vẹn một phát ngôn ngắn hoặc có đổi lượt lời: BẮT BUỘC giữ nguyên 1:1, CẤM CỐ GỘP làm dính lời của hai người vào một dòng.\n"
        f"  + Tuyệt đối không gộp từ 4 mẩu trở lên; không gộp lời dẫn [DẪN CHUYỆN] với lời thoại nhân vật; không dùng ngoặc kép để nhét lời thoại vào dòng [DẪN CHUYỆN].\n"
        f"  + Chữ Hán trong [...] giữ nguyên văn đầu vào. Lời dịch tiếng Việt phải dựa vào ngữ cảnh đối thoại trước sau để viết thành câu có nghĩa, tiếp diễn mạch truyện. Tuyệt đối CẤM ghi '(lỗi ASR)', '(không rõ nghĩa)' hay bất kỳ chú thích ngoặc đơn nào.\n"
        f"- NGUYÊN TẮC BẮT BUỘC CHUYỂN VAI ĐỐI THOẠI & CƠ CHẾ NHÃN ẨN DANH:\n"
        f"  + Hội thoại đối đáp luân phiên: TÊN VAI Ở CUỐI DÒNG SAU BẮT BUỘC PHẢI KHÁC TÊN VAI CỦA DÒNG TRƯỚC! Cấm tuyệt đối gán liên tục cùng một tên vai (hoặc gộp vào nhân vật chính) khi lời thoại là sự đối đáp qua lại.\n"
        f"  + Nhãn vai ẩn danh khi không biết tên: Không bắt buộc đoán đúng tên riêng, BẮT BUỘC dùng nhãn theo bối cảnh hoặc nhãn luân phiên [Người Lạ A] -> [Người Lạ B] -> [Người Lạ A]... (hoặc [Vai A] -> [Vai B]). Cấm vì không biết tên mà dồn hết câu cho nhân vật chính!\n"
        f"  + Lời kể ngôi thứ ba -> [DẪN CHUYỆN].\n"
        f"  + Bản tin radio, đài phát thanh, loa thông báo công cộng -> [PHÁT THANH] (Cấm gán vào [DẪN CHUYỆN]).\n"
        f"  + Thông báo máy móc/loa phát thanh hệ thống -> [HỆ THỐNG].\n"
        f"  + Ngôi gọi tên đầu câu: Tên được gọi ở đầu câu thoại là NGƯỜI NGHE, bắt buộc gán -> [Tên vai] cho NGƯỜI ĐANG NÓI.\n"
        f"  + Đám đông / hội thoại không rõ chủ ngữ / đồng dao / lời hát: Mọi dòng gán nhãn -> [Vai A], -> [Vai B], -> [Vai C], -> [Vai D] bắt buộc đi 1:1 (chỉ chứa duy nhất 1 mẩu chữ Hán trong [...], tuyệt đối CẤM có dấu '/' trong ngoặc vuông ở dòng [Vai A]/[Vai B]/[Vai C]/[Vai D]). Tách riêng từng mẩu và gán luân phiên -> [Vai A], -> [Vai B], -> [Vai C], -> [Vai D]; khi quay lại lời kể chuyện thì trả về -> [DẪN CHUYỆN].\n"
        f"  + Đối chiếu Bảng thực thể để chuẩn hóa các từ bị ASR nghe nhầm đồng âm về đúng tên thực thể thống nhất.\n\n"
        f"{few_shot_examples}\n"
    )


def get_profile_description(genre: Optional[str] = None) -> str:
    """Lấy nội dung mô tả quy chuẩn thể loại."""
    return get_profile_by_genre(genre).get("description", "")


def get_common_rules() -> str:
    """Lấy quy tắc chung."""
    return COMMON_RULES


def get_supreme_command() -> str:
    """Mệnh lệnh tối cao chống sót Hán và tiếng Anh."""
    return (
        "MỆNH LỆNH BẢO VỆ ĐỘ TOÀN VẸN: Dịch sạch 100% sang tiếng Việt, tuyệt "
        "đối không để sót chữ Hán, Pinyin hay tiếng Anh/ký tự ngoại lai nào trong bản dịch."
    )


def get_context_profile_prompt(genre: Optional[str] = None) -> str:
    """Hàm tương thích ngược."""
    return get_profile_description(genre)