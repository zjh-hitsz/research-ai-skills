# Regression-First and Fail-Stop Contract

## Existing records only

A regression record is upstream evidence. It contains an entrypoint reference, a textual invocation summary, authorization state, execution timestamp, exit code, expected reference, actual reference, status, and reason codes.

Use `templates/regression_record.json` when converting a reviewed human-readable report into a machine-readable record. The conversion is manual evidence transcription, not report interpretation. The structured template contains `expected`, `actual`, `artifact_refs`, and `timestamp`; the builder validates selected artifact IDs and normalizes these values into the existing `handoff-manifest-v1` regression fields. No Foundation schema field is added.

For a structured record:

- `PASS` requires authorization, exit code zero, equal non-null expected/actual references, and at least one selected artifact reference.
- `FAIL` requires actual evidence and non-empty reason codes. A hash mismatch remains `FAIL / HASH_MISMATCH`; it is not relabeled as an upstream command failure when the recorded exit code is zero.
- `PARTIAL` records missing or incomplete evidence with non-empty reason codes and may use a null actual reference or exit code.
- `artifact_refs` must identify assets already selected into the handoff. They are validation links, not instructions to copy artifacts.
- `timestamp` becomes the canonical recorded execution/evidence timestamp. The Skill does not generate it.

The Skill reads these fields only. It must never pass `invocation_summary`, setup instructions, or entrypoint content to a shell, subprocess, notebook, macro host, solver, or scheduler.

## Readiness rules

| Evidence state | Handoff readiness |
|---|---|
| Required record is present, authorized, executed, exit code is zero, actual reference exists, and upstream status is `PASS` | May remain `PASS` |
| Required record is absent | `PARTIAL / MISSING_REGRESSION_RECORD` |
| Record is `PARTIAL` or `SKIPPED` | `PARTIAL` with upstream reasons |
| Record is unauthorized, has a non-zero exit code, or is `FAIL` | `FAIL` |

An upstream PASS is consumed as evidence, not independently reproduced. A handoff PASS therefore means “the declared record and package checks pass,” not “the underlying scientific computation is correct.”

## Fail-stop behavior

Do not render a non-ready package as if it were ready. A renderer may document a non-ready manifest for diagnosis, but the status and reasons must remain prominent. Do not change FAIL to PARTIAL to preserve workflow progress.
