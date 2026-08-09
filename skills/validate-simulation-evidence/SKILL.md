---
name: validate-simulation-evidence
description: Aggregate configured simulation evidence gates into a schema-compatible PASS, FAIL, PARTIAL, or SKIPPED report; compute explicitly configured conservation checks, graph connectivity, and case fairness; and render traceable claim boundaries. Use when reviewing simulation runs, comparing cases, qualifying mapped fields or independent re-solves, recording solved-but-invalid outcomes, or preparing evidence status for downstream research workflows. Do not use to invent acceptance thresholds or decide physical correctness, novelty, publication readiness, or scientific truth.
---

# Validate Simulation Evidence

## Purpose

Convert already available simulation evidence into an auditable gate report. Keep numerical execution, evidence qualification, and scientific judgment separate.

## When to use

Use this Skill when a task asks whether configured validation gates passed, why a solved case remains invalid, whether two cases were compared under declared equal conditions, whether a topology satisfies an explicit graph criterion, or which claims the recorded evidence does and does not support.

## Inputs

Require config or manifest input. Do not infer missing criteria.

- For aggregation, start from `templates/validation_request.yaml`. Supply every gate status, criterion description, criterion source, evidence reference, claim boundary, limitation, and fixed `created_at` value.
- For a correction or revalidation sequence, supply one frozen report snapshot and its complete `lineage` object. Link the previous report with `supersedes_report_id`; do not mix gates from different attempts.
- Use either legacy string evidence references or structured `evidence-manifest-v1` objects. Supply parse status and provenance; the Skill does not inspect the artifact or infer them.
- Use either the legacy three-list claim boundary or the object-based `claims` profile. Every object-based supporting gate ID must identify a gate in the same report.
- For conservation, supply source/target values plus `metric`, `operator`, `threshold`, and `source_ref`.
- For connectivity, supply explicit nodes, edges, directedness, mode, expected connectivity, and any required node pairs. Do not infer adjacency from an image or solver file.
- For fairness, supply case A/B condition objects, the exact fields that must match, and every numeric tolerance including its combination and reference rule.
- Treat `.yaml` templates as the library's JSON-compatible YAML profile. The v0.1 scripts use only the Python standard library.

## Workflow

1. Read `references/status-and-aggregation-contract.md` before interpreting status or writing reason codes.
2. Read `references/simulation-validation-checklist.md` to select checks. Do not treat the checklist as a source of numerical criteria.
3. If comparing cases or independent re-solves, read `references/fairness-resolve-and-claim-boundaries.md`.
4. Run the relevant check scripts. Each emits one gate object:
   - `scripts/check_conservation.py`
   - `scripts/check_connectivity.py`
   - `scripts/audit_fairness.py`
5. Place the gate objects in the validation request. Preserve their evidence references and limitations. Use `evidence-integrity` when an externally performed parse/hash check is itself a gate.
6. Run `scripts/evaluate_gates.py REQUEST --output gate_report.json`.
7. Run `scripts/render_gate_report.py gate_report.json --output gate_report.md` when a human-readable report is required.
8. Report evidence status and unresolved limitations. Escalate scientific interpretation to the researcher.

## Outputs

- `gate_report.json`, compatible with the library-level `gate-report-v1.schema.json` contract. Legacy `1.0.0` and additive `1.1.0` profiles are accepted.
- Optional deterministic Markdown rendered from the JSON.
- Explicit legacy claim lists or object claims supplied by the request; the scripts preserve rather than invent their status.
- Machine-readable reason codes for every non-PASS status.

## Dependencies

- Read the library-level `schemas/common-status-v1.schema.json`; do not define a local status enum.
- Read the library-level `schemas/gate-report-v1.schema.json`; do not create a competing gate schema.
- Read the library-level `schemas/evidence-manifest-v1.schema.json` for structured evidence; keep legacy string references valid.
- Use Python 3.9 or later and only the standard library.
- Accept upstream evidence records, but do not run COMSOL, Fluent, surrogate models, or other research solvers.

## Validation

- Parse every Python source file before distribution and exercise the scripts with synthetic requests derived from `templates/`.
- Run the system `skill-creator` quick validator after editing `SKILL.md`.
- Require identical bytes from repeated runs with identical inputs.
- Treat overall FAIL/PARTIAL/SKIPPED as valid evidence outcomes; reserve nonzero script exit codes for invalid config or I/O failure.

## Failure modes

- Reject missing criteria, criteria without a source, invalid status/reason combinations, duplicate gate IDs, missing evidence for PASS/FAIL, or a request with no required gate.
- Reject incomplete lineage objects, malformed structured evidence, duplicate artifact IDs within one gate, duplicate claim IDs, or object claims that reference an unknown gate ID.
- Reject ordinary YAML syntax in v0.1 if it is not also valid JSON. Convert it to the JSON-compatible profile; do not silently reinterpret it.
- Return input error without generating a gate report when a threshold, tolerance, graph endpoint, or required field is missing.
- Return PARTIAL for a configured relative conservation check with a zero nonmatching reference because the requested metric cannot be computed.
- Never turn optional-gate failure into required-gate failure; disclose it in the gate table and limitations.

## Not suitable for

- Deciding whether a simulation is physically correct, publishable, innovative, or scientifically true.
- Selecting thresholds, tolerances, governing equations, boundary conditions, or fair-comparison variables.
- Parsing solver-native models or results, reconstructing topology from figures, or running simulations.
- Replacing mesh-independence studies, experimental validation, expert review, novelty search, or peer review.
- Upgrading weak, missing, or skipped evidence to PASS.
