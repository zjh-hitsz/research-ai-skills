from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from file_intelligence.database import inspect_schema
from file_intelligence.engine import (
    _scan_everything,
    _scan_filesystem,
    deep_onboard,
    machine_binding,
    migrate_state,
    rollback_state,
    status,
)


def _create_v1_state(state: Path, scanned_root: Path) -> None:
    state.mkdir(parents=True)
    with closing(sqlite3.connect(state / "catalog.db")) as connection:
        connection.executescript(
            """
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE files (
                path_key TEXT PRIMARY KEY,path TEXT NOT NULL,root_path TEXT NOT NULL,relative_path TEXT NOT NULL,
                filename TEXT NOT NULL,extension TEXT NOT NULL,size INTEGER NOT NULL,modified TEXT NOT NULL,
                created TEXT NOT NULL,scope_kind TEXT NOT NULL,is_communication INTEGER NOT NULL,project_id TEXT,
                project_name TEXT,fingerprint TEXT,status TEXT NOT NULL,first_seen TEXT NOT NULL,last_seen TEXT NOT NULL,
                missing_since TEXT
            );
            CREATE TABLE projects (project_id TEXT PRIMARY KEY,name TEXT NOT NULL,file_count INTEGER NOT NULL,total_size INTEGER NOT NULL,status TEXT NOT NULL);
            CREATE TABLE runs (run_id TEXT PRIMARY KEY,mode TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,summary_json TEXT NOT NULL);
            """
        )
        connection.commit()
    baseline = {
        "schema_version": 1,
        "baseline_id": "synthetic-v1",
        "version": "0.1.0",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "mode": "MAINTENANCE_READY",
        "machine_binding": machine_binding(),
        "roots": [{"path": str(scanned_root.resolve()), "kind": "primary"}],
        "backend": "filesystem-fallback",
        "counts": {"files": 0},
        "real_execution_enabled": False,
        "digest": "synthetic",
    }
    (state / "baseline.json").write_text(json.dumps(baseline), encoding="utf-8")
    (state / "last_changes.json").write_text("{}", encoding="utf-8")


