from __future__ import annotations
import ctypes
import sys
class PlayerError(Exception):
    pass
def _mci(command: str) -> str:
    if sys.platform != 'win32':
        raise PlayerError('Audio preview is only available on Windows.')
    buf = ctypes.create_unicode_buffer(256)
    err = ctypes.windll.winmm.mciSendStringW(command, buf, 254, 0)
    if err:
        ebuf = ctypes.create_unicode_buffer(256)
        ctypes.windll.winmm.mciGetErrorStringW(err, ebuf, 254)
        raise PlayerError(ebuf.value or f'MCI error {err}')
    return buf.value
def _short_path(path: str) -> str:
    buf = ctypes.create_unicode_buffer(1024)
    if ctypes.windll.kernel32.GetShortPathNameW(path, buf, 1024):
        return buf.value
    return path
class AudioPlayer:
    ALIAS = 'bnsrt_preview'
    def __init__(self) -> None:
        self._loaded = False
    @property
    def loaded(self) -> bool:
        return self._loaded
    def load(self, wav_path: str) -> None:
        self.close()
        _mci(f'open "{_short_path(wav_path)}" type waveaudio alias {self.ALIAS}')
        _mci(f'set {self.ALIAS} time format milliseconds')
        self._loaded = True
    def play(self, from_ms: int | None=None) -> None:
        if from_ms is not None:
            _mci(f'play {self.ALIAS} from {int(from_ms)}')
        else:
            _mci(f'play {self.ALIAS}')
    def pause(self) -> None:
        _mci(f'pause {self.ALIAS}')
    def resume(self) -> None:
        try:
            _mci(f'resume {self.ALIAS}')
        except PlayerError:
            _mci(f'play {self.ALIAS}')
    def seek(self, ms: int) -> None:
        _mci(f'seek {self.ALIAS} to {int(ms)}')
    def position(self) -> int:
        return int(_mci(f'status {self.ALIAS} position') or 0)
    def length(self) -> int:
        return int(_mci(f'status {self.ALIAS} length') or 0)
    def mode(self) -> str:
        if not self._loaded:
            return ''
        try:
            return _mci(f'status {self.ALIAS} mode')
        except PlayerError:
            return ''
    def close(self) -> None:
        if self._loaded:
            try:
                _mci(f'close {self.ALIAS}')
            except PlayerError:
                pass
            self._loaded = False
