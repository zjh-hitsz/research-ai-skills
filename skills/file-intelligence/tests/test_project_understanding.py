from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from file_intelligence.engine import (
    assertions,
    deep_onboard,
    get_asset_details,
    maintain,
    understand_project,
)
from file_intelligence.fingerprints import full_sha256, staged_sample_fingerprint
from file_intelligence.inspectors import extract_references
from file_intelligence.cli import build_parser


class ProjectUnderstandingTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, Path]:
        project = root / "科研项目_热电冷板"
        active = project / "Journal_Rebuild_V2_active"
        completed = project / "Journal_Extension_completed"
        conference = project / "SET2026_oral_assets_final"
        for folder in (active, completed, conference):
            folder.mkdir(parents=True)
        (project / "README.md").write_text(
            "# STEG cold-plate research project\n"
            "Canonical model: Journal_Rebuild_V2_active/models/canonical_model.mph\n"
            "## Canonical final deliverables\n"
            "- Final manuscript: `Journal_Extension_completed/paper_final.docx`\n"
            "The rebuild workstream is active. The extension is completed. SET2026 oral assets are frozen.\n",
            encoding="utf-8",
        )
        (active / "models").mkdir()
        (active / "models" / "canonical_model.mph").write_bytes(b"synthetic-model-v2")
        (active / "run.py").write_text(
            "model = 'models/canonical_model.mph'\noutput = '../Journal_Extension_completed/results.csv'\n",
            encoding="utf-8",
        )
        (completed / "paper_final.docx").write_bytes(b"synthetic-docx-placeholder")
        (completed / "results.csv").write_text("x,y\n1,2\n", encoding="utf-8")
        (conference / "SET2026_oral_final.pptx").write_bytes(b"synthetic-pptx-placeholder")
        (active / "模型副本.mph").write_bytes(b"synthetic-model-v2")
        (project / "Tencent" / "QQ").mkdir(parents=True)
        (project / "Tencent" / "QQ" / "runtime.db-wal").write_bytes(b"volatile")
        (project / ".venv" / "Lib").mkdir(parents=True)
        (project / ".venv" / "Lib" / "package.py").write_text("synthetic", encoding="utf-8")
        return project, active / "models" / "canonical_model.mph"

    def test_hierarchy_authority_dependencies_aggregates_and_dashboard(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project, model = self._fixture(base)
            state = base / "state"
            baseline = deep_onboard([project], state_dir=state, backend="filesystem", max_fingerprints=100)
            self.assertEqual(baseline["counts"]["aggregate_nodes"], 1)
            self.assertEqual(baseline["counts"]["aggregate_files"], 1)

            result = understand_project(project, state_dir=state, max_inspections=100, max_stage1_hashes=100, max_full_hashes=10)
            self.assertIn("STEG", result["project"]["purpose"])
            self.assertEqual(result["project"]["lifecycle"], "MIXED")
            self.assertGreaterEqual(len(result["workstreams"]), 3)
            lifecycles = {item["lifecycle"] for item in result["workstreams"]}
            self.assertIn("ACTIVE", lifecycles)
            self.assertIn("FROZEN", lifecycles)
            self.assertGreaterEqual(result["dependencies"]["resolved"], 2)
            self.assertGreaterEqual(result["duplicates"], 1)
            self.assertEqual(result["project"]["aggregate_nodes"], 1)
            self.assertEqual(result["project"]["aggregate_files"], 1)
            self.assertEqual(
                result["project"]["file_count"],
                baseline["counts"]["files"] + baseline["counts"]["aggregate_files"],
            )
            self.assertTrue(Path(result["dashboard"]).is_file())
            self.assertIn("File Intelligence v0.3.0", Path(result["dashboard"]).read_text(encoding="utf-8"))

            asset = get_asset_details(model, state_dir=state, verify_full_hash=True)
            self.assertEqual(asset["role"], "canonical_model")
            self.assertEqual(asset["authority_level"], "CANONICAL")
            self.assertGreaterEqual(asset["confidence"], 0.9)
            self.assertEqual(asset["full_sha256"], full_sha256(model)["value"])
            self.assertTrue(asset["incoming_references"])
            self.assertTrue(any(edge["relation"] == "DECLARES_AUTHORITY" for edge in asset["incoming_references"]))
            self.assertEqual(asset["physical_actions"], 0)

            maintenance = maintain(state_dir=state, backend="filesystem")
            self.assertEqual(maintenance["status"], "NO_OP")
            with closing(sqlite3.connect(state / "catalog.db")) as connection:
                aggregate_project = connection.execute("SELECT project_id FROM aggregate_nodes").fetchone()[0]
            self.assertEqual(aggregate_project, result["project"]["project_id"])

    def test_user_assertion_is_explicit_and_removable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project, model = self._fixture(base)
            state = base / "state"
            deep_onboard([project], state_dir=state, backend="filesystem")
            understand_project(project, state_dir=state)
            from file_intelligence.engine import path_key

            created = assertions(
                state_dir=state,
                action="set",
                subject_type="asset",
                subject_key=path_key(model),
                predicate="authority_level",
                value="PRIMARY",
            )
            understand_project(project, state_dir=state)
            asset = get_asset_details(model, state_dir=state)
            self.assertEqual(asset["authority_level"], "PRIMARY")
            self.assertEqual(asset["provenance_type"], "explicit_user")
            listed = assertions(state_dir=state, action="list")
            self.assertEqual(len(listed["assertions"]), 1)
            removed = assertions(state_dir=state, action="remove", assertion_id=created["assertion_id"])
            self.assertTrue(removed["removed"])

    def test_project_merge_authority_section_lifecycle_raw_data_and_supersession(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = base / "thermal_project"
            for folder in ("source", "final", "archive", "candidate", "reference", "reproducibility", "experiments/next_design", ".venv/Lib"):
                (project / folder).mkdir(parents=True)
            readme = project / "README.md"
            readme.write_text(
                "# Thermal research\n\n"
                "The baseline report is completed. New work is active under `experiments/next_design`.\n\n"
                "## Authority\n\n"
                "- Canonical input: `source/raw_measurements.csv`\n"
                "- Final report: `final/FINAL_REPORT.md`\n"
                "- Reproducibility manifest: `reproducibility/manifest.json`\n\n"
                "The `archive/report_draft.md` file is historical and superseded by `final/FINAL_REPORT.md`.\n",
                encoding="utf-8",
            )
            reference = project / "reference" / "context.md"
            reference.write_text("# Context only\n", encoding="utf-8")
            with readme.open("a", encoding="utf-8") as handle:
                handle.write(
                    "Use `reference/context.md` as background evidence, not as scientific authority.\n\n"
                    "## Authority candidates\n\n"
                    "- `candidate/unconfirmed.py` — candidate only; human confirmation required.\n"
                )
            unconfirmed = project / "candidate" / "unconfirmed.py"
            unconfirmed.write_text("# candidate\n", encoding="utf-8")
            raw = project / "source" / "raw_measurements.csv"
            raw.write_text("x,y\n1,2\n", encoding="utf-8")
            final = project / "final" / "FINAL_REPORT.md"
            final.write_text("# Final report\n", encoding="utf-8")
            manifest = project / "reproducibility" / "manifest.json"
            manifest.write_text('{"input":"../source/raw_measurements.csv"}\n', encoding="utf-8")
            draft = project / "archive" / "report_draft.md"
            draft.write_text("Historical draft. Superseded by `../final/FINAL_REPORT.md`.\n", encoding="utf-8")
            (project / "experiments" / "next_design" / "notes.md").write_text("active experiment\n", encoding="utf-8")
            (project / ".venv" / "Lib" / "dependency.py").write_text("# dependency\n", encoding="utf-8")
            state = base / "state"
            deep_onboard([project], state_dir=state, backend="filesystem")
            result = understand_project(project, state_dir=state, max_inspections=50, max_stage1_hashes=50, max_full_hashes=8)

            self.assertEqual(result["project"]["lifecycle"], "MIXED")
            self.assertTrue(any(item["name"].casefold() == "experiments" and item["lifecycle"] == "ACTIVE" for item in result["workstreams"]))
            self.assertEqual(get_asset_details(raw, state_dir=state)["role"], "raw_data")
            self.assertEqual(get_asset_details(final, state_dir=state)["authority_level"], "CANONICAL")
            self.assertEqual(get_asset_details(manifest, state_dir=state)["authority_level"], "CANONICAL")
            self.assertNotEqual(get_asset_details(reference, state_dir=state)["authority_level"], "CANONICAL")
            self.assertNotEqual(get_asset_details(unconfirmed, state_dir=state)["authority_level"], "CANONICAL")
            draft_details = get_asset_details(draft, state_dir=state)
            self.assertEqual(draft_details["superseded_by"], str(final.resolve()))
            self.assertEqual(draft_details["archive_recommendation"], "REVIEW_SUPERSEDED")
            self.assertTrue(any(edge["relation"] == "SUPERSEDED_BY" for edge in draft_details["outgoing_references"]))

            maintain(state_dir=state, backend="filesystem")
            with closing(sqlite3.connect(state / "catalog.db")) as connection:
                projects = connection.execute("SELECT status,name FROM projects").fetchall()
                names = connection.execute("SELECT DISTINCT project_name FROM files WHERE status='present'").fetchall()
            self.assertEqual(projects, [("understood", "thermal_project")])
            self.assertEqual(names, [("thermal_project",)])

            before = build_parser().parse_args(["--state-dir", str(state), "status"])
            after = build_parser().parse_args(["status", "--state-dir", str(state)])
            self.assertEqual(before.state_dir, after.state_dir)

    def test_absolute_and_relative_reference_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "scripts" / "run.py"
            source.parent.mkdir()
            target = root / "model.mph"
            target.write_bytes(b"model")
            text = f"a = '../model.mph'\nb = r'{target}'\n"
            references = extract_references(text, source, root)
            self.assertGreaterEqual(len(references), 2)
            self.assertTrue(all(item["resolution_status"] == "resolved_existing" for item in references))

    def test_staged_large_fingerprint_and_same_size_difference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "large-a.mph"
            second = root / "large-b.mph"
            first.write_bytes(b"A" * 200_000 + b"MIDDLE-A" + b"Z" * 200_000)
            second.write_bytes(b"A" * 200_000 + b"MIDDLE-B" + b"Z" * 200_000)
            self.assertEqual(first.stat().st_size, second.stat().st_size)
            sample_a = staged_sample_fingerprint(first)
            sample_b = staged_sample_fingerprint(second)
            self.assertNotEqual(sample_a["value"], sample_b["value"])
            self.assertNotEqual(full_sha256(first)["value"], full_sha256(second)["value"])

    def test_rename_move_copy_move_and_return(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            (project / "a").mkdir(parents=True)
            (project / "b").mkdir()
            state = root / "state"
            original = project / "a" / "model.mph"
            original.write_bytes(b"identity-content")
            deep_onboard([project], state_dir=state, backend="filesystem", max_fingerprints=100)

            renamed = project / "a" / "renamed.mph"
            original.rename(renamed)
            first = maintain(state_dir=state, backend="filesystem", max_fingerprints=100)
            self.assertEqual(first["identity_events"]["RENAMED"], 1)

            moved = project / "b" / "renamed.mph"
            renamed.rename(moved)
            second = maintain(state_dir=state, backend="filesystem", max_fingerprints=100)
            self.assertEqual(second["identity_events"]["MOVED"], 1)

            copied = project / "b" / "copy.mph"
            copied.write_bytes(moved.read_bytes())
            third = maintain(state_dir=state, backend="filesystem", max_fingerprints=100)
            self.assertEqual(third["identity_events"]["COPIED"], 1)

            copied.unlink()
            moved.rename(original)
            fourth = maintain(state_dir=state, backend="filesystem", max_fingerprints=100)
            self.assertGreaterEqual(fourth["identity_events"]["MOVED"], 1)

    def test_changed_same_size_and_volatile_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            volatile = project / "Tencent" / "QQ" / "runtime.db-wal"
            stable = project / "data.bin"
            volatile.parent.mkdir(parents=True)
            volatile.write_bytes(b"1111")
            stable.write_bytes(b"aaaa")
            state = root / "state"
            deep_onboard([project], state_dir=state, backend="filesystem")
            volatile.write_bytes(b"2222")
            os.utime(volatile, ns=(volatile.stat().st_atime_ns, volatile.stat().st_mtime_ns + 1_000_000))
            result = maintain(state_dir=state, backend="filesystem")
            self.assertEqual(result["volatile_changes"], 1)
            self.assertEqual(result["meaningful_changes"], 0)
            self.assertEqual(result["semantic_status"], "NO_MEANINGFUL_CHANGE")
            stable.write_bytes(b"bbbb")
            os.utime(stable, ns=(stable.stat().st_atime_ns, stable.stat().st_mtime_ns + 1_000_000))
            result = maintain(state_dir=state, backend="filesystem")
            self.assertEqual(result["meaningful_changes"], 1)

    def test_chinese_and_long_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "中文项目"
            long_folder = project / ("很长的科研路径" * 12)
            long_folder.mkdir(parents=True)
            target = long_folder / "模型说明.md"
            target.write_text("# 中文热电项目\n", encoding="utf-8")
            state = root / "state"
            result = deep_onboard([project], state_dir=state, backend="filesystem")
            self.assertEqual(result["counts"]["files"], 1)
            with sqlite3.connect(state / "catalog.db") as connection:
                stored = connection.execute("SELECT path FROM files").fetchone()[0]
            from file_intelligence.engine import path_key

            self.assertEqual(path_key(stored), path_key(target))
            connection.close()


if __name__ == "__main__":
    unittest.main()
