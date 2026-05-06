import os
import sys
import math
import random
import shutil
import subprocess
import threading
import platform
import queue
import tkinter as tk
import time

from tkinter import ttk, filedialog, messagebox
from concurrent.futures import ProcessPoolExecutor, as_completed
from PIL import Image

# =========================================================
# CONFIG
# =========================================================

APP_BG = "#0A0E27"
CARD_BG = "#111827"
CARD_HOVER = "#1A2236"
ACCENT = "#3B82F6"
ACCENT_HOVER = "#2563EB"
ACCENT_PRESSED = "#1D4ED8"
DANGER = "#EF4444"
DANGER_HOVER = "#DC2626"
SUCCESS = "#10B981"
TEXT_PRIMARY = "#F9FAFB"
TEXT_SECONDARY = "#9CA3AF"
TEXT_MUTED = "#6B7280"
BORDER_COLOR = "#1F2937"
INPUT_BG = "#0F172A"

CACHE_IMAGE_FOLDER = "cache_images"
CACHE_AUDIO_FOLDER = "cache_audio"
CACHE_VIDEO_FOLDER = "cache_videos"
TEMP_FOLDER = "temp"
OUTPUT_VIDEO = "final_video.mp4"

VIDEO_PRESETS = {
    "TikTok (Dọc)": {"width": 1080, "height": 1920, "fps": 30},
    "YouTube (Ngang)": {"width": 1920, "height": 1080, "fps": 30},
    "Instagram Reel": {"width": 1080, "height": 1920, "fps": 30},
    "YouTube Shorts": {"width": 1080, "height": 1920, "fps": 30},
}

# =========================================================
# PLATFORM DETECTION
# =========================================================

IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"

SUBPROCESS_FLAGS = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0

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

def extract_audio_segment(args):
    index, source_video, start_time, duration = args
    output_file = os.path.join(CACHE_AUDIO_FOLDER, f"audio_{index:04d}.m4a")
    
    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-accurate_seek",
        "-ss", str(start_time),
        "-t", str(duration),
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
    index, image_video_path, audio_path, duration, fps = args
    output_file = os.path.join(TEMP_FOLDER, f"seg_{index:04d}.mp4")
    
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
    index, image_video_path, audio_path, duration, fps, transition_duration = args
    output_file = os.path.join(TEMP_FOLDER, f"seg_{index:04d}.mp4")
    
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
# UI WIDGETS
# =========================================================

class FlatButton(tk.Frame):
    def __init__(self, parent, text, command=None, 
                 bg=ACCENT, hover_bg=ACCENT_HOVER, pressed_bg=ACCENT_PRESSED,
                 fg=TEXT_PRIMARY, width=None, height=40, font=("Segoe UI", 10, "bold"),
                 padx=20):
        super().__init__(parent, bg=bg, height=height, cursor="hand2")
        
        self.command = command
        self.bg = bg
        self.hover_bg = hover_bg
        self.pressed_bg = pressed_bg
        self.disabled_bg = "#374151"
        self.enabled = True
        
        if width:
            self.configure(width=width)
        self.pack_propagate(False)
        
        self.label = tk.Label(
            self, text=text, bg=bg, fg=fg, font=font,
            cursor="hand2", padx=padx
        )
        self.label.pack(expand=True, fill="both")
        
        for widget in [self, self.label]:
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)
            widget.bind("<Button-1>", self._on_press)
            widget.bind("<ButtonRelease-1>", self._on_release)
    
    def _set_color(self, color):
        self.configure(bg=color)
        self.label.configure(bg=color)
    
    def _on_enter(self, e):
        if self.enabled:
            self._set_color(self.hover_bg)
    
    def _on_leave(self, e):
        if self.enabled:
            self._set_color(self.bg)
    
    def _on_press(self, e):
        if self.enabled:
            self._set_color(self.pressed_bg)
    
    def _on_release(self, e):
        if self.enabled:
            self._set_color(self.hover_bg)
            if self.command:
                self.command()
    
    def set_state(self, state):
        self.enabled = (state == "normal")
        if self.enabled:
            self._set_color(self.bg)
            self.configure(cursor="hand2")
            self.label.configure(cursor="hand2")
        else:
            self._set_color(self.disabled_bg)
            self.configure(cursor="")
            self.label.configure(cursor="")
    
    def set_text(self, text):
        self.label.configure(text=text)


