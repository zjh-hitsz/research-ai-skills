from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..intelligence_models import Capability, SensorHealth, SensorObservation
from .base import BaseSensor
from .locator import first_executable


class WingetSensor(BaseSensor):
    sensor_id = "winget"
    display_name = "Windows Package Manager Sensor"

    def detect(self) -> dict[str, Any]:
        configured = self.config.get("executables", {}).get("winget")
        exe = first_executable([configured], ["winget.exe", "winget"])
        return {"executable": exe}

    def capabilities(self) -> set[Capability]:
        return {Capability.SOFTWARE_INVENTORY}

    def version(self) -> str | None:
        exe = self.detect()["executable"]
        if not exe:
            return None
        result = self.runner.run([str(exe), "--version"], timeout=10)
        return result.stdout.strip() if result.ok else None

    def source(self) -> dict[str, str]:
        return {"vendor": "Microsoft", "official_url": "https://learn.microsoft.com/windows/package-manager/winget/"}

    def health_check(self):
        exe = self.detect()["executable"]
        if not exe:
            return self._status(version=None, available=False, health=SensorHealth.UNAVAILABLE, executable_path=None, failure_reason="WinGet is not available; use Microsoft App Installer")
        result = self.runner.run([str(exe), "--version"], timeout=10)
        return self._status(
            version=result.stdout.strip() if result.ok else None,
            available=result.ok,
            health=SensorHealth.HEALTHY if result.ok else SensorHealth.UNAVAILABLE,
            executable_path=str(exe),
            failure_reason=None if result.ok else (result.stderr.strip() or result.exception or "App execution alias is unusable"),
            details={"metrics": self._metrics(result)},
        )

    def collect(self, capability: Capability, *, scope: str, **kwargs: Any) -> SensorObservation:
        if capability != Capability.SOFTWARE_INVENTORY:
            return self._failure(capability, scope, "UNSUPPORTED_CAPABILITY", capability.value)
        exe = self.detect()["executable"]
        if not exe:
            return self._failure(capability, scope, "EXECUTABLE_MISSING", "WinGet is unavailable")
        # list is read-only. No agreement-accepting, install, upgrade, repair or uninstall flags are permitted.
        result = self.runner.run([str(exe), "list", "--disable-interactivity"], timeout=float(kwargs.get("timeout", 60)))
        metrics = self._metrics(result)
        if not result.ok:
            text = f"{result.stdout}\n{result.stderr}"
            code = "MANUAL_STEP_REQUIRED" if "agreement" in text.casefold() else "COMMAND_FAILED"
            return self._failure(capability, scope, code, result.stderr.strip() or result.exception or "winget list failed", metrics=metrics)
        raw_ref = self._write_raw("winget_list", result.stdout)
        return self._success(SensorObservation.data(self.sensor_id, capability, scope, self.normalize(result.stdout, capability, scope=scope), raw_reference=raw_ref, confidence=0.8, metrics=metrics))

    def normalize(self, raw: Any, capability: Capability, *, scope: str) -> Any:
        # WinGet's table is locale-dependent. Preserve bounded raw lines and let Windows native inventory provide stable fields.
        if not isinstance(raw, str):
            raise ValueError("WinGet output must be text")
        return {"format": "winget_localized_table", "line_count": len(raw.splitlines())}


