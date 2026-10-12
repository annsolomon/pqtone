# Edge agent threat model (Tier 2, milestone A6)

- **Scope:** the edge agent (`pqt/edge/agent`) on a shop's edge box, its cameras, and its link to the
  cloud gateway. Fleet updates, device attestation and model-weight protection are later milestones
  (MASTER-README P2–P4, Tier 5) and are listed under residual risks.
- **Method:** assets, trust boundaries, then threats by STRIDE with the control that answers each.

## Assets

| Asset | Why it matters |
|---|---|
| Live video | People in a shop. Must never leave the box except as a reviewed, blurred incident clip. |
| Events in the outbox | The shop's record of what happened; losing or altering them breaks the product promise. |
| OAuth client secret | Lets anyone publish events as this store. |
| Track secret | Derives the daily pseudonymous `trackId`; with it, ids within a day could be linked across cameras. |
| Camera credentials | RTSP URLs carry the camera's password; with them, anyone on the shop LAN can watch. |

## Trust boundaries

```
cameras (shop LAN) ──RTSP──► edge agent ──HTTPS, outbound only──► gateway ──► event-core
                              │ /config (secrets, read-only)
                              │ /var/lib/pqt-edge (outbox, status)
```

## Threats and controls

| STRIDE | Threat | Control (where) | Tested |
|---|---|---|---|
| Spoofing | Someone publishes events as the store | OAuth client credentials; event-core only accepts sources under the client's prefix (`client-sources`) | e2e-http, e2e-vision |
| Spoofing | A fake camera on the LAN | Camera URL with credentials; one URL per camera from a secret file. (Camera TLS/SRTP is not available on target cameras.) | — |
| Tampering | Events changed or dropped on the way | Outbox before send; event-core dedup on (source, id); same id re-sent unchanged; a changed body comes back as `conflict` and is quarantined | outbox tests, chaos-edge |
| Tampering | A crash or power cut corrupts the outbox | SQLite WAL with `synchronous=FULL`; one transaction per append | kill -9 test (unit and chaos-edge) |
| Repudiation | Not knowing which agent sent what | Per-camera gap-free `sequence`; heartbeats with agent id and version | chaos-edge verdict |
| Information disclosure | Video leaves the box | The agent has no code path that sends frames; only events (no images, boxes or crops) | `pqt_vision` hygiene tests, schema review |
| Information disclosure | Secrets in logs, status files or images | Secrets only from mode-600 files (refused otherwise); camera URLs redacted; config objects hide secrets in `repr`; OpenCV/FFmpeg logging silenced; `.dockerignore` keeps local data and models out of images | config and ingest tests |
| Information disclosure | Re-identifying people | `trackId` = HMAC of a per-day key: unrelated across days and cameras; no appearance features at all in Tier 2 | emitter tests |
| Denial of service | Uplink down for hours | Outbox grows to its quota, then pauses perception (alarm) rather than dropping; back-off capped at 60 s | outbox tests, chaos-edge (120 s in CI; 1800 s by hand) |
| Denial of service | Camera reboot or stream errors | Timeouts, reconnect with back-off and jitter; `camera_down` alarm | ingest tests, chaos-edge camera outage (< 10 s) |
| Elevation of privilege | A compromised agent process | Non-root user, read-only root filesystem, all capabilities dropped, `no-new-privileges`; only `/var/lib/pqt-edge` writable; no inbound ports | compose definition |
| Elevation of privilege | Agent used to reach cloud internals | Internal `edge` network reaches only the gateway; the gateway exposes ingest and auth, not event-core or the database | compose networks |

## Residual risks (accepted for Tier 2, owned by later milestones)

1. **Secrets at rest are files, not sealed to the device.** Anyone with root on the box can read them.
   P4 (licence and lease agent) moves them into a TPM-sealed store.
2. **No signed configuration yet.** Zones and camera configs are local files; Tier 5 fleet config is
   signed and verified on the box.
3. **The clip buffer is not built.** Until it is, no clip can leave the box at all (the safest state);
   when it lands it must keep head blur and reviewed-incident-only export.
4. **Clock trust.** Offset is estimated from the server's `Date` header (±0.5 s) and alarmed above 2 s;
   event times are never rewritten. chrony on the box remains the real fix.
5. **Camera links are plain RTSP on the shop LAN.** Put cameras on their own VLAN; most target cameras
   cannot do better.

## Review (A6 checklist, 12 Oct 2026)

Applied `.claude/agents/security-reviewer.md` to the Tier 2 edge and vision changes:

| Check | Result |
|---|---|
| Privacy | No frames, crops or embeddings leave the agent; fixtures and chaos video are synthetic. |
| Authorization | No new endpoint. Edge events go through the existing ingest with source-prefix checks; the console's live and recent views now show only `com.pqt.store.*`. |
| Injection | SQL in the e2e and chaos checks is parameterised; YAML is `safe_load`; no shell is built from input. |
| Secrets | Mode-600 files, redaction, quiet OpenCV logging; CI uploads `.work/reports` only, never `.work/edge/config`. |
| Least privilege | Agent and simulated camera run with the hardened profile; internal networks; no published ports. |
| Dependencies | No new Python dependency. New image: `bluenviron/mediamtx` (MIT), test-only. |

No blocking findings.
