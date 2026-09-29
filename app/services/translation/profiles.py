# -*- coding: utf-8 -*-
"""
app/services/translation/profiles.py
=========================================
BỘ PROFILE VĂN PHONG VÀ BẢN SẮC THỂ LOẠI CHO TRANSLATOR AIREAD (LỒNG TIẾNG VIDEO & AUDIO)

CẤU TRÚC CHUẨN MỰC (v3):
1. YÊU CẦU BẢN SẮC & LỜI DẪN TRUYỆN (Ngôi thứ 3 - CẤM TUYỆT ĐỐI DÙNG 'Y').
2. DANH SÁCH CẤM TUYỆT ĐỐI TOÀN BỘ XƯNG HÔ HIỆN ĐẠI (Không phân loại để tránh AI hiểu nhầm chỉ cấm trong nhà).
3. NGUYÊN TẮC THAY THẾ CHỐNG Ô NHIỄM (Bề trên/người già khó chọn từ thì bắt buộc dùng "Ta").
4. BẢNG XƯNG HÔ CỔ ĐẠI NÊN DÙNG / BẮT BUỘC DÙNG (Kèm ví dụ đối chiếu trực quan).
5. THUẬT NGỮ BẢN SẮC THỂ LOẠI & QUY CHUẨN ĐỊA DANH.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


# =====================================================================
# KHỐI QUY TẮC XƯNG HÔ CỔ PHONG DÙNG CHUNG (BÊ NGUYÊN XI TỪ AIREAD)
# =====================================================================

def _co_phong_addressing_block(danh_xung_bo_sung: str = "") -> str:
    """Khung xưng hô cổ phong chuẩn mực: ngắn gọn, đúng trọng tâm, bám sát từ gốc và giữ xuyên suốt bối phận."""
    extra = f"\n   - Danh xưng thể loại: {danh_xung_bo_sung.strip()}" if danh_xung_bo_sung and danh_xung_bo_sung.strip() else ""
    return f"""3. QUY CHUẨN XƯNG HÔ CỔ PHONG (BẮT BUỘC):
   - BÁM SÁT TỪ GỐC & TÔN TRỌNG KHOẢNG CÁCH:
     + Từ gốc là đại từ trung tính (你, 我) hoặc xưng hô khách sáo, xa cách, đối địch (kể cả giữa người thân): BẮT BUỘC dịch trung tính chuẩn cổ phong (Ta, Ngươi, Huynh đài, Các hạ, Tại hạ...). TUYỆT ĐỐI KHÔNG tự suy diễn đưa về xưng hô bối phận gia đình hay thân mật làm sai lệch nguyên tác.
     + Khi gặp từ khó, tối nghĩa hoặc mơ hồ quan hệ: BẮT BUỘC quy về "Ta — Ngươi", không tự biến tấu danh xưng phức tạp.
   - CHUẨN BỐI PHẬN & GIỮ XUYÊN SUỐT:
     + Đã xác định xưng hô hoặc bối phận ban đầu thì BẮT BUỘC GIỮ XUYÊN SUỐT toàn truyện (ví dụ: đã xưng với dì là "con" thì luôn là "con", cấm lúc xưng "con" lúc nhảy sang "em").
     + CHỈ ĐƯỢC ĐỔI XƯNG HÔ khi diễn biến cốt truyện có bước chuyển biến quan hệ rõ rệt (người lạ sau bái sư, kết nghĩa huynh đệ...). Cấm tự ý đổi xưng hô tùy tiện.
   - CHUYỂN NGỮ TỪ GỐC HAY GẶP:
     + 叔 / 大叔 → Thúc thúc / Đại thúc / Ta (CẤM: chú).
     + 伯 / 大伯 / 伯父 → Bá phụ / Đại bá / Ta (CẤM: bác).
     + 爷 / 大爷 / 老汉 / 老夫 → Lão gia / Đại gia / Lão hán / Lão phu / Ta (CẤM: ông, chú, bác).
     + 娘 / 大娘 / 婶 / 姑 / 姨 → Đại nương / Thẩm thẩm / Cô mẫu / Di nương / Tiểu di (CẤM: cô, dì, thím, mợ).
     + 兄弟 / 小哥 / 哥 → Huynh đệ / Tiểu ca / Ca ca (CẤM: anh trai, em trai, bạn).
     + 姑娘 / 小姐 / 丫头 / 妹 → Cô nương / Tiểu thư / Nha đầu / Muội muội (CẤM: em gái, cô bé, chị).
     + 师傅 / 师父 → Sư phụ / Sư phó (CẤM: thầy, bác tài).
     + 跟班 / 小跟班 / 玩伴 / 宠物 (chỉ ĐỘNG VẬT/LINH THÚ) → Sủng vật / Linh sủng / Bạn đồng hành (CẤM: nha đầu, người hầu, nô tì).
   - LỜI DẪN NGÔI 3: Dùng hắn, nàng, gã, thị, đối phương, thiếu niên, lão giả... TUYỆT ĐỐI CẤM DÙNG "Y".
   - CẤM TOÀN BỘ ĐẠI TỪ HIỆN ĐẠI: Cấm chú, bác, cô, dì, thím, mợ, dượng, cậu, cháu, ông, bà, bố, ba, má, thầy, anh trai, chị gái, em trai, em gái, con trai, con gái, tôi, bạn, tớ, mình, chúng mình, bọn em, tụi mình, các bạn, mày, tao, tụi mày, chú mày.{extra}"""



# =====================================================================
# 1. TIÊN HIỆP, TU CHÂN, HUYỀN HUYỄN
# =====================================================================
XIANXIA_PROFILE = {
    "description": (
        """YÊU CẦU DỊCH THỂ LOẠI TIÊN HIỆP, TU CHÂN, HUYỀN HUYỄN
