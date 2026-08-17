from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable


SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from file_intelligence.engine import deep_onboard, maintain, understand_project  # noqa: E402


def _rss() -> int | None:
    try:
        import psutil  # type: ignore

        return int(psutil.Process().memory_info().rss)
    except ImportError:
        return None


def _measure(operation: Callable[[], dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    wall = time.perf_counter()
    cpu = time.process_time()
    before = _rss()
    result = operation()
    after = _rss()
    return result, {
        "elapsed_seconds": round(time.perf_counter() - wall, 6),
        "cpu_seconds": round(time.process_time() - cpu, 6),
        "rss_before": before,
        "rss_after": after,
        "rss_delta": None if before is None or after is None else after - before,
    }


def _fixture(root: Path, files_per_project: int) -> tuple[Path, Path]:
    alpha = root / "Project_Alpha_active"
    beta = root / "Project_Beta_completed"
    (alpha / "src").mkdir(parents=True)
    (beta / "results").mkdir(parents=True)
    (alpha / "README.md").write_text(
        "# Synthetic thermal project\nThe Alpha workstream is active.\n"
        "Canonical model: `src/canonical_model.mph`\n",
        encoding="utf-8",
    )
    (alpha / "src" / "canonical_model.mph").write_bytes(b"synthetic-canonical-model")
    (beta / "README.md").write_text("# Completed synthetic results\nThis workstream is completed.\n", encoding="utf-8")
    for index in range(files_per_project):
        (alpha / "src" / f"source_{index:05d}.txt").write_text(f"source={index:05d}\n", encoding="utf-8")
        (beta / "results" / f"result_{index:05d}.csv").write_text(f"x,y\n{index},{index * 2}\n", encoding="utf-8")
    environment = alpha / ".venv" / "Lib"
    environment.mkdir(parents=True)
    for index in range(min(250, files_per_project)):
        (environment / f"dependency_{index:04d}.py").write_text("# synthetic dependency\n", encoding="utf-8")
    return alpha, beta


def main() -> int:
    parser = argparse.ArgumentParser(description="Synthetic onboarding and incremental-maintenance benchmark")
    parser.add_argument("--workspace", required=True, type=Path, help="External parent for a unique synthetic benchmark run")
    parser.add_argument("--files-per-project", type=int, default=1000)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    run_root = arguments.workspace.resolve() / f"maintenance-{uuid.uuid4().hex[:12]}"
    fixture_root = run_root / "synthetic-files"
    state = run_root / "state"
    alpha, _ = _fixture(fixture_root, max(1, arguments.files_per_project))

    onboarding, onboarding_perf = _measure(
        lambda: deep_onboard([fixture_root], state_dir=state, backend="filesystem", max_fingerprints=100)
    )
    no_op, no_op_perf = _measure(lambda: maintain(state_dir=state, backend="filesystem", max_fingerprints=100))
    changed_path = alpha / "src" / "source_00000.txt"
    changed_path.write_text("source=CHANGED\n", encoding="utf-8")
    os.utime(changed_path, None)
    one_change, one_change_perf = _measure(lambda: maintain(state_dir=state, backend="filesystem", max_fingerprints=100))
    second_no_op, second_no_op_perf = _measure(lambda: maintain(state_dir=state, backend="filesystem", max_fingerprints=100))
    understanding, understanding_perf = _measure(
        lambda: understand_project(
            fixture_root, state_dir=state, max_inspections=80, max_stage1_hashes=100, max_full_hashes=8
        )
    )
    payload = {
        "synthetic_root": str(fixture_root),
        "state_path": str(state),
        "files_per_project": arguments.files_per_project,
        "database_bytes": (state / "catalog.db").stat().st_size,
        "onboarding": {"performance": onboarding_perf, "counts": onboarding["counts"]},
        "first_no_op": {
            "performance": no_op_perf,
            "status": no_op["status"],
            "counts": no_op["counts"],
            "content_inspected": no_op["performance"]["content_inspected"],
            "stage1_hashes": no_op["performance"]["stage1_hashes"],
        },
        "single_change": {
            "performance": one_change_perf,
            "status": one_change["status"],
            "counts": one_change["counts"],
            "meaningful_changes": one_change["meaningful_changes"],
            "affected_projects": one_change["affected_projects"],
            "project_reinspection": one_change["project_reinspection"],
        },
        "second_no_op": {
            "performance": second_no_op_perf,
            "status": second_no_op["status"],
            "counts": second_no_op["counts"],
            "content_inspected": second_no_op["performance"]["content_inspected"],
            "stage1_hashes": second_no_op["performance"]["stage1_hashes"],
        },
        "project_understanding": {
            "performance": understanding_perf,
            "workstreams": len(understanding["workstreams"]),
            "authorities": understanding["authority_total"],
            "dependencies": understanding["dependencies"],
        },
        "physical_project_actions": 0,
    }
    output = arguments.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, output)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
