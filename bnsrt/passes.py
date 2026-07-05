from __future__ import annotations
import json
import re
from collections.abc import Callable
from .errors import PipelineError
from .providers.base import LlmProvider
from .srt import Cue
BATCH_SIZE = 30
BATCH_ATTEMPTS = 3
CORRECTION_SYSTEM = 'You are a meticulous Bengali subtitle proofreader. You receive subtitle lines\nproduced by automatic speech recognition of Bengali audio.\n\nRules:\n- Correct obvious recognition mistakes ONLY when you are highly confident.\n- Use the full transcript (provided as "transcript_context") to resolve\n  ASR mishearings: proper nouns, product or brand names, and recurring terms\n  are often garbled in one line but clear from the rest of the audio. Fix\n  these consistently everywhere they appear.\n- Preserve the original meaning. Never invent dialogue or add information.\n- Improve punctuation, spacing, and spelling. Use the danda (।) for sentence ends.\n- Convert English words naturally spoken in Bengali into common Bengali script\n  (e.g. "computer" -> "কম্পিউটার") when that spelling is clearly standard.\n  Keep proper nouns, brand names, URLs, email addresses, and technical\n  identifiers unchanged unless the transcript context shows the ASR misheard them.\n- If a line is genuinely ambiguous, keep the original text instead of guessing.\n- If one line clearly contains speech from two different speakers, mark the\n  change by starting each speaker\'s part with "- " (dash space). Never invent\n  speaker changes that are not evident.\n- Do NOT merge, split, reorder, or drop lines. Return exactly one output line\n  per input line, with the same id.\n\nInput is a JSON object {"items":[...], "transcript_context":"<full transcript>"}.\nSome items may appear under "context" for continuity only — do not include\nthem in output. Respond with ONLY a JSON object:\n{"items":[{"i":<id>,"t":"<corrected text>"}...]}.\n'
TRANSLATION_SYSTEM = 'You are an expert Bengali-to-English subtitle translator.\n\nRules:\n- Translate each Bengali subtitle line into fluent, natural, conversational\n  English. Prioritize readability and idiomatic phrasing over word-for-word\n  literalness, but preserve intent, tone, and emotion exactly.\n- Do not add or remove information.\n- Keep names, places, and technical terms accurate, and translate them\n  consistently — the full transcript is provided as "transcript_context" so\n  you can keep terminology and names uniform across all lines.\n- Each item includes "max" — the character budget that fits that subtitle\'s\n  on-screen duration at a comfortable reading speed. Stay within it whenever\n  possible by phrasing concisely; never sacrifice meaning to fit.\n- If the Bengali line marks speaker changes with "- " prefixes, keep the same\n  "- " structure in the English translation.\n- Do NOT merge, split, reorder, or drop lines. Return exactly one output line\n  per input line, with the same id.\n\nInput is a JSON object {"items":[...], "transcript_context":"<full transcript>"}.\nSome items may appear under "context" for continuity only — do not include\nthem in output. Respond with ONLY a JSON object:\n{"items":[{"i":<id>,"t":"<English translation>"}...]}.\n'
ProgressFn = Callable[[float], None]
MAX_INSTRUCTION_CHARS = 500
def correct_bengali(cues: list[Cue], llm: LlmProvider, progress: ProgressFn | None=None, instruction: str='') -> list[Cue]:
    system = CORRECTION_SYSTEM + _instruction_block(instruction)
    texts = _transform(cues, llm, system, progress, on_batch_failure='keep_original')
    return _apply_texts(cues, texts)
def translate_english(cues: list[Cue], llm: LlmProvider, progress: ProgressFn | None=None, instruction: str='') -> list[Cue]:
    system = TRANSLATION_SYSTEM + _instruction_block(instruction)
    texts = _transform(cues, llm, system, progress, on_batch_failure='one_by_one', char_budget=True)
    return _apply_texts(cues, texts)
def _instruction_block(instruction: str) -> str:
    instruction = ' '.join(instruction.split())[:MAX_INSTRUCTION_CHARS].strip()
    if not instruction:
        return ''
    return f'\n\nAdditional user instructions — apply them to the text of each line where relevant. If they conflict with the rules above about ids, line counts, ordering, or output format, the rules above always win:\n{instruction}\n'
def _apply_texts(cues: list[Cue], texts: list[str]) -> list[Cue]:
    return [cue.with_text(text) if text.strip() else cue.with_text(cue.text) for cue, text in zip(cues, texts)]
def _transform(cues: list[Cue], llm: LlmProvider, system: str, progress: ProgressFn | None, on_batch_failure: str, char_budget: bool=False) -> list[str]:
    items: list[dict] = [{'i': i, 't': ' '.join(c.text.split())} for i, c in enumerate(cues)]
    if char_budget:
        for item, cue in zip(items, cues):
            item['max'] = max(20, int((cue.end - cue.start) * 20))
    transcript = _transcript_context(items)
    out: dict[int, str] = {}
    for batch_start in range(0, len(items), BATCH_SIZE):
        batch = items[batch_start:batch_start + BATCH_SIZE]
        context = items[max(0, batch_start - 2):batch_start]
        result = _run_batch(llm, system, batch, context, transcript)
        if result is None:
            if on_batch_failure == 'keep_original':
                result = {item['i']: item['t'] for item in batch}
            else:
                result = {}
                for item in batch:
                    single = _run_batch(llm, system, [item], [], transcript)
                    if single is None:
                        raise PipelineError(f"The LLM could not process subtitle #{item['i'] + 1} after multiple retries. Try a different LLM model.")
                    result.update(single)
        out.update(result)
        if progress:
            progress(min(1.0, (batch_start + len(batch)) / max(1, len(items))))
    return [out.get(i, items[i]['t']) for i in range(len(items))]
def _transcript_context(items: list[dict], max_chars: int=6000) -> str:
    text = ' '.join((item['t'] for item in items))
    if len(text) > max_chars:
        half = max_chars // 2
        text = f'{text[:half]} […] {text[-half:]}'
    return text
def _run_batch(llm: LlmProvider, system: str, batch: list[dict], context: list[dict], transcript: str='') -> dict[int, str] | None:
    payload: dict = {'items': batch}
    if transcript:
        payload['transcript_context'] = transcript
    if context:
        payload['context'] = context
    user = json.dumps(payload, ensure_ascii=False)
    expected_ids = {item['i'] for item in batch}
    for _ in range(BATCH_ATTEMPTS):
        try:
            reply = llm.complete(system, user)
            parsed = _parse_items(reply)
        except PipelineError:
            raise
        except Exception:
            continue
        if parsed is not None and expected_ids <= set(parsed):
            return {i: parsed[i] for i in expected_ids}
    return None
def _parse_items(reply: str) -> dict[int, str] | None:
    text = reply.strip()
    text = re.sub('^```(?:json)?\\s*|\\s*```$', '', text)
    start, end = (text.find('{'), text.rfind('}'))
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
        items = data['items']
        return {int(item['i']): str(item['t']) for item in items}
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None
