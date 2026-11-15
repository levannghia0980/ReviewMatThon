import gc
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

logger = logging.getLogger(__name__)

_WINDOW_WORKING_SET_RESET = -1


def _get_win32_kernel_and_handle():
    """Trả về instance WinDLL('kernel32') và handle hiện tại với kiểu wintypes.HANDLE 64-bit chuẩn."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=False)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    return kernel32, kernel32.GetCurrentProcess()


def get_process_memory_mb() -> Dict[str, float]:
    """Đọc mức tiêu thụ RAM thực tế của tiến trình Python (Working Set & Private Bytes)."""
    try:
        import psutil
        mem = psutil.Process(os.getpid()).memory_info()
        return {
            "rss_mb": round(mem.rss / (1024 * 1024), 1),
            "vms_mb": round(mem.vms / (1024 * 1024), 1),
        }
    except ImportError:
        pass
    except Exception:
        return {}

    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class _PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                    ("PrivateUsage", ctypes.c_size_t),
                ]

            pmc = _PROCESS_MEMORY_COUNTERS_EX()
            pmc.cb = ctypes.sizeof(_PROCESS_MEMORY_COUNTERS_EX)
            kernel32, handle = _get_win32_kernel_and_handle()
            get_mem = getattr(kernel32, "K32GetProcessMemoryInfo", None)
            if get_mem is None:
                get_mem = ctypes.WinDLL("psapi", use_last_error=False).GetProcessMemoryInfo
            get_mem.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESS_MEMORY_COUNTERS_EX), wintypes.DWORD]
            get_mem.restype = wintypes.BOOL
            if get_mem(handle, ctypes.byref(pmc), pmc.cb):
                return {
                    "rss_mb": round(pmc.WorkingSetSize / (1024 * 1024), 1),
                    "vms_mb": round((pmc.PrivateUsage or pmc.PagefileUsage) / (1024 * 1024), 1),
                }
        except Exception:
            pass
    else:
        try:
            import resource
            ru = resource.getrusage(resource.RUSAGE_SELF)
            rss_kb = ru.ru_maxrss / 1024.0 if sys.platform == "darwin" else float(ru.ru_maxrss)
            return {"rss_mb": round(rss_kb / 1024.0, 1), "vms_mb": 0.0}
        except Exception:
            pass
    return {}


def kill_pid_tree(pid: Optional[int], wait_timeout: float = 1.5) -> bool:
    """Diệt sạch một PID và toàn bộ cây tiến trình con (hỗ trợ cả khi không có psutil)."""
    my_pid = os.getpid()
    if not pid or not isinstance(pid, int) or pid <= 0 or pid == my_pid:
        return False

    try:
        import psutil
        try:
            parent = psutil.Process(pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False

        targets: List[Any] = []
        try:
            targets.extend(parent.children(recursive=True))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        targets.append(parent)

        seen: Set[int] = set()
        killed_procs: List[Any] = []
        for proc in targets:
            try:
                if proc.pid == my_pid or proc.pid in seen:
                    continue
                seen.add(proc.pid)
                proc.kill()
                killed_procs.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        if killed_procs and wait_timeout > 0:
            try:
                psutil.wait_procs(killed_procs, timeout=wait_timeout)
            except Exception:
                pass
        return bool(killed_procs)
    except ImportError:
        pass
    except Exception:
        pass

    if sys.platform == "win32":
        try:
            res = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return res.returncode == 0
        except Exception:
            return False
    else:
        import signal
        try:
            os.kill(pid, signal.SIGKILL)
            return True
        except Exception:
            return False


def find_processes_by_cmdline(
    name_substrings: Iterable[str],
    cmdline_keywords: Iterable[str],
) -> List[Dict[str, Any]]:
    """Quét danh sách tiến trình khớp tên & chuỗi dòng lệnh (dùng psutil hoặc Windows CIM fallback)."""
    name_subs = tuple(s.lower() for s in name_substrings if s)
    cmd_keys = tuple(k.lower() for k in cmdline_keywords if k)
    my_pid = os.getpid()
    results: List[Dict[str, Any]] = []

    try:
        import psutil
        for proc in psutil.process_iter(["pid", "ppid", "name", "cmdline"]):
            try:
                pid = proc.info.get("pid")
                if not pid or pid == my_pid:
                    continue
                pname = (proc.info.get("name") or "").lower()
                if name_subs and not any(s in pname for s in name_subs):
                    continue
                cmdline = " ".join(proc.info.get("cmdline") or [])
                if cmd_keys and not any(k in cmdline.lower() for k in cmd_keys):
                    continue
                results.append({
                    "pid": pid,
                    "ppid": proc.info.get("ppid"),
                    "name": pname,
                    "cmdline": cmdline,
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return results
    except ImportError:
        pass
    except Exception:
        return results

    if sys.platform == "win32":
        try:
            wmi_filter = " or ".join(f"Name like '%{re.sub(r'[^a-zA-Z0-9_.-]', '', s)}%'" for s in name_subs)
            cmd_regex = "|".join(re.escape(k) for k in cmd_keys)
            ps_script = (
                f"Get-CimInstance Win32_Process"
                + (f" -Filter \"{wmi_filter}\"" if wmi_filter else "")
                + " | Where-Object { $_.CommandLine"
                + (f" -and ($_.CommandLine -match '{cmd_regex}')" if cmd_regex else "")
                + " } | Select-Object ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json -Compress"
            )
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                capture_output=True,
                text=True,
                timeout=6,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            raw = (proc.stdout or "").strip()
            if not raw:
                return results
            parsed = json.loads(raw)
            rows = parsed if isinstance(parsed, list) else [parsed]
            for r in rows:
                pid = r.get("ProcessId")
                if not pid or pid == my_pid:
                    continue
                results.append({
                    "pid": int(pid),
                    "ppid": int(r["ParentProcessId"]) if r.get("ParentProcessId") is not None else None,
                    "name": str(r.get("Name") or "").lower(),
                    "cmdline": str(r.get("CommandLine") or ""),
                })
        except Exception:
            pass
    return results


def bounded_cache_set(cache_dict: Dict[Any, Any], key: Any, value: Any, max_entries: int = 128) -> None:
    """Lưu giá trị vào dict cache có giới hạn số phần tử để tránh phình RAM vô hạn."""
    if max_entries <= 0:
        return
    if key in cache_dict:
        cache_dict.pop(key, None)
    cache_dict[key] = value
    while len(cache_dict) > max_entries:
        oldest_key = next(iter(cache_dict))
        cache_dict.pop(oldest_key, None)


def trim_windows_working_set() -> bool:
    """Ép hệ điều hành Windows thu hồi các trang RAM rảnh (C++/ONNX/OpenCV/Python heap)."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32, handle = _get_win32_kernel_and_handle()
        if hasattr(kernel32, "SetProcessWorkingSetSizeEx"):
            kernel32.SetProcessWorkingSetSizeEx.argtypes = [
                wintypes.HANDLE,
                ctypes.c_size_t,
                ctypes.c_size_t,
                wintypes.DWORD,
            ]
            kernel32.SetProcessWorkingSetSizeEx.restype = wintypes.BOOL
            if kernel32.SetProcessWorkingSetSizeEx(handle, ctypes.c_size_t(-1), ctypes.c_size_t(-1), 0):
                return True
        kernel32.SetProcessWorkingSetSize.argtypes = [wintypes.HANDLE, ctypes.c_size_t, ctypes.c_size_t]
        kernel32.SetProcessWorkingSetSize.restype = wintypes.BOOL
        if kernel32.SetProcessWorkingSetSize(handle, ctypes.c_size_t(-1), ctypes.c_size_t(-1)):
            return True
    except Exception:
        pass
    try:
        import ctypes
        from ctypes import wintypes

        psapi = ctypes.WinDLL("psapi", use_last_error=False)
        _, handle = _get_win32_kernel_and_handle()
        psapi.EmptyWorkingSet.argtypes = [wintypes.HANDLE]
        psapi.EmptyWorkingSet.restype = wintypes.BOOL
        return bool(psapi.EmptyWorkingSet(handle))
    except Exception:
        return False


