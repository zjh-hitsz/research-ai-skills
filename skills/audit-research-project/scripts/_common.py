#!/usr/bin/env python3
"""Shared standard-library contracts for audit-research-project."""

from __future__ import annotations

import csv
import fnmatch
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


class AuditInputError(ValueError):
    """Raised when an audit request violates the frozen interface."""


SKILL_VERSION = "0.1.0"
SKILL_ROOT = Path(__file__).resolve().parents[1]
LIBRARY_ROOT = SKILL_ROOT.parents[1]
COMMON_STATUS_SCHEMA = LIBRARY_ROOT / "schemas" / "common-status-v1.schema.json"

INVENTORY_COLUMNS = [
    "relative_path",
    "entry_kind",
    "file_type",
    "size_bytes",
    "modified_at",
    "sha256",
    "hash_status",
    "scan_status",
    "reason_codes",
    "link_scope",
]


def load_document(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise AuditInputError(f"Cannot read {source}: {exc}") from exc
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AuditInputError(
            f"{source} is not valid JSON-compatible YAML: line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(value, dict):
        raise AuditInputError(f"{source} must contain one object")
    return value


def write_json(path: str | Path, value: Any) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def write_inventory(path: str | Path, rows: list[dict[str, Any]]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=INVENTORY_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in INVENTORY_COLUMNS})


def read_inventory(path: str | Path) -> list[dict[str, str]]:
    source = Path(path)
    try:
        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != INVENTORY_COLUMNS:
                raise AuditInputError("Inventory columns do not match the v0.1 contract")
            return [dict(row) for row in reader]
    except OSError as exc:
        raise AuditInputError(f"Cannot read inventory {source}: {exc}") from exc


def require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AuditInputError(f"{label} must be an object")
    return value


def require_array(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise AuditInputError(f"{label} must be an array")
    return value


def require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AuditInputError(f"{label} must be a non-empty string")
    return value


def require_keys(value: dict[str, Any], keys: Iterable[str], label: str) -> None:
    missing = [key for key in keys if key not in value]
    if missing:
        raise AuditInputError(f"{label} is missing required fields: {', '.join(missing)}")


def validate_relative_reference(value: Any, label: str) -> str:
    reference = require_string(value, label).replace("\\", "/")
    path = Path(reference)
    if path.is_absolute() or re.match(r"^[A-Za-z]:/", reference) or ".." in path.parts:
        raise AuditInputError(f"{label} must be a safe relative path")
    return reference


def validate_relative_glob(value: Any, label: str) -> str:
    pattern = require_string(value, label).replace("\\", "/")
    if pattern.startswith("/") or re.match(r"^[A-Za-z]:/", pattern):
        raise AuditInputError(f"{label} must be a relative glob")
    if any(part in ("", ".", "..") for part in pattern.split("/")):
        raise AuditInputError(f"{label} must not contain empty, dot, or parent segments")
    return pattern


def validate_folder_name(value: Any, label: str) -> str:
    name = require_string(value, label)
    if name in (".", "..") or "/" in name or "\\" in name:
        raise AuditInputError(f"{label} must be one folder name")
    return name


def validate_unique_strings(values: Any, label: str, validator) -> list[str]:
    items = require_array(values, label)
    normalized = [validator(item, f"{label}[{index}]") for index, item in enumerate(items)]
    if len(normalized) != len(set(value.casefold() for value in normalized)):
        raise AuditInputError(f"{label} must not contain duplicates")
    return normalized


def unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def load_request(path: str | Path) -> dict[str, Any]:
    request = load_document(path)
    require_keys(
        request,
        ("request_version", "audit_id", "project", "scan", "classification", "risk", "report"),
        "project audit request",
    )
    if request["request_version"] != "0.1.0":
        raise AuditInputError("request_version must be 0.1.0")
    require_string(request["audit_id"], "audit_id")
    for key in ("project", "scan", "classification", "risk", "report"):
        require_object(request[key], key)
    require_keys(request["project"], ("project_id", "root", "known_entrypoints", "expected_outputs"), "project")
    require_string(request["project"]["project_id"], "project.project_id")
    require_string(request["project"]["root"], "project.root")
    require_array(request["project"]["known_entrypoints"], "project.known_entrypoints")
    require_array(request["project"]["expected_outputs"], "project.expected_outputs")
    for index, entrypoint in enumerate(request["project"]["known_entrypoints"]):
        validate_relative_reference(entrypoint, f"project.known_entrypoints[{index}]")
    for index, expected in enumerate(request["project"]["expected_outputs"]):
        expected_object = require_object(expected, f"project.expected_outputs[{index}]")
        require_keys(expected_object, ("path", "required", "rationale"), f"project.expected_outputs[{index}]")
        validate_relative_reference(expected_object["path"], f"project.expected_outputs[{index}].path")
        if not isinstance(expected_object["required"], bool):
            raise AuditInputError(f"project.expected_outputs[{index}].required must be boolean")
        require_string(expected_object["rationale"], f"project.expected_outputs[{index}].rationale")
    require_keys(request["scan"], ("follow_links", "exclude", "hash"), "scan")
    if request["scan"]["follow_links"] is not False:
        raise AuditInputError("scan.follow_links must be false in v0.1")
    require_array(request["scan"]["exclude"], "scan.exclude")
    hash_policy = require_object(request["scan"]["hash"], "scan.hash")
    require_keys(hash_policy, ("mode", "max_bytes"), "scan.hash")
    if hash_policy["mode"] not in ("none", "all", "below-size"):
        raise AuditInputError("scan.hash.mode must be none, all, or below-size")
    if not isinstance(hash_policy["max_bytes"], int) or isinstance(hash_policy["max_bytes"], bool) or hash_policy["max_bytes"] < 0:
        raise AuditInputError("scan.hash.max_bytes must be a non-negative integer")
    classification = request["classification"]
    require_keys(
        classification,
        ("cache_directory_names", "source_directory_names", "result_directory_names"),
        "classification",
    )
    for key in ("cache_directory_names", "source_directory_names", "result_directory_names"):
        validate_unique_strings(classification[key], f"classification.{key}", validate_folder_name)
    profile = classification.get("profile")
    if profile is not None:
        profile = require_object(profile, "classification.profile")
        profile_fields = (
            "profile_id",
            "include_rules",
            "exclude_rules",
            "priority_paths",
            "environment_folders",
            "cache_folders",
            "source_hints",
        )
        require_keys(profile, profile_fields, "classification.profile")
        unknown = sorted(set(profile) - set(profile_fields))
        if unknown:
            raise AuditInputError(f"classification.profile has unknown fields: {', '.join(unknown)}")
        require_string(profile["profile_id"], "classification.profile.profile_id")
        for key in ("include_rules", "exclude_rules", "priority_paths", "source_hints"):
            validate_unique_strings(profile[key], f"classification.profile.{key}", validate_relative_glob)
        for key in ("environment_folders", "cache_folders"):
            validate_unique_strings(profile[key], f"classification.profile.{key}", validate_folder_name)
    require_keys(request["risk"], ("large_file_bytes", "text_scan_max_bytes", "text_extensions"), "risk")
    for key in ("large_file_bytes", "text_scan_max_bytes"):
        if not isinstance(request["risk"][key], int) or isinstance(request["risk"][key], bool) or request["risk"][key] < 0:
            raise AuditInputError(f"risk.{key} must be a non-negative integer")
    require_array(request["risk"]["text_extensions"], "risk.text_extensions")
    require_keys(request["report"], ("created_at",), "report")
    require_string(request["report"]["created_at"], "report.created_at")
    return request


def resolve_project_root(request_path: str | Path, request: dict[str, Any]) -> Path:
    raw = request["project"]["root"]
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = Path(request_path).resolve().parent / candidate
    root = candidate.resolve(strict=False)
    if not root.exists() or not root.is_dir():
        raise AuditInputError("project.root must identify an existing directory")
    if root.is_symlink() or is_junction(root):
        raise AuditInputError("project.root cannot itself be a symlink or junction")
    return root


def ensure_output_outside_project(output: str | Path, project_root: Path) -> Path:
    destination = Path(output).resolve(strict=False)
    try:
        destination.relative_to(project_root)
    except ValueError:
        return destination
    raise AuditInputError("Audit outputs must be outside the read-only project root")


def is_junction(path: str | Path) -> bool:
    checker = getattr(Path(path), "is_junction", None)
    if checker is not None:
        try:
            return bool(checker())
        except OSError:
            return False
    checker = getattr(__import__("os").path, "isjunction", None)
    if checker is None:
        return False
    try:
        return bool(checker(path))
    except OSError:
        return False


def relative_path(path: str | Path, root: Path) -> str:
    return Path(path).absolute().relative_to(root.absolute()).as_posix()


def format_time(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def matches_any(path: str, patterns: list[Any]) -> bool:
    return any(isinstance(pattern, str) and fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def status_object(status: str, reason_codes: list[str]) -> dict[str, Any]:
    schema = load_document(COMMON_STATUS_SCHEMA)
    statuses = schema["$defs"]["status"]["enum"]
    pattern = re.compile(schema["$defs"]["reasonCode"]["pattern"])
    reasons = unique(reason_codes)
    if status not in statuses:
        raise AuditInputError(f"Unknown common status: {status}")
    if status == "PASS" and reasons:
        raise AuditInputError("PASS status cannot carry reason codes")
    if status != "PASS" and not reasons:
        raise AuditInputError(f"{status} status requires reason codes")
    if any(not pattern.fullmatch(reason) for reason in reasons):
        raise AuditInputError("A reason code does not match common-status-v1")
    return {"schema_version": "1.0.0", "status": status, "reason_codes": reasons}


def split_reasons(value: str) -> list[str]:
    return [reason for reason in value.split(";") if reason]


def finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))
