"""The push API on the edge box (X1 B5b, ADR-037 §5): the shop's software, or a payment gateway's webhook, posts
billing records over HTTP; they go through the same importer as files.

    POST /v1/records             Bearer token per client; a JSON object with lists per kind ({"bills": [...]}),
                                 canonical field names or the client's profile's; optional HMAC signature.
    POST /v1/webhooks/{client}   a webhook body (one object, or a list at the profile's path), signature required.
    GET  /v1/health, /v1/schema  liveness; the canonical records schema.

Security: loopback unless told otherwise, and then only with TLS (or an explicit --insecure-lan); tokens are
stored as SHA-256 and compared in constant time; signatures are HMAC-SHA256 over the raw body; bodies over
`max_body` are refused unread; a token bucket per client; errors are RFC 9457 problem details that never echo
submitted values; the log has one line per request with no body, token or document number. A canonical
document is checked against the records schema before anything is imported; a request whose records are
reported row by row returns 200 with the errors (the rest was imported). Retried requests add nothing.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets as pysecrets
import socket
import ssl
import stat
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

import yaml

from . import profile as profile_mod
from .importer import Importer, State
from .profile import KINDS, ORDER, Profile
from .records import MISSING, json_loads, resolve as resolve_path
from .refs import Refs
from .secrets import resolve as resolve_secret

ROUTES: dict[tuple[str, str], list[int]] = {
    ("POST", "/v1/records"): [200, 400, 401, 403, 411, 413, 415, 422, 429, 500, 503],
    ("POST", "/v1/webhooks/{client}"): [200, 400, 401, 404, 411, 413, 415, 422, 429, 500, 503],
    ("GET", "/v1/health"): [200, 503],
    ("GET", "/v1/schema"): [200, 503],
}
REQUEST_TIMEOUT_S = 15      # a connection idle this long (half a request, or a silent TLS client) is dropped
MAX_CONNECTIONS = 64        # beyond this, new connections get 503 at once instead of a thread each
_QUOTED = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")
_DIGITS = re.compile(r"\d[\d ,.-]{4,}\d")


def value_free(message: str) -> str:
    """A row error as it may leave the box: quoted values, long digit runs and parser echoes removed."""
    m = _QUOTED.sub("'…'", message)
    m = re.sub(r"(unconverted data remains|does not match format|Invalid isoformat string)\s*:?.*", r"\1", m)
    return _DIGITS.sub("…", m)
API = "connector.pqt/v1"
MAX_RECORDS = 20_000


@dataclass
class Signature:
    header: str
    secret: str
    prefix: str = ""
    encoding: str = "hex"

    def expected(self, body: bytes) -> str:
        mac = hmac.new(self.secret.encode(), body, hashlib.sha256).digest()
        return self.prefix + (base64.b64encode(mac).decode() if self.encoding == "base64" else mac.hex())

    def ok(self, body: bytes, presented: str | None) -> bool:
        return presented is not None and hmac.compare_digest(self.expected(body).encode(), presented.strip().encode())


@dataclass
class Client:
    id: str
    profile: Profile
    profile_path: str
    token_sha256: str | None = None
    kinds: set[str] | None = None
    signature: Signature | None = None
    webhook: bool = False
    kind: str | None = None
    canonical: bool = field(default=False)


class Clients:
    def __init__(self, clients: list[Client]) -> None:
        self.clients = clients

    def by_token(self, token: str | None) -> Client | None:
        if not token:
            return None
        presented = hashlib.sha256(token.encode()).hexdigest().encode()
        found = None
        for c in self.clients:  # every client is compared, so timing does not tell which one matched
            if c.token_sha256 and hmac.compare_digest(presented, c.token_sha256.encode()) and not c.webhook:
                found = c
        return found

    def webhook(self, client_id: str) -> Client | None:
        return next((c for c in self.clients if c.webhook and c.id == client_id), None)


def _is_canonical(p: Profile) -> bool:
    return all(src == f for cols in p.columns.values() for f, src in cols.items())


def load_clients(path: str | Path) -> Clients:
    path = Path(path)
    if path.exists():
        mode = stat.S_IMODE(os.stat(path).st_mode)
        if mode & 0o022:
            raise ValueError(f"{path} is writable by others (anyone who can write it can add a client): chmod 600 it")
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {"apiVersion": API, "clients": []}
    if not isinstance(doc, dict) or doc.get("apiVersion") != API:
        raise ValueError(f"{path}: not a {API} clients file")
    out, ids = [], set()
    base = path.parent
    for c in doc.get("clients") or []:
        cid = str(c.get("id", ""))
        if not cid or not all(ch.isalnum() or ch in "-_" for ch in cid) or len(cid) > 64:
            raise ValueError(f"{path}: client id {cid!r} must be letters, digits, - or _")
        if cid in ids:
            raise ValueError(f"{path}: client id {cid} appears twice")
        ids.add(cid)
        unknown = set(c) - {"id", "tokenSha256", "profile", "kinds", "signature", "webhook", "kind"}
        if unknown:
            raise ValueError(f"{path}: client {cid}: unknown key(s) {sorted(unknown)}")
        ppath = Path(c.get("profile", ""))
        ppath = ppath if ppath.is_absolute() else base / ppath
        prof = profile_mod.load(ppath)
        sig = None
        if c.get("signature"):
            s = c["signature"]
            if set(s) - {"header", "secret", "prefix", "encoding"} or not s.get("header") or not s.get("secret"):
                raise ValueError(f"{path}: client {cid}: signature needs header and secret (env:/file:)")
            if s.get("encoding", "hex") not in ("hex", "base64"):
                raise ValueError(f"{path}: client {cid}: signature encoding is hex or base64")
            sig = Signature(s["header"], resolve_secret(s["secret"]), str(s.get("prefix", "")), s.get("encoding", "hex"))
        webhook = bool(c.get("webhook", False))
        kinds = set(c["kinds"]) if c.get("kinds") else None
        if kinds and kinds - set(KINDS):
            raise ValueError(f"{path}: client {cid}: kinds must be among {sorted(KINDS)}")
        if webhook:
            if sig is None:
                raise ValueError(f"{path}: webhook client {cid} needs a signature")
            if c.get("kind") not in prof.columns:
                raise ValueError(f"{path}: webhook client {cid} needs a kind its profile maps")
        else:
            th = str(c.get("tokenSha256", ""))
            if len(th) != 64 or any(ch not in "0123456789abcdef" for ch in th):
                raise ValueError(f"{path}: client {cid} needs tokenSha256 (pqt-connectors push-client add)")
        out.append(Client(cid, prof, str(ppath), c.get("tokenSha256"), kinds, sig, webhook, c.get("kind"),
                          _is_canonical(prof)))
    return Clients(out)


def add_client(path: str | Path, *, client_id: str, profile: str, kinds: list[str] | None = None,
               webhook: bool = False, kind: str | None = None, signature: dict | None = None) -> str | None:
    """Add a client to the clients file (mode 600); returns the new token (shown once), None for webhooks."""
    path = Path(path)
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {"apiVersion": API, "clients": []}
    if any(c.get("id") == client_id for c in doc.get("clients") or []):
        raise ValueError(f"client {client_id} already exists")
    entry: dict[str, Any] = {"id": client_id, "profile": profile}
    token = None
    if webhook:
        entry.update(webhook=True, kind=kind)
    else:
        token = pysecrets.token_urlsafe(32)
        entry["tokenSha256"] = hashlib.sha256(token.encode()).hexdigest()
    if kinds:
        entry["kinds"] = kinds
    if signature:
        entry["signature"] = signature
    doc.setdefault("clients", []).append(entry)
    _write_private(path, doc)
    load_clients(path)  # refuse to leave a file the server would not start with
    return token


def remove_client(path: str | Path, client_id: str) -> None:
    path = Path(path)
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    before = len(doc.get("clients") or [])
    doc["clients"] = [c for c in doc.get("clients") or [] if c.get("id") != client_id]
    if len(doc["clients"]) == before:
        raise ValueError(f"no client {client_id}")
    _write_private(path, doc)


def _write_private(path: Path, doc: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")  # O_EXCL, mode 600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("# PQT push clients (ADR-037). Tokens are stored as SHA-256; secrets only as env:/file: references.\n"
                    + yaml.safe_dump(doc, sort_keys=False))
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class Bucket:
    def __init__(self, rate: float, burst: int) -> None:
        self.rate, self.burst = rate, burst
        self.tokens: dict[str, float] = {}
        self.at: dict[str, float] = {}
        self.lock = threading.Lock()

    def take(self, key: str) -> float:
        """0 when allowed, else seconds to wait."""
        with self.lock:
            now = time.monotonic()
            tokens = min(self.burst, self.tokens.get(key, self.burst) + (now - self.at.get(key, now)) * self.rate)
            self.at[key] = now
            if tokens >= 1:
                self.tokens[key] = tokens - 1
                return 0.0
            self.tokens[key] = tokens
            return (1 - tokens) / self.rate


def _schema_messages(validator, doc: Any) -> list[str]:
    """Schema errors that name where and what is wrong, never the submitted value."""
    out = []
    for e in sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path)):
        where = "/".join(map(str, e.absolute_path)) or "(document)"
        v = e.validator
        if v == "enum":
            what = f"must be one of {', '.join(map(str, e.validator_value))}"
        elif v in ("required", "additionalProperties"):
            what = e.message.split(" (")[0] if v == "required" else e.message.replace("Additional properties are not allowed", "not allowed")
        elif v == "type":
            what = f"must be {e.validator_value}"
        elif v in ("pattern", "anyOf", "allOf"):
            what = "does not have the expected format"
        elif v in ("maxItems", "minItems", "maxLength", "minLength", "minimum", "maximum", "minProperties"):
            what = f"violates {v} {e.validator_value}"
        else:
            what = f"fails {v}"
        out.append(f"{where}: {what}")
    return out[:50]


def _records_schema(path: str | Path | None):
    from jsonschema import Draft202012Validator

    candidates = [Path(path)] if path else [Path(__file__).resolve().parent.parent / "schemas" / "records.schema.json",
                                            Path("/app/connector-schemas/records.schema.json")]
    for p in candidates:
        if p.is_file():
            doc = json.loads(p.read_text(encoding="utf-8"))
            return doc, Draft202012Validator(doc)
    raise FileNotFoundError("records.schema.json not found (pass --records-schema)")


def _loopback(bind: str) -> bool:
    if bind == "localhost":
        return True
    try:
        return ipaddress.ip_address(bind).is_loopback
    except ValueError:
        return False


class _Server(ThreadingHTTPServer):
    """A thread per connection, at most `max_connections` at once; TLS handshakes and idle connections time out in
    the connection's own thread, so one silent client never blocks the accept loop."""
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, *, tls_ctx: ssl.SSLContext | None, timeout_s: float, max_connections: int,
                 emit: Callable[[str], None]) -> None:
        super().__init__(addr, handler)
        self.tls_ctx, self.timeout_s, self.emit = tls_ctx, timeout_s, emit
        self.slots = threading.BoundedSemaphore(max_connections)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            body = b'{"type":"about:blank","title":"Busy; retry shortly","status":503,"code":"busy"}'
            try:
                request.settimeout(1)
                if self.tls_ctx is None:
                    request.sendall(b"HTTP/1.1 503 Service Unavailable\r\nContent-Type: application/problem+json\r\n"
                                    b"Retry-After: 5\r\nConnection: close\r\nContent-Length: " + str(len(body)).encode()
                                    + b"\r\n\r\n" + body)
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def finish_request(self, request, client_address):
        request.settimeout(self.timeout_s)
        if self.tls_ctx is None:
            self.RequestHandlerClass(request, client_address, self)
            return
        try:
            tls = self.tls_ctx.wrap_socket(request, server_side=True)  # the handshake, here, with the timeout
        except (ssl.SSLError, OSError):
            return
        try:
            self.RequestHandlerClass(tls, client_address, self)
        finally:
            try:
                tls.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            tls.close()

    def handle_error(self, request, client_address):
        """Never print a traceback (it can quote what was sent): one line with the exception type."""
        kind = sys.exc_info()[0].__name__ if sys.exc_info()[0] else "error"
        self.emit(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "error": kind}))


