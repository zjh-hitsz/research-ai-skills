#!/usr/bin/env python3
"""Evaluate existing regression records without executing their commands."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from _common import HandoffInputError, load_document, merge_readiness, parse_datetime, stable_id, validate_status


CANONICAL_FIELDS = {
    "regression_id",
    "command_record",
    "exit_code",
    "expected_ref",
    "actual_ref",
    "status",
    "reason_codes",
}
STRUCTURED_FIELDS = {
    "regression_id",
    "command_record",
    "expected",
    "actual",
    "status",
    "reason_codes",
    "artifact_refs",
    "timestamp",
}


def normalize_regression_record(
    record: dict[str, Any], selected_artifact_ids: set[str] | None = None
) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise HandoffInputError("Regression record must be an object")
    if set(record) == CANONICAL_FIELDS:
        return record
    if set(record) != STRUCTURED_FIELDS:
        raise HandoffInputError("Regression record fields match neither the structured template nor handoff-manifest-v1")

    stable_id(record["regression_id"], "regression_id")
    status, reasons = validate_status(record["status"], record["reason_codes"], "regression")
    expected = record["expected"]
    actual = record["actual"]
    if not isinstance(expected, str) or not expected:
        raise HandoffInputError("structured regression expected must be a non-empty reference")
    if actual is not None and (not isinstance(actual, str) or not actual):
        raise HandoffInputError("structured regression actual must be a non-empty reference or null")

    artifact_refs = record["artifact_refs"]
    if not isinstance(artifact_refs, list):
        raise HandoffInputError("structured regression artifact_refs must be an array")
    normalized_refs = [stable_id(item, f"artifact_refs[{index}]") for index, item in enumerate(artifact_refs)]
    if len(normalized_refs) != len(set(normalized_refs)):
        raise HandoffInputError("structured regression artifact_refs must be unique")
    if selected_artifact_ids is not None:
        unknown = sorted(set(normalized_refs) - selected_artifact_ids)
        if unknown:
            raise HandoffInputError(f"structured regression references unselected artifacts: {unknown}")

    command = record["command_record"]
    command_fields = {"entrypoint_ref", "invocation_summary", "authorized", "exit_code"}
    if not isinstance(command, dict) or set(command) != command_fields:
        raise HandoffInputError("structured regression command_record fields are invalid")
    stable_id(command["entrypoint_ref"], "command_record.entrypoint_ref")
    if not isinstance(command["invocation_summary"], str) or not command["invocation_summary"]:
        raise HandoffInputError("invocation_summary is required")
    if not isinstance(command["authorized"], bool):
        raise HandoffInputError("command_record.authorized must be boolean")
    exit_code = command["exit_code"]
    if exit_code is not None and (not isinstance(exit_code, int) or isinstance(exit_code, bool)):
        raise HandoffInputError("command_record.exit_code must be integer or null")
    timestamp = parse_datetime(record["timestamp"], "timestamp")

    if status == "PASS":
        if not command["authorized"] or exit_code != 0 or actual is None or expected != actual or not normalized_refs:
            raise HandoffInputError("structured PASS requires authorization, exit code zero, equal expected/actual, and artifact_refs")
    elif status == "FAIL" and actual is None:
        raise HandoffInputError("structured FAIL requires actual evidence; use PARTIAL when evidence is missing")

    return {
        "regression_id": record["regression_id"],
        "command_record": {
            "entrypoint_ref": command["entrypoint_ref"],
            "invocation_summary": command["invocation_summary"],
            "authorized": command["authorized"],
            "executed_at": timestamp,
        },
        "exit_code": exit_code,
        "expected_ref": expected,
        "actual_ref": actual,
        "status": status,
        "reason_codes": reasons,
    }


def normalize_regression_records(
    records: list[dict[str, Any]], selected_artifact_ids: set[str] | None = None
) -> list[dict[str, Any]]:
    if not isinstance(records, list):
        raise HandoffInputError("regressions must be an array")
    return [normalize_regression_record(record, selected_artifact_ids) for record in records]


def evaluate_record(record: dict[str, Any]) -> dict[str, Any]:
    record = normalize_regression_record(record)
    stable_id(record["regression_id"], "regression_id")
    status, reasons = validate_status(record["status"], record["reason_codes"], "regression")
    command = record["command_record"]
    command_required = {"entrypoint_ref", "invocation_summary", "authorized", "executed_at"}
    if not isinstance(command, dict) or set(command) != command_required:
        raise HandoffInputError("command_record fields do not match handoff-manifest-v1")
    stable_id(command["entrypoint_ref"], "command_record.entrypoint_ref")
    if not isinstance(command["invocation_summary"], str) or not command["invocation_summary"]:
        raise HandoffInputError("invocation_summary is required")
    if not isinstance(command["authorized"], bool):
        raise HandoffInputError("command_record.authorized must be boolean")
    if record["exit_code"] is not None and not isinstance(record["exit_code"], int):
        raise HandoffInputError("exit_code must be integer or null")

    if not command["authorized"]:
        result = {"status": "FAIL", "reason_codes": ["UNAUTHORIZED_REGRESSION_RECORD"]}
    elif status == "FAIL":
        failure_reasons = [*reasons]
        if record["exit_code"] is not None and record["exit_code"] != 0:
            failure_reasons.append("UPSTREAM_RUN_FAILED")
        result = {"status": "FAIL", "reason_codes": failure_reasons}
    elif record["exit_code"] is not None and record["exit_code"] != 0:
        result = {"status": "FAIL", "reason_codes": ["UPSTREAM_RUN_FAILED"]}
    elif status in ("PARTIAL", "SKIPPED"):
        result = {"status": "PARTIAL", "reason_codes": reasons}
    elif command["executed_at"] is None or record["exit_code"] is None or record["actual_ref"] is None:
        result = {"status": "PARTIAL", "reason_codes": ["REGRESSION_RECORD_INCOMPLETE"]}
    else:
        result = {"status": "PASS", "reason_codes": []}
    return {
        "regression_id": record["regression_id"],
        "status": result["status"],
        "reason_codes": list(dict.fromkeys(result["reason_codes"])),
        "command_executed_by_skill": False,
    }


def evaluate_records(records: list[dict[str, Any]], require_regression: bool = False) -> dict[str, Any]:
    if not records:
        if require_regression:
            return {"status": "PARTIAL", "reason_codes": ["MISSING_REGRESSION_RECORD"], "records": [], "command_executed_by_skill": False}
        return {"status": "PASS", "reason_codes": [], "records": [], "command_executed_by_skill": False}
    evaluated = [evaluate_record(record) for record in normalize_regression_records(records)]
    merged = merge_readiness((item["status"], item["reason_codes"]) for item in evaluated)
    return {**merged, "records": evaluated, "command_executed_by_skill": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("record")
    parser.add_argument("--require-regression", action="store_true")
    args = parser.parse_args(argv)
    try:
        document = load_document(args.record)
        if "regressions" in document:
            records = document["regressions"]
            if not isinstance(records, list):
                raise HandoffInputError("regressions must be an array")
        else:
            records = [document]
        result = evaluate_records(records, args.require_regression)
    except HandoffInputError as exc:
        print(f"INPUT_ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
