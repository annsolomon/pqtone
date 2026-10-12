# edge-agent (`pqt-edge`)

Tier 2, Project 8. Runs on the shop's edge box: cameras in, CloudEvents out, nothing lost on the way.
Plan: `docs/plans/tier-2-real-vision.md` (A1–A6).

```
camera (RTSP) ─► CameraReader ─► Perception (pqt-vision) ─► Outbox (SQLite WAL) ─► Uplinker ─► event-core
                 reconnect,       detector → ByteTrack →     every event stored     batches, OAuth,
                 back-off,        zones → emitter            before it is sent;     429/5xx back-off,
                 fps limiter                                 gap-free sequence      dedup makes retries safe
```

- **Ingest** (`ingest.py`): OpenCV's FFmpeg backend (its wheel bundles an LGPL FFmpeg; no PyAV needed),
  5 s open/read timeouts, reconnect with exponential back-off and jitter, frame-rate limiter. Camera URLs
  carry passwords, so they live in secret files and are redacted everywhere.
- **Outbox** (`outbox.py`): `pending → sent | rejected`; transient failures stay pending; sent rows are
  evicted oldest-first under the disk quota; pending rows are never dropped: a full outbox pauses
  perception (`OutboxFull`) and shows in the status file.
- **Uplinker** (`uplinker.py`): drains forever; a cut cable is just a longer back-off (capped at 60 s);
  a batch event-core refuses (e.g. 413) is halved until the bad event is isolated and marked rejected.
- **Config** (`config.py`): YAML plus secret files that must be mode 600 (client secret, track secret,
  camera URLs). A6 replaces the files with a device-sealed store.

## Run and test

```bash
make dev-setup && .venv/bin/pytest -q edge/agent/tests     # unit tests + an in-process outage test
make up && make chaos-edge                                 # A4 cable test (CUT_SECONDS=120; the plan's full run: 1800)
.venv/bin/pqt-edge outbox --path .work/edge/state/outbox.db  # outbox statistics
```
