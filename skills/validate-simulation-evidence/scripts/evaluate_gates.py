#!/usr/bin/env python3
"""Aggregate configured simulation-evidence gates into gate-report-v1."""

from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path
from typing import Any

from _common import (
    SkillInputError,
    load_contracts,
    load_document,
    unique_preserving_order,
    validate_gate_report,
    validate_gate_request,
    write_json,
)


def aggregate_required_gates(gates: list[dict[str, Any]]) -> dict[str, Any]:
    required = [gate for gate in gates if gate["required"]]
    failed = [gate for gate in required if gate["status"] == "FAIL"]
    partial = [gate for gate in required if gate["status"] == "PARTIAL"]
    skipped = [gate for gate in required if gate["status"] == "SKIPPED"]

    if failed:
        reasons = ["REQUIRED_GATE_FAILED"]
        reasons.extend(code for gate in failed for code in gate["reason_codes"])
        return {"status": "FAIL", "reason_codes": unique_preserving_order(reasons)}

    if partial:
        reasons = ["REQUIRED_GATE_PARTIAL"]
        reasons.extend(code for gate in partial + skipped for code in gate["reason_codes"])
        return {"status": "PARTIAL", "reason_codes": unique_preserving_order(reasons)}

    if skipped:
        reasons = ["ALL_REQUIRED_GATES_SKIPPED" if len(skipped) == len(required) else "REQUIRED_GATE_SKIPPED"]
        reasons.extend(code for gate in skipped for code in gate["reason_codes"])
        status = "SKIPPED" if len(skipped) == len(required) else "PARTIAL"
        return {"status": status, "reason_codes": unique_preserving_order(reasons)}

    return {"status": "PASS", "reason_codes": []}


def evaluate(request: dict[str, Any]) -> dict[str, Any]:
    common_schema, gate_schema = load_contracts()
    validate_gate_request(request, common_schema, gate_schema)
    report = copy.deepcopy(request)
    report["overall"] = aggregate_required_gates(report["gates"])
    validate_gate_report(report, common_schema, gate_schema)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", help="JSON or JSON-compatible YAML validation request")
    parser.add_argument("--output", default="gate_report.json", help="Output JSON path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = evaluate(load_document(args.request))
        write_json(args.output, report)
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
