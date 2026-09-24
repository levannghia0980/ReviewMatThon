import json
import os
import httpx
import re
import logging
from typing import List, Dict, Any
from app.config import settings
from app.core.llm_client import post_gemini_with_retry, post_openrouter_with_retry, safe_json_loads

logger = logging.getLogger(__name__)


async def _get_llm_config():
    """Đọc cấu hình LLM provider/model/api_key và xác định là Gemini, OpenRouter hay Grok."""
    provider_val = os.environ.get("TRANSLATION_PROVIDER") or os.environ.get("AIREAD_PROVIDER") or settings.TRANSLATION_PROVIDER or "gemini"
    provider = str(provider_val).lower().strip()
    
    if provider == "openrouter":
        model = (os.environ.get("OPENROUTER_MODEL") or os.environ.get("AIREAD_MODEL") or settings.OPENROUTER_MODEL or "google/gemini-2.5-flash").strip()
        raw_api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("AIREAD_API_KEYS") or settings.OPENROUTER_API_KEY or ""
    else:
        model = (os.environ.get("GEMINI_MODEL") or os.environ.get("AIREAD_MODEL") or settings.GEMINI_MODEL or "gemini-3.1-flash-lite").strip()
        raw_api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("AIREAD_API_KEYS") or settings.GEMINI_API_KEY or ""
    
    api_key = raw_api_key.split(',')[0].strip() if raw_api_key else ""

    is_grok_local = (provider in ["grok_local", "grok", "grok_web"]) or ("grok" in model.lower())
    is_openrouter = not is_grok_local and ((provider == "openrouter") or ("/" in model) or ("qwen" in model.lower()) or ("openrouter" in model.lower()))

    # Nếu đang chọn Grok và có API key (Gemini):
    # Dùng gemini-3.1-flash-lite cho khâu bóc tách thực thể để trả về JSON siêu tốc 1-2s, tránh lỗi 404
    if is_grok_local:
        if api_key:
            model = settings.GEMINI_MODEL or "gemini-3.1-flash-lite"
            is_grok_local = False

    return model, api_key, is_openrouter, is_grok_local


async def _remove_sensitive_words_for_extraction(text: str) -> str:
    """
    Xóa bỏ các từ nhạy cảm 18+ trước khi gửi cho LLM xử lý NER/Entity extraction.
    Mục đích: Tránh Gemini/LLM bị chặn do vi phạm SafetyPolicy khi phân tích văn bản có nội dung nhạy cảm.
    """
    try:
        from app.services.unblock.common.dictionary_loader import load_zh_erotic_map
        zh_map = load_zh_erotic_map()
        for word in zh_map:
            if word in text:
                text = text.replace(word, "")
    except Exception:
        pass
    return text


