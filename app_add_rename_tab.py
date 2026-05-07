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

from tkinter import ttk, filedialog, messagebox, colorchooser
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

def process_segment_sub_pip(args):
    """
    Top-level (picklable) function: burn subtitle (nếu có) rồi apply PiP (nếu có)
    lên 1 segment. Dùng bởi ProcessPoolExecutor.
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

    # Pass 1: burn subtitle
    if ass_p is not None:
        sub_out = out_p.replace(".mp4", "_sub.mp4")
        ok, _ = burn_subtitle_segment_task(
            (pos_i, cur, ass_p, sub_out, o_fps, o_enc)
        )
        if ok and os.path.exists(sub_out) and os.path.getsize(sub_out) > 0:
            cur = sub_out

    # Pass 2: apply PiP
    if o_paths:
        _, ok, _, err = apply_pip_to_segment(
            (pos_i, cur, o_paths, o_pos, o_sizes, out_p, o_fps, o_enc)
        )
        return (pos_i, ok, out_p, err)

    # Chỉ subtitle, không PiP: copy cur → out_p
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
                        progress_callback=None):
    """
    Vẽ banner top/bottom lên video bằng drawbox + drawtext filter.
    banners: list of dict {position, text, fontsize, textcolor, bgcolor, bgopacity}
    """
    active = [b for b in banners if b.get("text", "").strip()]
    if not active:
        return True, ""

    filter_parts = []
    for b in active:
        fontsize  = max(8, int(b.get("fontsize", 48)))
        bh        = int(fontsize * 2.0)          # banner height = 2× font
        textcolor = b.get("textcolor", "#FFFFFF").replace("#", "0x")
        bgcolor   = b.get("bgcolor",   "#000000").replace("#", "0x")
        bgopa     = max(0.0, min(1.0, float(b.get("bgopacity", 0.7))))
        pos       = b.get("position", "top")
        text_esc  = _esc_drawtext(b["text"].strip())

        if pos == "top":
            box_y  = 0
            text_y = f"({bh}-text_h)/2"
        else:
            box_y  = f"ih-{bh}"
            text_y = f"H-{bh}+({bh}-text_h)/2"

        # Nền banner full-width — drawbox dùng iw/ih (không phải W/H)
        filter_parts.append(
            f"drawbox=x=0:y={box_y}:w=iw:h={bh}:color={bgcolor}@{bgopa:.2f}:t=fill"
        )

        # Chữ căn giữa
        if font_path:
            fp_esc = font_path.replace("\\", "/").replace(":", "\\:")
            font_opt = f":fontfile='{fp_esc}'"
        else:
            font_opt = ""

        filter_parts.append(
            f"drawtext=text='{text_esc}':fontsize={fontsize}"
            f":fontcolor={textcolor}{font_opt}"
            f":x=(W-text_w)/2:y={text_y}"
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
        text=True, creationflags=SUBPROCESS_FLAGS
    )
    for line in proc.stdout:
        if line.startswith("out_time_us=") and main_duration > 0 and progress_callback:
            try:
                us = int(line.strip().split("=")[1])
                progress_callback(min(us / 1_000_000 / main_duration, 1.0))
            except Exception:
                pass
    proc.wait()
    return proc.returncode == 0, proc.stderr.read()


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
        output_path
    ]
    result = subprocess.run(
        command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE, text=True, creationflags=SUBPROCESS_FLAGS
    )
    return (idx, result.returncode == 0, output_path, result.stderr)


def apply_pip_overlays(input_video, overlay_paths, overlay_positions,
                        overlay_size_pcts, output_video, main_duration, fps,
                        best_encoder, progress_callback=None, log_callback=None):
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
        text=True,
        creationflags=SUBPROCESS_FLAGS
    )

    for line in proc.stdout:
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
            text=True, creationflags=SUBPROCESS_FLAGS
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
        text=True, creationflags=SUBPROCESS_FLAGS
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
            "-t", str(duration), "-shortest",
            "-fflags", "+genpts", "-avoid_negative_ts", "make_zero",
            output_path
        ]
        result = subprocess.run(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, creationflags=SUBPROCESS_FLAGS
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
        "-t", str(duration), "-shortest",
        "-fflags", "+genpts", "-avoid_negative_ts", "make_zero",
        output_path
    ]
    result = subprocess.run(
        command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        text=True, creationflags=SUBPROCESS_FLAGS
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
    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", str(clip_start),
        "-t", str(clip_sec),
        "-i", source_video,
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
        text=True,
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
        text=True, creationflags=SUBPROCESS_FLAGS
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
        text=True,
        creationflags=SUBPROCESS_FLAGS
    )
    return (index, result.returncode, output_path, result.stderr)


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
        """Duyệt đệ quy, áp border ACCENT khi focus cho mọi tk.Entry và ttk.Combobox."""
        for w in root.winfo_children():
            if type(w) is tk.Entry:
                try:
                    parent_bg = w.master.cget("bg")
                except Exception:
                    parent_bg = CARD_HEADER
                w.configure(
                    highlightthickness=2,
                    highlightbackground=parent_bg,
                    highlightcolor=ACCENT,
                )
            elif isinstance(w, ttk.Combobox):
                frame = w.master
                def _in(e, f=frame):  f.configure(bg=ACCENT)
                def _out(e, f=frame): f.configure(bg=BORDER_COLOR)
                w.bind("<FocusIn>",  _in,  add="+")
                w.bind("<FocusOut>", _out, add="+")

                try:
                    frame.configure(bg=BORDER_COLOR)
                except Exception:
                    pass
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
        
        # === Toggle chọn nguồn hình ===
        clip_toggle_wrapper = tk.Frame(parent, bg=APP_BG)
        clip_toggle_wrapper.pack(fill="x", pady=(0, 6), padx=(0, 4))

        ToggleSwitch(
            clip_toggle_wrapper,
            "⬡  Dùng video động thay vì thư mục ảnh",
            self.use_video_clips,
            command=self._toggle_clip_mode
        ).pack(anchor="w")

        # === Video Clip Source Card (ẩn mặc định) ===
        self.clip_source_card = Card(parent, "⬡  Video nguồn clip động")

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
        self.images_card = Card(parent, "⊞  Thư mục ảnh")
        self.images_card.pack(fill="x", pady=(0, 12), padx=(0, 4))

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

        def _toggle_crawl():
            if self.show_crawl.get():
                crawl_opts.pack(fill="x")
            else:
                crawl_opts.pack_forget()

        ToggleSwitch(
            self.images_card.body, "⬇  Tải ảnh theo từ khoá",
            self.show_crawl,
            command=_toggle_crawl
        ).pack(anchor="w", fill="x", pady=(0, 4))

        # --- crawl_opts content (ẩn mặc định) ---
        kw_row = tk.Frame(crawl_opts, bg=CARD_HEADER)
        kw_row.pack(fill="x", pady=(0, 6))
        tk.Label(kw_row, text="Từ khoá", bg=CARD_HEADER, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 9)).pack(side="left", padx=(12, 6), pady=8)
        tk.Entry(kw_row, textvariable=self.crawl_keyword,
                 bg=INPUT_BG, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
                 font=(UI_FONT, 10), relief="flat", borderwidth=0
                 ).pack(side="left", fill="x", expand=True, padx=(0, 8), pady=3)

        engine_row = tk.Frame(crawl_opts, bg=CARD_BG)
        engine_row.pack(fill="x", pady=(0, 6))

        tk.Label(engine_row, text="Nguồn tìm", bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 9)).pack(side="left", padx=(8, 4))
        ttk.Combobox(engine_row, textvariable=self.crawl_engine,
                     values=["Bing", "Pinterest", "Flickr"],
                     state="readonly", font=(UI_FONT, 9), width=9,
                     style="Modern.TCombobox").pack(side="left")

        opts_row = tk.Frame(crawl_opts, bg=CARD_BG)
        opts_row.pack(fill="x", pady=(0, 6))

        tk.Label(opts_row, text="Loại ảnh", bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 9)).pack(side="left", padx=(8, 4))
        ttk.Combobox(opts_row, textvariable=self.crawl_layout,
                     values=["Ngang", "Dọc", "Vuông", "Tất cả"],
                     state="readonly", font=(UI_FONT, 9), width=7,
                     style="Modern.TCombobox").pack(side="left", padx=(0, 14))

        tk.Label(opts_row, text="Số ảnh", bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 9)).pack(side="left", padx=(0, 4))
        f1 = tk.Frame(opts_row, bg=CARD_HEADER)
        f1.pack(side="left", padx=(0, 14))
        tk.Entry(f1, textvariable=self.crawl_max_num, width=5,
                 bg=CARD_HEADER, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
                 font=(UI_FONT, 10), relief="flat", borderwidth=0,
                 justify="center").pack(padx=6, ipady=4)

        tk.Label(opts_row, text="Min px", bg=CARD_BG, fg=TEXT_SECONDARY,
                 font=(UI_FONT, 9)).pack(side="left", padx=(0, 4))
        f2 = tk.Frame(opts_row, bg=CARD_HEADER)
        f2.pack(side="left")
        tk.Entry(f2, textvariable=self.crawl_min_width, width=6,
                 bg=CARD_HEADER, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
                 font=(UI_FONT, 10), relief="flat", borderwidth=0,
                 justify="center").pack(padx=6, ipady=4)

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
        params_grid.pack(fill="x")
        params_grid.grid_columnconfigure(0, weight=1)
        params_grid.grid_columnconfigure(1, weight=1)
        params_grid.grid_columnconfigure(2, weight=1)

        self._make_number_input(params_grid, "Độ dài đoạn (s)", self.segment_seconds, 0, 0)
        self._make_number_input(params_grid, "Số luồng", self.max_workers, 0, 1)
        self._make_number_input(params_grid, "Hiệu ứng (s)", self.transition_duration, 0, 2, is_float=True)
        
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
                 justify="center", width=5).pack(padx=6, ipady=4)

        # Clip mode options (ẩn mặc định, hiện khi bật video clip mode)
        self.clip_options_section = tk.Frame(options_card.body, bg=CARD_BG)

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

    def _start_crawl(self):
        threading.Thread(target=self._download_images, daemon=True).start()

    def _download_images(self):
        engine = self.crawl_engine.get()

        if engine == "Bing":
            try:
                from icrawler.builtin import BingImageCrawler as _chk  # noqa: F401
            except ImportError:
                self.log("Thiếu thư viện: pip install icrawler", "error")
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
                from icrawler.builtin import BingImageCrawler
                crawler = BingImageCrawler(
                    storage={"root_dir": save_folder},
                    feeder_threads=1, parser_threads=1, downloader_threads=4
                )
                filters = {"size": "large"}
                if layout != "any":
                    filters["layout"] = layout
                crawler.crawl(keyword=keyword, filters=filters, max_num=max_num)

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
            self.clip_source_card.pack(fill="x", pady=(0, 12), padx=(0, 4),
                                       before=self.config_card)
            self.clip_options_section.pack(fill="x", pady=(0, 4))
        else:
            self.clip_source_card.pack_forget()
            self.images_card.pack(fill="x", pady=(0, 12), padx=(0, 4),
                                  before=self.config_card)
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
            self.log(f"  Load Whisper '{model_name}' để phân tích nội dung...", "muted")
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
            n_audio = sum(1 for p in self.video_paths if VideoTab._is_audio_file(p))
            n_video = len(self.video_paths) - n_audio
            self.log(f"Nguồn: {n_video} video, {n_audio} audio")
            self.log(f"Hiệu ứng: {'Có (fade ' + str(transition_dur) + 's)' if use_transition else 'Không'}")
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
                self.log(f"[2/{total_steps}] {len(audio_tasks)} đoạn audio + {len(clip_video_infos)} video clip nguồn ({time.time()-t0:.1f}s)", "success")
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
                        if returncode == 0 and os.path.exists(audio_path) and os.path.getsize(audio_path) > 0:
                            audio_info[idx] = (audio_path, actual_duration)

                    completed += 1
                    pct = 30 + (completed / total_assets) * 15
                    label3 = "audio" if use_video_clips else "audio + ảnh"
                    self.update_status(
                        f"Bước {current_step}/{total_steps}: Xử lý {label3}",
                        f"Đã xử lý {completed}/{total_assets}", pct
                    )

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

            else:
                # Image mode — giữ nguyên
                render_tasks = []
                render_func_to_use = render_segment_with_transition if use_transition else render_segment_fast
                for new_idx, (orig_idx, audio_path, duration) in enumerate(final_audio_order):
                    image_video = random.choice(valid_image_videos)
                    if use_transition:
                        render_tasks.append((new_idx, image_video, audio_path, duration, fps,
                                             transition_dur, self.TEMP_FOLDER))
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
                        completed += 1
                        pct = 75 + (completed / total) * 10
                        self.update_status(
                            f"Bước {current_step}/{total_steps}: Ghép video",
                            f"Đã ghép {completed}/{total} đoạn", pct
                        )

            total_segs = len(final_audio_order)
            self.log(f"[{current_step}/{total_steps}] Ghép {len(results)}/{total_segs} đoạn ({time.time()-t0:.1f}s)", "success")
            
            if not results:
                raise ValueError("Không ghép được video")
            
            # STEP 6 (or 7): Burn subtitle per-segment (song song) → Concat → PiP
            t0 = time.time()
            current_step = 6 + step_offset
            sorted_results = [results[idx] for idx in sorted(results.keys())]

            use_pip_now = self.use_pip.get() and bool(self.pip_items)

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
                final_srt = self.OUTPUT_VIDEO.replace(".mp4", ".srt")
                shutil.copy(srt_path, final_srt)
                self.log(f"  Đã lưu file SRT: {final_srt}", "info")

                seg_cumul = 0.0
                burn_tasks = []
                for pos_i, (orig_idx, _ap, seg_dur) in enumerate(final_audio_order):
                    seg_start = seg_cumul
                    seg_end   = seg_cumul + seg_dur

                    # Dùng dict lookup — tránh IndexError khi một số segment render thất bại
                    seg_video = results.get(pos_i)
                    if seg_video is None:
                        seg_cumul += seg_dur
                        continue

                    seg_subs  = [
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
                    burn_tasks.append((pos_i, seg_video, ass_p, burned_path, fps, BEST_ENCODER))
                    seg_cumul += seg_dur

                self.update_status(f"Bước {current_step}/{total_steps}: Burn phụ đề song song",
                                   f"Đang burn 0/{len(burn_tasks)} đoạn...", 86)
                burned_results = {}
                with ProcessPoolExecutor(max_workers=max_workers) as ex:
                    bfuts = {ex.submit(burn_subtitle_segment_task, t): t[0] for t in burn_tasks}
                    done_b = 0
                    for future in as_completed(bfuts):
                        bi, ok, bpath, _ = future.result()
                        if ok and os.path.exists(bpath) and os.path.getsize(bpath) > 0:
                            burned_results[bi] = bpath
                        done_b += 1
                        pct = 86 + (done_b / len(burn_tasks)) * 8
                        self.update_status(
                            f"Bước {current_step}/{total_steps}: Burn phụ đề song song",
                            f"Đã burn {done_b}/{len(burn_tasks)} đoạn", pct
                        )
                self.log(
                    f"[{current_step}/{total_steps}] Burn {len(burned_results)}/{len(burn_tasks)} "
                    f"segment ({time.time()-t0:.1f}s)", "success"
                )
                pre_concat = [burned_results[i] for i in sorted(burned_results.keys())]
            else:
                pre_concat = sorted_results

            # ── 6b: Concat → intermediate (nếu có PiP) hoặc OUTPUT_VIDEO ──
            t0 = time.time()
            concat_target = (
                os.path.join(self.TEMP_FOLDER, "pre_pip.mp4")
                if use_pip_now else self.OUTPUT_VIDEO
            )
            step_label_concat = "Ghép cuối" if not use_pip_now else "Ghép trước PiP"
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
                stderr=subprocess.PIPE, text=True, creationflags=SUBPROCESS_FLAGS
            )
            if result.returncode != 0:
                raise Exception(f"Lỗi concat: {result.stderr}")
            self.log(f"[{current_step}/{total_steps}] Concat xong ({time.time()-t0:.1f}s)", "success")

            # ── 6c: Apply PiP lên video đã concat (single pass, full bandwidth) ──
            if use_pip_now:
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

                    def _pip_cb(pct):
                        self.update_status(
                            f"Bước {current_step}/{total_steps}: PiP Overlay",
                            f"Render overlay... {pct*100:.0f}%",
                            95 + pct * 4
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
            fieldbackground=INPUT_BG, background=INPUT_BG,
            foreground=TEXT_PRIMARY, arrowcolor=TEXT_SECONDARY,
            borderwidth=0, relief="flat",
            lightcolor=INPUT_BG, darkcolor=INPUT_BG,
            bordercolor=INPUT_BG, insertcolor=TEXT_PRIMARY,
            font=(UI_FONT, 10), padding=(6, 3))
        style.map("Modern.TCombobox",
            fieldbackground=[("readonly", INPUT_BG), ("focus", INPUT_BG)],
            background=[("readonly", INPUT_BG), ("focus", INPUT_BG)],
            lightcolor=[("readonly", INPUT_BG), ("focus", INPUT_BG)],
            darkcolor=[("readonly", INPUT_BG), ("focus", INPUT_BG)],
            bordercolor=[("readonly", INPUT_BG), ("focus", INPUT_BG)],
            selectbackground=[("readonly", INPUT_BG)],
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
