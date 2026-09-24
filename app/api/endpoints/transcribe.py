import os
from pathlib import Path
from fastapi import APIRouter, HTTPException
from app.schemas.transcript import (
    TranscribeRequest,
    TranscribeResponse,
    CleanTextRequest,
    CleanTextResponse
)
from app.services.audio_extractor import AudioExtractorService
from app.services.whisper_service import WhisperService
from app.services.text_cleaner import TextCleanerService

router = APIRouter()

@router.post("/process", response_model=TranscribeResponse, summary="Bóc tách lời thoại AI từ file Video hoặc Audio")
def transcribe_media(req: TranscribeRequest):
    try:
        src_path = req.audio_or_video_path
        if not os.path.exists(src_path):
            raise HTTPException(status_code=404, detail=f"Không tìm thấy file: {src_path}")
            
        # Nếu là file video MP4/MKV -> tự động tách sang 16kHz WAV
        ext = Path(src_path).suffix.lower()
        if ext in [".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv"]:
            audio_path = AudioExtractorService.extract_audio_16k_wav(src_path)
        else:
            audio_path = src_path

        segments, srt_path, txt_path, json_path = WhisperService.transcribe(
            audio_path=audio_path,
            language=req.language,
            model_size=req.model_size,
            clean_text=req.clean_text
        )

        total_dur = segments[-1].end if segments else 0.0

        return TranscribeResponse(
            status="success",
            language=req.language,
            total_segments=len(segments),
            total_duration=round(total_dur, 2),
            segments=segments,
            srt_path=srt_path,
            txt_path=txt_path,
            json_path=json_path
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/clean", response_model=CleanTextResponse, summary="Làm sạch danh sách câu thoại (loại bỏ từ đệm, tạp âm)")
def clean_transcript(req: CleanTextRequest):
    try:
        cleaned = TextCleanerService.clean_segments(req.segments, is_chinese=True)
        return CleanTextResponse(
            status="success",
            original_count=len(req.segments),
            cleaned_count=len(cleaned),
            segments=cleaned
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