class ToggleSwitch(tk.Frame):
    def __init__(self, parent, text, variable, command=None):
        super().__init__(parent, bg=CARD_BG, cursor="hand2")
        
        self.variable = variable
        self.command = command
        
        self.canvas = tk.Canvas(
            self, width=44, height=24,
            bg=CARD_BG, highlightthickness=0, cursor="hand2"
        )
        self.canvas.pack(side="left", padx=(0, 12))
        
        self.label = tk.Label(
            self, text=text, bg=CARD_BG, fg=TEXT_PRIMARY,
            font=("Segoe UI", 10), cursor="hand2"
        )
        self.label.pack(side="left")
        
        self._draw()
        
        for widget in [self, self.canvas, self.label]:
            widget.bind("<Button-1>", self._toggle)
        
        self.variable.trace_add("write", lambda *a: self._draw())
    
    def _draw(self):
        self.canvas.delete("all")
        is_on = self.variable.get()
        
        track_color = ACCENT if is_on else "#374151"
        self.canvas.create_rectangle(2, 4, 42, 20, fill=track_color, outline="", width=0)
        self.canvas.create_oval(0, 2, 16, 22, fill=track_color, outline="")
        self.canvas.create_oval(28, 2, 44, 22, fill=track_color, outline="")
        
        thumb_x = 26 if is_on else 4
        self.canvas.create_oval(thumb_x, 4, thumb_x + 16, 20, fill="white", outline="")
    
    def _toggle(self, e=None):
        self.variable.set(not self.variable.get())
        if self.command:
            self.command()


class Card(tk.Frame):
    def __init__(self, parent, title=None, **kwargs):
        super().__init__(parent, bg=CARD_BG, **kwargs)
        
        if title:
            tk.Label(
                self, text=title, bg=CARD_BG, fg=TEXT_PRIMARY,
                font=("Segoe UI", 11, "bold")
            ).pack(anchor="w", padx=18, pady=(14, 8))
        
        self.body = tk.Frame(self, bg=CARD_BG)
        self.body.pack(fill="both", expand=True, padx=18, pady=(0, 14))


