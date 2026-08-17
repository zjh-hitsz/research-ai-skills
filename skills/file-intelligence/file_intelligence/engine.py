from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import tempfile
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import __version__


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
CATALOG_NAME = "catalog.db"
BASELINE_NAME = "baseline.json"
CHANGES_NAME = "last_changes.json"
EXCLUDED_DIRS = {
    ".git", ".venv", "venv", "__pycache__", "node_modules", ".pytest_cache",
    "$recycle.bin", "system volume information",
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
    return os.path.normcase(os.path.abspath(os.fspath(path))).casefold()


def extension_for(name: str) -> str:
    lower = name.casefold()
    for extension in COMPOUND_EXTENSIONS:
        if lower.endswith(extension):
            return extension
    return Path(name).suffix.casefold()


def machine_binding() -> str:
    material = "|".join((platform.node(), platform.system(), platform.machine()))
    return hashlib.sha256(material.encode("utf-8", errors="replace")).hexdigest()


def quick_fingerprint(path: Path, size: int, sample_bytes: int = 65536) -> str | None:
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
        path.resolve().relative_to(parent.resolve())
        return True
    except (OSError, ValueError):
        return False


def _candidate_everything_paths(explicit: str | Path | None = None) -> Iterable[Path]:
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


def find_everything_cli(explicit: str | Path | None = None) -> Path | None:
    seen: set[str] = set()
    for candidate in _candidate_everything_paths(explicit):
        marker = path_key(candidate)
        if marker in seen:
            continue
        seen.add(marker)
        if candidate.is_file():
            return candidate.resolve()
    return None


def everything_status(explicit: str | Path | None = None) -> dict[str, Any]:
    cli = find_everything_cli(explicit)
    service_install = False
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(variable)
        if root and (Path(root) / "Everything" / "Everything.exe").is_file():
            service_install = True
            break
    return {
        "cli_found": bool(cli),
        "cli_path": str(cli) if cli else None,
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


def _record(path: Path, stat_size: int, modified: str, created: str, scope: dict[str, str]) -> dict[str, Any]:
    root = Path(scope["path"])
    try:
        relative = str(path.relative_to(root))
    except ValueError:
        relative = path.name
    return {
        "path_key": path_key(path),
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
        "status": "present",
    }


def _scan_filesystem(scopes: list[dict[str, str]], state_dir: Path) -> dict[str, Any]:
    records: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, str]] = []
    successful: list[str] = []
    excluded_roots = (state_dir.resolve(), PACKAGE_ROOT.resolve())
    for scope in scopes:
        root = Path(scope["path"])
        successful.append(scope["path"])
        for folder, directories, files in os.walk(root, followlinks=False):
            folder_path = Path(folder)
            kept: list[str] = []
            for name in directories:
                candidate = folder_path / name
                if name.casefold() in EXCLUDED_DIRS or candidate.is_symlink():
                    continue
                if any(_is_below(candidate, excluded) for excluded in excluded_roots):
                    continue
                kept.append(name)
            directories[:] = kept
            for name in files:
                path = folder_path / name
                if path.is_symlink() or any(_is_below(path, excluded) for excluded in excluded_roots):
                    continue
                try:
                    stat = path.stat()
                except OSError as exc:
                    errors.append({"path": str(path), "error": str(exc)})
                    continue
                item = _record(path.resolve(), stat.st_size, str(stat.st_mtime_ns), str(stat.st_ctime_ns), scope)
                previous = records.get(item["path_key"])
                if previous and item["is_communication"]:
                    item["scope_kind"] = "primary+communication"
                records[item["path_key"]] = item
    return {
        "records": sorted(records.values(), key=lambda row: row["path_key"]),
        "errors": errors,
        "successful_roots": successful,
        "backend": "filesystem-fallback",
    }


