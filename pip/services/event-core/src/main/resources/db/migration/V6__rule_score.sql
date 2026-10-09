-- Milestone R6: the live (e2e) score of every rule, recorded by pip-scorer after each run, so admins
-- can see how a shadow rule performs before anyone proposes promoting it to enforce.
--
-- Additive only (a new table). Append-only like the other evidence tables: a score is a measurement
-- of one run and is never edited. One row per (run, rule); re-scoring the same run inserts nothing.
-- The gate thresholds that applied are stored with each row, so the console can show readiness
-- without reading scorer/thresholds.yaml.

CREATE TABLE pip.rule_score (
    score_id        bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    measured_at     timestamptz NOT NULL DEFAULT now(),
    source          text        NOT NULL CHECK (source IN ('e2e')),
    sim_run_id      text        NOT NULL CHECK (length(sim_run_id) BETWEEN 1 AND 64),
    scenario        text        NOT NULL CHECK (length(scenario) BETWEEN 1 AND 64),
    rule_id         text        NOT NULL CHECK (length(rule_id) BETWEEN 1 AND 64),
    rule_version    text        NOT NULL CHECK (rule_version ~ '^[0-9]+\.[0-9]+\.[0-9]+$'),
    mode            text        NOT NULL CHECK (mode IN ('enforce', 'shadow')),
    tp              int         NOT NULL CHECK (tp >= 0),
    fp              int         NOT NULL CHECK (fp >= 0),
    fn              int         NOT NULL CHECK (fn >= 0),
    latency_p95_ms  bigint      CHECK (latency_p95_ms IS NULL OR latency_p95_ms >= 0),
    precision_min   double precision NOT NULL CHECK (precision_min BETWEEN 0 AND 1),
    recall_min      double precision NOT NULL CHECK (recall_min BETWEEN 0 AND 1),
    min_n           int         CHECK (min_n IS NULL OR min_n > 0),
    min_lower_bound double precision CHECK (min_lower_bound IS NULL OR min_lower_bound BETWEEN 0 AND 1),
    UNIQUE (sim_run_id, rule_id)
);
CREATE INDEX rule_score_rule_idx ON pip.rule_score (rule_id, rule_version, measured_at DESC);

CREATE TRIGGER rule_score_append_only BEFORE UPDATE OR DELETE ON pip.rule_score
    FOR EACH ROW EXECUTE FUNCTION pip.reject_mutation();
CREATE TRIGGER rule_score_no_truncate BEFORE TRUNCATE ON pip.rule_score
    FOR EACH STATEMENT EXECUTE FUNCTION pip.reject_mutation();

-- Least privilege: the scorer records through pip_app (insert only, no update/delete); event-core reads.
GRANT SELECT, INSERT ON pip.rule_score TO pip_app;
GRANT USAGE, SELECT ON SEQUENCE pip.rule_score_score_id_seq TO pip_app;
GRANT SELECT ON pip.rule_score TO pip_read;
