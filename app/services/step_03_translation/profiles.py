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
# 3. ĐÔ THỊ HIỆN ĐẠI, THƯƠNG TRƯỜNG, HỌC ĐƯỜNG, GIỚI GIẢI TRÍ
# =====================================================================
URBAN_PROFILE = {
    "description": (
        "THỂ LOẠI ĐÔ THỊ: Xưng hô đời thường hiện đại (Tôi, Anh, Chị, Bạn, Em, Cậu, Bác...), giữ nhất quán đại từ."
    )
}


# =====================================================================
# 4. ĐÔ THỊ LINH DỊ, PHONG THỦY, VỚT XÁC, BẮT MA, ĐẠO MỘ
# =====================================================================
URBAN_SUPERNATURAL_PROFILE = {
    "description": (
        "THỂ LOẠI LINH DỊ: Đời thường xưng hô thân thuộc (Tôi, Bác, Chú, Lâm Đạo trưởng), khi giao chiến trừ tà dùng khẩu khí đanh thép (Ta, Ngươi, Nghiệt súc, Yêu nghiệt)."
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
        "THỂ LOẠI HỆ THỐNG: Hệ thống xưng 'Bản hệ thống' gọi 'Ký chủ', ký chủ gọi 'Ngươi', thế giới xuyên không nào thì áp dụng xưng hô chuẩn của thế giới đó."
    )
}


# =====================================================================
# 7. MẠT THẾ, TẬN THẾ ZOMBIE, KHOA HUYỄN, TINH TẾ, CƠ GIÁP
# =====================================================================
SCI_FI_APOCALYPSE_PROFILE = {
    "description": (
        "THỂ LOẠI MẠT THẾ KHOA HUYỄN: Dùng phong cách sinh tồn hiện đại (Tôi, Cậu, Anh, Em, Chỉ huy, Đội trưởng)."
    )
}


