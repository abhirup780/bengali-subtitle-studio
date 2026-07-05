from __future__ import annotations
import json
import os
from dataclasses import asdict, dataclass, field, fields
from . import secret
def _config_dir() -> str:
    base = os.environ.get('APPDATA') or os.path.expanduser('~')
    return os.path.join(base, 'BengaliSubtitleStudio')
CONFIG_PATH = os.path.join(_config_dir(), 'config.json')
@dataclass
class Settings:
    api_key: str = ''
    stt_model: str = 'google/chirp-3'
    llm_model: str = 'google/gemini-3.1-flash-lite'
    language: str = 'bn-IN'
    custom_instruction: str = ''
    last_output_dir: str = ''
    save_raw_transcript: bool = True
    _extra: dict = field(default_factory=dict, repr=False)
    _had_plaintext_key: bool = field(default=False, repr=False)
    @classmethod
    def load(cls) -> 'Settings':
        try:
            with open(CONFIG_PATH, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return cls()
        protected = data.pop('api_key_protected', '')
        known = {f.name for f in fields(cls) if not f.name.startswith('_')}
        kwargs = {k: v for k, v in data.items() if k in known}
        settings = cls(**kwargs)
        settings._extra = {k: v for k, v in data.items() if k not in known}
        if protected:
            try:
                settings.api_key = secret.unprotect(protected)
            except secret.SecretError:
                settings.api_key = ''
        elif settings.api_key:
            settings._had_plaintext_key = True
        return settings
    def save(self) -> None:
        os.makedirs(_config_dir(), exist_ok=True)
        data = {k: v for k, v in asdict(self).items() if not k.startswith('_')}
        data.update(self._extra)
        plain_key = data.pop('api_key', '')
        if plain_key:
            try:
                data['api_key_protected'] = secret.protect(plain_key)
            except secret.SecretError:
                data['api_key'] = plain_key
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
