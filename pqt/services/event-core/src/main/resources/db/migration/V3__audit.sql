CREATE TABLE pqt.audit_log (
    seq        bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at         timestamptz NOT NULL,
    actor      text        NOT NULL,
    action     text        NOT NULL,
    target     text        NOT NULL,
    details    jsonb       NOT NULL DEFAULT '{}'::jsonb,
    prev_hash  char(64)    NOT NULL,
    row_hash   char(64)    NOT NULL UNIQUE
);
COMMENT ON TABLE pqt.audit_log IS
  'Append-only, hash-chained. row_hash = sha256(canonical{prev, at(micros), actor, action, target, details}).';
