from __future__ import annotations
from .srt import Cue, Word, wrap_two_lines
SENTENCE_END = ('।', '?', '!', '…')
CLAUSE_END = (',', ';', ':', '—')
MAX_LINE_CHARS = 42
MAX_CUE_CHARS = MAX_LINE_CHARS * 2
MAX_CUE_DURATION = 6.0
PAUSE_SPLIT = 0.6
HARD_PAUSE_SPLIT = 1.2
MIN_CHARS_BEFORE_PAUSE_SPLIT = 12
def words_to_cues(words: list[Word]) -> list[Cue]:
    words = [w for w in words if w.text.strip()]
    cues: list[Cue] = []
    current: list[Word] = []
    def current_chars() -> int:
        return sum((len(w.text) for w in current)) + max(0, len(current) - 1)
    def flush() -> None:
        if not current:
            return
        text = ' '.join((w.text for w in current))
        cues.append(Cue(start=current[0].start, end=current[-1].end, lines=wrap_two_lines(text, MAX_LINE_CHARS)))
        current.clear()
    for word in words:
        if current:
            gap = word.start - current[-1].end
            duration_if_added = word.end - current[0].start
            chars_if_added = current_chars() + 1 + len(word.text)
            prev_text = current[-1].text
            sentence_done = prev_text.endswith(SENTENCE_END)
            clause_done = prev_text.endswith(CLAUSE_END)
            should_split = chars_if_added > MAX_CUE_CHARS or duration_if_added > MAX_CUE_DURATION or gap >= HARD_PAUSE_SPLIT or (gap >= PAUSE_SPLIT and current_chars() >= MIN_CHARS_BEFORE_PAUSE_SPLIT) or (sentence_done and current_chars() >= 20) or (clause_done and chars_if_added > MAX_CUE_CHARS * 0.8)
            if should_split:
                flush()
        current.append(word)
    flush()
    return _resolve_overlaps(cues)
def segments_to_cues(segments: list[tuple[float, float, str]]) -> list[Cue]:
    cues = [Cue(start=start, end=end, lines=wrap_two_lines(text, MAX_LINE_CHARS)) for start, end, text in segments if text.strip()]
    return _resolve_overlaps(cues)
def _resolve_overlaps(cues: list[Cue]) -> list[Cue]:
    for i in range(len(cues) - 1):
        if cues[i].end > cues[i + 1].start:
            cues[i].end = cues[i + 1].start
    return [c for c in cues if c.end > c.start and c.lines]
