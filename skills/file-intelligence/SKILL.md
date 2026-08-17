---
name: file-intelligence
description: Build and maintain a private, read-only Windows file knowledge base with a portable Codex Skill. Use when Codex must onboard a new computer or folder into a local catalog, Project Map, content fingerprints, communication-evidence index, and baseline; detect whether Everything/ES is available; run incremental maintenance after onboarding; report new, changed, or missing files; or explain safe installation and updates without moving, deleting, renaming, archiving, or uploading user files.
---

# File Intelligence

Keep Skill code and machine memory separate. Treat the installed Skill directory as immutable code. Store every catalog, baseline, fingerprint, run record, and local path under `%LOCALAPPDATA%\FileIntelligence` unless the user explicitly supplies `--state-dir` or `FILE_INTELLIGENCE_STATE_DIR`.

## Route the request

1. Run `python scripts/file_intelligence_cli.py status` from this Skill directory.
2. If status is `DEEP_ONBOARDING_REQUIRED`, explain that this computer has no local baseline and ask for the root or roots to index. Then run explicit Deep Onboarding.
3. If status is `MAINTENANCE_READY`, default to incremental maintenance. Do not rebuild the baseline unless the user explicitly requests `--rebuild`.
4. Keep all source files read-only. This Skill has no move, delete, rename, archive, copy, or transaction-execution command.

## Deep Onboarding

Run once for each new machine or explicit rebuild:

```powershell
python scripts/file_intelligence_cli.py onboard --root <folder> [--root <folder>] [--communication-root <folder>]
```

This creates that machine's own SQLite catalog, Project Map, quick content fingerprints, communication-evidence metadata, and signed baseline in the external state directory. It never imports another machine's projects or memory.

Use `--backend auto` by default. When an Everything `es.exe` bridge is discoverable, use targeted indexed queries. If it is not discoverable or a query fails, use the read-only filesystem fallback and report that Everything CLI is recommended. Never install system software automatically.

## Maintenance

After onboarding, run:

```powershell
python scripts/file_intelligence_cli.py maintain
```

Compare the saved scopes with the prior catalog, reuse unchanged fingerprints, refresh only new or changed candidates, retain missing records as history, and write `last_changes.json` under local state. A second unchanged run must be a no-op.

Use these read-only views when needed:

```powershell
python scripts/file_intelligence_cli.py status
python scripts/file_intelligence_cli.py projects
python scripts/file_intelligence_cli.py changes
```

## Safety and privacy

- Never put state below the Skill directory.
- Never upload catalogs, baselines, fingerprints, communication records, paths, project files, or run outputs.
- Treat project labels and communication matches as heuristics, not user assertions.
- Report recommendations only; require a separate, explicitly authorized workflow for physical file changes.
- Read `references/architecture.md` for state boundaries and `references/privacy-and-safety.md` before packaging, exporting, or sharing anything.

## Installation and validation

Use `scripts/install.ps1` for a clean install and `scripts/update.ps1` for code-only updates. Both preserve external machine state. For release verification, run `scripts/privacy_audit.py`, the unit tests, and `scripts/verify_fresh_install.py` against a disposable sandbox.
