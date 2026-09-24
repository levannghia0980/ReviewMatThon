"""
Bridge config module for backward compatibility with older services/modules.
"""
import os
from typing import Any
from app.config import settings, BASE_DIR, OUTPUT_DIR, DB_PATH

PROJECT_ROOT = settings.BASE_DIR

async def get_active_setting(key: str, default: Any = "") -> Any:
    """Lấy setting từ settings object hoặc biến môi trường os.environ."""
    val = getattr(settings, key, None)
    if val is not None and val != "":
        return val
    return os.environ.get(key, default)
