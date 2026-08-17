from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def _run(command: list[str], cwd: Path | None = None) -> dict[str, Any]:
    completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"Command failed ({completed.returncode}): {' '.join(command)}\n{completed.stdout}\n{completed.stderr}")
    output = completed.stdout.strip()
    return json.loads(output) if output else {}


def _powershell() -> str:
    command = shutil.which("pwsh") or shutil.which("powershell")
    if not command:
        raise RuntimeError("PowerShell is required for the Windows clean-install test.")
    return command


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(source: Path, sandbox: Path) -> dict[str, str]:
    source = source.resolve()
    if any(sandbox.iterdir()):
        raise RuntimeError("Fresh-install sandbox must be empty.")
    skill_root = sandbox / "codex-skills"
    state = sandbox / "local-state"
    primary = sandbox / "synthetic-files"
    communication = sandbox / "synthetic-inbox"
    (primary / "alpha-work" / "src").mkdir(parents=True)
    (primary / "beta-study" / "notes").mkdir(parents=True)
    communication.mkdir(parents=True)
    (primary / "alpha-work" / "src" / "model.py").write_text("print('synthetic')\n", encoding="utf-8")
    (primary / "beta-study" / "notes" / "overview.md").write_text("synthetic baseline\n", encoding="utf-8")
    (communication / "incoming_note.txt").write_text("synthetic attachment evidence\n", encoding="utf-8")

    _run([
        _powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(source / "scripts" / "install.ps1"),
        "-SourcePath", str(source), "-DestinationRoot", str(skill_root),
    ])
    installed = skill_root / "file-intelligence"
    cli = installed / "scripts" / "file_intelligence_cli.py"
    skill_text = (installed / "SKILL.md").read_text(encoding="utf-8")
    discovery_pass = skill_text.startswith("---\nname: file-intelligence\n") and (installed / "agents" / "openai.yaml").is_file()
    status_before = _run([sys.executable, str(cli), "status", "--state-dir", str(state)])
    if status_before.get("status") != "DEEP_ONBOARDING_REQUIRED":
        raise RuntimeError("Fresh state did not route to Deep Onboarding.")
    onboarding = _run([
        sys.executable, str(cli), "onboard", "--root", str(primary), "--communication-root", str(communication),
        "--state-dir", str(state), "--backend", "filesystem",
    ])
    if onboarding.get("status") != "BASELINE_CREATED" or not (state / "catalog.db").is_file() or not (state / "baseline.json").is_file():
        raise RuntimeError("Synthetic Deep Onboarding did not create the external baseline.")
    (primary / "alpha-work" / "src" / "new_input.txt").write_text("synthetic change\n", encoding="utf-8")
    first = _run([sys.executable, str(cli), "maintain", "--state-dir", str(state), "--backend", "filesystem"])
    if first.get("status") != "CHANGES_RECORDED" or first.get("counts", {}).get("new") != 1:
        raise RuntimeError("First Maintenance did not record the synthetic change.")
    second = _run([sys.executable, str(cli), "maintain", "--state-dir", str(state), "--backend", "filesystem"])
    if second.get("status") != "NO_OP" or any(second.get("counts", {}).get(key) for key in ("new", "changed", "missing")):
        raise RuntimeError("Second unchanged Maintenance was not a no-op.")
    protected = {path.name: _sha256(path) for path in (state / "catalog.db", state / "baseline.json", state / "last_changes.json")}
    _run([
        _powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(source / "scripts" / "update.ps1"),
        "-SourcePath", str(source), "-DestinationRoot", str(skill_root),
    ])
    observed = {path.name: _sha256(path) for path in (state / "catalog.db", state / "baseline.json", state / "last_changes.json")}
    if protected != observed:
        raise RuntimeError("Code-only update changed external machine state.")
    return {
        "fresh_machine": "PASS",
        "install": "PASS",
        "skill_discovery": "PASS" if discovery_pass else "FAIL",
        "synthetic_deep_onboarding": "PASS",
        "baseline_creation": "PASS",
        "maintenance": "PASS",
        "noop_maintenance": "PASS",
        "update_preserves_state": "PASS",
        "production_state_used": "NO",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify File Intelligence in a disposable fresh-machine sandbox")
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--sandbox", type=Path)
    arguments = parser.parse_args()
    if arguments.sandbox:
        arguments.sandbox.mkdir(parents=True, exist_ok=True)
        result = verify(arguments.source, arguments.sandbox)
    else:
        with tempfile.TemporaryDirectory(prefix="file_intelligence_fresh_") as temporary:
            result = verify(arguments.source, Path(temporary))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if all(value in {"PASS", "NO"} for value in result.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
