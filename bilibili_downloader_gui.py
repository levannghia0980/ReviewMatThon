import os
import sys
import re
import glob
import json
import time
import shutil
import subprocess
import threading
import queue
import socket
import concurrent.futures
import requests
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

DEFAULT_DOWNLOAD_DIR = r"D:\download\cococ"

# File cài đặt lưu path cookie người dùng nhập
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

FAST_BILIBILI_MIRRORS = [
    "upos-sz-mirroraliov.bilivideo.com",
    "upos-sz-mirrorcos.bilivideo.com",
    "upos-sz-mirrorcoso1.bilivideo.com",
    "upos-sz-mirrorali.bilivideo.com",
    "upos-sz-mirrorbos.bilivideo.com",
    "upos-tf-all-hw.bilivideo.com",
    "upos-sz-upcdnbda2.bilivideo.com"
]

QUALITY_PRESETS = {
    "720p 60fps / 30fps (Khuyen dung - Net & Cuc Nhanh)": {
        "max_height": 720,
        "format": "bv*[height<=720]+ba/b[height<=720]/best",
        "format_sort": "res:720,fps:60,ext:mp4:m4a"
    },
    "1080p Full HD (Chuan dep nhat)": {
        "max_height": 1080,
        "format": "bv*[height<=1080]+ba/b[height<=1080]/best",
        "format_sort": "res:1080,fps:60,ext:mp4:m4a"
    },
    "480p (Tieu chuan nhe)": {
        "max_height": 480,
        "format": "bv*[height<=480]+ba/b[height<=480]/best",
        "format_sort": "res:480,ext:mp4:m4a"
    },
    "360p (Tiet kiem dung luong)": {
        "max_height": 360,
        "format": "bv*[height<=360]+ba/b[height<=360]/worst",
        "format_sort": "res:360,ext:mp4:m4a"
    },
    "Goc cao nhat (Best Original 2K/4K neu co)": {
        "max_height": 99999,
        "format": "bv*+ba/best",
        "format_sort": "res,fps,ext:mp4:m4a"
    }
}

def load_settings():
    default = {
        "cookie_file": os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt"),
        "download_dir": DEFAULT_DOWNLOAD_DIR
    }
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                default.update(saved)
        except Exception:
            pass
    return default

def save_settings(settings):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


class VideoDownloaderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("TOOL CAO VIDEO BILIBILI SIEU TOC - 720P 60FPS / 1080P")
        self.root.geometry("960x860")
        self.root.minsize(860, 720)

        self.is_downloading = False
        self.should_stop = False
        self.current_process = None
        self.log_queue = queue.Queue()

        self.settings = load_settings()
        os.makedirs(self.settings.get("download_dir", DEFAULT_DOWNLOAD_DIR), exist_ok=True)

        self._setup_style()
        self._build_ui()
        self._update_link_count()
        self.start_num_var.set("01")

        self.root.after(100, self._process_log_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _setup_style(self):
        style = ttk.Style()
        try:
            style.theme_use('clam')
        except Exception:
            pass

        self.bg_color = "#181825"
        self.card_bg = "#24273a"
        self.text_color = "#cad3f5"
        self.accent_color = "#a6e3a1"
        self.danger_color = "#f38ba8"
        self.btn_bg = "#363a4f"
        self.highlight = "#89dceb"

        self.root.configure(bg=self.bg_color)
        style.configure(".", background=self.bg_color, foreground=self.text_color, font=("Segoe UI", 10))
        style.configure("TLabel", background=self.bg_color, foreground=self.text_color)
        style.configure("Header.TLabel", font=("Segoe UI", 15, "bold"), foreground=self.highlight)
        style.configure("SubHeader.TLabel", font=("Segoe UI", 9), foreground="#c6a0f6")
        style.configure("Card.TFrame", background=self.card_bg)
        style.configure("Card.TLabel", background=self.card_bg, foreground=self.text_color)

    def _build_ui(self):
        # 1. Header
        header_frame = tk.Frame(self.root, bg=self.bg_color, pady=8)
        header_frame.pack(fill="x", padx=15)

        title_lbl = ttk.Label(header_frame, text="TOOL CAO VIDEO BILIBILI SIEU TOC - FIX TOC DO 100%", style="Header.TLabel")
        title_lbl.pack(anchor="w")

        sub_lbl = ttk.Label(
            header_frame,
            text="Da khac phuc nghen CDN | Tai truc tiep Native sieu nhanh (5-20MB/s) | aria2c 16 luong | Tu dong danh so STT",
            style="SubHeader.TLabel"
        )
        sub_lbl.pack(anchor="w", pady=(2, 0))

        # 2. Khung cau hinh chinh
        config_frame = tk.LabelFrame(
            self.root,
            text="  Cau Hinh Luu Tru, Chat Luong & STT  ",
            bg=self.card_bg,
            fg=self.highlight,
            font=("Segoe UI", 10, "bold"),
            padx=12,
            pady=8,
            relief="groove"
        )
        config_frame.pack(fill="x", padx=15, pady=(0, 6))

        # Dong 1: Thu muc luu
        dir_frame = tk.Frame(config_frame, bg=self.card_bg)
        dir_frame.pack(fill="x", pady=2)

        tk.Label(dir_frame, text="Thu muc luu:", bg=self.card_bg, fg=self.text_color, width=14, anchor="w").pack(side="left")
        self.dir_var = tk.StringVar(value=self.settings.get("download_dir", DEFAULT_DOWNLOAD_DIR))
        self.dir_entry = tk.Entry(dir_frame, textvariable=self.dir_var, font=("Segoe UI", 9), bg="#1e1e2e", fg="#a6e3a1", relief="flat", insertbackground="white")
        self.dir_entry.pack(side="left", fill="x", expand=True, padx=5, ipady=3)

        btn_browse = tk.Button(dir_frame, text="Chon thu muc", command=self._browse_dir, bg=self.btn_bg, fg=self.text_color, relief="flat", padx=8, cursor="hand2")
        btn_browse.pack(side="left", padx=2)

        btn_open = tk.Button(dir_frame, text="Mo thu muc", command=self._open_dir, bg="#6272a4", fg="#ffffff", relief="flat", padx=8, cursor="hand2")
        btn_open.pack(side="left", padx=2)

        # Dong 2: STT & Dinh dang ten
        opt_frame = tk.Frame(config_frame, bg=self.card_bg)
        opt_frame.pack(fill="x", pady=(6, 2))

        tk.Label(opt_frame, text="STT bat dau:", bg=self.card_bg, fg=self.text_color, width=14, anchor="w").pack(side="left")
        self.start_num_var = tk.StringVar(value="01")
        self.start_num_entry = tk.Entry(opt_frame, textvariable=self.start_num_var, width=8, font=("Segoe UI", 10, "bold"), bg="#1e1e2e", fg="#f9e2af", justify="center", relief="flat", insertbackground="white")
        self.start_num_entry.pack(side="left", padx=5, ipady=2)

        btn_auto_num = tk.Button(
            opt_frame,
            text="Quet STT tiep theo",
            command=lambda: self._refresh_next_sequence(silent=False),
            bg=self.btn_bg,
            fg="#89dceb",
            relief="flat",
            padx=8,
            cursor="hand2"
        )
        btn_auto_num.pack(side="left", padx=5)

        self.naming_mode_var = tk.StringVar(value="number_only")
        rb_num = tk.Radiobutton(
            opt_frame,
            text="Chi so: 01.mp4, 02.mp4 (Mac dinh)",
            variable=self.naming_mode_var,
            value="number_only",
            bg=self.card_bg,
            fg=self.text_color,
            selectcolor=self.bg_color,
            activebackground=self.card_bg,
            activeforeground=self.text_color
        )
        rb_num.pack(side="left", padx=10)

        rb_title = tk.Radiobutton(
            opt_frame,
            text="Kem ten: 01 - TenVideo.mp4",
            variable=self.naming_mode_var,
            value="number_title",
            bg=self.card_bg,
            fg=self.text_color,
            selectcolor=self.bg_color,
            activebackground=self.card_bg,
            activeforeground=self.text_color
        )
        rb_title.pack(side="left", padx=5)

        # Dong 3: Chat luong
        opt2_frame = tk.Frame(config_frame, bg=self.card_bg)
        opt2_frame.pack(fill="x", pady=(6, 2))

        tk.Label(opt2_frame, text="Chat luong video:", bg=self.card_bg, fg=self.text_color, width=14, anchor="w").pack(side="left")
        self.quality_var = tk.StringVar(value=list(QUALITY_PRESETS.keys())[0])
        quality_cb = ttk.Combobox(opt2_frame, textvariable=self.quality_var, values=list(QUALITY_PRESETS.keys()), width=55, state="readonly")
        quality_cb.pack(side="left", padx=5)

        # Dong 4: Tuy chon toi uu tai
        opt4_frame = tk.Frame(config_frame, bg=self.card_bg)
        opt4_frame.pack(fill="x", pady=(6, 2))

        self.skip_existing_var = tk.BooleanVar(value=True)
        chk_skip = tk.Checkbutton(
            opt4_frame,
            text="Tu dong nhay so tranh ghi de",
            variable=self.skip_existing_var,
            bg=self.card_bg,
            fg="#cad3f5",
            selectcolor=self.bg_color,
            activebackground=self.card_bg,
            activeforeground=self.text_color
        )
        chk_skip.pack(side="left", padx=(5, 15))

        self.single_part_var = tk.BooleanVar(value=True)
        chk_single = tk.Checkbutton(
            opt4_frame,
            text="Chi tai tap chi dinh (Bo qua playlist neu khong can)",
            variable=self.single_part_var,
            bg=self.card_bg,
            fg="#a6e3a1",
            selectcolor=self.bg_color,
            activebackground=self.card_bg,
            activeforeground="#a6e3a1"
        )
        chk_single.pack(side="left", padx=5)

        # 3. Khung Cai Dat Cookie (Settings)
        settings_frame = tk.LabelFrame(
            self.root,
            text="  Cookie Settings (Tuy chon - Danh cho tai khoan VIP/Premium Bilibili)  ",
            bg=self.card_bg,
            fg="#f9e2af",
            font=("Segoe UI", 10, "bold"),
            padx=12,
            pady=8,
            relief="groove"
        )
        settings_frame.pack(fill="x", padx=15, pady=(0, 6))

        # Dong 1: Path file cookie
        ck_file_frame = tk.Frame(settings_frame, bg=self.card_bg)
        ck_file_frame.pack(fill="x", pady=2)

        tk.Label(ck_file_frame, text="File Cookie:", bg=self.card_bg, fg="#f9e2af", width=12, anchor="w").pack(side="left")

        self.cookie_path_var = tk.StringVar(value=self.settings.get("cookie_file", ""))
        self.cookie_path_entry = tk.Entry(
            ck_file_frame,
            textvariable=self.cookie_path_var,
            font=("Segoe UI", 9),
            bg="#1e1e2e",
            fg="#f9e2af",
            relief="flat",
            insertbackground="white"
        )
        self.cookie_path_entry.pack(side="left", fill="x", expand=True, padx=5, ipady=3)

        btn_browse_ck = tk.Button(
            ck_file_frame,
            text="Chon file cookie",
            command=self._browse_cookie_file,
            bg=self.btn_bg,
            fg="#f9e2af",
            relief="flat",
            padx=8,
            cursor="hand2"
        )
        btn_browse_ck.pack(side="left", padx=2)

        btn_clear_ck = tk.Button(
            ck_file_frame,
            text="Xoa cookie",
            command=self._clear_cookie,
            bg="#45475a",
            fg="#f38ba8",
            relief="flat",
            padx=8,
            cursor="hand2"
        )
        btn_clear_ck.pack(side="left", padx=2)

        btn_save_ck = tk.Button(
            ck_file_frame,
            text="Luu cai dat",
            command=self._save_cookie_settings,
            bg="#28a745",
            fg="#ffffff",
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            padx=8,
            cursor="hand2"
        )
        btn_save_ck.pack(side="left", padx=2)

        # Dong 2: Trang thai cookie + chon nguon cookie tu Browser
        ck_status_frame = tk.Frame(settings_frame, bg=self.card_bg)
        ck_status_frame.pack(fill="x", pady=(4, 0))

        self.cookie_status_lbl = tk.Label(
            ck_status_frame,
            text="",
            bg=self.card_bg,
            fg="#a6e3a1",
            font=("Segoe UI", 9)
        )
        self.cookie_status_lbl.pack(side="left", padx=5)

        tk.Label(ck_status_frame, text="Hoac lay tu browser:", bg=self.card_bg, fg="#89dceb", font=("Segoe UI", 9)).pack(side="left", padx=(20, 5))

        self.browser_cookie_var = tk.StringVar(value="Khong dung Browser Cookie")
        browser_opts = [
            "Khong dung Browser Cookie",
            "Edge (lay cookie tu Edge dang dang nhap)",
            "Chrome (lay cookie tu Chrome dang dang nhap)",
            "Coc Coc",
            "Firefox"
        ]
        browser_ck_cb = ttk.Combobox(
            ck_status_frame,
            textvariable=self.browser_cookie_var,
            values=browser_opts,
            width=40,
            state="readonly"
        )
        browser_ck_cb.pack(side="left", padx=5)

        self._refresh_cookie_status()

        # 4. Khung dan Link
        links_box_frame = tk.LabelFrame(
            self.root,
            text="  Danh Sach Link Video Can Tai (Moi dong 1 link Bilibili / YouTube)  ",
            bg=self.card_bg,
            fg=self.highlight,
            font=("Segoe UI", 10, "bold"),
            padx=10,
            pady=6,
            relief="groove"
        )
        links_box_frame.pack(fill="both", expand=True, padx=15, pady=(0, 6))

        link_toolbar = tk.Frame(links_box_frame, bg=self.card_bg)
        link_toolbar.pack(fill="x", pady=(0, 4))

        btn_paste = tk.Button(
            link_toolbar,
            text="Dan tu Clipboard",
            command=self._paste_clipboard,
            bg="#c6a0f6",
            fg="#181825",
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            padx=10,
            cursor="hand2"
        )
        btn_paste.pack(side="left", padx=(0, 5))

        btn_clear = tk.Button(
            link_toolbar,
            text="Xoa danh sach",
            command=self._clear_links,
            bg=self.btn_bg,
            fg=self.text_color,
            relief="flat",
            padx=10,
            cursor="hand2"
        )
        btn_clear.pack(side="left", padx=5)

        self.link_count_lbl = tk.Label(link_toolbar, text="So link: 0", bg=self.card_bg, fg="#a6e3a1", font=("Segoe UI", 9, "bold"))
        self.link_count_lbl.pack(side="right", padx=5)

        txt_container = tk.Frame(links_box_frame, bg=self.card_bg)
        txt_container.pack(fill="both", expand=True)

        self.links_text = tk.Text(
            txt_container,
            wrap="none",
            height=6,
            bg="#11111b",
            fg="#ffffff",
            font=("Consolas", 10),
            relief="flat",
            insertbackground="white",
            selectbackground="#45475a",
            padx=8,
            pady=6
        )
        scrollbar_y = ttk.Scrollbar(txt_container, orient="vertical", command=self.links_text.yview)
        scrollbar_x = ttk.Scrollbar(txt_container, orient="horizontal", command=self.links_text.xview)
        self.links_text.configure(xscrollcommand=scrollbar_x.set, yscrollcommand=scrollbar_y.set)

        scrollbar_y.pack(side="right", fill="y")
        scrollbar_x.pack(side="bottom", fill="x")
        self.links_text.pack(side="left", fill="both", expand=True)
        self.links_text.bind("<KeyRelease>", lambda e: self._update_link_count())
        self._create_context_menu(self.links_text)

        # 5. Dieu Khien & Tien trinh
        action_frame = tk.Frame(self.root, bg=self.bg_color)
        action_frame.pack(fill="x", padx=15, pady=(0, 6))

        self.btn_start = tk.Button(
            action_frame,
            text="BAT DAU TAI SIEU TOC",
            command=self._start_download,
            bg="#28a745",
            fg="#ffffff",
            font=("Segoe UI", 12, "bold"),
            relief="flat",
            padx=20,
            pady=7,
            cursor="hand2"
        )
        self.btn_start.pack(side="left", fill="x", expand=True, padx=(0, 5))

        self.btn_stop = tk.Button(
            action_frame,
            text="DUNG LAI",
            command=self._stop_download,
            bg="#dc3545",
            fg="#ffffff",
            font=("Segoe UI", 12, "bold"),
            relief="flat",
            padx=20,
            pady=7,
            width=14,
            state="disabled",
            cursor="hand2"
        )
        self.btn_stop.pack(side="right", padx=(5, 0))

        prog_frame = tk.Frame(self.root, bg=self.bg_color)
        prog_frame.pack(fill="x", padx=15, pady=(0, 5))

        self.status_lbl = tk.Label(prog_frame, text="San sang tai. Dan link video vao khung va bam 'BAT DAU TAI SIEU TOC'.", bg=self.bg_color, fg="#89dceb", anchor="w", font=("Segoe UI", 9))
        self.status_lbl.pack(fill="x")

        self.progress_bar = ttk.Progressbar(prog_frame, mode="determinate")
        self.progress_bar.pack(fill="x", pady=(2, 0))

        # 6. Cua so Log
        log_frame = tk.LabelFrame(
            self.root,
            text="  Nhat Ky Qua Trinh Tai (Logs)  ",
            bg=self.card_bg,
            fg=self.highlight,
            font=("Segoe UI", 9, "bold"),
            padx=8,
            pady=4,
            relief="groove"
        )
        log_frame.pack(fill="both", expand=True, padx=15, pady=(0, 8))

        self.log_text = tk.Text(
            log_frame,
            height=7,
            bg="#11111b",
            fg="#a6adc8",
            font=("Consolas", 9),
            wrap="word",
            relief="flat",
            state="disabled"
        )
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        log_scroll.pack(side="right", fill="y")
        self.log_text.pack(fill="both", expand=True)

        self.log_text.tag_config("info", foreground="#89dceb")
        self.log_text.tag_config("speed", foreground="#50fa7b")
        self.log_text.tag_config("success", foreground="#a6e3a1")
        self.log_text.tag_config("warning", foreground="#f9e2af")
        self.log_text.tag_config("error", foreground="#f38ba8")

    # ── COOKIE SETTINGS ──
    def _browse_cookie_file(self):
        initial_dir = os.path.dirname(self.cookie_path_var.get()) if self.cookie_path_var.get() else os.path.dirname(os.path.abspath(__file__))
        path = filedialog.askopenfilename(
            initialdir=initial_dir,
            title="Chon file Cookie (Netscape format)",
            filetypes=[("Cookie files", "*.txt"), ("All files", "*.*")]
        )
        if path:
            self.cookie_path_var.set(os.path.normpath(path))
            self._refresh_cookie_status()

    def _clear_cookie(self):
        self.cookie_path_var.set("")
        self._refresh_cookie_status()

    def _save_cookie_settings(self):
        self.settings["cookie_file"] = self.cookie_path_var.get().strip()
        self.settings["download_dir"] = self.dir_var.get().strip()
        save_settings(self.settings)
        self._refresh_cookie_status()
        self._log("[Settings] Da luu cai dat cookie & thu muc tai thanh cong!", "success")
        messagebox.showinfo("Da luu", "Cai dat da duoc luu vao settings.json.\nMoi lan mo tool se tu dong dung cai dat nay.")

    def _refresh_cookie_status(self):
        ck_path = self.cookie_path_var.get().strip()
        if ck_path and os.path.exists(ck_path):
            size_kb = os.path.getsize(ck_path) // 1024
            self.cookie_status_lbl.config(
                text=f"[OK] File cookie hop le: {os.path.basename(ck_path)} ({size_kb}KB)",
                fg="#a6e3a1"
            )
        elif ck_path:
            self.cookie_status_lbl.config(
                text=f"[!] File khong ton tai: {ck_path} -- Se tai khong cookie",
                fg="#f9e2af"
            )
        else:
            self.cookie_status_lbl.config(
                text="[i] Khong dung file cookie -- Tai o che do khach (OK voi video cong khai)",
                fg="#89dceb"
            )

    # ── COOKIE ARGS ──
    def _get_cookie_args(self):
        """Tra ve list tham so cookie cho yt-dlp. Uu tien: 1) File, 2) Browser, 3) Khong."""
        ck_path = self.cookie_path_var.get().strip()
        if ck_path and os.path.exists(ck_path):
            return [("file", ck_path)]
        browser_sel = self.browser_cookie_var.get()
        if "Edge" in browser_sel:
            return [("browser", "edge")]
        elif "Chrome" in browser_sel:
            return [("browser", "chrome")]
        elif "Coc Coc" in browser_sel:
            return [("browser", "coccoc")]
        elif "Firefox" in browser_sel:
            return [("browser", "firefox")]
        return []

    def _build_ytdlp_cookie_args(self):
        args = self._get_cookie_args()
        result = []
        for kind, val in args:
            if kind == "file":
                result.extend(["--cookies", val])
            elif kind == "browser":
                result.extend(["--cookies-from-browser", val])
        return result

    def _get_aria2_cookie_path(self):
        ck_path = self.cookie_path_var.get().strip()
        if ck_path and os.path.exists(ck_path):
            return ck_path
        return None

    # ── LOG ──
    def _log(self, text, tag="info"):
        self.log_queue.put((text, tag))

    def _process_log_queue(self):
        while not self.log_queue.empty():
            msg, tag = self.log_queue.get_nowait()
            self.log_text.configure(state="normal")
            self.log_text.insert("end", msg + "\n", tag)
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        self.root.after(100, self._process_log_queue)

    # ── DIRECTORY ──
    def _browse_dir(self):
        path = filedialog.askdirectory(initialdir=self.dir_var.get())
        if path:
            self.dir_var.set(os.path.normpath(path))
            self._refresh_next_sequence(silent=True)

    def _open_dir(self):
        d = self.dir_var.get().strip()
        if not os.path.exists(d):
            os.makedirs(d, exist_ok=True)
        try:
            os.startfile(d)
        except Exception as e:
            messagebox.showerror("Loi", f"Khong the mo thu muc: {e}")

    # ── LINKS ──
    def _paste_clipboard(self):
        try:
            clipboard = self.root.clipboard_get()
            if clipboard:
                current_text = self.links_text.get("1.0", "end-1c").strip()
                if current_text:
                    self.links_text.insert("end", "\n" + clipboard)
                else:
                    self.links_text.insert("end", clipboard)
                self._update_link_count()
        except Exception:
            messagebox.showwarning("Thong bao", "Bo nho tam khong co du lieu van ban!")

    def _clear_links(self):
        self.links_text.delete("1.0", "end")
        self._update_link_count()

    def _get_links(self):
        raw = self.links_text.get("1.0", "end-1c")
        lines = [line.strip() for line in raw.splitlines()]
        valid_links = []
        for line in lines:
            if not line or line.startswith("#"):
                continue
            url_match = re.search(r"https?://[^\s]+", line)
            if url_match:
                url = url_match.group(0).rstrip("),;>\"'")
                valid_links.append(url)
            elif "bilibili.com" in line or "b23.tv" in line or line.startswith("BV") or line.startswith("av"):
                valid_links.append(line)
        return valid_links

    def _update_link_count(self):
        links = self._get_links()
        self.link_count_lbl.config(text=f"So link: {len(links)}")

    def _get_existing_numbers(self, download_dir):
        numbers = set()
        if not os.path.exists(download_dir):
            return numbers
        for filename in os.listdir(download_dir):
            m = re.match(r"^(\d+)", filename)
            if m:
                try:
                    numbers.add(int(m.group(1)))
                except ValueError:
                    pass
        return numbers

    def _refresh_next_sequence(self, silent=False):
        d = self.dir_var.get().strip()
        existing = self._get_existing_numbers(d)
        next_num = max(existing) + 1 if existing else 1
        formatted = f"{next_num:02d}"
        self.start_num_var.set(formatted)
        if not silent:
            self._log(f"[*] Da quet thu muc '{d}': So tiep theo la {formatted}", "info")

    def _format_sequence_number(self, num):
        return f"{num:02d}"

    def _create_context_menu(self, widget):
        menu = tk.Menu(widget, tearoff=0, bg="#24273a", fg="#cad3f5", activebackground="#363a4f", activeforeground="#a6e3a1")
        menu.add_command(label="Cat (Cut)", command=lambda: widget.event_generate("<<Cut>>"))
        menu.add_command(label="Sao chep (Copy)", command=lambda: widget.event_generate("<<Copy>>"))
        menu.add_command(label="Dan (Paste)", command=lambda: (widget.event_generate("<<Paste>>"), self.root.after(50, self._update_link_count)))
        menu.add_separator()
        menu.add_command(label="Chon tat ca (Select All)", command=lambda: widget.tag_add("sel", "1.0", "end"))

        def show_menu(event):
            menu.tk_popup(event.x_root, event.y_root)
        widget.bind("<Button-3>", show_menu)

    # ── DOWNLOAD CONTROL ──
    def _start_download(self):
        links = self._get_links()
        if not links:
            messagebox.showwarning("Chua co link", "Vui long dan it nhat 1 link video vao khung danh sach!")
            return

        out_dir = self.dir_var.get().strip()
        if not out_dir:
            messagebox.showerror("Loi", "Vui long nhap duong dan thu muc luu video!")
            return

        try:
            start_num = int(self.start_num_var.get().strip())
            if start_num <= 0:
                start_num = 1
        except ValueError:
            messagebox.showerror("Loi", "STT bat dau phai la so nguyen (vi du: 01, 1, 10)!")
            return

        os.makedirs(out_dir, exist_ok=True)

        selected_quality = self.quality_var.get()
        cookie_args = self._build_ytdlp_cookie_args()

        self.is_downloading = True
        self.should_stop = False
        self.btn_start.config(state="disabled", bg="#6c757d")
        self.btn_stop.config(state="normal", bg="#dc3545")
        self.progress_bar["value"] = 0
        self.status_lbl.config(text=f"Bat dau tai {len(links)} video...", fg="#89dceb")

        ck_path = self.cookie_path_var.get().strip()
        if ck_path and os.path.exists(ck_path):
            ck_info = f"File: {os.path.basename(ck_path)}"
        elif self.browser_cookie_var.get() != "Khong dung Browser Cookie":
            ck_info = f"Browser: {self.browser_cookie_var.get()}"
        else:
            ck_info = "Khong dung Cookie (tai video cong khai)"

        self._log("==================================================", "info")
        self._log(f"[+] BAT DAU TAI {len(links)} VIDEO (Che do Native Sieu Toc)", "info")
        self._log(f"[+] Chat luong: {selected_quality}", "info")
        self._log(f"[+] Thu muc luu: {out_dir}", "info")
        self._log(f"[+] STT bat dau: {self._format_sequence_number(start_num)}", "info")
        self._log(f"[+] Cookie: {ck_info}", "info")
        self._log("==================================================", "info")

        download_thread = threading.Thread(
            target=self._download_worker,
            args=(links, out_dir, start_num, selected_quality, cookie_args),
            daemon=True
        )
        download_thread.start()

    def _stop_download(self):
        if not self.is_downloading:
            return
        self.should_stop = True
        self.status_lbl.config(text="Dang dung tien trinh...", fg="#f38ba8")
        self._log("[!] Nguoi dung yeu cau dung qua trinh tai!", "warning")
        if self.current_process:
            try:
                self.current_process.terminate()
                self.current_process.kill()
            except Exception:
                pass

    # ── ARIA2 PATH ──
    def _get_aria2_path(self):
        local_aria = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aria2c.exe")
        if os.path.exists(local_aria):
            return local_aria
        return shutil.which("aria2c")

    # ── FAST MIRROR CDN ──
    def _get_fast_mirror_url(self, raw_url):
        if not raw_url:
            return raw_url
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Referer": "https://www.bilibili.com/"
        }
        try:
            r = requests.get(raw_url, headers=dict(headers, Range="bytes=0-0"), timeout=2.5)
            if r.status_code in (200, 206):
                return raw_url
        except Exception:
            pass
        for m in FAST_BILIBILI_MIRRORS:
            cand = re.sub(r"https?://[^/]+", f"https://{m}", raw_url)
            try:
                r = requests.get(cand, headers=dict(headers, Range="bytes=0-0"), timeout=3.0)
                if r.status_code in (200, 206):
                    return cand
            except Exception:
                try:
                    r = requests.head(cand, headers=headers, timeout=2.0)
                    if r.status_code in (200, 206, 302):
                        return cand
                except Exception:
                    continue
        return raw_url

    # ── DOWNLOAD STREAM - ARIA2C (16 luong song song) ──
    def _download_stream_aria2(self, stream_url, out_dir, temp_filename, label, expected_display, idx, total_links, weight=0.5, start_offset=0.0):
        aria_bin = self._get_aria2_path() or "aria2c"
        cmd = [
            aria_bin,
            "-s", "16",
            "-x", "16",
            "-k", "1M",
            "--min-split-size=1M",
            "--timeout=30",
            "--connect-timeout=10",
            "--max-tries=5",
            "--file-allocation=none",
            "--header=Referer: https://www.bilibili.com/",
            "--header=Origin: https://www.bilibili.com",
            "--header=User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "-d", out_dir,
            "-o", temp_filename,
            stream_url,
            "--allow-overwrite=true",
            "--summary-interval=1"
        ]

        aria2_ck = self._get_aria2_cookie_path()
        if aria2_ck:
            cmd.append(f"--load-cookies={aria2_ck}")

        startupinfo = None
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0

        self.current_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            startupinfo=startupinfo
        )

        last_pct_log = -1
        for raw_line in self.current_process.stdout:
            if self.should_stop:
                try:
                    self.current_process.kill()
                except Exception:
                    pass
                return False

            line = raw_line.strip()
            if not line:
                continue

            m_pct = re.search(r"\((\d+)%\)", line)
            m_speed = re.search(r"DL:([^\s\]]+)", line)
            m_eta = re.search(r"ETA:([^\s\]]+)", line)

            if m_pct:
                pct_val = float(m_pct.group(1))
                task_pct = start_offset + (pct_val * weight)
                overall_pct = ((idx - 1) + (task_pct / 100.0)) / total_links * 100
                self.root.after(0, lambda val=overall_pct: self.progress_bar.configure(value=val))

                speed_str = m_speed.group(1) if m_speed else "Tai nhanh"
                eta_str = m_eta.group(1) if m_eta else ""

                self.root.after(0, lambda p=pct_val, s=speed_str, e=eta_str, lbl=label: self.status_lbl.config(
                    text=f"Dang tai {expected_display} ({lbl}: {p:.0f}%) | Toc do: {s} | Con lai: {e}", fg="#50fa7b"
                ))

                if int(pct_val) != last_pct_log and (int(pct_val) % 20 == 0 or pct_val >= 99.0):
                    last_pct_log = int(pct_val)
                    self._log(f"  [speed] [{label}] Da tai {pct_val:.0f}% | Toc do: {speed_str} | ETA: {eta_str}", "speed")

        self.current_process.wait()
        return self.current_process.returncode == 0

    # ── DOWNLOAD STREAM - REQUESTS (fallback) ──
    def _download_stream_requests(self, stream_url, out_dir, temp_filename, label, expected_display, idx, total_links, weight=0.5, start_offset=0.0):
        target_path = os.path.join(out_dir, temp_filename)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Referer": "https://www.bilibili.com/"
        }
        try:
            r = requests.get(stream_url, headers=headers, stream=True, timeout=20)
            r.raise_for_status()
            total_size = int(r.headers.get("content-length", 0))
            downloaded = 0
            t0 = time.time()
            last_print = 0
            last_pct_log = -1

            with open(target_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=512 * 1024):
                    if self.should_stop:
                        return False
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        now = time.time()
                        if now - last_print >= 0.5 or downloaded == total_size:
                            last_print = now
                            pct_val = (downloaded / total_size * 100) if total_size > 0 else 0
                            task_pct = start_offset + (pct_val * weight)
                            overall_pct = ((idx - 1) + (task_pct / 100.0)) / total_links * 100
                            self.root.after(0, lambda val=overall_pct: self.progress_bar.configure(value=val))

                            speed = (downloaded / 1024 / 1024) / (now - t0 + 0.001)
                            speed_str = f"{speed:.2f} MB/s"
                            self.root.after(0, lambda p=pct_val, s=speed_str, lbl=label: self.status_lbl.config(
                                text=f"Dang tai {expected_display} ({lbl}: {p:.0f}%) | Toc do: {s}", fg="#50fa7b"
                            ))

                            if int(pct_val) != last_pct_log and (int(pct_val) % 20 == 0 or pct_val >= 99.0):
                                last_pct_log = int(pct_val)
                                self._log(f"  [speed] [{label}] Da tai {pct_val:.0f}% ({downloaded//1024//1024}MB) | Toc do: {speed_str}", "speed")
            return True
        except Exception as e:
            self._log(f"  [error] Loi tai stream ({label}): {e}", "error")
            return False

    # ── NATIVE BILIBILI DOWNLOAD ──
    def _download_bilibili_native(self, url, final_output, expected_display, idx, total_links, selected_quality, cookie_args):
        out_dir = os.path.dirname(final_output)
        base_name = os.path.splitext(os.path.basename(final_output))[0]
        temp_v = f"_{base_name}_v.m4s"
        temp_a = f"_{base_name}_a.m4s"
        temp_v_path = os.path.join(out_dir, temp_v)
        temp_a_path = os.path.join(out_dir, temp_a)

        preset = QUALITY_PRESETS.get(selected_quality, list(QUALITY_PRESETS.values())[0])
        max_h = preset.get("max_height", 720)

        # 1. Trich xuat metadata
        self._log(f"  [1/4] Dang lay thong tin dinh dang video Bilibili...", "info")
        extract_cmd = ["yt-dlp", "-j", "--no-playlist", url]
        if cookie_args:
            extract_cmd.extend(cookie_args)

        startupinfo = None
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0

        self.current_process = subprocess.Popen(
            extract_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            startupinfo=startupinfo
        )
        stdout, stderr = self.current_process.communicate()
        self.current_process = None

        if not stdout:
            self._log(f"  [!] Khong lay duoc du lieu JSON, chuyen sang yt-dlp du phong...", "warning")
            return None

        try:
            data = json.loads(stdout)
        except Exception:
            self._log(f"  [!] Loi doc JSON metadata Bilibili.", "warning")
            return None

        formats = data.get("formats", [])
        if not formats:
            return None

        v_candidates = [f for f in formats if f.get("vcodec") != "none" and f.get("acodec") == "none"]
        a_candidates = [f for f in formats if f.get("acodec") != "none" and f.get("vcodec") == "none"]

        v_matched = [f for f in v_candidates if (f.get("height") or 0) <= max_h]
        chosen_v = v_matched[-1] if v_matched else (v_candidates[-1] if v_candidates else formats[-1])
        chosen_a = sorted(a_candidates, key=lambda x: x.get("abr") or 0)[-1] if a_candidates else None

        v_res = chosen_v.get("resolution") or f"{chosen_v.get('height', 'Unknown')}p"
        self._log(f"  [2/4] Da chon do phan giai: {v_res} (Format ID: {chosen_v.get('format_id')})", "info")

        # 2. Doi may chu CDN chong nghen
        self._log(f"  [3/4] Dang ket noi may chu CDN toi uu toc do (Chong chan/503)...", "info")
        v_stream_url = self._get_fast_mirror_url(chosen_v.get("url"))
        a_stream_url = self._get_fast_mirror_url(chosen_a.get("url")) if chosen_a else None

        chosen_host = v_stream_url.split("/")[2] if "://" in v_stream_url else "mac dinh"
        orig_host = chosen_v.get("url", "").split("/")[2] if "://" in chosen_v.get("url", "") else ""
        if chosen_host != orig_host:
            self._log(f"  [speed] Da kich hoat may chu CDN sieu toc: {chosen_host}", "speed")
        else:
            self._log(f"  [speed] May chu CDN ket noi: {chosen_host}", "info")

        has_aria2 = self._get_aria2_path() is not None

        # 3. Tai luong Video
        weight_v = 0.85 if chosen_a else 1.0
        weight_a = 0.15 if chosen_a else 0.0

        if has_aria2:
            ok_v = self._download_stream_aria2(v_stream_url, out_dir, temp_v, "Video", expected_display, idx, total_links, weight=weight_v, start_offset=0.0)
        else:
            ok_v = self._download_stream_requests(v_stream_url, out_dir, temp_v, "Video", expected_display, idx, total_links, weight=weight_v, start_offset=0.0)

        if not ok_v or self.should_stop:
            for p in [temp_v_path, temp_a_path]:
                if os.path.exists(p):
                    try: os.remove(p)
                    except Exception: pass
            return False

        # Tai luong Audio neu co
        if chosen_a and a_stream_url:
            if has_aria2:
                ok_a = self._download_stream_aria2(a_stream_url, out_dir, temp_a, "Audio", expected_display, idx, total_links, weight=weight_a, start_offset=85.0)
            else:
                ok_a = self._download_stream_requests(a_stream_url, out_dir, temp_a, "Audio", expected_display, idx, total_links, weight=weight_a, start_offset=85.0)
            if not ok_a or self.should_stop:
                for p in [temp_v_path, temp_a_path]:
                    if os.path.exists(p):
                        try: os.remove(p)
                        except Exception: pass
                return False

        # 4. Ghep noi MP4 bang ffmpeg
        self._log(f"  [4/4] Dang dong goi hoan chinh video MP4...", "info")
        self.root.after(0, lambda: self.status_lbl.config(text=f"Dang hoan tat dong goi file {expected_display}...", fg="#a6e3a1"))

        if chosen_a and os.path.exists(temp_a_path):
            merge_cmd = ["ffmpeg", "-y", "-i", temp_v_path, "-i", temp_a_path, "-c:v", "copy", "-c:a", "copy", final_output]
        else:
            merge_cmd = ["ffmpeg", "-y", "-i", temp_v_path, "-c:v", "copy", final_output]

        merge_proc = subprocess.run(merge_cmd, capture_output=True, text=True)

        for p in [temp_v_path, temp_a_path]:
            if os.path.exists(p):
                try: os.remove(p)
                except Exception: pass
            if os.path.exists(p + ".aria2"):
                try: os.remove(p + ".aria2")
                except Exception: pass

        if merge_proc.returncode == 0 and os.path.exists(final_output):
            return True
        else:
            self._log(f"  [error] Loi khi ghep ffmpeg: {merge_proc.stderr}", "error")
            return False

    # ── YT-DLP SUBPROCESS (fallback - 16 concurrent fragments) ──
    def _run_subprocess_download(self, cmd, expected_display, idx, total_links):
        startupinfo = None
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0

        self.current_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            startupinfo=startupinfo
        )

        for raw_line in self.current_process.stdout:
            line = raw_line.strip()
            if not line:
                continue

            if "[download]" in line:
                m_pct = re.search(r"(\d+(\.\d+)?)%", line)
                m_speed = re.search(r"at\s+([^\s]+)", line)
                m_eta = re.search(r"ETA\s+([^\s]+)", line)

                if m_pct:
                    pct_val = float(m_pct.group(1))
                    overall_pct = ((idx - 1) + (pct_val / 100.0)) / total_links * 100
                    self.root.after(0, lambda val=overall_pct: self.progress_bar.configure(value=val))

                speed_str = m_speed.group(1) if m_speed else ""
                eta_str = m_eta.group(1) if m_eta else ""

                if speed_str and eta_str:
                    pct_display = f"{float(m_pct.group(1)):.1f}%" if m_pct else ""
                    self.root.after(0, lambda p=pct_display, s=speed_str, e=eta_str: self.status_lbl.config(
                        text=f"Dang tai {expected_display} [{p}] | Toc do: {s} | Con lai: {e}", fg="#50fa7b"
                    ))

                clean_line = re.sub(r"\s+", " ", line)
                if m_pct and (int(float(m_pct.group(1))) % 20 == 0 or float(m_pct.group(1)) >= 99.0):
                    self._log(f"  [speed] {clean_line}", "speed")
            elif "[Merger]" in line or "Merging" in line:
                self._log(f"  [speed] Dang hoan tat ghep video & am thanh (MP4)...", "info")
            elif "ERROR:" in line:
                self._log(f"  [error] {line}", "error")
            elif any(k in line for k in ["[BiliBili]", "[youtube]", "Destination", "[info]"]):
                self._log(f"  {line}", "info")

        self.current_process.wait()
        return self.current_process.returncode

    # ── DOWNLOAD WORKER ──
    def _download_worker(self, links, out_dir, start_num, selected_quality, cookie_args):
        total_links = len(links)
        current_num = start_num
        naming_mode = self.naming_mode_var.get()
        skip_existing = self.skip_existing_var.get()
        single_part = self.single_part_var.get()

        preset = QUALITY_PRESETS.get(selected_quality, list(QUALITY_PRESETS.values())[0])
        success_count = 0
        fail_count = 0

        for idx, url in enumerate(links, start=1):
            if self.should_stop:
                break

            if skip_existing:
                existing_nums = self._get_existing_numbers(out_dir)
                while current_num in existing_nums:
                    self._log(f"[*] So {self._format_sequence_number(current_num)} da co, tang len {self._format_sequence_number(current_num + 1)}", "warning")
                    current_num += 1

            num_str = self._format_sequence_number(current_num)

            if naming_mode == "number_only":
                final_output_path = os.path.join(out_dir, f"{num_str}.mp4")
                out_template = os.path.join(out_dir, f"{num_str}.%(ext)s")
                expected_display = f"{num_str}.mp4"
            else:
                final_output_path = os.path.join(out_dir, f"{num_str} - [Tieu de].mp4")
                out_template = os.path.join(out_dir, f"{num_str} - %(title).100s.%(ext)s")
                expected_display = f"{num_str} - [Tieu de].mp4"

            self.root.after(0, lambda i=idx, t=total_links, fn=expected_display:
                self.status_lbl.config(text=f"Dang chuan bi tai {i}/{t} -> File: {fn}", fg="#a6e3a1")
            )

            self._log(f"\n---> [{idx}/{total_links}] Dang ket noi tai: {url}", "info")
            self._log(f"     Ten file luu du kien: {expected_display}", "info")

            is_bilibili = any(k in url for k in ["bilibili.com", "b23.tv", "BV", "av"])
            dl_success = False

            # Thu tai Native Bilibili truoc (nhanh nhat: aria2c 16 luong)
            if is_bilibili and naming_mode == "number_only":
                try:
                    res = self._download_bilibili_native(url, final_output_path, expected_display, idx, total_links, selected_quality, cookie_args)
                    if res is True:
                        dl_success = True
                except Exception as e:
                    self._log(f"  [!] Che do Native gap loi ({e}), chuyen sang yt-dlp du phong...", "warning")

            # Fallback: yt-dlp voi concurrent-fragments 16 (KHONG PROXY)
            if not dl_success and not self.should_stop:
                cmd = [
                    "yt-dlp",
                    url,
                    "--force-ipv4",
                    "-f", preset["format"],
                    "--format-sort", preset["format_sort"],
                    "--merge-output-format", "mp4",
                    "--no-mtime",
                    "--socket-timeout", "30",
                    "--retries", "10",
                    "--fragment-retries", "10",
                    "--concurrent-fragments", "16",
                    "--buffer-size", "1M",
                    "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                    "-o", out_template,
                    "--newline",
                    "--progress"
                ]

                if single_part:
                    cmd.append("--no-playlist")

                if is_bilibili:
                    cmd.extend(["--referer", "https://www.bilibili.com/"])

                if cookie_args:
                    cmd.extend(cookie_args)

                try:
                    exit_code = self._run_subprocess_download(cmd, expected_display, idx, total_links)
                    if exit_code == 0:
                        dl_success = True
                except Exception as e:
                    self._log(f"  [error] Ngoai le khi tai: {e}", "error")

            if dl_success:
                self._log(f"  [success] TAI THANH CONG -> {expected_display}", "success")
                success_count += 1
                current_num += 1
            else:
                if self.should_stop:
                    self._log(f"  [warning] Da huy tai video nay.", "warning")
                    break
                else:
                    self._log(f"  [error] Tai that bai.", "error")
                    fail_count += 1
                    current_num += 1

            self.root.after(0, lambda val=(idx / total_links * 100): self.progress_bar.configure(value=val))

        self._on_download_finished(success_count, fail_count, total_links)

    # ── FINISH / CLOSE ──
    def _on_download_finished(self, success, fail, total):
        self.is_downloading = False
        self.current_process = None

        def ui_finish():
            self.btn_start.config(state="normal", bg="#28a745")
            self.btn_stop.config(state="disabled", bg="#6c757d")
            self._refresh_next_sequence(silent=True)

            if self.should_stop:
                self.status_lbl.config(text="Dang dung qua trinh tai theo yeu cau.", fg="#f9e2af")
                self._log(f"\n[!] QUA TRINH TAI DA DUNG! Thanh cong: {success}, Loi/Bo qua: {fail}", "warning")
            else:
                self.status_lbl.config(text=f"Hoan thanh! Thanh cong {success}/{total} video.", fg="#a6e3a1")
                self._log(f"\n[success] TAT CA DA HOAN THANH! Thanh cong: {success}/{total} video (Luu tai: {self.dir_var.get()})", "success")
                messagebox.showinfo(
                    "Hoan Thanh",
                    f"Qua trinh tai hoan tat!\n\n- Thanh cong: {success} video\n- That bai/Loi: {fail} video\n- Thu muc luu: {self.dir_var.get()}"
                )

        self.root.after(0, ui_finish)

    def _on_close(self):
        self.settings["download_dir"] = self.dir_var.get().strip()
        self.settings["cookie_file"] = self.cookie_path_var.get().strip()
        save_settings(self.settings)

        self.should_stop = True
        if self.current_process:
            try:
                self.current_process.terminate()
                self.current_process.kill()
            except Exception:
                pass
        try:
            self.root.destroy()
        except Exception:
            pass
        os._exit(0)


if __name__ == "__main__":
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding='utf-8')
            sys.stderr.reconfigure(encoding='utf-8')
        except Exception:
            pass

    root = tk.Tk()
    app = VideoDownloaderApp(root)
    root.mainloop()
