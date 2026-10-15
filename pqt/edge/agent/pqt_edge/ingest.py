"""Camera ingest (A2): RTSP through OpenCV's FFmpeg backend, with reconnects and a frame-rate limiter.

OpenCV's wheels bundle an LGPL FFmpeg build (ADR-012), so no extra dependency is needed for RTSP.
- Open and read timeouts (5 s) turn a dead camera or network into an error instead of a hang.
- Any open failure, read failure or decoder error drops the session and reconnects with exponential
  back-off plus jitter, capped at 2 s: probing a camera on the shop's own LAN every 1-2 s costs nothing,
  and the A2 bar is "back within 10 s" of the camera returning (a 10 s cap missed it after a 15 s outage).
- The limiter keeps at most `fps` frames per second; the rest are read and discarded so the stream
  does not lag behind real time.
- Camera URLs often carry credentials; they are never logged (`redact`).
"""
from __future__ import annotations

import os
import random
import re
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

# Before cv2 loads: RTSP over TCP, and OpenCV/FFmpeg kept quiet so a camera URL (with its password) never
# reaches a log line through a library warning (A6).
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
os.environ.setdefault("OPENCV_LOG_LEVEL", "SILENT")
os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")

import cv2  # noqa: E402


def redact(url: str) -> str:
    """rtsp://user:pass@host/x → rtsp://***@host/x"""
    return re.sub(r"//[^/@]*@", "//***@", url)


def _open_cv(url: str, open_timeout_ms: int, read_timeout_ms: int):
    return cv2.VideoCapture(url, cv2.CAP_FFMPEG, [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, open_timeout_ms,
                                                  cv2.CAP_PROP_READ_TIMEOUT_MSEC, read_timeout_ms])


@dataclass
class IngestMetrics:
    frames: int = 0           # frames handed to perception
    skipped: int = 0          # frames read but dropped by the limiter
    read_errors: int = 0      # failed reads / decoder errors
    reconnects: int = 0       # sessions (re)opened after the first
    open_failures: int = 0
    connected: bool = False
    last_frame_ms: int | None = None
    fps_window: list = field(default_factory=list, repr=False)

    def fps(self, now_ms: int, window_ms: int = 10_000) -> float:
        self.fps_window = [t for t in self.fps_window if t > now_ms - window_ms]
        return round(len(self.fps_window) * 1000 / window_ms, 2)


class CameraReader:
    def __init__(self, url: str, *, fps: float = 5.0, open_timeout_ms: int = 5000, read_timeout_ms: int = 5000,
                 backoff_max_s: float = 2.0, opener: Callable = _open_cv, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.url, self.fps = url, fps
        self.open_timeout_ms, self.read_timeout_ms = open_timeout_ms, read_timeout_ms
        self.backoff_max_s = backoff_max_s
        self.opener, self.clock, self.sleep = opener, clock, sleep
        self.metrics = IngestMetrics()
        self._sessions = 0

    def __repr__(self) -> str:
        return f"CameraReader({redact(self.url)!r}, fps={self.fps})"

    def frames(self, stop: threading.Event) -> Iterator[tuple[int, object]]:
        """(wall-clock ms, BGR image) at up to `fps`, reconnecting forever until `stop` is set."""
        m = self.metrics
        backoff = 0.5
        min_gap_ms = 1000.0 / self.fps if self.fps > 0 else 0.0
        last_yield = -1e18
        while not stop.is_set():
            cap = self.opener(self.url, self.open_timeout_ms, self.read_timeout_ms)
            if cap is None or not cap.isOpened():
                m.open_failures += 1
                m.connected = False
                self.sleep(backoff * (0.5 + random.random() / 2))
                backoff = min(self.backoff_max_s, backoff * 2)
                continue
            if self._sessions:
                m.reconnects += 1
            self._sessions += 1
            m.connected = True
            backoff = 0.5
            try:
                while not stop.is_set():
                    ok, image = cap.read()
                    if not ok or image is None:
                        m.read_errors += 1
                        break  # drop the session and reconnect
                    now = int(self.clock() * 1000)
                    if now - last_yield < min_gap_ms:
                        m.skipped += 1
                        continue
                    last_yield = now
                    m.frames += 1
                    m.last_frame_ms = now
                    m.fps_window.append(now)
                    yield now, image
            finally:
                cap.release()
                m.connected = False
