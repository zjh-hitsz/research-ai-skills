# Portable architecture

## Separation boundary

The installed directory contains reusable Skill code only. The default state directory is `%LOCALAPPDATA%\FileIntelligence`; callers may override it with `FILE_INTELLIGENCE_STATE_DIR` or `--state-dir`. Installation and update scripts reject a state directory nested below the Skill destination.

Local state may contain:

- `catalog.db`: current and historical file metadata;
- `baseline.json`: machine binding, configured scopes, project summaries, and baseline digest;
- `last_changes.json`: the most recent incremental comparison;
- local run records and quick content fingerprints.

None of these state files belongs in a Skill package or Git repository.

## Mode transition

```text
No local baseline -> explicit Deep Onboarding -> Maintenance ready
Maintenance ready -> incremental comparison -> updated local state
No observed change -> no-op Maintenance result
```

Deep Onboarding is read-only with respect to scanned roots. It writes only to the external state directory. Maintenance uses the roots recorded during onboarding and does not silently expand scope.

## Metadata backends

`auto` first looks for an explicitly supplied Everything CLI, then `es.exe` on `PATH`, then common program installation locations. A successful query uses Everything IPC metadata. If the bridge is absent or the query fails, the engine reports the reason and uses the filesystem fallback. It never downloads or installs Everything.

## Trust boundaries

Project grouping is a deterministic path heuristic. Communication evidence means only that a file was observed inside a caller-declared communication root. Neither label is a user assertion or proof of provenance. Physical file changes are outside this Skill's authority.
