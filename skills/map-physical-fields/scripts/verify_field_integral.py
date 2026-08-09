#!/usr/bin/env python3
"""Verify source/target integrals using only a declared criterion."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _common import (
    SkillInputError,
    compare_declared,
    evidence_manifest,
    inspect_table,
    load_request,
    make_gate,
    require_object,
    require_string,
    resolve_request_path,
    sha256_file,
    skill_producer,
    unique,
    validate_coordinates,
    validate_criterion,
    validate_grid,
    write_json,
)


def integral(rows: list[dict[str, float]], value_column: str, grid: dict) -> float:
    cell_area = float(grid["spacing"][0]) * float(grid["spacing"][1])
    return sum(row[value_column] * cell_area for row in rows)


def evaluate(request_path: str | Path) -> dict:
    request = load_request(request_path)
    source = request["source"]
    normalization = request["normalization"]
    target = request["target"]
    mapping = request["mapping"]
    verification = request["verification"]
    criterion = validate_criterion(verification.get("integral_criterion"), "verification.integral_criterion")
    source_grid = validate_grid(normalization.get("output_grid"), "normalization.output_grid")
    target_grid = validate_grid(target.get("grid"), "target.grid")
    axes = list(validate_coordinates(target.get("coordinates"), "target.coordinates")["axis_order"])
    columns = require_object(source.get("columns"), "source.columns")
    value_column = require_string(columns.get("value"), "source.columns.value")
    source_path = resolve_request_path(request_path, normalization.get("output_table"), "normalization.output_table")
    target_path = resolve_request_path(request_path, mapping.get("output_table"), "mapping.output_table")
    source_inspection = inspect_table(source_path, axes, value_column, source_grid)
    target_inspection = inspect_table(target_path, axes, value_column, target_grid, extra_numeric_columns=["coverage"])
    reasons = unique([*source_inspection["reason_codes"], *target_inspection["reason_codes"]])
    source_integral = None
    target_integral = None
    relative_error = None
    coverage = 0.0
    if not reasons:
        source_integral = integral(source_inspection["rows"], value_column, source_grid)
        target_integral = integral(target_inspection["rows"], value_column, target_grid)
        cell_area = float(target_grid["spacing"][0]) * float(target_grid["spacing"][1])
        total_area = len(target_inspection["rows"]) * cell_area
        coverage = sum(row.get("coverage", 0.0) * cell_area for row in target_inspection["rows"]) / total_area
        if source_integral == 0.0:
            if target_integral == 0.0:
                relative_error = 0.0
            else:
                reasons.append("ZERO_REFERENCE_INTEGRAL")
        else:
            relative_error = abs(target_integral - source_integral) / abs(source_integral)
        if relative_error is not None and not compare_declared(relative_error, criterion):
            reasons.append("INTEGRAL_CRITERION_NOT_MET")
    reasons = unique(reasons)
    status = "PASS" if not reasons else ("PARTIAL" if reasons == ["ZERO_REFERENCE_INTEGRAL"] else "FAIL")
    target_hash = sha256_file(target_path)
    evidence = evidence_manifest(
        f"{request['mapping_id']}.mapped-field",
        mapping["output_table"],
        target_hash,
        "text/csv",
        "PASS",
        [],
        skill_producer(request, "field-mapping"),
        require_string(request["gate_report"]["lineage"]["snapshot_at"], "gate_report.lineage.snapshot_at"),
        "field-mapping",
    )
    gate = make_gate(
        f"{request['mapping_id']}.integral-verification",
        "conservation",
        status,
        reasons,
        {
            "source_integral": source_integral,
            "target_integral": target_integral,
            "relative_error": relative_error,
            "coverage": coverage,
        },
        criterion,
        "relative-error",
        [evidence],
        [] if status == "PASS" else ["Integral evidence did not satisfy the supplied criterion."],
    )
    return {
        "report_version": "0.1.0",
        "mapping_id": request["mapping_id"],
        "source_integral": source_integral,
        "target_integral": target_integral,
        "relative_error": relative_error,
        "coverage": coverage,
        "criterion": criterion,
        "status": status,
        "reason_codes": reasons,
        "gate": gate,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request")
    parser.add_argument("--output", default="integral_report.json")
    args = parser.parse_args(argv)
    try:
        write_json(args.output, evaluate(args.request))
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
