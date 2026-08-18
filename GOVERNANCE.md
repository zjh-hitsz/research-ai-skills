# Governance

## Scope and authority

This repository is authoritative only for reusable, public-safe Skill procedures, schemas, empty templates, and discovery metadata. It is not authoritative for research Memory, project status, scientific facts, private file indexes, machine paths, credentials, or runtime outputs.

`registry/skill-registry.yaml` is the single machine-readable catalog. A Skill directory must not exist without a matching registry entry, and a registry entry must not exist without its directory.

## Change rules

- Preserve the public/private boundary. Never add real research inputs, outputs, locators, identities, databases, or secrets.
- Treat discovery metadata as an interface: examples must describe the capability honestly and limitations must remain explicit.
- Keep dependencies acyclic and declare them in the registry.
- Use Semantic Versioning independently for the repository and each Skill.
- Require review and a passing validation workflow before release.

Scientific decisions remain human decisions. A Skill may structure or validate configured evidence but cannot make itself authoritative by being present in this repository.
