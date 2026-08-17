from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


DEFAULT_SAMPLE_BYTES = 64 * 1024


def staged_sample_fingerprint(
    path: Path,
    size: int | None = None,
    *,
    sample_bytes: int = DEFAULT_SAMPLE_BYTES,
) -> dict[str, Any] | None:
    """Return a deterministic Stage-1 head/middle/tail fingerprint.

    The digest is an identity hint, not proof of equality. Call ``full_sha256``
    before reporting an exact duplicate, copy, move, or rename.
    """
    try:
        actual_size = path.stat().st_size if size is None else int(size)
        digest = hashlib.sha256()
        digest.update(b"fi-stage1-v2\0")
        digest.update(str(actual_size).encode("ascii"))
        positions = sorted(
            {
                0,
                max(0, (actual_size // 2) - (sample_bytes // 2)),
                max(0, actual_size - sample_bytes),
            }
        )
        bytes_read = 0
        with path.open("rb") as handle:
            for position in positions:
                handle.seek(position)
                chunk = handle.read(sample_bytes)
                digest.update(str(position).encode("ascii"))
                digest.update(b"\0")
                digest.update(chunk)
                bytes_read += len(chunk)
        return {
            "stage": 1,
            "algorithm": "sha256-head-middle-tail-v2",
            "value": digest.hexdigest(),
            "bytes_read": bytes_read,
            "size": actual_size,
        }
    except (OSError, PermissionError):
        return None


def full_sha256(path: Path, *, chunk_bytes: int = 4 * 1024 * 1024) -> dict[str, Any] | None:
    """Return a Stage-2 full-file SHA-256 proof without changing the file."""
    try:
        digest = hashlib.sha256()
        bytes_read = 0
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(chunk_bytes)
                if not chunk:
                    break
                digest.update(chunk)
                bytes_read += len(chunk)
        return {
            "stage": 2,
            "algorithm": "sha256-full",
            "value": digest.hexdigest(),
            "bytes_read": bytes_read,
            "size": bytes_read,
        }
    except (OSError, PermissionError):
        return None
