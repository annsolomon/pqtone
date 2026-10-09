# Roadmap: from Virtual Supermarket to a physical-intelligence SaaS

This is the master plan. It covers:

- the end goal and how the five tiers get there;
- how the repository grows: one monorepo, never restarted;
- how to build every project with Claude Opus in Claude Code;
- the gates you must pass before moving up a tier;
- a realistic timeline.

Each tier has its own plan file:

| File | Tier | Projects |
|---|---|---|
| `tier-1-virtual-supermarket.md` | 1 | event-core, store-sim, rules-engine, ops-console, scorer |
| `tier-2-real-vision.md` | 2 | vision-lab, noise-model, edge-agent, mtmc-fusion |
| `tier-3-enterprise-saas.md` | 3 | tenant-platform, onboarding-wizard, billing-and-api, observability |
| `tier-4-intelligence.md` | 4 | analytics, retail-integrations, assistant, anomaly |
| `tier-5-scale.md` | 5 | fleet, cells |

`CLAUDE.md` and `claude-kit/` are starter files that go into the repo root (see §4).

---

## 1. The end goal, stated so it can be tested

> A store owner connects existing cameras to a small edge box. Within a day, they see live queue, dwell and footfall on their floor plan. They get alerts that are right more often than not. They review incidents with evidence. They ask questions in plain language and get cited answers. None of this ever identifies a person.
>
> The operator, you, runs many tenants across isolated cells, with data kept in India. Devices update safely, and quality is measured continuously.

Every tier adds one capability to that sentence. The **event contract** carries it all:
- CloudEvents with a versioned schema;
- `(source, id)` dedup;
- event time throughout.

Every module either produces those events or consumes them. That is why the modules can be built one at a time and still compose into a single platform:

```
             ┌──────────── producers (all emit the same CloudEvents) ───────────┐
             │ store-sim (T1)   vision-lab (T2)   edge-agent (T2)   POS-sim (T4)│
             └───────────────────────────────┬──────────────────────────────────┘
                                             ▼
         event-core (T1): validate · dedup · store · outbox      ← tenant-platform (T3) adds tenancy
                                             ▼
         rules-engine (T1) · anomaly (T4) · analytics (T4)       ← scorer (T1) grades every one of them
                                             ▼
         ops-console (T1) · onboarding (T3) · public API (T3) · assistant (T4)
                                             ▼
         observability (T3) · fleet (T5) · cells (T5)            ← run it for many customers
```

**The scorer is your compass.** Every tier must keep it green, and every new detector gets scored against ground truth. That works because store-sim, the noise-model and later recorded video all produce ground truth.

---

## 2. Build on top or start new? Build on top, in one monorepo

**Do not start a new repository per project.** Keep the `pqt` repo you already have and grow it.

| Reason | What it buys you |
|---|---|
| One contract | `schemas/` is shared. A schema change is one PR that updates the producers, consumers and tests together. In separate repos, contracts drift silently. |
| One gate | `make all` and the scorer protect everything at once. A Tier 4 change that breaks a Tier 1 rule fails CI. |
| One stack | `deploy/compose` gains services behind **profiles**, so each project still runs alone (`make demo-vision`, `make demo-edge`). |
| Opus works better | Claude Code sees the real contracts, tests and conventions in one place instead of you pasting them in. |

"Standalone and demoable" is delivered by **profiles, make targets and git tags**, not separate repos:

- **One directory per project**, each with its own README and `make` targets.
- **One compose profile per project**, so `docker compose --profile edge up` starts only what that demo needs.
- **One git tag per finished project** (`t1-scorer-v1`, `t2-vision-lab-v1`, …). Checking out a tag gives a working demo of that stage.
- **One demo script per project** in `docs/demos/`: a five-minute walkthrough you can run for anyone.

Target layout once all tiers are done:

```
pqt/
  CLAUDE.md                 rules for Claude Code (≤100 lines)
  .claude/                  agents, skills, settings (hooks, permissions)
  .devcontainer/            Codespaces toolchain (JDK 21, Maven, Node 22, Python 3.12, Docker)
  schemas/                  THE contract: envelope + data schemas + catalog
  config/                   rules, layouts, noise profiles
  services/
    event-core/             T1, later + tenancy (T3), public API v2 (T3)
    rules-engine/           T1
    tenant-api/             T3 (OpenFGA, hierarchy, onboarding backend)
    billing/                T3
    assistant/              T4
  sim/                      T1 store-sim, T2 noise injection, T4 POS/inventory sims
  scorer/                   T1, extended every tier
  vision/
    lab/                    T2 detection + tracking + homography (GPU-capable)
    noise/                  T2 error measurement
    fusion/                 T2 multi-camera
  edge/agent/               T2 edge-agent, later fleet client (T5)
  analytics/                T4 ClickHouse schemas, rollups, reports
  anomaly/                  T4
  web/ops-console/          one React app; onboarding and admin are routes, not new apps
  deploy/compose/           local stack with profiles
  infra/                    T5 Terraform + Kubernetes
  docs/  plans/ adr/ runbooks/ demos/
```

