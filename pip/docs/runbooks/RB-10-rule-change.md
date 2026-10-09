# RB-10: Change a rule in a running stack

Milestone R4, ADR-030. For tuning during a pilot: a threshold, a mode (shadow ↔ enforce), a
severity. Not for grace or R-FOOT-001's window, history or zones: those need a restart.

## Steps

1. Copy the rules file and change it. **Bump the version of every rule you change** (a threshold
   is a MINOR change: 1.0.0 → 1.1.0; a different meaning is MAJOR).
   ```bash
   cd /workspaces/pqtone/pip
   mkdir -p .work/rules && cp config/rules.yaml .work/rules/tuned.yaml && "$EDITOR" .work/rules/tuned.yaml
   ```
2. Publish. The publisher refuses invalid documents, structural changes and missing bumps, and
   prints why.
   ```bash
   make rules-publish RULES=.work/rules/tuned.yaml
   ```
   Output: `{"outcome":"published","sha256":"…","versions":{…},"problems":[]}`.
3. Check it is active everywhere: the heartbeat of every task shows it.
   ```bash
   docker compose --env-file .env -f deploy/compose/docker-compose.yml exec postgres \
     psql -U postgres -d pip -c "SELECT task_id, payload->'rules' FROM pip.pipeline_heartbeat ORDER BY task_id"
   ```
   `source` is `topic` and `versions` shows the new version. New incidents carry it; old ones keep
   theirs.
4. Make it permanent through a PR that changes `config/rules.yaml` the same way (CI checks the
   bump), then go back to the file:
   ```bash
   make rules-reset
   ```

## If something goes wrong

- `"outcome":"refused"`: read `problems`. A missing bump, a structural change, or an invalid
  document. Nothing changed in the running engines.
- An engine refused a document someone produced without the publisher: its log says
  `refused published rules`, and `pip.rules.config.reloads{outcome="refused"}` went up. It kept
  running the previous rules.
- To undo a published change immediately: `make rules-reset` (back to the file).
