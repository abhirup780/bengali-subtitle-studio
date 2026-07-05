from __future__ import annotations
import re
from dataclasses import dataclass, field, replace
@dataclass
class Word:
    text: str
    start: float
    end: float
@dataclass
class Cue:
    start: float
    end: float
    lines: list[str] = field(default_factory=list)
    @property
    def text(self) -> str:
        return '\n'.join(self.lines)
    def with_text(self, text: str, max_line_chars: int=42) -> 'Cue':
        return replace(self, lines=wrap_two_lines(text, max_line_chars))
def format_timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f'{h:02d}:{m:02d}:{s:02d},{ms:03d}'
_NO_BREAK_AFTER = {'a', 'an', 'the', 'of', 'to', 'in', 'on', 'at', 'for', 'with', 'and', 'but', 'or', 'nor', 'as', 'by', 'from', 'into', 'onto', 'over', 'under', 'is', 'are', 'was', 'were', 'be', 'been', 'am', 'his', 'her', 'their', 'its', 'our', 'your', 'my', 'this', 'that', 'these', 'those', 'very', 'এবং', 'আর', 'কিন্তু', 'বা', 'অথবা', 'যে', 'যা', 'এই', 'ওই', 'সেই', 'এক', 'একটা', 'একটি', 'কোনো', 'কোন', 'খুব', 'আমার', 'তোমার', 'তার', 'আমাদের', 'তাদের', 'এর', 'ও', 'না', 'নয়'}
_LINE_END_PUNCT = ('।', '?', '!', ',', ';', ':', '…')
def wrap_two_lines(text: str, max_line_chars: int=42) -> list[str]:
    text = ' '.join(text.split())
    if not text:
        return []
    if text.startswith('-'):
        second = text.find(' - ', 1)
        if second > 0:
            return [text[:second].strip(), text[second + 1:].strip()]
    if len(text) <= max_line_chars:
        return [text]
    words = text.split(' ')
    best: tuple[float, list[str]] | None = None
    for i in range(1, len(words)):
        line1 = ' '.join(words[:i])
        line2 = ' '.join(words[i:])
        longest = max(len(line1), len(line2))
        score = max(0, longest - max_line_chars) * 100.0
        score += abs(len(line1) - len(line2))
        score += 0 if len(line1) >= len(line2) else 1
        last = words[i - 1]
        first_next = words[i]
        if last.lower().strip('\'"') in _NO_BREAK_AFTER:
            score += 14
        if last[-1].isdigit() and len(first_next) <= 6:
            score += 14
        if last.endswith(_LINE_END_PUNCT):
            score -= 6
        if last.endswith('-'):
            score += 20
        if best is None or score < best[0]:
            best = (score, [line1, line2])
    assert best is not None
    return best[1]
_TS_LINE = re.compile('(\\d+):(\\d{2}):(\\d{2})[,.](\\d{1,3})\\s*-->\\s*(\\d+):(\\d{2}):(\\d{2})[,.](\\d{1,3})')
def parse_srt_with_lines(text: str) -> list[tuple[Cue, int, int]]:
    lines = text.split('\n')
    entries: list[tuple[Cue, int, int]] = []
    i = 0
    while i < len(lines):
        match = _TS_LINE.search(lines[i])
        if not match:
            i += 1
            continue
        g = match.groups()
        start = int(g[0]) * 3600 + int(g[1]) * 60 + int(g[2]) + int(g[3].ljust(3, '0')) / 1000
        end = int(g[4]) * 3600 + int(g[5]) * 60 + int(g[6]) + int(g[7].ljust(3, '0')) / 1000
        j = i + 1
        cue_lines = []
        while j < len(lines) and lines[j].strip():
            cue_lines.append(lines[j])
            j += 1
        if cue_lines and end > start:
            entries.append((Cue(start=start, end=end, lines=cue_lines), i + 2, j))
        i = j + 1
    return entries
def render_srt(cues: list[Cue]) -> str:
    blocks = []
    for i, cue in enumerate(cues, start=1):
        blocks.append(f'{i}\n{format_timestamp(cue.start)} --> {format_timestamp(cue.end)}\n{cue.text}')
    return '\n\n'.join(blocks) + '\n'
def write_srt(cues: list[Cue], path: str) -> None:
    with open(path, 'w', encoding='utf-8-sig', newline='\n') as f:
        f.write(render_srt(cues))
