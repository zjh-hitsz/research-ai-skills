#!/usr/bin/env python3
"""Build handoff-manifest-v1 from an audit output and explicit asset selection."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Any

from _common import (
    HandoffInputError,
    expect_keys,
    load_document,
    merge_readiness,
    parse_datetime,
    portable_relative,
    resolve_request_path,
    sha256_value,
    stable_id,
    validate_handoff_manifest,
    validate_locator,
    validate_status,
    validate_status_object,
    write_json,
)
from evaluate_regression_record import evaluate_records, normalize_regression_records
from evaluate_scope_review_record import evaluate_scope_review


REQUEST_FIELDS = {
    "request_version",
    "handoff_id",
    "profile",
    "audit",
    "selected_assets",
    "read_order",
    "entrypoints",
    "environment",
    "regressions",
    "reuse",
    "do_not_reuse",
    "open_decisions",
    "private_dependencies",
    "readiness",
    "next_action",
    "limitations",
    "created_at",
}


def load_inventory(path: Path) -> dict[str, dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise HandoffInputError("Cannot read the audit inventory") from exc
    required = {"relative_path", "entry_kind", "sha256", "hash_status", "scan_status", "reason_codes"}
    if not rows:
        raise HandoffInputError("Audit inventory is empty")
    if not required <= set(rows[0]):
        raise HandoffInputError("Audit inventory is missing required columns")
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        if row.get("entry_kind") != "file":
            continue
        relative = portable_relative(row.get("relative_path"), "audit inventory relative_path")
        if relative in result:
            raise HandoffInputError("Audit inventory contains a duplicate relative path")
        result[relative] = row
    return result


def audited_hash(row: dict[str, str], label: str) -> str:
    if row.get("scan_status") != "PASS" or row.get("hash_status") != "PASS":
        raise HandoffInputError(f"{label} is not fully hashed in the audit inventory")
    return sha256_value(row.get("sha256"), f"{label}.sha256")


def selected_hash(selection: dict[str, Any], inventory: dict[str, dict[str, str]], label: str) -> tuple[dict[str, str] | None, str | None]:
    locator_value = selection.get("locator")
    if locator_value is None:
        return None, None
    locator = validate_locator(locator_value, f"{label}.locator")
    if "relative_path" in locator:
        audit_relative = portable_relative(selection.get("audit_relative_path"), f"{label}.audit_relative_path")
        if locator["relative_path"] != audit_relative:
            raise HandoffInputError(f"{label} locator must match audit_relative_path")
        row = inventory.get(audit_relative)
        if row is None:
            raise HandoffInputError(f"{label} was not found in the supplied audit inventory")
        return locator, audited_hash(row, label)
    source_hash = selection.get("source_hash")
    return locator, sha256_value(source_hash, f"{label}.source_hash")


def audit_readiness(
    audit: dict[str, Any],
    scope_review: dict[str, Any] | None,
) -> tuple[str, list[str]]:
    status, reasons = validate_status_object(audit.get("status"), "project audit status")
    if status == "PASS":
        return "PASS", []
    if status == "FAIL":
        return "FAIL", [*reasons, "UPSTREAM_AUDIT_FAILED"]
    if scope_review is not None:
        review_result = evaluate_scope_review(scope_review, audit)
        if review_result["status"] == "PASS":
            return "PASS", []
        if review_result["status"] == "FAIL":
            return "FAIL", review_result["reason_codes"]
        return "PARTIAL", review_result["reason_codes"]
    return "PARTIAL", [*reasons, "UPSTREAM_AUDIT_INCOMPLETE"]


def output_readiness(outputs: list[dict[str, Any]]) -> tuple[str, list[str]]:
    states: list[tuple[str, list[str]]] = []
    for output in outputs:
        status, reasons = validate_status(output["status"], output["reason_codes"], f"output {output['artifact_id']}")
        if status == "FAIL":
            states.append(("FAIL", reasons or ["UPSTREAM_OUTPUT_FAILED"]))
        elif status in ("PARTIAL", "SKIPPED"):
            states.append(("PARTIAL", reasons or ["UPSTREAM_OUTPUT_INCOMPLETE"]))
    merged = merge_readiness(states)
    return merged["status"], merged["reason_codes"]


def dependency_readiness(dependencies: list[dict[str, Any]]) -> tuple[str, list[str]]:
    states: list[tuple[str, list[str]]] = []
    for dependency in dependencies:
        if not dependency["required"]:
            continue
        status, reasons = validate_status(
            dependency["availability_status"],
            dependency["reason_codes"],
            f"private dependency {dependency['dependency_id']}",
        )
        if status == "FAIL":
            states.append(("FAIL", reasons or ["PRIVATE_DEPENDENCY_MISSING"]))
        elif status in ("PARTIAL", "SKIPPED"):
            states.append(("PARTIAL", reasons or ["PRIVATE_DEPENDENCY_UNVERIFIED"]))
    merged = merge_readiness(states)
    return merged["status"], merged["reason_codes"]


def build(request_path: str | Path) -> dict[str, Any]:
    request = load_document(request_path)
    expect_keys(request, REQUEST_FIELDS, REQUEST_FIELDS, "request")
    if request["request_version"] != "0.1.0":
        raise HandoffInputError("Unsupported handoff request version")
    handoff_id = stable_id(request["handoff_id"], "handoff_id")
    if request["profile"] not in ("minimal", "full"):
        raise HandoffInputError("profile must be minimal or full")
    created_at = parse_datetime(request["created_at"], "created_at")

    audit_config = expect_keys(
        request["audit"],
        {"project_audit_path", "inventory_path", "project_audit_ref"},
        {"project_audit_path", "inventory_path", "project_audit_ref", "scope_review_record_path"},
        "audit",
    )
    audit_path = resolve_request_path(request_path, audit_config["project_audit_path"], "audit.project_audit_path")
    inventory_path = resolve_request_path(request_path, audit_config["inventory_path"], "audit.inventory_path")
    audit = load_document(audit_path)
    inventory = load_inventory(inventory_path)
    scope_review = None
    if "scope_review_record_path" in audit_config:
        scope_review_path = resolve_request_path(
            request_path,
            audit_config["scope_review_record_path"],
            "audit.scope_review_record_path",
        )
        scope_review = load_document(scope_review_path)
    if not isinstance(audit_config["project_audit_ref"], str) or not audit_config["project_audit_ref"]:
        raise HandoffInputError("audit.project_audit_ref is required")

    selections = request["selected_assets"]
    if not isinstance(selections, list) or not selections:
        raise HandoffInputError("selected_assets must not be empty")
    inputs: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []
    selected_ids: list[str] = []
    selected_locators: list[dict[str, str]] = []
    for index, selection in enumerate(selections):
        if not isinstance(selection, dict):
            raise HandoffInputError(f"selected_assets[{index}] must be an object")
        role = selection.get("role")
        allowed = {"artifact_id", "role", "audit_relative_path", "locator", "source_hash", "provenance_ref"}
        if role == "output":
            allowed |= {"status", "reason_codes", "generation_rule"}
        unknown = set(selection) - allowed
        if unknown:
            raise HandoffInputError(f"selected_assets[{index}] has unknown fields: {sorted(unknown)}")
        artifact_id = stable_id(selection.get("artifact_id"), f"selected_assets[{index}].artifact_id")
        if role not in ("input", "output"):
            raise HandoffInputError("selected asset role must be input or output")
        provenance_ref = selection.get("provenance_ref")
        if not isinstance(provenance_ref, str) or not provenance_ref:
            raise HandoffInputError("selected asset provenance_ref is required")
        locator, digest = selected_hash(selection, inventory, f"selected_assets[{index}]")
        if locator is not None:
            selected_locators.append(locator)
        if role == "input":
            if locator is None or digest is None:
                raise HandoffInputError("input assets require an audited or external locator and hash")
            inputs.append({"artifact_id": artifact_id, "locator": locator, "hash": digest, "provenance_ref": provenance_ref})
        else:
            status, reasons = validate_status(selection.get("status"), selection.get("reason_codes"), f"selected_assets[{index}]")
            generation_rule = selection.get("generation_rule")
            if not isinstance(generation_rule, str) or not generation_rule:
                raise HandoffInputError("output generation_rule is required")
            if status == "PASS" and (locator is None or digest is None):
                raise HandoffInputError("PASS output requires an audited or external locator and hash")
            outputs.append(
                {
                    "artifact_id": artifact_id,
                    "locator": locator,
                    "status": status,
                    "reason_codes": reasons,
                    "hash": digest,
                    "generation_rule": generation_rule,
                    "provenance_ref": provenance_ref,
                }
            )
        selected_ids.append(artifact_id)
    if len(selected_ids) != len(set(selected_ids)):
        raise HandoffInputError("selected artifact IDs must be unique")

    read_order = request["read_order"]
    if not isinstance(read_order, list) or not read_order or len(read_order) != len(set(read_order)):
        raise HandoffInputError("read_order must be a non-empty unique array")
    for item in read_order:
        stable_id(item, "read_order item")
        if item not in selected_ids:
            raise HandoffInputError("read_order references an unselected artifact")

    entrypoints = request["entrypoints"]
    if not isinstance(entrypoints, list) or not entrypoints:
        raise HandoffInputError("entrypoints must not be empty")
    normalized_entrypoints: list[dict[str, Any]] = []
    for index, item in enumerate(entrypoints):
        item = expect_keys(item, {"entrypoint_id", "description", "locator"}, {"entrypoint_id", "description", "locator"}, f"entrypoints[{index}]")
        normalized = validate_locator(item["locator"], f"entrypoints[{index}].locator")
        if normalized not in selected_locators:
            raise HandoffInputError("Every entrypoint must identify a selected asset")
        normalized_entrypoints.append(
            {
                "entrypoint_id": stable_id(item["entrypoint_id"], f"entrypoints[{index}].entrypoint_id"),
                "description": item["description"],
                "locator": normalized,
            }
        )

    if not isinstance(request["regressions"], list):
        raise HandoffInputError("regressions must be an array")
    normalized_regressions = normalize_regression_records(request["regressions"], set(selected_ids))
    readiness = expect_keys(request["readiness"], {"require_regression"}, {"require_regression"}, "readiness")
    if not isinstance(readiness["require_regression"], bool):
        raise HandoffInputError("readiness.require_regression must be boolean")
    regression_result = evaluate_records(normalized_regressions, readiness["require_regression"])

    if not isinstance(request["private_dependencies"], list):
        raise HandoffInputError("private_dependencies must be an array")
    for index, dependency in enumerate(request["private_dependencies"]):
        required = {"dependency_id", "required", "availability_status", "reason_codes"}
        expect_keys(dependency, required, required, f"private_dependencies[{index}]")
        stable_id(dependency["dependency_id"], f"private_dependencies[{index}].dependency_id")
        if not isinstance(dependency["required"], bool):
            raise HandoffInputError("private dependency required must be boolean")
        validate_status(dependency["availability_status"], dependency["reason_codes"], f"private_dependencies[{index}]")

    states = [
        audit_readiness(audit, scope_review),
        (regression_result["status"], regression_result["reason_codes"]),
        output_readiness(outputs),
        dependency_readiness(request["private_dependencies"]),
    ]
    verification = merge_readiness(states)
    limitations = list(request["limitations"])
    if scope_review is not None:
        review_result = evaluate_scope_review(scope_review, audit)
        audit_status, audit_reasons = validate_status_object(audit.get("status"), "project audit status")
        limitations.append(
            "Human scope review "
            f"{review_result['scope_id']} recorded decision={review_result['decision']} "
            f"for upstream audit status={audit_status} boundaries={','.join(audit_reasons) or 'none'}; "
            "the review does not change the upstream audit record or establish scientific authority."
        )
    manifest = {
        "schema_version": "1.0.0",
        "handoff_id": handoff_id,
        "profile": request["profile"],
        "project_audit_ref": audit_config["project_audit_ref"],
        "read_order": read_order,
        "entrypoints": normalized_entrypoints,
        "environment": request["environment"],
        "inputs": inputs,
        "outputs": outputs,
        "regressions": normalized_regressions,
        "reuse": request["reuse"],
        "do_not_reuse": request["do_not_reuse"],
        "open_decisions": request["open_decisions"],
        "private_dependencies": request["private_dependencies"],
        "next_action": request["next_action"],
        "verification": {**verification, "checked_at": created_at},
        "limitations": limitations,
        "created_at": created_at,
    }
    validate_handoff_manifest(manifest)
    return manifest


def run(request_path: str | Path, output: str | Path) -> dict[str, Any]:
    manifest = build(request_path)
    write_json(output, manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        manifest = run(args.request, args.output)
    except HandoffInputError as exc:
        print(f"INPUT_ERROR: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"IO_ERROR: {exc}", file=sys.stderr)
        return 3
    print(f"{manifest['handoff_id']} {manifest['verification']['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
