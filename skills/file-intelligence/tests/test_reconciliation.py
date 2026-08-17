from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from file_intelligence.database import connect_current, rollback_migration
from file_intelligence.intelligence_models import Capability, SensorObservation
from file_intelligence.reconciliation import (
    ForeignStateError,
    LegacyCardImporter,
    ReconciliationError,
    SemanticConflictError,
    extension_status,
    import_cleanup_evidence_manifest,
    inspect_cleanup_evidence_manifest,
    persist_sensor_observation,
    reconcile_legacy_state,
    upsert_file_card_semantics,
)


LEGACY_SCHEMA = """
CREATE TABLE schema_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE file_cards(
 file_id TEXT PRIMARY KEY,current_path TEXT,filename TEXT,file_type TEXT,tier INTEGER,project_id TEXT,
 workstream_id TEXT,asset_role TEXT,importance TEXT,status TEXT,summary TEXT,authority TEXT,confidence REAL,
 evidence_json TEXT,first_seen TEXT,last_seen TEXT,last_changed TEXT,size INTEGER,allocated_size INTEGER,
 rebuildable TEXT,rebuild_cost TEXT,canonical_status TEXT,supersedes_json TEXT,superseded_by_json TEXT,
 version_family TEXT,references_json TEXT,referenced_by_json TEXT,duplicate_group TEXT,archive_membership TEXT,
 producer_evidence_json TEXT,sensor_observations_json TEXT,unresolved_questions_json TEXT,source_signature_json TEXT,
 revision INTEGER,updated_at TEXT,content_identity TEXT,missing_since TEXT,status_before_missing TEXT,
 content_identity_kind TEXT
);
CREATE TABLE file_card_revisions(file_id TEXT,revision INTEGER,recorded_at TEXT,reasons_json TEXT,snapshot_json TEXT);
CREATE TABLE file_card_events(event_id INTEGER PRIMARY KEY,file_id TEXT,recorded_at TEXT,event_type TEXT,reasons_json TEXT,from_revision INTEGER,to_revision INTEGER);
CREATE TABLE file_card_evidence(evidence_id TEXT PRIMARY KEY,file_id TEXT,claim TEXT,authority TEXT,description TEXT,source_ref TEXT,confidence REAL,observed_at TEXT,semantic_eligible INTEGER,payload_json TEXT);
CREATE TABLE file_path_history(history_id INTEGER PRIMARY KEY,file_id TEXT,path TEXT,observed_from TEXT,observed_to TEXT,event_type TEXT,source_event_id TEXT);
CREATE TABLE project_cards(project_id TEXT PRIMARY KEY,name TEXT,status TEXT,authority TEXT,confidence REAL,revision INTEGER,semantic_fingerprint TEXT,snapshot_json TEXT,updated_at TEXT);
CREATE TABLE project_card_revisions(project_id TEXT,revision INTEGER,recorded_at TEXT,reasons_json TEXT,snapshot_json TEXT);
CREATE TABLE project_card_events(event_id INTEGER PRIMARY KEY,project_id TEXT,recorded_at TEXT,event_type TEXT,reasons_json TEXT,from_revision INTEGER,to_revision INTEGER);
CREATE TABLE project_relations(relation_id TEXT PRIMARY KEY,source_project_id TEXT,target_id TEXT,target_name TEXT,relation_type TEXT,entity_type TEXT,authority TEXT,confidence REAL,evidence_json TEXT,active INTEGER,updated_at TEXT);
CREATE TABLE user_assertions(assertion_id TEXT PRIMARY KEY,subject_type TEXT,subject_id TEXT,field_name TEXT,value_json TEXT,authority TEXT,source_text TEXT,source_ref TEXT,asserted_at TEXT,active INTEGER,revoked_at TEXT);
CREATE TABLE user_assertion_events(event_id INTEGER PRIMARY KEY,assertion_id TEXT,event_type TEXT,recorded_at TEXT,payload_json TEXT);
CREATE TABLE semantic_changes(change_id TEXT PRIMARY KEY,project_id TEXT,time_range_json TEXT,change_type TEXT,summary TEXT,importance TEXT,authority TEXT,confidence REAL,evidence_json TEXT,related_file_ids_json TEXT,related_event_ids_json TEXT,storage_delta INTEGER,status TEXT,recorded_at TEXT);
"""


