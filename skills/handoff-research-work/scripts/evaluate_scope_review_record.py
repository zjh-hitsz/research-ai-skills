#!/usr/bin/env python3
"""Validate a human-authored partial-scope review without approving it."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from _common import (
    HandoffInputError,
    expect_keys,
    load_document,
    parse_datetime,
    stable_id,
    validate_status_object,
)


FIELDS = {
    "scope_id",
    "reviewed_boundaries",
    "accepted_exclusions",
    "blocked_items",
    "reviewer_role",
    "timestamp",
    "decision",
}
AUTOMATED_ROLE_TOKENS = {"agent", "ai", "automation", "automated", "bot", "codex", "machine"}


def unique_strings(value: Any, label: str, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise HandoffInputError(f"{label} must contain non-empty strings")
    if not allow_empty and not value:
        raise HandoffInputError(f"{label} must not be empty")
    if len(value) != len(set(value)):
        raise HandoffInputError(f"{label} must contain unique values")
    return list(value)


def evaluate_scope_review(record: dict[str, Any], audit: dict[str, Any]) -> dict[str, Any]:
    """Evaluate record completeness and coverage; never create or change its decision."""
    expect_keys(record, FIELDS, FIELDS, "scope review record")
    scope_id = stable_id(record["scope_id"], "scope_id")
    reviewed = unique_strings(record["reviewed_boundaries"], "reviewed_boundaries", allow_empty=False)
    accepted_exclusions = unique_strings(record["accepted_exclusions"], "accepted_exclusions")
    blocked_items = unique_strings(record["blocked_items"], "blocked_items")
    reviewer_role = record["reviewer_role"]
    if not isinstance(reviewer_role, str) or not reviewer_role.strip():
        raise HandoffInputError("reviewer_role must identify a human review role")
    role_tokens = set(reviewer_role.lower().replace("-", " ").replace("_", " ").split())
    if role_tokens & AUTOMATED_ROLE_TOKENS:
        raise HandoffInputError("reviewer_role must not identify an automated reviewer")
    timestamp = parse_datetime(record["timestamp"], "timestamp")
    decision = record["decision"]
    if decision not in ("accepted", "rejected"):
        raise HandoffInputError("decision must be accepted or rejected and must be supplied by a human reviewer")

    audit_status, audit_reasons = validate_status_object(audit.get("status"), "project audit status")
    missing_boundaries = [reason for reason in audit_reasons if reason not in reviewed]
    if audit_status == "FAIL":
        status = "FAIL"
        reason_codes = [*audit_reasons, "UPSTREAM_AUDIT_FAILED"]
    elif audit_status == "SKIPPED":
        status = "PARTIAL"
        reason_codes = [*audit_reasons, "UPSTREAM_AUDIT_INCOMPLETE"]
    elif decision == "rejected":
        status = "FAIL"
        reason_codes = ["SCOPE_REVIEW_REJECTED"]
        if not blocked_items:
            reason_codes.append("SCOPE_REVIEW_INCOMPLETE")
    elif blocked_items:
        status = "FAIL"
        reason_codes = ["SCOPE_REVIEW_HAS_BLOCKED_ITEMS"]
    elif missing_boundaries or (audit_status == "PARTIAL" and not accepted_exclusions):
        status = "PARTIAL"
        reason_codes = ["SCOPE_REVIEW_INCOMPLETE"]
    else:
        status = "PASS"
        reason_codes = []

    return {
        "scope_id": scope_id,
        "decision": decision,
        "status": status,
        "reason_codes": list(dict.fromkeys(reason_codes)),
        "reviewed_boundaries": reviewed,
        "accepted_exclusions": accepted_exclusions,
        "blocked_items": blocked_items,
        "reviewer_role": reviewer_role,
        "timestamp": timestamp,
        "missing_boundaries": missing_boundaries,
        "automatic_approval_performed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("record")
    parser.add_argument("--audit", required=True)
    args = parser.parse_args(argv)
    try:
        result = evaluate_scope_review(load_document(args.record), load_document(args.audit))
    except HandoffInputError as exc:
        print(f"INPUT_ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
