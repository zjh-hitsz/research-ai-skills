from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(slots=True)
class StorageSnapshotPolicy:
    cadence: timedelta = timedelta(days=7)

    def should_run(
        self,
        *,
        reason: str,
        last_success: datetime | None,
        now: datetime | None = None,
    ) -> bool:
        current = now or datetime.now(timezone.utc)
        if reason in {"FIRST_BASELINE", "MAJOR_GROWTH", "USER_REQUEST"}:
            return True
        if reason != "PERIODIC":
            return False
        if last_success is None:
            return True
        if last_success.tzinfo is None:
            last_success = last_success.replace(tzinfo=timezone.utc)
        return current - last_success >= self.cadence


SEMANTIC_EVENT_TYPES = {
    "STORAGE_GROWTH",
    "STORAGE_SHRINK",
    "AGGREGATE_GROWTH",
    "SOFTWARE_INSTALLED",
    "SOFTWARE_REMOVED",
    "SOFTWARE_UPDATED",
    "GIT_COMMIT",
    "GIT_DIRTY_CHANGE",
    "DISK_HEALTH_WARNING",
}


def timeline_event_allowed(event_type: str, *, package_level: bool = False) -> bool:
    if package_level:
        return False
    return event_type in SEMANTIC_EVENT_TYPES