1. THUẬT NGỮ BẢN SẮC & CẢNH GIỚI TU TIÊN:
   - Cảnh giới & tu vi: Luyện Khí, Trúc Cơ, Kim Đan, Nguyên Anh, Hóa Thần, Luyện Hư, Hợp Thể, Đại Thừa, Độ Kiếp (kèm sơ/trung/hậu kỳ, đỉnh phong, viên mãn, bán bộ; bình cảnh, đột phá, đốn ngộ).
   - QUY CHUẨN CẢNH GIỚI / CÔNG PHÁP: BẮT BUỘC ghép trực tiếp danh xưng cảnh giới với số Hán-Việt (Luyện Linh tam cảnh, Luyện Linh tứ cảnh, Luyện Khí nhị trọng / tam trọng / tầng hai, Kim Đan ngũ chuyển, Đệ tam cảnh...). TUYỆT ĐỐI CẤM dịch ghép lủng củng thành 'cảnh giới thứ hai / thứ ba / thứ tư / thứ N' hay 'Luyện Khí thứ hai / thứ ba' (ví dụ đúng: 'Luyện Linh tam cảnh đột phá lên tứ cảnh'; CẤM: 'Luyện Linh cảnh giới thứ ba đột phá cảnh giới thứ tư').
   - Tu luyện & tài nguyên: bế quan, độ kiếp, thiên kiếp, tâm ma, tẩu hỏa nhập ma, đoạt xá, vẫn lạc, đan điền, thức hải, thần thức, linh khí, linh căn, pháp lực, đạo tâm, linh thạch, đan dược, pháp bảo, linh bảo.
   - Môn phái & chức danh: Tông môn, thánh địa, động phủ, đệ tử, chấp sự, trưởng lão, phong chủ, tông chủ, chưởng môn, lão tổ, đạo lữ, tán tu (cấm dịch 'đạo hữu bán lẻ'), linh sủng / sủng vật / bạn đồng hành (từ gốc: 跟班/小跟班/玩伴/宠物 khi chỉ động vật — CẤM dịch thành nha đầu/nha hoàn/người hầu/nô tì).
