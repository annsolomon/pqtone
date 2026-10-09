# Security policy

Report vulnerabilities privately through GitHub Security Advisories ("Report a vulnerability" on the
Security tab). Do not open public issues for security problems. We acknowledge within 3 working days.

Scope: everything in this repository. The local stack's generated secrets (`.env`) are for local use only.

Design commitments that a report may test against:

- Synthetic data only in Tier 1. No images, video, biometrics or person identification, ever.
- Every incident outcome is decided by a person; rules never act on their own.
- Producers can only publish for their own `source` prefix; events are immutable once accepted.
- Review decisions and rejected duplicates are recorded in a hash-chained, append-only audit log.

See docs/ARCHITECTURE.md §11 (threat model) and §12 (privacy).
