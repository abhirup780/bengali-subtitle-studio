from __future__ import annotations
import base64
import os
from ..errors import ApiError
from ..openrouter import OpenRouterClient
from ..srt import Word
from .base import TranscriptionProvider, TranscriptionResult
TRANSCRIBE_TIMEOUT = 900.0
class OpenRouterTranscriptionProvider(TranscriptionProvider):
    def __init__(self, client: OpenRouterClient, model: str='google/chirp-3'):
        self.client = client
        self.model = model
    def transcribe(self, audio_path: str, language: str='bn') -> TranscriptionResult:
        with open(audio_path, 'rb') as f:
            audio_b64 = base64.b64encode(f.read()).decode('ascii')
        fmt = os.path.splitext(audio_path)[1].lstrip('.').lower() or 'flac'
        payload = {'model': self.model, 'input_audio': {'data': audio_b64, 'format': fmt}, 'language': language, 'temperature': 0, 'response_format': 'verbose_json', 'timestamp_granularities': ['word', 'segment']}
        try:
            data = self.client.post_json('audio/transcriptions', payload, timeout=TRANSCRIBE_TIMEOUT)
        except ApiError as exc:
            if exc.status is not None and 400 <= exc.status < 500 and (exc.status != 429):
                for key in ('response_format', 'timestamp_granularities'):
                    payload.pop(key, None)
                data = self.client.post_json('audio/transcriptions', payload, timeout=TRANSCRIBE_TIMEOUT)
            else:
                raise
        return _parse_response(data)
def _parse_response(data: dict) -> TranscriptionResult:
    words = _extract_words(data)
    segments = _extract_segments(data)
    text = data.get('text') or ' '.join((w.text for w in words)) or ' '.join((t for _, _, t in segments))
    return TranscriptionResult(text=(text or '').strip(), words=words, segments=segments, raw=data)
def _extract_words(data: dict) -> list[Word]:
    candidates: list = []
    if isinstance(data.get('words'), list):
        candidates = data['words']
    elif isinstance(data.get('segments'), list):
        for seg in data['segments']:
            if isinstance(seg, dict) and isinstance(seg.get('words'), list):
                candidates.extend(seg['words'])
    if not candidates and isinstance(data.get('results'), list):
        for result in data['results']:
            alts = result.get('alternatives') if isinstance(result, dict) else None
            if alts and isinstance(alts[0], dict) and isinstance(alts[0].get('words'), list):
                candidates.extend(alts[0]['words'])
    words: list[Word] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        text = item.get('word') or item.get('text') or ''
        start = _time_value(item, ('start', 'start_time', 'startTime', 'startOffset'))
        end = _time_value(item, ('end', 'end_time', 'endTime', 'endOffset'))
        if text.strip() and start is not None and (end is not None):
            words.append(Word(text=text.strip(), start=start, end=end))
    return words
def _extract_segments(data: dict) -> list[tuple[float, float, str]]:
    candidates: list = []
    if isinstance(data.get('segments'), list):
        candidates = data['segments']
    elif isinstance(data.get('chunks'), list):
        candidates = data['chunks']
    segments: list[tuple[float, float, str]] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        text = item.get('text') or item.get('transcript') or ''
        ts = item.get('timestamp')
        if isinstance(ts, (list, tuple)) and len(ts) == 2 and (ts[0] is not None) and (ts[1] is not None):
            start, end = (float(ts[0]), float(ts[1]))
        else:
            start = _time_value(item, ('start', 'start_time', 'startTime', 'startOffset'))
            end = _time_value(item, ('end', 'end_time', 'endTime', 'endOffset'))
        if text.strip() and start is not None and (end is not None):
            segments.append((float(start), float(end), text.strip()))
    return segments
def _time_value(item: dict, keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = item.get(key)
        if value is None:
            continue
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.rstrip('s'))
            except ValueError:
                continue
        if isinstance(value, dict):
            try:
                return float(value.get('seconds', 0)) + float(value.get('nanos', 0)) / 1000000000.0
            except (TypeError, ValueError):
                continue
    return None
