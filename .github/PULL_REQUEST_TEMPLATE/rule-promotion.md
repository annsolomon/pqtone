<!-- Milestone R6. Use for promoting a shadow rule to enforce: open the PR with ?template=rule-promotion.md -->
## Promote a shadow rule to enforce

**Rule:** `R-XXX-000` (from `shadow` to `enforce`)
**Version:** `1.0.0` → `1.1.0` <!-- CI refuses a changed rule without a higher version (R4) -->

### Evidence (copy from the console: Shadow rules → Live scores)

| Runs | Hits / false / missed | Precision (lower bound) | Recall (lower bound) | Verdict |
|---:|---|---|---|---|
|  |  |  |  | Ready to propose for enforce |

- [ ] The scorecard says **Ready to propose for enforce** for this rule version (enough samples, point and lower-bound gates met).
- [ ] The offline matrix in this PR's score comment shows no drop for this rule in any scenario.
- [ ] Alert routing is ready: the people who will receive these alerts know what to do (runbook linked below).

### Change

- [ ] `config/rules.yaml`: `mode: enforce` and the version raised; nothing else changed in the rule.
- [ ] Thresholds unchanged. If they change, link the ADR: <!-- docs/adr/NNN-... -->
- [ ] `scorer/baseline.json` unchanged (a promotion changes who is alerted, not what is detected).

### Rollout and rollback

- Deploy, or publish without a restart: `make rules-publish RULES=config/rules.yaml` (RB-10).
- Rollback: revert this PR and publish again; incidents raised meanwhile keep the version that raised them.

Runbook: `pip/docs/runbooks/RB-11-promote-shadow-rule.md`. Promotion is never a console toggle.
