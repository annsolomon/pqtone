-- Milestone E3: who is handling an incident. EXPAND step of expand -> migrate -> contract.
--
-- expand   (this file): add a NULLABLE column with no default, so the ALTER is a catalog-only
--                       change (no table rewrite, no long lock) and every running event-core keeps
--                       working because none of its queries mention the column.
-- migrate  (later):     ship code that reads and writes assignee; backfill if ever needed, in batches.
-- contract (later):     only after no running version depends on the old shape, tighten it
--                       (e.g. NOT NULL) or drop what it replaced, in its own migration.
--
-- Runs outside a transaction (V5__incident_assignee.sql.conf) because CREATE INDEX CONCURRENTLY
-- cannot run inside one. Every statement is idempotent, so a failed run can simply be retried.
-- Rollback: see docs/learn/migrations.md. Migrations are forward-only; nothing here needs undoing
-- to roll the application back, because older code ignores the column.

ALTER TABLE pip.incident ADD COLUMN IF NOT EXISTS assignee text
    CHECK (assignee IS NULL OR (length(assignee) BETWEEN 1 AND 128));

COMMENT ON COLUMN pip.incident.assignee IS
  'Console username of the person handling the incident. NULL = unassigned. Never a customer or shopper.';

-- Least privilege: pip_app may set or clear the assignee, nothing more (column-level grant).
GRANT UPDATE (assignee) ON pip.incident TO pip_app;

-- "My open incidents" for the console; partial, so unassigned rows cost nothing.
CREATE INDEX CONCURRENTLY IF NOT EXISTS incident_assignee_idx
    ON pip.incident (assignee, status, detected_at DESC)
    WHERE assignee IS NOT NULL;
