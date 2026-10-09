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
| `sim/store_sim/noise_profile.schema.json` | The schema every profile must match (packaged with the simulator). |

## Using one in a scenario

```yaml
faults:
  vision: {profile: ../../config/noise/none.yaml}   # path relative to the scenario file
```

The simulator validates the profile, records its name and SHA-256 in `manifest.json`, and folds
the hash into the run id. **In Tier 1 it applies nothing** (`visionNoiseApplied: 0` in the
manifest counts). Perception noise runs on its own RNG stream, before the transport faults
(`outOfOrder`, `late`, `duplicates`, `malformed`), so turning it on never changes their draws.

## Privacy

A profile holds rates and fitted distributions only. Never put frames, crops, embeddings,
timestamps of identifiable visits or anything else that could identify a person in it; the
schema rejects unknown fields.
