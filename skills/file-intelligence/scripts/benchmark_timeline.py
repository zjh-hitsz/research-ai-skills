from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from file_intelligence.database import connect_current  # noqa: E402
from file_intelligence.engine import (  # noqa: E402
    deep_onboard,
    maintain,
    snapshot_now,
    timeline_context,
    understand_project,
)
from file_intelligence.timeline import append_event, create_snapshot, materialize_semantic_changes  # noqa: E402


def _rss() -> int | None:
    try:
        import psutil  # type: ignore

        return int(psutil.Process().memory_info().rss)
    except ImportError:
        return None


def _state_bytes(state: Path) -> int:
    return sum(path.stat().st_size for path in state.glob("catalog.db*") if path.is_file())


def _measure(state: Path, operation: Callable[[], dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    wall = time.perf_counter()
    cpu = time.process_time()
    rss_before = _rss()
    db_before = _state_bytes(state)
    result = operation()
    rss_after = _rss()
    return result, {
        "elapsed_seconds": round(time.perf_counter() - wall, 6),
        "cpu_seconds": round(time.process_time() - cpu, 6),
        "rss_before": rss_before,
        "rss_after": rss_after,
        "rss_delta": None if rss_before is None or rss_after is None else rss_after - rss_before,
        "database_growth_bytes": _state_bytes(state) - db_before,
        "files_inspected": int(result.get("performance", {}).get("content_inspected", 0)),
        "hashes": int(result.get("performance", {}).get("stage1_hashes", 0)),
        "documents_parsed": int(result.get("performance", {}).get("documents_parsed", 0)),
    }


def _fixture(root: Path, count: int) -> tuple[Path, Path]:
    alpha = root / "Project_Alpha"
    beta = root / "Project_Beta"
    (alpha / "src").mkdir(parents=True)
    (beta / "results").mkdir(parents=True)
    (alpha / "README.md").write_text(
        "# Synthetic Alpha\nPrimary solver: Fluent.\n\n## Authority\n- `src/canonical.cas.h5`\n", encoding="utf-8",
    )
    (alpha / "src" / "canonical.cas.h5").write_bytes(b"synthetic-canonical")
    (beta / "README.md").write_text("# Synthetic Beta\n", encoding="utf-8")
    for index in range(count):
        (alpha / "src" / f"source_{index:05d}.txt").write_text(f"alpha={index}\n", encoding="utf-8")
        (beta / "results" / f"result_{index:05d}.csv").write_text(f"x,y\n{index},{index * 2}\n", encoding="utf-8")
    return alpha, beta


def _growth_simulation(state: Path, project_id: str, events_per_day: int) -> dict[str, Any]:
    horizons = {1, 30, 180, 365}
    results: dict[str, Any] = {}
    start_size = _state_bytes(state)
    start_time = datetime.now(timezone.utc) - timedelta(days=365)
    with connect_current(state / "catalog.db") as connection:
        for day in range(1, 366):
            stamp = (start_time + timedelta(days=day)).isoformat(timespec="seconds")
            run_id = f"growth-simulation-day-{day:03d}"
            for index in range(events_per_day):
                importance = "MEDIUM" if index < max(1, events_per_day // 20) else "LOW"
                append_event(connection, {
                    "event_id": "sim_" + uuid.uuid4().hex,
                    "event_type": "FILE_CHANGED" if index % 3 else "FILE_CREATED",
                    "occurred_at": stamp, "subject_type": "file", "project_id": project_id,
                    "size_delta": 4096 if index % 3 == 0 else 0, "confidence": 0.9,
                    "evidence": [{"type": "synthetic_benchmark", "detail": "Generic event-store growth simulation."}],
                    "run_id": run_id, "semantic_importance": importance,
                    "importance_score": 45 if importance == "MEDIUM" else 20,
                    "importance_reasons": ["Synthetic benchmark workload."], "permanent": False,
                })
            materialize_semantic_changes(connection, run_id, stamp)
            create_snapshot(
                connection, state, snapshot_kind="weekly" if day % 7 == 0 else "daily", run_id=run_id, stamp=stamp,
            )
            connection.commit()
            if day in horizons:
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                results[str(day)] = {
                    "days": day, "events_per_day": events_per_day,
                    "database_bytes": _state_bytes(state), "growth_bytes": _state_bytes(state) - start_size,
                    "event_rows": int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]),
                    "snapshot_rows": int(connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]),
                    "semantic_change_rows": int(connection.execute("SELECT COUNT(*) FROM semantic_changes").fetchone()[0]),
                }
    return {"workload_assumption": {"events_per_day": events_per_day, "understood_projects": 1}, "horizons": results}


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark Timeline maintenance, query latency, and long-term SQLite growth")
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--files-per-project", type=int, default=500)
    parser.add_argument("--events-per-day", type=int, default=100)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    run_root = arguments.workspace.resolve() / f"timeline-{uuid.uuid4().hex[:12]}"
    fixture = run_root / "synthetic-files"
    state = run_root / "state"
    alpha, _ = _fixture(fixture, max(100, arguments.files_per_project))
    onboarding, onboarding_perf = _measure(
        state, lambda: deep_onboard([fixture], state_dir=state, backend="filesystem", max_fingerprints=200)
    )
    understanding, understanding_perf = _measure(
        state, lambda: understand_project(alpha, state_dir=state, max_inspections=60, max_stage1_hashes=120, max_full_hashes=4)
    )
    no_op, no_op_perf = _measure(state, lambda: maintain(state_dir=state, backend="filesystem", max_fingerprints=200))
    one = alpha / "src" / "source_00000.txt"
    one.write_text("alpha=changed\n", encoding="utf-8")
    small, small_perf = _measure(state, lambda: maintain(state_dir=state, backend="filesystem", max_fingerprints=200))
    for index in range(100):
        path = alpha / "src" / f"source_{index:05d}.txt"
        path.write_text(f"alpha=bulk-{index}\n", encoding="utf-8")
    hundred, hundred_perf = _measure(state, lambda: maintain(state_dir=state, backend="filesystem", max_fingerprints=200))
    for index in range(10):
        path = alpha / "src" / f"project_change_{index:03d}.dat"
        path.write_bytes(f"project={index}".encode("ascii"))
    project_change, project_change_perf = _measure(state, lambda: maintain(state_dir=state, backend="filesystem", max_fingerprints=200))
    daily, daily_perf = _measure(state, lambda: snapshot_now(state_dir=state, snapshot_kind="daily"))
    (alpha / "README.md").write_text(
        "# Synthetic Alpha\nPrimary solver: Fluent.\n\n## Authority\n- `src/canonical.cas.h5`\n\nWeekly review.\n", encoding="utf-8",
    )
    weekly, weekly_perf = _measure(
        state, lambda: maintain(state_dir=state, backend="filesystem", max_fingerprints=200, deep=True, snapshot_kind="weekly")
    )
    query, query_perf = _measure(state, lambda: timeline_context(state_dir=state, days=7))
    project_id = understanding["project"]["project_id"]
    growth = _growth_simulation(state, project_id, max(1, arguments.events_per_day))
    payload = {
        "synthetic_root": str(fixture), "state_path": str(state), "files_per_project": arguments.files_per_project,
        "onboarding": {"performance": onboarding_perf, "counts": onboarding["counts"]},
        "no_op_maintenance": {"performance": no_op_perf, "meaningful_changes": no_op["meaningful_changes"], "events": no_op["events_recorded"]},
        "small_change_maintenance": {"performance": small_perf, "counts": small["counts"], "meaningful_changes": small["meaningful_changes"]},
        "hundred_file_change": {"performance": hundred_perf, "counts": hundred["counts"], "meaningful_changes": hundred["meaningful_changes"]},
        "one_project_change": {"performance": project_change_perf, "counts": project_change["counts"], "affected_projects": project_change["affected_projects"]},
        "daily_snapshot": {"performance": daily_perf, "snapshot": daily["snapshot"]},
        "weekly_deep_snapshot": {"performance": weekly_perf, "deep_project_refreshes": weekly["deep_project_refreshes"]},
        "timeline_query": {"performance": query_perf, "events": query["raw_event_count"], "meaningful": query["meaningful_event_count"]},
        "growth_simulation": growth, "final_database_bytes": _state_bytes(state), "physical_project_actions": 0,
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
