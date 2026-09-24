import os
import re
import sys
import json
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, Tuple, Optional
import requests
from app.config import settings
from app.utils.bin_helper import get_ytdlp_cmd, get_ffmpeg_cmd, get_aria2c_cmd

FAST_BILIBILI_MIRRORS = [
    "upos-sz-mirroraliov.bilivideo.com",
    "upos-sz-mirrorcos.bilivideo.com",
    "upos-sz-mirrorali.bilivideo.com",
    "upos-sz-mirrorbos.bilivideo.com",
    "upos-tf-all-hw.bilivideo.com",
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
        """Tự động chuẩn hóa link đầu vào (BV code, YouTube, Douyin, B23, TikTok...)"""
        u = (raw_url or "").strip()
        if not u:
            return ""
        if re.match(r'^(BV[a-zA-Z0-9]{10}|av\d+)$', u, re.IGNORECASE):
            return f"https://www.bilibili.com/video/{u}"
        return u

    @staticmethod
    def get_fast_mirror_url(raw_url: str) -> str:
        """Đổi máy chủ CDN Bilibili sang các Mirror siêu tốc không bị bóp băng thông"""
        if not raw_url:
            return raw_url
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": "https://www.bilibili.com/"
        }
        for m in FAST_BILIBILI_MIRRORS:
            cand = re.sub(r"https?://[^/]+", f"https://{m}", raw_url)
            try:
                r = requests.head(cand, headers=headers, timeout=2.0)
                if r.status_code in (200, 206, 302):
                    return cand
            except Exception:
                continue
        return raw_url

    @staticmethod
    def download_stream_aria2(stream_url: str, out_dir: str, temp_filename: str) -> bool:
        """Tải 1 stream (Video hoặc Audio) bằng aria2c tăng tốc 8 kết nối song song (hoặc requests fallback)"""
        aria2_cmd = get_aria2c_cmd()
        target = os.path.join(out_dir, temp_filename)
        
        if aria2_cmd:
            cmd = [
                *aria2_cmd,
                "-s", "8",
                "-x", "8",
                "-k", "1M",
                "--file-allocation=none",
                "--header=Referer: https://www.bilibili.com/",
                "--header=User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "-d", str(out_dir),
                "-o", str(temp_filename),
                stream_url,
                "--allow-overwrite=true",
                "--summary-interval=1"
            ]
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
                if res.returncode == 0 and os.path.exists(target) and os.path.getsize(target) > 0:
                    return True
            except Exception:
                pass
                
        # Fallback: Tải trực tiếp bằng requests stream nếu không có aria2c
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Referer": "https://www.bilibili.com/"
            }
            with requests.get(stream_url, headers=headers, stream=True, timeout=60) as r:
                r.raise_for_status()
                with open(target, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=1024*1024):
                        if chunk:
                            f.write(chunk)
            return os.path.exists(target) and os.path.getsize(target) > 0
        except Exception:
            return False

    @staticmethod
    def download_bilibili_turbo(target_url: str, final_output_path: str, quality: str = "480p") -> bool:
        """Thuật toán tải Bilibili Native: Đổi CDN Mirror + Aria2c 8 luồng + Ghép MP4 0.1s"""
        out_dir = os.path.dirname(final_output_path)
        base_name = os.path.splitext(os.path.basename(final_output_path))[0]
        temp_v = f"_{base_name}_v.m4s"
        temp_a = f"_{base_name}_a.m4s"
        temp_v_path = os.path.join(out_dir, temp_v)
        temp_a_path = os.path.join(out_dir, temp_a)

        q_key = quality.lower().strip()
        preset = QUALITY_PRESETS.get(q_key, QUALITY_PRESETS["480p"])
        max_h = preset.get("max_height", 720)

        # 1. Trích xuất metadata định dạng video bằng yt-dlp
        ytdlp_cmd = get_ytdlp_cmd()
        extract_cmd = [
            *ytdlp_cmd,
            "-j",
            "--no-playlist",
            "--no-check-certificates",
            "--referer", "https://www.bilibili.com/",
            "--extractor-args", "bilibili:player_type=html5",
            target_url
        ]
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

        # 2. Đổi sang CDN Mirror tốc độ cao
        v_stream_url = DownloaderService.get_fast_mirror_url(chosen_v.get("url"))
        a_stream_url = DownloaderService.get_fast_mirror_url(chosen_a.get("url")) if chosen_a else None

        # 3. Tải song song Video và Audio
        ok_v = DownloaderService.download_stream_aria2(v_stream_url, out_dir, temp_v)
        if not ok_v:
            for p in [temp_v_path, temp_a_path]:
                if os.path.exists(p): os.remove(p)
            return False

        if a_stream_url:
            ok_a = DownloaderService.download_stream_aria2(a_stream_url, out_dir, temp_a)
            if not ok_a:
                for p in [temp_v_path, temp_a_path]:
                    if os.path.exists(p): os.remove(p)
                return False

        # 4. Ghép Video + Audio thành MP4 siêu tốc (0.1 giây bằng FFmpeg Copy)
        ffmpeg_cmd = get_ffmpeg_cmd()
        if os.path.exists(temp_v_path) and os.path.exists(temp_a_path):
            merge_cmd = [
                *ffmpeg_cmd, "-y",
                "-i", temp_v_path,
                "-i", temp_a_path,
                "-c", "copy",
                final_output_path
            ]
        elif os.path.exists(temp_v_path):
            merge_cmd = [
                *ffmpeg_cmd, "-y",
                "-i", temp_v_path,
                "-c", "copy",
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
        for p in [temp_v_path, temp_a_path]:
            try:
                if os.path.exists(p): os.remove(p)
            except Exception:
                pass

        return success

    @staticmethod
    def get_video_info(url: str) -> Dict[str, Any]:
        """Lấy thông tin metadata của video bằng yt-dlp"""
        target_url = DownloaderService.normalize_url(url)
        if not target_url:
            raise ValueError("URL video không được để trống!")

        ytdlp_cmd = get_ytdlp_cmd()
        cmd = [
            *ytdlp_cmd,
            target_url,
            "--dump-json",
            "--no-playlist",
            "--no-check-certificates",
            "--geo-bypass"
        ]
        
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
    def download_video(url: str, quality: str = "1080p", custom_filename: str = None) -> Tuple[str, Dict[str, Any]]:
        """
        Tải video đa nền tảng siêu tốc (Bilibili Native Turbo Mirror + Aria2c 8-16 connections + yt-dlp fallback)
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

        # 1. Thử chế độ Bilibili Native Turbo (CDN Mirror + Aria2c) nếu là link Bilibili
        if is_bilibili:
            try:
                ok = DownloaderService.download_bilibili_turbo(target_url, target_path, quality=quality)
                if ok and os.path.exists(target_path) and os.path.getsize(target_path) > 0:
                    return target_path, info
            except Exception as e:
                print(f"[DownloaderService] Native turbo fallback to yt-dlp: {e}")

        # 2. Chế độ yt-dlp tối ưu đa luồng Aria2c (cho YouTube, Douyin, TikTok, hoặc fallback Bilibili)
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
            "--socket-timeout", "30",
            "--retries", "10",
            "--fragment-retries", "10",
            "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "-o", out_template
        ]

        if aria2_cmd:
            cmd.extend([
                "--external-downloader", str(aria2_cmd[0]),
                "--external-downloader-args", "aria2c:-s 8 -x 8 -k 1M --file-allocation=none"
            ])
        else:
            cmd.extend([
                "--concurrent-fragments", "5",
                "--buffer-size", "16M",
                "--http-chunk-size", "10M"
            ])

        if is_bilibili:
            cmd.extend([
                "--referer", "https://www.bilibili.com/",
                "--extractor-args", "bilibili:player_type=html5"
            ])

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        except FileNotFoundError:
            raise RuntimeError("Không tìm thấy yt-dlp trên máy. Vui lòng cài đặt lại theo hướng dẫn.")

        # Fallback tự động lần cuối nếu định dạng bị kén
        if res.returncode != 0:
            fallback_cmd = [
                *ytdlp_cmd,
                target_url,
                "--force-ipv4",
                "-f", "bestvideo+bestaudio/best",
                "--merge-output-format", "mp4",
                "--no-playlist",
                "--no-check-certificates",
                "-o", out_template
            ]
            if is_bilibili:
                fallback_cmd.extend(["--referer", "https://www.bilibili.com/"])
            subprocess.run(fallback_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")

        if not os.path.exists(target_path):
            candidates = list(settings.INPUT_VIDEOS_DIR.glob(f"{filename}.*"))
            if candidates:
                target_path = str(candidates[0])
            else:
                raise FileNotFoundError(f"Không tìm thấy file video sau khi tải: {target_path}")

        return target_path, info
