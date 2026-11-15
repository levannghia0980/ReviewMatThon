import threading
import time
import asyncio
from typing import Dict, Any, Optional, List, Set

class TaskManager:
    _tasks: Dict[str, Dict[str, Any]] = {}
    _project_logs: Dict[int, List[Dict[str, Any]]] = {}
    _subscribers: Set[asyncio.Queue] = set()
    _active_subprocesses: Dict[str, Set[Any]] = {}
    _lock = threading.Lock()

    @classmethod
    def register_subprocess(cls, task_id: str, proc: Any):
        """Đăng ký tiến trình con (FFmpeg, Python CLI) để sẵn sàng kill tức thì khi hủy task"""
        if not task_id or not proc:
            return
        with cls._lock:
            if task_id not in cls._active_subprocesses:
                cls._active_subprocesses[task_id] = set()
            cls._active_subprocesses[task_id].add(proc)

    @classmethod
    def unregister_subprocess(cls, task_id: str, proc: Any):
        """Hủy đăng ký khi tiến trình con kết thúc bình thường"""
        if not task_id or not proc:
            return
        with cls._lock:
            if task_id in cls._active_subprocesses:
                cls._active_subprocesses[task_id].discard(proc)
                if not cls._active_subprocesses[task_id]:
                    cls._active_subprocesses.pop(task_id, None)

    @classmethod
    def clear_project_logs(cls, project_id: int) -> None:
        """Xóa nhật ký log in-memory của project sau khi project bị xóa."""
        with cls._lock:
            cls._project_logs.pop(project_id, None)

    @classmethod
    def cancel_tasks_for_project(cls, project_id: int) -> int:
        """Hủy toàn bộ tác vụ đang chạy/chờ của một project trước khi xóa dữ liệu."""
        if not project_id:
            return 0
        with cls._lock:
            task_ids = [
                tid for tid, t in cls._tasks.items()
                if t.get("status") in ("running", "pending")
                and t.get("meta", {}).get("project_id") == project_id
            ]
        for tid in task_ids:
            try:
                cls.cancel_task(tid)
            except Exception:
                pass
        cls.clear_project_logs(project_id)
        return len(task_ids)

    @classmethod
    def get_active_task_for_project(cls, project_id: int) -> Optional[str]:
        """Tìm task đang chạy của một project để chống chạy đè nhiều task"""
        if not project_id:
            return None
        with cls._lock:
            for tid, t in cls._tasks.items():
                if t.get("status") in ("running", "pending"):
                    if t.get("meta", {}).get("project_id") == project_id:
                        return tid
        return None

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
    def update_task_meta(cls, task_id: str, **meta_updates):
        """Cập nhật metadata động cho task (project_id, video_id, video_path, phase...)."""
        with cls._lock:
            if task_id in cls._tasks:
                meta = cls._tasks[task_id].setdefault("meta", {})
                meta.update({k: v for k, v in meta_updates.items() if v is not None})
                p_id = meta.get("project_id")
                if p_id and p_id not in cls._project_logs:
                    cls._project_logs[p_id] = []
                cls._tasks[task_id]["updated_at"] = time.time()

    @classmethod
    def count_active_tasks(cls, exclude_task_id: Optional[str] = None, phase: Optional[str] = None) -> int:
        """Đếm số tác vụ đang chạy/chờ (có thể lọc theo phase để tránh tắt nhầm tài nguyên dùng chung)."""
        with cls._lock:
            count = 0
            for tid, t in cls._tasks.items():
                if exclude_task_id and tid == exclude_task_id:
                    continue
                if t.get("status") in ("running", "pending"):
                    if phase and t.get("meta", {}).get("phase") != phase:
                        continue
                    count += 1
            return count

    @classmethod
    def prune_old_tasks(cls, max_completed: int = 30, max_age_sec: float = 7200.0) -> int:
        """Cắt tỉa lịch sử các task đã kết thúc khỏi RAM để server chạy cả ngày không phình bộ nhớ."""
        now = time.time()
        removed = 0
        with cls._lock:
            active_project_ids = {
                t.get("meta", {}).get("project_id")
                for t in cls._tasks.values()
                if t.get("status") in ("running", "pending") and t.get("meta", {}).get("project_id")
            }
            finished = [
                (tid, t.get("updated_at", 0.0))
                for tid, t in cls._tasks.items()
                if t.get("status") not in ("running", "pending")
            ]
            # Xóa trước các task đã xong quá hạn
            still_kept = []
            for tid, upd in finished:
                if (now - upd) > max_age_sec:
                    cls._tasks.pop(tid, None)
                    removed += 1
                else:
                    still_kept.append((tid, upd))
            # Nếu vẫn vượt quá max_completed, xóa các task cũ nhất
            if len(still_kept) > max_completed:
                still_kept.sort(key=lambda x: x[1])
                for tid, _ in still_kept[: len(still_kept) - max_completed]:
                    cls._tasks.pop(tid, None)
                    removed += 1

            # Cắt bớt nhật ký theo project nếu quá 30 project cũ (không đụng project đang chạy)
            if len(cls._project_logs) > 30:
                candidates = [pid for pid in list(cls._project_logs.keys()) if pid not in active_project_ids]
                for pid in candidates[: max(0, len(cls._project_logs) - 30)]:
                    cls._project_logs.pop(pid, None)
        return removed

    @classmethod
    def _kill_proc_tree(cls, proc: Any, label: str = "") -> None:
        """Diệt sạch tiến trình con và toàn bộ cây tiến trình con (FFmpeg, Chrome, Playwright)."""
        pid = getattr(proc, "pid", None)
        if pid:
            try:
                from app.utils.resource_cleaner import kill_pid_tree
                kill_pid_tree(int(pid), wait_timeout=1.0)
            except Exception:
                pass
        try:
            if hasattr(proc, "poll") and proc.poll() is None:
                proc.kill()
                print(f"🛑 [TaskManager] Đã cưỡng chế kill tiến trình PID {pid or 'N/A'}{label}")
        except Exception as e_kill:
            print(f"⚠️ [TaskManager] Lỗi khi kill tiến trình {pid or 'N/A'}: {e_kill}")

    @classmethod
    def kill_all_subprocesses(cls) -> int:
        """Cưỡng chế đóng toàn bộ tiến trình con đang chạy (gọi khi tắt server)."""
        with cls._lock:
            all_procs = []
            for tid in list(cls._active_subprocesses.keys()):
                all_procs.extend(list(cls._active_subprocesses.pop(tid, set())))
        for p in all_procs:
            cls._kill_proc_tree(p, " (shutdown)")
        return len(all_procs)

    @classmethod
    def cancel_task(cls, task_id: str):
        p_id = None
        meta_snap: Dict[str, Any] = {}
        procs_to_kill = []
        with cls._lock:
            if task_id in cls._tasks:
                cls._tasks[task_id]["status"] = "cancelled"
                cls._tasks[task_id]["message"] = "Đã hủy theo yêu cầu của người dùng"
                cls._tasks[task_id]["updated_at"] = time.time()
                meta_snap = dict(cls._tasks[task_id].get("meta") or {})
                p_id = meta_snap.get("project_id")
                cls._tasks[task_id]["logs"].append({
                    "time": time.strftime("%H:%M:%S"),
                    "text": "🛑 Tiến trình đã bị người dùng hủy bỏ. Đang cưỡng chế dừng mọi tiến trình ngầm (FFmpeg/Worker)...",
                    "type": "amber"
                })
            procs_to_kill = list(cls._active_subprocesses.pop(task_id, set()))

        # CƯỠNG CHẾ KILL NGAY LẬP TỨC TẤT CẢ TIẾN TRÌNH CON ĐANG CHẠY (FFMPEG, AUDIO MIXER, WORKER)
        for p in procs_to_kill:
            cls._kill_proc_tree(p, f" của task {task_id}")

        cls._broadcast_event("task_cancelled", {"task_id": task_id})

        # DỌN DẸP SẠCH FILE TẠM ĐÚNG PHẠM VI PROJECT/TASK ĐÓ (KHÔNG XÓA NHẦM TASK KHÁC ĐANG CHẠY SONG SONG)
        try:
            from app.utils.resource_cleaner import clear_project_temp_files
            vid = meta_snap.get("video_id")
            vpath = meta_snap.get("video_path")
            apath = meta_snap.get("audio_path")
            cpath = meta_snap.get("clean_audio_path")
            if p_id and (not vid or not vpath):
                try:
                    from app.db.session import SessionLocal
                    from app.models import ProjectTask
                    db = SessionLocal()
                    try:
                        proj = db.query(ProjectTask).filter(ProjectTask.id == p_id).first()
                        if proj:
                            vid = vid or proj.video_id
                            vpath = vpath or proj.video_path
                            apath = apath or proj.audio_path
                    finally:
                        db.close()
                except Exception:
                    pass
            clear_project_temp_files(
                video_id=vid,
                video_path=vpath,
                audio_path=apath,
                clean_audio_path=cpath,
                task_id=task_id,
                clear_visual_cache=True,
                exclude_task_id=task_id,
            )
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

