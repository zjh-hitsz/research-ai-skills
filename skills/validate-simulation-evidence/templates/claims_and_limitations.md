# Claims and limitations

Use `claim_boundary.claims` for new `1.1.0` reports. Give each claim a stable `claim_id`, bounded `text`, user-supplied `status`, same-report `supporting_gate_ids`, and explicit `limitations`. Keep the legacy three-list profile only for backward compatibility; do not mix profiles.

## Supported

- Add only claims directly supported by passed required gates and referenced evidence.

## Conditional

- Add claims limited by model scope, PARTIAL/SKIPPED checks, optional-gate results, or unresolved sensitivity.

## Unsupported

- Add claims contradicted by failed gates or not covered by available evidence.

## Limitations

- Record missing evidence, model-scope limits, unexecuted checks, uncertainty, and external validation still required.

## Human review required

- Identify the researcher responsible for approving scientific interpretation. A gate report cannot approve physical correctness, novelty, or publication readiness.
