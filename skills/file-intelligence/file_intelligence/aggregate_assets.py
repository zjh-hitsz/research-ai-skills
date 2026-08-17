from __future__ import annotations

from pathlib import Path
from typing import Any

from .intelligence_models import AggregateAsset


_KNOWN_ROLES: dict[str, tuple[str, bool | None]] = {
    ".venv": ("PYTHON_ENVIRONMENT", True),
    "venv": ("PYTHON_ENVIRONMENT", True),
    "node_modules": ("PACKAGE_DEPENDENCIES", True),
    "build": ("BUILD_OUTPUT", True),
    "dist": ("BUILD_OUTPUT", True),
    "cache": ("CACHE", True),
    ".cache": ("CACHE", True),
    "temp": ("TEMPORARY_OUTPUT", True),
    "tmp": ("TEMPORARY_OUTPUT", True),
    "render": ("RENDER_OUTPUT", None),
    "renders": ("RENDER_OUTPUT", None),
    "telemetry": ("TELEMETRY", None),
    "recovery": ("RECOVERY_ASSET", False),
}


def aggregate_asset_from_storage(
    path: str,
    storage: dict[str, Any],
    *,
    project_id: str | None = None,
) -> AggregateAsset | None:
    """Create a summary-only aggregate candidate without indexing its contents semantically."""
    value = Path(path)
    match = _KNOWN_ROLES.get(value.name.casefold())
    if match is None:
        return None
    role, rebuildable = match
    confidence = 0.9 if role in {"PYTHON_ENVIRONMENT", "PACKAGE_DEPENDENCIES"} else 0.75
    return AggregateAsset(
        aggregate_id=f"aggregate:{str(value.resolve()).casefold()}",
        path=str(value),
        project_id=project_id,
        role=role,
        logical_size=storage.get("logical_size"),
        allocated_size=storage.get("allocated_size"),
        file_count=int(storage.get("file_count") or 0),
        last_modified=storage.get("last_modified"),
        internal_indexing="SUMMARY_ONLY",
        rebuildable=rebuildable,
        confidence=confidence,
        evidence=[
            {
                "kind": "directory_name_pattern",
                "value": value.name,
                "semantic_contents_inspected": False,
            }
        ],
    )

