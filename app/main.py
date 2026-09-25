import os
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# Tự động nạp PATH cho ffmpeg, ffprobe, yt-dlp trong tools/
try:
    from app.utils.bin_helper import setup_system_path
    setup_system_path()
except Exception:
    pass

# Tắt cảnh báo ConnectionResetError [WinError 10054] vô hại khi trình duyệt ngắt socket stream video
import sys
if sys.platform == "win32":
    try:
        from asyncio.proactor_events import _ProactorBasePipeTransport
        _orig_call_connection_lost = _ProactorBasePipeTransport._call_connection_lost

        def _silenced_call_connection_lost(self, exc):
            try:
                _orig_call_connection_lost(self, exc)
            except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
                pass
            except Exception:
                pass

        _ProactorBasePipeTransport._call_connection_lost = _silenced_call_connection_lost
    except Exception:
        pass

from app.config import settings
from app.db.session import init_db
from app.api.api_v1 import api_router

FRONTEND_DIST_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"
FRONTEND_DIST_DIR.mkdir(parents=True, exist_ok=True)

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="""
    ## ⚡ ReviewMatThon Video Dubbing & Translation Backend (React Studio UI)
    Hệ thống Backend FastAPI kết hợp SQLite Database & React Frontend:
    - Phân trang nhẹ nhàng, chống tràn RAM/ổ đĩa.
    - Cào video 720p/1080p Bilibili, tách âm thanh 16kHz, AI Faster-Whisper.
    """,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Gắn API Routers
app.include_router(api_router, prefix=settings.API_V1_STR)

# Phục vụ Static Files từ React Build (frontend/dist/assets)
assets_dir = FRONTEND_DIST_DIR / "assets"
if assets_dir.exists():
    app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

@app.get("/api/health", tags=["Health Check"])
def health_check():
    return {
        "status": "online",
        "project": settings.PROJECT_NAME,
        "database": "SQLite (database/review_mat_thon.db)",
        "input_dir": str(settings.INPUT_DIR),
        "output_dir": str(settings.OUTPUT_DIR)
    }

@app.get("/{full_path:path}", include_in_schema=False)
def serve_react_spa(full_path: str):
    # Nếu file tồn tại trong dist, phục vụ trực tiếp
    target_file = FRONTEND_DIST_DIR / full_path
    if target_file.is_file():
        return FileResponse(str(target_file))
    
    # Mặc định trả về index.html của React SPA
    index_file = FRONTEND_DIST_DIR / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
        
    return {
        "status": "online",
        "message": "React Frontend đang được build...",
        "docs_url": "/docs"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
