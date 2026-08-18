#!/usr/bin/env python3
"""Fail-closed, public-safe validation for the reusable Skill repository."""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

import jsonschema
import yaml


FORBIDDEN_SUFFIXES = {
    ".db", ".sqlite", ".sqlite3", ".mph", ".cas", ".dat", ".msh",
    ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx",
}
FORBIDDEN_NAMES = {".env", "id_rsa", "id_ed25519"}
PRIVATE_PATTERNS = {
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "windows-user-path": re.compile(r"(?i)\b[A-Z]:[\\/]Users[\\/][^\\/\s]+"),
    "literal-local-drive-path": re.compile(r"(?i)\b(?:C|D|E|F):[\\/](?![\\/])"),
    "openai-key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "github-token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
}


class ValidationFailure(RuntimeError):
    pass


def _tracked_files(root: Path) -> list[Path]:
    excluded = {".git", ".venv", "__pycache__", ".pytest_cache"}
    return sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file() and not excluded.intersection(path.relative_to(root).parts)
        ),
        key=lambda path: path.relative_to(root).as_posix(),
    )


def _validate_registry(root: Path) -> dict:
    registry_path = root / "registry" / "skill-registry.yaml"
    schema_path = root / "schemas" / "skill-registry-v1.schema.json"
    registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(registry, schema)

    names = [item["name"] for item in registry["skills"]]
    if len(names) != len(set(names)):
        raise ValidationFailure("Skill registry contains duplicate names.")
    directories = sorted(
        path.name for path in (root / "skills").iterdir() if path.is_dir()
    )
    if sorted(names) != directories:
        raise ValidationFailure(
            f"Registry/directory mismatch: registry={sorted(names)!r}, directories={directories!r}"
        )
    graph = {item["name"]: tuple(item["dependencies"]) for item in registry["skills"]}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visiting:
            raise ValidationFailure("Skill dependency graph contains a cycle.")
        if name in visited:
            return
        visiting.add(name)
        for dependency in graph[name]:
            if dependency not in graph:
                raise ValidationFailure(f"Unknown dependency {dependency!r} for {name!r}.")
            visit(dependency)
        visiting.remove(name)
        visited.add(name)

    for name in graph:
        visit(name)
    return registry


def _validate_sources(root: Path, files: list[Path]) -> None:
    for path in files:
        relative = path.relative_to(root)
        if path.name.casefold() in FORBIDDEN_NAMES or path.suffix.casefold() in FORBIDDEN_SUFFIXES:
            raise ValidationFailure(f"Forbidden public artifact: {relative.as_posix()}")
        if path.suffix == ".py":
            ast.parse(path.read_text(encoding="utf-8"), filename=str(relative))
        if path.suffix == ".json":
            json.loads(path.read_text(encoding="utf-8"))
        if path.suffix.casefold() in {".png", ".jpg", ".jpeg", ".gif", ".zip"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationFailure(f"Unexpected binary file: {relative.as_posix()}") from exc
        for label, pattern in PRIVATE_PATTERNS.items():
            if pattern.search(text):
                raise ValidationFailure(
                    f"Privacy pattern {label!r} matched {relative.as_posix()}."
                )


def validate(root: Path) -> dict[str, object]:
    root = root.resolve()
    registry = _validate_registry(root)
    files = _tracked_files(root)
    _validate_sources(root, files)
    return {
        "status": "PASS",
        "registry_version": registry["registry_version"],
        "repository_version": registry["repository_version"],
        "skill_count": len(registry["skills"]),
        "file_count": len(files),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        result = validate(args.root)
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
