from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from ..intelligence_models import Capability, ObservationOutcome, SensorHealth, SensorObservation
from .base import BaseSensor, CommandRunner, ObservationCache
from .everything import EverythingSensor
from .filesystem import FilesystemFallbackSensor
from .git import GitSensor
from .github_cli import GitHubCLISensor
from .python_archive import PythonArchiveSensor
from .sevenzip import SevenZipSensor
from .smartctl import SmartctlSensor
from .sysinternals import SysinternalsSensor
from .windows_native import WindowsNativeSensor
from .winget import WingetSensor
from .wiztree import WizTreeSensor


DEFAULT_PRIORITIES = {
    Capability.FILE_SEARCH: ["everything", "filesystem_fallback"],
    Capability.FILE_METADATA: ["everything", "windows_native", "filesystem_fallback"],
    Capability.STORAGE_TREE: ["wiztree", "filesystem_fallback"],
    Capability.STORAGE_USAGE: ["windows_native"],
    Capability.ALLOCATED_SIZE: ["wiztree", "filesystem_fallback"],
    Capability.ARCHIVE_INSPECTION: ["sevenzip", "python_archive"],
    Capability.HARDLINK_RESOLUTION: ["sysinternals"],
    Capability.JUNCTION_RESOLUTION: ["sysinternals"],
    Capability.FILE_HANDLE_OWNER: ["sysinternals"],
    Capability.FILE_SIGNATURE: ["sysinternals"],
    Capability.SOFTWARE_INVENTORY: ["winget", "windows_native"],
    Capability.SYSTEM_INFO: ["windows_native"],
    Capability.VOLUME_INFO: ["windows_native"],
    Capability.GPU_STATUS: ["windows_native"],
    Capability.GIT_STATUS: ["git"],
    Capability.GITHUB_STATUS: ["github_cli"],
    Capability.DISK_HEALTH: ["smartctl", "windows_native"],
    Capability.PROCESS_ACTIVITY: ["windows_native"],
    Capability.FILE_ACTIVITY_TRACE: ["sysinternals"],
}


class CapabilityRegistry:
    def __init__(self, priorities: dict[Capability, list[str]] | None = None) -> None:
        self._sensors: dict[str, BaseSensor] = {}
        self._priorities = {key: list(value) for key, value in (priorities or DEFAULT_PRIORITIES).items()}

    def register(self, sensor: BaseSensor) -> None:
        if sensor.sensor_id in self._sensors:
            raise ValueError(f"Duplicate sensor_id: {sensor.sensor_id}")
        self._sensors[sensor.sensor_id] = sensor

    def sensor(self, sensor_id: str) -> BaseSensor:
        return self._sensors[sensor_id]

    def providers(self, capability: Capability) -> list[BaseSensor]:
        preferred = self._priorities.get(capability, [])
        ordered = [self._sensors[item] for item in preferred if item in self._sensors and capability in self._sensors[item].capabilities()]
        remaining = [sensor for sensor in self._sensors.values() if capability in sensor.capabilities() and sensor not in ordered]
        return ordered + remaining

    def request(self, capability: Capability, *, scope: str, **kwargs: Any) -> SensorObservation:
        errors: list[dict[str, Any]] = []
        first_failure: SensorObservation | None = None
        for sensor in self.providers(capability):
            status = sensor.health_check()
            if status.health in {SensorHealth.UNAVAILABLE, SensorHealth.MANUAL_STEP_REQUIRED, SensorHealth.DISABLED, SensorHealth.AUTH_UNAVAILABLE}:
                errors.append({"sensor_id": sensor.sensor_id, "code": status.health.value, "message": status.failure_reason})
                continue
            observation = sensor.collect(capability, scope=scope, **kwargs)
            if observation.outcome != ObservationOutcome.SENSOR_ERROR:
                observation.upstream_errors = errors
                return observation
            if first_failure is None:
                first_failure = observation
            errors.append({"sensor_id": sensor.sensor_id, **(observation.error or {"code": "SENSOR_ERROR"})})
        if first_failure is not None:
            first_failure.upstream_errors = errors[1:]
            return first_failure
        return SensorObservation.failure(
            "capability_registry",
            capability,
            scope,
            "CAPABILITY_UNAVAILABLE",
            "No healthy sensor provider is available",
            details={"providers": errors},
        )

    def health_snapshot(self) -> list[dict[str, Any]]:
        return [self._sensors[key].health_check().to_dict() for key in sorted(self._sensors)]


def build_default_registry(
    config: dict[str, Any] | None = None,
    *,
    cache_path: Path | None = None,
    raw_dir: Path | None = None,
    runner: CommandRunner | None = None,
) -> CapabilityRegistry:
    values = config or {}
    shared_runner = runner or CommandRunner()
    shared_cache = ObservationCache(cache_path)
    priorities = {key: list(value) for key, value in DEFAULT_PRIORITIES.items()}
    configured_priorities = values.get("capability_preferences", {})
    if isinstance(configured_priorities, dict):
        for raw_capability, raw_providers in configured_priorities.items():
            try:
                capability = Capability(str(raw_capability))
            except ValueError:
                continue
            if isinstance(raw_providers, list) and all(isinstance(item, str) for item in raw_providers):
                priorities[capability] = list(raw_providers)
    registry = CapabilityRegistry(priorities)
    for sensor_type in (
        EverythingSensor,
        WizTreeSensor,
        SevenZipSensor,
        PythonArchiveSensor,
        SysinternalsSensor,
        GitSensor,
        GitHubCLISensor,
        WingetSensor,
        SmartctlSensor,
        WindowsNativeSensor,
        FilesystemFallbackSensor,
    ):
        registry.register(sensor_type(values, runner=shared_runner, cache=shared_cache, raw_dir=raw_dir))
    return registry

