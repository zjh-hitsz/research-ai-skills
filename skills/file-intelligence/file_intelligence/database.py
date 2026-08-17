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


SCHEMA_VERSION = 2
APPLICATION_ID = 0x46494E54  # "FINT"


SCHEMA_V2 = """
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
    missing_since TEXT
);
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
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assets_project ON assets(project_id, authority_level, role);
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
"""


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _execute_schema_statements(connection: sqlite3.Connection) -> None:
    for statement in SCHEMA_V2.split(";"):
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
        "recognized": version in {1, SCHEMA_VERSION},
        "migration_required": version == 1,
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
    connection.executescript(SCHEMA_V2)
    connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
    connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    connection.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version',?)", (str(SCHEMA_VERSION),))
    return connection


def migration_plan(state_dir: Path) -> dict[str, Any]:
    catalog = state_dir / "catalog.db"
    schema = inspect_schema(catalog)
    return {
        "schema": schema,
        "target_version": SCHEMA_VERSION,
        "action": "MIGRATE_V1_TO_V2" if schema.get("migration_required") else "NO_ACTION",
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


def migrate_v1_to_v2(state_dir: Path, *, apply: bool = False) -> dict[str, Any]:
    plan = migration_plan(state_dir)
    if not apply or plan["action"] == "NO_ACTION":
        return {**plan, "applied": False}
    catalog = state_dir / "catalog.db"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = state_dir / "migrations" / f"schema-v1-{stamp}"
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
    migration_id = f"migration_v1_v2_{stamp}"
    (backup / "backup_manifest.json").write_text(
        json.dumps({"migration_id": migration_id, "files": copied}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    connection = sqlite3.connect(catalog)
    try:
        connection.execute("BEGIN IMMEDIATE")
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
        _execute_schema_statements(connection)
        connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        connection.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version',?)", (str(SCHEMA_VERSION),))
        connection.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('last_migration_backup',?)", (str(backup),))
        connection.execute(
            "INSERT INTO migrations(migration_id,from_version,to_version,applied_at,backup_path,status) VALUES(?,?,?,?,?,?)",
            (migration_id, 1, 2, datetime.now(timezone.utc).isoformat(timespec="seconds"), str(backup), "APPLIED"),
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
        baseline["migration"] = {"id": migration_id, "from": 1, "to": 2, "backup_path": str(backup)}
        baseline["digest"] = _digest(baseline)
        temporary = baseline_path.with_suffix(".json.migration.tmp")
        temporary.write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, baseline_path)
    return {**plan, "applied": True, "migration_id": migration_id, "backup_path": str(backup), "backup_files": copied}


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
    for item in payload.get("files", []):
        source = backup_path / item["name"]
        target = state_dir / item["name"]
        temporary = target.with_suffix(target.suffix + ".rollback.tmp")
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    return {"action": "ROLLBACK", "applied": True, "checks": checks, "physical_project_actions": 0}
