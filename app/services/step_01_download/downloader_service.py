import os
import re
import sys
import time
import json
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, Tuple, Optional, Callable
import requests
from app.config import settings
from app.utils.bin_helper import get_ytdlp_cmd, get_ffmpeg_cmd, get_aria2c_cmd

FAST_BILIBILI_MIRRORS = [
    "upos-sz-mirroraliov.bilivideo.com",
    "upos-sz-mirrorcosov.bilivideo.com",
    "upos-hz-mirrorakam.akamaized.net"
]

QUALITY_PRESETS = {
    "1080p": {
        "max_height": 1080,
        "format": "bv*[height<=1080]+ba/b[height<=1080]/bestvideo[height<=1080]+bestaudio/best",
        "format_sort": "res:1080,fps:60,ext:mp4:m4a"
    },
    "720p": {
        "max_height": 720,
        "format": "bv*[height<=720]+ba/b[height<=720]/bestvideo[height<=720]+bestaudio/best",
        "format_sort": "res:720,fps:60,ext:mp4:m4a"
    },
    "1440p": {
        "max_height": 99999,
        "format": "bv*+ba/b/bestvideo+bestaudio/best",
        "format_sort": "hasvid,res,fps,ext:mp4:m4a"
    },
    "480p": {
        "max_height": 480,
        "format": "bv*[height<=480]+ba/b[height<=480]/best",
        "format_sort": "res:480,ext:mp4:m4a"
    },
    "360p": {
        "max_height": 360,
        "format": "bv*[height<=360]+ba/b[height<=360]/worst",
        "format_sort": "res:360,ext:mp4:m4a"
    },
    "audio": {
        "max_height": 0,
        "format": "ba/b/bestaudio",
        "format_sort": "ext:m4a:mp3"
    }
}

