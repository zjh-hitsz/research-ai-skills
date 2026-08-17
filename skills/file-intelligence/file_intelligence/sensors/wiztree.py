from __future__ import annotations

import csv
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from ..intelligence_models import Capability, SensorHealth, SensorObservation
from .base import BaseSensor
from .locator import first_executable, toolbox_hint


class WizTreeSensor(BaseSensor):
    sensor_id = "wiztree"
    display_name = "WizTree Storage Sensor"

    def detect(self) -> dict[str, Any]:
        configured = self.config.get("executables", {}).get("wiztree")
        toolbox = toolbox_hint(self.config, "wiztree")
        exe = first_executable([configured, toolbox], ["WizTree64.exe", "WizTree.exe"])
        return {"executable": exe}

    def capabilities(self) -> set[Capability]:
        return {Capability.STORAGE_TREE, Capability.ALLOCATED_SIZE}

    def _license_accepted(self) -> bool:
        return bool(self.config.get("sensors", {}).get("wiztree", {}).get("license_accepted", False))

    def version(self) -> str | None:
        value = self.config.get("tool_versions", {}).get("wiztree")
        return str(value) if value else None

    def source(self) -> dict[str, str]:
        return {
            "vendor": "Antibody Software",
            "official_url": "https://diskanalyzer.com/download",
            "license_url": "https://diskanalyzer.com/eula",
        }

    def health_check(self):
        exe = self.detect()["executable"]
        if not exe:
            return self._status(
                version=self.version(),
                available=False,
                health=SensorHealth.MANUAL_STEP_REQUIRED,
                executable_path=None,
                failure_reason="Portable archive may be downloaded, but EULA acceptance must be completed by the user before deployment",
                details={"license_accepted": False},
            )
        if not self._license_accepted():
            return self._status(
                version=self.version(),
                available=True,
                health=SensorHealth.MANUAL_STEP_REQUIRED,
                executable_path=str(exe),
                failure_reason="WizTree EULA has not been explicitly accepted in local sensor configuration",
                details={"license_accepted": False},
            )
        return self._status(
            version=self.version(),
            available=True,
            health=SensorHealth.HEALTHY,
            executable_path=str(exe),
            details={"license_accepted": True, "default_admin_mode": False},
        )

    @staticmethod
    def _is_drive_root(scope: Path) -> bool:
        try:
            return scope.resolve() == Path(scope.anchor).resolve()
        except OSError:
            return False

    def collect(self, capability: Capability, *, scope: str, **kwargs: Any) -> SensorObservation:
        if capability not in self.capabilities():
            return self._failure(capability, scope, "UNSUPPORTED_CAPABILITY", capability.value)
        status = self.health_check()
        if status.health == SensorHealth.MANUAL_STEP_REQUIRED:
            return self._failure(capability, scope, "MANUAL_STEP_REQUIRED", status.failure_reason or "WizTree license step required")
        target = Path(scope)
        if not target.exists():
            return self._failure(capability, scope, "SCOPE_MISSING", "Storage scope does not exist")
        if self._is_drive_root(target) and not kwargs.get("allow_drive_scan", False):
            return self._failure(capability, scope, "FULL_DRIVE_SCAN_BLOCKED", "Drive-root scan requires explicit allow_drive_scan")
        exe = Path(status.executable_path or "")
        with tempfile.TemporaryDirectory(prefix="computer_intel_wiztree_") as temp:
            csv_path = Path(temp) / "storage.csv"
            argv = [
                str(exe),
                str(target),
                f'/export="{csv_path}"',
                "/admin=0",
                "/exportdrivecapacity=1",
                "/exportalldates=1",
                "/sortby=2",
            ]
            max_depth = kwargs.get("max_depth")
            if max_depth is not None:
                argv.append(f"/exportmaxdepth={int(max_depth)}")
            result = self.runner.run(argv, timeout=float(kwargs.get("timeout", 120)))
            metrics = self._metrics(result)
            if not result.ok or not csv_path.is_file():
                return self._failure(
                    capability,
                    scope,
                    "TIMEOUT" if result.timed_out else "COMMAND_FAILED",
                    result.stderr.strip() or result.exception or "WizTree CSV export failed",
                    metrics=metrics,
                )
            try:
                rows = self._read_csv(csv_path, max_rows=int(kwargs.get("max_rows", 500000)))
                normalized = self.normalize(rows, capability, scope=scope)
            except (OSError, UnicodeError, csv.Error, ValueError) as exc:
                return self._failure(capability, scope, "MALFORMED_EXPORT", str(exc), metrics=metrics)
            raw_ref = None
            if self.raw_dir:
                self.raw_dir.mkdir(parents=True, exist_ok=True)
                raw_target = self.raw_dir / f"wiztree_{int(csv_path.stat().st_mtime_ns)}.csv"
                shutil.copy2(csv_path, raw_target)
                raw_ref = str(raw_target)
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

    @staticmethod
    def _read_csv(path: Path, max_rows: int) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            required = {"File Name", "Size", "Allocated"}
            if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
                raise ValueError(f"Missing required WizTree columns: {sorted(required)}")
            for row in reader:
                rows.append(dict(row))
                if len(rows) >= max_rows:
                    break
        return rows

    def normalize(self, raw: Any, capability: Capability, *, scope: str) -> dict[str, Any]:
        if not isinstance(raw, list):
            raise ValueError("WizTree rows must be a list")
        normalized: list[dict[str, Any]] = []
        for row in raw:
            if not isinstance(row, dict) or not row.get("File Name"):
                continue
            try:
                logical = int(str(row.get("Size") or "0").replace(",", ""))
                allocated_text = str(row.get("Allocated") or "0").replace(",", "")
                allocated = int(allocated_text)
            except ValueError as exc:
                raise ValueError(f"Invalid size value for {row.get('File Name')}: {exc}") from exc
            normalized.append(
                {
                    "path": row["File Name"].rstrip("\\"),
                    "logical_size": logical,
                    "allocated_size": allocated,
                    "file_count": int(row.get("Files") or 0),
                    "directory_count": int(row.get("Folders") or 0),
                    "modified": row.get("Modified"),
                    "hardlink_non_additional_allocation": allocated_text.startswith("0") and len(allocated_text) > 1,
                }
            )
        root = normalized[0] if normalized else None
        return {
            "path": str(Path(scope).resolve()),
            "logical_size": root.get("logical_size") if root else 0,
            "allocated_size": root.get("allocated_size") if root else 0,
            "file_count": root.get("file_count") if root else 0,
            "directory_count": root.get("directory_count") if root else 0,
            "largest_children": sorted(normalized[1:], key=lambda item: item["allocated_size"], reverse=True)[:20],
            "entry_count": len(normalized),
            "complete": True,
        }


