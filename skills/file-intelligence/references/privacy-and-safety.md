# Privacy and safety policy

Before publishing or sharing this Skill, require all of the following:

- no absolute personal path;
- no credential, email address, account identifier, or machine identifier;
- no real project artifact, private filename, or private project label;
- no catalog, database, baseline, project graph, dependency edge, authority result, user assertion, state backup, fingerprint cache, dashboard, real benchmark, run output, transaction journal, approval request, rollback manifest, or machine hash manifest;
- no communication attachment record or source path;
- only generic rules and disposable synthetic tests.

Run `scripts/privacy_audit.py` over the exact release root. Supply release-specific private markers with repeatable `--denylist` arguments when they are known. Review the final Git diff separately because an automated scan cannot prove that every project name is public.

The engine is read-only for scanned roots. It follows neither directory symlinks nor junction-like links, aggregates common environment/cache/build trees, and has no physical organization executor. A result can support human review, but cannot authorize a move, deletion, rename, archive, copy, upload, or reference rewrite. Real-machine development reports must remain in external local state even when the repository contains a generic report template or documentation.
