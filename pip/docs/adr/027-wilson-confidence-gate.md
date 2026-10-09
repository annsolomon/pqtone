# ADR-027: Wilson intervals in the scorer, as an extra gate that never relaxes the point gate

- **Status:** accepted
- **Date:** 2026-10-09
- **Milestone:** Q4 (confidence intervals)

## Context

The scorer gates precision and recall per (scenario, rule) on point estimates: R-ABS-001
"precision 1.000" in `register_delay` rests on 5 incidents, in `baseline` on 1. A pilot customer
reading "100% precise" from 2 incidents would be misled. Milestone Q4 asks for Wilson intervals,
with gates that "use the lower bound when n >= 20, otherwise warn".

Taken literally that rule would do two things the repository forbids:

1. **Weaken existing gates.** Most rows have n < 20 today. "Otherwise warn" would turn their
   failing point estimates into warnings, and `CLAUDE.md` says a gate is never weakened.
2. **Fail perfect results.** Comparing the lower bound with the existing point thresholds
   (0.95) fails a perfect 20/20, whose 95% lower bound is 0.839, and even 49/49 (0.927). The
   thresholds would effectively change without anyone deciding a new number.

## Decision

1. Every score carries `nPrecision`, `nRecall` and 95% Wilson intervals (`precisionCI`,
   `recallCI`), shown in `score.md` next to each point estimate.
2. The point gate (`precision >= threshold`, `recall >= threshold`) is unchanged for every n.
3. A second gate is added in `thresholds.yaml` under `confidence:`. When n >= `minN` (20), the
   Wilson lower bound must reach `minLowerBound` (0.80) or the row fails. 0.80 is the bar a
   perfect run passes at n = 20 (0.839), so the new gate only fails a row whose evidence is both
   large and imperfect enough to leave real doubt.
4. When 0 < n < `minN` and the lower bound is under the point threshold, the row gets a
   **warning** ("too few samples to certify >= 0.95"). Warnings are listed in the report and in
   `score.json`, and never fail the build. Rows with n = 0 neither fail nor warn.
5. z = 1.96 (95% two-sided), configurable in the same block.

## Consequences

- No gate is weaker than before; one is stricter for large samples.
- Today's matrix passes unchanged: the only row with n >= 20 is R-DWELL-001 in
  `restricted_dwell` (49/49, lower bound 0.927).
- The report now says how much each number can be trusted, which is what Tier 2 and the pilot
  need before quoting precision to a customer.
- Raising `minLowerBound` or lowering `minN` changes a threshold and needs a new ADR.