def make_server(clients: Clients, *, store: str, key: bytes, state_path: str | Path, sink: Callable[[list[dict]], None],
                schemas: str | Path | None = None, bind: str = "127.0.0.1", port: int = 8787,
                tls: tuple[str, str] | None = None, allow_insecure_lan: bool = False, max_body: int = 5 * 1024 * 1024,
                rate: tuple[float, int] = (10.0, 20), records_schema: str | Path | None = None,
                log: Callable[[str], None] | None = None, request_timeout: float = REQUEST_TIMEOUT_S,
                max_connections: int = MAX_CONNECTIONS) -> ThreadingHTTPServer:
    if not _loopback(bind) and tls is None and not allow_insecure_lan:
        raise ValueError(f"binding to {bind} exposes the push API to the shop network: give a TLS certificate and key "
                         "(or --insecure-lan on a closed, trusted network)")
    schema_doc, schema_validator = _records_schema(records_schema)
    import_lock = threading.Lock()
    bucket = Bucket(*rate)
    emit = log or (lambda line: print(line, file=sys.stderr, flush=True))

    class Handler(BaseHTTPRequestHandler):
        server_version = "pqt-connectors"
        sys_version = ""
        protocol_version = "HTTP/1.1"
        timeout = request_timeout

        def log_message(self, fmt: str, *args) -> None:  # replaced by one structured line per request
            pass

        def _send(self, status: int, body: Any, *, problem: bool = False, headers: dict | None = None) -> None:
            if self.command == "POST" and not getattr(self, "_read_body", False):
                self.close_connection = True  # an unread body must never be taken for the next request
            raw = json.dumps(body, ensure_ascii=False, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/problem+json" if problem else "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if self.close_connection:
                self.send_header("Connection", "close")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(raw)
            emit(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "method": self.command,
                             "path": self.path.split("?")[0][:200], "status": status,
                             "client": getattr(self, "_client", "-"),
                             "events": body.get("events") if isinstance(body, dict) else None}))

        def _problem(self, status: int, code: str, title: str, *, errors: list | None = None,
                     headers: dict | None = None) -> None:
            body = {"type": "about:blank", "title": title, "status": status, "code": code}
            if errors:
                body["errors"] = errors
            self._send(status, body, problem=True, headers=headers)

        # -- GET ---------------------------------------------------------------------------------
        def do_GET(self) -> None:  # noqa: N802 (http.server naming)
            path = self.path.split("?")[0]
            if path == "/v1/health":
                return self._send(200, {"status": "ok"})
            if path == "/v1/schema":
                return self._send(200, schema_doc)
            if path == "/v1/records" or path.startswith("/v1/webhooks/"):
                return self._problem(405, "method-not-allowed", "Use POST", headers={"Allow": "POST"})
            return self._problem(404, "not-found", "No such resource")

        # -- POST --------------------------------------------------------------------------------
        def _body(self) -> bytes | None:
            if self.headers.get("Transfer-Encoding"):
                self._problem(411, "length-required", "Send Content-Length; chunked bodies are not accepted")
                return None
            length = (self.headers.get("Content-Length") or "").strip()
            if not re.fullmatch(r"[0-9]{1,12}", length):
                self._problem(411, "length-required", "Content-Length (plain digits) is required")
                return None
            n = int(length)
            if n > max_body:
                self._problem(413, "too-large", f"Bodies over {max_body} bytes are refused; send fewer records per request")
                return None
            if not (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() == "application/json":
                self._problem(415, "unsupported-media-type", "Content-Type must be application/json")
                return None
            raw = self.rfile.read(n)
            if len(raw) < n:
                self.close_connection = True
                return None  # the client went away mid-body: nothing to answer
            self._read_body = True
            return raw

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?")[0]
            if path == "/v1/records":
                return self._records()
            if path.startswith("/v1/webhooks/"):
                return self._webhook(path[len("/v1/webhooks/"):])
            return self._problem(404, "not-found", "No such resource")

        def _limited(self, key: str) -> bool:
            wait = bucket.take(key)
            if wait:
                self._problem(429, "rate-limited", "Too many requests; retry later",
                              headers={"Retry-After": str(max(1, int(wait + 0.999)))})
                return True
            return False

        def _parse(self, raw: bytes) -> Any:
            try:
                return json_loads(raw)
            except (UnicodeDecodeError, ValueError):
                self._problem(400, "malformed-json", "The body is not JSON (or is nested too deeply)")
                return MISSING

        def _records(self) -> None:
            auth = self.headers.get("Authorization") or ""
            client = clients.by_token(auth[7:].strip() if auth.startswith("Bearer ") else None)
            if client is None:
                return self._problem(401, "unauthorized", "A valid bearer token is required",
                                     headers={"WWW-Authenticate": 'Bearer realm="pqt-connectors"'})
            self._client = client.id
            if self._limited(client.id):
                return
            raw = self._body()
            if raw is None:
                return
            if client.signature and not client.signature.ok(raw, self.headers.get(client.signature.header)):
                return self._problem(401, "bad-signature", f"{client.signature.header} does not match the body",
                                     headers={"WWW-Authenticate": 'Bearer realm="pqt-connectors"'})
            doc = self._parse(raw)
            if doc is MISSING:
                return
            if not isinstance(doc, dict) or not doc or set(doc) - set(KINDS) or not all(isinstance(v, list) for v in doc.values()):
                return self._problem(422, "invalid-records", "Send an object with lists per kind",
                                     errors=[f"keys must be among {', '.join(ORDER)}, each a list"])
            if client.canonical:
                errs = _schema_messages(schema_validator, doc)
                if errs:
                    return self._problem(422, "invalid-records", "The records do not match the canonical schema",
                                         errors=errs)
            if client.kinds and set(doc) - client.kinds:
                return self._problem(403, "kind-not-allowed", f"This client may send {', '.join(sorted(client.kinds))}")
            missing = set(doc) - set(client.profile.columns)
            if missing:
                return self._problem(422, "invalid-records", f"The client's profile maps no {', '.join(sorted(missing))}")
            if sum(len(v) for v in doc.values()) > MAX_RECORDS:
                return self._problem(413, "too-large", f"At most {MAX_RECORDS} records per request")
            self._import(client, [(k, doc[k]) for k in ORDER if k in doc])

        def _webhook(self, client_id: str) -> None:
            client = clients.webhook(client_id)
            if client is None:
                return self._problem(404, "not-found", "No such webhook")
            self._client = client.id
            raw = self._body()
            if raw is None:
                return
            if not client.signature.ok(raw, self.headers.get(client.signature.header)):
                # Forgeries draw on their own allowance, so they cannot starve the sender's real deliveries.
                if self._limited(f"bad-signature:{client.id}"):
                    return
                return self._problem(401, "bad-signature", f"{client.signature.header} does not match the body")
            if self._limited(client.id):
                return
            doc = self._parse(raw)
            if doc is MISSING:
                return
            where = client.profile.file_opts.get(client.kind, {}).get("path")
            found = resolve_path(doc, where) if where else doc
            records = [found] if isinstance(found, dict) else found
            if not isinstance(records, list):
                return self._problem(422, "invalid-records", "No record where the client's profile says (path)")
            self._import(client, [(client.kind, records)])

        def _import(self, client: Client, batches: list[tuple[str, list]]) -> None:
            sink_failed: list[bool] = []

            def guarded(events: list[dict]) -> None:
                try:
                    sink(events)
                except Exception:
                    sink_failed.append(True)
                    raise
            with import_lock:
                state = State(state_path)
                try:
                    imp = Importer(client.profile, store=store, refs=Refs(key, client.profile.system), state=state,
                                   schemas=schemas)
                    for kind, records in batches:
                        imp.feed(kind, kind, records)
                    report = imp.commit(guarded)
                except Exception as e:  # noqa: BLE001 - answer every request; never echo what was sent
                    if sink_failed:  # the outbox refused: nothing was recorded, the client retries
                        return self._problem(503, "outbox-unavailable", "The edge outbox is unavailable; retry later",
                                             headers={"Retry-After": "30"})
                    emit(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "client": client.id,
                                     "error": type(e).__name__}))
                    return self._problem(500, "internal-error", "The records could not be processed; nothing was kept")
                finally:
                    state.close()
            d = report.as_dict()
            self._send(200, {"events": d["events"], "alreadySent": d["alreadySent"], "records": d["files"],
                             "bills": d["bills"],
                             "errors": [{"kind": e["file"], "record": e["row"], "error": value_free(e["error"])}
                                        for e in d["errors"]]})

    ctx = None
    if tls:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(*tls)
    srv = _Server((bind, port), Handler, tls_ctx=ctx, timeout_s=request_timeout, max_connections=max_connections,
                  emit=emit)
    srv.clients = clients
    return srv
