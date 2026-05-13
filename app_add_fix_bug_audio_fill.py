import os
import tempfile
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

from tkinter import ttk, filedialog, messagebox, colorchooser
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from PIL import Image, ImageTk

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

DEMUCS_AVAILABLE = False
try:
    import demucs
    DEMUCS_AVAILABLE = True
except ImportError:
    pass

YT_DLP_AVAILABLE = False
try:
    import yt_dlp
    YT_DLP_AVAILABLE = True
except ImportError:
    pass

BING_DL_AVAILABLE = False
try:
    import bing_image_downloader  # noqa: F401
    BING_DL_AVAILABLE = True
except ImportError:
    pass

# =========================================================
# UTILS
# =========================================================

def _center_window(win, parent=None):
    """Căn giữa Toplevel theo parent hoặc màn hình."""
    win.update_idletasks()
    w = win.winfo_width()
    h = win.winfo_height()
    if parent and parent.winfo_exists():
        px = parent.winfo_rootx()
        py = parent.winfo_rooty()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
    else:
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
    win.geometry(f"+{max(0, x)}+{max(0, y)}")

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
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5,
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
    def _probe(entries):
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", entries,
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=SUBPROCESS_FLAGS
        )
        vals = [float(x) for x in r.stdout.strip().splitlines() if x.strip() and x.strip() != "N/A"]
        return max(vals) if vals else 0.0

    # Format duration có thể sai với MP3 VBR, MKV ghép stream → lấy max của format và stream
    fmt_dur = _probe("format=duration")
    stream_dur = _probe("stream=duration")
    dur = max(fmt_dur, stream_dur)
    if dur > 0:
        return dur
    raise ValueError(f"Không đọc được duration: {path}")

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
        text=True, encoding="utf-8", errors="replace",
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
        text=True, encoding="utf-8", errors="replace",
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
                probe_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
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
        text=True, encoding="utf-8", errors="replace",
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
        text=True, encoding="utf-8", errors="replace",
        creationflags=SUBPROCESS_FLAGS
    )
    return (index, result.returncode, output_file, result.stderr)

def process_segment_sub_pip(args):
    """
    Thứ tự render: PiP trước → subtitle sau
    Subtitle luôn nằm trên cùng, không bị PiP che.
    """
    (pos_i, input_seg, ass_p,
     o_paths, o_pos, o_sizes, out_p, o_fps, o_enc) = args

    cur = input_seg

    # Không có gì cả: stream copy nhanh
    if ass_p is None and not o_paths:
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
               "-i", cur, "-c", "copy", out_p]
        subprocess.run(cmd, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL,
                       creationflags=SUBPROCESS_FLAGS)
        return (pos_i, True, out_p, "")

    # Pass 1: apply PiP trước (layer dưới)
    if o_paths:
        pip_out = out_p.replace(".mp4", "_pip.mp4")
        _, ok, _, err = apply_pip_to_segment(
            (pos_i, cur, o_paths, o_pos, o_sizes, pip_out, o_fps, o_enc)
        )
        if ok and os.path.exists(pip_out) and os.path.getsize(pip_out) > 0:
            cur = pip_out
        # Nếu PiP lỗi, tiếp tục với cur gốc

    # Pass 2: burn subtitle lên trên cùng (layer trên PiP)
    if ass_p is not None:
        sub_out = out_p.replace(".mp4", "_sub.mp4")
        ok, _ = burn_subtitle_segment_task(
            (pos_i, cur, ass_p, sub_out, o_fps, o_enc)
        )
        if ok and os.path.exists(sub_out) and os.path.getsize(sub_out) > 0:
            cur = sub_out

    # Copy kết quả cuối → out_p
    if cur != out_p:
        try:
            shutil.copy2(cur, out_p)
        except Exception as e:
            return (pos_i, False, out_p, str(e))
    return (pos_i, True, out_p, "")


def _find_drawtext_font():
    """Tìm font hỗ trợ Unicode/tiếng Việt cho ffmpeg drawtext."""
    candidates = (
        ["/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
         "/System/Library/Fonts/Helvetica.ttc",
         "/Library/Fonts/Arial Unicode.ttf"]
        if IS_MAC else
        ["C:/Windows/Fonts/arial.ttf",
         "C:/Windows/Fonts/segoeui.ttf",
         "C:/Windows/Fonts/tahoma.ttf"]
        if IS_WINDOWS else
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"]
    )
    for f in candidates:
        if os.path.exists(f):
            return f
    return None


def _esc_drawtext(text):
    """Escape text cho ffmpeg drawtext filter."""
    return (text.replace("\\", "\\\\")
                .replace("'",  "\\'")
                .replace(":",  "\\:")
                .replace("%",  "\\%"))


def apply_text_banners(input_video, output_video, banners, main_duration,
                        fps, best_encoder, font_path=None,
                        progress_callback=None, stop_event=None):
    """
    Chèn banner top/bottom vào NGOÀI khung hình video (không đè lên video).
    Video được scale nhỏ lại để nhường chỗ cho banner, tổng kích thước giữ nguyên.
    banners: list of dict {position, text, fontsize, textcolor, bgcolor, bgopacity}
    """
    active = [b for b in banners if b.get("text", "").strip()]
    if not active:
        return True, ""

    # Tính chiều cao mỗi banner
    banner_top = next((b for b in active if b.get("position","top") == "top"), None)
    banner_bot = next((b for b in active if b.get("position","top") == "bottom"), None)

    def _bh(b):
        return int(max(8, int(b.get("fontsize", 48))) * 2.2) if b else 0

    top_h = _bh(banner_top)
    bot_h = _bh(banner_bot)

    if font_path:
        fp_esc   = font_path.replace("\\", "/").replace(":", "\\:")
        font_opt = f":fontfile='{fp_esc}'"
    else:
        font_opt = ""

    # ── Xây dựng filtergraph ────────────────────────────────────────────
    # Bước 1: scale video xuống để vừa khu vực giữa (giữ nguyên width, giảm height)
    # out_h = iw*ih/iw = ih nhưng trừ banner → video_h = ih - top_h - bot_h
    # Dùng iw × (ih-top_h-bot_h) rồi pad lại thành iw × ih
    video_h_expr = f"iw*(ih-{top_h}-{bot_h})/iw"   # = ih - top_h - bot_h

    # Scale video vừa khít khu vực giữa, giữ aspect ratio
    scale_f = (
        f"[0:v]scale=iw:{top_h + bot_h}*-1+"
        f"ih:flags=lanczos,setsar=1[vid_scaled]"
    )
    # Đơn giản hơn: scale trực tiếp width=iw, height=ih-top_h-bot_h
    scale_f = f"[0:v]scale=iw:ih-{top_h}-{bot_h}:flags=lanczos,setsar=1[vid_scaled]"

    parts = [scale_f]
    stack_inputs = []

    # Bước 2: tạo banner top (nếu có)
    if banner_top:
        fs  = max(8, int(banner_top.get("fontsize", 48)))
        tc  = banner_top.get("textcolor", "#FFFFFF").replace("#", "0x")
        bc  = banner_top.get("bgcolor",   "#000000").replace("#", "")
        opa = max(0.0, min(1.0, float(banner_top.get("bgopacity", 0.7))))
        txt = _esc_drawtext(banner_top["text"].strip())
        # Nền màu + chữ
        parts.append(
            f"color=c=#{bc}@{opa:.2f}:s=iw_main_x{top_h},"
            f"color=c=#{bc}:s=1x1,scale=iw_in:iw_in[bg_top_dummy]"
        )
        # Dùng cách đúng hơn: color filter rồi drawtext
        parts[-1] = (
            f"[0:v]scale=iw:1:flags=neighbor[w_ref];"   # lấy width từ video
            f"[w_ref]crop=iw:1:0:0,scale=iw:{top_h},drawtext="
            f"text='{txt}':fontsize={fs}:fontcolor={tc}{font_opt}"
            f":x=(w-text_w)/2:y=(h-text_h)/2,"
            f"drawbox=x=0:y=0:w=iw:h={top_h}:color=#{bc}@{opa:.2f}:t=fill,"
            f"drawtext=text='{txt}':fontsize={fs}:fontcolor={tc}{font_opt}"
            f":x=(w-text_w)/2:y=(h-text_h)/2"
            f"[banner_top]"
        )
        stack_inputs.append("[banner_top]")

    stack_inputs.append("[vid_scaled]")

    if banner_bot:
        fs  = max(8, int(banner_bot.get("fontsize", 48)))
        tc  = banner_bot.get("textcolor", "#FFFFFF").replace("#", "0x")
        bc  = banner_bot.get("bgcolor",   "#000000").replace("#", "")
        opa = max(0.0, min(1.0, float(banner_bot.get("bgopacity", 0.7))))
        txt = _esc_drawtext(banner_bot["text"].strip())
        parts.append(
            f"[0:v]scale=iw:1:flags=neighbor[w_ref2];"
            f"[w_ref2]crop=iw:1:0:0,scale=iw:{bot_h},drawbox="
            f"x=0:y=0:w=iw:h={bot_h}:color=#{bc}@{opa:.2f}:t=fill,"
            f"drawtext=text='{txt}':fontsize={fs}:fontcolor={tc}{font_opt}"
            f":x=(w-text_w)/2:y=(h-text_h)/2"
            f"[banner_bot]"
        )
        stack_inputs.append("[banner_bot]")

    # Bước 3: stack dọc: banner_top + video + banner_bot
    n = len(stack_inputs)
    stack_f = f"{''.join(stack_inputs)}vstack=inputs={n}[vout]"

    # Ghép tất cả thành một filtergraph đơn giản, rõ ràng
    # Dùng cách khác — pad approach: scale video nhỏ xuống, pad lại đúng size
    # Top banner: vẽ lên vùng pad trên; bot banner: vẽ lên vùng pad dưới

    # ── Cách đơn giản & đáng tin cậy nhất: scale + pad + drawbox/drawtext ──
    filter_parts = []

    # 1. Scale video xuống để fit khu vực giữa
    filter_parts.append(
        f"scale=iw:ih-{top_h}-{bot_h}:flags=lanczos"
    )
    # 2. Pad lại đúng kích thước gốc, video ở giữa (offset y = top_h)
    filter_parts.append(
        f"pad=iw:ih+{top_h}+{bot_h}:0:{top_h}:color=black"
    )

    # 3. Vẽ nền + chữ banner top
    if banner_top:
        fs  = max(8, int(banner_top.get("fontsize", 48)))
        tc  = banner_top.get("textcolor", "#FFFFFF").replace("#", "0x")
        bc  = banner_top.get("bgcolor",   "#000000").replace("#", "0x")
        opa = max(0.0, min(1.0, float(banner_top.get("bgopacity", 0.7))))
        txt = _esc_drawtext(banner_top["text"].strip())
        filter_parts.append(
            f"drawbox=x=0:y=0:w=iw:h={top_h}:color={bc}@{opa:.2f}:t=fill"
        )
        filter_parts.append(
            f"drawtext=text='{txt}':fontsize={fs}:fontcolor={tc}{font_opt}"
            f":x=(W-text_w)/2:y=({top_h}-text_h)/2"
        )

    # 4. Vẽ nền + chữ banner bot
    if banner_bot:
        fs  = max(8, int(banner_bot.get("fontsize", 48)))
        tc  = banner_bot.get("textcolor", "#FFFFFF").replace("#", "0x")
        bc  = banner_bot.get("bgcolor",   "#000000").replace("#", "0x")
        opa = max(0.0, min(1.0, float(banner_bot.get("bgopacity", 0.7))))
        txt = _esc_drawtext(banner_bot["text"].strip())
        filter_parts.append(
            f"drawbox=x=0:y=ih-{bot_h}:w=iw:h={bot_h}:color={bc}@{opa:.2f}:t=fill"
        )
        filter_parts.append(
            f"drawtext=text='{txt}':fontsize={fs}:fontcolor={tc}{font_opt}"
            f":x=(W-text_w)/2:y=H-{bot_h}+({bot_h}-text_h)/2"
        )

    encoder_args = get_encoder_args(best_encoder, fps)
    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-threads", "0",
        "-progress", "pipe:1", "-stats_period", "1",
        "-i", input_video,
        "-vf", ",".join(filter_parts),
        *encoder_args,
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        "-c:a", "copy",
        "-t", str(main_duration),
        "-movflags", "+faststart",
        output_video
    ]

    proc = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", creationflags=SUBPROCESS_FLAGS
    )
    for line in proc.stdout:
        if stop_event is not None and stop_event.is_set():
            proc.terminate()
            break
        if line.startswith("out_time_us=") and main_duration > 0 and progress_callback:
            try:
                us = int(line.strip().split("=")[1])
                progress_callback(min(us / 1_000_000 / main_duration, 1.0))
            except Exception:
                pass
    proc.wait()
    return proc.returncode == 0, proc.stderr.read()


def _apply_pip_segment_progress(input_path, overlay_paths, overlay_positions,
                                 overlay_size_pcts, output_path, fps, best_encoder,
                                 progress_cb=None):
    """Apply PiP lên 1 segment với progress callback realtime."""
    n = len(overlay_paths)
    if n == 0:
        import shutil
        shutil.copy2(input_path, output_path)
        return True, output_path, ""

    margin = 20
    pos_map = {
        "top-left":     (margin, margin),
        "top-right":    (f"W-w-{margin}", margin),
        "center":       ("(W-w)/2", "(H-h)/2"),
        "bottom-left":  (margin, f"H-h-{margin}"),
        "bottom-right": (f"W-w-{margin}", f"H-h-{margin}"),
    }

    try:
        dur_res = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", input_path],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10, creationflags=SUBPROCESS_FLAGS
        )
        seg_dur = float(dur_res.stdout.strip())
    except Exception:
        seg_dur = 0

    input_args = ["-i", input_path]
    for opath in overlay_paths:
        input_args += ["-stream_loop", "-1", "-i", opath]

    filter_parts = []
    for i, ow in enumerate(overlay_size_pcts):
        ow = max(2, int(ow) // 2 * 2)
        filter_parts.append(f"[{i+1}:v]scale={ow}:-2[pip{i}]")
    current = "0:v"
    for i, pos in enumerate(overlay_positions):
        x, y = pos_map.get(pos, (margin, margin))
        out = "vfinal" if i == n - 1 else f"vtmp{i}"
        filter_parts.append(f"[{current}][pip{i}]overlay={x}:{y}[{out}]")
        current = out

    pip_encoder = best_encoder if best_encoder == "h264_videotoolbox" else "libx264"
    encoder_args = get_encoder_args(pip_encoder, fps)

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-nostdin", "-loglevel", "error",
        "-progress", "pipe:1", "-stats_period", "0.5",
        *input_args,
        "-filter_complex", ";".join(filter_parts),
        "-map", "[vfinal]", "-map", "0:a:0",
        *encoder_args,
        "-pix_fmt", "yuv420p", "-r", str(fps), "-c:a", "copy",
        "-shortest",
    ]
    if seg_dur > 0:
        cmd += ["-t", str(seg_dur)]
    cmd.append(output_path)

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, creationflags=SUBPROCESS_FLAGS
        )
        for line in proc.stdout:
            if progress_cb and seg_dur > 0 and line.startswith("out_time_us="):
                try:
                    us = int(line.strip().split("=")[1])
                    progress_cb(min(us / 1_000_000 / seg_dur, 1.0))
                except Exception:
                    pass
        proc.wait()
        err = proc.stderr.read()
        return proc.returncode == 0, output_path, err
    except Exception as ex:
        return False, output_path, str(ex)


def apply_pip_to_segment(args):
    """Apply PiP overlay lên 1 segment ngắn (chạy song song)."""
    (idx, input_path, overlay_paths, overlay_positions,
     overlay_size_pcts, output_path, fps, best_encoder) = args

    n = len(overlay_paths)
    if n == 0:
        return (idx, True, output_path, "")

    margin = 20
    input_args = ["-i", input_path]
    for opath in overlay_paths:
        input_args += ["-stream_loop", "-1", "-i", opath]

    pos_map = {
        "top-left":     (margin, margin),
        "top-right":    (f"W-w-{margin}", margin),
        "center":       ("(W-w)/2", "(H-h)/2"),
        "bottom-left":  (margin, f"H-h-{margin}"),
        "bottom-right": (f"W-w-{margin}", f"H-h-{margin}"),
    }

    filter_parts = []
    for i, ow in enumerate(overlay_size_pcts):
        ow = max(2, int(ow) // 2 * 2)
        filter_parts.append(f"[{i+1}:v]scale={ow}:-2[pip{i}]")

    current = "0:v"
    for i, pos in enumerate(overlay_positions):
        x, y = pos_map.get(pos, (margin, margin))
        out = "vfinal" if i == n - 1 else f"vtmp{i}"
        filter_parts.append(f"[{current}][pip{i}]overlay={x}:{y}[{out}]")
        current = out

    pip_encoder = best_encoder if best_encoder == "h264_videotoolbox" else "libx264"
    encoder_args = get_encoder_args(pip_encoder, fps)
    # Lấy thời lượng segment chính để giới hạn output (tránh loop vô tận)
    try:
        dur_res = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", input_path],
            capture_output=True, text=True, timeout=10,
            creationflags=SUBPROCESS_FLAGS
        )
        seg_dur = float(dur_res.stdout.strip())
    except Exception:
        seg_dur = None

    command = [
        "ffmpeg", "-y", "-hide_banner", "-nostdin", "-loglevel", "error",
        *input_args,
        "-filter_complex", ";".join(filter_parts),
        "-map", "[vfinal]",
        "-map", "0:a:0",
        *encoder_args,
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        "-c:a", "copy",
        "-shortest",
    ]
    if seg_dur:
        command += ["-t", str(seg_dur)]
    command.append(output_path)

    result = subprocess.run(
        command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
        creationflags=SUBPROCESS_FLAGS
    )
    return (idx, result.returncode == 0, output_path, result.stderr)


def apply_pip_overlays(input_video, overlay_paths, overlay_positions,
                        overlay_size_pcts, output_video, main_duration, fps,
                        best_encoder, progress_callback=None, log_callback=None,
                        stop_event=None):
    """PiP overlay với progress realtime. Luôn dùng CPU overlay; Mac giữ videotoolbox encode."""
    n = len(overlay_paths)
    if n == 0:
        return True, ""

    margin = 20
    pos_map = {
        "top-left":     (margin, margin),
        "top-right":    (f"W-w-{margin}", margin),
        "center":       ("(W-w)/2", "(H-h)/2"),
        "bottom-left":  (margin, f"H-h-{margin}"),
        "bottom-right": (f"W-w-{margin}", f"H-h-{margin}"),
    }

    # Mac dùng videotoolbox; Windows dùng libx264 (tránh treo với nvenc/qsv/amf + filter_complex)
    pip_encoder = best_encoder if best_encoder == "h264_videotoolbox" else "libx264"
    encoder_args = get_encoder_args(pip_encoder, fps)

    input_args = ["-i", input_video]
    for opath in overlay_paths:
        input_args += ["-stream_loop", "-1", "-i", opath]

    filter_parts = []
    for i, ow in enumerate(overlay_size_pcts):
        ow = max(2, int(ow) // 2 * 2)
        filter_parts.append(f"[{i+1}:v]scale={ow}:-2[pip{i}]")

    current = "0:v"
    for i, pos in enumerate(overlay_positions):
        x, y = pos_map.get(pos, (margin, margin))
        out = "vfinal" if i == n - 1 else f"vtmp{i}"
        filter_parts.append(f"[{current}][pip{i}]overlay={x}:{y}[{out}]")
        current = out

    command = [
        "ffmpeg", "-y", "-hide_banner", "-nostdin", "-loglevel", "error",
        "-threads", "0",
        "-progress", "pipe:1", "-stats_period", "1",
        *input_args,
        "-filter_complex", ";".join(filter_parts),
        "-map", "[vfinal]",
        "-map", "0:a:0",
        *encoder_args,
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        "-c:a", "copy",
        "-t", str(main_duration),
        "-movflags", "+faststart",
        output_video
    ]

    proc = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
        creationflags=SUBPROCESS_FLAGS
    )

    for line in proc.stdout:
        if stop_event is not None and stop_event.is_set():
            proc.terminate()
            break
        if line.startswith("out_time_us=") and main_duration > 0 and progress_callback:
            try:
                us = int(line.strip().split("=")[1])
                pct = min(us / 1_000_000 / main_duration, 1.0)
                progress_callback(pct)
            except Exception:
                pass

    proc.wait()
    stderr_output = proc.stderr.read()
    return proc.returncode == 0, stderr_output


def burn_subtitle_segment_task(args):
    """
    Burn subtitle vào 1 segment video nhỏ (song song).
    Nếu không có subtitle entry nào cho segment này: stream-copy trực tiếp (không re-encode).
    """
    (new_idx, video_path, ass_path, output_path, fps, best_encoder) = args

    if ass_path is None:
        # Không có phụ đề cho đoạn này — copy stream, cực nhanh
        command = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", video_path,
            "-c", "copy",
            output_path
        ]
        result = subprocess.run(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", creationflags=SUBPROCESS_FLAGS
        )
        return (new_idx, result.returncode == 0, output_path, result.stderr)

    ass_escaped = ass_path.replace("\\", "/").replace(":", "\\:")
    encoder_args = get_encoder_args(best_encoder, fps)

    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", video_path,
        "-vf", f"subtitles='{ass_escaped}'",
        *encoder_args,
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        output_path
    ]
    result = subprocess.run(
        command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", creationflags=SUBPROCESS_FLAGS
    )
    try:
        os.remove(ass_path)
    except Exception:
        pass
    return (new_idx, result.returncode == 0, output_path, result.stderr)


def concat_and_mux_segment(args):
    """
    Concat danh sách clip (đã extract sẵn, cùng codec) + mux audio.
    - 1 clip: mux trực tiếp (không cần concat, nhanh nhất)
    - N clips: concat demuxer (stream-copy) rồi mux
    """
    new_idx, clip_files, audio_path, duration, output_path = args

    if len(clip_files) == 1:
        # Mux trực tiếp — không qua concat, tiết kiệm 1 bước I/O
        command = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", clip_files[0],
            "-i", audio_path,
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "copy",
            "-t", str(duration),
            "-fflags", "+genpts", "-avoid_negative_ts", "make_zero",
            output_path
        ]
        result = subprocess.run(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", creationflags=SUBPROCESS_FLAGS
        )
        return (new_idx, result.returncode, output_path, result.stderr)

    # N clips: concat demuxer (stream-copy) + mux
    list_path = output_path.replace(".mp4", "_clist.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for cp in clip_files:
            f.write(f"file '{normalize_path(os.path.abspath(cp))}'\n")

    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", list_path,
        "-i", audio_path,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "copy",
        "-t", str(duration),
        "-fflags", "+genpts", "-avoid_negative_ts", "make_zero",
        output_path
    ]
    result = subprocess.run(
        command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", creationflags=SUBPROCESS_FLAGS
    )
    try:
        os.remove(list_path)
    except Exception:
        pass
    return (new_idx, result.returncode, output_path, result.stderr)


def extract_video_clip_only(args):
    """Trích clip_sec giây video (không audio) từ source, scale/crop về target size."""
    index, source_video, clip_start, clip_sec, output_path, width, height, fps = args

    scale_filter = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}"
    )
    encoder_args = get_encoder_args(BEST_ENCODER, fps, bitrate="3M")
    # -stream_loop -1: loop vô hạn để clip ngắn hơn seg_dur vẫn đủ độ dài
    # -t clip_sec đặt SAU -i (output option) để cắt đúng duration đầu ra
    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-stream_loop", "-1",
        "-ss", str(clip_start),
        "-i", source_video,
        "-t", str(clip_sec),
        "-vf", scale_filter,
        *encoder_args,
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        "-g", str(fps),
        "-an",
        output_path
    ]
    result = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
        creationflags=SUBPROCESS_FLAGS
    )
    return (index, result.returncode, output_path, result.stderr)


def render_multi_clip_segment(args):
    """
    Trích N clip từ video nguồn (random: N clip khác nhau / mapping: 1 clip liên tục),
    nối tiếp nhau rồi mux với audio. KHÔNG loop lại clip cũ.
    """
    (index, clip_assignments, audio_path, duration, output_path,
     width, height, fps, use_fade, fade_dur) = args
    # clip_assignments: list of (source_video, start_time, clip_sec)

    base_scale = (
        f"setpts=PTS-STARTPTS,"
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}"
    )

    n = len(clip_assignments)

    if n == 1:
        vpath, vstart, vdur = clip_assignments[0]
        if use_fade:
            fo = max(0, duration - fade_dur)
            vf = f"{base_scale},fade=t=in:st=0:d={fade_dur},fade=t=out:st={fo}:d={fade_dur}"
        else:
            vf = base_scale
        command = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", str(vstart), "-t", str(vdur), "-i", vpath,
            "-i", audio_path,
            "-vf", vf,
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-r", str(fps), "-g", str(fps),
            "-c:a", "copy",
            "-t", str(duration), "-shortest",
            "-fflags", "+genpts", "-avoid_negative_ts", "make_zero",
            output_path
        ]
    else:
        # Nhiều clip: mỗi clip extract từ vị trí khác nhau → concat → mux audio
        input_args = []
        for vpath, vstart, vdur in clip_assignments:
            input_args += ["-ss", str(vstart), "-t", str(vdur), "-i", vpath]
        audio_idx = n
        input_args += ["-i", audio_path]

        filter_parts = [f"[{i}:v]{base_scale}[v{i}]" for i in range(n)]
        concat_in = "".join(f"[v{i}]" for i in range(n))
        filter_parts.append(f"{concat_in}concat=n={n}:v=1:a=0[vcat]")

        if use_fade:
            fo = max(0, duration - fade_dur)
            filter_parts.append(
                f"[vcat]fade=t=in:st=0:d={fade_dur},"
                f"fade=t=out:st={fo}:d={fade_dur}[vout]"
            )
            map_v = "[vout]"
        else:
            map_v = "[vcat]"

        command = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            *input_args,
            "-filter_complex", ";".join(filter_parts),
            "-map", map_v,
            "-map", f"{audio_idx}:a:0",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-r", str(fps), "-g", str(fps),
            "-c:a", "copy",
            "-t", str(duration), "-shortest",
            "-fflags", "+genpts", "-avoid_negative_ts", "make_zero",
            output_path
        ]

    result = subprocess.run(
        command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", creationflags=SUBPROCESS_FLAGS
    )
    return (index, result.returncode, output_path, result.stderr)


def render_video_clip_segment(args):
    """Trích đoạn video chuyển động từ source + ghép audio — không loop ảnh tĩnh."""
    index, source_video, clip_start, audio_path, duration, output_path, width, height, fps, use_fade, fade_dur = args

    scale_filter = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}"
    )

    if use_fade:
        fade_out_start = max(0, duration - fade_dur)
        vf = (
            f"{scale_filter},"
            f"fade=t=in:st=0:d={fade_dur},"
            f"fade=t=out:st={fade_out_start}:d={fade_dur}"
        )
    else:
        vf = scale_filter

    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", str(clip_start),
        "-t", str(duration),
        "-i", source_video,
        "-i", audio_path,
        "-vf", vf,
        "-map", "0:v:0",
        "-map", "1:a:0",
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
        output_path
    ]

    result = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
        creationflags=SUBPROCESS_FLAGS
    )
    return (index, result.returncode, output_path, result.stderr)


def render_segment_with_transition(args):
    """Fade đen hoặc trắng per-segment — nhanh, không re-encode sau concat."""
    index, image_video_path, audio_path, duration, fps, transition_duration, fade_color, temp_folder = args
    output_file = os.path.join(temp_folder, f"seg_{index:04d}.mp4")

    loop_count = math.ceil(duration)
    fade_out_start = max(0, duration - transition_duration)
    color = "white" if fade_color == "fade_white" else "black"

    vf = (f"fade=t=in:st=0:d={transition_duration}:color={color},"
          f"fade=t=out:st={fade_out_start}:d={transition_duration}:color={color}")

    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-stream_loop", str(loop_count),
        "-i", image_video_path,
        "-i", audio_path,
        "-map", "0:v:0", "-map", "1:a:0",
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-r", str(fps), "-g", str(fps),
        "-c:a", "copy", "-t", str(duration),
        "-shortest", "-fflags", "+genpts", "-avoid_negative_ts", "make_zero",
        output_file
    ]
    result = subprocess.run(
        command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", creationflags=SUBPROCESS_FLAGS
    )
    return (index, result.returncode, output_file, result.stderr)



def _normalize_segment(src, dst, fps):
    """Re-encode segment về clean libx264/yuv420p để xfade hoạt động đúng."""
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", src,
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-r", str(fps),
        "-c:a", "copy", dst
    ]
    r = subprocess.run(cmd, capture_output=True, creationflags=SUBPROCESS_FLAGS)
    return r.returncode == 0 and os.path.exists(dst)


def apply_xfade_sequential(segments, output_video, xfade_type, xfade_dur,
                            fps, best_encoder, progress_callback=None,
                            log_callback=None):
    """Apply xfade tuần tự từng cặp — đáng tin cậy nhất.
    Re-encode segments về clean format trước để tránh lỗi xfade.
    """
    import shutil as _sh
    if not segments:
        return False, "Không có segment"
    if len(segments) == 1:
        _sh.copy2(segments[0], output_video)
        return True, ""

    n = len(segments)
    tmp_dir = os.path.dirname(output_video)

    # Normalize segment đầu tiên
    norm0 = os.path.join(tmp_dir, "_xf_norm_0.mp4")
    current = norm0 if _normalize_segment(segments[0], norm0, fps) else segments[0]

    for i in range(1, n):
        is_last = (i == n - 1)
        out = output_video if is_last else os.path.join(tmp_dir, f"_xf_tmp_{i}.mp4")

        # Normalize segment tiếp theo
        norm_i = os.path.join(tmp_dir, f"_xf_norm_{i}.mp4")
        if not _normalize_segment(segments[i], norm_i, fps):
            norm_i = segments[i]

        # Duration của current để tính offset
        try:
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", current],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=10, creationflags=SUBPROCESS_FLAGS
            )
            cur_dur = float(probe.stdout.strip())
        except Exception:
            cur_dur = 30.0
        offset = max(0.0, cur_dur - xfade_dur)

        # Random: chọn hiệu ứng khác nhau mỗi pair
        _XFADE_POOL = [
            "slideleft", "slideright", "slideup", "slidedown",
            "wipeleft", "wiperight", "wipeup", "wipedown",
            "dissolve", "pixelize", "circleopen", "circleclose",
            "radial", "zoomin", "smoothleft", "smoothright",
            "fadeblack", "fadewhite",
        ]
        actual_type = random.choice(_XFADE_POOL) if xfade_type == "random" else xfade_type
        if log_callback and xfade_type == "random":
            log_callback(f"  [xfade] pair {i}: {actual_type}", "muted")

        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", current, "-i", norm_i,
            "-filter_complex",
            f"[0:v][1:v]xfade=transition={actual_type}"
            f":duration={xfade_dur}:offset={offset:.3f}[vout];"
            f"[0:a][1:a]acrossfade=d={xfade_dur}[aout]",
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-r", str(fps),
            "-c:a", "aac", "-b:a", "192k", out
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", creationflags=SUBPROCESS_FLAGS
        )

        # Xóa norm_i
        if norm_i != segments[i] and os.path.exists(norm_i):
            try: os.remove(norm_i)
            except Exception: pass

        if result.returncode != 0:
            if log_callback:
                log_callback(f"  [xfade {i}/{n-1}] lỗi: {result.stderr.strip()[-120:]}", "error")
            if not is_last:
                _sh.copy2(current, out)
        else:
            if log_callback:
                log_callback(f"  [xfade] {i}/{n-1} ✓", "muted")
            if progress_callback:
                progress_callback(i / (n - 1))

        # Xóa current tmp
        if current not in (segments[0], segments[i]) and os.path.exists(current):
            try: os.remove(current)
            except Exception: pass
        current = out

    # Xóa norm0
    if norm0 != segments[0] and os.path.exists(norm0):
        try: os.remove(norm0)
        except Exception: pass

    return os.path.exists(output_video), ""


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


class PositionPicker(tk.Frame):
    """Widget chọn vị trí PiP dạng lưới 3×3 (4 góc + giữa)."""
    _SLOTS = [
        ("↖", "top-left",     0, 0),
        ("↗", "top-right",    0, 2),
        ("⊕", "center",       1, 1),
        ("↙", "bottom-left",  2, 0),
        ("↘", "bottom-right", 2, 2),
    ]

    def __init__(self, parent, variable, bg=None):
        super().__init__(parent, bg=bg or CARD_BG)
        self.variable = variable
        self._btns = {}

        for label, value, row, col in self._SLOTS:
            btn = tk.Label(
                self, text=label, bg=CARD_HEADER, fg=TEXT_SECONDARY,
                font=(UI_FONT, 13), cursor="hand2",
                width=2, relief="flat", padx=6, pady=4
            )
            btn.grid(row=row, column=col, padx=2, pady=2)
            btn.bind("<Button-1>", lambda e, v=value: self.variable.set(v))
            self._btns[value] = btn

        # Ô trống cho lưới đầy đủ
        for r in range(3):
            for c in range(3):
                if not any(ro == r and co == c for _, _, ro, co in self._SLOTS):
                    tk.Label(self, bg=bg or CARD_BG, width=2).grid(row=r, column=c)

        self.variable.trace_add("write", lambda *_: self._refresh())
        self._refresh()

    def _refresh(self):
        cur = self.variable.get()
        for val, btn in self._btns.items():
            btn.config(bg=ACCENT if val == cur else CARD_HEADER,
                       fg=APP_BG  if val == cur else TEXT_SECONDARY)


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
        def _scroll(event):
            if IS_WINDOWS:
                self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            else:
                # Mac: event.delta là ±1..±10, Linux: Button-4/5 riêng
                self.canvas.yview_scroll(int(-1 * event.delta), "units")
            return "break"

        self._scroll_handler = _scroll
        self._bind_scroll_widget(self.canvas)
        self._bind_scroll_widget(self.inner)

    def _bind_scroll_widget(self, widget):
        widget.bind("<MouseWheel>", self._scroll_handler, add="+")
        widget.bind("<Button-4>",
                    lambda e: (self.canvas.yview_scroll(-1, "units"), "break")[-1], add="+")
        widget.bind("<Button-5>",
                    lambda e: (self.canvas.yview_scroll(1, "units"), "break")[-1], add="+")

    def bind_scroll_to_all_children(self, widget=None):
        """Bind scroll trực tiếp lên toàn bộ widget con — gọi sau khi build UI xong."""
        if widget is None:
            widget = self.inner
        for child in widget.winfo_children():
            self._bind_scroll_widget(child)
            self.bind_scroll_to_all_children(child)


