-- X1 milestone B3: typed homes for the billing and stock events (ADR-033 contract, ADR-036 storage).
--
-- Additive only: six new tables, nothing existing changes. Every row is a projection of one accepted
-- event in pqt.event, written by event-core in the same transaction as the event (IngestService), so
-- the two never disagree. Like the event store they are:
--   * append-only for every role (a bill is corrected by a new revision, never by an update);
--   * partitioned by day on received_at, and dropped with the same retention as events;
--   * free of personal data: bill, document and payment references are store-keyed HMACs (ADR-033).
--
-- Row-level security is enabled and forced. pqt_app sees and writes only the store named by the
-- transaction-local setting pqt.store_id, which event-core sets per transaction to the one store a
-- request concerns (set_config(..., true)); with no setting, no row is visible. A wrong WHERE clause
-- therefore cannot leak another store's sales. pqt_read (scorer, analysts) reads every store.
-- Tier 3 replaces the store scope with the tenant scope (tenant-platform).

CREATE TABLE pqt.pos_sale_line (
    received_at   timestamptz NOT NULL DEFAULT now(),
    source        text        NOT NULL,
    event_id      text        NOT NULL,
    store_id      text        NOT NULL CHECK (store_id ~ '^[a-z0-9-]{1,64}$'),
    sim_run_id    text,
    event_time    timestamptz NOT NULL,
    till_id       text        NOT NULL CHECK (till_id ~ '^[a-z0-9-]{1,64}$'),
    bill_ref      text        NOT NULL CHECK (bill_ref ~ '^bill-[0-9a-f]{32}$'),
    revision      int         NOT NULL CHECK (revision BETWEEN 0 AND 1000),
    line_no       int         NOT NULL CHECK (line_no BETWEEN 1 AND 10000),
    sku           text        NOT NULL CHECK (sku ~ '^[A-Za-z0-9._-]{1,64}$'),
    qty_milli     bigint      NOT NULL,
    unit          text        NOT NULL,
    amount_minor  bigint      NOT NULL,
    tax_minor     bigint,
    currency      char(3)     NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    PRIMARY KEY (received_at, source, event_id)
) PARTITION BY RANGE (received_at);
CREATE TABLE pqt.pos_sale_line_default PARTITION OF pqt.pos_sale_line DEFAULT;
CREATE INDEX pos_sale_line_time_idx ON pqt.pos_sale_line (store_id, event_time);
CREATE INDEX pos_sale_line_bill_idx ON pqt.pos_sale_line (store_id, bill_ref, revision);

CREATE TABLE pqt.pos_payment (
    received_at   timestamptz NOT NULL DEFAULT now(),
    source        text        NOT NULL,
    event_id      text        NOT NULL,
    store_id      text        NOT NULL CHECK (store_id ~ '^[a-z0-9-]{1,64}$'),
    sim_run_id    text,
    event_time    timestamptz NOT NULL,
    till_id       text        NOT NULL CHECK (till_id ~ '^[a-z0-9-]{1,64}$'),
    bill_ref      text        CHECK (bill_ref ~ '^bill-[0-9a-f]{32}$'),
    payment_no    int         NOT NULL CHECK (payment_no BETWEEN 1 AND 1000),
    tender        text        NOT NULL CHECK (tender IN ('cash', 'card', 'upi', 'wallet', 'credit', 'voucher', 'other')),
    direction     text        NOT NULL CHECK (direction IN ('in', 'out')),
    amount_minor  bigint      NOT NULL CHECK (amount_minor > 0),
    currency      char(3)     NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    PRIMARY KEY (received_at, source, event_id)
) PARTITION BY RANGE (received_at);
CREATE TABLE pqt.pos_payment_default PARTITION OF pqt.pos_payment DEFAULT;
CREATE INDEX pos_payment_time_idx ON pqt.pos_payment (store_id, event_time);