def _remove_path_safe(target: Path) -> bool:
    """Xóa an toàn một file hoặc thư mục mà không ném ngoại lệ."""
    try:
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
            return not target.exists()
        if target.exists() or target.is_symlink():
            target.unlink(missing_ok=True)
            return not target.exists()
    except Exception:
        pass
    return False


def _is_protected_raw_audio(candidate: Path, raw_paths: Iterable[Optional[str]]) -> bool:
    """Bảo vệ file audio gốc / audio 16k chuẩn của project, chỉ xóa bản _clean / chunk trung gian."""
    try:
        cand_res = str(candidate.resolve()).lower()
    except Exception:
        cand_res = str(candidate).lower()
    for p in raw_paths:
        if not p:
            continue
        try:
            if str(Path(p).resolve()).lower() == cand_res:
                return True
        except Exception:
            if str(p).lower() == cand_res:
                return True
    return False


def shutdown_gemini_browser(exclude_task_id: Optional[str] = None, force: bool = False) -> bool:
    """Đóng trình duyệt Chromium Gemini Web từ luồng đồng bộ nếu không còn tác vụ dịch khác đang chạy."""
    try:
        from app.services.task_manager import task_manager
        if not force and task_manager.count_active_tasks(exclude_task_id=exclude_task_id, phase="translating") > 0:
            return False
    except Exception:
        pass

    try:
        from app.services.step_03_translation.gemini_web_service import GeminiWebService
        GeminiWebService.shutdown_worker_sync()
        return True
    except Exception as e:
        logger.debug(f"Lỗi khi đóng Gemini Web Worker (sync): {e}")
        return False


