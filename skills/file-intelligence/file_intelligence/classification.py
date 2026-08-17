from __future__ import annotations

import re
from pathlib import Path
from typing import Any


AGGREGATE_DIR_KINDS = {
    ".venv": "python_environment",
    "venv": "python_environment",
    "node_modules": "javascript_dependencies",
    "__pycache__": "python_cache",
    ".pytest_cache": "test_cache",
    ".mypy_cache": "typecheck_cache",
    ".ruff_cache": "lint_cache",
    "build": "build_output",
    "dist": "distribution_output",
    "cache": "cache",
    ".cache": "cache",
    "render": "render_output",
    "renders": "render_output",
    "preview": "preview_output",
    "previews": "preview_output",
    "tmp": "temporary_output",
    "temp": "temporary_output",
}


VOLATILE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("database_wal", re.compile(r"(?:^|[\\/]).+\.(?:wal|shm|journal)$", re.I)),
    ("lock_file", re.compile(r"(?:^|[\\/])(?:~\$|\.~lock\.|[^\\/]+\.(?:lock|lck))", re.I)),
    ("runtime_log", re.compile(r"(?:^|[\\/])(?:logs?|runtime|sessions?)(?:[\\/]|$)|\.(?:log|tmp)$", re.I)),
    ("browser_state", re.compile(r"(?:chrome|edge|firefox|browser).*(?:cache|history|cookies|session)", re.I)),
    ("communication_runtime", re.compile(r"(?:tencent|wechat|weixin|qq).*(?:wal|shm|log|cache|temp)", re.I)),
    ("codex_runtime", re.compile(r"(?:[\\/])\.codex(?:[\\/]).*(?:sessions?|logs?|tmp)(?:[\\/]|$)", re.I)),
    ("cloud_metadata", re.compile(r"(?:baidunetdisk|onedrive|dropbox).*(?:metadata|cache|sync|log)", re.I)),
)


ROLE_EXTENSIONS = {
    ".py": "code",
    ".m": "code",
    ".ps1": "code",
    ".bat": "code",
    ".cmd": "code",
    ".java": "code",
    ".ipynb": "code",
    ".mph": "working_model",
    ".cas": "working_model",
    ".cas.h5": "working_model",
    ".dat": "processed_data",
    ".dat.h5": "processed_data",
    ".msh": "raw_data",
    ".msh.h5": "raw_data",
    ".scdoc": "source",
    ".scdocx": "source",
    ".step": "source",
    ".stp": "source",
    ".csv": "processed_data",
    ".xlsx": "processed_data",
    ".docx": "documentation",
    ".pptx": "presentation",
    ".pdf": "report",
    ".md": "documentation",
    ".txt": "documentation",
    ".png": "figure",
    ".jpg": "figure",
    ".jpeg": "figure",
    ".svg": "figure",
    ".gif": "figure",
    ".mp4": "deliverable",
    ".avi": "deliverable",
    ".zip": "archive_candidate",
    ".7z": "archive_candidate",
}


def aggregate_kind(name: str) -> str | None:
    return AGGREGATE_DIR_KINDS.get(name.casefold())


def volatile_class(path: str | Path) -> tuple[bool, str | None, str | None]:
    text = str(path)
    for category, pattern in VOLATILE_PATTERNS:
        if pattern.search(text):
            return True, category, f"Path matches explainable volatile rule: {category}"
    return False, None, None


