#!/usr/bin/env python3
"""Compare explicitly selected conditions between two cases."""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Any

from _common import (
    SkillInputError,
    gate_criterion,
    load_contracts,
    load_document,
    require_keys,
    require_list,
    require_string,
    unique_preserving_order,
    validate_gate,
    write_json,
)


MISSING = object()


def get_path(value: dict[str, Any], dotted_path: str) -> Any:
    current: Any = value
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return MISSING
        current = current[part]
    return current


def relative_difference(left: float, right: float, reference: str) -> float | None:
    if reference == "case-a":
        denominator = abs(left)
    elif reference == "case-b":
        denominator = abs(right)
    elif reference == "max-absolute":
        denominator = max(abs(left), abs(right))
    else:
        raise SkillInputError(f"Unsupported relative_reference: {reference!r}")
    difference = abs(left - right)
    if denominator == 0.0:
        return 0.0 if difference == 0.0 else None
    return difference / denominator


def compare_numeric(left: float, right: float, rule: dict[str, Any], field: str) -> tuple[bool, dict[str, Any]]:
    require_keys(rule, ("mode",), f"numeric_tolerances.{field}")
    mode = rule["mode"]
    allowed = ("absolute", "relative", "absolute-or-relative", "absolute-and-relative")
    if mode not in allowed:
        raise SkillInputError(f"numeric_tolerances.{field}.mode must be one of: {', '.join(allowed)}")
    absolute = abs(left - right)
    absolute_ok: bool | None = None
    relative: float | None = None
    relative_ok: bool | None = None
    if mode in ("absolute", "absolute-or-relative", "absolute-and-relative"):
        if "absolute" not in rule or not isinstance(rule["absolute"], (int, float)) or isinstance(rule["absolute"], bool):
            raise SkillInputError(f"numeric_tolerances.{field}.absolute must be explicitly numeric")
        if rule["absolute"] < 0:
            raise SkillInputError(f"numeric_tolerances.{field}.absolute must be non-negative")
        if not math.isfinite(float(rule["absolute"])):
            raise SkillInputError(f"numeric_tolerances.{field}.absolute must be finite")
        absolute_ok = absolute <= float(rule["absolute"])
    if mode in ("relative", "absolute-or-relative", "absolute-and-relative"):
        require_keys(rule, ("relative", "relative_reference"), f"numeric_tolerances.{field}")
        if not isinstance(rule["relative"], (int, float)) or isinstance(rule["relative"], bool):
            raise SkillInputError(f"numeric_tolerances.{field}.relative must be explicitly numeric")
        if rule["relative"] < 0:
            raise SkillInputError(f"numeric_tolerances.{field}.relative must be non-negative")
        if not math.isfinite(float(rule["relative"])):
            raise SkillInputError(f"numeric_tolerances.{field}.relative must be finite")
        relative = relative_difference(left, right, rule["relative_reference"])
        relative_ok = relative is not None and relative <= float(rule["relative"])
    if mode == "absolute":
        passed = bool(absolute_ok)
    elif mode == "relative":
        passed = bool(relative_ok)
    elif mode == "absolute-or-relative":
        passed = bool(absolute_ok or relative_ok)
    else:
        passed = bool(absolute_ok and relative_ok)
    return passed, {
        "absolute_difference": absolute,
        "relative_difference": relative,
        "rule": rule,
    }


