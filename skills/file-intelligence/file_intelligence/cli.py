from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from . import __version__
from .engine import (
    FileIntelligenceError,
    assertions,
    dashboard,
    deep_onboard,
    get_asset_details,
    last_changes,
    list_projects,
    maintain,
    migrate_state,
    rollback_state,
    status,
    understand_project,
)


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--state-dir", default=argparse.SUPPRESS,
        help="External local state directory. Accepted before or after the subcommand.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Portable, read-only File Intelligence engine")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--state-dir", help="External local state directory. Never place it inside the Skill directory.")
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

    migrate = commands.add_parser("migrate", help="Preview or explicitly apply a versioned state migration")
    _common(migrate)
    migrate.add_argument("--apply", action="store_true", help="Create an integrity-checked backup, then apply the migration")

    rollback = commands.add_parser("rollback", help="Preview or explicitly restore a migration backup")
    _common(rollback)
    rollback.add_argument("--backup", required=True)
    rollback.add_argument("--apply", action="store_true")

    understand = commands.add_parser("understand", help="Build evidence-backed understanding for one catalogued project root")
    _common(understand)
    understand.add_argument("--project-root", required=True)
    understand.add_argument("--max-inspections", type=int, default=400)
    understand.add_argument("--max-stage1-hashes", type=int, default=1200)
    understand.add_argument("--max-full-hashes", type=int, default=32)
    understand.add_argument("--exclude-content-pattern", action="append", default=[])
    understand.add_argument("--summary-only", action="store_true", help="Print a compact summary; the full result remains in local state")

    asset = commands.add_parser("asset", help="Explain one asset, its authority, references, identity, and archive recommendation")
    _common(asset)
    asset.add_argument("--path", required=True)
    asset.add_argument("--verify-full-hash", action="store_true", help="Read the complete file once and store Stage-2 SHA-256 evidence in local state")

    assertion = commands.add_parser("assertion", help="Manage local explicit-user knowledge without modifying project files")
    _common(assertion)
    assertion.add_argument("action", choices=("list", "set", "remove"))
    assertion.add_argument("--assertion-id")
    assertion.add_argument("--subject-type", choices=("project", "asset", "workstream"))
    assertion.add_argument("--subject-key")
    assertion.add_argument("--predicate")
    assertion.add_argument("--value", help="JSON value, or a plain string")

    home = commands.add_parser("dashboard", help="Regenerate File Intelligence Home from private local state")
    _common(home)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
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
        elif arguments.command == "changes":
            payload = last_changes(state_dir=arguments.state_dir)
        elif arguments.command == "migrate":
            payload = migrate_state(state_dir=arguments.state_dir, apply=arguments.apply)
        elif arguments.command == "rollback":
            payload = rollback_state(state_dir=arguments.state_dir, backup_path=arguments.backup, apply=arguments.apply)
        elif arguments.command == "understand":
            payload = understand_project(
                arguments.project_root,
                state_dir=arguments.state_dir,
                max_inspections=arguments.max_inspections,
                max_stage1_hashes=arguments.max_stage1_hashes,
                max_full_hashes=arguments.max_full_hashes,
                content_exclude_patterns=arguments.exclude_content_pattern,
            )
            if arguments.summary_only:
                payload = {
                    "schema_version": payload["schema_version"], "project": payload["project"],
                    "workstreams": payload["workstreams"], "authority_count": payload.get("authority_total", len(payload["authorities"])),
                    "authority_sample": payload["authorities"][:20], "dependencies": payload["dependencies"],
                    "duplicates": payload["duplicates"], "archive_candidates": payload["archive_candidates"],
                    "inspections": payload["inspections"], "hashes": payload["hashes"],
                    "dashboard": payload["dashboard"], "result_path": payload["result_path"], "physical_actions": 0,
                }
        elif arguments.command == "asset":
            payload = get_asset_details(arguments.path, state_dir=arguments.state_dir, verify_full_hash=arguments.verify_full_hash)
        elif arguments.command == "assertion":
            value = arguments.value
            if value is not None:
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    pass
            payload = assertions(
                state_dir=arguments.state_dir, action=arguments.action, assertion_id=arguments.assertion_id,
                subject_type=arguments.subject_type, subject_key=arguments.subject_key,
                predicate=arguments.predicate, value=value,
            )
        else:
            payload = dashboard(state_dir=arguments.state_dir)
    except FileIntelligenceError as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc), "physical_actions": 0}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
