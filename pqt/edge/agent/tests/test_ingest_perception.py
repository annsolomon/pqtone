"""Camera ingest resilience (A2) and edge perception, with fake cameras and synthetic people."""
import threading
from pathlib import Path

import numpy as np

from pqt_edge.ingest import CameraReader, redact
from pqt_edge.perception import Perception
from pqt_edge.synthetic import SyntheticQueueDetector
from pqt_vision import camera as cam
from pqt_vision.emitter import contract_validator
from pqt_vision.synthetic import camera_doc

SCHEMAS = Path(__file__).resolve().parents[3] / "schemas"
IMG = np.zeros((8, 8, 3), dtype=np.uint8)


class FakeCap:
    def __init__(self, reads):
        self.reads, self.released = list(reads), False

    def isOpened(self):
        return True

    def read(self):
        return (True, IMG) if self.reads and self.reads.pop(0) else (False, None)

    def release(self):
        self.released = True


class Closed:
    def isOpened(self):
        return False


def test_reconnects_after_open_failures_and_stream_drops():
    script = [Closed(), Closed(), FakeCap([1, 1, 1, 0]), FakeCap([1, 1, 0])]
    sleeps = []
    stop = threading.Event()
    clock = iter(range(0, 10**9, 1000))  # 1 s between frames: the limiter never drops one

    def opener(url, a, b):
        if not script:
            stop.set()
            return Closed()
        return script.pop(0)

    r = CameraReader("rtsp://admin:hunter2@10.0.0.5/stream", fps=5, opener=opener,
                     clock=lambda: next(clock) / 1000, sleep=sleeps.append)
    got = list(r.frames(stop))
    m = r.metrics
    assert len(got) == 5 and m.frames == 5
    assert (m.open_failures, m.reconnects, m.read_errors) == (3, 1, 2)
    assert sleeps[0] < sleeps[1] * 2.1 and sleeps[1] >= sleeps[0] * 0.5  # back-off grows (with jitter)
    assert "hunter2" not in repr(r)


def test_a_camera_that_is_away_for_long_is_still_probed_every_two_seconds():
    """A4 cable test: after a 15 s outage the 10 s back-off cap kept the reader asleep for up to 8 s after the
    camera was back (10.8 s recovery against the 10 s bar). The cap is 2 s now."""
    stop = threading.Event()
    sleeps = []
    attempts = iter(range(30))

    def opener(url, a, b):
        if next(attempts) == 29:
            stop.set()
        return Closed()

    r = CameraReader("rtsp://cam", fps=5, opener=opener, sleep=sleeps.append)
    list(r.frames(stop))
    assert len(sleeps) >= 25 and max(sleeps) <= 2.0 and max(sleeps) > 1.0


def test_the_limiter_keeps_at_most_fps_frames_per_second():
    stop = threading.Event()
    t = iter(range(0, 10**9, 40))  # a 25 fps camera
    caps = [FakeCap([1] * 50)]
    r = CameraReader("rtsp://cam", fps=5, opener=lambda *a: caps.pop(0) if caps else (stop.set() or Closed()),
                     clock=lambda: next(t) / 1000, sleep=lambda s: None)
    got = list(r.frames(stop))
    assert len(got) == 10 and r.metrics.skipped == 40  # 2 s of video at 5 fps


def test_redact():
    assert redact("rtsp://user:pa:ss@host:554/x") == "rtsp://***@host:554/x"
    assert redact("rtsp://host/x") == "rtsp://host/x"


def test_perception_turns_frames_into_contract_valid_events_in_time_order():
    camera = cam.from_doc(camera_doc(store="store-001", camera="cam-01", queue_id="q-main"))
    p = Perception(camera, SyntheticQueueDetector(), start_ms=1_791_000_000_000, secret=b"k" * 32, fps=10)
    events = []
    for k in range(0, 2600):  # 260 s at 10 fps: one full synthetic queue cycle
        events += p.process(1_791_000_000_000 + k * 100, IMG)
    events += p.finish(1_791_000_000_000 + 2600 * 100)
    check = contract_validator(SCHEMAS)
    assert [err for e in events for err in check(e)] == []
    times = [e["time"] for e in events]
    assert times == sorted(times)
    lengths = [e["data"]["length"] for e in events if e["type"].endswith("queue.length")]
    assert max(lengths) == 8 and lengths[-1] == 0
    assert sum(1 for e in events if e["type"].endswith("clock.tick")) >= 26
