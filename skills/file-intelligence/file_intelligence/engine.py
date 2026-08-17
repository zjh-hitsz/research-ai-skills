from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import tempfile
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import __version__
from .classification import aggregate_kind, volatile_class
from .dashboard import build_dashboard
from .database import SCHEMA_VERSION, connect_current, inspect_schema, migrate_to_current, migration_plan, rollback_migration
from .fingerprints import full_sha256, staged_sample_fingerprint
from .paths import path_key as canonical_path_key
from .timeline import (
    build_timeline_page,
    context_summary as build_context_summary,
    correlate_identities,
    create_snapshot,
    file_history as query_file_history,
    initialize_identities,
    materialize_semantic_changes,
    project_history as query_project_history,
    query_timeline,
    record_maintenance_events,
    retention_plan,
    schedule_plan,
    storage_growth as query_storage_growth,
    sync_file_cards,
    update_project_activity,
)
from .understanding import (
    asset_details as query_asset_details,
    list_assertions as query_assertions,
    remove_assertion as deactivate_assertion,
    set_assertion as store_assertion,
    understand_project as build_project_understanding,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
CATALOG_NAME = "catalog.db"
BASELINE_NAME = "baseline.json"
CHANGES_NAME = "last_changes.json"
EXCLUDED_DIRS = {
    ".git", "$recycle.bin", "system volume information",
}
GENERIC_CONTAINERS = {
    "desktop", "downloads", "documents", "research", "projects", "project",
    "data", "results", "output", "figures", "docs", "scripts", "src", "tests",
}
COMPOUND_EXTENSIONS = (".cas.h5", ".dat.h5", ".msh.h5", ".tar.gz", ".scdocx")
HIGH_VALUE_EXTENSIONS = {
    ".md", ".txt", ".pdf", ".docx", ".pptx", ".xlsx", ".py", ".ipynb",
    ".m", ".ps1", ".json", ".yaml", ".yml", ".cas", ".cas.h5", ".dat",
    ".dat.h5", ".msh", ".msh.h5", ".mph", ".scdoc", ".scdocx", ".step",
    ".stp", ".stl",
}


class FileIntelligenceError(RuntimeError):
    """Expected user-facing error."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_state_dir() -> Path:
    override = os.environ.get("FILE_INTELLIGENCE_STATE_DIR")
    if override:
        return Path(override).expanduser()
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "FileIntelligence"
    return Path.home() / ".local" / "share" / "FileIntelligence"


def resolve_state_dir(value: str | os.PathLike[str] | None = None) -> Path:
    state_dir = Path(value).expanduser() if value else default_state_dir()
    state_dir = state_dir.resolve()
    try:
        state_dir.relative_to(PACKAGE_ROOT.resolve())
    except ValueError:
        return state_dir
    raise FileIntelligenceError("Local state must be outside the installed Skill directory.")


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def path_key(path: str | Path) -> str:
    return canonical_path_key(path)


def extension_for(name: str) -> str:
    lower = name.casefold()
    for extension in COMPOUND_EXTENSIONS:
        if lower.endswith(extension):
            return extension
    return Path(name).suffix.casefold()


def legacy_machine_binding() -> str:
    material = "|".join((platform.node(), platform.system(), platform.machine()))
    return hashlib.sha256(material.encode("utf-8", errors="replace")).hexdigest()


def _windows_machine_guid() -> str | None:
    if platform.system() != "Windows":
        return None
    try:
        import winreg

        access = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0, access) as key:
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
        return str(value).strip() or None
    except (ImportError, OSError):
        return None


def machine_binding() -> str:
    machine_guid = _windows_machine_guid()
    if not machine_guid:
        return legacy_machine_binding()
    material = "|".join(("windows-machine-guid-v2", machine_guid, platform.system(), platform.machine()))
    return hashlib.sha256(material.encode("utf-8", errors="replace")).hexdigest()


def machine_binding_matches(stored: object) -> bool:
    value = str(stored or "")
    return value in {machine_binding(), legacy_machine_binding()}


def quick_fingerprint(path: Path, size: int, sample_bytes: int = 65536) -> str | None:
    """Compatibility wrapper for the v2 Stage-1 sample fingerprint."""
    result = staged_sample_fingerprint(path, size, sample_bytes=sample_bytes)
    return result["value"] if result else None


def _legacy_quick_fingerprint(path: Path, size: int, sample_bytes: int = 65536) -> str | None:
    try:
        digest = hashlib.sha256()
        digest.update(str(size).encode("ascii"))
        with path.open("rb") as handle:
            digest.update(handle.read(sample_bytes))
            if size > sample_bytes:
                handle.seek(max(sample_bytes, size - sample_bytes))
                digest.update(handle.read(sample_bytes))
        return digest.hexdigest()
    except (OSError, PermissionError):
        return None


def _is_below(path: Path, parent: Path) -> bool:
    try:
        candidate = os.path.normcase(os.path.abspath(str(path))).casefold()
        boundary = os.path.normcase(os.path.abspath(str(parent))).casefold()
        return os.path.commonpath((candidate, boundary)) == boundary
    except (OSError, ValueError):
        return False


def _candidate_everything_paths(
    explicit: str | Path | None = None,
    state_dir: str | Path | None = None,
) -> Iterable[Path]:
    if explicit:
        yield Path(explicit).expanduser()
    configured = os.environ.get("FILE_INTELLIGENCE_EVERYTHING_CLI")
    if configured:
        yield Path(configured).expanduser()
    for command in ("es.exe", "es"):
        located = shutil.which(command)
        if located:
            yield Path(located)
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(variable)
        if root:
            yield Path(root) / "Everything" / "es.exe"
    tool_root = Path(state_dir).resolve() / "tools" / "Everything-ES" if state_dir else default_state_dir() / "tools" / "Everything-ES"
    if tool_root.is_dir():
        yield from sorted(tool_root.glob("*/es.exe"), reverse=True)


def _everything_registry_install() -> Path | None:
    try:
        import winreg
    except ImportError:
        return None
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for key_name in (r"SOFTWARE\voidtools\Everything", r"SOFTWARE\WOW6432Node\voidtools\Everything"):
            try:
                with winreg.OpenKey(hive, key_name) as key:
                    value, _ = winreg.QueryValueEx(key, "InstallLocation")
                    if value:
                        return Path(str(value))
            except OSError:
                continue
    return None


def find_everything_cli(
    explicit: str | Path | None = None,
    state_dir: str | Path | None = None,
) -> Path | None:
    seen: set[str] = set()
    for candidate in _candidate_everything_paths(explicit, state_dir):
        marker = path_key(candidate)
        if marker in seen:
            continue
        seen.add(marker)
        if candidate.is_file():
            return candidate.resolve()
    return None


def everything_status(
    explicit: str | Path | None = None,
    state_dir: str | Path | None = None,
) -> dict[str, Any]:
    cli = find_everything_cli(explicit, state_dir)
    service_install = False
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(variable)
        if root and (Path(root) / "Everything" / "Everything.exe").is_file():
            service_install = True
            break
    registry_install = _everything_registry_install()
    if registry_install and (registry_install / "Everything.exe").is_file():
        service_install = True
    version = None
    query_ready = False
    if cli:
        try:
            completed = subprocess.run(
                [str(cli), "-version"], capture_output=True, timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
            )
            version = completed.stdout.decode("utf-8", errors="replace").strip() or None
            query_ready = completed.returncode == 0
        except (OSError, subprocess.SubprocessError):
            pass
    return {
        "cli_found": bool(cli),
        "cli_path": str(cli) if cli else None,
        "cli_version": version,
        "query_ready": query_ready,
        "everything_install_detected": service_install,
        "recommended": None if cli else "Install or expose the optional Everything ES.exe CLI bridge for indexed scans.",
    }


def _scope_payload(roots: Iterable[str | Path], communication_roots: Iterable[str | Path]) -> list[dict[str, str]]:
    scopes: list[dict[str, str]] = []
    seen: set[str] = set()
    for kind, values in (("primary", roots), ("communication", communication_roots)):
        for value in values:
            root = Path(value).expanduser().resolve()
            if not root.is_dir():
                raise FileIntelligenceError(f"Scan root is not an accessible directory: {root}")
            marker = path_key(root)
            if marker in seen:
                if kind == "communication":
                    for item in scopes:
                        if path_key(item["path"]) == marker:
                            item["kind"] = "primary+communication"
                continue
            seen.add(marker)
            scopes.append({"path": str(root), "kind": kind})
    if not scopes:
        raise FileIntelligenceError("At least one scan root is required.")
    return scopes


def _record(
    path: Path,
    stat_size: int,
    modified: str,
    created: str,
    scope: dict[str, str],
    *,
    canonical_input: bool = False,
    native_file_id: str | None = None,
) -> dict[str, Any]:
    root = Path(scope["path"])
    try:
        relative = str(path.relative_to(root))
    except ValueError:
        relative = path.name
    is_volatile, volatile_category, _ = volatile_class(path)
    return {
        "path_key": os.path.normcase(os.path.abspath(str(path))).casefold() if canonical_input else path_key(path),
        "path": str(path),
        "root_path": str(root),
        "relative_path": relative,
        "filename": path.name,
        "extension": extension_for(path.name),
        "size": int(stat_size),
        "modified": str(modified),
        "created": str(created),
        "scope_kind": scope["kind"],
        "is_communication": "communication" in scope["kind"],
        "volatile_class": volatile_category if is_volatile else None,
        "status": "present",
        "file_id": None,
        "native_file_id": native_file_id,
        "identity_confidence": None,
        "identity_evidence_json": None,
    }


def _is_reparse_point(path: Path) -> bool:
    try:
        stat = path.stat(follow_symlinks=False)
    except OSError:
        return True
    attributes = getattr(stat, "st_file_attributes", 0)
    reparse_flag = getattr(__import__("stat"), "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attributes & reparse_flag)


def _aggregate_tree(path: Path, kind: str) -> tuple[dict[str, Any], list[dict[str, str]]]:
    total_size = 0
    file_count = 0
    errors: list[dict[str, str]] = []
    for folder, directories, files in os.walk(path, followlinks=False, onerror=lambda exc: errors.append({"path": str(path), "error": str(exc)})):
        folder_path = Path(folder)
        directories[:] = [name for name in directories if not _is_reparse_point(folder_path / name)]
        for name in files:
            candidate = folder_path / name
            if _is_reparse_point(candidate):
                continue
            try:
                total_size += candidate.stat().st_size
                file_count += 1
            except OSError as exc:
                errors.append({"path": str(candidate), "error": str(exc)})
    return {
        "aggregate_id": "aggregate_" + hashlib.sha256(path_key(path).encode("utf-8")).hexdigest()[:20],
        "path": str(path), "kind": kind, "total_size": total_size, "file_count": file_count,
        "internal_indexing": "skipped", "rebuildability": "likely", "confidence": 0.82,
        "evidence": [{"type": "structural_inference", "detail": f"Directory name maps to aggregate kind {kind}."}],
    }, errors


def _scan_filesystem(scopes: list[dict[str, str]], state_dir: Path) -> dict[str, Any]:
    records: dict[str, dict[str, Any]] = {}
    aggregates: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, str]] = []
    successful: list[str] = []
    excluded_roots = (state_dir.resolve(), PACKAGE_ROOT.resolve())
    for scope in scopes:
        root = Path(scope["path"])
        successful.append(scope["path"])
        def on_error(exc: OSError) -> None:
            errors.append({"path": str(getattr(exc, "filename", root)), "error": str(exc)})

        for folder, directories, files in os.walk(root, followlinks=False, onerror=on_error):
            folder_path = Path(folder)
            kept: list[str] = []
            for name in directories:
                candidate = folder_path / name
                if name.casefold() in EXCLUDED_DIRS or _is_reparse_point(candidate):
                    continue
                if any(_is_below(candidate, excluded) for excluded in excluded_roots):
                    continue
                kind = aggregate_kind(name)
                if kind:
                    aggregate, aggregate_errors = _aggregate_tree(candidate, kind)
                    aggregates[aggregate["aggregate_id"]] = aggregate
                    errors.extend(aggregate_errors)
                    continue
                kept.append(name)
            directories[:] = kept
            for name in files:
                path = folder_path / name
                if _is_reparse_point(path) or any(_is_below(path, excluded) for excluded in excluded_roots):
                    continue
                try:
                    stat = path.stat()
                except OSError as exc:
                    errors.append({"path": str(path), "error": str(exc)})
                    continue
                native_id = f"{int(getattr(stat, 'st_dev', 0) or 0):x}:{int(getattr(stat, 'st_ino', 0) or 0):x}" if int(getattr(stat, "st_ino", 0) or 0) else None
                item = _record(
                    path.resolve(), stat.st_size, str(stat.st_mtime_ns), str(stat.st_ctime_ns), scope,
                    canonical_input=True, native_file_id=native_id,
                )
                previous = records.get(item["path_key"])
                if previous and item["is_communication"]:
                    item["scope_kind"] = "primary+communication"
                records[item["path_key"]] = item
    return {
        "records": sorted(records.values(), key=lambda row: row["path_key"]),
        "aggregates": sorted(aggregates.values(), key=lambda row: row["path"].casefold()),
        "errors": errors,
        "successful_roots": successful,
        "backend": "filesystem-fallback",
    }


def _scan_everything(scopes: list[dict[str, str]], state_dir: Path, es_path: Path) -> dict[str, Any]:
    records: dict[str, dict[str, Any]] = {}
    aggregates: dict[str, dict[str, Any]] = {}
    successful: list[str] = []
    excluded_roots = (state_dir.resolve(), PACKAGE_ROOT.resolve())
    reparse_cache: dict[str, bool] = {}

    def below_reparse(path: Path, root: Path) -> bool:
        current = path.parent
        boundary = os.path.normcase(os.path.abspath(str(root))).casefold()
        while True:
            marker = os.path.normcase(os.path.abspath(str(current))).casefold()
            if marker == boundary or not marker.startswith(boundary.rstrip("\\/") + "\\"):
                return False
            if marker in reparse_cache:
                if reparse_cache[marker]:
                    return True
            else:
                reparse_cache[marker] = _is_reparse_point(current)
                if reparse_cache[marker]:
                    return True
            if current.parent == current:
                return False
            current = current.parent
    for scope in scopes:
        with tempfile.TemporaryDirectory(prefix="file_intelligence_es_") as temporary:
            export_path = Path(temporary) / "results.json"
            command = [
                str(es_path), "-full-path-and-name", "-size", "-date-modified", "-date-created",
                "-size-format", "1", "-date-format", "1", "-no-digit-grouping", "-utf8-bom",
                "-timeout", "10000", "-sort", "path", "-export-json", str(export_path),
                "-path", scope["path"], "/a-d",
            ]
            completed = subprocess.run(
                command,
                capture_output=True,
                timeout=180,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
            if completed.returncode != 0:
                detail = completed.stderr.decode("utf-8", errors="replace").strip()
                raise FileIntelligenceError(detail or f"Everything CLI returned {completed.returncode}")
            raw = export_path.read_bytes() if export_path.is_file() else b""
        successful.append(scope["path"])
        rows = json.loads(raw.decode("utf-8-sig")) if raw.strip() else []
        for row in rows:
            raw_path = row.get("filename") or row.get("Filename") or row.get("full-path-and-name") or row.get("name")
            if not raw_path:
                continue
            path = Path(str(raw_path).rstrip("\\/"))
            if any(_is_below(path, excluded) for excluded in excluded_roots):
                continue
            if below_reparse(path, Path(scope["path"])):
                continue
            try:
                relative_parts = path.relative_to(Path(scope["path"])).parts
            except ValueError:
                relative_parts = ()
            if any(part.casefold() in EXCLUDED_DIRS for part in relative_parts):
                continue
            aggregate_at = next(
                ((index, aggregate_kind(part)) for index, part in enumerate(relative_parts[:-1]) if aggregate_kind(part)),
                None,
            )
            raw_size = str(row.get("size") or row.get("Size") or "0").replace(",", "")
            try:
                size = int(raw_size)
            except ValueError:
                size = 0
            if aggregate_at:
                index, kind = aggregate_at
                aggregate_path = Path(scope["path"]).joinpath(*relative_parts[: index + 1])
                aggregate_id = "aggregate_" + hashlib.sha256(path_key(aggregate_path).encode("utf-8")).hexdigest()[:20]
                aggregate = aggregates.setdefault(
                    aggregate_id,
                    {
                        "aggregate_id": aggregate_id, "path": str(aggregate_path), "kind": kind,
                        "total_size": 0, "file_count": 0, "internal_indexing": "skipped",
                        "rebuildability": "likely", "confidence": 0.82,
                        "evidence": [{"type": "structural_inference", "detail": f"Directory name maps to aggregate kind {kind}."}],
                    },
                )
                aggregate["total_size"] += size
                aggregate["file_count"] += 1
                continue
            modified = row.get("date_modified") or row.get("date-modified") or row.get("Date Modified") or ""
            created = row.get("date_created") or row.get("date-created") or row.get("Date Created") or ""
            item = _record(path, size, str(modified), str(created), scope, canonical_input=True)
            previous = records.get(item["path_key"])
            if previous and item["is_communication"]:
                item["scope_kind"] = "primary+communication"
            records[item["path_key"]] = item
    return {
        "records": sorted(records.values(), key=lambda row: row["path_key"]),
        "aggregates": sorted(aggregates.values(), key=lambda row: row["path"].casefold()),
        "errors": [],
        "successful_roots": successful,
        "backend": "everything-es-index",
    }


def scan(
    scopes: list[dict[str, str]],
    state_dir: Path,
    backend: str = "auto",
    everything_cli: str | Path | None = None,
) -> dict[str, Any]:
    if backend not in {"auto", "everything", "filesystem"}:
        raise FileIntelligenceError(f"Unsupported backend: {backend}")
    es_path = find_everything_cli(everything_cli, state_dir)
    warning = None
    if backend in {"auto", "everything"} and es_path:
        try:
            result = _scan_everything(scopes, state_dir, es_path)
            result["everything"] = everything_status(es_path, state_dir)
            return result
        except (FileIntelligenceError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
            if backend == "everything":
                raise FileIntelligenceError(f"Everything query failed: {exc}") from exc
            warning = f"Everything query failed; used filesystem fallback: {exc}"
    elif backend == "everything":
        raise FileIntelligenceError("Everything backend requested, but ES.exe was not found.")
    elif backend == "auto":
        warning = "Everything ES.exe was not found; used the read-only filesystem fallback."
    result = _scan_filesystem(scopes, state_dir)
    result["warning"] = warning
    result["everything"] = everything_status(everything_cli, state_dir)
    return result


def _assign_projects(records: list[dict[str, Any]], scopes: list[dict[str, str]]) -> list[dict[str, Any]]:
    projects: dict[str, dict[str, Any]] = {}
    primary_roots = [Path(scope["path"]) for scope in scopes if scope["kind"] in {"primary", "primary+communication"}]
    for row in records:
        row["project_id"] = None
        row["project_name"] = None
        path = Path(row["path"])
        matched_root = next((root for root in primary_roots if _is_below(path, root)), None)
        if not matched_root:
            continue
        try:
            relative = path.relative_to(matched_root)
        except ValueError:
            continue
        directories = relative.parts[:-1]
        if not directories:
            continue
        candidate = directories[0]
        if candidate.casefold() in EXCLUDED_DIRS:
            continue
        if candidate.casefold() in GENERIC_CONTAINERS and len(directories) > 1:
            candidate = directories[1]
        if candidate.casefold() in GENERIC_CONTAINERS:
            continue
        project_id = "project_" + hashlib.sha256(
            (path_key(matched_root) + "|" + candidate.casefold()).encode("utf-8")
        ).hexdigest()[:16]
        row["project_id"] = project_id
        row["project_name"] = candidate
        project = projects.setdefault(
            project_id,
            {
                "project_id": project_id, "name": candidate, "file_count": 0, "total_size": 0,
                "status": "heuristic", "root_path": str(matched_root / directories[0]),
                "purpose": None, "lifecycle": "UNKNOWN", "confidence": 0.35,
                "evidence": [{"type": "structural_inference", "detail": "Initial project candidate inferred from directory structure."}],
            },
        )
        project["file_count"] += 1
        project["total_size"] += int(row["size"])
    return sorted(projects.values(), key=lambda item: (item["name"].casefold(), item["project_id"]))


def _merge_understood_project_assignments(
    connection: sqlite3.Connection,
    records: list[dict[str, Any]],
    heuristic_projects: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    understood = [
        dict(row)
        for row in connection.execute(
            "SELECT project_id,name,root_path FROM projects WHERE status='understood' AND root_path IS NOT NULL"
        )
    ]
    roots = sorted(
        ((Path(item["root_path"]), item) for item in understood),
        key=lambda item: len(str(item[0])), reverse=True,
    )
    for row in records:
        match = next((item for project_root, item in roots if _is_below(Path(row["path"]), project_root)), None)
        if match:
            row["project_id"] = match["project_id"]
            row["project_name"] = match["name"]
    return [
        project for project in heuristic_projects
        if not any(_is_below(Path(project["root_path"]), project_root) for project_root, _ in roots)
    ]


def _fingerprint_candidates(records: list[dict[str, Any]], limit: int) -> None:
    ordered = sorted(
        records,
        key=lambda row: (
            not row["is_communication"],
            row["extension"] not in HIGH_VALUE_EXTENSIONS,
            int(row["size"]),
            row["path_key"],
        ),
    )
    used = 0
    for row in ordered:
        row["fingerprint"] = None
        row["sample_fingerprint"] = None
        row["full_sha256"] = row.get("full_sha256")
        row["fingerprint_stage"] = 0
        if used >= limit:
            continue
        fingerprint = staged_sample_fingerprint(Path(row["path"]), int(row["size"]))
        if fingerprint:
            row["fingerprint"] = fingerprint["value"]
            row["sample_fingerprint"] = fingerprint["value"]
            row["fingerprint_stage"] = 1
            used += 1


def _connect(path: Path) -> sqlite3.Connection:
    try:
        return connect_current(path, create=not path.exists())
    except RuntimeError as exc:
        raise FileIntelligenceError(str(exc)) from exc


def _replace_catalog(connection: sqlite3.Connection, records: list[dict[str, Any]], projects: list[dict[str, Any]], stamp: str) -> None:
    connection.execute("DELETE FROM files")
    connection.execute("DELETE FROM projects WHERE status!='understood'")
    connection.executemany(
        """INSERT INTO files (
            path_key,path,root_path,relative_path,filename,extension,size,modified,created,
            scope_kind,is_communication,project_id,project_name,fingerprint,sample_fingerprint,full_sha256,
            fingerprint_stage,volatile_class,status,first_seen,last_seen,missing_since,file_id,native_file_id,
            identity_confidence,identity_evidence_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [
            (
                row["path_key"], row["path"], row["root_path"], row["relative_path"], row["filename"],
                row["extension"], row["size"], row["modified"], row["created"], row["scope_kind"],
                int(row["is_communication"]), row.get("project_id"), row.get("project_name"),
                row.get("fingerprint"), row.get("sample_fingerprint"), row.get("full_sha256"),
                int(row.get("fingerprint_stage") or 0), row.get("volatile_class"),
                row.get("status", "present"), row.get("first_seen", stamp), stamp, row.get("missing_since"),
                row.get("file_id"), row.get("native_file_id"), float(row.get("identity_confidence") or 0.7),
                row.get("identity_evidence_json") or json.dumps(row.get("identity_evidence", []), ensure_ascii=False),
            )
            for row in records
        ],
    )
    connection.executemany(
        """INSERT OR REPLACE INTO projects(project_id,name,file_count,total_size,status,root_path,purpose,lifecycle,confidence,evidence_json,parent_project_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        [
            (
                p["project_id"], p["name"], p["file_count"], p["total_size"], p["status"], p.get("root_path"),
                p.get("purpose"), p.get("lifecycle"), p.get("confidence"), json.dumps(p.get("evidence", []), ensure_ascii=False), None,
            )
            for p in projects
        ],
    )


def _sync_record_fingerprints(connection: sqlite3.Connection, records: list[dict[str, Any]], stamp: str) -> None:
    for row in records:
        if row.get("sample_fingerprint"):
            connection.execute(
                "INSERT OR REPLACE INTO fingerprints(path_key,stage,algorithm,value,bytes_read,verified_at) VALUES(?,?,?,?,?,?)",
                (row["path_key"], 1, "sha256-head-middle-tail-v2", row["sample_fingerprint"], 0, stamp),
            )
        if row.get("full_sha256"):
            connection.execute(
                "INSERT OR REPLACE INTO fingerprints(path_key,stage,algorithm,value,bytes_read,verified_at) VALUES(?,?,?,?,?,?)",
                (row["path_key"], 2, "sha256-full", row["full_sha256"], int(row["size"]), stamp),
            )


def _replace_aggregates(connection: sqlite3.Connection, aggregates: list[dict[str, Any]], projects: list[dict[str, Any]], stamp: str) -> None:
    connection.execute("DELETE FROM aggregate_nodes")
    understood_roots = [
        (Path(row["root_path"]), row["project_id"])
        for row in connection.execute("SELECT project_id,root_path FROM projects WHERE status='understood' AND root_path IS NOT NULL")
    ]
    heuristic_roots = [(Path(project["root_path"]), project["project_id"]) for project in projects if project.get("root_path")]
    project_roots = sorted(
        understood_roots + heuristic_roots,
        key=lambda item: len(str(item[0])), reverse=True,
    )
    for aggregate in aggregates:
        aggregate_path = Path(aggregate["path"])
        project_id = next((project_id for root, project_id in project_roots if _is_below(aggregate_path, root)), None)
        connection.execute(
            """INSERT INTO aggregate_nodes(
                aggregate_id,project_id,path,kind,total_size,file_count,internal_indexing,rebuildability,confidence,evidence_json,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                aggregate["aggregate_id"], project_id, aggregate["path"], aggregate["kind"], aggregate["total_size"],
                aggregate["file_count"], aggregate["internal_indexing"], aggregate["rebuildability"], aggregate["confidence"],
                json.dumps(aggregate["evidence"], ensure_ascii=False), stamp,
            ),
        )
    for project_id, in connection.execute("SELECT project_id FROM projects WHERE status='understood'"):
        files = connection.execute(
            "SELECT COUNT(*),COALESCE(SUM(size),0) FROM files WHERE status='present' AND project_id=?", (project_id,)
        ).fetchone()
        aggregate = connection.execute(
            "SELECT COALESCE(SUM(file_count),0),COALESCE(SUM(total_size),0) FROM aggregate_nodes WHERE project_id=?", (project_id,)
        ).fetchone()
        connection.execute(
            "UPDATE projects SET file_count=?,total_size=? WHERE project_id=?",
            (int(files[0]) + int(aggregate[0]), int(files[1]) + int(aggregate[1]), project_id),
        )


def _record_run(connection: sqlite3.Connection, summary: dict[str, Any]) -> None:
    connection.execute(
        "INSERT INTO runs (run_id,mode,status,created_at,summary_json) VALUES (?,?,?,?,?)",
        (summary["run_id"], summary["mode"], summary["status"], summary["generated_at"], json.dumps(summary, ensure_ascii=False)),
    )


def _counts(records: list[dict[str, Any]], projects: list[dict[str, Any]], aggregates: list[dict[str, Any]] | None = None) -> dict[str, int]:
    return {
        "files": sum(1 for row in records if row.get("status") == "present"),
        "projects": len(projects),
        "fingerprints": sum(1 for row in records if row.get("sample_fingerprint") or row.get("fingerprint") or row.get("full_sha256")),
        "communication_records": sum(1 for row in records if row.get("is_communication")),
        "aggregate_nodes": len(aggregates or []),
        "aggregate_files": sum(int(row["file_count"]) for row in (aggregates or [])),
    }


def _manifest_digest(payload: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in payload.items() if key != "digest"}
    return hashlib.sha256(json.dumps(unsigned, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def deep_onboard(
    roots: Iterable[str | Path],
    communication_roots: Iterable[str | Path] = (),
    *,
    state_dir: str | Path | None = None,
    backend: str = "auto",
    everything_cli: str | Path | None = None,
    rebuild: bool = False,
    max_fingerprints: int = 2000,
) -> dict[str, Any]:
    started = time.perf_counter()
    cpu_started = time.process_time()
    state = resolve_state_dir(state_dir)
    baseline_path = state / BASELINE_NAME
    if baseline_path.exists() and not rebuild:
        raise FileIntelligenceError("A local baseline already exists. Use Maintenance, or pass --rebuild explicitly.")
    state.mkdir(parents=True, exist_ok=True)
    if rebuild and baseline_path.exists():
        history = state / "history" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        history.mkdir(parents=True, exist_ok=False)
        for name in (CATALOG_NAME, BASELINE_NAME, CHANGES_NAME):
            candidate = state / name
            if candidate.exists():
                shutil.move(str(candidate), str(history / name))
    scopes = _scope_payload(roots, communication_roots)
    snapshot = scan(scopes, state, backend, everything_cli)
    records = snapshot["records"]
    aggregates = snapshot.get("aggregates", [])
    projects = _assign_projects(records, scopes)
    _fingerprint_candidates(records, max(0, max_fingerprints))
    stamp = now_iso()
    initialize_identities(records, stamp)
    run_id = "run_" + uuid.uuid4().hex[:20]
    binding = machine_binding()
    baseline_id = "baseline_" + hashlib.sha256(
        f"{binding}|{stamp}|{len(records)}".encode("utf-8")
    ).hexdigest()[:20]
    counts = _counts(records, projects, aggregates)
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "baseline_id": baseline_id,
        "version": __version__,
        "generated_at": stamp,
        "mode": "MAINTENANCE_READY",
        "machine_binding": binding,
        "machine_binding_version": 2 if _windows_machine_guid() else 1,
        "roots": scopes,
        "backend": snapshot["backend"],
        "counts": counts,
        "real_execution_enabled": False,
        "digest": "",
    }
    manifest["digest"] = _manifest_digest(manifest)
    summary = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "mode": "DEEP_ONBOARDING",
        "status": "BASELINE_CREATED",
        "generated_at": stamp,
        "baseline_id": baseline_id,
        "backend": snapshot["backend"],
        "counts": counts,
        "scope_errors": snapshot.get("errors", []),
        "warning": snapshot.get("warning"),
        "everything": snapshot["everything"],
        "state_dir": str(state),
        "physical_actions": 0,
        "performance": {
            "elapsed_seconds": round(time.perf_counter() - started, 6),
            "cpu_seconds": round(time.process_time() - cpu_started, 6),
            "files_discovered": len(records) + sum(int(item["file_count"]) for item in aggregates),
            "files_indexed": len(records),
            "content_inspected": 0,
            "stage1_hashes": counts["fingerprints"],
        },
    }
    with closing(_connect(state / CATALOG_NAME)) as connection:
        _replace_catalog(connection, records, projects, stamp)
        _replace_aggregates(connection, aggregates, projects, stamp)
        _sync_record_fingerprints(connection, records, stamp)
        sync_file_cards(connection, records, stamp)
        connection.execute("INSERT OR REPLACE INTO meta (key,value) VALUES (?,?)", ("baseline_id", baseline_id))
        connection.execute("INSERT OR REPLACE INTO meta (key,value) VALUES (?,?)", ("machine_binding", binding))
        _record_run(connection, summary)
        snapshot_summary = create_snapshot(
            connection, state, snapshot_kind="baseline", run_id=run_id, stamp=stamp,
        )
        connection.commit()
        dashboard = build_dashboard(connection, state, last_summary=summary)
        timeline_page = build_timeline_page(connection, state)
        summary["dashboard"] = str(dashboard)
        summary["timeline"] = str(timeline_page)
        summary["snapshot"] = snapshot_summary
    atomic_json(baseline_path, manifest)
    atomic_json(state / CHANGES_NAME, {"schema_version": SCHEMA_VERSION, "mode": "DEEP_ONBOARDING", "changes": [], "counts": {"new": 0, "changed": 0, "missing": 0}, "physical_actions": 0})
    return summary


def _load_files(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute("SELECT * FROM files ORDER BY path_key")]


def _diff(old_records: list[dict[str, Any]], current_records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    old = {row["path_key"]: row for row in old_records}
    current = {row["path_key"]: row for row in current_records}
    changes: list[dict[str, Any]] = []
    counts = {"new": 0, "changed": 0, "missing": 0, "unchanged": 0, "reappeared": 0}
    for key, row in current.items():
        before = old.get(key)
        if before is None or before.get("status") == "missing":
            row["change_type"] = "NEW" if before is None else "REAPPEARED"
            counts["new"] += 1
            if before is not None:
                counts["reappeared"] += 1
                row["first_seen"] = before.get("first_seen")
                row["file_id"] = before.get("file_id")
                row["identity_confidence"] = before.get("identity_confidence")
                row["identity_evidence_json"] = before.get("identity_evidence_json")
            changes.append({
                "change_type": row["change_type"], "relative_path": row["relative_path"], "path": row["path"],
                "path_key": row["path_key"], "project": row.get("project_name"),
                "volatile": bool(row.get("volatile_class")), "volatile_class": row.get("volatile_class"),
            })
        elif (int(before["size"]), str(before["modified"])) != (int(row["size"]), str(row["modified"])):
            row["change_type"] = "CHANGED"
            row["first_seen"] = before.get("first_seen")
            row["file_id"] = before.get("file_id")
            row["native_file_id"] = row.get("native_file_id") or before.get("native_file_id")
            row["identity_confidence"] = before.get("identity_confidence")
            row["identity_evidence_json"] = before.get("identity_evidence_json")
            counts["changed"] += 1
            changes.append({
                "change_type": "CHANGED", "relative_path": row["relative_path"], "path": row["path"],
                "path_key": row["path_key"], "project": row.get("project_name"),
                "volatile": bool(row.get("volatile_class")), "volatile_class": row.get("volatile_class"),
            })
        else:
            row["fingerprint"] = before.get("fingerprint")
            row["sample_fingerprint"] = before.get("sample_fingerprint")
            row["full_sha256"] = before.get("full_sha256")
            row["fingerprint_stage"] = before.get("fingerprint_stage") or 0
            row["first_seen"] = before.get("first_seen")
            row["file_id"] = before.get("file_id")
            row["native_file_id"] = row.get("native_file_id") or before.get("native_file_id")
            row["identity_confidence"] = before.get("identity_confidence")
            row["identity_evidence_json"] = before.get("identity_evidence_json")
            row["change_type"] = "UNCHANGED"
            counts["unchanged"] += 1
    for key, before in old.items():
        if key not in current and before.get("status") != "missing":
            missing = dict(before)
            missing["status"] = "missing"
            missing["missing_since"] = now_iso()
            missing["change_type"] = "MISSING"
            current_records.append(missing)
            counts["missing"] += 1
            changes.append({
                "change_type": "MISSING", "relative_path": missing["relative_path"], "path": missing["path"],
                "path_key": missing["path_key"], "project": missing.get("project_name"),
                "volatile": bool(missing.get("volatile_class")), "volatile_class": missing.get("volatile_class"),
            })
        elif key not in current:
            current_records.append(dict(before))
    changes.sort(key=lambda item: (item["change_type"], str(item["relative_path"]).casefold()))
    return changes, counts


def _identity_events(
    previous: list[dict[str, Any]],
    current: list[dict[str, Any]],
    changes: list[dict[str, Any]],
    run_id: str,
    stamp: str,
) -> list[dict[str, Any]]:
    return correlate_identities(previous, current, changes, run_id, stamp)


def maintain(
    *,
    state_dir: str | Path | None = None,
    backend: str = "auto",
    everything_cli: str | Path | None = None,
    max_fingerprints: int = 2000,
    deep: bool = False,
    snapshot_kind: str | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    cpu_started = time.process_time()
    state = resolve_state_dir(state_dir)
    baseline_path = state / BASELINE_NAME
    catalog_path = state / CATALOG_NAME
    if not baseline_path.is_file() or not catalog_path.is_file():
        raise FileIntelligenceError("No local baseline exists. Run explicit Deep Onboarding first.")
    schema = inspect_schema(catalog_path)
    if schema.get("migration_required"):
        raise FileIntelligenceError(
            f"Catalog schema v{schema['version']} requires explicit `migrate --apply` before Maintenance; no state was changed."
        )
    baseline = read_json(baseline_path)
    if not machine_binding_matches(baseline.get("machine_binding")):
        raise FileIntelligenceError("Local state is bound to another machine. Run explicit Deep Onboarding or a reviewed rebind.")
    scopes = baseline.get("roots", [])
    effective_backend = backend
    if backend == "auto":
        baseline_backend = str(baseline.get("backend") or "")
        if baseline_backend == "everything-es-index":
            effective_backend = "everything"
        elif baseline_backend == "filesystem-fallback":
            effective_backend = "filesystem"
    snapshot = scan(scopes, state, effective_backend, everything_cli)
    current = snapshot["records"]
    aggregates = snapshot.get("aggregates", [])
    projects = _assign_projects(current, scopes)
    stamp = now_iso()
    with closing(_connect(catalog_path)) as connection:
        projects = _merge_understood_project_assignments(connection, current, projects)
        previous = _load_files(connection)
        old_aggregates = [dict(row) for row in connection.execute("SELECT * FROM aggregate_nodes")]
        understood_roots = {
            row["project_id"]: row["root_path"]
            for row in connection.execute("SELECT project_id,root_path FROM projects WHERE status='understood' AND root_path IS NOT NULL")
        }
        aggregate_roots = sorted(
            [(Path(root), project_id) for project_id, root in understood_roots.items()]
            + [(Path(item["root_path"]), item["project_id"]) for item in projects if item.get("root_path")],
            key=lambda item: len(str(item[0])), reverse=True,
        )
        for aggregate in aggregates:
            aggregate["project_id"] = next(
                (project_id for root, project_id in aggregate_roots if _is_below(Path(aggregate["path"]), root)), None,
            )
        changes, change_counts = _diff(previous, current)
        candidates = [row for row in current if row.get("change_type") in {"NEW", "REAPPEARED", "CHANGED"}]
        _fingerprint_candidates(candidates, max(0, max_fingerprints))
        for row in current:
            if row.get("change_type") == "UNCHANGED":
                continue
            match = next((candidate for candidate in candidates if candidate["path_key"] == row["path_key"]), None)
            if match:
                row["fingerprint"] = match.get("fingerprint")
                row["sample_fingerprint"] = match.get("sample_fingerprint")
                row["fingerprint_stage"] = match.get("fingerprint_stage") or 0
        run_id = "run_" + uuid.uuid4().hex[:20]
        identity_events = _identity_events(previous, current, changes, run_id, stamp)
        identity_by_target = {event["target_path_key"]: event for event in identity_events}
        identity_by_source = {event["source_path_key"]: event for event in identity_events if event["event_type"] in {"MOVED", "RENAMED"}}
        for change in changes:
            event = identity_by_target.get(change["path_key"]) or identity_by_source.get(change["path_key"])
            if event:
                change["identity_event"] = event["event_type"]
                change["identity_confidence"] = event["confidence"]
        for event in identity_events:
            change_counts[event["event_type"].casefold()] = change_counts.get(event["event_type"].casefold(), 0) + 1
        volatile_changes = sum(1 for item in changes if item.get("volatile"))
        affected_projects = sorted({str(item["project"]) for item in changes if item.get("project") and not item.get("volatile")})
        current_by_key = {row["path_key"]: row for row in current}
        affected_project_ids = sorted({
            str((current_by_key.get(item["path_key"], {}) or {}).get("project_id"))
            for item in changes if not item.get("volatile")
            if (current_by_key.get(item["path_key"], {}) or {}).get("project_id")
        })
        event_metrics = record_maintenance_events(
            connection, previous, current, changes, identity_events, old_aggregates, aggregates, run_id, stamp,
        )
        catalog_counts = _counts(current, projects, aggregates)
        catalog_counts["projects"] += connection.execute(
            "SELECT COUNT(*) FROM projects WHERE status='understood'"
        ).fetchone()[0]
        _replace_catalog(connection, current, projects, stamp)
        _replace_aggregates(connection, aggregates, projects, stamp)
        _sync_record_fingerprints(connection, current, stamp)
        for event in identity_events:
            connection.execute(
                """INSERT INTO identity_events(
                    event_id,run_id,event_type,source_path_key,target_path_key,full_sha256,confidence,evidence_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    event["event_id"], event["run_id"], event["event_type"], event["source_path_key"], event["target_path_key"],
                    event["full_sha256"], event["confidence"], json.dumps(event["evidence"], ensure_ascii=False), event["created_at"],
                ),
            )
        deep_results: list[dict[str, Any]] = []
        if deep:
            for project_id in affected_project_ids:
                root = understood_roots.get(project_id)
                if not root or not Path(root).is_dir():
                    continue
                deep_results.append(build_project_understanding(
                    connection, Path(root), max_inspections=120, max_stage1_hashes=400, max_full_hashes=8, commit=False,
                ))
        sync_file_cards(connection, current, stamp)
        activity_events = update_project_activity(connection, run_id, stamp)
        semantic_summary = materialize_semantic_changes(connection, run_id, stamp)
        meaningful_changes = int(semantic_summary["meaningful_event_count"])
        snapshot_summary = create_snapshot(
            connection, state, snapshot_kind=snapshot_kind or ("weekly" if deep else "daily"), run_id=run_id, stamp=stamp,
        )
        summary = {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "mode": "WEEKLY_DEEP_MAINTENANCE" if deep else "MAINTENANCE",
            "status": "NO_OP" if not changes and event_metrics["events_recorded"] == 0 and not activity_events else "CHANGES_RECORDED",
            "generated_at": stamp,
            "baseline_id": baseline["baseline_id"],
            "backend": snapshot["backend"],
            "counts": change_counts,
            "catalog_counts": catalog_counts,
            "filesystem_changes": len(changes),
            "volatile_changes": volatile_changes,
            "meaningful_changes": meaningful_changes,
            "semantic_status": "NO_MEANINGFUL_CHANGE" if meaningful_changes == 0 else "MEANINGFUL_CHANGE",
            "semantic_summary": semantic_summary,
            "events_recorded": event_metrics["events_recorded"] + len(activity_events),
            "identity_events": {key: sum(1 for event in identity_events if event["event_type"] == key) for key in ("MOVED", "RENAMED", "COPIED", "POSSIBLE_MOVE")},
            "affected_projects": affected_projects,
            "project_reinspection": "completed" if deep and deep_results else ("not_required" if not affected_projects else "targeted_refresh_recommended"),
            "deep_project_refreshes": len(deep_results),
            "snapshot": snapshot_summary,
            "scope_errors": snapshot.get("errors", []),
            "warning": snapshot.get("warning"),
            "everything": snapshot["everything"],
            "physical_actions": 0,
            "performance": {
                "elapsed_seconds": round(time.perf_counter() - started, 6),
                "cpu_seconds": round(time.process_time() - cpu_started, 6),
                "files_discovered": len(current) + sum(int(item["file_count"]) for item in aggregates),
                "content_inspected": sum(int(result.get("inspections", {}).get("selected", 0)) for result in deep_results),
                "stage1_hashes": sum(1 for row in candidates if row.get("sample_fingerprint")),
                "documents_parsed": sum(int(result.get("inspections", {}).get("successful", 0)) for result in deep_results),
            },
        }
        _record_run(connection, summary)
        connection.commit()
        dashboard = build_dashboard(connection, state, last_summary=summary)
        timeline_page = build_timeline_page(connection, state)
        summary["dashboard"] = str(dashboard)
        summary["timeline"] = str(timeline_page)
    atomic_json(state / CHANGES_NAME, {**summary, "changes": changes})
    return summary


def status(*, state_dir: str | Path | None = None, everything_cli: str | Path | None = None) -> dict[str, Any]:
    state = resolve_state_dir(state_dir)
    baseline_path = state / BASELINE_NAME
    catalog_path = state / CATALOG_NAME
    capability = everything_status(everything_cli, state)
    if not baseline_path.is_file() or not catalog_path.is_file():
        return {
            "skill_version": __version__,
            "schema_version": SCHEMA_VERSION,
            "status": "DEEP_ONBOARDING_REQUIRED",
            "baseline_present": False,
            "state_dir": str(state),
            "everything": capability,
            "real_execution_enabled": False,
        }
    baseline = read_json(baseline_path)
    binding_matches = machine_binding_matches(baseline.get("machine_binding"))
    schema = inspect_schema(catalog_path)
    if schema.get("migration_required"):
        uri = f"file:{catalog_path.as_posix()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            counts = {
                "files": connection.execute("SELECT COUNT(*) FROM files WHERE status='present'").fetchone()[0],
                "projects": connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0],
                "fingerprints": connection.execute("SELECT COUNT(*) FROM files WHERE fingerprint IS NOT NULL").fetchone()[0],
                "communication_records": connection.execute("SELECT COUNT(*) FROM files WHERE is_communication=1 AND status='present'").fetchone()[0],
            }
        return {
            "skill_version": __version__,
            "schema_version": schema["version"], "target_schema_version": SCHEMA_VERSION,
            "status": "MIGRATION_REQUIRED", "baseline_present": True, "baseline_id": baseline.get("baseline_id"),
            "machine_binding_valid": binding_matches, "counts": counts, "state_dir": str(state),
            "migration": migration_plan(state), "everything": capability, "real_execution_enabled": False,
        }
    with closing(_connect(catalog_path)) as connection:
        counts = {
            "files": connection.execute("SELECT COUNT(*) FROM files WHERE status='present'").fetchone()[0],
            "projects": connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0],
            "fingerprints": connection.execute(
                "SELECT COUNT(*) FROM files WHERE fingerprint IS NOT NULL OR sample_fingerprint IS NOT NULL OR full_sha256 IS NOT NULL"
            ).fetchone()[0],
            "communication_records": connection.execute("SELECT COUNT(*) FROM files WHERE is_communication=1 AND status='present'").fetchone()[0],
            "understood_projects": connection.execute("SELECT COUNT(*) FROM projects WHERE status='understood'").fetchone()[0],
            "dependencies": connection.execute("SELECT COUNT(*) FROM dependencies").fetchone()[0],
            "authority_assets": connection.execute("SELECT COUNT(*) FROM assets WHERE authority_level IN ('PRIMARY','CANONICAL','ACTIVE')").fetchone()[0],
            "aggregate_nodes": connection.execute("SELECT COUNT(*) FROM aggregate_nodes").fetchone()[0],
            "file_cards": connection.execute("SELECT COUNT(*) FROM file_cards").fetchone()[0],
            "events": connection.execute("SELECT COUNT(*) FROM events").fetchone()[0],
            "snapshots": connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0],
            "open_asset_alerts": connection.execute("SELECT COUNT(*) FROM asset_alerts WHERE status='OPEN'").fetchone()[0],
        }
    return {
        "skill_version": __version__,
        "schema_version": SCHEMA_VERSION,
        "status": "MAINTENANCE_READY" if binding_matches else "MACHINE_REBIND_REQUIRED",
        "baseline_present": True,
        "baseline_id": baseline.get("baseline_id"),
        "baseline_digest_valid": baseline.get("digest") == _manifest_digest(baseline),
        "machine_binding_valid": binding_matches,
        "counts": counts,
        "state_dir": str(state),
        "everything": capability,
        "real_execution_enabled": False,
    }


def list_projects(*, state_dir: str | Path | None = None) -> dict[str, Any]:
    state = resolve_state_dir(state_dir)
    catalog_path = state / CATALOG_NAME
    if not catalog_path.is_file():
        raise FileIntelligenceError("No local catalog exists. Run explicit Deep Onboarding first.")
    with closing(_connect(catalog_path)) as connection:
        projects = [dict(row) for row in connection.execute("SELECT * FROM projects ORDER BY name COLLATE NOCASE")]
    for project in projects:
        if project.get("evidence_json"):
            project["evidence"] = json.loads(project.pop("evidence_json"))
    return {"schema_version": SCHEMA_VERSION, "projects": projects, "authority": "mixed_evidence", "physical_actions": 0}


def last_changes(*, state_dir: str | Path | None = None) -> dict[str, Any]:
    state = resolve_state_dir(state_dir)
    path = state / CHANGES_NAME
    if not path.is_file():
        raise FileIntelligenceError("No maintenance result exists. Run Deep Onboarding first.")
    return read_json(path)


def migrate_state(*, state_dir: str | Path | None = None, apply: bool = False) -> dict[str, Any]:
    state = resolve_state_dir(state_dir)
    try:
        return migrate_to_current(state, apply=apply)
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise FileIntelligenceError(f"Schema migration failed without modifying project files: {exc}") from exc


def rollback_state(*, state_dir: str | Path | None = None, backup_path: str | Path, apply: bool = False) -> dict[str, Any]:
    state = resolve_state_dir(state_dir)
    try:
        return rollback_migration(state, Path(backup_path).resolve(), apply=apply)
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise FileIntelligenceError(f"Schema rollback failed: {exc}") from exc


def understand_project(
    project_root: str | Path,
    *,
    state_dir: str | Path | None = None,
    max_inspections: int = 400,
    max_stage1_hashes: int = 1200,
    max_full_hashes: int = 32,
    content_exclude_patterns: Iterable[str] = (),
) -> dict[str, Any]:
    state = resolve_state_dir(state_dir)
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise FileIntelligenceError(f"Project root is not an accessible directory: {root}")
    try:
        with closing(_connect(state / CATALOG_NAME)) as connection:
            result = build_project_understanding(
                connection, root, max_inspections=max_inspections, max_stage1_hashes=max_stage1_hashes,
                max_full_hashes=max_full_hashes, content_exclude_patterns=content_exclude_patterns,
            )
            result["semantic_summary"] = materialize_semantic_changes(connection, result["timeline_run_id"], now_iso())
            sync_file_cards(connection, _load_files(connection), now_iso())
            connection.commit()
            dashboard = build_dashboard(connection, state, last_summary={"semantic_status": "PROJECT_UNDERSTANDING_UPDATED"})
            timeline_page = build_timeline_page(connection, state)
    except (ValueError, sqlite3.Error) as exc:
        raise FileIntelligenceError(str(exc)) from exc
    output_dir = state / "project_understanding"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{result['project']['project_id']}.json"
    result["dashboard"] = str(dashboard)
    result["timeline"] = str(timeline_page)
    result["result_path"] = str(output)
    atomic_json(output, result)
    return result


def get_asset_details(
    path: str | Path,
    *,
    state_dir: str | Path | None = None,
    verify_full_hash: bool = False,
) -> dict[str, Any]:
    state = resolve_state_dir(state_dir)
    try:
        with closing(_connect(state / CATALOG_NAME)) as connection:
            if verify_full_hash:
                key = path_key(path)
                row = connection.execute("SELECT path,size,full_sha256 FROM files WHERE path_key=? AND status='present'", (key,)).fetchone()
                if row is None:
                    raise ValueError(f"Asset is not in the catalog: {path}")
                if not row["full_sha256"]:
                    proof = full_sha256(Path(row["path"]))
                    if not proof:
                        raise ValueError(f"Could not read asset for Stage-2 verification: {path}")
                    stamp = now_iso()
                    connection.execute("UPDATE files SET full_sha256=?,fingerprint_stage=2 WHERE path_key=?", (proof["value"], key))
                    connection.execute(
                        "INSERT OR REPLACE INTO fingerprints(path_key,stage,algorithm,value,bytes_read,verified_at) VALUES(?,?,?,?,?,?)",
                        (key, 2, proof["algorithm"], proof["value"], proof["bytes_read"], stamp),
                    )
                    connection.commit()
            return query_asset_details(connection, Path(path))
    except (ValueError, sqlite3.Error) as exc:
        raise FileIntelligenceError(str(exc)) from exc


def assertions(
    *,
    state_dir: str | Path | None = None,
    action: str = "list",
    assertion_id: str | None = None,
    subject_type: str | None = None,
    subject_key: str | None = None,
    predicate: str | None = None,
    value: Any = None,
) -> dict[str, Any]:
    state = resolve_state_dir(state_dir)
    try:
        with closing(_connect(state / CATALOG_NAME)) as connection:
            if action == "list":
                return {"assertions": query_assertions(connection), "physical_actions": 0}
            if action == "remove":
                if not assertion_id:
                    raise ValueError("assertion_id is required")
                return deactivate_assertion(connection, assertion_id)
            if not all((subject_type, subject_key, predicate)):
                raise ValueError("subject_type, subject_key, and predicate are required")
            return store_assertion(
                connection, subject_type=str(subject_type), subject_key=str(subject_key), predicate=str(predicate), value=value,
            )
    except (ValueError, sqlite3.Error) as exc:
        raise FileIntelligenceError(str(exc)) from exc


def dashboard(*, state_dir: str | Path | None = None) -> dict[str, Any]:
    state = resolve_state_dir(state_dir)
    try:
        with closing(_connect(state / CATALOG_NAME)) as connection:
            path = build_dashboard(connection, state)
            timeline_path = build_timeline_page(connection, state)
    except (OSError, sqlite3.Error) as exc:
        raise FileIntelligenceError(str(exc)) from exc
    return {"dashboard": str(path), "timeline": str(timeline_path), "physical_actions": 0}


def timeline_history(
    *,
    state_dir: str | Path | None = None,
    since_last_scan: bool = False,
    days: int | None = None,
    from_value: str | None = None,
    to_value: str | None = None,
    project: str | None = None,
    event_type: str | None = None,
    importance: str | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    state = resolve_state_dir(state_dir)
    try:
        with closing(_connect(state / CATALOG_NAME)) as connection:
            return query_timeline(
                connection, since_last_scan=since_last_scan, days=days, from_value=from_value, to_value=to_value,
                project=project, event_type=event_type, importance=importance, limit=limit,
            )
    except (ValueError, sqlite3.Error) as exc:
        raise FileIntelligenceError(str(exc)) from exc


def timeline_context(
    *,
    state_dir: str | Path | None = None,
    since_last_scan: bool = False,
    days: int | None = None,
    from_value: str | None = None,
    to_value: str | None = None,
    project: str | None = None,
) -> dict[str, Any]:
    state = resolve_state_dir(state_dir)
    try:
        with closing(_connect(state / CATALOG_NAME)) as connection:
            return build_context_summary(
                connection, since_last_scan=since_last_scan, days=days, from_value=from_value, to_value=to_value,
                project=project,
            )
    except (ValueError, sqlite3.Error) as exc:
        raise FileIntelligenceError(str(exc)) from exc


def project_history(*, project: str, state_dir: str | Path | None = None, limit: int = 1000) -> dict[str, Any]:
    state = resolve_state_dir(state_dir)
    try:
        with closing(_connect(state / CATALOG_NAME)) as connection:
            return query_project_history(connection, project, limit=limit)
    except (ValueError, sqlite3.Error) as exc:
        raise FileIntelligenceError(str(exc)) from exc


def file_history(*, file: str, state_dir: str | Path | None = None, limit: int = 500) -> dict[str, Any]:
    state = resolve_state_dir(state_dir)
    try:
        with closing(_connect(state / CATALOG_NAME)) as connection:
            return query_file_history(connection, file, limit=limit)
    except (ValueError, sqlite3.Error) as exc:
        raise FileIntelligenceError(str(exc)) from exc


def storage_history(*, state_dir: str | Path | None = None, days: int = 7, project: str | None = None) -> dict[str, Any]:
    state = resolve_state_dir(state_dir)
    try:
        with closing(_connect(state / CATALOG_NAME)) as connection:
            return query_storage_growth(connection, days=days, project=project)
    except (ValueError, sqlite3.Error) as exc:
        raise FileIntelligenceError(str(exc)) from exc


def snapshot_now(
    *, state_dir: str | Path | None = None, snapshot_kind: str = "manual",
) -> dict[str, Any]:
    state = resolve_state_dir(state_dir)
    stamp = now_iso()
    try:
        with closing(_connect(state / CATALOG_NAME)) as connection:
            result = create_snapshot(connection, state, snapshot_kind=snapshot_kind, run_id=None, stamp=stamp)
            connection.commit()
            return {"snapshot": result, "physical_actions": 0}
    except (ValueError, OSError, sqlite3.Error) as exc:
        raise FileIntelligenceError(str(exc)) from exc


def retention(
    *, state_dir: str | Path | None = None, apply: bool = False,
) -> dict[str, Any]:
    state = resolve_state_dir(state_dir)
    try:
        with closing(_connect(state / CATALOG_NAME)) as connection:
            return retention_plan(connection, apply=apply)
    except (ValueError, sqlite3.Error) as exc:
        raise FileIntelligenceError(str(exc)) from exc


def scheduler_support(*, state_dir: str | Path | None = None) -> dict[str, Any]:
    return schedule_plan(PACKAGE_ROOT, resolve_state_dir(state_dir))
