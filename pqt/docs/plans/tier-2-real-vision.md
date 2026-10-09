# Tier 2: Real vision

**Goal.** Replace the simulator with cameras, without changing anything downstream. Cameras produce the *same CloudEvents* as store-sim, so event-core, rules-engine, the console and the scorer keep working.

This tier proves three things:
1. the vision is accurate enough;
2. the rules survive realistic vision noise;
3. the edge never loses data.

**Where it runs.**

| Work | Machine |
|---|---|
| vision-lab training and evaluation, mtmc-fusion | A GPU rented by the hour |
| Code, tests, pre-computed detections | Codespaces (CPU) |
| edge-agent | Codespaces, with simulated cameras |

**Exit gate (T2 → T3).**
- Queue count within ±1 of manual on at least 95% of samples, across 3 clips.
- The edge chaos test passes with zero loss.
- Rules re-scored under the measured noise profile, with thresholds held or revised in an ADR.
- Privacy controls in place: blur, no identity, ephemeral embeddings.

---

## Before you start: data, licences, privacy (do this first)

1. **Footage.** Record your own footage *with consent*:
   - a friend's or family shop, or a mock checkout with volunteers;
   - signage visible, written consent from the people recorded and from the premises owner.

   Aim for 3–6 clips of 20–30 minutes from 1–3 fixed cameras: an overhead-ish angle on the checkout queue, plus one aisle.
2. **Public datasets.** Use them for learning and benchmarks only. MOT17, MOT20 and CrowdHuman are licensed **non-commercial**. Do not ship a model trained on them.
3. **Detector licence.**
   - Ultralytics YOLO (v8, 11) is **AGPL-3.0**. Using it inside a commercial SaaS needs an Ultralytics enterprise licence or full AGPL compliance.
   - Apache-2.0 alternatives: **YOLOX**, **RT-DETR** (Hugging Face Transformers or PaddleDetection), **RTMDet** (MMDetection).
   - **ByteTrack** is MIT; **torchreid / OSNet** is MIT.
   - Decide in ADR-012 before writing code. Recommendation: learn on Ultralytics if you like the tooling, but keep the detector behind an interface, and ship on an Apache-2.0 model.
4. **Annotation.** Use **CVAT** (MIT, self-hostable). Label only what the metric needs: people boxes on sampled frames, and manual queue counts every 10 s.
5. **Privacy by design.**
   - Raw video never leaves the edge or GPU box except as consented research data.
   - Store detections and tracks, not frames.
   - Faces are blurred in any clip that is exported.
   - Write `docs/privacy/vision-data.md`: what you collect, why, how long you keep it, and who can see it.

---

## Project 6: vision-lab (`vision/lab/`)

**Learn:** detection, multi-object tracking, homography, zone geometry, evaluation.

**Done when:** queue count is within ±1 of a manual count.

The precise metric: on each of 3 held-out clips, at least 95% of 10-second samples have |predicted − manual| ≤ 1, and the mean absolute error is ≤ 0.5.

**Pipeline.**

```
video/RTSP → decode (PyAV) → detector (people) → ByteTrack → foot point (bbox bottom-centre)
  → homography H (image px → floor metres) → zone polygons (shapely) → per-track zone state
  → event emitter: zone.entered / zone.exited / queue.joined / queue.left / queue.length
  → CloudEvents (same schemas, source urn:pqt:edge:<store>:<camera>) → file | HTTP ingest
```

