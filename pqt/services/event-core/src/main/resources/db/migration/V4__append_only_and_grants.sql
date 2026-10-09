-- Append-only enforcement: UPDATE/DELETE (and TRUNCATE on unpartitioned tables) are rejected for every role, including
-- the owner, unless the trigger is explicitly disabled by a DBA (which is itself audited
-- by PostgreSQL logging).
CREATE OR REPLACE FUNCTION pqt.reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'table %.% is append-only (% rejected)', TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'insufficient_privilege';
END $$;

CREATE TRIGGER event_append_only        BEFORE UPDATE OR DELETE ON pqt.event           FOR EACH ROW EXECUTE FUNCTION pqt.reject_mutation();
CREATE TRIGGER event_dedup_append_only  BEFORE UPDATE OR DELETE ON pqt.event_dedup     FOR EACH ROW EXECUTE FUNCTION pqt.reject_mutation();
CREATE TRIGGER review_append_only       BEFORE UPDATE OR DELETE ON pqt.incident_review FOR EACH ROW EXECUTE FUNCTION pqt.reject_mutation();
CREATE TRIGGER audit_append_only        BEFORE UPDATE OR DELETE ON pqt.audit_log       FOR EACH ROW EXECUTE FUNCTION pqt.reject_mutation();
CREATE TRIGGER event_dedup_no_truncate  BEFORE TRUNCATE ON pqt.event_dedup     FOR EACH STATEMENT EXECUTE FUNCTION pqt.reject_mutation();
CREATE TRIGGER review_no_truncate       BEFORE TRUNCATE ON pqt.incident_review FOR EACH STATEMENT EXECUTE FUNCTION pqt.reject_mutation();
CREATE TRIGGER audit_no_truncate        BEFORE TRUNCATE ON pqt.audit_log       FOR EACH STATEMENT EXECUTE FUNCTION pqt.reject_mutation();

-- Least privilege. pqt_app: what event-core needs and nothing more.
REVOKE ALL ON ALL TABLES IN SCHEMA pqt FROM PUBLIC;
GRANT USAGE ON SCHEMA pqt TO pqt_app, pqt_read;

GRANT SELECT, INSERT ON pqt.event_dedup, pqt.event, pqt.incident_review, pqt.audit_log TO pqt_app;
GRANT SELECT, INSERT, DELETE ON pqt.outbox TO pqt_app;
GRANT UPDATE (published_at) ON pqt.outbox TO pqt_app;
GRANT SELECT, INSERT ON pqt.incident TO pqt_app;
GRANT UPDATE (status, resolved_at, updated_at, version) ON pqt.incident TO pqt_app;
GRANT SELECT, INSERT, UPDATE ON pqt.pipeline_heartbeat TO pqt_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA pqt TO pqt_app;
GRANT EXECUTE ON FUNCTION pqt.ensure_event_partitions(int), pqt.drop_event_partitions_older_than(int) TO pqt_app;
REVOKE EXECUTE ON FUNCTION pqt.ensure_event_partitions(int), pqt.drop_event_partitions_older_than(int) FROM PUBLIC;

-- pqt_read: scorer and analysts, read-only.
GRANT SELECT ON ALL TABLES IN SCHEMA pqt TO pqt_read;
ALTER DEFAULT PRIVILEGES IN SCHEMA pqt GRANT SELECT ON TABLES TO pqt_read;
