from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_ready(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return json_ready(asdict(value))
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_ready(item) for item in value]
    return value


class Capability(str, Enum):
    FILE_SEARCH = "FILE_SEARCH"
    FILE_METADATA = "FILE_METADATA"
    STORAGE_TREE = "STORAGE_TREE"
    STORAGE_USAGE = "STORAGE_USAGE"
    ALLOCATED_SIZE = "ALLOCATED_SIZE"
    ARCHIVE_INSPECTION = "ARCHIVE_INSPECTION"
    HARDLINK_RESOLUTION = "HARDLINK_RESOLUTION"
    JUNCTION_RESOLUTION = "JUNCTION_RESOLUTION"
    FILE_HANDLE_OWNER = "FILE_HANDLE_OWNER"
    FILE_SIGNATURE = "FILE_SIGNATURE"
    SOFTWARE_INVENTORY = "SOFTWARE_INVENTORY"
    SYSTEM_INFO = "SYSTEM_INFO"
    VOLUME_INFO = "VOLUME_INFO"
    GPU_STATUS = "GPU_STATUS"
    GIT_STATUS = "GIT_STATUS"
    GITHUB_STATUS = "GITHUB_STATUS"
    DISK_HEALTH = "DISK_HEALTH"
    PROCESS_ACTIVITY = "PROCESS_ACTIVITY"
    FILE_ACTIVITY_TRACE = "FILE_ACTIVITY_TRACE"


class SensorHealth(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    SENSOR_ERROR = "SENSOR_ERROR"
    ADMIN_REQUIRED = "ADMIN_REQUIRED"
    AUTH_UNAVAILABLE = "AUTH_UNAVAILABLE"
    DISABLED = "DISABLED"
    MANUAL_STEP_REQUIRED = "MANUAL_STEP_REQUIRED"
    UNKNOWN = "UNKNOWN"


class ObservationOutcome(str, Enum):
    DATA = "DATA"
    NO_DATA = "NO_DATA"
    SENSOR_ERROR = "SENSOR_ERROR"


class CacheStatus(str, Enum):
    LIVE = "LIVE"
    LAST_KNOWN_GOOD = "LAST_KNOWN_GOOD"
    STALE = "STALE"
    NONE = "NONE"


@dataclass(slots=True)
class SensorStatus:
    sensor_id: str
    name: str
    version: str | None
    available: bool
    health: SensorHealth
    executable_path: str | None
    capabilities: list[str]
    last_success: str | None = None
    last_failure: str | None = None
    failure_reason: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))


