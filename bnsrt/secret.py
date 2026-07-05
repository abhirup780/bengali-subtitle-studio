from __future__ import annotations
import base64
import ctypes
import sys
from ctypes import wintypes
_ENTROPY = b'BengaliSubtitleStudio.v1'
class SecretError(Exception):
    pass
class _DataBlob(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]
def _blob(data: bytes) -> _DataBlob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
def _call(func_name: str, data: bytes) -> bytes:
    if sys.platform != 'win32':
        raise SecretError('DPAPI is only available on Windows.')
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    inp = _blob(data)
    entropy = _blob(_ENTROPY)
    out = _DataBlob()
    func = getattr(crypt32, func_name)
    if not func(ctypes.byref(inp), None, ctypes.byref(entropy), None, None, 0, ctypes.byref(out)):
        raise SecretError(f'{func_name} failed (error {kernel32.GetLastError()})')
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)
def protect(text: str) -> str:
    return base64.b64encode(_call('CryptProtectData', text.encode('utf-8'))).decode('ascii')
def unprotect(token: str) -> str:
    try:
        raw = base64.b64decode(token.encode('ascii'))
    except (ValueError, UnicodeEncodeError) as exc:
        raise SecretError(f'Malformed secret blob: {exc}') from exc
    return _call('CryptUnprotectData', raw).decode('utf-8')
