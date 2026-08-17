# Portable architecture

## Separation boundary

The installed folder is reusable code. The default private state directory is `%LOCALAPPDATA%\FileIntelligence`; `FILE_INTELLIGENCE_STATE_DIR` and `--state-dir` may select another external directory. State nested below the Skill is rejected.

Local state includes:

- `catalog.db`: schema-v2 file facts, projects, nodes, assets, dependencies, inspections, fingerprints, identity events, aggregates, assertions, migrations, and runs;
- `baseline.json`: machine binding, fixed scan scopes, backend, counts, and digest;
- `last_changes.json`: raw and semantic maintenance changes;
- `File Intelligence Home.html`: private dashboard;
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
 local assertions + dashboard + read-only recommendations
```

Everything is an optional discovery accelerator, not the semantic engine. Both backends must produce canonical path keys and must not cross symlink/junction/reparse boundaries. The scanner excludes its own state and Skill package.

## Schema v2

The catalog uses SQLite `PRAGMA user_version=2` and application id `FINT`. Major tables are:

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

Schema v1 is recognized but never silently altered. `migrate --apply` copies the database, baseline, and latest changes into a timestamped directory, records SHA-256 values, performs a transactional database migration, then updates the baseline digest. `rollback` verifies backup hashes before an explicit restoration.

## Evidence and authority

Supported roles include `source`, `raw_data`, `processed_data`, `code`, `environment`, `canonical_model`, `working_model`, `checkpoint`, `recovery`, `final`, `deliverable`, `manuscript`, `figure`, `presentation`, `report`, `documentation`, `temporary`, `cache`, `intermediate`, `rejected_route`, `historical_provenance`, `archive_candidate`, and `unknown`.

Authority levels are `PRIMARY`, `CANONICAL`, `ACTIVE`, `REFERENCE`, `HISTORICAL`, `DERIVED`, and `UNKNOWN`. Evidence provenance is stored separately and currently includes `explicit_user`, `explicit_project_metadata`, `document_evidence`, `structural_inference`, `temporal_inference`, and `content_similarity`. The schema leaves room for `git_evidence` and `model_inference`.

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

Maintenance compares path facts first, fingerprints only affected candidates, and emits identity evidence when missing/new or present/new paths match. A same-parent identity change is `RENAMED`; a different-parent change is `MOVED`; a source that remains present is `COPIED`.

## Incremental behavior

An unchanged maintenance run performs no content inspection and no new hash. Changed files are catalogued and affected projects are marked for targeted refresh; maintenance does not re-parse the whole machine. Raw changes, volatile changes, meaningful changes, and semantic status are reported separately.

## Trust boundary

Project Understanding is evidence-backed recommendation, not scientific truth. It cannot decide that a manuscript, simulation, result, or archive candidate is scientifically final. It cannot repair references or perform physical file operations. Those require a separate plan, preview, preflight, and explicit authorization workflow.
