#!/usr/bin/env python3
"""Apply bounded heuristic research-asset labels to an inventory."""

from __future__ import annotations

import argparse
import fnmatch
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from _common import (
    AuditInputError,
    ensure_output_outside_project,
    load_request,
    read_inventory,
    resolve_project_root,
    status_object,
    write_json,
)


CONFIG_EXTENSIONS = {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg"}
CODE_EXTENSIONS = {".py", ".m", ".java", ".c", ".cpp", ".h", ".hpp", ".f90", ".sh", ".ps1", ".ipynb"}
REPORT_EXTENSIONS = {".md", ".pdf", ".docx", ".pptx", ".html"}


def matches_profile_rule(path: str, rules: list[Any]) -> bool:
    candidate = path.replace("\\", "/").casefold()
    return any(
        isinstance(rule, str) and fnmatch.fnmatchcase(candidate, rule.replace("\\", "/").casefold())
        for rule in rules
    )


def profile_context(path: str, entry_kind: str, config: dict[str, Any]) -> dict[str, Any]:
    profile = config.get("profile") if isinstance(config.get("profile"), dict) else {}
    parts = {part.casefold() for part in Path(path).parts}
    environment_folders = {str(value).casefold() for value in profile.get("environment_folders", [])}
    cache_folders = {str(value).casefold() for value in profile.get("cache_folders", [])}
    included = matches_profile_rule(path, profile.get("include_rules", []))
    excluded = matches_profile_rule(path, profile.get("exclude_rules", []))
    priority = matches_profile_rule(path, profile.get("priority_paths", []))
    source_hint = matches_profile_rule(path, profile.get("source_hints", []))
    environment = bool(environment_folders.intersection(parts))
    profile_cache = bool(cache_folders.intersection(parts))
    scope = "excluded" if excluded else "included" if included else "neutral"
    role = "environment" if environment else "cache" if profile_cache else "source" if source_hint else None
    primary = entry_kind == "file" and not (excluded or environment or profile_cache) and (included or priority or source_hint)
    basis: list[str] = []
    if included:
        basis.append("profile-include-rule")
    if excluded:
        basis.append("profile-exclude-rule")
    if priority:
        basis.append("profile-priority-path")
    if environment:
        basis.append("profile-environment-folder")
    if profile_cache:
        basis.append("profile-cache-folder")
    if source_hint:
        basis.append("profile-source-hint")
    return {
        "profile_scope": scope,
        "profile_priority": priority,
        "profile_role": role,
        "primary_asset_candidate": primary,
        "profile_basis": basis,
    }


def classify(path: str, entry_kind: str, config: dict[str, Any]) -> tuple[str, str, list[str], dict[str, Any]]:
    parts = [part.casefold() for part in Path(path).parts]
    suffix = Path(path).suffix.casefold()
    cache_names = {str(value).casefold() for value in config.get("cache_directory_names", [])}
    source_names = {str(value).casefold() for value in config.get("source_directory_names", [])}
    result_names = {str(value).casefold() for value in config.get("result_directory_names", [])}
    context = profile_context(path, entry_kind, config)
    if context["profile_role"] == "environment":
        return "cache", "high", ["profile-environment-folder-non-primary"], context
    if context["profile_role"] == "cache" or cache_names.intersection(parts):
        return "cache", "high", ["configured-cache-directory-name", *context["profile_basis"]], context
    if context["profile_scope"] == "excluded":
        return "unknown", "low", ["profile-excluded-from-primary-assets"], context
    if entry_kind != "file":
        return "unknown", "low", ["non-file-entry", *context["profile_basis"]], context
    if context["profile_role"] == "source" or source_names.intersection(parts):
        confidence = "high" if context["profile_role"] == "source" else "medium"
        return "source", confidence, ["profile-source-hint" if context["profile_role"] == "source" else "configured-source-directory-name", *context["profile_basis"]], context
    if result_names.intersection(parts):
        return "result", "medium", ["configured-result-directory-name", *context["profile_basis"]], context
    if suffix in CONFIG_EXTENSIONS or "config" in parts:
        return "config", "medium", ["configuration-extension-or-directory", *context["profile_basis"]], context
    if suffix in CODE_EXTENSIONS or "scripts" in parts or "code" in parts:
        return "code", "medium", ["code-extension-or-directory", *context["profile_basis"]], context
    if suffix in REPORT_EXTENSIONS or Path(path).name.casefold().startswith("readme"):
        return "report", "medium", ["report-extension-or-name", *context["profile_basis"]], context
    confidence = "medium" if context["profile_priority"] else "low"
    return "unknown", confidence, ["no-heuristic-match", *context["profile_basis"]], context


def evaluate(request_path: str | Path, inventory_path: str | Path) -> dict[str, Any]:
    request = load_request(request_path)
    rows = read_inventory(inventory_path)
    config = request["classification"]
    entrypoints = {str(value).replace("\\", "/") for value in request["project"]["known_entrypoints"]}
    items: list[dict[str, Any]] = []
    for row in rows:
        label, confidence, basis, context = classify(row["relative_path"], row["entry_kind"], config)
        declared_entrypoint = row["relative_path"] in entrypoints and row["entry_kind"] == "file"
        items.append(
            {
                "relative_path": row["relative_path"],
                "classification": label,
                "confidence": confidence,
                "basis": basis,
                **context,
                "authority_candidate": declared_entrypoint and label != "cache" and context["profile_scope"] != "excluded",
                "authority_basis": ["declared-entrypoint-present"] if declared_entrypoint and context["profile_scope"] != "excluded" else [],
                "requires_human_decision": declared_entrypoint,
            }
        )
    counts = Counter(item["classification"] for item in items)
    profile = config.get("profile") if isinstance(config.get("profile"), dict) else None
    profile_summary = {
        "applied": profile is not None,
        "profile_id": profile.get("profile_id") if profile else None,
        "included_count": sum(item["profile_scope"] == "included" for item in items),
        "excluded_count": sum(item["profile_scope"] == "excluded" for item in items),
        "priority_count": sum(item["profile_priority"] for item in items),
        "primary_asset_candidate_count": sum(item["primary_asset_candidate"] for item in items),
        "environment_count": sum(item["profile_role"] == "environment" for item in items),
        "cache_count": sum(item["profile_role"] == "cache" for item in items),
        "source_hint_count": sum(item["profile_role"] == "source" for item in items),
    }
    return {
        "schema_version": "0.1.0",
        "audit_id": request["audit_id"],
        "project_id": request["project"]["project_id"],
        "created_at": request["report"]["created_at"],
        "status": status_object("PASS", []),
        "method": "profile-assisted-heuristic-file-and-directory-rules" if profile else "heuristic-file-and-directory-rules",
        "profile": profile_summary,
        "counts": {key: counts.get(key, 0) for key in ("source", "config", "code", "report", "result", "cache", "unknown")},
        "items": items,
        "limitations": [
            "Classifications are heuristic and may be wrong.",
            "Authority candidates are not authoritative versions.",
            "Scientific meaning is not inferred from names or extensions.",
            "Profile rules adjust heuristic priority only; they do not establish authority or scientific importance.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request")
    parser.add_argument("inventory")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        request = load_request(args.request)
        root = resolve_project_root(args.request, request)
        destination = ensure_output_outside_project(args.output, root)
        write_json(destination, evaluate(args.request, args.inventory))
    except AuditInputError as exc:
        print(f"INPUT_ERROR: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"IO_ERROR: {exc}", file=sys.stderr)
        return 3
    print(Path(args.output).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
