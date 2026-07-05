from __future__ import annotations
import ctypes
import sys
import threading
import time
from ctypes import wintypes
try:
    import asyncio
    import winrt.runtime as _winrt_runtime
    from winrt.windows.security.credentials.ui import UserConsentVerificationResult, UserConsentVerifier, UserConsentVerifierAvailability
    _HAVE_HELLO = True
except Exception:
    _HAVE_HELLO = False
def verify_user(reason: str, password_prompt=None, pump=None) -> bool | None:
    hello = _try_hello(reason, pump)
    if hello is not None:
        return hello
    if password_prompt is None:
        return None
    return _password_fallback(reason, password_prompt)
def _try_hello(reason: str, pump=None) -> bool | None:
    if not _HAVE_HELLO or sys.platform != 'win32':
        return None
    outcome: list = [None]
    def worker() -> None:
        try:
            _winrt_runtime.init_apartment(_winrt_runtime.ApartmentType.MULTI_THREADED)
        except Exception:
            pass
        async def run() -> bool | None:
            availability = await UserConsentVerifier.check_availability_async()
            if availability != UserConsentVerifierAvailability.AVAILABLE:
                return None
            threading.Thread(target=_bring_hello_to_front, daemon=True).start()
            result = await UserConsentVerifier.request_verification_async(reason)
            return result == UserConsentVerificationResult.VERIFIED
        try:
            outcome[0] = asyncio.run(run())
        except Exception:
            outcome[0] = None
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    while thread.is_alive():
        thread.join(0.05)
        if pump:
            try:
                pump()
            except Exception:
                pass
    return outcome[0]
def _bring_hello_to_front() -> None:
    user32 = ctypes.windll.user32
    HWND_TOPMOST, HWND_NOTOPMOST = (-1, -2)
    FLAGS = 1 | 2 | 64
    ASFW_ANY = -1
    user32.AllowSetForegroundWindow(ASFW_ANY)
    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline:
        hwnd = user32.FindWindowW('Credential Dialog Xaml Host', None)
        if hwnd:
            user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, FLAGS)
            user32.SetForegroundWindow(hwnd)
            user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, FLAGS)
            return
        time.sleep(0.05)
def _password_fallback(reason: str, password_prompt) -> bool | None:
    while True:
        password = password_prompt(reason)
        if password is None:
            return False
        result = check_windows_password(password)
        if result is None:
            return None
        if result:
            return True
def check_windows_password(password: str) -> bool | None:
    if sys.platform != 'win32':
        return None
    advapi32 = ctypes.windll.advapi32
    kernel32 = ctypes.windll.kernel32
    size = wintypes.DWORD(0)
    advapi32.GetUserNameW = ctypes.windll.advapi32.GetUserNameW
    advapi32.GetUserNameW(None, ctypes.byref(size))
    buf = ctypes.create_unicode_buffer(size.value + 1)
    if not advapi32.GetUserNameW(buf, ctypes.byref(size)):
        return None
    username = buf.value
    LOGON32_LOGON_INTERACTIVE = 2
    LOGON32_PROVIDER_DEFAULT = 0
    ERROR_LOGON_FAILURE = 1326
    token = wintypes.HANDLE()
    ok = advapi32.LogonUserW(username, '.', password, LOGON32_LOGON_INTERACTIVE, LOGON32_PROVIDER_DEFAULT, ctypes.byref(token))
    if ok:
        kernel32.CloseHandle(token)
        return True
    if kernel32.GetLastError() == ERROR_LOGON_FAILURE:
        return False
    return None
