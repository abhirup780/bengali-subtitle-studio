from __future__ import annotations
import os
import re
from dataclasses import dataclass
MEDIA_EXTS = ['.mp3', '.wav', '.m4a', '.mp4', '.mkv', '.mov', '.flac', '.ogg', '.aac', '.webm']
_SRT_RE = re.compile('^(?P<stem>.+)_(?P<lang>bn|en)(?:_v(?P<v>\\d+))?\\.srt$', re.IGNORECASE)
@dataclass
class PreviewSet:
    audio: str | None = None
    bn: str | None = None
    en: str | None = None
    @property
    def has_subtitles(self) -> bool:
        return bool(self.bn or self.en)
def find_pair(selected_path: str) -> PreviewSet:
    folder = os.path.dirname(os.path.abspath(selected_path))
    name = os.path.basename(selected_path)
    ext = os.path.splitext(name)[1].lower()
    if ext == '.srt':
        match = _SRT_RE.match(name)
        stem = match.group('stem') if match else os.path.splitext(name)[0]
    else:
        stem = os.path.splitext(name)[0]
    result = PreviewSet(bn=_latest_srt(folder, stem, 'bn'), en=_latest_srt(folder, stem, 'en'))
    if ext == '.srt':
        result.audio = _find_media(folder, stem)
        if not result.has_subtitles:
            result.bn = selected_path
    else:
        result.audio = selected_path
    return result
def _latest_srt(folder: str, stem: str, lang: str) -> str | None:
    best: tuple[int, str] | None = None
    try:
        names = os.listdir(folder)
    except OSError:
        return None
    for name in names:
        match = _SRT_RE.match(name)
        if not match:
            continue
        if match.group('stem').lower() != stem.lower() or match.group('lang').lower() != lang:
            continue
        version = int(match.group('v') or 1)
        if best is None or version > best[0]:
            best = (version, os.path.join(folder, name))
    return best[1] if best else None
def _find_media(folder: str, stem: str) -> str | None:
    for ext in MEDIA_EXTS:
        candidate = os.path.join(folder, stem + ext)
        if os.path.exists(candidate):
            return candidate
    return None