FULL_EXTRACTION_RULES = """=== BỘ NGUYÊN TẮC BÓC TÁCH THỰC THỂ CỐT LÕI (MỆNH LỆNH VÉT CẠN 6 NHÓM ĐẶC TRƯNG) ===
MỆNH LỆNH TỐI CAO: TUYỆT ĐỐI KHÔNG ĐƯỢC BỎ SÓT 6 NHÓM THỰC THỂ ĐẶC TRƯNG (NAME, CREATURE, PLACE, SECT, ITEM, SKILL). Nhóm OTHER/Lore có thể linh hoạt nhưng 6 nhóm thực thể chính này bắt buộc phải vét cạn 100% không để lọt dù chỉ xuất hiện 1 lần!

4 CĂN CỨ TRUY VẾT & NHẬN DIỆN THỰC THỂ CỰC MẠNH (DỰA VÀO 4 TIÊU CHÍ):
1. ĐỘ LẶP LẠI (FREQUENCY): Cụm từ/danh từ xuất hiện lặp lại nhiều lần trong các chương, các đoạn đối thoại hoặc miêu tả.
2. TỪ ĐI KÈM & TIỀN TỐ/HẬU TỐ ĐỊNH DANH (COLLOCATION):
   - Đi kèm chức vị/danh xưng: Tông chủ, Trưởng lão, Sư huynh/đệ/tỷ/muội, Đạo hữu, Cô nương, Công tử, Thiếu gia, Tiền bối, Lão nhân, Tiên sinh...
   - Đi kèm từ chỉ loại: Sơn, Phong, Các, Điện, Động, Thành, Thôn, Trấn, Giới, Cốc, Tự, Tông, Môn, Phái, Bang, Viện, Kiếm, Đao, Thương, Đan, Dược, Thảo, Thú, Quyền, Chưởng, Quyết, Công, Pháp, Trận...
   - Đi kèm động từ chỉ hành vi: ...nói, ...quát, ...nghĩ thầm, tế ra..., thi triển..., rút ra..., đi tới..., bái kiến...
3. DẤU HIỆU DANH TỪ RIÊNG & ĐỊNH DẠNG (PROPER NOUNS & FORMATTING):
   - Đóng vai trò chủ ngữ/tân ngữ độc lập, hoặc nằm trong các dấu ngoặc kép/ngoặc đơn 《...》, 「...」, 【...】, “...”.
4. NGỮ CẢNH MẠCH TRUYỆN (STORY CONTEXT):
   - Dựa vào diễn biến cốt truyện, cảnh giao tranh, bối cảnh thế giới quan để bắt trọn mọi tên riêng được nhắc đến dù chỉ 1 lần.

CHI TIẾT 6 NHÓM THỰC THỂ BẮT BUỘC KHÔNG ĐƯỢC BỎ SÓT:
1. 'NAME': Tên người, nhân vật & Ngoại hiệu:
   - Tên đầy đủ, tên gọi tắt, họ tên của tất cả nhân vật.
   - Ngoại hiệu giang hồ / hảo hán / danh hiệu võ lâm (ví dụ: Bạch Y Tú Sĩ, Thác Tháp Thiên Vương, Báo Tử Đầu...).
   - Tên cúng cơm, biệt danh, nhũ danh, tên dân dã nông thôn của nhân vật đặt theo đồ vật, con vật, hòn đá, cây cỏ bình dị: BẮT BUỘC DỊCH 100% SANG ÂM HÁN-VIỆT, TUYỆT ĐỐI CẤM DỊCH NGHĨA VIỆT THUẦN (ví dụ: 石头 -> Thạch Đầu [CẤM dịch thành cục đá], 石猴子 -> Thạch Hầu Tử [CẤM dịch thành khỉ đá], 二狗 -> Nhị Cẩu [CẤM dịch 'Hai Con Chó'], 大壮 -> Đại Tráng, 木头 -> Mộc Đầu, 铁蛋 -> Thiết Đản, 柱子 -> Trụ Tử...).
   - Lưu ý họ '杨' trong tên người luôn luôn dịch là 'Dương' (Dương Quá, Dương Tiễn...).

2. 'CREATURE': Tên con vật, linh thú, yêu thú & Chủng loài thần thú:
   - Tên riêng của thú cưng, con vật nuôi, linh sủng đặt theo màu sắc, đồ vật, thức ăn: BẮT BUỘC DỊCH 100% SANG ÂM HÁN-VIỆT, TUYỆT ĐỐI CẤM DỊCH NGHĨA TIẾNG VIỆT (ví dụ: 黑狗 -> Hắc Cẩu [CẤM dịch thành chó đen], 乌云 -> Ô Vân [CẤM dịch thành mây đen], 雪球 -> Tuyết Cầu [CẤM dịch thành hòn tuyết], 白猫 -> Bạch Miêu, 大黑 -> Đại Hắc, 小黄 -> Tiểu Hoàng, 旺财 -> Vượng Tài...).
   - Tên chung của các chủng loài yêu thú, thần thú, dị thú, long tộc mang bản sắc: BẮT BUỘC GIỮ ÂM HÁN-VIỆT CHUẨN CỤM TỪ (ví dụ: 九尾天狐 -> Cửu Vĩ Thiên Hồ, 吞天蟒 -> Thôn Thiên Mãng, 太古神龙 -> Thái Cổ Thần Long...).

3. 'PLACE': Địa danh, không gian:
   - Núi non, sông biển, hồ đầm, thôn xóm, trấn, quận huyện, phủ nha, thành trì, quốc gia, bí cảnh, động phủ, giới diện, cấm địa, đường phố, phường xóm, ngõ ngách, hướng địa lý.
   - PHÂN BIỆT NGUYÊN TẮC DỊCH TRẬT TỰ ĐỊA DANH (PLACE):
     * DẠNG GIỮ NGUYÊN TRẬT TỰ HÁN-VIỆT BẢN SẮC CỔ PHONG (ÁP DỤNG CHO HẦU HẾT ĐỊA DANH): Tất cả núi non, sông biển, hồ đầm, sơn môn, cung điện, lầu các, cấm địa, thành trì, quốc gia BẮT BUỘC giữ nguyên trật tự Hán-Việt chuẩn bản sắc cổ phong (ví dụ: 太和山 -> Thái Hòa Sơn, 紫禁城 -> Tử Cấm Thành, 灵法阁 -> Linh Pháp Các, 少林寺 -> Thiếu Lâm Tự, 长安城 -> Trường An Thành, 洛阳城 -> Lạc Dương Thành, 沧海 -> Thương Hải...).
     * DẠNG ĐẢO TRẬT TỰ TIẾNG VIỆT THUẦN (TUYỆT ĐỐI CHỈ ÁP DỤNG CHO HƯỚNG ĐỊA LÝ & NGÕ NGÁCH ĐỜI THƯỜNG): CHỈ DUY NHẤT từ chỉ phương hướng/vị trí địa lý (đông, tây, nam, bắc) hoặc ngõ ngách đường nhỏ đời thường mới được đảo trật tự để dễ hiểu (ví dụ: 幽州以北 -> phía bắc U Châu, 城东 -> phía đông thành, 青云巷 -> ngõ Thanh Vân, 柳条巷 -> ngõ Liễu Điều...). Tất cả các dạng địa danh khác cấm đảo trật tự!

4. 'SECT': Thế lực, tổ chức:
   - Tông môn, môn phái, thế gia gia tộc, bang hội, triều đình, hoàng triều, cơ quan, quân đoàn, phủ nha.

5. 'ITEM': Vật phẩm, trang bị, bảo vật:
   - Pháp bảo, thần binh, vũ khí, đan dược, linh thảo, linh dược, điển tịch, tác phẩm văn học, điển cố nghệ thuật. Kể cả tên đồ vật, pháp bảo bình dị đều dịch âm Hán-Việt.

6. 'SKILL': Võ học, công pháp:
   - Tuyệt kỹ võ công, tâm pháp tu luyện, thần thông, chiêu thức, trận pháp, bí thuật, cấm thuật.

7. 'OTHER' (BẢN SẮC THỂ LOẠI, THUẬT NGỮ THẾ GIỚI QUAN & CẢNH GIỚI):
   - Cảnh giới tu vi & Thứ bậc cao thủ: Bắt buộc bóc tách (ví dụ: 筑基 -> Trúc Cơ, 金丹 -> Kim Đan, 元婴 -> Nguyên Anh, 后天 -> Hậu Thiên, 先天 -> Tiên Thiên, 宗师 -> Tông Sư, 大宗师 -> Đại Tông Sư, 三流高手 -> Tam Lưu Cao Thủ...).
   - Khái niệm, hiện tượng & Quy ước thế giới quan: (ví dụ: 道侣 -> Đạo Lữ, 走火入魔 -> Tẩu Hỏa Nhập Ma, 夺舍 -> Đoạt Xá, 陨落 -> Vẫn Lạc, 心魔 -> Tâm Ma, 气海 -> Khí Hải, 丹田 -> Đan Điền, 任督二脉 -> Nhâm Đốc Nhị Mạch, 瓶颈 -> Bình Cảnh, 顿悟 -> Đốn Ngộ...).
   - Hệ thống (System), bảng trạng thái, nhiệm vụ: (ví dụ: 大反派系统 -> Đại Phản Phái Hệ Thống, 属性面板 -> Bảng Thuộc Tính...).
   - Thần vị, danh hiệu truyền thuyết, tước vị đặc thù: (ví dụ: 太上老君 -> Thái Thượng Lão Quân, 纯阳真人 -> Thuần Dương Chân Nhân, 镇元大仙 -> Trấn Nguyên Đại Tiên...).
   - Tiền tệ & Tài nguyên đặc thù của thể loại: (ví dụ: 灵石 -> Linh Thạch, 灵钱 -> Linh Tiền...).

CÁC TRƯỜNG HỢP TUYỆT ĐỐI CẤM BÓC TÁCH VÀO BẢNG THỰC THỂ (NEGATIVE CONSTRAINTS):
1. LỜI CẢM ƠN / XIN PHIẾU / PS CỦA TÁC GIẢ Ở CUỐI CHƯƠNG:
   - Tuyệt đối CẤM bóc tách tên người, nick độc giả từ các dòng lời nhắn này vào mảng 'entities'.
   - HÃY TRÍCH XUẤT NGUYÊN VĂN CÁC DÒNG LỜI NHẮN NGOÀI LỀ NÀY ĐƯA VÀO MẢNG 'author_notes' (CHỈ QUÉT Ở ĐOẠN CUỐI CỦA CÁC CHƯƠNG, KHÔNG QUÉT TRONG THÂN BÀI CỐT TRUYỆN).
   - BẢO TOÀN NỘI DUNG TRUYỆN: Chỉ đưa các câu lời nhắn rác của tác giả vào 'author_notes', TUYỆT ĐỐI CẤM đưa bất kỳ câu văn, lời thoại hay diễn biến nào của cốt truyện vào!

2. NHẬN DIỆN CHƯƠNG LỜI TÁC GIẢ HOÀN TOÀN (PURE AUTHOR NOTE CHAPTERS):
   - Quét kỹ toàn bộ các chương trong lô. Nếu có chương nào NỘI DUNG 100% LÀ LỜI CẢM ƠN / XIN PHIẾU / DANH SÁCH DONATE / THÔNG BÁO / LỜI NHẮN NGOÀI LỀ CỦA TÁC GIẢ (ví dụ: '致谢', '感言', '单章', '通知', '说明', hoặc các chương toàn danh sách người ủng hộ không có diễn biến cốt truyện), hãy đưa số chương đó vào mảng JSON "pure_author_note_chapters": [14].

NGUYÊN TẮC DỊCH TÊN CHUẨN XÁC THEO NGỮ CẢNH VĂN HỌC & BÓC TÁCH TOÀN DIỆN:
- NGUYÊN TẮC BÓC TÁCH ĐẦY ĐỦ 100% (KHÔNG PHỤ THUỘC ĐỘ LẶP LẠI): Bắt buộc quét trích xuất TẤT CẢ các thực thể (Tên người, Địa danh, Vật phẩm, Võ học, Linh thú, Cảnh giới...) xuất hiện trong văn bản. Dù thực thể đó CHỈ XUẤT HIỆN 1 LẦN DUY NHẤT hay xuất hiện nhiều lần đều BẮT BUỘC phải bóc tách chi tiết và đầy đủ 100%, TUYỆT ĐỐI KHÔNG ĐƯỢC BỎ SÓT bất kỳ tên riêng nào chỉ vì nó ít lặp lại!
- DỊCH ĐÚNG ÂM HÁN-VIỆT VĂN HỌC BÁC HỌC CHUẨN 100% TIẾNG VIỆT:
  * BẮT BUỘC đọc ngữ cảnh câu chuyện để dịch đúng nghĩa thực thể (không tra từ điển cơ học từng chữ làm sai lệch bản chất vũ khí, sinh vật, chức vị hay địa danh).
  * TUYỆT ĐỐI CẤM phiên âm qua tiếng Trung Pinyin rồi tự ép nối dấu thành tiếng Việt sai âm (CẤM Pinyin 'Shen'->'Sấm', 'Sheng'->'Khốt', 'Jin'->'Cận').
- Tách sạch động từ/tiền tố ngữ pháp đứng liền trước (như 给, 杀, 救, 看, 当, 见...) và trạng từ/hành động đứng liền sau (như 神色, 冷笑, 说道, 喝道...).
- Đánh giá 'evaluation':
  * "TÊN CỐ ĐỊNH": Cho 'NAME', 'PLACE', 'SECT', 'ITEM' (tên riêng cố định 1-1).
  * "NÊN DÙNG BẢN SẮC": Cho 'SKILL', 'CREATURE', 'OTHER'."""


