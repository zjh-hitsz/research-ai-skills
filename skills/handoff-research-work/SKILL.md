---
name: handoff-research-work
description: Freeze an explicitly selected, already audited research-work snapshot into a verifiable minimal or full handoff manifest and deterministic continuation documents. Use when Codex must preserve read order, entrypoints, input/output identities and hashes, environment requirements, existing regression records, reuse and do-not-reuse boundaries, private dependencies, open decisions, and the next action for another task or researcher. Do not use to scan a project, execute commands or solvers, copy large/private artifacts, modify research files, or decide scientific correctness.
---

# Handoff Research Work

## Purpose

Freeze reviewed research context so another task can verify the selected snapshot and continue without rediscovering the project. Treat the handoff manifest as the single machine-readable source for all rendered documents.

## When to use

Use after `audit-research-project` has produced a reviewed project audit and relative inventory. Use minimal profile for a bounded next action with only essential inputs; use full profile when outputs and at least one existing regression record must travel with the handoff.

Read `references/minimal-and-full-handoff-profiles.md` before selecting a profile. Do not invoke this Skill to onboard an unaudited directory.

## Inputs

Start from `templates/handoff_request.yaml`. Supply only explicit, JSON-compatible YAML values:

- relative runtime references to the audit JSON and audit inventory;
- a stable audit reference and selected asset IDs from that inventory;
- portable relative locators or declared external references;
- read order, entrypoints, environment, existing regression records, reuse exclusions, open decisions, private dependencies, next action, limitations, and a deterministic timestamp.

Use `templates/regression_record.json` to manually structure an already reviewed regression report. The builder accepts this Skill-local template and normalizes it to the unchanged Foundation handoff manifest contract. It never parses prose into a status or executes the recorded invocation.

When an upstream audit is `PARTIAL`, a human reviewer may supply `templates/scope_review_record.json` through `audit.scope_review_record_path`. Read `references/partial-scope-human-review.md` first. The evaluator validates and records the supplied decision; it never creates or auto-approves a decision, and an upstream `FAIL` cannot be overridden.

Every selected asset must carry a provenance reference. Criteria, availability, output status, and regression status come from the request or upstream records; the Skill does not infer them.

## Workflow

1. Confirm the audit output was reviewed and that selected relative paths exist in its inventory. Do not rescan the project.
2. Read `references/reuse-exclusion-and-private-dependency-policy.md` and separate portable selected assets from external or private dependencies.
3. Run `scripts/evaluate_regression_record.py RECORD` when regression records are present. It accepts canonical or structured records, reads them only, and never executes `invocation_summary`.
4. If a human partial-scope record is supplied, run `scripts/evaluate_scope_review_record.py RECORD --audit AUDIT`. Stop on rejection, incomplete coverage, an automated reviewer role, or any upstream `FAIL`.
5. Run `scripts/build_handoff_manifest.py REQUEST --output MANIFEST`. Build `handoff-manifest-v1` from the audit snapshot and explicit selection.
6. Run `scripts/verify_handoff_package.py MANIFEST --package-root PACKAGE_ROOT`. Verify only manifest-listed files, hashes, relative paths, regression readiness, and required private dependencies. The verifier writes nothing.
7. Stop if verification is not `PASS`. Read `references/regression-first-and-fail-stop.md` before interpreting a missing or failed regression.
8. Run `scripts/render_handoff_documents.py MANIFEST --output-dir OUTPUT_DIR` to generate `HANDOFF.md`, `README.md`, `NEXT_PROMPT.md`, and `OPEN_DECISIONS.md` from one manifest.

## Outputs

- one `handoff-manifest-v1` JSON document;
- a read-only verification result on stdout;
- four deterministic Markdown continuation documents;
- explicit non-ready reason codes when evidence, hashes, dependencies, or upstream regression records are insufficient.

The package references selected artifacts; it does not copy them.

## Dependencies

- Required Skill: `audit-research-project`, whose audit JSON and relative inventory are consumed without rescanning.
- Foundation `handoff-manifest-v1`, `asset-provenance-v1`, and `common-status-v1` contracts.
- Python 3.10+ standard library only.

This Skill must not call the audit Skill in reverse during normal use. The integration eval may run the audit workflow first to prove the one-way contract.

## Validation

- Parse every Python source file before distribution and exercise success, failure, partial, boundary, safety, and audit-to-handoff behavior with disposable synthetic inputs outside the Skill folder.
- Require repeated builds and renders to be byte deterministic.
- Require package content to remain unchanged during build and verification.
- Require hash drift to return `FAIL`, missing required regression evidence to remain non-ready, and failed upstream regression to return `FAIL`.
- Require no command-execution API in `scripts/` and no absolute or historical project path in persistent assets.
- Validate the folder with the library Skill validator when its host YAML dependency is available.

## Failure modes

- Reject malformed requests, unknown audit selections, duplicate IDs, unsafe locators, or schema-incompatible manifests as input errors.
- Return `FAIL` for input hash drift, failed required upstream runs, or missing required private dependencies explicitly recorded as unavailable.
- Return `PARTIAL` when a required regression record is absent or upstream evidence is incomplete.
- Return `FAIL` for a rejected scope review, and `PARTIAL` for an accepted record that does not cover every upstream boundary.
- Return non-ready for unresolved external artifacts; never invent a local path or copy a private dependency.
- Preserve upstream reason codes while using only the Foundation status vocabulary.

## Not suitable for

- scanning, classifying, cleaning, moving, deleting, renaming, archiving, or copying project files;
- executing shell commands, notebooks, scripts, macros, solvers, regression commands, or setup instructions;
- embedding large models, real research data, credentials, private locators, or unpublished results;
- selecting the scientifically final model, manuscript, dataset, or result;
- deciding reproducibility, physical correctness, novelty, publication readiness, or whether an open decision is resolved.
