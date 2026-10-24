import os
import json
import re
from pathlib import Path
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, Body, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, case
from app.db.session import get_db
from app.models.project import ProjectTask
from app.models.dialogue import DialogueSegmentModel, TTSDialogueSegmentModel
from pydantic import BaseModel
from app.config import settings

router = APIRouter()

class DialogueItemUpdate(BaseModel):
    id: int
    translated_text: str
    speaker: Optional[str] = None

class BatchDialogueUpdatePayload(BaseModel):
    items: List[DialogueItemUpdate]

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
        # Đếm chuẩn xác theo số câu gốc đã được xử lý dịch/gộp (TRANSLATED, DUBBED, SILENT_VALID hoặc có text)
        translated_count = db.query(func.count(DialogueSegmentModel.id)).filter(
            DialogueSegmentModel.task_id == p.id,
            (DialogueSegmentModel.status.in_(["TRANSLATED", "DUBBED", "SILENT_VALID"])) |
            ((DialogueSegmentModel.translated_text.isnot(None)) & (DialogueSegmentModel.translated_text != ""))
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
        
    # Tìm file entities trong project_{id}/entities/
    from app.config import get_project_dirs
    p_dirs = get_project_dirs(project_id)
    all_ent_file = p_dirs["entities"] / "all_entities.json"
    
    if all_ent_file.exists():
        with open(all_ent_file, "r", encoding="utf-8") as f:
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
        
    from app.config import get_project_dirs
    p_dirs = get_project_dirs(project_id)
    target_file = p_dirs["entities"] / "all_entities.json"
    
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
    from_term = (payload.get("from_term") or "").strip()
    to_term = (payload.get("to_term") or "").strip()
    if from_term and to_term and from_term.lower() != to_term.lower():
        if not any(r.get("old_val", "").strip().lower() == from_term.lower() for r in replacements):
            replacements.insert(0, {"old_val": from_term, "new_val": to_term})

    if not replacements:
        return {"status": "success", "message": "Không có từ nào cần thay thế", "updated_count": 0}
        
    def _smart_sub(content: str, old_str: str, new_str: str) -> str:
        if not content or not old_str or not new_str or old_str == new_str:
            return content
        if old_str in content:
            content = content.replace(old_str, new_str)
        # Quét Regex Case-Insensitive an toàn
        pat = re.compile(re.escape(old_str), re.IGNORECASE)
        if pat.search(content):
            content = pat.sub(new_str, content)
        return content

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
            text = _smart_sub(text, old_val, new_val)
                
        if text != original_text:
            d.translated_text = text
            updated_lines_count += 1

    # Cập nhật cả bảng TTS (tts_dialogue_segments)
    tts_dialogues = db.query(TTSDialogueSegmentModel).filter(
        TTSDialogueSegmentModel.task_id == project_id,
        TTSDialogueSegmentModel.translated_text.isnot(None)
    ).all()

    for t in tts_dialogues:
        t_text = t.translated_text
        t_original = t_text
        for r in replacements:
            old_val = r.get("old_val", "").strip()
            new_val = r.get("new_val", "").strip()
            t_text = _smart_sub(t_text, old_val, new_val)
        if t_text != t_original:
            t.translated_text = t_text
            updated_lines_count += 1
            
    db.commit()
    
    # Cập nhật lại các file SRT và TXT của project
    from app.config import get_project_dirs
    p_dirs = get_project_dirs(project_id)
    root_dir = p_dirs["root"]
    tts_dir = p_dirs["tts_voiceover"]
    sub_dir = p_dirs["video_subtitles"]

    target_files = [
        project.srt_path,
        root_dir / "transcript_vi.srt",
        root_dir / "script_vi.txt",
        tts_dir / "script_tts.txt",
        tts_dir / "transcript_tts.srt",
        sub_dir / "subtitles_karaoke.srt"
    ]

    for f_target in target_files:
        if f_target and os.path.exists(str(f_target)):
            try:
                with open(str(f_target), "r", encoding="utf-8") as f:
                    content = f.read()
                original_content = content
                for r in replacements:
                    old_val = r.get("old_val", "").strip()
                    new_val = r.get("new_val", "").strip()
                    content = _smart_sub(content, old_val, new_val)
                if content != original_content:
                    with open(str(f_target), "w", encoding="utf-8") as f:
                        f.write(content)
            except Exception as e:
                print(f"Lỗi khi cập nhật file {f_target}: {e}")

    # Cập nhật cả file all_entities.json nếu có thay thế cụm từ con
    ent_file = p_dirs["entities"] / "all_entities.json"
    if ent_file.exists():
        try:
            with open(str(ent_file), "r", encoding="utf-8") as f:
                ent_data = json.load(f)
            ent_changed = False
            if isinstance(ent_data, dict):
                for e in ent_data.get("entities", []):
                    v = e.get("vietnamese_name", "")
                    for r in replacements:
                        v_new = _smart_sub(v, r["old_val"], r["new_val"])
                        if v_new != v:
                            e["vietnamese_name"] = v_new
                            v = v_new
                            ent_changed = True
            elif isinstance(ent_data, list):
                for e in ent_data:
                    v = e.get("viet", "") or e.get("vietnamese_name", "")
                    for r in replacements:
                        v_new = _smart_sub(v, r["old_val"], r["new_val"])
                        if v_new != v:
                            if "viet" in e: e["viet"] = v_new
                            if "vietnamese_name" in e: e["vietnamese_name"] = v_new
                            v = v_new
                            ent_changed = True
            if ent_changed:
                with open(str(ent_file), "w", encoding="utf-8") as f:
                    json.dump(ent_data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

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

@router.get("/{project_id}/batches", summary="Lấy danh mục các Lô (Chương) cực nhẹ chuẩn AIREAD")
def get_project_batches(
    project_id: int,
    batch_size: int = Query(default=250, ge=10, le=1000),
    db: Session = Depends(get_db)
):
    total_count = db.query(func.count(DialogueSegmentModel.id)).filter(
        DialogueSegmentModel.task_id == project_id
    ).scalar() or 0

    if total_count == 0:
        return {
            "project_id": project_id,
            "total_items": 0,
            "total_translated": 0,
            "batch_size": batch_size,
            "total_batches": 0,
            "batches": []
        }

    # Đếm số câu gốc đã hoàn tất xử lý (bao gồm cả các câu con đã gộp thành công MERGED_CHILD)
    total_translated = db.query(func.count(DialogueSegmentModel.id)).filter(
        DialogueSegmentModel.task_id == project_id,
        (DialogueSegmentModel.status.in_(["TRANSLATED", "DUBBED", "SILENT_VALID", "MERGED_CHILD"])) | 
        ((DialogueSegmentModel.translated_text.isnot(None)) & (DialogueSegmentModel.translated_text != ""))
    ).scalar() or 0

    # Lấy phân bố trạng thái từng lô theo câu query SQL tối ưu
    # SQLite: index bắt đầu từ 1 -> (index - 1) / batch_size là batch_index
    batch_stats = db.query(
        ((DialogueSegmentModel.index - 1) / batch_size).label("batch_idx"),
        func.count(DialogueSegmentModel.id).label("total_in_batch"),
        func.sum(
            case(
                (
                    (DialogueSegmentModel.status.in_(["TRANSLATED", "DUBBED", "SILENT_VALID", "MERGED_CHILD"])) |
                    ((DialogueSegmentModel.translated_text.isnot(None)) & (DialogueSegmentModel.translated_text != "")),
                    1
                ),
                else_=0
            )
        ).label("translated_in_batch")
    ).filter(
        DialogueSegmentModel.task_id == project_id
    ).group_by("batch_idx").order_by("batch_idx").all()

    stats_map = {int(b[0]): (b[1], int(b[2] or 0)) for b in batch_stats if b[0] is not None}

    total_batches = (total_count + batch_size - 1) // batch_size
    batches = []
    for b_idx in range(total_batches):
        start_num = b_idx * batch_size + 1
        end_num = min((b_idx + 1) * batch_size, total_count)
        b_count, b_trans = stats_map.get(b_idx, (end_num - start_num + 1, 0))
        
        status = "PENDING"
        if b_trans >= b_count and b_count > 0:
            status = "COMPLETED"
        elif b_trans > 0:
            status = "IN_PROGRESS"

        batches.append({
            "batch_index": b_idx,
            "batch_number": b_idx + 1,
            "title": f"Lô #{b_idx + 1}",
            "range_label": f"Câu {start_num} - {end_num}",
            "start_index": start_num,
            "end_index": end_num,
            "total": b_count,
            "translated": b_trans,
            "status": status,
            "is_done": status == "COMPLETED"
        })

    return {
        "project_id": project_id,
        "total_items": total_count,
        "total_translated": total_translated,
        "batch_size": batch_size,
        "total_batches": total_batches,
        "batches": batches
    }

@router.get("/{project_id}/dialogues", summary="Lấy danh sách câu thoại theo Lô hoặc phân trang an toàn")
def get_project_dialogues_paginated(
    project_id: int,
    batch_index: Optional[int] = Query(default=None, ge=0),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=250, ge=5, le=2000),
    db: Session = Depends(get_db)
):
    query = db.query(DialogueSegmentModel).filter(DialogueSegmentModel.task_id == project_id)

    total_count = query.with_entities(func.count(DialogueSegmentModel.id)).scalar() or 0

    # Nếu người dùng truyền batch_index trực tiếp
    if batch_index is not None:
        start_idx = batch_index * page_size + 1
        end_idx = (batch_index + 1) * page_size
        dialogues = query.filter(
            DialogueSegmentModel.index >= start_idx,
            DialogueSegmentModel.index <= end_idx
        ).order_by(DialogueSegmentModel.index.asc()).all()

        return {
            "project_id": project_id,
            "batch_index": batch_index,
            "page_size": page_size,
            "total_items": total_count,
            "items": [
                {
                    "id": d.id,
                    "index": d.index,
                    "tts_index": d.tts_index,
                    "start_time": d.start_time,
                    "end_time": d.end_time,
                    "duration": d.duration,
                    "original_text": d.original_text,
                    "clean_text": d.clean_text,
                    "translated_text": d.translated_text,
                    "voice_audio_path": getattr(d, 'voice_audio_path', None),
                    "speed_ratio": getattr(d, 'speed_ratio', 1.0),
                    "status": d.status
                }
                for d in dialogues
            ]
        }

    # Phân trang thông thường nhưng giới hạn page_size mặc định gọn nhẹ 250
    total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 1
    offset = (page - 1) * page_size
    dialogues = query.order_by(DialogueSegmentModel.index.asc()).offset(offset).limit(page_size).all()
    
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
                "tts_index": d.tts_index,
                "start_time": d.start_time,
                "end_time": d.end_time,
                "duration": d.duration,
                "original_text": d.original_text,
                "clean_text": d.clean_text,
                "translated_text": d.translated_text,
                "voice_audio_path": getattr(d, 'voice_audio_path', None),
                "speed_ratio": getattr(d, 'speed_ratio', 1.0),
                "status": d.status
            }
            for d in dialogues
        ]
    }

