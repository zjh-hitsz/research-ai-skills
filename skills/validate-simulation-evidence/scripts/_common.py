#!/usr/bin/env python3
"""Shared, standard-library-only helpers for validate-simulation-evidence."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Iterable


class SkillInputError(ValueError):
    """Raised when a config or manifest violates the declared contract."""


SKILL_ROOT = Path(__file__).resolve().parents[1]
LIBRARY_ROOT = SKILL_ROOT.parents[1]
COMMON_STATUS_SCHEMA = LIBRARY_ROOT / "schemas" / "common-status-v1.schema.json"
GATE_REPORT_SCHEMA = LIBRARY_ROOT / "schemas" / "gate-report-v1.schema.json"
EVIDENCE_MANIFEST_SCHEMA = LIBRARY_ROOT / "schemas" / "evidence-manifest-v1.schema.json"


def load_document(path: str | Path) -> dict[str, Any]:
    """Load JSON or the JSON-compatible subset of YAML used by library templates."""
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillInputError(f"Cannot read input document: {source}: {exc}") from exc
    def reject_constant(constant: str) -> None:
        raise SkillInputError(f"{source} contains a non-finite JSON number: {constant}")

    try:
        value = json.loads(text, parse_constant=reject_constant)
    except json.JSONDecodeError as exc:
        raise SkillInputError(
            f"{source} is not valid JSON-compatible YAML: line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(value, dict):
        raise SkillInputError(f"{source} must contain one object at the document root")
    return value


def write_json(path: str | Path, value: Any) -> None:
    """Write deterministic UTF-8 JSON with sorted keys and a final newline."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    except ValueError as exc:
        raise SkillInputError(f"Output contains a non-finite number: {exc}") from exc
    destination.write_text(payload, encoding="utf-8", newline="\n")


def write_text(path: str | Path, value: str) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(value, encoding="utf-8", newline="\n")


