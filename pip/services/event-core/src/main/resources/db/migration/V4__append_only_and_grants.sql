-- Append-only enforcement: UPDATE/DELETE (and TRUNCATE on unpartitioned tables) are rejected for every role, including
-- the owner, unless the trigger is explicitly disabled by a DBA (which is itself audited
-- by PostgreSQL logging).
CREATE OR REPLACE FUNCTION pip.reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'table %.% is append-only (% rejected)', TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'insufficient_privilege';
END $$;

CREATE TRIGGER event_append_only        BEFORE UPDATE OR DELETE ON pip.event           FOR EACH ROW EXECUTE FUNCTION pip.reject_mutation();
CREATE TRIGGER event_dedup_append_only  BEFORE UPDATE OR DELETE ON pip.event_dedup     FOR EACH ROW EXECUTE FUNCTION pip.reject_mutation();
CREATE TRIGGER review_append_only       BEFORE UPDATE OR DELETE ON pip.incident_review FOR EACH ROW EXECUTE FUNCTION pip.reject_mutation();
CREATE TRIGGER audit_append_only        BEFORE UPDATE OR DELETE ON pip.audit_log       FOR EACH ROW EXECUTE FUNCTION pip.reject_mutation();
CREATE TRIGGER event_dedup_no_truncate  BEFORE TRUNCATE ON pip.event_dedup     FOR EACH STATEMENT EXECUTE FUNCTION pip.reject_mutation();
CREATE TRIGGER review_no_truncate       BEFORE TRUNCATE ON pip.incident_review FOR EACH STATEMENT EXECUTE FUNCTION pip.reject_mutation();
CREATE TRIGGER audit_no_truncate        BEFORE TRUNCATE ON pip.audit_log       FOR EACH STATEMENT EXECUTE FUNCTION pip.reject_mutation();

-- Least privilege. pip_app: what event-core needs and nothing more.
REVOKE ALL ON ALL TABLES IN SCHEMA pip FROM PUBLIC;
GRANT USAGE ON SCHEMA pip TO pip_app, pip_read;

GRANT SELECT, INSERT ON pip.event_dedup, pip.event, pip.incident_review, pip.audit_log TO pip_app;
GRANT SELECT, INSERT, DELETE ON pip.outbox TO pip_app;
GRANT UPDATE (published_at) ON pip.outbox TO pip_app;
GRANT SELECT, INSERT ON pip.incident TO pip_app;
GRANT UPDATE (status, resolved_at, updated_at, version) ON pip.incident TO pip_app;
GRANT SELECT, INSERT, UPDATE ON pip.pipeline_heartbeat TO pip_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA pip TO pip_app;
GRANT EXECUTE ON FUNCTION pip.ensure_event_partitions(int), pip.drop_event_partitions_older_than(int) TO pip_app;
REVOKE EXECUTE ON FUNCTION pip.ensure_event_partitions(int), pip.drop_event_partitions_older_than(int) FROM PUBLIC;

-- pip_read: scorer and analysts, read-only.
GRANT SELECT ON ALL TABLES IN SCHEMA pip TO pip_read;
ALTER DEFAULT PRIVILEGES IN SCHEMA pip GRANT SELECT ON TABLES TO pip_read;