class MigrationAndSafetyTests(unittest.TestCase):
    def test_migration_requires_explicit_apply_and_is_rollbackable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            scanned = root / "project"
            scanned.mkdir()
            _create_v1_state(state, scanned)
            wal_writer = sqlite3.connect(state / "catalog.db")
            wal_writer.execute("PRAGMA journal_mode=WAL")
            wal_writer.execute("INSERT INTO meta(key,value) VALUES('wal_marker','preserved')")
            wal_writer.commit()
            before_hash = hashlib.sha256((state / "catalog.db").read_bytes()).hexdigest()

            result = status(state_dir=state)
            self.assertEqual(result["status"], "MIGRATION_REQUIRED")
            self.assertEqual(inspect_schema(state / "catalog.db")["version"], 1)
            preview = migrate_state(state_dir=state)
            self.assertFalse(preview["applied"])
            self.assertEqual(hashlib.sha256((state / "catalog.db").read_bytes()).hexdigest(), before_hash)

            applied = migrate_state(state_dir=state, apply=True)
            self.assertTrue(applied["applied"])
            self.assertEqual(inspect_schema(state / "catalog.db")["version"], 2)
            backup_catalog = Path(applied["backup_path"]) / "catalog.db"
            self.assertTrue((Path(applied["backup_path"]) / "backup_manifest.json").is_file())
            with closing(sqlite3.connect(backup_catalog)) as backup_connection:
                self.assertEqual(backup_connection.execute("SELECT value FROM meta WHERE key='wal_marker'").fetchone()[0], "preserved")
            self.assertEqual(status(state_dir=state)["status"], "MAINTENANCE_READY")
            wal_writer.close()

            rollback_preview = rollback_state(state_dir=state, backup_path=applied["backup_path"])
            self.assertFalse(rollback_preview["applied"])
            rolled_back = rollback_state(state_dir=state, backup_path=applied["backup_path"], apply=True)
            self.assertTrue(rolled_back["applied"])
            self.assertEqual(inspect_schema(state / "catalog.db")["version"], 1)
            with closing(sqlite3.connect(state / "catalog.db")) as restored_connection:
                self.assertEqual(restored_connection.execute("SELECT value FROM meta WHERE key='wal_marker'").fetchone()[0], "preserved")

    def test_interrupted_scan_does_not_create_catalog_or_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            state = root / "state"
            project.mkdir()
            with mock.patch("file_intelligence.engine.scan", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    deep_onboard([project], state_dir=state, backend="filesystem")
            self.assertFalse((state / "catalog.db").exists())
            self.assertFalse((state / "baseline.json").exists())

    def test_project_files_remain_byte_and_timestamp_identical(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            source = project / "source.dat"
            source.write_bytes(b"immutable synthetic source")
            before = (hashlib.sha256(source.read_bytes()).hexdigest(), source.stat().st_mtime_ns)
            deep_onboard([project], state_dir=root / "external-state", backend="filesystem")
            after = (hashlib.sha256(source.read_bytes()).hexdigest(), source.stat().st_mtime_ns)
            self.assertEqual(before, after)

    def test_everything_unavailable_uses_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            (project / "file.txt").write_text("synthetic", encoding="utf-8")
            with mock.patch("file_intelligence.engine.find_everything_cli", return_value=None):
                result = deep_onboard([project], state_dir=root / "state", backend="auto")
            self.assertEqual(result["backend"], "filesystem-fallback")
            self.assertIn("fallback", result["warning"])

    def test_everything_json_parser_matches_filesystem_for_unicode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "中文项目"
            project.mkdir()
            first = project / "数据.csv"
            second = project / "model.mph"
            first.write_text("x,y\n", encoding="utf-8")
            second.write_bytes(b"model")
            state = root / "state"
            scope = [{"path": str(project.resolve()), "kind": "primary"}]
            filesystem = _scan_filesystem(scope, state)

            def fake_run(command: list[str], **_: object) -> SimpleNamespace:
                export = Path(command[command.index("-export-json") + 1])
                rows = []
                for path in (first, second):
                    stat = path.stat()
                    rows.append(
                        {
                            "filename": str(path.resolve()),
                            "size": stat.st_size,
                            "date_modified": str(stat.st_mtime_ns),
                            "date_created": str(stat.st_ctime_ns),
                        }
                    )
                export.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8-sig")
                return SimpleNamespace(returncode=0, stderr=b"")

            fake_es = root / "es.exe"
            fake_es.write_bytes(b"synthetic")
            with mock.patch("file_intelligence.engine.subprocess.run", side_effect=fake_run):
                everything = _scan_everything(scope, state, fake_es)
            fs_values = [(row["path_key"], row["size"]) for row in filesystem["records"]]
            es_values = [(row["path_key"], row["size"]) for row in everything["records"]]
            self.assertEqual(fs_values, es_values)

    def test_access_denied_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            root.mkdir(exist_ok=True)

            def denied_walk(path: Path, **kwargs: object):
                onerror = kwargs["onerror"]
                error = PermissionError(13, "synthetic access denied", str(path / "denied"))
                onerror(error)
                return iter(())

            with mock.patch("file_intelligence.engine.os.walk", side_effect=denied_walk):
                result = _scan_filesystem([{"path": str(root), "kind": "primary"}], state)
            self.assertEqual(len(result["errors"]), 1)
            self.assertIn("access denied", result["errors"][0]["error"])

    def test_symlink_or_reparse_directory_is_not_followed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            outside = root / "outside"
            project.mkdir()
            outside.mkdir()
            (outside / "secret.txt").write_text("outside", encoding="utf-8")
            link = project / "junction_like"
            try:
                os.symlink(outside, link, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"Creating a synthetic reparse link is unavailable: {exc}")
            result = _scan_filesystem([{"path": str(project), "kind": "primary"}], root / "state")
            self.assertFalse(any(row["filename"] == "secret.txt" for row in result["records"]))


if __name__ == "__main__":
    unittest.main()
