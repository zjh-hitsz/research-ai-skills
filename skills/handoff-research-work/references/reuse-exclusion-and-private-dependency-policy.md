# Reuse, Exclusion, and Private Dependency Policy

## Reuse

List only methods, configuration concepts, documented interfaces, and selected artifacts that the next task is permitted to reuse. Reuse entries are guidance, not scientific validation or license approval.

## Do not reuse

Record obsolete outputs, failed attempts, project-specific constants, stale caches, superseded snapshots, and artifacts outside the next task's authority. An exclusion does not delete or archive anything.

## Private and large dependencies

- Do not copy a large model, unpublished dataset, credential, license file, private server path, or solver cache into a handoff.
- Represent a private requirement with an opaque `dependency_id`, required flag, availability status, and reason codes.
- Represent portable files with relative paths. Use an external reference only when the downstream resolver is separately authorized.
- A required private dependency with `FAIL` makes the handoff `FAIL`; incomplete or skipped availability keeps it non-ready.

## Provenance

Every selected input or output carries a `provenance_ref`. This Skill verifies the reference is declared but does not infer source rights or resolve private locators. Rights, privacy, and extraction decisions belong to the Foundation asset-provenance registry and human governance.

## Human decisions

The researcher decides which audited candidates are authoritative, which dependencies may be shared, whether an exclusion remains valid, and whether an open decision is resolved. The Skill freezes those declarations without upgrading them to scientific truth.
