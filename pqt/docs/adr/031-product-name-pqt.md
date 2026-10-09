# ADR-031: Product name `pqt`, applied in one mechanical rename

- **Status:** accepted
- **Date:** 2026-10-09
- **Milestone:** 0.7

## Context

The placeholder name `pip` collided with Python's package manager and was spread through Java packages
(`com.pip`), CloudEvents types and sources (`com.pip.store.*`, `urn:pip:`), schema ids
(`schemas.pip.local`), the database schema and roles, environment variables (`PIP_*`), the Keycloak realm,
image names, the scorer package and the project folder. Renaming gets more expensive with every tier.

## Decision

1. The product name is **PQT** (`pqt` in identifiers, `PQT_` for environment variables).
   It is 3 letters, one fewer than the runbook's 4–10 guideline; it is a valid Java and Python package
   name and was chosen by the owner.
2. The rename is one mechanical change made by `scripts/rename-product.py`, which is kept in the
   repository so the change can be audited and repeated. It leaves Python's package manager alone
   (`pip install`, `python -m pip`, `pip3`, `.venv/bin/pip`, pip's own `PIP_*` variables), words that only
   contain the letters (pipeline, pipefail), lines that tell the history of the name, and the audit
   advisory-lock constant.
3. **The event contract changes name, not shape.** Types become `com.pqt.store.*`, sources `urn:pqt:…`,
   schema ids `https://schemas.pqt.local/…`. No field changes. This is acceptable only because nothing
   outside this repository produces or consumes these events yet (no edge device, no customer). From
   Tier 2 on, a rename like this would need a new version and a dual-accept period instead.
4. **Migrations V1–V6 are edited in place** (schema `pqt`, roles `pqt_app`, `pqt_migrator`, `pqt_read`).
   This breaks the forward-only rule on purpose: no database outside a developer's machine or CI exists,
   and the alternative (an `ALTER SCHEMA … RENAME` migration plus role renames outside Flyway) would leave
   the history describing a schema that never existed in any deployment. Every local stack must be reset
   once: `make clean && scripts/mode.sh codespaces && make all`. After `v0.1.0`, migrations are strictly
   forward-only again.

## Consequences

- Local `.env` files and data volumes from before the rename do not work; `make clean` is required once.
- Plan documents keep the old name where they describe the rename itself or the name's history.
- `scripts/rename-product.py` stays as an audit trail; it is not run again.
