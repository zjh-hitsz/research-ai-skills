from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable


TEXT_SUFFIXES = {
    ".md", ".txt", ".py", ".ps1", ".yaml", ".yml", ".json", ".toml", ".ini", ".cfg", ".csv", "",
}
REAL_ARTIFACT_SUFFIXES = {
    ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".pdf", ".mph", ".cas", ".h5", ".scdoc", ".scdocx", ".zip", ".7z", ".rar",
}
MACHINE_STATE_FILENAMES = {
    "catalog.db", "catalog.sqlite", "baseline.json", "machine_identity.json", "production_state.json",
    "transaction_result.json", "rollback_manifest.json", "approval_request.json", "journal.jsonl", "last_changes.json",
    "events.jsonl", "snapshots.json", "semantic_summary.json", "project_understanding_summary.json",
    "file intelligence home.html", "computer timeline.html",
}
MACHINE_STATE_DIRECTORIES = {"state", "output", "sandbox", "state_backup", "production_baseline", "project_understanding", "migrations"}
PRIVATE_COMMUNICATION_DIRECTORIES = {"attachments", "communication_records", "chat_exports", "message_exports"}
IGNORED_DIRECTORIES = {".git", "__pycache__", ".pytest_cache"}

WINDOWS_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/](?:[^\s<>:\"|?*]+[\\/])*[^\s<>:\"|?*]*")
EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w.-])")
CREDENTIAL = re.compile(r"(?i)(?:api[_-]?key|password|secret|access[_-]?token)\s*[:=]\s*['\"][^'\"\s]{8,}['\"]")
KNOWN_TOKEN = re.compile(r"(?:ghp_|github_pat_|sk-)[A-Za-z0-9_-]{16,}")
WXID = re.compile(r"(?i)wxid_[A-Za-z0-9_-]{5,}")
QQ_FIELD = re.compile(r"(?i)['\"]?qq[_-]?id['\"]?\s*[:=]\s*['\"]?[1-9][0-9]{4,11}['\"]?")


def _text_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if not path.is_file() or any(part.casefold() in IGNORED_DIRECTORIES for part in path.parts):
            continue
        if path.suffix.casefold() in TEXT_SUFFIXES or path.name in {"VERSION"}:
            yield path


def audit_package(root: Path, denylist: Iterable[str] = ()) -> dict[str, Any]:
    root = root.resolve()
    findings: dict[str, list[dict[str, str]]] = {
        "personal_paths": [],
        "credentials": [],
        "real_project_artifacts": [],
        "machine_state": [],
        "private_communication_records": [],
    }
    for path in root.rglob("*"):
        if not path.is_file() or any(part.casefold() in IGNORED_DIRECTORIES for part in path.parts):
            continue
        relative = path.relative_to(root)
        parts = {part.casefold() for part in relative.parts[:-1]}
        suffix = path.suffix.casefold()
        name = path.name.casefold()
        if suffix in REAL_ARTIFACT_SUFFIXES:
            findings["real_project_artifacts"].append({"path": str(relative), "reason": "project artifact file type"})
        if name in MACHINE_STATE_FILENAMES or parts & MACHINE_STATE_DIRECTORIES or suffix in {".db", ".sqlite", ".sqlite3", ".wal", ".shm"}:
            findings["machine_state"].append({"path": str(relative), "reason": "machine state path or file type"})
        if parts & PRIVATE_COMMUNICATION_DIRECTORIES:
            findings["private_communication_records"].append({"path": str(relative), "reason": "private communication record directory"})
    normalized_denylist = [value.casefold() for value in denylist if value.strip()]
    for path in _text_files(root):
        relative = str(path.relative_to(root))
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            findings["real_project_artifacts"].append({"path": relative, "reason": "unexpected binary content in text file"})
            continue
        for match in WINDOWS_PATH.finditer(text):
            findings["personal_paths"].append({"path": relative, "reason": match.group(0)})
        for expression, reason in ((EMAIL, "email address"), (CREDENTIAL, "credential assignment"), (KNOWN_TOKEN, "credential-like token")):
            if expression.search(text):
                findings["credentials"].append({"path": relative, "reason": reason})
        for expression, reason in ((WXID, "account identifier"), (QQ_FIELD, "account identifier")):
            if expression.search(text):
                findings["private_communication_records"].append({"path": relative, "reason": reason})
        lowered = text.casefold()
        for marker in normalized_denylist:
            if marker in lowered:
                findings["real_project_artifacts"].append({"path": relative, "reason": "release-specific private marker"})
    counts = {key: len(value) for key, value in findings.items()}
    return {"root": str(root), "counts": counts, "findings": findings, "pass": not any(counts.values())}


def render_markdown(result: dict[str, Any]) -> str:
    counts = result["counts"]
    lines = [
        "# File Intelligence Public Release Audit",
        "",
        "Audit scope: exact public Skill staging tree.",
        "",
        f"- Personal absolute paths = {counts['personal_paths']}",
        f"- Credentials = {counts['credentials']}",
        f"- Real project artifacts = {counts['real_project_artifacts']}",
        f"- Machine state = {counts['machine_state']}",
        f"- Private communication records = {counts['private_communication_records']}",
        "",
    ]
    if result["pass"]:
        lines.extend([
            "Result: **PASS**",
            "",
            "The package contains only portable engine code, generic rules and schemas, public documentation, installation/update helpers, and synthetic tests.",
        ])
    else:
        lines.extend(["Result: **FAIL**", "", "## Findings", ""])
        for category, items in result["findings"].items():
            for item in items:
                lines.append(f"- `{category}`: `{item['path']}` — {item['reason']}")
    return "\n".join(lines) + "\n"
