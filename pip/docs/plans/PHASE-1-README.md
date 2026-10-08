# Phase 1 runbook: finish Tier 1, then grow to the full platform

**Project:** Physical Intelligence Platform (placeholder name `pip`)
**Repo:** `github.com/annsolomon/pqtone` (since 9 Oct 2026; imported from `trinamichelle29/pqtone` at `3bd157c`, whose history stays there). Repo root `/workspaces/pqtone`, project in `/workspaces/pqtone/pip`
**Codespace:** `fuzzy-guide-777rwj996746cpxp9`, 2-core / 8 GB, built from `.devcontainer/` at the repo root
**Full plan (big picture):** https://claude.ai/code/artifact/4778190e-6cca-4b39-805c-137f0a37811e. It has three tabs: the build plan and budget, architecture and implementation, and AI and shop apps.
**Written:** 9 Oct 2026

---

## How to use this file

- **Work top to bottom.** Every step says what to run, what you should see, and when it is done.
- **Tick the boxes** in the checklist below as you go, and commit this file with your progress.
- **When output doesn't match "You should see"**, stop and paste it into a Claude chat (Part G says how to start one).
- **Run every `make` command from the project folder** (`/workspaces/pqtone/pip`). After milestone 0.7 renames it, use the new folder name everywhere this file says `pip`.
- **Implementation from milestone 0.2 onward happens in Claude Code** inside the Codespace (run `claude` in the terminal). Use chat for planning, reviews and debugging.

---

## Where you are

**Done**

- `make all` is green in a fresh Codespace built from the new devcontainer:
  - offline gate: 24 cases PASS;
  - live Kafka end-to-end: PASS, 4482/4482 events stored;
  - HTTP tests: 15 passed, 9 skipped (the browser-login tests skip in Codespaces URL mode);
  - DB tests: 15/15.
- Milestones 0.3 (Java compiles) and 0.4 (stack healthy) are done.
- Milestone 0.1 (devcontainer) is done on branch `chore/0.1-devcontainer`; it only needs merging.
- The Keycloak `sslRequired` fix (`88e1f44`) is on that branch.

**Left**

- Stage 0: 0.1 merge, Claude Code setup, 0.5, 0.2, 0.6, 0.7.
- The Tier 1 exit milestones: C1, C2, R1, R2, E2, Q1, then tag `v0.1.0`.
- The other 20 Tier 1 milestones (Part D).

---

## Progress checklist

**Part A: workspace**
- [ ] A1 One Codespace kept, the old one deleted
- [ ] A4 Idle timeout set to 30 minutes

**Part B: finish Stage 0**
- [x] B1 Milestone 0.1 closed and merged
- [x] B2 Claude Code set up in the repo (plans, CLAUDE.md, agents, skill, scripts)
- [x] B3 Milestone 0.5: second consecutive green `make all`
- [x] B4 Milestone 0.2: `make test-fast`
- [x] B5 Milestone 0.6: CI green on GitHub (owner still to do: install Renovate, protect `main`)
- [ ] B6 Milestone 0.7: product rename

**Part C: Tier 1 exit milestones**
- [x] C1 Alert toast
- [x] C2 Playwright demo test
- [x] C3 R1 Watermarks doc
- [ ] C4 R2 Windowed footfall rule
- [ ] C5 E2 Schema versioning and compat check
- [ ] C6 Q1 Scorer PR comment
- [ ] C7 Gate checked, demo script written, tag `v0.1.0`

**Part D: rest of Tier 1**
- [ ] Before Tier 2: S5, E4, Q2, Q4
- [ ] Before the pilot: R4
- [ ] Any time: E1, E3, E5, S1, S2, S3, S4, R3, R5, R6, C3, C4, C5, Q3, Q5

---

## Rules for every session

1. **One milestone = one branch = one pull request.** Branch names: `chore/…`, `feat/…`, `test/…`, `docs/…`, `ci/…`.
2. **Never weaken, skip or delete a test to make a gate pass.** If a test looks wrong, stop and ask.
3. **Pipe long runs through `tee`, never `tail`.** `tail` hides progress until the very end, so a working run looks frozen.
4. **No person identification, ever.** No faces, no identity, no cross-day re-identification.
5. **State the licence before adding any dependency.** AGPL, SSPL, BSL or non-commercial licences need an ADR.
6. **Stop the Codespace when you stop working** (A3). The free plan gives you about 60 hours a month on 2-core.

---

## Part A: workspace

### A1. Keep one Codespace

Go to https://github.com/codespaces.

- **Keep `fuzzy-guide-777rwj996746cpxp9`** (branch `chore/0.1-devcontainer`).
- **Delete the other one.** First open it and run:

```bash
cd /workspaces/pqtone
git status --short
git log --branches --not --remotes --oneline
git stash list
```

**You should see:** nothing printed by any of the three. Then click **…** → **Delete** next to it on github.com/codespaces. If anything prints, push it first or ask in chat.

### A2. Start of every session

1. Open github.com/codespaces and click `fuzzy-guide…` to resume it.
2. Run:

```bash
docker info >/dev/null 2>&1 && echo "docker ok" || sudo /usr/local/share/docker-init.sh
cd /workspaces/pqtone && git switch main && git pull
cd pip && make up
make summary
```

**You should see:** `docker ok`, every service marked `✓ … healthy`, and the console URL and logins.

### A3. End of every session

```bash
cd /workspaces/pqtone
git status --short                              # commit or stash what's left
git log --branches --not --remotes --oneline    # push what's listed
cd pip && make down
```

Then stop the Codespace: press `Cmd+Shift+P` (Mac) or `Ctrl+Shift+P`, run **Codespaces: Stop Current Codespace**. `make down` keeps your data; only `make clean` deletes it.

### A4. One-time settings

- At https://github.com/settings/codespaces, set **Default idle timeout** to 30 minutes.
- Check your usage at https://github.com/settings/billing. Beyond the free 120 core-hours (60 hours on 2-core), a 2-core machine costs $0.18 an hour.

---

## Part B: finish Stage 0

### B1. Close milestone 0.1

**1. Check the browser login.**

```bash
cd /workspaces/pqtone/pip && make up && make summary
```

Open the console URL it prints and log in as `reviewer` with the password it prints.

**You should see:** the floor map. If you see "HTTPS required" or a redirect loop instead, stop and paste a screenshot into chat.

**2. Watch an alert.** Keep the console open, then run:

```bash
SCENARIO=register_delay make sim
```

