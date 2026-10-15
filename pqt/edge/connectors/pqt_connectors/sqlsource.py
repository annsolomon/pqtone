"""The database source (X1 B5c, ADR-037): poll a read-only query against the billing system's database.

Per record kind, a query returns rows changed after a cursor (`WHERE modified_at > :cursor ORDER BY
modified_at`); the profile maps its columns. The cursor is the largest value of the `cursor` column read so
far, stored beside the importer's state and advanced only after the outbox took the events, so a restart
neither skips nor repeats anything. `lookback` re-reads a window before the cursor to catch rows committed
late with an earlier stamp (re-reading adds nothing). For bills, the query must return every line of each
bill that changed (put the cursor on the bill header): a bill is compared as a whole.

Read-only, twice over: the statement must be one SELECT (or WITH ... SELECT) with the `:cursor` parameter and
no writing keyword outside strings and comments, and the connection itself is opened read-only (SQLite
`mode=ro` + `query_only`; PostgreSQL `default_transaction_read_only` and a statement timeout; MySQL a
read-only session; SQL Server `ApplicationIntent=ReadOnly`). Give the connector a database user that can
only read the tables or views it needs.

Paging: `maxRows` caps one poll; a page never ends in the middle of a cursor value (all rows sharing the last
value wait for the next poll), so a bill is never split. Rows the importer reports (a bad line) hold the
cursor just before them: nothing is skipped silently, and once the row is fixed (or edited, which moves its
cursor value) it is read again.

Drivers: SQLite (standard library); PostgreSQL `psycopg` (LGPL-3.0), MySQL/MariaDB `PyMySQL` (MIT) and SQL
Server `pyodbc` (MIT-0) are optional installs (`pip install pqt-connectors[postgresql]` etc.).
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from . import profile as profile_mod
from .importer import Importer
from .profile import KINDS, ORDER, Profile
from .secrets import resolve as resolve_secret

DRIVERS = {"sqlite": "named", "postgresql": "pyformat", "mysql": "pyformat", "sqlserver": "qmark"}
# Words that can make a SELECT-led statement write (data-modifying CTEs, SELECT INTO, locking reads, file output).
# Commands that cannot start with SELECT or WITH are refused by the first-word rule.
WRITING = {"INSERT", "UPDATE", "DELETE", "MERGE", "UPSERT", "DROP", "ALTER", "CREATE", "TRUNCATE", "GRANT", "REVOKE",
           "EXEC", "EXECUTE", "CALL", "COPY", "INTO", "LOCK", "ATTACH", "DETACH", "PRAGMA", "OUTFILE", "DUMPFILE"}
DEFAULT_MAX_ROWS = 10_000
_DURATION = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


def _scan(sql: str, on_code: Callable[[str], str]) -> str:
    """Rebuild `sql` passing only code (not string literals, quoted identifiers or comments) through `on_code`."""
    out, i, n, code = [], 0, len(sql), []

    def flush():
        if code:
            out.append(on_code("".join(code)))
            code.clear()
    while i < n:
        c = sql[i]
        if c in "'\"`[":
            flush()
            close = {"'": "'", '"': '"', "`": "`", "[": "]"}[c]
            j = i + 1
            while j < n:
                if sql[j] == close:
                    if close in "'\"" and j + 1 < n and sql[j + 1] == close:  # doubled quote inside a literal
                        j += 2
                        continue
                    break
                j += 1
            out.append(sql[i:j + 1])
            i = j + 1
        elif sql.startswith("--", i):
            flush()
            j = sql.find("\n", i)
            j = n if j < 0 else j
            out.append(" ")
            i = j
        elif sql.startswith("/*", i):
            flush()
            j = sql.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(" ")
            i = j
        else:
            code.append(c)
            i += 1
    flush()
    return "".join(out)


def check_sql(sql: str) -> None:
    """Refuse anything but one SELECT with the :cursor parameter (ValueError)."""
    pieces: list[str] = []
    _scan(sql, lambda code: pieces.append(code) or code)
    code = " ".join(pieces).strip()
    code = code[:-1].rstrip() if code.endswith(";") else code
    if ";" in code:
        raise ValueError("only one statement is allowed")
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", code)
    if not words or words[0].upper() not in ("SELECT", "WITH"):
        raise ValueError("the query must be a SELECT (or WITH ... SELECT)")
    bad = sorted({w.upper() for w in words} & WRITING)
    if bad:
        raise ValueError(f"the query may only read: {', '.join(bad)} is not allowed")
    if not re.search(r":cursor\b", code):
        raise ValueError("the query must use the :cursor parameter (WHERE <cursor column> > :cursor)")


def bind(sql: str, value: Any, style: str) -> tuple[str, Any]:
    """`:cursor` in code -> the driver's placeholder; returns (sql, parameters)."""
    count = [0]
    if style in ("pyformat", "format"):
        sql = sql.replace("%", "%%")  # literal percent signs must be doubled when parameters are passed
    placeholder = {"qmark": "?", "named": ":cursor", "pyformat": "%(cursor)s", "format": "%s"}[style]

    def sub(code: str) -> str:
        def rep(m):
            count[0] += 1
            return placeholder
        return re.sub(r":cursor\b", rep, code)
    out = _scan(sql, sub)
    if style in ("qmark", "format"):
        return out, tuple([value] * count[0])
    return out, {"cursor": value}


