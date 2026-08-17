from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .intelligence_models import SensorObservation, SensorStatus


AUTHORITY_ORDER = {
    "HEURISTIC": 10,
    "AI_INFERRED": 20,
    "SYSTEM_OBSERVED": 30,
    "CODE_EVIDENCE": 40,
    "DOCUMENT_EVIDENCE": 50,
    "HASH_EVIDENCE": 60,
    "EXPLICIT_METADATA": 70,
    "USER_ASSERTED": 80,
}

LEGACY_FILE_EVENTS = {
    "FIRST_SEEN",
    "CONTENT_CHANGED",
    "METADATA_ONLY_CHANGE",
    "MOVED",
    "RENAMED",
    "COPIED",
    "MISSING",
    "REAPPEARED",
}

SEMANTIC_FILE_FIELDS = {
    "file_type",
    "tier",
    "project_id",
    "workstream_id",
    "asset_role",
    "importance",
    "summary",
    "authority",
    "confidence",
    "last_changed",
    "logical_size",
    "allocated_size",
    "allocated_size_complete",
    "rebuildable",
    "rebuild_cost",
    "canonical_status",
    "supersedes_json",
    "superseded_by_json",
    "version_family",
    "references_json",
    "referenced_by_json",
    "duplicate_group",
    "archive_membership",
    "producer_evidence_json",
    "unresolved_questions_json",
    "source_signature_json",
    "content_identity",
    "content_identity_kind",
}

JSON_DEFAULTS = {
    "supersedes_json": "[]",
    "superseded_by_json": "[]",
    "references_json": "[]",
    "referenced_by_json": "[]",
    "producer_evidence_json": "[]",
    "sensor_links_json": "[]",
    "unresolved_questions_json": "[]",
    "source_signature_json": "{}",
}

CLEANUP_CLAIM_TYPES = {
    "EXACT_DUPLICATE",
    "REBUILDABLE",
    "SUPERSEDED_LINEAGE",
    "FAILED_PROVENANCE",
    "ARCHIVE_CANDIDATE",
    "STRUCTURAL_DOUBLE_WRITE",
    "PROTECTED_EVIDENCE",
}

CLEANUP_PATCH_FIELDS = {
    "asset_role",
    "duplicate_group",
    "canonical_status",
    "rebuildable",
    "superseded_by_json",
    "archive_recommendation",
}


class ReconciliationError(RuntimeError):
    pass


class ForeignStateError(ReconciliationError):
    pass


class SemanticConflictError(ReconciliationError):
    def __init__(self, conflicts: list[dict[str, Any]]) -> None:
        super().__init__(f"Legacy import has {len(conflicts)} unresolved semantic conflict(s)")
        self.conflicts = conflicts


@dataclass(frozen=True)
class ImportReport:
    import_id: str
    status: str
    source_schema: str
    source_sha256: str
    counts: dict[str, int]
    conflicts: list[dict[str, Any]]
    physical_file_actions: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "import_id": self.import_id,
            "status": self.status,
            "source_schema": self.source_schema,
            "source_sha256": self.source_sha256,
            "counts": dict(self.counts),
            "conflicts": list(self.conflicts),
            "physical_file_actions": self.physical_file_actions,
        }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list, int, float, bool)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _json_text(value: Any, default: Any) -> str:
    return canonical_json(_json(value, default))


