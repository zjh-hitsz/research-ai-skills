# Optional Sensor Foundation

The sensor layer supplies bounded read-only observations to File Intelligence. It does not reorganize files, install software, accept licenses, elevate privileges, or decide canonical project meaning.

## Contract

Every provider exposes:

- detection and version/source metadata;
- declared capabilities;
- a health check;
- bounded collection;
- normalized output.

Observation outcomes distinguish `DATA`, `NO_DATA`, and `SENSOR_ERROR`. Cache state distinguishes live, last-known-good, stale, and absent data. The capability registry falls back after sensor failure or unavailability, but a valid empty result stops fallback.

## Safety

- subprocesses use argument arrays with `shell=False`;
- timeouts and output metrics are recorded;
- filesystem fallback is bounded and does not follow symlinks/reparse points;
- drive-root WizTree scans require explicit permission;
- archive fallback lists members without extraction;
- Sysinternals and WizTree license acceptance is always a user-controlled local setting;
- ProcMon/Sysmon tracing is disabled outside a separate explicit diagnostic workflow;
- smartctl self-tests are not started;
- Git safe-directory handling is invocation-local and never changes global config;
- GitHub authentication output is never retained.

## Providers

The default registry can use Everything/ES, bounded filesystem fallback, WizTree, 7-Zip/Python archive readers, Sysinternals, Git, GitHub CLI, WinGet, smartctl, Windows native APIs/PowerShell, and NVIDIA SMI. All third-party tools are optional and remain outside the Skill.

Normalized observations may be persisted in the private Core catalog with `semantic_eligible=0`. A separate policy and eligible evidence are required before an observation can change a semantic claim.
