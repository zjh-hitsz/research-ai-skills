# File Intelligence

File Intelligence 0.3 is a public-safe, read-only Windows knowledge engine for answering “what exists?”, “what role does it play?”, and “what changed over time?”. It combines a current catalog with stable FileCards, an append-oriented Event Store, lightweight snapshots, and semantic change summaries. The repository contains generic code, schemas, documentation, and synthetic tests only. Machine catalogs, paths, events, snapshots, fingerprints, assertions, dashboards, and real benchmark results belong in external local state.

## Capabilities

- Everything `es.exe` or filesystem discovery with path-set consistency reporting;
- SQLite catalog and explicit, backup-first v1-to-v2 migration;
- hierarchical Project, Workstream, Asset Group, Deliverable, and Archive nodes;
- asset role, authority level, confidence, provenance, supersession, and review-only archive recommendation;
- references and `DECLARES_AUTHORITY` edges extracted from text, code, notebooks, manifests, and Office documents;
- bounded DOCX, PPTX, PDF, ZIP, HDF5, COMSOL-container, Fluent-container, and source-code inspection;
- Stage 0 size, Stage 1 head/middle/tail sample hash, and on-demand Stage 2 full SHA-256;
- `MOVED`, `RENAMED`, and `COPIED` identity evidence;
- raw-versus-volatile-versus-meaningful maintenance results;
- aggregate `.venv`, `node_modules`, cache, build, render, preview, and temporary nodes;
- explicit local user assertions and a private HTML dashboard.
- stable file identity separated from paths, using native file metadata when available and staged content evidence otherwise;
- historical `FileEvent`/project/workstream events that survive later Maintenance runs;
- daily/weekly/manual materialized snapshots without copying the catalog or user files;
- transparent meaningful-change scoring, important-asset alerts, project activity, storage history, and time-range queries;
- `PROJECT_WIDE`, `WORKSTREAM_LOCAL`, `GATE_LOCAL`, and `FILE_LOCAL` authority scope;
- `PRIMARY_TOOL`, `SECONDARY_TOOL`, and `REFERENCE_TOOL` purpose centrality;
- a Computer Intelligence home page plus a separate filterable Computer Timeline page.

There is no physical file-operation executor.

## Requirements

- Windows PowerShell 5.1 or PowerShell 7;
- Python 3.10 or later;
- optional official Everything plus `es.exe` for indexed discovery;
- optional `pypdf`, `h5py`, and `psutil` from `requirements-optional.txt` for richer PDF/HDF5 inspection and benchmark memory metrics.

The core runtime is Python standard-library only. Installation never installs system software or changes Everything configuration.

## Install for Codex

Ask Codex to install repository path `skills/file-intelligence`, or from a clone run:

```powershell
& .\skills\file-intelligence\scripts\install.ps1
```

The code destination is the current user's Codex Skill directory. Private state defaults to `%LOCALAPPDATA%\FileIntelligence`.

## First run and migration

```powershell
python scripts\file_intelligence_cli.py status
python scripts\file_intelligence_cli.py onboard --root <folder> --backend auto
```

Default Maintenance inherits the baseline discovery backend. This prevents filesystem/Everything timestamp-representation differences from becoming false file changes; an explicit `--backend` remains available for a reviewed backend transition.

An existing schema-v1 or schema-v2 catalog reports `MIGRATION_REQUIRED`; no read command upgrades it silently:

```powershell
python scripts\file_intelligence_cli.py migrate
python scripts\file_intelligence_cli.py migrate --apply
```

The apply command creates a SHA-256 backup manifest under the external state's `migrations` folder. `rollback` previews restoration unless `--apply` is also given.

## Project and asset queries

```powershell
python scripts\file_intelligence_cli.py understand --project-root <folder> --summary-only
python scripts\file_intelligence_cli.py asset --path <file>
python scripts\file_intelligence_cli.py asset --path <large-file> --verify-full-hash
python scripts\file_intelligence_cli.py dashboard
python scripts\file_intelligence_cli.py timeline --since-last-scan
python scripts\file_intelligence_cli.py context-summary --days 7 --project <name-or-id>
python scripts\file_intelligence_cli.py project-history <name-or-id>
python scripts\file_intelligence_cli.py file-history <path-or-file-id>
python scripts\file_intelligence_cli.py storage-growth --days 7
```

`understand` is deterministic and evidence-first. It does not send filenames or content to an LLM or network service. Optional future semantic inference must remain subordinate to explicit metadata and user assertions.

## Maintenance

```powershell
python scripts\file_intelligence_cli.py maintain
python scripts\file_intelligence_cli.py changes
```

An unchanged run performs no content inspections or new hashes. Volatile changes remain visible but do not turn a maintenance result into a meaningful project change.

Daily Maintenance scans metadata and changed candidates, appends evidence-backed events, creates a summary snapshot, and refreshes both local pages. `maintain --deep --snapshot-kind weekly` additionally refreshes only affected understood projects with bounded inspectors. It never launches a solver.

`schedule-plan` describes the bundled Task Scheduler-compatible runner but installs nothing. The runner writes logs below the private state directory. `retention` previews removal of old volatile events and old daily snapshots; only explicit `--apply` changes private state, and high/authority events are retained.

## Optional inspectors

```powershell
python -m pip install -r requirements-optional.txt
```

Without optional packages, PDF/HDF5 files still receive bounded magic/container metadata and an explicit “backend unavailable” result. Heavy research software is never started by default.

## Release validation

```powershell
python scripts\privacy_audit.py --root . --report FILE_INTELLIGENCE_PUBLIC_RELEASE_AUDIT.md
python -m unittest discover -s tests -v
python scripts\verify_fresh_install.py --source .
python scripts\benchmark_backends.py --root <synthetic-or-reviewed-root> --state-parent <external-state> --everything-cli <es.exe> --output <external-report.json>
python scripts\benchmark_maintenance.py --workspace <external-state> --output <external-report.json>
python scripts\benchmark_timeline.py --workspace <external-state> --output <external-report.json>
python scripts\benchmark_project_timeline_noop.py --project-root <reviewed-root> --state-parent <external-state> --output <external-report.json>
```

Real benchmark reports must be written outside the repository.
