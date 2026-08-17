from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .classification import infer_asset_role
from .fingerprints import full_sha256, staged_sample_fingerprint
from .inspectors import inspect_file
from .paths import path_key
from .timeline import append_event


DOMAIN_TERMS = {
    "steg": "STEG",
    "thermoelectric": "thermoelectric",
    "热电": "thermoelectric",
    "cold plate": "cold-plate",
    "cold-plate": "cold-plate",
    "冷板": "cold-plate",
    "comsol": "COMSOL simulation",
    "fluent": "Fluent/CFD",
    "cfd": "CFD",
    "topology optimization": "topology optimization",
    "拓扑优化": "topology optimization",
    "heat transfer": "heat-transfer",
    "传热": "heat-transfer",
}
WORKSTREAM_HINTS = re.compile(
    r"journal|paper|manuscript|rebuild|extension|conference|oral|poster|set\d{4}|experiment|simulation|"
    r"analysis|revision|submission|response|会议|论文|实验|仿真|重建|扩展",
    re.I,
)
GENERIC_WORKSTREAM_DIRS = {
    "src", "code", "data", "docs", "figures", "results", "output", "inputs", "scripts", "models",
    "readme", "management", "archive", "temp", "tmp", "render", "cache",
}
PROVENANCE_CONTAINER_HINTS = {"audit", "archive", "backup", "history", "historical", "transfer_audit"}
DELIVERABLE_CONTAINER_HINTS = {"handin", "handoff", "deliverable", "delivery", "submission"}
INSPECTABLE_EXTENSIONS = {
    ".md", ".txt", ".json", ".yaml", ".yml", ".xml", ".py", ".ps1", ".bat", ".cmd",
    ".java", ".ipynb", ".csv", ".m", ".docx", ".pptx", ".pdf", ".zip", ".mph",
    ".cas.h5", ".dat.h5", ".msh.h5", ".h5", ".mat", ".scdocx",
}
HIGH_AUTHORITY_WORDS = re.compile(r"final|canonical|official|authoritative|source.of.truth|submitted|accepted|正式|最终|权威", re.I)
VERSION_WORDS = re.compile(
    r"(?:^|[_\-. ])(?:v(?:er(?:sion)?)?\d+(?:\.\d+)*|rev\d+|r\d+|20\d{6}|copy\s*\d*|副本)(?:$|[_\-. ])",
    re.I,
)
FILENAME_TOKEN_RE = re.compile(
    r"(?<![\w])([\w\-+.()\u4e00-\u9fff]{3,}\.(?:mph|cas(?:\.h5)?|dat(?:\.h5)?|msh(?:\.h5)?|docx|pptx|pdf|"
    r"json|ya?ml|csv|xlsx|py|m|java|md|txt|zip|scdocx?|step|stp))(?![\w])",
    re.I,
)
SUPERSESSION_RE = re.compile(r"(?i)superseded\s+by|replaced\s+by|replaces?|取代|替代")
GATE_SCOPE_TOKENS = {"gate", "checkpoint", "iteration", "iter", "phase", "stage", "validation", "verify", "audit", "交付门", "检查点"}
IMPORTANT_AUTHORITIES = {"PRIMARY", "CANONICAL", "ACTIVE"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _key(path: str | Path) -> str:
    return path_key(path)


def _id(prefix: str, *parts: str) -> str:
    return prefix + "_" + hashlib.sha256("|".join(parts).encode("utf-8", errors="replace")).hexdigest()[:20]


def _evidence(evidence_type: str, detail: str, source_path: str | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"type": evidence_type, "detail": detail}
    if source_path:
        item["source_path"] = source_path
    return item


def _clean_workstream_name(name: str, project_name: str) -> str:
    value = re.sub(r"[_\-.]+", " ", name).strip()
    value = re.sub(r"\b20\d{6}\b", "", value).strip()
    value = re.sub(r"\s+", " ", value)
    if value.casefold().startswith(project_name.casefold() + " "):
        value = value[len(project_name) :].strip()
    return value or name


def _inspection_priority(row: dict[str, Any]) -> tuple[Any, ...]:
    name = row["filename"].casefold()
    relative = row["relative_path"].casefold()
    extension = row["extension"].casefold()
    return (
        not name.startswith("readme"),
        relative.count("\\") + relative.count("/"),
        not any(token in name for token in ("manifest", "index", "status", "report", "deliverable", "decision")),
        extension not in {".md", ".txt", ".json", ".yaml", ".yml"},
        extension not in {".docx", ".pptx", ".pdf"},
        "archive" in relative or "histor" in relative,
        int(row["size"]),
        row["path_key"],
    )


def _choose_inspections(records: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    candidates = [row for row in records if row["extension"].casefold() in INSPECTABLE_EXTENSIONS or row["filename"].casefold().startswith("readme")]
    return sorted(candidates, key=_inspection_priority)[: max(0, limit)]


def _relative_parts(path: Path, root: Path) -> tuple[str, ...]:
    try:
        return path.relative_to(root).parts
    except ValueError:
        return ()


def _workstreams(
    records: list[dict[str, Any]],
    project_root: Path,
    project_name: str,
    document_texts: list[tuple[str, str]],
) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        parts = _relative_parts(Path(row["path"]), project_root)
        if len(parts) > 1:
            buckets[parts[0]].append(row)
    total = max(1, len(records))
    results: list[dict[str, Any]] = []
    for folder, members in buckets.items():
        normalized = re.sub(r"^\d+[_\-. ]*", "", folder).casefold()
        folder_tokens = set(token for token in re.split(r"[^a-z0-9\u4e00-\u9fff]+", normalized) if token)
        if folder_tokens & (PROVENANCE_CONTAINER_HINTS | DELIVERABLE_CONTAINER_HINTS):
            continue
        semantic = bool(WORKSTREAM_HINTS.search(folder))
        substantial = len(members) >= max(4, int(total * 0.04))
        if normalized in GENERIC_WORKSTREAM_DIRS or (not semantic and not substantial):
            continue
        display = _clean_workstream_name(folder, project_name)
        names = " ".join(row["filename"].casefold() for row in members)
        workstream_root = (project_root / folder).resolve()
        local_text = "\n".join(
            text for source, text in document_texts
            if _key(source).startswith(_key(workstream_root).rstrip("\\/") + "\\")
            and (
                Path(source).name.casefold().startswith("readme")
                or Path(source).name.casefold() in {"current_status.md", "project_status.md", "status.md", "final_deliverable_index.md"}
            )
        )
        root_context = "\n".join(
            text for source, text in document_texts
            if len(_relative_parts(Path(source), project_root)) == 1
            and Path(source).name.casefold() in {"readme.md", "current_status.md", "project_status.md", "status.md"}
            and (folder.casefold() in text.casefold() or display.casefold() in text.casefold())
        )
        if root_context:
            local_text = f"{local_text}\n{root_context}"
        combined = f"{folder}\n{local_text}".casefold()
        scores = Counter()
        folder_lower = folder.casefold()
        if any(token in folder_lower for token in ("active", "current", "rebuild", "recovery", "进行中", "当前")):
            scores["ACTIVE"] += 6
        if any(token in folder_lower for token in ("completed", "submitted", "accepted", "已完成", "定稿")):
            scores["COMPLETED"] += 7
        if any(token in folder_lower for token in ("frozen", "conference", "oral", "poster", "冻结")):
            scores["FROZEN"] += 6
        if any(token in folder_lower for token in ("historical", "archive", "legacy", "rejected", "历史", "废弃")):
            scores["HISTORICAL"] += 7
        explicit_status = re.findall(
            r"(?im)(?:status|lifecycle|state|状态|阶段)\s*[:=：-]\s*\**(active|completed|complete|frozen|historical|blocked|preflight_blocked|进行中|已完成|冻结|历史|阻塞)",
            local_text,
        )
        for status in explicit_status:
            mapped = {
                "active": "ACTIVE", "进行中": "ACTIVE", "completed": "COMPLETED", "complete": "COMPLETED",
                "已完成": "COMPLETED", "frozen": "FROZEN", "冻结": "FROZEN", "historical": "HISTORICAL", "历史": "HISTORICAL",
                "blocked": "BLOCKED", "preflight_blocked": "BLOCKED", "阻塞": "BLOCKED",
            }[status.casefold()]
            scores[mapped] += 10
        if re.search(r"(?i)active\s+phase|当前(?:活跃|阶段).{0,30}(?:active|进行)", local_text):
            scores["ACTIVE"] += 12
        if re.search(r"(?i)\b(?:is|remains|currently)\s+active\b|\bactive\s+(?:work|research|development)\b|进行中", local_text):
            scores["ACTIVE"] += 10
        final_count = len(re.findall(r"final|official|submitted|正式|最终", names))
        if final_count >= 2:
            scores["COMPLETED"] += 2
        if "extension" in folder_lower and final_count >= 2:
            scores["COMPLETED"] += 3
        if any(token in folder_lower for token in ("oral", "conference", "poster")) and "final" in folder_lower:
            scores["FROZEN"] += 4
        has_final_delivery = any(
            any(part.casefold() in {"final_delivery", "final deliverable", "final_deliverable", "06_final_delivery"} for part in _relative_parts(Path(row["path"]), workstream_root)[:-1])
            or (
                len(_relative_parts(Path(row["path"]), workstream_root)) <= 2
                and bool(re.search(r"(?i)^final.*report|final_deliverable_index", row["filename"]))
            )
            for row in members
        )
        if has_final_delivery:
            scores["COMPLETED"] += 12
        lifecycle = scores.most_common(1)[0][0] if scores else "UNKNOWN"
        confidence = min(0.94, 0.52 + 0.08 * (scores[lifecycle] if scores else 0) + (0.08 if semantic else 0))
        evidence = [
            _evidence("structural_inference", f"Top-level cluster contains {len(members)} files and {sum(int(row['size']) for row in members)} bytes.", str(project_root / folder))
        ]
        if scores:
            evidence.append(_evidence("document_evidence", f"Lifecycle keyword scores: {dict(scores)}."))
        results.append(
            {
                "workstream_id": _id("workstream", _key(project_root), folder.casefold()),
                "name": display,
                "path": str(project_root / folder),
                "lifecycle": lifecycle,
                "confidence": round(confidence, 3),
                "evidence": evidence,
                "file_count": len(members),
                "total_size": sum(int(row["size"]) for row in members),
            }
        )
    return sorted(results, key=lambda item: item["name"].casefold())


def _project_lifecycle(
    workstreams: list[dict[str, Any]],
    document_texts: list[tuple[str, str]],
    project_root: Path,
) -> tuple[str, list[dict[str, Any]]]:
    established = {item["lifecycle"] for item in workstreams if item["lifecycle"] != "UNKNOWN"}
    evidence: list[dict[str, Any]] = []
    if len(established) >= 2:
        return "MIXED", evidence
    if len(workstreams) < 2 or len(established) < 2:
        root_status_texts = [
            (source, text) for source, text in document_texts
            if len(_relative_parts(Path(source), project_root)) == 1
            and Path(source).name.casefold() in {"readme.md", "current_status.md", "project_status.md", "status.md"}
        ]
        status_set: set[str] = set()
        for source, text in root_status_texts:
            lowered = text.casefold()
            if re.search(r"\b(?:active|ongoing|in progress)\b|进行中|当前.*(?:工作|研究)", lowered):
                status_set.add("ACTIVE")
            if re.search(r"\b(?:completed?|finished|finalized|submitted|accepted)\b|已完成|最终交付", lowered):
                status_set.add("COMPLETED")
            if re.search(r"\b(?:frozen|locked)\b|冻结", lowered):
                status_set.add("FROZEN")
            if status_set:
                evidence.append(_evidence("document_evidence", f"Root status document signals {sorted(status_set)}.", source))
        established.update(status_set)
    if len(established) > 1:
        return "MIXED", evidence
    if established:
        return next(iter(established)), evidence
    return "UNKNOWN", evidence


def _purpose(project_name: str, texts: list[tuple[str, str]], project_root: Path) -> tuple[str, list[dict[str, Any]], float]:
    excluded_evidence_parts = {"literature", "papers", "theses", "patents", "official_ansys", "browser_profile"}
    preferred_texts = [
        (source, text) for source, text in texts
        if not excluded_evidence_parts.intersection(part.casefold() for part in _relative_parts(Path(source), project_root))
    ]
    preferred_texts.sort(
        key=lambda item: (
            not Path(item[0]).name.casefold().startswith("readme"),
            len(_relative_parts(Path(item[0]), project_root)),
            Path(item[0]).name.casefold(),
        )
    )
    evidence_texts = preferred_texts or texts
    combined = "\n".join(text[:200_000] for _, text in evidence_texts).casefold()
    terms: list[str] = []
    sources: list[str] = []
    for needle, label in DOMAIN_TERMS.items():
        if needle in combined and label not in terms:
            terms.append(label)
            for source, text in evidence_texts:
                if needle in text.casefold():
                    sources.append(source)
                    break
    title = None
    title_source = None
    for source, text in evidence_texts:
        for line in text.splitlines()[:80]:
            stripped = line.strip().lstrip("#").strip()
            if 5 <= len(stripped) <= 180 and not stripped.startswith(("{", "[", "|")):
                title, title_source = stripped, source
                break
        if title:
            break
    if terms:
        summary = f"{project_name}: " + " / ".join(terms[:5]) + " research project"
        confidence = min(0.92, 0.62 + 0.06 * len(terms))
    elif title:
        summary = title
        confidence = 0.58
    else:
        summary = f"{project_name} project; purpose not yet established"
        confidence = 0.25
    evidence = [_evidence("document_evidence", f"Domain terms found: {', '.join(terms)}.", sources[0] if sources else title_source)] if terms else []
    if title:
        evidence.append(_evidence("document_evidence", f"Representative document heading: {title}", title_source))
    if not evidence:
        evidence.append(_evidence("structural_inference", "No sufficiently specific purpose statement was found."))
    return summary, evidence, round(confidence, 3)


def _tool_centrality(
    records: list[dict[str, Any]], texts: list[tuple[str, str]], project_root: Path,
) -> list[dict[str, Any]]:
    definitions = {
        "Fluent": {"extensions": {".cas", ".cas.h5", ".dat", ".dat.h5", ".msh", ".msh.h5"}, "terms": ("fluent", "ansys fluent")},
        "COMSOL": {"extensions": {".mph"}, "terms": ("comsol",)},
        "SpaceClaim": {"extensions": {".scdoc", ".scdocx"}, "terms": ("spaceclaim",)},
        "Python": {"extensions": {".py", ".ipynb"}, "terms": ("python",)},
        "MATLAB": {"extensions": {".m", ".mat"}, "terms": ("matlab",)},
    }
    scored: list[dict[str, Any]] = []
    reference_tokens = {"reference", "references", "literature", "papers", "evidence", "supporting", "background", "参考", "文献"}
    for tool, definition in definitions.items():
        native = [row for row in records if row["extension"].casefold() in definition["extensions"]]
        primary_native = [
            row for row in native
            if not ({token for part in _relative_parts(Path(row["path"]), project_root) for token in re.split(r"[^a-z0-9\u4e00-\u9fff]+", part.casefold()) if token} & reference_tokens)
        ]
        reference_native = [row for row in native if row not in primary_native]
        score = min(40.0, len(primary_native) * 4.0 + len(reference_native) * 0.5 + sum(min(12.0, int(row["size"]) / (512 * 1024**2)) for row in primary_native))
        evidence: list[dict[str, Any]] = []
        if native:
            evidence.append(_evidence(
                "structural_inference",
                f"{len(primary_native)} primary-context and {len(reference_native)} reference-context native {tool} asset(s) are catalogued.",
            ))
        primary_mentions = 0
        supporting_mentions = 0
        reference_mentions = 0
        for source, text in texts:
            parts = {
                token for part in _relative_parts(Path(source), project_root)
                for token in re.split(r"[^a-z0-9\u4e00-\u9fff]+", part.casefold()) if token
            }
            source_reference = bool(parts & reference_tokens)
            for line in text.splitlines():
                lowered = line.casefold()
                mentions = sum(lowered.count(term) for term in definition["terms"])
                if not mentions:
                    continue
                reference_context = source_reference or bool(re.search(
                    r"(?i)reference|supporting|background|golden|example|comparison|not\s+(?:the\s+)?(?:main|primary)|reference\s+only|参考|文献|辅助|仅为",
                    line,
                ))
                primary_context = bool(re.search(
                    rf"(?i)(primary|main|production|authoritative|主要求解|主链).{{0,40}}{re.escape(tool)}|{re.escape(tool)}.{{0,40}}(primary|main|production|authoritative|主要求解|主链)",
                    line,
                ))
                if reference_context:
                    reference_mentions += mentions
                elif primary_context:
                    primary_mentions += mentions
                else:
                    supporting_mentions += mentions
        score += min(35.0, primary_mentions * 8.0) + min(12.0, supporting_mentions * 0.5) + min(3.0, reference_mentions * 0.1)
        if primary_mentions:
            evidence.append(_evidence("explicit_project_metadata", f"Primary-tool language appears {primary_mentions} time(s)."))
        if supporting_mentions:
            evidence.append(_evidence("document_evidence", f"Non-reference project documents mention {tool} {supporting_mentions} time(s)."))
        if reference_mentions:
            evidence.append(_evidence("document_evidence", f"Reference/supporting evidence mentions {tool} {reference_mentions} time(s)."))
        if score > 0:
            scored.append({
                "tool_name": tool, "score": round(score, 3), "evidence": evidence,
                "native_assets": len(native), "primary_native_assets": len(primary_native),
                "reference_native_assets": len(reference_native), "primary_mentions": primary_mentions,
                "reference_mentions": reference_mentions,
            })
    if not scored:
        return []
    scored.sort(key=lambda item: (-item["score"], item["tool_name"]))
    top_score = scored[0]["score"]
    for index, item in enumerate(scored):
        if index == 0 and (item["native_assets"] or item["score"] >= 8):
            item["centrality"] = "PRIMARY_TOOL"
        elif (
            item["primary_native_assets"]
            or item["primary_mentions"] >= max(2, item["reference_mentions"] * 0.25)
        ) and item["score"] >= max(6.0, top_score * 0.35):
            item["centrality"] = "SECONDARY_TOOL"
        else:
            item["centrality"] = "REFERENCE_TOOL"
    return scored


def _authority_scope(
    asset_path: Path, project_root: Path, workstream_id: str | None, *, source_path: Path | None = None,
) -> tuple[str, str | None]:
    parts = {token for part in _relative_parts(asset_path, project_root) for token in re.split(r"[^a-z0-9\u4e00-\u9fff]+", part.casefold()) if token}
    if parts & GATE_SCOPE_TOKENS:
        gate = next(iter(sorted(parts & GATE_SCOPE_TOKENS)), "gate")
        return "GATE_LOCAL", _id("gate", _key(project_root), gate)
    if workstream_id:
        return "WORKSTREAM_LOCAL", workstream_id
    if source_path and len(_relative_parts(source_path, project_root)) == 1:
        return "PROJECT_WIDE", _id("node", _id("project", _key(project_root)), "project")
    return "FILE_LOCAL", None


def _family_key(row: dict[str, Any]) -> str:
    stem = Path(row["filename"]).stem.casefold()
    stem = HIGH_AUTHORITY_WORDS.sub("", stem)
    stem = VERSION_WORDS.sub(" ", stem)
    if row["extension"].casefold() in {".mph", ".cas", ".cas.h5", ".dat", ".dat.h5", ".msh", ".msh.h5"}:
        stem = re.sub(r"(?:^|[_\-. ])(?:iter(?:ation)?\d+|tol[0-9ep+.-]+|r\d+(?:p\d+)?|solved|refined|validated)(?:$|[_\-. ])", " ", stem, flags=re.I)
    stem = re.sub(r"[_\-. ()]+", " ", stem).strip()
    if len(stem) < 6 or stem in {"readme", "index", "report", "manifest", "config", "output", "result", "model", "data", "run", "status"}:
        return f"unique|{row['path_key']}"
    return f"{row['extension'].casefold()}|{stem}"


def _workstream_for(path: Path, workstreams: list[dict[str, Any]]) -> str | None:
    for item in workstreams:
        try:
            path.relative_to(Path(item["path"]))
            return item["workstream_id"]
        except ValueError:
            continue
    return None


def _load_assertions(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute("SELECT * FROM user_assertions WHERE active=1 ORDER BY updated_at")]


def _apply_assertions(
    assets: dict[str, dict[str, Any]],
    project: dict[str, Any],
    assertions: list[dict[str, Any]],
) -> None:
    for assertion in assertions:
        value = json.loads(assertion["value_json"])
        if assertion["subject_type"] == "asset" and assertion["subject_key"] in assets:
            asset = assets[assertion["subject_key"]]
            if assertion["predicate"] in {"role", "authority_level", "authority_scope", "archive_recommendation", "rebuildability", "workstream_id"}:
                asset[assertion["predicate"]] = value
                asset["confidence"] = 1.0
                asset["provenance_type"] = "explicit_user"
                asset["evidence"].append(_evidence("explicit_user", f"User assertion sets {assertion['predicate']}={value}."))
        if assertion["subject_type"] == "project" and assertion["subject_key"] == project["project_id"]:
            if assertion["predicate"] in {"purpose", "lifecycle", "name"}:
                project[assertion["predicate"]] = value
                project["confidence"] = 1.0
                project["evidence"].append(_evidence("explicit_user", f"User assertion sets {assertion['predicate']}={value}."))


def understand_project(
    connection: sqlite3.Connection,
    project_root: Path,
    *,
    max_inspections: int = 400,
    max_stage1_hashes: int = 1200,
    max_full_hashes: int = 32,
    content_exclude_patterns: Iterable[str] = (),
    commit: bool = True,
) -> dict[str, Any]:
    """Build a deterministic, evidence-carrying Project Understanding graph."""
    root = project_root.resolve()
    root_key = _key(root)
    all_records = [dict(row) for row in connection.execute("SELECT * FROM files WHERE status='present' ORDER BY path_key")]
    records = [row for row in all_records if _key(row["path"]) == root_key or _key(row["path"]).startswith(root_key.rstrip("\\/") + "\\")]
    if not records:
        raise ValueError(f"No catalog records exist below project root: {root}")
    project_name = root.name
    project_id = _id("project", root_key)
    previous_project = connection.execute("SELECT * FROM projects WHERE project_id=? AND status='understood'", (project_id,)).fetchone()
    previous_assets = {row["path_key"]: dict(row) for row in connection.execute("SELECT * FROM assets WHERE project_id=?", (project_id,))}
    previous_dependencies = {row["edge_id"]: dict(row) for row in connection.execute("SELECT * FROM dependencies WHERE project_id=?", (project_id,))}
    previous_workstreams = {
        row["node_id"]: dict(row)
        for row in connection.execute("SELECT * FROM project_nodes WHERE project_id=? AND node_type='workstream'", (project_id,))
    }
    previous_duplicate_count = int(connection.execute(
        "SELECT COUNT(*) FROM identity_events WHERE run_id=? AND event_type='DUPLICATE'", (f"understand:{project_id}",)
    ).fetchone()[0])
    aggregate_records = [
        dict(row)
        for row in connection.execute("SELECT * FROM aggregate_nodes ORDER BY path")
        if _key(row["path"]) == root_key or _key(row["path"]).startswith(root_key.rstrip("\\/") + "\\")
    ]
    excluded = [re.compile(pattern, re.I) for pattern in content_exclude_patterns]
    inspection_rows = [row for row in _choose_inspections(records, max_inspections) if not any(pattern.search(row["relative_path"]) for pattern in excluded)]
    inspections: dict[str, dict[str, Any]] = {}
    document_texts: list[tuple[str, str]] = []
    dependency_rows: list[dict[str, Any]] = []
    declarations: list[tuple[dict[str, Any], dict[str, Any]]] = []
    stamp = _now()
    for row in inspection_rows:
        result = inspect_file(Path(row["path"]), root)
        inspections[row["path_key"]] = result
        if result.get("text"):
            document_texts.append((row["path"], result["text"]))
        for reference in result.get("references", []):
            dependency_rows.append({"source": row, **reference})
        for declaration in result.get("authority_declarations", []):
            declarations.append((row, declaration))
    workstreams = _workstreams(records, root, project_name, document_texts)
    purpose, purpose_evidence, purpose_confidence = _purpose(project_name, document_texts, root)
    tool_roles = _tool_centrality(records, document_texts, root)
    lifecycle, lifecycle_evidence = _project_lifecycle(workstreams, document_texts, root)
    project = {
        "project_id": project_id,
        "name": project_name,
        "root_path": str(root),
        "purpose": purpose,
        "lifecycle": lifecycle,
        "confidence": purpose_confidence,
        "evidence": purpose_evidence + lifecycle_evidence,
        "file_count": len(records) + sum(int(row["file_count"]) for row in aggregate_records),
        "total_size": sum(int(row["size"]) for row in records) + sum(int(row["total_size"]) for row in aggregate_records),
        "individually_indexed_files": len(records),
        "aggregate_nodes": len(aggregate_records),
        "aggregate_files": sum(int(row["file_count"]) for row in aggregate_records),
    }
    assets: dict[str, dict[str, Any]] = {}
    for row in records:
        text = inspections.get(row["path_key"], {}).get("text", "")
        inference = infer_asset_role(row["relative_path"], row["extension"], text)
        workstream_id = _workstream_for(Path(row["path"]), workstreams)
        authority_scope, authority_context = _authority_scope(Path(row["path"]), root, workstream_id)
        assets[row["path_key"]] = {
            "path_key": row["path_key"],
            "path": row["path"],
            "project_id": project_id,
            "workstream_id": workstream_id,
            "role": inference["role"],
            "authority_level": inference["authority_level"],
            "confidence": inference["confidence"],
            "evidence": inference["evidence"],
            "provenance_type": inference["evidence"][0]["type"],
            "rebuildability": "likely" if inference["role"] in {"temporary", "cache", "intermediate", "derived"} else "unknown",
            "archive_recommendation": "NO_RECOMMENDATION",
            "superseded_by_path_key": None,
            "asset_kind": "file",
            "entity_path": row["path"],
            "authority_scope": authority_scope,
            "authority_context_id": authority_context,
        }
    path_lookup = {_key(row["path"]): row["path_key"] for row in records}
    directory_lookup: dict[str, str] = {}
    for row in records:
        current = Path(row["path"]).parent
        while current != root and _key(current).startswith(root_key.rstrip("\\/") + "\\"):
            directory_lookup[_key(current)] = str(current)
            current = current.parent
    path_lookup.update({key: key for key in directory_lookup})
    filename_lookup: dict[str, list[str]] = defaultdict(list)
    record_lookup = {row["path_key"]: row for row in records}
    for row in records:
        filename_lookup[row["filename"].casefold()].append(row["path_key"])
    for source, declaration in declarations:
        targets = [path_lookup.get(_key(target)) for target in declaration["targets"]]
        if not any(targets):
            excerpt = declaration["excerpt"].casefold()
            for token in FILENAME_TOKEN_RE.findall(excerpt):
                keys = filename_lookup.get(Path(token).name.casefold(), [])
                if len(keys) == 1:
                    targets.extend(keys)
        for target_key in {target for target in targets if target}:
            if target_key not in assets and target_key in directory_lookup:
                directory_path = Path(directory_lookup[target_key])
                workstream_id = _workstream_for(directory_path, workstreams)
                authority_scope, authority_context = _authority_scope(directory_path, root, workstream_id, source_path=Path(source["path"]))
                assets[target_key] = {
                    "path_key": target_key, "path": str(directory_path), "project_id": project_id,
                    "workstream_id": workstream_id, "role": "asset_group", "authority_level": "UNKNOWN",
                    "confidence": 0.5, "evidence": [_evidence("structural_inference", "Directory authority entity created from an explicit declaration.")],
                    "provenance_type": "structural_inference", "rebuildability": "unknown",
                    "archive_recommendation": "NO_RECOMMENDATION", "superseded_by_path_key": None,
                    "asset_kind": "directory", "entity_path": str(directory_path),
                    "authority_scope": authority_scope, "authority_context_id": authority_context,
                }
            asset = assets[target_key]
            asset["authority_level"] = "CANONICAL"
            if asset["role"] == "working_model":
                asset["role"] = "canonical_model"
            asset["confidence"] = max(asset["confidence"], declaration["confidence"])
            asset["provenance_type"] = "explicit_project_metadata"
            asset["evidence"].append(
                _evidence("explicit_project_metadata", declaration["excerpt"], source["path"])
            )
            asset["archive_recommendation"] = "KEEP_AUTHORITY"
            asset["authority_scope"], asset["authority_context_id"] = _authority_scope(
                Path(asset["path"]), root, asset.get("workstream_id"), source_path=Path(source["path"]),
            )
            dependency_rows.append(
                {
                    "source": source,
                    "target_text": next((target for target in declaration["targets"] if path_lookup.get(_key(target)) == target_key), assets[target_key]["path"]),
                    "resolved_path": assets[target_key]["path"],
                    "resolution_status": "resolved_existing",
                    "line_no": declaration["line_no"],
                    "relation": "DECLARES_AUTHORITY",
                    "evidence_type": "explicit_project_metadata",
                    "confidence": declaration["confidence"],
                }
            )
    explicit_supersessions: set[tuple[str, str]] = set()
    for source in inspection_rows:
        result = inspections.get(source["path_key"], {})
        references_by_line: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for reference in result.get("references", []):
            references_by_line[int(reference["line_no"])].append(reference)
        for line_no, line in enumerate(result.get("text", "").splitlines(), 1):
            if not SUPERSESSION_RE.search(line):
                continue
            resolved_keys = [
                path_lookup.get(_key(reference["resolved_path"]))
                for reference in references_by_line.get(line_no, [])
                if reference.get("resolved_path")
            ]
            resolved_keys = [key for key in resolved_keys if key]
            if len(resolved_keys) >= 2:
                predecessor_key, successor_key = resolved_keys[0], resolved_keys[-1]
            elif len(resolved_keys) == 1 and (
                assets[source["path_key"]]["role"] in {"historical_provenance", "rejected_route"}
                or re.search(r"(?i)draft|old|legacy|historical|archive|草稿|历史", source["relative_path"])
            ):
                predecessor_key, successor_key = source["path_key"], resolved_keys[0]
            else:
                continue
            if predecessor_key == successor_key or (predecessor_key, successor_key) in explicit_supersessions:
                continue
            explicit_supersessions.add((predecessor_key, successor_key))
            predecessor = assets[predecessor_key]
            if predecessor["authority_level"] not in {"CANONICAL", "PRIMARY", "ACTIVE"}:
                predecessor["superseded_by_path_key"] = successor_key
                predecessor["archive_recommendation"] = "REVIEW_SUPERSEDED"
                predecessor["confidence"] = max(predecessor["confidence"], 0.97)
                predecessor["evidence"].append(
                    _evidence("explicit_project_metadata", line.strip()[:500], source["path"])
                )
            dependency_rows.append(
                {
                    "source": record_lookup[predecessor_key],
                    "target_text": assets[successor_key]["path"],
                    "resolved_path": assets[successor_key]["path"],
                    "resolution_status": "resolved_existing",
                    "line_no": line_no,
                    "relation": "SUPERSEDED_BY",
                    "evidence_type": "explicit_project_metadata",
                    "confidence": 0.97,
                }
            )
    families: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        if row["extension"]:
            families[_family_key(row)].append(row)
    for family in families.values():
        if len(family) < 2:
            continue
        ranked = sorted(
            family,
            key=lambda row: (
                assets[row["path_key"]]["authority_level"] in {"CANONICAL", "PRIMARY", "ACTIVE"},
                bool(HIGH_AUTHORITY_WORDS.search(row["filename"])),
                str(row["modified"]),
            ),
            reverse=True,
        )
        authority = ranked[0]
        for candidate in ranked[1:]:
            if candidate["path_key"] == authority["path_key"]:
                continue
            asset = assets[candidate["path_key"]]
            authority_asset = assets[authority["path_key"]]
            same_parent = Path(candidate["path"]).parent == Path(authority["path"]).parent
            same_workstream = asset["workstream_id"] and asset["workstream_id"] == authority_asset["workstream_id"]
            version_signalled = bool(
                VERSION_WORDS.search(candidate["filename"]) or VERSION_WORDS.search(authority["filename"])
                or HIGH_AUTHORITY_WORDS.search(authority["filename"])
            )
            historical_role = asset["role"] in {"historical_provenance", "rejected_route", "temporary", "checkpoint", "recovery"}
            if not (historical_role or (same_parent and version_signalled) or (same_workstream and version_signalled)):
                continue
            if asset["authority_level"] not in {"CANONICAL", "PRIMARY", "ACTIVE"} and not asset["superseded_by_path_key"]:
                asset["superseded_by_path_key"] = authority["path_key"]
                asset["evidence"].append(_evidence("temporal_inference", f"Same version family has a higher-ranked asset: {authority['filename']}."))
                asset["archive_recommendation"] = "REVIEW_SUPERSEDED"
    hash_candidates = sorted(
        records,
        key=lambda row: (
            row["extension"] not in {".mph", ".cas.h5", ".dat.h5", ".msh.h5", ".zip", ".pptx", ".pdf", ".docx"},
            -int(row["size"]),
            row["path_key"],
        ),
    )[: max(0, max_stage1_hashes)]
    stage1_groups: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    hash_metrics = {"stage1": 0, "stage2": 0, "stage1_cached": 0, "stage2_cached": 0, "bytes_read": 0}
    for row in hash_candidates:
        if row.get("sample_fingerprint"):
            stage1_groups[(int(row["size"]), row["sample_fingerprint"])].append(row)
            hash_metrics["stage1_cached"] += 1
        else:
            sample = staged_sample_fingerprint(Path(row["path"]), int(row["size"]))
            if not sample:
                continue
            row["sample_fingerprint"] = sample["value"]
            stage1_groups[(int(row["size"]), sample["value"])].append(row)
            connection.execute(
                "INSERT OR REPLACE INTO fingerprints(path_key,stage,algorithm,value,bytes_read,verified_at) VALUES(?,?,?,?,?,?)",
                (row["path_key"], 1, sample["algorithm"], sample["value"], sample["bytes_read"], stamp),
            )
            connection.execute(
                "UPDATE files SET sample_fingerprint=?, fingerprint_stage=MAX(fingerprint_stage,1) WHERE path_key=?",
                (sample["value"], row["path_key"]),
            )
            hash_metrics["stage1"] += 1
            hash_metrics["bytes_read"] += sample["bytes_read"]
    full_budget = max(0, max_full_hashes)
    duplicates: list[dict[str, Any]] = []
    for candidates in stage1_groups.values():
        if len(candidates) < 2 or full_budget < 2:
            continue
        full_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in candidates[:full_budget]:
            if row.get("full_sha256"):
                full_groups[row["full_sha256"]].append(row)
                hash_metrics["stage2_cached"] += 1
            else:
                proof = full_sha256(Path(row["path"]))
                if not proof:
                    continue
                full_groups[proof["value"]].append(row)
                connection.execute(
                    "INSERT OR REPLACE INTO fingerprints(path_key,stage,algorithm,value,bytes_read,verified_at) VALUES(?,?,?,?,?,?)",
                    (row["path_key"], 2, proof["algorithm"], proof["value"], proof["bytes_read"], stamp),
                )
                connection.execute(
                    "UPDATE files SET full_sha256=?, fingerprint_stage=2 WHERE path_key=?",
                    (proof["value"], row["path_key"]),
                )
                hash_metrics["stage2"] += 1
                hash_metrics["bytes_read"] += proof["bytes_read"]
            full_budget -= 1
        for digest, members in full_groups.items():
            if len(members) < 2:
                continue
            canonical = max(
                members,
                key=lambda row: (
                    assets[row["path_key"]]["authority_level"] in {"CANONICAL", "PRIMARY", "ACTIVE"},
                    bool(HIGH_AUTHORITY_WORDS.search(row["filename"])),
                    str(row["modified"]),
                ),
            )
            for duplicate in members:
                if duplicate["path_key"] == canonical["path_key"]:
                    continue
                asset = assets[duplicate["path_key"]]
                asset["archive_recommendation"] = "REVIEW_EXACT_DUPLICATE"
                asset["evidence"].append(_evidence("content_similarity", f"Full SHA-256 equals {canonical['filename']}: {digest}."))
                event = {
                    "event_id": "event_" + uuid.uuid4().hex,
                    "event_type": "DUPLICATE",
                    "source_path_key": canonical["path_key"],
                    "target_path_key": duplicate["path_key"],
                    "full_sha256": digest,
                    "confidence": 1.0,
                }
                duplicates.append(event)
    _apply_assertions(assets, project, _load_assertions(connection))
    connection.execute("DELETE FROM dependencies WHERE project_id=?", (project_id,))
    for item in dependency_rows:
        resolved_key = path_lookup.get(_key(item["resolved_path"])) if item.get("resolved_path") else None
        edge_id = _id("edge", item["source"]["path_key"], item["relation"], item["target_text"], str(item["line_no"]))
        connection.execute(
            """INSERT OR REPLACE INTO dependencies(
                edge_id,project_id,source_path_key,target_path_key,target_text,resolved_path,relation,
                resolution_status,evidence_type,line_no,confidence
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                edge_id, project_id, item["source"]["path_key"], resolved_key, item["target_text"], item["resolved_path"],
                item["relation"], item["resolution_status"], item["evidence_type"], item["line_no"], item["confidence"],
            ),
        )
    connection.execute("DELETE FROM inspections WHERE path_key IN (SELECT path_key FROM assets WHERE project_id=?)", (project_id,))
    for path_key, result in inspections.items():
        row = next(item for item in records if item["path_key"] == path_key)
        connection.execute(
            """INSERT OR REPLACE INTO inspections(
                path_key,inspector,inspected_size,inspected_modified,text_digest,metadata_json,error,inspected_at
            ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                path_key, result["inspector"], row["size"], row["modified"], result.get("text_digest"),
                json.dumps(result.get("metadata", {}), ensure_ascii=False), result.get("error"), stamp,
            ),
        )
    connection.execute("DELETE FROM assets WHERE project_id=?", (project_id,))
    for asset in assets.values():
        connection.execute(
            """INSERT INTO assets(
                path_key,project_id,workstream_id,role,authority_level,confidence,evidence_json,provenance_type,
                rebuildability,archive_recommendation,superseded_by_path_key,updated_at
                ,asset_kind,entity_path,authority_scope,authority_context_id
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                asset["path_key"], project_id, asset["workstream_id"], asset["role"], asset["authority_level"],
                asset["confidence"], json.dumps(asset["evidence"], ensure_ascii=False), asset["provenance_type"],
                asset["rebuildability"], asset["archive_recommendation"], asset["superseded_by_path_key"], stamp,
                asset["asset_kind"], asset["entity_path"], asset["authority_scope"], asset["authority_context_id"],
            ),
        )
    connection.execute("DELETE FROM project_tools WHERE project_id=?", (project_id,))
    for tool in tool_roles:
        connection.execute(
            "INSERT INTO project_tools(project_id,tool_name,centrality,score,evidence_json,updated_at) VALUES(?,?,?,?,?,?)",
            (project_id, tool["tool_name"], tool["centrality"], tool["score"], json.dumps(tool["evidence"], ensure_ascii=False), stamp),
        )
    connection.execute("DELETE FROM identity_events WHERE run_id=?", (f"understand:{project_id}",))
    for event in duplicates:
        connection.execute(
            "INSERT INTO identity_events(event_id,run_id,event_type,source_path_key,target_path_key,full_sha256,confidence,evidence_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                event["event_id"], f"understand:{project_id}", event["event_type"], event["source_path_key"],
                event["target_path_key"], event["full_sha256"], event["confidence"],
                json.dumps([_evidence("content_similarity", "Exact equality verified by Stage-2 full SHA-256.")]), stamp,
            ),
        )
    for candidate in connection.execute("SELECT project_id,root_path FROM projects WHERE status='heuristic' AND root_path IS NOT NULL"):
        candidate_key = _key(candidate["root_path"])
        if candidate_key == root_key or candidate_key.startswith(root_key.rstrip("\\/") + "\\"):
            connection.execute("DELETE FROM projects WHERE project_id=?", (candidate["project_id"],))
    connection.executemany(
        "UPDATE files SET project_id=?,project_name=? WHERE path_key=?",
        [(project_id, project_name, row["path_key"]) for row in records],
    )
    connection.execute(
        """INSERT OR REPLACE INTO projects(
            project_id,name,file_count,total_size,status,root_path,purpose,lifecycle,confidence,evidence_json,parent_project_id
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (
            project_id, project_name, project["file_count"], project["total_size"], "understood", str(root),
            project["purpose"], project["lifecycle"], project["confidence"], json.dumps(project["evidence"], ensure_ascii=False), None,
        ),
    )
    connection.execute("DELETE FROM project_nodes WHERE project_id=?", (project_id,))
    project_node = _id("node", project_id, "project")
    connection.execute(
        "INSERT INTO project_nodes VALUES(?,?,?,?,?,?,?,?,?)",
        (project_node, project_id, None, "project", project_name, str(root), lifecycle, project["confidence"], json.dumps(project["evidence"], ensure_ascii=False)),
    )
    for workstream in workstreams:
        connection.execute(
            "INSERT INTO project_nodes VALUES(?,?,?,?,?,?,?,?,?)",
            (
                workstream["workstream_id"], project_id, project_node, "workstream", workstream["name"],
                workstream["path"], workstream["lifecycle"], workstream["confidence"],
                json.dumps(workstream["evidence"], ensure_ascii=False),
            ),
        )
        role_counts = Counter(asset["role"] for asset in assets.values() if asset["workstream_id"] == workstream["workstream_id"])
        for role, count in role_counts.items():
            node_id = _id("group", workstream["workstream_id"], role)
            connection.execute(
                "INSERT INTO project_nodes VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    node_id, project_id, workstream["workstream_id"], "asset_group", f"{role} ({count})", None,
                    None, 0.9, json.dumps([_evidence("structural_inference", "Files grouped by inferred asset role.")]),
                ),
            )
    top_level_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        parts = _relative_parts(Path(row["path"]), root)
        if len(parts) > 1:
            top_level_buckets[parts[0]].append(row)
    for folder, members in top_level_buckets.items():
        tokens = set(token for token in re.split(r"[^a-z0-9\u4e00-\u9fff]+", folder.casefold()) if token)
        node_type = "archive" if tokens & PROVENANCE_CONTAINER_HINTS else ("deliverable" if tokens & DELIVERABLE_CONTAINER_HINTS else None)
        if not node_type:
            continue
        connection.execute(
            "INSERT INTO project_nodes VALUES(?,?,?,?,?,?,?,?,?)",
            (
                _id(node_type, project_id, folder.casefold()), project_id, project_node, node_type,
                folder, str(root / folder), "FROZEN" if node_type == "deliverable" else "HISTORICAL", 0.84,
                json.dumps([_evidence("structural_inference", f"Top-level {node_type} container with {len(members)} files.", str(root / folder))], ensure_ascii=False),
            ),
        )
    for aggregate in aggregate_records:
        connection.execute("UPDATE aggregate_nodes SET project_id=? WHERE aggregate_id=?", (project_id, aggregate["aggregate_id"]))
    deliverables = [asset for asset in assets.values() if asset["authority_level"] in {"PRIMARY", "CANONICAL"} and asset["role"] in {"final", "deliverable", "manuscript", "presentation", "canonical_model"}]
    for asset in deliverables[:100]:
        connection.execute(
            "INSERT INTO project_nodes VALUES(?,?,?,?,?,?,?,?,?)",
            (
                _id("deliverable", asset["path_key"]), project_id, asset["workstream_id"] or project_node,
                "deliverable", Path(asset["path"]).name, asset["path"], None, asset["confidence"],
                json.dumps(asset["evidence"], ensure_ascii=False),
            ),
        )
    archive_assets = [asset for asset in assets.values() if asset["archive_recommendation"].startswith("REVIEW_")]
    if archive_assets:
        connection.execute(
            "INSERT INTO project_nodes VALUES(?,?,?,?,?,?,?,?,?)",
            (
                _id("archive", project_id), project_id, project_node, "archive", f"Review candidates ({len(archive_assets)})",
                None, None, 0.7, json.dumps([_evidence("structural_inference", "Recommendations require human review; no file action is enabled.")]),
            ),
        )
    activity_status = lifecycle if lifecycle in {"FROZEN", "COMPLETED"} else ("ACTIVE" if lifecycle == "ACTIVE" else "UNKNOWN")
    connection.execute(
        """INSERT INTO project_activity(project_id,activity_status,last_meaningful_activity,activity_score,evidence_json,updated_at)
           VALUES(?,?,?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET
           activity_status=CASE WHEN project_activity.activity_status IN ('ACTIVE','LOW_ACTIVITY','DORMANT')
                                AND excluded.activity_status='UNKNOWN' THEN project_activity.activity_status ELSE excluded.activity_status END,
           last_meaningful_activity=project_activity.last_meaningful_activity,
           activity_score=excluded.activity_score,evidence_json=excluded.evidence_json,updated_at=excluded.updated_at""",
        (project_id, activity_status, None, 95.0 if activity_status in {"FROZEN", "COMPLETED"} else 35.0,
         json.dumps([_evidence("project_understanding", f"Activity initialized from lifecycle {lifecycle}.")], ensure_ascii=False), stamp),
    )
    for workstream in workstreams:
        ws_status = workstream["lifecycle"] if workstream["lifecycle"] in {"ACTIVE", "FROZEN", "COMPLETED"} else "UNKNOWN"
        connection.execute(
            """INSERT INTO workstream_activity(workstream_id,project_id,activity_status,last_meaningful_activity,activity_score,evidence_json,updated_at)
               VALUES(?,?,?,?,?,?,?) ON CONFLICT(workstream_id) DO UPDATE SET activity_status=excluded.activity_status,
               activity_score=excluded.activity_score,evidence_json=excluded.evidence_json,updated_at=excluded.updated_at""",
            (workstream["workstream_id"], project_id, ws_status, None, 90.0 if ws_status != "UNKNOWN" else 30.0,
             json.dumps(workstream["evidence"], ensure_ascii=False), stamp),
        )
    timeline_run = f"understand:{project_id}:{stamp}"

    def emit(event_type: str, *, importance: str, score: float, subject_type: str = "project", **values: Any) -> None:
        append_event(connection, {
            "event_type": event_type, "occurred_at": stamp, "subject_type": subject_type,
            "project_id": project_id, "run_id": timeline_run, "semantic_importance": importance,
            "importance_score": score, "importance_reasons": [f"Evidence graph delta produced {event_type}."],
            "confidence": values.pop("confidence", 0.95), "evidence": values.pop("evidence", []),
            "permanent": importance in {"HIGH", "VERY_HIGH"}, **values,
        })

    if previous_project is None:
        emit(
            "PROJECT_CREATED", importance="MEDIUM", score=48, new_value={"name": project_name, "purpose": project["purpose"]},
            evidence=[_evidence("project_understanding", "First evidence-backed Project graph was materialized.")],
        )
    for workstream in workstreams:
        prior = previous_workstreams.get(workstream["workstream_id"])
        if prior is None:
            emit(
                "WORKSTREAM_CREATED", importance="MEDIUM", score=42, subject_type="workstream",
                workstream_id=workstream["workstream_id"], new_value={"name": workstream["name"], "status": workstream["lifecycle"]},
                confidence=workstream["confidence"], evidence=workstream["evidence"],
            )
        elif prior.get("lifecycle") != workstream["lifecycle"]:
            emit(
                "WORKSTREAM_STATUS_CHANGED", importance="MEDIUM", score=54, subject_type="workstream",
                workstream_id=workstream["workstream_id"], old_value=prior.get("lifecycle"), new_value=workstream["lifecycle"],
                confidence=workstream["confidence"], evidence=workstream["evidence"],
            )
    if previous_project is not None:
        authority_deltas = []
        for key, asset in assets.items():
            prior = previous_assets.get(key)
            old_level = prior.get("authority_level") if prior else None
            if old_level != asset["authority_level"] and asset["authority_level"] in IMPORTANT_AUTHORITIES:
                authority_deltas.append((key, old_level, asset))
        for key, old_level, asset in authority_deltas[:100]:
            file_row = record_lookup.get(key, {})
            emit(
                "AUTHORITY_CHANGED", importance="HIGH", score=78, subject_type=asset["asset_kind"],
                file_id=file_row.get("file_id"), path_key=key, path_after=asset["path"],
                workstream_id=asset.get("workstream_id"), old_value=old_level, new_value={
                    "authority_level": asset["authority_level"], "authority_scope": asset["authority_scope"],
                }, confidence=asset["confidence"], evidence=asset["evidence"],
            )
        current_dependencies = {row["edge_id"]: dict(row) for row in connection.execute("SELECT * FROM dependencies WHERE project_id=?", (project_id,))}
        added = sorted(set(current_dependencies) - set(previous_dependencies))
        removed = sorted(set(previous_dependencies) - set(current_dependencies))
        if added:
            emit("DEPENDENCY_ADDED", importance="LOW", score=32, subject_type="dependency", aggregate_count=len(added),
                 new_value={"count": len(added), "sample_edge_ids": added[:20]}, evidence=[_evidence("dependency_graph_diff", "New resolved or unresolved references appeared.")])
        if removed:
            emit("DEPENDENCY_REMOVED", importance="MEDIUM", score=42, subject_type="dependency", aggregate_count=len(removed),
                 old_value={"count": len(removed), "sample_edge_ids": removed[:20]}, evidence=[_evidence("dependency_graph_diff", "Previously observed reference edges are absent.")])
    if len(duplicates) > previous_duplicate_count:
        emit("DUPLICATE_DETECTED", importance="MEDIUM", score=46, subject_type="duplicate", aggregate_count=len(duplicates) - previous_duplicate_count,
             new_value={"new_exact_duplicate_evidence": len(duplicates) - previous_duplicate_count},
             evidence=[_evidence("content_similarity", "Stage-2 full SHA-256 verified exact equality.")])
    prior_archive = sum(1 for asset in previous_assets.values() if str(asset.get("archive_recommendation") or "").startswith("REVIEW_"))
    if len(archive_assets) > prior_archive:
        emit("ARCHIVE_CANDIDATE_DETECTED", importance="LOW", score=34, subject_type="archive_candidate", aggregate_count=len(archive_assets) - prior_archive,
             new_value={"new_review_candidates": len(archive_assets) - prior_archive},
             evidence=[_evidence("project_understanding", "Archive candidates remain review-only and do not authorize file actions.")])
    if commit:
        connection.commit()
    all_authorities = sorted(
        (
            {
                "path": asset["path"], "role": asset["role"], "authority_level": asset["authority_level"],
                "confidence": asset["confidence"], "evidence": asset["evidence"],
                "asset_kind": asset["asset_kind"], "authority_scope": asset["authority_scope"],
                "authority_context_id": asset["authority_context_id"],
            }
            for asset in assets.values()
            if asset["authority_level"] in {"PRIMARY", "CANONICAL", "ACTIVE"}
        ),
        key=lambda item: (-item["confidence"], item["path"].casefold()),
    )
    authorities = [item for item in all_authorities if item["authority_scope"] in {"PROJECT_WIDE", "FILE_LOCAL"}]
    scoped_authorities = [item for item in all_authorities if item["authority_scope"] in {"WORKSTREAM_LOCAL", "GATE_LOCAL"}]
    return {
        "schema_version": 3,
        "project": project,
        "workstreams": workstreams,
        "authorities": authorities[:100],
        "scoped_authorities": scoped_authorities[:100],
        "authority_total": len(all_authorities),
        "authority_scope_counts": dict(Counter(item["authority_scope"] for item in all_authorities)),
        "tool_roles": tool_roles,
        "dependencies": {"total": len(dependency_rows), "resolved": sum(1 for item in dependency_rows if item["resolution_status"] == "resolved_existing")},
        "duplicates": len(duplicates),
        "archive_candidates": len(archive_assets),
        "inspections": {
            "selected": len(inspection_rows),
            "successful": sum(1 for item in inspections.values() if not item.get("error")),
            "elapsed_seconds": round(sum(float(item.get("metadata", {}).get("inspection_elapsed_seconds", 0)) for item in inspections.values()), 6),
            "slowest": sorted(
                (
                    {
                        "path": next(row["path"] for row in records if row["path_key"] == path_key),
                        "seconds": float(item.get("metadata", {}).get("inspection_elapsed_seconds", 0)),
                        "inspector": item["inspector"],
                    }
                    for path_key, item in inspections.items()
                ),
                key=lambda item: item["seconds"], reverse=True,
            )[:10],
        },
        "hashes": hash_metrics,
        "timeline_run_id": timeline_run,
        "physical_actions": 0,
    }


def asset_details(connection: sqlite3.Connection, path: Path) -> dict[str, Any]:
    path_key = _key(path)
    row = connection.execute(
        """SELECT f.*,a.role,a.authority_level,a.confidence,a.evidence_json,a.provenance_type,
                  a.rebuildability,a.archive_recommendation,a.superseded_by_path_key,a.workstream_id,
                  a.asset_kind,a.entity_path,a.authority_scope,a.authority_context_id,
                  p.name AS understood_project,p.purpose,p.lifecycle
           FROM files f LEFT JOIN assets a ON a.path_key=f.path_key
           LEFT JOIN projects p ON p.project_id=a.project_id WHERE f.path_key=?""",
        (path_key,),
    ).fetchone()
    if row is None:
        row = connection.execute(
            """SELECT a.*,p.name AS understood_project,p.purpose,p.lifecycle FROM assets a
               LEFT JOIN projects p ON p.project_id=a.project_id WHERE a.path_key=?""", (path_key,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Asset is not in the catalog: {path}")
        item = {**dict(row), "path": str(path), "status": "present" if path.exists() else "missing", "size": None, "full_sha256": None}
    else:
        item = dict(row)
    item["evidence"] = json.loads(item.pop("evidence_json") or "[]")
    item["incoming_references"] = [dict(edge) for edge in connection.execute("SELECT * FROM dependencies WHERE target_path_key=? OR resolved_path=?", (path_key, str(path.resolve())))]
    item["outgoing_references"] = [dict(edge) for edge in connection.execute("SELECT * FROM dependencies WHERE source_path_key=?", (path_key,))]
    item["identity_events"] = [dict(event) for event in connection.execute("SELECT * FROM identity_events WHERE source_path_key=? OR target_path_key=?", (path_key, path_key))]
    file_id = item.get("file_id")
    item["timeline_events"] = [
        dict(event) for event in connection.execute(
            "SELECT event_id,event_type,occurred_at,semantic_importance,importance_score,path_before,path_after,confidence,resolution_status FROM events WHERE file_id=? OR path_key=? ORDER BY occurred_at DESC LIMIT 100",
            (file_id, path_key),
        )
    ]
    item["exact_content_matches"] = []
    if item.get("full_sha256"):
        item["exact_content_matches"] = [
            dict(match) for match in connection.execute(
                "SELECT path,path_key,size FROM files WHERE full_sha256=? AND path_key!=? AND status='present' ORDER BY path",
                (item["full_sha256"], path_key),
            )
        ]
    if item.get("superseded_by_path_key"):
        target = connection.execute("SELECT path FROM files WHERE path_key=?", (item["superseded_by_path_key"],)).fetchone()
        item["superseded_by"] = target[0] if target else None
    else:
        item["superseded_by"] = None
    item["physical_actions"] = 0
    return item


def set_assertion(
    connection: sqlite3.Connection,
    *,
    subject_type: str,
    subject_key: str,
    predicate: str,
    value: Any,
) -> dict[str, Any]:
    if subject_type not in {"project", "asset", "workstream"}:
        raise ValueError("subject_type must be project, asset, or workstream")
    stamp = _now()
    assertion_id = _id("assertion", subject_type, subject_key, predicate)
    connection.execute(
        """INSERT INTO user_assertions(assertion_id,subject_type,subject_key,predicate,value_json,provenance,created_at,updated_at,active)
           VALUES(?,?,?,?,?,?,?,?,1)
           ON CONFLICT(assertion_id) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at,active=1""",
        (assertion_id, subject_type, subject_key, predicate, json.dumps(value, ensure_ascii=False), "explicit_user", stamp, stamp),
    )
    connection.commit()
    return {"assertion_id": assertion_id, "provenance": "explicit_user", "active": True, "physical_actions": 0}


def remove_assertion(connection: sqlite3.Connection, assertion_id: str) -> dict[str, Any]:
    changed = connection.execute("UPDATE user_assertions SET active=0,updated_at=? WHERE assertion_id=? AND active=1", (_now(), assertion_id)).rowcount
    connection.commit()
    return {"assertion_id": assertion_id, "removed": bool(changed), "physical_actions": 0}


def list_assertions(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = [dict(row) for row in connection.execute("SELECT * FROM user_assertions WHERE active=1 ORDER BY updated_at DESC")]
    for row in rows:
        row["value"] = json.loads(row.pop("value_json"))
    return rows