async def shutdown_gemini_browser_async(exclude_task_id: Optional[str] = None, force: bool = False) -> bool:
    """Đóng trình duyệt Chromium Gemini Web từ ngữ cảnh async (FastAPI endpoint/lifespan)."""
    try:
        from app.services.task_manager import task_manager
        if not force and task_manager.count_active_tasks(exclude_task_id=exclude_task_id, phase="translating") > 0:
            return False
    except Exception:
        pass

    try:
        from app.services.step_03_translation.gemini_web_service import GeminiWebService
        await GeminiWebService.shutdown_worker()
        return True
    except Exception as e:
        logger.debug(f"Lỗi khi đóng Gemini Web Worker (async): {e}")
        return False


def clear_stt_caches_and_temp(
    audio_path: Optional[str] = None,
    clean_audio_path: Optional[str] = None,
    video_path: Optional[str] = None,
    video_id: Optional[str] = None,
    purge_visual_cache: bool = False,
) -> int:
    """Giải phóng model Whisper (RAM/VRAM) và xóa file trung gian STT (_clean.wav, *_chunks, OCR cache)."""
    removed = 0
    try:
        from app.services.step_02_stt.whisper_service import WhisperService
        removed += WhisperService.unload_all()
    except Exception:
        pass

    try:
        from app.config import settings
        # 1. Xóa file *_clean.wav (không đụng tới audio_path thô nếu fallback không tạo file clean)
        if clean_audio_path:
            c_path = Path(clean_audio_path)
            if c_path.name.endswith("_clean.wav") and not _is_protected_raw_audio(c_path, [audio_path]):
                if _remove_path_safe(c_path):
                    removed += 1

        candidate_audio_paths = [p for p in (audio_path, clean_audio_path) if p]
        seen_dirs: Set[str] = set()
        for p_str in candidate_audio_paths:
            p_obj = Path(p_str)
            chunk_dir = p_obj.parent / f"{p_obj.stem}_chunks"
            if str(chunk_dir) not in seen_dirs and chunk_dir.exists():
                seen_dirs.add(str(chunk_dir))
                if _remove_path_safe(chunk_dir):
                    removed += 1
            groq_tmp = p_obj.parent / "tmp_groq_chunks"
            if str(groq_tmp) not in seen_dirs and groq_tmp.exists():
                seen_dirs.add(str(groq_tmp))
                if _remove_path_safe(groq_tmp):
                    removed += 1

        # Quét thêm trong INPUT_AUDIO_DIR theo video_id nếu có
        if video_id and settings.INPUT_AUDIO_DIR.exists():
            for f in list(settings.INPUT_AUDIO_DIR.glob(f"*{video_id}*_clean.wav")):
                if not _is_protected_raw_audio(f, [audio_path]) and _remove_path_safe(f):
                    removed += 1
            for d in list(settings.INPUT_AUDIO_DIR.glob(f"*{video_id}*_chunks")):
                if d.is_dir() and _remove_path_safe(d):
                    removed += 1
    except Exception as e:
        logger.debug(f"Lỗi dọn file trung gian STT: {e}")

    if purge_visual_cache:
        try:
            from app.services.step_02_stt.visual_sub_refiner import clear_visual_cache_for_video
            removed += clear_visual_cache_for_video(video_path=video_path, video_id=video_id)
        except Exception:
            pass

    return removed