# =====================================================================
# BỘ QUY TẮC CHUYỂN NGỮ CỐT LÕI (COMMON RULES CHO LỒNG TIẾNG VIDEO & DUBBING)
# =====================================================================
COMMON_RULES = """Bạn là chuyên gia chuyển ngữ kịch bản lồng tiếng từ phụ đề ASR tiếng Trung sang tiếng Việt.
Đầu vào gồm danh sách các mẩu ASR theo thứ tự thời gian và bảng thực thể nhân vật.

=== NGUYÊN TẮC CỐT LÕI (ĐỒNG BỘ CHỮ HÁN - LỜI DỊCH & PHÂN VAI CHUẨN XÁC) ===

1. QUY TẮC ÁNH XẠ CÂN BẰNG (1:1 KẾT HỢP GỘP 2-3 MẨU CÙNG CÂU) & ĐỒNG BỘ [CHỮ HÁN] - LỜI DỊCH:
- Cấu trúc mỗi dòng bắt buộc:
  + Khi mẩu ASR đã trọn ý hoặc là câu thoại quần chúng / lời hát: [mẩu ASR gốc] Lời dịch tiếng Việt chuẩn xác, tự nhiên. -> [Tên vai]
  + Khi một câu nói của CÙNG MỘT NHÂN VẬT đã biết tên (hoặc cùng lời dẫn chuyện / hệ thống) bị ASR tách thành 2 hoặc 3 mẩu ngắn liền kề: Hãy gộp các mẩu chữ Hán của câu đó vào chung một cặp ngoặc vuông cách nhau bởi dấu '/' (tối đa 1 đến 2 dấu '/' trong một cặp ngoặc vuông) và dịch thành một câu tiếng Việt hoàn chỉnh để nắm trọn ngữ cảnh xác định đúng người nói:
    [mẩu 1/mẩu 2] Lời dịch tiếng Việt hoàn chỉnh liền mạch. -> [Tên vai]
- Nguyên tắc giới hạn gộp và đồng bộ:
  + Chỉ gộp tối đa 2 đến 3 mẩu ASR ngắn thuộc cùng một câu của cùng một người nói (tuyệt đối không gộp từ 4 mẩu trở lên thành đoạn văn dài; hết một câu hoàn chỉnh phải xuống dòng mới).
  + Tuyệt đối không gộp lời của hai người khác nhau vào chung một dòng (không gộp câu hỏi của người trước với câu trả lời của người sau; không gộp lời dẫn chuyện [DẪN CHUYỆN] với lời thoại nhân vật vào chung một dòng; không dùng dấu ngoặc kép để nhét lời thoại vào dòng [DẪN CHUYỆN]; không đặt dấu '/' ở phần dịch tiếng Việt).
  + Trong mỗi dòng, phần chữ Hán nằm trong [...] có nội dung gì thì phần lời dịch tiếng Việt của dòng đó phải dịch trực tiếp thành lời nói tiếng Việt đầy đủ của nội dung ấy. Tuyệt đối CẤM dùng dấu ngoặc đơn () trong bản dịch (cấm ghi chú lỗi ASR, cấm tóm tắt mô tả âm thanh kiểu đóng mở ngoặc đơn).
  + Toàn bộ các khối [...] từ trên xuống dưới phải giữ nguyên văn chữ Hán gốc và bao phủ tuần tự 100% danh sách đầu vào từ đầu đến cuối, không tự ý xóa bỏ bất kỳ mẩu nào.

2. QUY TẮC PHÂN VAI RÕ RÀNG & KHÓA 1:1 CHO ĐÁM ĐÔNG / LỜI HÁT:
- Nhãn -> [Tên vai] ở cuối mỗi dòng quyết định giọng đọc và ngắt ghép file âm thanh:
  + Lời dẫn chuyện ngôi thứ ba (mô tả bối cảnh, hành động, tâm lý khách quan): Gán -> [DẪN CHUYỆN].
  + Âm thanh máy móc, loa phát thanh, bảng thông báo hệ thống: Gán -> [HỆ THỐNG].
  + Lời thoại trực tiếp hoặc độc thoại nội tâm của nhân vật đã rõ danh tính (theo ngữ cảnh hoặc Bảng thực thể): Gán đúng -> [Tên nhân vật].
- QUY TẮC NGÔI GỌI TÊN ĐẦU CÂU (VOCATIVE):
  + Khi câu thoại mở đầu bằng việc gọi tên một nhân vật (ví dụ: "A, chúng ta đi thôi" hoặc "B, thôi bỏ đi"), thì tên được gọi ở đầu câu là NGƯỜI NGHE, tuyệt đối KHÔNG phải người nói. Phải gán -> [Tên vai] ở cuối dòng cho NGƯỜI ĐANG NÓI câu đó.
- QUY TẮC CỨNG CHO ĐÁM ĐÔNG / HỘI THOẠI KHÔNG RÕ CHỦ NGỮ / ĐỒNG DAO / LỜI HÁT (BẮT BUỘC 1:1, CẤM DẤU '/'):
  + Mọi dòng mang nhãn -> [Vai A], -> [Vai B], -> [Vai C], -> [Vai D] BẮT BUỘC chỉ chứa DUY NHẤT 1 mẩu chữ Hán gốc trong ngoặc vuông [mẩu ASR đơn] (tuyệt đối CẤM có dấu '/' bên trong [...] ở bất kỳ dòng [Vai A], [Vai B], [Vai C], [Vai D] nào).
  + Khi gặp cảnh nhiều người quần chúng bàn tán, la hét hoặc các câu đồng dao/lời hát/câu nói kỳ dị liên tiếp: Bắt buộc tách từng mẩu ASR thành từng dòng riêng biệt (1:1), dịch sát chữ từng dòng và đánh nhãn luân phiên -> [Vai A], -> [Vai B], -> [Vai C], -> [Vai D] (không dùng nhãn [ĐÁM ĐÔNG], [QUẦN CHÚNG] hay [Quỷ Dị]). Ngay khi quay lại lời kể chuyện ngôi thứ ba thì trả về -> [DẪN CHUYỆN].
- Chỉ giữ nguyên cùng [Tên vai] ở hai dòng liên tiếp khi chắc chắn cùng một nhân vật (hoặc cùng [DẪN CHUYỆN]) đang nói câu tiếp theo của mình; ngay khi đổi lượt lời sang người khác thì bắt buộc đổi sang [Tên vai] của người mới.

3. YÊU CẦU DỊCH THUẬT & CHUẨN HÓA LỖI ĐỒNG ÂM ASR:
- Dịch sạch 100% sang tiếng Việt tự nhiên, giữ đúng dấu câu ngắt nghỉ và quy chuẩn xưng hô của thể loại.
- Văn bản đầu vào là ASR tự động nên chữ Hán đôi khi bị lệch âm hoặc nhầm chữ đồng âm. Phải đối chiếu ngữ cảnh và Bảng thực thể để dịch thống nhất 100% về đúng tên nhân vật, vật phẩm, địa danh chuẩn xuyên suốt kịch bản (khi sửa lỗi đồng âm trong bản dịch tiếng Việt, phần chữ Hán trong [...] vẫn phải giữ nguyên văn theo đầu vào; tuyệt đối không viết chú thích ASR trong ngoặc đơn).
- Tuyệt đối không để sót chữ Hán, Pinyin hay tiếng Anh trong lời dịch tiếng Việt."""


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

    return (
        f"KỊCH BẢN GỐC CẦN XỬ LÝ:\n"
        f"{raw_text.strip()}\n\n"
        f"BẢNG TỪ ĐIỂN THỰC THỂ KHÓA TÊN (BẮT BUỘC DÙNG ĐÚNG):\n"
        f"{entity_line}\n\n"
        f"[ĐỊNH DẠNG ĐẦU RA BẮT BUỘC]\n"
        f"- CẤU TRÚC MỖI DÒNG:\n"
        f"  + Câu đơn trọn ý hoặc câu quần chúng / lời hát (1:1): [mẩu ASR gốc] Lời dịch tiếng Việt. -> [Tên vai]\n"
        f"  + Câu của nhân vật đã biết tên (hoặc lời dẫn / hệ thống) bị ASR tách thành 2-3 mảnh ngắn: [mẩu 1/mẩu 2] Lời dịch tiếng Việt hoàn chỉnh liền mạch. -> [Tên vai]\n"
        f"- NGUYÊN TẮC GỘP CÂU CÂN BẰNG (TỐI ĐA 2-3 MẨU) & ĐỒNG BỘ [CHỮ HÁN] - LỜI DỊCH:\n"
        f"  + Chủ động gộp 2 đến tối đa 3 mẩu ASR ngắn liền kề của cùng một câu và cùng một người nói bằng dấu '/' trong [...] để tạo thành câu hoàn chỉnh, giúp xác định chính xác tên nhân vật. Tuyệt đối không gộp từ 4 mẩu trở lên thành đoạn văn dài; không gộp lời của 2 người khác nhau; không gộp lời dẫn [DẪN CHUYỆN] với lời thoại nhân vật vào chung 1 dòng; không dùng dấu ngoặc kép để nhét lời thoại vào dòng [DẪN CHUYỆN]; không đặt dấu '/' ở phần dịch tiếng Việt.\n"
        f"  + Chữ Hán trong [...] của dòng nào phải dịch trực tiếp và đầy đủ sang lời thoại tiếng Việt ở dòng đó; tuyệt đối CẤM dùng dấu ngoặc đơn () để ghi chú lỗi ASR hay tóm tắt âm thanh. Giữ nguyên văn chữ Hán gốc trong [...] và bao phủ tuần tự 100% các mẩu đầu vào từ đầu đến cuối.\n"
        f"- QUY ĐỊNH PHÂN VAI & KHÓA 1:1 CHO [Vai A], [Vai B], [Vai C], [Vai D]:\n"
        f"  + Lời kể ngôi thứ ba -> [DẪN CHUYỆN]; thông báo máy móc/loa phát thanh -> [HỆ THỐNG]; hội thoại hoặc độc thoại nội tâm rõ nhân vật -> [Tên nhân vật].\n"
        f"  + Ngôi gọi tên đầu câu: Tên được gọi ở đầu câu thoại là NGƯỜI NGHE, bắt buộc gán -> [Tên vai] cho NGƯỜI ĐANG NÓI.\n"
        f"  + Đám đông / hội thoại không rõ chủ ngữ / đồng dao / lời hát: Mọi dòng gán nhãn -> [Vai A], -> [Vai B], -> [Vai C], -> [Vai D] bắt buộc đi 1:1 (chỉ chứa duy nhất 1 mẩu chữ Hán trong [...], tuyệt đối CẤM có dấu '/' trong ngoặc vuông ở dòng [Vai A]/[Vai B]/[Vai C]/[Vai D]). Tách riêng từng mẩu và gán luân phiên -> [Vai A], -> [Vai B], -> [Vai C], -> [Vai D]; khi quay lại lời kể chuyện thì trả về -> [DẪN CHUYỆN].\n"
        f"  + Đối chiếu Bảng thực thể để chuẩn hóa các từ bị ASR nghe nhầm đồng âm về đúng tên thực thể thống nhất.\n\n"
        f"Ví dụ minh họa cấu trúc chuẩn:\n"
        f"[mẩu ASR lời kể] Lời dịch trần thuật bối cảnh. -> [DẪN CHUYỆN]\n"
        f"[mẩu 1 bị cắt ngang/mẩu 2 nối tiếp] Lời dịch hoàn chỉnh ghép từ hai mẩu bị cắt của cùng một câu. -> [Tên nhân vật A]\n"
        f"[mẩu ASR phát thanh] Lời thông báo từ thiết bị. -> [HỆ THỐNG]\n"
        f"[mẩu ASR nhân vật A] Lời nói của người thứ nhất. -> [Tên nhân vật A]\n"
        f"[mẩu ASR nhân vật B] Lời đáp của người thứ hai. -> [Tên nhân vật B]\n"
        f"[mẩu ASR quần chúng 1] Tiếng bàn tán thứ nhất tách dòng đơn không có dấu gạch chéo. -> [Vai A]\n"
        f"[mẩu ASR quần chúng 2] Tiếng bàn tán thứ hai tách dòng đơn không có dấu gạch chéo. -> [Vai B]\n"
        f"[mẩu ASR quần chúng 3] Tiếng bàn tán thứ ba tách dòng đơn không có dấu gạch chéo. -> [Vai C]\n"
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
