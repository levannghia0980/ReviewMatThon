/**
 * Danh sách 7 thể loại chuẩn mực duy nhất từ AIRead.
 * Đồng bộ 100% với profiles.py ở Backend.
 */
export const AIREAD_GENRES = [
  {
    code: "xianxia",
    name: "Tiên Hiệp / Cổ Trang / Huyền Huyễn",
    icon: "☯️",
    desc: "Ta/Ngươi, Hắn/Nàng, Trúc Cơ, Đan Điền, Tông Môn, Cấm anh-em"
  },
  {
    code: "wuxia",
    name: "Võ Lâm / Kiếm Hiệp",
    icon: "⚔️",
    desc: "Huynh/Đệ, Tỷ/Muội, Gia Tộc, Chưởng Môn, Giang hồ ân oán"
  },
  {
    code: "urban",
    name: "Đô Thị / Hiện Đại / Thương Chiến",
    icon: "🏙️",
    desc: "Tôi/Cậu/Anh/Em, Bố/Mẹ, Sếp/Chủ tịch, Công ty, Xe hơi"
  },
  {
    code: "urban_supernatural",
    name: "Linh Dị / Dị Năng / Cao Võ / Phong Thủy",
    icon: "🕯️",
    desc: "Tôi/Cậu/Mày/Tao, Vớt xác, Bắt ma, Trộm mộ, Dị năng"
  },
  {
    code: "romance",
    name: "Ngôn Tình / Điền Văn / Cung Đấu",
    icon: "💕",
    desc: "Chàng/Thiếp, Hoàng thượng/Thần thiếp, Lão thái thái, Điền viên mộc mạc"
  },
  {
    code: "system_reincarnation",
    name: "Hệ Thống / Trọng Sinh / Xuyên Không",
    icon: "⚡",
    desc: "Ký chủ, Tích điểm, 【Đinh!】, Nhiệm vụ, Thấu thị tương lai, Vô địch lưu"
  },
  {
    code: "sci_fi_apocalypse",
    name: "Mạt Thế / Khoa Huyễn / Viễn Tưởng",
    icon: "🚀",
    desc: "Zombie, Tang thi, Dị biến, Chỉ huy, Căn cứ sinh tồn, Tinh tế, Cơ giáp"
  }
];

export const DEFAULT_GENRE = "xianxia";