def clear_translation_caches() -> None:
    """Giải phóng toàn bộ bộ nhớ đệm từ điển, Trie nhạy cảm và Hán-Việt sau khi dịch xong."""
    try:
        from app.services.step_03_translation.dichhan.translator import clear_translator_caches
        clear_translator_caches()
    except Exception:
        pass
    try:
        from app.services.step_03_translation.dichhan.hanviet_data import clear_hanviet_caches
        clear_hanviet_caches()
    except Exception:
        pass
    try:
        from app.services.step_03_translation.unblock.rawt.rawt_pipeline import clear_rawt_trie_cache
        clear_rawt_trie_cache()
    except Exception:
        pass
    try:
        from app.services.step_03_translation.unblock.unblock_pipeline import clear_unblock_trie_cache
        clear_unblock_trie_cache()
    except Exception:
        pass


def cleanup_tts_temp_cache(video_id: Optional[str] = None) -> int:
    """Xóa thư mục chunk/cache TTS tạm trên đĩa cho một video (hoặc toàn bộ nếu video_id=None)."""
    removed = 0
    try:
        from app.config import settings
        tts_temp_root = settings.TEMP_TTS_DIR
        if not tts_temp_root.exists():
            return 0
        if video_id:
            targets = set(tts_temp_root.glob(f"*{video_id}*"))
            direct_dir = tts_temp_root / video_id
            if direct_dir.exists():
                targets.add(direct_dir)
            for item in targets:
                if _remove_path_safe(item):
                    removed += 1
        else:
            for item in list(tts_temp_root.iterdir()):
                if _remove_path_safe(item):
                    removed += 1
    except Exception as e:
        logger.debug(f"Lỗi dọn temp/tts_cache: {e}")
    return removed


def clear_tts_caches(video_id: Optional[str] = None, clear_disk_cache: bool = True) -> int:
    """Giải phóng AudioSegment/timings cache trong RAM của TTS và dọn cache đĩa nếu cần."""
    removed = 0
    try:
        from app.services.step_04_tts.capcut_tts_service import CapCutTTSService
        removed += CapCutTTSService.clear_memory_cache()
    except Exception:
        pass
    try:
        from app.services.step_04_tts.tiktok_tts_service import TikTokTTSService
        removed += TikTokTTSService.clear_memory_cache()
    except Exception:
        pass
    try:
        from app.services.step_04_tts.async_tts_engine import AsyncTTSEngine
        removed += AsyncTTSEngine.clear_memory_cache()
    except Exception:
        pass

    if clear_disk_cache and video_id:
        removed += cleanup_tts_temp_cache(video_id=video_id)
    return removed