**You should see:** the checkout queue grows, the checkout zone turns amber, and an incident appears in the rail.

**3. Merge the branch.**

```bash
cd /workspaces/pqtone
git switch chore/0.1-devcontainer && git pull
gh pr create --base main --head chore/0.1-devcontainer \
  --title "chore(devcontainer): milestone 0.1" \
  --body "Toolchain gate passes on a fresh Codespace; make all green (offline 24 PASS, e2e PASS, HTTP 15 passed + 9 skipped, DB 15/15). Restores the Keycloak sslRequired rendering fix."
gh pr merge chore/0.1-devcontainer --rebase --delete-branch
git switch main && git pull
git log --oneline -5
```

**You should see:** the devcontainer and `fix(bootstrap)` commits at the top of `main`. If `gh pr create` says a PR already exists, skip to `gh pr merge`. If `gh` complains about authentication, see Troubleshooting.

**Done when:** `chore/0.1-devcontainer` is merged into `main`.

### B2. Set up Claude Code in the repo

This puts the plans, rules, reviewers and helper scripts inside the repo, so every Claude Code session starts with the same context.

**1. Branch and folders**

```bash
cd /workspaces/pqtone && git switch main && git pull
git switch -c chore/claude-code-setup
mkdir -p pip/docs/plans pip/docs/adr pip/docs/learn pip/.claude/agents pip/.claude/skills/milestone
```

**2. Upload the plan files.** In the VS Code Explorer (left sidebar), drag these files from your computer into `pip/docs/plans/`:

- `00-ROADMAP.md`
- `tier-1-virtual-supermarket.md`
- `tier-2-real-vision.md`
- this file, named `PHASE-1-README.md`

```bash
ls pip/docs/plans
```

**You should see:** all four file names.

**3. CLAUDE.md** (rules Claude Code reads at the start of every session)

```bash
cat > /workspaces/pqtone/pip/CLAUDE.md <<'EOF'
# CLAUDE.md — rules for this repository

Physical-intelligence platform monorepo. This folder is the project root: run every command from here.
Plans: `docs/plans/` (start at `00-ROADMAP.md`; Phase 1 runbook: `PHASE-1-README.md`).
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
EOF
```

**4. Permissions** (what Claude Code may run without asking)

```bash
cat > /workspaces/pqtone/pip/.claude/settings.json <<'EOF'
{
  "$schema": "https://json.schemastore.org/claude-code-settings.json",
  "permissions": {
    "allow": [
      "Bash(make *)",
      "Bash(mvn *)",
      "Bash(npm *)",
      "Bash(npx *)",
      "Bash(.venv/bin/python *)",
      "Bash(python3 *)",
      "Bash(docker compose *)",
      "Bash(docker ps *)",
      "Bash(git status)",
      "Bash(git status *)",
      "Bash(git diff)",
      "Bash(git diff *)",
      "Bash(git log *)",
      "Bash(git add *)",
      "Bash(git commit *)",
      "Bash(git switch *)",
      "Bash(git branch *)",
      "Bash(gh run *)",
      "Bash(gh pr view *)",
      "Bash(gh pr checks *)"
    ],
    "ask": [
      "Bash(git push *)",
      "Bash(git reset *)",
      "Bash(rm -rf *)",
      "Bash(make clean)"
    ],
    "deny": [
      "Read(./.env)"
    ]
  }
}
EOF
python3 -m json.tool /workspaces/pqtone/pip/.claude/settings.json >/dev/null && echo "settings.json: valid"
```

**5. The two reviewer agents**

```bash
cat > /workspaces/pqtone/pip/.claude/agents/security-reviewer.md <<'EOF'
---
name: security-reviewer
description: Reviews the current diff for security and privacy problems. Use after implementing any milestone that touches services, deploy, edge code, schemas, auth or dependencies.
tools: Read, Grep, Glob, Bash
---

You review the current change set (`git diff main...HEAD` plus uncommitted changes) of a platform that turns retail camera footage into events. Report findings only; never edit files.

Check, in this order:
1. Privacy (absolute rules in CLAUDE.md): no face recognition, demographic inference, or cross-day or cross-site re-identification; no embeddings persisted or sent upstream; raw video stays on the edge; no real personal data in tests, fixtures or logs.
2. Authorization: every new endpoint, query and topic enforces role and store scope; no tenant or store id is trusted from the client without a check.
3. Injection: SQL is parameterised; no shell, template or XML built from untrusted input; XML is parsed with external entities disabled.
4. Secrets: nothing secret in code, tests, logs, images or committed files; `.env` never read into output.
5. Least privilege: database roles, Kafka ACLs, container users and capabilities, exposed ports.
6. Dependencies: every new dependency is named with its licence; AGPL, SSPL, BSL or non-commercial licences are blockers that need an ADR.

Output a table of findings (severity: critical, high, medium or low; file:line; problem; fix), then one line: "No blocking findings" or "Blocking: <count>". Don't report style issues.
EOF

cat > /workspaces/pqtone/pip/.claude/agents/contract-guardian.md <<'EOF'
---
name: contract-guardian
description: Checks the current diff for event-contract compatibility, event-time correctness and scorer impact. Use after any change to schemas, rules, the simulator, the scorer, detectors, or event producers and consumers.
tools: Read, Grep, Glob, Bash
---

You guard the event contract of this repository. Report findings only; never edit files.

Check, in this order:
1. Schemas: changes in `schemas/` are additive only; each new version has a `catalog.json` entry; `scripts/schema-compat.py` passes once it exists; producers and consumers change in the same diff.
2. Event time: every rule and window decision uses the CloudEvents `time` attribute, never wall-clock time; handling of late and out-of-order events is unchanged or explained.
3. Dedup: `(source, id)` stays the dedup key, and producers keep ids stable across retries.
4. Scorer: run `make offline` and compare with `scorer/baseline.json`. Any drop in precision or recall, or any threshold change without an ADR, is blocking.
5. Determinism: the same simulator seed still produces identical output.

Output a table of findings, the scorer's result line, then one line: "Contract safe" or "Blocking: <count>".
EOF
```

**6. The milestone skill** (you type `/milestone C1` in Claude Code)

