import os
import json
import re
from pathlib import Path
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, Body, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.db.session import get_db
from app.models.project import ProjectTask
from app.models.dialogue import DialogueSegmentModel
from app.config import settings

router = APIRouter()

@router.get("/", summary="Lấy danh sách tóm tắt Project (Phân trang siêu nhẹ, chống tràn RAM)")
def list_projects(
    response: Response,
    page: int = Query(default=1, ge=1, description="Số trang"),
    page_size: int = Query(default=50, ge=1, le=100, description="Số lượng mỗi trang"),
    status: Optional[str] = Query(default=None, description="Lọc theo trạng thái"),
    search: Optional[str] = Query(default=None, description="Tìm kiếm theo tiêu đề/ID"),
    db: Session = Depends(get_db)
):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    query = db.query(ProjectTask)
    if status and status != "ALL":
        query = query.filter(ProjectTask.status == status)
    if search:
        query = query.filter(ProjectTask.title.ilike(f"%{search}%") | ProjectTask.video_id.ilike(f"%{search}%"))
        
    total_count = query.count()
    total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 1
    
    offset = (page - 1) * page_size
    projects = query.order_by(ProjectTask.id.desc()).offset(offset).limit(page_size).all()
    
    items = []
    for p in projects:
        diag_count = db.query(func.count(DialogueSegmentModel.id)).filter(DialogueSegmentModel.task_id == p.id).scalar()
        translated_count = db.query(func.count(DialogueSegmentModel.id)).filter(
            DialogueSegmentModel.task_id == p.id,
            DialogueSegmentModel.translated_text.isnot(None),
            DialogueSegmentModel.translated_text != ""
        ).scalar()

        # Kiểm tra xem có video final không
        has_final = bool(p.final_video_path and os.path.exists(p.final_video_path))

        items.append({
            "id": p.id,
            "video_id": p.video_id,
            "title": p.title,
            "source_url": p.source_url,
            "status": p.status,
            "duration": p.duration,
            "total_dialogues": diag_count,
            "translated_dialogues": translated_count,
            "has_final": has_final,
            "video_path": p.video_path,
            "final_video_path": p.final_video_path,
            "srt_path": p.srt_path,
            "txt_path": p.txt_path,
            "created_at": p.created_at.strftime("%Y-%m-%d %H:%M:%S") if p.created_at else ""
        })
        
    return {
        "page": page,
        "page_size": page_size,
        "total_items": total_count,
        "total_pages": total_pages,
        "items": items
    }

def create_video_range_response(file_path: str, request: Request):
    """
    Phục vụ video MP4 với chuẩn HTTP 206 Partial Content & Byte-Range.
    - Hỗ trợ tua nhanh tức thì (instant seeking) mà không cần tải toàn bộ file.
    - Đọc theo chunk 512KB, chống tràn RAM và không nghẽn luồng uvicorn.
    - Tránh lag giật, treo máy khi gọi video hoàn chỉnh lên web.
    """
    file_size = os.path.getsize(file_path)
    range_header = request.headers.get("range")

    def file_chunk_generator(path: str, start: int, end: int, chunk_size: int = 512 * 1024):
        with open(path, "rb") as f:
            f.seek(start)
            bytes_left = end - start + 1
            while bytes_left > 0:
                read_size = min(chunk_size, bytes_left)
                chunk = f.read(read_size)
                if not chunk:
                    break
                bytes_left -= len(chunk)
                yield chunk

    if range_header:
        try:
            range_val = range_header.strip().replace("bytes=", "")
            if "-" in range_val:
                parts = range_val.split("-")
                start_str = parts[0].strip()
                end_str = parts[1].strip() if len(parts) > 1 else ""

                if start_str and end_str:
                    start = int(start_str)
                    end = int(end_str)
                elif start_str:
                    start = int(start_str)
                    end = file_size - 1
                elif end_str:
                    start = max(0, file_size - int(end_str))
                    end = file_size - 1
                else:
                    start = 0
                    end = file_size - 1
            else:
                start = 0
                end = file_size - 1

            start = max(0, min(start, file_size - 1))
            end = max(start, min(end, file_size - 1))
            content_length = end - start + 1

            headers = {
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(content_length),
                "Content-Type": "video/mp4",
                "Cache-Control": "no-cache",
            }
            return StreamingResponse(
                file_chunk_generator(file_path, start, end),
                status_code=206,
                headers=headers,
                media_type="video/mp4"
            )
        except Exception:
            pass

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(file_size),
        "Content-Type": "video/mp4",
        "Cache-Control": "no-cache",
    }
    return StreamingResponse(
        file_chunk_generator(file_path, 0, file_size - 1),
        status_code=200,
        headers=headers,
        media_type="video/mp4"
    )

