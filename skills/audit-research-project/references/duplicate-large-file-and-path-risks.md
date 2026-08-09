# Duplicate, large-file, and path risks

## Duplicate candidates

Group only files whose SHA-256 values are identical. Call the group a byte-identical duplicate candidate. Do not infer semantic duplication from filenames, sizes, timestamps, or similar content, and do not infer that any member is safe to remove.

Hashing is optional and request-driven. `none` hashes nothing, `all` hashes every readable file, and `below-size` hashes only files at or below the caller-supplied byte limit. An unhashed file is not evidence of uniqueness.

## Large files

Use only `risk.large_file_bytes` from the request. The Skill supplies no research or storage threshold. A large-file risk means the declared criterion was met; it does not mean the file is unnecessary or should be archived.

## Absolute paths

Scan only caller-approved small text extensions and sizes. Report the containing relative file and line numbers, but redact the absolute reference itself. Do not rewrite it automatically.

## Broken references

The v0.1 detector checks relative Markdown links in configured readable text files. A missing target may be obsolete, optional, intentionally not created, generated later, or truly broken. Report the observation and require human interpretation.

## Cache directories

Cache classification uses configured directory names and is heuristic. A cache may contain expensive or irreproducible intermediate evidence. Never translate a cache label directly into a cleanup action.
