# Bengali Subtitle Studio

A Windows desktop application that converts Bengali audio or video into two production-ready subtitle files: corrected Bengali (`name_bn.srt`) and fluent English (`name_en.srt`). Built for accuracy-first workflows: every subtitle timestamp is derived from measured positions in the audio, never estimated.

## Features

- Single-window dark UI with drag-free workflow: pick a file, press Generate, get both SRT files
- Speech to text with Google Chirp 3 through the OpenRouter API (swappable to Whisper and other models)
- Two LLM passes: Bengali correction (spelling, punctuation, recognition fixes informed by full-transcript context) and natural English translation with per-cue length budgets
- Professional subtitle standards enforced locally, without extra API calls
- Built-in preview player: play, pause, click-to-seek, a live caption bar, and auto-centered highlighting of the active cue while the audio plays
- Subtitle text is editable in place; save edits back to the file or copy the whole SRT to the clipboard
- Open existing audio and subtitle sets for preview: select any one file and the matching set is detected automatically, always picking the latest version
- Output versioning: existing subtitles are never overwritten, later runs produce `_v2`, `_v3`, and so on
- Optional custom instruction applied to both LLM passes (censoring, casing, terminology), limited to 500 characters
- API key stored encrypted with Windows DPAPI, bound to your Windows account; revealing it in the UI requires Windows Hello (fingerprint, face, or PIN) or your account password
- UTF-8 SRT output with BOM, compatible with VLC, YouTube, Premiere Pro, DaVinci Resolve, and CapCut

## How it works

```
media file
   |  FFmpeg: extract 16 kHz mono FLAC
   v
pause detection (3-pass ffmpeg silencedetect)
   |  split into cue-sized chunks (1.2 to 6 s) cut at real pauses
   v
parallel transcription (Chirp 3 via OpenRouter, 4 workers)
   |  chunk speech boundaries become cue timings, verbatim
   v
LLM pass 1: Bengali correction (text only, timing never leaves the app)
   v
local standards enforcement (line length, cue length, reading speed)
   v
LLM pass 2: English translation (identical cue timing, per-cue budgets)
   v
name_bn.srt + name_en.srt (+ raw transcript JSON for auditing)
```

Timing accuracy is the core design constraint:

- Cue start and end come from measured speech boundaries detected in the audio. The app never stretches, shifts, or invents timestamps.
- The LLMs only ever see and return text keyed by cue id. Cue timing lives in the app, so no model output can alter it. A final invariant check refuses to write files if timing differs between the two languages.
- If audio in a chunk is silence or music, it simply produces no subtitle.

## Subtitle standards enforced

All enforcement is local and costs no tokens:

| Rule | Value |
| --- | --- |
| Maximum line length | 42 characters |
| Maximum lines per subtitle | 2, with single lines preferred |
| Cue duration | 0.833 s to 7 s |
| Reading speed | 17 chars/s Bengali, 20 chars/s English |
| Gap between cues | at least 2 frames, or exactly contiguous |
| Line breaks | at natural language boundaries |

Long cues are split at word or punctuation boundaries with time divided proportionally to text length. Short cues merge into contiguous neighbours. Reading speed is relaxed by extending a cue into the following silence, never past the next cue. Lines do not end on articles, prepositions, or conjunctions (English and Bengali lists), never separate a number from its unit, and never break hyphenated pairs. Speaker changes detected by the correction pass are rendered one speaker per line with a leading dash.

## Requirements

- Windows 10 or 11
- [FFmpeg](https://ffmpeg.org/) on PATH (`winget install Gyan.FFmpeg`)
- An [OpenRouter API key](https://openrouter.ai/keys)
- Python 3.10 or newer to run from source (the packaged exe needs no Python)

The core application uses only the Python standard library. The optional Windows Hello prompt uses the `winrt` bridge packages:

```
pip install winrt-runtime winrt-Windows.Security.Credentials.UI winrt-Windows.Foundation
```

Without them the app still works and falls back to a Windows account password check.

## Running from source

```
py app.py
```

1. Choose an audio or video file (mp3, wav, m4a, mp4, mkv, mov, flac, ogg, aac, webm). The output folder defaults to the same directory.
2. Paste your OpenRouter API key (stored encrypted after the first run).
3. Optionally add a custom instruction for the LLM passes.
4. Press Generate subtitles and watch per-stage progress: extracting, transcribing, correcting, translating, saving.
5. Review in the preview player, edit any line, and save.

## Building the executable

```
pip install pyinstaller
py -m PyInstaller --noconfirm --onefile --noconsole --icon app.ico --add-data "app.ico;." --collect-submodules winrt --collect-data winrt --name "Bengali Subtitle Studio" app.py
```

The result is a single portable `dist/Bengali Subtitle Studio.exe`. Target machines only need FFmpeg on PATH.

## Configuration

Settings persist in `%APPDATA%\BengaliSubtitleStudio\config.json`:

| Setting | Default | Notes |
| --- | --- | --- |
| Speech to text model | `google/chirp-3` | Any model on OpenRouter's transcription endpoint |
| LLM model | `google/gemini-3.1-flash-lite` | Any OpenRouter chat model |
| Language | `bn-IN` | `bn-IN` or `bn-BD` |
| API key | none | Stored DPAPI-encrypted, never in plain text |

The output folder and custom instruction are per-run choices and reset on every launch.

## Architecture

```
app.py                      Tkinter UI, preview player, event loop
bnsrt/
  pipeline.py               Stage orchestration, parallel chunk transcription
  chunker.py                Pause detection and chunk planning
  ffmpeg.py                 Audio extraction and preview transcoding
  segmenter.py              Word/segment timings to subtitle cues
  standards.py              Subtitle standards enforcement
  srt.py                    Cue model, line wrapping, SRT read/write
  passes.py                 LLM correction and translation passes
  openrouter.py             HTTP client with retry and backoff
  pairing.py                Audio/subtitle set auto-detection
  player.py                 Audio playback (Windows MCI)
  secret.py                 DPAPI secret storage
  winauth.py                Windows Hello / password verification
  config.py                 Settings persistence
  providers/
    base.py                 Transcription and LLM provider interfaces
    openrouter_stt.py       Chirp 3 and compatible transcription models
    openrouter_llm.py       OpenRouter chat models
```

Providers are swappable: implement `TranscriptionProvider` or `LlmProvider` from `bnsrt/providers/base.py` and pass instances to `Pipeline`. If a transcription model returns word-level timestamps, the app automatically uses them at word precision instead of chunk boundaries.

## Error handling

- Transient API failures (429, 5xx, network) retry with exponential backoff
- A single rejected chunk becomes an empty cue; authentication, quota, and rate-limit exhaustion abort the run with the provider's message
- Malformed LLM batches are retried, then fall back safely: correction keeps the original text, translation retries line by line
- Generation never overwrites existing output files

## Limitations

- Windows only (playback uses MCI, secrets use DPAPI, identity uses Windows Hello)
- Subtitle timing is audio-derived; alignment to video shot cuts is out of scope
- Not an SDH pipeline: no sound-effect captions or speaker diarization from the model

## License

MIT. See [LICENSE](LICENSE).

## Author

Abhirup Sarkar