2. QUY CHUẨN ĐỊA DANH: Núi non, sông biển, thành trì, bí cảnh, tông môn giữ nguyên trật tự Hán-Việt cổ phong (Thái Hòa Sơn, Tử Cấm Thành, Thiếu Lâm Tự).
"""
        + _co_phong_addressing_block(
            "Đạo hữu, Chư vị, Tiền bối, Vãn bối, Đạo trưởng, Tiên tử, Tông chủ, Trưởng lão, Phong chủ, Sư tôn, Sư phụ, Đồ nhi, Đệ tử, Sư huynh, Sư đệ, Sư tỷ, Sư muội, Tiểu di."
        )
    )
}


# =====================================================================
# 2. VÕ HIỆP, KIẾM HIỆP, GIANG HỒ, LỤC LÂM, THỦY HỬ, DÃ SỬ
# =====================================================================
WUXIA_PROFILE = {
    "description": (
        """YÊU CẦU DỊCH THỂ LOẠI KIẾM HIỆP, VÕ LÂM, GIANG HỒ, LỤC LÂM, THỦY HỬ, SA TRƯỜNG
1. THUẬT NGỮ BẢN SẮC VÕ HIỆP GIANG HỒ:
   - Chiêu thức & nội lực: đan điền, huyệt đạo, kinh mạch, nội lực, chân khí, kình lực, kiếm khí, đao phong, chưởng lực, thân pháp, khẩu quyết, tâm pháp, bí kíp võ công.
   - QUY CHUẨN CẢNH GIỚI / CÔNG PHÁP: BẮT BUỘC dùng chuẩn Hán-Việt (nhất/nhị/tam trọng, tầng một/tầng hai, đệ tam tầng...); TUYỆT ĐỐI CẤM dịch thành 'cảnh giới thứ hai / thứ ba / thứ tư' hay 'tầng thứ hai / thứ ba'...
   - Giang hồ & Sa trường: Hắc đạo, Bạch đạo, Lục lâm, Thảo mãng, Sơn trại, Tiêu cục, Võ lâm minh chủ, Tỷ võ chiêu thân, hiệp khách, lãng khách, du hiệp, khoái đao, Tướng quân, Mạt tướng, Ty chức, Cấm quân, Bổ khoái, sủng vật / bạn đồng hành (từ gốc: 跟班/小跟班/玩伴/宠物 khi chỉ động vật — CẤM dịch thành nha đầu/nha hoàn/người hầu/nô tì).
2. QUY CHUẨN ĐỊA DANH: Giữ nguyên trật tự Hán-Việt cổ phong cho địa danh, bang phái, thành trì.
"""
        + _co_phong_addressing_block(
            "Huynh đài, Các hạ, Hảo hán, Tráng sĩ, Đại hiệp, Thiếu hiệp, Trại chủ, Bang chủ, Tiêu đầu, Tướng quân, Mạt tướng, Ty chức, Ca ca, Hiền đệ, Tiểu đệ, Tiểu di."
        )
    )
}


# =====================================================================
# 3. ĐÔ THỊ HIỆN ĐẠI, THƯƠNG TRƯỜNG, HỌC ĐƯỜNG, GIỚI GIẢI TRÍ
# =====================================================================
URBAN_PROFILE = {
    "description": (
        """YÊU CẦU DỊCH THỂ LOẠI ĐÔ THỊ HIỆN ĐẠI, THƯƠNG TRƯỜNG, HỌC ĐƯỜNG, GIỚI GIẢI TRÍ
1. THUẬT NGỮ BẢN SẮC:
   - Thương trường: Tập đoàn, Chủ tịch HĐQT, Tổng Giám đốc (Tổng tài / CEO), Trợ lý, Thư ký, Hợp đồng, Cổ phần, thú cưng / sủng vật (nuôi động vật — CẤM dịch thành nô tì/người hầu).
   - Học đường & Showbiz: Bạn học, Lớp trưởng, Giáo viên chủ nhiệm, Minh tinh, Đỉnh lưu, Ảnh đế, Ảnh hậu, Đạo diễn, Fan, Scandal.
