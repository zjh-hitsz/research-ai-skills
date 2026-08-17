# File Intelligence

File Intelligence 0.2 is a public-safe, read-only Windows knowledge engine for answering both “where is this file?” and “what role does it play in the project?”. The repository contains generic code, schemas, documentation, and synthetic tests only. Machine catalogs, project names, paths, fingerprints, assertions, communication evidence, dashboards, and real benchmark results belong in external local state.

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

An existing 0.1/schema-v1 catalog reports `MIGRATION_REQUIRED`; no read command upgrades it silently:

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
```

`understand` is deterministic and evidence-first. It does not send filenames or content to an LLM or network service. Optional future semantic inference must remain subordinate to explicit metadata and user assertions.

## Maintenance

```powershell
python scripts\file_intelligence_cli.py maintain
python scripts\file_intelligence_cli.py changes
```

An unchanged run performs no content inspections or new hashes. Volatile changes remain visible but do not turn a maintenance result into a meaningful project change.

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
```

Real benchmark reports must be written outside the repository.
