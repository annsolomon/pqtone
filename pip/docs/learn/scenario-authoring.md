# Writing a simulator scenario

Milestone S3. A scenario is a YAML file in `sim/scenarios/` that describes a stretch of a store's
day: who comes in, how they shop, how the staff react and what goes wrong in transport. The
simulator turns it into events plus ground truth (`docs/learn/store-sim.md`), and the scorer
grades every rule against that ground truth on three fixed seeds.

## The keys

Unknown keys are rejected (`store_sim.config.check_scenario`), so a typo fails loudly instead of
being ignored. Durations are ISO-8601 `PTnHnMnS`.

| Key | Required | Meaning | Default |
|---|---|---|---|
| `name` | yes | Same as the file name; appears in reports | |
| `description` | no | What the scenario is for and which rules it exercises. Keep it true: tests check it | |
| `duration` | yes | How long new shoppers keep arriving. Shoppers inside finish their visit afterwards | |
| `epoch` | no | Event time of t = 0 | `2026-01-01T09:00:00.000Z` |
| `stores` | no | Store ids to simulate in one run, each from `config/layouts/<id>.json` (milestone S2). Their events interleave in time order; arrivals, shoppers and staffing apply to each store; the first store keeps the run's seed and behaves exactly as it would alone | the `--layout` store |
| `arrivals` | yes | List of `{from, to, perHour}` segments; Poisson arrivals at that rate. Gaps mean nobody arrives. At least one rate > 0 | |
| `shoppers.checkoutProbability` | no | Share of shoppers who buy and queue | 0.85 |
| `shoppers.fittingRoomProbability` | no | Chance an apparel visit adds the fitting rooms | 0.35 |
| `shoppers.fittingRoomMedianSeconds`, `fittingRoomSigma` | no | Log-normal fitting-room stay | 240, 0.45 |
| `shoppers.serviceMedianSeconds` | no | Log-normal time at the register (σ 0.4) | 75 |
| `staffing.initialOpen` | no | Registers open at t = 0, per queue | 2 |
| `staffing.minOpen` | no | Never close below this many, per queue | 1 |
| `staffing.maxOpen` | no | Never staff more than this many per queue (a short-staffed day) | all registers of the queue |
| `staffing.openAtQueueLength` | no | Queue length that makes staff open another register | 6 |
| `staffing.reactionMin`, `reactionMax` | no | Uniform delay before that register opens | `PT60S`, `PT200S` |
| `staffing.closeAfterIdle` | no | An empty queue for this long closes a register | `PT300S` |
| `faults.outOfOrder` | no | `{rate, maxDelay}`: delayed emission within grace | none |
| `faults.late` | no | `{rate, minDelay, maxDelay}`: emission beyond grace (dropped by the rules) | none |
| `faults.duplicates` | no | `{rate}`: exact copies (deduplicated by event-core) | none |
| `faults.malformed` | no | `{rate}`: invalid events (rejected to the DLQ) | none |
| `faults.vision` | no | `{profile: path}`: perception noise profile, relative to the scenario file. No-op in Tier 1 | none |

Store-specific numbers (zones, zone weights, dwell medians, registers, queue) come from the layout
in `config/layouts/`, not from the scenario.

## How to write one

1. **Start from the question.** "Does the absence rule fire when nobody can open a register?"
   is a scenario. "A busy day" is not.
2. **Write the description first**, naming the rules it should exercise.
3. **Pick numbers you can reason about.** A register serves about 45 people an hour (median 75 s
   per customer), so with 85% of shoppers buying, one open register keeps up with about
   50 arrivals an hour. Above that the queue grows; below it, it drains.
4. **Check the ground truth on the three matrix seeds** before adding the file to the matrix:

   ```bash
   cd /workspaces/pqtone/pip
   .venv/bin/python - <<'PY'
   from collections import Counter
   from store_sim.generate import simulate
   for seed in (11, 42, 1337):
       out = simulate(seed=seed, layout_path="config/layouts/store-001.json",
                      scenario_path="sim/scenarios/MY_SCENARIO.yaml", rules_path="config/rules.yaml")
       print(seed, Counter(g["ruleId"] for g in out.ground_truth))
   PY
   ```
   If a rule you meant to exercise is missing on any seed, change the numbers, not the rule.
5. **Add a test** in `sim/tests/test_sim.py` that pins the behaviour the description promises
   (see `test_new_scenarios_produce_the_ground_truth_they_describe`).
6. **Add it to `scorer/matrix.yaml`.** CI then runs it through the Java rules engine and the
   scorer on every PR. The first green run gives the numbers for `scorer/baseline.json`
   (`docs/runbooks/RB-09-baseline-update.md`).
7. **Never tune a scenario to make a failing rule pass.** If the engine disagrees with ground
   truth, that is a finding.

## The scenarios

| Scenario | What it tests |
|---|---|
| `baseline` | An ordinary two hours; the reference for everything else |
| `rush_hour` | A steady peak; queue alerts that clear once staff respond |
| `register_delay` | Slow staff response; the absence rule escalates |
| `restricted_dwell` | Long fitting-room stays; the dwell rule |
| `footfall_spike` | A coach party; the windowed footfall rule |
| `staff_shortage` | One cashier, waves of customers; queue and absence on each wave |
| `flash_sale` | A 15-minute sale; footfall spike plus queue build-up |
| `closing_time` | Traffic tapers to zero; no false alerts while the store empties, everything resolves |
| `two_queues` | Store 002: two checkout lines with their own registers; each line breaches on its own (S2) |
| `multi_store` | Store 001 and store 002 in one run; per-store state, per-store ground truth (S2) |
| `duplicates` | 5% exact duplicates; dedup must make it look like `baseline` |
| `out_of_order` | Out-of-order delivery within grace; results unchanged |
| `late_beyond_grace` | Events later than grace; measures the cost of dropping them |
| `malformed` | Invalid events; rejected, nothing else affected |

## Layouts with several queues (milestone S2)

A layout lists `queues` (`{id, zoneId}`) and `registers`. Each register may name the queue it serves with
`queueId`; without it, it serves the first queue, so a one-queue layout needs nothing new. Shoppers join
the shortest line that has an open register (ties go to the first in the layout); this takes no random
draw, so adding the feature left every existing one-queue run byte-identical. Staffing parameters apply
to each queue on its own. `config/layouts/store-002.json` is the example: two lines, two registers each.