2. QUY CHUẨN XƯNG HÔ HIỆN ĐẠI:
   - Dùng đại từ đời thường (Tôi, Anh, Chị, Bạn, Em, Cậu, Tớ, Bố, Mẹ, Chú, Bác...). CẤM dùng xưng hô cổ trang (huynh đài, tại hạ, bản tọa, tiểu nữ...).
   - BẮT BUỘC nhìn từ gốc và chuẩn bối phận: Đã xưng hô thế nào thì giữ xuyên suốt toàn truyện, không tự ý nhảy loạn xưng hô (ví dụ: xưng với chú/dì/bác là con hoặc cháu thì giữ nhất quán). Nếu từ gốc chỉ là xưng hô khách sáo, người lạ, xã giao thì giữ chuẩn mực lịch sự (Tôi, Anh, Chị), không tự bịa bối phận thân mật."""
    )
}


# =====================================================================
# 4. ĐÔ THỊ LINH DỊ, PHONG THỦY, VỚT XÁC, BẮT MA, ĐẠO MỘ
# =====================================================================
URBAN_SUPERNATURAL_PROFILE = {
    "description": (
        """YÊU CẦU DỊCH THỂ LOẠI ĐÔ THỊ LINH DỊ, PHONG THỦY, VỚT XÁC, BẮT MA, ĐẠO MỘ
1. THUẬT NGỮ BẢN SẮC: Mao Sơn, Đạo sĩ, Thiên sư, Phong thủy sư, Âm dương tiên sinh, Chu sa, Bùa đào mộc kiếm, Âm khí, Dương khí, Tà khí, Cương thi, Lệ quỷ, Vớt xác, Đạo mộ, Mô kim hiệu úy, thú cưng / sủng vật / linh thú (nuôi động vật — CẤM dịch thành nô tì/người hầu).
2. QUY CHUẨN XƯNG HÔ: Đời thường dùng thân thuộc (Tôi, Bác, Chú, Cậu, Chú Ba, Cửu thúc, Lâm Đạo trưởng...). Khi trừ tà giao chiến dùng khẩu khí đanh thép: Ta, Ngươi, Nghiệt súc, Yêu nghiệt."""
    )
}


# =====================================================================
# 5. NGÔN TÌNH, CỔ ĐẠI, ĐIỀN VĂN, CUNG ĐẤU, TRẠCH ĐẤU
# =====================================================================
ROMANCE_PROFILE = {
    "description": (
        """YÊU CẦU DỊCH THỂ LOẠI NGÔN TÌNH, CỔ ĐẠI, ĐIỀN VĂN, CUNG ĐẤU, TRẠCH ĐẤU
1. THUẬT NGỮ BẢN SẮC & CHỨC DANH:
   - Hậu cung & triều đình: Hoàng thượng (Bệ hạ / Thánh thượng), Hoàng hậu (Nương nương), Thái hậu, Quý phi, Phi tần, Thái tử, Công chúa, Quận chúa, Thân vương, Thái giám (Công công), Thượng cung, Cung nữ.
   - Phủ đệ & trạch đấu: Hầu phủ, Tướng phủ, Lão phu nhân, Đại lão gia, Nhị gia, Đại phu nhân, Di nương (vợ lẽ/thiếp, BẮT BUỘC dùng Di nương, cấm dịch thành dì), Đích nữ, Thứ nữ, Đích tử, Thứ tử, Nha hoàn, sủng vật / bạn đồng hành (từ gốc: 跟班/小跟班/玩伴/宠物 khi chỉ động vật — CẤM dịch thành nha đầu/nha hoàn/người hầu/nô tì).
2. QUY CHUẨN ĐỊA DANH: Giữ nguyên Hán-Việt cho cung điện, phủ đệ, thành trì.
"""
        + _co_phong_addressing_block(
            "Phu quân, Thê tử, Nương tử, Bệ hạ, Thần thiếp, Trẫm, Ái phi, Chủ tử, Nô tài, Nô tỳ, Phụ thân, Mẫu thân, Di nương, Huynh trưởng, Tỷ tỷ, Muội muội, Tiểu di."
        )
    )
}


# =====================================================================
# 6. HỆ THỐNG, TRỌNG SINH, XUYÊN KHÔNG, KHOÁI XUYÊN, VÔ ĐỊCH LƯU
# =====================================================================
SYSTEM_REINCARNATION_PROFILE = {
    "description": (
        """YÊU CẦU DỊCH THỂ LOẠI HỆ THỐNG, TRỌNG SINH, XUYÊN KHÔNG, VÔ ĐỊCH LƯU
