# Changelog

File Intelligence follows Semantic Versioning for reusable Skill code. Private machine state has an independent, explicitly migrated schema version.

## Unreleased — reconciliation candidate

- Added a backward-compatible schema-v3 reconciliation extension for rich FileCard semantics, revisions, evidence, path history, derived ProjectCard views, relations, assertion events, sensor observations, and import ledgers.
- Added an explicit, backup-first, transactional importer for reviewed local FileCard/ProjectCard schema 2.0.1 and validation schema 1.0.0 state.
- Added a normalized legacy cleanup-evidence interface whose proposed recommendations remain read-only and have zero execution authority.
- Added optional capability-based read-only sensors with health, fallback, last-known-good, EULA/admin gates, and non-canonical observation persistence.
- Added foreign-state rejection, two-machine isolation, no-op import, rollback, exact-duplicate, privacy, and sensor-optional regression coverage.
- Improved Windows canonical path handling for long paths that include 8.3 aliases.
- Kept `VERSION`, schema-v3 `user_version`, `main`, tags, and production state unchanged.

## 0.3.0 — 2026-08-17

- Added stable FileCards and an append-oriented Computer Timeline Event Store.
- Added lightweight machine/project snapshots, time-range queries, storage history, and semantic change summaries.
- Added transparent meaningful-change scoring, project activity, important-asset alerts, and move/rename/copy distinction.
- Added authority scope, directory authority, and primary/secondary/reference tool centrality.
- Added additive, backup-first schema-v3 migration, rollback validation, conservative retention, and scheduled-maintenance support.
- Preserved the read-only boundary: no move, delete, rename, archive, upload, solver launch, or reference rewrite.

## 0.2.0

- Added hierarchical Project Understanding with workstreams, assets, roles, authority, confidence, evidence, provenance, dependencies, supersession, and local user assertions.
- Added bounded research-file inspectors, staged fingerprints, duplicate evidence, aggregate directories, and the File Intelligence Home dashboard.
- Added explicit schema-v1 to schema-v2 migration and rollback support.

## 0.1.0

- Added Windows file inventory, Everything/ES and filesystem discovery, external private state, initial baseline creation, and incremental Maintenance.
- Added volatile-change handling, project candidates, read-only safety checks, installation/update scripts, and fresh-install verification.