@router.get("/{project_id}/tts-dialogues", summary="Lấy danh sách câu gộp TTS (Phục vụ TTS & Giao diện AIREAD)")
def get_project_tts_dialogues(
    project_id: int,
    batch_index: Optional[int] = Query(default=None, ge=1),
    db: Session = Depends(get_db)
):
    query = db.query(TTSDialogueSegmentModel).filter(TTSDialogueSegmentModel.task_id == project_id)
    if batch_index is not None:
        query = query.filter(TTSDialogueSegmentModel.batch_index == batch_index)
    items = query.order_by(TTSDialogueSegmentModel.tts_index.asc()).all()
    return {
        "project_id": project_id,
        "batch_index": batch_index,
        "total_items": len(items),
        "items": [
            {
                "id": t.id,
                "tts_index": t.tts_index,
                "batch_index": t.batch_index,
                "src_start_index": t.src_start_index,
                "src_end_index": t.src_end_index,
                "src_indices": t.src_indices,
                "orig_count": t.orig_count,
                "start_time": t.start_time,
                "end_time": t.end_time,
                "duration": t.duration,
                "borrowed_start_time": t.borrowed_start_time,
                "borrowed_end_time": t.borrowed_end_time,
                "borrowed_duration": t.borrowed_duration,
                "original_text": t.original_text,
                "translated_text": t.translated_text,
                "speaker": t.speaker,
                "voice_code": t.voice_code,
                "voice_audio_path": t.voice_audio_path,
                "voice_duration": t.voice_duration,
                "speed_ratio": t.final_speed_ratio,
                "status": t.status,
                "is_locked": t.is_locked
            }
            for t in items
        ]
    }