CREATE TABLE pqt.pos_till_event (
    received_at   timestamptz NOT NULL DEFAULT now(),
    source        text        NOT NULL,
    event_id      text        NOT NULL,
    store_id      text        NOT NULL CHECK (store_id ~ '^[a-z0-9-]{1,64}$'),
    sim_run_id    text,
    event_time    timestamptz NOT NULL,
    till_id       text        NOT NULL CHECK (till_id ~ '^[a-z0-9-]{1,64}$'),
    kind          text        NOT NULL CHECK (kind IN ('void', 'refund', 'no_sale', 'price_override', 'drawer_open',
                                                       'shift_open', 'shift_close')),
    bill_ref      text        CHECK (bill_ref ~ '^bill-[0-9a-f]{32}$'),
    revision      int         CHECK (revision BETWEEN 0 AND 1000),
    amount_minor  bigint      CHECK (amount_minor >= 0),
    currency      char(3)     CHECK (currency ~ '^[A-Z]{3}$'),
    PRIMARY KEY (received_at, source, event_id)
) PARTITION BY RANGE (received_at);
CREATE TABLE pqt.pos_till_event_default PARTITION OF pqt.pos_till_event DEFAULT;
CREATE INDEX pos_till_event_time_idx ON pqt.pos_till_event (store_id, event_time);
CREATE INDEX pos_till_event_void_idx ON pqt.pos_till_event (store_id, bill_ref, revision) WHERE kind = 'void';

CREATE TABLE pqt.stock_movement (
    received_at   timestamptz NOT NULL DEFAULT now(),
    source        text        NOT NULL,
    event_id      text        NOT NULL,
    store_id      text        NOT NULL CHECK (store_id ~ '^[a-z0-9-]{1,64}$'),
    sim_run_id    text,
    event_time    timestamptz NOT NULL,
    sku           text        NOT NULL CHECK (sku ~ '^[A-Za-z0-9._-]{1,64}$'),
    direction     text        NOT NULL CHECK (direction IN ('in', 'out')),
    reason        text        NOT NULL CHECK (reason IN ('purchase_receipt', 'sale', 'customer_return', 'supplier_return',
                                                         'transfer_in', 'transfer_out', 'adjustment', 'damage')),
    qty_milli     bigint      NOT NULL CHECK (qty_milli > 0),
    unit          text        NOT NULL,
    doc_ref       text        CHECK (doc_ref ~ '^doc-[0-9a-f]{32}$'),
    line_no       int         CHECK (line_no BETWEEN 1 AND 10000),
    PRIMARY KEY (received_at, source, event_id)
) PARTITION BY RANGE (received_at);
CREATE TABLE pqt.stock_movement_default PARTITION OF pqt.stock_movement DEFAULT;
CREATE INDEX stock_movement_sku_idx ON pqt.stock_movement (store_id, sku, event_time);

CREATE TABLE pqt.stock_level (
    received_at   timestamptz NOT NULL DEFAULT now(),
    source        text        NOT NULL,
    event_id      text        NOT NULL,
    store_id      text        NOT NULL CHECK (store_id ~ '^[a-z0-9-]{1,64}$'),
    sim_run_id    text,
    event_time    timestamptz NOT NULL,
    sku           text        NOT NULL CHECK (sku ~ '^[A-Za-z0-9._-]{1,64}$'),
    qty_milli     bigint      NOT NULL,
    unit          text        NOT NULL,
    reported_by   text        NOT NULL CHECK (reported_by ~ '^[a-z0-9-]{1,32}$'),
    PRIMARY KEY (received_at, source, event_id)
) PARTITION BY RANGE (received_at);
CREATE TABLE pqt.stock_level_default PARTITION OF pqt.stock_level DEFAULT;
CREATE INDEX stock_level_sku_idx ON pqt.stock_level (store_id, sku, event_time);

CREATE TABLE pqt.pos_day_total (
    received_at        timestamptz NOT NULL DEFAULT now(),
    source             text        NOT NULL,
    event_id           text        NOT NULL,
    store_id           text        NOT NULL CHECK (store_id ~ '^[a-z0-9-]{1,64}$'),
    sim_run_id         text,
    event_time         timestamptz NOT NULL,
    business_date      date        NOT NULL,
    sales_minor        bigint      NOT NULL CHECK (sales_minor >= 0),
    refunds_minor      bigint      NOT NULL CHECK (refunds_minor >= 0),
    bills              int         NOT NULL CHECK (bills >= 0),
    payments_by_tender jsonb       NOT NULL CHECK (jsonb_typeof(payments_by_tender) = 'object'),
    currency           char(3)     NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    reported_by        text        NOT NULL CHECK (reported_by ~ '^[a-z0-9-]{1,32}$'),
    PRIMARY KEY (received_at, source, event_id)
) PARTITION BY RANGE (received_at);
CREATE TABLE pqt.pos_day_total_default PARTITION OF pqt.pos_day_total DEFAULT;
CREATE INDEX pos_day_total_date_idx ON pqt.pos_day_total (store_id, business_date);

