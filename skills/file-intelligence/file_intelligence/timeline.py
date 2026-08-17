from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import sqlite3
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

from .classification import infer_asset_role


TIMELINE_NAME = "Computer Timeline.html"
IMPORTANT_ROLES = {
    "canonical_model", "final", "deliverable", "manuscript", "presentation", "raw_data", "source", "recovery",
}
IMPORTANT_AUTHORITIES = {"CANONICAL", "PRIMARY", "ACTIVE"}
STORAGE_EVENT_TYPES = {
    "FILE_CREATED", "FILE_REAPPEARED", "FILE_MISSING", "FILE_CHANGED",
    "AGGREGATE_CREATED", "AGGREGATE_REMOVED", "AGGREGATE_SIZE_GROWTH", "AGGREGATE_SIZE_SHRINK",
}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _utc(value: str | None = None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def native_file_id(path: Path) -> str | None:
    """Return the platform file index when Python exposes one; never open for writing."""
    try:
        stat = path.stat(follow_symlinks=False)
    except OSError:
        return None
    inode = int(getattr(stat, "st_ino", 0) or 0)
    device = int(getattr(stat, "st_dev", 0) or 0)
    if not inode:
        return None
    return f"{device:x}:{inode:x}"


def _file_id(path_key: str, stamp: str, native_id: str | None = None) -> str:
    material = f"{native_id or ''}|{path_key}|{stamp}"
    return "file_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def initialize_identities(records: list[dict[str, Any]], stamp: str) -> None:
    for row in records:
        if row.get("file_id"):
            continue
        native_id = row.get("native_file_id")
        row["file_id"] = _file_id(row["path_key"], stamp, native_id)
        row["identity_confidence"] = 0.92 if native_id else 0.7
        row["identity_evidence"] = [{
            "type": "native_file_metadata" if native_id else "baseline_identity",
            "detail": "Identity initialized from native file metadata." if native_id else "Identity initialized at the first observed path.",
        }]


def _same_content(left: dict[str, Any], right: dict[str, Any]) -> tuple[str | None, float, list[dict[str, str]]]:
    if int(left.get("size") or 0) != int(right.get("size") or 0):
        return None, 0.0, []
    if left.get("native_file_id") and left.get("native_file_id") == right.get("native_file_id"):
        return "native_file_metadata", 0.995, [{
            "type": "native_file_metadata",
            "detail": "Volume/device and native file index match across paths.",
        }]
    if left.get("full_sha256") and left.get("full_sha256") == right.get("full_sha256"):
        return "full_sha256", 1.0, [{"type": "content_similarity", "detail": "Stage-2 full SHA-256 matches."}]
    left_sample = left.get("sample_fingerprint") or left.get("fingerprint")
    right_sample = right.get("sample_fingerprint") or right.get("fingerprint")
    if left_sample and left_sample == right_sample:
        return "staged_fingerprint", 0.96, [{
            "type": "content_similarity",
            "detail": "Size and Stage-1 head/middle/tail fingerprint match; this is strong identity evidence, not exact equality.",
        }]
    return None, 0.0, []


def correlate_identities(
    previous: list[dict[str, Any]],
    current: list[dict[str, Any]],
    changes: list[dict[str, Any]],
    run_id: str,
    stamp: str,
) -> list[dict[str, Any]]:
    old = {row["path_key"]: row for row in previous}
    current_by_key = {row["path_key"]: row for row in current}
    change_by_key = {item["path_key"]: item for item in changes}
    for row in current:
        before = old.get(row["path_key"])
        if before:
            for field in ("file_id", "native_file_id", "identity_confidence", "identity_evidence_json"):
                if before.get(field) is not None and row.get(field) is None:
                    row[field] = before.get(field)
    arrivals = [
        current_by_key[item["path_key"]]
        for item in changes
        if item["change_type"] in {"NEW", "REAPPEARED"} and item["path_key"] in current_by_key
    ]
    missing = [
        old[item["path_key"]]
        for item in changes
        if item["change_type"] == "MISSING" and item["path_key"] in old
    ]
    present_sources = [
        row for row in previous
        if row.get("status") == "present" and row["path_key"] in current_by_key
        and current_by_key[row["path_key"]].get("status") == "present"
    ]
    for arrival in arrivals:
        arrival["native_file_id"] = arrival.get("native_file_id") or native_file_id(Path(arrival["path"]))
    transitions: list[dict[str, Any]] = []
    used_missing: set[str] = set()
    for arrival in arrivals:
        ranked: list[tuple[float, dict[str, Any], str, list[dict[str, str]]]] = []
        for source in missing:
            if source["path_key"] in used_missing:
                continue
            method, confidence, evidence = _same_content(source, arrival)
            if method:
                ranked.append((confidence, source, method, evidence))
        if ranked:
            confidence, source, method, evidence = max(ranked, key=lambda item: item[0])
            same_parent = Path(source["path"]).parent == Path(arrival["path"]).parent
            transition_type = "RENAMED" if same_parent else "MOVED"
            arrival["file_id"] = source.get("file_id") or _file_id(source["path_key"], source.get("first_seen") or stamp, source.get("native_file_id"))
            arrival["identity_confidence"] = confidence
            arrival["identity_evidence"] = evidence
            transitions.append({
                "event_id": "identity_" + uuid.uuid4().hex,
                "run_id": run_id,
                "event_type": transition_type,
                "source_path_key": source["path_key"],
                "target_path_key": arrival["path_key"],
                "file_id": arrival["file_id"],
                "full_sha256": arrival.get("full_sha256") or source.get("full_sha256"),
                "confidence": confidence,
                "evidence": evidence,
                "created_at": stamp,
                "identity_method": method,
            })
            used_missing.add(source["path_key"])
            continue
        copy_matches: list[tuple[float, dict[str, Any], str, list[dict[str, str]]]] = []
        for source in present_sources:
            if source["path_key"] == arrival["path_key"]:
                continue
            method, confidence, evidence = _same_content(source, arrival)
            if method != "native_file_metadata" and method:
                copy_matches.append((confidence, source, method, evidence))
        if copy_matches:
            confidence, source, method, evidence = max(copy_matches, key=lambda item: item[0])
            arrival["file_id"] = _file_id(arrival["path_key"], stamp, arrival.get("native_file_id"))
            arrival["identity_confidence"] = confidence
            arrival["identity_evidence"] = evidence + [{"type": "path_state", "detail": "The source path remains present, so this is a copy rather than a move."}]
            transitions.append({
                "event_id": "identity_" + uuid.uuid4().hex,
                "run_id": run_id,
                "event_type": "COPIED",
                "source_path_key": source["path_key"],
                "target_path_key": arrival["path_key"],
                "file_id": arrival["file_id"],
                "source_file_id": source.get("file_id"),
                "full_sha256": arrival.get("full_sha256") or source.get("full_sha256"),
                "confidence": confidence,
                "evidence": arrival["identity_evidence"],
                "created_at": stamp,
                "identity_method": method,
            })
            continue
        possible = [source for source in missing if source["path_key"] not in used_missing and int(source.get("size") or 0) == int(arrival.get("size") or 0)]
        if len(possible) == 1:
            source = possible[0]
            similarity = SequenceMatcher(None, Path(source["path"]).stem.casefold(), Path(arrival["path"]).stem.casefold()).ratio()
            if similarity >= 0.72:
                transitions.append({
                    "event_id": "identity_" + uuid.uuid4().hex,
                    "run_id": run_id,
                    "event_type": "POSSIBLE_MOVE",
                    "source_path_key": source["path_key"],
                    "target_path_key": arrival["path_key"],
                    "file_id": None,
                    "full_sha256": None,
                    "confidence": round(0.45 + 0.2 * similarity, 3),
                    "evidence": [{"type": "weak_identity_candidate", "detail": "Unique equal-size candidate with a similar basename; content identity is unverified."}],
                    "created_at": stamp,
                    "identity_method": "weak_candidate",
                })
        arrival["file_id"] = arrival.get("file_id") or _file_id(arrival["path_key"], stamp, arrival.get("native_file_id"))
        arrival["identity_confidence"] = float(arrival.get("identity_confidence") or (0.92 if arrival.get("native_file_id") else 0.7))
        arrival["identity_evidence"] = arrival.get("identity_evidence") or [{"type": "new_identity", "detail": "No strong prior identity match was found."}]
    initialize_identities(current, stamp)
    for item in changes:
        transition = next(
            (event for event in transitions if item["path_key"] in {event.get("source_path_key"), event.get("target_path_key")}),
            None,
        )
        if transition:
            item["identity_event"] = transition["event_type"]
            item["identity_confidence"] = transition["confidence"]
        row = current_by_key.get(item["path_key"])
        if row:
            item["file_id"] = row.get("file_id")
    return transitions


def _importance(
    event_type: str,
    *,
    role: str | None,
    authority: str | None,
    size: int,
    volatile_class: str | None,
    rebuildability: str | None = None,
    dependency_count: int = 0,
    explicit_user: bool = False,
) -> tuple[float, str, list[str]]:
    if volatile_class:
        return 0.0, "VOLATILE", [f"Classified as explainable volatile state: {volatile_class}."]
    base = {
        "POSSIBLE_ASSET_LOSS": 58, "IMPORTANT_ASSET_ALERT": 58,
        "AUTHORITY_CHANGED": 48, "FILE_MISSING": 28, "LARGE_ASSET_CREATED": 32,
        "LARGE_ASSET_REMOVED": 36, "FILE_MOVED": 22, "FILE_RENAMED": 20,
        "FILE_CHANGED": 40, "FILE_CREATED": 14, "FILE_COPIED": 12,
        "PROJECT_BECAME_ACTIVE": 42, "PROJECT_BECAME_DORMANT": 42,
        "WORKSTREAM_STATUS_CHANGED": 24, "DEPENDENCY_ADDED": 10, "DEPENDENCY_REMOVED": 12,
        "AGGREGATE_SIZE_GROWTH": 14, "AGGREGATE_CREATED": 12,
    }.get(event_type, 10)
    score = float(base)
    reasons = [f"Base weight for {event_type}: {base}."]
    if authority == "CANONICAL":
        score += 30; reasons.append("Asset authority is CANONICAL (+30).")
    elif authority == "PRIMARY":
        score += 26; reasons.append("Asset authority is PRIMARY (+26).")
    elif authority == "ACTIVE":
        score += 20; reasons.append("Asset is an ACTIVE recovery/working authority (+20).")
    if role in IMPORTANT_ROLES:
        bonus = 18 if role in {"canonical_model", "manuscript", "raw_data"} else 12
        score += bonus; reasons.append(f"Scientifically important role {role} (+{bonus}).")
    elif event_type == "FILE_COPIED" and role == "working_model":
        score += 28; reasons.append("A research working model was copied; duplicate/lineage review is useful (+28).")
    if size >= 4 * 1024**3:
        score += 18; reasons.append("Affected asset is at least 4 GiB (+18).")
    elif size >= 1024**3:
        score += 12; reasons.append("Affected asset is at least 1 GiB (+12).")
    elif size >= 256 * 1024**2:
        score += 7; reasons.append("Affected asset is at least 256 MiB (+7).")
    if dependency_count:
        bonus = min(12, 2 + math.log2(dependency_count + 1) * 2)
        score += bonus; reasons.append(f"Referenced by {dependency_count} dependency edges (+{bonus:.1f}).")
    if rebuildability == "likely":
        score -= 12; reasons.append("Content is likely rebuildable (-12).")
    if explicit_user:
        score += 20; reasons.append("Explicit user assertion applies (+20).")
    score = round(max(0.0, min(score, 100.0)), 2)
    level = "VERY_HIGH" if score >= 85 else ("HIGH" if score >= 65 else ("MEDIUM" if score >= 40 else "LOW"))
    return score, level, reasons


def append_event(connection: sqlite3.Connection, event: dict[str, Any]) -> str:
    event_id = event.get("event_id") or "event_" + uuid.uuid4().hex
    connection.execute(
        """INSERT OR IGNORE INTO events(
            event_id,event_type,occurred_at,subject_type,file_id,path_key,project_id,workstream_id,
            old_value_json,new_value_json,size_delta,path_before,path_after,confidence,evidence_json,run_id,
            semantic_importance,importance_score,importance_reasons_json,volatile_class,resolution_status,permanent,aggregate_count
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            event_id, event["event_type"], event["occurred_at"], event.get("subject_type", "file"),
            event.get("file_id"), event.get("path_key"), event.get("project_id"), event.get("workstream_id"),
            _json(event.get("old_value")) if event.get("old_value") is not None else None,
            _json(event.get("new_value")) if event.get("new_value") is not None else None,
            int(event.get("size_delta") or 0), event.get("path_before"), event.get("path_after"),
            float(event.get("confidence", 1.0)), _json(event.get("evidence", [])), event["run_id"],
            event.get("semantic_importance", "LOW"), float(event.get("importance_score", 0)),
            _json(event.get("importance_reasons", [])), event.get("volatile_class"), event.get("resolution_status"),
            int(bool(event.get("permanent"))), int(event.get("aggregate_count") or 1),
        ),
    )
    return event_id


def _asset_context(connection: sqlite3.Connection, path_key: str | None, row: dict[str, Any] | None = None) -> dict[str, Any]:
    asset = None
    if path_key:
        asset = connection.execute(
            "SELECT role,authority_level,rebuildability,superseded_by_path_key,workstream_id,authority_scope,provenance_type FROM assets WHERE path_key=?",
            (path_key,),
        ).fetchone()
    if asset:
        return dict(asset)
    if row:
        inferred = infer_asset_role(row.get("relative_path") or row.get("path") or "", row.get("extension") or "")
        return {
            "role": inferred["role"], "authority_level": inferred["authority_level"],
            "rebuildability": "likely" if inferred["role"] in {"temporary", "cache", "intermediate", "derived"} else "unknown",
            "superseded_by_path_key": None, "workstream_id": None, "authority_scope": "FILE_LOCAL", "provenance_type": "structural_inference",
        }
    return {"role": "unknown", "authority_level": "UNKNOWN", "rebuildability": "unknown", "workstream_id": None, "authority_scope": "FILE_LOCAL"}


def _event(
    connection: sqlite3.Connection,
    *,
    event_type: str,
    run_id: str,
    stamp: str,
    row: dict[str, Any] | None,
    context: dict[str, Any],
    old_value: Any = None,
    new_value: Any = None,
    size_delta: int = 0,
    path_before: str | None = None,
    path_after: str | None = None,
    confidence: float = 1.0,
    evidence: list[dict[str, Any]] | None = None,
    subject_type: str = "file",
    project_id: str | None = None,
    aggregate_count: int = 1,
    volatile_class: str | None = None,
    resolution_status: str | None = None,
) -> str:
    path_key = row.get("path_key") if row else None
    dependency_count = 0
    if path_key:
        dependency_count = int(connection.execute(
            "SELECT COUNT(*) FROM dependencies WHERE target_path_key=?", (path_key,)
        ).fetchone()[0])
    size = int((row or {}).get("size") or abs(size_delta))
    score, importance, reasons = _importance(
        event_type, role=context.get("role"), authority=context.get("authority_level"), size=size,
        volatile_class=volatile_class, rebuildability=context.get("rebuildability"), dependency_count=dependency_count,
        explicit_user=context.get("provenance_type") == "explicit_user",
    )
    return append_event(connection, {
        "event_type": event_type, "occurred_at": stamp, "subject_type": subject_type,
        "file_id": (row or {}).get("file_id"), "path_key": path_key,
        "project_id": project_id if project_id is not None else (row or {}).get("project_id"),
        "workstream_id": context.get("workstream_id"), "old_value": old_value, "new_value": new_value,
        "size_delta": size_delta, "path_before": path_before, "path_after": path_after,
        "confidence": confidence, "evidence": evidence or [], "run_id": run_id,
        "semantic_importance": importance, "importance_score": score, "importance_reasons": reasons,
        "volatile_class": volatile_class, "resolution_status": resolution_status,
        "permanent": importance in {"VERY_HIGH", "HIGH"} or event_type in {"POSSIBLE_ASSET_LOSS", "AUTHORITY_CHANGED"},
        "aggregate_count": aggregate_count,
    })


def record_maintenance_events(
    connection: sqlite3.Connection,
    previous: list[dict[str, Any]],
    current: list[dict[str, Any]],
    changes: list[dict[str, Any]],
    transitions: list[dict[str, Any]],
    old_aggregates: list[dict[str, Any]],
    new_aggregates: list[dict[str, Any]],
    run_id: str,
    stamp: str,
) -> dict[str, Any]:
    old = {row["path_key"]: row for row in previous}
    now = {row["path_key"]: row for row in current}
    transition_by_target = {item["target_path_key"]: item for item in transitions}
    strong_source = {
        item["source_path_key"]: item for item in transitions if item["event_type"] in {"MOVED", "RENAMED"}
    }
    volatile_groups: dict[str, dict[str, int]] = defaultdict(lambda: {"count": 0, "size_delta": 0})
    event_ids: list[str] = []
    for item in changes:
        before = old.get(item["path_key"])
        row = now.get(item["path_key"], before)
        if not row:
            continue
        change_type = item["change_type"]
        volatile = row.get("volatile_class") or item.get("volatile_class")
        before_size = int((before or {}).get("size") or 0)
        after_size = int(row.get("size") or 0) if row.get("status") == "present" else 0
        delta = after_size - before_size
        if volatile:
            volatile_groups[str(volatile)]["count"] += 1
            volatile_groups[str(volatile)]["size_delta"] += delta
            continue
        if item["path_key"] in strong_source:
            continue
        transition = transition_by_target.get(item["path_key"])
        context = _asset_context(connection, (before or row).get("path_key"), before or row)
        if transition and transition["event_type"] in {"MOVED", "RENAMED", "COPIED", "POSSIBLE_MOVE"}:
            source = old.get(transition["source_path_key"])
            context = _asset_context(connection, transition["source_path_key"], source)
            target_context = _asset_context(connection, row.get("path_key"), row)
            event_type = transition["event_type"] if transition["event_type"] == "POSSIBLE_MOVE" else "FILE_" + transition["event_type"]
            event_ids.append(_event(
                connection, event_type=event_type, run_id=run_id, stamp=stamp, row=row,
                context=context, path_before=(source or {}).get("path"), path_after=row.get("path"),
                confidence=transition["confidence"], evidence=transition["evidence"],
                resolution_status="identity_resolved" if transition["event_type"] != "POSSIBLE_MOVE" else "unverified_candidate",
            ))
            if transition["event_type"] in {"MOVED", "RENAMED"} and target_context.get("role") != context.get("role"):
                event_ids.append(_event(
                    connection, event_type="ROLE_CHANGED", run_id=run_id, stamp=stamp, row=row,
                    context=target_context, old_value=context.get("role"), new_value=target_context.get("role"),
                    path_before=(source or {}).get("path"), path_after=row.get("path"), confidence=transition["confidence"],
                    evidence=transition["evidence"],
                ))
            if transition["event_type"] in {"MOVED", "RENAMED"} and target_context.get("authority_level") != context.get("authority_level"):
                event_ids.append(_event(
                    connection, event_type="AUTHORITY_CHANGED", run_id=run_id, stamp=stamp, row=row,
                    context=target_context, old_value=context.get("authority_level"), new_value=target_context.get("authority_level"),
                    path_before=(source or {}).get("path"), path_after=row.get("path"), confidence=transition["confidence"],
                    evidence=transition["evidence"],
                ))
            if transition["event_type"] != "POSSIBLE_MOVE":
                _resolve_alerts(connection, row.get("file_id"), event_ids[-1], stamp)
                continue
        if change_type == "NEW":
            event_type = "FILE_CREATED"
            delta = int(row.get("size") or 0)
            event_ids.append(_event(connection, event_type=event_type, run_id=run_id, stamp=stamp, row=row, context=context,
                                    new_value={"size": delta, "role": context.get("role")}, size_delta=delta,
                                    path_after=row.get("path"), evidence=[{"type": "catalog_diff", "detail": "Path was not present in the previous catalog."}]))
            if delta >= 1024**3 or (context.get("role") in {"working_model", "canonical_model"} and delta >= 256 * 1024**2):
                event_ids.append(_event(connection, event_type="LARGE_ASSET_CREATED", run_id=run_id, stamp=stamp, row=row, context=context,
                                        new_value={"size": delta}, path_after=row.get("path"), evidence=[{"type": "catalog_diff", "detail": "New large research asset threshold was crossed."}]))
        elif change_type == "REAPPEARED":
            event_ids.append(_event(connection, event_type="FILE_REAPPEARED", run_id=run_id, stamp=stamp, row=row, context=context,
                                    new_value={"size": int(row.get("size") or 0)}, size_delta=int(row.get("size") or 0), path_after=row.get("path"),
                                    evidence=[{"type": "catalog_diff", "detail": "A previously missing path is present again."}]))
            _resolve_alerts(connection, row.get("file_id"), event_ids[-1], stamp)
        elif change_type == "CHANGED":
            event_ids.append(_event(connection, event_type="FILE_CHANGED", run_id=run_id, stamp=stamp, row=row, context=context,
                                    old_value={"size": before_size, "modified": (before or {}).get("modified")},
                                    new_value={"size": after_size, "modified": row.get("modified")}, size_delta=delta,
                                    path_before=row.get("path"), path_after=row.get("path"),
                                    evidence=[{"type": "catalog_diff", "detail": "Size or modification metadata changed at the same path."}]))
            if delta and (abs(delta) >= 64 * 1024**2 or abs(delta) >= max(1, before_size) * 0.25):
                event_ids.append(_event(connection, event_type="FILE_SIZE_GROWTH" if delta > 0 else "FILE_SIZE_SHRINK",
                                        run_id=run_id, stamp=stamp, row=row, context=context, old_value=before_size,
                                        new_value=after_size, size_delta=delta, path_before=row.get("path"), path_after=row.get("path"),
                                        evidence=[{"type": "catalog_diff", "detail": "Material size change crossed the transparent size threshold."}]))
        elif change_type == "MISSING":
            survivors: list[tuple[dict[str, Any], str]] = []
            for candidate in current:
                if candidate.get("status") != "present" or candidate["path_key"] == row["path_key"]:
                    continue
                method, _, _ = _same_content(row, candidate)
                if method:
                    survivors.append((candidate, method))
            survivor = max(survivors, key=lambda item: 1 if item[1] == "full_sha256" else 0) if survivors else None
            resolution = "unresolved_missing"
            missing_evidence = [{"type": "catalog_diff", "detail": "The prior path was not observed; deletion is not assumed."}]
            if survivor:
                survivor_path = survivor[0]["path"]
                archived = any(token in survivor_path.casefold() for token in ("archive", "handoff", "backup", "历史", "归档"))
                resolution = ("archived_copy_" if archived else "content_copy_") + ("verified" if survivor[1] == "full_sha256" else "candidate")
                missing_evidence.append({
                    "type": "content_similarity",
                    "detail": f"A surviving {'exact' if survivor[1] == 'full_sha256' else 'Stage-1'} content match exists at {survivor_path}.",
                })
            event_ids.append(_event(connection, event_type="FILE_MISSING", run_id=run_id, stamp=stamp, row=row, context=context,
                                    old_value={"size": before_size}, size_delta=-before_size, path_before=row.get("path"),
                                    evidence=missing_evidence, resolution_status=resolution))
            if before_size >= 1024**3:
                event_ids.append(_event(connection, event_type="LARGE_ASSET_REMOVED", run_id=run_id, stamp=stamp, row=row, context=context,
                                        old_value={"size": before_size}, path_before=row.get("path"),
                                        evidence=[{"type": "catalog_diff", "detail": "A previously catalogued large asset is no longer observed."}],
                                        resolution_status="unresolved_missing"))
            superseded = context.get("superseded_by_path_key")
            superseded_present = bool(superseded and now.get(superseded, {}).get("status") == "present")
            important = context.get("authority_level") in IMPORTANT_AUTHORITIES or context.get("role") in IMPORTANT_ROLES
            if important and not superseded_present and not survivor:
                alert_event = _event(
                    connection, event_type="POSSIBLE_ASSET_LOSS", run_id=run_id, stamp=stamp, row=row, context=context,
                    old_value={"role": context.get("role"), "authority": context.get("authority_level"), "size": before_size},
                    path_before=row.get("path"), confidence=0.9,
                    evidence=[{"type": "asset_alert", "detail": "Important asset is missing after move/rename correlation and supersession checks."}],
                    resolution_status="open_alert",
                )
                alert_id = "alert_" + uuid.uuid4().hex
                connection.execute(
                    """INSERT INTO asset_alerts(alert_id,event_id,file_id,project_id,path_before,alert_type,status,importance,evidence_json,first_seen)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (alert_id, alert_event, row.get("file_id"), row.get("project_id"), row.get("path"), "IMPORTANT_ASSET_ALERT",
                     "OPEN", "VERY_HIGH" if context.get("authority_level") == "CANONICAL" else "HIGH",
                     _json([{"type": "asset_alert", "detail": "No strong relocation, archive, or supersession resolution exists."}]), stamp),
                )
                event_ids.append(alert_event)
    for category, aggregate in volatile_groups.items():
        score, importance, reasons = _importance("VOLATILE_CHANGE_AGGREGATE", role="cache", authority="DERIVED", size=abs(aggregate["size_delta"]), volatile_class=category, rebuildability="likely")
        event_ids.append(append_event(connection, {
            "event_type": "VOLATILE_CHANGE_AGGREGATE", "occurred_at": stamp, "subject_type": "aggregate",
            "size_delta": aggregate["size_delta"], "confidence": 0.95,
            "evidence": [{"type": "volatile_rule", "detail": f"Collapsed {aggregate['count']} raw changes in class {category}."}],
            "run_id": run_id, "semantic_importance": importance, "importance_score": score,
            "importance_reasons": reasons, "volatile_class": category, "aggregate_count": aggregate["count"],
        }))
    event_ids.extend(_record_aggregate_events(connection, old_aggregates, new_aggregates, run_id, stamp))
    return {"event_ids": event_ids, "events_recorded": len(event_ids), "volatile_groups": len(volatile_groups)}


def _resolve_alerts(connection: sqlite3.Connection, file_id: str | None, event_id: str, stamp: str) -> None:
    if not file_id:
        return
    connection.execute(
        "UPDATE asset_alerts SET status='RESOLVED',resolved_at=?,resolution_event_id=? WHERE file_id=? AND status='OPEN'",
        (stamp, event_id, file_id),
    )


def _record_aggregate_events(
    connection: sqlite3.Connection,
    old_rows: list[dict[str, Any]],
    new_rows: list[dict[str, Any]],
    run_id: str,
    stamp: str,
) -> list[str]:
    old = {row["aggregate_id"]: row for row in old_rows}
    new = {row["aggregate_id"]: row for row in new_rows}
    event_ids: list[str] = []
    for key in sorted(set(old) | set(new)):
        before, after = old.get(key), new.get(key)
        old_size, new_size = int((before or {}).get("total_size") or 0), int((after or {}).get("total_size") or 0)
        if before and after and old_size == new_size and int(before.get("file_count") or 0) == int(after.get("file_count") or 0):
            continue
        row = after or before or {}
        delta = new_size - old_size
        event_type = "AGGREGATE_CREATED" if before is None else ("AGGREGATE_REMOVED" if after is None else ("AGGREGATE_SIZE_GROWTH" if delta >= 0 else "AGGREGATE_SIZE_SHRINK"))
        context = {"role": "cache", "authority_level": "DERIVED", "rebuildability": row.get("rebuildability", "likely")}
        event_ids.append(_event(
            connection, event_type=event_type, run_id=run_id, stamp=stamp, row=None, context=context,
            old_value={"size": old_size, "file_count": int((before or {}).get("file_count") or 0)},
            new_value={"size": new_size, "file_count": int((after or {}).get("file_count") or 0)},
            size_delta=delta, path_before=(before or {}).get("path"), path_after=(after or {}).get("path"),
            confidence=float(row.get("confidence") or 0.82),
            evidence=[{"type": "aggregate_diff", "detail": f"Aggregate {row.get('kind', 'unknown')} changed without indexing its internal files."}],
            subject_type="aggregate", project_id=row.get("project_id"),
            aggregate_count=max(
                1,
                int((before or {}).get("file_count") or 0),
                int((after or {}).get("file_count") or 0),
            ),
        ))
    return event_ids


def sync_file_cards(connection: sqlite3.Connection, records: list[dict[str, Any]], stamp: str) -> None:
    selected: dict[str, dict[str, Any]] = {}
    for row in records:
        file_id = row.get("file_id")
        if not file_id:
            continue
        prior = selected.get(file_id)
        if prior is None or (prior.get("status") != "present" and row.get("status") == "present"):
            selected[file_id] = row
    for file_id, row in selected.items():
        asset = connection.execute(
            "SELECT role,authority_level,authority_scope FROM assets WHERE path_key=?", (row["path_key"],)
        ).fetchone()
        first_seen = row.get("first_seen") or stamp
        evidence = row.get("identity_evidence_json")
        if not evidence:
            evidence = _json(row.get("identity_evidence") or [{"type": "catalog_identity", "detail": "Identity retained across this observation."}])
        connection.execute(
            """INSERT INTO file_cards(
                file_id,native_file_id,current_path_key,first_seen,last_seen,status,identity_confidence,
                identity_evidence_json,last_role,last_authority,authority_scope
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(file_id) DO UPDATE SET
                native_file_id=COALESCE(excluded.native_file_id,file_cards.native_file_id),
                current_path_key=CASE WHEN excluded.status='present' THEN excluded.current_path_key ELSE file_cards.current_path_key END,
                last_seen=excluded.last_seen,status=excluded.status,identity_confidence=excluded.identity_confidence,
                identity_evidence_json=excluded.identity_evidence_json,last_role=COALESCE(excluded.last_role,file_cards.last_role),
                last_authority=COALESCE(excluded.last_authority,file_cards.last_authority),
                authority_scope=COALESCE(excluded.authority_scope,file_cards.authority_scope)""",
            (
                file_id, row.get("native_file_id"), row["path_key"], first_seen, stamp, row.get("status", "present"),
                float(row.get("identity_confidence") or 0.7), evidence,
                asset["role"] if asset else None, asset["authority_level"] if asset else None, asset["authority_scope"] if asset else None,
            ),
        )


def update_project_activity(connection: sqlite3.Connection, run_id: str, stamp: str) -> list[str]:
    meaningful_by_project = {
        row["project_id"]: (int(row["events"]), float(row["score"]))
        for row in connection.execute(
            """SELECT project_id,COUNT(*) AS events,MAX(importance_score) AS score FROM events
               WHERE run_id=? AND project_id IS NOT NULL AND semantic_importance NOT IN ('LOW','VOLATILE')
               GROUP BY project_id""", (run_id,)
        )
    }
    projects = [dict(row) for row in connection.execute("SELECT project_id,lifecycle,status FROM projects")]
    emitted: list[str] = []
    now = _utc(stamp)
    for project in projects:
        project_id = project["project_id"]
        previous = connection.execute("SELECT * FROM project_activity WHERE project_id=?", (project_id,)).fetchone()
        assertion = connection.execute(
            """SELECT value_json FROM user_assertions WHERE subject_type='project' AND subject_key=?
               AND predicate='activity_status' AND active=1 ORDER BY updated_at DESC LIMIT 1""", (project_id,)
        ).fetchone()
        evidence: list[dict[str, Any]] = []
        last_meaningful = previous["last_meaningful_activity"] if previous else None
        lifecycle = str(project.get("lifecycle") or "UNKNOWN").upper()
        event_count, event_score = meaningful_by_project.get(project_id, (0, 0.0))
        if assertion:
            status = str(json.loads(assertion["value_json"])).upper()
            score = 100.0
            evidence.append({"type": "explicit_user", "detail": f"User assertion fixes activity_status={status}."})
        elif lifecycle in {"FROZEN", "COMPLETED"}:
            status = lifecycle
            score = 95.0
            evidence.append({"type": "explicit_project_metadata", "detail": f"Project lifecycle is {lifecycle}."})
        elif event_count:
            status = "ACTIVE"
            score = min(100.0, 45.0 + event_score / 2)
            last_meaningful = stamp
            evidence.append({"type": "event_store", "detail": f"{event_count} meaningful events occurred in this maintenance run."})
        elif last_meaningful:
            age = (now - _utc(last_meaningful)).days
            status = "DORMANT" if age >= 30 else ("LOW_ACTIVITY" if age >= 7 else "ACTIVE")
            score = max(10.0, 70.0 - age * 2)
            evidence.append({"type": "temporal_inference", "detail": f"Last meaningful event was {age} days ago."})
        else:
            status = "UNKNOWN"
            score = 20.0
            evidence.append({"type": "insufficient_evidence", "detail": "No meaningful historical event has been observed yet."})
        connection.execute(
            """INSERT INTO project_activity(project_id,activity_status,last_meaningful_activity,activity_score,evidence_json,updated_at)
               VALUES(?,?,?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET activity_status=excluded.activity_status,
               last_meaningful_activity=excluded.last_meaning_activity,activity_score=excluded.activity_score,
               evidence_json=excluded.evidence_json,updated_at=excluded.updated_at""".replace("excluded.last_meaning_activity", "excluded.last_meaningful_activity"),
            (project_id, status, last_meaningful, round(score, 2), _json(evidence), stamp),
        )
        old_status = previous["activity_status"] if previous else None
        if old_status and old_status != status:
            event_type = "PROJECT_BECAME_ACTIVE" if status == "ACTIVE" else (
                "PROJECT_BECAME_DORMANT" if status == "DORMANT" else (
                    "PROJECT_FROZEN" if status == "FROZEN" else "PROJECT_ACTIVITY_STOPPED"
                )
            )
            importance_score, importance, reasons = _importance(event_type, role=None, authority=None, size=0, volatile_class=None)
            emitted.append(append_event(connection, {
                "event_type": event_type, "occurred_at": stamp, "subject_type": "project", "project_id": project_id,
                "old_value": old_status, "new_value": status, "confidence": min(1.0, score / 100), "evidence": evidence,
                "run_id": run_id, "semantic_importance": importance, "importance_score": importance_score,
                "importance_reasons": reasons,
            }))
    return emitted


def materialize_semantic_changes(connection: sqlite3.Connection, run_id: str, stamp: str) -> dict[str, Any]:
    rows = [dict(row) for row in connection.execute("SELECT * FROM events WHERE run_id=? ORDER BY importance_score DESC,event_id", (run_id,))]
    connection.execute("DELETE FROM semantic_changes WHERE run_id=?", (run_id,))
    raw_counts = Counter(row["event_type"] for row in rows)
    meaningful = [row for row in rows if row["semantic_importance"] not in {"LOW", "VOLATILE"}]
    volatile_count = sum(int(row.get("aggregate_count") or 1) for row in rows if row["semantic_importance"] == "VOLATILE")
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row.get("project_id") or "unassigned"].append(row)
    project_changes: list[dict[str, Any]] = []
    for key, events in groups.items():
        nonvolatile = [event for event in events if event["semantic_importance"] != "VOLATILE"]
        if not nonvolatile:
            continue
        storage_delta = sum(int(event["size_delta"]) for event in nonvolatile if event["event_type"] in STORAGE_EVENT_TYPES)
        top = max(nonvolatile, key=lambda event: float(event["importance_score"]))
        project_name = None
        if key != "unassigned":
            name = connection.execute("SELECT name FROM projects WHERE project_id=?", (key,)).fetchone()
            project_name = name[0] if name else key
        item = {
            "project_id": None if key == "unassigned" else key,
            "project_name": project_name or "Unassigned / system aggregate",
            "event_count": sum(int(event.get("aggregate_count") or 1) for event in nonvolatile),
            "meaningful_event_count": sum(1 for event in nonvolatile if event["semantic_importance"] not in {"LOW", "VOLATILE"}),
            "size_delta": storage_delta,
            "importance": top["semantic_importance"],
            "importance_score": float(top["importance_score"]),
            "top_event": _public_event(top),
            "event_types": dict(Counter(event["event_type"] for event in nonvolatile)),
        }
        project_changes.append(item)
        connection.execute(
            """INSERT INTO semantic_changes(semantic_change_id,run_id,created_at,project_id,workstream_id,change_kind,
               importance,importance_score,event_count,size_delta,summary_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            ("semantic_" + uuid.uuid4().hex, run_id, stamp, item["project_id"], None, "PROJECT_CHANGE",
             item["importance"], item["importance_score"], item["event_count"], item["size_delta"], _json(item)),
        )
    project_changes.sort(key=lambda item: (-item["importance_score"], -abs(item["size_delta"]), item["project_name"].casefold()))
    alerts = [dict(row) for row in connection.execute(
        "SELECT alert_id,file_id,project_id,path_before,alert_type,status,importance,first_seen FROM asset_alerts WHERE status='OPEN' ORDER BY first_seen DESC"
    )]
    important_changes = [_public_event(row) for row in meaningful[:20]]
    storage_changes = sorted(
        ({"project_id": item["project_id"], "project_name": item["project_name"], "size_delta": item["size_delta"]} for item in project_changes if item["size_delta"]),
        key=lambda item: -abs(item["size_delta"]),
    )
    return {
        "run_id": run_id, "time_range": {"from": stamp, "to": stamp},
        "raw_event_counts": dict(raw_counts), "meaningful_event_count": len(meaningful), "volatile_raw_change_count": volatile_count,
        "important_changes": important_changes, "project_changes": project_changes,
        "storage_changes": storage_changes, "asset_alerts": alerts,
        "uncertainties": [
            _public_event(row) for row in rows if row.get("resolution_status") in {"unverified_candidate", "unresolved_missing"}
        ][:20],
    }


def _public_event(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": row.get("event_id"), "event_type": row.get("event_type"), "timestamp": row.get("occurred_at"),
        "project_id": row.get("project_id"), "workstream_id": row.get("workstream_id"), "file_id": row.get("file_id"),
        "path_before": row.get("path_before"), "path_after": row.get("path_after"), "size_delta": int(row.get("size_delta") or 0),
        "importance": row.get("semantic_importance"), "importance_score": float(row.get("importance_score") or 0),
        "confidence": float(row.get("confidence") or 0), "resolution_status": row.get("resolution_status"),
        "aggregate_count": int(row.get("aggregate_count") or 1),
        "why_important": json.loads(row["importance_reasons_json"]) if row.get("importance_reasons_json") else [],
    }


def create_snapshot(
    connection: sqlite3.Connection,
    state_dir: Path,
    *,
    snapshot_kind: str,
    run_id: str | None,
    stamp: str,
) -> dict[str, Any]:
    files = connection.execute("SELECT COUNT(*),COALESCE(SUM(size),0),MAX(last_seen) FROM files WHERE status='present'").fetchone()
    aggregates = connection.execute("SELECT COALESCE(SUM(file_count),0),COALESCE(SUM(total_size),0) FROM aggregate_nodes").fetchone()
    authorities = int(connection.execute("SELECT COUNT(*) FROM assets WHERE authority_level IN ('PRIMARY','CANONICAL','ACTIVE')").fetchone()[0])
    archive = int(connection.execute(
        "SELECT COALESCE(SUM(f.size),0) FROM assets a JOIN files f ON f.path_key=a.path_key WHERE f.status='present' AND a.archive_recommendation LIKE 'REVIEW_%'"
    ).fetchone()[0])
    cleanup = int(aggregates[1])
    projects = [dict(row) for row in connection.execute(
        """SELECT p.project_id,p.name,p.lifecycle,p.root_path,COALESCE(pa.activity_status,'UNKNOWN') AS activity_status
           FROM projects p LEFT JOIN project_activity pa ON pa.project_id=p.project_id
           WHERE p.status='understood' ORDER BY p.name COLLATE NOCASE"""
    )]
    disk_total = disk_used = disk_free = 0
    volume_roots = {Path(row[0]).anchor for row in connection.execute("SELECT DISTINCT root_path FROM files") if row[0]}
    volume_roots.add(state_dir.anchor or str(state_dir))
    observed_volumes = 0
    for volume in sorted(item for item in volume_roots if item):
        try:
            usage = shutil.disk_usage(volume)
        except OSError:
            continue
        disk_total += int(usage.total); disk_used += int(usage.used); disk_free += int(usage.free); observed_volumes += 1
    if not observed_volumes:
        disk_total = disk_used = disk_free = None
    digest_payload = {
        "files": int(files[0]), "size": int(files[1]), "max_seen": files[2], "aggregates": int(aggregates[1]),
        "projects": len(projects), "authorities": authorities,
    }
    digest = hashlib.sha256(_json(digest_payload).encode("utf-8")).hexdigest()
    snapshot_id = "snapshot_" + uuid.uuid4().hex
    important_assets = [dict(row) for row in connection.execute(
        """SELECT a.project_id,a.path_key,a.role,a.authority_level,a.authority_scope,f.path,f.size
           FROM assets a LEFT JOIN files f ON f.path_key=a.path_key
           WHERE a.authority_level IN ('PRIMARY','CANONICAL','ACTIVE') ORDER BY a.confidence DESC LIMIT 100"""
    )]
    connection.execute(
        """INSERT INTO snapshots(snapshot_id,created_at,snapshot_kind,run_id,files_count,logical_size,disk_total,disk_used,disk_free,
           project_count,active_projects,frozen_projects,authority_asset_count,aggregate_size,potential_cleanup,potential_archive,
           important_asset_summary_json,catalog_digest) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            snapshot_id, stamp, snapshot_kind, run_id, int(files[0]) + int(aggregates[0]), int(files[1]) + int(aggregates[1]),
            disk_total, disk_used, disk_free, len(projects), sum(1 for p in projects if p["activity_status"] == "ACTIVE"),
            sum(1 for p in projects if p["activity_status"] in {"FROZEN", "COMPLETED"}), authorities, int(aggregates[1]),
            cleanup, archive, _json(important_assets), digest,
        ),
    )
    for project in projects:
        project_id = project["project_id"]
        sizes = connection.execute("SELECT COUNT(*),COALESCE(SUM(size),0) FROM files WHERE status='present' AND project_id=?", (project_id,)).fetchone()
        aggregate_size = int(connection.execute("SELECT COALESCE(SUM(total_size),0) FROM aggregate_nodes WHERE project_id=?", (project_id,)).fetchone()[0])
        workstreams = [dict(row) for row in connection.execute(
            "SELECT node_id,name,lifecycle FROM project_nodes WHERE project_id=? AND node_type='workstream' ORDER BY name", (project_id,)
        )]
        authority_summary = [dict(row) for row in connection.execute(
            """SELECT path_key,role,authority_level,authority_scope,confidence FROM assets WHERE project_id=?
               AND authority_level IN ('PRIMARY','CANONICAL','ACTIVE') ORDER BY confidence DESC LIMIT 30""", (project_id,)
        )]
        recent = [_public_event(dict(row)) for row in connection.execute(
            "SELECT * FROM events WHERE project_id=? ORDER BY occurred_at DESC,event_id DESC LIMIT 20", (project_id,)
        )]
        connection.execute(
            """INSERT INTO project_snapshots(snapshot_id,project_id,project_name,total_size,file_count,lifecycle,activity_status,
               workstream_status_json,authority_summary_json,recent_activity_json) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (snapshot_id, project_id, project["name"], int(sizes[1]) + aggregate_size, int(sizes[0]), project.get("lifecycle"),
             project.get("activity_status"), _json(workstreams), _json(authority_summary), _json(recent)),
        )
    return {
        "snapshot_id": snapshot_id, "created_at": stamp, "snapshot_kind": snapshot_kind,
        "files_count": int(files[0]) + int(aggregates[0]), "logical_size": int(files[1]) + int(aggregates[1]),
        "project_count": len(projects), "authority_asset_count": authorities, "aggregate_size": int(aggregates[1]),
        "potential_cleanup": cleanup, "potential_archive": archive, "catalog_digest": digest,
    }


def _range(
    connection: sqlite3.Connection,
    *,
    since_last_scan: bool = False,
    days: int | None = None,
    from_value: str | None = None,
    to_value: str | None = None,
) -> tuple[str | None, str, str | None]:
    end = _utc(to_value).isoformat(timespec="seconds") if to_value else datetime.now(timezone.utc).isoformat(timespec="seconds")
    if since_last_scan:
        run = connection.execute(
            "SELECT run_id,created_at FROM runs WHERE mode IN ('MAINTENANCE','WEEKLY_DEEP_MAINTENANCE') ORDER BY created_at DESC,rowid DESC LIMIT 1"
        ).fetchone()
        return (run["created_at"] if run else None), end, (run["run_id"] if run else None)
    if from_value:
        start = _utc(from_value).isoformat(timespec="seconds")
    elif days is not None:
        start = (datetime.now(timezone.utc) - timedelta(days=max(0, days))).isoformat(timespec="seconds")
    else:
        start = None
    return start, end, None


def query_timeline(
    connection: sqlite3.Connection,
    *,
    since_last_scan: bool = False,
    days: int | None = None,
    from_value: str | None = None,
    to_value: str | None = None,
    project: str | None = None,
    event_type: str | None = None,
    importance: str | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    start, end, run_id = _range(connection, since_last_scan=since_last_scan, days=days, from_value=from_value, to_value=to_value)
    clauses = ["occurred_at<=?"]
    parameters: list[Any] = [end]
    if start:
        clauses.append("occurred_at>=?"); parameters.append(start)
    if run_id:
        clauses.append("run_id=?"); parameters.append(run_id)
    project_id = None
    if project:
        match = connection.execute("SELECT project_id FROM projects WHERE project_id=? OR name=? COLLATE NOCASE LIMIT 1", (project, project)).fetchone()
        project_id = match[0] if match else project
        clauses.append("project_id=?"); parameters.append(project_id)
    if event_type:
        clauses.append("event_type=?"); parameters.append(event_type)
    if importance:
        clauses.append("semantic_importance=?"); parameters.append(importance.upper())
    parameters.append(max(1, min(limit, 5000)))
    rows = [dict(row) for row in connection.execute(
        f"SELECT * FROM events WHERE {' AND '.join(clauses)} ORDER BY occurred_at DESC,importance_score DESC,event_id DESC LIMIT ?",
        parameters,
    )]
    return {
        "time_range": {"from": start, "to": end, "run_id": run_id}, "project_id": project_id,
        "event_count": len(rows), "events": [_public_event(row) for row in rows], "physical_actions": 0,
    }


def context_summary(connection: sqlite3.Connection, **filters: Any) -> dict[str, Any]:
    payload = query_timeline(connection, limit=5000, **filters)
    events = payload["events"]
    projects: dict[str, dict[str, Any]] = {}
    for event in events:
        key = event.get("project_id") or "unassigned"
        group = projects.setdefault(key, {"project_id": event.get("project_id"), "event_count": 0, "meaningful": 0, "size_delta": 0, "top": None})
        group["event_count"] += int(event.get("aggregate_count") or 1)
        if event.get("importance") not in {"LOW", "VOLATILE"}:
            group["meaningful"] += 1
        if event["event_type"] in STORAGE_EVENT_TYPES:
            group["size_delta"] += int(event.get("size_delta") or 0)
        if group["top"] is None or event["importance_score"] > group["top"]["importance_score"]:
            group["top"] = event
    project_changes = []
    for key, group in projects.items():
        name = "Unassigned / system aggregate"
        if key != "unassigned":
            row = connection.execute("SELECT name FROM projects WHERE project_id=?", (key,)).fetchone()
            name = row[0] if row else key
        project_changes.append({**group, "project_name": name})
    project_changes.sort(key=lambda item: (-(item["top"] or {}).get("importance_score", 0), -abs(item["size_delta"])))
    alerts = [dict(row) for row in connection.execute(
        "SELECT alert_id,file_id,project_id,path_before,alert_type,status,importance,first_seen FROM asset_alerts WHERE status='OPEN' ORDER BY first_seen DESC"
    )]
    important = [event for event in events if event["importance"] not in {"LOW", "VOLATILE"}]
    return {
        "time_range": payload["time_range"], "important_changes": important[:30], "project_changes": project_changes,
        "storage_changes": sorted(({"project_id": item["project_id"], "project_name": item["project_name"], "size_delta": item["size_delta"]} for item in project_changes if item["size_delta"]), key=lambda item: -abs(item["size_delta"])),
        "asset_alerts": alerts, "uncertainties": [event for event in events if event.get("resolution_status") in {"unverified_candidate", "unresolved_missing"}][:30],
        "raw_event_count": len(events), "meaningful_event_count": len(important),
        "volatile_raw_change_count": sum(int(event.get("aggregate_count") or 1) for event in events if event["importance"] == "VOLATILE"),
        "physical_actions": 0,
    }


def file_history(connection: sqlite3.Connection, value: str, limit: int = 500) -> dict[str, Any]:
    card = connection.execute("SELECT * FROM file_cards WHERE file_id=?", (value,)).fetchone()
    if card is None:
        path_key = os.path.normcase(os.path.abspath(value)).casefold()
        row = connection.execute("SELECT file_id FROM files WHERE path_key=? ORDER BY status='present' DESC LIMIT 1", (path_key,)).fetchone()
        if row:
            card = connection.execute("SELECT * FROM file_cards WHERE file_id=?", (row["file_id"],)).fetchone()
    if card is None:
        raise ValueError(f"No FileCard exists for: {value}")
    events = [dict(row) for row in connection.execute(
        "SELECT * FROM events WHERE file_id=? ORDER BY occurred_at DESC,event_id DESC LIMIT ?", (card["file_id"], max(1, min(limit, 5000)))
    )]
    return {"file_card": dict(card), "events": [_public_event(row) for row in events], "physical_actions": 0}


def project_history(connection: sqlite3.Connection, value: str, limit: int = 1000) -> dict[str, Any]:
    project = connection.execute("SELECT * FROM projects WHERE project_id=? OR name=? COLLATE NOCASE LIMIT 1", (value, value)).fetchone()
    if project is None:
        raise ValueError(f"No project matches: {value}")
    events = [dict(row) for row in connection.execute(
        "SELECT * FROM events WHERE project_id=? ORDER BY occurred_at DESC,event_id DESC LIMIT ?", (project["project_id"], max(1, min(limit, 5000)))
    )]
    snapshots = [dict(row) for row in connection.execute(
        """SELECT ps.* FROM project_snapshots ps
           JOIN snapshots s ON s.snapshot_id=ps.snapshot_id
           WHERE ps.project_id=? ORDER BY s.created_at DESC,s.rowid DESC LIMIT 100""", (project["project_id"],)
    )]
    return {"project": dict(project), "events": [_public_event(row) for row in events], "snapshots": snapshots, "physical_actions": 0}


def storage_growth(connection: sqlite3.Connection, *, days: int = 7, project: str | None = None) -> dict[str, Any]:
    comparable = """NOT EXISTS(
        SELECT 1 FROM runs r WHERE r.run_id=snapshots.run_id AND r.mode='STATE_CUTOVER'
    )"""
    end = connection.execute(
        f"SELECT * FROM snapshots WHERE {comparable} ORDER BY created_at DESC,rowid DESC LIMIT 1"
    ).fetchone()
    if end is None:
        return {"status": "NO_SNAPSHOTS", "days": days, "projects": [], "physical_actions": 0}
    target = (_utc(end["created_at"]) - timedelta(days=max(0, days))).isoformat(timespec="seconds")
    start = connection.execute(
        f"SELECT * FROM snapshots WHERE {comparable} AND created_at<=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
        (target,),
    ).fetchone()
    if start is None:
        start = connection.execute(
            f"SELECT * FROM snapshots WHERE {comparable} AND snapshot_id!=? ORDER BY created_at ASC LIMIT 1",
            (end["snapshot_id"],),
        ).fetchone()
    if start is None:
        return {"status": "INSUFFICIENT_HISTORY", "days": days, "from": None, "to": end["created_at"], "projects": [], "physical_actions": 0}
    clauses = ""
    params: list[Any] = [start["snapshot_id"], end["snapshot_id"]]
    if project:
        match = connection.execute("SELECT project_id FROM projects WHERE project_id=? OR name=? COLLATE NOCASE LIMIT 1", (project, project)).fetchone()
        clauses = " AND e.project_id=?"; params.append(match[0] if match else project)
    rows = [dict(row) for row in connection.execute(
        f"""SELECT e.project_id,e.project_name,s.total_size AS start_size,e.total_size AS end_size,
            e.total_size-s.total_size AS size_delta FROM project_snapshots s JOIN project_snapshots e ON e.project_id=s.project_id
            WHERE s.snapshot_id=? AND e.snapshot_id=?{clauses} ORDER BY ABS(e.total_size-s.total_size) DESC""", params
    )]
    return {
        "status": "OK", "days": days, "from": start["created_at"], "to": end["created_at"],
        "machine_size_delta": int(end["logical_size"]) - int(start["logical_size"]), "projects": rows,
        "excluded_state_cutover_snapshots": int(connection.execute(
            """SELECT COUNT(*) FROM snapshots s JOIN runs r ON r.run_id=s.run_id
               WHERE r.mode='STATE_CUTOVER'"""
        ).fetchone()[0]),
        "physical_actions": 0,
    }


def retention_plan(connection: sqlite3.Connection, *, apply: bool = False, now: str | None = None) -> dict[str, Any]:
    current = _utc(now)
    volatile_cutoff = (current - timedelta(days=30)).isoformat(timespec="seconds")
    daily_cutoff = (current - timedelta(days=90)).isoformat(timespec="seconds")
    volatile = int(connection.execute(
        "SELECT COUNT(*) FROM events WHERE semantic_importance='VOLATILE' AND permanent=0 AND occurred_at<?", (volatile_cutoff,)
    ).fetchone()[0])
    daily = int(connection.execute(
        "SELECT COUNT(*) FROM snapshots WHERE snapshot_kind='daily' AND created_at<?", (daily_cutoff,)
    ).fetchone()[0])
    plan = {
        "action": "RETENTION", "apply": apply, "volatile_events_eligible": volatile,
        "daily_snapshots_eligible": daily, "authority_or_high_importance_events_eligible": 0,
        "policy": {
            "volatile_events": "30 days", "daily_snapshots": "90 days then keep weekly/monthly summaries",
            "high_and_authority_events": "permanent", "weekly_snapshots": "long term", "monthly_summaries": "long term",
        },
        "physical_project_actions": 0,
    }
    if not apply:
        return plan
    connection.execute("DELETE FROM events WHERE semantic_importance='VOLATILE' AND permanent=0 AND occurred_at<?", (volatile_cutoff,))
    old_daily_ids = [row[0] for row in connection.execute("SELECT snapshot_id FROM snapshots WHERE snapshot_kind='daily' AND created_at<?", (daily_cutoff,))]
    connection.executemany("DELETE FROM project_snapshots WHERE snapshot_id=?", ((item,) for item in old_daily_ids))
    connection.executemany("DELETE FROM snapshots WHERE snapshot_id=?", ((item,) for item in old_daily_ids))
    connection.commit()
    return {**plan, "applied": True}


def schedule_plan(skill_root: Path, state_dir: Path) -> dict[str, Any]:
    runner = skill_root / "scripts" / "run_scheduled_maintenance.ps1"
    return {
        "installed": False,
        "reason": "Scheduler support is generated but no Windows task is created without an explicit user time configuration and approval.",
        "runner": str(runner), "state_dir": str(state_dir),
        "profiles": {
            "daily": {"operations": ["metadata diff", "changed-file identity", "semantic diff", "daily snapshot"], "resource_policy": "lightweight"},
            "weekly": {"operations": ["daily operations", "affected-project bounded refresh", "weekly snapshot", "dashboard"], "resource_policy": "bounded; no solver launch"},
        },
        "task_scheduler": {"view": "schtasks /Query", "disable": "schtasks /Change /TN <name> /DISABLE", "remove": "schtasks /Delete /TN <name>"},
        "physical_project_actions": 0,
    }


def build_timeline_page(connection: sqlite3.Connection, state_dir: Path) -> Path:
    rows = [dict(row) for row in connection.execute("SELECT * FROM events ORDER BY occurred_at DESC,importance_score DESC LIMIT 1000")]
    cards = []
    for row in rows:
        public = _public_event(row)
        project_name = "System / unassigned"
        if row.get("project_id"):
            found = connection.execute("SELECT name FROM projects WHERE project_id=?", (row["project_id"],)).fetchone()
            project_name = found[0] if found else row["project_id"]
        path = public.get("path_after") or public.get("path_before") or ""
        aggregate_note = f"<small>{public['aggregate_count']:,} aggregate members</small>" if public["aggregate_count"] > 1 else ""
        cards.append(
            f"<article data-project='{_h(project_name)}' data-type='{_h(public['event_type'])}' data-importance='{_h(public['importance'])}'>"
            f"<time>{_h(public['timestamp'])}</time><div><b>{_h(project_name)}</b><h3>{_h(public['event_type'])}</h3>"
            f"<p>{_h(path)}</p>{aggregate_note}</div><span class='level {_h(public['importance'].lower())}'>{_h(public['importance'])}</span></article>"
        )
    body = f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Computer Timeline</title><style>
body{{margin:0;background:#f3f6fa;color:#172033;font:14px/1.45 Segoe UI,Arial}}main{{max-width:1120px;margin:auto;padding:34px 22px 70px}}
h1{{font-size:32px;margin:0}}.lead{{color:#667085}}.filters{{display:flex;gap:10px;flex-wrap:wrap;margin:22px 0}}input,select{{padding:9px 12px;border:1px solid #ced6e0;border-radius:8px;background:white}}
article{{display:grid;grid-template-columns:180px 1fr auto;gap:18px;background:white;border:1px solid #dfe5ec;border-radius:12px;padding:16px;margin:10px 0}}time,p{{color:#687386}}h3{{margin:3px 0}}p{{margin:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.level{{border-radius:99px;padding:5px 9px;height:max-content;font-weight:700}}.very_high,.high{{background:#ffe9e7;color:#a62b21}}.medium{{background:#fff2cc;color:#7a5400}}.low{{background:#e8f0ff;color:#2656b8}}.volatile{{background:#edf0f3;color:#687386}}
@media(max-width:700px){{article{{grid-template-columns:1fr}}}}
</style></head><body><main><h1>Computer Timeline</h1><p class='lead'>Private append-oriented history. Inferences retain confidence and evidence; no file operation is available.</p>
<div class='filters'><input id='q' placeholder='Filter project or path'><select id='importance'><option value=''>All importance</option><option>VERY_HIGH</option><option>HIGH</option><option>MEDIUM</option><option>LOW</option><option>VOLATILE</option></select></div>
<section id='events'>{''.join(cards) if cards else '<article><div>No historical events recorded yet.</div></article>'}</section>
<script>const q=document.querySelector('#q'),i=document.querySelector('#importance');function f(){{document.querySelectorAll('#events article').forEach(x=>{{const text=x.innerText.toLowerCase(),ok=(!q.value||text.includes(q.value.toLowerCase()))&&(!i.value||x.dataset.importance===i.value);x.style.display=ok?'':'none'}})}}q.oninput=f;i.onchange=f;</script>
</main></body></html>"""
    state_dir.mkdir(parents=True, exist_ok=True)
    target = state_dir / TIMELINE_NAME
    temporary = target.with_suffix(".html.tmp")
    temporary.write_text(body, encoding="utf-8")
    temporary.replace(target)
    return target


def _h(value: Any) -> str:
    import html
    return html.escape(str(value or ""), quote=True)
