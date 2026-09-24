import threading
import time
from typing import Dict, Any, Optional, List

class TaskManager:
    _tasks: Dict[str, Dict[str, Any]] = {}
    _lock = threading.Lock()

    @classmethod
    def create_task(cls, task_id: str, meta: Dict[str, Any] = None) -> Dict[str, Any]:
        with cls._lock:
            task = {
                "id": task_id,
                "status": "pending",
                "step": 1, # 1: download, 2: audio, 3: whisper, 4: done
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
            return task

    @classmethod
    def add_log(cls, task_id: str, text: str, log_type: str = "cyan"):
        with cls._lock:
            if task_id in cls._tasks:
                cls._tasks[task_id]["logs"].append({
                    "time": time.strftime("%H:%M:%S"),
                    "text": text,
                    "type": log_type
                })
                cls._tasks[task_id]["updated_at"] = time.time()

    @classmethod
    def update_task(cls, task_id: str, **kwargs):
        with cls._lock:
            if task_id in cls._tasks:
                cls._tasks[task_id].update(kwargs)
                cls._tasks[task_id]["updated_at"] = time.time()

    @classmethod
    def cancel_task(cls, task_id: str):
        with cls._lock:
            if task_id in cls._tasks:
                cls._tasks[task_id]["status"] = "cancelled"
                cls._tasks[task_id]["message"] = "Đã hủy theo yêu cầu của người dùng"
                cls._tasks[task_id]["updated_at"] = time.time()
                cls._tasks[task_id]["logs"].append({
                    "time": time.strftime("%H:%M:%S"),
                    "text": "🛑 Tiến trình đã bị người dùng hủy bỏ. Đang dọn dẹp các chunk tạm...",
                    "type": "amber"
                })

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
    def list_tasks(cls) -> Dict[str, Dict[str, Any]]:
        with cls._lock:
            return dict(cls._tasks)

task_manager = TaskManager()

