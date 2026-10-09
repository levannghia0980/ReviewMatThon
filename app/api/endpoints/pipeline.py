import os
from pathlib import Path
import uuid
import threading
import asyncio
import concurrent.futures
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session
from app.db.session import SessionLocal, get_db
from app.models.project import ProjectTask
from app.models.dialogue import DialogueSegmentModel
from app.schemas.pipeline import (
    IngestPipelineRequest,
    FullAutoPipelineRequest,
    IngestPipelineResponse,
    AsyncTaskResponse,
    TaskStatusResponse,
    ExtractSTTRequest
)
from app.schemas.transcript import DialogueSegment
from app.services.downloader_service import DownloaderService
from app.services.audio_extractor import AudioExtractorService
from app.services.whisper_service import WhisperService
from app.services.translation.translation_pipeline import TranslationPipelineService
from app.services.tts.capcut_tts_service import CapCutTTSService
from app.services.tts.tiktok_tts_service import TikTokTTSService
from app.services.video.video_composer_service import VideoComposerService
from app.services.video.karaoke_subtitle_service import KaraokeSubtitleService
from app.services.video.audio_mixer_service import AudioMixerService
from app.services.task_manager import task_manager
from app.config import settings

router = APIRouter()

def _run_full_auto_worker(task_id: str, req: FullAutoPipelineRequest):
    """Worker chạy trọn gói từ A-Z: Tải video -> Bóc tách thoại -> Dịch AIREAD -> Quét lỗi Hán -> TTS TikTok -> Render Video"""
    db = SessionLocal()
    try:
        task_manager.update_task(task_id, status="running", step=1, progress=2)
        task_manager.add_log(task_id, "🚀 BẮT ĐẦU QUY TRÌNH TỰ ĐỘNG HÓA 100% (FULL-AUTO PIPELINE)", "purple")

        project_id = req.project_id
        project = None

        # BƯỚC 1: Nếu chưa có project_id thì Tải Video & STT
        if not project_id:
            if not req.url:
                raise ValueError("Cần cung cấp URL video hoặc Project ID có sẵn!")
            
            task_manager.add_log(task_id, f"[1/5] 📥 [1-Shot] Đang tải video từ: {req.url}", "cyan")
            video_path, info = DownloaderService.download_video(url=req.url, quality=req.quality)
            task_manager.add_log(task_id, f"   ✔ Video đã tải: {info['title']}", "emerald")

            task_manager.update_task(task_id, step=1, progress=10)
            task_manager.add_log(task_id, "[1/5] 🎵 Trích xuất Audio 16kHz PCM WAV...", "cyan")
            audio_path = AudioExtractorService.extract_audio_16k_wav(video_path)

            task_manager.update_task(task_id, step=1, progress=14)
            task_manager.add_log(task_id, "[1/5] 🎙️ Đang lọc sạch tạp âm & khử ồn nền trước khi gửi CapCut ASR...", "cyan")
            clean_audio_path = AudioExtractorService.get_clean_audio_for_asr(audio_path)
            task_manager.add_log(task_id, f"   ✔ Đã làm sạch âm thanh (Vocal Denoise) -> Sẵn sàng bóc tách.", "emerald")

            engine = getattr(settings, "ASR_ENGINE", "capcut").lower()
            engine_name = "CapCut Cloud STT (ByteDance - Chuẩn từng mili-giây)" if engine == "capcut" else "Groq Whisper Cloud"
            task_manager.update_task(task_id, step=1, progress=18)
            task_manager.add_log(task_id, f"[1/5] 🤖 Đang bóc tách lời thoại & timecode bằng {engine_name}...", "cyan")
            dialogues, srt_path, txt_path, json_path = WhisperService.transcribe(
                audio_path=clean_audio_path,
                language=req.source_language,
                clean_text=True,
                task_id=task_id
            )
            task_manager.add_log(task_id, f"   ✔ Đã bóc tách {len(dialogues)} câu thoại gốc.", "emerald")

            # Lưu DB
            project = ProjectTask(
                video_id=info["id"],
                title=info["title"],
                source_url=req.url,
                duration=info["duration"],
                status="TRANSCRIBED",
                source_language=req.source_language,
                video_path=video_path,
                audio_path=audio_path,
                srt_path=srt_path,
                txt_path=txt_path,
                json_path=json_path
            )
            db.flush()

            # Xóa sạch các câu thoại cũ nếu có (chống nhân đôi câu)
            db.query(DialogueSegmentModel).filter(DialogueSegmentModel.task_id == project.id).delete(synchronize_session=False)
            db.commit()
            for obj in list(db.identity_map.values()):
                if isinstance(obj, DialogueSegmentModel):
                    db.expunge(obj)

            for d in dialogues:
                db.add(DialogueSegmentModel(
                    task_id=project.id,
                    index=d.id,
                    start_time=d.start,
                    end_time=d.end,
                    duration=d.duration,
                    original_text=d.text,
                    clean_text=d.clean_text,
                    confidence=d.confidence,
                    status="RAW"
                ))
            db.commit()
            db.refresh(project)
            project_id = project.id
            task_manager.add_log(task_id, f"   ✔ Đã tạo Project #{project_id} trong SQLite CSDL.", "emerald")
        else:
            project = db.query(ProjectTask).filter(ProjectTask.id == project_id).first()
            if not project:
                raise ValueError(f"Không tìm thấy Project #{project_id}")
            task_manager.add_log(task_id, f"✔ Sử dụng Project #{project_id} có sẵn: {project.title}", "emerald")

            # Tự động kiểm tra và tải lại nếu file video gốc bị thiếu trên ổ đĩa
            if not project.video_path or not os.path.exists(project.video_path):
                if project.source_url:
                    task_manager.add_log(task_id, f"⚠️ File video gốc bị thiếu trên ổ đĩa. Đang tự động tải lại từ nguồn...", "amber")
                    downloaded_vpath, _ = DownloaderService.download_video(project.source_url)
                    project.video_path = downloaded_vpath
                    db.commit()
                    task_manager.add_log(task_id, f"✔ Đã tải video gốc thành công: {Path(downloaded_vpath).name}", "emerald")
                else:
                    raise FileNotFoundError(f"Không tìm thấy file video gốc trên ổ đĩa: {project.video_path}")

            # KIỂM TRA BỔ SUNG: Nếu Project chưa có câu thoại trong CSDL (do vừa Reset về lúc mới tải xong hoặc chưa STT)
            existing_dialogues_count = db.query(DialogueSegmentModel).filter(DialogueSegmentModel.task_id == project_id).count()
            if existing_dialogues_count == 0:
                task_manager.update_task(task_id, step=1, progress=10)
                task_manager.add_log(task_id, "[1/5] 🎵 Video chưa có câu thoại trong CSDL. Đang trích xuất Audio 16kHz PCM WAV...", "cyan")
                raw_audio_path = AudioExtractorService.extract_audio_16k_wav(project.video_path)
                project.audio_path = raw_audio_path
                db.commit()

                # Lọc sạch tạp âm RIÊNG cho CapCut ASR để nhận diện chuẩn, không ảnh hưởng đến âm thanh video gốc
                task_manager.update_task(task_id, step=1, progress=14)
                task_manager.add_log(task_id, "[1/5] 🎙️ Đang lọc sạch tạp âm & khử ồn nền trước khi gửi CapCut ASR...", "cyan")
                clean_audio_path = AudioExtractorService.get_clean_audio_for_asr(raw_audio_path)
                task_manager.add_log(task_id, f"   ✔ Đã làm sạch âm thanh (Vocal Denoise) -> Sẵn sàng bóc tách.", "emerald")

                engine = getattr(settings, "ASR_ENGINE", "capcut").lower()
                engine_name = "CapCut Cloud STT (ByteDance - Chuẩn từng mili-giây)" if engine == "capcut" else "Groq Whisper Cloud"
                task_manager.update_task(task_id, step=1, progress=18)
                task_manager.add_log(task_id, f"[1/5] 🤖 Đang bóc tách lời thoại & timecode bằng {engine_name}...", "cyan")
                dialogues, srt_path, txt_path, json_path = WhisperService.transcribe(
                    audio_path=clean_audio_path,
                    language=req.source_language or project.source_language or "zh",
                    clean_text=True,
                    task_id=task_id
                )
                task_manager.add_log(task_id, f"   ✔ Đã bóc tách {len(dialogues)} câu thoại gốc.", "emerald")

                project.srt_path = srt_path
                project.txt_path = txt_path
                project.json_path = json_path
                project.status = "TRANSCRIBED"
                db.commit()

                # Xóa sạch các câu thoại cũ trước khi lưu (chống nhân đôi câu)
                db.query(DialogueSegmentModel).filter(DialogueSegmentModel.task_id == project.id).delete(synchronize_session=False)
                db.commit()
                for obj in list(db.identity_map.values()):
                    if isinstance(obj, DialogueSegmentModel):
                        db.expunge(obj)

                for d in dialogues:
                    db.add(DialogueSegmentModel(
                        task_id=project.id,
                        index=d.id,
                        start_time=d.start,
                        end_time=d.end,
                        duration=d.duration,
                        original_text=d.text,
                        clean_text=d.clean_text,
                        confidence=d.confidence,
                        status="RAW"
                    ))
                db.commit()
                task_manager.add_log(task_id, f"   ✔ Đã lưu {len(dialogues)} câu thoại vào SQLite CSDL.", "emerald")

        # BƯỚC 2: Dịch Thuật AIREAD Đa Tầng (Tự động bỏ qua nếu đã dịch xong trước đó)
        total_dialogues = db.query(DialogueSegmentModel).filter(DialogueSegmentModel.task_id == project_id).count()
        if total_dialogues == 0:
            raise RuntimeError(f"Project #{project_id} không có câu thoại nào để dịch hoặc lồng tiếng! Vui lòng kiểm tra lại file âm thanh/video gốc.")

        untranslated_count = db.query(DialogueSegmentModel).filter(
            DialogueSegmentModel.task_id == project_id,
            (DialogueSegmentModel.translated_text == None) | (DialogueSegmentModel.translated_text == "")
        ).count()

        trans_res = {}
        if untranslated_count > 0:
            task_manager.update_task(task_id, step=2, progress=25)
            task_manager.add_log(task_id, f"[2/5] 🧠 Bắt đầu Dịch thuật AIREAD ({untranslated_count}/{total_dialogues} câu cần dịch)...", "cyan")
            
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            trans_res = loop.run_until_complete(
                TranslationPipelineService.translate_project_dialogues(
                    task_id=task_id,
                    project_id=project_id,
                    db=db,
                    genre=req.genre,
                    batch_size=req.batch_size or settings.TRANSLATION_BATCH_SIZE or 500,
                    max_chars=settings.TRANSLATION_MAX_CHARS or 50000,
                    provider=req.provider
                )
            )
            task_manager.add_log(task_id, f"   ✔ Dịch thuật & Quét lỗi Hán hoàn tất ({trans_res.get('total_segments', 0)} câu).", "emerald")
        else:
            task_manager.update_task(task_id, step=2, progress=50)
            task_manager.add_log(task_id, f"[2/5] ✔ Video đã có bản dịch hoàn chỉnh ({total_dialogues} câu). Bỏ qua dịch lại -> Chuyển sang TTS & Render.", "emerald")

        # BƯỚC 3 & 4: CHẠY SONG SONG 100% (PARALLEL MULTI-THREADING TĂNG TỐC 50%)
        task_manager.update_task(task_id, step=3, progress=55)
        task_manager.add_log(task_id, "[3/4] 🚀 KÍCH HOẠT CHẠY SONG SONG 2 LUỒNG (TIẾT KIỆM 50% THỜI GIAN):", "purple")
        task_manager.add_log(task_id, f"   • Luồng 1 (Mạng & Audio): 🎙️ Gọi TikTok TTS ({req.voice_code}) + 🎵 Mix Vocal Ducking BGM", "cyan")
        task_manager.add_log(task_id, "   • Luồng 2 (GPU Video): 🎬 GPU Render Subtitle Karaoke + Vùng che chữ gốc", "cyan")

        safe_title = "".join(c for c in project.title if c.isalnum() or c in (' ', '_', '-')).strip()
        if not safe_title:
            safe_title = f"project_{project.id}"

        # 1. Tạo file Karaoke ASS trước
        db.commit()
        ass_file = settings.OUTPUT_TRANSCRIPTS_DIR / f"{project.video_id}_karaoke.ass"
        dialogues_db = db.query(DialogueSegmentModel).filter(
            DialogueSegmentModel.task_id == project_id
        ).order_by(DialogueSegmentModel.index.asc()).all()

        # Khử trùng lặp tuyệt đối theo index để tránh bị vẽ đè 2 tầng sub
        seen_d_idx = set()
        unique_dialogues_db = []
        for d in dialogues_db:
            if d.index not in seen_d_idx:
                seen_d_idx.add(d.index)
                unique_dialogues_db.append(d)
        dialogues_db = unique_dialogues_db

        if not dialogues_db:
            raise RuntimeError(f"Project #{project_id} không có câu thoại nào trong CSDL để lồng tiếng hay tạo phụ đề!")

        segments = [
            DialogueSegment(
                id=d.index,
                start=d.start_time,
                end=d.end_time,
                duration=d.duration,
                text=d.original_text,
                clean_text=d.clean_text or d.original_text,
                translated_text=d.translated_text or d.clean_text or d.original_text,
                confidence=d.confidence
            )
            for d in dialogues_db
        ]

        vw, vh = VideoComposerService.get_video_resolution(project.video_path)
        crop_ratio = getattr(req, "crop_ratio", "16:9")
        has_mask = getattr(req, "has_mask", True)
        cut_pct = getattr(req, "bottom_cut_percent", None)
        if cut_pct is None:
            cut_pct = getattr(req, "mask_height", 12.0)
        t_cut_pct = float(getattr(req, "top_cut_percent", 0.0) or 0.0)

        if has_mask or (cut_pct and cut_pct > 0) or t_cut_pct > 0:
            crop_info = VideoComposerService.calculate_crop(vw, vh, cut_pct, top_cut_percent=t_cut_pct, target_ratio=crop_ratio)
            ass_w = crop_info["w"]
            ass_h = crop_info["h"]
        else:
            ass_w = (vw // 2) * 2
            ass_h = (vh // 2) * 2

        font_sz = getattr(req, "font_size", 14) or 14
        blur_h_val = float(getattr(req, "blur_height", 13.0) or 13.0)
        sub_offset_val = float(getattr(req, "sub_bottom_offset", 0.0) or 0.0)
        margin_v_val = getattr(req, "margin_v", None)
        if margin_v_val is None:
            margin_v_val = max(2, int(round(sub_offset_val + (blur_h_val * 0.22))))
        box_pad_val = getattr(req, "box_padding", 8) if getattr(req, "box_padding", None) is not None else 8

        raw_highlight = getattr(req, "karaoke_highlight_color", None)
        highlight_c = raw_highlight if (raw_highlight and raw_highlight != "&H00EB6325") else "&H000000FF"

        render_subs = getattr(req, "render_subtitles", True)

        if render_subs:
            task_manager.add_log(task_id, "[1/4] ✨ Đang sinh file phụ đề Karaoke ASS từng từ (Word-by-word Highlight)...", "cyan")
            KaraokeSubtitleService.create_karaoke_ass_file(
                segments=segments,
                output_ass_path=str(ass_file),
                video_title=project.title,
                width=ass_w,
                height=ass_h,
                font_size=font_sz,
                highlight_color=highlight_c,
                backdrop_opacity_hex=req.backdrop_opacity_hex,
                margin_v=margin_v_val,
                box_style=getattr(req, "box_style", "white_box"),
                box_padding=box_pad_val,
                blur_height=blur_h_val,
                sub_bottom_offset=sub_offset_val
            )
        else:
            task_manager.add_log(task_id, "[1/4] ⚡ Chế độ 'Không Sub': Bỏ qua tạo phụ đề Karaoke & dải mờ, chỉ Crop khung hình & lồng tiếng!", "cyan")
            if not ass_file.exists():
                with open(ass_file, "w", encoding="utf-8") as f:
                    f.write("")

        temp_visual_video = settings.OUTPUT_FINAL_VIDEOS_DIR / f"temp_{project.video_id}_visual.mp4"
        if temp_visual_video.exists():
            try:
                temp_visual_video.unlink(missing_ok=True)
            except Exception:
                pass

        mixed_audio_file = settings.OUTPUT_VOICEOVER_DIR / f"{project.video_id}_mixed_final.mp3"
        output_final_path = settings.OUTPUT_FINAL_VIDEOS_DIR / f"{project.video_id}_{safe_title}_final.mp4"

        audio_res_container = {}
        video_res_container = {}

        def _audio_worker():
            db_audio = SessionLocal()
            try:
                # TTS: Tự động kiểm tra file voiceover đã xuất thành công trước đó để tránh lãng phí thời gian tạo lại hàng ngàn câu thoại
                safe_title = "".join(c for c in project.title if c.isalnum() or c in (' ', '_', '-')).strip() or f"project_{project.id}"
                cached_master = settings.OUTPUT_VOICEOVER_DIR / f"{project.video_id}_{safe_title}_voiceover.mp3"
                force_regen = not (cached_master.exists() and cached_master.stat().st_size > 500_000)
                if not force_regen:
                    task_manager.add_log(task_id, f"⚡ Phát hiện file Voiceover hoàn chỉnh đã có sẵn ({cached_master.name}). Bỏ qua bước chờ TTS!", "emerald")

                engine = getattr(settings, "TTS_ENGINE", "capcut").lower()
                if engine in ("capcut", "capcut_cloud"):
                    tts_res = CapCutTTSService.produce_project_voiceover(
                        task_id=task_id,
                        project_id=project_id,
                        db=db_audio,
                        voice_code=req.voice_code,
                        apply_mastering=True,
                        auto_fit_timeline=True,
                        max_workers=settings.TTS_MAX_WORKERS or 96,
                        force_regenerate=force_regen
                    )
                else:
                    tts_res = TikTokTTSService.produce_project_voiceover(
                        task_id=task_id,
                        project_id=project_id,
                        db=db_audio,
                        voice_code=req.voice_code,
                        apply_mastering=True,
                        auto_fit_timeline=True,
                        max_workers=settings.TTS_MAX_WORKERS or 96,
                        force_regenerate=force_regen
                    )
                voiceover_file = tts_res.get("audio_path") or tts_res.get("master_voice_path")
                
                # MIX NHẠC NỀN BGM & HIỆU ỨNG ÂM THANH (CHỈ THAY THOẠI, GIỮ NGUYÊN BGM & SFX)
                if voiceover_file and os.path.exists(voiceover_file):
                    task_manager.add_log(task_id, "   🎵 Đang hòa trộn BGM gốc & lồng tiếng Việt (Vocal Ducking)...", "cyan")
                    mixed_path = AudioMixerService.mix_bgm_and_voiceover(
                        original_video_or_audio=project.video_path,
                        voiceover_mp3=str(voiceover_file),
                        output_mixed_audio=str(mixed_audio_file),
                        dialogue_segments=segments,
                        bgm_volume_when_speaking=0.036,
                        bgm_volume_normal=0.15,  # 0.15 khi không nói để giữ rõ âm thanh môi trường/BGM, 0.036 khi nói để giấu tiếng Trung
                        voiceover_volume=1.05
                    )
                    audio_res_container["audio_path"] = str(mixed_path)
                    audio_res_container["success"] = True
                    task_manager.add_log(task_id, "   ✔ [Luồng Audio] Đã lồng tiếng Việt & giữ BGM hoàn tất.", "emerald")
                else:
                    err_msg = tts_res.get("error") or "Không tạo được file lồng tiếng TikTok TTS!"
                    raise RuntimeError(f"Lỗi tạo giọng lồng tiếng: {err_msg}")
            except Exception as e:
                audio_res_container["error"] = str(e)
                task_manager.add_log(task_id, f"   ❌ [Lỗi Audio] {str(e)}", "rose")
                task_manager.cancel_task(task_id)  # Dừng ngay video worker để giải phóng GPU/CPU
            finally:
                db_audio.close()

        def _video_worker():
            try:
                v_ok = VideoComposerService.render_visual_stream(
                    task_id=task_id,
                    video_input_path=project.video_path,
                    ass_file_path=str(ass_file),
                    output_temp_video=str(temp_visual_video),
                    logo_position=req.logo_position,
                    logo_size=req.logo_size,
                    channel_name=req.channel_name,
                    channel_opacity=req.channel_opacity,
                    has_mask=req.has_mask,
                    mask_top=req.mask_top,
                    mask_left=req.mask_left,
                    mask_width=req.mask_width,
                    mask_height=cut_pct,
                    backdrop_opacity_hex=req.backdrop_opacity_hex,
                    target_ratio=crop_ratio,
                    bottom_cut_percent=cut_pct,
                    top_cut_percent=t_cut_pct,
                    blur_height=blur_h_val,
                    sub_bottom_offset=sub_offset_val,
                    render_subtitles=render_subs
                )
                if not v_ok or not temp_visual_video.exists() or temp_visual_video.stat().st_size < 1000:
                    raise RuntimeError(f"GPU/FFmpeg render video thất bại (file tạm {temp_visual_video.name} không tạo được hoặc 0 bytes). Kiểm tra lại file video gốc hoặc codec FFmpeg.")
                video_res_container["success"] = True
                if render_subs:
                    task_manager.add_log(task_id, "   ✔ [Luồng Video] GPU Render Karaoke + Che chữ gốc hoàn tất.", "emerald")
                else:
                    task_manager.add_log(task_id, "   ✔ [Luồng Video] GPU Crop Khung Hình & Mã Hóa hoàn tất (Không Sub).", "emerald")
            except Exception as e:
                video_res_container["error"] = str(e)
                task_manager.add_log(task_id, f"   ❌ [Lỗi Video] {str(e)}", "rose")
                task_manager.cancel_task(task_id)  # Dừng ngay audio worker nếu render video lỗi

        # Kiểm tra dung lượng ổ đĩa an toàn trước khi chạy video dài
        try:
            import shutil
            free_gb = shutil.disk_usage(settings.BASE_DIR).free / (1024 ** 3)
            if free_gb < 15.0:
                task_manager.add_log(task_id, f"⚠️ Cảnh báo ổ đĩa: Chỉ còn {free_gb:.1f}GB trống. Video dài có thể cần 25-35GB dung lượng đệm!", "amber")
        except Exception:
            pass

        # Chạy đồng thời 2 Worker Threads
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            f_audio = executor.submit(_audio_worker)
            f_video = executor.submit(_video_worker)
            concurrent.futures.wait([f_audio, f_video])

        if audio_res_container.get("error") or not audio_res_container.get("audio_path") or not os.path.exists(audio_res_container.get("audio_path")):
            err_details = audio_res_container.get("error") or "Không tìm thấy file âm thanh tiếng Việt hoàn chỉnh"
            raise RuntimeError(f"Lỗi tạo giọng lồng tiếng tiếng Việt: {err_details}. Đã chặn tiến trình để không xuất video giữ tiếng Trung gốc.")

        if video_res_container.get("error") or not video_res_container.get("success") or not temp_visual_video.exists() or temp_visual_video.stat().st_size < 1000:
            err_details = video_res_container.get("error") or "File video render tạm không tồn tại hoặc bị lỗi."
            raise RuntimeError(f"Lỗi Render Video GPU/CPU: {err_details}. Đã chặn tiến trình gộp video!")

        # BƯỚC 4: HỢP NHẤT STREAM COPY 0.5s
        task_manager.update_task(task_id, step=4, progress=92)
        task_manager.add_log(task_id, "[4/4] ⚡ Hợp nhất luồng Hình Ảnh GPU & Audio lồng tiếng (Stream Copy 0.5s)...", "cyan")

        audio_src = audio_res_container.get("audio_path")
        mux_ok = VideoComposerService.mux_final_video(
            visual_video_path=str(temp_visual_video),
            audio_source_path=audio_src,
            output_final_path=str(output_final_path)
        )

        # Dọn dẹp file tạm
        if temp_visual_video.exists():
            temp_visual_video.unlink(missing_ok=True)

        if not mux_ok or not output_final_path.exists():
            raise RuntimeError(f"Không thể hợp nhất file video thành phẩm: {output_final_path}")

        # Cập nhật CSDL
        project.final_video_path = str(output_final_path)
        project.status = "COMPLETED"
        db.commit()

        task_manager.update_task(
            task_id,
            status="completed",
            step=5,
            progress=100,
            message="🎉 QUY TRÌNH TỰ ĐỘNG HÓA 100% HOÀN TẤT!",
            result={
                "project_id": project_id,
                "video_title": project.title if project else "",
                "final_video_path": str(output_final_path),
                "karaoke_ass_path": str(ass_file),
                "srt_path": trans_res.get("srt_path", project.srt_path if project else None)
            }
        )
        task_manager.add_log(task_id, f"🎉 TỰ ĐỘNG HÓA HOÀN TẤT 100%! File xuất: output/final_videos/{output_final_path.name}", "emerald")

    except Exception as e:
        db.rollback()
        err_msg = str(e)
        task_manager.add_log(task_id, f"❌ LỖI FULL-AUTO PIPELINE: {err_msg}", "rose")
        task_manager.update_task(
            task_id,
            status="failed",
            error=err_msg,
            message=f"Lỗi quy trình tự động: {err_msg}"
        )
    finally:
        db.close()


def _run_pipeline_worker(task_id: str, req: IngestPipelineRequest):
    """Worker chạy background thread độc lập, không block HTTP request"""
    db = SessionLocal()
    try:
        task_manager.update_task(task_id, status="running", step=1, progress=10)
        task_manager.add_log(task_id, f"[1/4] 📥 Bắt đầu tải video từ URL: {req.url}", "cyan")
        task_manager.add_log(task_id, f"      Cấu hình: {req.quality} | Ngôn ngữ: {req.source_language}", "cyan")

        # Bước 1: Tải Video
        video_path, info = DownloaderService.download_video(
            url=req.url,
            quality=req.quality
        )
        task_manager.add_log(task_id, f"✔ Video đã tải xong: {info['title']}", "emerald")

        # Nếu không yêu cầu tách sub (khi bấm Tải Về ở giao diện, tách sub sẽ làm ở bước Bắt đầu)
        if not req.extract_sub:
            task_manager.update_task(task_id, step=2, progress=90)
            task_manager.add_log(task_id, "💾 Ghi nhận video vào CSDL dự án...", "cyan")

            project = db.query(ProjectTask).filter(
                (ProjectTask.video_id == info["id"]) | (ProjectTask.source_url == req.url)
            ).first()

            if not project:
                project = ProjectTask(
                    video_id=info["id"],
                    title=info["title"],
                    source_url=req.url,
                    duration=info.get("duration", 0.0),
                    status="DOWNLOADED",
                    source_language=req.source_language,
                    video_path=video_path,
                    audio_path=None,
                    srt_path=None,
                    txt_path=None,
                    json_path=None
                )
                db.add(project)
            else:
                project.video_path = video_path
                project.status = "DOWNLOADED"
                if not project.title:
                    project.title = info["title"]
                if not project.duration:
                    project.duration = info.get("duration", 0.0)

            db.commit()
            db.refresh(project)

            result_data = {
                "status": "success",
                "message": f"Tải video hoàn tất! Video đã sẵn sàng trong danh sách.",
                "project_id": project.id,
                "video_id": info["id"],
                "title": info["title"],
                "video_path": video_path,
                "duration": info.get("duration", 0.0),
                "total_dialogues": 0
            }

            task_manager.add_log(task_id, f"✔ HOÀN TẤT TẢI VIDEO: {info['title']}", "emerald")
            task_manager.add_log(task_id, f"   Trạng thái: MỚI TẢI (0 câu). Tách phụ đề sẽ được thực hiện khi nhấn Bắt đầu.", "emerald")
            task_manager.update_task(
                task_id,
                status="completed",
                step=2,
                progress=100,
                message="Tải video thành công!",
                result=result_data
            )
            return

        # Bước 2: Tách Audio 16kHz
        task_manager.update_task(task_id, step=2, progress=30)
        task_manager.add_log(task_id, f"[2/4] 🎵 Đang trích xuất Audio 16kHz PCM WAV qua FFmpeg (input/audio_raw)...", "cyan")
        audio_path = AudioExtractorService.extract_audio_16k_wav(video_path)
        task_manager.add_log(task_id, f"✔ Trích xuất Audio 16kHz thành công.", "emerald")

        # Bước 3: AI Bóc tách lời thoại & Làm sạch text
        task_manager.update_task(task_id, step=3, progress=50)
        if settings.GROQ_API_KEY:
            engine_name = "Groq Whisper Cloud (whisper-large-v3 - Siêu tốc ⚡)"
        else:
            engine_name = f"Faster-Whisper ({settings.WHISPER_MODEL_SIZE} Offline)"
        task_manager.add_log(task_id, f"[3/4] 🤖 AI [{engine_name}] đang bóc tách lời thoại & timecode từng mili-giây...", "cyan")
        dialogues, srt_path, txt_path, json_path = WhisperService.transcribe(
            audio_path=audio_path,
            language=req.source_language,
            clean_text=req.clean_text,
            task_id=task_id
        )
        task_manager.add_log(task_id, f"✔ Bóc tách xong {len(dialogues)} câu thoại timecode.", "emerald")

        # Bước 4: Lưu vào SQLite Database
        task_manager.update_task(task_id, step=4, progress=85)
        task_manager.add_log(task_id, f"[4/4] 💾 Đang lưu dữ liệu vào SQLite Database...", "cyan")
        project = ProjectTask(
            video_id=info["id"],
            title=info["title"],
            source_url=req.url,
            duration=info["duration"],
            status="TRANSCRIBED",
            source_language=req.source_language,
            video_path=video_path,
            audio_path=audio_path,
            srt_path=srt_path,
            txt_path=txt_path,
            json_path=json_path
        )
        db.add(project)
        db.flush()

        # Xóa sạch các câu thoại cũ nếu có (chống nhân đôi câu)
        db.query(DialogueSegmentModel).filter(DialogueSegmentModel.task_id == project.id).delete(synchronize_session=False)
        db.commit()
        for obj in list(db.identity_map.values()):
            if isinstance(obj, DialogueSegmentModel):
                db.expunge(obj)

        # Lưu từng câu thoại vào bảng dialogue_segments
        for d in dialogues:
            diag_model = DialogueSegmentModel(
                task_id=project.id,
                index=d.id,
                start_time=d.start,
                end_time=d.end,
                duration=d.duration,
                original_text=d.text,
                clean_text=d.clean_text,
                confidence=d.confidence,
                status="RAW"
            )
            db.add(diag_model)

        db.commit()
        db.refresh(project)

        result_data = {
            "status": "success",
            "message": f"Hoàn tất! Đã bóc tách {len(dialogues)} câu thoại và lưu vào Database SQLite.",
            "project_id": project.id,
            "video_id": info["id"],
            "title": info["title"],
            "video_path": video_path,
            "audio_path": audio_path,
            "duration": info["duration"],
            "total_dialogues": len(dialogues),
            "srt_path": srt_path,
            "txt_path": txt_path,
            "json_path": json_path
        }

        task_manager.add_log(task_id, f"[4/4] ✔ HOÀN TẤT THÀNH CÔNG! Đã bóc tách {len(dialogues)} câu thoại.", "emerald")
        task_manager.add_log(task_id, f"      Tiêu đề: {info['title']}", "emerald")
        task_manager.add_log(task_id, f"      File SRT: {srt_path}", "emerald")
        task_manager.add_log(task_id, f"      Đã lưu an toàn vào SQLite Database (Project: #{project.id})", "emerald")

        task_manager.update_task(
            task_id,
            status="completed",
            step=4,
            progress=100,
            message="Hoàn tất xử lý thành công!",
            result=result_data
        )

    except Exception as e:
        db.rollback()
        err_msg = str(e)
        task_manager.add_log(task_id, f"❌ LỖI PIPELINE: {err_msg}", "rose")
        task_manager.update_task(
            task_id,
            status="failed",
            error=err_msg,
            message=f"Lỗi: {err_msg}"
        )
    finally:
        db.close()


@router.post("/full-auto", response_model=AsyncTaskResponse, summary="Khởi chạy trọn gói 100% Tự động 1-Click (Tải -> Dịch AIREAD -> Quét lỗi -> TTS -> Video)")
def run_full_auto_pipeline_async(req: FullAutoPipelineRequest):
    """
    Kích hoạt toàn bộ chuỗi xử lý tự động:
    1. Tải Video & STT Whisper
    2. Dịch đa tầng AIREAD & Quét sạch lỗi chữ Hán
    3. Sản xuất giọng đọc TikTok TTS Smart Headroom
    4. Render Video Karaoke phủ hộp nền che phụ đề cũ + BGM Ducking + Logo & Watermark
    """
    task_id = f"task_auto_{uuid.uuid4().hex[:10]}"
    task_manager.create_task(task_id, meta={"url": req.url, "project_id": req.project_id, "genre": req.genre})
    
    worker_thread = threading.Thread(
        target=_run_full_auto_worker,
        args=(task_id, req),
        daemon=True
    )
    worker_thread.start()

    return AsyncTaskResponse(
        task_id=task_id,
        status="processing",
        message="Quy trình tự động hóa 1-Click đã khởi chạy trong background thread."
    )


@router.post("/ingest", response_model=AsyncTaskResponse, summary="Khởi chạy Pipeline 1-Shot chạy ngầm (Asynchronous Background Task)")
def run_ingest_pipeline_async(req: IngestPipelineRequest):
    """
    Khởi chạy quy trình Pipeline 1-Shot trong Background Thread:
    - Trả về `task_id` ngay lập tức để tránh Browser HTTP Timeout.
    - Frontend định kỳ thăm dò `/api/v1/pipeline/status/{task_id}` để cập nhật Stepper và Logs.
    """
    task_id = f"task_{uuid.uuid4().hex[:10]}"
    task_manager.create_task(task_id, meta={"url": req.url, "quality": req.quality})
    
    worker_thread = threading.Thread(
        target=_run_pipeline_worker,
        args=(task_id, req),
        daemon=True
    )
    worker_thread.start()

    return AsyncTaskResponse(
        task_id=task_id,
        status="processing",
        message="Pipeline đã khởi chạy trong background thread."
    )


@router.get("/status/{task_id}", response_model=TaskStatusResponse, summary="Kiểm tra trạng thái tiến trình Pipeline và Logs real-time")
def get_task_status(task_id: str):
    """Lấy thông tin bước hiện tại (1->4), % tiến trình, danh sách logs terminal và kết quả cuối cùng"""
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy tác vụ {task_id}")
    return task


@router.get("/active-task", summary="Lấy tác vụ tự động hóa (auto pipeline) đang chạy nếu có")
def get_active_pipeline_task():
    """Giúp Frontend phục hồi thanh tiến trình và log ngay cả khi chuyển tab hoặc mở lại trình duyệt"""
    tasks = task_manager.list_tasks()
    for tid, t in reversed(list(tasks.items())):
        if (tid.startswith("task_auto_") or tid.startswith("task_")) and not tid.startswith("task_trans_"):
            if t.get("status") in ["running", "pending"]:
                return t
    return {"status": "none"}


@router.post("/cancel/{task_id}", summary="Hủy tác vụ đang chạy và tự động dọn dẹp các chunk tạm")
def cancel_task_endpoint(task_id: str):
    """Hủy tức thì tác vụ đang xử lý và xóa các chunk audio/video dở dang"""
    task = task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy tác vụ {task_id}")
    
    task_manager.cancel_task(task_id)
    meta = task.get("meta", {})
    video_id = meta.get("video_id")
    if video_id:
        TikTokTTSService.cleanup_project_audio_chunks(video_id)
    
    return {"status": "success", "message": f"Đã hủy tác vụ {task_id} và dọn dẹp các chunk âm thanh tạm."}


@router.post("/cleanup-chunks/{video_id}", summary="Xóa sạch các chunk âm thanh tạm của một video")
def cleanup_chunks_endpoint(video_id: str):
    """Xóa toàn bộ các chunk mp3 trong thư mục segments để chuẩn bị cho lần tạo mới hoàn toàn"""
    TikTokTTSService.cleanup_project_audio_chunks(video_id)
    return {"status": "success", "message": f"Đã dọn dẹp sạch sẽ các chunk tạm của video {video_id}."}

def _run_extract_stt_worker(task_id: str, req: ExtractSTTRequest):
    db = SessionLocal()
    try:
        task_manager.update_task(task_id, status="running", step=1, progress=10)
        project = db.query(ProjectTask).filter(ProjectTask.id == req.project_id).first()
        if not project:
            raise ValueError(f"Không tìm thấy Project #{req.project_id}")
            
        task_manager.add_log(task_id, f"[1/2] 🎵 Đang xử lý Audio từ project: {project.title}", "cyan")
        
        # Đảm bảo có audio path hợp lệ
        audio_path = project.audio_path
        if not audio_path or not os.path.exists(audio_path):
            if project.video_path and os.path.exists(project.video_path):
                task_manager.add_log(task_id, f"🎵 Trích xuất Audio 16kHz PCM WAV từ video...", "cyan")
                audio_path = AudioExtractorService.extract_audio_16k_wav(project.video_path)
                project.audio_path = audio_path
                db.commit()
            else:
                raise ValueError("Không tìm thấy file Video hoặc Audio gốc của dự án này.")

        task_manager.update_task(task_id, step=1, progress=30)
        task_manager.add_log(task_id, f"[1/2] 🎙️ Đang lọc sạch tạp âm trước khi STT...", "cyan")
        clean_audio_path = AudioExtractorService.get_clean_audio_for_asr(audio_path)
        
        engine = getattr(settings, "ASR_ENGINE", "capcut").lower()
        engine_name = "CapCut Cloud STT" if engine == "capcut" else "Groq Whisper"
        task_manager.update_task(task_id, step=2, progress=50)
        task_manager.add_log(task_id, f"[2/2] 🤖 Đang bóc tách lời thoại bằng {engine_name}...", "cyan")
        
        dialogues, srt_path, txt_path, json_path = WhisperService.transcribe(
            audio_path=clean_audio_path,
            language=req.source_language,
            clean_text=True,
            task_id=task_id
        )
        task_manager.add_log(task_id, f"✔ Đã bóc tách {len(dialogues)} câu thoại.", "emerald")
        
        project.srt_path = srt_path
        project.txt_path = txt_path
        project.json_path = json_path
        project.status = "TRANSCRIBED"
        
        # Xoá các câu thoại cũ nếu có (an toàn)
        db.query(DialogueSegmentModel).filter(DialogueSegmentModel.task_id == project.id).delete(synchronize_session=False)
        db.commit()
        for obj in list(db.identity_map.values()):
            if isinstance(obj, DialogueSegmentModel):
                db.expunge(obj)
        
        for d in dialogues:
            db.add(DialogueSegmentModel(
                task_id=project.id,
                index=d.id,
                start_time=d.start,
                end_time=d.end,
                duration=d.duration,
                original_text=d.text,
                clean_text=d.clean_text,
                confidence=d.confidence,
                status="RAW"
            ))
        
        db.commit()
        
        task_manager.update_task(
            task_id,
            status="completed",
            step=2,
            progress=100,
            message="Bóc tách lời thoại thành công!",
            result={"project_id": project.id, "total_dialogues": len(dialogues)}
        )
    except Exception as e:
        db.rollback()
        err_msg = str(e)
        task_manager.add_log(task_id, f"❌ LỖI TÁCH THOẠI: {err_msg}", "rose")
        task_manager.update_task(task_id, status="failed", error=err_msg, message=f"Lỗi: {err_msg}")
    finally:
        db.close()


@router.post("/extract-stt", response_model=AsyncTaskResponse, summary="Bóc tách thoại riêng biệt cho project đã tải")
def extract_stt_async(req: ExtractSTTRequest):
    task_id = f"task_stt_{uuid.uuid4().hex[:10]}"
    task_manager.create_task(task_id, meta={"project_id": req.project_id})
    worker_thread = threading.Thread(target=_run_extract_stt_worker, args=(task_id, req), daemon=True)
    worker_thread.start()
    return AsyncTaskResponse(task_id=task_id, status="processing", message="Quy trình bóc thoại đã chạy ngầm.")
