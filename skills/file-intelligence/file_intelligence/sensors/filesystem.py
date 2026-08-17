from __future__ import annotations

import os
import shutil
import stat
import time
from pathlib import Path
from typing import Any

from ..intelligence_models import Capability, SensorHealth, SensorObservation
from .base import BaseSensor


def allocated_size(path: Path, logical_size: int) -> int | None:
    try:
        info = path.stat(follow_symlinks=False)
    except OSError:
        return None
    blocks = getattr(info, "st_blocks", None)
    if blocks is not None:
        return int(blocks) * 512
    if os.name != "nt" or not path.is_file():
        return logical_size if path.is_file() else 0
    try:
        import ctypes
        from ctypes import wintypes

        class FILE_STANDARD_INFO(ctypes.Structure):
            _fields_ = [
                ("AllocationSize", ctypes.c_longlong),
                ("EndOfFile", ctypes.c_longlong),
                ("NumberOfLinks", wintypes.DWORD),
                ("DeletePending", wintypes.BOOL),
                ("Directory", wintypes.BOOL),
            ]

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
        handle = kernel32.CreateFileW(
            str(path),
            0,
            0x00000001 | 0x00000002 | 0x00000004,
            None,
            3,
            0x02000000,
            None,
        )
        invalid_handle = wintypes.HANDLE(-1).value
        if handle != invalid_handle:
            standard = FILE_STANDARD_INFO()
            try:
                if kernel32.GetFileInformationByHandleEx(handle, 1, ctypes.byref(standard), ctypes.sizeof(standard)):
                    return int(standard.AllocationSize)
            finally:
                kernel32.CloseHandle(handle)

        # Older/limited environments may reject FILE_STANDARD_INFO; retain a read-only fallback.
        high = wintypes.DWORD(0)
        low = ctypes.windll.kernel32.GetCompressedFileSizeW(str(path), ctypes.byref(high))
        if low == 0xFFFFFFFF and ctypes.get_last_error() != 0:
            return None
        return (int(high.value) << 32) | int(low)
    except (OSError, AttributeError):
        return None


class FilesystemFallbackSensor(BaseSensor):
    sensor_id = "filesystem_fallback"
    display_name = "Bounded Filesystem Fallback"

    def detect(self) -> dict[str, Any]:
        return {"available": True}

    def capabilities(self) -> set[Capability]:
        return {Capability.FILE_SEARCH, Capability.FILE_METADATA, Capability.STORAGE_TREE, Capability.ALLOCATED_SIZE}

    def health_check(self):
        return self._status(
            version=None,
            available=True,
            health=SensorHealth.HEALTHY,
            executable_path=None,
            details={"bounded": True, "follows_reparse_points": False},
        )

    def version(self) -> str | None:
        return None

    def source(self) -> dict[str, str]:
        return {"vendor": "Python standard library", "official_url": "https://docs.python.org/3/library/os.html"}

    def collect(self, capability: Capability, *, scope: str, **kwargs: Any) -> SensorObservation:
        if capability not in self.capabilities():
            return self._failure(capability, scope, "UNSUPPORTED_CAPABILITY", capability.value)
        root = Path(scope)
        if not root.exists():
            return self._failure(capability, scope, "SCOPE_MISSING", "Scope does not exist")
        started = time.perf_counter()
        max_entries = max(1, int(kwargs.get("max_entries", 100000)))
        timeout = max(0.1, float(kwargs.get("timeout", 30)))
        search = str(kwargs.get("search", "")).casefold()
        stack = [root]
        rows: list[dict[str, Any]] = []
        logical_total = 0
        allocated_total = 0
        allocated_complete = True
        child_totals: dict[str, dict[str, Any]] = {}
        files = 0
        directories = 0
        errors: list[str] = []
        complete = True
        while stack:
            if len(rows) + files + directories >= max_entries or time.perf_counter() - started >= timeout:
                complete = False
                break
            current = stack.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        if len(rows) + files + directories >= max_entries or time.perf_counter() - started >= timeout:
                            complete = False
                            break
                        try:
                            if entry.is_symlink():
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                directories += 1
                                stack.append(Path(entry.path))
                                continue
                            if not entry.is_file(follow_symlinks=False):
                                continue
                            files += 1
                            item = Path(entry.path)
                            info = entry.stat(follow_symlinks=False)
                            logical = int(info.st_size)
                            allocated = allocated_size(item, logical)
                            logical_total += logical
                            if allocated is None:
                                allocated_complete = False
                            else:
                                allocated_total += allocated
                            try:
                                relative = item.relative_to(root)
                                child_name = relative.parts[0]
                                child_path = root / child_name
                                child = child_totals.setdefault(
                                    child_name,
                                    {
                                        "path": str(child_path),
                                        "logical_size": 0,
                                        "allocated_size": 0,
                                        "allocated_size_complete": True,
                                        "file_count": 0,
                                    },
                                )
                                child["logical_size"] += logical
                                child["file_count"] += 1
                                if allocated is None:
                                    child["allocated_size_complete"] = False
                                else:
                                    child["allocated_size"] += allocated
                            except ValueError:
                                pass
                            if capability in (Capability.FILE_SEARCH, Capability.FILE_METADATA):
                                if not search or search in entry.name.casefold() or search in entry.path.casefold():
                                    rows.append(
                                        {
                                            "path": entry.path,
                                            "logical_size": logical,
                                            "allocated_size": allocated,
                                            "modified_ns": int(info.st_mtime_ns),
                                        }
                                    )
                        except OSError as exc:
                            errors.append(f"{entry.path}: {exc}")
            except OSError as exc:
                errors.append(f"{current}: {exc}")
        duration = time.perf_counter() - started
        if capability in (Capability.STORAGE_TREE, Capability.ALLOCATED_SIZE):
            children = sorted(child_totals.values(), key=lambda item: item.get("logical_size") or 0, reverse=True)[:20]
            for child in children:
                if not child.pop("allocated_size_complete"):
                    child["allocated_size"] = None
            normalized: Any = {
                "path": str(root.resolve()),
                "logical_size": logical_total,
                "allocated_size": allocated_total if allocated_complete else None,
                "file_count": files,
                "directory_count": directories,
                "largest_children": children,
                "complete": complete,
                "errors": errors[:20],
            }
        else:
            normalized = rows
        observation = SensorObservation.data(
            self.sensor_id,
            capability,
            scope,
            normalized,
            confidence=1.0 if complete and not errors else 0.7,
            metrics={
                "startup_and_query_seconds": duration,
                "peak_memory_bytes": None,
                "output_bytes": 0,
                "entries_seen": files + directories,
                "complete": complete,
            },
        )
        if not complete:
            observation.error = {"code": "PARTIAL_OUTPUT", "message": "Bounded traversal stopped", "details": {"errors": errors[:20]}}
        return self._success(observation)

    def normalize(self, raw: Any, capability: Capability, *, scope: str) -> Any:
        return raw