def _duration(text: str) -> timedelta:
    m = _DURATION.match(str(text))
    if not m or not any(m.groups()) and str(text) not in ("PT0S", "P0D"):
        raise ValueError(f"lookback {text!r} is not an ISO 8601 duration such as PT10M")
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return timedelta(days=d, hours=h, minutes=mi, seconds=s)


def _encode(v: Any) -> str:
    if isinstance(v, bool):
        raise ValueError("a cursor cannot be a boolean")
    if isinstance(v, int):
        return json.dumps({"int": v})
    if isinstance(v, Decimal):
        return json.dumps({"decimal": str(v)})
    if isinstance(v, datetime):
        return json.dumps({"datetime": v.isoformat()})
    return json.dumps({"str": str(v)})


def _decode(s: str) -> Any:
    (kind, v), = json.loads(s).items()
    return {"int": int, "decimal": Decimal, "datetime": datetime.fromisoformat, "str": str}[kind](v)


def _minus(value: Any, lookback: Any) -> Any:
    if not lookback:
        return value
    if isinstance(value, datetime):
        return value - _duration(lookback)
    if isinstance(value, (int, Decimal)):
        if isinstance(lookback, str) and lookback.startswith("P"):
            raise ValueError("a numeric cursor takes a numeric lookback")
        return value - type(value)(lookback)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            raise ValueError(f"lookback needs a date-time or numeric cursor, got text {value!r}") from None
        back = dt - _duration(lookback)
        return back.isoformat(sep="T" if "T" in value else " ", timespec="microseconds" if "." in value else "seconds")
    raise ValueError(f"cannot apply a lookback to a {type(value).__name__} cursor")


@dataclass
class Query:
    kind: str
    sql: str
    cursor: str
    start: Any
    lookback: Any = None
    max_rows: int = DEFAULT_MAX_ROWS


