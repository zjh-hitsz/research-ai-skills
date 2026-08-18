# Research AI Skills

Research AI Skills is a public-safe collection of reusable AI-agent workflows for simulation evidence, physical-field transfer, research-project auditing, research handoff, and private Windows file intelligence. The repository contains procedures, standard-library scripts, empty templates, and machine-readable contracts. It intentionally excludes research memory, project snapshots, papers, models, real datasets, runtime outputs, and private locators.

## Architecture

The library separates three concerns:

- **Memory** is private, project-specific context. It stays outside this repository.
- **Skill** is reusable procedure. This repository contains only this layer.
- **AI** selects and executes Skills against inputs supplied at runtime; it must not treat Skill text as scientific evidence.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full three-layer model and dependency rules.

```text
research-ai-skills/
|-- README.md
|-- LICENSE
|-- CHANGELOG.md
|-- GOVERNANCE.md
|-- RELEASE_PROCESS.md
|-- scripts/
|-- registry/
|   `-- skill-registry.yaml
|-- schemas/
|-- skills/
|   |-- validate-simulation-evidence/
|   |-- map-physical-fields/
|   |-- audit-research-project/
|   |-- handoff-research-work/
|   `-- file-intelligence/
`-- docs/
    |-- ARCHITECTURE.md
    `-- GITHUB_RELEASE_CHECK.md
```

Each Skill contains a required `SKILL.md` plus only the resources needed at runtime: `agents/`, `references/`, `scripts/`, and `templates/`. Test fixtures and historical validation reports are not distributed.

## Skills

| Skill | Version | Status | Purpose | Dependencies |
| --- | --- | --- | --- | --- |
| `validate-simulation-evidence` | 0.2.0 | implementation | Aggregate configured simulation evidence gates and claim boundaries. | None |
| `map-physical-fields` | 0.1.0 | implementation | Validate and conservatively map regular 2D scalar fields. | `validate-simulation-evidence` |
| `audit-research-project` | 0.1.0 | implementation | Produce a read-only project inventory and bounded risk audit. | None |
| `handoff-research-work` | 0.1.0 | implementation | Freeze an audited selection into a verifiable handoff manifest. | `audit-research-project` |
| `file-intelligence` | 0.3.0 | released | Maintain private Windows file/project intelligence with append-oriented history, lightweight snapshots, semantic diffs, and no physical file changes. | None |

The machine-readable source of versions, statuses, dependencies, bilingual discovery examples, context signals, limitations, and execution classes is [registry/skill-registry.yaml](registry/skill-registry.yaml). Consumers should recommend from these fields rather than from a hard-coded Skill-name table.

## Usage

For the stable File Intelligence v0.3.0 Windows install and six-command first run, start with [`skills/file-intelligence/README.md`](skills/file-intelligence/README.md#quick-start) and use the historical compatible tag `v0.3.0`. Skill code comes from GitHub; every computer keeps its own private state under `%LOCALAPPDATA%\FileIntelligence`.

1. Copy one or more folders from `skills/` into the Skill directory used by your AI agent, preserving each folder name.
2. Keep required dependencies together. For example, install `validate-simulation-evidence` with `map-physical-fields`.
3. Invoke the Skill by name, such as: `Use $audit-research-project to produce a read-only audit of this project.`
4. Start from the selected Skill's templates and provide explicit runtime inputs. Keep real inputs, outputs, credentials, and private paths outside the repository.
5. Review all scientific criteria and decisions yourself. The Skills organize evidence; they do not establish scientific truth.

The scripts target Python 3.10 or later unless a Skill states otherwise. They use the Python standard library and expect paths in persistent manifests to be relative.

## Version Strategy

The repository and each Skill use Semantic Versioning independently:

- **Major**: incompatible workflow, interface, or schema change.
- **Minor**: backward-compatible capability or contract addition.
- **Patch**: backward-compatible correction or documentation improvement.

Repository tags use `repo-vX.Y.Z` and describe a tested collection snapshot. Per-Skill tags use `skill-<name>-vX.Y.Z`. Individual Skill versions remain in the registry and change only when that Skill changes. Schema files carry their own versioned names; incompatible schema revisions receive a new major-version file instead of silently changing an existing contract. See [RELEASE_PROCESS.md](RELEASE_PROCESS.md).

Skill status progresses through `planned`, `incubator`, `implementation`, `release-candidate`, `released`, and `deprecated`. A repository release does not automatically promote every Skill to `released`.

## Publication Boundary

Before any GitHub release, run `python scripts/validate_repository.py` and complete [docs/GITHUB_RELEASE_CHECK.md](docs/GITHUB_RELEASE_CHECK.md). In particular, do not commit Memory, canonical project records, project files, papers, models, real research data, credentials, absolute local paths, caches, generated reports, or runtime artifacts.

## License

Released under the [MIT License](LICENSE).