def evaluate(request: dict[str, Any]) -> dict[str, Any]:
    require_keys(
        request,
        (
            "schema_version",
            "check_id",
            "required",
            "case_a",
            "case_b",
            "criterion",
            "evidence_refs",
            "limitations",
        ),
        "fairness request",
    )
    if request["schema_version"] != "1.0.0":
        raise SkillInputError("fairness request.schema_version must be 1.0.0")
    require_string(request["check_id"], "fairness request.check_id")
    if not isinstance(request["required"], bool):
        raise SkillInputError("fairness request.required must be boolean")
    for label in ("case_a", "case_b"):
        case = request[label]
        if not isinstance(case, dict):
            raise SkillInputError(f"fairness request.{label} must be an object")
        require_keys(case, ("id", "conditions"), f"fairness request.{label}")
        require_string(case["id"], f"fairness request.{label}.id")
        if not isinstance(case["conditions"], dict):
            raise SkillInputError(f"fairness request.{label}.conditions must be an object")

    criterion = request["criterion"]
    if not isinstance(criterion, dict):
        raise SkillInputError("fairness request.criterion must be an object")
    require_keys(
        criterion,
        ("required_equal", "numeric_tolerances", "description", "source_ref"),
        "fairness request.criterion",
    )
    fields = require_list(criterion["required_equal"], "fairness request.criterion.required_equal")
    if not fields:
        raise SkillInputError("fairness criterion.required_equal must not be empty")
    fields = [require_string(field, f"fairness criterion.required_equal[{index}]") for index, field in enumerate(fields)]
    if len(fields) != len(set(fields)):
        raise SkillInputError("fairness criterion.required_equal must not contain duplicates")
    tolerances = criterion["numeric_tolerances"]
    if not isinstance(tolerances, dict):
        raise SkillInputError("fairness criterion.numeric_tolerances must be an object")
    unknown_tolerances = sorted(set(tolerances) - set(fields))
    if unknown_tolerances:
        raise SkillInputError(f"numeric tolerance declared for an unchecked field: {', '.join(unknown_tolerances)}")
    description = require_string(criterion["description"], "fairness request.criterion.description")
    source_ref = require_string(criterion["source_ref"], "fairness request.criterion.source_ref")

    mismatches: list[dict[str, Any]] = []
    missing_fields: list[str] = []
    matched_fields: list[str] = []
    for field in fields:
        left = get_path(request["case_a"]["conditions"], field)
        right = get_path(request["case_b"]["conditions"], field)
        if left is MISSING or right is MISSING:
            missing_fields.append(field)
            mismatches.append(
                {
                    "field": field,
                    "kind": "missing",
                    "missing_in": [
                        label
                        for label, value in ((request["case_a"]["id"], left), (request["case_b"]["id"], right))
                        if value is MISSING
                    ],
                }
            )
            continue
        if field in tolerances:
            if not isinstance(left, (int, float)) or isinstance(left, bool) or not isinstance(right, (int, float)) or isinstance(right, bool):
                raise SkillInputError(f"fairness field {field!r} has a numeric tolerance but non-numeric values")
            if not math.isfinite(float(left)) or not math.isfinite(float(right)):
                raise SkillInputError(f"fairness field {field!r} must contain finite numeric values")
            passed, diagnostics = compare_numeric(float(left), float(right), tolerances[field], field)
            if not passed:
                mismatches.append(
                    {
                        "field": field,
                        "kind": "numeric-tolerance",
                        "case_a": left,
                        "case_b": right,
                        **diagnostics,
                    }
                )
            else:
                matched_fields.append(field)
        elif left != right:
            mismatches.append({"field": field, "kind": "exact", "case_a": left, "case_b": right})
        else:
            matched_fields.append(field)

    reasons: list[str] = []
    if missing_fields:
        reasons.append("FAIRNESS_CONDITION_MISSING")
    if mismatches:
        reasons.append("FAIRNESS_CONDITION_MISMATCH")
    reasons = unique_preserving_order(reasons)
    status = "PASS" if not mismatches else "FAIL"
    threshold_contract = [
        {
            "field": field,
            "comparison": tolerances[field] if field in tolerances else {"mode": "exact"},
        }
        for field in fields
    ]
    result = {
        "gate_id": request["check_id"],
        "category": "fairness",
        "required": request["required"],
        "status": status,
        "reason_codes": reasons,
        "measured_value": {
            "case_a": request["case_a"]["id"],
            "case_b": request["case_b"]["id"],
            "matched_fields": matched_fields,
            "missing_fields": missing_fields,
            "mismatches": mismatches,
        },
        "criterion": gate_criterion(
            description,
            "textual",
            threshold_contract,
            source_ref,
        ),
        "units": None,
        "evidence_refs": require_list(request["evidence_refs"], "fairness request.evidence_refs"),
        "limitations": require_list(request["limitations"], "fairness request.limitations"),
    }
    common_schema, gate_schema = load_contracts()
    validate_gate(result, common_schema, gate_schema, "fairness gate")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", help="JSON or JSON-compatible YAML fairness request")
    parser.add_argument("--output", default="fairness_gate.json", help="Output gate JSON path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        write_json(args.output, evaluate(load_document(args.request)))
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
