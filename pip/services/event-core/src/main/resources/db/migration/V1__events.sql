-- Event store. Run by Flyway as pip_migrator (schema owner). The application role
-- pip_app never owns objects and never runs DDL except via SECURITY DEFINER helpers.

CREATE TABLE pip.event_dedup (
    source          text        NOT NULL,
    id              text        NOT NULL,
    payload_sha256  char(64)    NOT NULL,
    first_seen_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source, id)
);
COMMENT ON TABLE pip.event_dedup IS
  'Idempotency ledger keyed by CloudEvents (source, id). Kept separate from the partitioned '
  'event table because a unique constraint on a partitioned table must include the partition key.';

CREATE TABLE pip.event (
    event_seq     bigint      GENERATED ALWAYS AS IDENTITY,
    received_at   timestamptz NOT NULL DEFAULT now(),
    source        text        NOT NULL,
    id            text        NOT NULL,
    type          text        NOT NULL,
    event_time    timestamptz NOT NULL,
    subject       text,
    store_id      text        NOT NULL,
    sim_run_id    text,
    dataschema    text        NOT NULL,
    data          jsonb       NOT NULL,
    traceparent   text,
    channel       text        NOT NULL CHECK (channel IN ('http', 'kafka')),
    producer      text        NOT NULL,
    PRIMARY KEY (received_at, event_seq)
) PARTITION BY RANGE (received_at);

CREATE TABLE pip.event_default PARTITION OF pip.event DEFAULT;

CREATE INDEX event_store_time_idx ON pip.event (store_id, event_time);
CREATE INDEX event_run_idx        ON pip.event (sim_run_id) WHERE sim_run_id IS NOT NULL;
CREATE INDEX event_type_idx       ON pip.event (store_id, type, event_time);

CREATE OR REPLACE FUNCTION pip.ensure_event_partitions(days_ahead int DEFAULT 7)
RETURNS int LANGUAGE plpgsql SECURITY DEFINER SET search_path = pip, pg_temp AS $$
DECLARE
    d       date;
    pname   text;
    created int := 0;
BEGIN
    IF days_ahead < 0 OR days_ahead > 60 THEN
        RAISE EXCEPTION 'days_ahead out of range: %', days_ahead;
    END IF;
    FOR d IN SELECT generate_series(current_date - 1, current_date + days_ahead, interval '1 day')::date LOOP
        pname := format('event_p%s', to_char(d, 'YYYYMMDD'));
        IF to_regclass('pip.' || pname) IS NULL THEN
            EXECUTE format('CREATE TABLE pip.%I PARTITION OF pip.event FOR VALUES FROM (%L) TO (%L)',
                           pname, d::timestamptz, (d + 1)::timestamptz);
            created := created + 1;
        END IF;
    END LOOP;
    RETURN created;
END $$;

CREATE OR REPLACE FUNCTION pip.drop_event_partitions_older_than(retain_days int)
RETURNS int LANGUAGE plpgsql SECURITY DEFINER SET search_path = pip, pg_temp AS $$
DECLARE
    r       record;
    dropped int := 0;
    cutoff  date := current_date - retain_days;
BEGIN
    IF retain_days < 1 THEN
        RAISE EXCEPTION 'retain_days must be >= 1';
    END IF;
    FOR r IN SELECT c.relname FROM pg_inherits i
               JOIN pg_class c ON c.oid = i.inhrelid
               JOIN pg_class p ON p.oid = i.inhparent
              WHERE p.relname = 'event' AND c.relname ~ '^event_p[0-9]{8}$' LOOP
        IF to_date(substr(r.relname, 8), 'YYYYMMDD') < cutoff THEN
            EXECUTE format('DROP TABLE pip.%I', r.relname);
            dropped := dropped + 1;
        END IF;
    END LOOP;
    RETURN dropped;
END $$;

SELECT pip.ensure_event_partitions(7);

CREATE TABLE pip.outbox (
    outbox_id     bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at    timestamptz NOT NULL DEFAULT now(),
    topic         text        NOT NULL,
    msg_key       text        NOT NULL,
    payload       text        NOT NULL,
    headers       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    published_at  timestamptz
);
CREATE INDEX outbox_unpublished_idx ON pip.outbox (outbox_id) WHERE published_at IS NULL;
