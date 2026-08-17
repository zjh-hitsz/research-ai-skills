# Portable architecture

## Separation boundary

The installed folder is reusable code. The default private state directory is `%LOCALAPPDATA%\FileIntelligence`; `FILE_INTELLIGENCE_STATE_DIR` and `--state-dir` may select another external directory. State nested below the Skill is rejected.

Local state includes:

- `catalog.db`: schema-v3 current facts plus stable FileCards, append-oriented events, snapshots, semantic changes, activities, alerts, and v0.2 graph tables;
- `baseline.json`: machine binding, fixed scan scopes, backend, counts, and digest;
- `last_changes.json`: raw and semantic maintenance changes;
- `File Intelligence Home.html`: private dashboard;
- `Computer Timeline.html`: private, filterable historical event view;
- `project_understanding/`: private targeted query results;
- `migrations/`: rollbackable catalog/baseline backups.

None belongs in a Skill package or Git repository.

## Layers

```text
Everything ES or filesystem metadata
                |
                v
       catalog + aggregate nodes
                |
                v
 bounded inspectors + staged hashes
                |
                v
 dependency and authority evidence graph
                |
                v
 Project -> Workstream -> Asset Group / Deliverable / Archive
                |
                v
 stable FileCard + append-oriented Event Store
                |
                v
 summary Snapshot + Semantic Change + local dashboards
```

Everything is an optional discovery accelerator, not the semantic engine. Both backends must produce canonical path keys and must not cross symlink/junction/reparse boundaries. The scanner excludes its own state and Skill package.

Maintenance with `--backend auto` inherits the backend recorded by the baseline. This is a correctness rule: switching metadata providers can change timestamp representation without changing file content. A non-default state may use a globally installed/PATH-discoverable `es.exe`, but it does not silently borrow an ES bridge stored inside another private File Intelligence state.

## Schema v3

The catalog uses SQLite `PRAGMA user_version=3` and application id `FINT`. It keeps the schema-v2 current-state graph and adds historical intelligence. Major tables are:

- `files`: path facts, status, Stage-1/2 hashes, volatility;
- `projects`, `project_nodes`: heuristic candidates and understood hierarchy;
- `assets`: role, authority, confidence, evidence, provenance, supersession, rebuildability, review recommendation;
- `dependencies`: `REFERENCES` and `DECLARES_AUTHORITY` edges with resolution and line evidence;
- `inspections`: bounded inspector metadata and content digest;
- `fingerprints`: staged algorithm/value provenance;
- `identity_events`: move, rename, copy, and exact duplicate evidence;
- `aggregate_nodes`: environments, dependencies, cache, build, render, preview, and temporary trees;
- `user_assertions`: explicit local knowledge, never inferred automatically;
- `migrations`, `runs`: state transitions and performance summaries.
- `file_cards`: stable `file_id`, current path observation, native identity evidence, last role, and last authority;
- `events`: append-oriented file/project/workstream/dependency/storage events with old/new values, size delta, confidence, evidence, semantic importance, volatility, and run provenance;
- `snapshots`, `project_snapshots`: lightweight materialized summaries, never database or user-file copies;
- `semantic_changes`: project-level groupings for deterministic context generation;
- `asset_alerts`: open/resolved important-asset alerts;
- `project_activity`, `workstream_activity`: evidence-backed activity state independent of scientific lifecycle;
- `project_tools`: `PRIMARY_TOOL`, `SECONDARY_TOOL`, and `REFERENCE_TOOL` evidence.

Schema v1 and v2 are recognized but never silently altered. `migrate --apply` uses SQLite's online backup API so committed WAL content is included, copies the baseline/latest changes, records SHA-256 values, performs an additive transactional migration, initializes FileCards, then updates the baseline digest. `rollback` verifies backup hashes before an explicit restoration.

## Evidence and authority

Supported roles include `source`, `raw_data`, `processed_data`, `code`, `environment`, `canonical_model`, `working_model`, `checkpoint`, `recovery`, `final`, `deliverable`, `manuscript`, `figure`, `presentation`, `report`, `documentation`, `temporary`, `cache`, `intermediate`, `rejected_route`, `historical_provenance`, `archive_candidate`, and `unknown`.

Authority levels are `PRIMARY`, `CANONICAL`, `ACTIVE`, `REFERENCE`, `HISTORICAL`, `DERIVED`, and `UNKNOWN`. Evidence provenance is stored separately and currently includes `explicit_user`, `explicit_project_metadata`, `document_evidence`, `structural_inference`, `temporal_inference`, and `content_similarity`. The schema leaves room for `git_evidence` and `model_inference`.

Authority scope is independent of authority level:

- `PROJECT_WIDE`: declared by project-level metadata for the whole project;
- `WORKSTREAM_LOCAL`: authoritative inside one established workstream;
- `GATE_LOCAL`: valid only at a checkpoint, iteration, validation, or handoff gate;
- `FILE_LOCAL`: an individual authority candidate without broader proven scope.