class SqlSource:
    def __init__(self, source_id: str, profile: Profile, driver: str, dsn: str, queries: list[Query],
                 timeout_s: int = 60) -> None:
        self.id, self.profile, self.driver, self.dsn, self.queries, self.timeout_s = source_id, profile, driver, dsn, queries, timeout_s

    @classmethod
    def from_config(cls, cfg: dict, *, base: str | Path = ".") -> "SqlSource":
        base = Path(base)
        unknown = set(cfg) - {"id", "type", "profile", "driver", "dsn", "queries", "interval", "timeoutSeconds"}
        if unknown:
            raise ValueError(f"sql source: unknown key(s) {sorted(unknown)}")
        sid = str(cfg.get("id") or "")
        if not re.fullmatch(r"[a-z0-9-]{1,64}", sid):
            raise ValueError("sql source: id must match [a-z0-9-]{1,64}")
        driver = cfg.get("driver")
        if driver not in DRIVERS:
            raise ValueError(f"sql source {sid}: driver must be one of {sorted(DRIVERS)}")
        dsn = str(cfg.get("dsn") or "")
        if driver == "sqlite":
            if dsn.startswith(("env:", "file:")):
                dsn = resolve_secret(dsn)
            dsn = str(Path(dsn) if Path(dsn).is_absolute() else base / dsn)
        else:
            if not dsn.startswith(("env:", "file:")):
                raise ValueError(f"sql source {sid}: the dsn holds a password: give it as env:NAME or file:/path")
            dsn = resolve_secret(dsn)
        ppath = Path(cfg.get("profile", ""))
        prof = profile_mod.load(ppath if ppath.is_absolute() else base / ppath)
        queries = []
        for kind, q in (cfg.get("queries") or {}).items():
            if kind not in KINDS:
                raise ValueError(f"sql source {sid}: unknown kind {kind}")
            if kind not in prof.columns:
                raise ValueError(f"sql source {sid}: the profile maps no {kind}")
            if set(q) - {"sql", "cursor", "start", "lookback", "maxRows"}:
                raise ValueError(f"sql source {sid}.{kind}: unknown key(s) {sorted(set(q) - {'sql', 'cursor', 'start', 'lookback', 'maxRows'})}")
            check_sql(q.get("sql") or "")
            if not q.get("cursor") or "start" not in q:
                raise ValueError(f"sql source {sid}.{kind}: needs cursor (a column) and start (the first value to read after)")
            start = q["start"]
            if isinstance(start, datetime) and driver == "sqlite":
                start = start.isoformat(sep=" ")
            if q.get("lookback") not in (None, 0, "PT0S"):
                _minus(start, q["lookback"])  # fails now, not at the first poll, when the types do not fit
            max_rows = int(q.get("maxRows", DEFAULT_MAX_ROWS))
            if max_rows < 1:
                raise ValueError(f"sql source {sid}.{kind}: maxRows must be positive")
            if "maxRows" in q and not re.search(r"\border\s+by\b", q["sql"], re.IGNORECASE):
                raise ValueError(f"sql source {sid}.{kind}: maxRows needs ORDER BY the cursor column")
            queries.append(Query(kind, q["sql"], str(q["cursor"]), start, q.get("lookback"), max_rows))
        if not queries:
            raise ValueError(f"sql source {sid}: no queries")
        queries.sort(key=lambda q: ORDER.index(q.kind))
        return cls(sid, prof, driver, dsn, queries, int(cfg.get("timeoutSeconds", 60)))

    # -- connections ----------------------------------------------------------------------------
    def connect(self):
        if self.driver == "sqlite":
            conn = sqlite3.connect(f"file:{self.dsn}?mode=ro", uri=True, timeout=self.timeout_s)
            conn.execute("PRAGMA query_only = ON")
            return conn
        if self.driver == "postgresql":
            import psycopg  # LGPL-3.0, optional

            conn = psycopg.connect(self.dsn, connect_timeout=self.timeout_s,
                                   options=f"-c default_transaction_read_only=on -c statement_timeout={self.timeout_s * 1000}")
            conn.read_only = True
            return conn
        if self.driver == "mysql":
            import pymysql  # MIT, optional
            from urllib.parse import unquote, urlparse

            u = urlparse(self.dsn)
            conn = pymysql.connect(host=u.hostname, port=u.port or 3306, user=unquote(u.username or ""),
                                   password=unquote(u.password or ""), database=u.path.lstrip("/"),
                                   connect_timeout=self.timeout_s, read_timeout=self.timeout_s)
            with conn.cursor() as c:
                c.execute("SET SESSION TRANSACTION READ ONLY")
            return conn
        import pyodbc  # MIT-0, optional

        dsn = self.dsn if "applicationintent" in self.dsn.lower() else self.dsn.rstrip(";") + ";ApplicationIntent=ReadOnly"
        conn = pyodbc.connect(dsn, timeout=self.timeout_s, readonly=True)
        return conn

    def _fetch(self, conn, q: Query, after: Any) -> tuple[list[dict], Any, bool]:
        """Rows after `after` (one page) and the cursor value they reach; whether the page was cut short."""
        sql, params = bind(q.sql, after, DRIVERS[self.driver])
        cur = conn.cursor()
        try:
            cur.execute(sql, params)
            names = [d[0] for d in cur.description]
            if q.cursor not in names:
                raise ValueError(f"the {q.kind} query does not return its cursor column {q.cursor!r}")
            page = [dict(zip(names, r)) for r in cur.fetchmany(q.max_rows + 1)]
            cut = len(page) > q.max_rows
            if cut:
                boundary = page[-1][q.cursor]
                kept = [r for r in page if r[q.cursor] != boundary]
                if not kept:  # one cursor value holds more rows than a page: take all of them, never split it
                    kept = page + [dict(zip(names, r)) for r in cur.fetchall() if dict(zip(names, r))[q.cursor] == boundary]
                page = kept
        finally:
            cur.close()
        reached = max((r[q.cursor] for r in page if r[q.cursor] is not None), default=None)
        return page, reached, cut

    # -- polling --------------------------------------------------------------------------------
    def poll(self, imp: Importer, sink: Callable[[list[dict]], None]) -> dict:
        """One poll of every query into `imp`, handed to `sink`; cursors are stored only after the sink took it."""
        state = imp.state
        conn = self.connect()
        pages: dict[str, tuple[Query, list[dict], Any, Any]] = {}
        try:
            for q in self.queries:
                stored = state.cursor(self.id, q.kind)
                cursor = _decode(stored) if stored is not None else q.start
                page, reached, _cut = self._fetch(conn, q, _minus(cursor, q.lookback))
                pages[q.kind] = (q, page, cursor, reached)
                imp.feed(q.kind, q.kind, page)
        finally:
            conn.close()
        report = imp.commit(sink)
        doc = report.as_dict()
        new: dict[str, str] = {}
        for kind, (q, page, cursor, reached) in pages.items():
            bad = sorted({e["row"] for e in doc["errors"] if e["file"] == kind and isinstance(e["row"], int)})
            if bad:
                first_bad = page[bad[0] - 1][q.cursor]
                before = [r[q.cursor] for r in page if r[q.cursor] is not None and r[q.cursor] < first_bad]
                reached = max(before, default=None)
            if reached is not None and (cursor is None or reached > cursor):
                new[kind] = _encode(reached)
        state.set_cursors(self.id, new)
        return {"source": self.id, "records": {k: len(v[1]) for k, v in pages.items()}, "events": doc["events"],
                "errors": doc["errors"], "cursors": {k: json.loads(v) for k, v in new.items()}}
