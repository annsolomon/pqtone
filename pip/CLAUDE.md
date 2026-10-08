# CLAUDE.md — rules for this repository

Physical-intelligence platform monorepo. This folder is the project root: run every command from here.
Plans: `docs/plans/` (start at `00-ROADMAP.md`; Phase 1 runbook: `PHASE-1-README.md`; full blueprint: `MASTER-README.md`).
Architecture: `docs/ARCHITECTURE.md`.
First time in a Codespace: `make dev-setup`. Console mode: `scripts/mode.sh codespaces|localhost`.

## Gates (run them; paste the output before saying "done")
- Fast, no Docker: `make test-fast` (Java `mvn verify`, pytest, vitest, tsc).
- Rule quality: `make offline` (sim matrix → rules engine → scorer).
- Full: `make all` (builds images, starts stack, e2e). Run it before finishing any milestone that touches services, deploy or contracts.
- Never weaken, skip or delete a test to make a gate pass. If a test looks wrong, stop and explain.
- Long runs: pipe through `tee`, never `tail`.

## Contract rules
- `schemas/` is the contract. Changes are additive only. Every change gets a new version and a `catalog.json` entry, and must pass `scripts/schema-compat.py` once it exists (milestone E2).
- Every rule decision is made in event time (`time` attribute), never wall time.
- Dedup key is `(source, id)`. Producers must make ids stable across retries.
- Any change to rules or detectors must keep the scorer gate green. Thresholds change only with an ADR. The baseline is never updated automatically.

## Privacy rules (absolute)
- Never add face recognition, demographic inference or cross-day / cross-site re-identification.
- ReID embeddings are in-memory, TTL-bound and never persisted or sent upstream.
- Raw video stays on the edge. Clips are exported only for a reviewed incident, blurred.
- No real personal data in tests, fixtures or logs.

## Engineering rules
- Ask before adding a dependency, and state its licence. AGPL, SSPL, BSL or non-commercial licences need an ADR.
- Least privilege everywhere (DB roles, Kafka ACLs, container caps). No secrets in code. `.env` is generated and never read into output.
- SQL: parameterised only. Migrations are Flyway, forward-only, expand → migrate → contract.
- Java 21 / Spring Boot 3; Python 3.12 with type hints; React + strict TypeScript.
- Conventional commits. One milestone per branch. Don't push; the user pushes.

## Workflow
- Work one milestone at a time, as written in the plan file (the `/milestone` skill has the steps). Start in plan mode: list the files you'll touch, the tests you'll write first, and how you'll prove "Done when".
- If execution diverges from the approved plan, stop and return to plan mode.
- After implementing, run the `security-reviewer` and `contract-guardian` agents on the diff.
- Milestones marked 🧠 are learning exercises. Explain the concept and review the user's code instead of writing the core logic, unless the user says otherwise.
- At the end: tick the milestone with `scripts/tick.py`, add an ADR in `docs/adr/` if a decision was made, summarise what changed and how it was verified.
