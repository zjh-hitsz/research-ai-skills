from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from ..intelligence_models import Capability, SensorObservation, SensorStatus, utc_now


@dataclass(slots=True)
class CommandResult:
    argv: list[str]
    return_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    peak_memory_bytes: int | None
    output_bytes: int
    timed_out: bool = False
    exception: str | None = None

    @property
    def ok(self) -> bool:
        return self.return_code == 0 and not self.timed_out and self.exception is None


def _working_set_bytes(pid: int) -> int | None:
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(counters)
        try:
            if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                return None
            return int(counters.PeakWorkingSetSize)
        finally:
            kernel32.CloseHandle(handle)
    except (OSError, AttributeError, ValueError):
        return None


class CommandRunner:
    """Bounded subprocess runner with no shell and no hidden mutation helpers."""

    def run(
        self,
        argv: Iterable[str | os.PathLike[str]],
        *,
        timeout: float = 30.0,
        cwd: str | os.PathLike[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        args = [os.fspath(item) for item in argv]
        started = time.perf_counter()
        peak: int | None = None
        try:
            with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
                process = subprocess.Popen(
                    args,
                    cwd=os.fspath(cwd) if cwd else None,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    shell=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                deadline = started + timeout
                timed_out = False
                while process.poll() is None:
                    sample = _working_set_bytes(process.pid)
                    if sample is not None:
                        peak = max(peak or 0, sample)
                    if time.perf_counter() >= deadline:
                        timed_out = True
                        process.kill()
                        break
                    time.sleep(0.01)
                process.wait(timeout=5)
                sample = _working_set_bytes(process.pid)
                if sample is not None:
                    peak = max(peak or 0, sample)
                stdout_file.seek(0)
                stderr_file.seek(0)
                stdout_raw = stdout_file.read()
                stderr_raw = stderr_file.read()
            return CommandResult(
                argv=args,
                return_code=process.returncode,
                stdout=stdout_raw.decode("utf-8", errors="replace"),
                stderr=stderr_raw.decode("utf-8", errors="replace"),
                duration_seconds=time.perf_counter() - started,
                peak_memory_bytes=peak,
                output_bytes=len(stdout_raw) + len(stderr_raw),
                timed_out=timed_out,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return CommandResult(
                argv=args,
                return_code=None,
                stdout="",
                stderr="",
                duration_seconds=time.perf_counter() - started,
                peak_memory_bytes=peak,
                output_bytes=0,
                exception=f"{type(exc).__name__}: {exc}",
            )


class ObservationCache:
    """Small summary cache; raw sensor payloads stay in artifact files."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._data: dict[str, Any] = {"version": 1, "observations": {}}
        if path and path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict) and isinstance(loaded.get("observations"), dict):
                    self._data = loaded
            except (OSError, json.JSONDecodeError):
                pass

    @staticmethod
    def key(sensor_id: str, capability: Capability | str, scope: str) -> str:
        cap = str(getattr(capability, "value", capability))
        return f"{sensor_id}|{cap}|{scope.casefold()}"

    def get(self, sensor_id: str, capability: Capability | str, scope: str) -> Any:
        item = self._data["observations"].get(self.key(sensor_id, capability, scope))
        return item.get("normalized_data") if isinstance(item, dict) else None

    def remember(self, observation: SensorObservation) -> None:
        if observation.outcome.value == "SENSOR_ERROR":
            return
        self._data["observations"][self.key(observation.sensor_id, observation.capability, observation.scope)] = {
            "timestamp": observation.timestamp,
            "normalized_data": observation.normalized_data,
        }
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(self.path.suffix + ".tmp")
            temp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(self.path)


class BaseSensor(ABC):
    sensor_id = "base"
    display_name = "Base Sensor"

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        runner: CommandRunner | None = None,
        cache: ObservationCache | None = None,
        raw_dir: Path | None = None,
    ) -> None:
        self.config = config or {}
        self.runner = runner or CommandRunner()
        self.cache = cache or ObservationCache()
        self.raw_dir = raw_dir
        self._last_success: str | None = None
        self._last_failure: str | None = None

    @abstractmethod
    def detect(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def capabilities(self) -> set[Capability]:
        raise NotImplementedError

    @abstractmethod
    def health_check(self) -> SensorStatus:
        raise NotImplementedError

    @abstractmethod
    def collect(self, capability: Capability, *, scope: str, **kwargs: Any) -> SensorObservation:
        raise NotImplementedError

    @abstractmethod
    def normalize(self, raw: Any, capability: Capability, *, scope: str) -> Any:
        raise NotImplementedError

    @abstractmethod
    def version(self) -> str | None:
        raise NotImplementedError

    @abstractmethod
    def source(self) -> dict[str, str]:
        raise NotImplementedError

    def _metrics(self, result: CommandResult) -> dict[str, Any]:
        return {
            "startup_and_query_seconds": result.duration_seconds,
            "peak_memory_bytes": result.peak_memory_bytes,
            "output_bytes": result.output_bytes,
            "return_code": result.return_code,
            "timed_out": result.timed_out,
        }

    def _success(self, observation: SensorObservation) -> SensorObservation:
        self._last_success = utc_now()
        self.cache.remember(observation)
        return observation

    def _failure(
        self,
        capability: Capability,
        scope: str,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> SensorObservation:
        self._last_failure = utc_now()
        return SensorObservation.failure(
            self.sensor_id,
            capability,
            scope,
            code,
            message,
            details=details,
            last_known_good=self.cache.get(self.sensor_id, capability, scope),
            metrics=metrics,
        )

    def _write_raw(self, stem: str, content: str | bytes, suffix: str = ".txt") -> str | None:
        if self.raw_dir is None:
            return None
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        path = self.raw_dir / f"{stem}_{int(time.time() * 1000)}{suffix}"
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return str(path)

    def _status(
        self,
        *,
        version: str | None,
        available: bool,
        health: Any,
        executable_path: str | None,
        failure_reason: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> SensorStatus:
        return SensorStatus(
            sensor_id=self.sensor_id,
            name=self.display_name,
            version=version,
            available=available,
            health=health,
            executable_path=executable_path,
            capabilities=sorted(cap.value for cap in self.capabilities()),
            last_success=self._last_success,
            last_failure=self._last_failure,
            failure_reason=failure_reason,
            details=details or {},
        )