def clear_project_temp_files(
    video_id: Optional[str] = None,
    video_path: Optional[str] = None,
    audio_path: Optional[str] = None,
    clean_audio_path: Optional[str] = None,
    task_id: Optional[str] = None,
    clear_visual_cache: bool = True,
    exclude_task_id: Optional[str] = None,
) -> int:
    """
    Phương án B: Xóa triệt để mọi file tạm/trung gian gắn với project vừa hoàn tất hoặc bị lỗi/hủy
    mà KHÔNG làm ảnh hưởng tới các project khác đang chạy song song.
    """
    removed = 0
    try:
        from app.config import settings
        from app.services.task_manager import task_manager

        if video_id:
            removed += cleanup_tts_temp_cache(video_id=video_id)
            if settings.TEMP_DIR.exists():
                for item in list(settings.TEMP_DIR.glob(f"*{video_id}*")):
                    if item.name in ("tts_cache", "visual_sub_cache"):
                        continue
                    if _remove_path_safe(item):
                        removed += 1
            if settings.OUTPUT_FINAL_VIDEOS_DIR.exists():
                for seg_dir in list(settings.OUTPUT_FINAL_VIDEOS_DIR.glob(f"segs_{video_id}*")):
                    if seg_dir.is_dir() and _remove_path_safe(seg_dir):
                        removed += 1

        if task_id and settings.TEMP_DIR.exists():
            for item in list(settings.TEMP_DIR.glob(f"*{task_id}*")):
                if item.name in ("tts_cache", "visual_sub_cache"):
                    continue
                if _remove_path_safe(item):
                    removed += 1

        removed += clear_stt_caches_and_temp(
            audio_path=audio_path,
            clean_audio_path=clean_audio_path,
            video_path=video_path,
            video_id=video_id,
            purge_visual_cache=clear_visual_cache,
        )

        # Chỉ quét nốt file tạm vô chủ (tmp*, cache rỗng) khi KHÔNG còn tác vụ nào khác đang chạy
        eff_exclude = exclude_task_id or task_id
        if task_manager.count_active_tasks(exclude_task_id=eff_exclude) == 0:
            if settings.TEMP_TTS_DIR.exists():
                for stray in list(settings.TEMP_TTS_DIR.glob("tmp*")):
                    if _remove_path_safe(stray):
                        removed += 1
            if settings.TEMP_DIR.exists():
                for stray in list(settings.TEMP_DIR.glob("tmp*")):
                    if _remove_path_safe(stray):
                        removed += 1
            if settings.OUTPUT_FINAL_VIDEOS_DIR.exists():
                for seg_dir in list(settings.OUTPUT_FINAL_VIDEOS_DIR.glob("segs_*")):
                    if seg_dir.is_dir() and _remove_path_safe(seg_dir):
                        removed += 1
    except Exception as e:
        logger.debug(f"Lỗi khi dọn temp project: {e}")
    return removed


