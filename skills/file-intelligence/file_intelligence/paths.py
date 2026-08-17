from __future__ import annotations

import os
from pathlib import Path


def _windows_final_path(absolute: str) -> str | None:
    """Resolve 8.3 aliases and long existing paths without retaining an open handle."""
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.GetFinalPathNameByHandleW.argtypes = [
            wintypes.HANDLE,
            wintypes.LPWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
        candidate = absolute
        if not candidate.startswith("\\\\?\\"):
            if candidate.startswith("\\\\"):
                candidate = "\\\\?\\UNC\\" + candidate[2:]
            else:
                candidate = "\\\\?\\" + candidate
        handle = kernel32.CreateFileW(
            candidate,
            0,
            0x00000001 | 0x00000002 | 0x00000004,
            None,
            3,
            0x02000000,
            None,
        )
        invalid = wintypes.HANDLE(-1).value
        if handle == invalid:
            return None
        try:
            needed = kernel32.GetFinalPathNameByHandleW(handle, None, 0, 0)
            if not needed:
                return None
            buffer = ctypes.create_unicode_buffer(needed + 1)
            if not kernel32.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0):
                return None
            result = buffer.value
        finally:
            kernel32.CloseHandle(handle)
        if result.startswith("\\\\?\\UNC\\"):
            return "\\\\" + result[8:]
        if result.startswith("\\\\?\\"):
            return result[4:]
        return result
    except (AttributeError, OSError, ValueError):
        return None


def canonical_path(value: str | Path) -> str:
    absolute = os.path.abspath(os.fspath(value))
    if os.name != "nt":
        return absolute
    final = _windows_final_path(absolute)
    if final:
        return final
    try:
        import ctypes

        needed = ctypes.windll.kernel32.GetLongPathNameW(absolute, None, 0)
        if needed:
            buffer = ctypes.create_unicode_buffer(needed + 1)
            if ctypes.windll.kernel32.GetLongPathNameW(absolute, buffer, len(buffer)):
                return buffer.value
    except (AttributeError, OSError):
        pass
    return absolute


def path_key(value: str | Path) -> str:
    return os.path.normcase(canonical_path(value)).casefold()
