# -*- coding: utf-8 -*-
"""
app/services/translation/profiles.py
=========================================
BỘ PROFILE VĂN PHONG VÀ BẢN SẮC THỂ LOẠI CHO TRANSLATOR AIREAD (LỒNG TIẾNG VIDEO & DUBBING)

CẤU TRÚC CHUẨN MỰC (v4 - TINH CHỈNH ĐỒNG BỘ CHO LỒNG TIẾNG PHIM VIDEO):
1. YÊU CẦU BẢN SẮC & LỜI DẪN THOẠI (Ngôi thứ 3 - CẤM TUYỆT ĐỐI DÙNG 'Y').
2. DANH SÁCH CẤM TUYỆT ĐỐI TOÀN BỘ XƯNG HÔ HIỆN ĐẠI (Không phân loại để tránh AI hiểu nhầm chỉ cấm trong nhà).
3. NGUYÊN TẮC THAY THẾ CHỐNG Ô NHIỄM (Bề trên/người già khó chọn từ thì bắt buộc dùng "Ta").
4. BẢNG XƯNG HÔ CỔ ĐẠI NÊN DÙNG / BẮT BUỘC DÙNG (Kèm ví dụ đối chiếu trực quan).
5. THUẬT NGỮ BẢN SẮC THỂ LOẠI & QUY CHUẨN ĐỊA DANH.
6. COMMON RULES ĐẶC THÙ LỒNG TIẾNG PHIM: Câu từ súc tích, gãy gọn, vừa vặn nhịp đọc (ôm khít Start - End, không xé lẻ câu, không cắt cụt đuôi).
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
        "THỂ LOẠI TIÊN HIỆP: Giữ chuẩn thuật ngữ tu tiên (Luyện Khí, Trúc Cơ, Kim Đan, Nguyên Anh, thần thức, đan điền, linh căn, pháp bảo), địa danh giữ âm Hán-Việt.\n"
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
COMMON_RULES = """Bạn là AI xử lý kịch bản lồng tiếng phim chuyên nghiệp (Dubbing Engine). Nhiệm vụ của bạn là dịch và GỘP các mẩu thoại thô thành kịch bản hoàn chỉnh theo đúng các RÀNG BUỘC CỨNG sau đây:

[1. BẤT BIẾN LƯỢT LỜI & QUY TẮC DẤU NGĂN CÁCH '/']
Mỗi dòng kịch bản [Tên Vai|mảnh1/mảnh2] Lời thoại tiếng Việt tương ứng với DUY NHẤT 1 GIỌNG ĐỌC trong bộ máy lồng tiếng TTS. Do đó:
- ĐIỀU KIỆN GỘP: Chỉ được gộp các mẩu ASR liên tiếp khi và chỉ khi chúng là CÙNG MỘT NGƯỜI đang nói tiếp câu nói dở dang của chính họ.
- BẮT BUỘC DÙNG DẤU GẠCH CHÉO '/' KHI GỘP: Khi gộp nhiều mẩu ASR thành 1 câu, BẮT BUỘC dùng dấu '/' để ngăn cách các mẩu ASR trong phần chữ Hán [Tên Vai|mảnh1/mảnh2/mảnh3]. Mỗi mảnh là chữ Hán tương ứng của MỘT mẩu ASR thành phần. Tuyệt đối KHÔNG tự chèn '/' vào giữa nội dung của một mẩu ASR đơn lẻ.
- BẤT BIẾN TÁCH DÒNG (CẤM GỘP ĐA THOẠI): Bất cứ khi nào có NGƯỜI KHÁC LÊN TIẾNG (dù chỉ là 1 từ, 1 tiếng đệm, 1 câu đối đáp), hoặc chuyển giữa lời kể [DẪN CHUYỆN] và lời nhân vật -> BẮT BUỘC PHẢI NGẮT SANG DÒNG MỚI NGAY LẬP TỨC. Tuyệt đối KHÔNG phụ thuộc vào ký hiệu [KHÓA]. CẤM TUYỆT ĐỐI việc nhét lời của hai người nói khác nhau vào chung một dòng thoại, vì TTS sẽ đọc bằng một giọng duy nhất làm vỡ kịch bản.
- RANH GIỚI [KHÓA]: Gặp [KHÓA] ở mẩu nào, câu thoại BẮT BUỘC dừng và kết thúc ngay tại mẩu đó (khoảng lặng dài / chuyển cảnh). Tuyệt đối CẤM gộp xuyên qua [KHÓA].

