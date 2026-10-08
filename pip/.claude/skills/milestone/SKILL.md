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
