# GitHub Release Check

Complete this checklist from a clean local clone before creating a GitHub release. Do not release while any item is unresolved.

## Repository Scope

- [ ] The repository contains exactly the intended four Foundation Skill directories.
- [ ] No Memory export, canonical project record, project snapshot, paper, model, dataset, result bundle, or checkpoint is tracked.
- [ ] No eval fixture, runtime output, cache, generated validation report, or temporary report is tracked.
- [ ] Every tracked file is necessary for documentation, registry metadata, schemas, or Skill execution.

## Private Data and Secrets

- [ ] Every template contains placeholders or synthetic values only.
- [ ] No unpublished value, real sample identifier, participant information, hostname, username, email address, or private repository name appears in tracked files.
- [ ] Secret scanning reports no credentials, tokens, private keys, connection strings, or environment files.
- [ ] Git history contains none of the excluded material; deleting it only from the latest working tree is insufficient.

## Paths and Portability

- [ ] Documentation, templates, manifests, and examples contain no machine-specific absolute path.
- [ ] Persistent artifact references are relative or opaque external identifiers.
- [ ] Absolute-path detection expressions in audit code are guards only and contain no real locator.
- [ ] Commands and instructions work from the repository root without relying on a personal directory layout.

## Reproducible Structure

- [ ] `registry/skill-registry.yaml` lists a unique name, version, status, and dependency list for every Skill.
- [ ] Every registry dependency resolves to another registered Skill and the dependency graph is acyclic.
- [ ] Every Skill folder name matches the `name` in its `SKILL.md` frontmatter.
- [ ] Every JSON schema parses successfully and every schema reference resolves within `schemas/`.
- [ ] Every Python source file parses successfully with the supported Python version.
- [ ] The official Skill quick validator passes for all four Skill directories.
- [ ] A second clean clone has the same tracked structure and does not require ignored files to load a Skill.

## Git Review

- [ ] `git status --short --ignored` shows only understood files.
- [ ] `git ls-files` has been reviewed line by line.
- [ ] A secret scanner has been run against both the working tree and repository history.
- [ ] The release commit is tagged with the repository Semantic Version and the registry version matches the release notes.

## Release Decision

- [ ] **PASS — no private data**
- [ ] **PASS — no secrets**
- [ ] **PASS — no absolute local paths**
- [ ] **PASS — reproducible structure**

Reviewer: ____________________

Date: ____________________

Commit: ____________________