| # | Milestone | Done when | 🧠 |
|---|---|---|---|
| V1 | **Detector interface + offline runner.** `Detector.detect(frame) -> boxes` with two backends: your chosen licensed model, and a "replay" backend that reads pre-computed detections from Parquet. Lets you develop on CPU in Codespaces. | Runs on a clip end to end; detections cached to Parquet on the GPU box, replayed in Codespaces. | |
| V2 | **Tracking.** ByteTrack on top; tune `track_thresh`, `match_thresh`, buffer. Evaluate on your labelled frames with **TrackEval** (HOTA, IDF1, MOTA). | Metrics table in `vision/lab/reports/`. | |
| V3 | **Homography.** Calibrate by clicking 4+ floor points with known metre positions (CLI tool now; becomes the onboarding wizard in Tier 3). Compute H with `cv2.findHomography` (RANSAC). Report reprojection error. | Error < 15 cm on held-out points; config saved as `config/cameras/<cam>.yaml`. | 🧠 The homography maths |
| V4 | **Zones and queue logic.** Polygons in floor metres. Queue membership = in the queue polygon **and** tracked for at least N s **and** moving below a speed threshold. Hysteresis on enter and exit to avoid boundary flicker. | Unit tests with synthetic tracks: boundary jitter does not toggle membership. | 🧠 The membership state machine |
| V5 | **Event emitter.** Emits the existing schemas. `trackId` is a pseudonymous per-camera id that rotates daily. Sends through the edge path (file now, edge-agent later). | Events pass `schemas/` validation; event-core accepts them. | |
| V6 | **Ground truth from video.** Manual queue counts every 10 s as CSV → converted to the scorer's ground-truth format. The scorer gets a `count` mode (time-series error metrics) next to episode matching. | `pqt-scorer video --gt counts.csv --events events.jsonl` prints MAE and the % within ±1. | |
| V7 | **Hit the bar.** Iterate on model size, input resolution, confidence, zone polygon and membership timing. Log every run in `vision/lab/experiments.md`: parameters, metric, commit. | ±1 on at least 95% of samples on 3 held-out clips. Tag `t2-vision-lab-v1`. | |
| V8 | **Rules on real events.** Replay video events through the full stack (`make up`, then the HTTP sink). Score queue incidents against episodes derived from the manual counts with the same rule spec. | Precision and recall reported for real video. | |

**GPU workflow.**
- Use one Docker image (`vision/lab/Dockerfile.gpu`, CUDA base) for everything on the GPU.
- Script `scripts/gpu-run.sh` with these steps:
  1. rsync the code and clips to the box;
  2. run the experiment;
  3. pull back `reports/` and the Parquet detections;
  4. **destroy the box**.
- Billing is by the hour. Never leave it running, and never upload identifiable footage to a provider without a data-processing agreement.

---

## Project 7: noise-model (`vision/noise/`)

**Goal.** Make the simulator lie the way cameras lie, so every future rule is tested against realistic perception errors.

| # | Milestone | Done when |
|---|---|---|
| N1 | **Measure.** From labelled clips, compute per zone and per camera: miss rate vs crowd density, false-positive rate, ID switches per track-minute, fragmentation (one person → k tracks), entry/exit timing jitter, queue-count error distribution. | `noise-profile.yaml` with fitted distributions and their sample sizes. |
| N2 | **Inject.** store-sim's `faults.vision` applies the profile to clean tracks: drops, phantom short tracks, split tracks with new ids, timing jitter. Deterministic from the seed. 🧠 | Same seed + profile → identical output (test). |
| N3 | **Re-score the matrix** with noise: every scenario × seed × {clean, measured, 2× measured}. | The report shows which rules degrade, and by how much. |
| N4 | **Harden rules**, for example: minimum track age before a dwell starts, debounce on queue length, dwell based on a zone occupancy session rather than a single track. Each change goes through the scorer. Thresholds change only by ADR. | Rules meet thresholds under the measured profile, or an ADR documents the revised thresholds. |
| N5 | **Validate the noise model.** Does sim + profile reproduce the *distribution* of errors seen on real clips? Use a KS test on queue-count error. | Report in `vision/noise/validation.md`. |

---

## Project 8: edge-agent (`edge/agent/`)

**Learn:** store-and-forward, RTSP, resilience engineering.

**Done when:** you pull the network cable, nothing is lost, and the agent backfills on reconnect.

**Design.** One Docker Compose bundle runs on the edge box (an x86 mini-PC; later a Jetson). Containers:
- `camera-ingest` per camera: RTSP via PyAV/FFmpeg with reconnect and exponential back-off and jitter, plus a frame-rate limiter.
- `perception`: vision-lab as a library.
- `outbox`: **SQLite in WAL mode**. Every event is written before it is sent. Each camera has a monotonic `sequence`.
- `uplink`: batches to `/v1/events/batch` with OAuth client credentials, using the existing API. Event-core dedup makes retries safe. Marks rows sent only on 200 with a per-item `accepted` or `duplicate`.
- `heartbeat`: a new schema `com.pqt.edge.heartbeat` carrying camera fps, decode errors, outbox depth, disk free, temperature and clock offset.
- `clip-buffer`: rolling 10 s segments (FFmpeg segment muxer) for N minutes on local disk. A clip is exported **only** on an authorised request for a reviewed incident, with blur applied.
- `config`: pulls signed zone and camera config. Signatures are verified locally (preparing for fleet in Tier 5).