1. THUẬT NGỮ BẢN SẮC: Hệ thống - Ký chủ / Túc chủ; 'Đinh!' / 'Nhắc nhở:...' / 'Thông báo:...'; điểm tích lũy, điểm danh vọng, thăng cấp thuộc tính, thương thành, trọng sinh, xuyên không, ngón tay vàng, chiến sủng / linh sủng / thú cưng (nuôi động vật — CẤM dịch thành nô tì/người hầu).
2. QUY CHUẨN XƯNG HÔ THEO THẾ GIỚI XUYÊN VÀO:
   - Xuyên vào CỔ ĐẠI / TU CHÂN: BẮT BUỘC 100% tuân thủ xưng hô CỔ PHONG (Ta, Ngươi, Lão phu, Tại hạ...), Lời dẫn cấm dùng 'y', cấm xưng hô hiện đại.
   - Xuyên vào HIỆN ĐẠI: Dùng đại từ hiện đại (Tôi, Cậu, Bạn, Anh, Em...).
   - Với Hệ thống: Hệ thống xưng 'Bản hệ thống' ↔ gọi 'Ký chủ'; Ký chủ gọi 'Ngươi'."""
    )
}


# =====================================================================
# 7. MẠT THẾ, TẬN THẾ ZOMBIE, KHOA HUYỄN, TINH TẾ, CƠ GIÁP
# =====================================================================
SCI_FI_APOCALYPSE_PROFILE = {
    "description": (
        """YÊU CẦU DỊCH THỂ LOẠI MẠT THẾ, TẬN THẾ ZOMBIE, KHOA HUYỄN, TINH TẾ, CƠ GIÁP
