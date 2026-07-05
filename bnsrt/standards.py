from __future__ import annotations
import math
from .srt import Cue, wrap_two_lines
MAX_LINE_CHARS = 42
MAX_CUE_CHARS = MAX_LINE_CHARS * 2
MAX_CPS = 17.0
MAX_CPS_EN = 20.0
MIN_DURATION = 0.833
MAX_DURATION = 7.0
MIN_GAP = 2 / 24
MERGE_MAX_GAP = 0.3
PUNCTUATION = ('।', '?', '!', '…', ',', ';', ':', '—')
def enforce(cues: list[Cue]) -> list[Cue]:
    cues = _merge_short(cues)
    cues = _split_long(cues)
    cues = _prefer_single_lines(cues)
    _extend_for_reading_speed(cues)
    for cue in cues:
        cue.lines = wrap_two_lines(' '.join(cue.text.split()), MAX_LINE_CHARS)
    return cues
def rewrap(cues: list[Cue]) -> list[Cue]:
    for cue in cues:
        cue.lines = wrap_two_lines(' '.join(cue.text.split()), MAX_LINE_CHARS)
    return cues
def _merge_short(cues: list[Cue]) -> list[Cue]:
    out: list[Cue] = []
    for cue in cues:
        prev = out[-1] if out else None
        if prev is not None and (cue.end - cue.start < MIN_DURATION or prev.end - prev.start < MIN_DURATION) and (cue.start - prev.end <= MERGE_MAX_GAP) and (_chars(prev.text) + 1 + _chars(cue.text) <= MAX_CUE_CHARS) and (cue.end - prev.start <= MAX_DURATION):
            prev.lines = [f"{' '.join(prev.text.split())} {' '.join(cue.text.split())}"]
            prev.end = cue.end
        else:
            out.append(cue)
    return out
def _split_long(cues: list[Cue]) -> list[Cue]:
    out: list[Cue] = []
    for cue in cues:
        out.extend(_split_cue(cue))
    return out
def _split_cue(cue: Cue) -> list[Cue]:
    text = ' '.join(cue.text.split())
    duration = cue.end - cue.start
    if _chars(text) <= MAX_CUE_CHARS and duration <= MAX_DURATION:
        return [cue]
    words = text.split(' ')
    parts_needed = max(math.ceil(_chars(text) / MAX_CUE_CHARS), math.ceil(duration / MAX_DURATION))
    if len(words) < 2:
        return [cue]
    groups = _split_words(words, min(parts_needed, len(words)))
    while any((_chars(' '.join(g)) > MAX_CUE_CHARS for g in groups)) and len(groups) < len(words):
        parts_needed += 1
        groups = _split_words(words, parts_needed)
    total_chars = sum((_chars(' '.join(g)) for g in groups))
    duration = cue.end - cue.start
    result: list[Cue] = []
    pos = cue.start
    consumed = 0
    for i, group in enumerate(groups):
        consumed += _chars(' '.join(group))
        end = cue.end if i == len(groups) - 1 else cue.start + duration * consumed / total_chars
        result.append(Cue(start=pos, end=end, lines=[' '.join(group)]))
        pos = end
    return result
def _prefer_single_lines(cues: list[Cue]) -> list[Cue]:
    out: list[Cue] = []
    for cue in cues:
        text = ' '.join(cue.text.split())
        if _chars(text) <= MAX_LINE_CHARS or text.startswith('-'):
            out.append(cue)
            continue
        if _chars(text) / (cue.end - cue.start) > 16.0:
            out.append(cue)
            continue
        groups = _split_words(text.split(' '), 2)
        if len(groups) != 2 or any((_chars(' '.join(g)) > MAX_LINE_CHARS for g in groups)):
            out.append(cue)
            continue
        c1, c2 = (_chars(' '.join(g)) for g in groups)
        duration = cue.end - cue.start
        mid = cue.start + duration * c1 / (c1 + c2)
        if mid - cue.start < MIN_DURATION or cue.end - mid < MIN_DURATION:
            out.append(cue)
            continue
        out.append(Cue(start=cue.start, end=mid, lines=[' '.join(groups[0])]))
        out.append(Cue(start=mid, end=cue.end, lines=[' '.join(groups[1])]))
    return out
def _split_words(words: list[str], parts: int) -> list[list[str]]:
    if parts <= 1:
        return [words]
    cumulative = []
    total = 0
    for w in words:
        total += len(w) + 1
        cumulative.append(total)
    breaks: list[int] = []
    for k in range(1, parts):
        target = total * k / parts
        lo = breaks[-1] + 1 if breaks else 1
        candidates = range(lo, len(words))
        if not candidates:
            break
        best = min(candidates, key=lambda i: abs(cumulative[i - 1] - target))
        window = [i for i in candidates if abs(cumulative[i - 1] - target) <= total / parts * 0.35 and words[i - 1].endswith(PUNCTUATION)]
        if window:
            best = min(window, key=lambda i: abs(cumulative[i - 1] - target))
        breaks.append(best)
    bounds = [0] + breaks + [len(words)]
    return [words[a:b] for a, b in zip(bounds, bounds[1:]) if b > a]
def _extend_for_reading_speed(cues: list[Cue]) -> None:
    for i, cue in enumerate(cues):
        _extend_one(cues, i, _chars(cue.text) / MAX_CPS, (cue,))
def extend_pair(bn_cues: list[Cue], en_cues: list[Cue]) -> None:
    for i, (bn, en) in enumerate(zip(bn_cues, en_cues)):
        needed = max(_chars(bn.text) / MAX_CPS, _chars(en.text) / MAX_CPS_EN)
        _extend_one(bn_cues, i, needed, (bn, en))
def _extend_one(cues: list[Cue], i: int, needed: float, targets: tuple[Cue, ...]) -> None:
    cue = cues[i]
    if needed <= cue.end - cue.start:
        return
    new_end = cue.start + min(needed, MAX_DURATION)
    if i + 1 < len(cues):
        limit = cues[i + 1].start
    else:
        limit = cue.end + 1.5
    new_end = min(new_end, limit)
    if i + 1 < len(cues) and 0 < cues[i + 1].start - new_end < MIN_GAP:
        new_end = cues[i + 1].start
    if new_end > cue.end:
        for target in targets:
            target.end = new_end
def _chars(text: str) -> int:
    return len(' '.join(text.split()))
