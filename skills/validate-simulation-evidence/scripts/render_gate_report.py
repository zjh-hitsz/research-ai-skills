#!/usr/bin/env python3
"""Render a gate-report-v1 JSON document as deterministic Markdown."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from _common import SkillInputError, load_contracts, load_document, validate_gate_report, write_text


def inline(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def bullet_section(title: str, items: list[str]) -> list[str]:
    lines = [f"## {title}", ""]
    if items:
        lines.extend(f"- {item}" for item in items)
    else:
        lines.append("- None recorded.")
    lines.append("")
    return lines


def claim_items(boundary: dict[str, Any], status: str) -> list[str]:
    if "claims" not in boundary:
        return list(boundary[status])
    rendered: list[str] = []
    for claim in boundary["claims"]:
        if claim["status"] != status:
            continue
        gates = ", ".join(claim["supporting_gate_ids"]) or "none"
        limitations = "; ".join(claim["limitations"]) or "none"
        rendered.append(
            f"`{claim['claim_id']}` {claim['text']} "
            f"(supporting gates: {gates}; limitations: {limitations})"
        )
    return rendered


def render(report: dict[str, Any]) -> str:
    common_schema, gate_schema = load_contracts()
    validate_gate_report(report, common_schema, gate_schema)
    overall = report["overall"]
    lines = [
        "# Simulation Evidence Gate Report",
        "",
        f"- Report ID: `{inline(report['report_id'])}`",
        f"- Subject: `{inline(report['subject']['type'])}:{inline(report['subject']['id'])}`",
        f"- Producer: `{inline(report['producer']['skill'])}@{inline(report['producer']['skill_version'])}`",
        f"- Created at: `{inline(report['created_at'])}`",
        f"- Criteria source: `{inline(report['criteria_source'])}`",
    ]
    if "lineage" in report:
        lineage = report["lineage"]
        lines.extend(
            [
                f"- Workflow ID: `{inline(lineage['workflow_id'])}`",
                f"- Attempt ID: `{inline(lineage['attempt_id'])}`",
                f"- Stage: `{inline(lineage['stage'])}`",
                f"- Snapshot at: `{inline(lineage['snapshot_at'])}`",
                f"- Subject revision: `{inline(lineage['subject_revision'])}`",
                f"- Supersedes report: `{inline(lineage['supersedes_report_id'])}`",
            ]
        )
    lines.extend(
        [
        "",
        "## Overall evidence status",
        "",
        f"**{overall['status']}**",
        "",
        "Reason codes: " + (", ".join(f"`{code}`" for code in overall["reason_codes"]) or "None"),
        "",
        "## Gate results",
        "",
        "| Gate | Category | Required | Status | Measured value | Criterion | Reason codes | Evidence |",
        "|---|---|---:|---|---|---|---|---|",
        ]
    )
    for gate in report["gates"]:
        criterion = {
            "description": gate["criterion"].get("description"),
            "operator": gate["criterion"].get("operator"),
            "threshold": gate["criterion"].get("threshold"),
            "source_ref": gate["criterion"].get("source_ref"),
        }
        lines.append(
            "| "
            + " | ".join(
                [
                    inline(gate["gate_id"]),
                    inline(gate["category"]),
                    "yes" if gate["required"] else "no",
                    inline(gate["status"]),
                    inline(gate["measured_value"]),
                    inline(criterion),
                    inline(gate["reason_codes"]),
                    inline(gate["evidence_refs"]),
                ]
            )
            + " |"
        )
    lines.append("")
    lines.extend(bullet_section("Supported claims", claim_items(report["claim_boundary"], "supported")))
    lines.extend(bullet_section("Conditional claims", claim_items(report["claim_boundary"], "conditional")))
    lines.extend(bullet_section("Unsupported claims", claim_items(report["claim_boundary"], "unsupported")))
    lines.extend(bullet_section("Limitations", report.get("limitations", [])))
    lines.extend(
        [
            "## Interpretation boundary",
            "",
            "This report records evidence status only. It does not decide physical correctness, novelty, or publication readiness.",
            "",
        ]
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", help="gate-report-v1 JSON document")
    parser.add_argument("--output", default="gate_report.md", help="Output Markdown path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        write_text(args.output, render(load_document(args.report)))
    except SkillInputError as exc:
        print(f"INPUT_ERROR: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"IO_ERROR: {exc}", file=sys.stderr)
        return 3
    print(Path(args.output).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
