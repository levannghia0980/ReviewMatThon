import threading
import time
import asyncio
from typing import Dict, Any, Optional, List, Set

class TaskManager:
    _tasks: Dict[str, Dict[str, Any]] = {}
    _project_logs: Dict[int, List[Dict[str, Any]]] = {}
    _subscribers: Set[asyncio.Queue] = set()
    _lock = threading.Lock()

    @classmethod
    def create_task(cls, task_id: str, meta: Dict[str, Any] = None) -> Dict[str, Any]:
        with cls._lock:
            task = {
                "id": task_id,
                "status": "pending",
                "step": 1,
                "message": "Đang khởi tạo...",
                "progress": 0,
                "logs": [],
                "result": None,
                "error": None,
                "created_at": time.time(),
                "updated_at": time.time(),
                "meta": meta or {}
            }
            cls._tasks[task_id] = task
            p_id = (meta or {}).get("project_id")
            if p_id and p_id not in cls._project_logs:
                cls._project_logs[p_id] = []
            return task

    @classmethod
    def add_log(cls, task_id: str, text: str, log_type: str = "cyan"):
        with cls._lock:
            current_time = time.strftime("%H:%M:%S")
            log_item = {
                "time": current_time,
                "text": text,
                "type": log_type
            }
            if task_id in cls._tasks:
                task = cls._tasks[task_id]
                logs = task["logs"]
                logs.append(log_item)
                if len(logs) > 150:
                    del logs[:30]
                task["updated_at"] = time.time()
                
                # Lưu đồng thời vào danh mục Log của Project
                p_id = task.get("meta", {}).get("project_id")
                if p_id:
                    if p_id not in cls._project_logs:
                        cls._project_logs[p_id] = []
                    cls._project_logs[p_id].append(log_item)
                    if len(cls._project_logs[p_id]) > 200:
                        del cls._project_logs[p_id][:30]

            # In trực tiếp ra console terminal để người dùng theo dõi
            try:
                print(f"[{current_time}] {text}", flush=True)
            except UnicodeEncodeError:
                try:
                    safe_text = text.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
                    import sys
                    sys.stdout.buffer.write(f"[{current_time}] {safe_text}\n".encode("utf-8", errors="replace"))
                    sys.stdout.buffer.flush()
                except Exception:
                    pass
            except Exception:
                pass

        p_id = cls._tasks.get(task_id, {}).get("meta", {}).get("project_id")
        cls._broadcast_event("log", {
            "task_id": task_id,
            "project_id": p_id,
            "log": log_item
        })

    @classmethod
    def update_task(cls, task_id: str, **kwargs):
        t_snap = None
        with cls._lock:
            if task_id in cls._tasks:
                cls._tasks[task_id].update(kwargs)
                cls._tasks[task_id]["updated_at"] = time.time()
                t_snap = {
                    "id": task_id,
                    "status": cls._tasks[task_id].get("status"),
                    "step": cls._tasks[task_id].get("step", 1),
                    "progress": cls._tasks[task_id].get("progress", 0),
                    "message": cls._tasks[task_id].get("message", ""),
                    "project_id": cls._tasks[task_id].get("meta", {}).get("project_id")
                }
        if t_snap:
            cls._broadcast_event("task_update", t_snap)

    @classmethod
    def cancel_task(cls, task_id: str):
        p_id = None
        with cls._lock:
            if task_id in cls._tasks:
                cls._tasks[task_id]["status"] = "cancelled"
                cls._tasks[task_id]["message"] = "Đã hủy theo yêu cầu của người dùng"
                cls._tasks[task_id]["updated_at"] = time.time()
                p_id = cls._tasks[task_id].get("meta", {}).get("project_id")
                cls._tasks[task_id]["logs"].append({
                    "time": time.strftime("%H:%M:%S"),
                    "text": "🛑 Tiến trình đã bị người dùng hủy bỏ. Đang tự động dọn dẹp các file rác tạm thời...",
                    "type": "amber"
                })
        cls._broadcast_event("task_cancelled", {"task_id": task_id})

        # DỌN DẸP SẠCH BÓNG FILE RÁC TẠM TRONG FOLDER TEMP
        try:
            import shutil
            from app.config import get_project_dirs, TEMP_DIR
            # 1. Dọn folder temp của project nếu có project_id
            if p_id:
                p_dirs = get_project_dirs(p_id)
                proj_temp = p_dirs["temp"]
                if proj_temp.exists():
                    for item in proj_temp.glob("*"):
                        try:
                            if item.is_file():
                                item.unlink(missing_ok=True)
                            elif item.is_dir():
                                shutil.rmtree(item, ignore_errors=True)
                        except Exception:
                            pass
            # 2. Dọn folder temp chung của hệ thống
            if TEMP_DIR.exists():
                for item in TEMP_DIR.glob(f"*{task_id}*"):
                    try:
                        if item.is_file():
                            item.unlink(missing_ok=True)
                        elif item.is_dir():
                            shutil.rmtree(item, ignore_errors=True)
                    except Exception:
                        pass
        except Exception as e:
            print(f"Lỗi dọn dẹp file rác khi hủy task {task_id}: {e}")

    @classmethod
    def is_cancelled(cls, task_id: str) -> bool:
        with cls._lock:
            task = cls._tasks.get(task_id)
            if not task:
                return False
            return task.get("status") == "cancelled"

    @classmethod
    def get_task(cls, task_id: str) -> Optional[Dict[str, Any]]:
        with cls._lock:
            return cls._tasks.get(task_id)

    @classmethod
    def get_project_logs(cls, project_id: int) -> List[Dict[str, Any]]:
        with cls._lock:
            return list(cls._project_logs.get(project_id, []))

    @classmethod
    def get_all_active_tasks(cls) -> List[Dict[str, Any]]:
        with cls._lock:
            active = []
            for tid, t in cls._tasks.items():
                if t.get("status") in ["running", "pending"]:
                    p_id = t.get("meta", {}).get("project_id")
                    active.append({
                        "id": tid,
                        "project_id": p_id,
                        "status": t.get("status"),
                        "step": t.get("step", 1),
                        "progress": t.get("progress", 0),
                        "message": t.get("message", ""),
                        "last_log": t["logs"][-1]["text"] if t.get("logs") else ""
                    })
            return active

    @classmethod
    def list_tasks(cls) -> Dict[str, Dict[str, Any]]:
        with cls._lock:
            return dict(cls._tasks)

    @classmethod
    def subscribe(cls) -> asyncio.Queue:
        q = asyncio.Queue(maxsize=100)
        with cls._lock:
            cls._subscribers.add(q)
        return q

    @classmethod
    def unsubscribe(cls, q: asyncio.Queue):
        with cls._lock:
            cls._subscribers.discard(q)

    @classmethod
    def _broadcast_event(cls, event_type: str, data: Dict[str, Any]):
        """Bắn sự kiện sang toàn bộ các kết nối SSE đang mở"""
        with cls._lock:
            subs = list(cls._subscribers)
        if not subs:
            return
        payload = {"event": event_type, "data": data, "timestamp": time.time()}
        for q in subs:
            try:
                q.put_nowait(payload)
            except Exception:
                pass

task_manager = TaskManager()