class DownloaderService:
    @staticmethod
    def normalize_url(raw_url: str) -> str:
        """Tự động chuẩn hóa link đầu vào (BV code, Bilibili locale /vi/, /en/, tracking params, YouTube, Douyin, B23, TikTok...)"""
        u = (raw_url or "").strip()
        if not u:
            return ""

        # 1. Trường hợp người dùng chỉ nhập mã BV hoặc av (vd: BV1g18j6rExp hoặc av123456)
        if re.match(r'^(BV[a-zA-Z0-9]{10}|av\d+)$', u, re.IGNORECASE):
            return f"https://www.bilibili.com/video/{u}"

        # 2. Trường hợp link Bilibili (web, mobile, localized /vi/, /en/, /th/ hoặc chứa tham số tracking rác)
        if "bilibili.com" in u.lower() or "b23.tv" in u.lower():
            p_match = re.search(r'[?&]p=(\d+)', u)
            p_query = f"?p={p_match.group(1)}" if p_match else ""

            # Bóc tách mã BV (12 ký tự: BV + 10 ký tự alnum)
            bv_match = re.search(r'(BV[a-zA-Z0-9]{10})', u, re.IGNORECASE)
            if bv_match:
                return f"https://www.bilibili.com/video/{bv_match.group(1)}{p_query}"

            # Bóc tách mã av
            av_match = re.search(r'/(av\d+)', u, re.IGNORECASE) or re.search(r'\b(av\d+)\b', u, re.IGNORECASE)
            if av_match:
                return f"https://www.bilibili.com/video/{av_match.group(1)}{p_query}"

        return u

    @staticmethod
    def get_fast_mirror_candidates(raw_url: str) -> list:
        """Lấy danh sách các Mirror CDN Hải ngoại tốc độ cao (10-15MB/s), chốt server tốt nhất"""
        if not raw_url:
            return []
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": "https://www.bilibili.com/"
        }
        priority_mirrors = [
            "upos-sz-mirroraliov.bilivideo.com", # Alibaba Cloud Overseas (siêu tốc 10-15 MB/s, ổn định nhất)
            "upos-sz-mirrorcosov.bilivideo.com", # Tencent Cloud Overseas (3-5 MB/s)
            "upos-hz-mirrorakam.akamaized.net"   # Akamai Overseas (4-7 MB/s)
        ]
        candidates = []
        is_akam = "akam" in raw_url.lower()

        # Kiểm tra nhanh server nào đang hoạt động bằng Range bytes=0-0
        for m in priority_mirrors:
            cand = re.sub(r"https?://[^/]+", f"https://{m}", raw_url)
            try:
                r = requests.get(cand, headers=dict(headers, Range="bytes=0-0"), timeout=1.5)
                if r.status_code in (200, 206):
                    candidates.append(cand)
                    if len(candidates) >= 2:
                        break
            except Exception:
                pass

        if not is_akam and raw_url not in candidates:
            candidates.insert(0, raw_url)
        elif raw_url not in candidates:
            candidates.append(raw_url)

        return candidates if candidates else [raw_url]

    @staticmethod
    def get_fast_mirror_url(raw_url: str) -> str:
        """Đổi máy chủ CDN Bilibili sang Mirror siêu tốc tốt nhất (Tương thích ngược)"""
        cands = DownloaderService.get_fast_mirror_candidates(raw_url)
        return cands[0] if cands else raw_url

    @staticmethod
    def download_stream_aria2(
        stream_url: str,
        out_dir: str,
        temp_filename: str,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        stream_label: str = "Video"
    ) -> bool:
        """Tải 1 stream (Video hoặc Audio) bằng aria2c 8 luồng song song, hỗ trợ Resume (-c) và Watchdog chống nghẽn 0B/s"""
        aria2_cmd = get_aria2c_cmd()
        target = os.path.join(out_dir, temp_filename)
        cookie_path = settings.get_bilibili_cookie_path()
        
        if aria2_cmd:
            cmd = [
                *aria2_cmd,
                "-c",                       # Cho phép tải tiếp (Resume), không tải lại từ đầu
                "-s", "8",                  # 8 luồng song song tối ưu băng thông
                "-x", "8",                  # 8 kết nối tối đa
                "-k", "1M",                 # Chia đoạn 1MB để burst tốc độ
                "--min-split-size=1M",
                "--timeout=30",
                "--connect-timeout=15",
                "--lowest-speed-limit=20K", # Tự động ngắt socket nghẽn nếu tốc độ < 20KB/s
                "--max-tries=15",
                "--retry-wait=2",
                "--file-allocation=none",
                "--header=Referer: https://www.bilibili.com/",
                "--header=Origin: https://www.bilibili.com",
                "--header=User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "-d", str(out_dir),
                "-o", str(temp_filename),
                stream_url,
                "--allow-overwrite=true",
                "--summary-interval=1"
            ]
            if cookie_path and os.path.exists(cookie_path):
                cmd.append(f"--load-cookies={cookie_path}")

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1
                )
                
                # Regex aria2c: [#392942 12MiB/85MiB(14%) CN:16 DL:8.5MiB ETA:8s]
                aria_pattern = re.compile(r'\[#\w+\s+([\d\.]+\w+)/([\d\.]+\w+)\((\d+)%\)\s+CN:\d+\s+DL:([^\s\]]+)(?:\s+ETA:([\w\d]+))?\]')
                last_cb_time = 0.0
                zero_speed_streak = 0
                
                for line in proc.stdout:
                    line_str = line.strip()
                    m = aria_pattern.search(line_str)
                    if m:
                        downloaded = m.group(1)
                        total = m.group(2)
                        pct = float(m.group(3))
                        speed = m.group(4)
                        eta = m.group(5) or "--"

                        # Watchdog phát hiện nghẽn mạng: Nếu tốc độ về 0B/s quá 12s liên tục
                        if speed in ["0B", "0B/s", "0KiB", "0.0KiB", "0.0B"] and pct < 99.0:
                            zero_speed_streak += 1
                        else:
                            zero_speed_streak = 0

                        if zero_speed_streak >= 12:
                            sys.stdout.write(f"\n[DownloaderService] ⚡ CDN bị nghẽn (0B/s qua 12s) ở mức {pct:.0f}%. Chủ động ngắt để chuyển server...\n")
                            sys.stdout.flush()
                            try:
                                proc.kill()
                            except Exception:
                                pass
                            return False
                        
                        # In real-time lên cùng 1 dòng trên Terminal
                        sys.stdout.write(f"\r⚡ [Aria2c Turbo] Đang tải {stream_label}: {downloaded}/{total} ({pct:.0f}%) | {speed} | Còn {eta}   ")
                        sys.stdout.flush()
                        
                        now = time.time()
                        if progress_callback and (now - last_cb_time >= 0.8 or pct >= 100):
                            last_cb_time = now
                            progress_callback(pct, f"📥 [{stream_label}] {downloaded}/{total} ({pct:.0f}%) | Tốc độ: {speed} (Còn {eta})")
                
                proc.wait()
                sys.stdout.write("\n")
                sys.stdout.flush()
                
                if proc.returncode == 0 and os.path.exists(target) and os.path.getsize(target) > 0:
                    return True
            except Exception as e:
                print(f"\n[DownloaderService] Lỗi aria2c stream: {e}")
                
        # Fallback: Tải trực tiếp bằng requests stream có hỗ trợ Resume Range
        return DownloaderService.download_stream_requests(stream_url, out_dir, temp_filename, progress_callback=progress_callback, stream_label=stream_label)

    @staticmethod
    def download_stream_requests(
        stream_url: str,
        out_dir: str,
        temp_filename: str,
        progress_callback: Optional[Callable[[float, str], None]] = None,
        stream_label: str = "Video"
    ) -> bool:
        """Tải trực tiếp bằng requests stream có hỗ trợ Resume (Range bytes)"""
        target = os.path.join(out_dir, temp_filename)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": "https://www.bilibili.com/"
        }
        try:
            existing_size = os.path.getsize(target) if os.path.exists(target) else 0
            if existing_size > 0:
                headers["Range"] = f"bytes={existing_size}-"

            r = requests.get(stream_url, headers=headers, stream=True, timeout=20)
            if r.status_code == 416:  # Đã tải đầy đủ
                return True
            r.raise_for_status()

            if r.status_code == 206:
                content_len = int(r.headers.get("content-length", 0))
                total_size = existing_size + content_len
                downloaded_size = existing_size
                mode = "ab"
            else:
                total_size = int(r.headers.get("content-length", 0))
                downloaded_size = 0
                mode = "wb"

            t0 = time.time()
            last_cb_time = 0.0

            with open(target, mode) as f:
                for chunk in r.iter_content(chunk_size=512 * 1024):
                    if chunk:
                        f.write(chunk)
                        downloaded_size += len(chunk)
                        now = time.time()
                        if now - last_cb_time >= 0.8 or (total_size > 0 and downloaded_size >= total_size):
                            last_cb_time = now
                            pct = (downloaded_size / total_size * 100.0) if total_size > 0 else 0.0
                            speed_mb = (downloaded_size - (existing_size if mode == "ab" else 0)) / (1024 * 1024) / max(0.1, now - t0)
                            sys.stdout.write(f"\r📥 [Direct Stream] Đang tải {stream_label}: {downloaded_size/(1024*1024):.1f}/{total_size/(1024*1024):.1f}MB ({pct:.1f}%) | {speed_mb:.1f}MB/s   ")
                            sys.stdout.flush()
                            if progress_callback:
                                progress_callback(pct, f"📥 [{stream_label}] {pct:.1f}% ({speed_mb:.1f}MB/s)")

            sys.stdout.write("\n")
            sys.stdout.flush()
            return os.path.exists(target) and os.path.getsize(target) > 0
        except Exception as e:
            print(f"\n[DownloaderService] Lỗi requests stream ({stream_label}): {e}")
            return False

    @staticmethod
    def download_bilibili_turbo(
        target_url: str,
        final_output_path: str,
        quality: str = "480p",
        progress_callback: Optional[Callable[[float, str], None]] = None
    ) -> bool:
        """Thuật toán tải Bilibili Native: Đổi CDN Hải ngoại + Aria2c 8 luồng + Tự Failover & Resume + Ghép MP4 FastStart"""
        out_dir = os.path.dirname(final_output_path)
        base_name = os.path.splitext(os.path.basename(final_output_path))[0]
        temp_v = f"_{base_name}_v.m4s"
        temp_a = f"_{base_name}_a.m4s"
        temp_v_path = os.path.join(out_dir, temp_v)
        temp_a_path = os.path.join(out_dir, temp_a)

        q_key = quality.lower().strip()
        preset = QUALITY_PRESETS.get(q_key, QUALITY_PRESETS["480p"])
        max_h = preset.get("max_height", 720)

        if progress_callback:
            progress_callback(2.0, "🔍 Đang phân tích thông tin video & luồng CDN Bilibili...")

        # 1. Trích xuất metadata định dạng video bằng yt-dlp
        ytdlp_cmd = get_ytdlp_cmd()
        cookie_path = settings.get_bilibili_cookie_path()
        extract_cmd = [
            *ytdlp_cmd,
            "-j",
            "--no-playlist",
            "--no-check-certificates",
            "--referer", "https://www.bilibili.com/",
            "--extractor-args", "bilibili:player_type=html5",
        ]
        if cookie_path and os.path.exists(cookie_path):
            extract_cmd.extend(["--cookies", cookie_path])
        extract_cmd.append(target_url)
        try:
            res = subprocess.run(extract_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if res.returncode != 0 or not res.stdout.strip():
                return False
        except Exception:
            return False

        try:
            lines = [l.strip() for l in res.stdout.strip().split("\n") if l.strip().startswith("{")]
            data = json.loads(lines[-1]) if lines else json.loads(res.stdout.strip())
        except Exception:
            return False

        formats = data.get("formats", [])
        if not formats:
            return False

        v_candidates = [f for f in formats if f.get("vcodec") != "none" and f.get("acodec") == "none"]
        a_candidates = [f for f in formats if f.get("acodec") != "none" and f.get("vcodec") == "none"]

        v_matched = [f for f in v_candidates if (f.get("height") or 0) <= max_h]
        chosen_v = v_matched[-1] if v_matched else (v_candidates[-1] if v_candidates else formats[-1])
        chosen_a = sorted(a_candidates, key=lambda x: x.get("abr") or 0)[-1] if a_candidates else None

        if not chosen_v or not chosen_v.get("url"):
            return False

        # 2. Dò tìm các server CDN Hải ngoại tốt nhất chống nghẽn
        if progress_callback:
            progress_callback(5.0, "⚡ Đang kết nối máy chủ CDN Hải ngoại tốc độ cao (10-15MB/s)...")
        v_stream_candidates = DownloaderService.get_fast_mirror_candidates(chosen_v.get("url"))
        a_stream_candidates = DownloaderService.get_fast_mirror_candidates(chosen_a.get("url")) if chosen_a else []

        best_v_host = v_stream_candidates[0].split("/")[2] if "://" in v_stream_candidates[0] else "mặc định"
        print(f"[DownloaderService] ⚡ CDN khởi tạo: {best_v_host} (Sẵn sàng {len(v_stream_candidates)} server dự phòng)")

        has_aria2 = get_aria2c_cmd() is not None

        # Hàm tải stream có tự động luân chuyển server CDN và Resume nối tiếp dở dang
        def _download_stream_with_failover(candidates, temp_name, label, cb_wrapper):
            max_attempts = 15
            cand_idx = 0
            for attempt in range(1, max_attempts + 1):
                s_url = candidates[cand_idx % len(candidates)]
                host = s_url.split("/")[2] if "://" in s_url else "mặc định"
                if attempt > 1:
                    print(f"\n[DownloaderService] ⚡ Tải tiếp (Resume) [{label}] qua server: {host} (lần {attempt}/{max_attempts})...")

                if has_aria2:
                    ok = DownloaderService.download_stream_aria2(s_url, out_dir, temp_name, progress_callback=cb_wrapper, stream_label=label)
                else:
                    ok = DownloaderService.download_stream_requests(s_url, out_dir, temp_name, progress_callback=cb_wrapper, stream_label=label)

                if ok:
                    return True
                cand_idx += 1
                time.sleep(1.5)
            return False

        # Tải luồng Video (chiếm 80% tiến độ)
        def video_cb(pct, msg):
            if progress_callback:
                total_pct = 5.0 + (pct / 100.0) * 80.0
                progress_callback(total_pct, msg)

        ok_v = _download_stream_with_failover(v_stream_candidates, temp_v, "Hình ảnh Video", video_cb)
        if not ok_v:
            for p in [temp_v_path, temp_a_path, temp_v_path + ".aria2", temp_a_path + ".aria2"]:
                if os.path.exists(p):
                    try: os.remove(p)
                    except Exception: pass
            return False

        # Tải luồng Audio nếu có (chiếm 10% tiến độ)
        if chosen_a and a_stream_candidates:
            def audio_cb(pct, msg):
                if progress_callback:
                    total_pct = 85.0 + (pct / 100.0) * 10.0
                    progress_callback(total_pct, msg)

            ok_a = _download_stream_with_failover(a_stream_candidates, temp_a, "Âm thanh Audio", audio_cb)
            if not ok_a:
                for p in [temp_v_path, temp_a_path, temp_v_path + ".aria2", temp_a_path + ".aria2"]:
                    if os.path.exists(p):
                        try: os.remove(p)
                        except Exception: pass
                return False

        if progress_callback:
            progress_callback(96.0, "⚡ Ghép Video + Audio thành MP4 siêu tốc (FFmpeg FastStart)...")

        # 4. Ghép Video + Audio thành MP4 siêu tốc bằng FFmpeg Copy +faststart
        ffmpeg_cmd = get_ffmpeg_cmd()
        if os.path.exists(temp_v_path) and os.path.exists(temp_a_path):
            merge_cmd = [
                *ffmpeg_cmd, "-y",
                "-i", temp_v_path,
                "-i", temp_a_path,
                "-c:v", "copy",
                "-c:a", "copy",
                "-movflags", "+faststart",
                final_output_path
            ]
        elif os.path.exists(temp_v_path):
            merge_cmd = [
                *ffmpeg_cmd, "-y",
                "-i", temp_v_path,
                "-c:v", "copy",
                "-movflags", "+faststart",
                final_output_path
            ]
        else:
            return False

        try:
            merge_res = subprocess.run(merge_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            success = merge_res.returncode == 0 and os.path.exists(final_output_path) and os.path.getsize(final_output_path) > 0
        except Exception:
            success = False

        # Dọn dẹp file tạm
        for p in [temp_v_path, temp_a_path, temp_v_path + ".aria2", temp_a_path + ".aria2"]:
            try:
                if os.path.exists(p): os.remove(p)
            except Exception:
                pass

        if success and progress_callback:
            progress_callback(100.0, "✔ Hoàn tất tải video Bilibili Turbo 100%!")

        return success

    @staticmethod
    def get_video_info(url: str) -> Dict[str, Any]:
        """Lấy thông tin metadata của video bằng yt-dlp"""
        target_url = DownloaderService.normalize_url(url)
        if not target_url:
            raise ValueError("URL video không được để trống!")

        ytdlp_cmd = get_ytdlp_cmd()
        cookie_path = settings.get_bilibili_cookie_path()
        cmd = [
            *ytdlp_cmd,
            target_url,
            "--dump-json",
            "--no-playlist",
            "--no-check-certificates",
            "--geo-bypass"
        ]
        
        if cookie_path and os.path.exists(cookie_path):
            cmd.extend(["--cookies", cookie_path])

        if any(domain in target_url for domain in ["bilibili.com", "b23.tv", "/BV", "/av"]):
            cmd.extend([
                "--referer", "https://www.bilibili.com/",
                "--extractor-args", "bilibili:player_type=html5"
            ])

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        except FileNotFoundError:
            raise RuntimeError("Không tìm thấy công cụ tải yt-dlp trên hệ thống. Hãy kiểm tra venv hoặc chạy 1_CAI_DAT_HE_THONG.bat.")
        except Exception as e:
            raise RuntimeError(f"Lỗi khi thực thi yt-dlp: {e}")

        if res.returncode != 0:
            raise RuntimeError(f"Không thể lấy thông tin video: {res.stderr}")

        try:
            lines = [l.strip() for l in res.stdout.strip().split("\n") if l.strip().startswith("{")]
            data = json.loads(lines[-1]) if lines else json.loads(res.stdout.strip())
        except Exception:
            raise RuntimeError(f"Lỗi đọc thông tin video: {res.stdout[:200]}")

        return {
            "id": data.get("id", "unknown"),
            "title": data.get("title", "video"),
            "duration": float(data.get("duration", 0.0) or 0.0),
            "uploader": data.get("uploader", ""),
            "thumbnail": data.get("thumbnail", ""),
            "url": target_url
        }

    @staticmethod
    def download_video(
        url: str,
        quality: str = "1080p",
        custom_filename: str = None,
        progress_callback: Optional[Callable[[float, str], None]] = None
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Tải video đa nền tảng siêu tốc (Bilibili Native Turbo Mirror + Aria2c 16 connections + yt-dlp fallback)
        Không dùng proxy. Hỗ trợ file cookies.txt và báo cáo tiến độ/tốc độ real-time.
        """
        target_url = DownloaderService.normalize_url(url)
        info = DownloaderService.get_video_info(target_url)
        video_id = info["id"]

        safe_title = re.sub(r'[\\/*?:"<>|]', "", info["title"]).strip()[:60]
        if not safe_title:
            safe_title = video_id

        filename = custom_filename if custom_filename else f"{video_id}_{safe_title}"
        target_path = str(settings.INPUT_VIDEOS_DIR / f"{filename}.mp4")
        out_template = str(settings.INPUT_VIDEOS_DIR / f"{filename}.%(ext)s")

        is_bilibili = any(domain in target_url for domain in ["bilibili.com", "b23.tv", "/BV", "/av", "BV", "av"])
        cookie_path = settings.get_bilibili_cookie_path()

        # 1. Thử chế độ Bilibili Native Turbo (CDN Mirror + Aria2c 16 luồng) nếu là link Bilibili
        if is_bilibili:
            try:
                ok = DownloaderService.download_bilibili_turbo(target_url, target_path, quality=quality, progress_callback=progress_callback)
                if ok and os.path.exists(target_path) and os.path.getsize(target_path) > 0:
                    return target_path, info
            except Exception as e:
                print(f"[DownloaderService] Native turbo fallback to yt-dlp: {e}")

        # 2. Chế độ yt-dlp tối ưu đa luồng Aria2c / 16 fragments (cho YouTube, Douyin, TikTok, hoặc fallback Bilibili)
        q_key = (quality or "1080p").lower().strip()
        preset = QUALITY_PRESETS.get(q_key, QUALITY_PRESETS.get("1080p", QUALITY_PRESETS["720p"]))

        aria2_cmd = get_aria2c_cmd()
        ytdlp_cmd = get_ytdlp_cmd()

        cmd = [
            *ytdlp_cmd,
            target_url,
            "--force-ipv4",
            "-f", preset["format"],
            "--format-sort", preset["format_sort"],
            "--merge-output-format", "mp4",
            "--no-mtime",
            "--no-playlist",
            "--no-check-certificates",
            "--geo-bypass",
            "--socket-timeout", "60",
            "--retries", "25",
            "--fragment-retries", "25",
            "--buffer-size", "1M",
            "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "-o", out_template
        ]

        if cookie_path and os.path.exists(cookie_path):
            cmd.extend(["--cookies", cookie_path])

        if is_bilibili:
            cmd.extend([
                "--concurrent-fragments", "4",
                "--file-access-retries", "10",
                "--retry-sleep", "fragment:exp=1:15",
                "--referer", "https://www.bilibili.com/",
                "--extractor-args", "bilibili:player_type=html5"
            ])
        else:
            if aria2_cmd:
                cmd.extend([
                    "--external-downloader", str(aria2_cmd[0]),
                    "--external-downloader-args", "aria2c:-s 8 -x 8 -k 1M -c --lowest-speed-limit=20K --max-tries=15 --retry-wait=2 --connect-timeout=15 --timeout=30 --file-allocation=none"
                ])
            else:
                cmd.extend([
                    "--concurrent-fragments", "8",
                    "--buffer-size", "1M",
                    "--http-chunk-size", "10M"
                ])

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1
            )
            
            ytdlp_pattern = re.compile(r'\[download\]\s+([\d\.]+)%\s+of\s+~?([\d\.]+\w+)(?:\s+at\s+([\d\.]+\w+/s))?(?:\s+ETA\s+([\d:]+))?')
            last_cb_time = 0.0
            
            for line in proc.stdout:
                line_str = line.strip()
                m = ytdlp_pattern.search(line_str)
                if m:
                    pct = float(m.group(1))
                    tot = m.group(2)
                    spd = m.group(3) or "--"
                    eta = m.group(4) or "--"
                    sys.stdout.write(f"\r🚀 [yt-dlp 16P] {pct:.1f}% of {tot} | Tốc độ: {spd} | Còn {eta}   ")
                    sys.stdout.flush()
                    now = time.time()
                    if progress_callback and (now - last_cb_time >= 0.8 or pct >= 100):
                        last_cb_time = now
                        progress_callback(pct, f"📥 [yt-dlp] {pct:.1f}% of {tot} | {spd} (Còn {eta})")
                elif "Destination:" in line_str or "[Merger]" in line_str:
                    if progress_callback:
                        progress_callback(95.0, "⚡ Đang hợp nhất định dạng MP4...")
                        
            proc.wait()
            sys.stdout.write("\n")
            sys.stdout.flush()
            res_code = proc.returncode
        except FileNotFoundError:
            raise RuntimeError("Không tìm thấy yt-dlp trên máy. Vui lòng cài đặt lại theo hướng dẫn.")
        except Exception as e:
            res_code = 1

        # Fallback tự động lần cuối nếu định dạng bị kén
        if res_code != 0:
            fallback_cmd = [
                *ytdlp_cmd,
                target_url,
                "--force-ipv4",
                "-f", "bestvideo+bestaudio/best",
                "--merge-output-format", "mp4",
                "--no-playlist",
                "--no-check-certificates",
                "--concurrent-fragments", "16",
                "--buffer-size", "1M",
                "-o", out_template
            ]
            if cookie_path and os.path.exists(cookie_path):
                fallback_cmd.extend(["--cookies", cookie_path])
            if is_bilibili:
                fallback_cmd.extend(["--referer", "https://www.bilibili.com/"])
            try:
                proc2 = subprocess.Popen(
                    fallback_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1
                )
                for line in proc2.stdout:
                    l = line.strip()
                    m = ytdlp_pattern.search(l)
                    if m:
                        pct = float(m.group(1))
                        spd = m.group(3) or "--"
                        sys.stdout.write(f"\r🚀 [yt-dlp Fallback] {pct:.1f}% | {spd}   ")
                        sys.stdout.flush()
                        now = time.time()
                        if progress_callback and (now - last_cb_time >= 0.8 or pct >= 100):
                            last_cb_time = now
                            progress_callback(pct, f"📥 [Fallback] {pct:.1f}% | {spd}")
                proc2.wait()
                sys.stdout.write("\n")
                sys.stdout.flush()
            except Exception:
                pass

        if not os.path.exists(target_path):
            candidates = list(settings.INPUT_VIDEOS_DIR.glob(f"{filename}.*"))
            if candidates:
                target_path = str(candidates[0])
            else:
                raise FileNotFoundError(f"Không tìm thấy file video sau khi tải: {target_path}")

        if progress_callback:
            progress_callback(100.0, "✔ Video đã tải thành công 100%!")

        return target_path, info
