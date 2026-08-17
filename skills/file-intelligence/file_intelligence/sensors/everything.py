from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ..intelligence_models import Capability, SensorHealth, SensorObservation
from .base import BaseSensor
from .locator import first_executable, toolbox_hint


class EverythingSensor(BaseSensor):
    sensor_id = "everything"
    display_name = "Everything / ES"

    def detect(self) -> dict[str, Any]:
        configured = self.config.get("executables", {})
        toolbox = toolbox_hint(self.config, "everything")
        everything = first_executable(
            [configured.get("everything"), toolbox],
            ["Everything.exe"],
        )
        es = first_executable([configured.get("es"), toolbox], ["es.exe"])
        return {"everything": everything, "es": es}

    def capabilities(self) -> set[Capability]:
        return {Capability.FILE_SEARCH, Capability.FILE_METADATA}

    def _service_state(self) -> dict[str, Any]:
        if os.name != "nt":
            return {"installed": False, "running": False}
        result = self.runner.run(["sc.exe", "query", "Everything"], timeout=8)
        text = f"{result.stdout}\n{result.stderr}".upper()
        return {
            "installed": result.return_code == 0,
            "running": "STATE" in text and "RUNNING" in text,
            "return_code": result.return_code,
        }

    def version(self) -> str | None:
        es = self.detect()["es"]
        if not es:
            return None
        result = self.runner.run([str(es), "-version"], timeout=8)
        return result.stdout.strip().splitlines()[0] if result.ok and result.stdout.strip() else None

    def source(self) -> dict[str, str]:
        return {
            "vendor": "voidtools",
            "official_url": "https://www.voidtools.com/downloads/",
            "license_url": "https://www.voidtools.com/License.txt",
        }

    def health_check(self):
        found = self.detect()
        service = self._service_state()
        last_known_good = self.config.get("sensors", {}).get("everything", {}).get("last_known_good")
        es = found["es"]
        if not es:
            return self._status(
                version=None,
                available=False,
                health=SensorHealth.UNAVAILABLE,
                executable_path=None,
                failure_reason="ES.exe not found",
                details={"service": service, "query_usable": False, "last_known_good": last_known_good},
            )
        query = self.runner.run([str(es), "-get-everything-version"], timeout=8)
        query_usable = query.ok and bool(query.stdout.strip())
        health = SensorHealth.HEALTHY if query_usable else SensorHealth.SENSOR_ERROR
        reason = None if query_usable else (query.stderr.strip() or query.exception or "Everything IPC query failed")
        return self._status(
            version=self.version(),
            available=True,
            health=health,
            executable_path=str(es),
            failure_reason=reason,
            details={
                "service": service,
                "service_status": "SERVICE_RUNNING" if service["running"] else "SERVICE_NOT_RUNNING",
                "query_usable": query_usable,
                "query_status": "QUERY_USABLE" if query_usable else "SENSOR_FAILURE",
                "metrics": self._metrics(query),
                "last_known_good": last_known_good if not query_usable else None,
            },
        )

    def collect(self, capability: Capability, *, scope: str, **kwargs: Any) -> SensorObservation:
        if capability not in self.capabilities():
            return self._failure(capability, scope, "UNSUPPORTED_CAPABILITY", capability.value)
        es = self.detect()["es"]
        if not es:
            return self._failure(capability, scope, "EXECUTABLE_MISSING", "ES.exe not found")
        search = str(kwargs.get("search", ""))
        limit = max(1, min(int(kwargs.get("limit", 1000)), 10000))
        with tempfile.TemporaryDirectory(prefix="computer_intel_es_") as temp:
            export_path = Path(temp) / "results.json"
            argv = [
                str(es),
                "-full-path-and-name",
                "-size",
                "-date-modified",
                "-attributes",
                "-size-format",
                "1",
                "-date-format",
                "1",
                "-no-digit-grouping",
                "-utf8-bom",
                "-timeout",
                str(int(kwargs.get("ipc_timeout_ms", 5000))),
                "-n",
                str(limit),
                "-path",
                scope,
                "-export-json",
                str(export_path),
                "/a-d",
            ]
            if search:
                argv.append(search)
            result = self.runner.run(argv, timeout=float(kwargs.get("timeout", 15)))
            raw = export_path.read_bytes() if export_path.is_file() else b""
        metrics = self._metrics(result)
        if not result.ok:
            return self._failure(
                capability,
                scope,
                "IPC_UNAVAILABLE" if result.return_code == 8 else ("TIMEOUT" if result.timed_out else "COMMAND_FAILED"),
                result.stderr.strip() or result.exception or f"ES exited {result.return_code}",
                metrics=metrics,
            )
        try:
            parsed = json.loads(raw.decode("utf-8-sig")) if raw.strip() else []
            normalized = self.normalize(parsed, capability, scope=scope)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            return self._failure(capability, scope, "INVALID_OUTPUT", str(exc), metrics=metrics)
        raw_ref = self._write_raw("everything", raw, ".json") if raw else None
        return self._success(
            SensorObservation.data(
                self.sensor_id,
                capability,
                scope,
                normalized,
                confidence=1.0,
                raw_reference=raw_ref,
                metrics=metrics,
            )
        )

    def normalize(self, raw: Any, capability: Capability, *, scope: str) -> list[dict[str, Any]]:
        if not isinstance(raw, list):
            raise ValueError("Everything JSON root must be an array")
        rows: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            raw_path = item.get("filename") or item.get("Filename") or item.get("full-path-and-name")
            if not raw_path:
                continue
            raw_size = str(item.get("size") or item.get("Size") or "0").replace(",", "")
            try:
                size = int(raw_size)
            except ValueError:
                size = None
            rows.append(
                {
                    "path": str(Path(str(raw_path).rstrip("\\/"))),
                    "logical_size": size,
                    "modified": item.get("date_modified") or item.get("date-modified") or item.get("Date Modified"),
                    "attributes": item.get("attributes") or item.get("Attributes") or "",
                }
            )
        return rows

