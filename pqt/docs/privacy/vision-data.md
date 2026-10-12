# Vision data: what PQT collects, why, how long, who sees it

- **Owner:** the PQT data owner (today, the founder)
- **Applies to:** Tier 2 research footage, the edge agent, and everything derived from camera video
- **Law:** India's Digital Personal Data Protection Act, 2023 (DPDP). Video of people is personal data.
  This document is a working policy, not legal advice; it is reviewed by a lawyer before the first recording.

## 1. Purpose

PQT counts people in zones (queues, aisles) and measures how long they wait, so a shop can open a till or
send help in time. It never identifies a person. Every item below serves that purpose and nothing else.

## 2. What is collected, where it lives, and for how long

| Data | Contains people? | Where it lives | Kept for | Who can see it |
|---|---|---|---|---|
| Research footage (Tier 2, P4) | Yes: video of consenting people | The recording laptop or edge box (encrypted disk); Google Drive and Colab only while a run needs it | Until the Tier 2 gate passes, and at most 180 days from recording; deleted sooner on request | The data owner only |
| Live camera video (pilot, edge) | Yes | The edge box's encrypted ring buffer (10 s segments) | Minutes (the ring overwrites itself) | Nobody, unless a reviewed incident exports a clip |
| Exported incident clip | Yes, heads blurred | event-core storage | 30 days, then deleted | Reviewers of that store |
| Detections (boxes, scores) | No identity; positions only | Edge box; Parquet caches in the git-ignored `vision/lab/data/` | Research: as footage. Live: never leave the edge | The data owner |
| Tracks | A pseudonymous id that rotates daily; positions over time | Edge box memory | The current day | Nobody outside the process |
| Events (`zone.entered`, `queue.length`, …) | A daily-rotating pseudonymous `trackId`, zone ids, counts, times | event-core, Postgres, Kafka | Per tenant retention (default 1 year) | The tenant's users |
| ReID embeddings (mtmc-fusion only) | Appearance features | Process memory only, TTL of minutes | Minutes; never persisted, never sent upstream | Nobody |
| Benchmark reports (MOT17) | No: aggregate metrics only | The `vision-results` branch | Indefinitely | Public (the repository is public) |

## 3. What is never collected

- Faces as faces: no face recognition, face embeddings, age, gender, ethnicity or emotion inference.
- Identity across days or sites: `trackId` is derived from a per-camera secret that rotates every day,
  so the same person gets an unrelated id tomorrow and at another shop.
- Frames or crops in the cloud, except a blurred clip exported for a reviewed incident.
- Audio. Cameras are configured with audio off.

## 4. Consent and notice

- Research footage is recorded only with (a) the premises owner's written consent, (b) visible signage
  during recording, and (c) a signed form from every person who is recorded (`consent-form.md`).
- The form names every place the footage may be processed: the recording device, Google Drive and
  Google Colab (for GPU runs), and E2E Networks (India) as a fallback. Footage goes nowhere else.
- In a live pilot, the shop displays a notice at the entrance naming the purpose, the operator and a
  contact for requests.

## 5. Rights and requests

- **Withdrawal or deletion:** a person may withdraw at any time. Their footage is deleted from every
  location in section 2 within 7 days, along with any detections and reports derived from it that could
  be tied to that clip.
- **Contact:** the data owner's email, printed on the consent form and the shop notice.
- **Breach:** a suspected leak of footage is reported to the people affected and, as the DPDP Act requires,
  to the Data Protection Board, without undue delay.

## 6. Public datasets

MOT17 is used for benchmarks only, under ADR-013: downloaded on a GitHub runner, never stored elsewhere,
and only aggregate results leave the runner.

## 7. Engineering controls (tested)

- No schema, table or topic has an embedding or image field (a test asserts this from mtmc-fusion on).
- `vision/lab/data/` is git-ignored; CI fails if a video or image file is committed under `vision/`.
- `trackId` rotation is unit-tested (V5).
- Clip export requires a reviewed incident and applies head blur (edge-agent clip-buffer).