def _scan_everything(scopes: list[dict[str, str]], state_dir: Path, es_path: Path) -> dict[str, Any]:
    records: dict[str, dict[str, Any]] = {}
    successful: list[str] = []
    excluded_roots = (state_dir.resolve(), PACKAGE_ROOT.resolve())
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
            path = Path(str(raw_path).rstrip("\\/")).resolve()
            if any(_is_below(path, excluded) for excluded in excluded_roots):
                continue
            raw_size = str(row.get("size") or row.get("Size") or "0").replace(",", "")
            try:
                size = int(raw_size)
            except ValueError:
                size = 0
            modified = row.get("date_modified") or row.get("date-modified") or row.get("Date Modified") or ""
            created = row.get("date_created") or row.get("date-created") or row.get("Date Created") or ""
            item = _record(path, size, str(modified), str(created), scope)
            previous = records.get(item["path_key"])
            if previous and item["is_communication"]:
                item["scope_kind"] = "primary+communication"
            records[item["path_key"]] = item
    return {
        "records": sorted(records.values(), key=lambda row: row["path_key"]),
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
    es_path = find_everything_cli(everything_cli)
    warning = None
    if backend in {"auto", "everything"} and es_path:
        try:
            result = _scan_everything(scopes, state_dir, es_path)
            result["everything"] = everything_status(es_path)
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
    result["everything"] = everything_status(everything_cli)
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
            {"project_id": project_id, "name": candidate, "file_count": 0, "total_size": 0, "status": "heuristic"},
        )
        project["file_count"] += 1
        project["total_size"] += int(row["size"])
    return sorted(projects.values(), key=lambda item: (item["name"].casefold(), item["project_id"]))


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
        if used >= limit or int(row["size"]) > 64 * 1024 * 1024:
            continue
        fingerprint = quick_fingerprint(Path(row["path"]), int(row["size"]))
        if fingerprint:
            row["fingerprint"] = fingerprint
            used += 1


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS files (
            path_key TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            root_path TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            filename TEXT NOT NULL,
            extension TEXT NOT NULL,
            size INTEGER NOT NULL,
            modified TEXT NOT NULL,
            created TEXT NOT NULL,
            scope_kind TEXT NOT NULL,
            is_communication INTEGER NOT NULL,
            project_id TEXT,
            project_name TEXT,
            fingerprint TEXT,
            status TEXT NOT NULL,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            missing_since TEXT
        );
        CREATE TABLE IF NOT EXISTS projects (
            project_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            file_count INTEGER NOT NULL,
            total_size INTEGER NOT NULL,
            status TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            mode TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            summary_json TEXT NOT NULL
        );
        """
    )
    return connection


def _replace_catalog(connection: sqlite3.Connection, records: list[dict[str, Any]], projects: list[dict[str, Any]], stamp: str) -> None:
    connection.execute("DELETE FROM files")
    connection.execute("DELETE FROM projects")
    connection.executemany(
        """INSERT INTO files (
            path_key,path,root_path,relative_path,filename,extension,size,modified,created,
            scope_kind,is_communication,project_id,project_name,fingerprint,status,first_seen,last_seen,missing_since
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [
            (
                row["path_key"], row["path"], row["root_path"], row["relative_path"], row["filename"],
                row["extension"], row["size"], row["modified"], row["created"], row["scope_kind"],
                int(row["is_communication"]), row.get("project_id"), row.get("project_name"),
                row.get("fingerprint"), row.get("status", "present"), stamp, stamp, row.get("missing_since"),
            )
            for row in records
        ],
    )
    connection.executemany(
        "INSERT INTO projects (project_id,name,file_count,total_size,status) VALUES (?,?,?,?,?)",
        [(p["project_id"], p["name"], p["file_count"], p["total_size"], p["status"]) for p in projects],
    )


def _record_run(connection: sqlite3.Connection, summary: dict[str, Any]) -> None:
    connection.execute(
        "INSERT INTO runs (run_id,mode,status,created_at,summary_json) VALUES (?,?,?,?,?)",
        (summary["run_id"], summary["mode"], summary["status"], summary["generated_at"], json.dumps(summary, ensure_ascii=False)),
    )


def _counts(records: list[dict[str, Any]], projects: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "files": sum(1 for row in records if row.get("status") == "present"),
        "projects": len(projects),
        "fingerprints": sum(1 for row in records if row.get("fingerprint")),
        "communication_records": sum(1 for row in records if row.get("is_communication")),
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
    projects = _assign_projects(records, scopes)
    _fingerprint_candidates(records, max(0, max_fingerprints))
    stamp = now_iso()
    run_id = "run_" + uuid.uuid4().hex[:20]
    binding = machine_binding()
    baseline_id = "baseline_" + hashlib.sha256(
        f"{binding}|{stamp}|{len(records)}".encode("utf-8")
    ).hexdigest()[:20]
    counts = _counts(records, projects)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "baseline_id": baseline_id,
        "version": __version__,
        "generated_at": stamp,
        "mode": "MAINTENANCE_READY",
        "machine_binding": binding,
        "roots": scopes,
        "backend": snapshot["backend"],
        "counts": counts,
        "real_execution_enabled": False,
        "digest": "",
    }
    manifest["digest"] = _manifest_digest(manifest)
    summary = {
        "schema_version": 1,
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
    }
    with closing(_connect(state / CATALOG_NAME)) as connection:
        _replace_catalog(connection, records, projects, stamp)
        connection.execute("INSERT OR REPLACE INTO meta (key,value) VALUES (?,?)", ("baseline_id", baseline_id))
        connection.execute("INSERT OR REPLACE INTO meta (key,value) VALUES (?,?)", ("machine_binding", binding))
        _record_run(connection, summary)
        connection.commit()
    atomic_json(baseline_path, manifest)
    atomic_json(state / CHANGES_NAME, {"mode": "DEEP_ONBOARDING", "changes": [], "counts": {"new": 0, "changed": 0, "missing": 0}})
    return summary


def _load_files(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute("SELECT * FROM files ORDER BY path_key")]


