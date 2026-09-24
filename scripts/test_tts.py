import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

# UTF-8 output
sys.stdout.reconfigure(encoding='utf-8')

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from app.services.tts.tiktok_tts_service import TikTokTTSService

sample_texts = [
    "Trương Tiểu Phàm từ nhỏ đã sống tại chân núi Thanh Vân.",
    "Một ngày nọ, thôn làng của hắn gặp phải đại nạn ngập trời.",
    "Hắn cùng người bạn Điền Linh Nhi bái nhập môn hạ Đại Trúc Phong.",
    "Sư phụ Điền Bất Dịch nhìn bề ngoài lạnh lùng nhưng rất quan tâm đệ tử.",
    "Trong lúc tình cờ, Tiểu Phàm nhặt được Phệ Huyết Châu và Thiêu Hỏa Côn.",
    "Binh khí này kết hợp thành pháp bảo tà dị vô song.",
    "Tại đại hội Thất Mạch Hội Võ, hắn đã bộc lộ thực lực kinh người.",
    "Gặp gỡ Lục Tuyết Kỳ cùng Bích Dao, mở ra mối duyên tiền định.",
    "Trải qua muôn vàn trắc trở, tâm tính của hắn vẫn trước sau như một.",
    "Chính ma đại chiến nổ ra, số phận của hắn bước sang ngã rẽ mới."
] * 3  # 30 câu thoại thực tế

def test_synthesis(workers=12):
    print(f"==================================================")
    print(f"🚀 BẮT ĐẦU KIỂM TRA THỰC TẾ: {len(sample_texts)} CÂU THOẠI VỚI {workers} LUỒNG")
    print(f"==================================================")
    
    t0 = time.time()
    
    def worker_func(idx, text):
        t_start = time.time()
        seg = TikTokTTSService.synthesize_sentence(
            text=text,
            voice_code="vi_female_huong",
            apply_mastering=True
        )
        duration_ms = len(seg)
        t_cost = time.time() - t_start
        return idx, duration_ms, t_cost
    
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(worker_func, i, txt) for i, txt in enumerate(sample_texts)]
        results = [f.result() for f in futures]
    
    elapsed = time.time() - t0
    success_count = sum(1 for _, dur, _ in results if dur > 200)
    
    print(f"\n✔ KẾT QUẢ: Thành công {success_count}/{len(sample_texts)} câu thoại!")
    print(f"⏱ TỔNG THỜI GIAN: {elapsed:.2f} giây")
    print(f"⚡ TỐC ĐỘ TRUNG BÌNH: {elapsed/len(sample_texts):.2f} giây / câu")
    print(f"\n📊 CHI TIẾT 5 CÂU ĐẦU TIÊN:")
    for idx, dur, cost in results[:5]:
        print(f"  • Câu {idx+1}: {sample_texts[idx][:35]}... -> {dur}ms (Tạo trong {cost:.2f}s)")
    print(f"==================================================")

if __name__ == "__main__":
    test_synthesis(workers=12)