def inject_chapter_extraction_prompts(text: str) -> str:
    """
    Chuẩn hóa văn bản lô cho Dịch Hán: Giữ nguyên thẻ <chapter_N> để LLM nhận diện đúng số chương,
    thân bài nối liền 100% không xuống dòng để tiết kiệm tối đa token.
    """
    if not text:
        return text

    # 1. Nếu văn bản đã có thẻ <chapter_...>:
    if re.search(r"<\s*chapter_[^>]+>", text, re.IGNORECASE):
        chap_blocks = re.findall(r"(<\s*chapter_(\d+)\s*>)([\s\S]*?)(<\s*/\s*chapter_\2\s*>)", text, re.IGNORECASE)
        if chap_blocks:
            result_parts = []
            for open_tag, cno, body, close_tag in chap_blocks:
                clean_body = body.strip()
                chap_block = f"<chapter_{cno}>\n{clean_body}\n</chapter_{cno}>"
                result_parts.append(chap_block)
            return "\n".join(result_parts)

    return text.strip()


async def extract_batch_entities_direct_llm(
    combined_raw_text: str,
    existing_entities: Dict[str, Any] = None,
    return_author_notes: bool = False,
    genre_key: str = "xianxia",
    genre_profile_desc: str = ""
) -> Any:
    """
    Bóc tách thực thể trực tiếp từ toàn văn RAW của lô chương bằng LLM Reasoning (Zero Code Heuristics).
    - Quét trực tiếp toàn bộ các chương trong lô theo đúng Profile Bản Sắc Thể Loại của truyện.
    - Nhận diện đầy đủ 5 phân loại phổ biến + OTHER (tên riêng của mọi thực thể, tên cúng cơm, tên thú cưng nông thôn,
      ngoại hiệu võ học, điển cố, pháp bảo, hệ thống, cảnh giới...).
    - Quy chuẩn chuyển ngữ dễ hiểu: Cấm convert Hán-Việt tối nghĩa khó hiểu cho các từ bản sắc!
    - Quét các câu lời nhắn ngoài lề/cảm ơn/xin phiếu của tác giả ở CUỐI CHƯƠNG và đưa vào 'author_notes'.
    - Tiếp nhận danh sách thực thể đã có từ trước để khóa cứng bản dịch, chống biến đổi tên sau hàng trăm chương.
    - Lặp lại khối mệnh lệnh ở đầu MỖI CHƯƠNG trong lô để LLM không bị trôi chú ý / quên yêu cầu.
    """
    if not combined_raw_text or not combined_raw_text.strip():
        return ([], []) if return_author_notes else []

    model, api_key, is_openrouter, is_grok_local = await _get_llm_config()
    if not api_key and not is_grok_local:
        raise Exception("Không tìm thấy API Key hoặc Grok Server.")

    # MẶC ĐỊNH: Không tự ý bật Unblock, gửi văn bản tự nhiên 100%
    mapping_table = {}
    injected_text = inject_chapter_extraction_prompts(combined_raw_text)

    # Nạp thông tin Profile Thể Loại đặc thù của truyện
    from app.services.translation.profiles import get_profile_description, normalize_profile_key
    norm_genre = normalize_profile_key(genre_key)
    if not genre_profile_desc:
        genre_profile_desc = get_profile_description(norm_genre)

    genre_guidance = ""
    if genre_profile_desc:
        genre_guidance = f"""THÔNG TIN BẢN SẮC & QUY CHUẨN THỂ LOẠI CỦA BỘ TRUYỆN ({norm_genre.upper()}):
Dưới đây là Bảng thuật ngữ bản sắc và định hướng chuyển ngữ của thể loại truyện này:
{genre_profile_desc}

NGUYÊN TẮC BÓC TÁCH VÀ ĐẶT TÊN BẢN SẮC THỂ LOẠI (DỄ HIỂU — CẤM CONVERT THÔ):
1. VÉT CẠN CÁC THỰC THỂ BẢN SẮC: Dựa vào thể loại truyện trên để chủ động tìm và bóc tách đầy đủ các danh từ riêng, cảnh giới tu vi, tuyệt kỹ võ học/công pháp, pháp bảo/trang bị, chủng loài thần thú/dị thú, hệ thống đặc thù (SKILL, ITEM, CREATURE, SECT, OTHER).
2. QUY TẮC DỄ HIỂU — TUYỆT ĐỐI CẤM CONVERT THÔ TỐI NGHĨA:
   - Các từ ngữ bản sắc của thể loại truyện này chỉ nhằm mục đích hỗ trợ LLM dịch nắm bắt đúng không khí thế giới quan để dịch câu văn tự nhiên, mượt mà, dễ hiểu.
   - Khi gợi ý 'vietnamese_name' cho các thực thể mang bản sắc: BẮT BUỘC dùng từ Hán-Việt phổ thông quen thuộc hoặc từ thuần Việt dễ hiểu.
   - TUYỆT ĐỐI CẤM phiên âm thô cứng, ghép âm ngô nghê, tối nghĩa khó hiểu làm ảnh hưởng đến bản dịch và nhịp đọc TTS!
"""

    existing_ref = ""
    if existing_entities:
        ref_lines = []
        for cn, info in existing_entities.items():
            vn = info.get("vietnamese_name", "")
            etype = info.get("entity_type", "NAME")
            role = info.get("role", "")
            ref_lines.append(f"  • {cn} -> {vn} [{etype}] (Vai trò: {role})")

        existing_ref = f"""DANH SÁCH THỰC THỂ ĐÃ XUẤT HIỆN Ở CÁC CHƯƠNG TRƯỚC (BẮT BUỘC DỊCH Y HỆT 100%):
Hệ thống đã tự động rà soát CSDL và phát hiện các thực thể sau ĐÃ TỒN TẠI từ trước và ĐANG XUẤT HIỆN LẠI trong lô chương này:
{chr(10).join(ref_lines)}

MỆNH LỆNH BẮT BUỘC ĐỐI VỚI CÁC THỰC THỂ ĐÃ CÓ NÀY:
1. DỊCH Y HỆT 100%: Dùng đúng 100% bản dịch 'vietnamese_name' đã cho ở trên, TUYỆT ĐỐI CẤM tự ý dịch khác hay sửa đổi tên!
2. VẪN BẮT BUỘC TRẢ VỀ: Nếu các thực thể trên xuất hiện trong lô này, BẮT BUỘC vẫn đưa chúng vào mảng JSON "entities" trả về cùng với các thực thể mới (giữ nguyên 'vietnamese_name' chuẩn).
3. TẬP TRUNG TÌM MỚI: Đồng thời quét kỹ toàn bộ văn bản để bóc tách thêm tất cả các thực thể MỚI chưa có trong danh sách trên!
"""
    else:
        existing_ref = """DANH SÁCH THỰC THỂ ĐÃ XUẤT HIỆN Ở CÁC CHƯƠNG TRƯỚC:
(Chưa có thực thể cũ nào từ trước, đây là các chương đầu tiên. Hãy bóc tách toàn bộ thực thể mới xuất hiện!)
"""

    prompt = f"""Bạn là chuyên gia ngôn ngữ học và dịch thuật tiểu thuyết cao cấp, sở hữu vốn từ vựng Hán - Việt bác học và khả năng phân tích bối cảnh, ngữ pháp sâu sắc.

{genre_guidance}

{existing_ref}

=== VĂN BẢN TOÀN BỘ LÔ CHƯƠNG (LIÊN TỤC KHÔNG XUỐNG DÒNG) ===
{injected_text}

{FULL_EXTRACTION_RULES}

Yêu cầu trả về DUY NHẤT một JSON object theo đúng định dạng sau, không kèm bất kỳ lời dẫn nào:
{{
  "entities": [
    {{"chinese_name": "林冲", "vietnamese_name": "Lâm Xung", "entity_type": "NAME", "evaluation": "TÊN CỐ ĐỊNH", "gender": "male", "role": "nhân vật"}},
    {{"chinese_name": "豹子头", "vietnamese_name": "Báo Tử Đầu", "entity_type": "NAME", "evaluation": "TÊN CỐ ĐỊNH", "gender": "male", "role": "ngoại hiệu của Lâm Xung"}},
    {{"chinese_name": "二狗", "vietnamese_name": "Nhị Cẩu", "entity_type": "NAME", "evaluation": "TÊN CỐ ĐỊNH", "gender": "male", "role": "tên cúng cơm nông thôn"}},
    {{"chinese_name": "金翅大鹏", "vietnamese_name": "Kim Sí Đại Bàng", "entity_type": "CREATURE", "evaluation": "NÊN DÙNG BẢN SẮC", "gender": null, "role": "chủng loài thần thú"}},
    {{"chinese_name": "青云宗", "vietnamese_name": "Thanh Vân Tông", "entity_type": "SECT", "evaluation": "TÊN CỐ ĐỊNH", "gender": null, "role": "tông môn"}},
    {{"chinese_name": "落霞峰", "vietnamese_name": "Lạc Hà Phong", "entity_type": "PLACE", "evaluation": "TÊN CỐ ĐỊNH", "gender": null, "role": "địa danh núi"}},
    {{"chinese_name": "青龙偃月刀", "vietnamese_name": "Thanh Long Yển Nguyệt Đao", "entity_type": "ITEM", "evaluation": "NÊN DÙNG BẢN SẮC", "gender": null, "role": "thần binh binh khí"}},
    {{"chinese_name": "太极拳", "vietnamese_name": "Thái Cực Quyền", "entity_type": "SKILL", "evaluation": "NÊN DÙNG BẢN SẮC", "gender": null, "role": "công pháp võ học"}},
    {{"chinese_name": "筑基", "vietnamese_name": "Trúc Cơ", "entity_type": "OTHER", "evaluation": "NÊN DÙNG BẢN SẮC", "gender": null, "role": "cảnh giới tu vi"}},
    {{"chinese_name": "大宗师", "vietnamese_name": "Đại Tông Sư", "entity_type": "OTHER", "evaluation": "NÊN DÙNG BẢN SẮC", "gender": null, "role": "thứ bậc cao thủ"}},
    {{"chinese_name": "灵石", "vietnamese_name": "Linh Thạch", "entity_type": "OTHER", "evaluation": "NÊN DÙNG BẢN SẮC", "gender": null, "role": "tài nguyên tu luyện"}},
    {{"chinese_name": "大反派系统", "vietnamese_name": "Đại Phản Phái Hệ Thống", "entity_type": "OTHER", "evaluation": "NÊN DÙNG BẢN SẮC", "gender": null, "role": "hệ thống hỗ trợ"}}
  ],
  "author_notes": [
    "求月票，求推荐票！",
    "感谢盟主大佬的支持！"
  ],
  "pure_author_note_chapters": [
    14
  ]
}}
"""
    prompt = re.sub(r'\n{2,}', '\n', prompt.strip())

    text_response = ""
    if is_openrouter:
        url = "https://openrouter.ai/api/v1/chat/completions"
        or_headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:8000",
            "X-Title": "AiRead"
        }
        or_body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 4096,
            "temperature": 0.3
        }
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await post_openrouter_with_retry(client, url, or_headers, or_body)
        if resp.status_code != 200:
            raise Exception(f"OpenRouter API Error (HTTP {resp.status_code}): {resp.text}")
        res_json = resp.json()
        text_response = res_json["choices"][0]["message"]["content"].strip()
    elif is_grok_local:
        grok_url = os.environ.get("AIREAD_GROK_URL") or "http://127.0.0.1:8020/translate-text"
        from app.core.llm_client import post_grok_local_with_retry
        async with httpx.AsyncClient(timeout=180.0) as client:
            res_data = await post_grok_local_with_retry(client, grok_url, {"text": prompt, "timeout": 120.0})
            text_response = res_data.get("translated_text", "").strip()
    else:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        headers = {"Content-Type": "application/json"}
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.3
            }
        }
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await post_gemini_with_retry(client, url, headers, body)
            if resp.status_code != 200:
                raise Exception(f"Lỗi gọi Gemini API (HTTP {resp.status_code}): {resp.text}")
        res_json = resp.json()
        from app.services.unblock.unblock_pipeline import is_response_safety_blocked
        if is_response_safety_blocked(res_json):
            logger.warning("[LLM Extractor] ⚠️ Bị LLM chặn bởi Safety Policy! Tạm thời bật Unblock để vượt qua...")
            from app.services.unblock.unblock_pipeline import mask_text_with_dictionary
            masked_text, mapping_table, _ = await mask_text_with_dictionary(combined_raw_text)
            retry_injected_text = inject_chapter_extraction_prompts(masked_text)
            retry_prompt = prompt.replace(injected_text, retry_injected_text)
            body["contents"] = [{"parts": [{"text": retry_prompt}]}]
            async with httpx.AsyncClient(timeout=180.0) as client:
                resp = await post_gemini_with_retry(client, url, headers, body)
                res_json = resp.json()
        
        candidates = res_json.get("candidates") or []
        if candidates and "content" in candidates[0]:
            text_response = candidates[0]["content"]["parts"][0]["text"].strip()
        else:
            text_response = "{}"

    parsed = safe_json_loads(text_response)
    raw_entities = parsed.get("entities", []) if isinstance(parsed, dict) else []
    raw_author_notes = parsed.get("author_notes", []) if isinstance(parsed, dict) else []

    if mapping_table:
        from app.services.unblock.unblock_pipeline import unmask_text_with_dictionary
        for e in raw_entities:
            if "vietnamese_name" in e and e["vietnamese_name"]:
                e["vietnamese_name"] = unmask_text_with_dictionary(e["vietnamese_name"], mapping_table)

    EXCLUDED_CHINESE_TERMS = {
        "老登", "老家伙", "老头", "小子", "丫头", "老伯", "船公", "掌柜", "大兄弟",
        "老夫", "老朽", "在下", "草民", "小的", "奴才", "老娘", "大爷", "大叔", "大婶",
        "师兄", "师弟", "师姐", "师妹", "师尊", "师父", "掌门", "长老", "宗主", "堂主",
        "县尉", "捕快", "县令", "知府", "尚书", "侍郎", "皇帝", "皇后", "贵妃", "公公"
    }

    final_entities = []
    seen = set()
    for e in raw_entities:
        if not isinstance(e, dict):
            continue
        ch_name = e.get("chinese_name", "").strip()
        vn_name = e.get("vietnamese_name", "").strip()
        if not ch_name or not vn_name or len(ch_name) < 2 or ch_name in seen or ch_name in EXCLUDED_CHINESE_TERMS:
            continue

        e["chinese_name"] = ch_name
        # Khóa cứng 100% bản dịch cũ nếu đã có trong CSDL các chương trước
        if existing_entities and ch_name in existing_entities:
            e["vietnamese_name"] = existing_entities[ch_name].get("vietnamese_name", vn_name)
        else:
            e["vietnamese_name"] = vn_name

        seen.add(ch_name)
        final_entities.append(e)

    final_author_notes = []
    if isinstance(raw_author_notes, list):
        for note in raw_author_notes:
            if isinstance(note, str) and note.strip():
                clean_n = note.strip()
                if mapping_table:
                    clean_n = unmask_text_with_dictionary(clean_n, mapping_table)
                final_author_notes.append(clean_n)

    raw_pure_note_chaps = parsed.get("pure_author_note_chapters", []) if isinstance(parsed, dict) else []
    final_pure_note_chaps = []
    if isinstance(raw_pure_note_chaps, list):
        for c in raw_pure_note_chaps:
            try:
                final_pure_note_chaps.append(int(c))
            except Exception:
                pass

    if return_author_notes:
        return final_entities, final_author_notes, final_pure_note_chaps

    return final_entities


async def extract_entities_via_llm(raw_text: str) -> List[Dict[str, Any]]:
    """Hàm tương thích ngược: Bóc tách thực thể cho đoạn văn bản đơn lẻ."""
    return await extract_batch_entities_direct_llm(raw_text)


async def process_2branch_evidence_via_llm(evidence_data: Dict[str, Any]) -> Dict[str, Any]:
    """Hàm tương thích ngược: chuyển hướng sang bóc tách trực tiếp."""
    candidates = evidence_data.get("branch_1_ner_candidates", [])
    raw_texts = [c.get("context_han", "") for c in candidates if c.get("context_han")]
    combined = "\n".join(raw_texts)
    existing = evidence_data.get("existing_db_entities", {})
    ents = await extract_batch_entities_direct_llm(combined, existing)
    return {"entities": ents}