@router.post("/{project_id}/dialogues/update", summary="Cập nhật bản dịch câu thoại vào Database SQLite")
def update_project_dialogues(
    project_id: int,
    payload: BatchDialogueUpdatePayload,
    db: Session = Depends(get_db)
):
    project = db.query(ProjectTask).filter(ProjectTask.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Không tìm thấy dự án!")

    updated_count = 0
    for item in payload.items:
        new_text = (item.translated_text or "").strip()

        # 1. Thử tìm trong bảng câu gộp TTS (TTSDialogueSegmentModel)
        tts_row = db.query(TTSDialogueSegmentModel).filter(
            TTSDialogueSegmentModel.task_id == project_id,
            TTSDialogueSegmentModel.id == item.id
        ).first()

        if tts_row:
            tts_row.translated_text = new_text
            if item.speaker:
                tts_row.speaker = item.speaker
            if new_text:
                tts_row.status = "TRANSLATED"

            # Đồng bộ sang các mẩu ASR con trong DialogueSegmentModel
            src_str = tts_row.src_indices or ""
            child_ids = [int(x) for x in src_str.split(",") if x.strip().isdigit()]
            if not child_ids and tts_row.src_start_index and tts_row.src_end_index:
                child_ids = list(range(tts_row.src_start_index, tts_row.src_end_index + 1))

            if child_ids:
                diag_children = db.query(DialogueSegmentModel).filter(
                    DialogueSegmentModel.task_id == project_id,
                    DialogueSegmentModel.index.in_(child_ids)
                ).all()
                if len(diag_children) == 1:
                    diag_children[0].translated_text = new_text
                    if new_text:
                        diag_children[0].status = "TRANSLATED"
                elif len(diag_children) > 1:
                    diag_children[0].translated_text = new_text
                    if new_text:
                        diag_children[0].status = "TRANSLATED"
                    for dc in diag_children[1:]:
                        dc.translated_text = ""
                        dc.status = "MERGED_CHILD"
            updated_count += 1
            continue

        # 2. Nếu không tìm thấy trong TTS, tìm trực tiếp trong DialogueSegmentModel
        diag_row = db.query(DialogueSegmentModel).filter(
            DialogueSegmentModel.task_id == project_id,
            DialogueSegmentModel.id == item.id
        ).first()

        if diag_row:
            diag_row.translated_text = new_text
            if item.speaker:
                diag_row.speaker = item.speaker
            if new_text:
                diag_row.status = "TRANSLATED"

            # Đồng bộ ngược lại TTSDialogueSegmentModel nếu có chunk 1-1
            tts_parent = db.query(TTSDialogueSegmentModel).filter(
                TTSDialogueSegmentModel.task_id == project_id,
                TTSDialogueSegmentModel.src_start_index <= diag_row.index,
                TTSDialogueSegmentModel.src_end_index >= diag_row.index
            ).first()
            if tts_parent and tts_parent.orig_count == 1:
                tts_parent.translated_text = new_text
                if new_text:
                    tts_parent.status = "TRANSLATED"

            updated_count += 1

    db.commit()
    return {
        "status": "success",
        "updated_count": updated_count,
        "message": f"Đã lưu thành công {updated_count} câu thoại vào Database SQLite!"
    }

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

@router.post("/{project_id}/reset-to-translated", summary="Reset Project về tình trạng ĐÃ DỊCH (giữ video gốc và câu thoại đã dịch, chỉ dọn dẹp TTS audio và video render)")
def reset_project_to_translated(project_id: int, db: Session = Depends(get_db)):
    """
    Reset thông minh: Chỉ dọn dẹp âm thanh TTS thuyết minh và Video render thành phẩm.
    GIỮ NGUYÊN 100%:
    - Video gốc đã tải ({video_id}_raw.mp4)
    - Toàn bộ câu thoại gốc và bản dịch tiếng Việt trong Database
    - File SRT/TXT/JSON, timeline_balance_report.json, thư mục entities/ và llm_io/
    XÓA SẠCH:
    - Thư mục temp/ của dự án (chứa các đoạn render video tạm, file audio mix tạm)
    - File video hoàn chỉnh trong output/final_videos/
    - File voiceover cũ
    Đưa trạng thái dự án về 'TRANSLATED' để người dùng có thể chạy lại TTS/Render mà không phải tải lại hay dịch lại từ đầu.
    """
    project = db.query(ProjectTask).filter(ProjectTask.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Không tìm thấy project để reset!")

    vid = project.video_id
    raw_video_abs = os.path.abspath(project.video_path) if project.video_path else None
    deleted_files = []
    import shutil

    # 1. Dọn sạch các file tạm liên quan đến vid trong thư mục TEMP_DIR tổng
    if settings.TEMP_DIR.exists():
        for f in list(settings.TEMP_DIR.glob(f"*{vid}*")):
            try:
                if f.is_file():
                    f.unlink(missing_ok=True)
                    deleted_files.append(f"temp/{f.name}")
                elif f.is_dir():
                    shutil.rmtree(f, ignore_errors=True)
                    deleted_files.append(f"temp/{f.name}/")
            except Exception as e:
                print(f"Lỗi xóa temp: {e}")

    # 2. Quét dọn các file voiceover cũ và cache TTS
    scan_dirs = [
        settings.OUTPUT_VOICEOVER_DIR,
        settings.TEMP_TTS_DIR,
        settings.BASE_DIR / "scratch",
    ]
    for target_dir in scan_dirs:
        if target_dir.exists():
            for f in list(target_dir.glob(f"*{vid}*")):
                try:
                    abs_f = os.path.abspath(str(f))
                    if raw_video_abs and abs_f == raw_video_abs:
                        continue
                    if f.is_file():
                        f.unlink(missing_ok=True)
                        deleted_files.append(f.name)
                    elif f.is_dir():
                        shutil.rmtree(f, ignore_errors=True)
                        deleted_files.append(f"{f.name}/")
                except Exception:
                    pass

    # 3. Xóa final_video_path trong output/final_videos/ nếu có
    if project.final_video_path and os.path.exists(project.final_video_path):
        try:
            abs_p = os.path.abspath(project.final_video_path)
            if not (raw_video_abs and abs_p == raw_video_abs):
                os.remove(project.final_video_path)
                deleted_files.append(os.path.basename(project.final_video_path))
        except Exception:
            pass

    # Quét thêm trong output/final_videos các file dính vid
    if settings.OUTPUT_FINAL_VIDEOS_DIR.exists():
        for f in list(settings.OUTPUT_FINAL_VIDEOS_DIR.glob(f"*{vid}*")):
            try:
                abs_f = os.path.abspath(str(f))
                if not (raw_video_abs and abs_f == raw_video_abs):
                    f.unlink(missing_ok=True)
                    deleted_files.append(f.name)
            except Exception:
                pass

    # 4. Kiểm tra xem project có câu thoại đã dịch không để đặt trạng thái chuẩn
    total_diag = db.query(DialogueSegmentModel).filter(DialogueSegmentModel.task_id == project_id).count()
    translated_diag = db.query(DialogueSegmentModel).filter(
        DialogueSegmentModel.task_id == project_id,
        DialogueSegmentModel.translated_text.isnot(None),
        DialogueSegmentModel.translated_text != ""
    ).count()

    if translated_diag > 0:
        project.status = "TRANSLATED"
    elif total_diag > 0:
        project.status = "TRANSCRIBED"
    else:
        project.status = "DOWNLOADED"

    project.final_video_path = None
    project.error_message = None

    db.commit()
    db.refresh(project)

    return {
        "status": "success",
        "message": f"Đã reset dự án #{project_id} ({vid}) về tình trạng '{project.status}'! Đã giữ nguyên toàn bộ kịch bản/bản dịch và dọn dẹp {len(deleted_files)} file TTS & video render.",
        "project_id": project.id,
        "video_path": project.video_path,
        "status_code": project.status,
        "deleted_files_count": len(deleted_files),
        "deleted_files": deleted_files[:10]
    }


@router.post("/{project_id}/reset", summary="Reset Project về lúc mới tải xong (giữ lại video gốc, xóa toàn bộ sản phẩm phái sinh)")
def reset_project(project_id: int, db: Session = Depends(get_db)):
    """
    Reset về lúc mới tải xong:
    GIỮ NGUYÊN DUY NHẤT: File video gốc ({video_id}_raw.mp4 / .mkv) trong output/projects/project_{id}/
    XÓA SẠCH 100%:
    - Trong SQL: Xóa sạch cả 2 bảng dialogue_segments và tts_dialogue_segments
    - Trên đĩa:
      + temp/tts_cache/{video_id}
      + temp/*{video_id}*
      + output/final_videos/*{video_id}*
      + input/audio_raw/*{video_id}*
      + output/transcripts/*{video_id}*
      + Toàn bộ file và thư mục con trong project_{id} (trừ file video raw)
    Đưa trạng thái về DOWNLOADED.
    """
    project = db.query(ProjectTask).filter(ProjectTask.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Không tìm thấy project để reset!")

    vid = project.video_id
    raw_video_abs = os.path.abspath(project.video_path) if project.video_path else None
    deleted_files = []
    import shutil

    from app.config import get_project_dirs
    p_dirs = get_project_dirs(project_id)
    root_dir = p_dirs["root"]

    # 1. Dọn sạch toàn bộ thư mục con bên trong project_{id} (entities, llm_io, tts_voiceover, video_subtitles)
    target_sub_dirs = [
        p_dirs.get("entities"),
        p_dirs.get("llm_io"),
        p_dirs.get("tts_voiceover"),
        p_dirs.get("video_subtitles"),
    ]
    for sub in target_sub_dirs:
        if sub and sub.exists():
            for f in list(sub.iterdir()):
                try:
                    if f.is_file():
                        f.unlink(missing_ok=True)
                        deleted_files.append(f"{sub.name}/{f.name}")
                    elif f.is_dir():
                        shutil.rmtree(f, ignore_errors=True)
                        deleted_files.append(f"{sub.name}/{f.name}/")
                except Exception as e:
                    print(f"Lỗi xóa {sub.name}: {e}")

    # 2. Xóa các file phái sinh trong root_dir của project (GIỮ DUY NHẤT RAW VIDEO GỐC)
    if root_dir.exists():
        for f in list(root_dir.iterdir()):
            try:
                if f.is_dir():
                    continue
                abs_f = os.path.abspath(str(f))
                if raw_video_abs and abs_f == raw_video_abs:
                    continue
                if f.name.endswith("_raw.mp4") or f.name.endswith("_raw.mkv"):
                    continue
                f.unlink(missing_ok=True)
                deleted_files.append(f.name)
            except Exception as e:
                print(f"Lỗi xóa file trong root_dir {f.name}: {e}")

    # 3. Dọn sạch cache TTS từng câu trong temp/tts_cache/
    tts_cache_dir = settings.TEMP_TTS_DIR / vid
    if tts_cache_dir.exists():
        try:
            shutil.rmtree(tts_cache_dir, ignore_errors=True)
            deleted_files.append(f"temp/tts_cache/{vid}/")
        except Exception:
            pass

    if settings.TEMP_TTS_DIR.exists():
        for d in list(settings.TEMP_TTS_DIR.glob(f"*{vid}*")):
            try:
                if d.is_dir():
                    shutil.rmtree(d, ignore_errors=True)
                else:
                    d.unlink(missing_ok=True)
                deleted_files.append(f"temp/tts_cache/{d.name}")
            except Exception:
                pass

    # 4. Dọn sạch các file tạm trong TEMP_DIR tổng
    if settings.TEMP_DIR.exists():
        for f in list(settings.TEMP_DIR.glob(f"*{vid}*")):
            try:
                if f.is_file():
                    f.unlink(missing_ok=True)
                    deleted_files.append(f"temp/{f.name}")
                elif f.is_dir() and f.name != "tts_cache":
                    shutil.rmtree(f, ignore_errors=True)
                    deleted_files.append(f"temp/{f.name}/")
            except Exception:
                pass

    # 5. Dọn sạch final video trong output/final_videos/
    if settings.OUTPUT_FINAL_VIDEOS_DIR.exists():
        for f in list(settings.OUTPUT_FINAL_VIDEOS_DIR.glob(f"*{vid}*")):
            try:
                abs_f = os.path.abspath(str(f))
                if not (raw_video_abs and abs_f == raw_video_abs):
                    f.unlink(missing_ok=True)
                    deleted_files.append(f"output/final_videos/{f.name}")
            except Exception:
                pass

    # 6. Dọn sạch audio gốc bóc tách và transcript gốc (về đúng lúc mới tải xong)
    for target_dir in [settings.INPUT_AUDIO_DIR, settings.OUTPUT_TRANSCRIPTS_DIR]:
        if target_dir and target_dir.exists():
            for f in list(target_dir.glob(f"*{vid}*")):
                try:
                    if f.is_file():
                        f.unlink(missing_ok=True)
                        deleted_files.append(f"{target_dir.name}/{f.name}")
                    elif f.is_dir():
                        shutil.rmtree(f, ignore_errors=True)
                        deleted_files.append(f"{target_dir.name}/{f.name}/")
                except Exception:
                    pass

    # 7. Dọn sạch các legacy directory khác
    legacy_dirs = [
        settings.OUTPUT_RAW_CLEANED_DIR,
        settings.OUTPUT_ENTITIES_DIR,
        settings.OUTPUT_DICH_AI_LLM_DIR,
        settings.OUTPUT_POST_PROCESSED_DIR,
        settings.OUTPUT_TRANSLATIONS_DIR,
        settings.OUTPUT_VOICEOVER_DIR,
        settings.BASE_DIR / "scratch",
    ]
    for target_dir in legacy_dirs:
        if target_dir and target_dir.exists():
            for f in list(target_dir.glob(f"*{vid}*")):
                try:
                    abs_f = os.path.abspath(str(f))
                    if raw_video_abs and abs_f == raw_video_abs:
                        continue
                    if f.is_file():
                        f.unlink(missing_ok=True)
                        deleted_files.append(f"{target_dir.name}/{f.name}")
                    elif f.is_dir():
                        shutil.rmtree(f, ignore_errors=True)
                        deleted_files.append(f"{target_dir.name}/{f.name}/")
                except Exception:
                    pass

    # 8. XÓA SẠCH 100% CẢ 2 BẢNG TRONG SQLITE
    db.query(TTSDialogueSegmentModel).filter(TTSDialogueSegmentModel.task_id == project_id).delete(synchronize_session=False)
    db.query(DialogueSegmentModel).filter(DialogueSegmentModel.task_id == project_id).delete(synchronize_session=False)
    db.commit()

    for obj in list(db.identity_map.values()):
        if isinstance(obj, (DialogueSegmentModel, TTSDialogueSegmentModel)):
            db.expunge(obj)

    # 9. Đưa trạng thái ProjectTask về lúc mới tải xong (DOWNLOADED)
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
        "message": f"Đã reset dự án #{project_id} ({vid}) về lúc mới tải xong! Đã xóa sạch 100% câu thoại trong SQL và {len(deleted_files)} file trên đĩa, giữ nguyên duy nhất video gốc.",
        "project_id": project.id,
        "video_path": project.video_path,
        "status_code": project.status,
        "deleted_files_count": len(deleted_files),
        "deleted_files": deleted_files[:15]
    }


@router.post("/{project_id}/reset-translation", summary="Reset riêng bản dịch (giữ câu gốc tiếng Trung ASR, xóa sạch 100% bản dịch trên máy và DB)")
def reset_translation(project_id: int, db: Session = Depends(get_db)):
    """
    Reset bản dịch:
    GIỮ NGUYÊN:
    - Video gốc ({video_id}_raw.mp4 / .mkv)
    - Toàn bộ câu thoại gốc tiếng Trung ASR trong SQL (DialogueSegmentModel.original_text, clean_text, start_time, end_time)
    - File audio bóc tách giọng gốc (để KHÔNG PHẢI gửi CapCut STT lại)
    XÓA SẠCH 100%:
    - Trong SQL:
      + Xóa sạch bảng tts_dialogue_segments (0 câu)
      + Reset dialogue_segments: translated_text = NULL, status = 'RAW', tts_index = NULL
    - Trên đĩa:
      + temp/tts_cache/{video_id}/ (xóa toàn bộ audio từng câu trong cache)
      + temp/*{video_id}*
      + output/final_videos/*{video_id}* (xóa video thành phẩm)
      + llm_io/* (xóa batch_*_output.json, prompt)
      + entities/* (xóa all_entities.json)
      + tts_voiceover/* và video_subtitles/*
      + Toàn bộ file kịch bản tiếng Việt phái sinh (*_voiceover.mp3, *_karaoke.ass, script_vi.txt, transcript_vi.*)
    """
    project = db.query(ProjectTask).filter(ProjectTask.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Không tìm thấy project để reset bản dịch!")

    vid = project.video_id
    raw_video_abs = os.path.abspath(project.video_path) if project.video_path else None
    deleted_files = []
    import shutil

    from app.config import get_project_dirs
    p_dirs = get_project_dirs(project_id)
    root_dir = p_dirs["root"]

    # 1. Dọn sạch toàn bộ thư mục con llm_io, entities, tts_voiceover, video_subtitles
    target_sub_dirs = [
        p_dirs.get("entities"),
        p_dirs.get("llm_io"),
        p_dirs.get("tts_voiceover"),
        p_dirs.get("video_subtitles"),
    ]
    for sub in target_sub_dirs:
        if sub and sub.exists():
            for f in list(sub.iterdir()):
                try:
                    if f.is_file():
                        f.unlink(missing_ok=True)
                        deleted_files.append(f"{sub.name}/{f.name}")
                    elif f.is_dir():
                        shutil.rmtree(f, ignore_errors=True)
                        deleted_files.append(f"{sub.name}/{f.name}/")
                except Exception as e:
                    print(f"Lỗi xóa {sub.name}: {e}")

    # 2. Xóa các file phái sinh dịch thuật trong root_dir (GIỮ NGUYÊN video raw, audio wav gốc và text cleaned ASR)
    if root_dir.exists():
        for f in list(root_dir.iterdir()):
            try:
                if f.is_dir():
                    continue
                abs_f = os.path.abspath(str(f))
                if raw_video_abs and abs_f == raw_video_abs:
                    continue
                if f.name.endswith("_raw.mp4") or f.name.endswith("_raw.mkv"):
                    continue
                if f.name.endswith("_cleaned.txt") or f.name == f"{vid}.wav" or f.name.endswith("_16k.wav"):
                    # Giữ lại file audio/text ASR gốc để không phải STT lại
                    continue
                f.unlink(missing_ok=True)
                deleted_files.append(f.name)
            except Exception as e:
                print(f"Lỗi xóa file trong root_dir {f.name}: {e}")

    # 3. Dọn sạch cache TTS từng câu trong temp/tts_cache/
    tts_cache_dir = settings.TEMP_TTS_DIR / vid
    if tts_cache_dir.exists():
        try:
            shutil.rmtree(tts_cache_dir, ignore_errors=True)
            deleted_files.append(f"temp/tts_cache/{vid}/")
        except Exception:
            pass

    if settings.TEMP_TTS_DIR.exists():
        for d in list(settings.TEMP_TTS_DIR.glob(f"*{vid}*")):
            try:
                if d.is_dir():
                    shutil.rmtree(d, ignore_errors=True)
                else:
                    d.unlink(missing_ok=True)
                deleted_files.append(f"temp/tts_cache/{d.name}")
            except Exception:
                pass

    # 4. Dọn sạch file audio mix và file tạm trong TEMP_DIR
    if settings.TEMP_DIR.exists():
        for f in list(settings.TEMP_DIR.glob(f"*{vid}*")):
            try:
                if f.is_file():
                    f.unlink(missing_ok=True)
                    deleted_files.append(f"temp/{f.name}")
                elif f.is_dir() and f.name != "tts_cache":
                    shutil.rmtree(f, ignore_errors=True)
                    deleted_files.append(f"temp/{f.name}/")
            except Exception:
                pass

    # 5. Dọn sạch video thành phẩm trong output/final_videos/
    if settings.OUTPUT_FINAL_VIDEOS_DIR.exists():
        for f in list(settings.OUTPUT_FINAL_VIDEOS_DIR.glob(f"*{vid}*")):
            try:
                abs_f = os.path.abspath(str(f))
                if not (raw_video_abs and abs_f == raw_video_abs):
                    f.unlink(missing_ok=True)
                    deleted_files.append(f"output/final_videos/{f.name}")
            except Exception:
                pass

    # 6. Dọn sạch trong legacy translation directories
    legacy_trans_dirs = [
        settings.OUTPUT_ENTITIES_DIR,
        settings.OUTPUT_DICH_AI_LLM_DIR,
        settings.OUTPUT_POST_PROCESSED_DIR,
        settings.OUTPUT_TRANSLATIONS_DIR,
        settings.OUTPUT_VOICEOVER_DIR,
    ]
    for target_dir in legacy_trans_dirs:
        if target_dir and target_dir.exists():
            for f in list(target_dir.glob(f"*{vid}*")):
                try:
                    abs_f = os.path.abspath(str(f))
                    if raw_video_abs and abs_f == raw_video_abs:
                        continue
                    if f.is_file():
                        f.unlink(missing_ok=True)
                        deleted_files.append(f"{target_dir.name}/{f.name}")
                    elif f.is_dir():
                        shutil.rmtree(f, ignore_errors=True)
                        deleted_files.append(f"{target_dir.name}/{f.name}/")
                except Exception:
                    pass

    # 7. XÓA SẠCH TRONG SQLITE:
    # 7a. Xóa sạch 100% bảng TTSDialogueSegmentModel
    db.query(TTSDialogueSegmentModel).filter(TTSDialogueSegmentModel.task_id == project_id).delete(synchronize_session=False)

    # 7b. Reset DialogueSegmentModel về RAW và xóa translated_text (GIỮ NGUYÊN câu thoại gốc tiếng Trung ASR)
    db.query(DialogueSegmentModel).filter(DialogueSegmentModel.task_id == project_id).update({
        DialogueSegmentModel.translated_text: None,
        DialogueSegmentModel.status: "RAW",
        DialogueSegmentModel.tts_index: None
    }, synchronize_session=False)
    db.commit()

    for obj in list(db.identity_map.values()):
        if isinstance(obj, (DialogueSegmentModel, TTSDialogueSegmentModel)):
            db.expunge(obj)

    # 8. Cập nhật trạng thái project
    total_diag = db.query(DialogueSegmentModel).filter(DialogueSegmentModel.task_id == project_id).count()
    project.status = "TRANSCRIBED" if total_diag > 0 else "DOWNLOADED"
    project.srt_path = None
    project.final_video_path = None
    project.error_message = None

    db.commit()
    db.refresh(project)

    return {
        "status": "success",
        "message": f"Đã reset sạch 100% bản dịch tiếng Việt, audio TTS và video trong SQL và {len(deleted_files)} file trên đĩa! Giữ nguyên {total_diag} câu thoại gốc để dịch lại ngay mà không cần gửi CapCut STT lại.",
        "project_id": project.id,
        "video_path": project.video_path,
        "status_code": project.status,
        "total_dialogues": total_diag,
        "deleted_files_count": len(deleted_files),
        "deleted_files": deleted_files[:15]
    }
