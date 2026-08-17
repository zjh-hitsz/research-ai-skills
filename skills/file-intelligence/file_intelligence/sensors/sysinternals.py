from __future__ import annotations

from pathlib import Path
from typing import Any

from ..intelligence_models import Capability, SensorHealth, SensorObservation
from .base import BaseSensor
from .locator import first_executable, toolbox_hint


class SysinternalsSensor(BaseSensor):
    sensor_id = "sysinternals"
    display_name = "Microsoft Sysinternals Sensor"

    TOOL_NAMES = {
        "findlinks": ["FindLinks64.exe", "FindLinks.exe"],
        "junction": ["junction64.exe", "junction.exe"],
        "handle": ["handle64.exe", "handle.exe"],
        "sigcheck": ["sigcheck64.exe", "sigcheck.exe"],
        "procmon": ["Procmon64.exe", "Procmon.exe"],
        "sysmon": ["Sysmon64.exe", "Sysmon.exe"],
        "autoruns": ["Autoruns64.exe", "Autoruns.exe"],
    }

    CAPABILITY_TOOL = {
        Capability.HARDLINK_RESOLUTION: "findlinks",
        Capability.JUNCTION_RESOLUTION: "junction",
        Capability.FILE_HANDLE_OWNER: "handle",
        Capability.FILE_SIGNATURE: "sigcheck",
    }

    def detect(self) -> dict[str, Any]:
        configured_dir = self.config.get("executables", {}).get("sysinternals_dir")
        toolbox = toolbox_hint(self.config, "sysinternals")
        roots = [configured_dir, toolbox]
        return {key: first_executable(roots, names) for key, names in self.TOOL_NAMES.items()}

    def capabilities(self) -> set[Capability]:
        return set(self.CAPABILITY_TOOL) | {Capability.FILE_ACTIVITY_TRACE}

    def _eula_accepted(self) -> bool:
        return bool(self.config.get("sensors", {}).get("sysinternals", {}).get("eula_accepted", False))

    def version(self) -> str | None:
        return str(self.config.get("tool_versions", {}).get("sysinternals") or "Suite")

    def source(self) -> dict[str, str]:
        return {
            "vendor": "Microsoft",
            "official_url": "https://learn.microsoft.com/sysinternals/downloads/sysinternals-suite",
            "download_url": "https://download.sysinternals.com/files/SysinternalsSuite.zip",
        }

    def health_check(self):
        found = self.detect()
        passive = {key: str(path) if path else None for key, path in found.items() if key in {"findlinks", "junction", "handle", "sigcheck"}}
        available = all(passive.values())
        accepted = self._eula_accepted()
        if not available:
            health = SensorHealth.UNAVAILABLE
            reason = "One or more passive Sysinternals utilities are missing"
        elif not accepted:
            health = SensorHealth.MANUAL_STEP_REQUIRED
            reason = "Sysinternals EULA has not been explicitly accepted; -accepteula is never passed automatically"
        else:
            health = SensorHealth.HEALTHY
            reason = None
        return self._status(
            version=self.version(),
            available=available,
            health=health,
            executable_path=str(Path(next(iter(passive.values()))).parent) if available else None,
            failure_reason=reason,
            details={
                "tools": {key: str(value) if value else None for key, value in found.items()},
                "eula_accepted": accepted,
                "procmon": "AVAILABLE_DISABLED" if found.get("procmon") else "MISSING",
                "sysmon": "AVAILABLE_NOT_INSTALLED" if found.get("sysmon") else "MISSING",
                "diagnostic_mode": "DISABLED",
            },
        )

    def collect(self, capability: Capability, *, scope: str, **kwargs: Any) -> SensorObservation:
        if capability == Capability.FILE_ACTIVITY_TRACE:
            return self._failure(capability, scope, "DIAGNOSTIC_DISABLED", "ProcMon/Sysmon collection requires a separate explicit bounded diagnostic request")
        tool_key = self.CAPABILITY_TOOL.get(capability)
        if not tool_key:
            return self._failure(capability, scope, "UNSUPPORTED_CAPABILITY", capability.value)
        status = self.health_check()
        if status.health != SensorHealth.HEALTHY:
            code = "MANUAL_STEP_REQUIRED" if status.health == SensorHealth.MANUAL_STEP_REQUIRED else "EXECUTABLE_MISSING"
            return self._failure(capability, scope, code, status.failure_reason or status.health.value)
        exe = self.detect()[tool_key]
        commands = {
            "findlinks": [str(exe), "-nobanner", scope],
            "junction": [str(exe), "-nobanner", scope],
            "handle": [str(exe), "-nobanner", scope],
            "sigcheck": [str(exe), "-nobanner", "-q", "-a", "-h", scope],
        }
        result = self.runner.run(commands[tool_key], timeout=float(kwargs.get("timeout", 20)))
        metrics = self._metrics(result)
        if not result.ok:
            return self._failure(capability, scope, "COMMAND_FAILED", result.stderr.strip() or result.exception or f"{tool_key} failed", metrics=metrics)
        raw_ref = self._write_raw(f"sysinternals_{tool_key}", result.stdout)
        return self._success(SensorObservation.data(self.sensor_id, capability, scope, self.normalize(result.stdout, capability, scope=scope), raw_reference=raw_ref, confidence=0.9, metrics=metrics))

    def normalize(self, raw: Any, capability: Capability, *, scope: str) -> Any:
        if not isinstance(raw, str):
            raise ValueError("Sysinternals output must be text")
        return {"scope": scope, "lines": [line for line in raw.splitlines() if line.strip()][:1000]}


