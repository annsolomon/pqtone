# Vision noise profiles

A noise profile describes how a camera pipeline gets people wrong: missed detections (worse in
crowds), phantom detections, ID switches, one person split into several tracks, and timing
jitter on zone entry and exit. Tier 2 measures one from labelled clips (milestone N1) and the
simulator replays it onto clean tracks (milestone N2), so every rule is scored against realistic
perception errors before it meets a real camera.

| File | What it is |
|---|---|
| `none.yaml` | A perfect camera. The control in the noise matrix. |
| `example.yaml` | The shape of a measured profile, with made-up numbers. |
| `mot17-yolox-s-roi.yaml` | Measured (N1) on MOT17-02/04/09 with the selected V7 pipeline: YOLOX-S on the zone crop, ByteTrack defaults, the V4 zone engine. Used by `make offline-noise`. Open-dataset track (ADR-013): replace with a profile from our own footage at P5. Thin evidence (n < 30): phantom track duration (n = 3) and extra tracks per fragmented person (n = 15). |
| `sim/store_sim/noise_profile.schema.json` | The schema every profile must match (packaged with the simulator). |

## Using one in a scenario

```yaml
faults:
  vision: {profile: ../../config/noise/none.yaml}   # path relative to the scenario file
```

The simulator validates the profile, records its name and SHA-256 in `manifest.json`, and folds
the hash into the run id. Since Tier 2 (N2) it applies the profile to the visits before events are
made: missed visits, timing jitter, fragments and ID switches (new track ids), phantom tracks, and
queue membership rebuilt from the noisy tracks. Ground truth always comes from the clean visits.
Whole visits are missed at `detection.visitMissRate` when the profile has it (measured since N5); older
profiles fall back to `missRate`, a per-frame rate, applied per visit (an upper bound: see
`vision/noise/validation.md`). The manifest counts what was applied (`visionDropped`, `visionFragments`, `visionIdSwitches`,
`visionJittered`, `visionPhantoms`). `none.yaml` changes nothing and draws nothing. Perception noise
runs on its own RNG stream, before the transport faults (`outOfOrder`, `late`, `duplicates`,
`malformed`), so turning it on never changes their draws.

`store-sim matrix --noise-profile <file> --noise-scale 2` runs the whole matrix with every rate
doubled (jitter spread too); `make offline-noise` scores clean, measured and 2x measured side by side (CI runs it as its own job, "offline score under vision noise").

## Privacy

A profile holds rates and fitted distributions only. Never put frames, crops, embeddings,
timestamps of identifiable visits or anything else that could identify a person in it; the
schema rejects unknown fields.