1. THUẬT NGỮ BẢN SẮC: Tang thi / Zombie (triều tang thi, Tang thi vương), thú biến dị, tinh hạch, người thức tỉnh, chiến hạm không gian, cơ giáp, quang não, khiên năng lượng, thú cưng / chiến sủng / sủng vật (nuôi động vật — CẤM dịch thành nô tì/người hầu).
2. QUY CHUẨN XƯNG HÔ: Dùng phong cách hiện đại / quân đội sinh tồn (Tôi, Cậu, Tớ, Anh, Em, Chỉ huy Trương, Đội trưởng Lâm...). CẤM xưng hô cổ trang kiếm hiệp."""
    )
}


# =====================================================================
# BỘ QUY TẮC CHUYỂN NGỮ CỐT LÕI (COMMON RULES CHO TOÀN BỘ HỆ THỐNG) — CHUẨN AIREAD
# =====================================================================
COMMON_RULES = (
    "QUY TẮC CỐT LÕI (áp dụng mọi thể loại):\n"
    "1. TÊN RIÊNG: Dùng đúng 100% bản dịch đã có trong Bảng thực thể cho tên nhân vật, địa danh, môn phái, bảo vật. Mỗi từ CHỈ MỘT bản dịch tiếng Việt duy nhất, hòa vào câu văn — CẤM ghi dạng song ngữ kiểu \"Chữ Hán (bản dịch)\", cấm sót chữ Hán/Pinyin, CẤM TIẾNG ANH.\n"
    "2. DỊCH THOÁT Ý, BIẾN ĐỔI TỪ NGỮ THEO VĂN HỌC VIỆT & BỔ SUNG TỪ NGỮ ĐẦY ĐỦ Ý: TUYỆT ĐỐI CẤM dịch bám sát từng từ, dịch từng chữ hay convert rồi ghép lại cơ học gây tối nghĩa, gượng gạo, lố lăng. BẮT BUỘC hiểu sâu trọn vẹn ngữ cảnh của cả câu và đoạn văn để dịch thoát ý; ưu tiên dịch nghĩa bóng, hàm ý mở rộng và cách diễn đạt phổ thông quen thuộc trong văn học và khẩu ngữ lồng tiếng Việt Nam. ĐƯỢC PHÉP và KHUYẾN KHÍCH chủ động biến đổi, tái cấu trúc từ và cụm từ, linh hoạt thay thế bằng các từ/cụm từ tiếng Việt giàu sức gợi cảm, truyền cảm, trau chuốt và biểu cảm hơn để câu văn hay hơn, xuôi tai hơn, lột tả trọn vẹn và đúng nhất thần thái cùng ý đồ tác giả. ĐẶC BIỆT: Khi câu văn gốc bị thiếu ý, rút gọn hay lược bớt thành phần khiến câu dịch tiếng Việt có nguy cơ bị cụt ngủn, hụt hẫng, tối nghĩa hay khó hiểu, BẮT BUỘC chủ động thêm các từ đi kèm, từ nối, từ phụ trợ hoặc thành phần bổ ngữ để câu văn tròn ý, sáng rõ, mạch lạc, có nhạc điệu và tự nhiên nhất.\n"
    "3. VĂN PHONG LỒNG TIẾNG & ĐỘ DÀI NHỊP THOẠI TỰ NHIÊN: Kịch bản dùng để thu âm lồng tiếng (dubbing / voiceover) trực tiếp cho video, yêu cầu lời thoại và lời dẫn phải có nhạc điệu, có hồn, sống động, tự nhiên, đọc lên êm tai và truyền cảm. Cho phép độ dài câu văn co giãn tự nhiên theo dòng cảm xúc (thông thường từ 1.0 đến khoảng 2.0 - 2.2 lần số từ gốc). TUYỆT ĐỐI KHÔNG cắt gọt thô bạo làm câu văn cụt lủn, cộc lốc hoặc mất đi cảm xúc và sự uyển chuyển; nhưng cũng tránh bôi chữ lê thê dài dòng vô căn cứ để vừa vặn với nhịp thở của video.\n"
    "4. CẤU TRÚC CÂU & NGỮ PHÁP TIẾNG VIỆT CHUẨN MỰC: BẮT BUỘC sắp xếp và tổ chức câu văn theo đúng cấu trúc ngữ pháp và thói quen diễn đạt tự nhiên của tiếng Việt (Chủ ngữ - Vị ngữ rõ ràng, mạch lạc, có nhịp điệu văn chương uyển chuyển). TUYỆT ĐỐI KHÔNG bê nguyên xi trật tự đảo ngữ, câu lủng củng hay cấu trúc ngữ pháp rườm rà của tiếng Trung sang tiếng Việt. Toàn bộ bản dịch là 100% tiếng Việt chuẩn mực, đúng chính tả, trôi chảy; TUYỆT ĐỐI CẤM TIẾNG ANH và bất kỳ từ ngoại lai hay ký tự lạ nào trong toàn bộ văn bản.\n"
    "5. CẢNH GIỚI TU LUYỆN, ĐỘNG VẬT & THUẬT NGỮ BẢN SẮC: BẮT BUỘC dịch cảnh giới/công pháp/tầng thứ theo chuẩn Hán-Việt (Luyện Linh tam cảnh / tứ cảnh, Luyện Khí nhị trọng / tam trọng / tầng hai, Kim Đan ngũ chuyển, Đệ tam cảnh...); TUYỆT ĐỐI CẤM dùng 'cảnh giới thứ hai / thứ ba / thứ tư / thứ N' hay 'Luyện Khí thứ 2, thứ 3...' (cấm dịch: 'cảnh giới thứ ba đột phá cảnh giới thứ tư'). Khi nói về động vật / chim chóc / linh thú / sủng vật (như Hắc Ưng, chó mèo, linh thú...), các từ gốc chỉ bạn đồng hành / sủng vật BẮT BUỘC dịch là 'sủng vật / linh sủng / bạn đồng hành bên người', TUYỆT ĐỐI CẤM dịch nhầm thành người ('nha đầu', 'nha hoàn', 'người hầu', 'nô tì', 'tiểu đồng'). Với động vật thông thường (chó, mèo, gia súc...), dịch đúng từ ngữ tiếng Việt tự nhiên (chó hoang, mèo hoang, đàn chó...); cấm máy móc dịch thành 'lưu lang cẩu' hay ép gọi chó mèo đời thường là 'sủng vật'. Giữ nguyên thuật ngữ Hán-Việt quen thuộc (tu vi, đan điền, tán tu...), không ép thuần Việt hóa gượng gạo.\n"
    "6. NGUYÊN TẮC XƯNG HÔ GỐC & GIỮ XUYÊN SUỐT BỐI PHẬN (BẮT BUỘC TUÂN THỦ):\n"
    "   - BÁM SÁT TỪ GỐC & KHOẢNG CÁCH QUAN HỆ: Xưng hô khoảng cách (khách sáo, kính trọng, xa cách, dè chừng, đối địch...) dùng cho cả người thân lẫn người lạ. BẮT BUỘC giữ đúng tính chất trung tính của từ gốc (cổ đại dùng Ta, Ngươi, Huynh đài, Các hạ...; hiện đại dùng Tôi, Anh, Chị...). CẤM tự suy diễn ép về xưng hô thân mật hay gán ghép bối phận làm sai lệch nguyên tác. Khó dịch hoặc mơ hồ thì quy về 'Ta — Ngươi' (cổ đại) hoặc 'Tôi — Anh/Chị' (hiện đại).\n"
    "   - CHUẨN BỐI PHẬN & GIỮ XUYÊN SUỐT: Đã xưng hô thế nào thì GIỮ XUYÊN SUỐT toàn truyện (ví dụ: xưng với dì là 'con' thì luôn là 'con', cấm lúc 'con' lúc 'em'). CHỈ ĐƯỢC ĐỔI XƯNG HÔ khi diễn biến quan hệ thực sự thay đổi rõ rệt (người lạ sau bái sư, kết nghĩa...).\n"
    "7. TỰ ĐỘNG PHÁT HIỆN & KHẮC PHỤC LỖI TỪ ĐỒNG ÂM ASR (SPEECH-TO-TEXT): Kịch bản gốc tiếng Trung được bóc tách từ âm thanh video bằng AI thính giác (ASR), do đó thường xuyên xuất hiện hiện tượng nghe nhầm sang chữ Hán đồng âm hoặc gần âm Pinyin (homophones) — đặc biệt là tên nhân vật, địa danh, tông môn, võ học hoặc thuật ngữ bối cảnh bị nghe nhầm thành từ sinh hoạt thông thường. BẮT BUỘC biên tập viên quan sát toàn bộ ngữ cảnh mạch truyện và tiêu đề tác phẩm: nếu một từ trong câu xuất hiện phi lý, lủng củng hoặc không ăn nhập với bối cảnh thể loại, PHẢI tự động truy nguyên từ đồng âm chuẩn xác trong tiếng Trung theo ngữ cảnh đó và dịch thẳng sang tiếng Việt đúng nghĩa; TUYỆT ĐỐI CẤM dịch ngô nghê bám theo chữ Hán bị AI thính giác nghe nhầm.\n"
    "8. BẢO TOÀN ĐÁNH SỐ ĐỊNH DẠNG 1:1: Mỗi dòng câu thoại đầu vào tương ứng chính xác với 1 dòng bản dịch đầu ra giữ nguyên số thứ tự. Tuyệt đối KHÔNG gộp dòng, không bỏ sót dòng, không chèn gạch đầu dòng (-) hay dấu ngoặc kép thừa vào câu."
)


# =====================================================================
# BẢNG TẬP HỢP TẤT CẢ CONTEXT PROFILES
# =====================================================================
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


def _rut_gon_nhac_xung_ho(genre_rules: str) -> str:
    """Trích câu quy tắc dương tính đầu tiên của khối xưng hô để nhắc lại ngắn gọn ở cuối prompt."""
    marker = "QUY CHUẨN XƯNG HÔ"
    idx = genre_rules.find(marker)
    if idx == -1:
        return ""
    end = genre_rules.find("\n\n", idx)
    if end == -1:
        end = len(genre_rules)
    return " ".join(genre_rules[idx:end].split())


def build_standard_system_prompt(genre: Optional[str] = None, author_notes_block: str = "") -> str:
    """Tạo System Prompt CHUẨN DUY NHẤT cho toàn bộ hệ thống dịch lồng tiếng video."""
    prof = get_profile_by_genre(genre)
    genre_rules = prof.get("description", "")
    author_section = f"\n\n{author_notes_block.strip()}" if author_notes_block and author_notes_block.strip() else ""

    return (
        f"Bạn là BIÊN TẬP VIÊN DỊCH THUẬT LỒNG TIẾNG VIDEO CAO CẤP (DUBBING & VOICEOVER) TRUNG - VIỆT.\n"
        f"Nhiệm vụ: Chuyển ngữ kịch bản lời thoại video tiếng Trung sang tiếng Việt để LỒNG TIẾNG CHO VIDEO.\n"
        f"Văn phong: Thoát ý, mượt mà, giàu cảm xúc, truyền cảm, thuần Việt tự nhiên, chuẩn văn phong tiểu thuyết và kịch bản lồng tiếng.\n"
        f"TUYỆT ĐỐI CẤM dịch bám chữ convert máy móc hoặc dùng từ Hán-Việt tối nghĩa thô cứng.\n"
        f"KHUYẾN KHÍCH sử dụng từ ngữ gợi cảm, trau chuốt, câu văn giàu nhạc điệu, xuôi tai, biểu đạt trọn vẹn thần thái nhân vật.\n"
        f"ĐẶC BIỆT: Khi câu văn gốc bị thiếu ý hoặc rút gọn, BẮT BUỘC chủ động thêm từ phụ trợ, từ nối để câu tròn ý, không cụt ngủn.\n"
        f"Khống chế nhịp câu vừa vặn với lời thoại video (tỷ lệ khoảng 1.0 đến tối đa 2.2 lần số từ gốc), không bôi chữ lan man, câu văn gãy gọn và uyển chuyển.\n\n"
        f"=== QUY CHUẨN THỂ LOẠI (BẢN SẮC & QUY TẮC XƯNG HÔ CHUẨN AIREAD) ===\n"
        f"{genre_rules}\n\n"
        f"=== QUY TẮC CỐT LÕI DỊCH THUẬT CHUẨN AIREAD CHO LỒNG TIẾNG VIDEO ===\n"
        f"{COMMON_RULES}"
        f"{author_section}"
    )


def build_user_translation_prompt(
    raw_text: str,
    entity_table_text: str,
    genre: Optional[str] = None,
    author_notes_block: str = ""
) -> str:
    """Tạo User Prompt CHUẨN cho LLM Translator dịch lồng tiếng."""
    prof = get_profile_by_genre(genre)
    genre_rules = prof.get("description", "")
    author_section = f"\n\n{author_notes_block.strip()}" if author_notes_block and author_notes_block.strip() else ""
    nhac_lai_xung_ho = _rut_gon_nhac_xung_ho(genre_rules)
    nhac_lai_block = f"\nNHẮC LẠI: {nhac_lai_xung_ho}\n" if nhac_lai_xung_ho else ""

    return f"""=== QUY CHUẨN THỂ LOẠI (BẢN SẮC & QUY TẮC XƯNG HÔ CHUẨN AIREAD) ===
{genre_rules}{author_section}

