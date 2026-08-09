#!/usr/bin/env python3
"""Validate a manifest-declared regular 2D scalar-field CSV table."""

from __future__ import annotations

import argparse
import re
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
    unsupported_status,
    unique,
    validate_coordinates,
    validate_criterion,
    validate_field_declaration,
    validate_grid,
    write_json,
)


def evaluate(request_path: str | Path) -> dict:
    request = load_request(request_path)
    source = request["source"]
    target = request["target"]
    require_keys(
        source,
        (
            "artifact_id", "table", "expected_sha256", "media_type", "produced_by", "produced_at", "stage",
            "source_tool", "columns", "field", "coordinates", "grid", "validation_criterion",
        ),
        "source",
    )
    columns = require_object(source["columns"], "source.columns")
    require_keys(columns, ("coordinates", "value"), "source.columns")
    coordinate_columns = require_array(columns["coordinates"], "source.columns.coordinates", 2)
    if any(not isinstance(item, str) or not item for item in coordinate_columns):
        raise SkillInputError("source.columns.coordinates must contain two names")
    value_column = require_string(columns["value"], "source.columns.value")
    source_field = validate_field_declaration(source["field"], "source.field")
    target_field = validate_field_declaration(target.get("field"), "target.field")
    source_coordinates = validate_coordinates(source["coordinates"], "source.coordinates")
    target_coordinates = validate_coordinates(target.get("coordinates"), "target.coordinates")
    source_grid = validate_grid(source["grid"], "source.grid")
    target_grid = validate_grid(target.get("grid"), "target.grid")
    criterion = validate_criterion(source["validation_criterion"], "source.validation_criterion")
    source_path = resolve_request_path(request_path, source["table"], "source.table")
    actual_hash = sha256_file(source_path)
    expected_hash = require_string(source["expected_sha256"], "source.expected_sha256")
    if not re.fullmatch(r"[A-Fa-f0-9]{64}", expected_hash):
        raise SkillInputError("source.expected_sha256 must contain 64 hexadecimal characters")

    reasons: list[str] = []
    unsupported: list[str] = []
    if source_grid["type"] != "regular_2d" or target_grid["type"] != "regular_2d":
        unsupported.append("UNSUPPORTED_GRID_TYPE")
    if source_grid["cell_or_node_centered"] != "cell" or target_grid["cell_or_node_centered"] != "cell":
        unsupported.append("UNSUPPORTED_GRID_CENTERING")
    if source_coordinates["system"] != "cartesian" or target_coordinates["system"] != "cartesian":
        unsupported.append("UNSUPPORTED_COORDINATE_SYSTEM")
    if len(source_field["components"]) != 1 or len(target_field["components"]) != 1:
        unsupported.append("UNSUPPORTED_FIELD_RANK")
    if actual_hash.lower() != expected_hash.lower():
        reasons.append("SOURCE_HASH_MISMATCH")
    if list(coordinate_columns) != list(source_coordinates["axis_order"]):
        reasons.append("AXIS_ORDER_MISMATCH")
    if source_field["units"] != target_field["units"]:
        reasons.append("FIELD_UNIT_MISMATCH")
    if source_field["sign_convention"] != target_field["sign_convention"]:
        reasons.append("SIGN_CONVENTION_MISMATCH")
    if source_field["components"][0] != value_column:
        reasons.append("FIELD_COMPONENT_COLUMN_MISMATCH")

    inspection = inspect_table(source_path, list(coordinate_columns), value_column, source_grid)
    reasons.extend(inspection["reason_codes"])
    all_reasons = unique([*unsupported, *reasons])
    status = "PASS" if not all_reasons else (unsupported_status(all_reasons) if not reasons else "FAIL")
    parse_status = "FAIL" if inspection["parse_failed"] else "PASS"
    parse_reasons = ["EVIDENCE_PARSE_FAILED"] if inspection["parse_failed"] else []
    evidence = evidence_manifest(
        require_string(source["artifact_id"], "source.artifact_id"),
        source["table"],
        actual_hash,
        require_string(source["media_type"], "source.media_type"),
        parse_status,
        parse_reasons,
        require_object(source["produced_by"], "source.produced_by"),
        require_string(source["produced_at"], "source.produced_at"),
        require_string(source["stage"], "source.stage"),
    )
    gate = make_gate(
        f"{request['mapping_id']}.input-identity",
        "input-identity",
        status,
        all_reasons,
        {
            "actual_sha256": actual_hash,
            "expected_sha256": expected_hash,
            "row_count": len(inspection["rows"]),
            "observed_shape": inspection["observed_shape"],
        },
        criterion,
        None,
        [evidence],
        [] if status == "PASS" else ["The source field must not proceed to mapping while this gate is non-PASS."],
    )
    return {
        "report_version": "0.1.0",
        "mapping_id": request["mapping_id"],
        "source_artifact_id": source["artifact_id"],
        "source_sha256": actual_hash,
        "headers": inspection["headers"],
        "row_count": len(inspection["rows"]),
        "observed_shape": inspection["observed_shape"],
        "status": status,
        "reason_codes": all_reasons,
        "gate": gate,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", help="JSON or JSON-compatible YAML request")
    parser.add_argument("--output", default="field_table_validation.json")
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
