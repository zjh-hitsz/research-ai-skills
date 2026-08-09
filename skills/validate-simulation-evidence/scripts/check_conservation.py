#!/usr/bin/env python3
"""Evaluate an explicitly configured scalar conservation criterion."""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Any

from _common import (
    SkillInputError,
    compare_scalar,
    gate_criterion,
    load_contracts,
    load_document,
    require_keys,
    require_list,
    require_string,
    validate_gate,
    write_json,
)


def evaluate(request: dict[str, Any]) -> dict[str, Any]:
    require_keys(
        request,
        (
            "schema_version",
            "check_id",
            "required",
            "source_value",
            "target_value",
            "units",
            "criterion",
            "evidence_refs",
            "limitations",
        ),
        "conservation request",
    )
    if request["schema_version"] != "1.0.0":
        raise SkillInputError("conservation request.schema_version must be 1.0.0")
    require_string(request["check_id"], "conservation request.check_id")
    if not isinstance(request["required"], bool):
        raise SkillInputError("conservation request.required must be boolean")
    if not isinstance(request["source_value"], (int, float)) or isinstance(request["source_value"], bool):
        raise SkillInputError("conservation request.source_value must be numeric")
    if not isinstance(request["target_value"], (int, float)) or isinstance(request["target_value"], bool):
        raise SkillInputError("conservation request.target_value must be numeric")
    if not math.isfinite(float(request["source_value"])) or not math.isfinite(float(request["target_value"])):
        raise SkillInputError("conservation source_value and target_value must be finite")
    require_string(request["units"], "conservation request.units")
    criterion = request["criterion"]
    if not isinstance(criterion, dict):
        raise SkillInputError("conservation request.criterion must be an object")
    require_keys(criterion, ("metric", "operator", "threshold", "description", "source_ref"), "conservation request.criterion")
    metric = criterion["metric"]
    if metric not in ("absolute_error", "relative_error"):
        raise SkillInputError("conservation criterion.metric must be absolute_error or relative_error")
    operator = require_string(criterion["operator"], "conservation request.criterion.operator")
    description = require_string(criterion["description"], "conservation request.criterion.description")
    source_ref = require_string(criterion["source_ref"], "conservation request.criterion.source_ref")
    evidence_refs = require_list(request["evidence_refs"], "conservation request.evidence_refs")
    limitations = require_list(request["limitations"], "conservation request.limitations")

    source = float(request["source_value"])
    target = float(request["target_value"])
    absolute_error = abs(target - source)
    relative_error: float | None
    if source == 0.0:
        relative_error = 0.0 if target == 0.0 else None
    else:
        relative_error = absolute_error / abs(source)

    selected_value = absolute_error if metric == "absolute_error" else relative_error
    if selected_value is None:
        status = "PARTIAL"
        reasons = ["ZERO_REFERENCE_VALUE"]
    else:
        passed = compare_scalar(selected_value, operator, criterion["threshold"])
        status = "PASS" if passed else "FAIL"
        reasons = [] if passed else ["CONSERVATION_CRITERION_FAILED"]

    result = {
        "gate_id": request["check_id"],
        "category": "conservation",
        "required": request["required"],
        "status": status,
        "reason_codes": reasons,
        "measured_value": {
            "source_value": source,
            "target_value": target,
            "absolute_error": absolute_error,
            "relative_error": relative_error,
            "selected_metric": metric,
            "selected_value": selected_value,
        },
        "criterion": gate_criterion(description, operator, criterion["threshold"], source_ref),
        "units": "1" if metric == "relative_error" else request["units"],
        "evidence_refs": evidence_refs,
        "limitations": limitations,
    }
    common_schema, gate_schema = load_contracts()
    validate_gate(result, common_schema, gate_schema, "conservation gate")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", help="JSON or JSON-compatible YAML conservation request")
    parser.add_argument("--output", default="conservation_gate.json", help="Output gate JSON path")
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
