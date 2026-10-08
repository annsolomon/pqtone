CREATE TYPE pip.incident_status AS ENUM
    ('OPEN', 'ACKNOWLEDGED', 'CONFIRMED', 'DISMISSED', 'AUTO_RESOLVED', 'CLOSED');

CREATE TABLE pip.incident (
    incident_id   uuid        PRIMARY KEY,
    rule_id       text        NOT NULL,
    rule_version  text        NOT NULL,
    mode          text        NOT NULL CHECK (mode IN ('enforce', 'shadow')),
    store_id      text        NOT NULL,
    sim_run_id    text,
    incident_key  text        NOT NULL,
    subject       text,
    severity      text        NOT NULL CHECK (severity IN ('low', 'medium', 'high')),
    summary       text        NOT NULL,
    onset_at      timestamptz NOT NULL,
    detected_at   timestamptz NOT NULL,
    resolved_at   timestamptz,
    status        pip.incident_status NOT NULL DEFAULT 'OPEN',
    evidence      jsonb       NOT NULL DEFAULT '[]'::jsonb,
    attrs         jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    version       int         NOT NULL DEFAULT 1
);
CREATE INDEX incident_queue_idx ON pip.incident (mode, status, detected_at DESC);
CREATE INDEX incident_store_idx ON pip.incident (store_id, detected_at DESC);
CREATE INDEX incident_run_idx   ON pip.incident (sim_run_id) WHERE sim_run_id IS NOT NULL;

CREATE TABLE pip.incident_review (
    review_id     bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incident_id   uuid        NOT NULL REFERENCES pip.incident (incident_id),
    action        text        NOT NULL CHECK (action IN ('ack', 'confirm', 'dismiss', 'close')),
    from_status   pip.incident_status NOT NULL,
    to_status     pip.incident_status NOT NULL,
    reason_code   text,
    note          text        CHECK (note IS NULL OR length(note) <= 500),
    actor         text        NOT NULL,
    acted_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX incident_review_incident_idx ON pip.incident_review (incident_id, acted_at);

CREATE TABLE pip.pipeline_heartbeat (
    task_id      text        PRIMARY KEY,
    received_at  timestamptz NOT NULL,
    payload      jsonb       NOT NULL
);
