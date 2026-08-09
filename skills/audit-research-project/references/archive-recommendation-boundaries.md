# Archive recommendation boundaries

## Recommendation is not authorization

An archive recommendation is a review prompt. Every recommendation must include evidence, affected relative paths, preconditions, `requires_human_decision: true`, and `action_status: NOT_AUTHORIZED`.

The Skill must not:

- move, delete, rename, compress, overwrite, or upload files;
- generate a shell or PowerShell disposal command;
- select a canonical or scientifically final artifact;
- claim a duplicate candidate is redundant;
- treat missing expected output as proof of failed science;
- scan beyond a symlink or junction boundary.

## Required human checks

Before a person takes any later archive action, independently confirm:

- project ownership and retention policy;
- references from scripts, manifests, reports, and external systems;
- backups and recoverability;
- reproducibility and software/license dependencies;
- whether an artifact is current, superseded, failed, reference-only, or authoritative;
- whether a missing output is pending, intentionally absent, or a workflow defect.

## Safe report language

Use “review,” “candidate,” “consider,” “not observed,” and “human confirmation required.” Avoid imperative disposal language. Keep the action status explicitly unauthorized.
