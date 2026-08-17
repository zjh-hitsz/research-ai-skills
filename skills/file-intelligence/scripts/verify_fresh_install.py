from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any


def _run(command: list[str], cwd: Path | None = None) -> dict[str, Any]:
    completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"Command failed ({completed.returncode}): {' '.join(command)}\n{completed.stdout}\n{completed.stderr}")
    output = completed.stdout.strip()
    return json.loads(output) if output else {}


def _run_text(command: list[str], cwd: Path | None = None) -> str:
    completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"Command failed ({completed.returncode}): {' '.join(command)}\n{completed.stdout}\n{completed.stderr}")
    return completed.stdout.strip()


def _run_expected_failure(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if completed.returncode == 0:
        raise RuntimeError(f"Command unexpectedly succeeded: {' '.join(command)}")
    output = completed.stderr.strip() or completed.stdout.strip()
    return json.loads(output) if output else {"status": "ERROR"}


def _powershell() -> str:
    command = shutil.which("pwsh") or shutil.which("powershell")
    if not command:
        raise RuntimeError("PowerShell is required for the Windows clean-install test.")
    return command


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _state_hashes(state: Path) -> dict[str, str]:
    with closing(sqlite3.connect(state / "catalog.db")) as connection:
        logical_catalog = "\n".join(connection.iterdump()).encode("utf-8")
    return {
        "catalog.logical": hashlib.sha256(logical_catalog).hexdigest(),
        "baseline.json": _sha256(state / "baseline.json"),
        "last_changes.json": _sha256(state / "last_changes.json"),
    }


def _manifest_digest(payload: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in payload.items() if key != "digest"}
    return hashlib.sha256(json.dumps(unsigned, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def verify(source: Path, sandbox: Path) -> dict[str, str]:
    source = source.resolve()
    if any(sandbox.iterdir()):
        raise RuntimeError("Fresh-install sandbox must be empty.")
    skill_root = sandbox / "codex-skills"
    state = sandbox / "machine-a-state"
    second_state = sandbox / "machine-b-state"
    primary = sandbox / "machine-a-files"
    second_primary = sandbox / "machine-b-files"
    communication = sandbox / "synthetic-inbox"
    (primary / "alpha-work" / "src").mkdir(parents=True)
    (primary / "beta-study" / "notes").mkdir(parents=True)
    (second_primary / "independent-project").mkdir(parents=True)
    communication.mkdir(parents=True)
    (primary / "alpha-work" / "src" / "model.py").write_text("print('synthetic')\n", encoding="utf-8")
    (primary / "beta-study" / "notes" / "overview.md").write_text("synthetic baseline\n", encoding="utf-8")
    (communication / "incoming_note.txt").write_text("synthetic attachment evidence\n", encoding="utf-8")
    (second_primary / "independent-project" / "README.md").write_text("# Independent synthetic machine B\n", encoding="utf-8")

    _run([
        _powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(source / "scripts" / "install.ps1"),
        "-SourcePath", str(source), "-DestinationRoot", str(skill_root),
    ])
    installed = skill_root / "file-intelligence"
    cli = installed / "scripts" / "file_intelligence_cli.py"
    skill_text = (installed / "SKILL.md").read_text(encoding="utf-8")
    discovery_pass = skill_text.startswith("---\nname: file-intelligence\n") and (installed / "agents" / "openai.yaml").is_file()
    if _run_text([sys.executable, str(cli), "--version"]) != "0.3.0":
        raise RuntimeError("Installed package version is not 0.3.0.")
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
    timeline = _run([sys.executable, str(cli), "timeline", "--state-dir", str(state), "--days", "7"])
    if int(timeline.get("event_count") or 0) < 1:
        raise RuntimeError("Timeline query did not return the synthetic Maintenance event.")
    protected = _state_hashes(state)

    if installed.resolve().parent != skill_root.resolve():
        raise RuntimeError("Refusing to remove a Skill path outside the disposable install root.")
    shutil.rmtree(installed)
    if not (state / "catalog.db").is_file() or protected != _state_hashes(state):
        raise RuntimeError("Removing disposable Skill code changed external machine state.")
    _run([
        _powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(source / "scripts" / "install.ps1"),
        "-SourcePath", str(source), "-DestinationRoot", str(skill_root),
    ])
    cli = installed / "scripts" / "file_intelligence_cli.py"
    if _run_text([sys.executable, str(cli), "--version"]) != "0.3.0":
        raise RuntimeError("Reinstalled package version is not 0.3.0.")
    if _run([sys.executable, str(cli), "status", "--state-dir", str(state)]).get("status") != "MAINTENANCE_READY":
        raise RuntimeError("Reinstalled Skill did not rediscover the preserved state.")
    if protected != _state_hashes(state):
        raise RuntimeError("Reinstall changed external machine state.")

    _run([
        _powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(source / "scripts" / "update.ps1"),
        "-SourcePath", str(source), "-DestinationRoot", str(skill_root),
    ])
    observed = _state_hashes(state)
    if protected != observed:
        raise RuntimeError("Code-only update changed external machine state.")

    second_onboarding = _run([
        sys.executable, str(cli), "onboard", "--root", str(second_primary),
        "--state-dir", str(second_state), "--backend", "filesystem",
    ])
    first_baseline = json.loads((state / "baseline.json").read_text(encoding="utf-8"))
    second_baseline = json.loads((second_state / "baseline.json").read_text(encoding="utf-8"))
    if first_baseline["baseline_id"] == second_baseline["baseline_id"]:
        raise RuntimeError("Independent synthetic machines received the same baseline identity.")
    first_roots = {item["path"] for item in first_baseline["roots"]}
    second_roots = {item["path"] for item in second_baseline["roots"]}
    if first_roots & second_roots or second_onboarding.get("state_dir") != str(second_state.resolve()):
        raise RuntimeError("Synthetic machine states were not isolated.")

    foreign_state = sandbox / "copied-foreign-state"
    shutil.copytree(state, foreign_state)
    foreign_baseline_path = foreign_state / "baseline.json"
    foreign_baseline = json.loads(foreign_baseline_path.read_text(encoding="utf-8"))
    foreign_baseline["machine_binding"] = "synthetic-foreign-machine-binding"
    foreign_baseline["machine_binding_version"] = 2
    foreign_baseline["digest"] = _manifest_digest(foreign_baseline)
    foreign_baseline_path.write_text(json.dumps(foreign_baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    foreign_status = _run([sys.executable, str(cli), "status", "--state-dir", str(foreign_state)])
    if foreign_status.get("status") != "MACHINE_REBIND_REQUIRED" or foreign_status.get("machine_binding_valid") is not False:
        raise RuntimeError("A copied foreign state was not rejected by machine binding.")
    rejected = _run_expected_failure([sys.executable, str(cli), "maintain", "--state-dir", str(foreign_state)])
    if rejected.get("status") != "ERROR" or int(rejected.get("physical_actions", -1)) != 0:
        raise RuntimeError("Foreign-state Maintenance did not fail safely.")
    return {
        "fresh_machine": "PASS",
        "install": "PASS",
        "package_version_0_3_0": "PASS",
        "skill_discovery": "PASS" if discovery_pass else "FAIL",
        "synthetic_deep_onboarding": "PASS",
        "baseline_creation": "PASS",
        "maintenance": "PASS",
        "noop_maintenance": "PASS",
        "timeline_query": "PASS",
        "uninstall_reinstall_preserves_state": "PASS",
        "update_preserves_state": "PASS",
        "cross_machine_state_isolation": "PASS",
        "copied_foreign_state_rejected": "PASS",
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
