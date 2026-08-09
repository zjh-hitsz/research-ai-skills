# Minimal and Full Handoff Profiles

## Shared contract

Both profiles require an already produced project-audit reference, at least one selected input, a non-empty read order, an entrypoint, environment instructions, provenance references, reuse boundaries, open decisions, a next action, limitations, and verification status.

Selection is explicit. A handoff may read the supplied audit JSON and inventory, but it must not enumerate the project tree or discover extra files. Every `read_order` ID must identify a selected asset. Every portable locator must be relative to the handoff package root.

## Minimal profile

Use `minimal` for one bounded continuation task. It may omit outputs and regression records when the caller explicitly sets `readiness.require_regression` to false. Include only assets needed to understand or perform the next action.

A minimal handoff is not a claim that the entire project is reproducible. It only states that the selected snapshot passed the declared package checks.

## Full profile

Use `full` when the continuation depends on prior generated outputs and a recorded regression. Foundation `handoff-manifest-v1` requires at least one output and one regression record for this profile.

Full does not mean “copy everything.” Keep large or private artifacts as external identities or private dependencies. A full package is ready only when required outputs, listed portable files, required private dependencies, and upstream regression evidence are ready.

## Profile selection boundary

Do not choose full merely to look complete. Choose the smallest profile that preserves the next task's actual dependency chain. If the next task requires evidence not present in the audit snapshot, stop and return to human review or a new audit; do not silently expand selection.