def infer_asset_role(path: str | Path, extension: str, extracted_text: str = "") -> dict[str, Any]:
    item = Path(path)
    lower_path = str(item).casefold()
    lower_name = item.name.casefold()
    parts = [part.casefold() for part in item.parts]
    tokens = set(token for token in re.split(r"[^a-z0-9\u4e00-\u9fff]+", lower_path) if token)
    text = extracted_text.casefold()
    evidence: list[dict[str, Any]] = []
    role = ROLE_EXTENSIONS.get(extension.casefold(), "unknown")
    confidence = 0.45 if role != "unknown" else 0.2
    authority = "UNKNOWN"

    if lower_name.startswith("readme") or "readme" in lower_name or "index" in lower_name:
        role, confidence, authority = "documentation", 0.9, "REFERENCE"
        evidence.append({"type": "structural_inference", "detail": "README/index naming indicates project documentation."})
    checkpoint_path = bool(tokens & {"checkpoint", "recovery", "autosave", "restart"})
    temporary_path = bool(tokens & {"tmp", "temp", "cache", "preview", "debug", "failed", "test_output"})
    historical_path = bool(tokens & {"archive", "historical", "history", "old", "legacy", "rejected"})
    if checkpoint_path:
        role = "recovery" if tokens & {"recovery", "restart"} else "checkpoint"
        confidence = max(confidence, 0.82)
        authority = "ACTIVE" if tokens & {"active", "recovery"} else "HISTORICAL"
        evidence.append({"type": "structural_inference", "detail": "Path contains checkpoint/recovery terminology."})
    if temporary_path:
        role, confidence, authority = "temporary", 0.84, "DERIVED"
        evidence.append({"type": "structural_inference", "detail": "Path contains temporary/test/debug terminology."})
    if historical_path:
        role = "rejected_route" if "rejected" in tokens else "historical_provenance"
        confidence = max(confidence, 0.76)
        authority = "HISTORICAL"
        evidence.append({"type": "structural_inference", "detail": "Path contains archive/history/rejected terminology."})
    raw_data_tokens = {"raw", "raw_data", "input", "inputs", "measurement", "measurements", "source_data"}
    if extension.casefold() in {".csv", ".xlsx", ".xls", ".mat", ".dat", ".h5"} and tokens & raw_data_tokens:
        role = "raw_data"
        confidence = max(confidence, 0.82)
        evidence.append({"type": "structural_inference", "detail": "Data path contains raw/input/measurement terminology."})
    if extension.casefold() == ".docx" and any(token in lower_path for token in ("paper", "manuscript", "journal", "稿")):
        role, confidence = "manuscript", 0.84
    final_named = bool(re.search(r"(?:^|[^a-z0-9])(final|official|submitted|accepted)(?:[^a-z0-9]|$)|正式|最终", lower_name, re.I))
    if final_named and not (checkpoint_path or temporary_path or historical_path):
        if extension.casefold() in {".docx", ".pdf"} and role in {"documentation", "report", "manuscript"}:
            role = "manuscript" if "paper" in lower_path or "manuscript" in lower_path or "journal" in lower_path else "final"
        elif extension.casefold() == ".pptx":
            role = "presentation"
        elif extension.casefold() in {".mph", ".cas", ".cas.h5"}:
            role = "canonical_model"
        else:
            role = "final"
        if extension.casefold() in {".docx", ".pptx", ".pdf", ".mph", ".cas", ".cas.h5"}:
            authority = "CANONICAL"
        elif extension.casefold() in {".log", ".tmp", ".stdout", ".stderr"} or any(part in {"log", "logs"} for part in parts):
            authority = "DERIVED"
        else:
            authority = "REFERENCE"
        confidence = max(confidence, 0.86)
        evidence.append({"type": "structural_inference", "detail": "Filename contains final/official/submitted terminology."})
    delivery_context = bool(tokens & {"final", "deliverable", "delivery", "handin", "handoff", "submission"})
    if delivery_context and extension.casefold() in {".docx", ".pptx", ".pdf"} and not (checkpoint_path or temporary_path or historical_path):
        if extension.casefold() == ".pptx":
            role = "presentation"
        elif extension.casefold() == ".docx" and tokens & {"paper", "manuscript", "journal", "论文", "稿件"}:
            role = "manuscript"
        else:
            role = "deliverable"
        authority = "CANONICAL"
        confidence = max(confidence, 0.84)
        evidence.append({"type": "structural_inference", "detail": "Asset is stored inside a final/deliverable/handoff context."})
    if not evidence:
        evidence.append({"type": "structural_inference", "detail": f"Role inferred from extension {extension or '[none]'}."})
    return {
        "role": role,
        "authority_level": authority,
        "confidence": round(confidence, 3),
        "evidence": evidence,
    }
