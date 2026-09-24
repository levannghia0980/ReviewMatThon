from fastapi import APIRouter
from app.api.endpoints import video, transcribe, pipeline, project, settings, translate, tts, composer

api_router = APIRouter()

api_router.include_router(project.router, prefix="/projects", tags=["0. Database & Projects (SQLite)"])
api_router.include_router(pipeline.router, prefix="/pipeline", tags=["1. Full Pipelines"])
api_router.include_router(translate.router, prefix="/translate", tags=["2. AI Translation (AIREAD Core)"])
api_router.include_router(tts.router, prefix="/tts", tags=["3. AI Voiceover TTS (TikTok Engine)"])
api_router.include_router(composer.router, prefix="/composer", tags=["4. Video Studio & Karaoke Composer"])
api_router.include_router(video.router, prefix="/video", tags=["5. Video & Downloader (Input)"])
api_router.include_router(transcribe.router, prefix="/transcribe", tags=["6. Speech-to-Text & Clean (Output)"])
api_router.include_router(settings.router, prefix="/settings", tags=["7. System Settings"])
