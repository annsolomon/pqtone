"""Database guarantees that must hold regardless of application code."""
import os

import psycopg
import pytest

APP = os.environ["PIP_DB_APP_DSN"]
READ = os.environ["PIP_SCORER_DSN"]


def run(dsn, sql):
    with psycopg.connect(dsn, autocommit=True) as c:
        return c.execute(sql).fetchall() if sql.lstrip().upper().startswith("SELECT") else c.execute(sql)


def test_connections_are_tls():
    with psycopg.connect(APP) as c:
        assert c.execute("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()").fetchone()[0] is True


def test_plaintext_connections_are_refused():
    plain = APP.replace("sslmode=verify-full", "sslmode=disable")
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(plain, connect_timeout=5)


@pytest.mark.parametrize("sql", [
    "UPDATE pip.event SET subject = 'x' WHERE false",
    "DELETE FROM pip.event WHERE false",
    "UPDATE pip.event_dedup SET payload_sha256 = payload_sha256 WHERE false",
    "DELETE FROM pip.audit_log WHERE false",
    "UPDATE pip.audit_log SET actor = 'x' WHERE false",
    "DELETE FROM pip.incident_review WHERE false",
    "UPDATE pip.incident SET rule_id = 'x' WHERE false",
    "DELETE FROM pip.incident WHERE false",
    "CREATE TABLE pip.sneaky (x int)",
    "DROP TABLE pip.event_default",
])
def test_app_role_cannot_rewrite_history_or_change_schema(sql):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        run(APP, sql)


def test_append_only_triggers_are_installed():
    """Triggers reject UPDATE/DELETE for every role, including the schema owner."""
    names = {r[0] for r in run(READ, "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")}
    assert {"event_append_only", "event_dedup_append_only", "review_append_only", "audit_append_only"} <= names


def test_read_role_is_read_only():
    with pytest.raises((psycopg.errors.InsufficientPrivilege, psycopg.errors.ReadOnlySqlTransaction)):
        run(READ, "INSERT INTO pip.audit_log (at, actor, action, target, prev_hash, row_hash) "
                  "VALUES (now(), 'x', 'x', 'x', repeat('0', 64), repeat('1', 64))")


def test_events_were_stored_and_deduplicated():
    total, distinct = run(READ, "SELECT count(*), count(DISTINCT (source, id)) FROM pip.event")[0]
    assert total > 0 and total == distinct