def create_legacy(path: Path, *, project_name: str = "Synthetic Project") -> None:
    connection = sqlite3.connect(path)
    connection.executescript(LEGACY_SCHEMA)
    connection.execute("INSERT INTO schema_meta VALUES('schema_version','2.0.1')")
    card = {
        "file_id": "file_local_1",
        "current_path": os.path.join("C:" + os.sep, "Synthetic", "case.cas.h5"),
        "filename": "case.cas.h5",
        "file_type": "FLUENT_CASE",
        "tier": 1,
        "project_id": "project_1",
        "workstream_id": "workstream_1",
        "asset_role": "VALIDATED_CHECKPOINT",
        "importance": "HIGH",
        "status": "ACTIVE",
        "summary": "Synthetic validated checkpoint",
        "authority": "DOCUMENT_EVIDENCE",
        "confidence": 0.95,
        "evidence_json": "[]",
        "first_seen": "2026-01-01T00:00:00+00:00",
        "last_seen": "2026-01-02T00:00:00+00:00",
        "last_changed": "2026-01-02T00:00:00+00:00",
        "size": 100,
        "allocated_size": 4096,
        "rebuildable": "NO",
        "rebuild_cost": "HIGH",
        "canonical_status": "VALIDATED_CHECKPOINT",
        "supersedes_json": "[]",
        "superseded_by_json": "[]",
        "version_family": "case",
        "references_json": "[]",
        "referenced_by_json": "[]",
        "duplicate_group": "dup_1",
        "archive_membership": None,
        "producer_evidence_json": "[]",
        "sensor_observations_json": "[]",
        "unresolved_questions_json": "[]",
        "source_signature_json": "{}",
        "revision": 1,
        "updated_at": "2026-01-02T00:00:00+00:00",
        "content_identity": "a" * 64,
        "missing_since": None,
        "status_before_missing": None,
        "content_identity_kind": "SHA256_FULL",
    }
    columns = list(card)
    connection.execute(
        f"INSERT INTO file_cards({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
        tuple(card[column] for column in columns),
    )
    snapshot = json.dumps(card, sort_keys=True)
    connection.execute(
        "INSERT INTO file_card_revisions VALUES(?,?,?,?,?)",
        ("file_local_1", 1, "2026-01-02T00:00:00+00:00", '["INITIAL"]', snapshot),
    )
    connection.executemany(
        "INSERT INTO file_card_events VALUES(?,?,?,?,?,?,?)",
        [
            (1, "file_local_1", "2026-01-02T00:00:00+00:00", "CARD_CREATED", '["INITIAL"]', 0, 1),
            (2, "file_local_1", "2026-01-03T00:00:00+00:00", "CARD_REUSED", "[]", 1, 1),
        ],
    )
    connection.execute(
        "INSERT INTO file_card_evidence VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("ev_1", "file_local_1", "asset_role", "DOCUMENT_EVIDENCE", "Report states role", "synthetic", 0.95, "2026-01-02T00:00:00+00:00", 1, "{}"),
    )
    project_snapshot = {
        "project_id": "project_1",
        "name": project_name,
        "purpose": "Synthetic validation",
        "status": "VALIDATION",
        "roots": [os.path.join("C:" + os.sep, "Synthetic")],
    }
    project_json = json.dumps(project_snapshot, sort_keys=True)
    project_fp = __import__("hashlib").sha256(
        json.dumps(project_snapshot, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    connection.execute(
        "INSERT INTO project_cards VALUES(?,?,?,?,?,?,?,?,?)",
        ("project_1", project_name, "VALIDATION", "DOCUMENT_EVIDENCE", 0.95, 1, project_fp, project_json, "2026-01-02T00:00:00+00:00"),
    )
    connection.execute(
        "INSERT INTO project_card_revisions VALUES(?,?,?,?,?)",
        ("project_1", 1, "2026-01-02T00:00:00+00:00", '["INITIAL"]', project_json),
    )
    connection.executemany(
        "INSERT INTO project_card_events VALUES(?,?,?,?,?,?,?)",
        [
            (1, "project_1", "2026-01-02T00:00:00+00:00", "PROJECT_CARD_CREATED", '["INITIAL"]', 0, 1),
            (2, "project_1", "2026-01-03T00:00:00+00:00", "PROJECT_CARD_REUSED", "[]", 1, 1),
        ],
    )
    relation = {"target_id": "task_1", "relation_type": "CHILD_TECHNICAL_VALIDATION", "entity_type": "Task/Experiment"}
    connection.execute(
        "INSERT INTO project_relations VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("relation_1", "project_1", "task_1", "Synthetic Benchmark", relation["relation_type"], relation["entity_type"], "USER_ASSERTED", 1.0, "[]", 1, "2026-01-02T00:00:00+00:00"),
    )
    connection.execute(
        "INSERT INTO user_assertions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("assertion_1", "project_relation", "project_1", "relation_1", json.dumps(relation), "USER_ASSERTED", "Keep benchmark as a child task", "local-user:synthetic", "2026-01-02T00:00:00+00:00", 1, None),
    )
    connection.execute(
        "INSERT INTO user_assertion_events VALUES(?,?,?,?,?)",
        (1, "assertion_1", "ASSERTED", "2026-01-02T00:00:00+00:00", json.dumps(relation)),
    )
    connection.execute(
        "INSERT INTO semantic_changes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("change_1", "project_1", "{}", "PROJECT_STATUS_CHANGED", "Project entered validation", "HIGH", "DOCUMENT_EVIDENCE", 0.95, "[]", '["file_local_1"]', "[]", 0, "ACTIVE", "2026-01-02T00:00:00+00:00"),
    )
    connection.commit()
    connection.close()


def create_cleanup_manifest(path: Path, *, full_sha256: str = "b" * 64) -> None:
    payload = {
        "schema": "file-intelligence-cleanup-evidence-v1",
        "records": [
            {
                "evidence_id": "cleanup_ev_1",
                "subject": {"file_id": "file_1"},
                "claim_type": "EXACT_DUPLICATE",
                "authority": "HASH_EVIDENCE",
                "confidence": 1.0,
                "full_sha256": full_sha256,
                "duplicate_group": "duplicate_group_1",
                "evidence": [{"type": "full_sha256_match"}],
                "proposed_file_card_patch": {"duplicate_group": "duplicate_group_1"},
                "recommendation": {"action": "REVIEW_REDUNDANT_COPY", "read_only": True},
                "source_ref": "synthetic:cleanup-audit",
            }
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


class ExtensionTests(unittest.TestCase):
    def test_existing_v3_catalog_installs_extension_without_version_change(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "catalog.db"
            connection = connect_current(path, create=True)
            connection.commit()
            for table in (
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
            ):
                connection.execute(f"DROP TABLE {table}")
            connection.execute("DELETE FROM meta WHERE key='reconciliation_extension_version'")
            connection.commit()
            connection.close()
            reopened = connect_current(path)
            self.assertEqual(reopened.execute("PRAGMA user_version").fetchone()[0], 3)
            self.assertTrue(extension_status(reopened)["complete"])
            reopened.close()

    def test_sensor_observation_is_non_semantic(self):
        with tempfile.TemporaryDirectory() as raw:
            connection = connect_current(Path(raw) / "catalog.db", create=True)
            observation = SensorObservation.data("synthetic", Capability.GPU_STATUS, "machine", [{"gpu": "x"}])
            persist_sensor_observation(connection, machine_binding="machine-a", observation=observation)
            row = connection.execute(
                "SELECT semantic_eligible FROM sensor_observations WHERE observation_id=?", (observation.observation_id,)
            ).fetchone()
            self.assertEqual(row[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM file_card_revisions").fetchone()[0], 0)
            connection.close()

    def test_sensor_link_refresh_does_not_create_semantic_revision(self):
        with tempfile.TemporaryDirectory() as raw:
            connection = connect_current(Path(raw) / "catalog.db", create=True)
            first = upsert_file_card_semantics(
                connection,
                file_id="file_1",
                updates={"authority": "SYSTEM_OBSERVED", "confidence": 0.8, "summary": "x", "sensor_links_json": '[{"t":1}]'},
                source_system="test",
                reason="INITIAL",
            )
            second = upsert_file_card_semantics(
                connection,
                file_id="file_1",
                updates={"sensor_links_json": '[{"t":2}]'},
                source_system="test",
                reason="SENSOR_REFRESH",
            )
            self.assertTrue(first["changed"])
            self.assertFalse(second["changed"])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM file_card_revisions").fetchone()[0], 1)
            connection.close()

    def test_duplicate_group_requires_full_sha(self):
        with tempfile.TemporaryDirectory() as raw:
            connection = connect_current(Path(raw) / "catalog.db", create=True)
            with self.assertRaises(ReconciliationError):
                upsert_file_card_semantics(
                    connection,
                    file_id="file_1",
                    updates={
                        "authority": "HASH_EVIDENCE",
                        "confidence": 1.0,
                        "duplicate_group": "dup",
                        "content_identity": "quick",
                        "content_identity_kind": "QUICK_HASH",
                    },
                    source_system="test",
                    reason="BAD_DUPLICATE",
                )
            connection.close()

    def test_cleanup_evidence_is_imported_without_execution_or_card_mutation(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            manifest = root / "cleanup.json"
            create_cleanup_manifest(manifest)
            connection = connect_current(root / "catalog.db", create=True)
            connection.execute(
                "INSERT INTO file_cards VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("file_1", None, "synthetic", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00", "present", 1.0, "[]", None, None, "FILE_LOCAL"),
            )
            connection.commit()
            preview = import_cleanup_evidence_manifest(
                connection,
                manifest_path=manifest,
                target_machine_binding="machine-a",
                reviewed_unbound_source=True,
                apply=False,
            )
            self.assertEqual(preview["status"], "PREVIEW_ONLY")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM legacy_cleanup_evidence").fetchone()[0], 0)
            result = import_cleanup_evidence_manifest(
                connection,
                manifest_path=manifest,
                target_machine_binding="machine-a",
                reviewed_unbound_source=True,
                apply=True,
            )
            self.assertEqual(result["status"], "COMPLETED")
            recommendation = connection.execute(
                "SELECT status,execution_authorized FROM cleanup_recommendations"
            ).fetchone()
            self.assertEqual(tuple(recommendation), ("PROPOSED_READ_ONLY", 0))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM file_card_evidence").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM file_card_semantics").fetchone()[0], 0)
            again = import_cleanup_evidence_manifest(
                connection,
                manifest_path=manifest,
                target_machine_binding="machine-a",
                reviewed_unbound_source=True,
                apply=True,
            )
            self.assertEqual(again["status"], "NO_OP_ALREADY_IMPORTED")
            connection.close()

    def test_cleanup_exact_duplicate_rejects_non_full_hash(self):
        with tempfile.TemporaryDirectory() as raw:
            manifest = Path(raw) / "cleanup.json"
            create_cleanup_manifest(manifest, full_sha256="quick")
            inspection = inspect_cleanup_evidence_manifest(manifest)
            self.assertFalse(inspection["valid"])
            self.assertIn("full SHA-256", inspection["errors"][0]["errors"][0])


class LegacyImportTests(unittest.TestCase):
    def test_import_preserves_cards_projects_assertions_and_timeline(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "legacy.db"
            create_legacy(source)
            connection = connect_current(root / "catalog.db", create=True)
            connection.commit()
            report = LegacyCardImporter(
                connection,
                target_machine_binding="machine-a",
                reviewed_unbound_source=True,
            ).import_path(source)
            self.assertEqual(report.status, "COMPLETED")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM file_card_semantics").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM project_card_views").fetchone()[0], 1)
            relation = connection.execute("SELECT relation_type,entity_type,authority FROM project_relations").fetchone()
            self.assertEqual(tuple(relation), ("CHILD_TECHNICAL_VALIDATION", "Task/Experiment", "USER_ASSERTED"))
            assertion = connection.execute("SELECT active FROM user_assertions WHERE assertion_id='assertion_1'").fetchone()
            self.assertEqual(assertion[0], 1)
            event_types = {row[0] for row in connection.execute("SELECT event_type FROM events")}
            self.assertIn("LEGACY_CARD_CREATED", event_types)
            self.assertNotIn("CARD_REUSED", event_types)
            self.assertNotIn("PROJECT_CARD_REUSED", event_types)
            connection.close()

    def test_reimport_is_noop(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "legacy.db"
            create_legacy(source)
            connection = connect_current(root / "catalog.db", create=True)
            connection.commit()
            importer = LegacyCardImporter(connection, target_machine_binding="machine-a", reviewed_unbound_source=True)
            importer.import_path(source)
            before = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("file_card_revisions", "events", "project_card_revisions", "semantic_changes")
            }
            again = LegacyCardImporter(connection, target_machine_binding="machine-a", reviewed_unbound_source=True).import_path(source)
            after = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in before
            }
            self.assertEqual(again.status, "NO_OP_ALREADY_IMPORTED")
            self.assertEqual(before, after)
            connection.close()

    def test_unbound_source_requires_review(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "legacy.db"
            create_legacy(source)
            connection = connect_current(root / "catalog.db", create=True)
            connection.commit()
            with self.assertRaises(ForeignStateError):
                LegacyCardImporter(connection, target_machine_binding="machine-a").import_path(source)
            connection.close()

    def test_foreign_bound_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "legacy.db"
            create_legacy(source)
            connection = connect_current(root / "catalog.db", create=True)
            connection.commit()
            with self.assertRaises(ForeignStateError):
                LegacyCardImporter(
                    connection,
                    target_machine_binding="machine-a",
                    source_machine_binding="machine-b",
                    reviewed_unbound_source=True,
                ).import_path(source)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM legacy_imports").fetchone()[0], 0)
            connection.close()

    def test_semantic_collision_rolls_back_all_import_rows(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "legacy.db"
            create_legacy(source, project_name="Source Name")
            connection = connect_current(root / "catalog.db", create=True)
            connection.execute(
                "INSERT INTO projects VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("project_1", "Different Existing Name", 0, 0, "ACTIVE", None, None, None, 1.0, "[]", None),
            )
            connection.commit()
            with self.assertRaises(SemanticConflictError):
                LegacyCardImporter(connection, target_machine_binding="machine-a", reviewed_unbound_source=True).import_path(source)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM file_card_semantics").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM legacy_imports").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0], 1)
            connection.close()

    def test_two_machine_states_remain_isolated(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "legacy.db"
            create_legacy(source)
            a = connect_current(root / "a.db", create=True)
            b = connect_current(root / "b.db", create=True)
            a.commit()
            b.commit()
            LegacyCardImporter(a, target_machine_binding="machine-a", reviewed_unbound_source=True).import_path(source)
            self.assertEqual(a.execute("SELECT COUNT(*) FROM file_card_semantics").fetchone()[0], 1)
            self.assertEqual(b.execute("SELECT COUNT(*) FROM file_card_semantics").fetchone()[0], 0)
            self.assertEqual(a.execute("SELECT value FROM meta WHERE key='machine_binding'").fetchone()[0], "machine-a")
            self.assertIsNone(b.execute("SELECT value FROM meta WHERE key='machine_binding'").fetchone())
            a.close()
            b.close()

    def test_apply_wrapper_creates_backup_and_manifest(self):
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw) / "state"
            state.mkdir()
            source = Path(raw) / "legacy.db"
            create_legacy(source)
            connection = connect_current(state / "catalog.db", create=True)
            connection.commit()
            connection.close()
            result = reconcile_legacy_state(
                state_dir=state,
                source_path=source,
                target_machine_binding="machine-a",
                reviewed_unbound_source=True,
                apply=True,
            )
            self.assertEqual(result["status"], "COMPLETED")
            self.assertTrue(Path(result["backup"]["backup_catalog"]).is_file())
            self.assertTrue(Path(result["backup"]["manifest"]).is_file())
            self.assertEqual(result["physical_file_actions"], 0)
            preview = rollback_migration(state, Path(result["backup"]["manifest"]).parent, apply=False)
            self.assertFalse(preview["applied"])
            rollback = rollback_migration(state, Path(result["backup"]["manifest"]).parent, apply=True)
            self.assertTrue(rollback["applied"])
            restored = connect_current(state / "catalog.db")
            self.assertEqual(restored.execute("SELECT COUNT(*) FROM legacy_imports").fetchone()[0], 0)
            self.assertEqual(restored.execute("PRAGMA user_version").fetchone()[0], 3)
            restored.close()


if __name__ == "__main__":
    unittest.main()