@dataclass(slots=True)
class SensorObservation:
    sensor_id: str
    capability: str
    scope: str
    subject_id: str | None
    normalized_data: Any
    confidence: float
    outcome: ObservationOutcome
    observation_id: str = field(default_factory=lambda: f"obs_{uuid4().hex}")
    timestamp: str = field(default_factory=utc_now)
    raw_reference: str | None = None
    error: dict[str, Any] | None = None
    cache_status: CacheStatus = CacheStatus.LIVE
    metrics: dict[str, Any] = field(default_factory=dict)
    upstream_errors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return json_ready(asdict(self))

    @classmethod
    def data(
        cls,
        sensor_id: str,
        capability: Capability | str,
        scope: str,
        normalized_data: Any,
        *,
        subject_id: str | None = None,
        confidence: float = 1.0,
        raw_reference: str | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> "SensorObservation":
        outcome = ObservationOutcome.NO_DATA if normalized_data in (None, [], {}) else ObservationOutcome.DATA
        return cls(
            sensor_id=sensor_id,
            capability=str(getattr(capability, "value", capability)),
            scope=scope,
            subject_id=subject_id,
            normalized_data=normalized_data,
            confidence=confidence,
            outcome=outcome,
            raw_reference=raw_reference,
            metrics=metrics or {},
        )

    @classmethod
    def failure(
        cls,
        sensor_id: str,
        capability: Capability | str,
        scope: str,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        last_known_good: Any = None,
        metrics: dict[str, Any] | None = None,
    ) -> "SensorObservation":
        return cls(
            sensor_id=sensor_id,
            capability=str(getattr(capability, "value", capability)),
            scope=scope,
            subject_id=None,
            normalized_data=last_known_good,
            confidence=0.5 if last_known_good is not None else 0.0,
            outcome=ObservationOutcome.SENSOR_ERROR,
            error={"code": code, "message": message, "details": details or {}},
            cache_status=CacheStatus.LAST_KNOWN_GOOD if last_known_good is not None else CacheStatus.NONE,
            metrics=metrics or {},
        )


@dataclass(slots=True)
class StorageEntry:
    path: str
    logical_size: int | None
    allocated_size: int | None
    file_count: int
    directory_count: int
    largest_children: list[dict[str, Any]] = field(default_factory=list)
    complete: bool = True


@dataclass(slots=True)
class StorageSnapshot:
    timestamp: str
    drive: str
    total: int | None
    used: int | None
    free: int | None
    entries: list[StorageEntry] = field(default_factory=list)
    source_sensor: str | None = None


@dataclass(slots=True)
class AggregateAsset:
    aggregate_id: str
    path: str
    project_id: str | None
    role: str
    logical_size: int | None
    allocated_size: int | None
    file_count: int
    last_modified: str | None
    internal_indexing: str
    rebuildable: bool | None
    confidence: float
    evidence: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class ArchiveCard:
    archive_id: str
    path: str
    compressed_size: int | None
    unpacked_size: int | None
    member_count: int
    member_types: dict[str, int]
    contains_models: bool | None = None
    contains_reports: bool | None = None
    contains_scripts: bool | None = None
    contains_manifest: bool | None = None
    likely_role: str = "UNKNOWN"
    source_sensor: str | None = None


@dataclass(slots=True)
class GitRepoCard:
    repo_path: str
    project: str | None
    branch: str | None
    head: str | None
    dirty: bool
    modified_count: int
    untracked_count: int
    recent_commits: list[dict[str, Any]]
    remote: str | None
    last_activity: str | None
    github: dict[str, Any] | None = None


@dataclass(slots=True)
class SoftwareAsset:
    name: str
    version: str | None
    publisher: str | None
    install_source: str | None
    install_location: str | None
    detected_at: str = field(default_factory=utc_now)


@dataclass(slots=True)
class DiskCard:
    device: str
    model: str | None
    interface: str | None
    capacity: int | None
    health: str
    temperature: float | None
    wear_life: float | None
    smart_warnings: list[str]
    sensor_source: str
    timestamp: str = field(default_factory=utc_now)


@dataclass(slots=True)
class ComputerTimelineEvent:
    event_type: str
    timestamp: str
    subject_id: str
    summary: str
    evidence: list[dict[str, Any]]
    noise_key: str | None = None


@dataclass(slots=True)
class SemanticDiff:
    raw_summary: str
    semantic_summary: str
    canonical_evidence_changed: bool
    supporting_observations: list[str]


@dataclass(slots=True)
class FileSensorLinks:
    file_id: str
    logical_size: int | None = None
    allocated_size: int | None = None
    hardlink_info: dict[str, Any] | None = None
    archive_membership: list[str] = field(default_factory=list)
    process_owner: list[dict[str, Any]] = field(default_factory=list)
    git_status: dict[str, Any] | None = None
    storage_class: str | None = None
    disk_device: str | None = None
    external_software_producer: dict[str, Any] | None = None


@dataclass(slots=True)
class ProjectSensorSummary:
    project_id: str
    disk_usage: int | None
    allocated_usage: int | None
    growth_since_last_snapshot: int | None
    aggregate_assets: list[AggregateAsset] = field(default_factory=list)
    git_repositories: list[GitRepoCard] = field(default_factory=list)
    software_dependencies: list[SoftwareAsset] = field(default_factory=list)
    archive_packages: list[ArchiveCard] = field(default_factory=list)
    recent_semantic_changes: list[SemanticDiff] = field(default_factory=list)