```bash
cat > /workspaces/pqtone/pip/.claude/skills/milestone/SKILL.md <<'EOF'
---
name: milestone
description: Executes exactly one milestone from a plan file in docs/plans/, from plan to a verified commit. Use when asked to do a milestone such as 0.2, C1, R2 or V3.
---

Follow these steps in order. Stop and ask whenever a step can't be done as written.

1. Read CLAUDE.md and the milestone's row and section in docs/plans/. Restate its "Done when" as a command or an observable check.
2. Check the branch: the working tree is clean, and the branch is named `<type>/<id>-<slug>` and was created from an up-to-date `main`. Create it if needed.
3. Plan in plan mode: files to touch, tests to write first, any new dependency with its licence, and how you will prove "Done when". If the milestone is marked 🧠, name the part the user writes, describe its interface, and wait for the user's code before building on it. Wait for approval of the plan.
4. Write the tests first and show them failing for the right reason.
5. Implement until the tests pass. If the work drifts from the approved plan, stop and return to plan mode.
6. Run the gates: `make test-fast`; `make offline` if rules, the simulator or the scorer changed; `make all` if services, deploy or contracts changed. Paste the real output.
7. Run the security-reviewer and contract-guardian agents on the diff. Fix blocking findings and run the gates again.
8. Tick the milestone with `python3 scripts/tick.py docs/plans/<plan>.md <ID>`, add an ADR in docs/adr/ if a decision was made, and commit with a conventional message. Don't push.
9. Summarise what changed, how it was verified, and anything left open.
EOF
```

**7. Helper scripts**

`scripts/mode.sh` switches the console between Codespaces URL mode and localhost mode:

```bash
cat > /workspaces/pqtone/pip/scripts/mode.sh <<'EOF'
#!/usr/bin/env bash
# Switch .env between the two ways of opening the console, then re-render the Keycloak realm.
#   codespaces: open the console at the Codespace's forwarded URL (browser-login e2e tests skip)
#   localhost:  what CI uses; needed for the Playwright test (C2) and the browser-login e2e tests
set -euo pipefail
cd "$(dirname "$0")/.."
mode=${1:-}
[ -f .env ] || ./scripts/bootstrap.sh
set_env() { if grep -q "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env; else echo "$1=$2" >> .env; fi; }
case "$mode" in
  codespaces)
    : "${CODESPACE_NAME:?not running inside a Codespace}"
    set_env PIP_PUBLIC_URL "https://${CODESPACE_NAME}-8080.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}"
    set_env PIP_COOKIE_SECURE true
    set_env PIP_KC_SSL_REQUIRED none ;;
  localhost)
    set_env PIP_PUBLIC_URL http://localhost:8080
    set_env PIP_COOKIE_SECURE false
    set_env PIP_KC_SSL_REQUIRED external ;;
  *) echo "usage: scripts/mode.sh codespaces|localhost" >&2; exit 2 ;;
esac
rm -rf deploy/keycloak/generated
"${BOOTSTRAP:-./scripts/bootstrap.sh}"
grep -E '^(PIP_PUBLIC_URL|PIP_COOKIE_SECURE|PIP_KC_SSL_REQUIRED)=' .env
echo "Mode set to $mode. Restart the stack: make down && make up"
EOF
chmod +x /workspaces/pqtone/pip/scripts/mode.sh
```

`scripts/tick.py` marks a milestone row done in a plan file:

```bash
cat > /workspaces/pqtone/pip/scripts/tick.py <<'EOF'
#!/usr/bin/env python3
"""Mark a milestone row done in a plan file: scripts/tick.py docs/plans/<file>.md <ID> [note]"""
import sys
from pathlib import Path

if len(sys.argv) < 3:
    sys.exit("usage: scripts/tick.py <plan.md> <milestone id> [note]")
path, mid = Path(sys.argv[1]), sys.argv[2]
note = sys.argv[3] if len(sys.argv) > 3 else ""
lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
hits = [i for i, l in enumerate(lines) if l.startswith(f"| {mid} |") or l.startswith(f"| ✅ {mid} |")]
if len(hits) != 1:
    sys.exit(f"expected one row for {mid} in {path}, found {len(hits)}")
i = hits[0]
if lines[i].startswith(f"| ✅ {mid} |"):
    print(f"{mid} already ticked"); sys.exit(0)
row = lines[i].replace(f"| {mid} |", f"| ✅ {mid} |", 1)
if note:
    row = row.rstrip("\n").rstrip()
    row = row[:-1].rstrip() + f" Note: {note} |\n" if row.endswith("|") else row + f" {note}\n"
lines[i] = row
path.write_text("".join(lines), encoding="utf-8")
print(row.strip())
EOF
chmod +x /workspaces/pqtone/pip/scripts/tick.py
```

**8. The Stage 0 log**

```bash
cat > /workspaces/pqtone/pip/docs/plans/stage0-log.md <<'EOF'
# Stage 0 log

What had to change to make Tier 1 run for real, and why.

| Date | Milestone | What changed | Why |
| --- | --- | --- | --- |
| 2026-10 | 0.3–0.5 | Makefile: one `export` per line (`DOCKER_BUILDKIT`, `COMPOSE_DOCKER_CLI_BUILD`) | Combined exports broke the build environment |
| 2026-10 | 0.3–0.5 | `scripts/bootstrap.sh`: secrets from `openssl rand -hex 24`, fails on any empty secret | Empty secrets broke Keycloak and Postgres logins |
| 2026-10 | 0.3–0.5 | event-core and rules-engine runtime image: `eclipse-temurin:21-jre` (glibc) | Alpine (musl) broke the RocksDB and zstd native libraries |
| 2026-10 | 0.3–0.5 | `tests/http/conftest.py`: clears the Secure flag on cookies before the Keycloak form POST | Python's cookie jar doesn't send Secure cookies to http://localhost, browsers do |
| 2026-10 | 0.3–0.5 | nginx: `/actuator` returns 404; forwards X-Forwarded-Proto/Host/Port through maps | Hide internals; correct redirects behind the Codespaces proxy |
| 2026-10 | 0.3–0.5 | `application.yml`: `redirect-uri = ${PIP_PUBLIC_URL}/login/oauth2/code/{registrationId}` | Login redirect must match the public URL in Codespaces |
| 2026-10-03 | 0.1 | Devcontainer: Temurin 21, Maven 3.9.9, Docker CE docker-in-docker, git-lfs, toolchain gate | Docker had to be started by hand; Maven drifted to 3.10 |
| 2026-10-03 | 0.1 | `fix(bootstrap)` 88e1f44: realm `sslRequired` rendered from `PIP_KC_SSL_REQUIRED` | The earlier fix was lost; Codespaces URL mode needs `none` |
EOF
```

