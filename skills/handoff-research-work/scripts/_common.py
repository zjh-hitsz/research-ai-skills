#!/usr/bin/env python3
"""Shared contract helpers for handoff-research-work."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


class HandoffInputError(ValueError):
    """Raised when a handoff input violates the declared contract."""


LIBRARY_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_ROOT = LIBRARY_ROOT / "schemas"
STABLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,159}$")
SHA256 = re.compile(r"^[A-Fa-f0-9]{64}$")
REASON_CODE = re.compile(r"^[A-Z][A-Z0-9_]*$")
EXTERNAL_REF = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:.+")
WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")


def load_document(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HandoffInputError(f"Cannot read JSON-compatible document: {source.name}") from exc
    if not isinstance(value, dict):
        raise HandoffInputError(f"Document must contain an object: {source.name}")
    return value


def write_json(path: str | Path, value: Any) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def schema(name: str) -> dict[str, Any]:
    return load_document(SCHEMA_ROOT / name)


def allowed_statuses() -> set[str]:
    return set(schema("common-status-v1.schema.json")["$defs"]["status"]["enum"])


def unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def validate_status(status: Any, reason_codes: Any, label: str) -> tuple[str, list[str]]:
    if status not in allowed_statuses():
        raise HandoffInputError(f"{label}.status is not a Foundation status")
    if not isinstance(reason_codes, list) or any(not isinstance(code, str) or not REASON_CODE.fullmatch(code) for code in reason_codes):
        raise HandoffInputError(f"{label}.reason_codes must contain valid reason codes")
    if len(reason_codes) != len(set(reason_codes)):
        raise HandoffInputError(f"{label}.reason_codes must be unique")
    if status == "PASS" and reason_codes:
        raise HandoffInputError(f"{label} PASS cannot contain reason codes")
    if status != "PASS" and not reason_codes:
        raise HandoffInputError(f"{label} non-PASS requires a reason code")
    return status, list(reason_codes)


def validate_status_object(value: Any, label: str) -> tuple[str, list[str]]:
    if not isinstance(value, dict):
        raise HandoffInputError(f"{label} must be an object")
    return validate_status(value.get("status"), value.get("reason_codes"), label)


def status_object(status: str, reason_codes: Iterable[str]) -> dict[str, Any]:
    reasons = unique(reason_codes)
    validate_status(status, reasons, "status")
    return {"status": status, "reason_codes": reasons}


def parse_datetime(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise HandoffInputError(f"{label} must be an ISO 8601 timestamp")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HandoffInputError(f"{label} must be an ISO 8601 timestamp") from exc
    return value


def stable_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not STABLE_ID.fullmatch(value):
        raise HandoffInputError(f"{label} must be a stable ID")
    return value


def sha256_value(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise HandoffInputError(f"{label} must be a SHA-256 value")
    return value.lower()


def portable_relative(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HandoffInputError(f"{label} must be a non-empty relative path")
    if WINDOWS_ABSOLUTE.match(value) or value.startswith(("/", "\\")):
        raise HandoffInputError(f"{label} must not be absolute")
    normalized = value.replace("\\", "/")
    parts = PurePosixPath(normalized).parts
    if any(part in ("", ".", "..") for part in parts):
        raise HandoffInputError(f"{label} must not contain empty, dot, or parent segments")
    return PurePosixPath(*parts).as_posix()


def validate_locator(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) not in ({"relative_path"}, {"external_ref"}):
        raise HandoffInputError(f"{label} must contain exactly one locator kind")
    if "relative_path" in value:
        return {"relative_path": portable_relative(value["relative_path"], f"{label}.relative_path")}
    external = value["external_ref"]
    if not isinstance(external, str) or not EXTERNAL_REF.fullmatch(external):
        raise HandoffInputError(f"{label}.external_ref is invalid")
    return {"external_ref": external}


def resolve_request_path(request_path: str | Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value.strip() or WINDOWS_ABSOLUTE.match(value) or value.startswith(("/", "\\")):
        raise HandoffInputError(f"{label} must be a relative runtime reference")
    return (Path(request_path).resolve().parent / value).resolve()


def safe_join(root: str | Path, relative: str) -> Path:
    base = Path(root).resolve()
    normalized = portable_relative(relative, "artifact locator")
    candidate = (base / Path(*PurePosixPath(normalized).parts)).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise HandoffInputError("Artifact locator escapes the package root") from exc
    return candidate


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def expect_keys(value: Any, required: set[str], allowed: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HandoffInputError(f"{label} must be an object")
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - allowed)
    if missing or unknown:
        raise HandoffInputError(f"{label} fields invalid; missing={missing}, unknown={unknown}")
    return value


def validate_handoff_manifest(manifest: dict[str, Any]) -> None:
    contract = schema("handoff-manifest-v1.schema.json")
    expect_keys(manifest, set(contract["required"]), set(contract["properties"]), "manifest")
    if manifest["schema_version"] != "1.0.0":
        raise HandoffInputError("Unsupported handoff schema version")
    stable_id(manifest["handoff_id"], "handoff_id")
    if manifest["profile"] not in ("minimal", "full"):
        raise HandoffInputError("profile must be minimal or full")
    if not isinstance(manifest["project_audit_ref"], str) or not manifest["project_audit_ref"]:
        raise HandoffInputError("project_audit_ref is required")

    if not isinstance(manifest["inputs"], list) or not manifest["inputs"]:
        raise HandoffInputError("inputs must contain at least one asset")
    if not isinstance(manifest["outputs"], list) or not isinstance(manifest["regressions"], list):
        raise HandoffInputError("outputs and regressions must be arrays")
    if manifest["profile"] == "full" and (not manifest["outputs"] or not manifest["regressions"]):
        raise HandoffInputError("full profile requires an output and a regression")

    artifact_ids: list[str] = []
    readable_ids: set[str] = set()
    for index, item in enumerate(manifest["inputs"]):
        item = expect_keys(item, {"artifact_id", "locator", "hash", "provenance_ref"}, {"artifact_id", "locator", "hash", "provenance_ref"}, f"inputs[{index}]")
        artifact_id = stable_id(item["artifact_id"], f"inputs[{index}].artifact_id")
        artifact_ids.append(artifact_id)
        readable_ids.add(artifact_id)
        validate_locator(item["locator"], f"inputs[{index}].locator")
        sha256_value(item["hash"], f"inputs[{index}].hash")
        if not isinstance(item["provenance_ref"], str) or not item["provenance_ref"]:
            raise HandoffInputError("input provenance_ref is required")

    for index, item in enumerate(manifest["outputs"]):
        required = {"artifact_id", "locator", "status", "reason_codes", "hash", "generation_rule", "provenance_ref"}
        item = expect_keys(item, required, required, f"outputs[{index}]")
        artifact_id = stable_id(item["artifact_id"], f"outputs[{index}].artifact_id")
        artifact_ids.append(artifact_id)
        status, _ = validate_status(item["status"], item["reason_codes"], f"outputs[{index}]")
        if item["locator"] is not None:
            validate_locator(item["locator"], f"outputs[{index}].locator")
            readable_ids.add(artifact_id)
        if item["hash"] is not None:
            sha256_value(item["hash"], f"outputs[{index}].hash")
        if status == "PASS" and (item["locator"] is None or item["hash"] is None):
            raise HandoffInputError("PASS output requires locator and hash")
        if not isinstance(item["generation_rule"], str) or not item["generation_rule"]:
            raise HandoffInputError("output generation_rule is required")
        if not isinstance(item["provenance_ref"], str) or not item["provenance_ref"]:
            raise HandoffInputError("output provenance_ref is required")

    if len(artifact_ids) != len(set(artifact_ids)):
        raise HandoffInputError("artifact IDs must be unique")
    if not isinstance(manifest["read_order"], list) or not manifest["read_order"]:
        raise HandoffInputError("read_order must not be empty")
    for index, item in enumerate(manifest["read_order"]):
        stable_id(item, f"read_order[{index}]")
        if item not in readable_ids:
            raise HandoffInputError("read_order references an unknown or unavailable artifact")
    if len(manifest["read_order"]) != len(set(manifest["read_order"])):
        raise HandoffInputError("read_order must be unique")

    if not isinstance(manifest["entrypoints"], list) or not manifest["entrypoints"]:
        raise HandoffInputError("entrypoints must not be empty")
    entry_ids: list[str] = []
    for index, item in enumerate(manifest["entrypoints"]):
        item = expect_keys(item, {"entrypoint_id", "description", "locator"}, {"entrypoint_id", "description", "locator"}, f"entrypoints[{index}]")
        entry_ids.append(stable_id(item["entrypoint_id"], f"entrypoints[{index}].entrypoint_id"))
        if not isinstance(item["description"], str) or not item["description"]:
            raise HandoffInputError("entrypoint description is required")
        validate_locator(item["locator"], f"entrypoints[{index}].locator")
    if len(entry_ids) != len(set(entry_ids)):
        raise HandoffInputError("entrypoint IDs must be unique")

    environment = expect_keys(manifest["environment"], {"toolchain", "dependencies", "setup_instructions"}, {"toolchain", "dependencies", "setup_instructions"}, "environment")
    if not isinstance(environment["toolchain"], list) or not isinstance(environment["dependencies"], list):
        raise HandoffInputError("environment toolchain and dependencies must be arrays")
    if not isinstance(environment["setup_instructions"], str) or not environment["setup_instructions"]:
        raise HandoffInputError("environment setup_instructions are required")
    for index, tool in enumerate(environment["toolchain"]):
        expect_keys(tool, {"name", "version"}, {"name", "version"}, f"toolchain[{index}]")
    for index, dependency in enumerate(environment["dependencies"]):
        expect_keys(dependency, {"dependency_id", "required", "description"}, {"dependency_id", "required", "description"}, f"dependencies[{index}]")
        stable_id(dependency["dependency_id"], f"dependencies[{index}].dependency_id")

    for index, record in enumerate(manifest["regressions"]):
        required = {"regression_id", "command_record", "exit_code", "expected_ref", "actual_ref", "status", "reason_codes"}
        record = expect_keys(record, required, required, f"regressions[{index}]")
        stable_id(record["regression_id"], f"regressions[{index}].regression_id")
        validate_status(record["status"], record["reason_codes"], f"regressions[{index}]")
        command = expect_keys(record["command_record"], {"entrypoint_ref", "invocation_summary", "authorized", "executed_at"}, {"entrypoint_ref", "invocation_summary", "authorized", "executed_at"}, f"regressions[{index}].command_record")
        entrypoint_ref = stable_id(command["entrypoint_ref"], f"regressions[{index}].entrypoint_ref")
        if entrypoint_ref not in entry_ids:
            raise HandoffInputError("Regression record references an unknown entrypoint")
        if command["executed_at"] is not None:
            parse_datetime(command["executed_at"], f"regressions[{index}].executed_at")

    for name in ("reuse", "do_not_reuse", "limitations"):
        values = manifest[name]
        if not isinstance(values, list) or any(not isinstance(item, str) or not item for item in values) or len(values) != len(set(values)):
            raise HandoffInputError(f"{name} must contain unique non-empty strings")

    if not isinstance(manifest["open_decisions"], list) or not isinstance(manifest["private_dependencies"], list):
        raise HandoffInputError("open_decisions and private_dependencies must be arrays")
    for index, decision in enumerate(manifest["open_decisions"]):
        required = {"decision_id", "question", "status", "owner"}
        decision = expect_keys(decision, required, required, f"open_decisions[{index}]")
        stable_id(decision["decision_id"], f"open_decisions[{index}].decision_id")
        if decision["status"] not in ("open", "resolved", "deferred"):
            raise HandoffInputError("open decision status is invalid")
    for index, dependency in enumerate(manifest["private_dependencies"]):
        required = {"dependency_id", "required", "availability_status", "reason_codes"}
        dependency = expect_keys(dependency, required, required, f"private_dependencies[{index}]")
        stable_id(dependency["dependency_id"], f"private_dependencies[{index}].dependency_id")
        validate_status(dependency["availability_status"], dependency["reason_codes"], f"private_dependencies[{index}]")

    if not isinstance(manifest["next_action"], str) or not manifest["next_action"]:
        raise HandoffInputError("next_action is required")
    verification = expect_keys(manifest["verification"], {"status", "reason_codes", "checked_at"}, {"status", "reason_codes", "checked_at"}, "verification")
    validate_status(verification["status"], verification["reason_codes"], "verification")
    parse_datetime(verification["checked_at"], "verification.checked_at")
    parse_datetime(manifest["created_at"], "created_at")


def merge_readiness(states: Iterable[tuple[str, Iterable[str]]]) -> dict[str, Any]:
    failures: list[str] = []
    partials: list[str] = []
    for status, reasons in states:
        if status == "FAIL":
            failures.extend(reasons)
        elif status in ("PARTIAL", "SKIPPED"):
            partials.extend(reasons)
    if failures:
        return status_object("FAIL", failures)
    if partials:
        return status_object("PARTIAL", partials)
    return status_object("PASS", [])
