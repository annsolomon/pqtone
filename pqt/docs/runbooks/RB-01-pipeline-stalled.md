# RB-01 Pipeline stalled (rules-engine heartbeat stale)

**Signal.** `RulesEngineHeartbeatStale` / `RulesEngineDown` alert, console shows "The rules engine has not reported in".

**Impact.** New incidents are not raised. Events keep being accepted and stored; nothing is lost.

1. `make ps` — is `rules-engine` running and healthy?
2. `docker compose ... logs rules-engine --tail=200` — look for `streams state ... -> ERROR`, SASL/ACL errors (`TopicAuthorizationException`, `TransactionalIdAuthorizationException`) or OOM.
3. ACL errors: re-run `redpanda-init` (`docker compose ... up redpanda-init`); it is idempotent.
4. State corruption after a crash: stop rules-engine, delete the `rules-state` volume, start it. State is rebuilt from changelog topics.
5. Verify recovery: `/api/pipeline/health` returns `ok`; incidents resume. Event-time rules catch up on the backlog with identical results.