**9. Tick 0.1, 0.3 and 0.4, and ignore local files**

```bash
cd /workspaces/pqtone/pip
python3 scripts/tick.py docs/plans/tier-1-virtual-supermarket.md 0.1 "2-core / 8 GB proven by make all; 4-core optional"
python3 scripts/tick.py docs/plans/tier-1-virtual-supermarket.md 0.3
python3 scripts/tick.py docs/plans/tier-1-virtual-supermarket.md 0.4
printf '.venv/\n.claude/settings.local.json\n' >> .gitignore
```

**You should see:** three rows printed, each starting with `| ✅`.

**10. Commit and merge**

```bash
cd /workspaces/pqtone
git add pip/CLAUDE.md pip/.claude pip/docs pip/scripts/mode.sh pip/scripts/tick.py pip/.gitignore
git status --short
git commit -m "chore(claude): plans, CLAUDE.md, reviewer agents, milestone skill, helper scripts"
git push -u origin HEAD
gh pr create --fill
gh pr merge --rebase --delete-branch
git switch main && git pull
```

**11. Start Claude Code once to check it**

```bash
cd /workspaces/pqtone/pip && claude
```

Sign in when asked. Inside Claude Code, type:

- `/status`: it should show the project settings as loaded.
- `/agents`: it should list `security-reviewer` and `contract-guardian`.
- `/`: `milestone` should appear in the list.

Leave with `/exit`.

**Done when:** all three checks pass and the branch is merged.

### B3. Milestone 0.5: second consecutive green run

```bash
cd /workspaces/pqtone && git switch main && git pull && git switch -c chore/0.5-second-green-run
cd pip && make all 2>&1 | tee /tmp/make-all-run2.log
grep -iE "Result: |passed|failed|error" /tmp/make-all-run2.log | tail -20
```

**You should see:**
- two `**Result: PASS**` lines (offline and e2e);
- `15 passed, 9 skipped` (HTTP);
- `15 passed` (DB);
- no `failed`.

Then record it and merge:

```bash
printf '| %s | 0.5 | Second consecutive `make all` green, no `make clean` between | Proves reruns are idempotent |\n' "$(date +%F)" >> docs/plans/stage0-log.md
python3 scripts/tick.py docs/plans/tier-1-virtual-supermarket.md 0.5
git add docs/plans && git commit -m "docs(plans): 0.5 second consecutive green make all"
git push -u origin HEAD && gh pr create --fill && gh pr merge --rebase --delete-branch
git switch main && git pull
```

**Done when:** run 2 matches run 1 and the branch is merged.

### B4. Milestone 0.2: `make test-fast`

This adds a gate that runs every unit test in under 3 minutes without Docker. Claude Code runs it after every change.

```bash
cd /workspaces/pqtone && git switch main && git pull && git switch -c chore/0.2-test-fast
cd pip
python3 - <<'PY'
from pathlib import Path
p = Path("Makefile"); s = p.read_text()
if "test-fast:" in s:
    raise SystemExit("Makefile already has test-fast")
old = ".PHONY: all help bootstrap build offline up e2e e2e-pipeline e2e-http e2e-db summary sim down clean logs ps"
anchor = "bootstrap: ## Generate .env secrets"
for a in (old, anchor):
    if s.count(a) != 1:
        raise SystemExit(f"anchor not found exactly once: {a!r}")
T = "\t"  # Makefile recipes must start with a real tab
block = "\n".join([
    "VENV := .venv",
    "PY   := $(VENV)/bin/python",
    "MVN  ?= mvn",
    "",
    "dev-setup: ## One-time, no Docker: Python venv with sim + scorer, and web dependencies",
    T + "python3 -m venv $(VENV)",
    T + "$(PY) -m pip install -q --upgrade pip",
    T + '$(PY) -m pip install -q -e "./sim[test]" -e "./scorer[test]"',
    T + "cd web/ops-console && npm ci --no-audit --no-fund",
    "",
    "test-fast: ## No Docker, under 3 min: Java unit tests, Python tests, web typecheck and tests",
    T + '@[ -x "$(PY)" ] || { echo "Run \'make dev-setup\' once first." >&2; exit 1; }',
    T + "cd services/event-core && $(MVN) -B -q -ntp verify",
    T + "cd services/rules-engine && $(MVN) -B -q -ntp verify",
    T + "$(PY) -m pytest -q -p no:cacheprovider sim/tests scorer/tests",
    T + "cd web/ops-console && npm run -s typecheck && npm test --silent",
    "", "",
])
s = s.replace(old, old + " dev-setup test-fast").replace(anchor, block + anchor)
p.write_text(s)
print("Makefile patched: dev-setup, test-fast")
PY
make help | grep -E "dev-setup|test-fast"
make dev-setup
time make test-fast     # first run downloads Maven dependencies; ignore its time
time make test-fast     # this is the run that counts
```

**You should see:**
- `make help` lists both new targets;
- each run ends with vitest's `Tests 8 passed`, after 20 pytest passes and the Java tests;
- the second run's `real` time is under `3m`.

Then:

```bash
python3 scripts/tick.py docs/plans/tier-1-virtual-supermarket.md 0.2
git add Makefile docs/plans && git commit -m "build(make): add dev-setup and test-fast (milestone 0.2)"
git push -u origin HEAD && gh pr create --fill && gh pr merge --rebase --delete-branch
git switch main && git pull
```

**Done when:** `make test-fast` exits 0 in under 3 minutes, and the branch is merged.

### B5. Milestone 0.6: CI green on GitHub

GitHub only runs workflows from `.github/` at the repo root, so CI has never run. This moves it up and points the build job at `pip/`.

**1. Move the workflow and Renovate config**

```bash
cd /workspaces/pqtone && git switch main && git pull && git switch -c ci/0.6-root-workflow
git mv pip/.github .github
git mv pip/renovate.json renovate.json
python3 - <<'PY'
from pathlib import Path
p = Path(".github/workflows/ci.yml"); s = p.read_text()
if "working-directory: pip" in s:
    raise SystemExit("ci.yml already patched")
edits = [
    ("    runs-on: ubuntu-24.04\n    timeout-minutes: 60\n    steps:",
     "    runs-on: ubuntu-24.04\n    timeout-minutes: 60\n    defaults:\n      run:\n        working-directory: pip\n    steps:"),
    ("path: .work/reports\n", "path: pip/.work/reports\n"),
    ("path: .work/reports/offline/baseline.candidate.json", "path: pip/.work/reports/offline/baseline.candidate.json"),
]
for old, new in edits:
    if s.count(old) != 1:
        raise SystemExit(f"anchor not found exactly once: {old!r}")
    s = s.replace(old, new)
p.write_text(s)
print("ci.yml patched: pipeline job runs in pip/, artifact paths updated")
PY
```

