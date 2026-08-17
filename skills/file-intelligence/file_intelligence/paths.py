from __future__ import annotations

import os
from pathlib import Path


def canonical_path(value: str | Path) -> str:
    absolute = os.path.abspath(os.fspath(value))
    if os.name != "nt":
        return absolute
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
