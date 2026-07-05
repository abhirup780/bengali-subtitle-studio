from __future__ import annotations
import json
import os
import tempfile
import threading
from collections.abc import Callable
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from dataclasses import dataclass
from . import chunker, ffmpeg, passes, segmenter, standards
from .errors import CancelledError, PipelineError
from .providers.base import LlmProvider, TranscriptionProvider, TranscriptionResult
from .srt import Cue, Word, render_srt, write_srt
TRANSCRIBE_WORKERS = 4
STAGES = [('extract', 'Extracting audio', 0.0, 0.08), ('transcribe', 'Transcribing', 0.08, 0.45), ('segment', 'Building subtitles', 0.45, 0.5), ('correct', 'Correcting Bengali', 0.5, 0.7), ('translate', 'Translating English', 0.7, 0.95), ('save', 'Saving subtitles', 0.95, 1.0)]
ProgressFn = Callable[[str, float, str], None]
@dataclass
class PipelineResult:
    bn_path: str
    en_path: str
    bn_srt: str
    en_srt: str
    cue_count: int
class Pipeline:
    def __init__(self, transcriber: TranscriptionProvider, llm: LlmProvider, progress: ProgressFn | None=None, cancel_event: threading.Event | None=None, language: str='bn', save_raw_transcript: bool=True, custom_instruction: str=''):
        self.transcriber = transcriber
        self.llm = llm
        self.progress = progress or (lambda *a: None)
        self.cancel_event = cancel_event or threading.Event()
        self.language = language
        self.save_raw_transcript = save_raw_transcript
        self.custom_instruction = custom_instruction
    def run(self, input_path: str, output_dir: str='') -> PipelineResult:
        if not os.path.isfile(input_path):
            raise PipelineError(f'Input file not found:\n{input_path}')
        output_dir = output_dir or os.path.dirname(os.path.abspath(input_path))
        os.makedirs(output_dir, exist_ok=True)
        stem = os.path.splitext(os.path.basename(input_path))[0]
        bn_path, en_path, raw_path = _versioned_paths(output_dir, stem)
        self._report('extract', 0.0, os.path.basename(input_path))
        with tempfile.TemporaryDirectory(prefix='bnsrt_') as tmp:
            audio_path = os.path.join(tmp, 'audio.flac')
            ffmpeg.extract_audio(input_path, audio_path)
            self._check_cancel()
            self._report('transcribe', 0.0, 'detecting pauses')
            duration = ffmpeg.probe_duration(audio_path)
            if not duration:
                raise PipelineError('Could not determine the audio duration.')
            chunks = chunker.plan_chunks(duration, chunker.detect_pauses(audio_path))
            if not chunks:
                raise PipelineError('No audio content found to transcribe.')
            self._check_cancel()
            results = self._transcribe_chunks(audio_path, chunks, tmp)
        if self.save_raw_transcript:
            try:
                with open(raw_path, 'w', encoding='utf-8') as f:
                    json.dump({'chunks': [{'start': c.speech_start, 'end': c.speech_end, 'text': r.text if r else '', 'raw': r.raw if r else None} for c, r in zip(chunks, results)]}, f, ensure_ascii=False, indent=1)
            except OSError:
                pass
        self._report('segment', 0.5, '')
        cues = _build_cues(chunks, results)
        if not cues:
            raise PipelineError('No speech was detected in the audio.')
        self._check_cancel()
        corrected = passes.correct_bengali(cues, self.llm, progress=self._stage_progress('correct', len(cues)), instruction=self.custom_instruction)
        self._check_cancel()
        bn_cues = standards.enforce(corrected)
        translated = standards.rewrap(passes.translate_english(bn_cues, self.llm, progress=self._stage_progress('translate', len(bn_cues)), instruction=self.custom_instruction))
        standards.extend_pair(bn_cues, translated)
        self._check_cancel()
        self._report('save', 0.0, '')
        _assert_timing_identical(bn_cues, translated)
        write_srt(bn_cues, bn_path)
        write_srt(translated, en_path)
        self._report('save', 1.0, 'done')
        return PipelineResult(bn_path=bn_path, en_path=en_path, bn_srt=render_srt(bn_cues), en_srt=render_srt(translated), cue_count=len(bn_cues))
    def _transcribe_chunks(self, audio_path: str, chunks: list[chunker.Chunk], tmp_dir: str) -> list[TranscriptionResult | None]:
        results: list[TranscriptionResult | None] = [None] * len(chunks)
        swallowed: list[PipelineError] = []
        done_count = 0
        lock = threading.Lock()
        total = len(chunks)
        self._report('transcribe', 0.0, f'0/{total} segments')
        def job(i: int) -> None:
            nonlocal done_count
            self._check_cancel()
            piece = chunker.extract_chunk(audio_path, chunks[i], os.path.join(tmp_dir, f'chunk_{i:04d}.flac'))
            try:
                results[i] = self.transcriber.transcribe(piece, self.language)
            except PipelineError as exc:
                status = getattr(exc, 'status', None)
                if status in (401, 402, 403, 429) or status is None:
                    raise
                results[i] = None
                with lock:
                    swallowed.append(exc)
            finally:
                try:
                    os.remove(piece)
                except OSError:
                    pass
            with lock:
                done_count += 1
                self._report('transcribe', done_count / total, f'{done_count}/{total} segments')
        pool = ThreadPoolExecutor(max_workers=TRANSCRIBE_WORKERS)
        try:
            futures = [pool.submit(job, i) for i in range(len(chunks))]
            wait(futures, return_when=FIRST_EXCEPTION)
            for f in futures:
                if f.done() and (not f.cancelled()):
                    f.result()
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        self._check_cancel()
        if swallowed and all((r is None for r in results)):
            raise swallowed[0]
        return results
    def _check_cancel(self) -> None:
        if self.cancel_event.is_set():
            raise CancelledError('Cancelled by user.')
    def _report(self, stage_key: str, within: float, detail: str) -> None:
        for key, label, lo, hi in STAGES:
            if key == stage_key:
                self.progress(label, lo + (hi - lo) * max(0.0, min(1.0, within)), detail)
                return
    def _stage_progress(self, stage_key: str, total_cues: int):
        def cb(frac: float) -> None:
            self._check_cancel()
            self._report(stage_key, frac, f'{int(frac * total_cues)}/{total_cues} subtitles')
        return cb
