from __future__ import annotations

import html
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from . import __version__


DASHBOARD_NAME = "File Intelligence Home.html"


def _human_size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"


def _escape(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def build_dashboard(connection: sqlite3.Connection, state_dir: Path, *, last_summary: dict[str, Any] | None = None) -> Path:
    indexed = connection.execute(
        "SELECT COUNT(*),COALESCE(SUM(size),0) FROM files WHERE status='present'"
    ).fetchone()
    aggregated = connection.execute(
        "SELECT COALESCE(SUM(file_count),0),COALESCE(SUM(total_size),0) FROM aggregate_nodes"
    ).fetchone()
    machine = (int(indexed[0]) + int(aggregated[0]), int(indexed[1]) + int(aggregated[1]))
    projects = [dict(row) for row in connection.execute(
        """SELECT p.*,COALESCE(pa.activity_status,'UNKNOWN') AS activity_status,pa.last_meaningful_activity
           FROM projects p LEFT JOIN project_activity pa ON pa.project_id=p.project_id
           WHERE p.status='understood' AND p.root_path IS NOT NULL ORDER BY p.lifecycle,p.name COLLATE NOCASE"""
    )]
    lifecycle_counts = Counter((project.get("lifecycle") or "UNKNOWN") for project in projects)
    cards: list[str] = []
    for project in projects:
        authorities = [dict(row) for row in connection.execute(
            """SELECT COALESCE(f.path,a.entity_path) AS path,a.role,a.authority_level,a.authority_scope,a.confidence FROM assets a
               LEFT JOIN files f ON f.path_key=a.path_key WHERE a.project_id=?
               AND a.authority_level IN ('PRIMARY','CANONICAL','ACTIVE')
               AND a.authority_scope IN ('PROJECT_WIDE','FILE_LOCAL')
               ORDER BY a.confidence DESC LIMIT 8""",
            (project["project_id"],),
        )]
        workstreams = [dict(row) for row in connection.execute(
            "SELECT name,lifecycle,confidence FROM project_nodes WHERE project_id=? AND node_type='workstream' ORDER BY name",
            (project["project_id"],),
        )]
        dependencies = connection.execute("SELECT COUNT(*) FROM dependencies WHERE project_id=?", (project["project_id"],)).fetchone()[0]
        archive = connection.execute(
            """SELECT COUNT(*),COALESCE(SUM(f.size),0) FROM assets a
               JOIN files f ON f.path_key=a.path_key
               WHERE a.project_id=? AND a.archive_recommendation LIKE 'REVIEW_%'""",
            (project["project_id"],),
        ).fetchone()
        large_assets = [dict(row) for row in connection.execute(
            """SELECT f.path,f.size,a.role,a.authority_level FROM files f
               LEFT JOIN assets a ON a.path_key=f.path_key
               WHERE a.project_id=? AND f.status='present' ORDER BY f.size DESC LIMIT 6""",
            (project["project_id"],),
        )]
        aggregate_assets = [dict(row) for row in connection.execute(
            """SELECT path,total_size AS size,file_count,kind AS role,'AGGREGATE' AS authority_level
               FROM aggregate_nodes WHERE project_id=? ORDER BY total_size DESC LIMIT 4""",
            (project["project_id"],),
        )]
        tools = [dict(row) for row in connection.execute(
            "SELECT tool_name,centrality,score FROM project_tools WHERE project_id=? ORDER BY score DESC", (project["project_id"],)
        )]
        workstream_html = "".join(
            f"<li><span>{_escape(item['name'])}</span><b>{_escape(item['lifecycle'] or 'UNKNOWN')}</b> <small>{float(item['confidence']):.0%}</small></li>"
            for item in workstreams
        ) or "<li>No workstream established yet</li>"
        authority_html = "".join(
            f"<li title='{_escape(item['path'])}'><span>{_escape(Path(item['path']).name)}</span><b>{_escape(item['role'])}</b> <small>{float(item['confidence']):.0%}</small></li>"
            for item in authorities
        ) or "<li>No authority established yet</li>"
        large_html = "".join(
            f"<li title='{_escape(item['path'])}'><span>{_escape(Path(item['path']).name)}</span>"
            f"<b>{_human_size(int(item['size']))}</b> <small>{_escape(item.get('role') or 'unknown')}"
            f"{(' · ' + format(int(item['file_count']), ',') + ' members') if item.get('file_count') is not None else ''}</small></li>"
            for item in (large_assets + aggregate_assets)[:8]
        ) or "<li>No large-asset summary yet</li>"
        tool_html = " · ".join(f"{_escape(item['tool_name'])} <b>{_escape(item['centrality'])}</b>" for item in tools[:5]) or "Tool centrality not established"
        cards.append(
            "<article class='project'>"
            f"<header><div><h2>{_escape(project['name'])}</h2><p>{_escape(project.get('purpose'))}</p></div>"
            f"<span class='badge'>{_escape(project.get('activity_status') or 'UNKNOWN')}</span></header>"
            f"<div class='metrics'><span>{project['file_count']:,} files</span><span>{_human_size(project['total_size'])}</span>"
            f"<span>{dependencies:,} references</span><span>{int(archive[0]):,} review candidates / {_human_size(int(archive[1]))}</span>"
            f"<span>{tool_html}</span></div>"
            f"<div class='columns'><section><h3>Workstreams</h3><ul>{workstream_html}</ul></section>"
            f"<section><h3>Authority</h3><ul>{authority_html}</ul></section>"
            f"<section><h3>Large assets &amp; aggregates</h3><ul>{large_html}</ul></section></div></article>"
        )
    recent = last_summary or {}
    meaningful = recent.get("meaningful_changes", recent.get("semantic_summary", {}).get("meaningful_event_count", 0))
    volatile = recent.get("volatile_changes", 0)
    snapshots = [dict(row) for row in connection.execute("SELECT * FROM snapshots ORDER BY created_at DESC,rowid DESC LIMIT 2")]
    latest_snapshot = snapshots[0] if snapshots else {}
    storage_delta = int(latest_snapshot.get("logical_size") or machine[1]) - int(snapshots[1].get("logical_size") or machine[1]) if len(snapshots) > 1 else 0
    alerts = int(connection.execute("SELECT COUNT(*) FROM asset_alerts WHERE status='OPEN'").fetchone()[0])
    active_projects = sum(1 for project in projects if project.get("activity_status") == "ACTIVE")
    recent_events = [dict(row) for row in connection.execute(
        """SELECT e.*,COALESCE(p.name,'System / unassigned') AS project_name FROM events e
           LEFT JOIN projects p ON p.project_id=e.project_id
           WHERE e.semantic_importance NOT IN ('LOW','VOLATILE') ORDER BY e.occurred_at DESC,e.importance_score DESC LIMIT 12"""
    )]
    recent_html = "".join(
        f"<article class='change'><div><small>{_escape(item['occurred_at'])}</small><h3>{_escape(item['project_name'])}</h3>"
        f"<p>{_escape(item['event_type'])} · {_escape(item.get('path_after') or item.get('path_before') or '')}</p></div>"
        f"<b>{_escape(item['semantic_importance'])}</b></article>" for item in recent_events
    ) or "<p class='empty'>No meaningful historical change has been recorded yet.</p>"
    body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Computer Intelligence</title>
<style>
:root{{--ink:#172033;--muted:#687386;--line:#dfe5ec;--paper:#f5f7fa;--accent:#2859d6;--good:#19714d}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--paper);color:var(--ink);font:14px/1.45 Segoe UI,Arial,sans-serif}}
main{{max-width:1240px;margin:auto;padding:36px 24px 72px}} h1{{font-size:32px;margin:0}} .lead{{color:var(--muted);margin:6px 0 26px}}
.overview{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:24px}}
.stat,.project{{background:white;border:1px solid var(--line);border-radius:14px;box-shadow:0 4px 16px #1d2a4410}}
.stat{{padding:16px}} .stat b{{display:block;font-size:22px}} .stat span,small{{color:var(--muted)}}
.project{{padding:20px;margin:16px 0}} .project header{{display:flex;justify-content:space-between;gap:20px}} h2{{margin:0;font-size:22px}} h3{{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}}
.project p{{color:var(--muted);margin:4px 0}} .badge{{height:max-content;background:#eaf0ff;color:var(--accent);padding:5px 10px;border-radius:99px;font-weight:700}}
.metrics{{display:flex;flex-wrap:wrap;gap:14px;margin:15px 0;padding:10px 0;border-block:1px solid var(--line)}}
.columns{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:28px}} ul{{list-style:none;padding:0;margin:0}} li{{display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:8px;padding:5px 0;border-bottom:1px dotted var(--line)}} li span{{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.notice{{border-left:4px solid var(--good);padding:10px 14px;background:#edf8f3;margin:20px 0}} @media(max-width:700px){{.columns{{grid-template-columns:1fr}}}}
.change{{display:flex;justify-content:space-between;gap:20px;background:white;border:1px solid var(--line);border-radius:12px;padding:13px 16px;margin:8px 0}} .change h3,.change p{{margin:2px 0}} .timeline-link{{float:right;color:var(--accent);font-weight:700;text-decoration:none}}
</style></head><body><main>
<a class="timeline-link" href="Computer Timeline.html">Open Computer Timeline →</a><h1>Computer Intelligence</h1><p class="lead">File Intelligence v{_escape(__version__)} · private, local, read-only memory of current files, projects, storage and historical change.</p>
<section class="overview">
<div class="stat"><b>{int(machine[0]):,}</b><span>present files</span></div>
<div class="stat"><b>{_human_size(int(machine[1]))}</b><span>catalogued size</span></div>
<div class="stat"><b>{len(projects):,}</b><span>understood projects</span></div>
<div class="stat"><b>{active_projects:,}</b><span>active projects</span></div>
<div class="stat"><b>{_human_size(storage_delta)}</b><span>since last snapshot</span></div>
<div class="stat"><b>{int(meaningful):,}</b><span>meaningful changes</span></div>
<div class="stat"><b>{alerts:,}</b><span>important alerts</span></div>
<div class="stat"><b>{_human_size(int(latest_snapshot.get('potential_cleanup') or 0))}</b><span>potential cleanup</span></div>
<div class="stat"><b>{_human_size(int(latest_snapshot.get('potential_archive') or 0))}</b><span>potential archive</span></div>
<div class="stat"><b>{_human_size(int(latest_snapshot.get('disk_used') or 0))} / {_human_size(int(latest_snapshot.get('disk_total') or 0))}</b><span>disk used / total</span></div>
</section>
<div class="notice"><b>Latest semantic status:</b> {_escape(recent.get('semantic_status','BASELINE'))} · meaningful {int(meaningful):,} · volatile {int(volatile):,}</div>
<h2>Recent meaningful changes</h2>{recent_html}
<h2>Projects</h2>
{''.join(cards) if cards else '<article class="project"><h2>Project Understanding not run yet</h2><p>Catalog is ready; run a targeted read-only project analysis.</p></article>'}
</main></body></html>"""
    state_dir.mkdir(parents=True, exist_ok=True)
    target = state_dir / DASHBOARD_NAME
    temporary = target.with_suffix(".html.tmp")
    temporary.write_text(body, encoding="utf-8")
    temporary.replace(target)
    summary_path = state_dir / "project_understanding_summary.json"
    summary = {
        "projects": projects,
        "machine": {"files": int(machine[0]), "size": int(machine[1])},
        "dashboard": str(target),
        "physical_actions": 0,
    }
    temporary_json = summary_path.with_suffix(".json.tmp")
    temporary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary_json.replace(summary_path)
    return target
