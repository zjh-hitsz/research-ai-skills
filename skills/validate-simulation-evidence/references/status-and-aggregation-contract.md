# Status and aggregation contract

## Source of truth

Use the library-level `common-status-v1.schema.json` as the only status definition. This Skill must read it at runtime and must not maintain a second enum.

## Gate status

| Status | Meaning | Reason-code rule |
|---|---|---|
| `PASS` | The declared criterion was executed and satisfied with referenced evidence. | `reason_codes` must be empty. |
| `FAIL` | The declared criterion was executed and not satisfied. | At least one reason code is required. |
| `PARTIAL` | Some evidence exists, but the declared check or its coverage is incomplete. | At least one reason code is required. |
| `SKIPPED` | The check was not executed because it was blocked, not applicable, unauthorized, or unsupported. | At least one reason code is required. |

Reason codes use uppercase letters, digits, and underscores. Values such as `UNSOLVED`, `UPSTREAM_BLOCKED`, and `UNSUPPORTED_VERSION` are reason codes, not additional statuses.

Foundation v0.2 registers two additional reason codes without changing the status enum:

- `EVIDENCE_PARSE_FAILED`: an identified evidence artifact exists but does not parse under its declared media type or parser contract;
- `EVIDENCE_SNAPSHOT_MISMATCH`: evidence belongs to a different attempt, stage, snapshot, or subject revision.

Do not replace either code with a fifth status.

## Required-gate aggregation

Apply the following precedence to gates with `required: true`:

1. If any required gate is `FAIL`, set overall to `FAIL` and add `REQUIRED_GATE_FAILED` before the underlying failed-gate reasons.
2. Otherwise, if any required gate is `PARTIAL`, set overall to `PARTIAL` and add `REQUIRED_GATE_PARTIAL`. Include non-PASS reasons from required PARTIAL and required SKIPPED gates.
3. Otherwise, if required gates mix `PASS` and `SKIPPED`, set overall to `PARTIAL` and add `REQUIRED_GATE_SKIPPED`.
4. Otherwise, if every required gate is `SKIPPED`, set overall to `SKIPPED` and add `ALL_REQUIRED_GATES_SKIPPED`.
5. Only when every required gate is `PASS`, set overall to `PASS` with no reason codes.

Reject a request that contains no required gate. Optional gates remain visible but do not change overall status.

## Determinism

- Preserve gate order from the input manifest.
- Preserve first occurrence order when de-duplicating reason codes.
- Take `created_at` from input; do not generate the current time.
- Serialize JSON with sorted keys and stable indentation.
- Treat evidence FAIL as a successful script execution. Use a nonzero process exit only for invalid input or I/O failure.

## Criteria policy

Every criterion must identify its source. Numerical check scripts additionally require metric, operator, and threshold. Never supply a default scientific threshold. The evaluator aggregates declared gate results; it does not recompute or second-guess their domain meaning.

## Snapshot policy

Aggregate only gates from one frozen lineage snapshot. Use `supersedes_report_id` to connect failure, correction, and revalidation reports. A superseded failure remains a valid historical report but must not be copied into the required gates of the later snapshot.
