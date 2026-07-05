from __future__ import annotations
import os
import re
import shutil
import subprocess
import sys
from .errors import PipelineError
_CREATE_NO_WINDOW = 134217728 if os.name == 'nt' else 0
_ffmpeg_path: str | None = None
def find_ffmpeg() -> str:
    global _ffmpeg_path
    if _ffmpeg_path:
        return _ffmpeg_path
    candidates = []
    if getattr(sys, 'frozen', False):
        bundle = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
        candidates.append(os.path.join(bundle, 'ffmpeg.exe'))
        candidates.append(os.path.join(os.path.dirname(sys.executable), 'ffmpeg.exe'))
    for candidate in candidates:
        if os.path.isfile(candidate):
            _ffmpeg_path = candidate
            return candidate
    path = shutil.which('ffmpeg')
    if not path:
        raise PipelineError('FFmpeg was not found on PATH. Install it and restart the app.\nOn Windows:  winget install Gyan.FFmpeg')
    _ffmpeg_path = path
    return path
def extract_audio(input_path: str, out_path: str) -> str:
    ffmpeg = find_ffmpeg()
    cmd = [ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-i', input_path, '-vn', '-sn', '-dn', '-ac', '1', '-ar', '16000', '-c:a', 'flac', out_path]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', creationflags=_CREATE_NO_WINDOW)
    except OSError as exc:
        raise PipelineError(f'Failed to launch FFmpeg: {exc}') from exc
    if proc.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        detail = (proc.stderr or '').strip()[-2000:]
        raise PipelineError(f'FFmpeg could not extract audio from:\n{input_path}\n\n{detail}')
    return out_path
def extract_preview_wav(input_path: str, out_path: str) -> str:
    ffmpeg = find_ffmpeg()
    cmd = [ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-i', input_path, '-vn', '-sn', '-dn', '-ac', '1', '-ar', '44100', '-c:a', 'pcm_s16le', out_path]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', creationflags=_CREATE_NO_WINDOW)
    if proc.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        raise PipelineError(f"Could not prepare preview audio:\n{(proc.stderr or '').strip()[-800:]}")
    return out_path
def probe_duration(input_path: str) -> float | None:
    ffprobe = shutil.which('ffprobe')
    if ffprobe:
        try:
            proc = subprocess.run([ffprobe, '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', input_path], capture_output=True, text=True, encoding='utf-8', errors='replace', creationflags=_CREATE_NO_WINDOW)
            return float(proc.stdout.strip())
        except (OSError, ValueError):
            pass
    try:
        proc = subprocess.run([find_ffmpeg(), '-hide_banner', '-i', input_path], capture_output=True, text=True, encoding='utf-8', errors='replace', creationflags=_CREATE_NO_WINDOW)
        match = re.search('Duration:\\s*(\\d+):(\\d+):(\\d+(?:\\.\\d+)?)', proc.stderr or '')
        if match:
            h, m, s = match.groups()
            return int(h) * 3600 + int(m) * 60 + float(s)
        proc = subprocess.run([find_ffmpeg(), '-hide_banner', '-i', input_path, '-f', 'null', '-'], capture_output=True, text=True, encoding='utf-8', errors='replace', creationflags=_CREATE_NO_WINDOW)
        times = re.findall('time=(\\d+):(\\d+):(\\d+(?:\\.\\d+)?)', proc.stderr or '')
        if times:
            h, m, s = times[-1]
            return int(h) * 3600 + int(m) * 60 + float(s)
    except OSError:
        pass
    return None