Directory/result-set authority is stored as an `assets` row with `asset_kind=directory` and an explicit entity path. Top-level dashboard authority excludes workstream/gate-local entries and shows them in scoped context instead.

Tool centrality uses native solver assets, same-line declarations, and reference/supporting context. Text frequency alone cannot promote a tool. The highest evidence-backed chain is `PRIMARY_TOOL`; substantive supporting production tools are `SECONDARY_TOOL`; literature, golden comparisons, and reference-only artifacts are `REFERENCE_TOOL`.

Explicit README/manifest sections such as “Canonical final models” create `DECLARES_AUTHORITY` edges for their listed files. Filename-only conclusions remain structural inference. User assertions override inference and keep `explicit_user` provenance.

## Inspectors

All inspectors are bounded and read-only:

- source/text/notebook: decode at most the configured text budget and extract paths;
- DOCX/PPTX: read selected Office Open XML members;
- PDF: optional `pypdf`, maximum 40 pages and 64 MiB default file budget;
- ZIP/SpaceClaim/COMSOL zip containers: list a bounded member sample;
- HDF5/Fluent/MATLAB: optional `h5py`, stop after 500 object names and bounded root attributes;
- other binaries: magic bytes and file metadata.

No inspector launches or automates COMSOL, Fluent, ANSYS, SpaceClaim, Origin, or Microsoft Office. A future deep inspector must be a separate explicit mode with its own resource and safety gates.

## Fingerprints and identity

- Stage 0: byte size;
- Stage 1: SHA-256 over size plus head, middle, and tail samples;
- Stage 2: full-file SHA-256.

Stage 1 is a strong candidate signal, not equality proof. Exact duplicates require Stage 2. Large-file Stage 2 is performed only on reviewed candidates or an explicit `asset --verify-full-hash` request. Unchanged hashes are reused.

Path is an observation, not identity. Each FileCard has a stable `file_id`. Maintenance preserves an identity at an unchanged path, then correlates arrivals and missing observations using this precedence:

1. native device/volume plus file index when safely exposed by the platform;
2. Stage-2 full SHA-256;
3. equal size plus Stage-1 head/middle/tail fingerprint;
4. weak equal-size/name similarity, reported only as `POSSIBLE_MOVE`.

A same-parent strong match is `FILE_RENAMED`; a different-parent strong match is `FILE_MOVED`; if the source remains present, the arrival gets a distinct FileCard and `FILE_COPIED`. Weak evidence never merges identities.

## Incremental behavior

An unchanged maintenance run performs no content inspection and no new hash. Changed files are catalogued and only affected projects are eligible for bounded `--deep` refresh; maintenance does not re-parse the whole machine. Raw changes, volatile changes, events, meaningful changes, and semantic status are reported separately.

## Timeline and semantic diff

Maintenance appends events; later scans never overwrite them. Volatile file churn is collapsed by class into one aggregate event per run. Cache/environment/build trees remain aggregate nodes and produce directory-level storage events rather than thousands of file events.

The deterministic importance score is a transparent sum of event-type weight, authority, scientific role, size, incoming dependency count, rebuildability, and explicit-user evidence. `VERY_HIGH`, `HIGH`, `MEDIUM`, `LOW`, and `VOLATILE` are labels over that score. Every event stores the reasons.

An important missing asset first passes through relocation/copy correlation, surviving-content checks, archive-path context, and supersession checks. An unresolved canonical/primary/active or scientifically important source/result creates `POSSIBLE_ASSET_LOSS` plus an open alert. Reappearance or a strong relocation resolves the alert; no restoration is attempted.

`context-summary` returns structured time range, important changes, project changes, storage changes, alerts, and uncertainties. Codex supplies the final prose; the database layer does not hard-code a long natural-language answer.

## Snapshots, retention, and scheduling

Every Maintenance run materializes one compact machine summary and one row per understood project. Snapshot rows contain counts, logical size, observed-volume usage, activity, authority summaries, aggregate size, and cleanup/archive estimates. They do not duplicate the file catalog.

Default retention is conservative and preview-first:

- aggregate volatile events older than 30 days are eligible for removal;
- daily snapshots older than 90 days are eligible after weekly/monthly summaries exist;
- weekly snapshots and monthly summaries are long-lived;
- high/very-high and authority events are permanent.

`retention --apply` changes only private state. The bundled PowerShell runner supports Daily and Weekly profiles and writes external logs. `schedule-plan` creates no Windows task; task creation, timing, disabling, and removal remain explicit user-controlled operations. Neither profile starts COMSOL, Fluent, ANSYS, SpaceClaim, Origin, or another solver.

## Trust boundary

Project Understanding is evidence-backed recommendation, not scientific truth. It cannot decide that a manuscript, simulation, result, or archive candidate is scientifically final. It cannot repair references or perform physical file operations. Those require a separate plan, preview, preflight, and explicit authorization workflow.