**2. Add the CI badge to the root README**

```bash
cat > README.md <<'EOF'
# pqtone

![ci](https://github.com/trinamichelle29/pqtone/actions/workflows/ci.yml/badge.svg)

Physical-intelligence platform for retail stores. The project lives in [`pip/`](pip/README.md).
Plans and the Phase 1 runbook are in [`pip/docs/plans/`](pip/docs/plans/).
EOF
git add -A .github renovate.json pip README.md
git commit -m "ci: run the workflow from the repo root against pip/ (milestone 0.6)"
git push -u origin HEAD
gh pr create --fill
gh pr checks --watch
```

**You should see:** the jobs `build, test, score, e2e`, `secret scan`, `dependency and config scan` and three `codeql` jobs. The first run takes 25–40 minutes.

**3. If a job fails**, save its log and hand it to Claude Code:

```bash
cd /workspaces/pqtone
RUN=$(gh run list --branch "$(git branch --show-current)" -L 1 --json databaseId -q '.[0].databaseId')
gh run view "$RUN" --log-failed > /tmp/ci-fail.log
wc -l /tmp/ci-fail.log
```

In Claude Code (`cd pip && claude`):

```
Milestone 0.6. Read /tmp/ci-fail.log. Find the root cause of the first failing job, reproduce
it locally with the narrowest command, fix it, and log failure, cause and fix in
docs/plans/stage0-log.md. Never weaken a test. A Trivy finding is fixed by updating the base
image or dependency; accepting a CVE needs an ADR and a .trivyignore entry with an expiry date.
A gitleaks finding on a test value is fixed in the test, or allow-listed in .gitleaks.toml only
if the value is a known fake. Stop after the fix and show me the diff.
```

Push the fix (`git push`), and run `gh pr checks --watch` again. Repeat until everything is green.

**4. When everything is green**

```bash
gh pr merge --rebase --delete-branch && git switch main && git pull
```

**5. Pin actions with Renovate**

1. Install the Renovate app from https://github.com/apps/renovate on `pqtone` only.
2. Merge its onboarding PR once CI is green on it.
3. Merge the PR that pins actions to commit SHAs once CI is green.

Pinning matters: tags of popular actions have been hijacked before, and a pinned SHA can't be moved.

**6. Protect `main`.** In the repo, go to **Settings → Rules → Rulesets** (or **Branches** on older layouts) and add a rule for `main`:
- require a pull request;
- require the status checks `build, test, score, e2e`, `secret scan` and `dependency and config scan`.

**7. Tick 0.6**

```bash
cd /workspaces/pqtone && git switch -c docs/tick-0.6
python3 pip/scripts/tick.py pip/docs/plans/tier-1-virtual-supermarket.md 0.6
git add pip/docs/plans && git commit -m "docs(plans): tick 0.6" && git push -u origin HEAD
gh pr create --fill && gh pr checks --watch && gh pr merge --rebase --delete-branch
git switch main && git pull
```

**Cost note:** Actions minutes are free on public repos. On a private repo the Free plan includes 2,000 minutes a month, and each full CI run uses roughly 30–45 minutes across its jobs. Check https://github.com/settings/billing.

**Done when:** the badge is green on `main`, reports are uploaded as artifacts, Renovate pins are merged, and `main` is protected.

### B6. Milestone 0.7: product rename

`pip` collides with Python's package manager. Renaming takes hours now and weeks later.

**1. Pick the name.** Use these rules:
- 4–10 lowercase letters, easy to say in English, Tamil and Hindi;
- the `.in` and `.com` domains are free;
- no trademark clash in software or retail (search the name plus "software", "retail" and "CCTV");
- valid as a Java package (`com.<name>`) and a Python package (no hyphens).

**2. See what will change**

```bash
cd /workspaces/pqtone/pip
grep -rnIE --exclude-dir={node_modules,.venv,.work,target,dist,.git} \
  'com\.pip|urn:pip|schemas\.pip\.local|pip_scorer|pip-scorer|PIP_|realms/pip|pip/(event-core|rules-engine|gateway|tools)' . | wc -l
grep -rnIw --exclude-dir={node_modules,.venv,.work,target,dist,.git} 'pip' . | grep -vE 'pip install|python -m pip|pip3' | wc -l
```

**3. Run it in Claude Code** (replace `<name>` with your choice):

```
/milestone 0.7
New product name: <name>. Rename every project use of "pip": Java packages com.pip → com.<name>
(move the directories), urn:pip → urn:<name>, schemas.pip.local → schemas.<name>.local,
pip_scorer/pip-scorer → <name>_scorer/<name>-scorer, PIP_ env vars → <NAME>_, the Keycloak realm,
the compose project and image names (pip/...), docs, CLAUDE.md, scripts, .github/workflows/ci.yml
and the root README. Then rename the folder pip/ to <name>/ with git mv and update the CI
working-directory and artifact paths. Do NOT touch Python's package manager: "pip install",
"python -m pip" and pip3 stay. Do it as one mechanical change, then run make test-fast and
make all. Show me both grep counts from the runbook before and after; the first must be 0.
```

**4. Reset your local stack.** The realm and variable names changed, so old data won't match:

```bash
NAME=yourname                      # the name you chose in step 1
cd /workspaces/pqtone/$NAME
make clean                         # deletes local data, .env and reports
scripts/mode.sh codespaces         # regenerates .env and puts it back in Codespaces URL mode
make all 2>&1 | tee /tmp/make-all-rename.log
```

**5. Merge** after CI is green: `git push -u origin HEAD && gh pr create --fill && gh pr checks --watch && gh pr merge --rebase --delete-branch`.

**Done when:** `make all` and CI are green after the rename, and the first grep count is 0. From here on, read `pip` in this file as `<name>`.

---

## Part C: Tier 1 exit milestones

### C0. How every milestone runs

1. **Start clean.**

```bash
cd /workspaces/pqtone && git switch main && git pull
cd pip && claude
```

2. **Inside Claude Code:**

```
/clear
/milestone <ID>
<the extra instructions for that milestone from this file>
```

