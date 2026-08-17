from __future__ import annotations

import os
import tarfile
import time
import zipfile
from pathlib import Path
from typing import Any

from ..intelligence_models import ArchiveCard, Capability, SensorHealth, SensorObservation
from .base import BaseSensor


class PythonArchiveSensor(BaseSensor):
    sensor_id = "python_archive"
    display_name = "Python ZIP/TAR Archive Fallback"

    def detect(self) -> dict[str, Any]:
        return {"available": True, "formats": ["zip", "tar", "tar.gz", "tar.bz2", "tar.xz"]}

    def capabilities(self) -> set[Capability]:
        return {Capability.ARCHIVE_INSPECTION}

    def health_check(self):
        return self._status(
            version=self.version(),
            available=True,
            health=SensorHealth.HEALTHY,
            executable_path=None,
            details={"read_only": True, "extract_operation_exposed": False},
        )

    def version(self) -> str | None:
        return None

    def source(self) -> dict[str, str]:
        return {"vendor": "Python standard library", "official_url": "https://docs.python.org/3/library/archiving.html"}

    def collect(self, capability: Capability, *, scope: str, **kwargs: Any) -> SensorObservation:
        if capability != Capability.ARCHIVE_INSPECTION:
            return self._failure(capability, scope, "UNSUPPORTED_CAPABILITY", capability.value)
        archive = Path(scope)
        if not archive.is_file():
            return self._failure(capability, scope, "SCOPE_MISSING", "Archive does not exist")
        started = time.perf_counter()
        limit = max(1, min(int(kwargs.get("limit", 1000)), 10000))
        try:
            if zipfile.is_zipfile(archive):
                members, total, unpacked, types = self._zip_members(archive, limit)
            elif tarfile.is_tarfile(archive):
                members, total, unpacked, types = self._tar_members(archive, limit)
            else:
                return self._failure(
                    capability,
                    scope,
                    "UNSUPPORTED_ARCHIVE_FORMAT",
                    "Standard-library fallback supports ZIP and TAR families only",
                )
        except (OSError, zipfile.BadZipFile, tarfile.TarError, EOFError) as exc:
            return self._failure(capability, scope, "CORRUPT_ARCHIVE", str(exc))
        card = ArchiveCard(
            archive_id=f"archive:{os.path.normcase(str(archive.resolve()))}",
            path=str(archive.resolve()),
            compressed_size=archive.stat().st_size,
            unpacked_size=unpacked,
            member_count=total,
            member_types=types,
            source_sensor=self.sensor_id,
        )
        data = self.normalize({"card": card, "members": members}, capability, scope=scope)
        return self._success(
            SensorObservation.data(
                self.sensor_id,
                capability,
                scope,
                data,
                confidence=0.95,
                metrics={
                    "startup_and_query_seconds": time.perf_counter() - started,
                    "peak_memory_bytes": None,
                    "output_bytes": 0,
                    "member_count": total,
                },
            )
        )

    @staticmethod
    def _zip_members(path: Path, limit: int) -> tuple[list[dict[str, Any]], int, int, dict[str, int]]:
        rows: list[dict[str, Any]] = []
        types: dict[str, int] = {}
        total = 0
        unpacked = 0
        with zipfile.ZipFile(path, "r") as handle:
            for info in handle.infolist():
                if info.is_dir():
                    continue
                total += 1
                unpacked += info.file_size
                suffix = Path(info.filename).suffix.casefold() or "<none>"
                types[suffix] = types.get(suffix, 0) + 1
                if len(rows) < limit:
                    rows.append(
                        {
                            "path": info.filename,
                            "size": info.file_size,
                            "compressed_size": info.compress_size,
                            "crc32": f"{info.CRC:08X}",
                        }
                    )
        return rows, total, unpacked, types

    @staticmethod
    def _tar_members(path: Path, limit: int) -> tuple[list[dict[str, Any]], int, int, dict[str, int]]:
        rows: list[dict[str, Any]] = []
        types: dict[str, int] = {}
        total = 0
        unpacked = 0
        with tarfile.open(path, "r:*") as handle:
            for member in handle:
                if not member.isfile():
                    continue
                total += 1
                unpacked += member.size
                suffix = Path(member.name).suffix.casefold() or "<none>"
                types[suffix] = types.get(suffix, 0) + 1
                if len(rows) < limit:
                    rows.append({"path": member.name, "size": member.size, "modified": member.mtime})
        return rows, total, unpacked, types

    def normalize(self, raw: Any, capability: Capability, *, scope: str) -> dict[str, Any]:
        if not isinstance(raw, dict) or not isinstance(raw.get("card"), ArchiveCard):
            raise ValueError("archive fallback payload is invalid")
        card = raw["card"]
        return {
            "card": {name: getattr(card, name) for name in card.__dataclass_fields__},
            "members": raw.get("members", []),
        }

