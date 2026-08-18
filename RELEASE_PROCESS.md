# Release Process

1. Start from a clean clone and run `python scripts/validate_repository.py`.
2. Run any Skill-specific tests and review `docs/GITHUB_RELEASE_CHECK.md`.
3. Confirm the registry versions, changelog, and release notes agree.
4. Review the full diff and history for private material and secrets.
5. Merge only after CI and human review pass.
6. Create an annotated repository tag `repo-vX.Y.Z` for the collection snapshot.
7. When publishing a specific Skill version, also create `skill-<name>-vX.Y.Z` at the same reviewed commit.

The historical `v0.3.0` tag remains the compatible File Intelligence v0.3.0 install reference. New repository releases use the explicit `repo-v...` namespace so repository and per-Skill versions cannot be confused.

Release validation writes no report back into the repository and uploads no private artifacts.