-- Append-only, store-scoped, least privilege: the same treatment for all six.
DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['pos_sale_line', 'pos_payment', 'pos_till_event', 'stock_movement', 'stock_level',
                             'pos_day_total'] LOOP
        EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON pqt.%I FOR EACH ROW EXECUTE FUNCTION pqt.reject_mutation()',
                       t || '_append_only', t);
        EXECUTE format('ALTER TABLE pqt.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE pqt.%I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format($p$CREATE POLICY %I ON pqt.%I TO pqt_app
                          USING (store_id = current_setting('pqt.store_id', true))
                          WITH CHECK (store_id = current_setting('pqt.store_id', true))$p$, t || '_store_scope', t);
        EXECUTE format('CREATE POLICY %I ON pqt.%I FOR SELECT TO pqt_read USING (true)', t || '_read_all', t);
        EXECUTE format('REVOKE ALL ON pqt.%I FROM PUBLIC', t);
        EXECUTE format('GRANT SELECT, INSERT ON pqt.%I TO pqt_app', t);
        EXECUTE format('GRANT SELECT ON pqt.%I TO pqt_read', t);
    END LOOP;
END $$;

-- Daily partitions, created ahead and dropped with the event retention (PartitionMaintenance).
CREATE OR REPLACE FUNCTION pqt.ensure_billing_partitions(days_ahead int DEFAULT 7)
RETURNS int LANGUAGE plpgsql SECURITY DEFINER SET search_path = pqt, pg_temp AS $$
DECLARE
    t       text;
    d       date;
    pname   text;
    created int := 0;
BEGIN
    IF days_ahead < 0 OR days_ahead > 60 THEN
        RAISE EXCEPTION 'days_ahead out of range: %', days_ahead;
    END IF;
    FOREACH t IN ARRAY ARRAY['pos_sale_line', 'pos_payment', 'pos_till_event', 'stock_movement', 'stock_level',
                             'pos_day_total'] LOOP
        FOR d IN SELECT generate_series(current_date - 1, current_date + days_ahead, interval '1 day')::date LOOP
            pname := format('%s_p%s', t, to_char(d, 'YYYYMMDD'));
            IF to_regclass('pqt.' || pname) IS NULL THEN
                EXECUTE format('CREATE TABLE pqt.%I PARTITION OF pqt.%I FOR VALUES FROM (%L) TO (%L)',
                               pname, t, d::timestamptz, (d + 1)::timestamptz);
                created := created + 1;
            END IF;
        END LOOP;
    END LOOP;
    RETURN created;
END $$;

CREATE OR REPLACE FUNCTION pqt.drop_billing_partitions_older_than(retain_days int)
RETURNS int LANGUAGE plpgsql SECURITY DEFINER SET search_path = pqt, pg_temp AS $$
DECLARE
    t       text;
    r       record;
    dropped int := 0;
    cutoff  date := current_date - retain_days;
BEGIN
    IF retain_days < 1 THEN
        RAISE EXCEPTION 'retain_days must be >= 1';
    END IF;
    FOREACH t IN ARRAY ARRAY['pos_sale_line', 'pos_payment', 'pos_till_event', 'stock_movement', 'stock_level',
                             'pos_day_total'] LOOP
        FOR r IN SELECT c.relname FROM pg_inherits i
                   JOIN pg_class c ON c.oid = i.inhrelid
                   JOIN pg_class p ON p.oid = i.inhparent
                   JOIN pg_namespace n ON n.oid = p.relnamespace
                  WHERE n.nspname = 'pqt' AND p.relname = t AND c.relname ~ ('^' || t || '_p[0-9]{8}$') LOOP
            IF to_date(right(r.relname, 8), 'YYYYMMDD') < cutoff THEN
                EXECUTE format('DROP TABLE pqt.%I', r.relname);
                dropped := dropped + 1;
            END IF;
        END LOOP;
    END LOOP;
    RETURN dropped;
END $$;

REVOKE EXECUTE ON FUNCTION pqt.ensure_billing_partitions(int), pqt.drop_billing_partitions_older_than(int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION pqt.ensure_billing_partitions(int), pqt.drop_billing_partitions_older_than(int) TO pqt_app;

SELECT pqt.ensure_billing_partitions(7);
