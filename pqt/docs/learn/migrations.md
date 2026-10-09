# Schema migrations: expand, migrate, contract

Milestone E3. How the database schema changes without downtime and without a rollback script.

## The rules

- **Flyway, forward-only.** Files in `services/event-core/src/main/resources/db/migration`,
  named `V<n>__<what>.sql`, applied in order by the `flyway` compose service as `pqt_migrator`
  (the schema owner). A migration that has run is never edited; a mistake is fixed by the next one.
- **Least privilege.** event-core runs as `pqt_app`, which owns nothing and cannot run DDL.
  Every new table or column comes with exactly the grants the application needs.
- **Expand → migrate → contract.** A change that would break running code is split so that
  at every moment both the old and the new version of event-core work against the schema.

| Step | What | Example |
|---|---|---|
| **Expand** | Add only. New nullable columns, new tables, new indexes. Nothing existing changes meaning. | `V5__incident_assignee.sql`: `assignee text NULL`, a column grant, an index |
| **Migrate** | Ship code that uses the new shape; backfill old rows if needed, in batches. Old and new code both run fine. | The console starts showing and setting `assignee` |
| **Contract** | Only when no running version needs the old shape: tighten (`NOT NULL`, constraints) or drop what was replaced, in a new migration. | (Not needed for `assignee`: unassigned is a valid state) |

A rename, for example, is never one migration: add the new column (expand), write both and read
the new one (migrate), drop the old one (contract).

## V5, line by line

```sql
ALTER TABLE pqt.incident ADD COLUMN IF NOT EXISTS assignee text CHECK (...);
```
Nullable with no default: PostgreSQL only updates the catalog, so the table is not rewritten and
the lock is held for milliseconds. A `DEFAULT` with a volatile expression, or `NOT NULL` on a
populated table, would rewrite or scan it.

```sql
GRANT UPDATE (assignee) ON pqt.incident TO pqt_app;
```
A column grant: `pqt_app` can assign incidents but still cannot change `rule_id`, `summary` and
the rest (`MigrationsIT` checks both).

```sql
CREATE INDEX CONCURRENTLY IF NOT EXISTS incident_assignee_idx ... WHERE assignee IS NOT NULL;
```
`CONCURRENTLY` builds the index without blocking writes, but cannot run inside a transaction.
That is why `V5__incident_assignee.sql.conf` sets `executeInTransaction=false`, and why every
statement in the file is idempotent (`IF NOT EXISTS`): if the migration fails half way, running
it again finishes the job.

One more trap: `CONCURRENTLY` waits until every transaction that was open when it started has
finished, in any table. Flyway's default PostgreSQL lock is a *transactional* advisory lock, which
keeps a transaction open for the whole run, so the index build waits on Flyway itself and never
finishes. Both places that run Flyway (the compose `flyway` service and the integration tests'
`Db.migrate`) therefore set `postgresql.transactional.lock=false` (a session-level lock), and
`SET lock_timeout = '60s'` so any other blocked wait fails fast instead of hanging a deploy.
`MigrationsIT` has a timeout for the same reason.

## Rollback note

There is no down-migration, on purpose.

- **Rolling the application back** needs nothing in the database: the expand step only added
  things that older code ignores. That is the point of expanding first.
- **If V5 fails half way** (it runs outside a transaction): fix the cause and run `flyway
  migrate` again; the `IF NOT EXISTS` statements skip what was done. One trap: a failed
  `CREATE INDEX CONCURRENTLY` can leave an **invalid** index with the right name, and
  `IF NOT EXISTS` would then skip it. Check and rebuild:

  ```sql
  SELECT c.relname, i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
   WHERE c.relname = 'incident_assignee_idx';
  -- if indisvalid is false:
  DROP INDEX CONCURRENTLY pqt.incident_assignee_idx;   -- then run flyway migrate again
  ```
  Then `flyway repair` if Flyway recorded the failed attempt.
- **If the column itself must go** (a real mistake, not a deploy rollback): write a new
  migration, `V<n>__drop_incident_assignee.sql`, after every running event-core version has
  stopped using it. That is a contract step like any other.

## How it is tested

`services/event-core/src/test/java/com/pqt/eventcore/it/MigrationsIT.java`, part of
`make test-it` and `make all`:

- an empty database migrates to the latest version, the index is valid, the grants are exactly
  as intended, and a second `migrate` is a no-op;
- a database at **V4** with events and an incident written by the V4-era write path migrates
  to V5, keeps every row, leaves existing incidents unassigned, lets `pqt_app` set `assignee`
  (and nothing else), enforces the length check, and the old write path still works.
