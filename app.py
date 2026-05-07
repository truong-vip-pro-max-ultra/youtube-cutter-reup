import os
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
import sys
import math
import random
import shutil
import subprocess
import threading
import platform
import queue
import json
import tkinter as tk
import time
import re

from tkinter import ttk, filedialog, messagebox
from concurrent.futures import ProcessPoolExecutor, as_completed
from PIL import Image

# Whisper - lazy import to avoid slow startup if not used
WHISPER_AVAILABLE = False
try:
    import whisper
    WHISPER_AVAILABLE = True
except ImportError:
    pass

SENTENCE_TRANSFORMERS_AVAILABLE = False
try:
    from sentence_transformers import SentenceTransformer
    import numpy as np
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    pass

# =========================================================
# CONFIG
# =========================================================

APP_BG        = "#0D1117"
CARD_BG       = "#161B22"
CARD_HEADER   = "#1C2128"
DIVIDER       = "#21262D"
ACCENT        = "#58A6FF"
ACCENT_HOVER  = "#79B8FF"
ACCENT_PRESSED= "#388BFD"
DANGER        = "#F85149"
DANGER_HOVER  = "#FF7B72"
SUCCESS       = "#3FB950"
WARNING       = "#D29922"
TEXT_PRIMARY  = "#E6EDF3"
TEXT_SECONDARY= "#8B949E"
TEXT_MUTED    = "#484F58"
BORDER_COLOR  = "#30363D"
INPUT_BG      = "#0D1117"
BTN_SECONDARY = "#21262D"
BTN_SEC_HOVER = "#30363D"

CACHE_IMAGE_FOLDER = "cache_images"
CACHE_AUDIO_FOLDER = "cache_audio"
CACHE_VIDEO_FOLDER = "cache_videos"
CACHE_SUBTITLE_FOLDER = "cache_subtitles"
TEMP_FOLDER = "temp"
OUTPUT_VIDEO = "final_video.mp4"

VIDEO_PRESETS = {
    "TikTok (Dọc)": {"width": 1080, "height": 1920, "fps": 30},
    "YouTube (Ngang)": {"width": 1920, "height": 1080, "fps": 30},
    "Instagram Reel": {"width": 1080, "height": 1920, "fps": 30},
    "YouTube Shorts": {"width": 1080, "height": 1920, "fps": 30},
}

WHISPER_MODELS = ["tiny", "base", "small", "medium", "large"]

# =========================================================
# PLATFORM DETECTION
# =========================================================

IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"

SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0

if IS_MAC:
    UI_FONT  = "Helvetica Neue"
    MONO_FONT = "Menlo"
elif IS_WINDOWS:
    UI_FONT  = "Segoe UI"
    MONO_FONT = "Consolas"
else:
    UI_FONT  = "DejaVu Sans"
    MONO_FONT = "DejaVu Sans Mono"

def detect_best_encoder():
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=5,
            creationflags=SUBPROCESS_FLAGS
        )
        encoders = result.stdout.lower()
        
        if "h264_nvenc" in encoders:
            return "h264_nvenc"
        elif "h264_qsv" in encoders:
            return "h264_qsv"
        elif "h264_amf" in encoders:
            return "h264_amf"
        elif "h264_videotoolbox" in encoders:
            return "h264_videotoolbox"
        else:
            return "libx264"
    except:
        return "libx264"

BEST_ENCODER = detect_best_encoder()

def get_encoder_args(encoder, fps, bitrate="5M"):
    """Encoder args for video burn-in"""
    if encoder == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-b:v", bitrate, "-cq", "23"]
    elif encoder == "h264_qsv":
        return ["-c:v", "h264_qsv", "-preset", "veryfast", "-b:v", bitrate]
    elif encoder == "h264_amf":
        return ["-c:v", "h264_amf", "-quality", "speed", "-b:v", bitrate]
    elif encoder == "h264_videotoolbox":
        return ["-c:v", "h264_videotoolbox", "-allow_sw", "1", "-b:v", bitrate]
    else:
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"]

# =========================================================
# FFMPEG WORKERS
# =========================================================

def get_media_duration(path):
    command = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path
    ]
    result = subprocess.run(
        command, capture_output=True, text=True, check=True,
        creationflags=SUBPROCESS_FLAGS
    )
    return float(result.stdout.strip())

def normalize_path(path):
    return path.replace("\\", "/")

