from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..intelligence_models import Capability, GitRepoCard, SensorHealth, SensorObservation
from .base import BaseSensor
from .locator import first_executable, toolbox_hint


class GitSensor(BaseSensor):
    sensor_id = "git"
    display_name = "Git Repository Sensor"

    def detect(self) -> dict[str, Any]:
        configured = self.config.get("executables", {}).get("git")
        toolbox = toolbox_hint(self.config, "git")
        exe = first_executable([configured, toolbox], ["git.exe", "git"])
        return {"executable": exe}

    def capabilities(self) -> set[Capability]:
        return {Capability.GIT_STATUS}

    def version(self) -> str | None:
        exe = self.detect()["executable"]
        if not exe:
            return None
        result = self.runner.run([str(exe), "--version"], timeout=8)
        return result.stdout.strip() if result.ok else None

    def source(self) -> dict[str, str]:
        return {"vendor": "Git", "official_url": "https://git-scm.com/download/win"}

    def health_check(self):
        exe = self.detect()["executable"]
        version = self.version()
        return self._status(
            version=version,
            available=bool(exe),
            health=SensorHealth.HEALTHY if exe and version else SensorHealth.UNAVAILABLE,
            executable_path=str(exe) if exe else None,
            failure_reason=None if version else "git executable unavailable or failed",
        )

    def _git(self, exe: Path, repo: str, *args: str):
        # Trust only this explicitly requested scope for this one invocation. Never mutate global Git config.
        safe_scope = str(Path(repo).resolve())
        return self.runner.run([str(exe), "-c", f"safe.directory={safe_scope}", "-C", repo, *args], timeout=15)

    def collect(self, capability: Capability, *, scope: str, **kwargs: Any) -> SensorObservation:
        if capability != Capability.GIT_STATUS:
            return self._failure(capability, scope, "UNSUPPORTED_CAPABILITY", capability.value)
        exe = self.detect()["executable"]
        if not exe:
            return self._failure(capability, scope, "EXECUTABLE_MISSING", "git not found")
        inside = self._git(exe, scope, "rev-parse", "--is-inside-work-tree")
        if not inside.ok or inside.stdout.strip().lower() != "true":
            return self._failure(capability, scope, "NOT_A_REPOSITORY", inside.stderr.strip() or "Not a Git work tree", metrics=self._metrics(inside))
        status = self._git(exe, scope, "status", "--porcelain=v1", "--untracked-files=normal")
        branch = self._git(exe, scope, "branch", "--show-current")
        head = self._git(exe, scope, "rev-parse", "HEAD")
        remote = self._git(exe, scope, "remote", "get-url", "origin")
        log = self._git(exe, scope, "log", "-5", "--date=iso-strict", "--pretty=format:%H%x09%aI%x09%s")
        if not status.ok or not head.ok:
            failed = status if not status.ok else head
            return self._failure(capability, scope, "COMMAND_FAILED", failed.stderr.strip() or failed.exception or "git failed", metrics=self._metrics(failed))
        porcelain = status.stdout.splitlines()
        modified = sum(1 for line in porcelain if line and not line.startswith("??"))
        untracked = sum(1 for line in porcelain if line.startswith("??"))
        commits: list[dict[str, Any]] = []
        for line in log.stdout.splitlines():
            parts = line.split("\t", 2)
            if len(parts) == 3:
                commits.append({"commit": parts[0], "timestamp": parts[1], "subject": parts[2]})
        card = GitRepoCard(
            repo_path=str(Path(scope).resolve()),
            project=kwargs.get("project"),
            branch=branch.stdout.strip() or None,
            head=head.stdout.strip() or None,
            dirty=bool(porcelain),
            modified_count=modified,
            untracked_count=untracked,
            recent_commits=commits,
            remote=remote.stdout.strip() if remote.ok and remote.stdout.strip() else None,
            last_activity=commits[0]["timestamp"] if commits else None,
        )
        normalized = self.normalize(card, capability, scope=scope)
        metrics = self._metrics(status)
        return self._success(SensorObservation.data(self.sensor_id, capability, scope, normalized, metrics=metrics))

    def normalize(self, raw: Any, capability: Capability, *, scope: str) -> Any:
        if isinstance(raw, GitRepoCard):
            return {name: getattr(raw, name) for name in raw.__dataclass_fields__}
        return raw