| # | Milestone | Done when | 🧠 |
|---|---|---|---|
| A1 | **Simulated cameras.** **MediaMTX** (MIT) serves recorded clips as RTSP streams in compose (`--profile edge`). No hardware needed in Codespaces. | `ffprobe rtsp://mediamtx:8554/cam1` works. | |
| A2 | **Ingest resilience.** Reconnect on stream drop, decoder errors and camera reboot. Metrics for each. | A chaos test that kills MediaMTX mid-stream recovers in under 10 s. | |
| A3 | **SQLite outbox + uplink.** Write-ahead, batching, retries with back-off, 429 and `Retry-After` handling, disk quota with oldest-first eviction *of already-sent rows only*. When disk is full, stop perception rather than drop events, and raise an alarm. | Unit tests for every state transition. | 🧠 The outbox state machine |
| A4 | **The cable test.** A `toxiproxy` or `docker network disconnect` scenario: cut the uplink for 30 minutes during a sim/clip replay, reconnect, wait. Verify every per-camera `sequence` from first to last is present in Postgres exactly once. Also cover: power loss (`kill -9` the agent mid-batch), clock jump, a full disk. | `make chaos-edge` green: 0 missing, 0 duplicate rows. Tag `t2-edge-agent-v1`. | |
| A5 | **Time.** chrony/NTP status in the heartbeat; events carry camera time *and* agent receive time. Alarm if the offset exceeds 2 s; never silently rewrite event time. | Clock-skew test. | |
| A6 | **Security baseline.** Non-root containers. Credentials in an encrypted file sealed to the device (a TPM if available; otherwise file permissions plus an install-time secret). Outbound-only connections — no inbound ports. Camera passwords never logged. | security-reviewer pass; `docs/edge/threat-model.md`. | |

---

## Project 9: mtmc-fusion (`vision/fusion/`)

**Learn:** re-identification embeddings, camera topology, global association.

**Goal.** Global track ids across cameras within one store visit, so dwell and journeys span cameras.

**Privacy constraints (non-negotiable; write them as tests).**
- Embeddings live in memory only, with a TTL of minutes. They are never persisted and never sent upstream.
- Global ids are random per visit, never stable across days, never linked across sites.
- No faces: crop bodies only, and blur heads in any debug output.
- A test asserts that no embedding field exists in any schema, table or topic.

| # | Milestone | Done when |
|---|---|---|
| F1 | **Camera topology graph.** Nodes are cameras or zones; edges carry allowed transitions with min/max transit times, learned from data or configured. Overlapping fields of view share floor coordinates through the homographies. | `config/topology/<store>.yaml` plus a visualiser. |
| F2 | **Embeddings.** OSNet (torchreid, MIT) on the GPU; batched; cached per track as an EMA over good-quality crops. | Embedding extraction benchmarked (ms per crop). |
| F3 | **Association.** Hungarian matching on cost = appearance distance + spatiotemporal infeasibility penalty + floor-position distance (for overlapping views). Gating by the topology graph. 🧠 | IDF1 and HOTA on a multi-camera clip (your own recording, or a licensed benchmark for learning). |
| F4 | **Online service.** Consumes per-camera track events and emits `com.pqt.store.track.linked` (new schema: local track ids → visit id). Downstream dwell and journey rules use visit ids. | End-to-end on a 2–3 camera recording. |
| F5 | **Score it.** The simulator gains multi-camera layouts with ground-truth visit ids; noise-model adds ReID confusion. | Scorer reports cross-camera IDF1; rules stay above threshold. |

mtmc-fusion is the hardest research item in the ladder. **A single-camera pilot does not need it**, so don't let it block Tier 3.
