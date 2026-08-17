from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from . import __version__
from .engine import (
    FileIntelligenceError,
    assertions,
    dashboard,
    deep_onboard,
    get_asset_details,
    file_history,
    last_changes,
    list_projects,
    maintain,
    machine_binding,
    migrate_state,
    project_history,
    retention,
    resolve_state_dir,
    rollback_state,
    scheduler_support,
    snapshot_now,
    status,
    storage_history,
    timeline_context,
    timeline_history,
    understand_project,
)
from .reconciliation import ReconciliationError, reconcile_legacy_state


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--state-dir", default=argparse.SUPPRESS,
        help="External local state directory. Accepted before or after the subcommand.",
    )


def _time_range(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--since-last-scan", action="store_true")
    parser.add_argument("--days", type=int)
    parser.add_argument("--from", dest="from_value")
    parser.add_argument("--to", dest="to_value")
    parser.add_argument("--project")


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
    maintenance.add_argument(
        "--backend", choices=("auto", "everything", "filesystem"), default="auto",
        help="auto inherits the baseline backend; explicit values are reviewed overrides",
    )
    maintenance.add_argument("--everything-cli")
    maintenance.add_argument("--max-fingerprints", type=int, default=2000)
    maintenance.add_argument("--deep", action="store_true", help="Refresh only affected understood projects with bounded inspectors")
    maintenance.add_argument("--snapshot-kind", choices=("daily", "weekly", "manual"))

    projects = commands.add_parser("projects", help="List heuristic local Project candidates")
    _common(projects)

    changes = commands.add_parser("changes", help="Show the latest local change set")
    _common(changes)

    migrate = commands.add_parser("migrate", help="Preview or explicitly apply a versioned state migration")
    _common(migrate)
    migrate.add_argument("--apply", action="store_true", help="Create an integrity-checked backup, then apply the migration")

    reconcile = commands.add_parser(
        "reconcile-state",
        help="Preview or import a reviewed legacy FileCard/ProjectCard database into schema-v3 Core",
    )
    _common(reconcile)
    reconcile.add_argument("--source", required=True, help="Read-only legacy FileCard SQLite database")
    reconcile.add_argument("--source-machine-binding")
    reconcile.add_argument(
        "--reviewed-unbound-source",
        action="store_true",
        help="Confirm that an unbound legacy source has been reviewed for this machine",
    )
    reconcile.add_argument("--apply", action="store_true", help="Back up the target catalog, then import transactionally")

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

    timeline = commands.add_parser("timeline", help="Query append-oriented historical events")
    _common(timeline)
    _time_range(timeline)
    timeline.add_argument("--event-type")
    timeline.add_argument("--importance", choices=("VERY_HIGH", "HIGH", "MEDIUM", "LOW", "VOLATILE"))
    timeline.add_argument("--limit", type=int, default=500)

    context = commands.add_parser("context-summary", help="Return a structured semantic summary for Codex natural-language answers")
    _common(context)
    _time_range(context)

    important = commands.add_parser("important-changes", help="Return important semantic changes and alerts for a time range")
    _common(important)
    _time_range(important)

    project_history_parser = commands.add_parser("project-history", help="Show events and snapshots for one project")
    _common(project_history_parser)
    project_history_parser.add_argument("project")
    project_history_parser.add_argument("--limit", type=int, default=1000)

    file_history_parser = commands.add_parser("file-history", help="Show a stable FileCard and its history by path or file identity")
    _common(file_history_parser)
    file_history_parser.add_argument("file")
    file_history_parser.add_argument("--limit", type=int, default=500)

    growth = commands.add_parser("storage-growth", help="Compare materialized storage snapshots")
    _common(growth)
    growth.add_argument("--days", type=int, default=7)
    growth.add_argument("--project")

    snapshot_parser = commands.add_parser("snapshot", help="Create a lightweight materialized knowledge snapshot")
    _common(snapshot_parser)
    snapshot_parser.add_argument("--kind", choices=("daily", "weekly", "monthly", "manual"), default="manual")

    retention_parser = commands.add_parser("retention", help="Preview conservative private-state retention; apply only explicitly")
    _common(retention_parser)
    retention_parser.add_argument("--apply", action="store_true")

    scheduler = commands.add_parser("schedule-plan", help="Describe reversible Windows scheduled-maintenance support without installing a task")
    _common(scheduler)
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
                deep=arguments.deep,
                snapshot_kind=arguments.snapshot_kind,
            )
        elif arguments.command == "projects":
            payload = list_projects(state_dir=arguments.state_dir)
        elif arguments.command == "changes":
            payload = last_changes(state_dir=arguments.state_dir)
        elif arguments.command == "migrate":
            payload = migrate_state(state_dir=arguments.state_dir, apply=arguments.apply)
        elif arguments.command == "reconcile-state":
            payload = reconcile_legacy_state(
                state_dir=resolve_state_dir(arguments.state_dir),
                source_path=Path(arguments.source).expanduser().resolve(),
                target_machine_binding=machine_binding(),
                source_machine_binding=arguments.source_machine_binding,
                reviewed_unbound_source=arguments.reviewed_unbound_source,
                apply=arguments.apply,
            )
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
                    "authority_scope_counts": payload.get("authority_scope_counts", {}), "tool_roles": payload.get("tool_roles", []),
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
        elif arguments.command == "dashboard":
            payload = dashboard(state_dir=arguments.state_dir)
        elif arguments.command == "timeline":
            payload = timeline_history(
                state_dir=arguments.state_dir, since_last_scan=arguments.since_last_scan, days=arguments.days,
                from_value=arguments.from_value, to_value=arguments.to_value, project=arguments.project,
                event_type=arguments.event_type, importance=arguments.importance, limit=arguments.limit,
            )
        elif arguments.command in {"context-summary", "important-changes"}:
            payload = timeline_context(
                state_dir=arguments.state_dir, since_last_scan=arguments.since_last_scan, days=arguments.days,
                from_value=arguments.from_value, to_value=arguments.to_value, project=arguments.project,
            )
            if arguments.command == "important-changes":
                payload = {
                    "time_range": payload["time_range"], "important_changes": payload["important_changes"],
                    "asset_alerts": payload["asset_alerts"], "uncertainties": payload["uncertainties"],
                    "meaningful_event_count": payload["meaningful_event_count"], "physical_actions": 0,
                }
        elif arguments.command == "project-history":
            payload = project_history(project=arguments.project, state_dir=arguments.state_dir, limit=arguments.limit)
        elif arguments.command == "file-history":
            payload = file_history(file=arguments.file, state_dir=arguments.state_dir, limit=arguments.limit)
        elif arguments.command == "storage-growth":
            payload = storage_history(state_dir=arguments.state_dir, days=arguments.days, project=arguments.project)
        elif arguments.command == "snapshot":
            payload = snapshot_now(state_dir=arguments.state_dir, snapshot_kind=arguments.kind)
        elif arguments.command == "retention":
            payload = retention(state_dir=arguments.state_dir, apply=arguments.apply)
        else:
            payload = scheduler_support(state_dir=arguments.state_dir)
    except (FileIntelligenceError, ReconciliationError) as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc), "physical_actions": 0}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
