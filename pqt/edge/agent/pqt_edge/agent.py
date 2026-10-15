"""The edge agent: one ingest + perception thread per camera, one uplink thread, one shared outbox.

    camera (RTSP) ─► CameraReader ─► Perception ─► Outbox (SQLite, WAL) ─► Uplinker ─► event-core

If the outbox is full of unsent events (`OutboxFull`), perception pauses (frames are read and dropped,
no events are made) and the agent reports the alarm, rather than dropping events it already promised to
deliver. Configuration: see `config.py`.
"""
from __future__ import annotations

import json
import logging
import signal
import threading
import time
from dataclasses import dataclass, field

from pqt_vision import camera as cam
from pqt_vision.uplink import OAuthClient, Uplink

from . import __version__
from .config import AgentConfig, CameraSpec
from .ingest import CameraReader, redact
from .outbox import Outbox, OutboxFull
from .perception import Perception
from .uplinker import Uplinker

log = logging.getLogger("pqt_edge")


def make_detector(spec: dict, camera: cam.CameraConfig):
    kind = spec.get("kind", "yolox")
    if kind == "synthetic-queue":
        from .synthetic import SyntheticQueueDetector

        return SyntheticQueueDetector(speed=float(spec.get("speed", 1.0)))
    if kind == "yolox":
        import numpy as np

        from pqt_vision.detect.compose import RoiDetector, roi_for_zones
        from pqt_vision.detect.yolox import YoloxOnnxDetector
        from pqt_vision.models import resolve

        base = YoloxOnnxDetector(resolve(spec.get("model", "yolox_s")), name=spec.get("model", "yolox_s"),
                                 conf=float(spec.get("conf", 0.3)), threads=spec.get("threads"))
        if spec.get("mode", "roi") == "roi" and camera.zones:
            from pqt_vision.geometry import apply

            to_image = np.linalg.inv(camera.h)  # zones are in floor units; the crop is in image pixels
            polys = [np.array(z.polygon, dtype=float) if camera.units == "px" else apply(to_image, np.array(z.polygon, dtype=float))
                     for z in camera.zones]
            return RoiDetector(base, roi_for_zones(polys, width=camera.width, height=camera.height))
        return base
    raise ValueError(f"unknown detector kind {kind!r}")


@dataclass
class CameraWorker:
    spec: CameraSpec
    reader: CameraReader
    perception: Perception
    paused: bool = False
    events: int = 0
    errors: list[str] = field(default_factory=list)


class Agent:
    def __init__(self, cfg: AgentConfig) -> None:
        self.cfg = cfg
        self.outbox = Outbox(cfg.outbox_path, quota_bytes=cfg.outbox_quota_bytes)
        uplink = Uplink(cfg.ingest_url, OAuthClient(cfg.token_url, cfg.client_id, cfg.client_secret()), max_attempts=3)
        self.uplinker = Uplinker(self.outbox, uplink)
        self.stop = threading.Event()
        self.started_ms = int(time.time() * 1000)
        secret = cfg.track_secret()
        self.workers: list[CameraWorker] = []
        for spec in cfg.cameras:
            camera = cam.load(spec.config)
            self.workers.append(CameraWorker(
                spec, CameraReader(spec.url(), fps=spec.fps),
                Perception(camera, make_detector(cfg.detector, camera), start_ms=self.started_ms, secret=secret, fps=spec.fps)))
        self._lock = threading.Lock()

    def _camera_loop(self, w: CameraWorker) -> None:
        for wall_ms, image in w.reader.frames(self.stop):
            try:
                events = w.perception.process(wall_ms, image)
            except Exception as e:  # keep the camera loop alive; the error shows in status()
                w.errors = (w.errors + [f"{type(e).__name__}: {e}"])[-5:]
                log.exception("perception failed on %s", w.spec.id)
                continue
            if not events:
                continue
            try:
                self.outbox.append(events)
                w.events += len(events)
                w.paused = False
            except OutboxFull:
                if not w.paused:
                    log.error("outbox full of unsent events: pausing perception on %s", w.spec.id)
                w.paused = True  # these events are not made again; the stream resumes from the next frame

    def status(self) -> dict:
        return {"agent": self.cfg.agent_id, "version": __version__, "outbox": self.outbox.stats(),
                "uplink": self.uplinker.metrics,
                "cameras": [{"id": w.spec.id, "url": redact(w.spec.url()), "connected": w.reader.metrics.connected,
                             "frames": w.reader.metrics.frames, "readErrors": w.reader.metrics.read_errors,
                             "reconnects": w.reader.metrics.reconnects, "openFailures": w.reader.metrics.open_failures,
                             "events": w.events, "paused": w.paused, "errors": w.errors} for w in self.workers]}

    def run(self) -> None:
        threads = [threading.Thread(target=self._camera_loop, args=(w,), name=f"cam-{w.spec.id}", daemon=True)
                   for w in self.workers]
        threads.append(threading.Thread(target=self.uplinker.run, args=(self.stop,), name="uplink", daemon=True))
        for t in threads:
            t.start()
        if threading.current_thread() is threading.main_thread():  # tests run the agent in a thread
            signal.signal(signal.SIGTERM, lambda *_: self.stop.set())
            signal.signal(signal.SIGINT, lambda *_: self.stop.set())
        while not self.stop.wait(self.cfg.status_every_s):
            self.cfg.status_path.write_text(json.dumps(self.status(), indent=2) + "\n", encoding="utf-8")
            log.info("status %s", json.dumps({"outbox": self.outbox.stats(), "uplink": self.uplinker.metrics}))
        for t in threads:
            t.join(timeout=10)
        self._drain_on_stop(threads[-1])
        self.cfg.status_path.write_text(json.dumps(self.status(), indent=2) + "\n", encoding="utf-8")

    def _drain_on_stop(self, uplink_thread: threading.Thread) -> None:
        """The cameras have stopped, so nothing new is produced: send what the last seconds left pending, if
        event-core answers, instead of leaving it for the next start (A4 found the race at stop)."""
        if self.cfg.drain_on_stop_s <= 0 or uplink_thread.is_alive():
            return  # an uplink thread still busy (a long back-off) keeps the outbox to itself
        d = self.uplinker.drain(self.cfg.drain_on_stop_s)
        log.info("stop: sent %d pending events (%s); %d left for the next start", d["sent"], d["outcome"], d["pending"])