**Two exceptions** live in the monorepo but run elsewhere:
- **vision-lab training and inference** run on a rented GPU. Same code and Dockerfile; you push the image or clone the repo onto the GPU box.
- **cells and fleet infrastructure** apply to cloud accounts and real devices. The code still lives in `infra/` and `edge/`.

**One decision to make now:** the placeholder name. `pip` collides with Python's package manager, and `com.pip.*`, `schemas.pip.local` and `urn:pip:` are scattered everywhere. Renaming takes an hour today and a week after Tier 3. Pick a product name before Tier 2 and do one mechanical rename PR with Opus.

---

## 3. Tier gates: don't climb until these hold

| Gate | Must be true before moving on |
|---|---|
| **T1 → T2** | `make all` green in Codespaces and in GitHub Actions. Watching `make sim` shows a queue build up and an alert appear (recorded as a Playwright test). Scorer gate in CI with a committed baseline. Tag `v0.1.0`. |
| **T2 → T3** | Queue count within ±1 of manual count on at least 95% of samples across 3 recorded clips. Edge-agent survives a pulled cable with zero loss (automated chaos test). Rules re-scored under the measured noise profile and still above threshold, or thresholds consciously revised in an ADR. |
| **T3 → first pilot** | Cross-tenant isolation test suite at 0 leaks. SSO, RBAC scoped to stores, audit log. Onboarding a new store takes under an hour without you. A DPDP-aligned privacy notice, DPA template and data-retention settings exist. SLO dashboards and on-call runbooks exist. |
| **Pilot → T4** | One real site running for 4+ weeks, with at least 80% of incidents reviewed and measured precision reported to the customer. |
| **T4 → T5** | More than one paying site, or a signed second pilot. Assistant eval at least 90% correct with citations on 100 questions. Analytics reports used by the customer. |

---

## 4. How to build with Opus

### 4.1 Use Claude Code in the Codespace, not chat, for implementation

Tier 1 was written in chat, where the code could not be compiled or run. That is the biggest risk you are carrying right now.

Claude Code in a terminal can run `make`, `mvn`, `docker` and `pytest` itself, read the errors and iterate until things pass. Split the roles like this:

| Where | Use it for |
|---|---|
| **Claude Code (Opus) in the Codespace** | All implementation, debugging, refactors and tests. It must run the gate before it says it is done. |
| **Claude chat** | Architecture discussion, reviewing a plan file, ADRs, learning ("explain watermarks with a drawing"), second opinions on a diff. |
| **Claude Code GitHub Action** (optional, later) | PR review comments against `CLAUDE.md`. |

Docs: https://code.claude.com/docs (CLAUDE.md, plan mode, subagents, hooks, skills, git worktrees).

### 4.2 One-time setup

1. **Devcontainer.** Add `.devcontainer/devcontainer.json` with JDK 21, Maven, Node 22, Python 3.12 and Docker-in-Docker, so Claude can run every test without Docker images (Tier 1, milestone 0.1). Use at least a 4-core / 16 GB Codespace.
2. **`CLAUDE.md` at the repo root.** Use the starter in this folder and keep it short. It holds the rules Claude must follow every session:
   - the gate commands;
   - the contract rules;
   - the privacy rules;
   - the definition of done.
3. **`.claude/agents/`** gets two reviewers from `claude-kit/agents/`:
   - `security-reviewer`: least privilege, secrets, injection, authz, privacy.
   - `contract-guardian`: schema compatibility, event-time correctness, scorer impact.
4. **`.claude/skills/milestone/SKILL.md`** from `claude-kit/`. It is the step-by-step routine for executing one milestone of a plan file.
5. **Permissions.** Allow `make`, `mvn`, `npm`, `pytest`, `docker compose` and `git` without prompts. Keep `git push`, `rm -rf` and anything touching `.env` on ask.

### 4.3 The loop, one milestone per session

```
/clear
→ "Read docs/plans/tier-2-real-vision.md, section vision-lab, milestone M3. Use the milestone skill."
→ Plan mode: Claude explores and proposes a plan. YOU read it and push back.
→ Approve. Claude writes tests first, then code, runs the gate, and fixes until green.
→ "Run the security-reviewer and contract-guardian agents on this diff."
→ You read the diff. For every Learn: item, ask Claude to explain it, then explain it back.
→ Commit (conventional message), tick the milestone in the plan file, write an ADR if a decision was made.
```

