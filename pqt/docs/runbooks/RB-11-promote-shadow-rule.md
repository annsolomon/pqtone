# RB-11: Promote a shadow rule to enforce

**When:** a rule has been running in `shadow` mode (it raises incidents nobody is alerted about) and you
want it to start alerting staff. Milestone R6.

**Principle:** humans decide, with evidence, through review. The console shows the evidence; it never
changes a rule. A promotion is a pull request.

## 1. Check the evidence

Console, as an admin: **Shadow rules → Live scores**. Each row is a rule's current version, accumulated
over its end-to-end runs in the last 30 days (`pqt-scorer e2e --record` writes one row per run and rule
into `pqt.rule_score`).

A shadow rule shows **Ready to propose for enforce** only when all of these hold (the same gate the
scorer applies in CI, `scorer/thresholds.yaml`):

- precision and recall at or above the rule's thresholds;
- at least `confidence.minN` samples (20) for both;
- the 95% Wilson lower bounds at or above `confidence.minLowerBound` (0.80).

"Not yet" lists the reasons. A few perfect runs are not enough: keep the rule in shadow and let more
end-to-end runs (CI on every PR and on `main`) accumulate.

The same numbers are available to tooling at `GET /api/admin/rule-scores` (admin only).

## 2. Open the pull request

1. Branch: `feat/promote-<rule>`.
2. In `config/rules.yaml`: set `mode: enforce` and raise the rule's `version` (minor bump). CI's
   rule-version check refuses a changed rule with the same version (R4, ADR-030).
3. Open the PR with the template: append `?template=rule-promotion.md` to the compare URL, or pick
   **rule-promotion** in the template list. Paste the scorecard row.
4. Thresholds stay as they are. Changing one needs an ADR (CLAUDE.md).

## 3. Review and merge

The reviewer checks the scorecard row against the console, the offline score comment (no drop for this
rule) and that the people receiving the alerts have a runbook. CI must be green.

## 4. Roll out

Either deploy, or publish the reviewed file to running engines without a restart (RB-10):

```bash
make rules-publish RULES=config/rules.yaml
```

The heartbeat and `pqt.rules.config.reloads{outcome="applied"}` confirm it. New incidents carry the new
version and appear in the review queue.

## Rollback

Revert the PR and publish again (or `make rules-reset` to return to the deployed file). Incidents raised
in between keep the version that raised them.