=== KỊCH BẢN GỐC CẦN DỊCH LỒNG TIẾNG ===
{raw_text.strip()}

=== BẢNG THỰC THỂ KHÓA TÊN RIÊNG ===
{entity_table_text.strip()}

=== QUY TẮC DỊCH THUẬT CHUẨN AIREAD CHO LỒNG TIẾNG VIDEO ===
{COMMON_RULES}
{nhac_lai_block}
BẮT ĐẦU DỊCH NGAY BÂY GIỜ. Dịch kịch bản gốc ở trên sang tiếng Việt lồng tiếng thoát ý, mượt mà, giàu nhạc điệu và cảm xúc, thuần Việt tự nhiên, đọc lên êm tai, vừa vặn nhịp thở thoại video, đúng quy chuẩn thể loại và xưng hô đã nêu."""


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


def get_xml_structure_instruction(chap_count: int = 1, chap_list_str: str = "") -> str:
    """Quy chuẩn cấu trúc thẻ XML phân chia chương."""
    chap_info = f" ({chap_count} chương: {chap_list_str})" if chap_list_str else ""
    return (
        f"BẢO LƯU TOÀN BỘ CẤU TRÚC THẺ XML{chap_info}:\n"
        "- Giữ nguyên chính xác các thẻ phân tách: <chapter_N>...<chapter_N> "
        "tương ứng với từng phân đoạn trong văn bản gốc."
    )


def get_context_profile_prompt(genre: Optional[str] = None) -> str:
    """Hàm tương thích ngược."""
    return get_profile_description(genre)