**Rules that keep Opus effective on a project this size:**

- **Small, verifiable milestones.** Each plan file breaks projects into milestones with a *Done when* that is a command, not a feeling.
- **Fresh context per milestone.** The plan file is the hand-off. Don't let one session wander across three modules.
- **Make it prove things.** "Paste the output of `make test-fast` and the scorer table" beats "looks done".
- **Stop on scope creep.** `CLAUDE.md` tells Claude to stop and return to plan mode if execution diverges from the approved plan.
- **Ask before adding dependencies.** Check every new dependency's licence. This matters a lot in Tier 2: YOLO from Ultralytics is AGPL-3.0.
- **Use git worktrees for parallel work.** For example, one Claude session on `edge/agent` while another works on `vision/noise`. Never run two sessions on the same module.

### 4.4 Learning, not just shipping

Your ladder lists *Learn:* items. If Opus writes everything, you will own a platform you can't debug at 2 a.m. Use this split:

| Kind of work | Who writes it |
|---|---|
| Plumbing (Dockerfiles, compose, CI, boilerplate, UI layout) | Opus writes; you review. |
| **Core concepts** (watermarks, dedup, homography, tracking association, RLS policies, OTA rollback logic) | **You write the first version** while Opus explains and reviews. Or Opus writes it, and you re-implement a small piece from scratch as an exercise. |
| Tests for core concepts | You write at least one property or edge-case test per concept yourself. |

Each plan file marks these as **🧠 You write**.

### 4.5 Kickoff prompt template

```
Context: monorepo pqt. Read CLAUDE.md, then docs/plans/<file>.md section <project>.
Task: milestone <Mx> only.
Constraints: keep `make test-fast` and the scorer gate green; no new dependency without
asking (state its licence); no contract change without a schema version bump and
contract-guardian review; privacy rules in CLAUDE.md are absolute.
Start in plan mode. List the files you will touch, the tests you will add first, and how
you will prove "Done when". Wait for my approval.
```

---

## 5. Timeline (honest)

These are estimates for a solo builder at about 20 focused hours a week, with Opus doing most of the typing. Halve the hours and the calendar roughly doubles. The ranges are wide because Tier 2 involves real-world uncertainty: video, hardware and model accuracy.

| Stage | Calendar | Notes |
|---|---|---|
| T1: get green + learning milestones | 3–5 weeks | Most of the code exists. The work is making it run, then deepening it. |
| T2: vision-lab | 4–6 weeks | Data collection and labelling dominate. GPU hours mostly go to evaluation, not training. |
| T2: noise-model | 2 weeks | Small once vision-lab produces tracks. |
| T2: edge-agent | 4–6 weeks | Network-failure testing is the real work. |
| T2: mtmc-fusion | 4–8 weeks | The hardest research piece. It is optional before a single-camera pilot. |
| T3: all four projects | 10–14 weeks | Mostly well-trodden enterprise engineering. |
| First pilot | after T3 (≈ 7–10 months in) | You can start pilot *conversations* during T3. |
| T4 | 8–12 weeks | Only after a site is live. |
| T5 | 8–12+ weeks | Only when there are customers to scale for. |

Shortcut worth considering: a **single-camera, single-zone pilot** (queue alerts at one checkout) needs only T1, vision-lab, edge-agent and the tenant-platform plus onboarding slices of T3. mtmc-fusion can wait for the second pilot.

---

## 6. Non-negotiables that span every tier

These already appear in `docs/ARCHITECTURE.md` §12. Repeat them in every plan, because the vision tiers will tempt you to break them.

1. **No person identification, ever.**
   - No face recognition, no demographic inference, no cross-day or cross-site re-identification.
   - ReID embeddings in mtmc-fusion are in-memory, short-lived and only used to stitch tracks within one visit.
2. **Humans decide.** Rules raise incidents; people confirm or dismiss them.
3. **Minimise raw video.**
   - Video stays on the edge in a ring buffer.
   - Clips leave only for a reviewed incident, blurred, with a retention timer.
4. **India data residency and DPDP Act 2023 alignment** from the first pilot:
   - notice and signage templates;
   - a purpose limitation statement;
   - retention controls;
   - breach runbook.
   - Check the current state of the DPDP Rules and get a lawyer's review before the first paid contract. This plan is not legal advice.
5. **Every detector is scored.** If it can't be scored against ground truth, it isn't shipped.
6. **Licences are architecture.**
   - Check model, library and dataset licences before you depend on them.
   - Most public tracking datasets (MOT17/20, CrowdHuman) are non-commercial. Fine for learning and benchmarking, but not for training a product model.