@router.get("/{project_id}/video", summary="Stream trực tiếp file video của Project")
def stream_project_video(project_id: int, request: Request, type: str = "auto", db: Session = Depends(get_db)):
    project = db.query(ProjectTask).filter(ProjectTask.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Không tìm thấy project")
        
    if type == "raw":
        file_path = project.video_path
    elif type == "final":
        file_path = project.final_video_path if (project.final_video_path and os.path.exists(project.final_video_path)) else project.video_path
    else: # auto: ưu tiên video thành phẩm lồng tiếng & karaoke nếu đã render xong
        file_path = project.final_video_path if (project.final_video_path and os.path.exists(project.final_video_path)) else project.video_path

    if not file_path or not os.path.exists(file_path):
        if project.video_path and os.path.exists(project.video_path):
            file_path = project.video_path
        else:
            raise HTTPException(status_code=404, detail=f"Không tìm thấy file video: {file_path}")
            
    return create_video_range_response(file_path, request)

@router.post("/{project_id}/open-folder", summary="Mở thư mục chứa video thành phẩm trong Windows Explorer")
def open_project_folder(project_id: int, db: Session = Depends(get_db)):
    project = db.query(ProjectTask).filter(ProjectTask.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Không tìm thấy project")
    
    target_path = project.final_video_path if (project.final_video_path and os.path.exists(project.final_video_path)) else str(settings.OUTPUT_FINAL_VIDEOS_DIR)
    try:
        if os.path.exists(target_path):
            if os.path.isfile(target_path):
                os.system(f'explorer /select,"{os.path.abspath(target_path)}"')
            else:
                os.system(f'explorer "{os.path.abspath(target_path)}"')
        else:
            os.system(f'explorer "{os.path.abspath(settings.OUTPUT_FINAL_VIDEOS_DIR)}"')
        return {"status": "success", "message": "Đã mở thư mục chứa video trong Windows!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Không thể mở thư mục: {str(e)}")

@router.get("/{project_id}/entities", summary="Lấy danh sách thực thể bóc tách hoặc từ điển")
def get_project_entities(project_id: int, db: Session = Depends(get_db)):
    project = db.query(ProjectTask).filter(ProjectTask.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Không tìm thấy project")
        
    # Tìm file entities trong output/02_entities
    entities_files = list(settings.OUTPUT_ENTITIES_DIR.glob(f"{project.video_id}*.json"))
    if entities_files and entities_files[0].exists():
        with open(entities_files[0], "r", encoding="utf-8") as f:
            data = json.load(f)
            
            entities_list = []
            if isinstance(data, list):
                for e in data:
                    entities_list.append({
                        "chinese_name": e.get("raw", ""),
                        "vietnamese_name": e.get("viet", ""),
                        "entity_type": e.get("type", "NAME"),
                        "role": e.get("desc", "Thực thể"),
                        "evaluation": "TÊN CỐ ĐỊNH"
                    })
                return {"status": "success", "entities": {"entities": entities_list, "summary": ""}}
            elif isinstance(data, dict):
                entities_list = data.get("entities", [])
                if not entities_list:
                    chars = data.get("characters", [])
                    terms = data.get("terms", [])
                    for c in chars:
                        entities_list.append({
                            "chinese_name": c.get("raw", ""),
                            "vietnamese_name": c.get("viet", ""),
                            "entity_type": "NAME",
                            "role": c.get("role", "Nhân vật"),
                            "evaluation": "TÊN CỐ ĐỊNH"
                        })
                    for t in terms:
                        entities_list.append({
                            "chinese_name": t.get("raw", ""),
                            "vietnamese_name": t.get("viet", ""),
                            "entity_type": t.get("type", "OTHER"),
                            "role": t.get("category", "Thuật ngữ"),
                            "evaluation": "NÊN DÙNG BẢN SẮC"
                        })
                    data["entities"] = entities_list
                return {"status": "success", "entities": data}
            
    return {"status": "success", "entities": {"entities": [], "characters": [], "terms": [], "summary": ""}}

@router.put("/{project_id}/entities", summary="Lưu cập nhật từ điển thực thể")
def save_project_entities(project_id: int, payload: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    project = db.query(ProjectTask).filter(ProjectTask.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Không tìm thấy project")
        
    clean_title = re.sub(r'[^\w\-_\. ]', '_', project.title)
    target_file = settings.OUTPUT_ENTITIES_DIR / f"{project.video_id}_{clean_title}_entities.json"
    
    with open(target_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        
    return {"status": "success", "message": "Đã lưu từ điển thành công!", "file": str(target_file)}

@router.post("/{project_id}/entities/apply", summary="Áp dụng sửa từ điển vào toàn bộ kịch bản đã dịch")
def apply_entities_to_dialogues(
    project_id: int, 
    payload: Dict[str, Any] = Body(...), 
    db: Session = Depends(get_db)
):
    """
    Quét toàn bộ câu thoại đã dịch của Project, thay thế các từ cũ bằng từ mới trong từ điển
    và cập nhật lại cả Database lẫn file SRT.
    """
    project = db.query(ProjectTask).filter(ProjectTask.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Không tìm thấy project")
        
    replacements = payload.get("replacements", []) # List of {"old_val": "...", "new_val": "..."}
    if not replacements:
        return {"status": "success", "message": "Không có từ nào cần thay thế", "updated_count": 0}
        
    dialogues = db.query(DialogueSegmentModel).filter(
        DialogueSegmentModel.task_id == project_id,
        DialogueSegmentModel.translated_text.isnot(None)
    ).all()
    
    updated_lines_count = 0
    for d in dialogues:
        text = d.translated_text
        original_text = text
        for r in replacements:
            old_val = r.get("old_val", "").strip()
            new_val = r.get("new_val", "").strip()
            if old_val and new_val and old_val != new_val and old_val in text:
                text = text.replace(old_val, new_val)
                
        if text != original_text:
            d.translated_text = text
            updated_lines_count += 1
            
    db.commit()
    
    # Cập nhật lại file SRT nếu có
    if project.srt_path and os.path.exists(project.srt_path):
        try:
            with open(project.srt_path, "r", encoding="utf-8") as f:
                srt_content = f.read()
            for r in replacements:
                old_val = r.get("old_val", "").strip()
                new_val = r.get("new_val", "").strip()
                if old_val and new_val and old_val != new_val:
                    srt_content = srt_content.replace(old_val, new_val)
            with open(project.srt_path, "w", encoding="utf-8") as f:
                f.write(srt_content)
        except Exception as e:
            print(f"Lỗi khi cập nhật file SRT: {e}")
            
    return {
        "status": "success",
        "message": f"Đã cập nhật thành công {updated_lines_count} câu thoại theo từ điển mới!",
        "updated_count": updated_lines_count
    }

@router.get("/{project_id}/stats", summary="Lấy thống kê tóm tắt nhanh của Project")
def get_project_stats(project_id: int, db: Session = Depends(get_db)):
    project = db.query(ProjectTask).filter(ProjectTask.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Không tìm thấy project")
        
    count = db.query(func.count(DialogueSegmentModel.id)).filter(DialogueSegmentModel.task_id == project_id).scalar()
    return {
        "id": project.id,
        "video_id": project.video_id,
        "title": project.title,
        "status": project.status,
        "duration": project.duration,
        "total_dialogues": count
    }

@router.get("/{project_id}/dialogues", summary="Lấy danh sách câu thoại có phân trang")
def get_project_dialogues_paginated(
    project_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=2000, ge=5, le=10000),
    db: Session = Depends(get_db)
):

    total_count = db.query(func.count(DialogueSegmentModel.id)).filter(DialogueSegmentModel.task_id == project_id).scalar()
    total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 1
    
    offset = (page - 1) * page_size
    dialogues = db.query(DialogueSegmentModel).filter(
        DialogueSegmentModel.task_id == project_id
    ).order_by(DialogueSegmentModel.index.asc()).offset(offset).limit(page_size).all()
    
    return {
        "project_id": project_id,
        "page": page,
        "page_size": page_size,
        "total_items": total_count,
        "total_pages": total_pages,
        "items": [
            {
                "id": d.id,
                "index": d.index,
                "start_time": d.start_time,
                "end_time": d.end_time,
                "duration": d.duration,
                "original_text": d.original_text,
                "clean_text": d.clean_text,
                "translated_text": d.translated_text,
                "voice_audio_path": d.voice_audio_path,
                "speed_ratio": d.speed_ratio,
                "status": d.status
            }
            for d in dialogues
        ]
    }

@router.delete("/{project_id}", summary="Xóa triệt để Project và toàn bộ dữ liệu/file liên quan")
def delete_project(project_id: int, db: Session = Depends(get_db)):
    project = db.query(ProjectTask).filter(ProjectTask.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Không tìm thấy project để xóa!")
        
    vid = project.video_id
    deleted_files = []

    # 1. Danh sách các thư mục cần quét và dọn sạch file liên quan đến video_id
    scan_dirs = [
        settings.INPUT_VIDEOS_DIR,
        settings.INPUT_AUDIO_DIR,
        settings.OUTPUT_TRANSCRIPTS_DIR,
        settings.OUTPUT_RAW_CLEANED_DIR,
        settings.OUTPUT_ENTITIES_DIR,
        settings.OUTPUT_DICH_AI_LLM_DIR,
        settings.OUTPUT_POST_PROCESSED_DIR,
        settings.OUTPUT_TRANSLATIONS_DIR,
        settings.OUTPUT_VOICEOVER_DIR,
        settings.OUTPUT_FINAL_VIDEOS_DIR,
        settings.TEMP_TTS_DIR,
        settings.BASE_DIR / "scratch",
    ]

    for target_dir in scan_dirs:
        if target_dir.exists():
            for f in list(target_dir.glob(f"*{vid}*")):
                try:
                    if f.is_file():
                        f.unlink(missing_ok=True)
                        deleted_files.append(f.name)
                    elif f.is_dir():
                        import shutil
                        shutil.rmtree(f, ignore_errors=True)
                        deleted_files.append(f"{f.name}/")
                except Exception as e:
                    print(f"Không thể xóa file {f}: {e}")

    # 2. Xóa các đường dẫn cụ thể được lưu trong database nếu còn sót
    for path_attr in [project.video_path, project.audio_path, project.srt_path, project.txt_path, project.json_path, project.final_video_path]:
        if path_attr and os.path.exists(path_attr):
            try:
                os.remove(path_attr)
                deleted_files.append(os.path.basename(path_attr))
            except Exception:
                pass

    # 3. Xóa Database Record (tự động cascade xóa các câu thoại trong DialogueSegmentModel)
    db.delete(project)
    db.commit()

    return {
        "status": "success",
        "message": f"Đã xóa vĩnh viễn Project #{project_id} ({vid}) cùng {len(deleted_files)} file liên quan sạch sẽ 100%!",
        "deleted_files_count": len(deleted_files),
        "deleted_files": deleted_files[:10]
    }

@router.post("/{project_id}/reset", summary="Reset Project về lúc mới tải xong (giữ lại video gốc, xóa toàn bộ sản phẩm phái sinh)")
def reset_project(project_id: int, db: Session = Depends(get_db)):
    project = db.query(ProjectTask).filter(ProjectTask.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Không tìm thấy project để reset!")

    vid = project.video_id
    raw_video_abs = os.path.abspath(project.video_path) if project.video_path else None
    deleted_files = []

    # 1. Các thư mục cần quét và xóa sạch sản phẩm phái sinh (KHÔNG BAO GỒM file video gốc)
    scan_dirs = [
        settings.INPUT_AUDIO_DIR,
        settings.OUTPUT_TRANSCRIPTS_DIR,
        settings.OUTPUT_RAW_CLEANED_DIR,
        settings.OUTPUT_ENTITIES_DIR,
        settings.OUTPUT_DICH_AI_LLM_DIR,
        settings.OUTPUT_POST_PROCESSED_DIR,
        settings.OUTPUT_TRANSLATIONS_DIR,
        settings.OUTPUT_VOICEOVER_DIR,
        settings.OUTPUT_FINAL_VIDEOS_DIR,
        settings.TEMP_TTS_DIR,
        settings.BASE_DIR / "scratch",
    ]

    for target_dir in scan_dirs:
        if target_dir.exists():
            for f in list(target_dir.glob(f"*{vid}*")):
                try:
                    abs_f = os.path.abspath(str(f))
                    # Tuyệt đối không xóa nếu trùng với file video gốc
                    if raw_video_abs and abs_f == raw_video_abs:
                        continue
                    if f.is_file():
                        f.unlink(missing_ok=True)
                        deleted_files.append(f.name)
                    elif f.is_dir():
                        import shutil
                        shutil.rmtree(f, ignore_errors=True)
                        deleted_files.append(f"{f.name}/")
                except Exception as e:
                    print(f"Không thể xóa file {f}: {e}")

    # 2. Xóa các file phái sinh cụ thể được lưu trong database (bỏ qua video_path gốc)
    for path_attr in [project.audio_path, project.srt_path, project.txt_path, project.json_path, project.final_video_path]:
        if path_attr and os.path.exists(path_attr):
            try:
                abs_p = os.path.abspath(path_attr)
                if raw_video_abs and abs_p == raw_video_abs:
                    continue
                os.remove(path_attr)
                deleted_files.append(os.path.basename(path_attr))
            except Exception:
                pass

    # 3. Xóa toàn bộ câu thoại (DialogueSegmentModel) liên quan đến project này
    db.query(DialogueSegmentModel).filter(DialogueSegmentModel.task_id == project_id).delete(synchronize_session=False)

    # 4. Đưa trạng thái ProjectTask về lúc mới tải xong (DOWNLOADED)
    project.status = "DOWNLOADED"
    project.audio_path = None
    project.srt_path = None
    project.txt_path = None
    project.json_path = None
    project.final_video_path = None
    project.error_message = None

    db.commit()
    db.refresh(project)

    return {
        "status": "success",
        "message": f"Đã reset dự án #{project_id} ({vid}) về lúc mới tải xong! Đã dọn sạch {len(deleted_files)} file trung gian và toàn bộ câu thoại, giữ nguyên video gốc.",
        "project_id": project.id,
        "video_path": project.video_path,
        "status_code": project.status,
        "deleted_files_count": len(deleted_files),
        "deleted_files": deleted_files[:10]
    }
