# Field-mapping failure boundaries

## Evidence failures

These are valid script executions that produce a non-PASS gate:

| Condition | Status | Typical reason code |
|---|---|---|
| Actual source hash differs from request | FAIL | `SOURCE_HASH_MISMATCH` |
| Coordinate columns do not match declared axis order | FAIL | `AXIS_ORDER_MISMATCH` |
| Source/target field sign declarations differ | FAIL | `SIGN_CONVENTION_MISMATCH` |
| Field units differ without a supported value conversion | FAIL | `FIELD_UNIT_MISMATCH` |
| Missing, nonnumeric, or non-finite value | FAIL | `MISSING_VALUE`, `NONNUMERIC_VALUE`, `NONFINITE_VALUE` |
| Duplicate cell-center coordinate | FAIL | `DUPLICATE_COORDINATE` |
| Observed points do not match declared regular grid | FAIL | `GRID_SHAPE_MISMATCH`, `GRID_COORDINATE_MISMATCH` |
| An accepted or injected coordinate cannot resolve to one canonical source cell | FAIL | `COORDINATE_LOOKUP_FAILED` |
| Explicit transform does not produce its declared output grid | FAIL | `COORDINATE_TRANSFORM_MISMATCH` |
| Source and target physical domains differ | FAIL | `TARGET_DOMAIN_MISMATCH` |
| Coverage misses caller criterion | FAIL | `COVERAGE_CRITERION_NOT_MET` |
| Relative integral error misses caller criterion | FAIL | `INTEGRAL_CRITERION_NOT_MET` |

Stage scripts return zero after writing these reports. The shared gate aggregator owns the overall status.

## Unsupported requests

Requests for 3D, non-Cartesian or rotated coordinates, unstructured or node-centered grids, vector/tensor/transient fields, another mapping method, extrapolation, fill/clip, or missing-value imputation are outside v0.1. Return `SKIPPED` with a specific `UNSUPPORTED_*` reason where a valid request reaches a stage. Do not approximate with the regular-grid method.

## Invalid requests and I/O failures

Malformed JSON-compatible YAML, missing required declarations, unsafe/absolute/escaping paths, invalid criteria, and unreadable files are request or I/O errors. The CLI exits non-zero and does not fabricate a scientific status.

## Release boundaries

- Stop the normal workflow after a required non-PASS stage.
- Do not release a partial mapped field after domain or coverage failure.
- Do not overwrite the source table.
- Do not use a successful integral alone to approve physical correctness.
- Do not infer variable equivalence, normal direction, units, axis order, or acceptable threshold.
- Do not execute COMSOL, Fluent, another solver, or arbitrary commands.
- Do not turn gate PASS into a publication, novelty, mechanism, or model-validity claim.

## Human review remains required

A researcher or appropriate upstream audit must decide whether the declared variable is the intended physical quantity, whether sign and units are scientifically correct, whether source and target fields are semantically equivalent, and whether the supplied coverage/integral criteria are adequate for the study.