def _versioned_paths(output_dir: str, stem: str) -> tuple[str, str, str]:
    def paths(suffix: str) -> tuple[str, str, str]:
        return (os.path.join(output_dir, f'{stem}_bn{suffix}.srt'), os.path.join(output_dir, f'{stem}_en{suffix}.srt'), os.path.join(output_dir, f'{stem}.transcript{suffix}.json'))
    def taken(candidate: tuple[str, str, str]) -> bool:
        return any((os.path.exists(p) for p in candidate))
    candidate = paths('')
    version = 2
    while taken(candidate):
        candidate = paths(f'_v{version}')
        version += 1
    return candidate
def _build_cues(chunks: list[chunker.Chunk], results: list[TranscriptionResult | None]) -> list[Cue]:
    pairs = [(c, r) for c, r in zip(chunks, results) if r and r.text.strip()]
    if not pairs:
        return []
    if all((r.words for _, r in pairs)):
        words = [Word(w.text, w.start + c.start, w.end + c.start) for c, r in pairs for w in r.words]
        return segmenter.words_to_cues(words)
    return segmenter.segments_to_cues([(c.speech_start, c.speech_end, r.text) for c, r in pairs])
def _assert_timing_identical(original: list[Cue], *variants: list[Cue]) -> None:
    for variant in variants:
        if len(variant) != len(original) or any((v.start != o.start or v.end != o.end for v, o in zip(variant, original))):
            raise PipelineError('Internal timing invariant violated; refusing to write SRT files.')