3. **Plan mode.** Claude proposes files, tests and checks. Read the plan, push back, then approve.
4. **Build.** Claude writes tests first, then code, runs the gates and the two reviewers, ticks the milestone and commits.
5. **You check:**

```bash
git diff main...HEAD --stat
make test-fast
```

Also run the milestone's "Check" commands below. For every concept you didn't write yourself, ask Claude to explain it, then explain it back.

6. **Push and merge:**

```bash
git push -u origin HEAD && gh pr create --fill && gh pr checks --watch && gh pr merge --rebase --delete-branch
```

If Claude's session stops halfway, start a new one with `/milestone <ID>` and add: "Continue from the current branch; read `git log main..HEAD` and the plan first."

### C1. Alert toast (🧠 you write the reducer)

**Branch:** `feat/c1-alert-toast`

**Extra instructions:**

```
I write web/ops-console/src/alerts/alertReducer.ts and its tests myself. In plan mode, propose
the reducer's state and action types and the component wiring, then stop. After I have written
the reducer and its tests, build the toast, the zone outline animation, the browser
Notification (permission requested only from a click) and a sound toggle around my reducer,
without changing it.
```

**Check:**

```bash
cd web/ops-console && npm test && npm run typecheck && cd ../..
make build && make up && SCENARIO=register_delay make sim
```

**Done when:** a new enforce incident shows a toast and an outline animation; your reducer tests pass.

### C2. Playwright demo test

**Branch:** `test/c2-playwright-demo`

**Extra instructions:**

```
Add tests/ui with Playwright for Python (Apache-2.0) in a pinned mcr.microsoft.com/playwright/python
image. Add a compose service e2e-ui in the tools profile with network_mode "service:gateway",
like e2e-http, so http://localhost:8080 is the browser origin. The test logs in as reviewer,
starts the register_delay scenario at 20x through the store-sim, waits until the queue counter
reaches 6 or more, sees the incident appear in the rail, opens it and confirms it. Record a
video to .work/reports/ui/. Skip with a clear message when PIP_PUBLIC_URL is not
http://localhost:8080 (Codespaces URL mode), exactly as the browser-login HTTP tests do. Add it
to make e2e and upload the video in CI.
```

**Check.** Localhost mode is needed to run it in the Codespace:

```bash
scripts/mode.sh localhost && make down && make all 2>&1 | tee /tmp/c2.log
ls -la .work/reports/ui/
scripts/mode.sh codespaces && make down && make up     # back to normal
```

**Done when:**
- `make all` is green in localhost mode with the UI test passing, not skipped;
- CI is green and its artifacts include the video.

### C3. R1: watermarks doc (🧠 you write all of it)

**Branch:** `docs/r1-watermarks`

**1. Find the code that decides lateness:**

```bash
grep -rn "grace\|watermark\|streamTime" services/rules-engine/src/main --include=*.java | head -30
make offline 2>&1 | tee /tmp/offline.log
grep late_beyond_grace /tmp/offline.log
```

**2. Write `docs/learn/watermarks.md` yourself.** Cover:
- stream time vs event time vs wall time;
- why grace exists;
- what "late" means here, compared with Flink;
- a worked example from `late_beyond_grace` (its `lateDropped` counts are 59–73 per seed).

**3. Test yourself** in Claude Code:

```
Milestone R1 is mine to write. Give me a hand-made sequence of 12 events for one store, with
event times, but not the answer. After I predict which events get dropped as late, run the
sequence through the OfflineRunner and compare with my prediction. Don't edit my doc; point
out mistakes in it.
```

**Done when:** your prediction matches the OfflineRunner, and the doc is merged.

### C4. R2: windowed footfall rule (🧠 you write the windowing code)

**Branch:** `feat/r2-footfall-window`

**Extra instructions:**

```
I write the Kafka Streams windowing code myself (TimeWindows.ofSizeAndGrace + suppress
untilWindowCloses). In plan mode, define the class and method I fill in, then stop until I've
written it. You: add a footfall-spike scenario with ground truth to sim/scenarios and
scorer/matrix.yaml; add R-FOOT-001 to config/rules.yaml in shadow mode; add the rule to the
Python reference rules in sim/ and to the scorer; wire the topology around my code; add JUnit
tests for window boundaries and late records. Propose updating scorer/baseline.json only as a
separate, explained commit. Switch the rule to enforce only after the offline and e2e scores pass.
```

**Check:**

```bash
make test-fast
make offline 2>&1 | tee /tmp/offline.log && grep -E "R-FOOT-001|Result" /tmp/offline.log
make all 2>&1 | tee /tmp/make-all.log
```

**Done when:** the offline matrix and e2e are green with R-FOOT-001 rows, and the JUnit window tests pass.

### C5. E2: schema versioning and compat check (🧠 you write the compat rules)

**Branch:** `feat/e2-schema-versioning`

**Extra instructions:**

```
I write the compatibility rules myself: the function in scripts/schema-compat.py that compares
two versions of a schema and fails if a field is removed or renamed, a type is tightened, or a
required field is added. You write the CLI around it (it checks every catalog.json entry
against the previous version), schemas/data/store.queue.length-1.1.0.schema.json with an
optional estimatedWaitSeconds, its catalog entry, event-core tests proving 1.0.0 and 1.1.0
events are both accepted, and a deliberately breaking 1.2.0 test fixture (not registered in
catalog.json) that must make the script fail. Run the script in make test-fast and in CI.
```

**Check:**

```bash
.venv/bin/python scripts/schema-compat.py; echo "exit=$?"      # must be 0
make test-fast && make all 2>&1 | tee /tmp/make-all.log
```

**Done when:**
- the script passes on the real schemas;
- the breaking fixture test proves it fails on a bad version;
- both versions are accepted by event-core;
- CI runs the check.

### C6. Q1: scorer PR comment

**Branch:** `ci/q1-score-comment`

**Extra instructions:**

```
Post the offline score as one sticky PR comment that is updated on every push, with a delta
column against scorer/baseline.json. Use a separate job that needs the pipeline job, downloads
the reports artifact, and has only pull-requests: write (least privilege). Use
marocchino/sticky-pull-request-comment (MIT) pinned to a commit SHA, or gh pr comment
--edit-last. Skip on pushes to main.
```

**Check:** open the PR. The comment should appear with the score table, and update when you push again.

**Done when:** it is visible and updating on a real PR.

### C7. Gate, demo script, tag `v0.1.0`

