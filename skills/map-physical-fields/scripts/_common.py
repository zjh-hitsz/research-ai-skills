#!/usr/bin/env python3
"""Standard-library helpers for map-physical-fields v0.1."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable


class SkillInputError(ValueError):
    """Raised when a request violates the declared interface."""


SKILL_VERSION = "0.1.0"
SKILL_ROOT = Path(__file__).resolve().parents[1]
LIBRARY_ROOT = SKILL_ROOT.parents[1]
COMMON_STATUS_SCHEMA = LIBRARY_ROOT / "schemas" / "common-status-v1.schema.json"
FIELD_MANIFEST_SCHEMA = LIBRARY_ROOT / "schemas" / "field-manifest-v1.schema.json"
GATE_REPORT_SCHEMA = LIBRARY_ROOT / "schemas" / "gate-report-v1.schema.json"


def load_document(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillInputError(f"Cannot read input document {source}: {exc}") from exc

    def reject_constant(value: str) -> None:
        raise SkillInputError(f"{source} contains a non-finite JSON number: {value}")

    try:
        document = json.loads(text, parse_constant=reject_constant)
    except json.JSONDecodeError as exc:
        raise SkillInputError(
            f"{source} is not valid JSON-compatible YAML: line {exc.lineno}, "
            f"column {exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(document, dict):
        raise SkillInputError(f"{source} must contain one object at the document root")
    return document


def write_json(path: str | Path, value: Any) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    except ValueError as exc:
        raise SkillInputError(f"Output contains a non-finite number: {exc}") from exc
    destination.write_text(payload, encoding="utf-8", newline="\n")


def write_field_csv(path: str | Path, coordinate_columns: list[str], value_column: str, rows: list[dict[str, float]], include_coverage: bool = False) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [*coordinate_columns, value_column]
    if include_coverage:
        fieldnames.append("coverage")
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({name: format_float(row[name]) for name in fieldnames})


def format_float(value: float) -> str:
    if not math.isfinite(value):
        raise SkillInputError("Cannot serialize a non-finite field value")
    return format(value, ".17g")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve_request_path(request_path: str | Path, raw_path: Any, label: str) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise SkillInputError(f"{label} must be a non-empty relative path")
    candidate = Path(raw_path)
    if candidate.is_absolute() or re.match(r"^[A-Za-z]:[\\/]", raw_path) or raw_path.startswith(("/", "\\")):
        raise SkillInputError(f"{label} must be relative to the request file")
    base = Path(request_path).resolve().parent
    resolved = (base / candidate).resolve()
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise SkillInputError(f"{label} escapes the request directory") from exc
    return resolved


def require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SkillInputError(f"{label} must be an object")
    return value


def require_array(value: Any, label: str, length: int | None = None) -> list[Any]:
    if not isinstance(value, list):
        raise SkillInputError(f"{label} must be an array")
    if length is not None and len(value) != length:
        raise SkillInputError(f"{label} must contain exactly {length} items")
    return value


def require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SkillInputError(f"{label} must be a non-empty string")
    return value


def require_keys(value: dict[str, Any], keys: Iterable[str], label: str) -> None:
    missing = [key for key in keys if key not in value]
    if missing:
        raise SkillInputError(f"{label} is missing required fields: {', '.join(missing)}")


def reject_unknown(value: dict[str, Any], allowed: Iterable[str], label: str) -> None:
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise SkillInputError(f"{label} contains unknown fields: {', '.join(unknown)}")


def unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result


def load_status_contract() -> tuple[tuple[str, ...], re.Pattern[str]]:
    schema = load_document(COMMON_STATUS_SCHEMA)
    try:
        statuses = tuple(schema["$defs"]["status"]["enum"])
        pattern = re.compile(schema["$defs"]["reasonCode"]["pattern"])
    except (KeyError, TypeError, re.error) as exc:
        raise SkillInputError("common-status-v1 does not expose the expected contract") from exc
    return statuses, pattern


def validate_status(status: str, reasons: list[str], label: str) -> None:
    statuses, pattern = load_status_contract()
    if status not in statuses:
        raise SkillInputError(f"{label}.status is not in common-status-v1")
    if len(reasons) != len(set(reasons)):
        raise SkillInputError(f"{label}.reason_codes contains duplicates")
    for reason in reasons:
        if not isinstance(reason, str) or not pattern.fullmatch(reason):
            raise SkillInputError(f"{label}.reason_codes contains invalid code {reason!r}")
    if status == "PASS" and reasons:
        raise SkillInputError(f"{label}.reason_codes must be empty for PASS")
    if status != "PASS" and not reasons:
        raise SkillInputError(f"{label}.reason_codes must not be empty for {status}")


def load_request(path: str | Path) -> dict[str, Any]:
    request = load_document(path)
    require_keys(
        request,
        (
            "request_version",
            "mapping_id",
            "source",
            "normalization",
            "target",
            "mapping",
            "verification",
            "gate_report",
            "manifest",
        ),
        "field mapping request",
    )
    if request["request_version"] != "0.1.0":
        raise SkillInputError("field mapping request.request_version must be 0.1.0")
    require_string(request["mapping_id"], "field mapping request.mapping_id")
    for key in ("source", "normalization", "target", "mapping", "verification", "gate_report", "manifest"):
        require_object(request[key], f"field mapping request.{key}")
    return request


def validate_field_declaration(field: Any, label: str) -> dict[str, Any]:
    value = require_object(field, label)
    fields = ("name", "physical_role", "components", "units", "sign_convention")
    require_keys(value, fields, label)
    reject_unknown(value, fields, label)
    for key in ("name", "physical_role", "units", "sign_convention"):
        require_string(value[key], f"{label}.{key}")
    components = require_array(value["components"], f"{label}.components")
    if len(components) != 1 or not isinstance(components[0], str) or not components[0]:
        raise SkillInputError(f"{label}.components must declare exactly one scalar component")
    return value


def validate_coordinates(coordinates: Any, label: str) -> dict[str, Any]:
    value = require_object(coordinates, label)
    fields = ("system", "dimensions", "axis_order", "origin", "direction", "units")
    require_keys(value, fields, label)
    reject_unknown(value, fields, label)
    require_string(value["system"], f"{label}.system")
    if value["dimensions"] != 2:
        raise SkillInputError(f"{label}.dimensions must be 2")
    axes = require_array(value["axis_order"], f"{label}.axis_order", 2)
    if any(not isinstance(axis, str) or not axis for axis in axes) or len(set(axes)) != 2:
        raise SkillInputError(f"{label}.axis_order must contain two unique names")
    for key in ("origin", "direction"):
        require_array(value[key], f"{label}.{key}", 2)
    if any(not finite_number(item) for item in value["origin"]):
        raise SkillInputError(f"{label}.origin must contain finite numbers")
    if any(item not in (-1, 1) for item in value["direction"]):
        raise SkillInputError(f"{label}.direction must contain only -1 or 1")
    require_string(value["units"], f"{label}.units")
    return value


def validate_grid(grid: Any, label: str) -> dict[str, Any]:
    value = require_object(grid, label)
    fields = ("type", "shape", "spacing", "domain", "cell_or_node_centered")
    require_keys(value, fields, label)
    reject_unknown(value, fields, label)
    shape = require_array(value["shape"], f"{label}.shape", 2)
    spacing = require_array(value["spacing"], f"{label}.spacing", 2)
    if any(not isinstance(item, int) or isinstance(item, bool) or item < 1 for item in shape):
        raise SkillInputError(f"{label}.shape must contain positive integers")
    if any(not finite_number(item) or float(item) <= 0 for item in spacing):
        raise SkillInputError(f"{label}.spacing must contain positive finite numbers")
    domain = require_object(value["domain"], f"{label}.domain")
    require_keys(domain, ("minimum", "maximum"), f"{label}.domain")
    reject_unknown(domain, ("minimum", "maximum"), f"{label}.domain")
    minimum = require_array(domain["minimum"], f"{label}.domain.minimum", 2)
    maximum = require_array(domain["maximum"], f"{label}.domain.maximum", 2)
    if any(not finite_number(item) for item in [*minimum, *maximum]):
        raise SkillInputError(f"{label}.domain must contain finite numbers")
    for axis in range(2):
        if float(maximum[axis]) <= float(minimum[axis]):
            raise SkillInputError(f"{label}.domain maximum must exceed minimum")
        expected = int(shape[axis]) * float(spacing[axis])
        actual = float(maximum[axis]) - float(minimum[axis])
        if not close(actual, expected):
            raise SkillInputError(f"{label} domain extent must equal shape times spacing")
    return value


def finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def close(first: float, second: float, scale: float = 1.0) -> bool:
    tolerance = 1e-10 * max(scale, abs(first), abs(second), 1.0)
    return abs(first - second) <= tolerance


def grid_centers(grid: dict[str, Any]) -> tuple[list[float], list[float]]:
    minimum = grid["domain"]["minimum"]
    spacing = grid["spacing"]
    shape = grid["shape"]
    axes: list[list[float]] = []
    for axis in range(2):
        axes.append([float(minimum[axis]) + (index + 0.5) * float(spacing[axis]) for index in range(int(shape[axis]))])
    return axes[0], axes[1]


def coordinate_grid_index(coordinate: float, grid: dict[str, Any], axis: int) -> int | None:
    """Return the canonical integer cell index for an equivalent grid center."""
    minimum = float(grid["domain"]["minimum"][axis])
    spacing = float(grid["spacing"][axis])
    shape = int(grid["shape"][axis])
    raw_index = (float(coordinate) - minimum) / spacing - 0.5
    nearest = int(round(raw_index))
    if nearest < 0 or nearest >= shape:
        return None
    expected = minimum + (nearest + 0.5) * spacing
    return nearest if close(float(coordinate), expected) else None


def inspect_table(
    path: Path,
    coordinate_columns: list[str],
    value_column: str,
    grid: dict[str, Any],
    extra_numeric_columns: list[str] | None = None,
) -> dict[str, Any]:
    reasons: list[str] = []
    rows: list[dict[str, float]] = []
    try:
        handle = path.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise SkillInputError(f"Cannot read field table {path}: {exc}") from exc
    with handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
        required_columns = [*coordinate_columns, value_column, *(extra_numeric_columns or [])]
        if any(column not in headers for column in required_columns):
            return {
                "headers": headers,
                "rows": [],
                "reason_codes": ["MISSING_COLUMN"],
                "parse_failed": True,
                "observed_shape": [0, 0],
            }
        seen: set[tuple[float, float]] = set()
        for record in reader:
            parsed: dict[str, float] = {}
            row_failed = False
            for column in required_columns:
                raw = record.get(column)
                if raw is None or not raw.strip():
                    reasons.append("MISSING_VALUE")
                    row_failed = True
                    continue
                try:
                    number = float(raw)
                except ValueError:
                    reasons.append("NONNUMERIC_VALUE")
                    row_failed = True
                    continue
                if not math.isfinite(number):
                    reasons.append("NONFINITE_VALUE")
                    row_failed = True
                    continue
                parsed[column] = number
            if row_failed:
                continue
            key = (parsed[coordinate_columns[0]], parsed[coordinate_columns[1]])
            if key in seen:
                reasons.append("DUPLICATE_COORDINATE")
            seen.add(key)
            rows.append(parsed)

    unique_first = sorted({row[coordinate_columns[0]] for row in rows})
    unique_second = sorted({row[coordinate_columns[1]] for row in rows})
    observed_shape = [len(unique_first), len(unique_second)]
    parse_reasons = {"MISSING_COLUMN", "MISSING_VALUE", "NONNUMERIC_VALUE", "NONFINITE_VALUE"}
    parse_failed = any(reason in parse_reasons for reason in reasons)
    if not parse_failed:
        expected_count = int(grid["shape"][0]) * int(grid["shape"][1])
        canonical_indices: set[tuple[int, int]] = set()
        for row in rows:
            first_index = coordinate_grid_index(row[coordinate_columns[0]], grid, 0)
            second_index = coordinate_grid_index(row[coordinate_columns[1]], grid, 1)
            if first_index is None or second_index is None:
                reasons.append("GRID_COORDINATE_MISMATCH")
                continue
            canonical_key = (first_index, second_index)
            if canonical_key in canonical_indices:
                reasons.append("DUPLICATE_COORDINATE")
            canonical_indices.add(canonical_key)
        if len(rows) != expected_count or len(canonical_indices) != expected_count:
            reasons.append("GRID_SHAPE_MISMATCH")
    return {
        "headers": headers,
        "rows": rows,
        "reason_codes": unique(reasons),
        "parse_failed": parse_failed,
        "observed_shape": observed_shape,
    }


def same_axis(observed: list[float], expected: list[float]) -> bool:
    return len(observed) == len(expected) and all(close(first, second) for first, second in zip(observed, expected))


def validate_criterion(value: Any, label: str) -> dict[str, Any]:
    criterion = require_object(value, label)
    require_keys(criterion, ("description", "operator", "threshold", "source_ref"), label)
    require_string(criterion["description"], f"{label}.description")
    require_string(criterion["source_ref"], f"{label}.source_ref")
    allowed = ("lt", "lte", "eq", "gte", "gt", "between", "set-membership", "boolean", "textual", None)
    if criterion["operator"] not in allowed:
        raise SkillInputError(f"{label}.operator is not supported by gate-report-v1")
    return {
        "description": criterion["description"],
        "operator": criterion["operator"],
        "threshold": criterion["threshold"],
        "source_ref": criterion["source_ref"],
    }


def compare_declared(measured: float, criterion: dict[str, Any]) -> bool:
    operator = criterion["operator"]
    threshold = criterion["threshold"]
    if not math.isfinite(measured):
        raise SkillInputError("Measured value must be finite")
    if operator in ("lt", "lte", "eq", "gte", "gt"):
        if not finite_number(threshold):
            raise SkillInputError("A scalar criterion requires a finite numeric threshold")
        limit = float(threshold)
        return {
            "lt": measured < limit,
            "lte": measured <= limit,
            "eq": measured == limit,
            "gte": measured >= limit,
            "gt": measured > limit,
        }[operator]
    if operator == "between":
        limits = require_array(threshold, "criterion.threshold", 2)
        if any(not finite_number(item) for item in limits):
            raise SkillInputError("between criterion thresholds must be finite")
        return float(limits[0]) <= measured <= float(limits[1])
    raise SkillInputError("Numeric verification requires lt/lte/eq/gte/gt/between")


def evidence_manifest(
    artifact_id: str,
    ref: str,
    sha256: str,
    media_type: str,
    parse_status: str,
    parse_reasons: list[str],
    produced_by: dict[str, Any],
    produced_at: str,
    stage: str,
) -> dict[str, Any]:
    validate_status(parse_status, parse_reasons, "evidence.parse_status")
    return {
        "schema_version": "1.0.0",
        "artifact_id": artifact_id,
        "ref": ref,
        "sha256": sha256,
        "media_type": media_type,
        "parse_status": {"status": parse_status, "reason_codes": parse_reasons},
        "produced_by": produced_by,
        "produced_at": produced_at,
        "stage": stage,
    }


def skill_producer(request: dict[str, Any], stage: str) -> dict[str, Any]:
    lineage = require_object(request["gate_report"].get("lineage"), "gate_report.lineage")
    return {"name": "map-physical-fields", "version": SKILL_VERSION, "run_id": lineage.get("attempt_id") or stage}


def make_gate(
    gate_id: str,
    category: str,
    status: str,
    reason_codes: list[str],
    measured_value: Any,
    criterion: dict[str, Any],
    units: str | None,
    evidence_refs: list[Any],
    limitations: list[str],
) -> dict[str, Any]:
    reasons = unique(reason_codes)
    validate_status(status, reasons, "gate")
    return {
        "gate_id": gate_id,
        "category": category,
        "required": True,
        "status": status,
        "reason_codes": reasons,
        "measured_value": measured_value,
        "criterion": criterion,
        "units": units,
        "evidence_refs": evidence_refs,
        "limitations": unique(limitations),
    }


def unsupported_status(reasons: list[str]) -> str:
    return "SKIPPED" if reasons and all(reason.startswith("UNSUPPORTED_") for reason in reasons) else "FAIL"


def grids_share_domain(first: dict[str, Any], second: dict[str, Any]) -> bool:
    for key in ("minimum", "maximum"):
        for left, right in zip(first["domain"][key], second["domain"][key]):
            if not close(float(left), float(right)):
                return False
    return True


def read_report(path: Path, label: str) -> dict[str, Any]:
    try:
        return load_document(path)
    except SkillInputError as exc:
        raise SkillInputError(f"Cannot load {label}: {exc}") from exc


def validate_gate_report_minimal(report: dict[str, Any]) -> None:
    schema = load_document(GATE_REPORT_SCHEMA)
    require_keys(report, schema["required"], "gate report")
    versions = schema["properties"]["schema_version"]["enum"]
    if report["schema_version"] not in versions:
        raise SkillInputError("gate report schema_version is not compatible")
    gates = require_array(report["gates"], "gate report.gates")
    if not gates:
        raise SkillInputError("gate report.gates must not be empty")
    overall = require_object(report["overall"], "gate report.overall")
    require_keys(overall, ("status", "reason_codes"), "gate report.overall")
    validate_status(overall["status"], require_array(overall["reason_codes"], "gate report.overall.reason_codes"), "gate report.overall")


def validate_field_manifest_instance(manifest: dict[str, Any]) -> None:
    schema = load_document(FIELD_MANIFEST_SCHEMA)
    require_keys(manifest, schema["required"], "field manifest")
    reject_unknown(manifest, schema["properties"], "field manifest")
    if manifest["schema_version"] != schema["properties"]["schema_version"]["const"]:
        raise SkillInputError("field manifest schema_version is not compatible")
    stable_schema = schema["$defs"]["stableId"]
    stable_pattern = re.compile(stable_schema["pattern"])
    for label in ("field_id", "source_artifact_id"):
        value = require_string(manifest[label], f"field manifest.{label}")
        if not stable_pattern.fullmatch(value):
            raise SkillInputError(f"field manifest.{label} is not a stable ID")
    sha_pattern = re.compile(schema["$defs"]["sha256"]["pattern"])
    if not sha_pattern.fullmatch(require_string(manifest["source_hash"], "field manifest.source_hash")):
        raise SkillInputError("field manifest.source_hash is not SHA-256")
    source_tool = require_object(manifest["source_tool"], "field manifest.source_tool")
    source_tool_fields = ("name", "version", "export_method")
    require_keys(source_tool, source_tool_fields, "field manifest.source_tool")
    reject_unknown(source_tool, source_tool_fields, "field manifest.source_tool")
    for key in source_tool_fields:
        require_string(source_tool[key], f"field manifest.source_tool.{key}")
    validate_field_declaration(manifest["field"], "field manifest.field")
    validate_coordinates(manifest["coordinates"], "field manifest.coordinates")
    validate_grid(manifest["grid"], "field manifest.grid")
    if manifest["grid"]["type"] != "regular_2d":
        raise SkillInputError("field manifest.grid.type must be regular_2d")
    mapping = require_object(manifest["mapping"], "field manifest.mapping")
    mapping_fields = ("method", "parameters", "outside_domain_policy", "missing_value_policy")
    require_keys(mapping, mapping_fields, "field manifest.mapping")
    reject_unknown(mapping, mapping_fields, "field manifest.mapping")
    require_string(mapping["method"], "field manifest.mapping.method")
    require_object(mapping["parameters"], "field manifest.mapping.parameters")
    if mapping["outside_domain_policy"] not in ("reject", "fill", "clip", "extrapolate"):
        raise SkillInputError("field manifest.mapping.outside_domain_policy is invalid")
    if mapping["missing_value_policy"] not in ("reject", "fill", "ignore-with-coverage-penalty"):
        raise SkillInputError("field manifest.mapping.missing_value_policy is invalid")
    verification = require_object(manifest["verification"], "field manifest.verification")
    verification_fields = ("source_integral", "target_integral", "relative_error", "coverage", "gate_report_ref")
    require_keys(verification, verification_fields, "field manifest.verification")
    reject_unknown(verification, verification_fields, "field manifest.verification")
    for key in ("source_integral", "target_integral"):
        if verification[key] is not None and not finite_number(verification[key]):
            raise SkillInputError(f"field manifest.verification.{key} must be finite or null")
    if verification["relative_error"] is not None and (
        not finite_number(verification["relative_error"]) or float(verification["relative_error"]) < 0
    ):
        raise SkillInputError("field manifest.verification.relative_error must be non-negative or null")
    if not finite_number(verification["coverage"]) or not 0 <= float(verification["coverage"]) <= 1:
        raise SkillInputError("field manifest.verification.coverage must be within [0, 1]")
    require_string(verification["gate_report_ref"], "field manifest.verification.gate_report_ref")
    output = require_object(manifest["output"], "field manifest.output")
    output_fields = ("created", "path", "hash", "format")
    require_keys(output, output_fields, "field manifest.output")
    reject_unknown(output, output_fields, "field manifest.output")
    if not isinstance(output["created"], bool):
        raise SkillInputError("field manifest.output.created must be boolean")
    require_string(output["format"], "field manifest.output.format")
    if output["created"]:
        output_path = require_string(output["path"], "field manifest.output.path")
        if Path(output_path).is_absolute() or re.match(r"^[A-Za-z]:[\\/]", output_path):
            raise SkillInputError("field manifest.output.path must be relative")
        if not sha_pattern.fullmatch(require_string(output["hash"], "field manifest.output.hash")):
            raise SkillInputError("field manifest.output.hash is not SHA-256")
    elif output["path"] is not None or output["hash"] is not None:
        raise SkillInputError("uncreated field manifest output must use null path and hash")
    reasons = require_array(manifest["reason_codes"], "field manifest.reason_codes")
    validate_status(manifest["status"], reasons, "field manifest")
    if manifest["status"] == "PASS" and not output["created"]:
        raise SkillInputError("PASS field manifest must declare a created output")
    require_string(manifest["target_grid_ref"], "field manifest.target_grid_ref")
    require_string(manifest["provenance_ref"], "field manifest.provenance_ref")
    limitations = require_array(manifest["limitations"], "field manifest.limitations")
    if len(limitations) != len(set(limitations)) or any(not isinstance(item, str) or not item for item in limitations):
        raise SkillInputError("field manifest.limitations must contain unique non-empty strings")
    require_string(manifest["created_at"], "field manifest.created_at")
