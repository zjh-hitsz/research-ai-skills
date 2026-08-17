from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from . import __version__
from .engine import FileIntelligenceError, deep_onboard, last_changes, list_projects, maintain, status


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state-dir", help="External local state directory. Never place it inside the Skill directory.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Portable, read-only File Intelligence engine")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    status_parser = commands.add_parser("status", help="Report whether this machine needs Deep Onboarding")
    _common(status_parser)
    status_parser.add_argument("--everything-cli")

    onboard = commands.add_parser("onboard", help="Create this machine's private baseline")
    _common(onboard)
    onboard.add_argument("--root", action="append", required=True, help="Read-only primary scan root; repeatable")
    onboard.add_argument("--communication-root", action="append", default=[], help="Read-only communication attachment root; repeatable")
    onboard.add_argument("--backend", choices=("auto", "everything", "filesystem"), default="auto")
    onboard.add_argument("--everything-cli")
    onboard.add_argument("--max-fingerprints", type=int, default=2000)
    onboard.add_argument("--rebuild", action="store_true", help="Archive existing local state and build an explicit new baseline")

    maintenance = commands.add_parser("maintain", help="Compare registered scopes with the local catalog")
    _common(maintenance)
    maintenance.add_argument("--backend", choices=("auto", "everything", "filesystem"), default="auto")
    maintenance.add_argument("--everything-cli")
    maintenance.add_argument("--max-fingerprints", type=int, default=2000)

    projects = commands.add_parser("projects", help="List heuristic local Project candidates")
    _common(projects)

    changes = commands.add_parser("changes", help="Show the latest local change set")
    _common(changes)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        if arguments.command == "status":
            payload = status(state_dir=arguments.state_dir, everything_cli=arguments.everything_cli)
        elif arguments.command == "onboard":
            payload = deep_onboard(
                arguments.root,
                arguments.communication_root,
                state_dir=arguments.state_dir,
                backend=arguments.backend,
                everything_cli=arguments.everything_cli,
                rebuild=arguments.rebuild,
                max_fingerprints=arguments.max_fingerprints,
            )
        elif arguments.command == "maintain":
            payload = maintain(
                state_dir=arguments.state_dir,
                backend=arguments.backend,
                everything_cli=arguments.everything_cli,
                max_fingerprints=arguments.max_fingerprints,
            )
        elif arguments.command == "projects":
            payload = list_projects(state_dir=arguments.state_dir)
        else:
            payload = last_changes(state_dir=arguments.state_dir)
    except FileIntelligenceError as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc), "physical_actions": 0}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
