#!/usr/bin/env python3
"""Render project-audit JSON, Markdown, and a non-executable archive draft."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from _common import (
    AuditInputError,
    ensure_output_outside_project,
    load_document,
    load_request,
    read_inventory,
    resolve_project_root,
    split_reasons,
    status_object,
    unique,
    write_json,
)


INCOMPLETE_REASONS = {
    "PERMISSION_DENIED",
    "DIRECTORY_SCAN_FAILED",
    "ENTRY_STAT_FAILED",
    "HASH_READ_FAILED",
    "BROKEN_LINK",
    "SYMLINK_BOUNDARY",
    "JUNCTION_BOUNDARY",
    "CONTENT_SCAN_FAILED",
}


def render_markdown(audit: dict[str, Any]) -> str:
    facts = audit["facts"]
    lines = [
        "# PROJECT_AUDIT",
        "",
        f"Audit ID: `{audit['audit_id']}`  ",
        f"Project ID: `{audit['project_id']}`  ",
        f"Status: **{audit['status']['status']}**  ",
        f"Snapshot: `{audit['created_at']}`",
        "",
        "## Facts",
        "",
        f"- Inventory entries: {facts['inventory_entries']}",
        f"- Files: {facts['files']}",
        f"- Directories: {facts['directories']}",
        f"- Link boundaries: {facts['link_boundaries']}",
        f"- Authority candidates: {len(audit['authority_candidates'])}",
        f"- Classification profile: {facts['classification_profile']['profile_id'] or 'none'}",
        f"- Primary asset candidates: {facts['classification_profile']['primary_asset_candidate_count']}",
        "- Asset classifications are heuristic, not scientific facts.",
        "",
        "## Risks",
        "",
    ]
    if audit["risks"]:
        lines.extend(["| ID | Type | Severity | Relative paths |", "|---|---|---|---|"])
        for risk in audit["risks"]:
            lines.append(
                f"| {risk['risk_id']} | {risk['risk_type']} | {risk['severity']} | {', '.join(risk['relative_paths'])} |"
            )
    else:
        lines.append("No configured risk condition was detected.")
    lines.extend(["", "## Authority candidates", ""])
    if audit["authority_candidates"]:
        for candidate in audit["authority_candidates"]:
            lines.append(f"- `{candidate['relative_path']}` — candidate only; human confirmation required.")
    else:
        lines.append("No declared entrypoint was observed as an authority candidate.")
    lines.extend(["", "## Archive recommendations", ""])
    if audit["archive_recommendations"]:
        for item in audit["archive_recommendations"]:
            lines.append(f"- `{item['risk_id']}`: {item['recommendation']} Status: **NOT_AUTHORIZED**.")
    else:
        lines.append("No archive recommendation was generated.")
    lines.extend(["", "## Human decision required", ""])
    for decision in audit["human_decision_required"]:
        lines.append(f"- {decision}")
    lines.extend(["", "## Limitations", ""])
    for limitation in audit["limitations"]:
        lines.append(f"- {limitation}")
    return "\n".join(lines) + "\n"


def render_archive_plan(audit: dict[str, Any]) -> str:
    lines = [
        "# Archive Recommendation Draft",
        "",
        "This document is advisory only. No filesystem action is authorized.",
        "",
        "| Risk | Candidate paths | Recommendation | Preconditions | Human approval | Action status |",
        "|---|---|---|---|---|---|",
    ]
    for item in audit["archive_recommendations"]:
        lines.append(
            f"| {item['risk_id']} | {', '.join(item['relative_paths'])} | {item['recommendation']} | "
            "Verify references, reproducibility, ownership, and backup. | REQUIRED | NOT_AUTHORIZED |"
        )
    if not audit["archive_recommendations"]:
        lines.append("| none | none | No recommendation | Human review of scope | REQUIRED | NOT_AUTHORIZED |")
    return "\n".join(lines) + "\n"


def evaluate(request_path: str | Path, inventory_path: str | Path, classifications_path: str | Path, risks_path: str | Path) -> dict[str, Any]:
    request = load_request(request_path)
    rows = read_inventory(inventory_path)
    classifications = load_document(classifications_path)
    register = load_document(risks_path)
    items = classifications.get("items", [])
    risks = register.get("risks", [])
    if not isinstance(items, list) or not isinstance(risks, list):
        raise AuditInputError("Classification and risk inputs must contain arrays")
    reasons = unique(
        reason
        for row in rows
        for reason in split_reasons(row["reason_codes"])
        if reason in INCOMPLETE_REASONS
    )
    register_status = register.get("status", {})
    if isinstance(register_status, dict):
        reasons.extend(reason for reason in register_status.get("reason_codes", []) if reason in INCOMPLETE_REASONS)
    reasons = unique(reasons)
    overall = status_object("PARTIAL", reasons) if reasons else status_object("PASS", [])
    authority_candidates = [
        {
            "relative_path": item["relative_path"],
            "basis": item.get("authority_basis", []),
            "status": "candidate-only",
            "requires_human_decision": True,
        }
        for item in items
        if isinstance(item, dict) and item.get("authority_candidate") is True
    ]
    classifications_count = Counter(item.get("classification", "unknown") for item in items if isinstance(item, dict))
    recommendations = [
        {
            "risk_id": risk["risk_id"],
            "relative_paths": risk["relative_paths"],
            "recommendation": risk["recommendation"],
            "action_status": "NOT_AUTHORIZED",
            "requires_human_decision": True,
        }
        for risk in risks
        if isinstance(risk, dict)
    ]
    decisions = [f"Confirm whether {candidate['relative_path']} is authoritative for its declared role." for candidate in authority_candidates]
    decisions.extend(f"Review {risk['risk_id']} ({risk['risk_type']}) before any archive or retention decision." for risk in risks)
    if not decisions:
        decisions.append("Confirm that the declared audit scope and exclusions are complete.")
    profile_summary = classifications.get("profile")
    if not isinstance(profile_summary, dict):
        profile_summary = {
            "applied": False,
            "profile_id": None,
            "included_count": 0,
            "excluded_count": 0,
            "priority_count": 0,
            "primary_asset_candidate_count": 0,
            "environment_count": 0,
            "cache_count": 0,
            "source_hint_count": 0,
        }
    return {
        "schema_version": "0.1.0",
        "audit_id": request["audit_id"],
        "project_id": request["project"]["project_id"],
        "created_at": request["report"]["created_at"],
        "status": overall,
        "facts": {
            "inventory_entries": len(rows),
            "files": sum(row["entry_kind"] == "file" for row in rows),
            "directories": sum(row["entry_kind"] == "directory" for row in rows),
            "link_boundaries": sum(row["entry_kind"] in ("symlink", "junction") for row in rows),
            "classification_counts": dict(sorted(classifications_count.items())),
            "classification_profile": profile_summary,
        },
        "authority_candidates": authority_candidates,
        "risks": risks,
        "archive_recommendations": recommendations,
        "human_decision_required": decisions,
        "limitations": [
            "The audit is read-only and snapshot-bound.",
            "Classifications and authority candidates are heuristic.",
            "No deletion, movement, rename, archive operation, or shell command is authorized.",
            "Scientific authority and final-version selection remain human decisions.",
            "Profile-assisted primary asset candidates remain heuristic and require human review.",
        ],
    }


def run(
    request_path: str | Path,
    inventory_path: str | Path,
    classifications_path: str | Path,
    risks_path: str | Path,
    output_json: str | Path,
    output_markdown: str | Path,
    archive_plan: str | Path,
) -> dict[str, Any]:
    request = load_request(request_path)
    root = resolve_project_root(request_path, request)
    destinations = [ensure_output_outside_project(path, root) for path in (output_json, output_markdown, archive_plan)]
    audit = evaluate(request_path, inventory_path, classifications_path, risks_path)
    write_json(destinations[0], audit)
    destinations[1].parent.mkdir(parents=True, exist_ok=True)
    destinations[1].write_text(render_markdown(audit), encoding="utf-8", newline="\n")
    destinations[2].parent.mkdir(parents=True, exist_ok=True)
    destinations[2].write_text(render_archive_plan(audit), encoding="utf-8", newline="\n")
    return audit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request")
    parser.add_argument("inventory")
    parser.add_argument("classifications")
    parser.add_argument("risks")
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-markdown", required=True)
    parser.add_argument("--archive-plan", required=True)
    args = parser.parse_args(argv)
    try:
        run(
            args.request,
            args.inventory,
            args.classifications,
            args.risks,
            args.output_json,
            args.output_markdown,
            args.archive_plan,
        )
    except AuditInputError as exc:
        print(f"INPUT_ERROR: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"IO_ERROR: {exc}", file=sys.stderr)
        return 3
    print(Path(args.output_markdown).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
