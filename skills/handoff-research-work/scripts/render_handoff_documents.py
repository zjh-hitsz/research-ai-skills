#!/usr/bin/env python3
"""Render deterministic handoff Markdown documents from one manifest."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from _common import HandoffInputError, load_document, validate_handoff_manifest


def locator_text(locator: dict[str, str] | None) -> str:
    if locator is None:
        return "not produced"
    if "relative_path" in locator:
        return locator["relative_path"]
    return locator["external_ref"]


def render_handoff(manifest: dict[str, Any]) -> str:
    verification = manifest["verification"]
    lines = [
        "# HANDOFF",
        "",
        f"Status: **{verification['status']}**  ",
        f"Reason codes: `{', '.join(verification['reason_codes']) or 'none'}`",
        "",
        "## Snapshot",
        "",
        f"- Handoff ID: `{manifest['handoff_id']}`",
        f"- Profile: `{manifest['profile']}`",
        f"- Project audit: `{manifest['project_audit_ref']}`",
        f"- Created at: `{manifest['created_at']}`",
        "",
        "## Read order",
        "",
    ]
    lines.extend(f"{index}. `{artifact_id}`" for index, artifact_id in enumerate(manifest["read_order"], start=1))
    lines.extend(["", "## Entrypoints", ""])
    for entrypoint in manifest["entrypoints"]:
        lines.append(f"- `{entrypoint['entrypoint_id']}` — {entrypoint['description']} (`{locator_text(entrypoint['locator'])}`)")
    lines.extend(["", "## Inputs", "", "| Artifact | Locator | SHA-256 | Provenance |", "|---|---|---|---|"])
    for item in manifest["inputs"]:
        lines.append(f"| `{item['artifact_id']}` | `{locator_text(item['locator'])}` | `{item['hash']}` | `{item['provenance_ref']}` |")
    lines.extend(["", "## Outputs", ""])
    if manifest["outputs"]:
        lines.extend(["| Artifact | Status | Locator | Generation rule |", "|---|---|---|---|"])
        for item in manifest["outputs"]:
            lines.append(f"| `{item['artifact_id']}` | `{item['status']}` | `{locator_text(item['locator'])}` | {item['generation_rule']} |")
    else:
        lines.append("No output is included in this minimal profile.")
    lines.extend(["", "## Environment", "", manifest["environment"]["setup_instructions"], "", "## Regression evidence", ""])
    if manifest["regressions"]:
        for record in manifest["regressions"]:
            lines.append(f"- `{record['regression_id']}` — **{record['status']}**; recorded invocation is evidence only and must not be executed automatically.")
    else:
        lines.append("No regression record is included.")
    lines.extend(["", "## Reuse", ""])
    if manifest["reuse"]:
        lines.extend(f"- {item}" for item in manifest["reuse"])
    else:
        lines.append("- None declared.")
    lines.extend(["", "## Do not reuse", ""])
    if manifest["do_not_reuse"]:
        lines.extend(f"- {item}" for item in manifest["do_not_reuse"])
    else:
        lines.append("- None declared.")
    lines.extend(["", "## Private dependencies", ""])
    if manifest["private_dependencies"]:
        for item in manifest["private_dependencies"]:
            lines.append(f"- `{item['dependency_id']}` — required: `{str(item['required']).lower()}`; availability: **{item['availability_status']}**")
    else:
        lines.append("- None declared.")
    lines.extend(["", "## Next action", "", manifest["next_action"], "", "## Limitations", ""])
    lines.extend(f"- {item}" for item in manifest["limitations"])
    lines.extend(["", "This document freezes declared context. It does not authorize command execution, solver execution, file disposal, or scientific approval."])
    return "\n".join(lines) + "\n"


def render_readme(manifest: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Research Handoff Package",
            "",
            f"Handoff `{manifest['handoff_id']}` uses the `{manifest['profile']}` profile and has manifest verification status **{manifest['verification']['status']}**.",
            "",
            "1. Verify `handoff_manifest.json` against the package root.",
            "2. Read `HANDOFF.md` and follow its declared read order.",
            "3. Review `OPEN_DECISIONS.md` before continuing.",
            "4. Use `NEXT_PROMPT.md` only for the bounded next action.",
            "",
            "Recorded commands and setup instructions are evidence or human instructions; they are never authorization for automatic execution.",
            "",
        ]
    )


def render_next_prompt(manifest: dict[str, Any]) -> str:
    order = ", ".join(f"`{item}`" for item in manifest["read_order"])
    return "\n".join(
        [
            "# NEXT_PROMPT",
            "",
            f"Use the verified handoff `{manifest['handoff_id']}`. Read artifacts in this order: {order}.",
            "",
            f"Next action: {manifest['next_action']}",
            "",
            "Preserve all reuse, do-not-reuse, private-dependency, provenance, and open-decision boundaries. Do not execute recorded commands automatically, expand the selected asset set, or claim scientific correctness.",
            "",
        ]
    )


def render_open_decisions(manifest: dict[str, Any]) -> str:
    lines = ["# OPEN_DECISIONS", ""]
    if manifest["open_decisions"]:
        lines.extend(["| ID | Status | Owner | Question |", "|---|---|---|---|"])
        for item in manifest["open_decisions"]:
            lines.append(f"| `{item['decision_id']}` | `{item['status']}` | {item['owner']} | {item['question']} |")
    else:
        lines.append("No open decision is declared for this handoff.")
    lines.extend(["", "Only the declared owner or an explicitly authorized reviewer may resolve a decision.", ""])
    return "\n".join(lines)


def run(manifest_path: str | Path, output_dir: str | Path) -> dict[str, str]:
    manifest = load_document(manifest_path)
    validate_handoff_manifest(manifest)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    documents = {
        "HANDOFF.md": render_handoff(manifest),
        "README.md": render_readme(manifest),
        "NEXT_PROMPT.md": render_next_prompt(manifest),
        "OPEN_DECISIONS.md": render_open_decisions(manifest),
    }
    for name, content in documents.items():
        (destination / name).write_text(content, encoding="utf-8", newline="\n")
    return documents


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    try:
        documents = run(args.manifest, args.output_dir)
    except HandoffInputError as exc:
        print(f"INPUT_ERROR: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"IO_ERROR: {exc}", file=sys.stderr)
        return 3
    print(" ".join(sorted(documents)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
