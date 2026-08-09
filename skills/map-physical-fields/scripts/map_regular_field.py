#!/usr/bin/env python3
"""Conservatively map a regular 2D scalar cell field by rectangle overlap."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _common import (
    SkillInputError,
    compare_declared,
    coordinate_grid_index,
    evidence_manifest,
    grid_centers,
    grids_share_domain,
    inspect_table,
    load_request,
    make_gate,
    require_array,
    require_keys,
    require_object,
    require_string,
    resolve_request_path,
    sha256_file,
    skill_producer,
    unique,
    validate_coordinates,
    validate_criterion,
    validate_grid,
    write_field_csv,
    write_json,
)


SUPPORTED_METHOD = "area-overlap-piecewise-constant"


def overlap(left_min: float, left_max: float, right_min: float, right_max: float) -> float:
    return max(0.0, min(left_max, right_max) - max(left_min, right_min))


def evaluate(request_path: str | Path) -> dict:
    request = load_request(request_path)
    source = request["source"]
    normalization = request["normalization"]
    target = request["target"]
    mapping = request["mapping"]
    require_keys(mapping, ("method", "parameters", "outside_domain_policy", "missing_value_policy", "output_table", "coverage_criterion"), "mapping")
    method = require_string(mapping["method"], "mapping.method")
    outside_policy = require_string(mapping["outside_domain_policy"], "mapping.outside_domain_policy")
    missing_policy = require_string(mapping["missing_value_policy"], "mapping.missing_value_policy")
    parameters = require_object(mapping["parameters"], "mapping.parameters")
    criterion = validate_criterion(mapping["coverage_criterion"], "mapping.coverage_criterion")
    source_grid = validate_grid(normalization.get("output_grid"), "normalization.output_grid")
    target_grid = validate_grid(target.get("grid"), "target.grid")
    target_coordinates = validate_coordinates(target.get("coordinates"), "target.coordinates")
    columns = require_object(source.get("columns"), "source.columns")
    value_column = require_string(columns.get("value"), "source.columns.value")
    output_axes = list(target_coordinates["axis_order"])
    source_path = resolve_request_path(request_path, normalization.get("output_table"), "normalization.output_table")
    output_path = resolve_request_path(request_path, mapping["output_table"], "mapping.output_table")

    unsupported: list[str] = []
    if method != SUPPORTED_METHOD:
        unsupported.append("UNSUPPORTED_MAPPING_METHOD")
    if outside_policy != "reject":
        unsupported.append("UNSUPPORTED_OUTSIDE_DOMAIN_POLICY")
    if missing_policy != "reject":
        unsupported.append("UNSUPPORTED_MISSING_VALUE_POLICY")
    if source_grid["type"] != "regular_2d" or target_grid["type"] != "regular_2d":
        unsupported.append("UNSUPPORTED_GRID_TYPE")
    if source_grid["cell_or_node_centered"] != "cell" or target_grid["cell_or_node_centered"] != "cell":
        unsupported.append("UNSUPPORTED_GRID_CENTERING")
    if unsupported:
        gate = make_gate(
            f"{request['mapping_id']}.field-mapping",
            "field-mapping",
            "SKIPPED",
            unique(unsupported),
            None,
            criterion,
            "fraction",
            [normalization["output_table"]],
            ["The requested mapping is outside the frozen v0.1 support range."],
        )
        return {
            "report_version": "0.1.0",
            "mapping_id": request["mapping_id"],
            "method": method,
            "parameters": parameters,
            "coverage": 0.0,
            "status": "SKIPPED",
            "reason_codes": unique(unsupported),
            "output_table": None,
            "output_sha256": None,
            "gate": gate,
        }

    inspection = inspect_table(source_path, output_axes, value_column, source_grid)
    reasons = list(inspection["reason_codes"])
    if "GRID_COORDINATE_MISMATCH" in reasons or "GRID_SHAPE_MISMATCH" in reasons:
        reasons.append("COORDINATE_LOOKUP_FAILED")
    if not grids_share_domain(source_grid, target_grid):
        reasons.append("TARGET_DOMAIN_MISMATCH")
    mapped_rows: list[dict[str, float]] = []
    coverage = 0.0
    if not reasons:
        source_lookup: dict[tuple[int, int], float] = {}
        for row in inspection["rows"]:
            first_index = coordinate_grid_index(row[output_axes[0]], source_grid, 0)
            second_index = coordinate_grid_index(row[output_axes[1]], source_grid, 1)
            if first_index is None or second_index is None or (first_index, second_index) in source_lookup:
                reasons.append("COORDINATE_LOOKUP_FAILED")
                break
            source_lookup[(first_index, second_index)] = row[value_column]
        expected_keys = {
            (first_index, second_index)
            for first_index in range(int(source_grid["shape"][0]))
            for second_index in range(int(source_grid["shape"][1]))
        }
        if set(source_lookup) != expected_keys:
            reasons.append("COORDINATE_LOOKUP_FAILED")

    if not reasons:
        source_centers = grid_centers(source_grid)
        target_centers = grid_centers(target_grid)
        source_dx, source_dy = map(float, source_grid["spacing"])
        target_dx, target_dy = map(float, target_grid["spacing"])
        total_target_area = float(target_grid["shape"][0]) * float(target_grid["shape"][1]) * target_dx * target_dy
        covered_total = 0.0
        for target_y in target_centers[1]:
            for target_x in target_centers[0]:
                target_x_min, target_x_max = target_x - target_dx / 2.0, target_x + target_dx / 2.0
                target_y_min, target_y_max = target_y - target_dy / 2.0, target_y + target_dy / 2.0
                accumulated = 0.0
                covered = 0.0
                for source_y_index, source_y in enumerate(source_centers[1]):
                    y_overlap = overlap(target_y_min, target_y_max, source_y - source_dy / 2.0, source_y + source_dy / 2.0)
                    if y_overlap == 0.0:
                        continue
                    for source_x_index, source_x in enumerate(source_centers[0]):
                        x_overlap = overlap(target_x_min, target_x_max, source_x - source_dx / 2.0, source_x + source_dx / 2.0)
                        area = x_overlap * y_overlap
                        if area == 0.0:
                            continue
                        accumulated += source_lookup[(source_x_index, source_y_index)] * area
                        covered += area
                cell_area = target_dx * target_dy
                cell_coverage = covered / cell_area
                covered_total += covered
                mapped_rows.append(
                    {
                        output_axes[0]: target_x,
                        output_axes[1]: target_y,
                        value_column: accumulated / cell_area,
                        "coverage": cell_coverage,
                    }
                )
        coverage = covered_total / total_target_area
        if not compare_declared(coverage, criterion):
            reasons.append("COVERAGE_CRITERION_NOT_MET")
    reasons = unique(reasons)
    status = "PASS" if not reasons else "FAIL"
    output_hash = None
    evidence_refs: list[object] = [normalization["output_table"]]
    if status == "PASS":
        write_field_csv(output_path, output_axes, value_column, mapped_rows, include_coverage=True)
        output_hash = sha256_file(output_path)
        evidence_refs = [
            evidence_manifest(
                f"{request['mapping_id']}.mapped-field",
                mapping["output_table"],
                output_hash,
                "text/csv",
                "PASS",
                [],
                skill_producer(request, "field-mapping"),
                require_string(request["gate_report"]["lineage"]["snapshot_at"], "gate_report.lineage.snapshot_at"),
                "field-mapping",
            )
        ]
    gate = make_gate(
        f"{request['mapping_id']}.field-mapping",
        "field-mapping",
        status,
        reasons,
        {"method": method, "coverage": coverage, "source_shape": source_grid["shape"], "target_shape": target_grid["shape"]},
        criterion,
        "fraction",
        evidence_refs,
        [] if status == "PASS" else ["No mapped field was released."],
    )
    return {
        "report_version": "0.1.0",
        "mapping_id": request["mapping_id"],
        "method": method,
        "parameters": {
            **parameters,
            "source_shape": source_grid["shape"],
            "target_shape": target_grid["shape"],
            "source_representation": "piecewise-constant-cell-average",
        },
        "coverage": coverage,
        "status": status,
        "reason_codes": reasons,
        "output_table": mapping["output_table"] if status == "PASS" else None,
        "output_sha256": output_hash,
        "gate": gate,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request")
    parser.add_argument("--output", default="mapping_report.json")
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