# =========================================================
# VIDEO TAB  (một job = một tab)
# =========================================================


class VideoTab(tk.Frame):

    def __init__(self, parent, root_window, tab_number, close_callback=None):
        super().__init__(parent, bg=APP_BG)
        self._root_window = root_window
        self._tab_number  = tab_number
        self._close_cb    = close_callback

        self._job_dir_name = f"job_{tab_number}"
        self._update_paths(self._job_dir_name)

        self._setup_variables()
        self._build_ui()

        self.ui_queue = queue.Queue()
        self.after(50, self._process_ui_queue)

        self.whisper_model      = None
        self.whisper_model_name = None
    
    def _update_paths(self, dir_name):
        self._job_dir_name         = dir_name
        self.CACHE_IMAGE_FOLDER    = os.path.join(dir_name, "cache_images")
        self.CACHE_AUDIO_FOLDER    = os.path.join(dir_name, "cache_audio")
        self.CACHE_VIDEO_FOLDER    = os.path.join(dir_name, "cache_videos")
        self.CACHE_SUBTITLE_FOLDER = os.path.join(dir_name, "cache_subtitles")
        self.TEMP_FOLDER           = os.path.join(dir_name, "temp")
        self.OUTPUT_VIDEO          = f"{dir_name}.mp4"

    def on_rename(self, new_title):
        safe = "".join(c if c.isalnum() or c in "-_" else "_"
                       for c in new_title).strip("_") or self._job_dir_name
        if safe == self._job_dir_name:
            return
        old_dir = self._job_dir_name
        if os.path.exists(old_dir):
            try:
                os.rename(old_dir, safe)
            except Exception as e:
                self.log(f"Không đổi tên thư mục: {e}", "error")
                return
        old_out = self.OUTPUT_VIDEO
        self._update_paths(safe)
        if os.path.exists(old_out):
            try:
                os.rename(old_out, self.OUTPUT_VIDEO)
            except Exception:
                pass
        self.log(f"Đổi tên job: '{old_dir}' → '{safe}'", "info")

    def _setup_variables(self):
        self._stop_event = threading.Event()
        self.video_paths = []
        self.image_folder = tk.StringVar()
        self.segment_seconds = tk.IntVar(value=30)
        self.transition_duration = tk.DoubleVar(value=0.5)
        default_workers = 8 if IS_WINDOWS else 6
        self.max_workers = tk.IntVar(value=default_workers)
        self.preset = tk.StringVar(value="TikTok (Dọc)")
        
        self.random_order = tk.BooleanVar(value=True)
        self.keep_first_audio = tk.BooleanVar(value=False)
        self.smooth_audio_transition = tk.BooleanVar(value=True)
        self.audio_fade_dur = tk.DoubleVar(value=0.5)
        self.use_transition    = tk.BooleanVar(value=False)
        self.transition_type   = tk.StringVar(value="fade_black")
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

        # PiP Overlay
        self.use_pip = tk.BooleanVar(value=False)
        self.pip_items = []   # list of {"path", "pos_var", "size_var", "frame"}

        # Text Banner
        self.use_banner = tk.BooleanVar(value=False)
        self.banner_top_enabled  = tk.BooleanVar(value=True)
        self.banner_top_text     = tk.StringVar(value="")
        self.banner_top_fontsize = tk.IntVar(value=52)
        self.banner_top_textcolor = tk.StringVar(value="#FFFFFF")
        self.banner_top_bgcolor   = tk.StringVar(value="#000000")
        self.banner_top_bgopacity = tk.DoubleVar(value=0.75)
        self.banner_bot_enabled  = tk.BooleanVar(value=True)
        self.banner_bot_text     = tk.StringVar(value="")
        self.banner_bot_fontsize = tk.IntVar(value=44)
        self.banner_bot_textcolor = tk.StringVar(value="#FFFFFF")
        self.banner_bot_bgcolor   = tk.StringVar(value="#000000")
        self.banner_bot_bgopacity = tk.DoubleVar(value=0.75)

        # Image crawler
        self.show_crawl      = tk.BooleanVar(value=False)
        self.crawl_engine    = tk.StringVar(value="Bing")
        self.crawl_keyword   = tk.StringVar(value="")
        self.crawl_layout    = tk.StringVar(value="Ngang")
        self.crawl_max_num   = tk.IntVar(value=30)
        self.crawl_min_width = tk.IntVar(value=1000)

        # YouTube downloader
        self.show_ytdl       = tk.BooleanVar(value=False)
        self.ytdl_mode       = tk.StringVar(value="Từ khoá")   # "Từ khoá" | "Link URL"
        self.ytdl_keyword    = tk.StringVar(value="")
        self._ytdl_url_text  = None   # tk.Text widget, set khi build UI
        self.ytdl_type       = tk.StringVar(value="audio")
        self.ytdl_max_res    = tk.IntVar(value=5)
        self.ytdl_max_dur    = tk.IntVar(value=10)   # phút
        self.ytdl_workers    = tk.IntVar(value=5)
        self.ytdl_afmt       = tk.StringVar(value="mp3")
        self.ytdl_aqual      = tk.StringVar(value="320")
        self.ytdl_out_folder = tk.StringVar(value="")

        # Video clip mode (thay thế thư mục ảnh)
        self.use_video_clips = tk.BooleanVar(value=False)
        self.video_clip_random = tk.BooleanVar(value=True)
        self.clip_duration_seconds = tk.DoubleVar(value=5.0)
        self.clip_source_paths = []
    
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
        self._apply_entry_focus_border(main)
        self.left_scrollable.bind_scroll_to_all_children()
    
    def _apply_entry_focus_border(self, root):
        """Duyệt đệ quy, áp underline ACCENT khi focus cho tk.Entry."""
        for w in root.winfo_children():
            if type(w) is tk.Entry:
                try:
                    parent_bg = w.master.cget("bg")
                except Exception:
                    parent_bg = CARD_BG
                w.configure(
                    highlightthickness=1,
                    highlightbackground=parent_bg,
                    highlightcolor=ACCENT,
                )
            # Combobox: KHÔNG đổi bg frame cha — để tự nhiên, không có viền
            self._apply_entry_focus_border(w)

    def _build_left_column(self, parent):
        # === Video / Audio Sources ===
        videos_card = Card(parent, "▶  Video / Audio nguồn")
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

        FlatButton(vbtn_frame, "+ Thêm tệp", command=self.add_media,
                   width=110, height=34).pack(side="left", padx=(0, 6))
        FlatButton(vbtn_frame, "Xóa chọn", command=self.remove_selected_videos,
                   bg=BTN_SECONDARY, hover_bg=BTN_SEC_HOVER, pressed_bg=DIVIDER,
                   width=90, height=34).pack(side="left", padx=(0, 6))
        FlatButton(vbtn_frame, "Xóa tất cả", command=self.clear_videos,
                   bg=DANGER, hover_bg=DANGER_HOVER, pressed_bg="#C0392B",
                   width=90, height=34).pack(side="left")

        self.video_count_label = tk.Label(
            vbtn_frame, text="0 tệp", bg=CARD_BG, fg=TEXT_MUTED,
            font=(UI_FONT, 9)
        )
        self.video_count_label.pack(side="right")

        # === YouTube Downloader — nằm trong card Video/Audio nguồn ===
        tk.Frame(videos_card.body, bg=DIVIDER, height=1).pack(fill="x", pady=(10, 0))

        ytdl_opts = tk.Frame(videos_card.body, bg=CARD_BG)

        ytdl_toggle = ToggleSwitch(videos_card.body, "⬇  Tải từ YouTube",
                                   self.show_ytdl, command=lambda: None)
        ytdl_toggle.pack(anchor="w", fill="x", pady=(6, 0))

        def _toggle_ytdl():
            if self.show_ytdl.get():
                ytdl_opts.pack(fill="x", after=ytdl_toggle)
            else:
                ytdl_opts.pack_forget()
        ytdl_toggle.command = _toggle_ytdl

        # Mode selector: Từ khoá / Link URL
        mode_sel_row = tk.Frame(ytdl_opts, bg=CARD_BG)
        mode_sel_row.pack(fill="x", pady=(6, 2))
        tk.Label(mode_sel_row, text="Chế độ", bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 9)).pack(side="left", padx=(8, 6))
        _mode_cb_f = tk.Frame(mode_sel_row, bg=CARD_HEADER)
        _mode_cb_f.pack(side="left")
        ttk.Combobox(_mode_cb_f, textvariable=self.ytdl_mode,
                     values=["Từ khoá", "Link URL"],
                     state="readonly", font=(UI_FONT, 9), width=9,
                     style="Modern.TCombobox", takefocus=0).pack(padx=2, pady=2)

        # Keyword input
        kw_f = tk.Frame(ytdl_opts, bg=CARD_BG)
        kw_f.pack(fill="x", pady=(4, 4))
        tk.Label(kw_f, text="Từ khoá", bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 9)).pack(side="left", padx=(8, 6))
        kw_entry_f = tk.Frame(kw_f, bg=CARD_HEADER)
        kw_entry_f.pack(side="left", fill="x", expand=True, padx=(0, 8))
        tk.Entry(kw_entry_f, textvariable=self.ytdl_keyword,
                 bg=CARD_HEADER, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
                 font=(UI_FONT, 10), relief="flat", borderwidth=0,
                 highlightthickness=0,
                 ).pack(fill="x", padx=6, pady=4)

        # URL input — multi-line (hidden by default)
        url_f = tk.Frame(ytdl_opts, bg=CARD_BG)
        url_hdr = tk.Frame(url_f, bg=CARD_BG)
        url_hdr.pack(fill="x", padx=8, pady=(4, 2))
        tk.Label(url_hdr, text="Link URL", bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 9)).pack(side="left")
        tk.Label(url_hdr, text="(mỗi link 1 dòng)", bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 8)).pack(side="left", padx=(8, 0))
        url_text_f = tk.Frame(url_f, bg=CARD_HEADER)
        url_text_f.pack(fill="x", padx=8, pady=(0, 4))
        self._ytdl_url_text = tk.Text(
            url_text_f, bg=CARD_HEADER, fg=TEXT_PRIMARY,
            insertbackground=TEXT_PRIMARY,
            font=(UI_FONT, 10), relief="flat", borderwidth=0,
            highlightthickness=0, height=8, wrap="none",
        )
        _url_scroll = tk.Scrollbar(url_text_f, command=self._ytdl_url_text.yview)
        self._ytdl_url_text.config(yscrollcommand=_url_scroll.set)
        self._ytdl_url_text.pack(side="left", fill="x", expand=True, padx=(6, 0), pady=4)
        _url_scroll.pack(side="right", fill="y", pady=4)

        def _on_ytdl_mode(*_):
            if self.ytdl_mode.get() == "Link URL":
                kw_f.pack_forget()
                url_f.pack(fill="x", pady=(4, 4), after=mode_sel_row)
            else:
                url_f.pack_forget()
                kw_f.pack(fill="x", pady=(4, 4), after=mode_sel_row)
        self.ytdl_mode.trace_add("write", _on_ytdl_mode)

        # Media type + audio options
        type_row = tk.Frame(ytdl_opts, bg=CARD_BG)
        type_row.pack(fill="x", pady=(0, 6))
        tk.Label(type_row, text="Loại", bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 9)).pack(side="left", padx=(8, 6))
        _cb_f = tk.Frame(type_row, bg=CARD_HEADER)
        _cb_f.pack(side="left", padx=(0, 14))
        ttk.Combobox(_cb_f, textvariable=self.ytdl_type,
                     values=["audio", "video"], state="readonly",
                     font=(UI_FONT, 9), width=7,
                     style="Modern.TCombobox", takefocus=0).pack(padx=2, pady=2)

        audio_opts = tk.Frame(type_row, bg=CARD_BG)
        audio_opts.pack(side="left")
        tk.Label(audio_opts, text="Định dạng", bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 9)).pack(side="left", padx=(0, 4))
        _cb_f2 = tk.Frame(audio_opts, bg=CARD_HEADER)
        _cb_f2.pack(side="left", padx=(0, 12))
        ttk.Combobox(_cb_f2, textvariable=self.ytdl_afmt,
                     values=["mp3", "m4a", "opus", "flac", "wav"],
                     state="readonly", font=(UI_FONT, 9), width=6,
                     style="Modern.TCombobox", takefocus=0).pack(padx=2, pady=2)
        tk.Label(audio_opts, text="Chất lượng", bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 9)).pack(side="left", padx=(0, 4))
        _cb_f3 = tk.Frame(audio_opts, bg=CARD_HEADER)
        _cb_f3.pack(side="left")
        ttk.Combobox(_cb_f3, textvariable=self.ytdl_aqual,
                     values=["128", "192", "256", "320"],
                     state="readonly", font=(UI_FONT, 9), width=5,
                     style="Modern.TCombobox", takefocus=0).pack(padx=2, pady=2)

        def _on_ytdl_type(*_):
            if self.ytdl_type.get() == "audio":
                audio_opts.pack(side="left")
            else:
                audio_opts.pack_forget()
        self.ytdl_type.trace_add("write", _on_ytdl_type)
        _on_ytdl_type()

        # Numbers row
        num_row = tk.Frame(ytdl_opts, bg=CARD_BG)
        num_row.pack(fill="x", pady=(0, 6))
        for label, var, w in [("Số lượng", self.ytdl_max_res, 4),
                               ("Max (phút)", self.ytdl_max_dur, 4),
                               ("Luồng", self.ytdl_workers, 3)]:
            tk.Label(num_row, text=label, bg=CARD_BG, fg=TEXT_SECONDARY,
                     font=(UI_FONT, 9)).pack(side="left", padx=(8, 4))
            _ef = tk.Frame(num_row, bg=CARD_HEADER)
            _ef.pack(side="left", padx=(0, 10))
            tk.Entry(_ef, textvariable=var, width=w, bg=CARD_HEADER,
                     fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
                     font=(UI_FONT, 10), relief="flat", borderwidth=0,
                     highlightthickness=0,
                     justify="center").pack(padx=5, ipady=3)

        # Output folder
        out_f = tk.Frame(ytdl_opts, bg=CARD_BG)
        out_f.pack(fill="x", pady=(0, 6))
        tk.Label(out_f, text="Thư mục lưu", bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 9)).pack(side="left", padx=(8, 6))
        out_entry_f = tk.Frame(out_f, bg=CARD_HEADER)
        out_entry_f.pack(side="left", fill="x", expand=True, padx=(0, 4))
        tk.Entry(out_entry_f, textvariable=self.ytdl_out_folder,
                 bg=CARD_HEADER, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
                 font=(UI_FONT, 10), relief="flat", borderwidth=0,
                 highlightthickness=0,
                 ).pack(fill="x", padx=6, pady=4)
        FlatButton(out_f, "Chọn", command=self._ytdl_select_folder,
                   width=70, height=30, padx=6).pack(side="right", padx=3, pady=3)

        # Download button + progress
        self.ytdl_btn = FlatButton(ytdl_opts, "⬇  Tải xuống",
                                   command=self._ytdl_start, height=34)
        self.ytdl_btn.pack(fill="x", pady=(0, 6))

        self.ytdl_progress = ttk.Progressbar(ytdl_opts, mode="determinate",
                                              maximum=100,
                                              style="Modern.Horizontal.TProgressbar")
        self.ytdl_progress.pack(fill="x", ipady=3, pady=(0, 2))

        self.ytdl_count_label = tk.Label(ytdl_opts, text="", bg=CARD_BG,
                                         fg=TEXT_SECONDARY, font=(UI_FONT, 8))
        self.ytdl_count_label.pack(anchor="e", padx=4, pady=(0, 6))

        _toggle_ytdl()  # ẩn mặc định

        # ── Audio options ───────────────────────────────────────────────
        tk.Frame(videos_card.body, bg=DIVIDER, height=1).pack(fill="x", pady=(6, 0))

        ToggleSwitch(videos_card.body, "Trộn ngẫu nhiên thứ tự audio",
                     self.random_order).pack(anchor="w", pady=(8, 4), fill="x")
        ToggleSwitch(videos_card.body, "Giữ nguyên đoạn audio đầu tiên",
                     self.keep_first_audio).pack(anchor="w", pady=(0, 4), fill="x")

        smooth_audio_row = tk.Frame(videos_card.body, bg=CARD_BG)
        smooth_audio_row.pack(fill="x", pady=(0, 4))
        ToggleSwitch(smooth_audio_row, "Làm mịn chuyển cảnh audio",
                     self.smooth_audio_transition).pack(side="left")
        tk.Label(smooth_audio_row, text="Fade (s):", bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 9)).pack(side="left", padx=(12, 4))
        _fade_field = tk.Frame(smooth_audio_row, bg=CARD_HEADER)
        _fade_field.pack(side="left")
        tk.Entry(_fade_field, textvariable=self.audio_fade_dur,
                 bg=CARD_HEADER, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
                 font=(UI_FONT, 10), relief="flat", borderwidth=0,
                 justify="center", width=4).pack(padx=6, ipady=4)

        trim_row = tk.Frame(videos_card.body, bg=CARD_BG)
        trim_row.pack(fill="x", pady=(0, 8))
        ToggleSwitch(trim_row, "Cắt bớt phần đầu audio (nhạc intro)",
                     self.trim_audio_start).pack(side="left")
        tk.Label(trim_row, text="Số giây:", bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 9)).pack(side="left", padx=(20, 4))
        trim_field = tk.Frame(trim_row, bg=CARD_HEADER)
        trim_field.pack(side="left")
        tk.Entry(trim_field, textvariable=self.trim_audio_seconds,
                 bg=CARD_HEADER, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
                 font=(UI_FONT, 10), relief="flat", borderwidth=0,
                 justify="center", width=5).pack(padx=6, ipady=4)

        # === Toggle chọn nguồn hình ===
        clip_toggle_wrapper = tk.Frame(parent, bg=APP_BG)
        clip_toggle_wrapper.pack(fill="x", pady=(0, 6), padx=(0, 4))

        ToggleSwitch(
            clip_toggle_wrapper,
            "⬡  Dùng video động thay vì thư mục ảnh",
            self.use_video_clips,
            command=self._toggle_clip_mode
        ).pack(anchor="w")

        # === Wrapper cố định — giữ vị trí trong layout, tránh reorder khi toggle ===
        self._clip_img_wrapper = tk.Frame(parent, bg=APP_BG)
        self._clip_img_wrapper.pack(fill="x", pady=(0, 0), padx=(0, 4))

        # === Video Clip Source Card (ẩn mặc định) ===
        self.clip_source_card = Card(self._clip_img_wrapper, "⬡  Video nguồn clip động")

        cv_list_inner = tk.Frame(self.clip_source_card.body, bg=CARD_HEADER)
        cv_list_inner.pack(fill="x", pady=(0, 10))

        self.clip_listbox = tk.Listbox(
            cv_list_inner, bg=CARD_HEADER, fg=TEXT_PRIMARY,
            selectbackground=ACCENT, selectforeground=APP_BG,
            font=(UI_FONT, 9), height=4,
            relief="flat", borderwidth=0,
            activestyle="none", selectmode="extended"
        )
        self.clip_listbox.pack(side="left", fill="both", expand=True)

        cv_scroll = tk.Scrollbar(cv_list_inner, command=self.clip_listbox.yview)
        cv_scroll.pack(side="right", fill="y")
        self.clip_listbox.config(yscrollcommand=cv_scroll.set)

        cv_btn_frame = tk.Frame(self.clip_source_card.body, bg=CARD_BG)
        cv_btn_frame.pack(fill="x")

        FlatButton(cv_btn_frame, "+ Thêm video", command=self.add_clip_videos,
                   width=120, height=34).pack(side="left", padx=(0, 6))
        FlatButton(cv_btn_frame, "Xóa chọn", command=self.remove_selected_clip_videos,
                   bg=BTN_SECONDARY, hover_bg=BTN_SEC_HOVER, pressed_bg=DIVIDER,
                   width=100, height=34).pack(side="left", padx=(0, 6))
        FlatButton(cv_btn_frame, "Xóa tất cả", command=self.clear_clip_videos,
                   bg=DANGER, hover_bg=DANGER_HOVER, pressed_bg="#C0392B",
                   width=100, height=34).pack(side="left")

        self.clip_count_label = tk.Label(
            cv_btn_frame, text="0 video", bg=CARD_BG, fg=TEXT_MUTED,
            font=(UI_FONT, 9)
        )
        self.clip_count_label.pack(side="right")

        # === Image Folder Card (hiện mặc định) ===
        self.images_card = Card(self._clip_img_wrapper, "⊞  Thư mục ảnh")
        self.images_card.pack(fill="x", pady=(0, 12))

        img_field = tk.Frame(self.images_card.body, bg=CARD_HEADER)
        img_field.pack(fill="x")

        self.img_entry = tk.Entry(
            img_field, textvariable=self.image_folder,
            bg=CARD_HEADER, fg=TEXT_PRIMARY,
            insertbackground=TEXT_PRIMARY,
            font=(UI_FONT, 10), relief="flat", borderwidth=0
        )
        self.img_entry.pack(side="left", fill="x", expand=True, padx=6, pady=4)

        FlatButton(img_field, "Chọn", command=self.select_images,
                   width=76, height=34, padx=10).pack(side="right", padx=3, pady=3)

        # ── Tải ảnh theo từ khoá (collapsible) ──
        tk.Frame(self.images_card.body, bg=CARD_HEADER, height=1).pack(fill="x", padx=8, pady=(4, 6))

        crawl_opts = tk.Frame(self.images_card.body, bg=CARD_BG)

        crawl_toggle = ToggleSwitch(
            self.images_card.body, "⬇  Tải ảnh theo từ khoá",
            self.show_crawl, command=lambda: None
        )
        crawl_toggle.pack(anchor="w", fill="x", pady=(0, 4))

        def _toggle_crawl():
            if self.show_crawl.get():
                crawl_opts.pack(fill="x", after=crawl_toggle)
            else:
                crawl_opts.pack_forget()
        crawl_toggle.command = _toggle_crawl

        # --- crawl_opts content (ẩn mặc định) ---
        kw_row = tk.Frame(crawl_opts, bg=CARD_BG)
        kw_row.pack(fill="x", pady=(0, 4))
        tk.Label(kw_row, text="Từ khoá", bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 9)).pack(side="left", padx=(8, 6))
        _ckw_f = tk.Frame(kw_row, bg=CARD_HEADER)
        _ckw_f.pack(side="left", fill="x", expand=True, padx=(0, 8))
        tk.Entry(_ckw_f, textvariable=self.crawl_keyword,
                 bg=CARD_HEADER, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
                 font=(UI_FONT, 10), relief="flat", borderwidth=0,
                 highlightthickness=0,
                 ).pack(fill="x", padx=6, pady=4)

        engine_row = tk.Frame(crawl_opts, bg=CARD_BG)
        engine_row.pack(fill="x", pady=(0, 6))

        tk.Label(engine_row, text="Nguồn tìm", bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 9)).pack(side="left", padx=(8, 4))
        _ce_f = tk.Frame(engine_row, bg=CARD_HEADER)
        _ce_f.pack(side="left")
        ttk.Combobox(_ce_f, textvariable=self.crawl_engine,
                     values=["Bing", "Pinterest", "Flickr"],
                     state="readonly", font=(UI_FONT, 9), width=9,
                     style="Modern.TCombobox", takefocus=0).pack(padx=2, pady=2)

        opts_row = tk.Frame(crawl_opts, bg=CARD_BG)
        opts_row.pack(fill="x", pady=(0, 6))

        tk.Label(opts_row, text="Loại ảnh", bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 9)).pack(side="left", padx=(8, 4))
        _cl_f = tk.Frame(opts_row, bg=CARD_HEADER)
        _cl_f.pack(side="left", padx=(0, 14))
        ttk.Combobox(_cl_f, textvariable=self.crawl_layout,
                     values=["Ngang", "Dọc", "Vuông", "Tất cả"],
                     state="readonly", font=(UI_FONT, 9), width=7,
                     style="Modern.TCombobox", takefocus=0).pack(padx=2, pady=2)

        tk.Label(opts_row, text="Số ảnh", bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 9)).pack(side="left", padx=(0, 4))
        _cn_f = tk.Frame(opts_row, bg=CARD_HEADER)
        _cn_f.pack(side="left", padx=(0, 14))
        tk.Entry(_cn_f, textvariable=self.crawl_max_num, width=5,
                 bg=CARD_HEADER, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
                 font=(UI_FONT, 10), relief="flat", borderwidth=0,
                 highlightthickness=0, justify="center").pack(padx=5, ipady=3)

        tk.Label(opts_row, text="Min px", bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 9)).pack(side="left", padx=(0, 4))
        _cm_f = tk.Frame(opts_row, bg=CARD_HEADER)
        _cm_f.pack(side="left")
        tk.Entry(_cm_f, textvariable=self.crawl_min_width, width=6,
                 bg=CARD_HEADER, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
                 font=(UI_FONT, 10), relief="flat", borderwidth=0,
                 highlightthickness=0, justify="center").pack(padx=5, ipady=3)

        self.crawl_btn = FlatButton(crawl_opts, "⬇  Tải ảnh",
                                    command=self._start_crawl, height=34)
        self.crawl_btn.pack(fill="x", pady=(0, 6))

        self.crawl_progress = ttk.Progressbar(
            crawl_opts, mode="determinate",
            style="Modern.Horizontal.TProgressbar"
        )
        self.crawl_progress.pack(fill="x", ipady=3, pady=(0, 2))
        self.crawl_progress["value"] = 0

        self.crawl_count_label = tk.Label(
            crawl_opts, text="", bg=CARD_BG, fg=TEXT_SECONDARY,
            font=(UI_FONT, 8)
        )
        self.crawl_count_label.pack(anchor="e", padx=4, pady=(0, 8))

        _toggle_crawl()   # ẩn mặc định

        # ── Hiệu ứng fade ──────────────────────────────────────────────
        tk.Frame(self.images_card.body, bg=DIVIDER, height=1).pack(fill="x", pady=(4, 0))
        # ── Toggle hiệu ứng ──────────────────────────────────────────
        fx_toggle_row = tk.Frame(self.images_card.body, bg=CARD_BG)
        fx_toggle_row.pack(fill="x", pady=(6, 2))
        ToggleSwitch(fx_toggle_row, "Hiệu ứng chuyển ảnh",
                     self.use_transition).pack(side="left")
        tk.Label(fx_toggle_row, text="Thời lượng (s):", bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 9)).pack(side="left", padx=(16, 4))
        fade_dur_f = tk.Frame(fx_toggle_row, bg=CARD_HEADER)
        fade_dur_f.pack(side="left")
        tk.Entry(fade_dur_f, textvariable=self.transition_duration,
                 bg=CARD_HEADER, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
                 font=(UI_FONT, 10), relief="flat", borderwidth=0,
                 justify="center", width=4).pack(padx=6, ipady=4)

        # ── Chọn kiểu hiệu ứng ───────────────────────────────────────
        _FAST_FX  = [
            ("🎲 Random",   "random"),
            ("Fade đen",    "fade_black"),
            ("Fade trắng",  "fade_white"),
        ]
        _XFADE_FX = [
            ("Slide trái",   "slideleft"),
            ("Slide phải",   "slideright"),
            ("Slide lên",    "slideup"),
            ("Slide xuống",  "slidedown"),
            ("Wipe trái",    "wipeleft"),
            ("Wipe phải",    "wiperight"),
            ("Dissolve",     "dissolve"),
            ("Pixelize",     "pixelize"),
            ("Circle mở",    "circleopen"),
            ("Circle đóng",  "circleclose"),
            ("Radial",       "radial"),
            ("Zoom in",      "zoomin"),
            ("Smooth trái",  "smoothleft"),
            ("Smooth phải",  "smoothright"),
        ]

        fx_type_row = tk.Frame(self.images_card.body, bg=CARD_BG)
        fx_type_row.pack(fill="x", pady=(0, 6))

        tk.Label(fx_type_row, text="Kiểu hiệu ứng:", bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 9)).pack(side="left", padx=(0, 6))

        _all_fx_labels  = [l for l, _ in _FAST_FX] + ["─── Đẹp (chậm hơn) ───"] + [l for l, _ in _XFADE_FX]
        _all_fx_values  = [v for _, v in _FAST_FX] + ["_sep"] + [v for _, v in _XFADE_FX]

        fx_cb_f = tk.Frame(fx_type_row, bg=CARD_HEADER)
        fx_cb_f.pack(side="left")
        _fx_display = tk.StringVar(value="🎲 Random")
        fx_combo = ttk.Combobox(fx_cb_f, textvariable=_fx_display,
                                values=_all_fx_labels, state="readonly",
                                font=(UI_FONT, 9), width=18,
                                style="Modern.TCombobox", takefocus=0)
        fx_combo.pack(padx=2, pady=2)

        def _on_fx_select(e=None):
            label = _fx_display.get()
            if label == "─── Đẹp (chậm hơn) ───":
                prev_val = self.transition_type.get()
                prev_idx = _all_fx_values.index(prev_val) if prev_val in _all_fx_values else 0
                _fx_display.set(_all_fx_labels[prev_idx])
                return
            idx = _all_fx_labels.index(label) if label in _all_fx_labels else 0
            self.transition_type.set(_all_fx_values[idx])

        fx_combo.bind("<<ComboboxSelected>>", _on_fx_select)
        self.transition_type.set("random")

        # Note xfade
        self._xfade_values = set(v for _, v in _XFADE_FX)
        self._all_fx_values_list = _all_fx_values  # keep reference for random

        tk.Label(fx_type_row, text="⚡ Nhanh  /  ✦ Đẹp hơn nhưng chậm hơn",
                 bg=CARD_BG, fg=TEXT_MUTED, font=(UI_FONT, 8)
                 ).pack(side="left", padx=(10, 0))

        # === Configuration ===
        self.config_card = Card(parent, "⚙  Cấu hình")
        self.config_card.pack(fill="x", pady=(0, 12), padx=(0, 4))

        fmt_row = tk.Frame(self.config_card.body, bg=CARD_BG)
        fmt_row.pack(fill="x", pady=(0, 10))
        tk.Label(fmt_row, text="Định dạng:", bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 9)).pack(side="left", padx=(0, 8))
        fmt_field = tk.Frame(fmt_row, bg=CARD_HEADER)
        fmt_field.pack(side="left")
        self.format_combo = ttk.Combobox(
            fmt_field, textvariable=self.preset,
            values=list(VIDEO_PRESETS.keys()),
            state="readonly", font=(UI_FONT, 10),
            width=18, style="Modern.TCombobox"
        )
        self.format_combo.pack(padx=4, pady=2)

        params_grid = tk.Frame(self.config_card.body, bg=CARD_BG)
        params_grid.pack(anchor="w")
        params_grid.grid_columnconfigure(0, weight=0)
        params_grid.grid_columnconfigure(1, weight=0)

        self._make_number_input(params_grid, "Độ dài đoạn random (s)", self.segment_seconds, 0, 0)
        self._make_number_input(params_grid, "Số luồng", self.max_workers, 0, 1)

        ToggleSwitch(self.config_card.body, "Tự động xóa file tạm sau khi hoàn thành",
                     self.cleanup_temp).pack(anchor="w", pady=(10, 4), fill="x")

        # Clip mode options (ẩn mặc định, hiện khi bật video clip mode)
        self.clip_options_section = tk.Frame(parent, bg=CARD_BG)

        tk.Frame(self.clip_options_section, bg=BORDER_COLOR, height=1).pack(fill="x", pady=(6, 8))
        tk.Label(
            self.clip_options_section, text="Chế độ lấy video clip",
            bg=CARD_BG, fg=TEXT_SECONDARY, font=(UI_FONT, 9)
        ).pack(anchor="w", pady=(0, 4))
        ToggleSwitch(
            self.clip_options_section,
            "Cắt clip ngẫu nhiên  (tắt = mapping xuyên suốt)",
            self.video_clip_random
        ).pack(anchor="w", pady=6, fill="x")

        clip_dur_row = tk.Frame(self.clip_options_section, bg=CARD_BG)
        clip_dur_row.pack(fill="x", pady=(2, 4))
        tk.Label(
            clip_dur_row, text="Thời lượng mỗi clip (giây):",
            bg=CARD_BG, fg=TEXT_SECONDARY, font=(UI_FONT, 9)
        ).pack(side="left")
        clip_dur_field = tk.Frame(clip_dur_row, bg=CARD_HEADER)
        clip_dur_field.pack(side="left", padx=(10, 0))
        tk.Entry(clip_dur_field, textvariable=self.clip_duration_seconds,
                 bg=CARD_HEADER, fg=TEXT_PRIMARY,
                 insertbackground=TEXT_PRIMARY,
                 font=(UI_FONT, 10), relief="flat", borderwidth=0,
                 justify="center", width=5).pack(padx=6, ipady=4)
        tk.Label(
            clip_dur_row,
            text="(clip lặp lại để phủ full audio)",
            bg=CARD_BG, fg=TEXT_MUTED, font=(UI_FONT, 8)
        ).pack(side="left", padx=(8, 0))

        # ── helper: toggle pack/pack_forget ──────────────────────
        def _show(frame, var):
            if var.get():
                frame.pack(fill="x")
            else:
                frame.pack_forget()

        # === Subtitle Card ===
        subtitle_card = Card(parent, "CC  Phụ đề tự động  (Whisper AI)")
        subtitle_card.pack(fill="x", pady=(0, 12), padx=(0, 4))

        sub_opts = tk.Frame(subtitle_card.body, bg=CARD_BG)   # collapsible

        ToggleSwitch(
            subtitle_card.body, "Bật phụ đề tự động", self.use_subtitle,
            command=lambda: _show(sub_opts, self.use_subtitle)
        ).pack(anchor="w", pady=(0, 6), fill="x")

        # --- sub_opts content ---
        whisper_row = tk.Frame(sub_opts, bg=CARD_BG)
        whisper_row.pack(fill="x", pady=(0, 10))
        tk.Label(whisper_row, text="Model:", bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 9)).pack(side="left", padx=(0, 8))
        model_field = tk.Frame(whisper_row, bg=CARD_HEADER)
        model_field.pack(side="left")
        ttk.Combobox(model_field, textvariable=self.whisper_model_var,
                     values=WHISPER_MODELS, state="readonly",
                     font=(UI_FONT, 10), width=10, style="Modern.TCombobox"
                     ).pack(padx=4, pady=2)

        sub_grid = tk.Frame(sub_opts, bg=CARD_BG)
        sub_grid.pack(fill="x")
        sub_grid.grid_columnconfigure(0, weight=1)
        sub_grid.grid_columnconfigure(1, weight=1)

        lang_frame = tk.Frame(sub_grid, bg=CARD_BG)
        lang_frame.grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=4)
        tk.Label(lang_frame, text="Ngôn ngữ (vi/en/ja/...)",
                 bg=CARD_BG, fg=TEXT_SECONDARY, font=(UI_FONT, 9)
                 ).pack(anchor="w", pady=(0, 4))
        lang_f = tk.Frame(lang_frame, bg=CARD_HEADER)
        lang_f.pack(anchor="w")
        tk.Entry(lang_f, textvariable=self.subtitle_language,
                 bg=CARD_HEADER, fg=TEXT_PRIMARY,
                 insertbackground=TEXT_PRIMARY,
                 font=(UI_FONT, 10), relief="flat", borderwidth=0,
                 justify="center", width=4).pack(padx=6, ipady=4)

        self._make_number_input(sub_grid, "Cỡ chữ phụ đề", self.subtitle_font_size, 0, 1)

        _show(sub_opts, self.use_subtitle)   # initial state

        # === AI Ordering Card ===
        ai_card = Card(parent, "◑  Sắp xếp thông minh  (Local AI)")
        ai_card.pack(fill="x", pady=(0, 12), padx=(0, 4))

        st_status = ("✓ sentence-transformers đã cài" if SENTENCE_TRANSFORMERS_AVAILABLE
                     else "✗ Cài: pip install sentence-transformers")
        tk.Label(ai_card.body, text=st_status, bg=CARD_BG,
                 fg=SUCCESS if SENTENCE_TRANSFORMERS_AVAILABLE else DANGER,
                 font=(UI_FONT, 9)).pack(anchor="w", pady=(0, 4))

        ai_opts = tk.Frame(ai_card.body, bg=CARD_BG)   # collapsible

        ToggleSwitch(
            ai_card.body,
            "Sắp xếp audio theo ngữ nghĩa (semantic similarity)",
            self.use_ai_ordering,
            command=lambda: _show(ai_opts, self.use_ai_ordering)
        ).pack(anchor="w", pady=(4, 6), fill="x")

        # --- ai_opts content ---
        tk.Label(ai_opts, text="Embedding model (lần đầu tự tải ~120MB)",
                 bg=CARD_BG, fg=TEXT_SECONDARY, font=(UI_FONT, 9)
                 ).pack(anchor="w", pady=(0, 4))
        ai_field = tk.Frame(ai_opts, bg=CARD_HEADER)
        ai_field.pack(anchor="w")
        ttk.Combobox(ai_field, textvariable=self.st_model_name,
                     values=["paraphrase-multilingual-MiniLM-L12-v2",
                             "paraphrase-multilingual-mpnet-base-v2",
                             "all-MiniLM-L6-v2"],
                     state="readonly", font=(UI_FONT, 10),
                     width=36, style="Modern.TCombobox"
                     ).pack(padx=4, pady=2)

        _show(ai_opts, self.use_ai_ordering)   # initial state

        # === PiP Overlay Card ===
        pip_card = Card(parent, "◈  Video overlay (Picture-in-Picture)")
        pip_card.pack(fill="x", pady=(0, 12), padx=(0, 4))

        pip_opts = tk.Frame(pip_card.body, bg=CARD_BG)   # collapsible

        self.pip_list_frame = tk.Frame(pip_opts, bg=CARD_BG)
        self.pip_list_frame.pack(fill="x")

        self.pip_add_btn = FlatButton(
            pip_opts, "+ Thêm video overlay", command=self._add_pip_video,
            height=34, font=(UI_FONT, 9, "bold")
        )
        self.pip_add_btn.pack(fill="x", pady=(6, 0))

        def _on_pip():
            _show(pip_opts, self.use_pip)

        ToggleSwitch(
            pip_card.body, "Bật video overlay (PiP)",
            self.use_pip, command=_on_pip
        ).pack(anchor="w", pady=(0, 6), fill="x")

        _show(pip_opts, self.use_pip)   # initial state

        # === Text Banner Card ===
        banner_card = Card(parent, "✍  Tiêu đề / Banner")
        banner_card.pack(fill="x", pady=(0, 12), padx=(0, 4))

        banner_body = tk.Frame(banner_card.body, bg=CARD_BG)   # collapsible (main)

        ToggleSwitch(
            banner_card.body, "Bật tiêu đề banner", self.use_banner,
            command=lambda: _show(banner_body, self.use_banner)
        ).pack(anchor="w", pady=(0, 6), fill="x")

        # --- banner_body content: top + bottom ---
        for (lbl, en_var, txt_var, fs_var, tc_var, bc_var, bo_var) in [
            ("Banner trên",  self.banner_top_enabled, self.banner_top_text,
             self.banner_top_fontsize,  self.banner_top_textcolor,
             self.banner_top_bgcolor,   self.banner_top_bgopacity),
            ("Banner dưới", self.banner_bot_enabled, self.banner_bot_text,
             self.banner_bot_fontsize,  self.banner_bot_textcolor,
             self.banner_bot_bgcolor,   self.banner_bot_bgopacity),
        ]:
            sec = tk.Frame(banner_body, bg=CARD_HEADER)
            sec.pack(fill="x", pady=(0, 8))

            # Header: toggle controls sub-options visibility
            hdr = tk.Frame(sec, bg=CARD_HEADER)
            hdr.pack(fill="x", padx=8, pady=(6, 2))

            sub_opts_b = tk.Frame(sec, bg=CARD_HEADER)   # collapsible per-banner

            def _on_banner_toggle(f=sub_opts_b, v=en_var):
                _show(f, v)

            ToggleSwitch(hdr, lbl, en_var, bg=CARD_HEADER,
                         command=_on_banner_toggle).pack(side="left")

            # Text input
            txt_f = tk.Frame(sub_opts_b, bg=INPUT_BG)
            txt_f.pack(fill="x", padx=8, pady=(2, 4))
            tk.Entry(txt_f, textvariable=txt_var,
                     bg=INPUT_BG, fg=TEXT_PRIMARY,
                     insertbackground=TEXT_PRIMARY,
                     font=(UI_FONT, 10), relief="flat", bd=0,
                     ).pack(fill="x", padx=4, pady=3)

            # Controls row
            ctrl = tk.Frame(sub_opts_b, bg=CARD_HEADER)
            ctrl.pack(fill="x", padx=8, pady=(0, 8))

            tk.Label(ctrl, text="Cỡ:", bg=CARD_HEADER,
                     fg=TEXT_SECONDARY, font=(UI_FONT, 8)).pack(side="left")
            fs_f = tk.Frame(ctrl, bg=INPUT_BG)
            fs_f.pack(side="left", padx=(3, 10))
            tk.Entry(fs_f, textvariable=fs_var, width=4,
                     bg=INPUT_BG, fg=TEXT_PRIMARY,
                     insertbackground=TEXT_PRIMARY,
                     font=(UI_FONT, 9), relief="flat", bd=0,
                     justify="center").pack(padx=4, pady=3)

            for col_lbl, col_var in [("Chữ", tc_var), ("Nền", bc_var)]:
                tk.Label(ctrl, text=f"{col_lbl}:", bg=CARD_HEADER,
                         fg=TEXT_SECONDARY, font=(UI_FONT, 8)).pack(side="left")
                swatch = tk.Label(ctrl, width=3, height=1, cursor="hand2",
                                  relief="flat", bd=1)
                swatch.pack(side="left", padx=(3, 10))

                def _update_swatch(*_, sw=swatch, cv=col_var):
                    try:
                        sw.config(bg=cv.get())
                    except Exception:
                        pass

                def _pick_color(cv=col_var, sw=swatch):
                    result = colorchooser.askcolor(color=cv.get(), title="Chọn màu")[1]
                    if result:
                        cv.set(result)

                col_var.trace_add("write", _update_swatch)
                swatch.bind("<Button-1>", lambda e, fn=_pick_color: fn())
                _update_swatch()

            tk.Label(ctrl, text="Mờ:", bg=CARD_HEADER,
                     fg=TEXT_SECONDARY, font=(UI_FONT, 8)).pack(side="left")
            op_f = tk.Frame(ctrl, bg=INPUT_BG)
            op_f.pack(side="left", padx=(3, 0))
            tk.Entry(op_f, textvariable=bo_var, width=4,
                     bg=INPUT_BG, fg=TEXT_PRIMARY,
                     insertbackground=TEXT_PRIMARY,
                     font=(UI_FONT, 9), relief="flat", bd=0,
                     justify="center").pack(padx=4, pady=3)

            _show(sub_opts_b, en_var)   # initial: show if toggle ON

        _show(banner_body, self.use_banner)   # initial state

        # === Generate / Stop Buttons ===
        gen_row = tk.Frame(parent, bg=APP_BG)
        gen_row.pack(fill="x", pady=(12, 16), padx=(0, 4))

        self.generate_btn = FlatButton(
            gen_row, "▶  TẠO VIDEO", command=self.start_generation,
            height=50, font=(UI_FONT, 13, "bold")
        )
        self.generate_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.stop_btn = FlatButton(
            gen_row, "⏹ Dừng",
            command=self._request_stop,
            bg=DANGER, hover_bg=DANGER_HOVER, pressed_bg="#C0392B",
            height=50, width=90, font=(UI_FONT, 11, "bold")
        )
        self.stop_btn.pack(side="right")
        self.stop_btn.set_state("disabled")
    
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
                   padx=(0, 4) if col == 0 else (4, 0) if col == 2 else (4, 4), pady=4)

        tk.Label(frame, text=label, bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 9)).pack(anchor="w", pady=(0, 4))

        field = tk.Frame(frame, bg=CARD_HEADER)
        field.pack(anchor="w")
        tk.Entry(field, textvariable=variable,
                 bg=CARD_HEADER, fg=TEXT_PRIMARY,
                 insertbackground=TEXT_PRIMARY,
                 font=(UI_FONT, 10), relief="flat", borderwidth=0,
                 justify="center", width=5).pack(padx=6, ipady=4)
    
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
    
    @staticmethod
    def _is_audio_file(path):
        return path.lower().endswith(('.mp3', '.wav', '.m4a', '.aac', '.ogg', '.flac', '.opus'))

    def _add_media_paths(self, paths):
        added = 0
        for path in paths:
            if path not in self.video_paths:
                self.video_paths.append(path)
                prefix = "♪" if self._is_audio_file(path) else "▶"
                self.videos_listbox.insert("end", f"  {prefix} {os.path.basename(path)}")
                added += 1
        return added

    def add_media(self):
        paths = filedialog.askopenfilenames(
            title="Chọn video / audio",
            filetypes=[
                ("Video & Audio", "*.mp4 *.mov *.mkv *.avi *.flv *.mp3 *.wav *.m4a *.aac *.ogg *.flac *.opus"),
                ("Video", "*.mp4 *.mov *.mkv *.avi *.flv"),
                ("Audio", "*.mp3 *.wav *.m4a *.aac *.ogg *.flac *.opus"),
            ]
        )
        added = self._add_media_paths(paths)
        if added:
            n_a = sum(1 for p in paths if self._is_audio_file(p))
            n_v = len(paths) - n_a
            parts = []
            if n_v: parts.append(f"{n_v} video")
            if n_a: parts.append(f"{n_a} audio")
            self.log(f"Đã thêm {', '.join(parts)}", "success")
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
        self.video_count_label.config(text=f"{count} tệp")
    
    def select_images(self):
        folder = filedialog.askdirectory(title="Chọn thư mục ảnh")
        if folder:
            self.image_folder.set(folder)
            count = len([f for f in os.listdir(folder)
                        if f.lower().endswith(('.jpg', '.jpeg', '.png'))])
            self.log(f"Đã chọn thư mục với {count} ảnh", "success")

    # =========================================================
    # YOUTUBE DOWNLOADER
    # =========================================================

    def _ytdl_select_folder(self):
        folder = filedialog.askdirectory(title="Chọn thư mục lưu")
        if folder:
            self.ytdl_out_folder.set(folder)

    def _ytdl_start(self):
        threading.Thread(target=self._ytdl_download, daemon=True).start()

    def _ytdl_download(self):
        if not YT_DLP_AVAILABLE:
            self.log("Thiếu thư viện: pip install yt-dlp", "error")
            return

        mode        = self.ytdl_mode.get()
        media_type  = self.ytdl_type.get()
        max_results = self.ytdl_max_res.get()
        max_dur_min = self.ytdl_max_dur.get()
        max_dur_sec = max_dur_min * 60
        workers     = self.ytdl_workers.get()
        afmt        = self.ytdl_afmt.get()
        aqual       = self.ytdl_aqual.get()

        # Auto-generate folder
        chosen_dir = self.ytdl_out_folder.get().strip()
        if chosen_dir:
            out_dir = chosen_dir
        else:
            if mode == "Link URL":
                slug = "url_download"
            else:
                kw = self.ytdl_keyword.get().strip()
                slug = "".join(c if c.isalnum() or c in " _-" else "_" for c in kw)[:40].strip()
            out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   f"{media_type}_{slug}")
        os.makedirs(out_dir, exist_ok=True)

        self.queue_ui(lambda: self.ytdl_btn.set_state("disabled"))
        self.queue_ui(lambda: self.ytdl_btn.set_text("Đang tìm..."))
        self.queue_ui(lambda: self.ytdl_progress.configure(value=0))
        self.queue_ui(lambda: self.ytdl_count_label.configure(text=""))

        try:
            # ── Bước 1: Lấy danh sách video ──
            if mode == "Link URL":
                raw_text = self._ytdl_url_text.get("1.0", "end") if self._ytdl_url_text else ""
                urls = [u.strip() for u in raw_text.splitlines() if u.strip()]
                if not urls:
                    self.log("Vui lòng nhập ít nhất 1 link YouTube", "error")
                    return
                self.log(f"Kiểm tra {len(urls)} URL | max={max_results}/URL | dur<={max_dur_min}ph", "info")

                info_opts = {
                    "quiet": True, "no_warnings": True,
                    "noplaylist": True,           # chỉ lấy video cụ thể, không lấy playlist/mix
                    "playlistend": max_results,
                }

                def _extract_url(u):
                    try:
                        with yt_dlp.YoutubeDL(info_opts) as ydl:
                            info = ydl.extract_info(u, download=False)
                        if info.get("_type") == "playlist":
                            entries = [e for e in (info.get("entries") or []) if e]
                            return [
                                {
                                    "url": e.get("webpage_url") or e.get("url", ""),
                                    "title": e.get("title", "?"),
                                    "duration": e.get("duration") or 0,
                                }
                                for e in entries
                                if (e.get("duration") or 0) <= max_dur_sec or not e.get("duration")
                            ][:max_results]
                        else:
                            dur = info.get("duration") or 0
                            return [{"url": info.get("webpage_url") or u,
                                     "title": info.get("title", "?"),
                                     "duration": dur}]
                    except Exception as exc:
                        self.log(f"  Lỗi URL {u[:60]}: {exc}", "error")
                        return []

                from concurrent.futures import ThreadPoolExecutor as _TP, as_completed as _ac
                videos = []
                with _TP(max_workers=min(workers, len(urls))) as _exe:
                    _futs = {_exe.submit(_extract_url, u): u for u in urls}
                    for _fut in _ac(_futs):
                        videos.extend(_fut.result())
            else:
                keyword = self.ytdl_keyword.get().strip()
                if not keyword:
                    self.log("Vui lòng nhập từ khoá", "error")
                    return
                self.log(f"Tìm [{media_type}]: '{keyword}' | max={max_results} | dur<={max_dur_min}ph", "info")
                search_opts = {
                    "quiet": True, "no_warnings": True,
                    "match_filter": yt_dlp.utils.match_filter_func(f"duration <= {max_dur_sec}"),
                }
                with yt_dlp.YoutubeDL(search_opts) as ydl:
                    info = ydl.extract_info(f"ytsearch{max_results}:{keyword}", download=False)
                videos = [
                    {"url": e["webpage_url"], "title": e["title"], "duration": e.get("duration", 0)}
                    for e in (info.get("entries") or []) if e
                ]

            if not videos:
                self.log("Không tìm thấy video nào phù hợp", "error")
                return

            self.log(f"Tìm thấy {len(videos)} kết quả → lưu vào: {out_dir}", "info")
            for i, v in enumerate(videos, 1):
                dur = v["duration"] or 0
                m, s = divmod(int(dur), 60)
                dur_str = f"{m}:{s:02d}" if dur else "?"
                self.log(f"  {i}. {v['title'][:60]} ({dur_str})", "info")

            outtmpl = os.path.join(out_dir, "%(title)s.%(ext)s")

            # ── Bước 2: Tải song song ──
            import threading as _thr
            _lock       = _thr.Lock()
            _file_bytes = {}
            total_files = len(videos)

            def _make_hook(fid):
                def _hook(d):
                    if d["status"] == "downloading":
                        dl  = d.get("downloaded_bytes") or 0
                        tot = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                        with _lock:
                            _file_bytes[fid] = (dl, tot)
                        with _lock:
                            vals = list(_file_bytes.values())
                        known   = [t for _, t in vals if t > 0]
                        dl_sum  = sum(d for d, _ in vals)
                        tot_sum = sum(known) if known else 1
                        pct     = min(dl_sum / tot_sum * 100, 99)
                        done    = sum(1 for d_, t in vals if t > 0 and d_ >= t)
                        spd     = d.get("_speed_str", "").strip()
                        eta     = d.get("_eta_str", "").strip()

                        def _fmt(b):
                            if b >= 1024**3: return f"{b/1024**3:.1f} GB"
                            if b >= 1024**2: return f"{b/1024**2:.1f} MB"
                            if b >= 1024:    return f"{b/1024:.0f} KB"
                            return f"{b} B"

                        size_str = _fmt(dl_sum)
                        if tot_sum > 1 and known:
                            size_str += f" / {_fmt(tot_sum)}"

                        self.queue_ui(lambda p=pct: self.ytdl_progress.configure(value=p))
                        self.queue_ui(lambda n=done, sz=size_str, s=spd, e=eta:
                            self.ytdl_count_label.configure(
                                text="  ".join(x for x in [f"{n}/{total_files} file", sz, s, f"ETA {e}" if e else ""] if x).strip()))
                    elif d["status"] == "finished":
                        with _lock:
                            t = _file_bytes.get(fid, (0, 0))[1]
                            _file_bytes[fid] = (t, t)
                return _hook

            _dl_noplaylist = mode == "Link URL"   # chỉ enforce noplaylist khi download theo URL
            if media_type == "audio":
                def _make_opts(fid):
                    opts = {
                        "format": "bestaudio/best", "outtmpl": outtmpl,
                        "postprocessors": [{"key": "FFmpegExtractAudio",
                                            "preferredcodec": afmt,
                                            "preferredquality": aqual}],
                        "quiet": True, "no_warnings": True,
                        "progress_hooks": [_make_hook(fid)],
                    }
                    if _dl_noplaylist:
                        opts["noplaylist"] = True
                    return opts
            else:
                def _make_opts(fid):
                    opts = {
                        "format": "(bestvideo[vcodec^=avc1][height<=1080]+bestaudio[acodec^=mp4a])/best[height<=1080]",
                        "outtmpl": outtmpl,
                        "postprocessors": [{"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}],
                        "quiet": True, "no_warnings": True,
                        "progress_hooks": [_make_hook(fid)],
                    }
                    if _dl_noplaylist:
                        opts["noplaylist"] = True
                    return opts

            self.queue_ui(lambda: self.ytdl_btn.set_text("Đang tải..."))
            done_count = [0]

            def _download_one(v, fid):
                try:
                    with yt_dlp.YoutubeDL(_make_opts(fid)) as ydl:
                        ydl.download([v["url"]])
                    return "success", v["title"]
                except Exception as e:
                    return "error", v["title"], str(e)

            from concurrent.futures import ThreadPoolExecutor, as_completed as _as_completed
            with ThreadPoolExecutor(max_workers=workers) as exe:
                futures = {exe.submit(_download_one, v, i): v
                           for i, v in enumerate(videos)}
                for fut in _as_completed(futures):
                    res = fut.result()
                    done_count[0] += 1
                    n = done_count[0]
                    if res[0] == "success":
                        self.log(f"  ✓ [{n}/{total_files}] {res[1]}", "success")
                    else:
                        self.log(f"  ✗ [{n}/{total_files}] {res[1]}: {res[2]}", "error")

            self.queue_ui(lambda: self.ytdl_progress.configure(value=100))
            self.log(f"Hoàn tất {done_count[0]}/{total_files} {media_type} → {out_dir}", "success")
        except Exception as e:
            self.log(f"Lỗi tải: {e}", "error")
        finally:
            self.queue_ui(lambda: self.ytdl_btn.set_state("normal"))
            self.queue_ui(lambda: self.ytdl_btn.set_text("⬇  Tải xuống"))
            self.queue_ui(lambda: self.ytdl_progress.configure(value=0))
            self.queue_ui(lambda: self.ytdl_count_label.configure(text=""))

    def _start_crawl(self):
        threading.Thread(target=self._download_images, daemon=True).start()

    def _download_images(self):
        engine = self.crawl_engine.get()

        if engine == "Bing":
            if not BING_DL_AVAILABLE:
                self.log("Thiếu thư viện: pip install bing_image_downloader", "error")
                return
        elif engine in ("Pinterest", "Flickr"):
            if not shutil.which("gallery-dl"):
                self.log("Thiếu gallery-dl: pip install gallery-dl", "error")
                return

        keyword = self.crawl_keyword.get().strip()
        if not keyword:
            self.log("Vui lòng nhập từ khoá", "error")
            return

        layout_map = {"Ngang": "wide", "Dọc": "tall", "Vuông": "square", "Tất cả": "any"}
        layout    = layout_map.get(self.crawl_layout.get(), "wide")
        max_num   = self.crawl_max_num.get()
        min_width = self.crawl_min_width.get()

        safe = "".join(c if c.isalnum() or c in " _-" else "_" for c in keyword)[:40].strip()
        save_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"crawl_{safe}")

        self.queue_ui(lambda: self.crawl_btn.set_state("disabled"))
        self.queue_ui(lambda: self.crawl_btn.set_text("Đang tải..."))

        self.log(f"Tải ảnh [{engine}]: '{keyword}' | loại={self.crawl_layout.get()} | max={max_num} | min_px={min_width}", "info")

        try:
            if os.path.exists(save_folder):
                shutil.rmtree(save_folder, ignore_errors=True)
            os.makedirs(save_folder, exist_ok=True)

            # Reset progress bar
            self.queue_ui(lambda: self.crawl_progress.configure(value=0, maximum=max_num))
            self.queue_ui(lambda: self.crawl_count_label.configure(text=f"0 / {max_num}"))

            # Thread giám sát thư mục, log + cập nhật progress theo thời gian thực
            stop_monitor = threading.Event()
            def _monitor():
                seen = set()
                count = 0
                while not stop_monitor.is_set():
                    try:
                        # Thu thập đệ quy (gallery-dl tạo subfolder)
                        current = set()
                        for root, _, files in os.walk(save_folder):
                            for f in files:
                                current.add(os.path.join(root, f))
                        for fpath in sorted(current - seen):
                            count += 1
                            self.log(f"  [{count}/{max_num}] ↓ {os.path.basename(fpath)}", "info")
                            n = count
                            self.queue_ui(lambda v=n: self.crawl_progress.configure(value=v))
                            self.queue_ui(lambda v=n: self.crawl_count_label.configure(
                                text=f"{v} / {max_num}"))
                        seen = current
                    except Exception:
                        pass
                    stop_monitor.wait(0.5)
            threading.Thread(target=_monitor, daemon=True).start()

            if engine == "Bing":
                from bing_image_downloader import downloader as _bing_dl
                _bing_dl.download(
                    keyword,
                    limit=max_num,
                    output_dir=save_folder,
                    adult_filter_off=True,
                    force_replace=False,
                    timeout=60,
                    verbose=False,
                )

            elif engine in ("Pinterest", "Flickr"):
                import urllib.parse
                q = urllib.parse.quote(keyword)
                url = (
                    f"https://www.pinterest.com/search/pins/?q={q}"
                    if engine == "Pinterest"
                    else f"https://www.flickr.com/search/?text={q}&sort=relevance"
                )
                subprocess.run(
                    ["gallery-dl", "--dest", save_folder,
                     "--range", f"1-{max_num}", url],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=SUBPROCESS_FLAGS
                )

            stop_monitor.set()

            # Gom tất cả file từ subfolder (gallery-dl tạo subfolder) về root
            all_files = []
            for root, _, files in os.walk(save_folder):
                for f in files:
                    all_files.append(os.path.join(root, f))

            kept = deleted = 0
            for fpath in all_files:
                dest = os.path.join(save_folder, os.path.basename(fpath))
                if fpath != dest:
                    try:
                        os.rename(fpath, dest)
                        fpath = dest
                    except Exception:
                        pass
                try:
                    img = Image.open(fpath)
                    w, h = img.size
                    img.close()
                    ratio = w / h if h else 1
                    layout_ok = (
                        layout == "any"
                        or (layout == "wide"   and ratio > 1.2)
                        or (layout == "tall"   and ratio < 0.85)
                        or (layout == "square" and 0.85 <= ratio <= 1.2)
                    )
                    if w < min_width or not layout_ok:
                        os.remove(fpath)
                        deleted += 1
                    else:
                        kept += 1
                except Exception:
                    try:
                        os.remove(fpath)
                    except Exception:
                        pass
                    deleted += 1

            # Xoá subfolder trống còn sót
            for root, dirs, _ in os.walk(save_folder, topdown=False):
                for d in dirs:
                    try:
                        os.rmdir(os.path.join(root, d))
                    except Exception:
                        pass

            self.queue_ui(lambda f=save_folder: self.image_folder.set(f))
            self.log(f"Xong: {kept} ảnh đạt chuẩn, bỏ {deleted} ảnh nhỏ → {save_folder}", "success")
        except Exception as e:
            self.log(f"Lỗi tải ảnh: {e}", "error")
        finally:
            self.queue_ui(lambda: self.crawl_btn.set_state("normal"))
            self.queue_ui(lambda: self.crawl_btn.set_text("⬇  Tải ảnh"))
            self.queue_ui(lambda: self.crawl_progress.configure(value=0))
            self.queue_ui(lambda: self.crawl_count_label.configure(text=""))

    # =========================================================
    # VIDEO CLIP MODE HANDLERS
    # =========================================================

    def _toggle_clip_mode(self):
        if self.use_video_clips.get():
            self.images_card.pack_forget()
            self.clip_source_card.pack(fill="x", pady=(0, 12))
            self.clip_options_section.pack(fill="x", pady=(0, 4))
        else:
            self.clip_source_card.pack_forget()
            self.images_card.pack(fill="x", pady=(0, 12))
            self.clip_options_section.pack_forget()

    def add_clip_videos(self):
        paths = filedialog.askopenfilenames(
            title="Chọn video nguồn clip",
            filetypes=[("Video Files", "*.mp4 *.mov *.mkv *.avi *.flv")]
        )
        added = 0
        for path in paths:
            if path not in self.clip_source_paths:
                self.clip_source_paths.append(path)
                self.clip_listbox.insert("end", f"  {os.path.basename(path)}")
                added += 1
        if added:
            self.log(f"Đã thêm {added} video clip nguồn", "success")
        self._update_clip_count()

    def remove_selected_clip_videos(self):
        selected = self.clip_listbox.curselection()
        if not selected:
            self.log("Chưa chọn video clip nào để xóa", "muted")
            return
        for idx in reversed(selected):
            self.clip_source_paths.pop(idx)
            self.clip_listbox.delete(idx)
        self.log(f"Đã xóa {len(selected)} video clip", "info")
        self._update_clip_count()

    def clear_clip_videos(self):
        if not self.clip_source_paths:
            return
        count = len(self.clip_source_paths)
        self.clip_source_paths.clear()
        self.clip_listbox.delete(0, "end")
        self.log(f"Đã xóa toàn bộ {count} video clip", "info")
        self._update_clip_count()

    def _update_clip_count(self):
        self.clip_count_label.config(text=f"{len(self.clip_source_paths)} video")

    # =========================================================
    # PiP OVERLAY HANDLERS
    # =========================================================

    def _on_pip_toggle(self):
        pass  # handled by _on_pip lambda in _build_left_column

    def _add_pip_video(self):
        path = filedialog.askopenfilename(
            title="Chọn video overlay",
            filetypes=[("Video Files", "*.mp4 *.mov *.mkv *.avi *.flv")]
        )
        if not path:
            return

        pos_var  = tk.StringVar(value="bottom-right")
        size_var = tk.IntVar(value=25)

        item_frame = tk.Frame(self.pip_list_frame, bg=CARD_HEADER)
        item_frame.pack(fill="x", pady=(0, 6))

        # Row 1: filename + delete button
        row1 = tk.Frame(item_frame, bg=CARD_HEADER)
        row1.pack(fill="x", padx=8, pady=(6, 2))

        # Dùng item dict để xóa theo reference — không phụ thuộc index
        item = {"path": path, "pos_var": pos_var, "size_var": size_var,
                "frame": item_frame}

        # Pack ✕ trước để tkinter giữ chỗ cho nó; label mở rộng phần còn lại
        del_lbl = tk.Label(
            row1, text="✕", bg=CARD_HEADER, fg=DANGER,
            font=(UI_FONT, 11), cursor="hand2", padx=6
        )
        del_lbl.pack(side="right")
        del_lbl.bind("<Button-1>", lambda e, it=item: self._remove_pip_item(it))

        tk.Label(
            row1, text=f"▶ {os.path.basename(path)}", bg=CARD_HEADER,
            fg=TEXT_PRIMARY, font=(UI_FONT, 9), anchor="w"
        ).pack(side="left", fill="x", expand=True)

        # Row 2: position picker + size input
        row2 = tk.Frame(item_frame, bg=CARD_HEADER)
        row2.pack(fill="x", padx=8, pady=(2, 8))

        PositionPicker(row2, pos_var, bg=CARD_HEADER).pack(side="left")

        size_col = tk.Frame(row2, bg=CARD_HEADER)
        size_col.pack(side="left", padx=(20, 0), anchor="n")

        tk.Label(size_col, text="Kích thước (%)",
                 bg=CARD_HEADER, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 8)).pack(anchor="w")

        size_field = tk.Frame(size_col, bg=INPUT_BG)
        size_field.pack(pady=(3, 0))
        tk.Entry(
            size_field, textvariable=size_var, width=5,
            bg=INPUT_BG, fg=TEXT_PRIMARY,
            insertbackground=TEXT_PRIMARY,
            font=(UI_FONT, 11), relief="flat", bd=0,
            justify="center"
        ).pack(padx=4, pady=3)

        self.pip_items.append(item)
        self.log(f"Đã thêm overlay: {os.path.basename(path)}", "success")

    def _remove_pip_item(self, item):
        if item in self.pip_items:
            self.pip_items.remove(item)
            item["frame"].destroy()
            self.log("Đã xóa overlay", "info")

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
Style: Default,Arial,{font_size},&H00FFFFFF,&H000000FF,&H4D000000,&H4D000000,1,0,0,0,100,100,0,0,3,5,0,2,10,10,30,1

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
                text = seg["text"].replace("\n", "\\N")
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
            text=True, encoding="utf-8", errors="replace",
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

    def _extract_texts_from_transcription(self, transcription_data, audio_metadata, audio_info):
        """
        Tái sử dụng kết quả Whisper đã có (từ bước phụ đề) để lấy text cho từng
        audio segment — không cần chạy Whisper lại.
        """
        texts = {}
        for idx in sorted(audio_info.keys()):
            video_path, seg_start, seg_dur = audio_metadata[idx]
            seg_end = seg_start + seg_dur
            whisper_segs = transcription_data.get(video_path, [])
            parts = [
                seg["text"].strip()
                for seg in whisper_segs
                if seg["end"] > seg_start and seg["start"] < seg_end and seg["text"].strip()
            ]
            texts[idx] = " ".join(parts)
        return texts

    def transcribe_audio_files(self, audio_info, model_name="base",
                              step_label="", pct_start=60.0, pct_end=70.0):
        """Transcribe từng file audio segment để lấy nội dung cho AI phân tích"""
        if self.whisper_model is None or self.whisper_model_name != model_name:
            self.log(f"  Load Whisper \'{model_name}\' để phân tích nội dung...", "muted")
            self.whisper_model = whisper.load_model(model_name)
            self.whisper_model_name = model_name

        texts = {}
        sorted_keys = sorted(audio_info.keys())
        n = len(sorted_keys)
        for i, idx in enumerate(sorted_keys):
            audio_path, duration = audio_info[idx]
            pct = pct_start + (i / max(n, 1)) * (pct_end - pct_start)
            self.update_status(
                step_label or "[AI] Transcribe audio",
                f"[AI] Transcribe đoạn {i+1}/{n}...",
                pct
            )
            try:
                result = self.whisper_model.transcribe(audio_path, fp16=False)
                texts[idx] = result["text"].strip()
                preview = texts[idx][:60].replace("\n", " ")
                self.log(f"  [{i+1}/{n}] Đoạn {idx}: \"{preview}...\"", "muted")
            except Exception as e:
                texts[idx] = ""
                self.log(f"  [{i+1}/{n}] Đoạn {idx}: lỗi transcribe ({e})", "error")
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
        job_dir = self._job_dir_name
        if not os.path.exists(job_dir):
            return

        def _force_rmtree(path):
            """rmtree với onerror handler: trên Windows thử chmod rồi xóa lại."""
            def _on_err(func, fpath, exc):
                try:
                    os.chmod(fpath, 0o777)
                    func(fpath)
                except Exception:
                    pass
            shutil.rmtree(path, onerror=_on_err)

        # Thử xóa parent directory trực tiếp (đệ quy toàn bộ)
        try:
            _force_rmtree(job_dir)
            self.log(f"Đã dọn dẹp {job_dir}", "success")
            return
        except Exception:
            pass

        # Retry sau 0.5s (file handle có thể chưa release kịp)
        time.sleep(0.5)
        try:
            _force_rmtree(job_dir)
            self.log(f"Đã dọn dẹp {job_dir} (retry)", "success")
        except Exception as e:
            # Fallback: xóa từng sub-folder, bỏ qua lỗi
            for sub in [self.CACHE_IMAGE_FOLDER, self.CACHE_AUDIO_FOLDER,
                        self.CACHE_VIDEO_FOLDER, self.CACHE_SUBTITLE_FOLDER,
                        self.TEMP_FOLDER]:
                try:
                    if os.path.exists(sub):
                        _force_rmtree(sub)
                except Exception:
                    pass
            self.log(f"Dọn dẹp một phần {job_dir} ({e})", "error")
    
    # =========================================================
    # GENERATION
    # =========================================================
    
    def _request_stop(self):
        """Yêu cầu dừng generation — gọi từ UI thread."""
        self._stop_event.set()
        self.stop_btn.set_state("disabled")
        self.stop_btn.set_text("Đang dừng...")
        self.log("⏹ Yêu cầu dừng — chờ bước hiện tại hoàn thành...", "error")

    def _check_stop(self):
        """Gọi ở các checkpoint — raise nếu user đã bấm Dừng."""
        if self._stop_event.is_set():
            raise InterruptedError("Người dùng dừng quá trình tạo video")

    def _apply_audio_fades(self, segments, fade_dur,
                           step_label="Làm mịn audio", pct_start=90.0, pct_end=94.0):
        """Fade-out cuối đoạn N + fade-in đầu đoạn N+1 để chuyển cảnh audio mượt mà."""
        n = len(segments)
        result = []
        for i, seg_path in enumerate(segments):
            pct = pct_start + (i / n) * (pct_end - pct_start)
            self.update_status(step_label, f"Đoạn {i+1}/{n}...", pct)

            out_path = seg_path.replace(".mp4", "_afade.mp4")
            try:
                dur_res = subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", seg_path],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=10, creationflags=SUBPROCESS_FLAGS
                )
                dur = float(dur_res.stdout.strip())
            except Exception:
                result.append(seg_path)
                continue

            if dur < fade_dur * 2:
                result.append(seg_path)
                continue

            af_parts = []
            if i > 0:
                af_parts.append(f"afade=t=in:st=0:d={fade_dur:.3f}")
            if i < n - 1:
                af_parts.append(f"afade=t=out:st={dur - fade_dur:.3f}:d={fade_dur:.3f}")

            if not af_parts:
                result.append(seg_path)
                continue

            rc = subprocess.run(
                ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                 "-i", seg_path,
                 "-c:v", "copy", "-af", ",".join(af_parts),
                 out_path],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
                creationflags=SUBPROCESS_FLAGS
            ).returncode
            result.append(
                out_path if rc == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0
                else seg_path
            )
            if (i + 1) % 10 == 0 or i == n - 1:
                self.log(f"  [Fade] {i+1}/{n} đoạn", "muted")
        return result

    def start_generation(self):
        self._stop_event = threading.Event()
        threading.Thread(target=self.process, daemon=True).start()
    
    def process(self):
        stop_timer = None
        try:
            start_time = time.time()
            
            self.queue_ui(lambda: self.generate_btn.set_state("disabled"))
            self.queue_ui(lambda: self.generate_btn.set_text("ĐANG XỬ LÝ..."))
            self.queue_ui(lambda: self.stop_btn.set_state("normal"))
            self.queue_ui(lambda: self.stop_btn.set_text("⏹ Dừng"))
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
                raise ValueError("Vui lòng thêm ít nhất 1 video hoặc audio")

            use_video_clips = self.use_video_clips.get()
            clip_is_random = self.video_clip_random.get()

            if use_video_clips:
                if not self.clip_source_paths:
                    raise ValueError("Vui lòng thêm ít nhất 1 video nguồn clip")
            else:
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
            transition_dur  = self.transition_duration.get()
            use_transition  = self.use_transition.get()
            transition_type = self.transition_type.get()

            # "random": chọn ngẫu nhiên 1 hiệu ứng mỗi lần tạo video
            # "random" → mỗi đoạn chuyển dùng hiệu ứng khác nhau (xử lý trong apply_xfade_sequential)
            is_random_fx = use_transition and transition_type == "random"
            use_xfade    = is_random_fx or (
                use_transition and transition_type not in ("fade_black", "fade_white", "_sep", "random")
            )
            if is_random_fx:
                self.log("  🎲 Random: mỗi đoạn chuyển sẽ dùng hiệu ứng ngẫu nhiên khác nhau", "info")
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
            n_audio = sum(1 for p in self.video_paths if VideoTab._is_audio_file(p))
            n_video = len(self.video_paths) - n_audio
            self.log(f"Nguồn: {n_video} video, {n_audio} audio")
            if use_transition:
                fx_label = f"{transition_type} {transition_dur}s" + (" [xfade]" if use_xfade else " [fast]")
                self.log(f"Hiệu ứng: {fx_label}")
            else:
                self.log("Hiệu ứng: Không")
            self.log(f"Phụ đề: {'Có (Whisper ' + whisper_model_name + ', ' + sub_language + ')' if use_subtitle else 'Không'}")
            self.log(f"Cắt đầu audio: {'Có (cắt ' + str(trim_audio_secs) + 's đầu tiên)' if trim_audio_secs > 0 else 'Không'}")
            self.log(f"AI Ordering: {'Có (' + st_model + ')' if use_ai_ordering else 'Không'}")
            self.log(f"Nguồn hình: {'Video clip (' + ('ngẫu nhiên' if clip_is_random else 'mapping') + ')' if use_video_clips else 'Thư mục ảnh'}")
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
            self._check_stop()
            
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
                kind = "audio" if VideoTab._is_audio_file(video_path) else "video"
                self.log(f"  [{kind}] {os.path.basename(video_path)}: {video_duration:.1f}s -> {video_segments} đoạn")
                
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
            
            if use_video_clips:
                # Lấy duration của từng video nguồn clip
                clip_video_infos = []
                for vp in self.clip_source_paths:
                    dur = get_media_duration(vp)
                    clip_video_infos.append((vp, dur))
                    self.log(f"  Clip: {os.path.basename(vp)} ({dur:.1f}s)")
                total_clip_dur = sum(d for _, d in clip_video_infos)
                image_tasks = []
                cached_images = []
                self.log(f"[2/{total_steps}] {len(audio_tasks)} đoạn audio (nguồn: {total_input_duration:.0f}s={total_input_duration/60:.1f}ph) + {len(clip_video_infos)} video clip ({time.time()-t0:.1f}s)", "success")
                self._check_stop()
            else:
                clip_video_infos = []
                total_clip_dur = 0.0
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
                self.log(f"[2/{total_steps}] Tổng: {len(audio_tasks)} đoạn audio (nguồn: {total_input_duration:.0f}s={total_input_duration/60:.1f}ph) + {len(image_tasks)} ảnh ({time.time()-t0:.1f}s)", "success")
                self._check_stop()
            
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
                self._check_stop()
                step_offset = 1
            else:
                step_offset = 0
            
            # STEP 3 (or 4): Extract audio + cache images
            t0 = time.time()
            current_step = 3 + step_offset
            self.update_status(f"Bước {current_step}/{total_steps}: Xử lý audio + ảnh", 
                              "Trích xuất audio và resize ảnh...", 30)
            
            audio_info = {}
            total_assets = len(audio_tasks) + (0 if use_video_clips else len(image_tasks))
            completed = 0

            with ProcessPoolExecutor(max_workers=max_workers) as executor:
                audio_futures = {executor.submit(extract_audio_segment, task): ("audio", task[0])
                                for task in audio_tasks}
                if use_video_clips:
                    all_futures = audio_futures
                else:
                    image_futures = {executor.submit(preprocess_image, task): ("image", i)
                                    for i, task in enumerate(image_tasks)}
                    all_futures = {**audio_futures, **image_futures}

                for future in as_completed(all_futures):
                    task_type, task_id = all_futures[future]

                    if task_type == "audio":
                        idx, returncode, audio_path, actual_duration, stderr = future.result()
                        if returncode == 0 and os.path.exists(audio_path) and os.path.getsize(audio_path) > 0 and actual_duration > 0.5:
                            audio_info[idx] = (audio_path, actual_duration)
                        else:
                            meta = audio_metadata.get(idx, ("?", 0, 0))
                            reason = (f"rc={returncode}" if returncode != 0
                                      else f"size={os.path.getsize(audio_path) if os.path.exists(audio_path) else 0}B dur={actual_duration:.2f}s")
                            self.log(f"  [WARN] Đoạn {idx} (start={meta[1]:.0f}s) bị bỏ: {reason}", "error")
                            if stderr and returncode != 0:
                                self.log(f"         ffmpeg: {stderr.strip()[:120]}", "error")

                    completed += 1
                    pct = 30 + (completed / total_assets) * 15
                    label3 = "audio" if use_video_clips else "audio + ảnh"
                    self.update_status(
                        f"Bước {current_step}/{total_steps}: Xử lý {label3}",
                        f"Đã xử lý {completed}/{total_assets}", pct
                    )
                    if self._stop_event.is_set():
                        raise InterruptedError("Người dùng dừng quá trình tạo video")

            if use_video_clips:
                self.log(f"[{current_step}/{total_steps}] Audio: {len(audio_info)}/{len(audio_tasks)} ({time.time()-t0:.1f}s)", "success")
            else:
                self.log(f"[{current_step}/{total_steps}] Audio: {len(audio_info)}/{len(audio_tasks)}, Ảnh: {len(cached_images)} ({time.time()-t0:.1f}s)", "success")
            
            if not audio_info:
                raise ValueError("Không trích xuất được audio")
            
            # Đọc clip_sec một lần
            clip_sec = max(1.0, self.clip_duration_seconds.get()) if use_video_clips else 0.0

            # STEP 4: Mã hóa ảnh (image mode) hoặc Xác định thứ tự + Trích clip (video clip mode)
            t0 = time.time()
            current_step = 4 + step_offset

            sorted_indices = sorted(audio_info.keys())
            ordered_audio = [(idx, audio_info[idx][0], audio_info[idx][1]) for idx in sorted_indices]
            final_audio_order = None

            if use_video_clips:
                # --- 4a: Xác định thứ tự audio ---
                self.update_status(
                    f"Bước {current_step}/{total_steps}: Xác định thứ tự + Lên kế hoạch clip",
                    "Đang xác định thứ tự audio...", 45
                )

                if use_ai_ordering:
                    try:
                        if use_subtitle and transcription_data:
                            self.log("  [AI] Tái sử dụng kết quả Whisper từ bước phụ đề...", "info")
                            self.update_status(
                                f"Bước {current_step}/{total_steps}: Xác định thứ tự",
                                "[AI] Mapping text từ dữ liệu Whisper đã có...", 47.0
                            )
                            texts = self._extract_texts_from_transcription(
                                transcription_data, audio_metadata, audio_info
                            )
                        else:
                            self.log("  [AI] Transcribe từng đoạn audio...", "info")
                            texts = self.transcribe_audio_files(
                                audio_info, model_name="base",
                                step_label=f"Bước {current_step}/{total_steps}: Xác định thứ tự",
                                pct_start=45.0, pct_end=53.0
                            )
                        self.log(f"  [AI] Tính semantic similarity ({st_model})...", "info")
                        self.update_status(
                            f"Bước {current_step}/{total_steps}: Xác định thứ tự",
                            f"[AI] Tính semantic similarity ({st_model})...", 53.0
                        )
                        keep_first = self.keep_first_audio.get()
                        ai_order = self.order_by_semantic_similarity(
                            texts, sorted_indices, st_model, keep_first=keep_first
                        )
                        final_audio_order = [
                            (sorted_indices[i], audio_info[sorted_indices[i]][0], audio_info[sorted_indices[i]][1])
                            for i in ai_order
                        ]
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

                # --- 4b: Tính cumulative + lên kế hoạch clip_plan ---
                cumuls = []
                acc = 0.0
                for _, _, dur in final_audio_order:
                    cumuls.append(acc)
                    acc += dur
                total_out_dur = acc

                clip_plan = {}
                for new_idx, (orig_idx, audio_path_seg, seg_dur) in enumerate(final_audio_order):
                    assignments = []
                    if clip_is_random:
                        # Random: N clip khác nhau, clip cuối trim về đúng phần còn lại
                        n_clips = max(1, math.ceil(seg_dur / clip_sec))
                        for ci in range(n_clips):
                            # Clip cuối chỉ cần phần duration còn lại, không encode thừa
                            if ci < n_clips - 1:
                                actual_sec = clip_sec
                            else:
                                actual_sec = seg_dur - ci * clip_sec
                                actual_sec = max(0.5, actual_sec)
                            vpath, vdur = random.choice(clip_video_infos)
                            max_start = max(0.0, vdur - actual_sec)
                            cstart = random.uniform(0, max_start)
                            assignments.append((vpath, cstart, actual_sec))
                    else:
                        # Mapping: 1 clip liên tục độ dài seg_dur từ vị trí proportional
                        progress = cumuls[new_idx] / total_out_dur if total_out_dur > 0 else 0
                        target = progress * total_clip_dur
                        cumul = 0.0
                        vpath, vdur = clip_video_infos[-1]
                        cstart = max(0.0, vdur - seg_dur)
                        for fvp, fvd in clip_video_infos:
                            if target <= cumul + fvd:
                                vpath, vdur = fvp, fvd
                                raw = target - cumul
                                cstart = min(raw, max(0.0, fvd - seg_dur))
                                break
                            cumul += fvd
                        assignments.append((vpath, cstart, seg_dur))
                    clip_plan[new_idx] = assignments

                total_sub = sum(len(v) for v in clip_plan.values())
                mode_str = f"random ({clip_sec:.1f}s/clip)" if clip_is_random else "mapping"
                self.log(
                    f"[{current_step}/{total_steps}] Lên kế hoạch {len(clip_plan)} segment "
                    f"({total_sub} clip tổng, {mode_str}, {time.time()-t0:.1f}s)", "success"
                )
                valid_image_videos = []

            else:
                # Image mode: mã hóa ảnh
                self.update_status(f"Bước {current_step}/{total_steps}: Mã hóa ảnh",
                                  "Chuyển ảnh thành video clip...", 45)
                video_image_tasks = []
                cached_image_videos = []

                for idx, img_path in enumerate(cached_images):
                    vp = os.path.join(self.CACHE_VIDEO_FOLDER, f"imgvid_{idx:04d}.mp4")
                    cached_image_videos.append(vp)
                    video_image_tasks.append((img_path, vp, width, height, fps))

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
                        if self._stop_event.is_set():
                            raise InterruptedError("Người dùng dừng quá trình tạo video")

                valid_image_videos = [v for v in cached_image_videos
                                      if os.path.exists(v) and os.path.getsize(v) > 0]
                if not valid_image_videos:
                    raise ValueError("Không tạo được video từ ảnh")
                self.log(f"[{current_step}/{total_steps}] Mã hóa {len(valid_image_videos)} clip ảnh ({time.time()-t0:.1f}s)", "success")

            # STEP 5 (or 6): Build pairs + assemble
            t0 = time.time()
            current_step = 5 + step_offset
            self.update_status(f"Bước {current_step}/{total_steps}: Ghép video",
                              "Tạo các đoạn video...", 65)

            # Xác định thứ tự audio cho image mode (video clip mode đã làm ở Step 4)
            if not use_video_clips:
                if use_ai_ordering:
                    try:
                        if use_subtitle and transcription_data:
                            self.log("  [AI] Tái sử dụng kết quả Whisper từ bước phụ đề...", "info")
                            self.update_status(
                                f"Bước {current_step}/{total_steps}: Ghép video",
                                "[AI] Mapping text từ dữ liệu Whisper đã có...", 67.0
                            )
                            texts = self._extract_texts_from_transcription(
                                transcription_data, audio_metadata, audio_info
                            )
                        else:
                            self.log("  [AI] Transcribe từng đoạn audio...", "info")
                            texts = self.transcribe_audio_files(
                                audio_info, model_name="base",
                                step_label=f"Bước {current_step}/{total_steps}: Ghép video",
                                pct_start=65.0, pct_end=74.0
                            )
                        self.log(f"  [AI] Tính semantic similarity ({st_model})...", "info")
                        self.update_status(
                            f"Bước {current_step}/{total_steps}: Ghép video",
                            f"[AI] Tính semantic similarity ({st_model})...", 74.0
                        )
                        keep_first = self.keep_first_audio.get()
                        ai_order = self.order_by_semantic_similarity(
                            texts, sorted_indices, st_model, keep_first=keep_first
                        )
                        final_audio_order = [
                            (sorted_indices[i], audio_info[sorted_indices[i]][0], audio_info[sorted_indices[i]][1])
                            for i in ai_order
                        ]
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
                meta = audio_metadata[orig_idx]
                final_audio_with_meta.append(
                    (orig_idx, audio_path, duration, meta[0], meta[1])
                )

            results = {}

            if use_video_clips:
                # ── Sub-step 5a: Extract từng mini-clip song song (progress rõ) ──
                extract_tasks = []
                task_key_map = {}   # task_list_index → (new_idx, clip_i)

                for new_idx, assignments in sorted(clip_plan.items()):
                    for ci, (vpath, cstart, csec) in enumerate(assignments):
                        t_i = len(extract_tasks)
                        clip_out = os.path.join(
                            self.CACHE_VIDEO_FOLDER, f"s{new_idx:04d}c{ci:04d}.mp4"
                        )
                        extract_tasks.append(
                            (t_i, vpath, cstart, csec, clip_out, width, height, fps)
                        )
                        task_key_map[t_i] = (new_idx, ci)

                extracted_mini = {}   # (new_idx, ci) → clip_path
                total_mini = len(extract_tasks)

                self.update_status(
                    f"Bước {current_step}/{total_steps}: Trích mini-clip",
                    f"Đang trích 0/{total_mini} clip...", 65
                )

                with ProcessPoolExecutor(max_workers=max_workers) as ex:
                    mini_futures = {
                        ex.submit(extract_video_clip_only, task): task[0]
                        for task in extract_tasks
                    }
                    done_mini = 0
                    for future in as_completed(mini_futures):
                        t_i = mini_futures[future]
                        _, rc, clip_path, _ = future.result()
                        if rc == 0 and os.path.exists(clip_path) and os.path.getsize(clip_path) > 0:
                            extracted_mini[task_key_map[t_i]] = clip_path
                        done_mini += 1
                        pct = 65 + (done_mini / total_mini) * 12
                        self.update_status(
                            f"Bước {current_step}/{total_steps}: Trích mini-clip",
                            f"Đã trích {done_mini}/{total_mini} clip", pct
                        )
                        if self._stop_event.is_set():
                            raise InterruptedError("Người dùng dừng quá trình tạo video")

                self.log(
                    f"  [5a] Trích {len(extracted_mini)}/{total_mini} mini-clip xong",
                    "success"
                )

                # ── Sub-step 5b: Concat clip + mux audio (stream-copy, nhanh) ──
                self.update_status(
                    f"Bước {current_step}/{total_steps}: Concat + Mux",
                    "Ghép clip và mux audio...", 77
                )

                mux_tasks = []
                for new_idx, (orig_idx, audio_path, duration) in enumerate(final_audio_order):
                    assignments = clip_plan.get(new_idx, [])
                    clip_files = [
                        extracted_mini[(new_idx, ci)]
                        for ci in range(len(assignments))
                        if (new_idx, ci) in extracted_mini
                    ]
                    if not clip_files:
                        continue
                    out_path = os.path.join(self.TEMP_FOLDER, f"seg_{new_idx:04d}.mp4")
                    mux_tasks.append((new_idx, clip_files, audio_path, duration, out_path))

                with ProcessPoolExecutor(max_workers=max_workers) as ex:
                    mux_futures = [ex.submit(concat_and_mux_segment, task) for task in mux_tasks]
                    done_mux = 0
                    total_mux = len(mux_futures)
                    for future in as_completed(mux_futures):
                        idx, rc, vpath, _ = future.result()
                        if rc == 0 and os.path.exists(vpath) and os.path.getsize(vpath) > 0:
                            results[idx] = vpath
                        done_mux += 1
                        pct = 77 + (done_mux / total_mux) * 8
                        self.update_status(
                            f"Bước {current_step}/{total_steps}: Concat + Mux",
                            f"Đã mux {done_mux}/{total_mux} đoạn", pct
                        )
                        if self._stop_event.is_set():
                            raise InterruptedError("Người dùng dừng quá trình tạo video")

            else:
                # Image mode — giữ nguyên
                render_tasks = []
                # xfade: render segment bình thường (fade apply sau concat)
                use_fast_fade = use_transition and not use_xfade
                render_func_to_use = render_segment_with_transition if use_fast_fade else render_segment_fast
                for new_idx, (orig_idx, audio_path, duration) in enumerate(final_audio_order):
                    image_video = random.choice(valid_image_videos)
                    if use_fast_fade:
                        render_tasks.append((new_idx, image_video, audio_path, duration, fps,
                                             transition_dur, transition_type, self.TEMP_FOLDER))
                    else:
                        render_tasks.append((new_idx, image_video, audio_path, duration, fps,
                                             self.TEMP_FOLDER))

                with ProcessPoolExecutor(max_workers=max_workers) as executor_ref:
                    futures = [executor_ref.submit(render_func_to_use, task) for task in render_tasks]
                    completed = 0
                    total = len(futures)
                    for future in as_completed(futures):
                        idx, returncode, video_path, stderr = future.result()
                        if returncode == 0 and os.path.exists(video_path) and os.path.getsize(video_path) > 0:
                            results[idx] = video_path
                        else:
                            reason = (f"rc={returncode}" if returncode != 0
                                      else f"size={os.path.getsize(video_path) if os.path.exists(video_path) else 0}B")
                            self.log(f"  [WARN] Render đoạn {idx} bị bỏ: {reason}", "error")
                            if stderr and returncode != 0:
                                self.log(f"         ffmpeg: {stderr.strip()[:120]}", "error")
                        completed += 1
                        pct = 75 + (completed / total) * 10
                        self.update_status(
                            f"Bước {current_step}/{total_steps}: Ghép video",
                            f"Đã ghép {completed}/{total} đoạn", pct
                        )
                        if self._stop_event.is_set():
                            raise InterruptedError("Người dùng dừng quá trình tạo video")

            total_segs = len(final_audio_order)
            self.log(f"[{current_step}/{total_steps}] Ghép {len(results)}/{total_segs} đoạn ({time.time()-t0:.1f}s)", "success")
            
            if not results:
                raise ValueError("Không ghép được video")
            
            # STEP 6 (or 7): Burn subtitle per-segment (song song) → Concat → PiP
            t0 = time.time()
            current_step = 6 + step_offset
            sorted_results = [results[idx] for idx in sorted(results.keys())]

            use_pip_now = self.use_pip.get() and bool(self.pip_items)
            # Khi có cả PiP lẫn subtitle: apply PiP per-segment trước,
            # rồi burn subtitle lên trên → subtitle không bị PiP che
            pip_before_sub = use_pip_now and use_subtitle

            # ── 6a-pre: Apply PiP per-segment TRƯỚC khi burn subtitle (nếu cả hai) ──
            if pip_before_sub:
                pip_paths = [it["path"]         for it in self.pip_items]
                pip_pos   = [it["pos_var"].get() for it in self.pip_items]
                pip_sizes = [
                    max(2, int(width * it["size_var"].get() / 100) // 2 * 2)
                    for it in self.pip_items
                ]
                self.update_status(
                    f"Bước {current_step}/{total_steps}: PiP per-segment",
                    f"Đang apply PiP lên {len(sorted_results)} đoạn...", 83)
                pip_seg_tasks = [
                    (i, seg, pip_paths, pip_pos, pip_sizes,
                     seg.replace(".mp4", "_pipseg.mp4"), fps, BEST_ENCODER)
                    for i, seg in enumerate(sorted_results)
                ]
                total_pip_seg = len(pip_seg_tasks)
                pip_seg_results = {}
                self.log(f"  [PiP] Bắt đầu apply {total_pip_seg} đoạn...", "info")

                # Chạy tuần tự với progress realtime từ ffmpeg
                for task_i, task in enumerate(pip_seg_tasks):
                    (pi, in_path, o_paths, o_pos, o_sizes, out_path, t_fps, t_enc) = task
                    seg_num = task_i + 1
                    self.update_status(
                        f"Bước {current_step}/{total_steps}: PiP overlay",
                        f"Đoạn {seg_num}/{total_pip_seg}...", 83 + (task_i / total_pip_seg) * 2
                    )
                    self.log(f"  [PiP] Đoạn {seg_num}/{total_pip_seg}...", "muted")

                    ok, ppath, perr = _apply_pip_segment_progress(
                        in_path, o_paths, o_pos, o_sizes, out_path, t_fps, t_enc,
                        progress_cb=lambda pct, n=seg_num, tot=total_pip_seg: self.update_status(
                            f"Bước {current_step}/{total_steps}: PiP overlay",
                            f"Đoạn {n}/{tot}: {pct*100:.0f}%",
                            83 + ((n - 1 + pct) / tot) * 2
                        )
                    )
                    pip_seg_results[pi] = out_path if (
                        ok and os.path.exists(out_path) and os.path.getsize(out_path) > 0
                    ) else in_path
                    self.log(
                        f"  [PiP] Đoạn {seg_num}/{total_pip_seg} {'✓' if ok else '✗'}",
                        "success" if ok else "error"
                    )
                    if perr and not ok:
                        self.log(f"       {perr[:80]}", "error")

                sorted_results = [pip_seg_results.get(i, sorted_results[i])
                                  for i in range(len(sorted_results))]
                self.log(f"  [PiP] Hoàn thành {len(pip_seg_results)}/{total_pip_seg} đoạn", "success")

            # ── 6a: Burn subtitle per-segment song song (nếu có) ──
            if use_subtitle:
                self.update_status(f"Bước {current_step}/{total_steps}: Burn phụ đề song song",
                                   "Remap subtitle...", 85)
                new_segments = self.remap_subtitles(
                    transcription_data, final_audio_with_meta, segment_seconds
                )
                self.log(f"  Tạo {len(new_segments)} câu phụ đề trên timeline mới")
                srt_path = os.path.join(self.TEMP_FOLDER, "subtitle.srt")
                self.write_srt(new_segments, srt_path)
                final_srt = os.path.join(self._job_dir_name,
                                         os.path.basename(self.OUTPUT_VIDEO).replace(".mp4", ".srt"))
                shutil.copy(srt_path, final_srt)
                self.log(f"  Đã lưu file SRT: {final_srt}", "info")

                # Dùng sorted_results (đã bao gồm PiP nếu pip_before_sub=True)
                # key = pos_i, value = video path
                sorted_seg_map = {i: p for i, p in enumerate(sorted_results)}

                # Subtitle burn dùng libx264 thay vì hardware encoder
                # để tránh treo trên macOS khi chạy trong thread context
                sub_encoder = "libx264"

                seg_cumul = 0.0
                burn_tasks = []
                for pos_i, (orig_idx, _ap, seg_dur) in enumerate(final_audio_order):
                    seg_start = seg_cumul
                    seg_end   = seg_cumul + seg_dur

                    seg_video = sorted_seg_map.get(pos_i)
                    if seg_video is None or not os.path.exists(seg_video):
                        seg_cumul += seg_dur
                        continue

                    seg_subs = [
                        {
                            "start": max(0.0, s["start"] - seg_start),
                            "end":   min(seg_dur, s["end"]  - seg_start),
                            "text":  s["text"]
                        }
                        for s in new_segments
                        if s["end"] > seg_start and s["start"] < seg_end
                    ]
                    burned_path = os.path.join(self.TEMP_FOLDER, f"burned_{pos_i:04d}.mp4")
                    if seg_subs:
                        ass_p = os.path.join(self.TEMP_FOLDER, f"sub_{pos_i:04d}.ass")
                        self.write_ass(seg_subs, ass_p, sub_font_size, width, height)
                    else:
                        ass_p = None
                    burn_tasks.append((pos_i, seg_video, ass_p, burned_path, fps, sub_encoder))
                    seg_cumul += seg_dur

                total_burn = len(burn_tasks)
                self.log(f"  Burn phụ đề: {total_burn} đoạn...", "info")
                self.update_status(f"Bước {current_step}/{total_steps}: Burn phụ đề",
                                   f"Đang burn 0/{total_burn} đoạn...", 86)
                burned_results = {}
                # Giới hạn workers để tránh quá tải I/O khi burn subtitle
                burn_workers = min(max_workers, 4)
                with ThreadPoolExecutor(max_workers=burn_workers) as ex:
                    bfuts = {ex.submit(burn_subtitle_segment_task, t): t[0]
                             for t in burn_tasks}
                    done_b = 0
                    for future in as_completed(bfuts):
                        bi, ok, bpath, berr = future.result()
                        if ok and os.path.exists(bpath) and os.path.getsize(bpath) > 0:
                            burned_results[bi] = bpath
                        else:
                            # Fallback: dùng segment không có subtitle
                            burned_results[bi] = sorted_seg_map.get(bi, bpath)
                            if berr:
                                self.log(f"  [Sub {bi}] lỗi: {berr[:80]}", "error")
                        done_b += 1
                        pct = 86 + (done_b / max(total_burn, 1)) * 8
                        self.update_status(
                            f"Bước {current_step}/{total_steps}: Burn phụ đề",
                            f"Đã burn {done_b}/{total_burn} đoạn...", pct
                        )
                        self.log(f"  [Sub] {done_b}/{total_burn} xong", "muted")
                        if self._stop_event.is_set():
                            raise InterruptedError("Người dùng dừng quá trình tạo video")
                self.log(
                    f"[{current_step}/{total_steps}] Burn {len(burned_results)}/{total_burn} "
                    f"segment ({time.time()-t0:.1f}s)", "success"
                )
                pre_concat = [burned_results[i] for i in sorted(burned_results.keys())]
            else:
                pre_concat = sorted_results

            # ── 6b: Concat → intermediate (nếu có PiP đơn) hoặc OUTPUT_VIDEO ──
            t0 = time.time()
            # pip_before_sub: PiP đã xử lý per-segment rồi, không cần pass toàn video nữa
            pip_full_pass = use_pip_now and not pip_before_sub
            concat_target = (
                os.path.join(self.TEMP_FOLDER, "pre_pip.mp4")
                if pip_full_pass else self.OUTPUT_VIDEO
            )

            if use_xfade and not pip_full_pass:
                # xfade tự tạo output — bỏ qua concat thường
                pass
            else:
                if self.smooth_audio_transition.get() and len(pre_concat) > 1:
                    fade_dur = max(0.1, min(3.0, self.audio_fade_dur.get()))
                    self.log(f"  Làm mịn audio: fade {fade_dur}s × {len(pre_concat)} đoạn...", "info")
                    pre_concat = self._apply_audio_fades(
                        pre_concat, fade_dur,
                        step_label=f"Bước {current_step}/{total_steps}: Làm mịn audio",
                        pct_start=90.0, pct_end=94.0
                    )

                step_label_concat = "Ghép cuối" if not pip_full_pass else "Ghép trước PiP"
                self.update_status(f"Bước {current_step}/{total_steps}: {step_label_concat}",
                                   "Concat các đoạn...", 94)
                concat_file = os.path.join(self.TEMP_FOLDER, "concat_final.txt")
                with open(concat_file, "w", encoding="utf-8") as f:
                    for vp in pre_concat:
                        f.write(f"file '{normalize_path(os.path.abspath(vp))}'\n")
                result = subprocess.run(
                    ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                     "-f", "concat", "-safe", "0", "-i", concat_file,
                     "-c", "copy", "-movflags", "+faststart", concat_target],
                    stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                    creationflags=SUBPROCESS_FLAGS
                )
                if result.returncode != 0:
                    raise Exception(f"Lỗi concat: {result.stderr}")
                self.log(f"[{current_step}/{total_steps}] Concat xong ({time.time()-t0:.1f}s)", "success")

            # ── 6b2: Apply xfade transitions trên pre_concat segments (nhóm Đẹp) ──
            if use_xfade and not pip_full_pass:
                t0 = time.time()
                total_xf = len(pre_concat)
                self.log(f"  [Xfade] {transition_type} — {total_xf} đoạn...", "info")
                xfade_out = concat_target  # ghi thẳng vào concat_target

                def _xf_progress(pct):
                    self.update_status(
                        f"Bước {current_step}/{total_steps}: Xfade {transition_type}",
                        f"{int(pct * (total_xf-1))}/{total_xf-1} cặp...",
                        94 + pct * 4
                    )

                ok, err = apply_xfade_sequential(
                    pre_concat, xfade_out, transition_type, transition_dur,
                    fps, BEST_ENCODER,
                    progress_callback=_xf_progress,
                    log_callback=self.log
                )
                if ok:
                    self.log(f"  [Xfade] Hoàn thành ({time.time()-t0:.1f}s)", "success")
                else:
                    self.log(f"  [Xfade] Lỗi: {err[:80]}", "error")

            # ── 6c: Apply PiP lên video đã concat — chỉ khi KHÔNG có subtitle ──
            if pip_full_pass:
                t0 = time.time()
                pip_paths = [it["path"]         for it in self.pip_items]
                pip_pos   = [it["pos_var"].get() for it in self.pip_items]
                pip_sizes = [
                    max(2, int(width * it["size_var"].get() / 100) // 2 * 2)
                    for it in self.pip_items
                ]

                self.log(f"  PiP: {len(pip_paths)} overlay — Pass 1: pre-scale...", "info")
                self.update_status(f"Bước {current_step}/{total_steps}: PiP Overlay",
                                   f"Pass 1/{len(pip_paths)+1}: Pre-scale overlay...", 94)
                try:
                    pip_dur = get_media_duration(concat_target)

                    t_pre = time.time()

                    _pip_last_log = [0.0]
                    def _pip_cb(pct):
                        self.update_status(
                            f"Bước {current_step}/{total_steps}: PiP Overlay",
                            f"Render overlay... {pct*100:.0f}%",
                            95 + pct * 4
                        )
                        # Log mỗi 10% để thấy tiến trình trong log box
                        if pct - _pip_last_log[0] >= 0.10:
                            _pip_last_log[0] = pct
                            self.log(
                                f"  [PiP] Render {pct*100:.0f}% / {pip_dur:.0f}s",
                                "muted"
                            )

                    ok, err = apply_pip_overlays(
                        concat_target, pip_paths, pip_pos, pip_sizes,
                        self.OUTPUT_VIDEO, pip_dur, fps, BEST_ENCODER,
                        progress_callback=_pip_cb,
                        log_callback=self.log
                    )
                    if not ok:
                        raise Exception(f"Lỗi PiP: {err}")
                    self.log(f"[{current_step}/{total_steps}] PiP xong ({time.time()-t0:.1f}s)", "success")
                finally:
                    try:
                        os.remove(concat_target)
                    except Exception:
                        pass

            # BƯỚC Banner: Chèn tiêu đề top/bottom
            if self.use_banner.get():
                banners = []
                for pos, en, txt, fs, tc, bc, bo in [
                    ("top",    self.banner_top_enabled, self.banner_top_text,
                     self.banner_top_fontsize, self.banner_top_textcolor,
                     self.banner_top_bgcolor,  self.banner_top_bgopacity),
                    ("bottom", self.banner_bot_enabled, self.banner_bot_text,
                     self.banner_bot_fontsize, self.banner_bot_textcolor,
                     self.banner_bot_bgcolor,  self.banner_bot_bgopacity),
                ]:
                    if en.get() and txt.get().strip():
                        banners.append({
                            "position":  pos,
                            "text":      txt.get(),
                            "fontsize":  fs.get(),
                            "textcolor": tc.get(),
                            "bgcolor":   bc.get(),
                            "bgopacity": bo.get(),
                        })

                if banners:
                    t0 = time.time()
                    self.log(f"  Banner: {len(banners)} dòng tiêu đề...", "info")
                    self.update_status("Banner tiêu đề",
                                       "Đang chèn tiêu đề...", 98)

                    font_path = _find_drawtext_font()
                    ban_temp  = self.OUTPUT_VIDEO.replace(".mp4", "_pre_banner.mp4")
                    os.rename(self.OUTPUT_VIDEO, ban_temp)
                    try:
                        ban_dur = get_media_duration(ban_temp)

                        def _ban_cb(pct):
                            self.update_status(
                                "Banner tiêu đề",
                                f"Render banner... {pct*100:.0f}%",
                                98 + pct
                            )

                        ok, err = apply_text_banners(
                            ban_temp, self.OUTPUT_VIDEO, banners,
                            ban_dur, fps, BEST_ENCODER, font_path,
                            progress_callback=_ban_cb
                        )
                        if not ok:
                            os.rename(ban_temp, self.OUTPUT_VIDEO)
                            raise Exception(f"Lỗi banner: {err}")
                        self.log(f"  Banner xong ({time.time()-t0:.1f}s)", "success")
                    finally:
                        try:
                            os.remove(ban_temp)
                        except Exception:
                            pass

            if self.cleanup_temp.get():
                self.update_status("Dọn dẹp", "Xóa file tạm...", 99)
                self.cleanup_temp_folders()

            stop_timer.set()

            # Gợi ý tên video từ nội dung (nếu có transcription hoặc ST model)

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
            
            out_path = os.path.abspath(self.OUTPUT_VIDEO)
            out_dir  = os.path.dirname(out_path)
            msg = (
                f"Đã tạo video trong {total_time:.1f} giây!\n\n"
                f"File: {self.OUTPUT_VIDEO}\n"
                f"Kích thước: {file_size:.2f} MB\n"
                f"Thời lượng: {output_duration:.1f}s\n"
                f"Tốc độ: {total_input_duration/total_time:.1f}x realtime"
            )



            def _show_done(msg=msg, out_dir=out_dir, cur_out=self.OUTPUT_VIDEO):
                dlg = tk.Toplevel(self._root_window)
                dlg.title("Hoàn thành")
                dlg.configure(bg=APP_BG)
                dlg.geometry("440x300")
                dlg.resizable(False, False)
                dlg.grab_set()
                dlg.after(10, lambda: _center_window(dlg, self._root_window))

                body = tk.Frame(dlg, bg=APP_BG)
                body.pack(fill="both", expand=True, padx=24, pady=18)

                tk.Label(body, text="✓  Tạo video thành công!",
                         bg=APP_BG, fg=SUCCESS,
                         font=(UI_FONT, 13, "bold")).pack(anchor="w", pady=(0, 8))
                tk.Label(body, text=msg, bg=APP_BG, fg=TEXT_SECONDARY,
                         font=(UI_FONT, 9), justify="left").pack(anchor="w")

                btn_row = tk.Frame(dlg, bg=APP_BG)
                btn_row.pack(side="bottom", fill="x", padx=24, pady=(0, 18))

                def _open_folder():
                    if IS_MAC:   subprocess.Popen(["open", out_dir])
                    elif IS_WINDOWS: subprocess.Popen(["explorer", out_dir],
                                                      creationflags=SUBPROCESS_FLAGS)
                    else:        subprocess.Popen(["xdg-open", out_dir])
                    dlg.destroy()

                FlatButton(btn_row, "📂  Mở thư mục output", command=_open_folder,
                           height=42, font=(UI_FONT, 11, "bold"), padx=24,
                           bg=BTN_SECONDARY, hover_bg=BTN_SEC_HOVER, pressed_bg=DIVIDER
                           ).pack(fill="x", pady=(0, 6))
                FlatButton(btn_row, "Đóng", command=dlg.destroy,
                           height=34, font=(UI_FONT, 10), padx=24,
                           bg=BTN_SECONDARY, hover_bg=BTN_SEC_HOVER, pressed_bg=DIVIDER
                           ).pack(fill="x")

            self.queue_ui(_show_done)
            
        except InterruptedError as e:
            self.log(f"\n⏹ ĐÃ DỪNG: {e}", "error")
            self.update_status("Đang dọn dẹp...", "Xóa file tạm...", 0)
            self.cleanup_temp_folders()
            self.update_status("Đã dừng", "Người dùng hủy — đã xóa file tạm", 0)
            self.log("  Đã xóa file tạm.", "muted")
        except Exception as e:
            error_msg = str(e)
            self.log(f"\nLỖI: {error_msg}", "error")
            self.update_status("Có lỗi xảy ra", error_msg, 0)
            self.queue_ui(lambda msg=error_msg: messagebox.showerror("Lỗi", msg,
                                                                     parent=self._root_window))
        finally:
            if stop_timer:
                stop_timer.set()
            self._stop_event.clear()
            self.queue_ui(lambda: self.generate_btn.set_state("normal"))
            self.queue_ui(lambda: self.generate_btn.set_text("▶  TẠO VIDEO"))
            self.queue_ui(lambda: self.stop_btn.set_state("disabled"))
            self.queue_ui(lambda: self.stop_btn.set_text("⏹ Dừng"))


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

        edit = tk.Label(inner, text="✎",
                        bg=APP_BG, fg=TEXT_MUTED,
                        font=(UI_FONT, 12), cursor="hand2", padx=6)
        edit.pack(side="left", ipady=6)

        lbl = tk.Label(inner, text=f"{title}  ",
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
                               lbl=lbl, edit=edit, close=close,
                               content=content, rename_cb=None, title=title)
        self._order.append(tid)

        # Bindings
        for w in (grp, inner, lbl):
            w.bind("<Button-1>", lambda e, t=tid: self.select(t))
            w.bind("<Enter>",    lambda e, t=tid: self._hover(t, True))
            w.bind("<Leave>",    lambda e, t=tid: self._hover(t, False))
        edit.bind("<Button-1>", lambda e, t=tid: self._start_rename(t))
        edit.bind("<Enter>",    lambda e: edit.configure(fg=ACCENT))
        edit.bind("<Leave>",    lambda e, t=tid: self._restore_edit(t))
        close.bind("<Button-1>", lambda e, t=tid: self._close(t))
        close.bind("<Enter>",    lambda e: close.configure(fg=DANGER))
        close.bind("<Leave>",    lambda e, t=tid: self._restore_close(t))

        self.select(tid)
        return tid, content

    def set_rename_callback(self, tid, cb):
        self._tabs[tid]["rename_cb"] = cb

    def rename_tab(self, tid, new_title):
        t = self._tabs[tid]
        t["title"] = new_title
        t["lbl"].configure(text=f"{new_title}  ")
        if t["rename_cb"]:
            t["rename_cb"](new_title)

    def _start_rename(self, tid):
        t = self._tabs[tid]
        lbl = t["lbl"]
        current = t["title"]

        edit  = t["edit"]
        close = t["close"]
        inner = t["inner"]

        lbl.pack_forget()
        edit.pack_forget()
        close.pack_forget()

        entry = tk.Entry(inner, bg=CARD_HEADER, fg=TEXT_PRIMARY,
                         insertbackground=TEXT_PRIMARY,
                         font=(UI_FONT, 10), relief="flat", borderwidth=0,
                         width=max(8, len(current) + 2))
        entry.insert(0, current)
        entry.select_range(0, "end")
        entry.pack(side="left", ipady=5, padx=4)
        close.pack(side="left", ipady=6)
        entry.focus_force()

        done  = [False]
        root  = inner.winfo_toplevel()
        cbid  = [None]

        def _restore():
            close.pack_forget()
            edit.pack(side="left", ipady=6)
            lbl.pack(side="left", ipady=6)
            close.pack(side="left", ipady=6)
            if cbid[0]:
                try:
                    root.unbind("<Button-1>", cbid[0])
                except Exception:
                    pass

        def _commit(e=None):
            if done[0] or not entry.winfo_exists():
                return
            done[0] = True
            new = entry.get().strip() or current
            entry.destroy()
            _restore()
            self.rename_tab(tid, new)

        def _cancel(e=None):
            if done[0] or not entry.winfo_exists():
                return
            done[0] = True
            entry.destroy()
            _restore()

        def _on_click_away(e):
            if entry.winfo_exists() and e.widget is not entry:
                _commit()

        entry.bind("<Return>",   _commit)
        entry.bind("<Escape>",   _cancel)
        entry.bind("<FocusOut>", _commit)
        cbid[0] = root.bind("<Button-1>", _on_click_away, add="+")

    # ------------------------------------------------------------------
    def select(self, tid):
        self._active = tid
        for t_id, t in self._tabs.items():
            on = (t_id == tid)
            bg = CARD_HEADER if on else APP_BG
            for k in ("grp", "inner", "lbl", "edit", "close"):
                if k in t:
                    t[k].configure(bg=bg)
            t["lbl"].configure(fg=TEXT_PRIMARY if on else TEXT_SECONDARY)
            t["edit"].configure(fg=TEXT_SECONDARY if on else TEXT_MUTED)
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
        for k in ("grp", "inner", "lbl", "edit", "close"):
            if k in self._tabs[tid]:
                self._tabs[tid][k].configure(bg=bg)

    def _restore_edit(self, tid):
        fg = TEXT_SECONDARY if tid == self._active else TEXT_MUTED
        self._tabs[tid]["edit"].configure(fg=fg)

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
# MEDIA CUTTER DIALOG
# =========================================================

class MediaCutterDialog:
    """Modal popup for cutting video/audio with in-app preview player."""

    _PAD    = 4    # canvas horizontal padding (nhỏ để handle đi sát cạnh)
    _HDL    = 8    # handle half-width
    _BAR_H  = 64   # timeline bar height (taller for waveform visibility)
    _TICK_H = 20   # tick row height
    _VID_H  = 290  # video preview canvas height
    _FPS    = 25   # target playback fps

    def __init__(self, root):
        self.root = root
        self.top  = tk.Toplevel(root)
        self.top.title("Cắt Video / Audio")
        self.top.geometry("880x860")
        self.top.configure(bg=APP_BG)
        self.top.resizable(True, True)
        self.top.grab_set()
        self.top.after(10, lambda: _center_window(self.top, root))
        self.top.protocol("WM_DELETE_WINDOW", self._on_close)

        self.media_path       = ""
        self.media_type       = tk.StringVar(value="video")
        self.has_video_stream = False
        self.duration         = 0.0
        self.start_sec        = 0.0
        self.end_sec          = 0.0
        self.output_path      = tk.StringVar()
        self._speed_var       = tk.StringVar(value="1x")

        # Playback state
        self._playing          = False
        self._pos_sec          = 0.0
        self._play_start_wall  = 0.0
        self._play_start_pos   = 0.0
        self._video_proc       = None
        self._audio_proc       = None
        self._audio_ffmpeg_proc = None
        self._video_session    = 0      # counter để tránh race condition

        # Crop
        self._orig_w          = 0
        self._orig_h          = 0
        self._crop_enabled    = tk.BooleanVar(value=False)
        self._crop_x          = tk.IntVar(value=0)
        self._crop_y          = tk.IntVar(value=0)
        self._crop_w          = tk.IntVar(value=0)
        self._crop_h          = tk.IntVar(value=0)
        self._crop_rect_id    = None
        self._crop_drag_start = None  # (canvas_x, canvas_y) khi bắt đầu kéo

        # Timeline view (zoom/pan)
        self._view_start       = 0.0   # thời gian đầu vùng hiển thị
        self._view_end         = 0.0   # thời gian cuối vùng hiển thị

        # Display
        self._drag_handle      = None
        self._waveform_photo   = None
        self._waveform_data    = None
        self._current_photo    = None
        self._waveform_tmp     = "_mc_waveform.png"

        # Scrubbing (realtime preview khi kéo playhead)
        self._scrub_after      = None   # after() job id
        self._scrub_busy       = False  # đang extract frame
        self._display_gen      = 0      # vô hiệu hoá thread cũ khi đổi file



        self._build()
        self.top.after(50, self._tick)

    # ─────────────────────── TICK (position updater) ─────────────────
    def _tick(self):
        if not self.top.winfo_exists():
            return
        # Không cập nhật _pos_sec khi đang kéo playhead (tránh snap-back)
        if self._playing and self._drag_handle != "pos":
            elapsed = time.time() - self._play_start_wall
            speed = float(self._speed_var.get().rstrip("x"))
            self._pos_sec = min(self._play_start_pos + elapsed * speed, self.end_sec)
            self._update_pos_display()
            if self._pos_sec >= self.end_sec:
                self.top.after(0, self._on_playback_end)
        self.top.after(50, self._tick)

    # ─────────────────────── CLOSE ────────────────────────────────────
    def _on_close(self):
        self._stop_all()
        for tmp in [self._waveform_tmp, getattr(self, "_preview_tmp", None)]:
            try:
                if tmp and os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
        try:
            self.top.destroy()
        except Exception:
            pass

    # ─────────────────────── BUILD UI ─────────────────────────────────
    def _build(self):
        hdr = tk.Frame(self.top, bg=CARD_HEADER, height=50)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="✂  Cắt Video / Audio", bg=CARD_HEADER,
                 fg=TEXT_PRIMARY, font=(UI_FONT, 13, "bold")
                 ).pack(side="left", padx=18, pady=8)
        tk.Label(hdr, text="Kéo handle | Nhập thời gian | ▶ Play xem trước",
                 bg=CARD_HEADER, fg=TEXT_MUTED, font=(UI_FONT, 8)
                 ).pack(side="left")

        body = tk.Frame(self.top, bg=APP_BG)
        body.pack(fill="both", expand=True, padx=14, pady=10)

        # ── Source ──────────────────────────────────────────────────────
        src = tk.Frame(body, bg=CARD_BG)
        src.pack(fill="x", pady=(0, 8))

        file_row = tk.Frame(src, bg=CARD_BG)
        file_row.pack(fill="x", padx=10, pady=(6, 6))
        self.type_badge = tk.Label(file_row, text="—", bg=CARD_BG,
                                   fg=TEXT_MUTED, font=(UI_FONT, 8, "bold"),
                                   padx=6, pady=2)
        self.type_badge.pack(side="left", padx=(4, 4), pady=4)
        self.file_entry = tk.Entry(file_row, bg=CARD_BG, fg=TEXT_PRIMARY,
                                   insertbackground=TEXT_PRIMARY,
                                   font=(UI_FONT, 9), relief="flat", bd=0,
                                   highlightthickness=0)
        self.file_entry.pack(side="left", fill="x", expand=True, padx=(0, 4), pady=3)
        FlatButton(file_row, "Duyệt...", command=self._browse_file,
                   width=78, height=28, padx=8,
                   font=(UI_FONT, 9, "bold")).pack(side="right", padx=4, pady=3)

        self.info_label = tk.Label(src, text="Chưa chọn file",
                                   bg=CARD_BG, fg=TEXT_MUTED,
                                   font=(UI_FONT, 8), anchor="w")
        self.info_label.pack(fill="x", padx=12, pady=(0, 6))

        # ── Video / Audio preview canvas ─────────────────────────────────
        self.video_canvas = tk.Canvas(body, bg="#000000", height=self._VID_H,
                                      highlightthickness=0)
        self.video_canvas.pack(fill="x")
        self.video_canvas.bind("<Configure>", self._on_vid_resize)
        self._draw_vid_placeholder()

        # ── Playback controls ─────────────────────────────────────────────
        ctrl = tk.Frame(body, bg="#09101A")
        ctrl.pack(fill="x")
        ctrl_in = tk.Frame(ctrl, bg="#09101A")
        ctrl_in.pack(side="left", padx=10, pady=7)

        self.play_btn = FlatButton(
            ctrl_in, "▶", command=self._toggle_play,
            width=46, height=38, padx=4, font=(UI_FONT, 18)
        )
        self.play_btn.pack(side="left", padx=(0, 6))

        FlatButton(ctrl_in, "⏹", command=self._stop_and_reset,
                   bg=BTN_SECONDARY, hover_bg=BTN_SEC_HOVER, pressed_bg=DIVIDER,
                   width=38, height=38, padx=4,
                   font=(UI_FONT, 14)).pack(side="left", padx=(0, 14))

        self.pos_label = tk.Label(ctrl_in, text="00:00:00.000 / 00:00:00.000",
                                   bg="#09101A", fg=TEXT_PRIMARY,
                                   font=(MONO_FONT, 10))
        self.pos_label.pack(side="left")

        # ── Timeline canvas ───────────────────────────────────────────────
        tl_wrap = tk.Frame(body, bg=DIVIDER)
        tl_wrap.pack(fill="x", pady=(8, 6))
        self.canvas = tk.Canvas(tl_wrap, bg=CARD_HEADER,
                                height=self._BAR_H + self._TICK_H,
                                highlightthickness=0, cursor="hand2")
        self.canvas.pack(fill="x", padx=1, pady=1)
        self.canvas.bind("<Configure>",       lambda e: self._draw_timeline())
        self.canvas.bind("<Button-1>",        self._on_canvas_click)
        self.canvas.bind("<B1-Motion>",       self._on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_canvas_release)
        self.canvas.bind("<MouseWheel>",      self._on_tl_scroll)   # Mac/Win
        self.canvas.bind("<Button-4>",        self._on_tl_scroll)   # Linux
        self.canvas.bind("<Button-5>",        self._on_tl_scroll)   # Linux
        self.canvas.bind("<Double-Button-1>", self._on_tl_dblclick) # reset zoom

        # ── Time inputs ───────────────────────────────────────────────────
        time_card = tk.Frame(body, bg=CARD_BG)
        time_card.pack(fill="x", pady=(0, 8))
        time_in = tk.Frame(time_card, bg=CARD_BG)
        time_in.pack(padx=10, pady=8, fill="x")
        for lbl_text, attr, fn in [
            ("Bắt đầu:", "start_entry", self._on_start_entry),
            ("Kết thúc:", "end_entry",  self._on_end_entry),
        ]:
            tk.Label(time_in, text=lbl_text, bg=CARD_BG, fg=TEXT_SECONDARY,
                     font=(UI_FONT, 9)).pack(side="left", padx=(0, 4))
            ef = tk.Frame(time_in, bg=BORDER_COLOR, bd=0)
            ef.pack(side="left", padx=(0, 18))
            entry = tk.Entry(ef, bg=INPUT_BG, fg=TEXT_PRIMARY,
                             insertbackground=TEXT_PRIMARY,
                             font=(MONO_FONT, 10), relief="flat", bd=0,
                             width=13, justify="center",
                             highlightthickness=1,
                             highlightbackground=BORDER_COLOR,
                             highlightcolor=ACCENT)
            entry.pack(padx=1, pady=1)
            entry.insert(0, "00:00:00.000")
            entry.bind("<Return>",   fn)
            entry.bind("<FocusOut>", fn)
            setattr(self, attr, entry)
        self.seg_dur_label = tk.Label(time_in, text="Độ dài: —",
                                      bg=CARD_BG, fg=TEXT_MUTED, font=(UI_FONT, 9))
        self.seg_dur_label.pack(side="right", padx=(0, 4))

        # Tốc độ (speed) — cùng hàng, pack right
        _spd_cb_f = tk.Frame(time_in, bg=CARD_HEADER)
        _spd_cb_f.pack(side="right", padx=(0, 8))
        _SPEEDS = ["0.25x", "0.5x", "0.75x", "1x", "1.25x", "1.5x", "2x", "3x", "4x"]
        _spd_cb = ttk.Combobox(_spd_cb_f, textvariable=self._speed_var,
                                values=_SPEEDS, state="readonly",
                                font=(UI_FONT, 9), width=6,
                                style="Modern.TCombobox", takefocus=0)
        _spd_cb.pack(padx=4, pady=2)
        _spd_cb.bind("<<ComboboxSelected>>", lambda e: self._on_speed_change())
        tk.Label(time_in, text="Tốc độ:", bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 9)).pack(side="right", padx=(0, 4))

        # ── Crop (video only, hiện khi load video) ───────────────────────
        self._crop_frame = tk.Frame(body, bg=CARD_BG)
        # nội dung crop frame
        crop_hdr = tk.Frame(self._crop_frame, bg=CARD_BG)
        crop_hdr.pack(fill="x", padx=10, pady=(6, 4))
        ToggleSwitch(crop_hdr, "✂  Crop khung hình", self._crop_enabled,
                     command=self._on_crop_toggle, bg=CARD_BG).pack(side="left")
        tk.Label(crop_hdr, text="(kéo chuột trên preview để chọn vùng)",
                 bg=CARD_BG, fg=TEXT_MUTED, font=(UI_FONT, 8)
                 ).pack(side="left", padx=(10, 0))

        self._crop_inputs = tk.Frame(self._crop_frame, bg=CARD_BG)
        ci = self._crop_inputs
        for lbl, var in [("X:", self._crop_x), ("Y:", self._crop_y),
                          ("W:", self._crop_w), ("H:", self._crop_h)]:
            tk.Label(ci, text=lbl, bg=CARD_BG, fg=TEXT_SECONDARY,
                     font=(UI_FONT, 9)).pack(side="left", padx=(8, 2))
            _ef = tk.Frame(ci, bg=CARD_HEADER)
            _ef.pack(side="left", padx=(0, 6))
            e = tk.Entry(_ef, textvariable=var, width=6,
                         bg=CARD_HEADER, fg=TEXT_PRIMARY,
                         insertbackground=TEXT_PRIMARY,
                         font=(MONO_FONT, 9), relief="flat", bd=0,
                         justify="center",
                         highlightthickness=1,
                         highlightbackground=CARD_HEADER,
                         highlightcolor=ACCENT)
            e.pack(padx=4, pady=3)
            var.trace_add("write", lambda *_: self._draw_crop_rect())

        FlatButton(ci, "Reset", command=self._reset_crop,
                   bg=BTN_SECONDARY, hover_bg=BTN_SEC_HOVER, pressed_bg=DIVIDER,
                   width=60, height=26, font=(UI_FONT, 8)
                   ).pack(side="left", padx=(4, 0))

        self._crop_size_label = tk.Label(ci, text="", bg=CARD_BG,
                                         fg=TEXT_MUTED, font=(UI_FONT, 8))
        self._crop_size_label.pack(side="right", padx=(0, 10))

        # bind canvas mouse cho crop drag
        self.video_canvas.bind("<ButtonPress-1>",   self._on_crop_press)
        self.video_canvas.bind("<B1-Motion>",        self._on_crop_drag)
        self.video_canvas.bind("<ButtonRelease-1>",  self._on_crop_release)

        # ── Output ────────────────────────────────────────────────────────
        out_f = tk.Frame(body, bg=CARD_HEADER)
        out_f.pack(fill="x", pady=(0, 6))
        tk.Label(out_f, text="Lưu:", bg=CARD_HEADER, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 9)).pack(side="left", padx=(8, 4))
        self.out_entry = tk.Entry(out_f, textvariable=self.output_path,
                                  bg=CARD_HEADER, fg=TEXT_PRIMARY,
                                  insertbackground=TEXT_PRIMARY,
                                  font=(UI_FONT, 9), relief="flat", bd=0,
                                  highlightthickness=0)
        self.out_entry.pack(side="left", fill="x", expand=True, pady=5)
        FlatButton(out_f, "...", command=self._browse_output,
                   width=36, height=28, padx=4,
                   font=(UI_FONT, 10, "bold")).pack(side="right", padx=4, pady=3)

        # ── Status + progress + action buttons ───────────────────────────
        self.status_label = tk.Label(body, text="", bg=APP_BG, fg=TEXT_MUTED,
                                     font=(UI_FONT, 8), anchor="w")
        self.status_label.pack(fill="x", side="bottom", pady=(2, 0))

        self._cut_progress_var = tk.DoubleVar(value=0)
        prog_row = tk.Frame(body, bg=APP_BG)
        prog_row.pack(fill="x", side="bottom", pady=(0, 4))
        self._cut_pct_label = tk.Label(prog_row, text="", bg=APP_BG,
                                       fg=TEXT_SECONDARY, font=(UI_FONT, 9), width=5)
        self._cut_pct_label.pack(side="right", padx=(6, 0))
        self._cut_progress_bar = ttk.Progressbar(
            prog_row, variable=self._cut_progress_var,
            maximum=100, mode="determinate",
            style="Modern.Horizontal.TProgressbar"
        )
        self._cut_progress_bar.pack(fill="x", expand=True, ipady=2)

        btn_row = tk.Frame(body, bg=APP_BG)
        btn_row.pack(fill="x", side="bottom", pady=(0, 4))
        self.cut_btn = FlatButton(btn_row, "✂  Cắt & Lưu", command=self._do_cut,
                                  height=40, font=(UI_FONT, 11, "bold"))
        self.cut_btn.pack(side="left", fill="x", expand=True, padx=(0, 8))
        FlatButton(btn_row, "Đóng", command=self._on_close,
                   bg=BTN_SECONDARY, hover_bg=BTN_SEC_HOVER, pressed_bg=DIVIDER,
                   height=40, font=(UI_FONT, 10, "bold"),
                   width=100).pack(side="right")

        self._draw_timeline()

    # ─────────────────────── BROWSE ───────────────────────────────────
    def _browse_file(self):
        ftypes = [
            ("Video & Audio",
             "*.mp4 *.avi *.mkv *.mov *.wmv *.flv *.webm *.m4v "
             "*.mp3 *.m4a *.wav *.flac *.aac *.ogg *.opus"),
            ("Video", "*.mp4 *.avi *.mkv *.mov *.wmv *.flv *.webm *.m4v"),
            ("Audio", "*.mp3 *.m4a *.wav *.flac *.aac *.ogg *.opus"),
            ("Tất cả", "*.*"),
        ]
        path = filedialog.askopenfilename(parent=self.top, filetypes=ftypes,
                                          title="Chọn Video hoặc Audio")
        if not path:
            return
        self._stop_all()
        self.media_path = path
        self.file_entry.delete(0, "end")
        self.file_entry.insert(0, path)
        self._reset_for_new_file()
        self._load_media_info()

    def _reset_for_new_file(self):
        """Reset toàn bộ UI về trạng thái ban đầu khi load file mới."""
        # State
        self.duration         = 0.0
        self.start_sec        = 0.0
        self.end_sec          = 0.0
        self._pos_sec         = 0.0
        self._view_start      = 0.0
        self._view_end        = 0.0
        self.has_video_stream = False
        self._waveform_data   = None
        self._waveform_photo  = None
        self._current_photo   = None
        self._drag_handle     = None
        self._video_session  += 1      # vô hiệu hoá video thread cũ
        self._display_gen    += 1      # vô hiệu hoá audio display thread cũ
        self._scrub_busy      = False
        if self._scrub_after:
            try:
                self.top.after_cancel(self._scrub_after)
            except Exception:
                pass
            self._scrub_after = None
        # UI
        self.type_badge.config(text="—", fg=TEXT_MUTED)
        self.info_label.config(text="Đang đọc thông tin...", fg=TEXT_MUTED)
        self._draw_vid_placeholder()
        self._draw_timeline()
        self._update_entries()
        self._update_dur_label()
        self._set_play_icon(False)
        self.pos_label.config(text="00:00:00.000 / 00:00:00.000")

    def _browse_output(self):
        # Tự chọn format dựa trên file nguồn
        if self.has_video_stream:
            ftypes = [("MP4", "*.mp4"), ("MKV", "*.mkv"), ("Tất cả", "*.*")]
            defext = ".mp4"
        else:
            ftypes = [("MP3", "*.mp3"), ("M4A", "*.m4a"), ("WAV", "*.wav"),
                      ("Tất cả", "*.*")]
            defext = ".mp3"
        init = (os.path.basename(self.output_path.get())
                if self.output_path.get() else "output_cut")
        path = filedialog.asksaveasfilename(parent=self.top,
                                            defaultextension=defext,
                                            filetypes=ftypes, initialfile=init)
        if path:
            self.output_path.set(path)

    # ─────────────────────── LOAD MEDIA INFO ──────────────────────────
    def _load_media_info(self):
        if not self.media_path or not os.path.exists(self.media_path):
            return
        self.info_label.config(text="Đang đọc thông tin...", fg=TEXT_MUTED)
        self._waveform_data  = None
        self._waveform_photo = None
        self._current_photo  = None
        self._pos_sec        = 0.0
        self.top.update_idletasks()
        try:
            cmd = ["ffprobe", "-v", "error",
                   "-show_entries", "format=duration,format_name",
                   "-show_entries", "stream=codec_name,codec_type,width,height",
                   "-of", "json", self.media_path]
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                 timeout=15, creationflags=SUBPROCESS_FLAGS)
            data = json.loads(res.stdout)
            self.duration = float(data.get("format", {}).get("duration", 0))
            fmt    = data.get("format", {}).get("format_name", "?")
            streams = data.get("streams", [])
            codecs = [s.get("codec_name", "?") for s in streams]
            self.has_video_stream = any(
                s.get("codec_type") == "video" for s in streams)
            # Lấy kích thước video gốc cho crop
            self._orig_w, self._orig_h = 0, 0
            for s in streams:
                if s.get("codec_type") == "video":
                    self._orig_w = s.get("width", 0)
                    self._orig_h = s.get("height", 0)
                    break
            # Cập nhật badge loại file
            if self.has_video_stream:
                self.type_badge.config(text="VIDEO", fg=ACCENT, bg=CARD_HEADER)
            else:
                self.type_badge.config(text="AUDIO", fg=SUCCESS, bg=CARD_HEADER)
            self.info_label.config(
                text=(f"Thời lượng: {self._fmt_hhmmss(self.duration)}   |   "
                      f"Định dạng: {fmt}   |   Codec: {', '.join(codecs)}"),
                fg=TEXT_SECONDARY
            )
            self.start_sec   = 0.0
            self.end_sec     = self.duration
            self._pos_sec    = 0.0
            self._view_start = 0.0
            self._view_end   = self.duration
            self._update_entries()
            self._update_dur_label()
            base, ext = os.path.splitext(self.media_path)
            self.output_path.set(f"{base}_cut{ext}")

            self._draw_timeline()
            threading.Thread(target=self._gen_waveform, daemon=True).start()
            # Hiện / ẩn crop section
            if self.has_video_stream and self._orig_w and self._orig_h:
                self._crop_frame.pack(fill="x", pady=(0, 8), before=self.out_entry.master)
                self._reset_crop()
            else:
                self._crop_frame.pack_forget()
                self._crop_enabled.set(False)
        except Exception as ex:
            self.info_label.config(text=f"Lỗi đọc file: {ex}", fg=DANGER)
            self.duration = 0.0
            self._draw_timeline()
            return

        if self.has_video_stream:
            self._show_frame_at(0.0)
        else:
            self._draw_audio_display()

    # ─────────────────────── WAVEFORM ─────────────────────────────────
    def _gen_waveform(self):
        try:
            cw    = max(self.canvas.winfo_width(), 840) - 2 * self._PAD
            h     = self._BAR_H
            color = ACCENT.lstrip("#")
            # showwavespic scale=log cho waveform đầy đặn, trải đều theo chiều cao
            cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", self.media_path,
                "-filter_complex",
                f"[0:a]showwavespic=s={cw}x{h}:colors=#{color}:scale=log[wv]",
                "-map", "[wv]", "-frames:v", "1", self._waveform_tmp
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                 timeout=30, creationflags=SUBPROCESS_FLAGS)
            if res.returncode == 0 and os.path.exists(self._waveform_tmp):
                self._waveform_data = Image.open(self._waveform_tmp).convert("RGB")
            else:
                self._waveform_data = None
        except Exception:
            self._waveform_data = None
        self.top.after(0, self._draw_timeline)

    # ─────────────────────── VIDEO CANVAS DISPLAY ─────────────────────
    def _draw_vid_placeholder(self):
        c = self.video_canvas
        c.delete("all")
        cw = max(c.winfo_width(), 100)
        c.create_rectangle(0, 0, cw, self._VID_H, fill="#000000", outline="")
        c.create_text(cw // 2, self._VID_H // 2,
                      text="▶  Chọn file để xem preview",
                      fill=TEXT_MUTED, font=(UI_FONT, 13))

    def _draw_audio_display(self):
        c = self.video_canvas
        c.delete("all")
        cw = max(c.winfo_width(), 400)
        ch = self._VID_H
        c.create_rectangle(0, 0, cw, ch, fill="#050A10", outline="")
        c.create_text(cw // 2, ch // 2, text="♪",
                      fill=ACCENT, font=(UI_FONT, 52))
        c.create_text(cw // 2, ch // 2 + 55, text="Đang tải waveform...",
                      fill=TEXT_MUTED, font=(UI_FONT, 10))
        c.update()   # hiển thị ngay
        gen = self._display_gen
        threading.Thread(target=self._gen_audio_display,
                         args=(cw, ch, gen), daemon=True).start()

    def _gen_audio_display(self, cw, ch, gen):
        try:
            color = ACCENT.lstrip("#")
            tmp   = "_mc_audio_display.png"
            cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", self.media_path,
                "-filter_complex",
                f"[0:a]showwavespic=s={cw}x{ch}:colors=#{color}:scale=log[wv]",
                "-map", "[wv]", "-frames:v", "1", tmp
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                 timeout=30, creationflags=SUBPROCESS_FLAGS)
            if res.returncode == 0 and os.path.exists(tmp):
                photo = ImageTk.PhotoImage(
                    Image.open(tmp).convert("RGB"), master=self.video_canvas)
                def _upd(p=photo):
                    if self._display_gen == gen:
                        self.video_canvas.delete("all")
                        self.video_canvas.create_image(0, 0, image=p, anchor="nw")
                        self._current_photo = p
                self.top.after(0, _upd)
        except Exception:
            pass

    def _on_vid_resize(self, event=None):
        if not self.media_path:
            self._draw_vid_placeholder()
        # Không tự gọi lại khi resize để tránh vòng lặp — chỉ load_media_info gọi

    def _show_frame_at(self, t):
        """Hiển thị frame tại thời điểm t — chạy trong background thread."""
        if not self.media_path or not self.has_video_stream:
            return
        c   = self.video_canvas
        cw  = max(c.winfo_width(), 400)
        ch  = max(c.winfo_height(), self._VID_H)
        tmp = "_mc_frame.png"
        gen = self._display_gen

        path = self.media_path

        def _extract():
            try:
                cmd = [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", str(max(0.0, t)),
                    "-i", path, "-frames:v", "1",
                    "-vf", (f"scale={cw}:{ch}:force_original_aspect_ratio=decrease,"
                            f"pad={cw}:{ch}:(ow-iw)/2:(oh-ih)/2:color=black"),
                    tmp
                ]
                subprocess.run(cmd, capture_output=True, timeout=10,
                               creationflags=SUBPROCESS_FLAGS)
                if not os.path.exists(tmp):
                    return
                img   = Image.open(tmp).convert("RGB")
                photo = ImageTk.PhotoImage(img, master=c)
                def _show(p=photo):
                    if self._display_gen == gen:
                        c.delete("all")
                        c.create_image(0, 0, image=p, anchor="nw")
                        self._current_photo = p
                        self._draw_crop_rect()
                self.top.after(0, _show)
            except Exception:
                pass

        threading.Thread(target=_extract, daemon=True).start()


    # ─────────────────────── PLAYBACK CONTROL ─────────────────────────
    def _toggle_play(self):
        if self._playing:
            self._pause()
        else:
            self._play()

    def _set_play_icon(self, playing):
        """Cập nhật icon nút Play/Pause."""
        if playing:
            self.play_btn.set_text("⏸")
            self.play_btn.bg       = WARNING
            self.play_btn.hover_bg = "#E5B833"
            self.play_btn.configure(bg=WARNING)
            self.play_btn.label.configure(bg=WARNING)
        else:
            self.play_btn.set_text("▶")
            self.play_btn.bg       = ACCENT
            self.play_btn.hover_bg = ACCENT_HOVER
            self.play_btn.configure(bg=ACCENT)
            self.play_btn.label.configure(bg=ACCENT)

    def _play(self):
        if not self.media_path:
            messagebox.showwarning("Cảnh báo", "Chưa chọn file!", parent=self.top)
            return
        if self._pos_sec >= self.end_sec:
            self._pos_sec = self.start_sec
        self._playing         = True
        self._play_start_wall = time.time()
        self._play_start_pos  = self._pos_sec
        self._set_play_icon(True)
        if self.has_video_stream:
            self._video_session += 1   # session mới, thread cũ sẽ không trigger end
            threading.Thread(target=self._video_thread, daemon=True).start()
        self._start_audio()

    def _pause(self):
        self._playing = False
        self._set_play_icon(False)
        self._stop_processes()

    def _stop_and_reset(self):
        self._playing = False
        self._pos_sec = self.start_sec
        self._set_play_icon(False)
        self._stop_processes()
        self._update_pos_display()
        if self.has_video_stream:
            self._show_frame_at(self.start_sec)

    def _seek_to(self, t):
        """Tua đến vị trí t — giữ trạng thái phát nếu đang phát."""
        was_playing = self._playing
        self._stop_processes()
        self._playing = False
        self._pos_sec = max(0.0, min(t, self.duration))
        self._update_pos_display()
        if not was_playing:
            # Chỉ hiện frame cho video; audio chỉ cần cập nhật playhead
            if self.has_video_stream:
                self._show_frame_at(self._pos_sec)
        else:
            # Resume từ vị trí mới (cả video lẫn audio)
            self._playing         = True
            self._play_start_wall = time.time()
            self._play_start_pos  = self._pos_sec
            self._set_play_icon(True)
            if self.has_video_stream:
                self._video_session += 1
                threading.Thread(target=self._video_thread, daemon=True).start()
            self._start_audio()

    def _on_speed_change(self):
        """Restart playback ngay khi đổi tốc độ, giữ nguyên vị trí hiện tại."""
        if not self._playing:
            return
        pos = self._pos_sec
        self._stop_all()
        self._play_start_pos  = pos
        self._play_start_wall = time.time()
        self._playing = True
        self._set_play_icon(True)
        if self.has_video_stream:
            self._video_session += 1
            threading.Thread(target=self._video_thread, daemon=True).start()
        self._start_audio()

    def _stop_all(self):
        self._playing = False
        self._stop_processes()

    def _stop_processes(self):
        for attr in ("_video_proc", "_audio_proc", "_audio_ffmpeg_proc"):
            proc = getattr(self, attr, None)
            if proc and proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass
            setattr(self, attr, None)

    def _on_playback_end(self):
        self._playing = False
        self._pos_sec = self.end_sec
        self._set_play_icon(False)
        self._stop_processes()
        self._update_pos_display()

    # ─────────────────────── VIDEO THREAD ─────────────────────────────
    def _video_thread(self):
        my_session = self._video_session   # ghi nhớ session khi thread bắt đầu
        self.top.update_idletasks()
        cw    = max(self.video_canvas.winfo_width(), 400)
        ch    = self._VID_H
        speed = float(self._speed_var.get().rstrip("x"))
        dur   = (self.end_sec - self._play_start_pos) / speed
        vf    = (f"setpts={1/speed:.6f}*PTS," if abs(speed - 1.0) > 0.001 else "") + \
                (f"scale={cw}:{ch}:force_original_aspect_ratio=decrease,"
                 f"pad={cw}:{ch}:(ow-iw)/2:(oh-ih)/2:color=black")
        cmd = [
            "ffmpeg", "-ss", str(self._play_start_pos),
            "-t", str(self.end_sec - self._play_start_pos),
            "-i", self.media_path,
            "-vf", vf,
            "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-r", str(self._FPS), "pipe:1"
        ]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL,
                                    creationflags=SUBPROCESS_FLAGS)
            self._video_proc = proc
            frame_size = cw * ch * 3
            frame_dur  = 1.0 / self._FPS
            start_wall = self._play_start_wall
            frame_idx  = 0

            while self._playing:
                raw = proc.stdout.read(frame_size)
                if not raw or len(raw) < frame_size:
                    break
                frame_idx += 1
                target = start_wall + frame_idx * frame_dur
                delay  = target - time.time()
                if delay > 0:
                    time.sleep(delay)
                elif delay < -0.15:
                    continue
                if not self._playing:
                    break
                data = bytes(raw[:frame_size])
                def _upd(d=data, w=cw, h=ch):
                    try:
                        if self._video_session != my_session:
                            return
                        img   = Image.frombytes("RGB", (w, h), d)
                        photo = ImageTk.PhotoImage(img, master=self.video_canvas)
                        self.video_canvas.delete("all")
                        self.video_canvas.create_image(0, 0, image=photo, anchor="nw")
                        self._current_photo = photo
                        self._draw_crop_rect()
                    except Exception:
                        pass
                self.top.after(0, _upd)

            proc.stdout.close()
            proc.wait()
        except Exception:
            pass
        # Chỉ trigger end nếu đây vẫn là session hiện tại (tránh race condition)
        if self._playing and my_session == self._video_session:
            self.top.after(0, self._on_playback_end)

    # ─────────────────────── AUDIO ────────────────────────────────────
    def _start_audio(self):
        """Trích audio ra file WAV tạm rồi phát — đáng tin cậy trên mọi OS."""
        dur   = self.end_sec - self._play_start_pos
        pos   = self._play_start_pos
        path  = self.media_path
        speed = float(self._speed_var.get().rstrip("x"))
        tmp   = os.path.join(tempfile.gettempdir(), f"_mc_audio_{os.getpid()}.wav")

        def _atempo(s):
            chain = []
            while s < 0.5:  chain.append("atempo=0.5"); s *= 2
            while s > 2.0:  chain.append("atempo=2.0"); s /= 2
            chain.append(f"atempo={s:.6f}")
            return ",".join(chain)

        def _run():
            try:
                # Bước 1: trích audio + áp tốc độ ra WAV
                af_args = ["-af", _atempo(speed)] if abs(speed - 1.0) > 0.001 else []
                cmd = [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", str(pos), "-t", str(dur),
                    "-i", path,
                    "-vn", *af_args,
                    "-acodec", "pcm_s16le",
                    "-ar", "44100", "-ac", "2",
                    tmp
                ]
                subprocess.run(cmd, capture_output=True,
                               creationflags=SUBPROCESS_FLAGS)

                if not os.path.exists(tmp) or not self._playing:
                    return

                # Bước 2: phát WAV
                if IS_MAC:
                    ap = subprocess.Popen(
                        ["afplay", tmp],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                elif IS_LINUX:
                    ap = subprocess.Popen(
                        ["aplay", "-q", tmp],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    ap = subprocess.Popen(
                        ["ffplay", "-nodisp", "-autoexit", tmp],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        creationflags=SUBPROCESS_FLAGS)
                self._audio_proc = ap
                ap.wait()
            except Exception:
                pass
            finally:
                try:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                except Exception:
                    pass

        threading.Thread(target=_run, daemon=True).start()

    # ─────────────────────── POSITION DISPLAY ─────────────────────────
    def _update_pos_display(self):
        self.pos_label.config(
            text=f"{self._fmt_hhmmss(self._pos_sec)} / {self._fmt_hhmmss(self.end_sec)}"
        )
        self._draw_timeline()

    # ─────────────────────── TIMELINE DRAW ────────────────────────────
    def _draw_timeline(self):
        c  = self.canvas
        c.delete("all")
        cw = c.winfo_width()
        if cw < 20:
            return
        pad = self._PAD
        bh  = self._BAR_H

        c.create_rectangle(0, 0, cw, bh + self._TICK_H,
                           fill=CARD_HEADER, outline="")

        if self.duration <= 0:
            c.create_text(cw // 2, bh // 2,
                          text="Chọn file để hiện timeline",
                          fill=TEXT_MUTED, font=(UI_FONT, 10))
            return

        sx = self._t2x(self.start_sec, cw)
        ex = self._t2x(self.end_sec,   cw)

        wf = self._waveform_data
        if wf is not None:
            try:
                resized = wf.resize((cw - 2 * pad, bh), Image.LANCZOS)
                photo   = ImageTk.PhotoImage(resized, master=c)
                self._waveform_photo = photo
                c.create_image(pad, 0, image=photo, anchor="nw")
            except Exception:
                self._draw_fallback_bar(c, pad, bh, cw)
        else:
            self._draw_fallback_bar(c, pad, bh, cw)

        # Dim outside selection
        if sx > pad:
            c.create_rectangle(pad, 0, sx, bh,
                               fill="#000000", stipple="gray50", outline="")
        if ex < cw - pad:
            c.create_rectangle(ex, 0, cw - pad, bh,
                               fill="#000000", stipple="gray50", outline="")

        # Selected range border
        c.create_rectangle(sx, 1, ex, bh - 1, outline=ACCENT, width=2, fill="")

        # Handles
        hdl = self._HDL
        for x, tag in [(sx, "hs"), (ex, "he")]:
            c.create_rectangle(x - hdl, 0, x + hdl, bh,
                               fill=TEXT_PRIMARY, outline="", tags=tag)
            c.create_text(x, bh // 2, text="⇕",
                          fill=CARD_HEADER, font=(UI_FONT, 10, "bold"), tags=tag)

        # Playback position indicator
        if self.duration > 0 and self.start_sec <= self._pos_sec <= self.end_sec:
            px = self._t2x(self._pos_sec, cw)
            c.create_line(px, 0, px, bh, fill=WARNING, width=2, tags="pos")
            c.create_polygon(px - 5, 0, px + 5, 0, px, 8,
                             fill=WARNING, outline="", tags="pos")

        # Tick marks theo view range
        ty   = bh + 2
        vstart = self._view_start
        vend   = self._view_end
        for pct in [0.0, 0.25, 0.5, 0.75, 1.0]:
            t = vstart + pct * (vend - vstart)
            x = self._t2x(t, cw)
            c.create_line(x, ty, x, ty + 5, fill=TEXT_MUTED, width=1)
            anc = "w" if pct == 0.0 else ("e" if pct == 1.0 else "center")
            c.create_text(x, ty + 13, text=self._fmt_mmss(t),
                          fill=TEXT_MUTED, font=(UI_FONT, 7), anchor=anc)

    def _draw_fallback_bar(self, c, pad, bh, cw):
        c.create_rectangle(pad, 6, cw - pad, bh - 6, fill="#1C2128", outline="")
        c.create_line(pad, bh // 2, cw - pad, bh // 2,
                      fill=ACCENT, width=1, dash=(6, 4))

    # ─────────────────────── HELPERS ──────────────────────────────────
    def _t2x(self, t, cw):
        pad  = self._PAD
        rng  = self._view_end - self._view_start
        if rng <= 0:
            return pad
        ratio = (t - self._view_start) / rng
        return pad + ratio * (cw - 2 * pad)

    def _x2t(self, x, cw):
        pad  = self._PAD
        rng  = self._view_end - self._view_start
        if rng <= 0:
            return self._view_start
        ratio = (x - pad) / max(cw - 2 * pad, 1)
        return max(0.0, min(self.duration,
                            self._view_start + ratio * rng))

    # ─────────────────────── TIMELINE ZOOM / PAN ──────────────────────
    def _on_tl_scroll(self, event):
        """Cuộn chuột → zoom timeline."""
        if self.duration <= 0:
            return
        cw  = self.canvas.winfo_width()
        # Điểm pivot = thời gian tại vị trí chuột
        pivot = self._x2t(event.x, cw)

        if event.num == 4 or (hasattr(event, 'delta') and event.delta > 0):
            factor = 0.8   # zoom in
        else:
            factor = 1.25  # zoom out

        rng   = self._view_end - self._view_start
        new_rng = max(0.5, min(self.duration, rng * factor))
        # Giữ pivot tại cùng vị trí pixel
        ratio = (pivot - self._view_start) / rng if rng > 0 else 0.5
        self._view_start = max(0.0, pivot - ratio * new_rng)
        self._view_end   = min(self.duration, self._view_start + new_rng)
        # Clamp
        if self._view_end > self.duration:
            self._view_end   = self.duration
            self._view_start = max(0.0, self._view_end - new_rng)
        self._draw_timeline()

    def _on_tl_dblclick(self, event):
        """Double-click → reset zoom về full view."""
        if self.duration <= 0:
            return
        self._view_start = 0.0
        self._view_end   = self.duration
        self._draw_timeline()

    def _fmt_hhmmss(self, secs):
        h = int(secs // 3600)
        m = int((secs % 3600) // 60)
        s = secs % 60
        return f"{h:02d}:{m:02d}:{s:06.3f}"

    def _fmt_mmss(self, secs):
        return f"{int(secs // 60)}:{int(secs % 60):02d}"

    def _parse_entry(self, text):
        text = text.strip()
        try:
            parts = text.split(":")
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
            if len(parts) == 2:
                return int(parts[0]) * 60 + float(parts[1])
            return float(text)
        except Exception:
            return None

    def _update_entries(self):
        self.start_entry.delete(0, "end")
        self.start_entry.insert(0, self._fmt_hhmmss(self.start_sec))
        self.end_entry.delete(0, "end")
        self.end_entry.insert(0, self._fmt_hhmmss(self.end_sec))

    def _update_dur_label(self):
        d = self.end_sec - self.start_sec
        self.seg_dur_label.config(text=f"Độ dài: {d:.3f}s")

    # ─────────────────────── ENTRY EVENTS ─────────────────────────────
    def _on_start_entry(self, e=None):
        v = self._parse_entry(self.start_entry.get())
        if v is not None:
            self.start_sec = max(0.0, min(v, max(self.end_sec - 0.1, 0.0)))
            self._update_entries()
            self._update_dur_label()
            if not self._playing:
                self._pos_sec = self.start_sec
                self._show_frame_at(self.start_sec)
            self._draw_timeline()

    def _on_end_entry(self, e=None):
        v = self._parse_entry(self.end_entry.get())
        if v is not None:
            self.end_sec = max(self.start_sec + 0.1, min(v, self.duration))
            self._update_entries()
            self._update_dur_label()
            self._draw_timeline()

    # ─────────────────────── CANVAS DRAG ──────────────────────────────
    def _on_canvas_click(self, event):
        if self.duration <= 0:
            return
        cw  = self.canvas.winfo_width()
        sx  = self._t2x(self.start_sec, cw)
        ex  = self._t2x(self.end_sec,   cw)
        hdl = self._HDL + 6

        if abs(event.x - sx) <= hdl:
            self._drag_handle = "start"
            self._on_canvas_drag(event)
        elif abs(event.x - ex) <= hdl:
            self._drag_handle = "end"
            self._on_canvas_drag(event)
        else:
            # Click nơi khác → tua vị trí phát (như CapCut)
            self._drag_handle = "pos"
            t = max(0.0, min(self._x2t(event.x, cw), self.duration))
            self._pos_sec = t
            self._update_pos_display()

    def _on_canvas_drag(self, event):
        if not self._drag_handle or self.duration <= 0:
            return
        cw  = self.canvas.winfo_width()
        pad = self._PAD

        # ── Auto-pan khi kéo gần mép (chỉ khi đã zoom in) ──────────────
        vrange = self._view_end - self._view_start
        EDGE   = 18                        # pixel vùng trigger
        SPEED  = max(0.01, vrange * 0.04)  # tốc độ pan
        panned = False
        if event.x <= pad + EDGE and self._view_start > 0:
            self._view_start = max(0.0, self._view_start - SPEED)
            self._view_end   = min(self.duration, self._view_start + vrange)
            panned = True
        elif event.x >= cw - pad - EDGE and self._view_end < self.duration:
            self._view_end   = min(self.duration, self._view_end + SPEED)
            self._view_start = max(0.0, self._view_end - vrange)
            panned = True

        t = self._x2t(event.x, cw)

        if self._drag_handle == "start":
            self.start_sec = max(0.0, min(t, self.end_sec - 0.1))
            self._update_entries()
            self._update_dur_label()
            self._draw_timeline()
        elif self._drag_handle == "end":
            self.end_sec = max(self.start_sec + 0.1, min(t, self.duration))
            self._update_entries()
            self._update_dur_label()
            self._draw_timeline()
        elif self._drag_handle == "pos":
            self._pos_sec = max(0.0, min(t, self.duration))
            self._update_pos_display()
            # Realtime frame preview khi kéo playhead
            if self.has_video_stream and not self._playing:
                self._schedule_scrub()
        elif panned:
            self._draw_timeline()

    def _on_canvas_release(self, e=None):
        if self._drag_handle == "pos":
            # Thả playhead → seek đến vị trí mới (cả video lẫn audio)
            self._seek_to(self._pos_sec)
        elif self._drag_handle in ("start", "end"):
            # Thả handle cắt → hiện frame (video) hoặc cập nhật playhead (audio)
            t = self.start_sec if self._drag_handle == "start" else self.end_sec
            self._pos_sec = t
            if self.has_video_stream and not self._playing:
                self._show_frame_at(t)
            else:
                self._update_pos_display()
        self._drag_handle = None

    # ─────────────────────── SCRUB REALTIME ───────────────────────────
    def _schedule_scrub(self):
        """Debounce 80ms rồi extract frame tại vị trí hiện tại."""
        if self._scrub_after:
            try:
                self.top.after_cancel(self._scrub_after)
            except Exception:
                pass
        self._scrub_after = self.top.after(80, self._do_scrub)

    def _do_scrub(self):
        self._scrub_after = None
        if self._drag_handle != "pos" or not self.has_video_stream:
            return
        gen = self._display_gen   # ghi nhớ generation hiện tại
        if self._scrub_busy:
            # Đang extract, đặt lại lịch để lấy vị trí mới nhất
            self._schedule_scrub()
            return
        pos  = self._pos_sec
        path = self.media_path
        cw   = max(self.video_canvas.winfo_width(), 400)
        ch   = self._VID_H
        tmp  = "_mc_scrub.png"

        def _extract():
            self._scrub_busy = True
            try:
                cmd = [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", str(max(0.0, pos)),
                    "-i", path,
                    "-frames:v", "1",
                    "-vf", (f"scale={cw}:{ch}:"
                            f"force_original_aspect_ratio=decrease,"
                            f"pad={cw}:{ch}:(ow-iw)/2:(oh-ih)/2:color=black"),
                    tmp
                ]
                subprocess.run(cmd, capture_output=True, timeout=5,
                               creationflags=SUBPROCESS_FLAGS)
                if os.path.exists(tmp) and self._drag_handle == "pos":
                    img   = Image.open(tmp).convert("RGB")
                    photo = ImageTk.PhotoImage(img, master=self.video_canvas)
                    def _show(p=photo):
                        # Gen check: không ghi đè nếu file đã thay đổi
                        if self._display_gen == gen and self._drag_handle == "pos":
                            self.video_canvas.delete("all")
                            self.video_canvas.create_image(0, 0, image=p, anchor="nw")
                            self._current_photo = p
                    self.top.after(0, _show)
            except Exception:
                pass
            finally:
                self._scrub_busy = False

        threading.Thread(target=_extract, daemon=True).start()

    # ─────────────────────── CUT ──────────────────────────────────────
    def _do_cut(self):
        if not self.media_path or not os.path.exists(self.media_path):
            messagebox.showwarning("Cảnh báo", "Chưa chọn file nguồn!", parent=self.top)
            return
        out = self.output_path.get().strip()
        if not out:
            messagebox.showwarning("Cảnh báo", "Chưa chọn đường dẫn lưu file!",
                                   parent=self.top)
            return
        if self.end_sec <= self.start_sec:
            messagebox.showwarning("Cảnh báo", "Thời gian không hợp lệ!", parent=self.top)
            return
        self._stop_all()
        dur = self.end_sec - self.start_sec
        speed = float(self._speed_var.get().rstrip("x"))
        use_crop = (self._crop_enabled.get() and self.has_video_stream
                    and self._crop_w.get() > 0 and self._crop_h.get() > 0)
        use_speed = abs(speed - 1.0) > 0.001

        base_cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-progress", "pipe:1", "-stats_period", "0.5",
            "-ss", str(self.start_sec), "-t", str(dur),
            "-i", self.media_path,
        ]

        if not use_crop and not use_speed:
            cmd = base_cmd + ["-c", "copy", out]
        else:
            vf_parts, af_parts = [], []
            if use_crop:
                cw_v = min(self._crop_w.get(), self._orig_w - self._crop_x.get())
                ch_v = min(self._crop_h.get(), self._orig_h - self._crop_y.get())
                vf_parts.append(f"crop={cw_v}:{ch_v}:{self._crop_x.get()}:{self._crop_y.get()}")
            if use_speed and self.has_video_stream:
                vf_parts.append(f"setpts={1/speed:.6f}*PTS")
            if use_speed:
                # atempo: range [0.5, 2.0] — chain nếu ngoài range
                s = speed
                chain = []
                while s < 0.5:
                    chain.append("atempo=0.5")
                    s /= 0.5
                while s > 2.0:
                    chain.append("atempo=2.0")
                    s /= 2.0
                chain.append(f"atempo={s:.6f}")
                af_parts.append(",".join(chain))

            extra = []
            if vf_parts:
                extra += ["-vf", ",".join(vf_parts)]
            elif not self.has_video_stream:
                extra += ["-vn"]
            if af_parts:
                extra += ["-af", ",".join(af_parts)]
            else:
                extra += ["-c:a", "copy"]
            cmd = base_cmd + extra + [out]

        self.cut_btn.set_state("disabled")
        self.cut_btn.set_text("Đang cắt...")
        self.status_label.config(text="Đang xử lý...", fg=TEXT_MUTED)
        self._cut_progress_var.set(0)
        self._cut_pct_label.config(text="0%")

        def _run():
            try:
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", errors="replace",
                    creationflags=SUBPROCESS_FLAGS
                )
                for line in proc.stdout:
                    if line.startswith("out_time_us=") and dur > 0:
                        try:
                            us = int(line.strip().split("=")[1])
                            pct = min(us / (dur * 1_000_000) * 100, 100)
                            self.top.after(0, lambda p=pct: (
                                self._cut_progress_var.set(p),
                                self._cut_pct_label.config(text=f"{p:.0f}%")
                            ))
                        except Exception:
                            pass
                proc.wait()
                stderr_out = proc.stderr.read()

                def _done():
                    self.cut_btn.set_state("normal")
                    self.cut_btn.set_text("✂  Cắt & Lưu")
                    if proc.returncode == 0:
                        self._cut_progress_var.set(100)
                        self._cut_pct_label.config(text="100%")
                        self.status_label.config(
                            text=f"Đã lưu: {os.path.basename(out)}", fg=SUCCESS)
                        messagebox.showinfo("Thành công",
                                            f"Đã cắt và lưu:\n{out}", parent=self.top)
                        self._cut_progress_var.set(0)
                        self._cut_pct_label.config(text="")
                    else:
                        self._cut_progress_var.set(0)
                        self._cut_pct_label.config(text="")
                        self.status_label.config(text="Lỗi khi cắt!", fg=DANGER)
                        messagebox.showerror("Lỗi", f"ffmpeg:\n{stderr_out[:600]}",
                                             parent=self.top)
                self.top.after(0, _done)
            except Exception as ex:
                def _err():
                    self.cut_btn.set_state("normal")
                    self.cut_btn.set_text("✂  Cắt & Lưu")
                    self._cut_progress_var.set(0)
                    self._cut_pct_label.config(text="")
                    self.status_label.config(text=f"Lỗi: {ex}", fg=DANGER)
                self.top.after(0, _err)

        threading.Thread(target=_run, daemon=True).start()

    # ─────────────────────── CROP ──────────────────────────────────────

    def _reset_crop(self):
        """Đặt lại crop về toàn khung hình."""
        self._crop_x.set(0)
        self._crop_y.set(0)
        self._crop_w.set(self._orig_w)
        self._crop_h.set(self._orig_h)
        self._update_crop_size_label()
        self._draw_crop_rect()

    def _on_crop_toggle(self):
        if self._crop_enabled.get():
            self._crop_inputs.pack(fill="x", padx=10, pady=(0, 8))
            if self._crop_w.get() == 0:
                self._reset_crop()
        else:
            self._crop_inputs.pack_forget()
        self._draw_crop_rect()

    def _update_crop_size_label(self):
        if self._orig_w and self._orig_h:
            self._crop_size_label.config(
                text=f"{self._orig_w}×{self._orig_h} → {self._crop_w.get()}×{self._crop_h.get()}"
            )

    def _canvas_to_video(self, cx, cy):
        """Chuyển tọa độ canvas → tọa độ pixel video gốc."""
        if not self._orig_w or not self._orig_h:
            return 0, 0
        cw_d = max(self.video_canvas.winfo_width(), 400)
        ch_d = max(self.video_canvas.winfo_height(), self._VID_H)
        scale = min(cw_d / self._orig_w, ch_d / self._orig_h)
        ox = (cw_d - self._orig_w * scale) / 2
        oy = (ch_d - self._orig_h * scale) / 2
        vx = int((cx - ox) / scale)
        vy = int((cy - oy) / scale)
        return max(0, min(self._orig_w, vx)), max(0, min(self._orig_h, vy))

    def _draw_crop_rect(self):
        """Vẽ hình chữ nhật crop lên video_canvas — dùng tag để xóa gọn."""
        c = self.video_canvas
        c.delete("crop_overlay")          # xóa tất cả items cũ bằng tag
        self._crop_rect_id = None
        if not self._crop_enabled.get() or not self._orig_w or not self._orig_h:
            return
        cw_d = max(c.winfo_width(), 400)
        ch_d = max(c.winfo_height(), self._VID_H)
        scale = min(cw_d / self._orig_w, ch_d / self._orig_h)
        ox = (cw_d - self._orig_w * scale) / 2
        oy = (ch_d - self._orig_h * scale) / 2
        x1 = ox + self._crop_x.get() * scale
        y1 = oy + self._crop_y.get() * scale
        x2 = x1 + self._crop_w.get() * scale
        y2 = y1 + self._crop_h.get() * scale
        # 4 vùng tối bên ngoài hình chữ nhật crop (không đè lên bên trong)
        for rx1, ry1, rx2, ry2 in [
            (0,   0,    cw_d, y1  ),   # trên
            (0,   y2,   cw_d, ch_d),   # dưới
            (0,   y1,   x1,   y2  ),   # trái
            (x2,  y1,   cw_d, y2  ),   # phải
        ]:
            c.create_rectangle(rx1, ry1, rx2, ry2,
                               fill="black", stipple="gray50", outline="",
                               tags=("crop_overlay",))
        # Đường viền nét đứt
        self._crop_rect_id = c.create_rectangle(
            x1, y1, x2, y2,
            outline=ACCENT, width=2, dash=(8, 4),
            tags=("crop_overlay",)
        )
        # Handle góc
        s = 6
        for hx, hy in [(x1, y1), (x2, y1), (x1, y2), (x2, y2)]:
            c.create_rectangle(hx - s, hy - s, hx + s, hy + s,
                               fill=ACCENT, outline="",
                               tags=("crop_overlay",))
        self._update_crop_size_label()

    def _on_crop_press(self, event):
        if not self._crop_enabled.get() or not self._orig_w:
            return
        self._crop_drag_start = (event.x, event.y)

    def _on_crop_drag(self, event):
        if not self._crop_enabled.get() or not self._crop_drag_start:
            return
        x0, y0 = self._canvas_to_video(*self._crop_drag_start)
        x1, y1 = self._canvas_to_video(event.x, event.y)
        rx, ry = min(x0, x1), min(y0, y1)
        rw, rh = abs(x1 - x0), abs(y1 - y0)
        self._crop_x.set(rx)
        self._crop_y.set(ry)
        self._crop_w.set(max(2, rw))
        self._crop_h.set(max(2, rh))
        self._draw_crop_rect()

    def _on_crop_release(self, event):
        if not self._crop_enabled.get() or not self._crop_drag_start:
            return
        self._on_crop_drag(event)
        self._crop_drag_start = None
        # Nếu drag quá nhỏ (click đơn) → reset về full frame
        if self._crop_w.get() < 10 and self._crop_h.get() < 10:
            self._reset_crop()


# =========================================================
# VIDEO EDITOR TAB
# =========================================================

class VideoEditorTab(tk.Frame):
    """One independent video editing job inside VideoEditorDialog."""

    def __init__(self, parent, root_window, tab_number, close_callback=None):
        super().__init__(parent, bg=APP_BG)
        self._root_window = root_window
        self._tab_number  = tab_number
        self._close_cb    = close_callback
        self._stop_event  = threading.Event()
        self._proc        = None
        self._pip_items   = []
        self.ui_queue     = queue.Queue()
        self._init_vars()
        self._build_ui()
        self.after(50, self._drain_queue)

    def on_rename(self, new_title):
        safe = re.sub(r'[\\/:*?"<>|]', "_", new_title).strip("_") or f"job_{self._tab_number}"
        self.output_name.set(f"{safe}_edited.mp4")

    # ── Queue / log ───────────────────────────────────────────────────────

    def _drain_queue(self):
        try:
            while True:
                self.ui_queue.get_nowait()()
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(50, self._drain_queue)

    def queue_ui(self, fn):
        self.ui_queue.put(fn)

    def log(self, text, tag=None):
        def _do():
            self._log_box.insert("end", text + "\n", tag or "")
            self._log_box.see("end")
        self.queue_ui(_do)

    def _upd_progress(self, pct, label=""):
        def _do():
            self._progress_var.set(pct * 100)
            self.percent_label.config(text=f"{pct*100:.0f}%")
            if label:
                self.detail_label.config(text=label)
        self.queue_ui(_do)

    # ── Variables ─────────────────────────────────────────────────────────

    def _init_vars(self):
        self.input_video  = tk.StringVar()
        self.output_dir   = tk.StringVar()
        self.output_name  = tk.StringVar(value=f"job_{self._tab_number}_edited.mp4")

        self.use_subtitle       = tk.BooleanVar(value=False)
        self.sub_mode           = tk.StringVar(value="whisper")
        self.srt_file_path      = tk.StringVar()
        self.whisper_model_var  = tk.StringVar(value="base")
        self.whisper_task       = tk.StringVar(value="transcribe")
        self.subtitle_language  = tk.StringVar(value="vi")
        self.subtitle_font_size = tk.IntVar(value=45)

        self.use_zoom      = tk.BooleanVar(value=False)
        self.zoom_interval = tk.IntVar(value=10)
        self.zoom_max      = tk.IntVar(value=130)

        self.use_pip = tk.BooleanVar(value=False)

        self.use_logo      = tk.BooleanVar(value=False)
        self.logo_path     = tk.StringVar()
        self.logo_pos      = tk.StringVar(value="bottom-right")
        self.logo_size     = tk.IntVar(value=15)
        self.logo_opacity  = tk.DoubleVar(value=1.0)
        self.logo_padding  = tk.IntVar(value=20)

        self.use_bgm    = tk.BooleanVar(value=False)
        self.bgm_path   = tk.StringVar()
        self.bgm_volume = tk.DoubleVar(value=0.3)
        self.bgm_mode   = tk.StringVar(value="mix")

        self.use_vocal_sep  = tk.BooleanVar(value=False)
        self.vocal_sep_keep = tk.StringVar(value="vocals")

        self.use_banner          = tk.BooleanVar(value=False)
        self.banner_top_enabled  = tk.BooleanVar(value=True)
        self.banner_top_text     = tk.StringVar(value="")
        self.banner_top_fontsize = tk.IntVar(value=36)
        self.banner_top_textcolor= tk.StringVar(value="#FFFFFF")
        self.banner_top_bgcolor  = tk.StringVar(value="#000000")
        self.banner_top_bgopacity= tk.DoubleVar(value=0.7)
        self.banner_bot_enabled  = tk.BooleanVar(value=False)
        self.banner_bot_text     = tk.StringVar(value="")
        self.banner_bot_fontsize = tk.IntVar(value=32)
        self.banner_bot_textcolor= tk.StringVar(value="#FFFFFF")
        self.banner_bot_bgcolor  = tk.StringVar(value="#000000")
        self.banner_bot_bgopacity= tk.DoubleVar(value=0.7)

    # ── UI ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        main = tk.Frame(self, bg=APP_BG)
        main.pack(fill="both", expand=True, padx=16, pady=12)
        main.grid_columnconfigure(0, weight=1, minsize=480)
        main.grid_columnconfigure(1, weight=0, minsize=380)
        main.grid_rowconfigure(0, weight=1)

        left_w = tk.Frame(main, bg=APP_BG)
        left_w.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self._scroll = ScrollableFrame(left_w)
        self._scroll.pack(fill="both", expand=True)

        right = tk.Frame(main, bg=APP_BG)
        right.grid(row=0, column=1, sticky="nsew", padx=(10, 0))

        self._build_left(self._scroll.inner)
        self._build_right(right)
        self._apply_entry_borders(main)
        self._scroll.bind_scroll_to_all_children()

    def _apply_entry_borders(self, root):
        for w in root.winfo_children():
            if type(w) is tk.Entry:
                try:
                    parent_bg = w.master.cget("bg")
                except Exception:
                    parent_bg = CARD_BG
                w.configure(highlightthickness=1,
                             highlightbackground=parent_bg,
                             highlightcolor=ACCENT)
            self._apply_entry_borders(w)

    # ── Left column ───────────────────────────────────────────────────────

    def _build_left(self, parent):
        def _show(frame, var):
            if var.get():
                frame.pack(fill="x")
            else:
                frame.pack_forget()

        # Card: Video nguồn
        src_card = Card(parent, "▶  Video nguồn")
        src_card.pack(fill="x", pady=(0, 12), padx=(0, 4))

        row_in = tk.Frame(src_card.body, bg=CARD_BG)
        row_in.pack(fill="x", pady=(0, 8))
        FlatButton(row_in, "📁  Chọn video", command=self._browse_input,
                   bg=BTN_SECONDARY, hover_bg=BTN_SEC_HOVER, pressed_bg=DIVIDER,
                   width=130, height=34, font=(UI_FONT, 9)
                   ).pack(side="left", padx=(0, 8))
        in_wrap = tk.Frame(row_in, bg=CARD_HEADER)
        in_wrap.pack(side="left", fill="x", expand=True)
        tk.Entry(in_wrap, textvariable=self.input_video, state="readonly",
                 readonlybackground=CARD_HEADER, fg=TEXT_PRIMARY,
                 font=(UI_FONT, 9), relief="flat", bd=0
                 ).pack(fill="x", padx=6, ipady=4)
        self._badge_lbl = tk.Label(row_in, text="VIDEO",
                                   bg=ACCENT, fg=APP_BG,
                                   font=(UI_FONT, 8, "bold"), padx=5, pady=2)

        row_out = tk.Frame(src_card.body, bg=CARD_BG)
        row_out.pack(fill="x", pady=(0, 8))
        FlatButton(row_out, "📁  Thư mục output", command=self._browse_output_dir,
                   bg=BTN_SECONDARY, hover_bg=BTN_SEC_HOVER, pressed_bg=DIVIDER,
                   width=130, height=34, font=(UI_FONT, 9)
                   ).pack(side="left", padx=(0, 8))
        out_wrap = tk.Frame(row_out, bg=CARD_HEADER)
        out_wrap.pack(side="left", fill="x", expand=True)
        tk.Entry(out_wrap, textvariable=self.output_dir, state="readonly",
                 readonlybackground=CARD_HEADER, fg=TEXT_PRIMARY,
                 font=(UI_FONT, 9), relief="flat", bd=0
                 ).pack(fill="x", padx=6, ipady=4)

        row_name = tk.Frame(src_card.body, bg=CARD_BG)
        row_name.pack(fill="x")
        tk.Label(row_name, text="Tên file output:", bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 9)).pack(side="left", padx=(0, 8))
        name_wrap = tk.Frame(row_name, bg=CARD_HEADER)
        name_wrap.pack(side="left", fill="x", expand=True)
        tk.Entry(name_wrap, textvariable=self.output_name,
                 bg=CARD_HEADER, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
                 font=(UI_FONT, 9), relief="flat", bd=0
                 ).pack(fill="x", padx=6, ipady=4)

        # Card: Zoom
        zoom_card = Card(parent, "⊕  Zoom  (Ken Burns)")
        zoom_card.pack(fill="x", pady=(0, 12), padx=(0, 4))

        zoom_body = tk.Frame(zoom_card.body, bg=CARD_BG)
        ToggleSwitch(zoom_card.body, "Bật zoom nhịp", self.use_zoom,
                     command=lambda: _show(zoom_body, self.use_zoom)
                     ).pack(anchor="w", pady=(0, 6), fill="x")

        zoom_ivl_row = tk.Frame(zoom_body, bg=CARD_BG)
        zoom_ivl_row.pack(fill="x", pady=(0, 8))
        tk.Label(zoom_ivl_row, text="Mỗi", bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 9)).pack(side="left", padx=(0, 6))
        _zif = tk.Frame(zoom_ivl_row, bg=CARD_HEADER)
        _zif.pack(side="left")
        tk.Entry(_zif, textvariable=self.zoom_interval, width=4,
                 bg=CARD_HEADER, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
                 font=(UI_FONT, 9), relief="flat", bd=0, justify="center"
                 ).pack(padx=6, ipady=4)
        tk.Label(zoom_ivl_row, text="giây zoom một lần", bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 9)).pack(side="left", padx=(6, 0))

        zoom_max_row = tk.Frame(zoom_body, bg=CARD_BG)
        zoom_max_row.pack(fill="x", pady=(0, 6))
        tk.Label(zoom_max_row, text="Mức zoom tối đa (%):", bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 9)).pack(side="left", padx=(0, 8))
        _zmf = tk.Frame(zoom_max_row, bg=CARD_HEADER)
        _zmf.pack(side="left")
        tk.Entry(_zmf, textvariable=self.zoom_max, width=5,
                 bg=CARD_HEADER, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
                 font=(UI_FONT, 9), relief="flat", bd=0, justify="center"
                 ).pack(padx=6, ipady=4)
        tk.Label(zoom_max_row, text="(130 = phóng to 130%)", bg=CARD_BG, fg=TEXT_MUTED,
                 font=(UI_FONT, 8)).pack(side="left", padx=(8, 0))

        _show(zoom_body, self.use_zoom)

        # Card: PiP
        pip_card = Card(parent, "◈  Video Overlay (Picture-in-Picture)")
        pip_card.pack(fill="x", pady=(0, 12), padx=(0, 4))

        pip_body = tk.Frame(pip_card.body, bg=CARD_BG)
        ToggleSwitch(pip_card.body, "Bật video overlay (PiP)", self.use_pip,
                     command=lambda: _show(pip_body, self.use_pip)
                     ).pack(anchor="w", pady=(0, 6), fill="x")
        self._pip_list_frame = tk.Frame(pip_body, bg=CARD_BG)
        self._pip_list_frame.pack(fill="x")
        FlatButton(pip_body, "+ Thêm video overlay", command=self._add_pip_item,
                   height=34, font=(UI_FONT, 9, "bold")).pack(fill="x", pady=(6, 0))
        _show(pip_body, self.use_pip)

        # Card: Logo kênh
        logo_card = Card(parent, "⊡  Logo kênh")
        logo_card.pack(fill="x", pady=(0, 12), padx=(0, 4))

        logo_body = tk.Frame(logo_card.body, bg=CARD_BG)
        ToggleSwitch(logo_card.body, "Bật logo overlay", self.use_logo,
                     command=lambda: _show(logo_body, self.use_logo)
                     ).pack(anchor="w", pady=(0, 6), fill="x")

        logo_row = tk.Frame(logo_body, bg=CARD_BG)
        logo_row.pack(fill="x", pady=(0, 8))
        FlatButton(logo_row, "📁  Chọn ảnh logo", command=self._browse_logo,
                   bg=BTN_SECONDARY, hover_bg=BTN_SEC_HOVER, pressed_bg=DIVIDER,
                   width=130, height=34, font=(UI_FONT, 9)
                   ).pack(side="left", padx=(0, 8))
        logo_path_wrap = tk.Frame(logo_row, bg=CARD_HEADER)
        logo_path_wrap.pack(side="left", fill="x", expand=True)
        tk.Entry(logo_path_wrap, textvariable=self.logo_path, state="readonly",
                 readonlybackground=CARD_HEADER, fg=TEXT_PRIMARY,
                 font=(UI_FONT, 9), relief="flat", bd=0
                 ).pack(fill="x", padx=6, ipady=4)

        logo_row2 = tk.Frame(logo_body, bg=CARD_BG)
        logo_row2.pack(fill="x")

        PositionPicker(logo_row2, self.logo_pos, bg=CARD_BG).pack(side="left")

        logo_opts = tk.Frame(logo_row2, bg=CARD_BG)
        logo_opts.pack(side="left", padx=(20, 0), anchor="n")

        tk.Label(logo_opts, text="Kích thước (%)", bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 8)).pack(anchor="w")
        _lsf = tk.Frame(logo_opts, bg=INPUT_BG)
        _lsf.pack(pady=(3, 8), anchor="w")
        tk.Entry(_lsf, textvariable=self.logo_size, width=5,
                 bg=INPUT_BG, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
                 font=(UI_FONT, 11), relief="flat", bd=0, justify="center"
                 ).pack(padx=4, pady=3)

        tk.Label(logo_opts, text="Độ mờ (0.0–1.0)", bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 8)).pack(anchor="w")
        _lof = tk.Frame(logo_opts, bg=INPUT_BG)
        _lof.pack(pady=(3, 8), anchor="w")
        tk.Entry(_lof, textvariable=self.logo_opacity, width=5,
                 bg=INPUT_BG, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
                 font=(UI_FONT, 11), relief="flat", bd=0, justify="center"
                 ).pack(padx=4, pady=3)

        tk.Label(logo_opts, text="Padding (px)", bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 8)).pack(anchor="w")
        _lpf = tk.Frame(logo_opts, bg=INPUT_BG)
        _lpf.pack(pady=(3, 0), anchor="w")
        tk.Entry(_lpf, textvariable=self.logo_padding, width=5,
                 bg=INPUT_BG, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
                 font=(UI_FONT, 11), relief="flat", bd=0, justify="center"
                 ).pack(padx=4, pady=3)

        _show(logo_body, self.use_logo)

        # Card: Phụ đề
        sub_card = Card(parent, "CC  Phụ đề  (Whisper AI / SRT)")
        sub_card.pack(fill="x", pady=(0, 12), padx=(0, 4))

        sub_body = tk.Frame(sub_card.body, bg=CARD_BG)
        ToggleSwitch(sub_card.body, "Bật phụ đề", self.use_subtitle,
                     command=lambda: _show(sub_body, self.use_subtitle)
                     ).pack(anchor="w", pady=(0, 6), fill="x")

        self._mode_row = tk.Frame(sub_body, bg=CARD_BG)
        self._mode_row.pack(fill="x", pady=(0, 8))
        tk.Label(self._mode_row, text="Chế độ:", bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 9)).pack(side="left", padx=(0, 10))
        for lbl, val in [("Tự động (Whisper)", "whisper"), ("Tải file SRT", "srt_file")]:
            tk.Radiobutton(
                self._mode_row, text=lbl, variable=self.sub_mode, value=val,
                bg=CARD_BG, fg=TEXT_PRIMARY, selectcolor=CARD_HEADER,
                activebackground=CARD_BG, activeforeground=TEXT_PRIMARY,
                font=(UI_FONT, 9), relief="flat", command=self._on_sub_mode_change
            ).pack(side="left", padx=(0, 14))

        self._whisper_opts = tk.Frame(sub_body, bg=CARD_BG)
        w_r1 = tk.Frame(self._whisper_opts, bg=CARD_BG)
        w_r1.pack(fill="x", pady=(0, 6))
        tk.Label(w_r1, text="Model:", bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 9)).pack(side="left", padx=(0, 6))
        _mf = tk.Frame(w_r1, bg=CARD_HEADER)
        _mf.pack(side="left", padx=(0, 16))
        ttk.Combobox(_mf, textvariable=self.whisper_model_var,
                     values=WHISPER_MODELS, state="readonly",
                     font=(UI_FONT, 9), width=9, style="Modern.TCombobox",
                     takefocus=0).pack(padx=4, pady=2)
        tk.Label(w_r1, text="Ngôn ngữ:", bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 9)).pack(side="left", padx=(0, 6))
        _lf = tk.Frame(w_r1, bg=CARD_HEADER)
        _lf.pack(side="left")
        tk.Entry(_lf, textvariable=self.subtitle_language, width=4,
                 bg=CARD_HEADER, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
                 font=(UI_FONT, 10), relief="flat", bd=0, justify="center"
                 ).pack(padx=6, ipady=4)

        w_r2 = tk.Frame(self._whisper_opts, bg=CARD_BG)
        w_r2.pack(fill="x", pady=(0, 6))
        tk.Label(w_r2, text="Tác vụ:", bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 9)).pack(side="left", padx=(0, 10))
        for lbl, val in [("Transcribe (giữ nguyên tiếng)", "transcribe"),
                          ("Translate → English", "translate")]:
            tk.Radiobutton(
                w_r2, text=lbl, variable=self.whisper_task, value=val,
                bg=CARD_BG, fg=TEXT_PRIMARY, selectcolor=CARD_HEADER,
                activebackground=CARD_BG, activeforeground=TEXT_PRIMARY,
                font=(UI_FONT, 9), relief="flat"
            ).pack(side="left", padx=(0, 14))

        if not WHISPER_AVAILABLE:
            tk.Label(self._whisper_opts,
                     text="⚠  Whisper chưa cài: pip install openai-whisper",
                     bg=CARD_BG, fg=WARNING, font=(UI_FONT, 9)
                     ).pack(anchor="w", pady=(0, 4))

        self._srt_opts = tk.Frame(sub_body, bg=CARD_BG)
        srt_row = tk.Frame(self._srt_opts, bg=CARD_BG)
        srt_row.pack(fill="x", pady=(0, 6))
        FlatButton(srt_row, "📄  Chọn file SRT", command=self._browse_srt,
                   bg=BTN_SECONDARY, hover_bg=BTN_SEC_HOVER, pressed_bg=DIVIDER,
                   width=130, height=34, font=(UI_FONT, 9)
                   ).pack(side="left", padx=(0, 8))
        srt_wrap = tk.Frame(srt_row, bg=CARD_HEADER)
        srt_wrap.pack(side="left", fill="x", expand=True)
        tk.Entry(srt_wrap, textvariable=self.srt_file_path, state="readonly",
                 readonlybackground=CARD_HEADER, fg=TEXT_PRIMARY,
                 font=(UI_FONT, 9), relief="flat", bd=0
                 ).pack(fill="x", padx=6, ipady=4)

        fs_row = tk.Frame(sub_body, bg=CARD_BG)
        fs_row.pack(fill="x", pady=(4, 0))
        tk.Label(fs_row, text="Cỡ chữ phụ đề:", bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 9)).pack(side="left", padx=(0, 8))
        _fsf = tk.Frame(fs_row, bg=CARD_HEADER)
        _fsf.pack(side="left")
        tk.Entry(_fsf, textvariable=self.subtitle_font_size, width=4,
                 bg=CARD_HEADER, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
                 font=(UI_FONT, 10), relief="flat", bd=0, justify="center"
                 ).pack(padx=6, ipady=4)

        _show(sub_body, self.use_subtitle)
        self._on_sub_mode_change()

        # Card: Banner
        banner_card = Card(parent, "✍  Tiêu đề / Banner")
        banner_card.pack(fill="x", pady=(0, 12), padx=(0, 4))

        banner_body = tk.Frame(banner_card.body, bg=CARD_BG)
        ToggleSwitch(banner_card.body, "Bật tiêu đề banner", self.use_banner,
                     command=lambda: _show(banner_body, self.use_banner)
                     ).pack(anchor="w", pady=(0, 6), fill="x")

        for (lbl, en_var, txt_var, fs_var, tc_var, bc_var, bo_var) in [
            ("Banner trên",  self.banner_top_enabled, self.banner_top_text,
             self.banner_top_fontsize,  self.banner_top_textcolor,
             self.banner_top_bgcolor,   self.banner_top_bgopacity),
            ("Banner dưới", self.banner_bot_enabled, self.banner_bot_text,
             self.banner_bot_fontsize,  self.banner_bot_textcolor,
             self.banner_bot_bgcolor,   self.banner_bot_bgopacity),
        ]:
            sec = tk.Frame(banner_body, bg=CARD_HEADER)
            sec.pack(fill="x", pady=(0, 8))
            hdr_f = tk.Frame(sec, bg=CARD_HEADER)
            hdr_f.pack(fill="x", padx=8, pady=(6, 2))
            sub_b = tk.Frame(sec, bg=CARD_HEADER)

            def _on_bt(f=sub_b, v=en_var):
                _show(f, v)

            ToggleSwitch(hdr_f, lbl, en_var, bg=CARD_HEADER,
                         command=_on_bt).pack(side="left")

            txt_f = tk.Frame(sub_b, bg=INPUT_BG)
            txt_f.pack(fill="x", padx=8, pady=(2, 4))
            tk.Entry(txt_f, textvariable=txt_var,
                     bg=INPUT_BG, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
                     font=(UI_FONT, 10), relief="flat", bd=0
                     ).pack(fill="x", padx=4, pady=3)

            ctl = tk.Frame(sub_b, bg=CARD_HEADER)
            ctl.pack(fill="x", padx=8, pady=(0, 8))
            tk.Label(ctl, text="Cỡ:", bg=CARD_HEADER, fg=TEXT_SECONDARY,
                     font=(UI_FONT, 8)).pack(side="left")
            _ff = tk.Frame(ctl, bg=INPUT_BG)
            _ff.pack(side="left", padx=(3, 10))
            tk.Entry(_ff, textvariable=fs_var, width=4,
                     bg=INPUT_BG, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
                     font=(UI_FONT, 9), relief="flat", bd=0, justify="center"
                     ).pack(padx=4, pady=3)

            for col_lbl, col_var in [("Chữ", tc_var), ("Nền", bc_var)]:
                tk.Label(ctl, text=f"{col_lbl}:", bg=CARD_HEADER, fg=TEXT_SECONDARY,
                         font=(UI_FONT, 8)).pack(side="left")
                sw = tk.Label(ctl, width=3, height=1, cursor="hand2",
                              relief="flat", bd=1)
                sw.pack(side="left", padx=(3, 10))

                def _upd_sw(*_, s=sw, cv=col_var):
                    try:
                        s.config(bg=cv.get())
                    except Exception:
                        pass

                def _pick(cv=col_var, s=sw):
                    r = colorchooser.askcolor(color=cv.get(), title="Chọn màu",
                                              parent=self._root_window)[1]
                    if r:
                        cv.set(r)

                col_var.trace_add("write", _upd_sw)
                sw.bind("<Button-1>", lambda e, fn=_pick: fn())
                _upd_sw()

            tk.Label(ctl, text="Mờ:", bg=CARD_HEADER, fg=TEXT_SECONDARY,
                     font=(UI_FONT, 8)).pack(side="left")
            _of = tk.Frame(ctl, bg=INPUT_BG)
            _of.pack(side="left", padx=(3, 0))
            tk.Entry(_of, textvariable=bo_var, width=4,
                     bg=INPUT_BG, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
                     font=(UI_FONT, 9), relief="flat", bd=0, justify="center"
                     ).pack(padx=4, pady=3)

            _show(sub_b, en_var)

        _show(banner_body, self.use_banner)

        # Card: Nhạc nền
        bgm_card = Card(parent, "🎵  Nhạc nền (BGM)")
        bgm_card.pack(fill="x", pady=(0, 12), padx=(0, 4))

        bgm_body = tk.Frame(bgm_card.body, bg=CARD_BG)
        ToggleSwitch(bgm_card.body, "Bật nhạc nền", self.use_bgm,
                     command=lambda: _show(bgm_body, self.use_bgm)
                     ).pack(anchor="w", pady=(0, 6), fill="x")

        bgm_row = tk.Frame(bgm_body, bg=CARD_BG)
        bgm_row.pack(fill="x", pady=(0, 8))
        FlatButton(bgm_row, "🎵  Chọn file nhạc", command=self._browse_bgm,
                   bg=BTN_SECONDARY, hover_bg=BTN_SEC_HOVER, pressed_bg=DIVIDER,
                   width=130, height=34, font=(UI_FONT, 9)
                   ).pack(side="left", padx=(0, 8))
        bgm_path_wrap = tk.Frame(bgm_row, bg=CARD_HEADER)
        bgm_path_wrap.pack(side="left", fill="x", expand=True)
        tk.Entry(bgm_path_wrap, textvariable=self.bgm_path, state="readonly",
                 readonlybackground=CARD_HEADER, fg=TEXT_PRIMARY,
                 font=(UI_FONT, 9), relief="flat", bd=0
                 ).pack(fill="x", padx=6, ipady=4)

        bgm_ctl = tk.Frame(bgm_body, bg=CARD_BG)
        bgm_ctl.pack(fill="x", pady=(0, 6))
        tk.Label(bgm_ctl, text="Âm lượng BGM:", bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 9)).pack(side="left", padx=(0, 6))
        _bvf = tk.Frame(bgm_ctl, bg=CARD_HEADER)
        _bvf.pack(side="left", padx=(0, 16))
        tk.Entry(_bvf, textvariable=self.bgm_volume, width=5,
                 bg=CARD_HEADER, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
                 font=(UI_FONT, 9), relief="flat", bd=0, justify="center"
                 ).pack(padx=6, ipady=4)

        bgm_mode_row = tk.Frame(bgm_body, bg=CARD_BG)
        bgm_mode_row.pack(fill="x")
        tk.Label(bgm_mode_row, text="Chế độ:", bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 9)).pack(side="left", padx=(0, 8))
        for lbl, val in [("Trộn với audio gốc", "mix"),
                          ("Thay thế audio gốc", "replace")]:
            tk.Radiobutton(bgm_mode_row, text=lbl, variable=self.bgm_mode, value=val,
                           bg=CARD_BG, fg=TEXT_PRIMARY, selectcolor=CARD_HEADER,
                           activebackground=CARD_BG, activeforeground=TEXT_PRIMARY,
                           font=(UI_FONT, 9), relief="flat"
                           ).pack(side="left", padx=(0, 10))

        _show(bgm_body, self.use_bgm)

        # Card: Tách giọng nói / nhạc nền
        vsep_card = Card(parent, "🎤  Tách giọng / nhạc nền (Demucs)")
        vsep_card.pack(fill="x", pady=(0, 12), padx=(0, 4))

        vsep_body = tk.Frame(vsep_card.body, bg=CARD_BG)
        ToggleSwitch(vsep_card.body, "Tách âm thanh (Demucs)", self.use_vocal_sep,
                     command=lambda: _show(vsep_body, self.use_vocal_sep)
                     ).pack(anchor="w", pady=(0, 6), fill="x")

        vsep_keep_row = tk.Frame(vsep_body, bg=CARD_BG)
        vsep_keep_row.pack(fill="x", pady=(0, 6))
        tk.Label(vsep_keep_row, text="Giữ lại:", bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 9)).pack(side="left", padx=(0, 8))
        for _lbl, _val in [("🎙 Giọng nói (bỏ nhạc)", "vocals"),
                            ("🎵 Nhạc nền (bỏ giọng)", "no_vocals")]:
            tk.Radiobutton(vsep_keep_row, text=_lbl, variable=self.vocal_sep_keep, value=_val,
                           bg=CARD_BG, fg=TEXT_PRIMARY, selectcolor=CARD_HEADER,
                           activebackground=CARD_BG, activeforeground=TEXT_PRIMARY,
                           font=(UI_FONT, 9), relief="flat"
                           ).pack(side="left", padx=(0, 10))

        if not DEMUCS_AVAILABLE:
            tk.Label(vsep_body, text="⚠ Demucs chưa cài. Chạy: pip install demucs",
                     bg=CARD_BG, fg=DANGER, font=(UI_FONT, 8)
                     ).pack(anchor="w", pady=(4, 0))
        else:
            tk.Label(vsep_body, text="Lần đầu chạy sẽ tự tải model ~80MB",
                     bg=CARD_BG, fg=TEXT_MUTED, font=(UI_FONT, 8)
                     ).pack(anchor="w", pady=(4, 0))

        _show(vsep_body, self.use_vocal_sep)

    # ── Right column ──────────────────────────────────────────────────────

    def _build_right(self, parent):
        if self._close_cb:
            close_bar = tk.Frame(parent, bg=APP_BG)
            close_bar.pack(fill="x", pady=(0, 6))
            FlatButton(close_bar, "× Đóng tab", command=self._close_cb,
                       bg=BTN_SECONDARY, hover_bg=DANGER, pressed_bg="#C0392B",
                       fg=TEXT_SECONDARY, height=26, font=(UI_FONT, 8), padx=10
                       ).pack(side="right")

        prog_card = Card(parent, "Tiến trình")
        prog_card.pack(fill="x", pady=(0, 14))

        top_row = tk.Frame(prog_card.body, bg=CARD_BG)
        top_row.pack(fill="x", pady=(0, 10))

        text_col = tk.Frame(top_row, bg=CARD_BG)
        text_col.pack(side="left", fill="x", expand=True)

        self.status_label = tk.Label(text_col, text="Sẵn sàng",
                                     bg=CARD_BG, fg=TEXT_PRIMARY,
                                     font=(UI_FONT, 12, "bold"), anchor="w")
        self.status_label.pack(anchor="w")

        self.detail_label = tk.Label(text_col,
                                     text="Chọn video và bật tính năng rồi nhấn Xử lý",
                                     bg=CARD_BG, fg=TEXT_SECONDARY,
                                     font=(UI_FONT, 9), anchor="w",
                                     wraplength=280, justify="left")
        self.detail_label.pack(anchor="w", pady=(3, 0))

        self.percent_label = tk.Label(top_row, text="—",
                                      bg=CARD_BG, fg=TEXT_MUTED,
                                      font=(UI_FONT, 26, "bold"))
        self.percent_label.pack(side="right", padx=(12, 0))

        self._progress_var = tk.DoubleVar(value=0)
        self.progress = ttk.Progressbar(prog_card.body, mode="determinate",
                                        variable=self._progress_var,
                                        style="Modern.Horizontal.TProgressbar")
        self.progress.pack(fill="x", ipady=3)

        bot_row = tk.Frame(prog_card.body, bg=CARD_BG)
        bot_row.pack(fill="x", pady=(8, 0))
        self.time_label = tk.Label(bot_row, text="⏱  0.0s",
                                   bg=CARD_BG, fg=TEXT_MUTED, font=(UI_FONT, 9))
        self.time_label.pack(side="left")

        log_card = Card(parent, "Nhật ký xử lý")
        log_card.pack(fill="both", expand=True, pady=(0, 14))

        log_inner = tk.Frame(log_card.body, bg=CARD_HEADER)
        log_inner.pack(fill="both", expand=True)

        self._log_box = tk.Text(
            log_inner, bg=CARD_HEADER, fg=TEXT_PRIMARY,
            insertbackground=TEXT_PRIMARY,
            font=(MONO_FONT, 9), relief="flat", borderwidth=0,
            wrap="word", padx=12, pady=10, spacing1=2, spacing3=2,
        )
        self._log_box.pack(side="left", fill="both", expand=True)
        log_sc = tk.Scrollbar(log_inner, command=self._log_box.yview)
        log_sc.pack(side="right", fill="y")
        self._log_box.config(yscrollcommand=log_sc.set)

        for tag, fg_c in [("success", SUCCESS), ("error", DANGER),
                           ("info", ACCENT), ("muted", TEXT_MUTED)]:
            self._log_box.tag_configure(tag, foreground=fg_c)
        self._log_box.tag_configure("bold", font=(MONO_FONT, 9, "bold"))

        gen_row = tk.Frame(parent, bg=APP_BG)
        gen_row.pack(fill="x")

        self._proc_btn = FlatButton(
            gen_row, "▶  Xử lý video", command=self._start_process,
            height=50, font=(UI_FONT, 13, "bold")
        )
        self._proc_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self._stop_btn = FlatButton(
            gen_row, "⏹  Dừng", command=self._request_stop,
            bg=DANGER, hover_bg=DANGER_HOVER, pressed_bg="#C0392B",
            fg=TEXT_PRIMARY, width=100, height=50, font=(UI_FONT, 11, "bold")
        )
        self._stop_btn.pack(side="left")
        self._stop_btn.set_state("disabled")

    # ── Browse ────────────────────────────────────────────────────────────

    def _browse_input(self):
        path = filedialog.askopenfilename(
            parent=self._root_window, title="Chọn video nguồn",
            filetypes=[("Video", "*.mp4 *.mkv *.mov *.avi *.webm *.flv *.m4v *.wmv"),
                       ("Tất cả", "*.*")]
        )
        if path:
            self.input_video.set(path)
            self._badge_lbl.pack(side="left", padx=(6, 0))
            if not self.output_dir.get():
                self.output_dir.set(os.path.dirname(path))
            base = os.path.splitext(os.path.basename(path))[0]
            self.output_name.set(f"{base}_edited.mp4")

    def _browse_output_dir(self):
        d = filedialog.askdirectory(parent=self._root_window, title="Chọn thư mục output")
        if d:
            self.output_dir.set(d)

    def _browse_srt(self):
        path = filedialog.askopenfilename(
            parent=self._root_window, title="Chọn file SRT",
            filetypes=[("SRT", "*.srt"), ("Tất cả", "*.*")]
        )
        if path:
            self.srt_file_path.set(path)

    def _on_sub_mode_change(self):
        if self.sub_mode.get() == "whisper":
            self._srt_opts.pack_forget()
            self._whisper_opts.pack(fill="x", pady=(0, 8), after=self._mode_row)
        else:
            self._whisper_opts.pack_forget()
            self._srt_opts.pack(fill="x", pady=(0, 8), after=self._mode_row)

    def _add_pip_item(self):
        path = filedialog.askopenfilename(
            parent=self._root_window, title="Chọn video overlay",
            filetypes=[("Video", "*.mp4 *.mov *.mkv *.avi *.flv"), ("Tất cả", "*.*")]
        )
        if not path:
            return

        pos_var  = tk.StringVar(value="bottom-right")
        size_var = tk.IntVar(value=25)

        item_frame = tk.Frame(self._pip_list_frame, bg=CARD_HEADER)
        item_frame.pack(fill="x", pady=(0, 6))

        item = {"path": path, "pos_var": pos_var, "size_var": size_var, "frame": item_frame}

        row1 = tk.Frame(item_frame, bg=CARD_HEADER)
        row1.pack(fill="x", padx=8, pady=(6, 2))

        del_lbl = tk.Label(row1, text="✕", bg=CARD_HEADER, fg=DANGER,
                           font=(UI_FONT, 11), cursor="hand2", padx=6)
        del_lbl.pack(side="right")
        del_lbl.bind("<Button-1>", lambda e, it=item: self._remove_pip_item(it))

        tk.Label(row1, text=f"▶ {os.path.basename(path)}",
                 bg=CARD_HEADER, fg=TEXT_PRIMARY, font=(UI_FONT, 9), anchor="w"
                 ).pack(side="left", fill="x", expand=True)

        row2 = tk.Frame(item_frame, bg=CARD_HEADER)
        row2.pack(fill="x", padx=8, pady=(2, 8))

        PositionPicker(row2, pos_var, bg=CARD_HEADER).pack(side="left")

        size_col = tk.Frame(row2, bg=CARD_HEADER)
        size_col.pack(side="left", padx=(20, 0), anchor="n")
        tk.Label(size_col, text="Kích thước (%)", bg=CARD_HEADER, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 8)).pack(anchor="w")
        size_f = tk.Frame(size_col, bg=INPUT_BG)
        size_f.pack(pady=(3, 0))
        tk.Entry(size_f, textvariable=size_var, width=5,
                 bg=INPUT_BG, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
                 font=(UI_FONT, 11), relief="flat", bd=0, justify="center"
                 ).pack(padx=4, pady=3)

        self._pip_items.append(item)

    def _remove_pip_item(self, item):
        if item in self._pip_items:
            self._pip_items.remove(item)
            item["frame"].destroy()

    def _browse_logo(self):
        path = filedialog.askopenfilename(
            parent=self._root_window, title="Chọn ảnh logo",
            filetypes=[("Ảnh", "*.png *.jpg *.jpeg *.gif *.bmp *.webp"),
                       ("Tất cả", "*.*")]
        )
        if path:
            self.logo_path.set(path)

    def _browse_bgm(self):
        path = filedialog.askopenfilename(
            parent=self._root_window, title="Chọn file nhạc nền",
            filetypes=[("Audio", "*.mp3 *.wav *.m4a *.aac *.ogg *.flac"),
                       ("Tất cả", "*.*")]
        )
        if path:
            self.bgm_path.set(path)

    # ── Process controls ──────────────────────────────────────────────────

    def _request_stop(self):
        self._stop_event.set()
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass

    def _check_stop(self):
        if self._stop_event.is_set():
            raise InterruptedError("Người dùng đã dừng")

    def _start_process(self):
        if not self.input_video.get():
            messagebox.showerror("Lỗi", "Vui lòng chọn video nguồn",
                                 parent=self._root_window)
            return
        if not self.output_dir.get():
            messagebox.showerror("Lỗi", "Vui lòng chọn thư mục output",
                                 parent=self._root_window)
            return
        if not self.output_name.get().strip():
            messagebox.showerror("Lỗi", "Vui lòng nhập tên file output",
                                 parent=self._root_window)
            return
        active = [self.use_zoom.get(),
                  self.use_pip.get() and bool(self._pip_items),
                  self.use_logo.get() and bool(self.logo_path.get()),
                  self.use_subtitle.get(), self.use_banner.get(),
                  self.use_bgm.get() and bool(self.bgm_path.get()),
                  self.use_vocal_sep.get()]
        if not any(active):
            messagebox.showinfo("Thông báo",
                                "Vui lòng bật ít nhất một tính năng",
                                parent=self._root_window)
            return

        self._stop_event.clear()
        self._proc_btn.set_state("disabled")
        self._proc_btn.set_text("ĐANG XỬ LÝ...")
        self._stop_btn.set_state("normal")
        self._progress_var.set(0)
        self.status_label.config(text="Đang xử lý...", fg=TEXT_PRIMARY)
        self.detail_label.config(text="Khởi tạo...")
        self.percent_label.config(text="0%", fg=TEXT_MUTED)
        self.time_label.config(text="⏱  0.0s")
        self._log_box.delete("1.0", "end")
        threading.Thread(target=self._process, daemon=True).start()

    # ── Main process ──────────────────────────────────────────────────────

    def _process(self):
        import tempfile as _tf
        temp_dir  = None
        out_dir   = self.output_dir.get()
        out_name  = self.output_name.get().strip()
        file_size = 0.0
        start_t   = time.time()
        stop_timer = threading.Event()

        def _tick():
            while not stop_timer.is_set():
                elapsed = time.time() - start_t
                self.queue_ui(lambda s=elapsed:
                              self.time_label.config(text=f"⏱  {s:.1f}s"))
                time.sleep(0.5)
        threading.Thread(target=_tick, daemon=True).start()

        try:
            if not out_name.lower().endswith(".mp4"):
                out_name += ".mp4"
            input_path  = self.input_video.get()
            output_path = os.path.join(out_dir, out_name)

            self.log("=" * 50, "muted")
            self.log("BẮT ĐẦU CHỈNH SỬA VIDEO", "bold")
            self.log("=" * 50, "muted")
            self.log(f"Input : {input_path}", "info")
            self.log(f"Output: {output_path}", "info")

            self.log("Đọc thông tin video...", "muted")
            vid_w, vid_h, vid_fps = self._get_video_info(input_path)
            vid_dur = get_media_duration(input_path)
            self.log(f"  {vid_w}×{vid_h} @ {vid_fps:.2f}fps  |  {vid_dur:.1f}s", "muted")
            self._check_stop()

            temp_dir = _tf.mkdtemp(prefix="videdit_")
            current  = input_path

            use_vsep = self.use_vocal_sep.get()
            use_zoom = self.use_zoom.get()
            use_pip  = self.use_pip.get() and bool(self._pip_items)
            use_logo = self.use_logo.get() and bool(self.logo_path.get())
            use_sub  = self.use_subtitle.get()
            use_ban  = self.use_banner.get()
            use_bgm  = self.use_bgm.get() and bool(self.bgm_path.get())
            total_steps = sum([use_vsep, use_zoom, use_pip, use_logo, use_sub, use_ban, use_bgm])
            done = 0

            # Tách giọng nói / nhạc nền (Demucs) — chạy đầu tiên để các bước sau dùng audio sạch
            if use_vsep:
                done += 1
                if not DEMUCS_AVAILABLE:
                    raise ValueError("Demucs chưa cài. Chạy: pip install demucs")

                _vsep_keep = self.vocal_sep_keep.get()
                _keep_lbl  = "Giọng nói" if _vsep_keep == "vocals" else "Nhạc nền"
                self.log(f"\n[{done}/{total_steps}] Tách âm thanh (Demucs)...", "info")
                self.log(f"  Giữ lại: {_keep_lbl}", "muted")
                self._upd_progress((done - 1) / total_steps, "Tách âm thanh...")

                # Trích audio ra WAV stereo
                _vsep_wav = os.path.join(temp_dir, "vsep_input.wav")
                self.log("  Trích audio...", "muted")
                _ext_cmd = [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-i", normalize_path(current),
                    "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", _vsep_wav
                ]
                self._proc = subprocess.Popen(
                    _ext_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", errors="replace",
                    creationflags=SUBPROCESS_FLAGS
                )
                self._proc.wait()
                _ext_ok  = self._proc.returncode == 0
                _ext_err = self._proc.stderr.read()
                self._proc = None
                if not _ext_ok:
                    self._check_stop()
                    raise Exception(f"Không thể trích audio: {_ext_err[:200]}")
                self._check_stop()

                # Chạy Demucs
                self.log("  Chạy Demucs (lần đầu tự tải model ~80MB)...", "muted")
                _vsep_out_dir = os.path.join(temp_dir, "demucs_out")
                os.makedirs(_vsep_out_dir, exist_ok=True)

                _demucs_cmd = [
                    sys.executable, "-m", "demucs",
                    "--two-stems=vocals",
                    "-o", _vsep_out_dir,
                    _vsep_wav
                ]
                self._proc = subprocess.Popen(
                    _demucs_cmd,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", errors="replace",
                    creationflags=SUBPROCESS_FLAGS
                )

                # tqdm ghi progress vào stderr bằng \r → đọc char-by-char trong thread riêng
                import re as _re
                _demucs_stderr_lines = []
                _vsep_base = (done - 1) / total_steps
                _vsep_span = 1.0 / total_steps

                def _read_demucs_stderr():
                    buf = ""
                    while True:
                        ch = self._proc.stderr.read(1)
                        if not ch:
                            break
                        if ch in ("\r", "\n"):
                            line = buf.strip()
                            buf = ""
                            if not line:
                                continue
                            _demucs_stderr_lines.append(line)
                            m = _re.search(r"(\d+)%\|", line)
                            if m:
                                _pct = int(m.group(1)) / 100.0
                                self._upd_progress(
                                    _vsep_base + _pct * _vsep_span * 0.9,
                                    f"Demucs... {int(_pct * 100)}%"
                                )
                            elif any(k in line for k in
                                     ("Separating", "Selected", "Model",
                                      "Downloading", "Using")):
                                self.log(f"  {line}", "muted")
                        else:
                            buf += ch

                _stderr_t = threading.Thread(target=_read_demucs_stderr, daemon=True)
                _stderr_t.start()

                # stdout: thông tin model / download
                _demucs_stdout_lines = []
                for _dline in self._proc.stdout:
                    if self._stop_event.is_set():
                        self._proc.terminate()
                        break
                    _dline = _dline.strip()
                    if _dline:
                        _demucs_stdout_lines.append(_dline)
                        if any(k in _dline for k in
                               ("Separating", "Selected", "Model",
                                "Downloading", "Using")):
                            self.log(f"  {_dline}", "muted")

                self._proc.wait()
                _stderr_t.join(timeout=3)
                _demucs_ok = self._proc.returncode == 0
                self._proc = None
                if not _demucs_ok:
                    self._check_stop()
                    _err_tail = (_demucs_stderr_lines + _demucs_stdout_lines)[-4:]
                    raise Exception(f"Demucs thất bại: {' | '.join(_err_tail)}")
                self._check_stop()

                # Tìm file output (htdemucs/STEM/vocals.wav hoặc no_vocals.wav)
                _stem_name   = os.path.splitext(os.path.basename(_vsep_wav))[0]
                _vsep_result = None
                for _mdir in ["htdemucs", "htdemucs_ft", "mdx_extra", "mdx_extra_q"]:
                    _cand = os.path.join(_vsep_out_dir, _mdir, _stem_name,
                                         f"{_vsep_keep}.wav")
                    if os.path.exists(_cand):
                        _vsep_result = _cand
                        break
                if not _vsep_result:
                    for _r, _d, _fs in os.walk(_vsep_out_dir):
                        for _f in _fs:
                            if _f == f"{_vsep_keep}.wav":
                                _vsep_result = os.path.join(_r, _f)
                                break
                        if _vsep_result:
                            break
                if not _vsep_result:
                    raise Exception(
                        f"Không tìm thấy {_vsep_keep}.wav trong output Demucs"
                    )

                # Ghép audio đã tách lại với video gốc
                self.log("  Ghép audio vào video...", "muted")
                _vsep_merged = os.path.join(temp_dir, "step_vsep.mp4")
                _merge_cmd = [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-i", normalize_path(current),
                    "-i", _vsep_result,
                    "-map", "0:v", "-map", "1:a",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                    "-movflags", "+faststart", _vsep_merged
                ]
                self._proc = subprocess.Popen(
                    _merge_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", errors="replace",
                    creationflags=SUBPROCESS_FLAGS
                )
                self._proc.wait()
                _merge_ok  = self._proc.returncode == 0
                _merge_err = self._proc.stderr.read()
                self._proc = None
                if not _merge_ok:
                    self._check_stop()
                    raise Exception(f"Lỗi ghép video: {_merge_err[:200]}")

                current = _vsep_merged
                self.log(f"  Tách âm thanh xong ({_keep_lbl})", "success")
                self._upd_progress(done / total_steps, "")
                self._check_stop()

            # Zoom (Ken Burns — pulse mỗi N giây)
            if use_zoom:
                done += 1
                _zoom_max  = max(1.05, self.zoom_max.get() / 100.0)
                _factor    = _zoom_max - 1.0
                _interval  = max(1, self.zoom_interval.get())

                # h264_videotoolbox + scale filter treo trên macOS → luôn dùng libx264
                _zoom_enc_args = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "23"]
                _zoom_base_pct  = (done - 1) / total_steps
                _zoom_step_span = 1 / total_steps

                import math as _math
                # 8 segment/cycle: đủ mịn, đạt đúng 1.0x và zoom_max
                _spc      = 8
                n_cycles  = max(1, min(round(vid_dur / _interval), 60 // _spc))
                n_segs    = n_cycles * _spc        # exact multiple → left-aligned sạch
                seg_dur_s = vid_dur / n_segs
                actual_ivl = vid_dur / n_cycles

                def _pulse_z(seg_idx):
                    # sine wave left-aligned: pos=0→z=1.0x, pos=0.5→z=zoom_max, pos=1.0→z=1.0x
                    pos  = (seg_idx % _spc) / _spc
                    wave = (1.0 - _math.cos(2 * _math.pi * pos)) / 2.0   # 0→1→0
                    return 1.0 + _factor * wave

                self.log(f"\n[{done}/{total_steps}] Zoom nhịp (Ken Burns)...", "info")
                self.log(f"  Mỗi ~{actual_ivl:.0f}s  |  Max: {self.zoom_max.get()}%"
                         f"  |  {n_cycles} lần  |  {n_segs} đoạn", "muted")
                self._upd_progress(_zoom_base_pct, "Áp dụng zoom...")

                # === PASS 1: encode từng đoạn VIDEO ONLY (không audio) ===
                _seg_paths = []
                for _si in range(n_segs):
                    self._check_stop()
                    _z        = max(1.0, min(_zoom_max, _pulse_z(_si)))
                    _sw       = int(vid_w * _z / 2) * 2
                    _sh       = int(vid_h * _z / 2) * 2
                    _cx       = (_sw - vid_w) // 2
                    _cy       = (_sh - vid_h) // 2
                    _seg_path = os.path.join(temp_dir, f"zoom_seg_{_si:02d}.mp4")
                    _seg_paths.append(_seg_path)

                    self._upd_progress(
                        _zoom_base_pct + (_si / n_segs * 0.85) * _zoom_step_span,
                        f"Zoom đoạn {_si+1}/{n_segs}"
                    )
                    _seg_cmd = [
                        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        "-ss", f"{_si * seg_dur_s:.3f}", "-i", normalize_path(current),
                        "-t", f"{seg_dur_s:.3f}",
                        "-vf", f"scale={_sw}:{_sh}:flags=lanczos,crop={vid_w}:{vid_h}:{_cx}:{_cy}",
                        *_zoom_enc_args, "-an",          # video only, không audio
                        "-pix_fmt", "yuv420p", _seg_path
                    ]
                    self._proc = subprocess.Popen(
                        _seg_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                        text=True, encoding="utf-8", errors="replace",
                        creationflags=SUBPROCESS_FLAGS
                    )
                    _, _seg_err = self._proc.communicate()
                    _seg_ok = self._proc.returncode == 0
                    self._proc = None
                    if not _seg_ok:
                        self._check_stop()
                        raise Exception(f"Lỗi zoom đoạn {_si+1}: {_seg_err[:200]}")

                # === PASS 2: concat video segments ===
                self._check_stop()
                self._upd_progress(
                    _zoom_base_pct + 0.85 * _zoom_step_span, "Ghép đoạn zoom...")
                _concat_txt  = os.path.join(temp_dir, "zoom_concat.txt")
                _zoom_vonly  = os.path.join(temp_dir, "zoom_vonly.mp4")
                with open(_concat_txt, "w", encoding="utf-8") as _cf:
                    for _sp in _seg_paths:
                        _cf.write(f"file '{_sp.replace(os.sep, '/')}'\n")
                self._proc = subprocess.Popen(
                    ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                     "-f", "concat", "-safe", "0", "-i", _concat_txt,
                     "-c", "copy", _zoom_vonly],
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", errors="replace",
                    creationflags=SUBPROCESS_FLAGS
                )
                _, _cat_err = self._proc.communicate()
                if self._proc.returncode != 0:
                    self._check_stop()
                    raise Exception(f"Lỗi ghép zoom: {_cat_err[:200]}")
                self._proc = None

                # === PASS 3: ghép lại audio gốc (không cắt, không encode lại) ===
                self._check_stop()
                self._upd_progress(
                    _zoom_base_pct + 0.95 * _zoom_step_span, "Ghép audio gốc...")
                zoom_out_path = os.path.join(temp_dir, "step_zoom.mp4")
                self._proc = subprocess.Popen(
                    ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                     "-i", _zoom_vonly,
                     "-i", normalize_path(current),
                     "-map", "0:v:0", "-map", "1:a?",
                     "-c:v", "copy", "-c:a", "copy",
                     "-movflags", "+faststart", zoom_out_path],
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", errors="replace",
                    creationflags=SUBPROCESS_FLAGS
                )
                _, _mrg_err = self._proc.communicate()
                if self._proc.returncode != 0:
                    self._check_stop()
                    raise Exception(f"Lỗi ghép audio: {_mrg_err[:200]}")
                self._proc = None

                current = zoom_out_path
                self.log("  Zoom xong", "success")
                self._upd_progress(done / total_steps, "")
                self._check_stop()

            # PiP
            if use_pip:
                done += 1
                self.log(f"\n[{done}/{total_steps}] Video overlay (PiP)...", "info")
                self._upd_progress((done-1)/total_steps, "Áp dụng PiP...")

                pip_paths = [it["path"] for it in self._pip_items]
                pip_pos   = [it["pos_var"].get() for it in self._pip_items]
                pip_sizes = [
                    max(2, int(vid_w * it["size_var"].get() / 100) // 2 * 2)
                    for it in self._pip_items
                ]
                pip_out  = os.path.join(temp_dir, "step_pip.mp4")
                base_pct = (done - 1) / total_steps
                step_span = 1 / total_steps

                def _pip_cb(pct, _bp=base_pct, _sp=step_span):
                    self._upd_progress(_bp + pct * _sp, f"PiP... {pct*100:.0f}%")

                ok, err = apply_pip_overlays(
                    current, pip_paths, pip_pos, pip_sizes,
                    pip_out, vid_dur, vid_fps, BEST_ENCODER,
                    progress_callback=_pip_cb, stop_event=self._stop_event
                )
                if not ok:
                    self._check_stop()
                    raise Exception(f"Lỗi PiP: {err[:200]}")
                current = pip_out
                self.log("  PiP xong", "success")
                self._upd_progress(done / total_steps, "")
                self._check_stop()

            # Logo overlay
            if use_logo:
                done += 1
                self.log(f"\n[{done}/{total_steps}] Logo kênh...", "info")
                self._upd_progress((done - 1) / total_steps, "Áp dụng logo...")

                _logo_path  = self.logo_path.get()
                _logo_pos   = self.logo_pos.get()
                _logo_w     = max(2, int(vid_w * self.logo_size.get() / 100) // 2 * 2)
                _logo_op    = max(0.01, min(1.0, float(self.logo_opacity.get())))
                logo_out    = os.path.join(temp_dir, "step_logo.mp4")

                _lmargin = max(0, self.logo_padding.get())
                _lpos_map = {
                    "top-left":     (_lmargin, _lmargin),
                    "top-right":    (f"W-w-{_lmargin}", _lmargin),
                    "center":       ("(W-w)/2", "(H-h)/2"),
                    "bottom-left":  (_lmargin, f"H-h-{_lmargin}"),
                    "bottom-right": (f"W-w-{_lmargin}", f"H-h-{_lmargin}"),
                }
                _lx, _ly = _lpos_map.get(_logo_pos, (_lmargin, _lmargin))

                _logo_filter = (
                    f"[1:v]scale={_logo_w}:-2:flags=lanczos,format=rgba,"
                    f"colorchannelmixer=aa={_logo_op:.3f}[logo];"
                    f"[0:v][logo]overlay={_lx}:{_ly}:format=auto[vout]"
                )
                _logo_base_pct  = (done - 1) / total_steps
                _logo_step_span = 1 / total_steps
                _logo_enc = BEST_ENCODER if BEST_ENCODER == "h264_videotoolbox" else "libx264"
                _logo_enc_args = get_encoder_args(_logo_enc, vid_fps)
                _logo_cmd = [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-progress", "pipe:1", "-stats_period", "1",
                    "-i", current, "-i", normalize_path(_logo_path),
                    "-filter_complex", _logo_filter,
                    "-map", "[vout]", "-map", "0:a?",
                    *_logo_enc_args, "-c:a", "copy",
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                    logo_out
                ]
                self._proc = subprocess.Popen(
                    _logo_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", errors="replace",
                    creationflags=SUBPROCESS_FLAGS
                )
                for line in self._proc.stdout:
                    if self._stop_event.is_set():
                        self._proc.terminate()
                        break
                    if line.startswith("out_time_us=") and vid_dur > 0:
                        try:
                            us = int(line.strip().split("=")[1])
                            pct = min(us / 1_000_000 / vid_dur, 1.0)
                            self._upd_progress(
                                _logo_base_pct + pct * _logo_step_span,
                                f"Logo... {pct*100:.0f}%"
                            )
                        except Exception:
                            pass
                self._proc.wait()
                _logo_stderr = self._proc.stderr.read()
                _logo_ok = self._proc.returncode == 0
                self._proc = None
                if not _logo_ok:
                    self._check_stop()
                    raise Exception(f"Lỗi logo: {_logo_stderr[:200]}")
                current = logo_out
                self.log("  Logo xong", "success")
                self._upd_progress(done / total_steps, "")
                self._check_stop()

            # Subtitle
            if use_sub:
                done += 1
                self.log(f"\n[{done}/{total_steps}] Phụ đề...", "info")
                self._upd_progress((done-1)/total_steps, "Chuẩn bị phụ đề...")

                srt_path = None
                if self.sub_mode.get() == "whisper":
                    if not WHISPER_AVAILABLE:
                        raise ValueError("Whisper chưa cài. Chạy: pip install openai-whisper")

                    wav_path = os.path.join(temp_dir, "audio.wav")
                    self.log("  Trích audio...", "muted")
                    if not extract_audio_for_whisper(current, wav_path):
                        raise Exception("Không thể trích audio từ video")
                    self._check_stop()

                    m_name = self.whisper_model_var.get()
                    task   = self.whisper_task.get()
                    lang   = self.subtitle_language.get().strip() or None
                    self.log(f"  Load Whisper '{m_name}'...", "muted")
                    self._upd_progress((done-1)/total_steps + 0.05, "Load Whisper...")
                    import whisper as _wh
                    _model = _wh.load_model(m_name)
                    self._check_stop()

                    task_lbl = ("translate → EN" if task == "translate"
                                else f"transcribe ({lang or 'auto'})")
                    self.log(f"  Transcribe [{task_lbl}]...", "muted")
                    self._upd_progress((done-1)/total_steps + 0.1, "Transcribing...")
                    result = _model.transcribe(wav_path, fp16=False, task=task, language=lang)
                    self._check_stop()

                    srt_path = os.path.join(temp_dir, "subtitle.srt")
                    self._write_srt_from_whisper(result, srt_path)
                    n_seg = len(result.get("segments", []))
                    self.log(f"  Transcribe xong: {n_seg} đoạn phụ đề", "success")

                else:
                    srt_path = self.srt_file_path.get()
                    if not srt_path or not os.path.exists(srt_path):
                        raise ValueError("Vui lòng chọn file SRT hợp lệ")
                    self.log(f"  Dùng SRT: {os.path.basename(srt_path)}", "info")

                self._check_stop()
                self.log("  Burn phụ đề vào video...", "muted")
                sub_out   = os.path.join(temp_dir, "step_subtitle.mp4")
                base_pct  = (done - 1) / total_steps
                step_span = 1 / total_steps

                def _sub_cb(pct, _bp=base_pct, _sp=step_span):
                    self._upd_progress(_bp + pct * _sp, f"Burn subtitle... {pct*100:.0f}%")

                ok, err = self._burn_subtitle(
                    current, srt_path, sub_out,
                    self.subtitle_font_size.get(), vid_w, vid_h, temp_dir, _sub_cb
                )
                if not ok:
                    self._check_stop()
                    raise Exception(f"Lỗi burn subtitle: {err[:200]}")
                current = sub_out
                self.log("  Phụ đề xong", "success")
                self._upd_progress(done / total_steps, "")
                self._check_stop()

            # Banner
            if use_ban:
                done += 1
                self.log(f"\n[{done}/{total_steps}] Banner...", "info")
                self._upd_progress((done-1)/total_steps, "Chuẩn bị banner...")

                banners = []
                for pos, en, txt, fs, tc, bc, bo in [
                    ("top",    self.banner_top_enabled, self.banner_top_text,
                     self.banner_top_fontsize, self.banner_top_textcolor,
                     self.banner_top_bgcolor,  self.banner_top_bgopacity),
                    ("bottom", self.banner_bot_enabled, self.banner_bot_text,
                     self.banner_bot_fontsize, self.banner_bot_textcolor,
                     self.banner_bot_bgcolor,  self.banner_bot_bgopacity),
                ]:
                    if en.get() and txt.get().strip():
                        banners.append({
                            "position": pos, "text": txt.get(),
                            "fontsize": fs.get(), "textcolor": tc.get(),
                            "bgcolor":  bc.get(), "bgopacity": bo.get(),
                        })

                if not banners:
                    self.log("  Không có banner nào có text → bỏ qua", "muted")
                else:
                    ban_out   = os.path.join(temp_dir, "step_banner.mp4")
                    ban_dur   = get_media_duration(current)
                    font_path = _find_drawtext_font()
                    base_pct  = (done - 1) / total_steps
                    step_span = 1 / total_steps

                    def _ban_cb(pct, _bp=base_pct, _sp=step_span):
                        self._upd_progress(_bp + pct * _sp, f"Render banner... {pct*100:.0f}%")

                    ok, err = apply_text_banners(
                        current, ban_out, banners, ban_dur,
                        vid_fps, BEST_ENCODER, font_path,
                        progress_callback=_ban_cb, stop_event=self._stop_event
                    )
                    if not ok:
                        self._check_stop()
                        raise Exception(f"Lỗi banner: {err[:200]}")
                    current = ban_out
                    self.log("  Banner xong", "success")
                    self._upd_progress(done / total_steps, "")

            # BGM
            if use_bgm:
                done += 1
                self.log(f"\n[{done}/{total_steps}] Nhạc nền (BGM)...", "info")
                self._upd_progress((done - 1) / total_steps, "Áp dụng nhạc nền...")

                _bgm_path  = self.bgm_path.get()
                _bgm_vol   = max(0.0, min(5.0, float(self.bgm_volume.get())))
                _bgm_mode  = self.bgm_mode.get()
                bgm_out    = os.path.join(temp_dir, "step_bgm.mp4")
                _bgm_dur   = get_media_duration(current)
                _bgm_base_pct  = (done - 1) / total_steps
                _bgm_step_span = 1 / total_steps

                if _bgm_mode == "mix":
                    _bgm_filter = (
                        f"[1:a]volume={_bgm_vol:.3f}[bgm];"
                        f"[0:a][bgm]amix=inputs=2:duration=first:normalize=0[aout]"
                    )
                    _bgm_cmd = [
                        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        "-progress", "pipe:1", "-stats_period", "1",
                        "-i", current,
                        "-stream_loop", "-1", "-i", normalize_path(_bgm_path),
                        "-filter_complex", _bgm_filter,
                        "-map", "0:v", "-map", "[aout]",
                        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                        "-movflags", "+faststart", bgm_out
                    ]
                else:
                    _bgm_cmd = [
                        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        "-progress", "pipe:1", "-stats_period", "1",
                        "-i", current,
                        "-stream_loop", "-1", "-i", normalize_path(_bgm_path),
                        "-map", "0:v", "-map", "1:a:0",
                        "-af", f"volume={_bgm_vol:.3f}",
                        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                        "-t", str(_bgm_dur),
                        "-movflags", "+faststart", bgm_out
                    ]

                self._proc = subprocess.Popen(
                    _bgm_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", errors="replace",
                    creationflags=SUBPROCESS_FLAGS
                )
                for line in self._proc.stdout:
                    if self._stop_event.is_set():
                        self._proc.terminate()
                        break
                    if line.startswith("out_time_us=") and _bgm_dur > 0:
                        try:
                            us = int(line.strip().split("=")[1])
                            pct = min(us / 1_000_000 / _bgm_dur, 1.0)
                            self._upd_progress(
                                _bgm_base_pct + pct * _bgm_step_span,
                                f"BGM... {pct*100:.0f}%"
                            )
                        except Exception:
                            pass
                self._proc.wait()
                _bgm_stderr = self._proc.stderr.read()
                _bgm_ok = self._proc.returncode == 0
                self._proc = None
                if not _bgm_ok:
                    self._check_stop()
                    raise Exception(f"Lỗi BGM: {_bgm_stderr[:200]}")
                current = bgm_out
                self.log("  Nhạc nền xong", "success")
                self._upd_progress(done / total_steps, "")
                self._check_stop()

            # Save
            self.log("\nLưu file output...", "info")
            self._upd_progress(0.98, "Lưu file...")
            shutil.copy2(current, output_path)
            file_size = os.path.getsize(output_path) / 1_048_576
            self.log(f"Xong! {output_path}", "success")
            self.log(f"Kích thước: {file_size:.2f} MB", "info")
            self._upd_progress(1.0, f"Xong — {file_size:.1f} MB")
            self.queue_ui(lambda: self.status_label.config(text="Hoàn thành!", fg=SUCCESS))
            self.queue_ui(lambda: self.percent_label.config(text="100%", fg=SUCCESS))

            def _show_done(_od=out_dir, _on=out_name, _fs=file_size):
                dlg = tk.Toplevel(self._root_window)
                dlg.title("Hoàn thành")
                dlg.configure(bg=APP_BG)
                dlg.geometry("380x210")
                dlg.resizable(False, False)
                dlg.grab_set()
                dlg.after(10, lambda: _center_window(dlg, self._root_window))

                body = tk.Frame(dlg, bg=APP_BG)
                body.pack(fill="both", expand=True, padx=24, pady=18)
                tk.Label(body, text="✓  Xử lý video thành công!",
                         bg=APP_BG, fg=SUCCESS,
                         font=(UI_FONT, 13, "bold")).pack(anchor="w", pady=(0, 8))
                tk.Label(body, text=f"File: {_on}\nKích thước: {_fs:.2f} MB",
                         bg=APP_BG, fg=TEXT_SECONDARY,
                         font=(UI_FONT, 9), justify="left").pack(anchor="w")

                btn_row = tk.Frame(dlg, bg=APP_BG)
                btn_row.pack(side="bottom", fill="x", padx=24, pady=(0, 18))

                def _open(d=_od):
                    if IS_MAC:       subprocess.Popen(["open", d])
                    elif IS_WINDOWS: subprocess.Popen(["explorer", d],
                                                      creationflags=SUBPROCESS_FLAGS)
                    else:            subprocess.Popen(["xdg-open", d])
                    dlg.destroy()

                FlatButton(btn_row, "📂  Mở thư mục output", command=_open,
                           height=38, font=(UI_FONT, 10, "bold"), padx=24,
                           bg=BTN_SECONDARY, hover_bg=BTN_SEC_HOVER,
                           pressed_bg=DIVIDER).pack(fill="x", pady=(0, 6))
                FlatButton(btn_row, "Đóng", command=dlg.destroy,
                           height=32, font=(UI_FONT, 9), padx=24,
                           bg=BTN_SECONDARY, hover_bg=BTN_SEC_HOVER,
                           pressed_bg=DIVIDER).pack(fill="x")

            self.queue_ui(_show_done)

        except InterruptedError:
            self.log("\n⏹ Đã dừng", "error")
            self.queue_ui(lambda: self.status_label.config(text="Đã dừng", fg=TEXT_PRIMARY))
            self.queue_ui(lambda: self.detail_label.config(text="Người dùng hủy"))
            self.queue_ui(lambda: self.percent_label.config(text="—", fg=TEXT_MUTED))
        except Exception as e:
            self.log(f"\nLỖI: {e}", "error")
            self.queue_ui(lambda: self.status_label.config(text="Lỗi xảy ra", fg=DANGER))
            self.queue_ui(lambda msg=str(e): self.detail_label.config(text=msg[:80]))
            self.queue_ui(lambda msg=str(e):
                          messagebox.showerror("Lỗi", msg, parent=self._root_window))
        finally:
            stop_timer.set()
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
            self.queue_ui(lambda: self._proc_btn.set_state("normal"))
            self.queue_ui(lambda: self._proc_btn.set_text("▶  Xử lý video"))
            self.queue_ui(lambda: self._stop_btn.set_state("disabled"))

    # ── Video helpers ─────────────────────────────────────────────────────

    def _get_video_info(self, path):
        cmd = ["ffprobe", "-v", "quiet", "-print_format", "json",
               "-show_streams", normalize_path(path)]
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", errors="replace",
                                creationflags=SUBPROCESS_FLAGS)
        try:
            for s in json.loads(result.stdout).get("streams", []):
                if s.get("codec_type") == "video":
                    w, h = s.get("width", 1920), s.get("height", 1080)
                    num, den = (s.get("r_frame_rate", "30/1").split("/") + ["1"])[:2]
                    return w, h, round(int(num) / max(int(den), 1), 3)
        except Exception:
            pass
        return 1920, 1080, 30.0

    def _write_srt_from_whisper(self, result, path):
        segs = result.get("segments", [])
        with open(path, "w", encoding="utf-8") as f:
            for i, s in enumerate(segs, 1):
                f.write(f"{i}\n")
                f.write(f"{format_srt_time(s['start'])} --> {format_srt_time(s['end'])}\n")
                f.write(f"{s['text'].strip()}\n\n")

    def _parse_srt(self, path):
        segments = []
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        for block in content.strip().split("\n\n"):
            lines = block.strip().split("\n")
            if len(lines) < 3:
                continue
            try:
                a, b = lines[1].split(" --> ")
                def _t2s(t):
                    t = t.strip().replace(",", ".")
                    p = t.split(":")
                    return int(p[0]) * 3600 + int(p[1]) * 60 + float(p[2])
                segments.append({"start": _t2s(a), "end": _t2s(b),
                                  "text": "\n".join(lines[2:])})
            except Exception:
                continue
        return segments

    def _write_ass(self, segments, path, font_size, width, height):
        def _fmt(s):
            h, m = int(s // 3600), int((s % 3600) // 60)
            return f"{h}:{m:02}:{int(s%60):02}.{int((s-int(s))*100):02}"
        header = (
            f"[Script Info]\nScriptType: v4.00+\n"
            f"PlayResX: {width}\nPlayResY: {height}\nScaledBorderAndShadow: yes\n\n"
            f"[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, "
            f"SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, "
            f"StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
            f"Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            f"Style: Default,Arial,{font_size},&H00FFFFFF,&H000000FF,"
            f"&H4D000000,&H4D000000,1,0,0,0,100,100,0,0,3,5,0,2,10,10,30,1\n\n"
            f"[Events]\nFormat: Layer, Start, End, Style, Name, "
            f"MarginL, MarginR, MarginV, Effect, Text\n"
        )
        with open(path, "w", encoding="utf-8") as f:
            f.write(header)
            for seg in segments:
                f.write(f"Dialogue: 0,{_fmt(seg['start'])},{_fmt(seg['end'])},"
                        f"Default,,0,0,0,,{seg['text'].replace(chr(10), chr(92)+'N')}\n")

    def _burn_subtitle(self, input_video, srt_path, output_video, font_size,
                       vid_w, vid_h, temp_dir, progress_callback=None):
        ass_path = os.path.join(temp_dir, "subtitle_ed.ass")
        segs = self._parse_srt(srt_path)
        if not segs:
            return False, "File SRT không có phụ đề"
        self._write_ass(segs, ass_path, font_size, vid_w, vid_h)
        ass_esc   = ass_path.replace("\\", "/").replace(":", "\\:")
        enc_args  = get_encoder_args("libx264", 30)
        total_dur = get_media_duration(input_video)
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-progress", "pipe:1", "-stats_period", "1",
            "-i", input_video,
            "-vf", f"subtitles='{ass_esc}'",
            *enc_args, "-pix_fmt", "yuv420p",
            "-c:a", "copy", "-movflags", "+faststart",
            output_video
        ]
        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            creationflags=SUBPROCESS_FLAGS
        )
        for line in self._proc.stdout:
            if self._stop_event.is_set():
                self._proc.terminate()
                break
            if line.startswith("out_time_us=") and total_dur > 0 and progress_callback:
                try:
                    us = int(line.strip().split("=")[1])
                    progress_callback(min(us / 1_000_000 / total_dur, 1.0))
                except Exception:
                    pass
        self._proc.wait()
        stderr_out = self._proc.stderr.read()
        ok = self._proc.returncode == 0
        self._proc = None
        return ok, stderr_out


