from __future__ import annotations

import argparse
import hashlib
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

from file_intelligence.engine import deep_onboard, maintain, timeline_context, understand_project  # noqa: E402


def _measure(operation: Callable[[], dict[str, Any]]) -> tuple[dict[str, Any], dict[str, float]]:
    wall = time.perf_counter()
    cpu = time.process_time()
    result = operation()
    return result, {
        "elapsed_seconds": round(time.perf_counter() - wall, 6),
        "cpu_seconds": round(time.process_time() - cpu, 6),
    }


def _project_observation(root: Path) -> dict[str, Any]:
    count = 0
    logical_size = 0
    latest_mtime = 0
    path_digest = hashlib.sha256()
    for folder, directories, files in os.walk(root, followlinks=False):
        directories.sort(key=str.casefold)
        files.sort(key=str.casefold)
        folder_path = Path(folder)
        for name in files:
            path = folder_path / name
            try:
                stat = path.stat(follow_symlinks=False)
            except OSError:
                continue
            count += 1
            logical_size += int(stat.st_size)
            latest_mtime = max(latest_mtime, int(stat.st_mtime_ns))
            path_digest.update(str(path.relative_to(root)).casefold().encode("utf-8", errors="replace"))
            path_digest.update(str(stat.st_size).encode("ascii"))
            path_digest.update(str(stat.st_mtime_ns).encode("ascii"))
    return {"file_count": count, "logical_size": logical_size, "latest_mtime_ns": latest_mtime, "metadata_digest": path_digest.hexdigest()}


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only real-project Timeline no-op benchmark")
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--state-parent", required=True, type=Path)
    parser.add_argument("--everything-cli", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--max-inspections", type=int, default=200)
    arguments = parser.parse_args()
    project = arguments.project_root.resolve()
    state = arguments.state_parent.resolve() / f"project-noop-{uuid.uuid4().hex[:12]}"
    before = _project_observation(project)
    onboarding, onboarding_perf = _measure(lambda: deep_onboard(
        [project], state_dir=state, backend="everything" if arguments.everything_cli else "filesystem",
        everything_cli=arguments.everything_cli, max_fingerprints=1000,
    ))
    understanding, understanding_perf = _measure(lambda: understand_project(
        project, state_dir=state, max_inspections=arguments.max_inspections, max_stage1_hashes=600, max_full_hashes=0,
    ))
    first, first_perf = _measure(lambda: maintain(
        state_dir=state, backend="everything" if arguments.everything_cli else "filesystem",
        everything_cli=arguments.everything_cli, max_fingerprints=1000,
    ))
    second, second_perf = _measure(lambda: maintain(
        state_dir=state, backend="everything" if arguments.everything_cli else "filesystem",
        everything_cli=arguments.everything_cli, max_fingerprints=1000,
    ))
    context = timeline_context(state_dir=state, since_last_scan=True)
    after = _project_observation(project)
    payload = {
        "project_root": str(project), "state_path": str(state),
        "project_observation_before": before, "project_observation_after": after,
        "project_unmodified": before == after,
        "onboarding": {"performance": onboarding_perf, "counts": onboarding["counts"]},
        "understanding": {
            "performance": understanding_perf, "workstreams": len(understanding["workstreams"]),
            "authority_total": understanding["authority_total"], "tool_roles": understanding.get("tool_roles", []),
        },
        "first_maintenance": {
            "performance": first_perf, "status": first["status"], "counts": first["counts"],
            "meaningful_changes": first["meaningful_changes"], "events_recorded": first["events_recorded"],
        },
        "second_maintenance": {
            "performance": second_perf, "status": second["status"], "counts": second["counts"],
            "meaningful_changes": second["meaningful_changes"], "events_recorded": second["events_recorded"],
        },
        "second_context": context, "physical_project_actions": 0,
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
