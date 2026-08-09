# Project audit profiles

A project profile is optional, request-local classification guidance. It never changes the read-only inventory boundary and never establishes scientific authority.

## Fields

- `include_rules`: mark matching files as eligible primary-asset candidates.
- `exclude_rules`: lower matching paths to non-primary `unknown`; the files remain inventoried and risk-scanned.
- `priority_paths`: raise attention for matching files without declaring them authoritative.
- `environment_folders`: classify dependency/environment contents as non-primary cache noise rather than project code.
- `cache_folders`: classify matching contents as cache so cache risks remain visible.
- `source_hints`: classify matching files as source before generic code-extension rules.

Rules are case-insensitive relative globs. They must not be absolute or contain parent segments. Folder fields accept one folder name per item.

## Precedence

1. Environment and cache folders remain non-primary cache.
2. Exclude rules make a path non-primary but do not remove it from inventory.
3. Source hints and configured source directories take precedence over code extensions.
4. Configured result directories take precedence over config/code extensions.
5. Generic extension rules apply last.

Priority and include matches affect candidate weight only. Declared entrypoints remain candidate-only, and every authority decision remains human.
