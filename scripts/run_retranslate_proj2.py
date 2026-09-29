import os
import sys
import asyncio
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding='utf-8')
from app.db.session import SessionLocal
from app.services.translation.translation_pipeline import TranslationPipelineService

async def main():
    db = SessionLocal()
    try:
        print("Đang khởi chạy TranslationPipelineService cho Project 2...")
        res = await TranslationPipelineService.translate_project_dialogues(
            task_id="trans_proj2_airead",
            project_id=2,
            db=db,
            genre="xianxia",
            batch_size=300,
            provider="gemini"
        )
        print("=== DỊCH THUẬT HOÀN TẤT THÀNH CÔNG ===")
        print("Kết quả:", res)
    except Exception as e:
        import traceback
        traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    asyncio.run(main())
