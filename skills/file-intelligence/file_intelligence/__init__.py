"""Portable, read-only File Intelligence engine."""

from pathlib import Path


def package_version() -> str:
    return (Path(__file__).resolve().parents[1] / "VERSION").read_text(encoding="utf-8").strip()


__version__ = package_version()