def format_srt_time(seconds):
    """Format seconds to SRT time format"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    milliseconds = int((seconds - int(seconds)) * 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{milliseconds:03}"

def extract_audio_for_whisper(video_path, output_path):
    """Extract audio in WAV format for Whisper"""
    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", video_path,
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        output_path
    ]
    
    result = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=SUBPROCESS_FLAGS
    )
    return result.returncode == 0

def extract_audio_segment(args):
    index, source_video, start_time, duration, trim_start, cache_audio_folder = args
    output_file = os.path.join(cache_audio_folder, f"audio_{index:04d}.m4a")

    actual_start = start_time + trim_start
    actual_duration = duration - trim_start

    if actual_duration <= 0.5:
        return (index, -1, output_file, 0, "Đoạn audio quá ngắn sau khi cắt đầu")

    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-accurate_seek",
        "-ss", str(actual_start),
        "-t", str(actual_duration),
        "-i", source_video,
        "-vn",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "48000",
        "-ac", "2",
        "-af", "aresample=async=1:first_pts=0",
        output_file
    ]
    
    result = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=SUBPROCESS_FLAGS
    )
    
    actual_duration = 0
    if result.returncode == 0 and os.path.exists(output_file):
        try:
            probe_cmd = [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                output_file
            ]
            probe_result = subprocess.run(
                probe_cmd, capture_output=True, text=True,
                creationflags=SUBPROCESS_FLAGS
            )
            actual_duration = float(probe_result.stdout.strip())
        except:
            actual_duration = 0
    
    return (index, result.returncode, output_file, actual_duration, result.stderr)

def preprocess_image(args):
    image_path, output_path, width, height = args
    
    try:
        with Image.open(image_path) as img:
            img = img.convert("RGB")
            src_w, src_h = img.size
            
            scale = max(width / src_w, height / src_h)
            new_w = int(src_w * scale)
            new_h = int(src_h * scale)
            
            img = img.resize((new_w, new_h), Image.LANCZOS)
            
            left = (new_w - width) // 2
            top = (new_h - height) // 2
            img = img.crop((left, top, left + width, top + height))
            
            img.save(output_path, "JPEG", quality=92, optimize=False)
        
        return True
    except Exception as e:
        print(f"Error: {e}")
        return False

def create_image_video(args):
    image_path, output_path, width, height, fps = args
    
    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-loop", "1",
        "-framerate", str(fps),
        "-t", "1",
        "-i", image_path,
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "stillimage",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        "-g", str(fps),
        "-keyint_min", str(fps),
        output_path
    ]
    
    result = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=SUBPROCESS_FLAGS
    )
    return (result.returncode, output_path, result.stderr)

def render_segment_fast(args):
    index, image_video_path, audio_path, duration, fps, temp_folder = args
    output_file = os.path.join(temp_folder, f"seg_{index:04d}.mp4")
    
    loop_count = math.ceil(duration)
    
    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-stream_loop", str(loop_count),
        "-i", image_video_path,
        "-i", audio_path,
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "copy",
        "-t", str(duration),
        "-shortest",
        "-fflags", "+genpts",
        "-avoid_negative_ts", "make_zero",
        output_file
    ]
    
    result = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=SUBPROCESS_FLAGS
    )
    return (index, result.returncode, output_file, result.stderr)

def render_segment_with_transition(args):
    index, image_video_path, audio_path, duration, fps, transition_duration, temp_folder = args
    output_file = os.path.join(temp_folder, f"seg_{index:04d}.mp4")
    
    loop_count = math.ceil(duration)
    fade_out_start = max(0, duration - transition_duration)
    
    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-stream_loop", str(loop_count),
        "-i", image_video_path,
        "-i", audio_path,
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-vf", f"fade=t=in:st=0:d={transition_duration},fade=t=out:st={fade_out_start}:d={transition_duration}",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        "-g", str(fps),
        "-c:a", "copy",
        "-t", str(duration),
        "-shortest",
        "-fflags", "+genpts",
        "-avoid_negative_ts", "make_zero",
        output_file
    ]
    
    result = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=SUBPROCESS_FLAGS
    )
    return (index, result.returncode, output_file, result.stderr)

# =========================================================
# WHISPER PROGRESS CAPTURE
# =========================================================

class _WhisperProgressCapture:
    """Redirect Whisper verbose stdout → parse timestamps → call progress callback.
    Whisper với verbose=True in từng segment theo format: [mm:ss.fff --> mm:ss.fff]  text
    """
    _ts_re = re.compile(r'\[(\d+):(\d+\.\d+)\s*-->')

    def __init__(self, callback, total_duration):
        self.callback = callback
        self.total_duration = max(total_duration, 1.0)
        self._buf = ""

    def write(self, text):
        self._buf += text
        while '\n' in self._buf:
            line, self._buf = self._buf.split('\n', 1)
            m = self._ts_re.search(line)
            if m:
                elapsed = int(m.group(1)) * 60 + float(m.group(2))
                pct = min(elapsed / self.total_duration, 1.0)
                seg_text = re.sub(r'^\[.*?\]\s*', '', line).strip()
                self.callback(pct, seg_text)

    def flush(self):
        pass

    def isatty(self):
        return False

    def fileno(self):
        return sys.__stdout__.fileno()


# =========================================================
# UI WIDGETS
# =========================================================

class FlatButton(tk.Frame):
    def __init__(self, parent, text, command=None,
                 bg=ACCENT, hover_bg=ACCENT_HOVER, pressed_bg=ACCENT_PRESSED,
                 fg=TEXT_PRIMARY, width=None, height=38, font=None, padx=20):
        if font is None:
            font = (UI_FONT, 10, "bold")
        super().__init__(parent, bg=bg, height=height, cursor="hand2")
        self.command   = command
        self.bg        = bg
        self.hover_bg  = hover_bg
        self.pressed_bg = pressed_bg
        self.fg        = fg
        self.enabled   = True

        if width:
            self.configure(width=width)
        self.pack_propagate(False)

        self.label = tk.Label(self, text=text, bg=bg, fg=fg,
                              font=font, cursor="hand2", padx=padx)
        self.label.pack(expand=True, fill="both")

        for w in (self, self.label):
            w.bind("<Enter>",          self._on_enter)
            w.bind("<Leave>",          self._on_leave)
            w.bind("<Button-1>",       self._on_press)
            w.bind("<ButtonRelease-1>",self._on_release)

    def _set_color(self, bg, fg=None):
        self.configure(bg=bg)
        self.label.configure(bg=bg, fg=fg or self.fg)

    def _on_enter(self, e):
        if self.enabled: self._set_color(self.hover_bg)

    def _on_leave(self, e):
        if self.enabled: self._set_color(self.bg)

    def _on_press(self, e):
        if self.enabled: self._set_color(self.pressed_bg)

    def _on_release(self, e):
        if self.enabled:
            self._set_color(self.hover_bg)
            if self.command: self.command()

    def set_state(self, state):
        self.enabled = (state == "normal")
        if self.enabled:
            self._set_color(self.bg)
            self.configure(cursor="hand2")
            self.label.configure(cursor="hand2")
        else:
            self._set_color("#21262D", TEXT_MUTED)
            self.configure(cursor="")
            self.label.configure(cursor="")

    def set_text(self, text):
        self.label.configure(text=text)


class ToggleSwitch(tk.Frame):
    TW, TH = 46, 24

    def __init__(self, parent, text, variable, command=None, bg=None):
        _bg = bg or CARD_BG
        super().__init__(parent, bg=_bg, cursor="hand2")
        self.variable = variable
        self.command  = command
        self._bg      = _bg

        self.canvas = tk.Canvas(self, width=self.TW, height=self.TH,
                                bg=_bg, highlightthickness=0, cursor="hand2")
        self.canvas.pack(side="left", padx=(0, 10))

        self.label = tk.Label(self, text=text, bg=_bg, fg=TEXT_PRIMARY,
                              font=(UI_FONT, 9), cursor="hand2")
        self.label.pack(side="left")

        self._draw()
        for w in (self, self.canvas, self.label):
            w.bind("<Button-1>", self._toggle)
        self.variable.trace_add("write", lambda *a: self._draw())

    def _draw(self):
        c = self.canvas
        c.delete("all")
        w, h = self.TW, self.TH
        is_on = self.variable.get()
        track = ACCENT if is_on else "#2D333B"

        # Pill track
        r = h // 2
        c.create_oval(0, 0, h, h, fill=track, outline="")
        c.create_oval(w - h, 0, w, h, fill=track, outline="")
        c.create_rectangle(r, 0, w - r, h, fill=track, outline="")

        # Thumb
        pad = 3
        tx = w - h + pad if is_on else pad
        c.create_oval(tx, pad, tx + h - 2*pad, h - pad, fill="white", outline="")

    def _toggle(self, e=None):
        self.variable.set(not self.variable.get())
        if self.command: self.command()


class Card(tk.Frame):
    def __init__(self, parent, title=None, **kwargs):
        super().__init__(parent, bg=CARD_BG, **kwargs)

        if title:
            header = tk.Frame(self, bg=CARD_HEADER)
            header.pack(fill="x")
            tk.Label(header, text=title, bg=CARD_HEADER, fg=TEXT_PRIMARY,
                     font=(UI_FONT, 10, "bold")
                     ).pack(anchor="w", padx=16, pady=10)
            tk.Frame(self, bg=DIVIDER, height=1).pack(fill="x")

        self.body = tk.Frame(self, bg=CARD_BG)
        self.body.pack(fill="both", expand=True, padx=16, pady=14)


class ScrollableFrame(tk.Frame):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, bg=APP_BG, **kwargs)
        
        self.canvas = tk.Canvas(
            self, bg=APP_BG, highlightthickness=0, borderwidth=0
        )
        self.scrollbar = ttk.Scrollbar(
            self, orient="vertical", command=self.canvas.yview
        )
        
        self.inner = tk.Frame(self.canvas, bg=APP_BG)
        
        self.window_id = self.canvas.create_window(
            (0, 0), window=self.inner, anchor="nw"
        )
        
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        
        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        
        self._bind_mousewheel()
    
    def _on_inner_configure(self, event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
    
    def _on_canvas_configure(self, event):
        self.canvas.itemconfig(self.window_id, width=event.width)
    
    def _bind_mousewheel(self):
        def _on_mousewheel(event):
            if IS_WINDOWS or IS_MAC:
                self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            else:
                self.canvas.yview_scroll(int(-1 * event.delta), "units")
        
        def _on_button4(event):
            self.canvas.yview_scroll(-1, "units")
        
        def _on_button5(event):
            self.canvas.yview_scroll(1, "units")
        
        self.canvas.bind("<Enter>", lambda e: self._activate_scroll())
        self.canvas.bind("<Leave>", lambda e: self._deactivate_scroll())
        self.inner.bind("<Enter>", lambda e: self._activate_scroll())
        
        self._on_mousewheel = _on_mousewheel
        self._on_button4 = _on_button4
        self._on_button5 = _on_button5
    
    def _activate_scroll(self):
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind_all("<Button-4>", self._on_button4)
        self.canvas.bind_all("<Button-5>", self._on_button5)
    
    def _deactivate_scroll(self):
        self.canvas.unbind_all("<MouseWheel>")
        self.canvas.unbind_all("<Button-4>")
        self.canvas.unbind_all("<Button-5>")


# =========================================================
# VIDEO TAB  (một job = một tab)
# =========================================================

class VideoTab(tk.Frame):

    def __init__(self, parent, root_window, tab_number, close_callback=None):
        super().__init__(parent, bg=APP_BG)
        self._root_window = root_window
        self._tab_number  = tab_number
        self._close_cb    = close_callback

        # Per-job isolated paths — no conflict between simultaneous jobs
        _d = f"job_{tab_number}"
        self.CACHE_IMAGE_FOLDER    = os.path.join(_d, "cache_images")
        self.CACHE_AUDIO_FOLDER    = os.path.join(_d, "cache_audio")
        self.CACHE_VIDEO_FOLDER    = os.path.join(_d, "cache_videos")
        self.CACHE_SUBTITLE_FOLDER = os.path.join(_d, "cache_subtitles")
        self.TEMP_FOLDER           = os.path.join(_d, "temp")
        self.OUTPUT_VIDEO          = f"final_video_job{tab_number}.mp4"

        self._setup_variables()
        self._build_ui()

        self.ui_queue = queue.Queue()
        self.after(50, self._process_ui_queue)

        self.whisper_model      = None
        self.whisper_model_name = None
    
    def _setup_variables(self):
        self.video_paths = []
        self.image_folder = tk.StringVar()
        self.segment_seconds = tk.IntVar(value=30)
        self.transition_duration = tk.DoubleVar(value=0.5)
        default_workers = 8 if IS_WINDOWS else 6
        self.max_workers = tk.IntVar(value=default_workers)
        self.preset = tk.StringVar(value="TikTok (Dọc)")
        
        self.random_order = tk.BooleanVar(value=True)
        self.keep_first_audio = tk.BooleanVar(value=False)
        self.use_transition = tk.BooleanVar(value=False)
        self.cleanup_temp = tk.BooleanVar(value=True)
        self.trim_audio_start = tk.BooleanVar(value=False)
        self.trim_audio_seconds = tk.DoubleVar(value=2.0)

        # AI ordering
        self.use_ai_ordering = tk.BooleanVar(value=False)
        self.st_model_name = tk.StringVar(value="paraphrase-multilingual-MiniLM-L12-v2")
        
        # Subtitle options
        self.use_subtitle = tk.BooleanVar(value=False)
        self.whisper_model_var = tk.StringVar(value="base")
        self.subtitle_language = tk.StringVar(value="vi")
        self.subtitle_font_size = tk.IntVar(value=45)
    
    def _build_ui(self):
        main = tk.Frame(self, bg=APP_BG)
        main.pack(fill="both", expand=True, padx=20, pady=14)

        main.grid_columnconfigure(0, weight=1, minsize=500)
        main.grid_columnconfigure(1, weight=1, minsize=480)
        main.grid_rowconfigure(0, weight=1)

        left_wrapper = tk.Frame(main, bg=APP_BG)
        left_wrapper.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        self.left_scrollable = ScrollableFrame(left_wrapper)
        self.left_scrollable.pack(fill="both", expand=True)

        right_col = tk.Frame(main, bg=APP_BG)
        right_col.grid(row=0, column=1, sticky="nsew", padx=(10, 0))

        self._build_left_column(self.left_scrollable.inner)
        self._build_right_column(right_col)
    
    def _build_left_column(self, parent):
        # === Video Sources ===
        videos_card = Card(parent, "▶  Video nguồn")
        videos_card.pack(fill="x", pady=(0, 12), padx=(0, 4))
        
        list_inner = tk.Frame(videos_card.body, bg=CARD_HEADER)
        list_inner.pack(fill="x", pady=(0, 10))
        
        self.videos_listbox = tk.Listbox(
            list_inner, bg=CARD_HEADER, fg=TEXT_PRIMARY,
            selectbackground=ACCENT, selectforeground=APP_BG,
            font=(UI_FONT, 9), height=5,
            relief="flat", borderwidth=0,
            activestyle="none", selectmode="extended",
        )
        self.videos_listbox.pack(side="left", fill="both", expand=True)
        
        list_scroll = tk.Scrollbar(list_inner, command=self.videos_listbox.yview)
        list_scroll.pack(side="right", fill="y")
        self.videos_listbox.config(yscrollcommand=list_scroll.set)
        
        vbtn_frame = tk.Frame(videos_card.body, bg=CARD_BG)
        vbtn_frame.pack(fill="x")
        
        FlatButton(vbtn_frame, "+ Thêm video", command=self.add_videos,
                   width=120, height=34).pack(side="left", padx=(0, 6))
        FlatButton(vbtn_frame, "Xóa chọn", command=self.remove_selected_videos,
                   bg=BTN_SECONDARY, hover_bg=BTN_SEC_HOVER, pressed_bg=DIVIDER,
                   width=100, height=34).pack(side="left", padx=(0, 6))
        FlatButton(vbtn_frame, "Xóa tất cả", command=self.clear_videos,
                   bg=DANGER, hover_bg=DANGER_HOVER, pressed_bg="#C0392B",
                   width=100, height=34).pack(side="left")
        
        self.video_count_label = tk.Label(
            vbtn_frame, text="0 video", bg=CARD_BG, fg=TEXT_MUTED,
            font=(UI_FONT, 9)
        )
        self.video_count_label.pack(side="right")
        
        # === Image Folder ===
        images_card = Card(parent, "⊞  Thư mục ảnh")
        images_card.pack(fill="x", pady=(0, 12), padx=(0, 4))
        
        img_field = tk.Frame(images_card.body, bg=CARD_HEADER)
        img_field.pack(fill="x")

        self.img_entry = tk.Entry(
            img_field, textvariable=self.image_folder,
            bg=CARD_HEADER, fg=TEXT_PRIMARY,
            insertbackground=TEXT_PRIMARY,
            font=(UI_FONT, 10), relief="flat", borderwidth=0
        )
        self.img_entry.pack(side="left", fill="x", expand=True, padx=12, pady=10)

        FlatButton(img_field, "Chọn", command=self.select_images,
                   width=76, height=34, padx=10).pack(side="right", padx=3, pady=3)
        
        # === Configuration ===
        config_card = Card(parent, "⚙  Cấu hình")
        config_card.pack(fill="x", pady=(0, 12), padx=(0, 4))
        
        tk.Label(
            config_card.body, text="Định dạng video",
            bg=CARD_BG, fg=TEXT_SECONDARY, font=(UI_FONT, 9)
        ).pack(anchor="w", pady=(0, 5))
        
        fmt_field = tk.Frame(config_card.body, bg=CARD_HEADER)
        fmt_field.pack(fill="x", pady=(0, 14))

        self.format_combo = ttk.Combobox(
            fmt_field, textvariable=self.preset,
            values=list(VIDEO_PRESETS.keys()),
            state="readonly", font=(UI_FONT, 10),
            style="Modern.TCombobox"
        )
        self.format_combo.pack(fill="x", padx=8, pady=6)
        
        params_grid = tk.Frame(config_card.body, bg=CARD_BG)
        params_grid.pack(fill="x")
        params_grid.grid_columnconfigure(0, weight=1)
        params_grid.grid_columnconfigure(1, weight=1)
        
        self._make_number_input(params_grid, "Độ dài đoạn (giây)", self.segment_seconds, 0, 0)
        self._make_number_input(params_grid, "Số luồng xử lý", self.max_workers, 0, 1)
        self._make_number_input(params_grid, "Hiệu ứng (giây)", self.transition_duration, 1, 0, is_float=True)
        
        # === Options ===
        options_card = Card(parent, "⊙  Tùy chọn")
        options_card.pack(fill="x", pady=(0, 12), padx=(0, 4))
        
        ToggleSwitch(options_card.body, "Trộn ngẫu nhiên thứ tự audio",
                     self.random_order).pack(anchor="w", pady=6, fill="x")
        ToggleSwitch(options_card.body, "Giữ nguyên đoạn audio đầu tiên",
                     self.keep_first_audio).pack(anchor="w", pady=6, fill="x")
        ToggleSwitch(options_card.body, "Hiệu ứng chuyển ảnh mượt (fade)",
                     self.use_transition).pack(anchor="w", pady=6, fill="x")
        ToggleSwitch(options_card.body, "Tự động xóa file tạm sau khi hoàn thành",
                     self.cleanup_temp).pack(anchor="w", pady=6, fill="x")

        trim_row = tk.Frame(options_card.body, bg=CARD_BG)
        trim_row.pack(fill="x", pady=6)

        ToggleSwitch(trim_row, "Cắt bớt phần đầu audio (nhạc intro)",
                     self.trim_audio_start).pack(side="left")

        tk.Label(
            trim_row, text="Số giây:", bg=CARD_BG, fg=TEXT_SECONDARY,
            font=(UI_FONT, 9)
        ).pack(side="left", padx=(20, 4))

        trim_field = tk.Frame(trim_row, bg=CARD_HEADER)
        trim_field.pack(side="left")
        tk.Entry(trim_field, textvariable=self.trim_audio_seconds,
                 bg=CARD_HEADER, fg=TEXT_PRIMARY,
                 insertbackground=TEXT_PRIMARY,
                 font=(UI_FONT, 10), relief="flat", borderwidth=0,
                 justify="center", width=6).pack(padx=8, pady=5)
        
        # === Subtitle Card ===
        subtitle_card = Card(parent, "CC  Phụ đề tự động  (Whisper AI)")
        subtitle_card.pack(fill="x", pady=(0, 12), padx=(0, 4))
        
        ToggleSwitch(subtitle_card.body, "Bật phụ đề tự động",
                     self.use_subtitle).pack(anchor="w", pady=(0, 10), fill="x")
        
        # Whisper model
        tk.Label(
            subtitle_card.body, text="Model Whisper (lớn hơn = chính xác hơn nhưng chậm hơn)",
            bg=CARD_BG, fg=TEXT_SECONDARY, font=(UI_FONT, 9)
        ).pack(anchor="w", pady=(0, 5))
        
        model_field = tk.Frame(subtitle_card.body, bg=CARD_HEADER)
        model_field.pack(fill="x", pady=(0, 10))

        ttk.Combobox(model_field, textvariable=self.whisper_model_var,
                     values=WHISPER_MODELS,
                     state="readonly", font=(UI_FONT, 10),
                     style="Modern.TCombobox"
                     ).pack(fill="x", padx=8, pady=6)
        
        # Language + Font size
        sub_grid = tk.Frame(subtitle_card.body, bg=CARD_BG)
        sub_grid.pack(fill="x")
        sub_grid.grid_columnconfigure(0, weight=1)
        sub_grid.grid_columnconfigure(1, weight=1)
        
        # Language
        lang_frame = tk.Frame(sub_grid, bg=CARD_BG)
        lang_frame.grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=4)
        
        tk.Label(
            lang_frame, text="Ngôn ngữ (vi/en/ja/...)",
            bg=CARD_BG, fg=TEXT_SECONDARY, font=(UI_FONT, 9)
        ).pack(anchor="w", pady=(0, 4))
        
        lang_field = tk.Frame(lang_frame, bg=CARD_HEADER)
        lang_field.pack(fill="x")
        tk.Entry(lang_field, textvariable=self.subtitle_language,
                 bg=CARD_HEADER, fg=TEXT_PRIMARY,
                 insertbackground=TEXT_PRIMARY,
                 font=(UI_FONT, 10), relief="flat", borderwidth=0,
                 justify="center").pack(fill="x", padx=10, pady=8)
        
        self._make_number_input(sub_grid, "Cỡ chữ phụ đề", self.subtitle_font_size, 0, 1)
        
        # === AI Ordering Card ===
        ai_card = Card(parent, "◑  Sắp xếp thông minh  (Local AI)")
        ai_card.pack(fill="x", pady=(0, 12), padx=(0, 4))

        st_status = "✓ sentence-transformers đã cài" if SENTENCE_TRANSFORMERS_AVAILABLE else "✗ Cài: pip install sentence-transformers"
        tk.Label(
            ai_card.body, text=st_status,
            bg=CARD_BG, fg=SUCCESS if SENTENCE_TRANSFORMERS_AVAILABLE else DANGER,
            font=(UI_FONT, 9)
        ).pack(anchor="w", pady=(0, 6))

        tk.Label(
            ai_card.body,
            text="Dùng embedding model chạy local (không cần API key)",
            bg=CARD_BG, fg=TEXT_MUTED, font=(UI_FONT, 8)
        ).pack(anchor="w", pady=(0, 8))

        ToggleSwitch(
            ai_card.body,
            "Sắp xếp audio theo ngữ nghĩa (semantic similarity)",
            self.use_ai_ordering
        ).pack(anchor="w", pady=(0, 10), fill="x")

        tk.Label(
            ai_card.body,
            text="Embedding model (lần đầu sẽ tự tải ~120MB)",
            bg=CARD_BG, fg=TEXT_SECONDARY, font=(UI_FONT, 9)
        ).pack(anchor="w", pady=(0, 4))

        ai_field = tk.Frame(ai_card.body, bg=CARD_HEADER)
        ai_field.pack(fill="x")
        ttk.Combobox(ai_field, textvariable=self.st_model_name,
                     values=["paraphrase-multilingual-MiniLM-L12-v2",
                             "paraphrase-multilingual-mpnet-base-v2",
                             "all-MiniLM-L6-v2"],
                     state="readonly", font=(UI_FONT, 10),
                     style="Modern.TCombobox"
                     ).pack(fill="x", padx=8, pady=6)

        # === Generate Button ===
        self.generate_btn = FlatButton(
            parent, "▶  TẠO VIDEO", command=self.start_generation,
            height=50, font=(UI_FONT, 13, "bold")
        )
        self.generate_btn.pack(fill="x", pady=(12, 16), padx=(0, 4))
    
    def _do_close(self):
        if self._close_cb:
            self._close_cb()

    def _build_right_column(self, parent):
        # Close-tab button (top-right, subtle)
        if self._close_cb:
            close_bar = tk.Frame(parent, bg=APP_BG)
            close_bar.pack(fill="x", pady=(0, 6))
            FlatButton(close_bar, "× Đóng tab", command=self._do_close,
                       bg=BTN_SECONDARY, hover_bg=DANGER, pressed_bg="#C0392B",
                       fg=TEXT_SECONDARY,
                       height=26, font=(UI_FONT, 8), padx=10
                       ).pack(side="right")

        # ── Progress card ──
        progress_card = Card(parent, "Tiến trình")
        progress_card.pack(fill="x", pady=(0, 14))

        # Top row: status text  |  big % number
        top_row = tk.Frame(progress_card.body, bg=CARD_BG)
        top_row.pack(fill="x", pady=(0, 10))

        text_col = tk.Frame(top_row, bg=CARD_BG)
        text_col.pack(side="left", fill="x", expand=True)

        self.status_label = tk.Label(
            text_col, text="Sẵn sàng",
            bg=CARD_BG, fg=TEXT_PRIMARY,
            font=(UI_FONT, 12, "bold"), anchor="w"
        )
        self.status_label.pack(anchor="w")

        self.detail_label = tk.Label(
            text_col, text="Thêm video và ảnh rồi nhấn TẠO VIDEO",
            bg=CARD_BG, fg=TEXT_SECONDARY,
            font=(UI_FONT, 9), anchor="w", wraplength=300, justify="left"
        )
        self.detail_label.pack(anchor="w", pady=(3, 0))

        self.percent_label = tk.Label(
            top_row, text="—",
            bg=CARD_BG, fg=TEXT_MUTED,
            font=(UI_FONT, 26, "bold")
        )
        self.percent_label.pack(side="right", padx=(12, 0))

        # Progress bar
        self.progress = ttk.Progressbar(
            progress_card.body, mode="determinate",
            style="Modern.Horizontal.TProgressbar"
        )
        self.progress.pack(fill="x", ipady=3)

        # Bottom row: time
        bot_row = tk.Frame(progress_card.body, bg=CARD_BG)
        bot_row.pack(fill="x", pady=(8, 0))

        self.time_label = tk.Label(
            bot_row, text="⏱  0.0s",
            bg=CARD_BG, fg=TEXT_MUTED, font=(UI_FONT, 9)
        )
        self.time_label.pack(side="left")

        # ── Log card ──
        log_card = Card(parent, "Nhật ký xử lý")
        log_card.pack(fill="both", expand=True)

        log_inner = tk.Frame(log_card.body, bg=CARD_HEADER)
        log_inner.pack(fill="both", expand=True)

        self.log_box = tk.Text(
            log_inner, bg=CARD_HEADER, fg=TEXT_PRIMARY,
            insertbackground=TEXT_PRIMARY,
            font=(MONO_FONT, 9), relief="flat", borderwidth=0,
            wrap="word", padx=12, pady=10, spacing1=2, spacing3=2,
        )
        self.log_box.pack(side="left", fill="both", expand=True)

        log_scroll = tk.Scrollbar(log_inner, command=self.log_box.yview)
        log_scroll.pack(side="right", fill="y")
        self.log_box.config(yscrollcommand=log_scroll.set)

        self.log_box.tag_configure("success", foreground=SUCCESS)
        self.log_box.tag_configure("error",   foreground=DANGER)
        self.log_box.tag_configure("info",    foreground=ACCENT)
        self.log_box.tag_configure("muted",   foreground=TEXT_MUTED)
        self.log_box.tag_configure("bold",    font=(MONO_FONT, 9, "bold"))
    
    def _make_number_input(self, parent, label, variable, row, col, is_float=False):
        frame = tk.Frame(parent, bg=CARD_BG)
        frame.grid(row=row, column=col, sticky="ew",
                   padx=(0, 6) if col == 0 else (6, 0), pady=4)

        tk.Label(frame, text=label, bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 9)).pack(anchor="w", pady=(0, 4))

        field = tk.Frame(frame, bg=CARD_HEADER)
        field.pack(fill="x")
        tk.Entry(field, textvariable=variable,
                 bg=CARD_HEADER, fg=TEXT_PRIMARY,
                 insertbackground=TEXT_PRIMARY,
                 font=(UI_FONT, 10), relief="flat", borderwidth=0,
                 justify="center").pack(fill="x", padx=10, pady=8)
    
    # =========================================================
    # UI QUEUE
    # =========================================================
    
    def _process_ui_queue(self):
        try:
            while True:
                action = self.ui_queue.get_nowait()
                action()
        except queue.Empty:
            pass
        finally:
            self.after(50, self._process_ui_queue)
    
    def queue_ui(self, action):
        self.ui_queue.put(action)
    
    def log(self, text, tag=None):
        def _log():
            if tag:
                self.log_box.insert("end", f"{text}\n", tag)
            else:
                self.log_box.insert("end", f"{text}\n")
            self.log_box.see("end")
        self.queue_ui(_log)
    
    def update_status(self, status, detail="", progress=None):
        def _update():
            self.status_label.config(text=status)
            if detail:
                self.detail_label.config(text=detail)
            if progress is not None:
                self.progress["value"] = progress
                if progress <= 0:
                    self.percent_label.config(text="—", fg=TEXT_MUTED)
                elif progress >= 100:
                    self.percent_label.config(text="✓", fg=SUCCESS)
                else:
                    self.percent_label.config(text=f"{int(progress)}%", fg=ACCENT)
        self.queue_ui(_update)

    def update_time(self, seconds):
        def _update():
            self.time_label.config(text=f"⏱  {seconds:.1f}s")
        self.queue_ui(_update)
    
    # =========================================================
    # FILE HANDLERS
    # =========================================================
    
    def add_videos(self):
        paths = filedialog.askopenfilenames(
            title="Chọn video",
            filetypes=[("Video Files", "*.mp4 *.mov *.mkv *.avi *.flv")]
        )
        added = 0
        for path in paths:
            if path not in self.video_paths:
                self.video_paths.append(path)
                self.videos_listbox.insert("end", f"  {os.path.basename(path)}")
                added += 1
        
        if added:
            self.log(f"Đã thêm {added} video", "success")
        
        self._update_video_count()
    
    def remove_selected_videos(self):
        selected = self.videos_listbox.curselection()
        if not selected:
            self.log("Chưa chọn video nào để xóa", "muted")
            return
        
        for idx in reversed(selected):
            self.video_paths.pop(idx)
            self.videos_listbox.delete(idx)
        
        self.log(f"Đã xóa {len(selected)} video", "info")
        self._update_video_count()
    
    def clear_videos(self):
        if not self.video_paths:
            return
        
        count = len(self.video_paths)
        self.video_paths.clear()
        self.videos_listbox.delete(0, "end")
        self.log(f"Đã xóa toàn bộ {count} video", "info")
        self._update_video_count()
    
    def _update_video_count(self):
        count = len(self.video_paths)
        self.video_count_label.config(text=f"{count} video")
    
    def select_images(self):
        folder = filedialog.askdirectory(title="Chọn thư mục ảnh")
        if folder:
            self.image_folder.set(folder)
            count = len([f for f in os.listdir(folder) 
                        if f.lower().endswith(('.jpg', '.jpeg', '.png'))])
            self.log(f"Đã chọn thư mục với {count} ảnh", "success")
    
    # =========================================================
    # SUBTITLE - Whisper transcription
    # =========================================================
    
    def transcribe_video(self, video_path, model_name, language, progress_callback=None):
        """Transcribe video → return list of segments with timestamps"""
        if self.whisper_model is None or self.whisper_model_name != model_name:
            self.log(f"  Đang load Whisper model '{model_name}'...", "muted")
            self.whisper_model = whisper.load_model(model_name)
            self.whisper_model_name = model_name

        wav_path = os.path.join(self.CACHE_SUBTITLE_FOLDER,
                                f"{os.path.basename(video_path)}.wav")

        if not extract_audio_for_whisper(video_path, wav_path):
            return None

        if progress_callback:
            try:
                duration = get_media_duration(wav_path)
            except:
                duration = 0
            capture = _WhisperProgressCapture(progress_callback, duration)
            old_stdout = sys.stdout
            sys.stdout = capture
            try:
                result = self.whisper_model.transcribe(wav_path, language=language, verbose=True)
            finally:
                sys.stdout = old_stdout
        else:
            result = self.whisper_model.transcribe(wav_path, language=language)

        try:
            os.remove(wav_path)
        except:
            pass

        return result["segments"]
    
    def remap_subtitles(self, transcription_data, final_audio_order, segment_seconds):
        """Build new SRT timeline based on shuffled audio order.
        
        transcription_data: dict {video_path: [whisper segments]}
        final_audio_order: list of (orig_idx, audio_path, duration) in shuffled order
        
        Each audio segment was originally:
        audio_idx -> (video_path, start_time, duration)
        
        We need to map whisper segments (which are in original timestamps)
        to new positions in the final video.
        """
        # First reconstruct: for each orig_idx, which (video, start, dur) it came from
        # We need to know this from audio_tasks, but let's pass it via final_audio_order
        # Actually we need additional info - let's compute it
        
        # The new SRT timeline
        new_segments = []
        current_time = 0  # Position in final video
        
        for new_idx, audio_meta in enumerate(final_audio_order):
            orig_idx, audio_path, duration, video_path, video_start_time = audio_meta
            
            # Find whisper segments that fall within this audio chunk
            whisper_segs = transcription_data.get(video_path, [])
            
            video_end_time = video_start_time + duration
            
            for seg in whisper_segs:
                seg_start = seg["start"]
                seg_end = seg["end"]
                
                # Skip if completely outside this chunk
                if seg_end <= video_start_time or seg_start >= video_end_time:
                    continue
                
                # Clip to chunk boundaries
                clipped_start = max(seg_start, video_start_time)
                clipped_end = min(seg_end, video_end_time)
                
                # Map to new timeline
                offset_in_chunk = clipped_start - video_start_time
                new_start = current_time + offset_in_chunk
                new_end = current_time + (clipped_end - video_start_time)
                
                new_segments.append({
                    "start": new_start,
                    "end": new_end,
                    "text": seg["text"].strip()
                })
            
            current_time += duration
        
        return new_segments
    
    def write_srt(self, segments, output_path):
        """Write segments to SRT file"""
        with open(output_path, "w", encoding="utf-8") as f:
            for i, seg in enumerate(segments, start=1):
                start = format_srt_time(seg["start"])
                end = format_srt_time(seg["end"])
                text = seg["text"]
                f.write(f"{i}\n")
                f.write(f"{start} --> {end}\n")
                f.write(f"{text}\n\n")
    
    def write_ass(self, segments, output_path, font_size, video_width, video_height):
        """Write segments to ASS file with embedded styling - no force_style needed"""
        
        # ASS header với style embedded
        header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {video_width}
PlayResY: {video_height}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,1,2,20,20,80,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        
        def format_ass_time(seconds):
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            secs = int(seconds % 60)
            centisec = int((seconds - int(seconds)) * 100)
            return f"{hours}:{minutes:02}:{secs:02}.{centisec:02}"
        
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(header)
            for seg in segments:
                start = format_ass_time(seg["start"])
                end = format_ass_time(seg["end"])
                # Replace newlines and escape commas in text
                text = seg["text"].replace("\n", "\\N").replace(",", "،")
                f.write(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}\n")
    
    def burn_subtitle(self, input_video, srt_path, output_video, font_size, fps, progress_callback=None):
        """Burn subtitle using ASS renderer with real-time progress tracking"""

        ass_path = os.path.join(self.TEMP_FOLDER, "subtitle.ass")

        preset_config = VIDEO_PRESETS[self.preset.get()]
        video_width = preset_config["width"]
        video_height = preset_config["height"]

        segments = self._parse_srt(srt_path)

        if not segments:
            return False, "Không có subtitle"

        self.write_ass(segments, ass_path, font_size, video_width, video_height)

        ass_escaped = ass_path.replace("\\", "/").replace(":", "\\:")
        encoder_args = get_encoder_args(BEST_ENCODER, fps)

        try:
            total_duration = get_media_duration(input_video)
        except:
            total_duration = 0

        command = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-progress", "pipe:1",
            "-stats_period", "1",
            "-i", input_video,
            "-vf", f"subtitles='{ass_escaped}'",
            *encoder_args,
            "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            "-movflags", "+faststart",
            output_video
        ]

        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=SUBPROCESS_FLAGS
        )

        for line in proc.stdout:
            if line.startswith("out_time_us=") and total_duration > 0 and progress_callback:
                try:
                    us = int(line.strip().split("=")[1])
                    pct = min(us / 1_000_000 / total_duration, 1.0)
                    progress_callback(pct)
                except:
                    pass

        proc.wait()
        stderr_output = proc.stderr.read()
        return proc.returncode == 0, stderr_output
    
    def _parse_srt(self, srt_path):
        """Parse SRT file to list of segments"""
        segments = []
        
        with open(srt_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        blocks = content.strip().split("\n\n")
        
        for block in blocks:
            lines = block.strip().split("\n")
            if len(lines) < 3:
                continue
            
            # Parse time line: "00:00:01,500 --> 00:00:05,000"
            time_line = lines[1]
            try:
                start_str, end_str = time_line.split(" --> ")
                start = self._srt_time_to_seconds(start_str)
                end = self._srt_time_to_seconds(end_str)
                text = "\n".join(lines[2:])
                segments.append({
                    "start": start,
                    "end": end,
                    "text": text
                })
            except:
                continue
        
        return segments
    
    def _srt_time_to_seconds(self, time_str):
        """Convert SRT time format to seconds"""
        # 00:00:01,500
        time_str = time_str.replace(",", ".")
        parts = time_str.split(":")
        hours = int(parts[0])
        minutes = int(parts[1])
        secs = float(parts[2])
        return hours * 3600 + minutes * 60 + secs
    
    def _find_system_font(self):
        """Find a Unicode-capable font on the system"""
        # Common fonts that support Vietnamese
        candidates = []
        
        if IS_MAC:
            candidates = [
                "/System/Library/Fonts/Helvetica.ttc",
                "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
                "/System/Library/Fonts/Avenir.ttc",
                "/Library/Fonts/Arial Unicode.ttf",
            ]
        elif IS_WINDOWS:
            candidates = [
                "C:/Windows/Fonts/arial.ttf",
                "C:/Windows/Fonts/segoeui.ttf",
                "C:/Windows/Fonts/tahoma.ttf",
            ]
        else:  # Linux
            candidates = [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
                "/usr/share/fonts/TTF/DejaVuSans.ttf",
            ]
        
        for font in candidates:
            if os.path.exists(font):
                return font
        
        return None
    # =========================================================
    # AI ORDERING
    # =========================================================

    def transcribe_audio_files(self, audio_info, model_name="base"):
        """Transcribe từng file audio segment để lấy nội dung cho AI phân tích"""
        if self.whisper_model is None or self.whisper_model_name != model_name:
            self.log(f"  Load Whisper '{model_name}' để phân tích nội dung...", "muted")
            self.whisper_model = whisper.load_model(model_name)
            self.whisper_model_name = model_name

        texts = {}
        sorted_keys = sorted(audio_info.keys())
        for i, idx in enumerate(sorted_keys):
            audio_path, duration = audio_info[idx]
            try:
                result = self.whisper_model.transcribe(audio_path, fp16=False)
                texts[idx] = result["text"].strip()
                preview = texts[idx][:60].replace("\n", " ")
                self.log(f"  [{i+1}/{len(sorted_keys)}] Đoạn {idx}: \"{preview}...\"", "muted")
            except Exception as e:
                texts[idx] = ""
                self.log(f"  [{i+1}/{len(sorted_keys)}] Đoạn {idx}: lỗi transcribe ({e})", "error")
        return texts

    def order_by_semantic_similarity(self, texts, sorted_indices, model_name, keep_first=False):
        """Greedy nearest-neighbor ordering dựa trên semantic similarity của embeddings"""
        from sentence_transformers import SentenceTransformer
        import numpy as np

        self.log(f"  Load embedding model '{model_name}'...", "muted")
        model = SentenceTransformer(model_name)

        sentences = [texts.get(idx, " ") or " " for idx in sorted_indices]
        self.log(f"  Tính embedding cho {len(sentences)} đoạn...", "muted")
        embeddings = model.encode(sentences, show_progress_bar=False)

        # Cosine similarity matrix
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1e-9, norms)
        normed = embeddings / norms
        sim = normed @ normed.T  # shape (n, n)

        n = len(sorted_indices)
        visited = [False] * n

        if keep_first:
            start = 0
        else:
            # Chọn điểm bắt đầu có similarity trung bình cao nhất (chủ đề trung tâm)
            avg_sim = (sim.sum(axis=1) - 1) / max(n - 1, 1)
            start = int(np.argmax(avg_sim))

        order = [start]
        visited[start] = True

        for _ in range(n - 1):
            cur = order[-1]
            best, best_sim = -1, -2.0
            for j in range(n):
                if not visited[j] and sim[cur][j] > best_sim:
                    best_sim = sim[cur][j]
                    best = j
            order.append(best)
            visited[best] = True

        self.log(f"  Thứ tự semantic: {order}", "success")
        return order

    # =========================================================
    # CLEANUP
    # =========================================================
    
    def cleanup_temp_folders(self):
        folders = [self.CACHE_IMAGE_FOLDER, self.CACHE_AUDIO_FOLDER,
                   self.CACHE_VIDEO_FOLDER, self.CACHE_SUBTITLE_FOLDER,
                   self.TEMP_FOLDER,
                   f"job_{self._tab_number}"]   # parent dir cuối cùng
        removed = []
        
        for folder in folders:
            if os.path.exists(folder):
                try:
                    shutil.rmtree(folder)
                    removed.append(folder)
                except Exception as e:
                    self.log(f"Không thể xóa {folder}: {e}", "error")
        
        if removed:
            self.log(f"Đã dọn dẹp {len(removed)} thư mục tạm", "success")
    
    # =========================================================
    # GENERATION
    # =========================================================
    
    def start_generation(self):
        threading.Thread(target=self.process, daemon=True).start()
    
    def process(self):
        stop_timer = None
        try:
            start_time = time.time()
            
            self.queue_ui(lambda: self.generate_btn.set_state("disabled"))
            self.queue_ui(lambda: self.generate_btn.set_text("ĐANG XỬ LÝ..."))
            self.queue_ui(lambda: self.log_box.delete("1.0", "end"))
            self.update_status("Khởi tạo...", "Kiểm tra đầu vào", 0)
            
            stop_timer = threading.Event()
            def update_time():
                while not stop_timer.is_set():
                    self.update_time(time.time() - start_time)
                    time.sleep(0.5)
            timer_thread = threading.Thread(target=update_time, daemon=True)
            timer_thread.start()
            
            if not self.video_paths:
                raise ValueError("Vui lòng thêm ít nhất 1 video")
            
            image_folder = self.image_folder.get()
            if not image_folder or not os.path.exists(image_folder):
                raise ValueError("Vui lòng chọn thư mục ảnh hợp lệ")
            
            # Check whisper if subtitle enabled
            use_subtitle = self.use_subtitle.get()
            if use_subtitle and not WHISPER_AVAILABLE:
                raise ValueError("Whisper chưa được cài. Chạy: pip install openai-whisper")

            use_ai_ordering = self.use_ai_ordering.get()
            if use_ai_ordering and not SENTENCE_TRANSFORMERS_AVAILABLE:
                raise ValueError("sentence-transformers chưa được cài. Chạy: pip install sentence-transformers")
            if use_ai_ordering and not WHISPER_AVAILABLE:
                raise ValueError("Whisper cần thiết để transcribe cho AI ordering. Chạy: pip install openai-whisper")
            
            preset_config = VIDEO_PRESETS[self.preset.get()]
            width = preset_config["width"]
            height = preset_config["height"]
            fps = preset_config["fps"]
            segment_seconds = self.segment_seconds.get()
            max_workers = self.max_workers.get()
            transition_dur = self.transition_duration.get()
            use_transition = self.use_transition.get()
            whisper_model_name = self.whisper_model_var.get()
            sub_language = self.subtitle_language.get()
            sub_font_size = self.subtitle_font_size.get()
            trim_audio_secs = self.trim_audio_seconds.get() if self.trim_audio_start.get() else 0.0
            st_model = self.st_model_name.get()
            
            # Adjust step count
            total_steps = 7 if use_subtitle else 6
            
            self.log("=" * 60, "muted")
            self.log("BẮT ĐẦU TẠO VIDEO", "bold")
            self.log("=" * 60, "muted")
            self.log(f"Định dạng: {self.preset.get()} ({width}x{height} @ {fps}fps)", "info")
            self.log(f"Số video: {len(self.video_paths)}")
            self.log(f"Hiệu ứng: {'Có (fade ' + str(transition_dur) + 's)' if use_transition else 'Không'}")
            self.log(f"Phụ đề: {'Có (Whisper ' + whisper_model_name + ', ' + sub_language + ')' if use_subtitle else 'Không'}")
            self.log(f"Cắt đầu audio: {'Có (cắt ' + str(trim_audio_secs) + 's đầu tiên)' if trim_audio_secs > 0 else 'Không'}")
            self.log(f"AI Ordering: {'Có (' + st_model + ')' if use_ai_ordering else 'Không'}")
            self.log(f"Encoder: {BEST_ENCODER}")
            self.log("")
            
            # STEP 1: Clean
            t0 = time.time()
            self.update_status(f"Bước 1/{total_steps}: Dọn dẹp", "Xóa file tạm cũ...", 2)
            
            for folder in [self.CACHE_IMAGE_FOLDER, self.CACHE_AUDIO_FOLDER,
                           self.CACHE_VIDEO_FOLDER, self.CACHE_SUBTITLE_FOLDER,
                           self.TEMP_FOLDER]:
                if os.path.exists(folder):
                    shutil.rmtree(folder)
                os.makedirs(folder)
            
            self.log(f"[1/{total_steps}] Dọn dẹp xong ({time.time()-t0:.1f}s)", "success")
            
            # STEP 2: Analyze + Transcribe (if subtitle enabled)
            t0 = time.time()
            self.update_status(f"Bước 2/{total_steps}: Phân tích video", "Đọc thông tin video...", 5)
            
            audio_tasks = []
            audio_metadata = {}  # idx -> (video_path, start_time, duration)
            audio_index = 0
            total_input_duration = 0
            
            for video_path in self.video_paths:
                video_duration = get_media_duration(video_path)
                total_input_duration += video_duration
                video_segments = math.ceil(video_duration / segment_seconds)
                
                self.log(f"  {os.path.basename(video_path)}: {video_duration:.1f}s -> {video_segments} đoạn")
                
                for seg_idx in range(video_segments):
                    seg_start = seg_idx * segment_seconds
                    remain = video_duration - seg_start
                    seg_duration = min(segment_seconds, remain)
                    
                    if seg_duration < 1.0:
                        continue
                    
                    trim_for_seg = trim_audio_secs if audio_index == 0 else 0.0
                    audio_tasks.append((audio_index, video_path, seg_start, seg_duration,
                                        trim_for_seg, self.CACHE_AUDIO_FOLDER))
                    audio_metadata[audio_index] = (video_path, seg_start, seg_duration)
                    audio_index += 1
            
            image_files = [
                os.path.join(image_folder, f)
                for f in os.listdir(image_folder)
                if f.lower().endswith(('.jpg', '.jpeg', '.png'))
            ]
            
            if not image_files:
                raise ValueError("Không tìm thấy ảnh trong thư mục")
            
            image_tasks = []
            cached_images = []
            for idx, image in enumerate(image_files):
                cache_path = os.path.join(self.CACHE_IMAGE_FOLDER, f"img_{idx:04d}.jpg")
                cached_images.append(cache_path)
                image_tasks.append((image, cache_path, width, height))
            
            self.log(f"[2/{total_steps}] Tổng: {len(audio_tasks)} đoạn audio + {len(image_tasks)} ảnh ({time.time()-t0:.1f}s)", "success")
            
            # STEP 2.5: Transcribe (if subtitle enabled)
            transcription_data = {}
            if use_subtitle:
                t0 = time.time()
                self.update_status(f"Bước 3/{total_steps}: Nhận dạng giọng nói", 
                                   f"Whisper {whisper_model_name} - đang xử lý...", 8)
                
                self.log(f"[Whisper] Sử dụng model '{whisper_model_name}', ngôn ngữ '{sub_language}'", "info")
                
                n_vids = len(self.video_paths)
                for vid_idx, video_path in enumerate(self.video_paths):
                    vid_name = os.path.basename(video_path)
                    self.log(f"  Đang transcribe {vid_name}...")
                    base_pct = 8 + (vid_idx / n_vids) * 22
                    scale_pct = 22 / n_vids

                    def _whisper_progress(pct, seg_text,
                                          _name=vid_name, _vidx=vid_idx,
                                          _base=base_pct, _scale=scale_pct):
                        preview = seg_text[:55] if seg_text else "..."
                        self.update_status(
                            f"Bước 3/{total_steps}: Nhận dạng giọng nói",
                            f"[{_vidx+1}/{n_vids}] {_name}  {pct*100:.0f}%  \"{preview}\"",
                            _base + pct * _scale
                        )

                    try:
                        segments = self.transcribe_video(
                            video_path, whisper_model_name, sub_language,
                            progress_callback=_whisper_progress
                        )
                        if segments:
                            transcription_data[video_path] = segments
                            self.log(f"  ✓ Trích {len(segments)} câu", "success")
                        else:
                            self.log(f"  ✗ Không trích được audio", "error")
                    except Exception as e:
                        self.log(f"  ✗ Lỗi: {e}", "error")
                
                self.log(f"[3/{total_steps}] Whisper hoàn thành ({time.time()-t0:.1f}s)", "success")
                step_offset = 1
            else:
                step_offset = 0
            
            # STEP 3 (or 4): Extract audio + cache images
            t0 = time.time()
            current_step = 3 + step_offset
            self.update_status(f"Bước {current_step}/{total_steps}: Xử lý audio + ảnh", 
                              "Trích xuất audio và resize ảnh...", 30)
            
            audio_info = {}
            total_assets = len(audio_tasks) + len(image_tasks)
            completed = 0
            
            with ProcessPoolExecutor(max_workers=max_workers) as executor:
                audio_futures = {executor.submit(extract_audio_segment, task): ("audio", task[0]) 
                                for task in audio_tasks}
                image_futures = {executor.submit(preprocess_image, task): ("image", i) 
                                for i, task in enumerate(image_tasks)}
                all_futures = {**audio_futures, **image_futures}
                
                for future in as_completed(all_futures):
                    task_type, task_id = all_futures[future]
                    
                    if task_type == "audio":
                        idx, returncode, audio_path, actual_duration, stderr = future.result()
                        if returncode == 0 and os.path.exists(audio_path) and os.path.getsize(audio_path) > 0:
                            audio_info[idx] = (audio_path, actual_duration)
                    
                    completed += 1
                    pct = 30 + (completed / total_assets) * 15
                    self.update_status(
                        f"Bước {current_step}/{total_steps}: Xử lý audio + ảnh",
                        f"Đã xử lý {completed}/{total_assets}", pct
                    )
            
            self.log(f"[{current_step}/{total_steps}] Audio: {len(audio_info)}/{len(audio_tasks)}, Ảnh: {len(cached_images)} ({time.time()-t0:.1f}s)", "success")
            
            if not audio_info:
                raise ValueError("Không trích xuất được audio")
            
            # STEP 4 (or 5): Pre-encode images
            t0 = time.time()
            current_step = 4 + step_offset
            self.update_status(f"Bước {current_step}/{total_steps}: Mã hóa ảnh", 
                              "Chuyển ảnh thành video clip...", 45)
            
            video_image_tasks = []
            cached_image_videos = []
            
            for idx, img_path in enumerate(cached_images):
                video_path = os.path.join(self.CACHE_VIDEO_FOLDER, f"imgvid_{idx:04d}.mp4")
                cached_image_videos.append(video_path)
                video_image_tasks.append((img_path, video_path, width, height, fps))
            
            with ProcessPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(create_image_video, task) for task in video_image_tasks]
                completed = 0
                total = len(futures)
                
                for future in as_completed(futures):
                    returncode, vpath, stderr = future.result()
                    completed += 1
                    pct = 45 + (completed / total) * 15
                    self.update_status(
                        f"Bước {current_step}/{total_steps}: Mã hóa ảnh",
                        f"Đã mã hóa {completed}/{total} ảnh", pct
                    )
            
            valid_image_videos = [v for v in cached_image_videos if os.path.exists(v) and os.path.getsize(v) > 0]
            
            if not valid_image_videos:
                raise ValueError("Không tạo được video từ ảnh")
            
            self.log(f"[{current_step}/{total_steps}] Mã hóa {len(valid_image_videos)} clip ảnh ({time.time()-t0:.1f}s)", "success")
            
            # STEP 5 (or 6): Build pairs + assemble
            t0 = time.time()
            current_step = 5 + step_offset
            self.update_status(f"Bước {current_step}/{total_steps}: Ghép video", 
                              "Tạo các đoạn video...", 60)
            
            sorted_indices = sorted(audio_info.keys())
            ordered_audio = [(idx, audio_info[idx][0], audio_info[idx][1]) for idx in sorted_indices]

            final_audio_order = None

            if use_ai_ordering:
                try:
                    self.update_status(
                        f"Bước {current_step}/{total_steps}: Ghép video",
                        "AI đang transcribe và tính semantic similarity...", 60
                    )
                    self.log("  [AI] Transcribe từng đoạn audio...", "info")
                    texts = self.transcribe_audio_files(audio_info, model_name="base")

                    self.log(f"  [AI] Tính semantic similarity ({st_model})...", "info")
                    keep_first = self.keep_first_audio.get()
                    ai_order = self.order_by_semantic_similarity(
                        texts, sorted_indices, st_model, keep_first=keep_first
                    )

                    mapped = [
                        (sorted_indices[i], audio_info[sorted_indices[i]][0], audio_info[sorted_indices[i]][1])
                        for i in ai_order
                    ]
                    final_audio_order = mapped
                except Exception as e:
                    self.log(f"  [AI] Lỗi: {e} → fallback sang random", "error")

            if final_audio_order is None:
                if self.random_order.get():
                    if self.keep_first_audio.get() and len(ordered_audio) > 1:
                        first = ordered_audio[0]
                        rest = ordered_audio[1:]
                        random.shuffle(rest)
                        final_audio_order = [first] + rest
                        self.log("  Thứ tự audio: ngẫu nhiên (giữ đoạn đầu)")
                    else:
                        final_audio_order = ordered_audio.copy()
                        random.shuffle(final_audio_order)
                        self.log("  Thứ tự audio: ngẫu nhiên hoàn toàn")
                else:
                    final_audio_order = ordered_audio
                    self.log("  Thứ tự audio: tuần tự")
            
            # Build extended audio info for subtitle remapping
            final_audio_with_meta = []
            for orig_idx, audio_path, duration in final_audio_order:
                meta = audio_metadata[orig_idx]  # (video_path, start_time, duration)
                final_audio_with_meta.append(
                    (orig_idx, audio_path, duration, meta[0], meta[1])
                )
            
            render_tasks = []
            for new_idx, (orig_idx, audio_path, duration) in enumerate(final_audio_order):
                image_video = random.choice(valid_image_videos)
                if use_transition:
                    render_tasks.append((new_idx, image_video, audio_path, duration, fps,
                                         transition_dur, self.TEMP_FOLDER))
                else:
                    render_tasks.append((new_idx, image_video, audio_path, duration, fps,
                                         self.TEMP_FOLDER))
            
            results = {}
            render_func = render_segment_with_transition if use_transition else render_segment_fast
            
            with ProcessPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(render_func, task) for task in render_tasks]
                completed = 0
                total = len(futures)
                
                for future in as_completed(futures):
                    idx, returncode, video_path, stderr = future.result()
                    
                    if returncode == 0 and os.path.exists(video_path) and os.path.getsize(video_path) > 0:
                        results[idx] = video_path
                    
                    completed += 1
                    pct = 60 + (completed / total) * 20
                    self.update_status(
                        f"Bước {current_step}/{total_steps}: Ghép video",
                        f"Đã ghép {completed}/{total} đoạn", pct
                    )
            
            self.log(f"[{current_step}/{total_steps}] Ghép {len(results)}/{total} đoạn ({time.time()-t0:.1f}s)", "success")
            
            if not results:
                raise ValueError("Không ghép được video")
            
            # STEP 6 (or 7): Final concat
            t0 = time.time()
            current_step = 6 + step_offset
            self.update_status(f"Bước {current_step}/{total_steps}: Hoàn thiện", 
                              "Ghép video cuối...", 80)
            
            sorted_results = [results[idx] for idx in sorted(results.keys())]
            
            concat_file = os.path.join(self.TEMP_FOLDER, "concat.txt")
            with open(concat_file, "w", encoding="utf-8") as f:
                for video in sorted_results:
                    abs_path = os.path.abspath(video)
                    normalized = normalize_path(abs_path)
                    f.write(f"file '{normalized}'\n")
            
            # If subtitle: concat to intermediate, then burn subtitle
            if use_subtitle:
                intermediate_video = os.path.join(self.TEMP_FOLDER, "no_subtitle.mp4")
            else:
                intermediate_video = self.OUTPUT_VIDEO
            
            merge_cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "concat", "-safe", "0",
                "-i", concat_file,
                "-c", "copy",
                "-movflags", "+faststart",
                intermediate_video
            ]
            
            result = subprocess.run(
                merge_cmd, stderr=subprocess.PIPE, text=True,
                creationflags=SUBPROCESS_FLAGS
            )
            
            if result.returncode != 0:
                raise Exception(f"Lỗi ghép video: {result.stderr}")
            
            self.log(f"[{current_step}/{total_steps}] Ghép xong ({time.time()-t0:.1f}s)", "success")
            
            # STEP 7: Burn subtitle (if enabled)
            if use_subtitle:
                t0 = time.time()
                self.update_status(f"Bước {total_steps}/{total_steps}: Ghép phụ đề", 
                                   "Tạo SRT và burn vào video...", 90)
                
                # Remap subtitle to new timeline
                new_segments = self.remap_subtitles(
                    transcription_data, final_audio_with_meta, segment_seconds
                )
                
                self.log(f"  Tạo {len(new_segments)} câu phụ đề trên timeline mới")
                
                # Write SRT
                srt_path = os.path.join(self.TEMP_FOLDER, "subtitle.srt")
                self.write_srt(new_segments, srt_path)

                final_srt = self.OUTPUT_VIDEO.replace(".mp4", ".srt")
                shutil.copy(srt_path, final_srt)
                self.log(f"  Đã lưu file SRT: {final_srt}", "info")

                self.log("  Đang burn subtitle vào video...")

                def _sub_progress(pct):
                    self.update_status(
                        f"Bước {total_steps}/{total_steps}: Burn phụ đề",
                        f"Đang render phụ đề... {pct*100:.0f}%",
                        90 + pct * 9
                    )

                success, stderr = self.burn_subtitle(
                    intermediate_video, srt_path, self.OUTPUT_VIDEO, sub_font_size, fps,
                    progress_callback=_sub_progress
                )
                
                if not success:
                    raise Exception(f"Lỗi burn subtitle: {stderr}")
                
                self.log(f"[{total_steps}/{total_steps}] Burn subtitle xong ({time.time()-t0:.1f}s)", "success")
            
            if self.cleanup_temp.get():
                self.update_status("Dọn dẹp", "Xóa file tạm...", 98)
                self.cleanup_temp_folders()
            
            stop_timer.set()
            
            output_duration = get_media_duration(self.OUTPUT_VIDEO)
            file_size = os.path.getsize(self.OUTPUT_VIDEO) / (1024 * 1024)
            total_time = time.time() - start_time
            
            self.update_status(
                "Hoàn thành!",
                f"Đã tạo: {self.OUTPUT_VIDEO} ({file_size:.1f} MB)",
                100
            )
            
            self.log("")
            self.log("=" * 60, "muted")
            self.log("HOÀN THÀNH", "success")
            self.log("=" * 60, "muted")
            self.log(f"Thời gian: {total_time:.1f}s", "info")
            self.log(f"Đầu ra: {os.path.abspath(self.OUTPUT_VIDEO)}")
            self.log(f"Kích thước: {file_size:.2f} MB")
            self.log(f"Đầu vào: {total_input_duration:.2f}s | Đầu ra: {output_duration:.2f}s")
            self.log(f"Tốc độ: {total_input_duration/total_time:.1f}x realtime", "success")
            
            self.queue_ui(lambda: messagebox.showinfo(
                "Thành công",
                f"Đã tạo video trong {total_time:.1f} giây!\n\n"
                f"File: {self.OUTPUT_VIDEO}\n"
                f"Kích thước: {file_size:.2f} MB\n"
                f"Thời lượng: {output_duration:.1f}s\n"
                f"Tốc độ: {total_input_duration/total_time:.1f}x realtime",
                parent=self._root_window
            ))
            
        except Exception as e:
            error_msg = str(e)
            self.log(f"\nLỖI: {error_msg}", "error")
            self.update_status("Có lỗi xảy ra", error_msg, 0)
            self.queue_ui(lambda msg=error_msg: messagebox.showerror("Lỗi", msg,
                                                                     parent=self._root_window))
        finally:
            if stop_timer:
                stop_timer.set()
            self.queue_ui(lambda: self.generate_btn.set_state("normal"))
            self.queue_ui(lambda: self.generate_btn.set_text("▶  TẠO VIDEO"))


# =========================================================
# CUSTOM TAB NOTEBOOK
# =========================================================

class TabNotebook(tk.Frame):
    """Custom polished tab bar — replaces ttk.Notebook."""

    TAB_H = 42   # height of the tab bar row

    def __init__(self, parent, add_command=None, **kwargs):
        super().__init__(parent, bg=APP_BG, **kwargs)
        self._tabs     = {}   # tid → {grp, acc, inner, lbl, close, content}
        self._order    = []
        self._active   = None
        self._next_id  = 0
        self._add_cmd  = add_command

        # ── Tab bar ───────────────────────────────────
        bar = tk.Frame(self, bg=APP_BG, height=self.TAB_H)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        self._tab_row = tk.Frame(bar, bg=APP_BG)
        self._tab_row.pack(side="left", fill="y", padx=(6, 0))

        # Inline "+" button
        plus_frame = tk.Frame(bar, bg=APP_BG, width=44, cursor="hand2")
        plus_frame.pack(side="left", fill="y", padx=(2, 0))
        plus_frame.pack_propagate(False)
        self._plus = tk.Label(plus_frame, text="+", bg=APP_BG, fg=TEXT_SECONDARY,
                              font=(UI_FONT, 16), cursor="hand2")
        self._plus.place(relx=0.5, rely=0.5, anchor="center")
        for w in (plus_frame, self._plus):
            w.bind("<Enter>",    lambda e: self._plus.configure(fg=TEXT_PRIMARY, bg=CARD_BG)
                                           or plus_frame.configure(bg=CARD_BG))
            w.bind("<Leave>",    lambda e: self._plus.configure(fg=TEXT_SECONDARY, bg=APP_BG)
                                           or plus_frame.configure(bg=APP_BG))
            w.bind("<Button-1>", lambda e: self._add_cmd() if self._add_cmd else None)

        # ── Separator ─────────────────────────────────
        tk.Frame(self, bg=BORDER_COLOR, height=1).pack(fill="x")

        # ── Content area ──────────────────────────────
        self._content = tk.Frame(self, bg=APP_BG)
        self._content.pack(fill="both", expand=True)

    # ------------------------------------------------------------------
    def new_tab(self, title):
        """Add a tab → returns (tab_id, content_frame)."""
        tid = self._next_id
        self._next_id += 1

        # Outer group frame
        grp = tk.Frame(self._tab_row, bg=APP_BG, cursor="hand2")
        grp.pack(side="left", padx=(0, 1))

        # 2-px top accent (active indicator)
        acc = tk.Frame(grp, bg=APP_BG, height=2)
        acc.pack(fill="x")

        # Inner label + × row
        inner = tk.Frame(grp, bg=APP_BG, cursor="hand2")
        inner.pack(fill="both", expand=True)

        lbl = tk.Label(inner, text=f"  {title}  ",
                       bg=APP_BG, fg=TEXT_SECONDARY,
                       font=(UI_FONT, 10), cursor="hand2")
        lbl.pack(side="left", ipady=6)

        close = tk.Label(inner, text=" ×",
                         bg=APP_BG, fg=TEXT_MUTED,
                         font=(UI_FONT, 12), cursor="hand2",
                         padx=6)
        close.pack(side="left", ipady=6)

        # Content frame
        content = tk.Frame(self._content, bg=APP_BG)

        self._tabs[tid] = dict(grp=grp, acc=acc, inner=inner,
                               lbl=lbl, close=close, content=content)
        self._order.append(tid)

        # Bindings
        for w in (grp, inner, lbl):
            w.bind("<Button-1>", lambda e, t=tid: self.select(t))
            w.bind("<Enter>",    lambda e, t=tid: self._hover(t, True))
            w.bind("<Leave>",    lambda e, t=tid: self._hover(t, False))
        close.bind("<Button-1>", lambda e, t=tid: self._close(t))
        close.bind("<Enter>",    lambda e: close.configure(fg=DANGER))
        close.bind("<Leave>",    lambda e, t=tid: self._restore_close(t))

        self.select(tid)
        return tid, content

    # ------------------------------------------------------------------
    def select(self, tid):
        self._active = tid
        for t_id, t in self._tabs.items():
            on = (t_id == tid)
            bg = CARD_HEADER if on else APP_BG
            for k in ("grp", "inner", "lbl", "close"):
                t[k].configure(bg=bg)
            t["lbl"].configure(fg=TEXT_PRIMARY if on else TEXT_SECONDARY)
            t["close"].configure(fg=TEXT_SECONDARY if on else TEXT_MUTED)
            t["acc"].configure(bg=ACCENT if on else APP_BG)
            if on:
                t["content"].pack(fill="both", expand=True)
            else:
                t["content"].pack_forget()

    def _hover(self, tid, entering):
        if tid == self._active:
            return
        bg = CARD_BG if entering else APP_BG
        for k in ("grp", "inner", "lbl", "close"):
            self._tabs[tid][k].configure(bg=bg)

    def _restore_close(self, tid):
        fg = TEXT_SECONDARY if tid == self._active else TEXT_MUTED
        self._tabs[tid]["close"].configure(fg=fg)

    def _close(self, tid):
        if len(self._tabs) <= 1:
            return
        idx = self._order.index(tid)
        t = self._tabs.pop(tid)
        t["grp"].destroy()
        t["content"].destroy()
        self._order.remove(tid)
        new = self._order[min(idx, len(self._order) - 1)]
        self.select(new)


# =========================================================
# APP SHELL  (quản lý cửa sổ + nhiều tab)
# =========================================================

class VideoGeneratorApp:

    def __init__(self, root):
        self.root = root
        self.root.title("Video Generator Pro")
        self.root.geometry("1340x900")
        self.root.minsize(1100, 680)
        self.root.configure(bg=APP_BG)

        self._tab_count = 0

        self._setup_styles()
        self._build_header()
        self._build_notebook()
        self._add_tab()

    # ── Styles ────────────────────────────────────────────
    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")

        style.configure("Modern.TCombobox",
            fieldbackground=CARD_HEADER, background=CARD_HEADER,
            foreground=TEXT_PRIMARY, arrowcolor=TEXT_SECONDARY,
            borderwidth=0, relief="flat",
            font=(UI_FONT, 10), padding=(8, 6))
        style.map("Modern.TCombobox",
            fieldbackground=[("readonly", CARD_HEADER)],
            selectbackground=[("readonly", CARD_HEADER)],
            selectforeground=[("readonly", TEXT_PRIMARY)])

        # (ttk.Notebook no longer used — custom TabNotebook handles tabs)

        style.configure("Modern.Horizontal.TProgressbar",
            background=ACCENT, troughcolor=DIVIDER,
            borderwidth=0, lightcolor=ACCENT, darkcolor=ACCENT,
            thickness=8)

        style.configure("Vertical.TScrollbar",
            background=CARD_HEADER, troughcolor=APP_BG,
            borderwidth=0, arrowcolor=TEXT_MUTED, gripcount=0)
        style.map("Vertical.TScrollbar",
            background=[("active", BORDER_COLOR), ("pressed", ACCENT)])

    # ── Header ────────────────────────────────────────────
    def _build_header(self):
        hdr = tk.Frame(self.root, bg=APP_BG)
        hdr.pack(fill="x", padx=26, pady=(18, 14))

        left = tk.Frame(hdr, bg=APP_BG)
        left.pack(side="left", fill="y")

        row = tk.Frame(left, bg=APP_BG)
        row.pack(anchor="w")
        tk.Label(row, text="Video Generator", bg=APP_BG, fg=TEXT_PRIMARY,
                 font=(UI_FONT, 18, "bold")).pack(side="left")
        tk.Label(row, text=" Pro", bg=APP_BG, fg=ACCENT,
                 font=(UI_FONT, 18, "bold")).pack(side="left")

        pills = tk.Frame(left, bg=APP_BG)
        pills.pack(anchor="w", pady=(5, 0))

        def _pill(text, ok):
            bg = "#1A3329" if ok else "#3B1C1C"
            fg = "#3FB950" if ok else "#F85149"
            f = tk.Frame(pills, bg=bg)
            f.pack(side="left", padx=(0, 5))
            tk.Label(f, text=f" {text} ", bg=bg, fg=fg,
                     font=(UI_FONT, 8, "bold")).pack(padx=2, pady=2)

        _pill(platform.system(), True)
        enc_ok = any(x in BEST_ENCODER for x in ("nvenc", "toolbox", "qsv", "amf"))
        _pill(BEST_ENCODER, enc_ok)
        _pill("Whisper ✓" if WHISPER_AVAILABLE else "Whisper ✗", WHISPER_AVAILABLE)
        _pill("ST ✓" if SENTENCE_TRANSFORMERS_AVAILABLE else "ST ✗",
              SENTENCE_TRANSFORMERS_AVAILABLE)

        tk.Frame(self.root, bg=BORDER_COLOR, height=1).pack(fill="x")

    # ── Tab notebook ──────────────────────────────────────
    def _build_notebook(self):
        self.notebook = TabNotebook(self.root, add_command=self._add_tab)
        self.notebook.pack(fill="both", expand=True)

    # ── Tab management ────────────────────────────────────
    def _add_tab(self):
        self._tab_count += 1
        tid, content = self.notebook.new_tab(f"Job {self._tab_count}")
        close_cb = lambda t=tid: self.notebook._close(t)
        tab = VideoTab(content, self.root, self._tab_count, close_callback=close_cb)
        tab.pack(fill="both", expand=True)


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    if IS_WINDOWS:
        from multiprocessing import freeze_support
        freeze_support()

    root = tk.Tk()
    app  = VideoGeneratorApp(root)
    root.mainloop()
