from __future__ import annotations

import argparse
import json
from pathlib import Path

from .intelligence_models import Capability
from .sensors.config import default_config_path, load_sensor_config
from .sensors.registry import build_default_registry


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Read-only Computer Intelligence sensor CLI")
    value.add_argument("--config", type=Path, default=default_config_path())
    value.add_argument("--cache", type=Path)
    value.add_argument("--raw-dir", type=Path)
    sub = value.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    collect = sub.add_parser("collect")
    collect.add_argument("capability", choices=[item.value for item in Capability])
    collect.add_argument("--scope", required=True)
    collect.add_argument("--search", default="")
    collect.add_argument("--limit", type=int, default=100)
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    config = load_sensor_config(args.config)
    registry = build_default_registry(config, cache_path=args.cache, raw_dir=args.raw_dir)
    if args.command == "status":
        payload = {"config": str(args.config), "sensors": registry.health_snapshot()}
    else:
        observation = registry.request(
            Capability(args.capability),
            scope=args.scope,
            search=args.search,
            limit=args.limit,
        )
        payload = observation.to_dict()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


