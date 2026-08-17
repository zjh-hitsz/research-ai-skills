from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 3
APPLICATION_ID = 0x46494E54  # "FINT"
RECONCILIATION_EXTENSION_VERSION = 1


SCHEMA_V3 = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS files (
    path_key TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    root_path TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    filename TEXT NOT NULL,
    extension TEXT NOT NULL,
    size INTEGER NOT NULL,
    modified TEXT NOT NULL,
    created TEXT NOT NULL,
    scope_kind TEXT NOT NULL,
    is_communication INTEGER NOT NULL,
    project_id TEXT,
    project_name TEXT,
    fingerprint TEXT,
    sample_fingerprint TEXT,
    full_sha256 TEXT,
    fingerprint_stage INTEGER NOT NULL DEFAULT 0,
    volatile_class TEXT,
    status TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    missing_since TEXT,
    file_id TEXT,
    native_file_id TEXT,
    identity_confidence REAL,
    identity_evidence_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_files_file_id ON files(file_id, status);
CREATE INDEX IF NOT EXISTS idx_files_native_id ON files(native_file_id, status);
CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    file_count INTEGER NOT NULL,
    total_size INTEGER NOT NULL,
    status TEXT NOT NULL,
    root_path TEXT,
    purpose TEXT,
    lifecycle TEXT,
    confidence REAL,
    evidence_json TEXT,
    parent_project_id TEXT
);
CREATE TABLE IF NOT EXISTS project_nodes (
    node_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    parent_node_id TEXT,
    node_type TEXT NOT NULL,
    name TEXT NOT NULL,
    path TEXT,
    lifecycle TEXT,
    confidence REAL NOT NULL,
    evidence_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_project_nodes_project ON project_nodes(project_id, node_type);
CREATE TABLE IF NOT EXISTS assets (
    path_key TEXT PRIMARY KEY,
    project_id TEXT,
    workstream_id TEXT,
    role TEXT NOT NULL,
    authority_level TEXT NOT NULL,
    confidence REAL NOT NULL,
    evidence_json TEXT NOT NULL,
    provenance_type TEXT NOT NULL,
    rebuildability TEXT,
    archive_recommendation TEXT,
    superseded_by_path_key TEXT,
    updated_at TEXT NOT NULL,
    asset_kind TEXT NOT NULL DEFAULT 'file',
    entity_path TEXT,
    authority_scope TEXT NOT NULL DEFAULT 'FILE_LOCAL',
    authority_context_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_assets_project ON assets(project_id, authority_level, role);
CREATE INDEX IF NOT EXISTS idx_assets_scope ON assets(project_id, authority_scope, authority_level);
CREATE TABLE IF NOT EXISTS dependencies (
    edge_id TEXT PRIMARY KEY,
    project_id TEXT,
    source_path_key TEXT NOT NULL,
    target_path_key TEXT,
    target_text TEXT NOT NULL,
    resolved_path TEXT,
    relation TEXT NOT NULL,
    resolution_status TEXT NOT NULL,
    evidence_type TEXT NOT NULL,
    line_no INTEGER,
    confidence REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dependencies_source ON dependencies(source_path_key);
CREATE INDEX IF NOT EXISTS idx_dependencies_target ON dependencies(target_path_key, resolved_path);
CREATE TABLE IF NOT EXISTS inspections (
    path_key TEXT PRIMARY KEY,
    inspector TEXT NOT NULL,
    inspected_size INTEGER NOT NULL,
    inspected_modified TEXT NOT NULL,
    text_digest TEXT,
    metadata_json TEXT NOT NULL,
    error TEXT,
    inspected_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fingerprints (
    path_key TEXT NOT NULL,
    stage INTEGER NOT NULL,
    algorithm TEXT NOT NULL,
    value TEXT NOT NULL,
    bytes_read INTEGER NOT NULL,
    verified_at TEXT NOT NULL,
    PRIMARY KEY(path_key, stage)
);
CREATE INDEX IF NOT EXISTS idx_fingerprints_value ON fingerprints(stage, value);
CREATE TABLE IF NOT EXISTS identity_events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    source_path_key TEXT,
    target_path_key TEXT,
    full_sha256 TEXT,
    confidence REAL NOT NULL,
    evidence_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS aggregate_nodes (
    aggregate_id TEXT PRIMARY KEY,
    project_id TEXT,
    path TEXT NOT NULL,
    kind TEXT NOT NULL,
    total_size INTEGER NOT NULL,
    file_count INTEGER NOT NULL,
    internal_indexing TEXT NOT NULL,
    rebuildability TEXT,
    confidence REAL NOT NULL,
    evidence_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_assertions (
    assertion_id TEXT PRIMARY KEY,
    subject_type TEXT NOT NULL,
    subject_key TEXT NOT NULL,
    predicate TEXT NOT NULL,
    value_json TEXT NOT NULL,
    provenance TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_assertions_subject ON user_assertions(subject_type, subject_key, active);
CREATE TABLE IF NOT EXISTS migrations (
    migration_id TEXT PRIMARY KEY,
    from_version INTEGER NOT NULL,
    to_version INTEGER NOT NULL,
    applied_at TEXT NOT NULL,
    backup_path TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    summary_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS file_cards (
    file_id TEXT PRIMARY KEY,
    native_file_id TEXT,
    current_path_key TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    status TEXT NOT NULL,
    identity_confidence REAL NOT NULL,
    identity_evidence_json TEXT NOT NULL,
    last_role TEXT,
    last_authority TEXT,
    authority_scope TEXT
);
CREATE INDEX IF NOT EXISTS idx_file_cards_native ON file_cards(native_file_id);
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    file_id TEXT,
    path_key TEXT,
    project_id TEXT,
    workstream_id TEXT,
    old_value_json TEXT,
    new_value_json TEXT,
    size_delta INTEGER NOT NULL DEFAULT 0,
    path_before TEXT,
    path_after TEXT,
    confidence REAL NOT NULL,
    evidence_json TEXT NOT NULL,
    run_id TEXT NOT NULL,
    semantic_importance TEXT NOT NULL,
    importance_score REAL NOT NULL,
    importance_reasons_json TEXT NOT NULL,
    volatile_class TEXT,
    resolution_status TEXT,
    permanent INTEGER NOT NULL DEFAULT 0,
    aggregate_count INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_events_time ON events(occurred_at, event_id);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, semantic_importance);
CREATE INDEX IF NOT EXISTS idx_events_project ON events(project_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_events_file ON events(file_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type, occurred_at);
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    snapshot_kind TEXT NOT NULL,
    run_id TEXT,
    files_count INTEGER NOT NULL,
    logical_size INTEGER NOT NULL,
    disk_total INTEGER,
    disk_used INTEGER,
    disk_free INTEGER,
    project_count INTEGER NOT NULL,
    active_projects INTEGER NOT NULL,
    frozen_projects INTEGER NOT NULL,
    authority_asset_count INTEGER NOT NULL,
    aggregate_size INTEGER NOT NULL,
    potential_cleanup INTEGER NOT NULL,
    potential_archive INTEGER NOT NULL,
    important_asset_summary_json TEXT NOT NULL,
    catalog_digest TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_time ON snapshots(created_at, snapshot_kind);
CREATE TABLE IF NOT EXISTS project_snapshots (
    snapshot_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    project_name TEXT NOT NULL,
    total_size INTEGER NOT NULL,
    file_count INTEGER NOT NULL,
    lifecycle TEXT,
    activity_status TEXT,
    workstream_status_json TEXT NOT NULL,
    authority_summary_json TEXT NOT NULL,
    recent_activity_json TEXT NOT NULL,
    PRIMARY KEY(snapshot_id, project_id)
);
CREATE INDEX IF NOT EXISTS idx_project_snapshots_history ON project_snapshots(project_id, snapshot_id);
CREATE TABLE IF NOT EXISTS semantic_changes (
    semantic_change_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    project_id TEXT,
    workstream_id TEXT,
    change_kind TEXT NOT NULL,
    importance TEXT NOT NULL,
    importance_score REAL NOT NULL,
    event_count INTEGER NOT NULL,
    size_delta INTEGER NOT NULL,
    summary_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_semantic_changes_time ON semantic_changes(created_at, importance);
CREATE INDEX IF NOT EXISTS idx_semantic_changes_project ON semantic_changes(project_id, created_at);
CREATE TABLE IF NOT EXISTS asset_alerts (
    alert_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    file_id TEXT,
    project_id TEXT,
    path_before TEXT,
    alert_type TEXT NOT NULL,
    status TEXT NOT NULL,
    importance TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    resolved_at TEXT,
    resolution_event_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_asset_alerts_open ON asset_alerts(status, importance, first_seen);
CREATE TABLE IF NOT EXISTS project_activity (
    project_id TEXT PRIMARY KEY,
    activity_status TEXT NOT NULL,
    last_meaningful_activity TEXT,
    activity_score REAL NOT NULL,
    evidence_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workstream_activity (
    workstream_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    activity_status TEXT NOT NULL,
    last_meaningful_activity TEXT,
    activity_score REAL NOT NULL,
    evidence_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_tools (
    project_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    centrality TEXT NOT NULL,
    score REAL NOT NULL,
    evidence_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(project_id, tool_name)
);
CREATE TABLE IF NOT EXISTS file_card_semantics (
    file_id TEXT PRIMARY KEY,
    file_type TEXT,
    tier INTEGER,
    project_id TEXT,
    workstream_id TEXT,
    asset_role TEXT,
    importance TEXT,
    summary TEXT,
    authority TEXT NOT NULL,
    confidence REAL NOT NULL,
    last_changed TEXT,
    logical_size INTEGER,
    allocated_size INTEGER,
    allocated_size_complete INTEGER,
    rebuildable TEXT,
    rebuild_cost TEXT,
    canonical_status TEXT,
    supersedes_json TEXT NOT NULL,
    superseded_by_json TEXT NOT NULL,
    version_family TEXT,
    references_json TEXT NOT NULL,
    referenced_by_json TEXT NOT NULL,
    duplicate_group TEXT,
    archive_membership TEXT,
    producer_evidence_json TEXT NOT NULL,
    sensor_links_json TEXT NOT NULL,
    unresolved_questions_json TEXT NOT NULL,
    source_signature_json TEXT NOT NULL,
    content_identity TEXT,
    content_identity_kind TEXT,
    revision INTEGER NOT NULL,
    semantic_fingerprint TEXT NOT NULL,
    source_system TEXT NOT NULL,
    source_revision INTEGER,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_file_card_semantics_project ON file_card_semantics(project_id, workstream_id, asset_role);
CREATE INDEX IF NOT EXISTS idx_file_card_semantics_authority ON file_card_semantics(authority, canonical_status);
CREATE INDEX IF NOT EXISTS idx_file_card_semantics_content ON file_card_semantics(content_identity_kind, content_identity);
CREATE TABLE IF NOT EXISTS file_card_revisions (
    file_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    source_system TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    semantic_fingerprint TEXT NOT NULL,
    source_event_id TEXT,
    PRIMARY KEY(file_id, revision, source_system)
);
CREATE INDEX IF NOT EXISTS idx_file_card_revisions_time ON file_card_revisions(recorded_at, file_id);
CREATE TABLE IF NOT EXISTS file_card_evidence (
    evidence_id TEXT PRIMARY KEY,
    file_id TEXT NOT NULL,
    claim TEXT NOT NULL,
    authority TEXT NOT NULL,
    description TEXT NOT NULL,
    source_ref TEXT,
    confidence REAL NOT NULL,
    observed_at TEXT NOT NULL,
    semantic_eligible INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    source_system TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_file_card_evidence_subject ON file_card_evidence(file_id, claim, authority);
CREATE TABLE IF NOT EXISTS file_path_history (
    history_key TEXT PRIMARY KEY,
    file_id TEXT NOT NULL,
    path TEXT NOT NULL,
    observed_from TEXT NOT NULL,
    observed_to TEXT,
    event_type TEXT NOT NULL,
    source_event_id TEXT,
    source_system TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_file_path_history_file ON file_path_history(file_id, observed_from);
CREATE TABLE IF NOT EXISTS project_card_views (
    project_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL,
    semantic_fingerprint TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    authority TEXT NOT NULL,
    confidence REAL NOT NULL,
    source_system TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_card_revisions (
    project_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    source_system TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    semantic_fingerprint TEXT NOT NULL,
    PRIMARY KEY(project_id, revision, source_system)
);
CREATE TABLE IF NOT EXISTS project_relations (
    relation_id TEXT PRIMARY KEY,
    source_project_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    target_name TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    authority TEXT NOT NULL,
    confidence REAL NOT NULL,
    evidence_json TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL,
    source_system TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_project_relations_source ON project_relations(source_project_id, active);
CREATE TABLE IF NOT EXISTS assertion_events (
    event_key TEXT PRIMARY KEY,
    assertion_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    source_text TEXT,
    source_ref TEXT,
    revoked_at TEXT,
    payload_json TEXT NOT NULL,
    source_system TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assertion_events_assertion ON assertion_events(assertion_id, recorded_at);
CREATE TABLE IF NOT EXISTS sensor_capability_status (
    machine_binding TEXT NOT NULL,
    sensor_id TEXT NOT NULL,
    capability TEXT NOT NULL,
    health TEXT NOT NULL,
    available INTEGER NOT NULL,
    version TEXT,
    executable_hint TEXT,
    failure_reason TEXT,
    details_json TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    PRIMARY KEY(machine_binding, sensor_id, capability)
);
CREATE TABLE IF NOT EXISTS sensor_observations (
    observation_id TEXT PRIMARY KEY,
    machine_binding TEXT NOT NULL,
    sensor_id TEXT NOT NULL,
    capability TEXT NOT NULL,
    scope TEXT NOT NULL,
    subject_id TEXT,
    outcome TEXT NOT NULL,
    cache_status TEXT NOT NULL,
    normalized_data_json TEXT,
    confidence REAL NOT NULL,
    observed_at TEXT NOT NULL,
    raw_reference TEXT,
    error_json TEXT,
    metrics_json TEXT NOT NULL,
    upstream_errors_json TEXT NOT NULL,
    semantic_eligible INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sensor_observations_subject ON sensor_observations(machine_binding, subject_id, capability, observed_at);
CREATE TABLE IF NOT EXISTS legacy_imports (
    import_id TEXT PRIMARY KEY,
    source_kind TEXT NOT NULL,
    source_schema TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    source_machine_binding TEXT,
    target_machine_binding TEXT NOT NULL,
    reviewed_unbound_source INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    counts_json TEXT NOT NULL,
    conflicts_json TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_legacy_import_source ON legacy_imports(source_kind, source_sha256, target_machine_binding, status);
CREATE TABLE IF NOT EXISTS legacy_record_map (
    import_id TEXT NOT NULL,
    source_table TEXT NOT NULL,
    source_key TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_key TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    decision TEXT NOT NULL,
    details_json TEXT NOT NULL,
    PRIMARY KEY(import_id, source_table, source_key)
);
"""


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _execute_schema_statements(connection: sqlite3.Connection) -> None:
    for statement in SCHEMA_V3.split(";"):
        if statement.strip():
            connection.execute(statement)


def inspect_schema(catalog_path: Path) -> dict[str, Any]:
    if not catalog_path.is_file():
        return {"exists": False, "version": 0, "recognized": True, "migration_required": False}
    uri = f"file:{catalog_path.as_posix()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
        tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if user_version == 0 and {"files", "projects", "runs", "meta"}.issubset(tables):
            version = 1
        else:
            version = user_version
    return {
        "exists": True,
        "version": version,
        "user_version": user_version,
        "application_id": application_id,
        "recognized": version in {1, 2, SCHEMA_VERSION},
        "migration_required": version in {1, 2},
    }


def connect_current(catalog_path: Path, *, create: bool = False) -> sqlite3.Connection:
    state = inspect_schema(catalog_path)
    if state["exists"] and state["version"] != SCHEMA_VERSION:
        raise RuntimeError(
            f"Catalog schema v{state['version']} requires explicit migration to v{SCHEMA_VERSION}; no changes were made."
        )
    if not state["exists"] and not create:
        raise FileNotFoundError(catalog_path)
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(catalog_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(SCHEMA_V3)
    connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
    connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    connection.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version',?)", (str(SCHEMA_VERSION),))
    connection.execute(
        "INSERT OR REPLACE INTO meta(key,value) VALUES('reconciliation_extension_version',?)",
        (str(RECONCILIATION_EXTENSION_VERSION),),
    )
    return connection


def migration_plan(state_dir: Path) -> dict[str, Any]:
    catalog = state_dir / "catalog.db"
    schema = inspect_schema(catalog)
    return {
        "schema": schema,
        "target_version": SCHEMA_VERSION,
        "action": f"MIGRATE_V{schema.get('version')}_TO_V{SCHEMA_VERSION}" if schema.get("migration_required") else "NO_ACTION",
        "backup_required": bool(schema.get("migration_required")),
        "rollback_supported": True,
        "physical_project_actions": 0,
    }


def _digest(payload: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in payload.items() if key != "digest"}
    return hashlib.sha256(json.dumps(unsigned, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _consistent_catalog_backup(source: Path, target: Path) -> None:
    """Create a transactionally consistent SQLite backup, including WAL content."""
    source_connection = sqlite3.connect(source)
    target_connection = sqlite3.connect(target)
    try:
        source_connection.backup(target_connection)
    finally:
        target_connection.close()
        source_connection.close()


def _add_v2_columns(connection: sqlite3.Connection) -> None:
    file_columns = _columns(connection, "files")
    for name, declaration in (
        ("sample_fingerprint", "TEXT"),
        ("full_sha256", "TEXT"),
        ("fingerprint_stage", "INTEGER NOT NULL DEFAULT 0"),
        ("volatile_class", "TEXT"),
    ):
        if name not in file_columns:
            connection.execute(f"ALTER TABLE files ADD COLUMN {name} {declaration}")
    project_columns = _columns(connection, "projects")
    for name, declaration in (
        ("root_path", "TEXT"),
        ("purpose", "TEXT"),
        ("lifecycle", "TEXT"),
        ("confidence", "REAL"),
        ("evidence_json", "TEXT"),
        ("parent_project_id", "TEXT"),
    ):
        if name not in project_columns:
            connection.execute(f"ALTER TABLE projects ADD COLUMN {name} {declaration}")


def _add_v3_columns(connection: sqlite3.Connection) -> None:
    file_columns = _columns(connection, "files")
    for name, declaration in (
        ("file_id", "TEXT"),
        ("native_file_id", "TEXT"),
        ("identity_confidence", "REAL"),
        ("identity_evidence_json", "TEXT"),
    ):
        if name not in file_columns:
            connection.execute(f"ALTER TABLE files ADD COLUMN {name} {declaration}")
    asset_columns = _columns(connection, "assets") if "assets" in {
        str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    } else set()
    for name, declaration in (
        ("asset_kind", "TEXT NOT NULL DEFAULT 'file'"),
        ("entity_path", "TEXT"),
        ("authority_scope", "TEXT NOT NULL DEFAULT 'FILE_LOCAL'"),
        ("authority_context_id", "TEXT"),
    ):
        if asset_columns and name not in asset_columns:
            connection.execute(f"ALTER TABLE assets ADD COLUMN {name} {declaration}")


def _populate_file_cards(connection: sqlite3.Connection) -> None:
    rows = [dict(row) for row in connection.execute(
        "SELECT path_key,path,status,first_seen,last_seen,file_id,native_file_id,identity_confidence,identity_evidence_json FROM files"
    )]
    updates: list[tuple[str, float, str, str]] = []
    cards: dict[str, tuple[Any, ...]] = {}
    for row in rows:
        file_id = row.get("file_id") or "file_" + hashlib.sha256(str(row["path_key"]).encode("utf-8")).hexdigest()[:24]
        evidence = row.get("identity_evidence_json") or json.dumps(
            [{"type": "migration", "detail": "Stable identity initialized from the existing canonical path key."}],
            ensure_ascii=False,
        )
        confidence = float(row.get("identity_confidence") or 0.7)
        updates.append((file_id, confidence, evidence, row["path_key"]))
        candidate = (
            file_id, row.get("native_file_id"), row["path_key"], row["first_seen"], row["last_seen"], row["status"],
            confidence, evidence, None, None, None,
        )
        prior = cards.get(file_id)
        if prior is None or (prior[5] != "present" and row["status"] == "present"):
            cards[file_id] = candidate
    connection.executemany(
        "UPDATE files SET file_id=?,identity_confidence=?,identity_evidence_json=? WHERE path_key=?", updates
    )
    connection.executemany(
        """INSERT OR REPLACE INTO file_cards(
            file_id,native_file_id,current_path_key,first_seen,last_seen,status,identity_confidence,
            identity_evidence_json,last_role,last_authority,authority_scope
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        cards.values(),
    )


def migrate_to_current(state_dir: Path, *, apply: bool = False) -> dict[str, Any]:
    plan = migration_plan(state_dir)
    if not apply or plan["action"] == "NO_ACTION":
        return {**plan, "applied": False}
    catalog = state_dir / "catalog.db"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    source_version = int(plan["schema"].get("version") or 0)
    backup = state_dir / "migrations" / f"schema-v{source_version}-{stamp}"
    backup.mkdir(parents=True, exist_ok=False)
    copied: list[dict[str, Any]] = []
    for name in ("catalog.db", "baseline.json", "last_changes.json"):
        source = state_dir / name
        if source.is_file():
            target = backup / name
            if name == "catalog.db":
                _consistent_catalog_backup(source, target)
            else:
                shutil.copy2(source, target)
            copied.append({"name": name, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
    migration_id = f"migration_v{source_version}_v{SCHEMA_VERSION}_{stamp}"
    (backup / "backup_manifest.json").write_text(
        json.dumps({"migration_id": migration_id, "files": copied}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    connection = sqlite3.connect(catalog)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN IMMEDIATE")
        if source_version == 1:
            _add_v2_columns(connection)
        _add_v3_columns(connection)
        _execute_schema_statements(connection)
        _populate_file_cards(connection)
        connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        connection.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version',?)", (str(SCHEMA_VERSION),))
        connection.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES('reconciliation_extension_version',?)",
            (str(RECONCILIATION_EXTENSION_VERSION),),
        )
        connection.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('last_migration_backup',?)", (str(backup),))
        connection.execute(
            "INSERT INTO migrations(migration_id,from_version,to_version,applied_at,backup_path,status) VALUES(?,?,?,?,?,?)",
            (migration_id, source_version, SCHEMA_VERSION, datetime.now(timezone.utc).isoformat(timespec="seconds"), str(backup), "APPLIED"),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    baseline_path = state_dir / "baseline.json"
    if baseline_path.is_file():
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        baseline["schema_version"] = SCHEMA_VERSION
        baseline["migration"] = {"id": migration_id, "from": source_version, "to": SCHEMA_VERSION, "backup_path": str(backup)}
        baseline["digest"] = _digest(baseline)
        temporary = baseline_path.with_suffix(".json.migration.tmp")
        temporary.write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, baseline_path)
    return {**plan, "applied": True, "migration_id": migration_id, "backup_path": str(backup), "backup_files": copied}


def migrate_v1_to_v2(state_dir: Path, *, apply: bool = False) -> dict[str, Any]:
    """Backward-compatible entry point; all recognized legacy catalogs migrate to the current schema."""
    return migrate_to_current(state_dir, apply=apply)


def rollback_migration(state_dir: Path, backup_path: Path, *, apply: bool = False) -> dict[str, Any]:
    manifest = backup_path / "backup_manifest.json"
    if not manifest.is_file():
        raise ValueError("Migration backup manifest is missing.")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    checks: list[dict[str, Any]] = []
    for item in payload.get("files", []):
        source = backup_path / item["name"]
        actual = hashlib.sha256(source.read_bytes()).hexdigest()
        checks.append({"name": item["name"], "valid": actual == item["sha256"]})
    if not all(item["valid"] for item in checks):
        raise ValueError("Migration backup integrity check failed.")
    if not apply:
        return {"action": "ROLLBACK", "applied": False, "checks": checks, "physical_project_actions": 0}
    catalog = state_dir / "catalog.db"
    if catalog.is_file():
        checkpoint = sqlite3.connect(catalog)
        try:
            checkpoint.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            checkpoint.close()
    sidecar_archive = backup_path / "rollback_superseded_sidecars"
    moved_sidecars: list[str] = []
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(catalog) + suffix)
        if not sidecar.exists():
            continue
        sidecar_archive.mkdir(parents=True, exist_ok=True)
        target = sidecar_archive / sidecar.name
        os.replace(sidecar, target)
        moved_sidecars.append(str(target))
    for item in payload.get("files", []):
        source = backup_path / item["name"]
        target = state_dir / item["name"]
        temporary = target.with_suffix(target.suffix + ".rollback.tmp")
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    return {
        "action": "ROLLBACK", "applied": True, "checks": checks,
        "superseded_sidecars_preserved": moved_sidecars, "physical_project_actions": 0,
    }
