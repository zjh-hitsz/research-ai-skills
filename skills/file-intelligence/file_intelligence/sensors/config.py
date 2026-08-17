from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def default_config_path() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "FileIntelligence" / "config" / "sensor_registry.json"
    return Path.home() / ".file-intelligence" / "config" / "sensor_registry.json"


def load_sensor_config(path: Path | None = None) -> dict[str, Any]:
    target = path or default_config_path()
    if not target.is_file():
        return {"version": 1, "executables": {}, "sensors": {}, "capability_preferences": {}}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "version": 1,
            "executables": {},
            "sensors": {},
            "capability_preferences": {},
            "config_error": f"{type(exc).__name__}: {exc}",
        }
    return data if isinstance(data, dict) else {"version": 1, "config_error": "root must be an object"}


def sensor_options(config: dict[str, Any], sensor_id: str) -> dict[str, Any]:
    values = config.get("sensors", {}).get(sensor_id, {})
    return dict(values) if isinstance(values, dict) else {}


def executable_hint(config: dict[str, Any], key: str) -> str | None:
    value = config.get("executables", {}).get(key)
    return str(value) if value else None


