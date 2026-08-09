#!/usr/bin/env python3
"""Verify only manifest-listed handoff files and readiness evidence; write nothing."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from _common import (
    HandoffInputError,
    load_document,
    merge_readiness,
    safe_join,
    sha256_file,
    validate_handoff_manifest,
)
from evaluate_regression_record import evaluate_records


def verify_artifact(
    artifact: dict[str, Any],
    package_root: Path,
    label: str,
) -> tuple[dict[str, Any], tuple[str, list[str]]]:
    locator = artifact["locator"]
    if "external_ref" in locator:
        result = {
            "artifact_id": artifact["artifact_id"],
            "locator_type": "external_ref",
            "status": "PARTIAL",
            "reason_codes": ["EXTERNAL_ARTIFACT_NOT_RESOLVED"],
        }
        return result, (result["status"], result["reason_codes"])
    path = safe_join(package_root, locator["relative_path"])
    if not path.is_file():
        result = {
            "artifact_id": artifact["artifact_id"],
            "locator_type": "relative_path",
            "status": "FAIL",
            "reason_codes": ["MISSING_REQUIRED_FILE"],
        }
        return result, (result["status"], result["reason_codes"])
    actual = sha256_file(path)
    if actual.lower() != artifact["hash"].lower():
        result = {
            "artifact_id": artifact["artifact_id"],
            "locator_type": "relative_path",
            "status": "FAIL",
            "reason_codes": ["HASH_MISMATCH"],
        }
        return result, (result["status"], result["reason_codes"])
    result = {
        "artifact_id": artifact["artifact_id"],
        "locator_type": "relative_path",
        "status": "PASS",
        "reason_codes": [],
    }
    return result, (result["status"], result["reason_codes"])


def verify(manifest_path: str | Path, package_root: str | Path) -> dict[str, Any]:
    manifest = load_document(manifest_path)
    validate_handoff_manifest(manifest)
    root = Path(package_root).resolve()
    states: list[tuple[str, list[str]]] = []
    checks: list[dict[str, Any]] = []

    if not root.is_dir():
        states.append(("FAIL", ["MISSING_PACKAGE_ROOT"]))
    else:
        for artifact in manifest["inputs"]:
            check, state = verify_artifact(artifact, root, "input")
            checks.append(check)
            states.append(state)
        for artifact in manifest["outputs"]:
            if artifact["status"] != "PASS":
                continue
            check, state = verify_artifact(artifact, root, "output")
            checks.append(check)
            states.append(state)
        for entrypoint in manifest["entrypoints"]:
            locator = entrypoint["locator"]
            if "external_ref" in locator:
                states.append(("PARTIAL", ["EXTERNAL_ENTRYPOINT_NOT_RESOLVED"]))
                continue
            path = safe_join(root, locator["relative_path"])
            if not path.is_file():
                states.append(("FAIL", ["MISSING_ENTRYPOINT"]))

    upstream_verification = manifest["verification"]
    states.append((upstream_verification["status"], upstream_verification["reason_codes"]))
    regression = evaluate_records(manifest["regressions"], manifest["profile"] == "full")
    states.append((regression["status"], regression["reason_codes"]))
    for dependency in manifest["private_dependencies"]:
        if not dependency["required"]:
            continue
        status = dependency["availability_status"]
        reasons = dependency["reason_codes"]
        if status == "FAIL":
            states.append(("FAIL", reasons or ["PRIVATE_DEPENDENCY_MISSING"]))
        elif status in ("PARTIAL", "SKIPPED"):
            states.append(("PARTIAL", reasons or ["PRIVATE_DEPENDENCY_UNVERIFIED"]))

    merged = merge_readiness(states)
    return {
        "schema_version": "1.0.0",
        "handoff_id": manifest["handoff_id"],
        "status": merged["status"],
        "reason_codes": merged["reason_codes"],
        "checked_at": manifest["verification"]["checked_at"],
        "artifact_checks": checks,
        "regression_evaluation": regression,
        "command_executed_by_skill": False,
        "package_modified_by_verifier": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest")
    parser.add_argument("--package-root", required=True)
    args = parser.parse_args(argv)
    try:
        result = verify(args.manifest, args.package_root)
    except HandoffInputError as exc:
        print(f"INPUT_ERROR: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"IO_ERROR: {exc}", file=sys.stderr)
        return 3
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
