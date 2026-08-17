from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Iterable


def first_executable(hints: Iterable[str | os.PathLike[str] | None], names: Iterable[str]) -> Path | None:
    candidates: list[Path] = []
    for raw in hints:
        if not raw:
            continue
        expanded = Path(os.path.expandvars(os.fspath(raw))).expanduser()
        if expanded.is_dir():
            candidates.extend(expanded / name for name in names)
        else:
            candidates.append(expanded)
    for name in names:
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    seen: set[str] = set()
    for candidate in candidates:
        key = os.path.normcase(os.path.abspath(candidate))
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_file():
            return candidate.resolve()
    return None


def toolbox_hint(config: dict, leaf: str) -> Path | None:
    root = config.get("toolbox_root") or os.environ.get("COMPUTER_INTELLIGENCE_ROOT")
    return Path(root) / "tools" / leaf if root else None