**1. Check the gate.** All must be true:

- [ ] `make all` green twice in a row on `main`
- [ ] CI green on `main`, badge green
- [ ] The C2 Playwright test passes in CI and its video is uploaded
- [ ] `scorer/baseline.json` committed and matching the current rules
- [ ] Milestones 0.1–0.7, C1, C2, R1, R2, E2 and Q1 ticked in the Tier 1 plan

**2. Write the demo script** in Claude Code:

```
Write docs/demos/tier-1.md from the demo script at the end of
docs/plans/tier-1-virtual-supermarket.md, using the commands that exist now (including
scripts/mode.sh). Then walk me through it step by step while I run it.
```

**3. Rehearse the 5-minute demo once**, end to end.

**4. Tag and release:**

```bash
cd /workspaces/pqtone && git switch main && git pull
git tag -a v0.1.0 -m "Tier 1: Virtual Supermarket"
git push origin v0.1.0
gh release create v0.1.0 --title "v0.1.0: Tier 1 Virtual Supermarket" --notes-file pip/docs/demos/tier-1.md
```

**Done when:** the release exists on GitHub. **Tier 1 exit gate passed; Tier 2 can start.**

---

## Part D: everything else in Tier 1

These finish Tier 1 completely. Each runs exactly like C0: `/milestone <ID>`, plus the note in the last column. The full description and "Done when" for each are in `docs/plans/tier-1-virtual-supermarket.md`.

| ID | Milestone | Hours | When | Note |
| --- | --- | --- | --- | --- |
| S5 | Noise hooks (`faults.vision`, no-op) | 2–3 | Before Tier 2 | Tier 2's noise-model fills it in |
| E4 | Testcontainers integration tests (🧠 race test) | 8–12 | Before Tier 2 | Testcontainers is MIT. Keep `make test-fast` Docker-free: integration tests run in `make all` and CI, and `test-fast` passes `-DskipITs` |
| Q2 | Wall-clock processing latency in e2e | 3–5 | Before Tier 2 | Adds a latency gate |
| Q4 | Wilson confidence intervals | 3–4 | Before Tier 2 | Needed before reporting precision to a pilot customer |
| R4 | Rule versioning and hot reload | 8–12 | Before the pilot | Lets you change a threshold without a restart |
| E1 | Trace one request end to end (🧠 you write) | 3–4 | Any time | Debugger + Jaeger |
| E3 | Flyway discipline | 3–4 | Any time | Latest migration is `V4__append_only_and_grants.sql`, so add `V5__incident_assignee.sql` |
| E5 | OpenAPI contract test | 3–5 | Any time | springdoc-openapi is Apache-2.0 |
| S1 | Explain the simulator (🧠 you write) | 3–4 | Any time | |
| S2 | Second store layout, multi-store | 6–10 | Any time | Adds `config/layouts/store-002.json`; console store picker |
| S3 | 3 new scenarios + authoring guide | 4–6 | Any time | `staff_shortage`, `flash_sale`, `closing_time` |
| S4 | HTTP sink e2e with OAuth client credentials | 3–5 | Any time | |
| R3 | ADR comparing windowing approaches | 2–3 | After R2 | `docs/adr/011-windowing.md` |
| R5 | Scale test: 50 stores at 10× | 6–10 | Any time | Too heavy for 2-core: switch to 4-core for that session (double core-hours) and record the machine in the report |
| R6 | Shadow-to-enforce promotion flow | 4–6 | After R4 | |
| C3 | Accessibility pass | 4–6 | After C2 | axe-core is MPL-2.0, test-only |
| C4 | Incident timeline view | 6–8 | Any time | Uses the existing `/api/events` |
| C5 | Review metrics for admins | 4–6 | Any time | |
| Q3 | Baseline update flow (`make baseline-accept`) | 2–3 | After Q1 | Never auto-update the baseline |
| Q5 | Hungarian matching (🧠 you write) | 4–6 | Any time | Property tests against brute force |

---

## Part E: Tier 2 prep while you finish Tier 1

None of these need code, and Tier 2 waits on them.

- [ ] **Product name decided** (needed for B6 anyway).
- [ ] **Footage:** a shop owner agrees to let you record their checkout with signage and written consent from the owner and the people recorded. Ask two or three shops this month.
- [ ] **Camera:** buy one TP-Link VIGI C440I 2.8 mm (about ₹2,900–5,000) and a PoE injector (about ₹1,500–2,000).
- [ ] **ADR-012, detector choice:** YOLOX-S (Apache-2.0), not Ultralytics YOLO (AGPL-3.0). Write it in `docs/adr/012-detector.md`.
- [ ] **GPU account:** open an E2E Networks account (India data centres, billed in rupees) and complete verification.
- [ ] **Privacy draft:** start `docs/privacy/vision-data.md`: what you record, why, how long you keep it, who can see it.

---

## Part F: after Phase 1, the road to the full platform

The full plan, budget and architecture are in the plan doc linked at the top. This is how to execute it.

### The ladder

| Stage | Exit check | When (20 h/week) | Plan file |
| --- | --- | --- | --- |
| 1. Tier 1 done | Tag `v0.1.0`, CI green | late Oct 2026 | `tier-1-virtual-supermarket.md` (exists) |
| 2. Real vision | ±1 queue count on ≥95% of samples, 3 clips; cable-pull test loses nothing | Jan 2027 | `tier-2-real-vision.md` (exists) |
| 3. Pilot-ready (Tier 3 slice) | 0 cross-tenant leaks; onboarding under 1 hour; lawyer-reviewed privacy pack | Mar 2027 | `tier-3-enterprise-saas.md` (write it first) |
| 4. First paid store | 4+ weeks live, ≥80% of incidents reviewed, signed contract | ~Jun 2027 | Pilot section of the plan doc |
| 5. Product | 3+ paying stores; Tally/Zoho connectors and assistant (X1–X2) | late 2027 | `expansion-x1-x2.md` (write it first) |
| 6. Platform | App platform, your own model, store AI box (X3–X6) | 2028 | `expansion-x3-x6.md` (write it first) |
| 7. Enterprise | Pen test, SOC 2 or ISO 27001, support SLA, a second engineer | when a chain is in talks | `tier-5-scale.md` + `expansion-x7.md` |

### How to run each stage

