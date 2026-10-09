"""End-to-end HTTP tests. They run inside the gateway's network namespace, so
http://localhost:8080 is exactly the origin a browser uses."""
from __future__ import annotations

import html
import os
import re
import uuid
from datetime import datetime, timezone

import pytest
import requests

BASE = os.environ.get("PQT_BASE_URL", "http://localhost:8080")
PUBLIC = os.environ.get("PQT_PUBLIC_URL", BASE)
TOKEN_URL = f"{BASE}/auth/realms/pqt/protocol/openid-connect/token"
FORM = re.compile(r"<form\b[^>]*\bid=\"kc-form-login\"[^>]*>", re.S)
ACTION = re.compile(r"\baction=\"([^\"]+)\"")
KC_ERROR = re.compile(r"(?:kc-feedback-text|alert-error|pf-v5-c-alert__title)[^>]*>\s*([^<]+)")


class LocalhostCookies(__import__("http.cookiejar").cookiejar.DefaultCookiePolicy):
    """Browsers send Secure cookies to http://localhost (a secure context); Python doesn't by default."""

    def return_ok_secure(self, cookie, request):
        return True


def now_iso(offset_s: int = 0) -> str:
    t = datetime.now(timezone.utc).timestamp() + offset_s
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(t * 1000) % 1000:03d}Z"


def edge_event(**overrides) -> dict:
    ev = {
        "specversion": "1.0", "id": f"e2e-{uuid.uuid4()}", "source": "urn:pqt:edge:store-001:gw-1",
        "type": "com.pqt.store.queue.length", "time": now_iso(), "subject": "queue:checkout-1",
        "dataschema": "https://schemas.pqt.local/store/queue.length/1.0.0", "datacontenttype": "application/json",
        "storeid": "store-001", "partitionkey": "store-001",
        "data": {"queueId": "checkout-1", "length": 2, "openRegisters": 1},
    }
    ev.update(overrides)
    return ev


def client_token(client_id: str, secret: str) -> str:
    r = requests.post(TOKEN_URL, data={"grant_type": "client_credentials", "client_id": client_id,
                                       "client_secret": secret}, timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]


@pytest.fixture(scope="session")
def edge_token() -> str:
    return client_token("edge-demo", os.environ["PQT_EDGE_CLIENT_SECRET"])


@pytest.fixture(scope="session")
def sim_token() -> str:
    return client_token("store-sim", os.environ["PQT_SIM_CLIENT_SECRET"])


class Console:
    """A logged-in browser session (cookies + CSRF), driven through the real Keycloak login form."""

    def __init__(self, username: str, password: str):
        if PUBLIC.rstrip("/") != BASE.rstrip("/"):
            pytest.skip(f"browser-login tests need PQT_PUBLIC_URL={BASE} (currently {PUBLIC})")
        self.s = requests.Session()
        self.s.cookies.set_policy(LocalhostCookies())
        r = self.s.get(f"{BASE}/oauth2/authorization/keycloak", timeout=15)
        form = FORM.search(r.text)
        assert form, f"Keycloak login form not found (status {r.status_code}, url {r.url})"
        action = html.unescape(ACTION.search(form.group(0)).group(1))
        # Browsers treat http://localhost as a secure context and send Secure cookies there;
        # requests does not, so mark the stored cookies non-secure before posting the form.
        for c in self.s.cookies:
            c.secure = False
        r = self.s.post(action, data={"username": username, "password": password, "credentialId": ""}, timeout=15)
        m = KC_ERROR.search(r.text)
        assert r.status_code == 200, f"Keycloak said: {m.group(1).strip() if m else r.text[:300]}"
        me = self.s.get(f"{BASE}/api/me", timeout=10)
        assert me.status_code == 200, f"login failed for {username}: {me.status_code}"
        self.me = me.json()

    def csrf(self) -> dict:
        token = self.s.cookies.get("XSRF-TOKEN")
        assert token, "XSRF-TOKEN cookie missing"
        return {"X-XSRF-TOKEN": token}

    def get(self, path: str, **kw):
        return self.s.get(f"{BASE}{path}", timeout=15, **kw)

    def post(self, path: str, json=None, headers=None, csrf: bool = True):
        h = dict(headers or {})
        if csrf:
            h.update(self.csrf())
        return self.s.post(f"{BASE}{path}", json=json, headers=h, timeout=15)


def login(role: str) -> Console:
    return Console(role, os.environ[f"PQT_{role.upper()}_PASSWORD"])
