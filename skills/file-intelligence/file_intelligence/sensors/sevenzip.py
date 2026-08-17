from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..intelligence_models import ArchiveCard, Capability, SensorHealth, SensorObservation
from .base import BaseSensor
from .locator import first_executable, toolbox_hint


class SevenZipSensor(BaseSensor):
    sensor_id = "sevenzip"
    display_name = "7-Zip"

    def detect(self) -> dict[str, Any]:
        configured = self.config.get("executables", {}).get("sevenzip")
        toolbox = toolbox_hint(self.config, "7zip")
        exe = first_executable(
            [
                configured,
                toolbox,
                Path(os.environ.get("ProgramFiles") or (os.environ.get("SystemDrive", "C:") + os.sep + "Program Files")) / "7-Zip",
            ],
            ["7z.exe", "7zz.exe", "7za.exe"],
        )
        return {"executable": exe}

    def capabilities(self) -> set[Capability]:
        return {Capability.ARCHIVE_INSPECTION}

    def version(self) -> str | None:
        exe = self.detect()["executable"]
        if not exe:
            return None
        result = self.runner.run([str(exe)], timeout=8)
        for line in f"{result.stdout}\n{result.stderr}".splitlines():
            if "7-Zip" in line:
                return line.strip()
        return None

    def source(self) -> dict[str, str]:
        return {
            "vendor": "Igor Pavlov / 7-Zip",
            "official_url": "https://www.7-zip.org/download.html",
            "license_url": "https://www.7-zip.org/license.txt",
        }

    def health_check(self):
        exe = self.detect()["executable"]
        if not exe:
            return self._status(
                version=None,
                available=False,
                health=SensorHealth.UNAVAILABLE,
                executable_path=None,
                failure_reason="7z/7zz/7za not found",
            )
        result = self.runner.run([str(exe)], timeout=8)
        return self._status(
            version=self.version(),
            available=True,
            health=SensorHealth.HEALTHY if result.return_code in (0, 7) and not result.timed_out else SensorHealth.SENSOR_ERROR,
            executable_path=str(exe),
            failure_reason=None if result.return_code in (0, 7) else (result.stderr.strip() or result.exception),
            details={"metrics": self._metrics(result)},
        )

    def collect(self, capability: Capability, *, scope: str, **kwargs: Any) -> SensorObservation:
        if capability != Capability.ARCHIVE_INSPECTION:
            return self._failure(capability, scope, "UNSUPPORTED_CAPABILITY", capability.value)
        archive = Path(scope)
        if not archive.is_file():
            return self._failure(capability, scope, "SCOPE_MISSING", "Archive does not exist")
        exe = self.detect()["executable"]
        if not exe:
            return self._failure(capability, scope, "EXECUTABLE_MISSING", "7-Zip CLI not found")
        result = self.runner.run([str(exe), "l", "-slt", "-ba", "-y", str(archive)], timeout=float(kwargs.get("timeout", 30)))
        metrics = self._metrics(result)
        if not result.ok:
            code = "CORRUPT_ARCHIVE" if result.return_code == 2 else ("TIMEOUT" if result.timed_out else "COMMAND_FAILED")
            return self._failure(capability, scope, code, result.stderr.strip() or result.exception or "7-Zip list failed", metrics=metrics)
        try:
            normalized = self.normalize(result.stdout, capability, scope=scope)
        except ValueError as exc:
            return self._failure(capability, scope, "INVALID_OUTPUT", str(exc), metrics=metrics)
        raw_ref = self._write_raw("sevenzip_list", result.stdout)
        return self._success(
            SensorObservation.data(
                self.sensor_id,
                capability,
                scope,
                normalized,
                raw_reference=raw_ref,
                confidence=1.0,
                metrics=metrics,
            )
        )

    def normalize(self, raw: Any, capability: Capability, *, scope: str) -> dict[str, Any]:
        if not isinstance(raw, str):
            raise ValueError("7-Zip output must be text")
        members: list[dict[str, str]] = []
        current: dict[str, str] = {}
        for line in raw.splitlines():
            if not line.strip():
                if current:
                    members.append(current)
                    current = {}
                continue
            if " = " in line:
                key, value = line.split(" = ", 1)
                current[key.strip()] = value.strip()
        if current:
            members.append(current)
        members = [item for item in members if item.get("Path")]
        extensions: dict[str, int] = {}
        unpacked = 0
        for item in members:
            suffix = Path(item["Path"]).suffix.casefold() or "<none>"
            extensions[suffix] = extensions.get(suffix, 0) + 1
            try:
                unpacked += int(item.get("Size", "0"))
            except ValueError:
                pass
        archive = Path(scope)
        card = ArchiveCard(
            archive_id=f"archive:{os.path.normcase(str(archive.resolve()))}",
            path=str(archive.resolve()),
            compressed_size=archive.stat().st_size,
            unpacked_size=unpacked,
            member_count=len(members),
            member_types=extensions,
            source_sensor=self.sensor_id,
        )
        return {"card": card.__dict__ if hasattr(card, "__dict__") else {name: getattr(card, name) for name in card.__dataclass_fields__}, "members": members[:1000]}