def load_contracts(
    common_status_schema: str | Path | None = None,
    gate_report_schema: str | Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    common = load_document(common_status_schema or COMMON_STATUS_SCHEMA)
    gate = load_document(gate_report_schema or GATE_REPORT_SCHEMA)
    return common, gate


def status_contract(common_schema: dict[str, Any]) -> tuple[tuple[str, ...], re.Pattern[str]]:
    try:
        statuses = tuple(common_schema["$defs"]["status"]["enum"])
        reason_pattern = common_schema["$defs"]["reasonCode"]["pattern"]
    except (KeyError, TypeError) as exc:
        raise SkillInputError("Common status schema does not expose the expected status contract") from exc
    return statuses, re.compile(reason_pattern)


def require_keys(value: dict[str, Any], keys: Iterable[str], label: str) -> None:
    missing = [key for key in keys if key not in value]
    if missing:
        raise SkillInputError(f"{label} is missing required fields: {', '.join(missing)}")


def reject_unknown_keys(value: dict[str, Any], allowed: Iterable[str], label: str) -> None:
    unexpected = sorted(set(value) - set(allowed))
    if unexpected:
        raise SkillInputError(f"{label} contains fields not allowed by gate-report-v1: {', '.join(unexpected)}")


def require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SkillInputError(f"{label} must be a non-empty string")
    return value


def require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise SkillInputError(f"{label} must be an array")
    return value


def allowed_schema_versions(property_schema: dict[str, Any], label: str) -> tuple[str, ...]:
    if "const" in property_schema:
        return (property_schema["const"],)
    versions = property_schema.get("enum")
    if isinstance(versions, list) and versions and all(isinstance(item, str) for item in versions):
        return tuple(versions)
    raise SkillInputError(f"{label} does not expose a const or enum version contract")


def validate_stable_id(value: Any, stable_id_schema: dict[str, Any], label: str) -> str:
    result = require_string(value, label)
    pattern = stable_id_schema.get("pattern")
    if pattern and not re.fullmatch(pattern, result):
        raise SkillInputError(f"{label} is not a valid stable ID")
    if len(result) < stable_id_schema.get("minLength", 0):
        raise SkillInputError(f"{label} is shorter than the stable-ID contract")
    if len(result) > stable_id_schema.get("maxLength", len(result)):
        raise SkillInputError(f"{label} is longer than the stable-ID contract")
    return result


def unique_preserving_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def validate_status_and_reasons(
    status: Any,
    reason_codes: Any,
    common_schema: dict[str, Any],
    label: str,
) -> None:
    statuses, reason_pattern = status_contract(common_schema)
    if status not in statuses:
        raise SkillInputError(f"{label}.status must be one of: {', '.join(statuses)}")
    reasons = require_list(reason_codes, f"{label}.reason_codes")
    if len(reasons) != len(set(reasons)):
        raise SkillInputError(f"{label}.reason_codes must not contain duplicates")
    for code in reasons:
        if not isinstance(code, str) or not reason_pattern.fullmatch(code):
            raise SkillInputError(f"{label}.reason_codes contains an invalid code: {code!r}")
    if status == "PASS" and reasons:
        raise SkillInputError(f"{label}.reason_codes must be empty when status is PASS")
    if status != "PASS" and not reasons:
        raise SkillInputError(f"{label}.reason_codes must contain at least one reason when status is {status}")


def validate_criterion(criterion: Any, criterion_schema: dict[str, Any], label: str) -> None:
    if not isinstance(criterion, dict):
        raise SkillInputError(f"{label} must be an object")
    require_keys(criterion, ("description", "source_ref"), label)
    reject_unknown_keys(criterion, criterion_schema["properties"], label)
    require_string(criterion["description"], f"{label}.description")
    require_string(criterion["source_ref"], f"{label}.source_ref")
    operator = criterion.get("operator")
    allowed_operators = criterion_schema["properties"]["operator"]["enum"]
    if operator not in allowed_operators:
        raise SkillInputError(f"{label}.operator is not allowed by gate-report-v1: {operator!r}")
    threshold = criterion.get("threshold")
    if isinstance(threshold, dict):
        raise SkillInputError(f"{label}.threshold must not be an object")


def validate_evidence_manifest(
    value: Any,
    common_schema: dict[str, Any],
    evidence_schema: dict[str, Any],
    label: str,
) -> str:
    if not isinstance(value, dict):
        raise SkillInputError(f"{label} must be a string reference or structured evidence object")
    require_keys(value, evidence_schema["required"], label)
    reject_unknown_keys(value, evidence_schema["properties"], label)
    versions = allowed_schema_versions(evidence_schema["properties"]["schema_version"], "evidence schema")
    if value["schema_version"] not in versions:
        raise SkillInputError(f"{label}.schema_version must be one of: {', '.join(versions)}")
    artifact_id = validate_stable_id(value["artifact_id"], evidence_schema["$defs"]["stableId"], f"{label}.artifact_id")
    if value.get("ref") is not None:
        require_string(value["ref"], f"{label}.ref")
    sha256 = require_string(value["sha256"], f"{label}.sha256")
    if not re.fullmatch(evidence_schema["properties"]["sha256"]["pattern"], sha256):
        raise SkillInputError(f"{label}.sha256 must contain 64 hexadecimal characters")
    media_type = require_string(value["media_type"], f"{label}.media_type")
    if not re.fullmatch(evidence_schema["properties"]["media_type"]["pattern"], media_type):
        raise SkillInputError(f"{label}.media_type must be a type/subtype media type")
    parse_status = value["parse_status"]
    if not isinstance(parse_status, dict):
        raise SkillInputError(f"{label}.parse_status must be an object")
    require_keys(parse_status, ("status", "reason_codes"), f"{label}.parse_status")
    reject_unknown_keys(parse_status, ("status", "reason_codes"), f"{label}.parse_status")
    validate_status_and_reasons(
        parse_status["status"],
        parse_status["reason_codes"],
        common_schema,
        f"{label}.parse_status",
    )
    if parse_status["status"] == "FAIL" and "EVIDENCE_PARSE_FAILED" not in parse_status["reason_codes"]:
        raise SkillInputError(f"{label}.parse_status FAIL must include EVIDENCE_PARSE_FAILED")
    producer = value["produced_by"]
    producer_schema = evidence_schema["$defs"]["producer"]
    if not isinstance(producer, dict):
        raise SkillInputError(f"{label}.produced_by must be an object")
    require_keys(producer, producer_schema["required"], f"{label}.produced_by")
    reject_unknown_keys(producer, producer_schema["properties"], f"{label}.produced_by")
    require_string(producer["name"], f"{label}.produced_by.name")
    if producer["version"] is not None:
        require_string(producer["version"], f"{label}.produced_by.version")
    if producer.get("run_id") is not None:
        validate_stable_id(producer["run_id"], evidence_schema["$defs"]["stableId"], f"{label}.produced_by.run_id")
    require_string(value["produced_at"], f"{label}.produced_at")
    require_string(value["stage"], f"{label}.stage")
    return artifact_id


def validate_lineage(value: Any, gate_schema: dict[str, Any], label: str = "lineage") -> None:
    if not isinstance(value, dict):
        raise SkillInputError(f"{label} must be an object")
    lineage_schema = gate_schema["$defs"]["reportLineage"]
    require_keys(value, lineage_schema["required"], label)
    reject_unknown_keys(value, lineage_schema["properties"], label)
    stable_id_schema = gate_schema["$defs"]["stableId"]
    validate_stable_id(value["workflow_id"], stable_id_schema, f"{label}.workflow_id")
    validate_stable_id(value["attempt_id"], stable_id_schema, f"{label}.attempt_id")
    require_string(value["stage"], f"{label}.stage")
    require_string(value["snapshot_at"], f"{label}.snapshot_at")
    require_string(value["subject_revision"], f"{label}.subject_revision")
    if value["supersedes_report_id"] is not None:
        validate_stable_id(
            value["supersedes_report_id"],
            stable_id_schema,
            f"{label}.supersedes_report_id",
        )


def validate_gate(gate: Any, common_schema: dict[str, Any], gate_schema: dict[str, Any], label: str) -> None:
    if not isinstance(gate, dict):
        raise SkillInputError(f"{label} must be an object")
    required_fields = gate_schema["$defs"]["gate"]["required"]
    require_keys(gate, required_fields, label)
    reject_unknown_keys(gate, gate_schema["$defs"]["gate"]["properties"], label)
    require_string(gate["gate_id"], f"{label}.gate_id")
    require_string(gate["category"], f"{label}.category")
    if not isinstance(gate["required"], bool):
        raise SkillInputError(f"{label}.required must be boolean")
    validate_status_and_reasons(gate["status"], gate["reason_codes"], common_schema, label)
    validate_criterion(gate["criterion"], gate_schema["$defs"]["criterion"], f"{label}.criterion")
    evidence_refs = require_list(gate["evidence_refs"], f"{label}.evidence_refs")
    evidence_schema: dict[str, Any] | None = None
    artifact_ids: list[str] = []
    for index, ref in enumerate(evidence_refs):
        ref_label = f"{label}.evidence_refs[{index}]"
        if isinstance(ref, str):
            require_string(ref, ref_label)
        else:
            if evidence_schema is None:
                evidence_schema = load_document(EVIDENCE_MANIFEST_SCHEMA)
            artifact_ids.append(validate_evidence_manifest(ref, common_schema, evidence_schema, ref_label))
    if len(artifact_ids) != len(set(artifact_ids)):
        raise SkillInputError(f"{label}.evidence_refs must use unique structured artifact_id values")
    if gate["status"] in ("PASS", "FAIL") and not evidence_refs:
        raise SkillInputError(f"{label}.evidence_refs must not be empty for {gate['status']}")
    limitations = require_list(gate["limitations"], f"{label}.limitations")
    if len(limitations) != len(set(limitations)):
        raise SkillInputError(f"{label}.limitations must not contain duplicates")
    for index, item in enumerate(limitations):
        require_string(item, f"{label}.limitations[{index}]")
    if gate["units"] is not None and not isinstance(gate["units"], str):
        raise SkillInputError(f"{label}.units must be a string or null")


def validate_claim_boundary(
    value: Any,
    gate_ids: Iterable[str],
    gate_schema: dict[str, Any],
    label: str = "claim_boundary",
) -> None:
    if not isinstance(value, dict):
        raise SkillInputError(f"{label} must be an object")
    allowed = gate_schema["$defs"]["claimBoundary"]["properties"]
    reject_unknown_keys(value, allowed, label)
    if "claims" in value:
        legacy = sorted(set(value) & {"supported", "unsupported", "conditional"})
        if legacy:
            raise SkillInputError(f"{label} must not mix claims with legacy fields: {', '.join(legacy)}")
        claims = require_list(value["claims"], f"{label}.claims")
        claim_schema = gate_schema["$defs"]["claimObject"]
        known_gate_ids = set(gate_ids)
        claim_ids: list[str] = []
        for index, claim in enumerate(claims):
            claim_label = f"{label}.claims[{index}]"
            if not isinstance(claim, dict):
                raise SkillInputError(f"{claim_label} must be an object")
            require_keys(claim, claim_schema["required"], claim_label)
            reject_unknown_keys(claim, claim_schema["properties"], claim_label)
            claim_ids.append(validate_stable_id(claim["claim_id"], gate_schema["$defs"]["stableId"], f"{claim_label}.claim_id"))
            require_string(claim["text"], f"{claim_label}.text")
            statuses = claim_schema["properties"]["status"]["enum"]
            if claim["status"] not in statuses:
                raise SkillInputError(f"{claim_label}.status must be one of: {', '.join(statuses)}")
            supporting = require_list(claim["supporting_gate_ids"], f"{claim_label}.supporting_gate_ids")
            if len(supporting) != len(set(supporting)):
                raise SkillInputError(f"{claim_label}.supporting_gate_ids must not contain duplicates")
            for gate_id in supporting:
                require_string(gate_id, f"{claim_label}.supporting_gate_ids")
                if gate_id not in known_gate_ids:
                    raise SkillInputError(f"{claim_label} references unknown gate_id: {gate_id}")
            limitations = require_list(claim["limitations"], f"{claim_label}.limitations")
            if len(limitations) != len(set(limitations)):
                raise SkillInputError(f"{claim_label}.limitations must not contain duplicates")
            for item_index, item in enumerate(limitations):
                require_string(item, f"{claim_label}.limitations[{item_index}]")
        if len(claim_ids) != len(set(claim_ids)):
            raise SkillInputError(f"{label}.claims must use unique claim_id values")
        return
    require_keys(value, ("supported", "unsupported", "conditional"), label)
    for key in ("supported", "unsupported", "conditional"):
        items = require_list(value[key], f"{label}.{key}")
        if len(items) != len(set(items)):
            raise SkillInputError(f"{label}.{key} must not contain duplicates")
        for index, item in enumerate(items):
            require_string(item, f"{label}.{key}[{index}]")


def validate_gate_request(
    request: dict[str, Any],
    common_schema: dict[str, Any],
    gate_schema: dict[str, Any],
) -> None:
    if "overall" in request:
        raise SkillInputError("A validation request must not provide overall; evaluate_gates.py owns aggregation")
    output_required = gate_schema["required"]
    request_required = [field for field in output_required if field != "overall"]
    require_keys(request, request_required, "validation request")
    request_allowed = [field for field in gate_schema["properties"] if field != "overall"]
    reject_unknown_keys(request, request_allowed, "validation request")
    supported_versions = allowed_schema_versions(
        gate_schema["properties"]["schema_version"],
        "gate-report schema",
    )
    if request["schema_version"] not in supported_versions:
        raise SkillInputError(
            "validation request.schema_version must be one of "
            f"{', '.join(supported_versions)}, got {request['schema_version']!r}"
        )
    require_string(request["report_id"], "validation request.report_id")
    if not isinstance(request["producer"], dict):
        raise SkillInputError("validation request.producer must be an object")
    require_keys(request["producer"], ("skill", "skill_version"), "validation request.producer")
    reject_unknown_keys(request["producer"], ("skill", "skill_version"), "validation request.producer")
    require_string(request["producer"]["skill"], "validation request.producer.skill")
    require_string(request["producer"]["skill_version"], "validation request.producer.skill_version")
    if not isinstance(request["subject"], dict):
        raise SkillInputError("validation request.subject must be an object")
    require_keys(request["subject"], ("type", "id", "input_manifest_refs"), "validation request.subject")
    reject_unknown_keys(request["subject"], ("type", "id", "input_manifest_refs"), "validation request.subject")
    require_string(request["subject"]["type"], "validation request.subject.type")
    require_string(request["subject"]["id"], "validation request.subject.id")
    require_list(request["subject"]["input_manifest_refs"], "validation request.subject.input_manifest_refs")
    if "lineage" in request:
        validate_lineage(request["lineage"], gate_schema, "validation request.lineage")
    if not isinstance(request["criteria_source"], dict):
        raise SkillInputError("validation request.criteria_source must be an object")
    require_keys(request["criteria_source"], ("type", "reference"), "validation request.criteria_source")
    reject_unknown_keys(
        request["criteria_source"],
        ("type", "reference", "version", "notes"),
        "validation request.criteria_source",
    )
    require_string(request["criteria_source"]["type"], "validation request.criteria_source.type")
    require_string(request["criteria_source"]["reference"], "validation request.criteria_source.reference")
    gates = require_list(request["gates"], "validation request.gates")
    if not gates:
        raise SkillInputError("validation request.gates must contain at least one gate")
    gate_ids: list[str] = []
    for index, gate in enumerate(gates):
        validate_gate(gate, common_schema, gate_schema, f"validation request.gates[{index}]")
        gate_ids.append(gate["gate_id"])
    if len(gate_ids) != len(set(gate_ids)):
        raise SkillInputError("validation request.gates must use unique gate_id values")
    if not any(gate["required"] for gate in gates):
        raise SkillInputError("validation request must contain at least one required gate")
    validate_claim_boundary(request["claim_boundary"], gate_ids, gate_schema)
    for index, item in enumerate(require_list(request.get("limitations", []), "validation request.limitations")):
        require_string(item, f"validation request.limitations[{index}]")
    require_string(request["created_at"], "validation request.created_at")


def validate_gate_report(
    report: dict[str, Any],
    common_schema: dict[str, Any],
    gate_schema: dict[str, Any],
) -> None:
    require_keys(report, gate_schema["required"], "gate report")
    request_view = dict(report)
    overall = request_view.pop("overall")
    validate_gate_request(request_view, common_schema, gate_schema)
    if not isinstance(overall, dict):
        raise SkillInputError("gate report.overall must be an object")
    require_keys(overall, ("status", "reason_codes"), "gate report.overall")
    validate_status_and_reasons(overall["status"], overall["reason_codes"], common_schema, "gate report.overall")


def compare_scalar(measured: float, operator: str, threshold: Any) -> bool:
    """Apply only the comparison explicitly declared by the input criterion."""
    if not math.isfinite(float(measured)):
        raise SkillInputError("Measured value must be finite")
    if operator == "lt":
        limit = float(threshold)
        if not math.isfinite(limit):
            raise SkillInputError("Criterion threshold must be finite")
        return measured < limit
    if operator == "lte":
        limit = float(threshold)
        if not math.isfinite(limit):
            raise SkillInputError("Criterion threshold must be finite")
        return measured <= limit
    if operator == "eq":
        limit = float(threshold)
        if not math.isfinite(limit):
            raise SkillInputError("Criterion threshold must be finite")
        return measured == limit
    if operator == "gte":
        limit = float(threshold)
        if not math.isfinite(limit):
            raise SkillInputError("Criterion threshold must be finite")
        return measured >= limit
    if operator == "gt":
        limit = float(threshold)
        if not math.isfinite(limit):
            raise SkillInputError("Criterion threshold must be finite")
        return measured > limit
    if operator == "between":
        if not isinstance(threshold, list) or len(threshold) != 2:
            raise SkillInputError("between criterion requires threshold: [minimum, maximum]")
        lower, upper = float(threshold[0]), float(threshold[1])
        if not math.isfinite(lower) or not math.isfinite(upper):
            raise SkillInputError("Criterion thresholds must be finite")
        return lower <= measured <= upper
    raise SkillInputError(f"Unsupported scalar comparison operator: {operator!r}")


def gate_criterion(description: str, operator: str | None, threshold: Any, source_ref: str) -> dict[str, Any]:
    return {
        "description": description,
        "operator": operator,
        "threshold": threshold,
        "source_ref": source_ref,
    }
