#!/usr/bin/env python3
"""Apply only a manifest-declared axis-aligned coordinate transform."""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

from _common import (
    SkillInputError,
    evidence_manifest,
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


def evaluate(request_path: str | Path) -> dict:
    request = load_request(request_path)
    source = request["source"]
    normalization = request["normalization"]
    target = request["target"]
    require_keys(normalization, ("output_table", "transform", "output_grid", "criterion"), "normalization")
    source_coordinates = validate_coordinates(source.get("coordinates"), "source.coordinates")
    target_coordinates = validate_coordinates(target.get("coordinates"), "target.coordinates")
    source_grid = validate_grid(source.get("grid"), "source.grid")
    output_grid = validate_grid(normalization["output_grid"], "normalization.output_grid")
    columns = require_object(source.get("columns"), "source.columns")
    coordinate_columns = require_array(columns.get("coordinates"), "source.columns.coordinates", 2)
    value_column = require_string(columns.get("value"), "source.columns.value")
    criterion = validate_criterion(normalization["criterion"], "normalization.criterion")
    transform = require_object(normalization["transform"], "normalization.transform")
    require_keys(transform, ("permutation", "scale", "offset"), "normalization.transform")
    permutation = require_array(transform["permutation"], "normalization.transform.permutation", 2)
    scale = require_array(transform["scale"], "normalization.transform.scale", 2)
    offset = require_array(transform["offset"], "normalization.transform.offset", 2)
    if sorted(permutation) != [0, 1] or any(not isinstance(item, int) or isinstance(item, bool) for item in permutation):
        raise SkillInputError("normalization.transform.permutation must be [0, 1] or [1, 0]")
    if any(not isinstance(item, (int, float)) or isinstance(item, bool) or not math.isfinite(float(item)) or float(item) == 0 for item in scale):
        raise SkillInputError("normalization.transform.scale must contain finite non-zero values")
    if any(not isinstance(item, (int, float)) or isinstance(item, bool) or not math.isfinite(float(item)) for item in offset):
        raise SkillInputError("normalization.transform.offset must contain finite values")
    if [source_coordinates["axis_order"][index] for index in permutation] != list(target_coordinates["axis_order"]):
        raise SkillInputError("normalization permutation does not map declared source axes to target axes")

    source_path = resolve_request_path(request_path, source.get("table"), "source.table")
    output_path = resolve_request_path(request_path, normalization["output_table"], "normalization.output_table")
    inspection = inspect_table(source_path, list(coordinate_columns), value_column, source_grid)
    reasons = list(inspection["reason_codes"])
    normalized_rows: list[dict[str, float]] = []
    output_axes = list(target_coordinates["axis_order"])
    if not reasons:
        for row in inspection["rows"]:
            source_values = [row[coordinate_columns[0]], row[coordinate_columns[1]]]
            normalized_rows.append(
                {
                    output_axes[0]: float(scale[0]) * source_values[permutation[0]] + float(offset[0]),
                    output_axes[1]: float(scale[1]) * source_values[permutation[1]] + float(offset[1]),
                    value_column: row[value_column],
                }
            )
        temporary = output_path.with_suffix(output_path.suffix + ".checking")
        write_field_csv(temporary, output_axes, value_column, normalized_rows)
        normalized_inspection = inspect_table(temporary, output_axes, value_column, output_grid)
        temporary.unlink(missing_ok=True)
        if normalized_inspection["reason_codes"]:
            reasons.append("COORDINATE_TRANSFORM_MISMATCH")
    reasons = unique(reasons)
    status = "PASS" if not reasons else "FAIL"
    output_hash = None
    evidence_refs = []
    if status == "PASS":
        write_field_csv(output_path, output_axes, value_column, normalized_rows)
        output_hash = sha256_file(output_path)
        evidence_refs.append(
            evidence_manifest(
                f"{request['mapping_id']}.normalized-field",
                normalization["output_table"],
                output_hash,
                "text/csv",
                "PASS",
                [],
                skill_producer(request, "coordinate-normalization"),
                require_string(request["gate_report"]["lineage"]["snapshot_at"], "gate_report.lineage.snapshot_at"),
                "coordinate-normalization",
            )
        )
    else:
        evidence_refs.append(source["table"])
    gate = make_gate(
        f"{request['mapping_id']}.coordinate-normalization",
        "field-coordinate-normalization",
        status,
        reasons,
        {"transform": transform, "output_sha256": output_hash},
        criterion,
        target_coordinates["units"],
        evidence_refs,
        [] if status == "PASS" else ["No normalized field was released."],
    )
    return {
        "report_version": "0.1.0",
        "mapping_id": request["mapping_id"],
        "status": status,
        "reason_codes": reasons,
        "transform": transform,
        "output_table": normalization["output_table"] if status == "PASS" else None,
        "output_sha256": output_hash,
        "gate": gate,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request")
    parser.add_argument("--output", default="coordinate_normalization.json")
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
