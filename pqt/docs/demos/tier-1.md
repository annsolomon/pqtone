# Tier 1 demo: Virtual Supermarket (5 minutes)

What it shows: a simulated store produces camera-style events; the pipeline validates and stores them,
detects queue, dwell and absence incidents in event time, alerts a reviewer who decides with evidence,
and every number is measured against ground truth. No person is identified at any point.

Run every command from the project folder (`/workspaces/pqtone/pqt`).

## Before the audience arrives (once)

```bash
docker info >/dev/null 2>&1 || sudo /usr/local/share/docker-init.sh
scripts/mode.sh codespaces      # or: scripts/mode.sh localhost on a laptop
make up && make summary         # every service healthy; prints the console URL and the four logins
```

Open the console URL in two browser windows: one signed in as **reviewer**, one as **admin**
(a private window keeps the sessions apart).

## 1. The floor (30 s)

Reviewer window, **Floor**. The floor plan shows live occupancy per zone and the checkout queue as dots.
Point out the **store picker**: store 002 has two checkout lines, each with its own registers (S2).
Switch back to **Demo store 001**. Mention **Show the floor as a table**: the same data for keyboard and
screen-reader users (C3).

## 2. A queue builds up (90 s)

```bash
SCENARIO=register_delay make sim
```

At 20x real time, shoppers arrive faster than one cashier serves them and staff react slowly. Watch the
queue count pass **6**: a minute later the checkout outline turns amber and an **alert toast** appears
(C1). The incident is also in the **Open incidents** rail. Turn on sound in the top bar if you like.

## 3. Review it with evidence (60 s)

Click **Open incident** on the toast. The **Timeline** shows why it fired (C4): queue length against the
threshold, when the breach started, when the alert was raised, and when a register opened. The same data
is available as a table. Click **Confirm incident**. The decision is recorded under your name.

Admin window, **Audit log** → **Check the chain**: every review decision is hash-chained, so editing
history breaks the chain.

## 4. How people review (30 s)

Admin window, **Review metrics** (C5): per rule, time to first action (median and 90th percentile) and the
confirm rate with a 95% range. Its lower end is the cautious precision to quote to a customer.

## 5. A rule that doesn't alert yet (45 s)

Admin window, **Shadow rules**. "No register opened" (R-ABS-001) runs in shadow: its incidents appear
here, nobody is alerted. **Live scores** (R6) show its precision and recall on end-to-end runs and
whether it has earned a promotion proposal. There is no switch: promotion is a reviewed pull request
(RB-11).

## 6. Quality is measured, not claimed (30 s)

Open the latest CI run of `main` on GitHub (Actions → ci). The job summary and the PR comment show the
quality report: precision, recall and latency per rule and scenario for the offline matrix (14 scenarios
x 3 seeds), the live end-to-end run, the processing-latency gate, and the accessibility report. The
Playwright video of this exact demo is in the run's artifacts (`ui-demo`).

## 7. Pull the plug (45 s)

```bash
docker compose --env-file .env -f deploy/compose/docker-compose.yml stop rules-engine
```

Within the heartbeat window the console shows **pipeline degraded**. Events keep arriving and are stored
by event-core. Start it again:

```bash
docker compose --env-file .env -f deploy/compose/docker-compose.yml start rules-engine
```

It restores its state from the changelogs and catches up; the banner clears. Incident ids are derived
from the rule, store, key and onset, so the catch-up produces the same incidents, never duplicates.

## Afterwards

`make down` keeps the data; `make clean` wipes it. If the console shows "HTTPS required" or loops on
login, the mode is wrong: `scripts/mode.sh codespaces && make down && make up`.
