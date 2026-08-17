from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import uuid
from contextlib import closing
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from file_intelligence.engine import deep_onboard  # noqa: E402


def _memory() -> int | None:
    try:
        import psutil  # type: ignore

        return int(psutil.Process().memory_info().rss)
    except ImportError:
        return None


def _catalog_paths(state: Path) -> list[str]:
    uri = f"file:{(state / 'catalog.db').as_posix()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        return [str(row[0]) for row in connection.execute("SELECT path FROM files WHERE status='present' ORDER BY path_key")]


def _run(root: Path, state: Path, backend: str, everything_cli: Path | None) -> dict[str, object]:
    wall = time.perf_counter()
    cpu = time.process_time()
    before_memory = _memory()
    result = deep_onboard(
        [root], state_dir=state, backend=backend, everything_cli=everything_cli,
        max_fingerprints=0,
    )
    after_memory = _memory()
    paths = _catalog_paths(state)
    return {
        "state_path": str(state),
        "backend": result["backend"],
        "elapsed_seconds": round(time.perf_counter() - wall, 6),
        "cpu_seconds": round(time.process_time() - cpu, 6),
        "rss_before": before_memory,
        "rss_after": after_memory,
        "rss_delta": None if before_memory is None or after_memory is None else after_memory - before_memory,
        "files_indexed": len(paths),
        "aggregate_files": result["counts"].get("aggregate_files", 0),
        "database_bytes": (state / "catalog.db").stat().st_size,
        "unicode_paths": sum(1 for path in paths if any(ord(character) > 127 for character in path)),
        "maximum_path_characters": max((len(path) for path in paths), default=0),
        "paths": paths,
        "warning": result.get("warning"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only filesystem-vs-Everything discovery benchmark")
    parser.add_argument("--root", required=True)
    parser.add_argument("--state-parent", required=True)
    parser.add_argument("--everything-cli")
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args()
    root = Path(arguments.root).resolve()
    state_parent = Path(arguments.state_parent).resolve()
    state_parent.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex[:12]
    filesystem = _run(root, state_parent / f"filesystem-{run_id}", "filesystem", None)
    everything = _run(
        root, state_parent / f"everything-{run_id}", "everything",
        Path(arguments.everything_cli).resolve() if arguments.everything_cli else None,
    )
    fs_paths = set(filesystem.pop("paths"))
    es_paths = set(everything.pop("paths"))
    payload = {
        "root": str(root),
        "filesystem": filesystem,
        "everything": everything,
        "consistency": {
            "common": len(fs_paths & es_paths),
            "filesystem_only": len(fs_paths - es_paths),
            "everything_only": len(es_paths - fs_paths),
            "path_set_equal": fs_paths == es_paths,
            "filesystem_only_sample": sorted(fs_paths - es_paths)[:30],
            "everything_only_sample": sorted(es_paths - fs_paths)[:30],
        },
        "project_files_modified": 0,
    }
    output = Path(arguments.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, output)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
