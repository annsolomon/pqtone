# RB-09: Update the quality baseline

`scorer/baseline.json` holds precision and recall per (scenario, rule). The scorer fails any run
whose numbers drop more than `regression.maxDropPp` (2 points) below it, so the baseline is the
floor every change to rules, the simulator or the scorer is held to. **It is never updated
automatically.**

## When

- A PR adds a rule or a scenario: the new rows have no baseline yet, so nothing protects them
  from regressing. Add them in a separate, explained commit in that PR (R2 did this), or with the
  flow below after merging.
- A deliberate change improves scores and you want the gate to hold the new level.
- A deliberate change lowers a score. This needs a reason in the PR, and if a threshold moves,
  an ADR.

## How

Every push to `main` uploads `baseline.candidate.json` from the offline score as the
`baseline-candidate` artifact of that CI run. In the Codespace:

```bash
cd /workspaces/pqtone/pqt
make baseline-accept              # latest green run on main
make baseline-accept RUN=<id>     # or a specific run
```

`scripts/baseline-accept.sh`:

1. Refuses to run with uncommitted changes.
2. Downloads the candidate with `gh run download`.
3. Compares it with `main`'s baseline (`python -m pqt_scorer.baseline_diff`). Identical → stops.
4. Otherwise creates `chore/baseline-<run>` from `origin/main`, commits the candidate, pushes,
   and opens a PR whose body is the change table: drops and removed rows first, flagged.

Then a human reads the table and merges the PR, or closes it. CI on that PR runs the scorer
against the new baseline, so a candidate that does not reproduce fails there.

## Reading the table

| Kind | Meaning | What to do |
|---|---|---|
| drop | precision or recall fell | Merge only with a written reason. Threshold changes need an ADR. |
| removed | a row disappeared (rule or scenario gone) | Expected only if the PR that removed it said so. |
| new | a row without a baseline | Usually a new rule or scenario; accept. |
| rise | a score improved | Accept to lock it in. |

## If something goes wrong

- `gh: not logged in`: `gh auth login -h github.com -p https -w`.
- `no baseline-candidate artifact`: the run was not a push to `main`, or the artifact expired
  (90 days). Use a newer run.
- `working tree not clean`: commit or stash, then retry.
