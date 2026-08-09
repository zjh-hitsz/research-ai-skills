# Read-only and authority policy

## Non-mutation contract

The audited project root is an evidence source, not an output directory. Place inventory, risk, and rendered reports outside that root. Compare a project-tree content snapshot before and after every audit. If any project entry is created, removed, renamed, or changed, invalidate the run.

Never follow a symlink, junction, or other reparse boundary in v0.1. Record the relative boundary and whether its target appears internal, external, broken, or unresolved. Do not emit the target's absolute path.

Do not execute project code, notebooks, solvers, macros, binaries, generated commands, or cleanup utilities.

## Facts, heuristics, and decisions

Keep three evidence levels separate:

1. Facts: observed relative path, entry kind, size, timestamp, hash state, scan status, and caller declarations.
2. Heuristics: source/config/code/report/result/cache/unknown labels and risk candidates derived from names, extensions, hashes, or configured thresholds.
3. Human decisions: scientific authority, final version, semantic duplication, retention, archive destination, and whether a missing output is expected.

An authority candidate means only that a declared entrypoint was observed and is not classified as cache. It is not an authoritative model, paper, dataset, or result.

## Incomplete scans

Return `PARTIAL` when permissions, broken links, symlink/junction boundaries, hash-read failures, or content-scan failures leave evidence unobserved. A partial audit may still contain useful facts, but it cannot support a completeness claim.
