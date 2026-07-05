from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from ..srt import Word
@dataclass
class TranscriptionResult:
    text: str
    words: list[Word] = field(default_factory=list)
    segments: list[tuple[float, float, str]] = field(default_factory=list)
    raw: dict = field(default_factory=dict)
    @property
    def has_timestamps(self) -> bool:
        return bool(self.words or self.segments)
class TranscriptionProvider(ABC):
    @abstractmethod
    def transcribe(self, audio_path: str, language: str) -> TranscriptionResult:
        pass
class LlmProvider(ABC):
    @abstractmethod
    def complete(self, system: str, user: str) -> str:
        pass
