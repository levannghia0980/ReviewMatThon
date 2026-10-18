# -*- coding: utf-8 -*-
"""
app/services/tts/tts_factory.py
=================================
TTSFactory – Diem truy cap duy nhat de chon engine TTS theo settings.

CACH DUNG:
    from app.services.step_04_tts.tts_factory import TTSFactory
    tts = TTSFactory.get_service()   # Doc settings.TTS_ENGINE, tra ve class phu hop
    result = tts.produce_project_voiceover(...)

KHONG BAO GIO viet if/else chon CapCut vs TikTok ngoai Factory nay.
"""

import logging
from app.config import settings

logger = logging.getLogger(__name__)


class TTSFactory:
    """
    Factory don gian cung cap class TTS Service theo bien moi truong TTS_ENGINE.

    TTS_ENGINE trong .env:
      - "capcut"  (mac dinh) -> CapCutTTSService
      - "tiktok"             -> TikTokTTSService

    Moi engine deu co cung interface cong khai:
      - produce_project_voiceover(task_id, project_id, db, voice_code, ...)
      - synthesize_chunk(text, voice_code, ...)
    """

    @staticmethod
    def get_service():
        """
        Tra ve class TTS Service dua tren settings.TTS_ENGINE.
        Import lazy de tranh circular import va khoi dong cham.
        """
        engine = getattr(settings, "TTS_ENGINE", "capcut").lower().strip()

        if engine in ("tiktok", "tiktok_legacy"):
            from app.services.step_04_tts.tiktok_tts_service import TikTokTTSService
            logger.info(f"[TTSFactory] Su dung engine: TikTok TTS (engine='{engine}')")
            return TikTokTTSService

        # Mac dinh: CapCut Cloud SAMI
        from app.services.step_04_tts.capcut_tts_service import CapCutTTSService
        logger.info(f"[TTSFactory] Su dung engine: CapCut TTS (engine='{engine}')")
        return CapCutTTSService

    @staticmethod
    def get_engine_name() -> str:
        """Tra ve ten engine hien tai (de log/UI hien thi)."""
        engine = getattr(settings, "TTS_ENGINE", "capcut").lower().strip()
        if engine in ("tiktok", "tiktok_legacy"):
            return "TikTok TTS (ByteDance)"
        return "CapCut Cloud TTS (SAMI)"

    @staticmethod
    def is_capcut() -> bool:
        """Kiem tra nhanh co dang dung CapCut khong."""
        return getattr(settings, "TTS_ENGINE", "capcut").lower().strip() not in ("tiktok", "tiktok_legacy")
