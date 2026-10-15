"""The connector service (X1 B5f, ADR-037): one long-running process on the edge box runs every configured source.

    pqt-connectors run --sources /etc/pqt/sources.yaml --store store-001 --key billing.key \\
        --state connector-state.db --outbox outbox.db --status connectors-status.json

A sources file (`apiVersion: connector.pqt/v1`, `sources: [...]`) lists sources of five types: `watch` (a drop
folder), `sql`, `http`, `tally` (polled) and `push` (the push API). Each runs on its own schedule (`interval`
seconds); a failing source backs off (interval x 2^failures, at most 15 minutes) and the others carry on. All of
them share one importer state, written by one source at a time.

Health goes to a status file (replaced whole, mode 600) that the edge agent puts into its heartbeat (B5f):
per source whether it is ok, how many failures in a row, when it last succeeded, whether that is stale (more
than three intervals ago), its last error (the exception type and a value-free message, never data or a
secret), and the events it has sent. Rows the importer reports (bad data) are counted but do not make a source
unhealthy: the connection works, the data needs fixing.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from . import profile as profile_mod
from .importer import Importer, State
from .push import value_free
from .refs import Refs

TYPES = {"watch", "sql", "http", "tally", "push"}
MAX_BACKOFF_S = 900
DEFAULT_INTERVAL = {"watch": 30, "sql": 60, "http": 300, "tally": 300, "push": 60}


def _iso(t: float | None) -> str | None:
    return None if t is None else datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Job:
    id: str
    type: str
    interval: float
    cfg: dict
    source: Any = None          # SqlSource / RestSource / TallySource; a Profile for watch; None for push
    ok: bool = True
    failures: int = 0
    last_attempt: float | None = None
    last_success: float | None = None
    last_error: str | None = None
    events: int = 0
    row_errors: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def health(self, now: float) -> dict:
        stale = self.type != "push" and (self.last_success is None or now - self.last_success > 3 * self.interval)
        if self.type != "push" and self.last_attempt is None:
            stale = False  # not run yet
        return {"id": self.id, "type": self.type, "ok": self.ok, "stale": stale, "failures": self.failures,
                "intervalSeconds": self.interval, "lastAttemptAt": _iso(self.last_attempt),
                "lastSuccessAt": _iso(self.last_success), "lastError": self.last_error, "events": self.events,
                "rowErrors": self.row_errors}


class Service:
    def __init__(self, jobs: list[Job], *, base: Path, store: str, key: bytes, state_path: str | Path,
                 sink: Callable[[list[dict]], None], schemas: str | Path | None, status_path: str | Path | None,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep) -> None:
        self.jobs, self.base, self.store, self.key = jobs, base, store, key
        self.state_path, self.sink, self.schemas, self.status_path = Path(state_path), sink, schemas, status_path
        self.clock, self.sleep = clock, sleep
        self.import_lock = threading.Lock()
        self.status_lock = threading.Lock()
        self._push_server = None

    # -- configuration --------------------------------------------------------------------------
    @classmethod
    def from_file(cls, path: str | Path, **kw) -> "Service":
        path = Path(path)
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or doc.get("apiVersion") != "connector.pqt/v1" or not isinstance(doc.get("sources"), list):
            raise ValueError(f"{path}: not a connector.pqt/v1 sources file")
        base = path.resolve().parent
        jobs, ids = [], set()
        for cfg in doc["sources"]:
            sid, typ = str(cfg.get("id") or ""), cfg.get("type")
            if typ not in TYPES:
                raise ValueError(f"source {sid or '?'}: type must be one of {sorted(TYPES)}")
            if not re.fullmatch(r"[a-z0-9-]{1,64}", sid):
                raise ValueError(f"source {sid!r}: id must match [a-z0-9-]{{1,64}}")
            if sid in ids:
                raise ValueError(f"source {sid} appears twice")
            ids.add(sid)
            interval = float(cfg.get("interval", DEFAULT_INTERVAL[typ]))
            if not 0 < interval <= 86_400:
                raise ValueError(f"source {sid}: interval must be 1 s to 1 day")
            jobs.append(Job(sid, typ, interval, cfg, cls._build(cfg, base, kw.get("sleep", time.sleep))))
        if sum(j.type == "push" for j in jobs) > 1:
            raise ValueError("at most one push source per box (one port)")
        return cls(jobs, base=base, **kw)

    @staticmethod
    def _build(cfg: dict, base: Path, sleep: Callable[[float], None] = time.sleep):
        typ = cfg["type"]
        poll_cfg = {k: v for k, v in cfg.items() if k != "interval"}
        if typ == "sql":
            from .sqlsource import SqlSource
            return SqlSource.from_config(poll_cfg, base=base)
        if typ == "http":
            from .restsource import RestSource
            return RestSource.from_config(poll_cfg, base=base, sleep=sleep)
        if typ == "tally":
            from .tally import TallySource
            return TallySource.from_config(poll_cfg, base=base)
        if typ == "watch":
            extra = set(cfg) - {"id", "type", "dir", "profile", "interval", "minAge"}
            if extra:
                raise ValueError(f"watch source {cfg['id']}: unknown key(s) {sorted(extra)}")
            d = Path(cfg.get("dir", ""))
            d = d if d.is_absolute() else base / d
            if not d.is_dir():
                raise ValueError(f"watch source {cfg['id']}: {d} is not a folder")
            p = Path(cfg.get("profile", ""))
            return (d, profile_mod.load(p if p.is_absolute() else base / p), float(cfg.get("minAge", 10)))
        extra = set(cfg) - {"id", "type", "clients", "bind", "port", "tls", "insecureLan", "maxBodyMb", "rate", "burst",
                            "interval"}
        if extra:
            raise ValueError(f"push source {cfg['id']}: unknown key(s) {sorted(extra)}")
        from .push import load_clients
        c = Path(cfg.get("clients", ""))
        return load_clients(c if c.is_absolute() else base / c)

    # -- one cycle --------------------------------------------------------------------------------
    def cycle(self, job: Job) -> float:
        """Run one poll of `job`; return the seconds to wait before the next."""
        job.last_attempt = self.clock()
        try:
            with self.import_lock:
                result = self._run(job)
            source_errors = [e for e in result.get("errors", []) if e.get("row") is None and e.get("file") not in self._files(result)]
            if source_errors:
                raise _SourceDown(source_errors[0]["error"])
            job.events += int(result.get("events", 0) or 0)
            job.row_errors = len(result.get("errors", []))
            job.ok, job.failures, job.last_error = True, 0, None
            job.last_success = self.clock()
            delay = job.interval
        except Exception as e:  # noqa: BLE001 - a source must never take the service down
            job.ok = False
            job.failures += 1
            msg = str(e) if isinstance(e, _SourceDown) else f"{type(e).__name__}: {e}"
            job.last_error = value_free(msg)[:300]
            delay = min(job.interval * 2 ** min(job.failures, 20), MAX_BACKOFF_S)
        self.write_status()
        return delay

    @staticmethod
    def _files(result: dict) -> set:
        return set(result.get("imported", [])) | set(result.get("failed", []))

    def _run(self, job: Job) -> dict:
        if job.type == "watch":
            from . import watch

            drop, prof, min_age = job.source

            def make() -> Importer:
                return Importer(prof, store=self.store, refs=Refs(self.key, prof.system), state=State(self.state_path),
                                schemas=self.schemas)
            summary = watch.run_once(drop, prof, make, self.sink, min_age_s=min_age, now=self.clock)
            if summary.get("refused"):
                raise _SourceDown(summary["refused"])
            return {"events": summary["events"], "errors": [{"file": f, "row": 0} for f in summary["failed"]]}
        src = job.source
        state = State(self.state_path)
        try:
            imp = Importer(src.profile, store=self.store, refs=Refs(self.key, src.profile.system), state=state,
                           schemas=self.schemas)
            return src.poll(imp, self.sink)
        finally:
            state.close()

    # -- status -----------------------------------------------------------------------------------
    def write_status(self) -> None:
        if not self.status_path:
            return
        now = self.clock()
        doc = {"updatedAt": _iso(now), "store": self.store, "sources": [j.health(now) for j in self.jobs]}
        path = Path(self.status_path)
        with self.status_lock:
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")  # mode 600
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(doc, f, indent=2)
                os.replace(tmp, path)
            except BaseException:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise

    # -- the push source ----------------------------------------------------------------------------
    def push_url(self) -> str | None:
        if self._push_server is None:
            return None
        host, port = self._push_server.server_address[:2]
        return f"{'https' if self._push_tls else 'http'}://{host}:{port}"

    def _start_push(self, job: Job) -> None:
        from .push import make_server

        cfg = job.cfg
        tls = (cfg["tls"]["cert"], cfg["tls"]["key"]) if cfg.get("tls") else None

        def on_request(line: str) -> None:
            try:
                rec = json.loads(line)
            except ValueError:
                return
            if rec.get("status") == 200:
                job.events += int(rec.get("events") or 0)
                job.last_success = self.clock()
            job.last_attempt = self.clock()
            if rec.get("error"):
                job.last_error = value_free(str(rec["error"]))[:300]
            print(line, flush=True)
        self._push_tls = tls is not None
        self._push_server = make_server(job.source, store=self.store, key=self.key, state_path=self.state_path,
                                        sink=self.sink, schemas=self.schemas, bind=cfg.get("bind", "127.0.0.1"),
                                        port=int(cfg.get("port", 8787)), tls=tls,
                                        allow_insecure_lan=bool(cfg.get("insecureLan", False)),
                                        max_body=int(float(cfg.get("maxBodyMb", 5)) * 1024 * 1024),
                                        rate=(float(cfg.get("rate", 10)), int(cfg.get("burst", 20))),
                                        log=on_request, import_lock=self.import_lock)
        job.ok, job.last_success = True, self.clock()

    # -- running ------------------------------------------------------------------------------------
    def run(self, stop: threading.Event) -> None:
        threads = []
        for job in self.jobs:
            if job.type == "push":
                self._start_push(job)
                t = threading.Thread(target=self._push_server.serve_forever, name=f"push-{job.id}", daemon=True)
            else:
                t = threading.Thread(target=self._loop, args=(job, stop), name=f"source-{job.id}", daemon=True)
            t.start()
            threads.append(t)
        self.write_status()
        while not stop.wait(5):
            self.write_status()  # keeps staleness current even when every source is quiet
        if self._push_server is not None:
            self._push_server.shutdown()
            self._push_server.server_close()
        for t in threads:
            t.join(timeout=10)
        self.write_status()

    def _loop(self, job: Job, stop: threading.Event) -> None:
        while not stop.is_set():
            delay = self.cycle(job)
            if stop.wait(delay):
                return


class _SourceDown(Exception):
    """A source-level failure reported in a poll's result (the API, database or folder could not be used)."""
