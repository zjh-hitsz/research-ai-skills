# File Intelligence

File Intelligence is a public-safe Codex Skill for building a private file knowledge base on Windows. The repository contains only the engine, generic rules, schemas, documentation, and synthetic tests. It contains no machine catalog, real project registry, user assertion, communication record, transaction journal, baseline, or project artifact.

## Behavior

- A computer without local state starts in **Deep Onboarding required** mode.
- Deep Onboarding creates that computer's catalog, Project Map, quick content fingerprints, communication-evidence metadata, and baseline.
- Later runs default to **Maintenance**, which reports new, changed, and missing files and reuses unchanged fingerprints.
- If Everything's `es.exe` bridge is available, targeted indexed metadata queries are used. Otherwise the engine falls back to a read-only filesystem walk and recommends the optional bridge.
- There is no command for moving, deleting, renaming, archiving, or copying user files.

## Requirements

- Windows PowerShell 5.1 or PowerShell 7
- Python 3.10 or later
- Optional: Everything plus its `es.exe` command-line bridge

The runtime uses only the Python standard library. The installer never installs Python, Everything, or any other system software.

## Install for Codex

Ask Codex:

> Install the `file-intelligence` Skill from `zjh-hitsz/research-ai-skills`, path `skills/file-intelligence`.

Restart Skill discovery if the host does not refresh automatically, then invoke `$file-intelligence`.

## Install with PowerShell

From a cloned repository:

```powershell
& .\skills\file-intelligence\scripts\install.ps1
```

The default code destination is the current user's Codex Skill directory. Machine state is kept separately under `%LOCALAPPDATA%\FileIntelligence`.

## First run

Ask Codex:

> Use `$file-intelligence` to perform read-only Deep Onboarding for the folder I provide. Build this computer's local catalog, Project Map, fingerprints, communication evidence, and baseline. Do not move or delete files.

Or run directly from the installed Skill directory:

```powershell
python scripts\file_intelligence_cli.py onboard --root <folder>
```

Add one or more explicit communication attachment roots when appropriate:

```powershell
python scripts\file_intelligence_cli.py onboard --root <folder> --communication-root <folder>
```

## Daily maintenance

Ask Codex:

> Use `$file-intelligence` to run incremental maintenance, summarize new, changed, and missing files, and make read-only recommendations.

Or run:

```powershell
python scripts\file_intelligence_cli.py maintain
```

## Update

Pull or download the newer repository snapshot, then run:

```powershell
& .\skills\file-intelligence\scripts\update.ps1
```

The update replaces only Skill code. It does not overwrite or delete the external state directory.

## State override

For testing or a deliberate custom location, pass `--state-dir` to every command or set `FILE_INTELLIGENCE_STATE_DIR`. Never place state inside the Skill directory.

## Release validation

```powershell
python scripts\privacy_audit.py --root . --report FILE_INTELLIGENCE_PUBLIC_RELEASE_AUDIT.md
python -m unittest discover -s tests -v
python scripts\verify_fresh_install.py --source .
```

All tests use disposable synthetic files and must not reference an existing production state.
