#!/usr/bin/env python3
"""Create a read-only relative-path inventory for one declared project root."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from _common import (
    AuditInputError,
    ensure_output_outside_project,
    format_time,
    is_junction,
    load_request,
    matches_any,
    relative_path,
    resolve_project_root,
    sha256_file,
    status_object,
    unique,
    write_inventory,
    write_json,
)


def empty_record(path: str, kind: str) -> dict[str, Any]:
    return {
        "relative_path": path,
        "entry_kind": kind,
        "file_type": "",
        "size_bytes": "",
        "modified_at": "",
        "sha256": "",
        "hash_status": "SKIPPED",
        "scan_status": "PASS",
        "reason_codes": "",
        "link_scope": "",
    }


def link_scope(path: Path, root: Path) -> str:
    if not os.path.exists(path):
        return "broken"
    try:
        path.resolve(strict=True).relative_to(root)
        return "internal-not-followed"
    except ValueError:
        return "external-not-followed"
    except OSError:
        return "unresolved-not-followed"


def mark_record(record: dict[str, Any], status: str, reason: str) -> None:
    existing = record["reason_codes"].split(";") if record["reason_codes"] else []
    record["reason_codes"] = ";".join(unique([*existing, reason]))
    if status == "PARTIAL" or record["scan_status"] == "PARTIAL":
        record["scan_status"] = "PARTIAL"
    elif status == "SKIPPED":
        record["scan_status"] = "SKIPPED"


def build_inventory(request_path: str | Path) -> tuple[list[dict[str, Any]], dict[str, Any], Path]:
    request = load_request(request_path)
    root = resolve_project_root(request_path, request)
    exclusions = request["scan"]["exclude"]
    hash_policy = request["scan"]["hash"]
    records: dict[str, dict[str, Any]] = {}
    pending: list[tuple[Path, str]] = [(root, ".")]

    while pending:
        directory, directory_relative = pending.pop(0)
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name.casefold())
        except PermissionError:
            record = records.setdefault(directory_relative, empty_record(directory_relative, "directory"))
            mark_record(record, "PARTIAL", "PERMISSION_DENIED")
            continue
        except OSError:
            record = records.setdefault(directory_relative, empty_record(directory_relative, "directory"))
            mark_record(record, "PARTIAL", "DIRECTORY_SCAN_FAILED")
            continue

        for entry in entries:
            entry_path = Path(entry.path)
            relative = relative_path(entry_path, root)
            if matches_any(relative, exclusions):
                record = empty_record(relative, "excluded")
                mark_record(record, "SKIPPED", "EXCLUDED_BY_POLICY")
                records[relative] = record
                continue

            junction = is_junction(entry_path)
            try:
                symlink = entry.is_symlink()
            except OSError:
                symlink = False
            if symlink or junction:
                reason = "JUNCTION_BOUNDARY" if junction else "SYMLINK_BOUNDARY"
                record = empty_record(relative, "junction" if junction else "symlink")
                record["link_scope"] = link_scope(entry_path, root)
                if record["link_scope"] == "broken":
                    mark_record(record, "PARTIAL", "BROKEN_LINK")
                mark_record(record, "SKIPPED", reason)
                records[relative] = record
                continue

            try:
                stat = entry.stat(follow_symlinks=False)
                directory_entry = entry.is_dir(follow_symlinks=False)
                file_entry = entry.is_file(follow_symlinks=False)
            except PermissionError:
                record = empty_record(relative, "unknown")
                mark_record(record, "PARTIAL", "PERMISSION_DENIED")
                records[relative] = record
                continue
            except OSError:
                record = empty_record(relative, "unknown")
                mark_record(record, "PARTIAL", "ENTRY_STAT_FAILED")
                records[relative] = record
                continue

            if directory_entry:
                record = empty_record(relative, "directory")
                record["modified_at"] = format_time(stat.st_mtime)
                records[relative] = record
                pending.append((entry_path, relative))
                continue

            if not file_entry:
                record = empty_record(relative, "other")
                mark_record(record, "SKIPPED", "UNSUPPORTED_ENTRY_TYPE")
                records[relative] = record
                continue

            record = empty_record(relative, "file")
            record["file_type"] = entry_path.suffix.lower() or "<none>"
            record["size_bytes"] = str(stat.st_size)
            record["modified_at"] = format_time(stat.st_mtime)
            should_hash = hash_policy["mode"] == "all" or (
                hash_policy["mode"] == "below-size" and stat.st_size <= hash_policy["max_bytes"]
            )
            if should_hash:
                try:
                    record["sha256"] = sha256_file(entry_path)
                    record["hash_status"] = "PASS"
                except PermissionError:
                    record["hash_status"] = "PARTIAL"
                    mark_record(record, "PARTIAL", "PERMISSION_DENIED")
                except OSError:
                    record["hash_status"] = "PARTIAL"
                    mark_record(record, "PARTIAL", "HASH_READ_FAILED")
            records[relative] = record

    rows = [records[key] for key in sorted(records, key=lambda value: value.casefold())]
    incomplete_reasons = unique(
        reason
        for row in rows
        for reason in row["reason_codes"].split(";")
        if reason in {
            "PERMISSION_DENIED",
            "DIRECTORY_SCAN_FAILED",
            "ENTRY_STAT_FAILED",
            "HASH_READ_FAILED",
            "BROKEN_LINK",
            "SYMLINK_BOUNDARY",
            "JUNCTION_BOUNDARY",
        }
    )
    status = status_object("PARTIAL", incomplete_reasons) if incomplete_reasons else status_object("PASS", [])
    summary = {
        "schema_version": "0.1.0",
        "audit_id": request["audit_id"],
        "project_id": request["project"]["project_id"],
        "created_at": request["report"]["created_at"],
        "status": status,
        "counts": {
            "entries": len(rows),
            "files": sum(row["entry_kind"] == "file" for row in rows),
            "directories": sum(row["entry_kind"] == "directory" for row in rows),
            "links_or_junctions": sum(row["entry_kind"] in ("symlink", "junction") for row in rows),
            "partial_or_skipped": sum(row["scan_status"] in ("PARTIAL", "SKIPPED") for row in rows),
        },
        "limitations": [
            "The inventory is relative-path only.",
            "Symlinks and junctions are recorded but never followed.",
            "Hash coverage follows the caller-supplied policy.",
        ],
    }
    return rows, summary, root


def run(request_path: str | Path, output: str | Path, summary_output: str | Path) -> dict[str, Any]:
    rows, summary, root = build_inventory(request_path)
    destination = ensure_output_outside_project(output, root)
    summary_destination = ensure_output_outside_project(summary_output, root)
    write_inventory(destination, rows)
    write_json(summary_destination, summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request")
    parser.add_argument("--output", required=True)
    parser.add_argument("--summary", required=True)
    args = parser.parse_args(argv)
    try:
        run(args.request, args.output, args.summary)
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
