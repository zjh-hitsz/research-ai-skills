#!/usr/bin/env python3
"""Evaluate connectivity on an explicit graph without assuming a grid topology."""

from __future__ import annotations

import argparse
import sys
from collections import deque
from pathlib import Path
from typing import Any

from _common import (
    SkillInputError,
    gate_criterion,
    load_contracts,
    load_document,
    require_keys,
    require_list,
    require_string,
    validate_gate,
    write_json,
)


def reachable(start: str, adjacency: dict[str, list[str]]) -> set[str]:
    seen = {start}
    queue: deque[str] = deque([start])
    while queue:
        current = queue.popleft()
        for neighbor in adjacency[current]:
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append(neighbor)
    return seen


def weak_components(nodes: list[str], edges: list[tuple[str, str]]) -> list[list[str]]:
    adjacency = {node: [] for node in nodes}
    for left, right in edges:
        adjacency[left].append(right)
        adjacency[right].append(left)
    for neighbors in adjacency.values():
        neighbors.sort()
    remaining = set(nodes)
    components: list[list[str]] = []
    while remaining:
        start = min(remaining)
        component = sorted(reachable(start, adjacency))
        components.append(component)
        remaining.difference_update(component)
    return components


def evaluate(request: dict[str, Any]) -> dict[str, Any]:
    require_keys(
        request,
        (
            "schema_version",
            "check_id",
            "required",
            "directed",
            "nodes",
            "edges",
            "criterion",
            "evidence_refs",
            "limitations",
        ),
        "connectivity request",
    )
    if request["schema_version"] != "1.0.0":
        raise SkillInputError("connectivity request.schema_version must be 1.0.0")
    require_string(request["check_id"], "connectivity request.check_id")
    if not isinstance(request["required"], bool):
        raise SkillInputError("connectivity request.required must be boolean")
    if not isinstance(request["directed"], bool):
        raise SkillInputError("connectivity request.directed must be boolean")

    raw_nodes = require_list(request["nodes"], "connectivity request.nodes")
    nodes = [require_string(node, f"connectivity request.nodes[{index}]") for index, node in enumerate(raw_nodes)]
    if not nodes:
        raise SkillInputError("connectivity request.nodes must not be empty")
    if len(nodes) != len(set(nodes)):
        raise SkillInputError("connectivity request.nodes must be unique")
    nodes = sorted(nodes)
    node_set = set(nodes)

    raw_edges = require_list(request["edges"], "connectivity request.edges")
    edges: list[tuple[str, str]] = []
    for index, edge in enumerate(raw_edges):
        if not isinstance(edge, list) or len(edge) != 2:
            raise SkillInputError(f"connectivity request.edges[{index}] must be [source, target]")
        left = require_string(edge[0], f"connectivity request.edges[{index}][0]")
        right = require_string(edge[1], f"connectivity request.edges[{index}][1]")
        if left not in node_set or right not in node_set:
            raise SkillInputError(f"connectivity request.edges[{index}] references an undeclared node")
        edges.append((left, right))
    edges.sort()

    criterion = request["criterion"]
    if not isinstance(criterion, dict):
        raise SkillInputError("connectivity request.criterion must be an object")
    require_keys(
        criterion,
        ("mode", "expected_connected", "description", "source_ref"),
        "connectivity request.criterion",
    )
    mode = criterion["mode"]
    if mode not in ("all-nodes-connected", "required-pairs-connected"):
        raise SkillInputError(
            "connectivity criterion.mode must be all-nodes-connected or required-pairs-connected"
        )
    if not isinstance(criterion["expected_connected"], bool):
        raise SkillInputError("connectivity criterion.expected_connected must be boolean")
    description = require_string(criterion["description"], "connectivity request.criterion.description")
    source_ref = require_string(criterion["source_ref"], "connectivity request.criterion.source_ref")

    adjacency = {node: [] for node in nodes}
    for left, right in edges:
        adjacency[left].append(right)
        if not request["directed"]:
            adjacency[right].append(left)
    for neighbors in adjacency.values():
        neighbors.sort()

    disconnected_pairs: list[list[str]] = []
    unreachable_nodes: list[str] = []
    if mode == "all-nodes-connected":
        if request["directed"]:
            connected = all(len(reachable(node, adjacency)) == len(nodes) for node in nodes)
            if not connected:
                unreachable_nodes = sorted(
                    {target for source in nodes for target in node_set - reachable(source, adjacency)}
                )
        else:
            reached = reachable(nodes[0], adjacency)
            connected = len(reached) == len(nodes)
            unreachable_nodes = sorted(node_set - reached)
        required_pairs: list[list[str]] = []
    else:
        raw_pairs = require_list(criterion.get("required_pairs"), "connectivity criterion.required_pairs")
        if not raw_pairs:
            raise SkillInputError("required-pairs-connected mode requires at least one required pair")
        required_pairs = []
        for index, pair in enumerate(raw_pairs):
            if not isinstance(pair, list) or len(pair) != 2:
                raise SkillInputError(f"connectivity criterion.required_pairs[{index}] must be [source, target]")
            source = require_string(pair[0], f"connectivity criterion.required_pairs[{index}][0]")
            target = require_string(pair[1], f"connectivity criterion.required_pairs[{index}][1]")
            if source not in node_set or target not in node_set:
                raise SkillInputError(f"connectivity criterion.required_pairs[{index}] references an undeclared node")
            required_pairs.append([source, target])
            if target not in reachable(source, adjacency):
                disconnected_pairs.append([source, target])
        connected = not disconnected_pairs

    passed = connected == criterion["expected_connected"]
    if passed:
        status = "PASS"
        reasons: list[str] = []
    else:
        status = "FAIL"
        reasons = ["DISCONNECTED_TOPOLOGY" if criterion["expected_connected"] else "UNEXPECTED_CONNECTED_TOPOLOGY"]

    result = {
        "gate_id": request["check_id"],
        "category": "connectivity",
        "required": request["required"],
        "status": status,
        "reason_codes": reasons,
        "measured_value": {
            "connected": connected,
            "directed": request["directed"],
            "node_count": len(nodes),
            "edge_count": len(edges),
            "required_pairs": required_pairs,
            "disconnected_pairs": disconnected_pairs,
            "unreachable_nodes": unreachable_nodes,
            "weak_components": weak_components(nodes, edges),
        },
        "criterion": gate_criterion(description, "boolean", criterion["expected_connected"], source_ref),
        "units": None,
        "evidence_refs": require_list(request["evidence_refs"], "connectivity request.evidence_refs"),
        "limitations": require_list(request["limitations"], "connectivity request.limitations"),
    }
    common_schema, gate_schema = load_contracts()
    validate_gate(result, common_schema, gate_schema, "connectivity gate")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", help="JSON or JSON-compatible YAML graph request")
    parser.add_argument("--output", default="connectivity_gate.json", help="Output gate JSON path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        write_json(args.output, evaluate(load_document(args.request)))
    except SkillInputError as exc:
        print(f"INPUT_ERROR: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"IO_ERROR: {exc}", file=sys.stderr)
        return 3
    print(Path(args.output).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