[2. QUY TẮC BIẾN TẤU CHỌN LỌC & HẠN MỨC SỐ TỪ (TIỆM CẬN MAX)]
Bạn là BIÊN KỊCH LỒNG TIẾNG, KHÔNG PHẢI MÁY DỊCH BÁM CHỮ! Hãy tư duy cách biểu đạt thuần Việt tự nhiên, có chọn lọc linh hoạt:
- NGUYÊN TẮC DỊCH TIỆM CẬN MAX (CÂU HAY, ĐẦY ĐỦ CẢM XÚC):
  + Khi câu dịch nằm trong hạn mức: Hãy dịch đầy đặn, mượt mà, giàu cảm xúc và tiệm cận với hạn mức (max X từ) để câu thoại không bị cụt ngủn hay cộc lốc.
  + Dung sai nhẹ: Được phép vượt nhẹ 1 từ nếu cần để giữ tròn ý nghĩa và nhạc điệu câu (ví dụ max 6 từ thì dịch 6-7 từ vẫn hoàn toàn hợp lệ).
- KHI NÀO MỚI KÍCH HOẠT BIẾN TẤU RÚT GỌN?
  + CHỈ KHI câu gốc quá nhiều chữ rườm rà khiến câu dịch có nguy cơ vượt xa hạn mức (lố từ 2-3 từ trở lên): Lúc này bạn MỚI ĐƯỢC PHÉP biến tấu có chọn lọc, lược bỏ các vị ngữ phụ rườm rà, chọn lọc động từ và cách nói súc tích nhất để đưa câu về lại mức tiệm cận max mà vẫn giữ trọn ý nghĩa cốt lõi của nhân vật.
- CÁCH TÍNH HẠN MỨC KHI GỘP:
  + Mẩu đơn (1 mẩu): Bám sát tiệm cận (max X từ) của mẩu đó (dung sai +1 từ).
  + Câu gộp (nhiều mẩu): Hạn mức = TỔNG tất cả (max X từ) của các mẩu trong cụm gộp. Dịch tiệm cận tổng hạn mức này, tuyệt đối không dịch phình to vượt quá xa (ví dụ tổng max 10 từ thì dịch 9-11 từ là đẹp, cấm dịch thành 16-20 từ).

[3. PHÂN VAI VÀ XƯNG HÔ]
- Dùng [DẪN CHUYỆN] cho lời kể bối cảnh ngôi thứ 3.
- Dùng [HỆ THỐNG] cho thông báo máy móc / hệ thống.
- Đặt tên cụ thể cho nhân vật chính/phụ ([Trần Bình], [Đại Đầu], [Lão Trương Đầu]...) dựa vào nội dung và từ điển thực thể.
- VỚI NHÂN VẬT CHƯA BIẾT TÊN / NGƯỜI QUA ĐƯỜNG ĐỐI ĐÁP: BẮT BUỘC đặt tên tịnh tiến theo bảng chữ cái: [Nhân vật A], [Nhân vật B], [Nhân vật C]... theo đúng thứ tự xuất hiện đối thoại trong cảnh. Tuyệt đối CẤM tự ý gom lời thoại nhân vật vào [DẪN CHUYỆN].
- Áp dụng đúng quy chuẩn xưng hô của thể loại được chỉ định.

[4. YÊU CẦU DỊCH THUẬT & KHÓA CỨNG THỰC THỂ]
- KHÓA THỰC THỂ 100%: Bắt buộc sử dụng chính xác các tên nhân vật, chiêu thức, địa danh, vật phẩm đã liệt kê trong "BẢNG TỪ ĐIỂN THỰC THỂ KHÓA TÊN", tuyệt đối không tự ý đổi tên khác.
- VĂN PHONG DỊCH THUẬT LỒNG TIẾNG: Dịch thoát ý tự nhiên theo lối hành văn nói thuần Việt sinh động, trôi chảy, giàu nhịp điệu lồng tiếng phim. Tuyệt đối CẤM dịch bám chữ convert máy móc cộc lốc hoặc dùng từ Hán-Việt tối nghĩa khó hiểu.
- BẢO TOÀN ĐỘ TOÀN VẸN: Dịch sạch 100% sang tiếng Việt, tuyệt đối không để sót chữ Hán hay ký tự ngoại lai nào trong lời dịch.

[5. KỸ THUẬT VIỆT HÓA CHỐNG CONVERT & BIẾN TẤU GỌN GÀNG]
- ĐẢO NGỮ PHÁP TRUNG -> VIỆT (CẤM DỊCH WORD-BY-WORD):
  + Tuyệt đối xóa bỏ cấu trúc câu ngược kiểu convert ("Bởi vì... cho nên...", "Đem [Tân ngữ] xử lý", "Tại... bên trong", "Của ta [Danh từ]").
  + Bắt buộc đảo lại trật tự thuần Việt: [Chủ ngữ] + [Động từ] + [Tân ngữ] + [Trạng ngữ chỉ nơi chốn/thời gian].