def _diff(old_records: list[dict[str, Any]], current_records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    old = {row["path_key"]: row for row in old_records}
    current = {row["path_key"]: row for row in current_records}
    changes: list[dict[str, Any]] = []
    counts = {"new": 0, "changed": 0, "missing": 0, "unchanged": 0}
    for key, row in current.items():
        before = old.get(key)
        if before is None or before.get("status") == "missing":
            row["change_type"] = "NEW" if before is None else "REAPPEARED"
            counts["new"] += 1
            changes.append({"change_type": row["change_type"], "relative_path": row["relative_path"], "project": row.get("project_name")})
        elif (int(before["size"]), str(before["modified"])) != (int(row["size"]), str(row["modified"])):
            row["change_type"] = "CHANGED"
            counts["changed"] += 1
            changes.append({"change_type": "CHANGED", "relative_path": row["relative_path"], "project": row.get("project_name")})
        else:
            row["fingerprint"] = before.get("fingerprint")
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
            changes.append({"change_type": "MISSING", "relative_path": missing["relative_path"], "project": missing.get("project_name")})
        elif key not in current:
            current_records.append(dict(before))
    changes.sort(key=lambda item: (item["change_type"], str(item["relative_path"]).casefold()))
    return changes, counts


def maintain(
    *,
    state_dir: str | Path | None = None,
    backend: str = "auto",
    everything_cli: str | Path | None = None,
    max_fingerprints: int = 2000,
) -> dict[str, Any]:
    state = resolve_state_dir(state_dir)
    baseline_path = state / BASELINE_NAME
    catalog_path = state / CATALOG_NAME
    if not baseline_path.is_file() or not catalog_path.is_file():
        raise FileIntelligenceError("No local baseline exists. Run explicit Deep Onboarding first.")
    baseline = read_json(baseline_path)
    if baseline.get("machine_binding") != machine_binding():
        raise FileIntelligenceError("Local state is bound to another machine. Run explicit Deep Onboarding or a reviewed rebind.")
    scopes = baseline.get("roots", [])
    snapshot = scan(scopes, state, backend, everything_cli)
    current = snapshot["records"]
    projects = _assign_projects(current, scopes)
    stamp = now_iso()
    with closing(_connect(catalog_path)) as connection:
        previous = _load_files(connection)
        changes, change_counts = _diff(previous, current)
        candidates = [row for row in current if row.get("change_type") in {"NEW", "REAPPEARED", "CHANGED"}]
        _fingerprint_candidates(candidates, max(0, max_fingerprints))
        for row in current:
            if row.get("change_type") == "UNCHANGED":
                continue
            match = next((candidate for candidate in candidates if candidate["path_key"] == row["path_key"]), None)
            if match:
                row["fingerprint"] = match.get("fingerprint")
        run_id = "run_" + uuid.uuid4().hex[:20]
        summary = {
            "schema_version": 1,
            "run_id": run_id,
            "mode": "MAINTENANCE",
            "status": "NO_OP" if not changes else "CHANGES_RECORDED",
            "generated_at": stamp,
            "baseline_id": baseline["baseline_id"],
            "backend": snapshot["backend"],
            "counts": change_counts,
            "catalog_counts": _counts(current, projects),
            "scope_errors": snapshot.get("errors", []),
            "warning": snapshot.get("warning"),
            "everything": snapshot["everything"],
            "physical_actions": 0,
        }
        _replace_catalog(connection, current, projects, stamp)
        _record_run(connection, summary)
        connection.commit()
    atomic_json(state / CHANGES_NAME, {**summary, "changes": changes})
    return summary


def status(*, state_dir: str | Path | None = None, everything_cli: str | Path | None = None) -> dict[str, Any]:
    state = resolve_state_dir(state_dir)
    baseline_path = state / BASELINE_NAME
    catalog_path = state / CATALOG_NAME
    capability = everything_status(everything_cli)
    if not baseline_path.is_file() or not catalog_path.is_file():
        return {
            "schema_version": 1,
            "status": "DEEP_ONBOARDING_REQUIRED",
            "baseline_present": False,
            "state_dir": str(state),
            "everything": capability,
            "real_execution_enabled": False,
        }
    baseline = read_json(baseline_path)
    binding_matches = baseline.get("machine_binding") == machine_binding()
    with closing(_connect(catalog_path)) as connection:
        counts = {
            "files": connection.execute("SELECT COUNT(*) FROM files WHERE status='present'").fetchone()[0],
            "projects": connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0],
            "fingerprints": connection.execute("SELECT COUNT(*) FROM files WHERE fingerprint IS NOT NULL").fetchone()[0],
            "communication_records": connection.execute("SELECT COUNT(*) FROM files WHERE is_communication=1 AND status='present'").fetchone()[0],
        }
    return {
        "schema_version": 1,
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
    return {"schema_version": 1, "projects": projects, "authority": "heuristic", "physical_actions": 0}


def last_changes(*, state_dir: str | Path | None = None) -> dict[str, Any]:
    state = resolve_state_dir(state_dir)
    path = state / CHANGES_NAME
    if not path.is_file():
        raise FileIntelligenceError("No maintenance result exists. Run Deep Onboarding first.")
    return read_json(path)