class ScrollableFrame(tk.Frame):
    """Scrollable frame with mousewheel support"""
    
    def __init__(self, parent, **kwargs):
        super().__init__(parent, bg=APP_BG, **kwargs)
        
        # Canvas + Scrollbar
        self.canvas = tk.Canvas(
            self, bg=APP_BG, highlightthickness=0, borderwidth=0
        )
        self.scrollbar = ttk.Scrollbar(
            self, orient="vertical", command=self.canvas.yview
        )
        
        # Inner frame that holds the actual content
        self.inner = tk.Frame(self.canvas, bg=APP_BG)
        
        # Place inner frame in canvas
        self.window_id = self.canvas.create_window(
            (0, 0), window=self.inner, anchor="nw"
        )
        
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        
        # Pack
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        
        # Update scrollregion when inner frame changes
        self.inner.bind("<Configure>", self._on_inner_configure)
        # Resize inner frame when canvas is resized
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        
        # Mouse wheel binding
        self._bind_mousewheel()
    
    def _on_inner_configure(self, event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
    
    def _on_canvas_configure(self, event):
        # Make inner frame fill canvas width
        self.canvas.itemconfig(self.window_id, width=event.width)
    
    def _bind_mousewheel(self):
        # Bind to all child widgets recursively
        def _on_mousewheel(event):
            if IS_WINDOWS or IS_MAC:
                self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            else:
                self.canvas.yview_scroll(int(-1 * event.delta), "units")
        
        def _on_button4(event):
            self.canvas.yview_scroll(-1, "units")
        
        def _on_button5(event):
            self.canvas.yview_scroll(1, "units")
        
        # Bind mousewheel when entering, unbind when leaving
        def _bind_to(widget):
            widget.bind("<Enter>", lambda e: self._activate_scroll())
            widget.bind("<Leave>", lambda e: self._deactivate_scroll())
        
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
# MAIN APP
# =========================================================

class VideoGeneratorApp:
    
    def __init__(self, root):
        self.root = root
        self.root.title("Video Generator Pro")
        self.root.geometry("1280x820")
        self.root.minsize(1100, 650)
        self.root.configure(bg=APP_BG)
        
        self._setup_styles()
        self._setup_variables()
        self._build_ui()
        
        self.ui_queue = queue.Queue()
        self.root.after(50, self._process_ui_queue)
    
    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        
        style.configure(
            "Modern.TCombobox",
            fieldbackground=INPUT_BG,
            background=INPUT_BG,
            foreground=TEXT_PRIMARY,
            arrowcolor=TEXT_SECONDARY,
            borderwidth=0,
            relief="flat"
        )
        style.map(
            "Modern.TCombobox",
            fieldbackground=[("readonly", INPUT_BG)],
            selectbackground=[("readonly", INPUT_BG)],
            selectforeground=[("readonly", TEXT_PRIMARY)]
        )
        
        style.configure(
            "Modern.Horizontal.TProgressbar",
            background=ACCENT,
            troughcolor=BORDER_COLOR,
            borderwidth=0,
            lightcolor=ACCENT,
            darkcolor=ACCENT
        )
        
        # Scrollbar styles
        style.configure(
            "Vertical.TScrollbar",
            background=BORDER_COLOR,
            troughcolor=APP_BG,
            borderwidth=0,
            arrowcolor=TEXT_SECONDARY,
            gripcount=0
        )
        style.map(
            "Vertical.TScrollbar",
            background=[("active", "#374151"), ("pressed", ACCENT)]
        )
    
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
    
    def _build_ui(self):
        # ============= HEADER =============
        header = tk.Frame(self.root, bg=APP_BG, height=70)
        header.pack(fill="x", padx=24, pady=(20, 0))
        header.pack_propagate(False)
        
        title_frame = tk.Frame(header, bg=APP_BG)
        title_frame.pack(side="left", fill="y")
        
        tk.Label(
            title_frame, text="Video Generator Pro",
            bg=APP_BG, fg=TEXT_PRIMARY,
            font=("Segoe UI", 22, "bold")
        ).pack(anchor="w")
        
        tk.Label(
            title_frame, text=f"{platform.system()} · {BEST_ENCODER} · Stream Copy Pipeline",
            bg=APP_BG, fg=TEXT_SECONDARY,
            font=("Segoe UI", 9)
        ).pack(anchor="w", pady=(2, 0))
        
        # ============= MAIN LAYOUT =============
        main = tk.Frame(self.root, bg=APP_BG)
        main.pack(fill="both", expand=True, padx=24, pady=20)
        
        main.grid_columnconfigure(0, weight=1, minsize=520)
        main.grid_columnconfigure(1, weight=1, minsize=520)
        main.grid_rowconfigure(0, weight=1)
        
        # LEFT COLUMN - WITH SCROLLBAR
        left_wrapper = tk.Frame(main, bg=APP_BG)
        left_wrapper.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        
        self.left_scrollable = ScrollableFrame(left_wrapper)
        self.left_scrollable.pack(fill="both", expand=True)
        
        # RIGHT COLUMN
        right_col = tk.Frame(main, bg=APP_BG)
        right_col.grid(row=0, column=1, sticky="nsew", padx=(12, 0))
        
        self._build_left_column(self.left_scrollable.inner)
        self._build_right_column(right_col)
    
    def _build_left_column(self, parent):
        # === Video Sources ===
        videos_card = Card(parent, "Video nguồn")
        videos_card.pack(fill="x", pady=(0, 12), padx=(0, 4))
        
        list_frame = tk.Frame(videos_card.body, bg=BORDER_COLOR)
        list_frame.pack(fill="x", pady=(0, 10))
        
        list_inner = tk.Frame(list_frame, bg=INPUT_BG)
        list_inner.pack(fill="both", expand=True, padx=1, pady=1)
        
        self.videos_listbox = tk.Listbox(
            list_inner, bg=INPUT_BG, fg=TEXT_PRIMARY,
            selectbackground=ACCENT, selectforeground=TEXT_PRIMARY,
            font=("Segoe UI", 9), height=5,
            relief="flat", borderwidth=0,
            activestyle="none", selectmode="extended"
        )
        self.videos_listbox.pack(side="left", fill="both", expand=True)
        
        list_scroll = tk.Scrollbar(list_inner, command=self.videos_listbox.yview)
        list_scroll.pack(side="right", fill="y")
        self.videos_listbox.config(yscrollcommand=list_scroll.set)
        
        vbtn_frame = tk.Frame(videos_card.body, bg=CARD_BG)
        vbtn_frame.pack(fill="x")
        
        FlatButton(
            vbtn_frame, "+ Thêm video", command=self.add_videos,
            width=130, height=36
        ).pack(side="left", padx=(0, 8))
        
        FlatButton(
            vbtn_frame, "Xóa đã chọn", command=self.remove_selected_videos,
            bg="#374151", hover_bg="#4B5563", pressed_bg="#1F2937",
            width=130, height=36
        ).pack(side="left", padx=(0, 8))
        
        FlatButton(
            vbtn_frame, "Xóa tất cả", command=self.clear_videos,
            bg=DANGER, hover_bg=DANGER_HOVER, pressed_bg="#B91C1C",
            width=110, height=36
        ).pack(side="left")
        
        self.video_count_label = tk.Label(
            vbtn_frame, text="0 video", bg=CARD_BG, fg=TEXT_MUTED,
            font=("Segoe UI", 9)
        )
        self.video_count_label.pack(side="right")
        
        # === Image Folder ===
        images_card = Card(parent, "Thư mục ảnh")
        images_card.pack(fill="x", pady=(0, 12), padx=(0, 4))
        
        img_input_frame = tk.Frame(images_card.body, bg=BORDER_COLOR)
        img_input_frame.pack(fill="x")
        
        img_inner = tk.Frame(img_input_frame, bg=INPUT_BG)
        img_inner.pack(fill="x", padx=1, pady=1)
        
        self.img_entry = tk.Entry(
            img_inner, textvariable=self.image_folder,
            bg=INPUT_BG, fg=TEXT_PRIMARY,
            insertbackground=TEXT_PRIMARY,
            font=("Segoe UI", 10), relief="flat", borderwidth=0
        )
        self.img_entry.pack(side="left", fill="x", expand=True, padx=12, pady=10)
        
        FlatButton(
            img_inner, "Chọn", command=self.select_images,
            width=80, height=36, padx=10
        ).pack(side="right", padx=2, pady=2)
        
        # === Configuration ===
        config_card = Card(parent, "Cấu hình")
        config_card.pack(fill="x", pady=(0, 12), padx=(0, 4))
        
        tk.Label(
            config_card.body, text="Định dạng video",
            bg=CARD_BG, fg=TEXT_SECONDARY, font=("Segoe UI", 9)
        ).pack(anchor="w", pady=(0, 5))
        
        format_frame = tk.Frame(config_card.body, bg=BORDER_COLOR)
        format_frame.pack(fill="x", pady=(0, 14))
        
        format_inner = tk.Frame(format_frame, bg=INPUT_BG)
        format_inner.pack(fill="x", padx=1, pady=1)
        
        self.format_combo = ttk.Combobox(
            format_inner, textvariable=self.preset,
            values=list(VIDEO_PRESETS.keys()),
            state="readonly", font=("Segoe UI", 10),
            style="Modern.TCombobox"
        )
        self.format_combo.pack(fill="x", padx=10, pady=8)
        
        params_grid = tk.Frame(config_card.body, bg=CARD_BG)
        params_grid.pack(fill="x")
        params_grid.grid_columnconfigure(0, weight=1)
        params_grid.grid_columnconfigure(1, weight=1)
        
        self._make_number_input(params_grid, "Độ dài đoạn (giây)", self.segment_seconds, 0, 0)
        self._make_number_input(params_grid, "Số luồng xử lý", self.max_workers, 0, 1)
        self._make_number_input(params_grid, "Hiệu ứng (giây)", self.transition_duration, 1, 0, is_float=True)
        
        # === Options ===
        options_card = Card(parent, "Tùy chọn")
        options_card.pack(fill="x", pady=(0, 12), padx=(0, 4))
        
        ToggleSwitch(
            options_card.body, "Trộn ngẫu nhiên thứ tự audio",
            self.random_order
        ).pack(anchor="w", pady=6, fill="x")
        
        ToggleSwitch(
            options_card.body, "Giữ nguyên đoạn audio đầu tiên",
            self.keep_first_audio
        ).pack(anchor="w", pady=6, fill="x")
        
        ToggleSwitch(
            options_card.body, "Hiệu ứng chuyển ảnh mượt (fade)",
            self.use_transition
        ).pack(anchor="w", pady=6, fill="x")
        
        ToggleSwitch(
            options_card.body, "Tự động xóa file tạm sau khi hoàn thành",
            self.cleanup_temp
        ).pack(anchor="w", pady=6, fill="x")
        
        # === Generate Button ===
        self.generate_btn = FlatButton(
            parent, "TẠO VIDEO", command=self.start_generation,
            height=52, font=("Segoe UI", 12, "bold")
        )
        self.generate_btn.pack(fill="x", pady=(8, 16), padx=(0, 4))
    
    def _build_right_column(self, parent):
        progress_card = Card(parent, "Tiến trình")
        progress_card.pack(fill="x", pady=(0, 12))
        
        self.status_label = tk.Label(
            progress_card.body, text="Sẵn sàng",
            bg=CARD_BG, fg=TEXT_PRIMARY,
            font=("Segoe UI", 11, "bold"), anchor="w"
        )
        self.status_label.pack(fill="x", pady=(0, 4))
        
        self.detail_label = tk.Label(
            progress_card.body, text="Hãy thêm video và ảnh để bắt đầu",
            bg=CARD_BG, fg=TEXT_SECONDARY,
            font=("Segoe UI", 9), anchor="w"
        )
        self.detail_label.pack(fill="x", pady=(0, 10))
        
        self.progress = ttk.Progressbar(
            progress_card.body, mode="determinate",
            style="Modern.Horizontal.TProgressbar"
        )
        self.progress.pack(fill="x", pady=(0, 8))
        
        stats_frame = tk.Frame(progress_card.body, bg=CARD_BG)
        stats_frame.pack(fill="x")
        
        self.time_label = tk.Label(
            stats_frame, text="Thời gian: 0s",
            bg=CARD_BG, fg=TEXT_MUTED, font=("Segoe UI", 9)
        )
        self.time_label.pack(side="left")
        
        self.percent_label = tk.Label(
            stats_frame, text="0%",
            bg=CARD_BG, fg=TEXT_MUTED, font=("Segoe UI", 9, "bold")
        )
        self.percent_label.pack(side="right")
        
        log_card = Card(parent, "Nhật ký xử lý")
        log_card.pack(fill="both", expand=True)
        
        log_container = tk.Frame(log_card.body, bg=BORDER_COLOR)
        log_container.pack(fill="both", expand=True)
        
        log_inner = tk.Frame(log_container, bg=INPUT_BG)
        log_inner.pack(fill="both", expand=True, padx=1, pady=1)
        
        self.log_box = tk.Text(
            log_inner, bg=INPUT_BG, fg=TEXT_PRIMARY,
            insertbackground=TEXT_PRIMARY,
            font=("Consolas", 9), relief="flat", borderwidth=0,
            wrap="word", padx=12, pady=10
        )
        self.log_box.pack(side="left", fill="both", expand=True)
        
        log_scroll = tk.Scrollbar(log_inner, command=self.log_box.yview)
        log_scroll.pack(side="right", fill="y")
        self.log_box.config(yscrollcommand=log_scroll.set)
        
        self.log_box.tag_configure("success", foreground=SUCCESS)
        self.log_box.tag_configure("error", foreground=DANGER)
        self.log_box.tag_configure("info", foreground=ACCENT)
        self.log_box.tag_configure("muted", foreground=TEXT_MUTED)
        self.log_box.tag_configure("bold", font=("Consolas", 9, "bold"))
    
    def _make_number_input(self, parent, label, variable, row, col, is_float=False):
        frame = tk.Frame(parent, bg=CARD_BG)
        frame.grid(row=row, column=col, sticky="ew",
                   padx=(0 if col == 0 else 6, 6 if col == 0 else 0), pady=4)
        
        tk.Label(
            frame, text=label, bg=CARD_BG, fg=TEXT_SECONDARY,
            font=("Segoe UI", 9)
        ).pack(anchor="w", pady=(0, 4))
        
        input_frame = tk.Frame(frame, bg=BORDER_COLOR)
        input_frame.pack(fill="x")
        
        input_inner = tk.Frame(input_frame, bg=INPUT_BG)
        input_inner.pack(fill="x", padx=1, pady=1)
        
        tk.Entry(
            input_inner, textvariable=variable,
            bg=INPUT_BG, fg=TEXT_PRIMARY,
            insertbackground=TEXT_PRIMARY,
            font=("Segoe UI", 10), relief="flat", borderwidth=0,
            justify="center"
        ).pack(fill="x", padx=10, pady=8)
    
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
            self.root.after(50, self._process_ui_queue)
    
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
                self.percent_label.config(text=f"{int(progress)}%")
        self.queue_ui(_update)
    
    def update_time(self, seconds):
        def _update():
            self.time_label.config(text=f"Thời gian: {seconds:.1f}s")
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
    # CLEANUP
    # =========================================================
    
    def cleanup_temp_folders(self):
        folders = [CACHE_IMAGE_FOLDER, CACHE_AUDIO_FOLDER, CACHE_VIDEO_FOLDER, TEMP_FOLDER]
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
            
            preset_config = VIDEO_PRESETS[self.preset.get()]
            width = preset_config["width"]
            height = preset_config["height"]
            fps = preset_config["fps"]
            segment_seconds = self.segment_seconds.get()
            max_workers = self.max_workers.get()
            transition_dur = self.transition_duration.get()
            use_transition = self.use_transition.get()
            
            self.log("=" * 60, "muted")
            self.log("BẮT ĐẦU TẠO VIDEO", "bold")
            self.log("=" * 60, "muted")
            self.log(f"Định dạng: {self.preset.get()} ({width}x{height} @ {fps}fps)", "info")
            self.log(f"Số video: {len(self.video_paths)}")
            self.log(f"Hiệu ứng: {'Có (fade ' + str(transition_dur) + 's)' if use_transition else 'Không'}")
            self.log(f"Encoder: {BEST_ENCODER}")
            self.log("")
            
            # STEP 1: Clean
            t0 = time.time()
            self.update_status("Bước 1/6: Dọn dẹp", "Xóa file tạm cũ...", 2)
            
            for folder in [CACHE_IMAGE_FOLDER, CACHE_AUDIO_FOLDER, CACHE_VIDEO_FOLDER, TEMP_FOLDER]:
                if os.path.exists(folder):
                    shutil.rmtree(folder)
                os.makedirs(folder)
            
            self.log(f"[1/6] Dọn dẹp xong ({time.time()-t0:.1f}s)", "success")
            
            # STEP 2: Analyze
            t0 = time.time()
            self.update_status("Bước 2/6: Phân tích video", "Đọc thông tin video...", 5)
            
            audio_tasks = []
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
                    
                    audio_tasks.append((audio_index, video_path, seg_start, seg_duration))
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
                cache_path = os.path.join(CACHE_IMAGE_FOLDER, f"img_{idx:04d}.jpg")
                cached_images.append(cache_path)
                image_tasks.append((image, cache_path, width, height))
            
            self.log(f"[2/6] Tổng: {len(audio_tasks)} đoạn audio + {len(image_tasks)} ảnh ({time.time()-t0:.1f}s)", "success")
            
            # STEP 3: Extract audio + cache images
            t0 = time.time()
            self.update_status("Bước 3/6: Xử lý audio + ảnh", "Trích xuất audio và resize ảnh...", 10)
            
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
                    pct = 10 + (completed / total_assets) * 25
                    self.update_status(
                        "Bước 3/6: Xử lý audio + ảnh",
                        f"Đã xử lý {completed}/{total_assets}", pct
                    )
            
            self.log(f"[3/6] Audio: {len(audio_info)}/{len(audio_tasks)}, Ảnh: {len(cached_images)} ({time.time()-t0:.1f}s)", "success")
            
            if not audio_info:
                raise ValueError("Không trích xuất được audio")
            
            # STEP 4: Pre-encode images
            t0 = time.time()
            self.update_status("Bước 4/6: Mã hóa ảnh", "Chuyển ảnh thành video clip...", 35)
            
            video_image_tasks = []
            cached_image_videos = []
            
            for idx, img_path in enumerate(cached_images):
                video_path = os.path.join(CACHE_VIDEO_FOLDER, f"imgvid_{idx:04d}.mp4")
                cached_image_videos.append(video_path)
                video_image_tasks.append((img_path, video_path, width, height, fps))
            
            with ProcessPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(create_image_video, task) for task in video_image_tasks]
                completed = 0
                total = len(futures)
                
                for future in as_completed(futures):
                    returncode, vpath, stderr = future.result()
                    completed += 1
                    pct = 35 + (completed / total) * 25
                    self.update_status(
                        "Bước 4/6: Mã hóa ảnh",
                        f"Đã mã hóa {completed}/{total} ảnh", pct
                    )
            
            valid_image_videos = [v for v in cached_image_videos if os.path.exists(v) and os.path.getsize(v) > 0]
            
            if not valid_image_videos:
                raise ValueError("Không tạo được video từ ảnh")
            
            self.log(f"[4/6] Mã hóa {len(valid_image_videos)} clip ảnh ({time.time()-t0:.1f}s)", "success")
            
            # STEP 5: Build pairs + assemble
            t0 = time.time()
            self.update_status("Bước 5/6: Ghép video", "Tạo các đoạn video...", 60)
            
            sorted_indices = sorted(audio_info.keys())
            ordered_audio = [(idx, audio_info[idx][0], audio_info[idx][1]) for idx in sorted_indices]
            
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
            
            render_tasks = []
            for new_idx, (orig_idx, audio_path, duration) in enumerate(final_audio_order):
                image_video = random.choice(valid_image_videos)
                if use_transition:
                    render_tasks.append((new_idx, image_video, audio_path, duration, fps, transition_dur))
                else:
                    render_tasks.append((new_idx, image_video, audio_path, duration, fps))
            
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
                    pct = 60 + (completed / total) * 30
                    self.update_status(
                        "Bước 5/6: Ghép video",
                        f"Đã ghép {completed}/{total} đoạn", pct
                    )
            
            self.log(f"[5/6] Ghép {len(results)}/{total} đoạn ({time.time()-t0:.1f}s)", "success")
            
            if not results:
                raise ValueError("Không ghép được video")
            
            # STEP 6: Final concat
            t0 = time.time()
            self.update_status("Bước 6/6: Hoàn thiện", "Ghép video cuối...", 92)
            
            sorted_results = [results[idx] for idx in sorted(results.keys())]
            
            concat_file = os.path.join(TEMP_FOLDER, "concat.txt")
            with open(concat_file, "w", encoding="utf-8") as f:
                for video in sorted_results:
                    abs_path = os.path.abspath(video)
                    normalized = normalize_path(abs_path)
                    f.write(f"file '{normalized}'\n")
            
            merge_cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "concat", "-safe", "0",
                "-i", concat_file,
                "-c", "copy",
                "-movflags", "+faststart",
                OUTPUT_VIDEO
            ]
            
            result = subprocess.run(
                merge_cmd, stderr=subprocess.PIPE, text=True,
                creationflags=SUBPROCESS_FLAGS
            )
            
            if result.returncode != 0:
                raise Exception(f"Lỗi ghép video: {result.stderr}")
            
            self.log(f"[6/6] Hoàn thiện ({time.time()-t0:.1f}s)", "success")
            
            if self.cleanup_temp.get():
                self.update_status("Dọn dẹp", "Xóa file tạm...", 98)
                self.cleanup_temp_folders()
            
            stop_timer.set()
            
            output_duration = get_media_duration(OUTPUT_VIDEO)
            file_size = os.path.getsize(OUTPUT_VIDEO) / (1024 * 1024)
            total_time = time.time() - start_time
            
            self.update_status(
                "Hoàn thành!",
                f"Đã tạo: {OUTPUT_VIDEO} ({file_size:.1f} MB)",
                100
            )
            
            self.log("")
            self.log("=" * 60, "muted")
            self.log("HOÀN THÀNH", "success")
            self.log("=" * 60, "muted")
            self.log(f"Thời gian: {total_time:.1f}s", "info")
            self.log(f"Đầu ra: {os.path.abspath(OUTPUT_VIDEO)}")
            self.log(f"Kích thước: {file_size:.2f} MB")
            self.log(f"Đầu vào: {total_input_duration:.2f}s | Đầu ra: {output_duration:.2f}s")
            self.log(f"Tốc độ: {total_input_duration/total_time:.1f}x realtime", "success")
            
            self.queue_ui(lambda: messagebox.showinfo(
                "Thành công",
                f"Đã tạo video trong {total_time:.1f} giây!\n\n"
                f"File: {OUTPUT_VIDEO}\n"
                f"Kích thước: {file_size:.2f} MB\n"
                f"Thời lượng: {output_duration:.1f}s\n"
                f"Tốc độ: {total_input_duration/total_time:.1f}x realtime"
            ))
            
        except Exception as e:
            self.log(f"\nLỖI: {str(e)}", "error")
            self.update_status("Có lỗi xảy ra", str(e), 0)
            self.queue_ui(lambda: messagebox.showerror("Lỗi", str(e)))
        finally:
            if stop_timer:
                stop_timer.set()
            self.queue_ui(lambda: self.generate_btn.set_state("normal"))
            self.queue_ui(lambda: self.generate_btn.set_text("TẠO VIDEO"))


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    if IS_WINDOWS:
        from multiprocessing import freeze_support
        freeze_support()
    
    root = tk.Tk()
    app = VideoGeneratorApp(root)
    root.mainloop()