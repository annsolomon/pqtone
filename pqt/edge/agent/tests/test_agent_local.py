"""The whole agent in one process: a looping video file as the camera, synthetic people, and a local
fake event-core that is down for a while (a cut cable). Every per-camera sequence must arrive once."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2
import numpy as np
import yaml

from pqt_edge import config
from pqt_edge.agent import Agent
from pqt_vision.synthetic import camera_doc


class FakeCore(BaseHTTPRequestHandler):
    down_until = 0.0
    refused = 0
    received: list = []
    lock = threading.Lock()

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        if self.path.endswith("/token"):
            return self._json(200, {"access_token": "t", "expires_in": 300})
        if time.time() < FakeCore.down_until:
            FakeCore.refused += 1
            return self._json(503, {"title": "down"})
        events = json.loads(body)
        out = []
        with FakeCore.lock:
            seen = {(e["source"], e["id"]) for e in FakeCore.received}
            for i, e in enumerate(events):
                dup = (e["source"], e["id"]) in seen
                if not dup:
                    FakeCore.received.append(e)
                    seen.add((e["source"], e["id"]))
                out.append({"index": i, "id": e["id"], "status": "duplicate" if dup else "accepted"})
        self._json(200, {"results": out})

    def _json(self, code, doc):
        data = json.dumps(doc).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def test_agent_delivers_every_sequence_once_through_an_outage(tmp_path: Path):
    video = tmp_path / "cam.avi"
    vw = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 20, (64, 48))
    for i in range(40):
        vw.write(np.full((48, 64, 3), i * 5, dtype=np.uint8))
    vw.release()
    (tmp_path / "cam.yaml").write_text(yaml.safe_dump(camera_doc(store="store-001", camera="cam-01", queue_id="q-a")))
    for name in ("client_secret", "track_secret"):
        (tmp_path / name).write_text("s" * 32)
        (tmp_path / name).chmod(0o600)
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeCore)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    (tmp_path / "agent.yaml").write_text(yaml.safe_dump({
        "agent": {"id": "edge-test", "store": "store-001"},
        "uplink": {"ingestUrl": f"{base}/v1/events", "tokenUrl": f"{base}/token", "clientId": "edge-demo",
                   "clientSecretFile": "client_secret"},
        "trackSecretFile": "track_secret",
        "outbox": {"path": str(tmp_path / "outbox.db"), "quotaMb": 16},
        "detector": {"kind": "synthetic-queue", "speed": 20},
        "cameras": [{"id": "cam-01", "url": str(video), "config": "cam.yaml", "fps": 10}],
        "statusPath": str(tmp_path / "status.json"), "statusEverySeconds": 0.5}))
    FakeCore.received, FakeCore.refused = [], 0
    FakeCore.down_until = time.time() + 3.0  # the cable is cut for the first 3 seconds
    agent = Agent(config.load(tmp_path / "agent.yaml"))
    agent.uplinker.idle_s = 0.05
    t = threading.Thread(target=agent.run)
    t.start()
    time.sleep(8.0)
    agent.stop.set()
    t.join(timeout=20)
    server.shutdown()
    assert agent.outbox.stats()["pending"] == 0, "a clean stop sends what the last moments produced (A4)"
    st = agent.outbox.stats()
    assert st["pending"] == 0 and st["rejected"] == 0 and st["sent"] > 10
    assert FakeCore.refused >= 1  # the agent did hit the outage (retries inside the uplink may hide it from the metrics)
    seqs = sorted(int(e["sequence"]) for e in FakeCore.received if e["source"].endswith("cam-01"))
    assert seqs == list(range(1, len(seqs) + 1))  # gap-free, each exactly once
    status = json.loads((tmp_path / "status.json").read_text())
    assert status["cameras"][0]["frames"] > 30 and "s" * 32 not in json.dumps(status)