1. **Before a stage starts, its plan file must exist in `docs/plans/`.** Tiers 3, 4, 5 and the X-phases are planned in the doc but not yet written as plan files. In a chat, ask Claude to write the next one (prompt in Part G), review it, and commit it.
2. **Build it milestone by milestone** with `/milestone <ID>` in Claude Code, exactly like Part C.
3. **Check the exit gate** before moving up. Don't start the next stage early.
4. **Record every decision** that changes the plan as an ADR in `docs/adr/`, then update the plan file.
5. **Keep the plan doc as the big picture** and the plan files as the source of truth for what gets built.

### The non-code tracks

| When | What |
| --- | --- |
| Now | Product name, footage consent, camera |
| Jan 2027 | Start pilot conversations with 2–3 stores |
| Feb 2027 | Book the lawyer; open the E2E staging server |
| Mar 2027 | Lawyer reviews notice, signage, DPA and pilot agreement before any camera goes into a customer's store |
| Before the first paid contract | Register the company; ask a chartered accountant about GST |
| After 3 paying stores | Hire one engineer |
| When a chain asks | Penetration test, then SOC 2 or ISO 27001 |

---

## Part G: continuing in a new chat

A new chat can't see earlier conversations or uploads. Give it everything it needs.

### What to attach

1. This file (`PHASE-1-README.md`) with your ticks.
2. `00-ROADMAP.md`, the plan file for the stage you're in, and `CLAUDE.md`.
3. The plan doc's three tabs, each exported as Markdown from the doc's export menu.
4. The latest gate output, if you're mid-milestone.

### Opening message while finishing Phase 1

```
PROJECT: Physical Intelligence Platform (placeholder "pip"), Tier 1 "Virtual Supermarket".
Repo github.com/trinamichelle29/pqtone; repo root /workspaces/pqtone, project in /workspaces/pqtone/pip.
Codespace fuzzy-guide-777rwj996746cpxp9 (2-core / 8 GB), devcontainer at repo root.
I follow the attached PHASE-1-README.md. Checklist state: <paste the checklist with ticks>.
Current milestone: <ID>. Last gate output: <paste, or "none yet">.
Implementation happens in Claude Code in my Codespace with /milestone; use this chat for plans,
reviews and debugging. I want direct answers with full copy-paste commands.
RULES: one milestone at a time; plan, then tests, then code; show the gate output; never weaken
a test; no person identification ever; state the licence before adding any dependency.
NEXT: <what you want, e.g. "help me fix this CI failure" or "review my alert reducer">.
```

### Opening message after `v0.1.0`

```
PROJECT: Physical Intelligence Platform (product name: <name>), repo github.com/trinamichelle29/pqtone,
project in /workspaces/pqtone/<name>. Tier 1 is done: tag v0.1.0, CI green.
Attached: PHASE-1-README.md (Part F has the ladder), 00-ROADMAP.md, tier-2-real-vision.md,
CLAUDE.md, and the three tabs of my build-plan doc as Markdown.
Implementation happens in Claude Code in my Codespace with /milestone; use this chat for plans,
reviews and debugging. I want direct answers with full copy-paste commands.
RULES: one milestone at a time; plan, then tests, then code; show the gate output; never weaken
a test; no person identification ever; state the licence before adding any dependency.
NEXT: start Tier 2. Check my Part E prep list, then give me the plan for milestone V1.
```

### Asking for the next plan file (before Tier 3, X1 and so on)

```
Write docs/plans/tier-3-enterprise-saas.md in the same format as tier-2-real-vision.md: goal,
where it runs, exit gate, then one table of milestones per project with "Done when" as a
command or observable check and 🧠 marking the parts I write. Base it on the attached plan doc
tabs and 00-ROADMAP.md. Include only what the exit gate needs; list anything else as "later".
```

---

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `docker: Cannot connect to the Docker daemon` | The devcontainer starts Docker by itself; if it didn't, run `sudo /usr/local/share/docker-init.sh`. If that's needed often, the Codespace wasn't built from the new devcontainer: create a new one from the branch (below). |
| `make all` looks frozen | You piped it through `tail`. Use `tee` only. Check progress with `docker ps`. |
| "95% memory" warning | Mostly page cache: check `free -h`, column `available`. Stop extras with `docker compose --env-file .env -f deploy/compose/docker-compose.yml stop grafana prometheus jaeger`, and `make down` when idle. |
| A container exited with code 137 | Out of memory. `make down`, close other workloads, or use a 4-core machine for that session. |
| Disk nearly full | `docker builder prune -af && docker image prune -f`, then `df -h /`. |
| "HTTPS required" or a login redirect loop | Wrong mode: `scripts/mode.sh codespaces && make down && make up`. Check with `grep -o '"sslRequired": "[a-z]*"' deploy/keycloak/generated/*.json` (should say `none`). |
| Login broken after `make clean` | `make clean` deletes `.env`. Run `scripts/mode.sh codespaces`, then `make all`. |
| 9 HTTP tests "skipped" | Expected in Codespaces URL mode. They run in localhost mode and in CI. |
| `git push` says git-lfs not found | `git lfs install`. The devcontainer feature normally installs it. |
| `gh`: "Resource not accessible by integration" or not logged in | `unset GITHUB_TOKEN; gh auth login -h github.com -p https -w`, then retry. |
| Rebuild Container does nothing | Create a fresh Codespace from the branch: `https://codespaces.new/trinamichelle29/pqtone/tree/<branch>`. Then `scripts/mode.sh codespaces && make dev-setup && make all`. |
| Shortcut `Ctrl+Shift+P` does nothing | On a Mac it's `Cmd+Shift+P`. Or ☰ → View → Command Palette, or F1. |
| First `make test-fast` is slow | Maven is downloading dependencies to `~/.m2`. The second run is the one that counts. |
| Claude Code doesn't list the agents or skill | Restart it: folders created during a session aren't picked up until the next start. |

---

## Time and cost left

| Part | Hours (20 h/week ≈ calendar) |
| --- | --- |
| B: finish Stage 0 | 12–20 h (≈ 1 week) |
| C: Tier 1 exit milestones | 35–50 h (≈ 2–3 weeks) |
| D: rest of Tier 1 | 90–130 h (≈ 5–6 weeks, can overlap Tier 2) |

- **Codespaces:** at 20 h/week on 2-core you use about 87 hours a month; beyond the free 60 hours, that's roughly $5 (about ₹470) a month.
- **GitHub Actions:** free on a public repo. On a private repo, watch the 2,000 free minutes a month.
- **Claude plan:** heavy Claude Code use usually needs a higher plan; check https://claude.com/pricing.
