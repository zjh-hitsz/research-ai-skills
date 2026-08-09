#!/usr/bin/env python3
"""Detect bounded project archive risks without producing disposition actions."""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
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


WINDOWS_ABSOLUTE = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/][^\s\"'<>]+")
POSIX_ABSOLUTE = re.compile(r"(?<![A-Za-z0-9_])/(?:home|Users|mnt|var|tmp)/[^\s\"'<>]+")
MARKDOWN_REFERENCE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def risk_record(risk_type: str, severity: str, paths: list[str], evidence: dict[str, Any], recommendation: str) -> dict[str, Any]:
    return {
        "risk_id": "",
        "risk_type": risk_type,
        "severity": severity,
        "relative_paths": sorted(unique(paths), key=str.casefold),
        "evidence": evidence,
        "recommendation": recommendation,
        "requires_human_decision": True,
        "automatic_disposition_authorized": False,
    }


def detect_text_risks(root: Path, rows: list[dict[str, str]], request: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    risks: list[dict[str, Any]] = []
    incomplete: list[str] = []
    allowed_extensions = {str(value).casefold() for value in request["risk"]["text_extensions"]}
    maximum = int(request["risk"]["text_scan_max_bytes"])
    for row in rows:
        if row["entry_kind"] != "file" or row["file_type"].casefold() not in allowed_extensions:
            continue
        try:
            size = int(row["size_bytes"])
        except ValueError:
            continue
        if size > maximum:
            continue
        source = root / Path(row["relative_path"])
        try:
            text = source.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            incomplete.append("CONTENT_SCAN_FAILED")
            risks.append(
                risk_record(
                    "CONTENT_SCAN_INCOMPLETE",
                    "medium",
                    [row["relative_path"]],
                    {"reason_code": "CONTENT_SCAN_FAILED"},
                    "Review this file manually before treating the path audit as complete.",
                )
            )
            continue
        absolute_lines = sorted(
            {
                line_number
                for line_number, line in enumerate(text.splitlines(), start=1)
                if WINDOWS_ABSOLUTE.search(line) or POSIX_ABSOLUTE.search(line)
            }
        )
        if absolute_lines:
            risks.append(
                risk_record(
                    "ABSOLUTE_PATH_REFERENCE",
                    "high",
                    [row["relative_path"]],
                    {"line_numbers": absolute_lines, "reference": "<absolute-path-redacted>"},
                    "Review and parameterize the reference; no automatic rewrite is authorized.",
                )
            )
        broken: set[str] = set()
        for match in MARKDOWN_REFERENCE.findall(text):
            reference = match.strip().split()[0].strip("<>\"'")
            if not reference or reference.startswith(("#", "http://", "https://", "mailto:")):
                continue
            candidate = (source.parent / reference).resolve(strict=False)
            try:
                candidate.relative_to(root)
            except ValueError:
                continue
            if not candidate.exists():
                broken.add(candidate.relative_to(root).as_posix())
        if broken:
            risks.append(
                risk_record(
                    "BROKEN_REFERENCE",
                    "medium",
                    [row["relative_path"]],
                    {"missing_relative_references": sorted(broken)},
                    "Review whether each missing reference is obsolete, optional, or an ungenerated dependency.",
                )
            )
    return risks, unique(incomplete)


def evaluate(request_path: str | Path, inventory_path: str | Path, classifications_path: str | Path) -> dict[str, Any]:
    request = load_request(request_path)
    root = resolve_project_root(request_path, request)
    rows = read_inventory(inventory_path)
    classifications = load_document(classifications_path)
    items = classifications.get("items")
    if not isinstance(items, list):
        raise AuditInputError("classifications.items must be an array")
    classification_by_path = {item["relative_path"]: item for item in items if isinstance(item, dict) and "relative_path" in item}
    risks: list[dict[str, Any]] = []

    hashes: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        if row["entry_kind"] == "file" and row["sha256"]:
            hashes[row["sha256"]].append(row["relative_path"])
    for digest, paths in sorted(hashes.items()):
        if len(paths) > 1:
            risks.append(
                risk_record(
                    "DUPLICATE_CANDIDATE",
                    "medium",
                    paths,
                    {"sha256": digest, "match_type": "byte-identical-candidate"},
                    "Review semantic roles and references before considering any archive decision.",
                )
            )

    threshold = int(request["risk"]["large_file_bytes"])
    for row in rows:
        if row["entry_kind"] != "file" or not row["size_bytes"]:
            continue
        size = int(row["size_bytes"])
        if size >= threshold:
            risks.append(
                risk_record(
                    "LARGE_FILE",
                    "medium",
                    [row["relative_path"]],
                    {"size_bytes": size, "criterion_bytes": threshold, "criteria_source": "project_audit_request.risk.large_file_bytes"},
                    "Review storage, reproducibility, and external-archive needs; no relocation is authorized.",
                )
            )

    cache_paths = sorted(
        path
        for path, item in classification_by_path.items()
        if isinstance(item, dict) and item.get("classification") == "cache"
    )
    if cache_paths:
        risks.append(
            risk_record(
                "CACHE_DIRECTORY",
                "low",
                cache_paths,
                {"classification": "heuristic-cache"},
                "Review rebuildability and retention policy; classification does not authorize cleanup.",
            )
        )

    incomplete_reasons: list[str] = []
    for row in rows:
        row_reasons = split_reasons(row["reason_codes"])
        if "BROKEN_LINK" in row_reasons:
            risks.append(
                risk_record(
                    "BROKEN_LINK",
                    "high",
                    [row["relative_path"]],
                    {"scan_reason_codes": row_reasons},
                    "Review whether the missing target is required; do not recreate or remove the link automatically.",
                )
            )
        if any(reason in row_reasons for reason in ("SYMLINK_BOUNDARY", "JUNCTION_BOUNDARY")):
            risks.append(
                risk_record(
                    "LINK_BOUNDARY",
                    "medium",
                    [row["relative_path"]],
                    {"link_scope": row["link_scope"], "scan_reason_codes": row_reasons},
                    "Review the external or unscanned dependency separately before declaring the audit complete.",
                )
            )
        partial = [reason for reason in row_reasons if reason in ("PERMISSION_DENIED", "DIRECTORY_SCAN_FAILED", "ENTRY_STAT_FAILED", "HASH_READ_FAILED")]
        if partial:
            incomplete_reasons.extend(partial)
            risks.append(
                risk_record(
                    "SCAN_INCOMPLETE",
                    "high",
                    [row["relative_path"]],
                    {"scan_reason_codes": partial},
                    "Obtain human authorization or an independent inventory before relying on project completeness.",
                )
            )

    present_paths = {row["relative_path"] for row in rows}
    for expected in request["project"]["expected_outputs"]:
        if not isinstance(expected, dict) or "path" not in expected:
            raise AuditInputError("Each project.expected_outputs item must contain path")
        expected_path = str(expected["path"]).replace("\\", "/")
        if expected_path not in present_paths:
            risks.append(
                risk_record(
                    "EXPECTED_OUTPUT_NOT_CREATED",
                    "medium" if expected.get("required", False) else "low",
                    [expected_path],
                    {"required": bool(expected.get("required", False)), "declared_state": "not-observed"},
                    "Confirm whether the output is pending, intentionally absent, or evidence of an incomplete run.",
                )
            )

    text_risks, text_incomplete = detect_text_risks(root, rows, request)
    risks.extend(text_risks)
    incomplete_reasons.extend(text_incomplete)
    risks.sort(key=lambda item: (item["risk_type"], item["relative_paths"]))
    for index, risk in enumerate(risks, start=1):
        risk["risk_id"] = f"RISK-{index:04d}"
    detector_status = status_object("PARTIAL", unique(incomplete_reasons)) if incomplete_reasons else status_object("PASS", [])
    return {
        "schema_version": "0.1.0",
        "audit_id": request["audit_id"],
        "project_id": request["project"]["project_id"],
        "created_at": request["report"]["created_at"],
        "status": detector_status,
        "criteria": {
            "large_file_bytes": threshold,
            "text_scan_max_bytes": request["risk"]["text_scan_max_bytes"],
            "text_extensions": request["risk"]["text_extensions"],
        },
        "risks": risks,
        "limitations": [
            "Duplicate records are byte-identical candidates, not semantic duplicates.",
            "Risk records do not authorize deletion, movement, renaming, or archival action.",
            "Only configured small text files are inspected for references.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request")
    parser.add_argument("inventory")
    parser.add_argument("classifications")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        request = load_request(args.request)
        root = resolve_project_root(args.request, request)
        destination = ensure_output_outside_project(args.output, root)
        write_json(destination, evaluate(args.request, args.inventory, args.classifications))
    except AuditInputError as exc:
        print(f"INPUT_ERROR: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"IO_ERROR: {exc}", file=sys.stderr)
        return 3
    print(Path(args.output).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
