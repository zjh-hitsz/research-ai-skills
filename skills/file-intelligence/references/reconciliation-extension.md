# Reconciliation Extension

This feature branch preserves the v0.3.0 schema-v3 Core and adds a backward-compatible extension for state created by earlier local FileCard, ProjectCard, and sensor prototypes.

## One state owner

- `files` and Core `file_cards` own identity and current presence.
- `projects`, nodes, assets, assertions, activity, tools, events, snapshots, and semantic changes own current meaning and history.
- `file_card_semantics` enriches the Core identity projection without replacing it.
- `project_card_views` is derived and revisioned; it is not a peer project authority.
- sensor observations are `SYSTEM_OBSERVED` evidence and are never directly canonical.

The Core SQLite `user_version` remains `3`. `meta.reconciliation_extension_version` versions the additive tables independently.

## Legacy import

`reconcile-state` is preview-only by default. `--apply`:

1. requires a current schema-v3 target catalog;
2. requires a matching source machine binding, or an explicit reviewed-unbound-source acknowledgement;
3. creates a consistent destination backup and SHA-256 manifest;
4. opens the legacy source with immutable/query-only SQLite access;
5. imports in one transaction and records an idempotent import ledger;
6. rejects conflicting stable IDs rather than overwriting them;
7. verifies target integrity after import.

Rollback restores the verified pre-import target backup. Source databases are never modified.

## Semantic invariants

- exact duplicate groups require full SHA-256 content identity;
- no-op imports do not create new revisions or events;
- sensor timestamp/cache refreshes do not create semantic revisions;
- user assertion source text, source reference, active/revoked state, and events remain auditable;
- imported no-op `CARD_REUSED` and `PROJECT_CARD_REUSED` records are not promoted into Timeline noise;
- foreign machine state is rejected by default;
- optional sensors may fail or be absent without blocking Core operation.

## Public/private boundary

The repository may contain sensor interfaces, adapters, schemas, tests, and generic documentation. It must not contain catalogs, machine bindings, private paths, project names, sensor configs, raw observations, credentials, third-party executables, or frozen-state manifests.
