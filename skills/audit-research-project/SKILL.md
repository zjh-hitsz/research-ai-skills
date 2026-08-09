---
name: audit-research-project
description: Inventory and audit a research-computing project without modifying it. Use when Codex must create a relative-path file inventory, heuristically classify research assets, identify duplicate candidates, large files, broken references, absolute-path leakage, cache directories, permission gaps, or symlink/junction boundaries, and produce authority candidates and archive recommendations that always require human review. Do not use to delete, move, rename, archive, overwrite, execute project code, or decide which manuscript, model, or result is scientifically final.
---

# Audit Research Project

## Purpose

Produce a deterministic, read-only fact inventory and bounded risk report for a research project. Keep facts, heuristic classifications, authority candidates, and human decisions separate. Never turn an audit recommendation into a filesystem action.

## When to use

Use this Skill to onboard or review a research project whose files, versions, caches, outputs, or dependencies are unclear. Use it before `handoff-research-work` so the handoff can consume a reviewed inventory instead of rescanning the project.

Do not use it when the request authorizes cleanup or restructuring. This Skill can recommend review targets, but it cannot perform or generate disposal commands.

## Inputs

Start from `templates/project_audit_request.yaml`. The file uses the library's JSON-compatible YAML profile and must explicitly declare:

- project root and stable project ID;
- known entrypoints and expected outputs;
- link-following policy, exclusions, and optional hashing policy;
- caller-supplied large-file threshold and text-reference scan limits;
- cache/source/result directory hints;
- an optional project profile with include/exclude rules, priority paths, environment/cache folders, and source hints;
- deterministic report timestamp.

Keep project roots out of persistent reports. Read `references/read-only-and-authority-policy.md` before setting the scope. Read `references/project-audit-profiles.md` when environment or dependency noise can distort source/code/cache labels.

## Workflow

1. Run `scripts/inventory_project.py REQUEST --output INVENTORY --summary SUMMARY`. Enumerate relative metadata, never follow symlinks/junctions, and record permission or broken-link boundaries.
2. Run `scripts/classify_research_assets.py REQUEST INVENTORY --output CLASSIFICATIONS`. Apply the optional profile only as classification guidance. Treat every source/config/code/report/result/cache/unknown label and primary-asset candidate as heuristic.
3. Run `scripts/detect_archive_risks.py REQUEST INVENTORY CLASSIFICATIONS --output RISKS`. Detect only the criteria declared by the request; emit risks, never actions.
4. Run `scripts/render_project_audit.py REQUEST INVENTORY CLASSIFICATIONS RISKS --output-json AUDIT --output-markdown PROJECT_AUDIT --archive-plan ARCHIVE_PLAN`.
5. Compare a project-tree content snapshot before and after the workflow. Any mutation invalidates the audit.
6. Route authority candidates, archive recommendations, missing outputs, and incomplete scan boundaries to a human reviewer.

Read `references/duplicate-large-file-and-path-risks.md` before interpreting risk records. Read `references/archive-recommendation-boundaries.md` before using the archive plan.

## Outputs

- relative-path CSV inventory with size, type, timestamp, optional SHA-256, scan state, and link boundary;
- heuristic classification JSON with evidence basis and authority-candidate flags;
- optional profile summary with included, excluded, priority, environment, cache, source-hint, and primary-candidate counts;
- risk register JSON;
- `PROJECT_AUDIT.md` with distinct Facts, Risks, and Human decision required sections;
- project-audit JSON and a non-executable archive recommendation draft.

Outputs describe evidence available at one snapshot. They do not certify scientific authority, reproducibility, or archival safety.

## Dependencies

- Foundation `common-status-v1` status and reason-code contract.
- Python 3.10+ standard library only.
- No solver, office application, project-specific runtime, or higher-level Skill.

This is a Foundation leaf Skill. It must not call `handoff-research-work`; handoff may consume this Skill's reviewed outputs.

## Validation

- Parse every Python source file before distribution and exercise success, failure, boundary, profile-noise, and safety behavior against disposable synthetic trees outside the Skill folder.
- Require identical reports across repeated runs of an unchanged fixture.
- Require the project-tree content snapshot to remain unchanged before and after every case.
- Require all persistent inventory paths to be relative and all detected absolute references to be redacted.
- Require no move, delete, rename, archive, shell-disposal command, or project-code execution path.
- Run the library Skill validator when its host dependencies are available.

## Failure modes

- Return `PARTIAL` with reason codes when permissions, broken links, symlink/junction boundaries, or content-read failures prevent a complete audit.
- Return risks without changing execution status when duplicate candidates, large files, caches, absolute references, or missing expected outputs are successfully detected.
- Treat malformed requests, unsafe output locations, missing roots, invalid thresholds, and unreadable request documents as CLI input errors.
- Never infer that same-name or similar-size files are duplicates; byte-identical SHA-256 groups are candidates only.

## Not suitable for

- deleting, moving, renaming, archiving, compressing, or overwriting project files;
- generating shell commands or automatic cleanup scripts;
- executing project scripts, solvers, notebooks, macros, or binaries;
- selecting a scientifically final manuscript, model, dataset, or result;
- proving reproducibility, physical correctness, novelty, or publication readiness;
- following external links or scanning outside the declared project boundary.