def trim_process_memory(exclude_task_id: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
    """Chạy Garbage Collection, cắt cache dài hạn và ép Windows trả RAM rảnh về OS."""
    pruned_tasks = 0
    pruned_meta = 0
    try:
        from app.services.task_manager import task_manager
        active_others = task_manager.count_active_tasks(exclude_task_id=exclude_task_id)
        should_trim_os = force or active_others == 0
    except Exception:
        should_trim_os = True

    try:
        from app.services.step_05_video.video_composer_service import VideoComposerService
        pruned_meta = VideoComposerService.prune_caches(max_entries=10 if should_trim_os else 25)
    except Exception:
        pass

    try:
        from app.services.task_manager import task_manager
        pruned_tasks = task_manager.prune_old_tasks()
    except Exception:
        pass

    collected = 0
    try:
        collected += gc.collect()
        collected += gc.collect()
    except Exception:
        pass

    ws_trimmed = False
    if should_trim_os:
        ws_trimmed = trim_windows_working_set()

    return {
        "gc_collected": collected,
        "pruned_tasks": pruned_tasks,
        "pruned_meta": pruned_meta,
        "working_set_trimmed": ws_trimmed,
        "memory": get_process_memory_mb(),
    }


def cleanup_after_task(
    stage: str,
    *,
    task_id: Optional[str] = None,
    project_id: Optional[int] = None,
    video_id: Optional[str] = None,
    video_path: Optional[str] = None,
    audio_path: Optional[str] = None,
    clean_audio_path: Optional[str] = None,
    purge_project_temp: bool = True,
    shutdown_browser: bool = True,
    force_trim: bool = False,
) -> Dict[str, Any]:
    """
    Hàm điều phối dọn tài nguyên chuẩn cho block `finally:` của mọi background worker.
    stage ∈ {"stt", "translate", "tts", "compose", "full_auto", "all"}
    """
    # Nếu mới chỉ có project_id, tra nhanh video_id / video_path / audio_path từ SQLite nếu chưa truyền
    if project_id and (not video_id or not video_path):
        try:
            from app.db.session import SessionLocal
            from app.models import ProjectTask
            db = SessionLocal()
            try:
                proj = db.query(ProjectTask).filter(ProjectTask.id == project_id).first()
                if proj:
                    video_id = video_id or proj.video_id
                    video_path = video_path or proj.video_path
                    audio_path = audio_path or proj.audio_path
            finally:
                db.close()
        except Exception:
            pass

    removed_files = 0
    browser_closed = False
    st = (stage or "all").lower()

    if st in ("translate", "full_auto", "all") and shutdown_browser:
        browser_closed = shutdown_gemini_browser(exclude_task_id=task_id, force=(st == "all"))

    if st in ("translate", "full_auto", "all"):
        clear_translation_caches()

    if st in ("stt", "translate", "full_auto", "all"):
        removed_files += clear_stt_caches_and_temp(
            audio_path=audio_path,
            clean_audio_path=clean_audio_path,
            video_path=video_path,
            video_id=video_id,
            purge_visual_cache=purge_project_temp,
        )

    if st in ("tts", "full_auto", "all"):
        removed_files += clear_tts_caches(video_id=video_id, clear_disk_cache=purge_project_temp)

    if st in ("compose", "full_auto", "all") and purge_project_temp:
        removed_files += clear_project_temp_files(
            video_id=video_id,
            video_path=video_path,
            audio_path=audio_path,
            clean_audio_path=clean_audio_path,
            task_id=task_id,
            clear_visual_cache=True,
            exclude_task_id=task_id,
        )

    if st == "all":
        try:
            from app.services.step_05_video.video_composer_service import VideoComposerService
            VideoComposerService.clear_metadata_cache()
        except Exception:
            pass

    trim_info = trim_process_memory(exclude_task_id=task_id, force=force_trim)
    return {
        "stage": st,
        "browser_closed": browser_closed,
        "removed_items": removed_files,
        **trim_info,
    }


async def cleanup_all_on_shutdown() -> None:
    """Dọn sạch toàn bộ tiến trình con, Chromium, cache RAM và working set khi tắt server."""
    try:
        from app.services.task_manager import task_manager
        task_manager.kill_all_subprocesses()
    except Exception:
        pass
    await shutdown_gemini_browser_async(force=True)
    clear_translation_caches()
    clear_stt_caches_and_temp(purge_visual_cache=False)
    clear_tts_caches(clear_disk_cache=False)
    try:
        from app.services.step_05_video.video_composer_service import VideoComposerService
        VideoComposerService.clear_metadata_cache()
    except Exception:
        pass
    trim_process_memory(force=True)
