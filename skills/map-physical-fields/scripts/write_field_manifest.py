#!/usr/bin/env python3
"""Write a field-manifest-v1 record from completed mapping evidence."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _common import (
    SkillInputError,
    load_request,
    read_report,
    require_array,
    require_keys,
    require_object,
    require_string,
    resolve_request_path,
    sha256_file,
    unique,
    validate_field_manifest_instance,
    validate_gate_report_minimal,
    write_json,
)


def build(request_path: str | Path) -> dict:
    request = load_request(request_path)
    manifest_config = request["manifest"]
    require_keys(
        manifest_config,
        (
            "field_id", "provenance_ref", "validation_report", "normalization_report", "mapping_report", "integral_report",
            "gate_report", "output_format", "limitations", "created_at",
        ),
        "manifest",
    )
    validation_report = read_report(resolve_request_path(request_path, manifest_config["validation_report"], "manifest.validation_report"), "validation report")
    normalization_report = read_report(resolve_request_path(request_path, manifest_config["normalization_report"], "manifest.normalization_report"), "normalization report")
    mapping_report = read_report(resolve_request_path(request_path, manifest_config["mapping_report"], "manifest.mapping_report"), "mapping report")
    integral_report = read_report(resolve_request_path(request_path, manifest_config["integral_report"], "manifest.integral_report"), "integral report")
    gate_report = read_report(resolve_request_path(request_path, manifest_config["gate_report"], "manifest.gate_report"), "gate report")
    validate_gate_report_minimal(gate_report)
    subject = require_object(gate_report["subject"], "gate report.subject")
    if subject.get("id") != request["mapping_id"]:
        raise SkillInputError("gate report subject.id does not match mapping_id")
    stage_reports = [validation_report, normalization_report, mapping_report, integral_report]
    if any(report.get("mapping_id") != request["mapping_id"] for report in stage_reports):
        raise SkillInputError("stage report mapping_id mismatch")
    if any(report.get("status") != "PASS" for report in stage_reports):
        raise SkillInputError("a field manifest may be released only after all four mapping stages PASS")
    gate_ids = {gate.get("gate_id") for gate in gate_report["gates"] if isinstance(gate, dict)}
    expected_gate_ids = {report["gate"]["gate_id"] for report in stage_reports}
    if not expected_gate_ids.issubset(gate_ids):
        raise SkillInputError("gate report does not contain every mapping stage gate")

    source = request["source"]
    target = request["target"]
    mapping = request["mapping"]
    source_path = resolve_request_path(request_path, source.get("table"), "source.table")
    output_path = resolve_request_path(request_path, mapping.get("output_table"), "mapping.output_table")
    output_created = output_path.is_file()
    output_hash = sha256_file(output_path) if output_created else None
    output_ref = mapping["output_table"] if output_created else None
    if output_created and mapping_report.get("output_sha256") != output_hash:
        raise SkillInputError("mapped output hash does not match mapping report")
    if validation_report.get("source_sha256") != sha256_file(source_path):
        raise SkillInputError("source hash does not match validation report")

    overall = require_object(gate_report["overall"], "gate report.overall")
    limitations = unique(
        [
            *require_array(manifest_config["limitations"], "manifest.limitations"),
            *require_array(gate_report.get("limitations", []), "gate report.limitations"),
        ]
    )
    manifest = {
        "schema_version": "1.0.0",
        "field_id": require_string(manifest_config["field_id"], "manifest.field_id"),
        "source_artifact_id": source["artifact_id"],
        "source_hash": validation_report["source_sha256"],
        "source_tool": source["source_tool"],
        "field": target["field"],
        "coordinates": target["coordinates"],
        "grid": target["grid"],
        "target_grid_ref": target["grid_ref"],
        "mapping": {
            "method": mapping_report["method"],
            "parameters": mapping_report["parameters"],
            "outside_domain_policy": mapping["outside_domain_policy"],
            "missing_value_policy": mapping["missing_value_policy"],
        },
        "verification": {
            "source_integral": integral_report["source_integral"],
            "target_integral": integral_report["target_integral"],
            "relative_error": integral_report["relative_error"],
            "coverage": integral_report["coverage"],
            "gate_report_ref": manifest_config["gate_report"],
        },
        "output": {
            "created": output_created,
            "path": output_ref,
            "hash": output_hash,
            "format": require_string(manifest_config["output_format"], "manifest.output_format"),
        },
        "provenance_ref": require_string(manifest_config["provenance_ref"], "manifest.provenance_ref"),
        "status": overall["status"],
        "reason_codes": overall["reason_codes"],
        "limitations": limitations,
        "created_at": require_string(manifest_config["created_at"], "manifest.created_at"),
    }
    validate_field_manifest_instance(manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request")
    parser.add_argument("--output", default="field_manifest.json")
    args = parser.parse_args(argv)
    try:
        write_json(args.output, build(args.request))
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