- NGHỆ THUẬT RÚT GỌN KHI CÂU GỐC QUÁ DÀI:
  + Khi câu gốc chứa nhiều vị ngữ hoặc trạng từ rườm rà nhưng hạn mức số từ lại ít: Hãy lược bỏ các chi tiết phụ, chọn lọc động từ mạnh và từ ngữ súc tích nhất để giữ trọn ý chính mà câu văn vẫn gọn gàng, không bị vỡ nhịp đọc.
- BỒI ĐẮP TỰ NHIÊN KHI CÂU GỐC QUÁ NGẮN:
  + Chỉ khi câu dịch bị quá ngắn so với hạn mức (ví dụ hạn mức 10 từ mà mới dịch được 3-4 từ), bạn mới dùng trợ từ ngữ khí cuối câu (đâu, đấy, nhé, rồi, mà...) hoặc từ nối để cân đối, nhưng luôn đảm bảo không vượt quá tổng hạn mức từ cho phép."""





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
        f"[4. ĐỊNH DẠNG ĐẦU RA (OUTPUT FORMAT) - BẮT BUỘC TUÂN THỦ]\n"
        f"- CẤU TRÚC BẮT BUỘC MỖI DÒNG:\n"
        f"[Tên Vai|mảnh1/mảnh2/mảnh3] Lời thoại tiếng Việt.\n"
        f"  (Trong đó: 'Tên Vai' là tên nhân vật như [DẪN CHUYỆN], [Trần Bình]...;\n"
        f"  'mảnh1/mảnh2/mảnh3' là CHÉP LẠI chữ Hán của CÁC MẨU ASR ĐƯỢC GỘP, BẮT BUỘC dùng dấu gạch chéo '/' ngăn cách giữa các mẩu ASR thành phần.\n"
        f"  Nếu câu chỉ gồm 1 mẩu ASR đơn lẻ (không gộp): [Trần Bình|mẩu1] Lời thoại tiếng Việt (không cần '/').\n"
        f"  TUYỆT ĐỐI KHÔNG tự chèn dấu '/' vào giữa nội dung của một mẩu ASR đơn lẻ!\n"
        f"  TUYỆT ĐỐI KHÔNG đánh số thứ tự vào thẻ!).\n\n"
        f"Ví dụ định dạng đầu ra chuẩn:\n"
        f"[DẪN CHUYỆN|为了不被饿死/男人偷偷溜进宗门稻田/打算偷点自己种出来的稻谷] Để khỏi chết đói, hắn lẻn vào ruộng lúa của Tông Môn, định trộm ít Linh Cốc do chính mình gieo trồng.\n"
        f"[Trần Bình|我只要一粒/一粒就好] Ta chỉ cần một hạt là đủ!\n"
        f"[DẪN CHUYỆN|男人不敢发出一点声音/因为一旦被发现偷窃灵骨/哪怕是一粒就会被打死] Hắn không dám phát ra một tiếng động, bởi vì một khi bị phát hiện trộm Linh Cốt, dù chỉ một hạt cũng sẽ bị đánh chết.\n"
        f"[Nhân vật A|晦气/又死一个] Xúi quẩy thật, lại chết thêm một mạng.\n"
        f"[Nhân vật B|算了年老体衰怎么经得起折腾] Kệ đi, già yếu quá làm sao chịu nổi.\n\n"
        f"- NGUYÊN TẮC BẢO TOÀN NỘI DUNG LIỀN MẠCH: Các đoạn chữ Hán trong các câu kịch bản PHẢI ghép nối liền mạch và bao phủ toàn bộ văn bản đầu vào theo đúng thứ tự (không được bỏ sót bất kỳ câu nào, không được nhảy cóc hay đảo lộn thứ tự).\n"
        f"- ĐỐI VỚI CÂU ĐƠN LẺ KHÔNG GỘP (Ví dụ mẩu đơn max 3-5 từ): Bắt buộc dùng câu cực kỳ ngắn gọn, gãy gọn, cấm bôi thêm từ đệm rườm rà làm vượt hạn mức từ.\n"
        f"- TUYỆT ĐỐI CẤM in lại chữ [KHÓA] hay các nhãn (max X từ) vào bất kỳ vị trí nào trong câu dịch!"
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

