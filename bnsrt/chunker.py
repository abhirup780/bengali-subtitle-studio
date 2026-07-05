from __future__ import annotations
import os
import re
import subprocess
from dataclasses import dataclass
from .errors import PipelineError
from .ffmpeg import _CREATE_NO_WINDOW, find_ffmpeg
SILENCE_PASSES = [('-35dB', 0.3, 3.0), ('-30dB', 0.2, 2.0), ('-25dB', 0.12, 1.0)]
MIN_CHUNK = 1.2
MAX_CHUNK = 6.0
HARD_MAX = 8.5
_SILENCE_RE = re.compile('silence_(start|end):\\s*([0-9.]+)(?:\\s*\\|\\s*silence_duration:\\s*([0-9.]+))?')
@dataclass
class Chunk:
    start: float
    end: float
    speech_start: float
    speech_end: float
@dataclass
class _Pause:
    start: float
    end: float
    weight: float
    @property
    def mid(self) -> float:
        return (self.start + self.end) / 2
def detect_silences(audio_path: str, noise: str, min_dur: float) -> list[tuple[float, float]]:
    cmd = [find_ffmpeg(), '-hide_banner', '-nostats', '-i', audio_path, '-af', f'silencedetect=noise={noise}:d={min_dur}', '-f', 'null', '-']
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', creationflags=_CREATE_NO_WINDOW)
    except OSError as exc:
        raise PipelineError(f'Failed to run silence detection: {exc}') from exc
    intervals: list[tuple[float, float]] = []
    pending_start: float | None = None
    for match in _SILENCE_RE.finditer(proc.stderr or ''):
        kind, value = (match.group(1), float(match.group(2)))
        if kind == 'start':
            pending_start = value
        elif pending_start is not None:
            intervals.append((pending_start, value))
            pending_start = None
    if pending_start is not None:
        intervals.append((pending_start, float('inf')))
    return intervals
def detect_pauses(audio_path: str) -> list[_Pause]:
    pauses: list[_Pause] = []
    for noise, min_dur, weight in SILENCE_PASSES:
        for start, end in detect_silences(audio_path, noise, min_dur):
            pauses.append(_Pause(start, end, weight))
    pauses.sort(key=lambda p: p.mid)
    return pauses
def plan_chunks(duration: float, pauses: list[_Pause]) -> list[Chunk]:
    finite = [p for p in pauses if p.end != float('inf')]
    cuts: list[_Pause] = []
    pos = 0.0
    while duration - pos > MAX_CHUNK:
        window = [p for p in finite if pos + MIN_CHUNK <= p.mid <= pos + MAX_CHUNK]
        if not window:
            window = [p for p in finite if pos + MIN_CHUNK <= p.mid <= pos + HARD_MAX]
        if window:
            best = max(window, key=lambda p: (p.weight, p.end - p.start, p.mid))
            cuts.append(best)
            pos = best.mid
        else:
            cut = pos + MAX_CHUNK
            cuts.append(_Pause(cut, cut, 0.0))
            pos = cut
    bounds = [0.0] + [c.mid for c in cuts] + [duration]
    chunks = []
    for a, b in zip(bounds, bounds[1:]):
        if b - a < 0.05:
            continue
        chunks.append(Chunk(start=a, end=b, speech_start=_trim_lead(a, b, pauses), speech_end=_trim_tail(a, b, pauses)))
    return chunks
def _trim_lead(a: float, b: float, pauses: list[_Pause]) -> float:
    best = a
    for p in pauses:
        if p.start <= a < p.end:
            best = max(best, min(p.end, b - 0.05))
    return best
def _trim_tail(a: float, b: float, pauses: list[_Pause]) -> float:
    best = b
    for p in pauses:
        if p.start < b <= p.end or (p.start < b and p.end == float('inf')):
            best = min(best, max(p.start, a + 0.05))
    return best
def extract_chunk(master_path: str, chunk: Chunk, out_path: str) -> str:
    cmd = [find_ffmpeg(), '-y', '-hide_banner', '-loglevel', 'error', '-ss', f'{chunk.start:.3f}', '-i', master_path, '-t', f'{chunk.end - chunk.start:.3f}', '-ac', '1', '-ar', '16000', '-c:a', 'flac', out_path]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', creationflags=_CREATE_NO_WINDOW)
    if proc.returncode != 0 or not os.path.exists(out_path):
        raise PipelineError(f"FFmpeg failed to cut chunk {chunk.start:.2f}-{chunk.end:.2f}s:\n{(proc.stderr or '').strip()[-800:]}")
    return out_path
