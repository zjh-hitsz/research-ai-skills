from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..intelligence_models import Capability, DiskCard, SensorHealth, SensorObservation
from .base import BaseSensor
from .locator import first_executable, toolbox_hint


class SmartctlSensor(BaseSensor):
    sensor_id = "smartctl"
    display_name = "smartmontools Disk Health Sensor"

    def detect(self) -> dict[str, Any]:
        configured = self.config.get("executables", {}).get("smartctl")
        toolbox = toolbox_hint(self.config, "smartmontools")
        exe = first_executable([configured, toolbox], ["smartctl.exe", "smartctl"])
        return {"executable": exe}

    def capabilities(self) -> set[Capability]:
        return {Capability.DISK_HEALTH}

    def version(self) -> str | None:
        exe = self.detect()["executable"]
        if not exe:
            return None
        result = self.runner.run([str(exe), "--version"], timeout=10)
        return result.stdout.splitlines()[0].strip() if result.ok and result.stdout else None

    def source(self) -> dict[str, str]:
        return {
            "vendor": "smartmontools",
            "official_url": "https://www.smartmontools.org/",
            "official_repository": "https://github.com/smartmontools/smartmontools",
        }

    def health_check(self):
        exe = self.detect()["executable"]
        if not exe:
            return self._status(version=None, available=False, health=SensorHealth.UNAVAILABLE, executable_path=None, failure_reason="smartctl not found")
        result = self.runner.run([str(exe), "--version"], timeout=10)
        return self._status(
            version=self.version(),
            available=result.ok,
            health=SensorHealth.HEALTHY if result.ok else SensorHealth.SENSOR_ERROR,
            executable_path=str(exe),
            failure_reason=None if result.ok else (result.stderr.strip() or result.exception),
            details={"read_only": True, "self_tests_enabled": False, "metrics": self._metrics(result)},
        )

    @staticmethod
    def _permission_denied(text: str) -> bool:
        lowered = text.casefold()
        return any(token in lowered for token in ("permission denied", "access is denied", "administrator rights", "requires admin"))

    def collect(self, capability: Capability, *, scope: str, **kwargs: Any) -> SensorObservation:
        if capability != Capability.DISK_HEALTH:
            return self._failure(capability, scope, "UNSUPPORTED_CAPABILITY", capability.value)
        exe = self.detect()["executable"]
        if not exe:
            return self._failure(capability, scope, "EXECUTABLE_MISSING", "smartctl not found")
        scan = self.runner.run([str(exe), "--scan", "--json=o"], timeout=20)
        metrics: dict[str, Any] = {"scan": self._metrics(scan)}
        if not scan.ok:
            text = f"{scan.stdout}\n{scan.stderr}"
            code = "ADMIN_REQUIRED" if self._permission_denied(text) else "COMMAND_FAILED"
            return self._failure(capability, scope, code, scan.stderr.strip() or scan.exception or "smartctl scan failed", metrics=metrics)
        try:
            scan_json = json.loads(scan.stdout)
        except json.JSONDecodeError as exc:
            return self._failure(capability, scope, "INVALID_OUTPUT", str(exc), metrics=metrics)
        devices = scan_json.get("devices", []) if isinstance(scan_json, dict) else []
        cards: list[dict[str, Any]] = []
        for device in devices:
            name = device.get("name") if isinstance(device, dict) else None
            if not name:
                continue
            argv = [str(exe), "-a", "--json=o"]
            device_type = device.get("type") if isinstance(device, dict) else None
            if device_type:
                argv.extend(["-d", str(device_type)])
            argv.append(str(name))
            result = self.runner.run(argv, timeout=30)
            metrics[str(name)] = self._metrics(result)
            text = f"{result.stdout}\n{result.stderr}"
            if self._permission_denied(text):
                return self._failure(capability, scope, "ADMIN_REQUIRED", f"Administrator rights required for {name}", metrics=metrics)
            try:
                payload = json.loads(result.stdout)
            except json.JSONDecodeError:
                continue
            cards.append(self.normalize(payload, capability, scope=str(name)))
        return self._success(SensorObservation.data(self.sensor_id, capability, scope, cards, confidence=1.0, metrics=metrics))

    def normalize(self, raw: Any, capability: Capability, *, scope: str) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ValueError("smartctl JSON must be an object")
        smart = raw.get("smart_status") or {}
        passed = smart.get("passed")
        nvme = raw.get("nvme_smart_health_information_log") or {}
        temperature = (raw.get("temperature") or {}).get("current")
        if temperature is None:
            temperature = nvme.get("temperature")
        warnings: list[str] = []
        critical = nvme.get("critical_warning")
        if critical not in (None, 0, "0"):
            warnings.append(f"NVMe critical_warning={critical}")
        messages = raw.get("smartctl", {}).get("messages", [])
        for item in messages:
            if isinstance(item, dict) and item.get("string"):
                warnings.append(str(item["string"]))
        wear = nvme.get("percentage_used")
        card = DiskCard(
            device=str((raw.get("device") or {}).get("name") or scope),
            model=raw.get("model_name") or raw.get("product"),
            interface=(raw.get("device") or {}).get("protocol") or raw.get("interface_speed", {}).get("current", {}).get("string"),
            capacity=(raw.get("user_capacity") or {}).get("bytes"),
            health="PASS" if passed is True else ("FAIL" if passed is False else "UNKNOWN"),
            temperature=float(temperature) if isinstance(temperature, (int, float)) else None,
            wear_life=(100.0 - float(wear)) if isinstance(wear, (int, float)) else None,
            smart_warnings=warnings,
            sensor_source=self.sensor_id,
        )
        return {name: getattr(card, name) for name in card.__dataclass_fields__}

