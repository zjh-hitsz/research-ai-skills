from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from file_intelligence.database import connect_current, inspect_schema
from file_intelligence.engine import (
    deep_onboard,
    file_history,
    get_asset_details,
    maintain,
    migrate_state,
    project_history,
    retention,
    rollback_state,
    storage_history,
    timeline_context,
    timeline_history,
    understand_project,
)
from file_intelligence.timeline import append_event


class TimelineTests(unittest.TestCase):
    def test_default_maintenance_inherits_filesystem_baseline_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "research" / "project_backend"
            state = root / "state"
            project.mkdir(parents=True)
            (project / "README.md").write_text("# Backend\n", encoding="utf-8")
            deep_onboard([root / "research"], state_dir=state, backend="filesystem")

            from file_intelligence import engine

            original_scan = engine.scan
            with mock.patch("file_intelligence.engine.scan", wraps=original_scan) as scanned:
                result = maintain(state_dir=state)
            self.assertEqual(scanned.call_args.args[2], "filesystem")
            self.assertEqual(result["backend"], "filesystem-fallback")
            self.assertEqual(result["filesystem_changes"], 0)
            self.assertEqual(result["events_recorded"], 0)

    def test_noop_and_volatile_change_have_zero_meaningful_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "research" / "project_alpha"
            state = root / "state"
            project.mkdir(parents=True)
            (project / "README.md").write_text("# Project Alpha\n", encoding="utf-8")
            runtime = project / "QQ" / "runtime.wal"
            runtime.parent.mkdir()
            runtime.write_text("1", encoding="utf-8")
            deep_onboard([root / "research"], state_dir=state, backend="filesystem")
            understand_project(project, state_dir=state)

            first = maintain(state_dir=state, backend="filesystem")
            second = maintain(state_dir=state, backend="filesystem")
            self.assertEqual(first["meaningful_changes"], 0)
            self.assertEqual(second["meaningful_changes"], 0)
            self.assertEqual(second["events_recorded"], 0)

            runtime.write_text("volatile growth", encoding="utf-8")
            volatile = maintain(state_dir=state, backend="filesystem")
            self.assertEqual(volatile["meaningful_changes"], 0)
            self.assertEqual(volatile["semantic_summary"]["volatile_raw_change_count"], 1)

    def test_move_rename_copy_missing_reappear_and_large_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scan_root = root / "research"
            project = scan_root / "project_beta"
            state = root / "state"
            working = project / "working" / "model.mph"
            working.parent.mkdir(parents=True)
            working.write_bytes(b"stable-model-content")
            (project / "README.md").write_text(
                "# Project Beta\n\n## Authority\n- `working/model.mph`\n", encoding="utf-8",
            )
            deep_onboard([scan_root], state_dir=state, backend="filesystem")
            understand_project(project, state_dir=state)
            original = get_asset_details(working, state_dir=state)
            original_id = original["file_id"]

            final = project / "final" / "canonical_model.mph"
            final.parent.mkdir()
            working.rename(final)
            moved = maintain(state_dir=state, backend="filesystem")
            self.assertEqual(moved["identity_events"]["MOVED"], 1)
            self.assertTrue(
                any(item["event_type"] == "FILE_MOVED" for item in moved["semantic_summary"]["important_changes"]),
                moved["semantic_summary"],
            )
            self.assertEqual(get_asset_details(final, state_dir=state)["file_id"], original_id)

            renamed = final.with_name("canonical_model_v2.mph")
            final.rename(renamed)
            renamed_result = maintain(state_dir=state, backend="filesystem")
            self.assertEqual(renamed_result["identity_events"]["RENAMED"], 1)
            self.assertEqual(get_asset_details(renamed, state_dir=state)["file_id"], original_id)

            copied = project / "handoff" / "canonical_model_v2.mph"
            copied.parent.mkdir()
            shutil.copy2(renamed, copied)
            copied_result = maintain(state_dir=state, backend="filesystem")
            self.assertEqual(copied_result["identity_events"]["COPIED"], 1)
            self.assertGreaterEqual(copied_result["meaningful_changes"], 1)
            self.assertTrue(any(
                item["event_type"] == "FILE_COPIED"
                for item in copied_result["semantic_summary"]["important_changes"]
            ))
            self.assertNotEqual(get_asset_details(copied, state_dir=state)["file_id"], original_id)

            understand_project(project, state_dir=state)
            renamed.unlink()
            preserved = maintain(state_dir=state, backend="filesystem")
            missing_events = [item for item in timeline_history(state_dir=state, since_last_scan=True)["events"] if item["event_type"] == "FILE_MISSING"]
            self.assertTrue(missing_events)
            self.assertIn(missing_events[0]["resolution_status"], {"content_copy_candidate", "archived_copy_candidate", "content_copy_verified", "archived_copy_verified"})
            self.assertFalse(preserved["semantic_summary"]["asset_alerts"])

            shutil.copy2(copied, renamed)
            reappeared = maintain(state_dir=state, backend="filesystem")
            self.assertEqual(reappeared["counts"]["reappeared"], 1)

            sparse = project / "models" / "new_large_case.cas.h5"
            sparse.parent.mkdir()
            with sparse.open("wb") as handle:
                handle.seek(5 * 1024**3 - 1)
                handle.write(b"\0")
            large = maintain(state_dir=state, backend="filesystem")
            self.assertTrue(any(item["event_type"] == "LARGE_ASSET_CREATED" for item in large["semantic_summary"]["important_changes"]))

            history = file_history(file=original_id, state_dir=state)
            self.assertEqual(history["file_card"]["file_id"], original_id)
            self.assertTrue(any(item["event_type"] in {"FILE_MOVED", "FILE_RENAMED"} for item in history["events"]))

    def test_important_asset_loss_alert_and_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scan_root = root / "research"
            project = scan_root / "project_gamma"
            state = root / "state"
            model = project / "final" / "canonical.cas.h5"
            model.parent.mkdir(parents=True)
            model.write_bytes(b"unique-authority-model")
            (project / "README.md").write_text("# Gamma\n\n## Authority\n- `final/canonical.cas.h5`\n", encoding="utf-8")
            deep_onboard([scan_root], state_dir=state, backend="filesystem")
            understand_project(project, state_dir=state)
            model_id = get_asset_details(model, state_dir=state)["file_id"]

            payload = model.read_bytes()
            model.unlink()
            missing = maintain(state_dir=state, backend="filesystem")
            self.assertTrue(any(item["event_type"] == "POSSIBLE_ASSET_LOSS" for item in missing["semantic_summary"]["important_changes"]))
            self.assertEqual(len(missing["semantic_summary"]["asset_alerts"]), 1)

            model.write_bytes(payload)
            maintain(state_dir=state, backend="filesystem")
            with closing(sqlite3.connect(state / "catalog.db")) as connection:
                open_alerts = connection.execute("SELECT COUNT(*) FROM asset_alerts WHERE file_id=? AND status='OPEN'", (model_id,)).fetchone()[0]
            self.assertEqual(open_alerts, 0)

    def test_semantic_grouping_prioritizes_final_over_temp_growth(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scan_root = root / "research"
            project = scan_root / "project_delta"
            state = root / "state"
            manuscript = project / "final" / "final_manuscript.docx"
            manuscript.parent.mkdir(parents=True)
            manuscript.write_bytes(b"draft-v1")
            (project / "README.md").write_text("# Delta\n\n## Authority\n- `final/final_manuscript.docx`\n", encoding="utf-8")
            deep_onboard([scan_root], state_dir=state, backend="filesystem")
            understand_project(project, state_dir=state)

            temp_dir = project / "tmp"
            temp_dir.mkdir()
            for index in range(500):
                (temp_dir / f"frame_{index:04d}.tmp").write_bytes(b"x" * 32)
            manuscript.write_bytes(b"final-manuscript-v2")
            result = maintain(state_dir=state, backend="filesystem")
            summary = timeline_context(state_dir=state, since_last_scan=True)
            self.assertGreaterEqual(result["meaningful_changes"], 1)
            self.assertEqual(summary["important_changes"][0]["event_type"], "FILE_CHANGED")
            self.assertLess(summary["raw_event_count"], 20)
            events = timeline_history(state_dir=state, since_last_scan=True)["events"]
            aggregate = next(item for item in events if item["event_type"] == "AGGREGATE_CREATED")
            self.assertEqual(aggregate["aggregate_count"], 500)
            self.assertIn("500 aggregate members", (state / "Computer Timeline.html").read_text(encoding="utf-8"))
            self.assertIn("500 members", (state / "File Intelligence Home.html").read_text(encoding="utf-8"))

    def test_project_becomes_dormant_from_meaningful_activity_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scan_root = root / "research"
            project = scan_root / "project_theta"
            state = root / "state"
            project.mkdir(parents=True)
            (project / "README.md").write_text("# Theta\n", encoding="utf-8")
            deep_onboard([scan_root], state_dir=state, backend="filesystem")
            understood = understand_project(project, state_dir=state)
            old = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat(timespec="seconds")
            with closing(sqlite3.connect(state / "catalog.db")) as connection:
                connection.execute(
                    "UPDATE project_activity SET activity_status='ACTIVE',last_meaningful_activity=? WHERE project_id=?",
                    (old, understood["project"]["project_id"]),
                )
                connection.commit()
            result = maintain(state_dir=state, backend="filesystem")
            self.assertTrue(any(item["event_type"] == "PROJECT_BECAME_DORMANT" for item in result["semantic_summary"]["important_changes"]))

    def test_time_queries_project_history_and_storage_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scan_root = root / "research"
            project = scan_root / "project_epsilon"
            state = root / "state"
            project.mkdir(parents=True)
            (project / "README.md").write_text("# Epsilon\n", encoding="utf-8")
            data = project / "result.dat"
            data.write_bytes(b"1")
            deep_onboard([scan_root], state_dir=state, backend="filesystem")
            understood = understand_project(project, state_dir=state)
            data.write_bytes(b"123456")
            maintain(state_dir=state, backend="filesystem")

            self.assertTrue(timeline_history(state_dir=state, since_last_scan=True)["events"])
            self.assertTrue(timeline_history(state_dir=state, days=1)["events"])
            tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(timespec="seconds")
            self.assertFalse(timeline_history(state_dir=state, from_value=tomorrow)["events"])
            history = project_history(project=understood["project"]["project_id"], state_dir=state)
            self.assertTrue(history["events"])
            growth = storage_history(state_dir=state, days=7)
            self.assertIn(growth["status"], {"OK", "INSUFFICIENT_HISTORY"})

            with closing(connect_current(state / "catalog.db")) as connection:
                connection.execute(
                    "INSERT INTO runs(run_id,mode,status,created_at,summary_json) VALUES(?,?,?,?,?)",
                    ("synthetic_cutover", "STATE_CUTOVER", "COMPLETED", "2000-01-01T00:00:00+00:00", "{}"),
                )
                connection.execute(
                    """INSERT INTO snapshots(
                        snapshot_id,created_at,snapshot_kind,run_id,files_count,logical_size,
                        project_count,active_projects,frozen_projects,authority_asset_count,aggregate_size,
                        potential_cleanup,potential_archive,important_asset_summary_json,catalog_digest
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    ("synthetic_cutover_snapshot", "2000-01-01T00:00:00+00:00", "manual", "synthetic_cutover",
                     999, 999999999, 1, 1, 0, 0, 0, 0, 0, "[]", "synthetic"),
                )
                connection.execute(
                    """INSERT INTO project_snapshots(
                        snapshot_id,project_id,project_name,total_size,file_count,lifecycle,activity_status,
                        workstream_status_json,authority_summary_json,recent_activity_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    ("synthetic_cutover_snapshot", understood["project"]["project_id"], "project_epsilon",
                     999999999, 999, "UNKNOWN", "UNKNOWN", "[]", "[]", "[]"),
                )
                connection.commit()
            growth = storage_history(state_dir=state, days=7)
            self.assertEqual(growth["status"], "OK")
            self.assertEqual(growth["excluded_state_cutover_snapshots"], 1)
            self.assertNotEqual(growth["from"], "2000-01-01T00:00:00+00:00")

    def test_v2_to_v3_additive_migration_is_backup_first_and_rollbackable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            state.mkdir()
            catalog = state / "catalog.db"
            connection = sqlite3.connect(catalog)
            connection.executescript(
                """
                CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE files(path_key TEXT PRIMARY KEY,path TEXT NOT NULL,root_path TEXT NOT NULL,relative_path TEXT NOT NULL,
                    filename TEXT NOT NULL,extension TEXT NOT NULL,size INTEGER NOT NULL,modified TEXT NOT NULL,created TEXT NOT NULL,
                    scope_kind TEXT NOT NULL,is_communication INTEGER NOT NULL,project_id TEXT,project_name TEXT,fingerprint TEXT,
                    sample_fingerprint TEXT,full_sha256 TEXT,fingerprint_stage INTEGER NOT NULL DEFAULT 0,volatile_class TEXT,status TEXT NOT NULL,
                    first_seen TEXT NOT NULL,last_seen TEXT NOT NULL,missing_since TEXT);
                CREATE TABLE projects(project_id TEXT PRIMARY KEY,name TEXT NOT NULL,file_count INTEGER NOT NULL,total_size INTEGER NOT NULL,
                    status TEXT NOT NULL,root_path TEXT,purpose TEXT,lifecycle TEXT,confidence REAL,evidence_json TEXT,parent_project_id TEXT);
                CREATE TABLE assets(path_key TEXT PRIMARY KEY,project_id TEXT,workstream_id TEXT,role TEXT NOT NULL,authority_level TEXT NOT NULL,
                    confidence REAL NOT NULL,evidence_json TEXT NOT NULL,provenance_type TEXT NOT NULL,rebuildability TEXT,
                    archive_recommendation TEXT,superseded_by_path_key TEXT,updated_at TEXT NOT NULL);
                CREATE TABLE migrations(migration_id TEXT PRIMARY KEY,from_version INTEGER NOT NULL,to_version INTEGER NOT NULL,
                    applied_at TEXT NOT NULL,backup_path TEXT NOT NULL,status TEXT NOT NULL);
                CREATE TABLE runs(run_id TEXT PRIMARY KEY,mode TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,summary_json TEXT NOT NULL);
                PRAGMA user_version=2;
                PRAGMA application_id=1179209300;
                """
            )
            stamp = "2026-01-01T00:00:00+00:00"
            path = root / "synthetic.dat"
            connection.execute(
                "INSERT INTO files(path_key,path,root_path,relative_path,filename,extension,size,modified,created,scope_kind,is_communication,status,first_seen,last_seen) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (str(path).casefold(), str(path), str(root), path.name, path.name, ".dat", 10, "1", "1", "primary", 0, "present", stamp, stamp),
            )
            connection.execute("INSERT INTO meta(key,value) VALUES('wal_marker','v2-preserved')")
            connection.commit()
            connection.close()
            baseline = {
                "schema_version": 2, "baseline_id": "v2", "version": "0.2.0", "generated_at": stamp,
                "mode": "MAINTENANCE_READY", "machine_binding": "synthetic", "roots": [], "counts": {},
                "real_execution_enabled": False, "digest": "synthetic",
            }
            (state / "baseline.json").write_text(json.dumps(baseline), encoding="utf-8")
            (state / "last_changes.json").write_text("{}", encoding="utf-8")

            preview = migrate_state(state_dir=state)
            self.assertEqual(preview["action"], "MIGRATE_V2_TO_V3")
            self.assertFalse(preview["applied"])
            applied = migrate_state(state_dir=state, apply=True)
            self.assertEqual(inspect_schema(catalog)["version"], 3)
            with closing(sqlite3.connect(catalog)) as migrated:
                self.assertEqual(migrated.execute("SELECT COUNT(*) FROM file_cards").fetchone()[0], 1)
                self.assertEqual(migrated.execute("SELECT value FROM meta WHERE key='wal_marker'").fetchone()[0], "v2-preserved")
            rollback_state(state_dir=state, backup_path=applied["backup_path"], apply=True)
            self.assertEqual(inspect_schema(catalog)["version"], 2)

    def test_retention_removes_only_old_volatile_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "research" / "project_retention"
            state = root / "state"
            project.mkdir(parents=True)
            (project / "README.md").write_text("# Retention\n", encoding="utf-8")
            deep_onboard([root / "research"], state_dir=state, backend="filesystem")
            old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat(timespec="seconds")
            with closing(sqlite3.connect(state / "catalog.db")) as connection:
                connection.row_factory = sqlite3.Row
                append_event(connection, {
                    "event_type": "VOLATILE_CHANGE_AGGREGATE", "occurred_at": old, "subject_type": "aggregate",
                    "run_id": "old-volatile", "semantic_importance": "VOLATILE", "importance_score": 0,
                    "importance_reasons": ["volatile"], "confidence": 1.0, "evidence": [], "volatile_class": "runtime_log",
                })
                append_event(connection, {
                    "event_type": "AUTHORITY_CHANGED", "occurred_at": old, "subject_type": "asset",
                    "run_id": "old-authority", "semantic_importance": "HIGH", "importance_score": 80,
                    "importance_reasons": ["authority"], "confidence": 1.0, "evidence": [], "permanent": True,
                })
                connection.commit()
            preview = retention(state_dir=state)
            self.assertEqual(preview["volatile_events_eligible"], 1)
            retention(state_dir=state, apply=True)
            with closing(sqlite3.connect(state / "catalog.db")) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM events WHERE run_id='old-volatile'").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM events WHERE run_id='old-authority'").fetchone()[0], 1)

    def test_authority_scope_directory_authority_and_tool_centrality(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scan_root = root / "research"
            project = scan_root / "project_zeta"
            state = root / "state"
            final_dir = project / "final_results"
            reference_dir = project / "reference"
            final_dir.mkdir(parents=True)
            reference_dir.mkdir()
            (final_dir / "baseline.cas.h5").write_bytes(b"fluent-case")
            (final_dir / "baseline.dat.h5").write_bytes(b"fluent-data")
            (reference_dir / "example.mph").write_bytes(b"reference-comsol")
            (project / "README.md").write_text(
                "# Zeta\nPrimary solver: Fluent. COMSOL is reference evidence only.\n\n## Authority\n- `final_results/`\n",
                encoding="utf-8",
            )
            deep_onboard([scan_root], state_dir=state, backend="filesystem")
            result = understand_project(project, state_dir=state)
            tools = {item["tool_name"]: item["centrality"] for item in result["tool_roles"]}
            self.assertEqual(tools["Fluent"], "PRIMARY_TOOL")
            self.assertEqual(tools["COMSOL"], "REFERENCE_TOOL")
            directory = get_asset_details(final_dir, state_dir=state)
            self.assertEqual(directory["asset_kind"], "directory")
            self.assertEqual(directory["authority_level"], "CANONICAL")
            self.assertEqual(directory["authority_scope"], "PROJECT_WIDE")


if __name__ == "__main__":
    unittest.main()
