from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from file_intelligence.privacy import audit_package, render_markdown  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit an exact File Intelligence public package")
    parser.add_argument("--root", type=Path, default=SKILL_ROOT)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--denylist", action="append", default=[], help="Release-specific private marker; repeatable")
    arguments = parser.parse_args()
    result = audit_package(arguments.root, arguments.denylist)
    if arguments.report:
        arguments.report.write_text(render_markdown(result), encoding="utf-8", newline="\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