def _normalize_path(value: str) -> str:
    return os.path.normcase(os.path.normpath(value)).casefold()


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _rows(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    if not _table_exists(connection, table):
        return []
    return [dict(row) for row in connection.execute(f'SELECT * FROM "{table}"')]


def _source_schema(connection: sqlite3.Connection) -> str:
    if _table_exists(connection, "schema_meta"):
        row = connection.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
        if row:
            return str(row[0])
    return "unknown"


def inspect_legacy_card_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    uri = f"file:{path.as_posix()}?mode=ro&immutable=1"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        schema = _source_schema(connection)
        tables = {
            str(row[0]): int(connection.execute(f'SELECT COUNT(*) FROM "{row[0]}"').fetchone()[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        }
    return {
        "path": str(path),
        "sha256": file_sha256(path),
        "bytes": path.stat().st_size,
        "quick_check": quick_check,
        "schema": schema,
        "supported": schema in {"1.0.0", "2.0.1"},
        "tables": tables,
    }


def _target_binding(connection: sqlite3.Connection) -> str | None:
    row = connection.execute("SELECT value FROM meta WHERE key='machine_binding'").fetchone()
    return str(row[0]) if row else None


def _validate_binding(
    connection: sqlite3.Connection,
    *,
    target_machine_binding: str,
    source_machine_binding: str | None,
    reviewed_unbound_source: bool,
) -> None:
    stored = _target_binding(connection)
    if stored and stored != target_machine_binding:
        raise ForeignStateError("Target catalog is bound to a different machine")
    if source_machine_binding and source_machine_binding != target_machine_binding:
        raise ForeignStateError("Legacy source is bound to a foreign machine")
    if not source_machine_binding and not reviewed_unbound_source:
        raise ForeignStateError("Unbound legacy source requires explicit reviewed_unbound_source=True")


def _source_event_id(source_sha: str, table: str, key: Any) -> str:
    material = f"{source_sha}|{table}|{key}".encode("utf-8")
    return "legacy_" + hashlib.sha256(material).hexdigest()[:32]


def _history_key(source_sha: str, file_id: str, path: str, key: Any) -> str:
    return _source_event_id(source_sha, "path_history", f"{file_id}|{path}|{key}")


def _record_map(
    connection: sqlite3.Connection,
    import_id: str,
    source_table: str,
    source_key: Any,
    target_type: str,
    target_key: str,
    payload: Any,
    decision: str,
    details: dict[str, Any] | None = None,
) -> None:
    connection.execute(
        """INSERT OR REPLACE INTO legacy_record_map(
            import_id,source_table,source_key,target_type,target_key,payload_sha256,decision,details_json
        ) VALUES(?,?,?,?,?,?,?,?)""",
        (
            import_id,
            source_table,
            str(source_key),
            target_type,
            target_key,
            digest_json(payload),
            decision,
            canonical_json(details or {}),
        ),
    )


def _semantic_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "file_type": row.get("file_type"),
        "tier": row.get("tier"),
        "project_id": row.get("project_id"),
        "workstream_id": row.get("workstream_id"),
        "asset_role": row.get("asset_role"),
        "importance": row.get("importance"),
        "summary": row.get("summary"),
        "authority": row.get("authority") or "AI_INFERRED",
        "confidence": float(row.get("confidence") or 0.0),
        "last_changed": row.get("last_changed"),
        "logical_size": row.get("size"),
        "allocated_size": row.get("allocated_size"),
        "allocated_size_complete": 1 if row.get("allocated_size") is not None else 0,
        "rebuildable": row.get("rebuildable"),
        "rebuild_cost": row.get("rebuild_cost"),
        "canonical_status": row.get("canonical_status"),
        "supersedes_json": _json_text(row.get("supersedes_json"), []),
        "superseded_by_json": _json_text(row.get("superseded_by_json"), []),
        "version_family": row.get("version_family"),
        "references_json": _json_text(row.get("references_json"), []),
        "referenced_by_json": _json_text(row.get("referenced_by_json"), []),
        "duplicate_group": row.get("duplicate_group"),
        "archive_membership": row.get("archive_membership"),
        "producer_evidence_json": _json_text(row.get("producer_evidence_json"), []),
        "sensor_links_json": _json_text(row.get("sensor_observations_json"), []),
        "unresolved_questions_json": _json_text(row.get("unresolved_questions_json"), []),
        "source_signature_json": _json_text(row.get("source_signature_json"), {}),
        "content_identity": row.get("content_identity"),
        "content_identity_kind": row.get("content_identity_kind"),
    }
    if payload["duplicate_group"] and payload["content_identity_kind"] != "SHA256_FULL":
        raise ReconciliationError(
            f"FileCard {row.get('file_id')} has a duplicate_group without SHA256_FULL content identity"
        )
    return payload


def semantic_file_fingerprint(payload: dict[str, Any]) -> str:
    # Volatile sensor links and collection timestamps intentionally do not create semantic revisions.
    stable = {key: payload.get(key) for key in sorted(SEMANTIC_FILE_FIELDS)}
    return digest_json(stable)


def historical_file_fingerprint(snapshot: dict[str, Any]) -> str:
    """Fingerprint legacy history without upgrading an old claim into current truth."""
    aliases = {"logical_size": "size"}
    stable = {
        key: snapshot.get(key, snapshot.get(aliases.get(key, "")))
        for key in sorted(SEMANTIC_FILE_FIELDS)
    }
    return digest_json(stable)


def upsert_file_card_semantics(
    connection: sqlite3.Connection,
    *,
    file_id: str,
    updates: dict[str, Any],
    source_system: str,
    reason: str,
    updated_at: str | None = None,
) -> dict[str, Any]:
    unknown = set(updates) - (SEMANTIC_FILE_FIELDS | {"sensor_links_json"})
    if unknown:
        raise ValueError(f"Unsupported FileCard semantic field(s): {sorted(unknown)}")
    existing_row = connection.execute(
        "SELECT * FROM file_card_semantics WHERE file_id=?", (file_id,)
    ).fetchone()
    existing = dict(existing_row) if existing_row else {}
    payload: dict[str, Any] = {key: existing.get(key) for key in SEMANTIC_FILE_FIELDS | {"sensor_links_json"}}
    payload.update(updates)
    for key, default in JSON_DEFAULTS.items():
        payload[key] = _json_text(payload.get(key), json.loads(default))
    payload["authority"] = str(payload.get("authority") or "AI_INFERRED")
    payload["confidence"] = float(payload.get("confidence") or 0.0)
    if payload.get("duplicate_group") and payload.get("content_identity_kind") != "SHA256_FULL":
        raise ReconciliationError("Exact duplicate groups require SHA256_FULL content identity")
    fingerprint = semantic_file_fingerprint(payload)
    if existing and existing.get("semantic_fingerprint") == fingerprint:
        if payload.get("sensor_links_json") != existing.get("sensor_links_json"):
            connection.execute(
                "UPDATE file_card_semantics SET sensor_links_json=?,updated_at=? WHERE file_id=?",
                (payload["sensor_links_json"], updated_at or utc_now(), file_id),
            )
        return {"changed": False, "revision": int(existing["revision"]), "semantic_fingerprint": fingerprint}
    revision = int(existing.get("revision") or 0) + 1
    stamp = updated_at or utc_now()
    values = {
        **payload,
        "file_id": file_id,
        "revision": revision,
        "semantic_fingerprint": fingerprint,
        "source_system": source_system,
        "source_revision": revision,
        "updated_at": stamp,
    }
    columns = list(values)
    connection.execute(
        f"INSERT OR REPLACE INTO file_card_semantics({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
        tuple(values[column] for column in columns),
    )
    snapshot = {key: values.get(key) for key in sorted(SEMANTIC_FILE_FIELDS | {"sensor_links_json"})}
    connection.execute(
        """INSERT INTO file_card_revisions(
            file_id,revision,source_system,recorded_at,reasons_json,snapshot_json,semantic_fingerprint,source_event_id
        ) VALUES(?,?,?,?,?,?,?,?)""",
        (file_id, revision, source_system, stamp, canonical_json([reason]), canonical_json(snapshot), fingerprint, None),
    )
    return {"changed": True, "revision": revision, "semantic_fingerprint": fingerprint}


def persist_sensor_status(
    connection: sqlite3.Connection,
    *,
    machine_binding: str,
    status: SensorStatus,
    checked_at: str | None = None,
) -> int:
    stamp = checked_at or utc_now()
    capabilities = status.capabilities or ["<none>"]
    for capability in capabilities:
        connection.execute(
            """INSERT OR REPLACE INTO sensor_capability_status(
                machine_binding,sensor_id,capability,health,available,version,executable_hint,
                failure_reason,details_json,checked_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                machine_binding,
                status.sensor_id,
                capability,
                status.health.value,
                int(status.available),
                status.version,
                status.executable_path,
                status.failure_reason,
                canonical_json(status.details),
                stamp,
            ),
        )
    return len(capabilities)


def persist_sensor_observation(
    connection: sqlite3.Connection,
    *,
    machine_binding: str,
    observation: SensorObservation,
) -> str:
    connection.execute(
        """INSERT OR REPLACE INTO sensor_observations(
            observation_id,machine_binding,sensor_id,capability,scope,subject_id,outcome,cache_status,
            normalized_data_json,confidence,observed_at,raw_reference,error_json,metrics_json,
            upstream_errors_json,semantic_eligible
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
        (
            observation.observation_id,
            machine_binding,
            observation.sensor_id,
            str(observation.capability),
            observation.scope,
            observation.subject_id,
            observation.outcome.value,
            observation.cache_status.value,
            canonical_json(observation.normalized_data) if observation.normalized_data is not None else None,
            float(observation.confidence),
            observation.timestamp,
            observation.raw_reference,
            canonical_json(observation.error) if observation.error else None,
            canonical_json(observation.metrics),
            canonical_json(observation.upstream_errors),
        ),
    )
    return observation.observation_id


def inspect_cleanup_evidence_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    raw = path.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReconciliationError(f"Cleanup evidence manifest is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReconciliationError("Cleanup evidence manifest root must be an object")
    if payload.get("schema") != "file-intelligence-cleanup-evidence-v1":
        raise ReconciliationError("Unsupported cleanup evidence schema")
    records = payload.get("records")
    if not isinstance(records, list):
        raise ReconciliationError("Cleanup evidence records must be an array")
    if len(records) > 100000:
        raise ReconciliationError("Cleanup evidence manifest exceeds the 100000-record safety limit")
    errors: list[dict[str, Any]] = []
    ids: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(records):
        if not isinstance(item, dict):
            errors.append({"index": index, "error": "record must be an object"})
            continue
        evidence_id = str(item.get("evidence_id") or "")
        claim_type = str(item.get("claim_type") or "")
        subject = item.get("subject") if isinstance(item.get("subject"), dict) else {}
        authority = str(item.get("authority") or "")
        confidence = item.get("confidence")
        recommendation = item.get("recommendation") if isinstance(item.get("recommendation"), dict) else {}
        proposed_patch = item.get("proposed_file_card_patch") if isinstance(item.get("proposed_file_card_patch"), dict) else {}
        item_errors: list[str] = []
        if not evidence_id or evidence_id in ids:
            item_errors.append("evidence_id is missing or duplicated")
        ids.add(evidence_id)
        if claim_type not in CLEANUP_CLAIM_TYPES:
            item_errors.append("claim_type is unsupported")
        if not subject.get("file_id") and not subject.get("path"):
            item_errors.append("subject.file_id or subject.path is required")
        if authority not in AUTHORITY_ORDER:
            item_errors.append("authority is unsupported")
        try:
            numeric_confidence = float(confidence)
            if not 0.0 <= numeric_confidence <= 1.0:
                raise ValueError
        except (TypeError, ValueError):
            numeric_confidence = 0.0
            item_errors.append("confidence must be between 0 and 1")
        unknown_patch = set(proposed_patch) - CLEANUP_PATCH_FIELDS
        if unknown_patch:
            item_errors.append(f"unsupported proposed patch fields: {sorted(unknown_patch)}")
        if recommendation and recommendation.get("read_only") is not True:
            item_errors.append("cleanup recommendation must declare read_only=true")
        full_sha = str(item.get("full_sha256") or "").casefold()
        duplicate_group = item.get("duplicate_group")
        if claim_type == "EXACT_DUPLICATE":
            if len(full_sha) != 64 or any(char not in "0123456789abcdef" for char in full_sha):
                item_errors.append("EXACT_DUPLICATE requires a full SHA-256")
            if not duplicate_group:
                item_errors.append("EXACT_DUPLICATE requires duplicate_group")
        if proposed_patch.get("duplicate_group") and claim_type != "EXACT_DUPLICATE":
            item_errors.append("duplicate_group patch requires EXACT_DUPLICATE evidence")
        if item_errors:
            errors.append({"index": index, "evidence_id": evidence_id, "errors": item_errors})
            continue
        normalized.append(
            {
                "evidence_id": evidence_id,
                "claim_type": claim_type,
                "subject": {"file_id": subject.get("file_id"), "path": subject.get("path")},
                "authority": authority,
                "confidence": numeric_confidence,
                "full_sha256": full_sha or None,
                "duplicate_group": duplicate_group,
                "evidence": item.get("evidence") if isinstance(item.get("evidence"), list) else [],
                "proposed_file_card_patch": proposed_patch,
                "recommendation": {**recommendation, "read_only": True} if recommendation else {},
                "source_ref": item.get("source_ref"),
                "semantic_change": item.get("semantic_change") if isinstance(item.get("semantic_change"), dict) else None,
            }
        )
    source_sha = hashlib.sha256(raw).hexdigest()
    return {
        "path": str(path),
        "schema": payload["schema"],
        "source_sha256": source_sha,
        "source_machine_binding": payload.get("source_machine_binding"),
        "record_count": len(records),
        "valid_record_count": len(normalized),
        "errors": errors,
        "valid": not errors,
        "records": normalized,
    }


def import_cleanup_evidence_manifest(
    connection: sqlite3.Connection,
    *,
    manifest_path: Path,
    target_machine_binding: str,
    reviewed_unbound_source: bool = False,
    apply: bool = False,
) -> dict[str, Any]:
    inspection = inspect_cleanup_evidence_manifest(manifest_path)
    if not inspection["valid"]:
        raise ReconciliationError(
            f"Cleanup evidence manifest has {len(inspection['errors'])} validation error(s)"
        )
    _validate_binding(
        connection,
        target_machine_binding=target_machine_binding,
        source_machine_binding=inspection.get("source_machine_binding"),
        reviewed_unbound_source=reviewed_unbound_source,
    )
    preview = {
        "mode": "LEGACY_CLEANUP_EVIDENCE_IMPORT",
        "status": "PREVIEW_ONLY" if not apply else "READY_TO_APPLY",
        "source_schema": inspection["schema"],
        "source_sha256": inspection["source_sha256"],
        "record_count": inspection["record_count"],
        "valid_record_count": inspection["valid_record_count"],
        "read_only_recommendations": True,
        "physical_file_actions": 0,
        "state_files_written": 0,
    }
    if not apply:
        return preview
    source_sha = str(inspection["source_sha256"])
    import_id = "cleanup_import_" + digest_json(
        {"source_sha256": source_sha, "target": target_machine_binding}
    )[:24]
    prior = connection.execute(
        """SELECT 1 FROM legacy_imports
           WHERE source_kind='cleanup_evidence_manifest' AND source_sha256=?
             AND target_machine_binding=? AND status='COMPLETED'""",
        (source_sha, target_machine_binding),
    ).fetchone()
    if prior:
        return {**preview, "status": "NO_OP_ALREADY_IMPORTED", "import_id": import_id}
    connection.execute("SAVEPOINT cleanup_evidence_import")
    try:
        started = utc_now()
        connection.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('machine_binding',?)", (target_machine_binding,))
        connection.execute(
            """INSERT INTO legacy_imports(
                import_id,source_kind,source_schema,source_sha256,source_machine_binding,target_machine_binding,
                reviewed_unbound_source,started_at,completed_at,status,counts_json,conflicts_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                import_id,
                "cleanup_evidence_manifest",
                inspection["schema"],
                source_sha,
                inspection.get("source_machine_binding"),
                target_machine_binding,
                int(reviewed_unbound_source),
                started,
                None,
                "RUNNING",
                "{}",
                "[]",
            ),
        )
        connection.execute(
            "INSERT OR IGNORE INTO runs(run_id,mode,status,created_at,summary_json) VALUES(?,?,?,?,?)",
            (import_id, "LEGACY_CLEANUP_EVIDENCE_IMPORT", "RUNNING", started, "{}"),
        )
        by_path = {
            _normalize_path(str(row[1])): str(row[0])
            for row in connection.execute("SELECT file_id,path FROM files WHERE file_id IS NOT NULL")
        }
        counts = {"evidence_imported": 0, "recommendations_imported": 0, "file_evidence_linked": 0, "unresolved_subjects": 0, "semantic_changes_imported": 0}
        for record in inspection["records"]:
            subject = record["subject"]
            file_id = str(subject.get("file_id") or "") or None
            if file_id and not connection.execute("SELECT 1 FROM file_cards WHERE file_id=?", (file_id,)).fetchone():
                file_id = None
            if not file_id and subject.get("path"):
                file_id = by_path.get(_normalize_path(str(subject["path"])))
            mapping_status = "MAPPED_TO_FILE_CARD" if file_id else "UNRESOLVED_SUBJECT"
            counts["unresolved_subjects" if not file_id else "evidence_imported"] += 1
            expected_payload = canonical_json(record["evidence"])
            existing = connection.execute(
                "SELECT source_sha256,evidence_json FROM legacy_cleanup_evidence WHERE evidence_id=?",
                (record["evidence_id"],),
            ).fetchone()
            if existing and (str(existing[0]) != source_sha or str(existing[1]) != expected_payload):
                raise SemanticConflictError(
                    [{"kind": "CLEANUP_EVIDENCE_ID_COLLISION", "source_key": record["evidence_id"]}]
                )
            if not existing:
                connection.execute(
                    """INSERT INTO legacy_cleanup_evidence(
                        evidence_id,import_id,subject_file_id,subject_path,claim_type,authority,confidence,
                        full_sha256,duplicate_group,evidence_json,proposed_file_card_patch_json,source_ref,
                        source_sha256,mapping_status,imported_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        record["evidence_id"],
                        import_id,
                        file_id,
                        subject.get("path"),
                        record["claim_type"],
                        record["authority"],
                        record["confidence"],
                        record["full_sha256"],
                        record["duplicate_group"],
                        expected_payload,
                        canonical_json(record["proposed_file_card_patch"]),
                        record["source_ref"],
                        source_sha,
                        mapping_status,
                        started,
                    ),
                )
                if not file_id:
                    counts["evidence_imported"] += 1
            if file_id:
                evidence_key = "cleanup_" + record["evidence_id"]
                result = connection.execute(
                    """INSERT OR IGNORE INTO file_card_evidence(
                        evidence_id,file_id,claim,authority,description,source_ref,confidence,observed_at,
                        semantic_eligible,payload_json,source_system
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        evidence_key,
                        file_id,
                        record["claim_type"],
                        record["authority"],
                        "Reviewed legacy cleanup evidence; recommendation remains read-only",
                        record["source_ref"],
                        record["confidence"],
                        started,
                        1,
                        expected_payload,
                        "legacy_cleanup_evidence",
                    ),
                )
                counts["file_evidence_linked"] += max(0, int(result.rowcount))
            if record["recommendation"]:
                result = connection.execute(
                    """INSERT OR IGNORE INTO cleanup_recommendations(
                        recommendation_id,evidence_id,subject_file_id,recommendation_json,status,
                        execution_authorized,created_at
                    ) VALUES(?,?,?,?,?,?,?)""",
                    (
                        "recommendation_" + record["evidence_id"],
                        record["evidence_id"],
                        file_id,
                        canonical_json(record["recommendation"]),
                        "PROPOSED_READ_ONLY",
                        0,
                        started,
                    ),
                )
                counts["recommendations_imported"] += max(0, int(result.rowcount))
            change = record.get("semantic_change")
            if change and change.get("project_id"):
                change_id = _source_event_id(source_sha, "cleanup_semantic_change", record["evidence_id"])
                result = connection.execute(
                    """INSERT OR IGNORE INTO semantic_changes(
                        semantic_change_id,run_id,created_at,project_id,workstream_id,change_kind,importance,
                        importance_score,event_count,size_delta,summary_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        change_id,
                        import_id,
                        started,
                        change["project_id"],
                        change.get("workstream_id"),
                        change.get("change_kind") or "LEGACY_CLEANUP_EVIDENCE",
                        str(change.get("importance") or "medium").casefold(),
                        float(change.get("importance_score") or record["confidence"]),
                        0,
                        int(change.get("size_delta") or 0),
                        canonical_json(
                            {"summary": change.get("summary"), "evidence_id": record["evidence_id"], "read_only": True}
                        ),
                    ),
                )
                counts["semantic_changes_imported"] += max(0, int(result.rowcount))
        completed = utc_now()
        connection.execute(
            "UPDATE legacy_imports SET completed_at=?,status='COMPLETED',counts_json=? WHERE import_id=?",
            (completed, canonical_json(counts), import_id),
        )
        connection.execute(
            "UPDATE runs SET status='COMPLETED',summary_json=? WHERE run_id=?",
            (canonical_json(counts), import_id),
        )
        connection.execute("RELEASE SAVEPOINT cleanup_evidence_import")
        connection.commit()
    except Exception:
        connection.execute("ROLLBACK TO SAVEPOINT cleanup_evidence_import")
        connection.execute("RELEASE SAVEPOINT cleanup_evidence_import")
        connection.rollback()
        raise
    return {
        **preview,
        "status": "COMPLETED",
        "import_id": import_id,
        "counts": counts,
        "state_files_written": 1,
    }


class LegacyCardImporter:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        target_machine_binding: str,
        source_machine_binding: str | None = None,
        reviewed_unbound_source: bool = False,
    ) -> None:
        self.connection = connection
        self.target_machine_binding = target_machine_binding
        self.source_machine_binding = source_machine_binding
        self.reviewed_unbound_source = reviewed_unbound_source
        self.conflicts: list[dict[str, Any]] = []
        self.counts: dict[str, int] = {}
        self.source_sha = ""
        self.import_id = ""
        self.source_schema = ""
        self._file_map: dict[str, str] = {}

    def _bump(self, key: str, amount: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + amount

    def _conflict(self, kind: str, source_key: Any, details: dict[str, Any]) -> None:
        self.conflicts.append({"kind": kind, "source_key": str(source_key), "details": details})

    def import_path(self, source_path: Path, *, source_kind: str = "local_file_card") -> ImportReport:
        inspection = inspect_legacy_card_state(source_path)
        if inspection["quick_check"] != "ok":
            raise ReconciliationError(f"Legacy source failed SQLite quick_check: {inspection['quick_check']}")
        if not inspection["supported"]:
            raise ReconciliationError(f"Unsupported legacy FileCard schema: {inspection['schema']}")
        _validate_binding(
            self.connection,
            target_machine_binding=self.target_machine_binding,
            source_machine_binding=self.source_machine_binding,
            reviewed_unbound_source=self.reviewed_unbound_source,
        )
        self.source_sha = str(inspection["sha256"])
        self.source_schema = str(inspection["schema"])
        token = digest_json(
            {"source_kind": source_kind, "source_sha256": self.source_sha, "target": self.target_machine_binding}
        )[:24]
        self.import_id = f"legacy_import_{token}"
        prior = self.connection.execute(
            """SELECT counts_json,conflicts_json FROM legacy_imports
               WHERE source_kind=? AND source_sha256=? AND target_machine_binding=? AND status='COMPLETED'""",
            (source_kind, self.source_sha, self.target_machine_binding),
        ).fetchone()
        if prior:
            return ImportReport(
                self.import_id,
                "NO_OP_ALREADY_IMPORTED",
                str(inspection["schema"]),
                self.source_sha,
                {key: int(value) for key, value in _json(prior[0], {}).items()},
                list(_json(prior[1], [])),
            )

        uri = f"file:{source_path.as_posix()}?mode=ro&immutable=1"
        source = sqlite3.connect(uri, uri=True)
        source.row_factory = sqlite3.Row
        source.execute("PRAGMA query_only=ON")
        self.connection.execute("SAVEPOINT legacy_card_import")
        try:
            started = utc_now()
            self.connection.execute(
                "INSERT OR REPLACE INTO meta(key,value) VALUES('machine_binding',?)",
                (self.target_machine_binding,),
            )
            self.connection.execute(
                """INSERT INTO legacy_imports(
                    import_id,source_kind,source_schema,source_sha256,source_machine_binding,
                    target_machine_binding,reviewed_unbound_source,started_at,completed_at,status,
                    counts_json,conflicts_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    self.import_id,
                    source_kind,
                    inspection["schema"],
                    self.source_sha,
                    self.source_machine_binding,
                    self.target_machine_binding,
                    int(self.reviewed_unbound_source),
                    started,
                    None,
                    "RUNNING",
                    "{}",
                    "[]",
                ),
            )
            self.connection.execute(
                "INSERT OR IGNORE INTO runs(run_id,mode,status,created_at,summary_json) VALUES(?,?,?,?,?)",
                (self.import_id, "LEGACY_STATE_IMPORT", "RUNNING", started, "{}"),
            )
            self._import_file_cards(source)
            self._import_file_history(source)
            self._import_file_events(source)
            self._import_projects(source)
            self._import_assertions(source)
            self._import_semantic_changes(source)
            if self.conflicts:
                raise SemanticConflictError(self.conflicts)
            completed = utc_now()
            self.connection.execute(
                """UPDATE legacy_imports SET completed_at=?,status='COMPLETED',counts_json=?,conflicts_json=?
                   WHERE import_id=?""",
                (completed, canonical_json(self.counts), canonical_json(self.conflicts), self.import_id),
            )
            self.connection.execute(
                "UPDATE runs SET status='COMPLETED',summary_json=? WHERE run_id=?",
                (canonical_json({"counts": self.counts, "conflicts": self.conflicts}), self.import_id),
            )
            self.connection.execute("RELEASE SAVEPOINT legacy_card_import")
            self.connection.commit()
        except Exception:
            self.connection.execute("ROLLBACK TO SAVEPOINT legacy_card_import")
            self.connection.execute("RELEASE SAVEPOINT legacy_card_import")
            self.connection.rollback()
            raise
        finally:
            source.close()
        return ImportReport(
            self.import_id,
            "COMPLETED",
            str(inspection["schema"]),
            self.source_sha,
            dict(self.counts),
            list(self.conflicts),
        )

    def _core_indexes(self) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        by_path: dict[str, dict[str, Any]] = {}
        by_full_hash: dict[str, dict[str, Any]] = {}
        for raw in self.connection.execute(
            "SELECT path_key,path,file_id,native_file_id,full_sha256,status,first_seen,last_seen,identity_confidence,identity_evidence_json FROM files"
        ):
            row = dict(raw)
            by_path[_normalize_path(str(row["path"]))] = row
            if row.get("full_sha256"):
                by_full_hash[str(row["full_sha256"]).casefold()] = row
        return by_path, by_full_hash

    def _import_file_cards(self, source: sqlite3.Connection) -> None:
        by_path, by_full_hash = self._core_indexes()
        for row in _rows(source, "file_cards"):
            local_id = str(row["file_id"])
            path = str(row.get("current_path") or "")
            core = by_path.get(_normalize_path(path)) if path else None
            if core is None and row.get("content_identity_kind") == "SHA256_FULL" and row.get("content_identity"):
                core = by_full_hash.get(str(row["content_identity"]).casefold())
            target_id = str(core.get("file_id") if core and core.get("file_id") else local_id)
            self._file_map[local_id] = target_id
            path_key = str(core.get("path_key")) if core else "legacy_path_" + hashlib.sha256(
                _normalize_path(path).encode("utf-8")
            ).hexdigest()[:24]
            status = "missing" if str(row.get("status") or "").upper() == "MISSING" else "present"
            identity_evidence = canonical_json(
                [{"type": "legacy_card_import", "source_file_id": local_id, "source_sha256": self.source_sha}]
            )
            existing_card = self.connection.execute(
                "SELECT * FROM file_cards WHERE file_id=?", (target_id,)
            ).fetchone()
            if not existing_card:
                self.connection.execute(
                    """INSERT INTO file_cards(
                        file_id,native_file_id,current_path_key,first_seen,last_seen,status,identity_confidence,
                        identity_evidence_json,last_role,last_authority,authority_scope
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        target_id,
                        core.get("native_file_id") if core else None,
                        path_key,
                        row.get("first_seen") or row.get("updated_at") or utc_now(),
                        row.get("last_seen") or row.get("updated_at") or utc_now(),
                        status,
                        float(core.get("identity_confidence") or row.get("confidence") or 0.7) if core else float(row.get("confidence") or 0.7),
                        core.get("identity_evidence_json") or identity_evidence if core else identity_evidence,
                        row.get("asset_role"),
                        row.get("authority"),
                        "FILE_LOCAL",
                    ),
                )
                self._bump("file_cards_created")
            try:
                payload = _semantic_payload(row)
            except ReconciliationError:
                if self.source_schema != "1.0.0" or not row.get("duplicate_group"):
                    raise
                # Schema 1.0.0 did not type content identity. Preserve the old claim only
                # as provenance and refuse to promote it into an exact duplicate group.
                downgraded = dict(row)
                downgraded["duplicate_group"] = None
                questions = _json(downgraded.get("unresolved_questions_json"), [])
                questions.append(
                    {
                        "type": "LEGACY_DUPLICATE_CLAIM_REQUIRES_FULL_SHA256",
                        "legacy_duplicate_group": row.get("duplicate_group"),
                    }
                )
                downgraded["unresolved_questions_json"] = canonical_json(questions)
                payload = _semantic_payload(downgraded)
                self._bump("legacy_duplicate_claims_demoted")
            fingerprint = semantic_file_fingerprint(payload)
            existing_semantic = self.connection.execute(
                "SELECT semantic_fingerprint,source_system FROM file_card_semantics WHERE file_id=?", (target_id,)
            ).fetchone()
            if existing_semantic and str(existing_semantic[0]) != fingerprint:
                self._conflict(
                    "FILE_CARD_SEMANTIC_COLLISION",
                    local_id,
                    {"target_file_id": target_id, "existing_source": existing_semantic[1]},
                )
                continue
            if not existing_semantic:
                values = {
                    **payload,
                    "file_id": target_id,
                    "revision": int(row.get("revision") or 1),
                    "semantic_fingerprint": fingerprint,
                    "source_system": "legacy_file_card_2x",
                    "source_revision": int(row.get("revision") or 1),
                    "updated_at": row.get("updated_at") or utc_now(),
                }
                columns = list(values)
                self.connection.execute(
                    f"INSERT INTO file_card_semantics({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                    tuple(values[column] for column in columns),
                )
                self._bump("file_card_semantics_imported")
            else:
                self._bump("file_card_semantics_deduplicated")
            _record_map(
                self.connection,
                self.import_id,
                "file_cards",
                local_id,
                "file_card",
                target_id,
                payload,
                "MATCHED_CORE_IDENTITY" if core else "IMPORTED_ORPHAN_CARD",
                {"path_key": path_key, "source_path": path},
            )

        for row in _rows(source, "file_card_revisions"):
            local_id = str(row["file_id"])
            target_id = self._file_map.get(local_id, local_id)
            snapshot = _json(row.get("snapshot_json"), {})
            fingerprint = historical_file_fingerprint(snapshot if isinstance(snapshot, dict) else {})
            result = self.connection.execute(
                """INSERT OR IGNORE INTO file_card_revisions(
                    file_id,revision,source_system,recorded_at,reasons_json,snapshot_json,semantic_fingerprint,source_event_id
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    target_id,
                    int(row.get("revision") or 1),
                    "legacy_file_card_2x",
                    row.get("recorded_at") or utc_now(),
                    _json_text(row.get("reasons_json"), []),
                    canonical_json(snapshot),
                    fingerprint,
                    None,
                ),
            )
            self._bump("file_card_revisions_imported", max(0, int(result.rowcount)))

        for row in _rows(source, "file_card_evidence"):
            local_id = str(row["file_id"])
            target_id = self._file_map.get(local_id, local_id)
            existing = self.connection.execute(
                "SELECT file_id,claim,authority,payload_json FROM file_card_evidence WHERE evidence_id=?",
                (row["evidence_id"],),
            ).fetchone()
            expected = {
                "file_id": target_id,
                "claim": row.get("claim") or "legacy_claim",
                "authority": row.get("authority") or "AI_INFERRED",
                "payload_json": _json_text(row.get("payload_json"), {}),
            }
            if existing and dict(existing) != expected:
                self._conflict("EVIDENCE_ID_COLLISION", row["evidence_id"], {"target_file_id": target_id})
                continue
            if not existing:
                self.connection.execute(
                    """INSERT INTO file_card_evidence(
                        evidence_id,file_id,claim,authority,description,source_ref,confidence,observed_at,
                        semantic_eligible,payload_json,source_system
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        row["evidence_id"],
                        target_id,
                        expected["claim"],
                        expected["authority"],
                        row.get("description") or "",
                        row.get("source_ref"),
                        float(row.get("confidence") or 0.0),
                        row.get("observed_at") or utc_now(),
                        int(row.get("semantic_eligible") or 0),
                        expected["payload_json"],
                        "legacy_file_card_2x",
                    ),
                )
                self._bump("file_card_evidence_imported")

    def _import_file_history(self, source: sqlite3.Connection) -> None:
        histories = _rows(source, "file_path_history")
        for row in histories:
            local_id = str(row["file_id"])
            target_id = self._file_map.get(local_id, local_id)
            key = _history_key(self.source_sha, target_id, str(row.get("path") or ""), row.get("history_id"))
            result = self.connection.execute(
                """INSERT OR IGNORE INTO file_path_history(
                    history_key,file_id,path,observed_from,observed_to,event_type,source_event_id,source_system
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    key,
                    target_id,
                    row.get("path") or "",
                    row.get("observed_from") or utc_now(),
                    row.get("observed_to"),
                    row.get("event_type") or "OBSERVED",
                    row.get("source_event_id"),
                    "legacy_file_card_2x",
                ),
            )
            self._bump("file_path_history_imported", max(0, int(result.rowcount)))
        if histories:
            return
        # Older card stores may have no path-history rows. Preserve the current path as an initial observation.
        for row in _rows(source, "file_cards"):
            path = str(row.get("current_path") or "")
            if not path:
                continue
            target_id = self._file_map.get(str(row["file_id"]), str(row["file_id"]))
            key = _history_key(self.source_sha, target_id, path, "current")
            result = self.connection.execute(
                "INSERT OR IGNORE INTO file_path_history VALUES(?,?,?,?,?,?,?,?)",
                (
                    key,
                    target_id,
                    path,
                    row.get("first_seen") or utc_now(),
                    None,
                    "IMPORTED_CURRENT_PATH",
                    None,
                    "legacy_file_card_2x",
                ),
            )
            self._bump("file_path_history_imported", max(0, int(result.rowcount)))

    def _import_file_events(self, source: sqlite3.Connection) -> None:
        for row in _rows(source, "file_card_events"):
            event_type = str(row.get("event_type") or "")
            if event_type == "CARD_REUSED":
                self._bump("legacy_noop_card_events_skipped")
                continue
            if event_type == "CARD_CREATED":
                event_type = "LEGACY_CARD_CREATED"
            if event_type not in LEGACY_FILE_EVENTS:
                if event_type != "LEGACY_CARD_CREATED":
                    self._conflict("UNSUPPORTED_FILE_EVENT", row.get("event_id"), {"event_type": event_type})
                    continue
            local_id = str(row["file_id"])
            target_id = self._file_map.get(local_id, local_id)
            event_id = _source_event_id(self.source_sha, "file_card_events", row.get("event_id"))
            result = self.connection.execute(
                """INSERT OR IGNORE INTO events(
                    event_id,event_type,occurred_at,subject_type,file_id,path_key,project_id,workstream_id,
                    old_value_json,new_value_json,size_delta,path_before,path_after,confidence,evidence_json,
                    run_id,semantic_importance,importance_score,importance_reasons_json,volatile_class,
                    resolution_status,permanent,aggregate_count
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event_id,
                    event_type,
                    row.get("recorded_at") or utc_now(),
                    "file",
                    target_id,
                    None,
                    None,
                    None,
                    canonical_json({"revision": row.get("from_revision")}),
                    canonical_json({"revision": row.get("to_revision")}),
                    0,
                    None,
                    None,
                    1.0,
                    canonical_json(
                        [{"type": "legacy_file_card_event", "reasons": _json(row.get("reasons_json"), [])}]
                    ),
                    self.import_id,
                    "important" if event_type in {"CONTENT_CHANGED", "MISSING", "REAPPEARED"} else "routine",
                    0.8 if event_type in {"CONTENT_CHANGED", "MISSING", "REAPPEARED"} else 0.4,
                    _json_text(row.get("reasons_json"), []),
                    None,
                    "IMPORTED",
                    1,
                    1,
                ),
            )
            self._bump("timeline_events_imported", max(0, int(result.rowcount)))

    def _ensure_project(self, row: dict[str, Any], snapshot: dict[str, Any]) -> None:
        project_id = str(row["project_id"])
        existing = self.connection.execute("SELECT name FROM projects WHERE project_id=?", (project_id,)).fetchone()
        if existing:
            if str(existing[0]) != str(row.get("name") or project_id):
                self._conflict(
                    "PROJECT_ID_COLLISION",
                    project_id,
                    {"existing_name": existing[0], "source_name": row.get("name")},
                )
            return
        counts = self.connection.execute(
            "SELECT COUNT(*),COALESCE(SUM(logical_size),0) FROM file_card_semantics WHERE project_id=?",
            (project_id,),
        ).fetchone()
        self.connection.execute(
            """INSERT INTO projects(
                project_id,name,file_count,total_size,status,root_path,purpose,lifecycle,confidence,evidence_json,parent_project_id
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                project_id,
                row.get("name") or project_id,
                int(counts[0]),
                int(counts[1]),
                row.get("status") or "UNKNOWN",
                snapshot.get("root_path") or (snapshot.get("roots") or [None])[0],
                snapshot.get("purpose"),
                row.get("status") or snapshot.get("status"),
                float(row.get("confidence") or 0.0),
                canonical_json(
                    [{"type": "legacy_project_card", "authority": row.get("authority"), "source_sha256": self.source_sha}]
                ),
                snapshot.get("parent_project_id"),
            ),
        )
        self._bump("projects_created")

    def _import_projects(self, source: sqlite3.Connection) -> None:
        for row in _rows(source, "project_cards"):
            project_id = str(row["project_id"])
            snapshot = _json(row.get("snapshot_json"), {})
            self._ensure_project(row, snapshot)
            fingerprint = str(row.get("semantic_fingerprint") or digest_json(snapshot))
            existing = self.connection.execute(
                "SELECT semantic_fingerprint,source_system FROM project_card_views WHERE project_id=?",
                (project_id,),
            ).fetchone()
            if existing and str(existing[0]) != fingerprint:
                self._conflict(
                    "PROJECT_CARD_VIEW_COLLISION",
                    project_id,
                    {"existing_source": existing[1], "source_fingerprint": fingerprint},
                )
                continue
            if not existing:
                self.connection.execute(
                    "INSERT INTO project_card_views VALUES(?,?,?,?,?,?,?,?)",
                    (
                        project_id,
                        int(row.get("revision") or 1),
                        fingerprint,
                        canonical_json(snapshot),
                        row.get("authority") or "AI_INFERRED",
                        float(row.get("confidence") or 0.0),
                        "legacy_project_card_2x",
                        row.get("updated_at") or utc_now(),
                    ),
                )
                self._bump("project_card_views_imported")
            _record_map(
                self.connection,
                self.import_id,
                "project_cards",
                project_id,
                "project_card_view",
                project_id,
                snapshot,
                "IMPORTED_DERIVED_VIEW",
            )

        for row in _rows(source, "project_card_revisions"):
            snapshot = _json(row.get("snapshot_json"), {})
            result = self.connection.execute(
                """INSERT OR IGNORE INTO project_card_revisions(
                    project_id,revision,source_system,recorded_at,reasons_json,snapshot_json,semantic_fingerprint
                ) VALUES(?,?,?,?,?,?,?)""",
                (
                    row["project_id"],
                    int(row.get("revision") or 1),
                    "legacy_project_card_2x",
                    row.get("recorded_at") or utc_now(),
                    _json_text(row.get("reasons_json"), []),
                    canonical_json(snapshot),
                    digest_json(snapshot),
                ),
            )
            self._bump("project_card_revisions_imported", max(0, int(result.rowcount)))

        for row in _rows(source, "project_relations"):
            relation_id = str(row["relation_id"])
            expected = {
                "source_project_id": row.get("source_project_id"),
                "target_id": row.get("target_id"),
                "relation_type": row.get("relation_type"),
                "entity_type": row.get("entity_type"),
                "authority": row.get("authority") or "AI_INFERRED",
                "active": int(row.get("active") if row.get("active") is not None else 1),
            }
            existing = self.connection.execute(
                """SELECT source_project_id,target_id,relation_type,entity_type,authority,active
                   FROM project_relations WHERE relation_id=?""",
                (relation_id,),
            ).fetchone()
            if existing and dict(existing) != expected:
                self._conflict("PROJECT_RELATION_COLLISION", relation_id, expected)
                continue
            if not existing:
                self.connection.execute(
                    "INSERT INTO project_relations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        relation_id,
                        expected["source_project_id"],
                        expected["target_id"],
                        row.get("target_name") or expected["target_id"],
                        expected["relation_type"],
                        expected["entity_type"],
                        expected["authority"],
                        float(row.get("confidence") or 0.0),
                        _json_text(row.get("evidence_json"), []),
                        expected["active"],
                        row.get("updated_at") or utc_now(),
                        "legacy_project_card_2x",
                    ),
                )
                self._bump("project_relations_imported")

        for row in _rows(source, "project_card_events"):
            if row.get("event_type") == "PROJECT_CARD_REUSED":
                self._bump("legacy_noop_project_events_skipped")
                continue
            event_id = _source_event_id(self.source_sha, "project_card_events", row.get("event_id"))
            result = self.connection.execute(
                """INSERT OR IGNORE INTO events(
                    event_id,event_type,occurred_at,subject_type,file_id,path_key,project_id,workstream_id,
                    old_value_json,new_value_json,size_delta,path_before,path_after,confidence,evidence_json,
                    run_id,semantic_importance,importance_score,importance_reasons_json,volatile_class,
                    resolution_status,permanent,aggregate_count
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event_id,
                    row.get("event_type") or "PROJECT_CARD_CHANGED",
                    row.get("recorded_at") or utc_now(),
                    "project",
                    None,
                    None,
                    row.get("project_id"),
                    None,
                    canonical_json({"revision": row.get("from_revision")}),
                    canonical_json({"revision": row.get("to_revision")}),
                    0,
                    None,
                    None,
                    1.0,
                    canonical_json(
                        [{"type": "legacy_project_card_event", "reasons": _json(row.get("reasons_json"), [])}]
                    ),
                    self.import_id,
                    "important",
                    0.8,
                    _json_text(row.get("reasons_json"), []),
                    None,
                    "IMPORTED",
                    1,
                    1,
                ),
            )
            self._bump("project_timeline_events_imported", max(0, int(result.rowcount)))

    def _import_assertions(self, source: sqlite3.Connection) -> None:
        for row in _rows(source, "user_assertions"):
            assertion_id = str(row["assertion_id"])
            expected = {
                "subject_type": row.get("subject_type"),
                "subject_key": row.get("subject_id"),
                "predicate": row.get("field_name"),
                "value_json": _json_text(row.get("value_json"), None),
                "active": int(row.get("active") if row.get("active") is not None else 1),
            }
            existing = self.connection.execute(
                "SELECT subject_type,subject_key,predicate,value_json,active FROM user_assertions WHERE assertion_id=?",
                (assertion_id,),
            ).fetchone()
            if existing and dict(existing) != expected:
                self._conflict("ASSERTION_ID_COLLISION", assertion_id, expected)
                continue
            if not existing:
                self.connection.execute(
                    """INSERT INTO user_assertions(
                        assertion_id,subject_type,subject_key,predicate,value_json,provenance,created_at,updated_at,active
                    ) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        assertion_id,
                        expected["subject_type"],
                        expected["subject_key"],
                        expected["predicate"],
                        expected["value_json"],
                        canonical_json(
                            {
                                "authority": row.get("authority") or "USER_ASSERTED",
                                "source_ref": row.get("source_ref"),
                                "source_system": "legacy_file_card_2x",
                            }
                        ),
                        row.get("asserted_at") or utc_now(),
                        row.get("revoked_at") or row.get("asserted_at") or utc_now(),
                        expected["active"],
                    ),
                )
                self._bump("user_assertions_imported")
            event_key = _source_event_id(self.source_sha, "user_assertions", assertion_id)
            result = self.connection.execute(
                """INSERT OR IGNORE INTO assertion_events(
                    event_key,assertion_id,event_type,recorded_at,source_text,source_ref,revoked_at,payload_json,source_system
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    event_key,
                    assertion_id,
                    "ASSERTED" if expected["active"] else "REVOKED",
                    row.get("asserted_at") or utc_now(),
                    row.get("source_text"),
                    row.get("source_ref"),
                    row.get("revoked_at"),
                    expected["value_json"],
                    "legacy_file_card_2x",
                ),
            )
            self._bump("assertion_events_imported", max(0, int(result.rowcount)))

        for row in _rows(source, "user_assertion_events"):
            event_key = _source_event_id(self.source_sha, "user_assertion_events", row.get("event_id"))
            result = self.connection.execute(
                """INSERT OR IGNORE INTO assertion_events(
                    event_key,assertion_id,event_type,recorded_at,source_text,source_ref,revoked_at,payload_json,source_system
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    event_key,
                    row.get("assertion_id"),
                    row.get("event_type") or "ASSERTION_EVENT",
                    row.get("recorded_at") or utc_now(),
                    None,
                    None,
                    None,
                    _json_text(row.get("payload_json"), {}),
                    "legacy_file_card_2x",
                ),
            )
            self._bump("assertion_events_imported", max(0, int(result.rowcount)))

    def _import_semantic_changes(self, source: sqlite3.Connection) -> None:
        for row in _rows(source, "semantic_changes"):
            change_id = _source_event_id(self.source_sha, "semantic_changes", row.get("change_id"))
            summary = {
                "legacy_change_id": row.get("change_id"),
                "summary": row.get("summary"),
                "authority": row.get("authority"),
                "confidence": row.get("confidence"),
                "status": row.get("status"),
                "time_range": _json(row.get("time_range_json"), {}),
                "evidence": _json(row.get("evidence_json"), []),
                "related_file_ids": [self._file_map.get(str(value), str(value)) for value in _json(row.get("related_file_ids_json"), [])],
                "related_event_ids": _json(row.get("related_event_ids_json"), []),
            }
            result = self.connection.execute(
                """INSERT OR IGNORE INTO semantic_changes(
                    semantic_change_id,run_id,created_at,project_id,workstream_id,change_kind,importance,
                    importance_score,event_count,size_delta,summary_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    change_id,
                    self.import_id,
                    row.get("recorded_at") or utc_now(),
                    row.get("project_id"),
                    None,
                    row.get("change_type") or "LEGACY_PROJECT_CHANGE",
                    str(row.get("importance") or "important").casefold(),
                    float(row.get("confidence") or 0.0),
                    len(summary["related_event_ids"]),
                    int(row.get("storage_delta") or 0),
                    canonical_json(summary),
                ),
            )
            self._bump("semantic_changes_imported", max(0, int(result.rowcount)))


def extension_status(connection: sqlite3.Connection) -> dict[str, Any]:
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    required = {
        "file_card_semantics",
        "file_card_revisions",
        "file_card_evidence",
        "file_path_history",
        "project_card_views",
        "project_card_revisions",
        "project_relations",
        "assertion_events",
        "sensor_capability_status",
        "sensor_observations",
        "legacy_imports",
        "legacy_record_map",
        "legacy_cleanup_evidence",
        "cleanup_recommendations",
    }
    row = connection.execute(
        "SELECT value FROM meta WHERE key='reconciliation_extension_version'"
    ).fetchone()
    return {
        "version": int(row[0]) if row else 0,
        "complete": required.issubset(tables),
        "missing_tables": sorted(required - tables),
        "physical_file_actions": 0,
    }


def reconcile_legacy_state(
    *,
    state_dir: Path,
    source_path: Path,
    target_machine_binding: str,
    source_machine_binding: str | None = None,
    reviewed_unbound_source: bool = False,
    apply: bool = False,
) -> dict[str, Any]:
    from .database import connect_current

    inspection = inspect_legacy_card_state(source_path)
    plan = {
        "mode": "LEGACY_CARD_RECONCILIATION",
        "apply_requested": bool(apply),
        "source": inspection,
        "target_state_dir": str(state_dir),
        "target_machine_binding": target_machine_binding,
        "source_machine_binding": source_machine_binding,
        "reviewed_unbound_source": bool(reviewed_unbound_source),
        "backup_required": True,
        "rollback": "RESTORE_VERIFIED_PRE_IMPORT_CATALOG_BACKUP",
        "physical_file_actions": 0,
    }
    if not apply:
        return {**plan, "status": "PREVIEW_ONLY", "state_files_written": 0}
    catalog = state_dir / "catalog.db"
    if not catalog.is_file():
        raise ReconciliationError(f"Target schema-v3 catalog does not exist: {catalog}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = state_dir / "migrations" / f"reconciliation-{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_catalog = backup_dir / "catalog.db"
    source_connection = sqlite3.connect(catalog)
    backup_connection = sqlite3.connect(backup_catalog)
    try:
        source_connection.backup(backup_connection)
    finally:
        backup_connection.close()
        source_connection.close()
    backup_hash = file_sha256(backup_catalog)
    manifest = {
        "created_at": utc_now(),
        "source_catalog": str(catalog),
        "backup_catalog": str(backup_catalog),
        "sha256": backup_hash,
        "files": [{"name": "catalog.db", "sha256": backup_hash}],
        "restore_required_for_rollback": True,
        "physical_file_actions": 0,
    }
    manifest_path = backup_dir / "backup_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    connection = connect_current(catalog)
    try:
        report = LegacyCardImporter(
            connection,
            target_machine_binding=target_machine_binding,
            source_machine_binding=source_machine_binding,
            reviewed_unbound_source=reviewed_unbound_source,
        ).import_path(source_path)
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        if integrity != "ok":
            raise ReconciliationError(f"Target failed integrity_check after import: {integrity}")
    finally:
        connection.close()
    return {
        **plan,
        "status": report.status,
        "report": report.to_dict(),
        "backup": {**manifest, "manifest": str(manifest_path)},
        "state_files_written": 3,
    }


def reconcile_cleanup_evidence_state(
    *,
    state_dir: Path,
    manifest_path: Path,
    target_machine_binding: str,
    reviewed_unbound_source: bool = False,
    apply: bool = False,
) -> dict[str, Any]:
    from .database import connect_current

    inspection = inspect_cleanup_evidence_manifest(manifest_path)
    if not inspection["valid"]:
        raise ReconciliationError(
            f"Cleanup evidence manifest has {len(inspection['errors'])} validation error(s)"
        )
    source_binding = inspection.get("source_machine_binding")
    if source_binding and source_binding != target_machine_binding:
        raise ForeignStateError("Cleanup evidence manifest is bound to a foreign machine")
    if not source_binding and not reviewed_unbound_source:
        raise ForeignStateError("Unbound cleanup evidence requires explicit reviewed_unbound_source=True")
    plan = {
        "mode": "LEGACY_CLEANUP_EVIDENCE_IMPORT",
        "source_schema": inspection["schema"],
        "source_sha256": inspection["source_sha256"],
        "record_count": inspection["record_count"],
        "valid_record_count": inspection["valid_record_count"],
        "target_state_dir": str(state_dir),
        "backup_required": True,
        "read_only_recommendations": True,
        "physical_file_actions": 0,
    }
    if not apply:
        return {**plan, "status": "PREVIEW_ONLY", "state_files_written": 0}
    catalog = state_dir / "catalog.db"
    if not catalog.is_file():
        raise ReconciliationError(f"Target schema-v3 catalog does not exist: {catalog}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = state_dir / "migrations" / f"cleanup-evidence-{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_catalog = backup_dir / "catalog.db"
    source_connection = sqlite3.connect(catalog)
    backup_connection = sqlite3.connect(backup_catalog)
    try:
        source_connection.backup(backup_connection)
    finally:
        backup_connection.close()
        source_connection.close()
    backup_hash = file_sha256(backup_catalog)
    backup_manifest = {
        "created_at": utc_now(),
        "source_catalog": str(catalog),
        "backup_catalog": str(backup_catalog),
        "sha256": backup_hash,
        "files": [{"name": "catalog.db", "sha256": backup_hash}],
        "restore_required_for_rollback": True,
        "physical_file_actions": 0,
    }
    backup_manifest_path = backup_dir / "backup_manifest.json"
    backup_manifest_path.write_text(
        json.dumps(backup_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    connection = connect_current(catalog)
    try:
        result = import_cleanup_evidence_manifest(
            connection,
            manifest_path=manifest_path,
            target_machine_binding=target_machine_binding,
            reviewed_unbound_source=reviewed_unbound_source,
            apply=True,
        )
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        if integrity != "ok":
            raise ReconciliationError(f"Target failed integrity_check after cleanup evidence import: {integrity}")
    finally:
        connection.close()
    return {
        **plan,
        **result,
        "backup": {**backup_manifest, "manifest": str(backup_manifest_path)},
        "state_files_written": 3,
    }
