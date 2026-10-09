# ADR-030: Rule versioning and hot reload through a compacted topic

- **Status:** accepted
- **Date:** 2026-10-09
- **Milestone:** R4

## Context

Changing a threshold meant editing `config/rules.yaml`, rebuilding or remounting, and restarting
the rules engine. A restart replays state from the changelogs and briefly stops alerting; during
a pilot that is a visible outage for a tuning change. Incidents already stamp `ruleVersion`, but
nothing forced the version to change when a rule changed, so the history could not tell two
behaviours apart.

## Decision

1. **Versions are enforced.** A rule whose definition (mode, severity, params) changes must carry a
   higher MAJOR.MINOR.PATCH version. CI checks every PR against `main`
   (`scripts/rules-version-check.py`); the engine checks every published document
   (`VersionCheck.java`) and refuses one that fails.
2. **Distribution.** `rules.config.v1`, one partition, `cleanup.policy=compact`, key `rules`, value
   = the whole rules document as YAML text. Each engine reads it through a Kafka Streams **global
   store**, so every instance and every stream thread sees every record. A tombstone means "go back
   to the deployment file".
3. **Swap.** `RulesHolder` keeps an immutable `Active` (rule set, engine, footfall detector,
   source). The global-store thread swaps it atomically; stream threads read it once per record,
   so a record is never evaluated against half of two configurations. Open episodes in state stay
   open and continue under the new parameters; their incident id keeps the major version they
   opened with.
4. **Restart after a reload.** Kafka Streams restores a global store from its topic without calling
   the processor, so `RulesConfigProcessor.init` applies the restored document: a restarted engine
   comes back with the last published rules, not the file.
5. **Structural settings are not hot-reloadable**: the global grace (it defines every state's
   watermark) and R-FOOT-001's presence, window, history and zones (the window topology and the
   stored history depend on them). The engine refuses such a document; change the file and restart.
6. **Who may publish.** `RulesPublisher` (the `rules-admin` compose service, `make rules-publish`)
   runs as the Redpanda **admin** principal: changing rules is an operator action. The rules-engine
   principal can only read the topic. The publisher validates, checks versions against what is
   running (the topic's document, or the file), and refuses anything the engine would refuse.

## Consequences

- A threshold change takes effect within seconds, without a restart or a gap in alerting
  (`make e2e-hot-reload` proves it on the live stack, checking the container's start time).
- The heartbeat reports the active rules (`sha256`, `source`, versions per rule), and
  `pqt.rules.config.reloads{outcome}` counts applied, refused and reset documents.
- **Determinism trade-off.** When a reload lands is wall-clock time, so replaying the same events
  after the fact does not reproduce exactly which events saw which version. Every incident says
  which version produced it, which is what review and scoring need. The offline gate is unaffected:
  it always runs one file.
- The repository file stays the reviewed source of truth: a document published for tuning should
  come back to `config/rules.yaml` through a PR (with its bumped version), then `make rules-reset`.
