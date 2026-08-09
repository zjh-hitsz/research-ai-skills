# Partial-scope human review contract

Use `templates/scope_review_record.json` only when an upstream project audit is `PARTIAL` and a human reviewer has explicitly decided whether a bounded handoff may exclude the unresolved boundaries.

The record contains exactly seven fields:

- `scope_id`: stable identity for this review decision;
- `reviewed_boundaries`: every upstream audit reason code the reviewer considered;
- `accepted_exclusions`: stable descriptions or IDs of exclusions the reviewer explicitly accepts;
- `blocked_items`: unresolved items that still prevent handoff;
- `reviewer_role`: a human role, not an agent or automation identity;
- `timestamp`: supplied review time;
- `decision`: `accepted` or `rejected`, supplied by the human reviewer.

An accepted record is complete only when it covers every upstream `PARTIAL` reason, declares at least one accepted exclusion, and contains no blocked item. A rejected record is fail-closed. An upstream `FAIL` can never be overridden.

The evaluator validates and records a supplied decision. It does not select exclusions, infer intent, authenticate identity, or approve anything. The handoff manifest retains the review identity and original audit boundaries in `limitations` because the unchanged Foundation schema has no scope-review field. The upstream audit record remains unchanged.

This contract does not establish scientific correctness, completeness of undiscovered files, publication readiness, permission to disclose private assets, or authority to delete or copy anything.
