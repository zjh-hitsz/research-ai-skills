from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..intelligence_models import Capability, SensorHealth, SensorObservation
from .base import BaseSensor
from .locator import first_executable, toolbox_hint


class GitHubCLISensor(BaseSensor):
    sensor_id = "github_cli"
    display_name = "GitHub CLI Sensor"
    MIN_SAFE_AUTH_STATUS = (2, 97, 0)

    def detect(self) -> dict[str, Any]:
        configured = self.config.get("executables", {}).get("gh")
        toolbox = toolbox_hint(self.config, "github-cli")
        exe = first_executable([configured, toolbox], ["gh.exe", "gh"])
        return {"executable": exe}

    def capabilities(self) -> set[Capability]:
        return {Capability.GITHUB_STATUS}

    @staticmethod
    def parse_version(text: str) -> tuple[int, int, int] | None:
        match = re.search(r"gh version\s+(\d+)\.(\d+)\.(\d+)", text)
        return tuple(int(value) for value in match.groups()) if match else None

    def version(self) -> str | None:
        exe = self.detect()["executable"]
        if not exe:
            return None
        result = self.runner.run([str(exe), "--version"], timeout=8)
        return result.stdout.splitlines()[0].strip() if result.ok and result.stdout.strip() else None

    def source(self) -> dict[str, str]:
        return {"vendor": "GitHub", "official_url": "https://github.com/cli/cli/releases"}

    def health_check(self):
        exe = self.detect()["executable"]
        if not exe:
            return self._status(version=None, available=False, health=SensorHealth.UNAVAILABLE, executable_path=None, failure_reason="gh not found")
        version = self.version()
        parsed = self.parse_version(version or "")
        if parsed is None or parsed < self.MIN_SAFE_AUTH_STATUS:
            return self._status(
                version=version,
                available=True,
                health=SensorHealth.DEGRADED,
                executable_path=str(exe),
                failure_reason="gh < 2.97.0 auth-status output is not considered safe to capture",
                details={"auth_checked": False},
            )
        auth = self.runner.run([str(exe), "auth", "status"], timeout=15)
        # Never retain stdout/stderr because authentication status output has had token-redaction defects.
        health = SensorHealth.HEALTHY if auth.return_code == 0 else SensorHealth.AUTH_UNAVAILABLE
        return self._status(
            version=version,
            available=True,
            health=health,
            executable_path=str(exe),
            failure_reason=None if auth.return_code == 0 else "GitHub authentication unavailable",
            details={"auth_checked": True, "authenticated": auth.return_code == 0, "metrics": self._metrics(auth)},
        )

    def collect(self, capability: Capability, *, scope: str, **kwargs: Any) -> SensorObservation:
        if capability != Capability.GITHUB_STATUS:
            return self._failure(capability, scope, "UNSUPPORTED_CAPABILITY", capability.value)
        status = self.health_check()
        if status.health == SensorHealth.AUTH_UNAVAILABLE:
            return self._failure(capability, scope, "AUTH_UNAVAILABLE", "GitHub CLI is not authenticated")
        if status.health != SensorHealth.HEALTHY:
            return self._failure(capability, scope, "SENSOR_UNHEALTHY", status.failure_reason or status.health.value)
        exe = Path(status.executable_path or "")
        repo = kwargs.get("repo") or scope
        commands = {
            "pull_requests": [str(exe), "pr", "list", "--repo", repo, "--limit", "20", "--json", "number,title,state,headRefName,baseRefName,updatedAt"],
            "issues": [str(exe), "issue", "list", "--repo", repo, "--limit", "20", "--json", "number,title,state,updatedAt"],
            "runs": [str(exe), "run", "list", "--repo", repo, "--limit", "20", "--json", "databaseId,name,status,conclusion,updatedAt"],
        }
        data: dict[str, Any] = {}
        metrics: dict[str, Any] = {}
        for key, argv in commands.items():
            result = self.runner.run(argv, timeout=30)
            metrics[key] = self._metrics(result)
            if not result.ok:
                return self._failure(capability, scope, "COMMAND_FAILED", f"gh {key} query failed", metrics=metrics)
            try:
                data[key] = json.loads(result.stdout)
            except json.JSONDecodeError as exc:
                return self._failure(capability, scope, "INVALID_OUTPUT", str(exc), metrics=metrics)
        return self._success(SensorObservation.data(self.sensor_id, capability, scope, self.normalize(data, capability, scope=scope), metrics=metrics))

    def normalize(self, raw: Any, capability: Capability, *, scope: str) -> Any:
        if not isinstance(raw, dict):
            raise ValueError("GitHub CLI output must be an object")
        return raw


