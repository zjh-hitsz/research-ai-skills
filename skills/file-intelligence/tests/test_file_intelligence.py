from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from file_intelligence.engine import FileIntelligenceError, deep_onboard, maintain, resolve_state_dir, status
from file_intelligence.privacy import audit_package


class FileIntelligenceTests(unittest.TestCase):
    def test_fresh_state_requires_onboarding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = status(state_dir=Path(temporary) / "state")
            self.assertEqual(result["status"], "DEEP_ONBOARDING_REQUIRED")
            self.assertFalse(result["real_execution_enabled"])

    def test_onboarding_maintenance_and_noop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            primary = root / "files"
            communication = root / "inbox"
            state = root / "local-state"
            (primary / "alpha-work" / "src").mkdir(parents=True)
            (primary / "beta-study" / "notes").mkdir(parents=True)
            communication.mkdir()
            (primary / "alpha-work" / "src" / "model.py").write_text("print('synthetic')\n", encoding="utf-8")
            (primary / "beta-study" / "notes" / "overview.md").write_text("synthetic\n", encoding="utf-8")
            (communication / "message.txt").write_text("synthetic evidence\n", encoding="utf-8")

            onboarding = deep_onboard([primary], [communication], state_dir=state, backend="filesystem")
            self.assertEqual(onboarding["status"], "BASELINE_CREATED")
            self.assertEqual(onboarding["counts"]["projects"], 2)
            self.assertEqual(onboarding["counts"]["communication_records"], 1)
            self.assertTrue((state / "catalog.db").is_file())
            self.assertTrue((state / "baseline.json").is_file())

            (primary / "alpha-work" / "src" / "new_input.txt").write_text("new\n", encoding="utf-8")
            first = maintain(state_dir=state, backend="filesystem")
            self.assertEqual(first["status"], "CHANGES_RECORDED")
            self.assertEqual(first["counts"]["new"], 1)
            second = maintain(state_dir=state, backend="filesystem")
            self.assertEqual(second["status"], "NO_OP")
            self.assertEqual(second["counts"]["new"], 0)
            self.assertEqual(second["counts"]["changed"], 0)
            self.assertEqual(second["counts"]["missing"], 0)

            (primary / "beta-study" / "notes" / "overview.md").unlink()
            third = maintain(state_dir=state, backend="filesystem")
            self.assertEqual(third["status"], "CHANGES_RECORDED")
            self.assertEqual(third["counts"]["missing"], 1)

    def test_state_cannot_live_inside_skill(self) -> None:
        forbidden = Path(__file__).resolve().parents[1] / "state-forbidden"
        with self.assertRaises(FileIntelligenceError):
            resolve_state_dir(forbidden)

    def test_public_package_is_privacy_clean(self) -> None:
        root = Path(__file__).resolve().parents[1]
        result = audit_package(root)
        self.assertTrue(result["pass"], result["findings"])


if __name__ == "__main__":
    unittest.main()
