"""Agent configuration (YAML) and secrets (files readable only by the agent's user).

    agent: {id: edge-001, store: store-001}
    uplink: {ingestUrl: https://gw/v1/events, tokenUrl: https://gw/auth/.../token, clientId: edge-demo,
             clientSecretFile: /run/secrets/client_secret}
    trackSecretFile: /run/secrets/track_secret
    outbox: {path: /var/lib/pqt-edge/outbox.db, quotaMb: 512}
    detector: {kind: yolox, model: yolox_s, mode: roi, conf: 0.3}      # or {kind: synthetic-queue}
    cameras:
      - {id: cam-01, urlFile: /run/secrets/cam-01.url, config: /config/cameras/cam-01.yaml, fps: 5}

Camera URLs usually contain the camera password, so they are read from files like the other secrets and
never logged. A6 (security baseline) replaces plain files with a device-sealed store; the interface stays.
"""
from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class ConfigError(ValueError):
    pass


def read_secret(path: str | Path, *, strict: bool = True) -> str:
    p = Path(path)
    try:
        st = p.stat()
    except OSError as e:
        raise ConfigError(f"secret file {p} is not readable") from e
    if strict and st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ConfigError(f"secret file {p} must not be readable by group or others (chmod 600)")
    value = p.read_text(encoding="utf-8").strip()
    if not value:
        raise ConfigError(f"secret file {p} is empty")
    return value


@dataclass(frozen=True)
class CameraSpec:
    id: str
    config: Path
    fps: float = 5.0
    url_file: Path | None = None
    url_value: str | None = field(default=None, repr=False)  # tests and simulated cameras only
    strict_secrets: bool = True

    def url(self) -> str:
        if self.url_value:
            return self.url_value
        if self.url_file is None:
            raise ConfigError(f"camera {self.id} has no urlFile")
        return read_secret(self.url_file, strict=self.strict_secrets)


@dataclass
class AgentConfig:
    agent_id: str
    store: str
    ingest_url: str
    token_url: str
    client_id: str
    client_secret_file: Path
    track_secret_file: Path
    outbox_path: Path
    outbox_quota_bytes: int
    detector: dict
    cameras: list[CameraSpec]
    status_path: Path
    status_every_s: float = 10.0
    strict_secrets: bool = True

    def client_secret(self) -> str:
        return read_secret(self.client_secret_file, strict=self.strict_secrets)

    def track_secret(self) -> bytes:
        v = read_secret(self.track_secret_file, strict=self.strict_secrets).encode()
        if len(v) < 16:
            raise ConfigError("the track secret must be at least 16 bytes")
        return v

    def __repr__(self) -> str:
        return f"AgentConfig(agent_id={self.agent_id!r}, store={self.store!r}, cameras={[c.id for c in self.cameras]})"


def load(path: str | Path) -> AgentConfig:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    base = Path(path).parent
    strict = os.environ.get("PQT_EDGE_STRICT_SECRETS", "1") != "0"
    try:
        up, ob = doc["uplink"], doc.get("outbox", {})
        cams = [CameraSpec(c["id"], base / c["config"], float(c.get("fps", 5)),
                           base / c["urlFile"] if "urlFile" in c else None, c.get("url"), strict)
                for c in doc["cameras"]]
        return AgentConfig(
            agent_id=doc["agent"]["id"], store=doc["agent"]["store"], ingest_url=up["ingestUrl"], token_url=up["tokenUrl"],
            client_id=up["clientId"], client_secret_file=base / up["clientSecretFile"],
            track_secret_file=base / doc["trackSecretFile"], outbox_path=Path(ob.get("path", "/var/lib/pqt-edge/outbox.db")),
            outbox_quota_bytes=int(float(ob.get("quotaMb", 512)) * 1024 * 1024), detector=doc.get("detector", {}),
            cameras=cams, status_path=Path(doc.get("statusPath", "/var/lib/pqt-edge/status.json")),
            status_every_s=float(doc.get("statusEverySeconds", 10)), strict_secrets=strict)
    except KeyError as e:
        raise ConfigError(f"missing setting {e}") from e