# =========================================================
# VIDEO EDITOR DIALOG
# =========================================================

class VideoEditorDialog:
    """Popup with TabNotebook — each tab is a VideoEditorTab job."""

    def __init__(self, root):
        self._root = root
        self._tab_count = 0
        self._build()

    def _build(self):
        win = tk.Toplevel(self._root)
        self.win = win
        win.title("✏  Chỉnh sửa Video Nguồn")
        win.configure(bg=APP_BG)
        win.geometry("1160x820")
        win.minsize(960, 640)
        win.resizable(True, True)
        win.protocol("WM_DELETE_WINDOW", win.destroy)
        win.after(10, lambda: _center_window(win, self._root))

        self.notebook = TabNotebook(win, add_command=self._add_tab)
        self.notebook.pack(fill="both", expand=True)
        self._add_tab()

    def _add_tab(self):
        self._tab_count += 1
        tid, content = self.notebook.new_tab(f"Job {self._tab_count}")
        close_cb = lambda t=tid: self.notebook._close(t)
        tab = VideoEditorTab(content, self.win, self._tab_count,
                             close_callback=close_cb)
        tab.pack(fill="both", expand=True)
        self.notebook.set_rename_callback(tid, tab.on_rename)


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
            lightcolor=CARD_HEADER, darkcolor=CARD_HEADER,
            bordercolor=CARD_HEADER, insertcolor=TEXT_PRIMARY,
            selectbackground=CARD_HEADER, selectforeground=TEXT_PRIMARY,
            focuscolor=CARD_HEADER,
            font=(UI_FONT, 10), padding=(4, 3))
        style.map("Modern.TCombobox",
            fieldbackground=[("readonly", CARD_HEADER), ("focus", CARD_HEADER),
                             ("active", CARD_HEADER)],
            background=[("readonly", CARD_HEADER), ("focus", CARD_HEADER),
                        ("active", CARD_HEADER)],
            lightcolor=[("readonly", CARD_HEADER), ("focus", CARD_HEADER),
                        ("active", CARD_HEADER)],
            darkcolor=[("readonly", CARD_HEADER), ("focus", CARD_HEADER),
                       ("active", CARD_HEADER)],
            bordercolor=[("readonly", CARD_HEADER), ("focus", CARD_HEADER),
                         ("active", CARD_HEADER)],
            selectbackground=[("readonly", CARD_HEADER)],
            selectforeground=[("readonly", TEXT_PRIMARY)],
            arrowcolor=[("disabled", TEXT_MUTED), ("readonly", TEXT_SECONDARY)])

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

        # ── Right side phải pack TRƯỚC left ──────────────────────────
        right = tk.Frame(hdr, bg=APP_BG)
        right.pack(side="right", fill="y")

        FlatButton(
            right, "✏  Chỉnh sửa video",
            command=lambda: VideoEditorDialog(self.root),
            bg=BTN_SECONDARY, hover_bg=BTN_SEC_HOVER, pressed_bg=DIVIDER,
            fg=TEXT_PRIMARY,
            width=180, height=38, font=(UI_FONT, 10, "bold"), padx=14
        ).pack(side="left", padx=(0, 8), anchor="center", expand=True)

        FlatButton(
            right, "✂  Cắt Video / Audio",
            command=lambda: MediaCutterDialog(self.root),
            bg=BTN_SECONDARY, hover_bg=BTN_SEC_HOVER, pressed_bg=DIVIDER,
            fg=TEXT_PRIMARY,
            width=190, height=38, font=(UI_FONT, 10, "bold"), padx=14
        ).pack(side="left", anchor="center", expand=True)

        # ── Left side ─────────────────────────────────────────────────
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
        _pill("Demucs ✓" if DEMUCS_AVAILABLE else "Demucs ✗", DEMUCS_AVAILABLE)
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
        self.notebook.set_rename_callback(tid, tab.on_rename)


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
