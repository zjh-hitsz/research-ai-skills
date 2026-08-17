---
name: file-intelligence
description: Build and maintain a private, read-only Windows file and project knowledge base. Use when Codex must onboard a machine or folder; use Everything/ES or filesystem discovery; infer hierarchical projects and workstreams; explain asset roles, authority, provenance, dependencies, duplicates, move/copy events, large-file fingerprints, aggregate environments, or meaningful versus volatile changes; manage explicit local user assertions; generate the File Intelligence Home dashboard; or migrate an existing v1 state safely without moving, deleting, renaming, archiving, or uploading user files.
---

# File Intelligence

Keep reusable Skill code and private machine knowledge separate. Store catalogs, paths, project graphs, evidence, fingerprints, assertions, run records, dashboards, and migration backups under `%LOCALAPPDATA%\FileIntelligence` unless the user explicitly supplies `--state-dir` or `FILE_INTELLIGENCE_STATE_DIR`.

## Route the request

1. Run `python scripts/file_intelligence_cli.py status`.
2. If status is `DEEP_ONBOARDING_REQUIRED`, obtain explicit roots and run onboarding once.
3. If status is `MIGRATION_REQUIRED`, preview `migrate`; apply it only as an explicit, reviewed state operation. Migration first creates a hash-recorded backup and never touches scanned project files.
4. If status is `MAINTENANCE_READY`, default to incremental `maintain`. Do not rebuild the baseline without an explicit `--rebuild` request.
5. For a project question, run targeted `understand --project-root <folder>`, then use `asset --path <file>` for evidence-backed asset explanations.

This Skill has no move, delete, rename, copy, archive, upload, or reference-rewrite command.

## Discovery and onboarding

```powershell
python scripts/file_intelligence_cli.py onboard --root <folder> [--root <folder>] [--communication-root <folder>] --backend auto
```

`auto` prefers an explicitly configured or locally discoverable official Everything `es.exe` bridge, then falls back to the read-only filesystem scanner. Everything answers where files are; File Intelligence performs local classification, parsing, graph construction, and inference. Communication roots are evidence sources only.

Cache, environment, build, render, and temporary trees are retained as aggregate nodes with size and file count; their internals are not added as thousands of file rows. Symlink and reparse boundaries are not followed.

## Project Understanding

```powershell
python scripts/file_intelligence_cli.py understand --project-root <folder> --summary-only
python scripts/file_intelligence_cli.py asset --path <file>
python scripts/file_intelligence_cli.py asset --path <large-file> --verify-full-hash
```

Build `Project -> Workstream -> Asset Group / Deliverable / Archive` nodes. Every semantic result must carry confidence and evidence provenance, such as `explicit_user`, `explicit_project_metadata`, `git_evidence`, `document_evidence`, `structural_inference`, `temporal_inference`, or `content_similarity`.

Never present structural or temporal inference as user-confirmed fact. Prefer README, manifest, final-deliverable index, current-status, decision-log, and explicit canonical declarations. Report uncertainty and contradictory evidence.

The default lightweight inspectors parse bounded text, Office Open XML, archive structure, and optional PDF/HDF5 metadata without launching COMSOL, Fluent, ANSYS, SpaceClaim, Origin, or another heavy application. Read `references/architecture.md` for limits and schema details.

## Maintenance

```powershell
python scripts/file_intelligence_cli.py maintain
python scripts/file_intelligence_cli.py changes
```

Report raw filesystem changes separately from meaningful project changes. Classify explainable WAL, lock, log, browser, communication-runtime, Codex-session, and cloud-metadata churn as volatile evidence instead of hiding it. Reuse unchanged fingerprints and perform zero content inspections on a no-op run.

Interpret same-size Stage-1 fingerprint matches as high-confidence identity evidence, not exact equality. Require Stage-2 full SHA-256 for exact duplicate claims. Maintenance may report `NEW`, `CHANGED`, `MISSING`, `REAPPEARED`, `MOVED`, `RENAMED`, and `COPIED`; it never executes those operations.

## User assertions

```powershell
python scripts/file_intelligence_cli.py assertion list
python scripts/file_intelligence_cli.py assertion set --subject-type asset --subject-key <catalog-key> --predicate authority_level --value PRIMARY
python scripts/file_intelligence_cli.py assertion remove --assertion-id <id>
```

Assertions live only in external local state, remain inspectable/removable, and use `explicit_user` provenance. Never promote an inference to an assertion automatically.

## Dashboard and migration

```powershell
python scripts/file_intelligence_cli.py dashboard
python scripts/file_intelligence_cli.py migrate
python scripts/file_intelligence_cli.py migrate --apply
python scripts/file_intelligence_cli.py rollback --backup <migration-backup>
```

`File Intelligence Home.html` summarizes projects, workstreams, authorities, dependencies, archive-review candidates, and recent meaningful changes without dumping the full catalog.

## Packaging boundary

Before install, update, publish, or share:

1. Read `references/privacy-and-safety.md`.
2. Run `scripts/privacy_audit.py` and review the Git diff.
3. Run the unit suite and `scripts/verify_fresh_install.py` in a disposable sandbox.
4. Keep real benchmark reports and machine paths in external state, never in the repository.
