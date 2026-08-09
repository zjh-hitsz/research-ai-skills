---
name: map-physical-fields
description: Validate, normalize, conservatively map, and document a manifest-defined regular two-dimensional scalar field on axis-aligned cell-centered grids. Use when a research workflow must transfer a tabular field between declared regular 2D grids while preserving field identity, coordinate/unit/sign declarations, SHA-256 provenance, coverage, and an integral check. Supports only explicit coordinate transforms and area-overlap piecewise-constant conservative mapping; do not use for 3D, unstructured meshes, vector/tensor or transient fields, automatic coordinate inference, solver execution, physical-correctness judgements, or scientific interpretation.
---

# Map Physical Fields

## Purpose

Transfer one declared scalar field between axis-aligned regular two-dimensional grids without losing its identity, coordinates, provenance, coverage, or integral audit trail. Treat every coordinate transform, mapping method, policy, criterion, and timestamp as input data.

This Skill establishes evidence about a mapping operation. It does not establish that the source variable is physically correct, that two solver variables are scientifically equivalent, or that a downstream claim is valid.

## When to use

Use this Skill when all of the following are true:

- the source is a CSV table containing two declared coordinate columns and one scalar-value column;
- the source and target are axis-aligned, regular, two-dimensional, cell-centered grids;
- the field is a scalar cell average represented as piecewise constant within each source cell;
- source and target cover the same declared physical domain;
- coordinate axis order, scale, offset, units, direction, field units, and sign convention are explicitly supplied;
- the mapping must be checked against caller-supplied coverage and integral criteria.

If any condition is false, stop with a bounded failure or unsupported status. Do not infer missing declarations.

## Inputs

Start from `templates/field_mapping_request.yaml`. It is YAML only by filename; the v0.1 scripts intentionally accept the library's JSON-compatible YAML profile so they need no external YAML package.

Required input roles are:

- source field table and expected SHA-256;
- source field, coordinate, grid, producer, and extraction identity;
- explicit axis-aligned coordinate transform and normalized source grid;
- target field, coordinate, grid, and stable grid reference;
- mapping method, outside-domain policy, missing-value policy, and coverage criterion;
- relative-integral-error criterion with a source reference;
- gate-report lineage and claim-boundary metadata;
- relative output/report references, provenance reference, and supplied creation time.

Read `references/field-coordinate-unit-sign-contract.md` before authoring the request. Read `references/regular-grid-mapping-methods.md` before choosing grid and method declarations. Read `references/field-mapping-failure-boundaries.md` before interpreting any status.

## Workflow

1. Run `scripts/validate_field_table.py REQUEST --output REPORT`. Reject missing columns or values, non-finite values, duplicate coordinates, hash mismatch, undeclared units, axis-order mismatch, sign mismatch, and grid inconsistency.
2. Run `scripts/normalize_coordinates.py REQUEST --output REPORT`. Apply only the declared signed permutation, scale, and offset; write the normalized table named by the request.
3. Run `scripts/map_regular_field.py REQUEST --output REPORT`. Resolve equivalent cell centers to canonical integer grid indices, then use area overlap on piecewise-constant source cells; write no mapped field when coordinate lookup, equal-domain, or coverage checks fail.
4. Run `scripts/verify_field_integral.py REQUEST --output REPORT`. Compute source integral, target integral, coverage, and relative error. The input criterion decides PASS or FAIL; the script supplies no tolerance.
5. Collect each stage report's `gate` object into a `gate-report-v1` request and call the required `validate-simulation-evidence` Skill. Do not reproduce its status aggregation locally.
6. Run `scripts/write_field_manifest.py REQUEST --output field_manifest.json` only after the gate report exists. It emits `field-manifest-v1` and derives its status from that gate report.

Stop after the first required non-PASS gate unless the caller explicitly needs a bounded diagnostic. Never silently continue with a partially mapped field.

## Outputs

- validated and normalized CSV field tables where the workflow reaches those stages;
- a mapped CSV with declared target coordinates, scalar value, and per-cell coverage;
- stage reports containing `gate-report-v1`-compatible gates and structured evidence identities;
- an integral report with source integral, target integral, relative error, and coverage;
- one `field-manifest-v1` record with field identity, coordinate/grid declarations, mapping parameters, output hash, provenance reference, verification values, gate reference, status, and limitations.

Use `templates/data_dictionary.csv`, `templates/integral_report.json`, and `templates/field_manifest.json` only as empty contract examples. Replace every placeholder.

## Dependencies

- Library contracts: `common-status-v1`, `evidence-manifest-v1`, `gate-report-v1` v1.1-compatible, and `field-manifest-v1`.
- Required Skill: `validate-simulation-evidence`, which exclusively owns required-gate aggregation.
- Python 3 standard library only. No solver, NumPy, SciPy, COMSOL, or Fluent runtime is required.

## Validation

- Parse every Python source file before distribution and exercise success, failure, decimal-coordinate, and coordinate-lookup boundaries with synthetic inputs derived from `templates/`.
- Require an end-to-end synthetic workflow to produce a source identity, explicit coordinate transform, conservative mapping, integral verification, gate aggregation, and schema-ready field manifest with overall `PASS`.
- Run the library Skill validator when its dependencies are available.
- Require byte-identical outputs across repeated runs with the same request.
- Require all emitted paths in manifests to remain relative and all hashes to be SHA-256.
- Confirm the generated gate request passes the existing `validate-simulation-evidence` runtime validator and the final manifest matches `field-manifest-v1`.

## Failure modes

- Treat a source hash mismatch, undeclared axis reversal, field sign mismatch, coordinate lookup failure, target-domain mismatch, missing value, non-finite value, duplicate point, incomplete coverage, or failed integral criterion as evidence failure.
- Treat unsupported dimension, grid type/centering, field rank, mapping method, or policy as `SKIPPED` with a reason code, not as PASS.
- Treat malformed request syntax, unsafe paths, missing required declarations, and I/O errors as script errors; do not fabricate a report.
- A zero source integral can only yield relative error zero when the target integral is also zero. Otherwise report `PARTIAL` with `ZERO_REFERENCE_INTEGRAL`.

## Not suitable for

- 3D, rotated coordinates, curvilinear coordinates, or unstructured meshes;
- node-centered, vector, tensor, complex, multi-component, or transient fields;
- extrapolation, clipping, fill policies, missing-value imputation, or automatic unit conversion;
- automatic axis, origin, normal, sign, unit, or variable-equivalence inference;
- solver setup or execution;
- deciding physical correctness, scientific interpretation, model validity, publishability, novelty, or claim strength.
