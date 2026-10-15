"""faults.vision: perception noise from a measured profile (milestone S5 hook, applied since Tier 2 N2).

A scenario opts in with

    faults:
      vision: {profile: ../../config/noise/none.yaml}   # relative to the scenario file

The profile is validated against noise_profile.schema.json and its hash is recorded in the run
manifest and folded into the run id, so two runs with different profiles never share an id.

`apply` turns the clean events into what a camera pipeline with that profile would have reported:

- visits (zone.entered → zone.exited of one track) are missed with probability visitMissRate when the
  profile has one (N5); otherwise missRate(base + perPersonInZone × people already in the zone, capped at
  max; per-zone overrides), a per-frame rate applied per visit, which overstates missed visits;
- the rest get entry/exit timing jitter;
- a visit can fragment (the tracker loses the person and finds them again under a new id) and suffer
  ID switches (Poisson in the visit's length): each splits it into pieces with new track ids; a fragment
  leaves the person untracked for fragmentation.gapMs (N5) before the new piece starts;
- phantom tracks appear at falsePositivesPerMinute with phantomTrackDurationMs durations;
- queue membership (queue.joined → queue.left) follows its visit (dropped, shifted and split with it;
  a phantom joins a queue zone's queue after 5 s, the zone engine's minimum dwell), and queue.length is
  recomputed from the noisy membership, keeping the clean openRegisters.

Ground truth is always derived from the clean events: noise changes what the pipeline sees, never what
happened. Deterministic from the run's vision RNG; a profile with every rate at zero ("none") changes
nothing and draws no random numbers.
"""
from __future__ import annotations

import json
import uuid
from collections import Counter
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import jsonschema
import numpy as np
import yaml

from .config import sha256_bytes
from .events import iso_ms, parse_iso_ms


class NoiseProfileError(ValueError):
    """The profile is missing or does not match the noise-profile schema."""


@dataclass(frozen=True)
class NoiseProfile:
    name: str
    path: Path
    sha256: str
    doc: dict


def _schema() -> dict:
    return json.loads(resources.files("store_sim").joinpath("noise_profile.schema.json").read_text(encoding="utf-8"))


def validate(doc: object) -> list[str]:
    """Schema errors for a parsed profile, as readable strings. Empty means valid."""
    v = jsonschema.Draft202012Validator(_schema())
    errors = sorted(v.iter_errors(doc), key=lambda e: list(e.absolute_path))
    return [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}" for e in errors]


def load(scenario_path: str | Path, vision: dict | None) -> NoiseProfile | None:
    """The profile named by a scenario's faults.vision section, or None when the section is absent."""
    if not vision:
        return None
    if set(vision) != {"profile"}:
        raise NoiseProfileError(f"faults.vision takes exactly one key, 'profile'; got {sorted(vision)}")
    rel = Path(str(vision["profile"]))
    path = rel if rel.is_absolute() else (Path(scenario_path).parent / rel)
    if not path.is_file():
        raise NoiseProfileError(f"noise profile not found: {path}")
    raw = path.read_bytes()
    doc = yaml.safe_load(raw)
    errors = validate(doc)
    if errors:
        raise NoiseProfileError(f"{path} is not a valid noise profile:\n  " + "\n  ".join(errors))
    return NoiseProfile(name=doc["name"], path=path, sha256=sha256_bytes(raw), doc=doc)




NOISE_NS = uuid.UUID("b6a4f0d2-3c1e-4e8a-9f60-2d7c5e1a9b44")
QUEUE_MIN_DWELL_MS = 5000          # the zone engine's default minimum dwell before a queue join (V4)
QUEUE_HANDOVER_MS = 5000           # the zone engine hands a lost member's place to a track appearing within this (N4)
DEFAULT_PHANTOM = {"kind": "lognormal", "mu": 7.6, "sigma": 0.6}


def sample(dist: dict | None, rng: np.random.Generator, default: float = 0.0) -> float:
    if not dist:
        return default
    k = dist["kind"]
    if k == "constant":
        return float(dist["value"])
    if k == "normal":
        return float(rng.normal(dist["mean"], dist["sd"])) if dist["sd"] > 0 else float(dist["mean"])
    if k == "lognormal":
        return float(rng.lognormal(dist["mu"], dist["sigma"]))
    if k == "uniform":
        return float(rng.uniform(dist["min"], dist["max"]))
    if k == "empirical":
        w = np.asarray(dist["weights"], dtype=float)
        return float(dist["values"][int(rng.choice(len(w), p=w / w.sum()))])
    raise NoiseProfileError(f"unknown distribution kind {k}")


def _zero(dist: dict | None) -> bool:
    return not dist or (dist["kind"] == "constant" and dist["value"] == 0) or \
        (dist["kind"] == "normal" and dist["mean"] == 0 and dist["sd"] == 0)


def is_identity(doc: dict) -> bool:
    """True when the profile can change nothing (the control profile)."""
    det, trk, tim = doc["detection"], doc["tracking"], doc["timing"]
    miss = det["missRate"]
    zones_zero = all(z.get("missRate", {}).get("base", 0) == 0 and not z.get("missRate", {}).get("perPersonInZone")
                     for z in doc.get("zones", {}).values())
    visit_zero = det.get("visitMissRate", {}).get("probability", 0) == 0
    return (miss["base"] == 0 and not miss.get("perPersonInZone") and zones_zero and visit_zero
            and det["falsePositivesPerMinute"] == 0
            and trk["idSwitchesPerTrackMinute"] == 0 and trk["fragmentation"]["probability"] == 0
            and _zero(tim["entryJitterMs"]) and _zero(tim["exitJitterMs"]))


def scale(doc: dict, factor: float, *, name: str | None = None) -> dict:
    """The same camera, `factor` times worse: rates and jitter spread multiplied, probabilities capped at 1."""
    import copy

    d = copy.deepcopy(doc)
    d["name"] = name or f"{doc['name']}-x{factor:g}"[:64]
    d["description"] = f"{doc['name']} with every error rate multiplied by {factor:g} (Tier 2 N3)."[:1000]

    def miss(m: dict) -> None:
        m["base"] = min(1.0, m["base"] * factor)
        if "perPersonInZone" in m:
            m["perPersonInZone"] = min(1.0, m["perPersonInZone"] * factor)
        if "max" in m:
            m["max"] = min(1.0, m["max"] * factor)

    miss(d["detection"]["missRate"])
    if "visitMissRate" in d["detection"]:
        vm = d["detection"]["visitMissRate"]
        vm["probability"] = min(1.0, vm["probability"] * factor)
    for z in d.get("zones", {}).values():
        if "missRate" in z:
            miss(z["missRate"])
    d["detection"]["falsePositivesPerMinute"] *= factor
    d["tracking"]["idSwitchesPerTrackMinute"] *= factor
    fr = d["tracking"]["fragmentation"]
    fr["probability"] = min(1.0, fr["probability"] * factor)
    for key in ("entryJitterMs", "exitJitterMs"):
        j = d["timing"][key]
        if j["kind"] == "normal":
            j["sd"] = j["sd"] * factor
        elif j["kind"] == "constant":
            j["value"] = j["value"] * factor
    return d


@dataclass
class _Visit:
    trk: str
    zone: str
    i_in: int
    t_in: int
    i_out: int | None
    t_out: int | None
    queue: list = field(default_factory=list)  # linked _QVisit


@dataclass
class _QVisit:
    trk: str
    queue: str
    i_join: int
    t_join: int
    i_left: int | None
    t_left: int | None
    served: bool


def _miss_p(doc: dict, zone: str, occupancy: int) -> float:
    """Probability that a whole visit is missed: the measured visit miss rate when the profile has one (N5),
    otherwise the per-frame miss rate applied per visit (the pre-N5 upper bound)."""
    vm = doc["detection"].get("visitMissRate")
    if vm is not None:
        return float(vm["probability"])
    m = doc.get("zones", {}).get(zone, {}).get("missRate") or doc["detection"]["missRate"]
    p = m["base"] + m.get("perPersonInZone", 0.0) * occupancy
    return float(min(p, m.get("max", 1.0), 1.0))


class _Builder:
    """Collects rebuilt events with an ordering rank that keeps untouched neighbours in place."""

    def __init__(self, events: list[dict]) -> None:
        self.events = events
        self.items: list[tuple[int, float, dict]] = []
        self.n_new = 0

    def keep(self, i: int) -> None:
        e = self.events[i]
        self.items.append((parse_iso_ms(e["time"]), float(i), e))

    def put(self, template_i: int, t: int, *, type_: str, subject: str, data: dict, rank: float, key: str) -> None:
        tpl = self.events[template_i]
        ev = dict(tpl)
        ev["type"] = "com.pqt.store." + type_
        # zone.entered/exited 1.1.0 add `how` (N4): a track that appeared inside or was lost inside the zone.
        version = "1.1.0" if type_ in ("zone.entered", "zone.exited") and "how" in data else "1.0.0"
        ev["dataschema"] = f"https://schemas.pqt.local/store/{type_}/{version}"
        ev["time"] = iso_ms(t)
        ev["subject"] = subject
        ev["data"] = data
        ev["id"] = str(uuid.uuid5(NOISE_NS, f"{tpl.get('simrunid', '')}:{tpl['storeid']}:{key}"))
        self.items.append((t, rank, ev))
        self.n_new += 1


def _apply_store(evs: list[dict], doc: dict, rng: np.random.Generator, counts: Counter) -> list[tuple[int, float, dict]]:
    times = [parse_iso_ms(e["time"]) for e in evs]
    t_min, t_max = min(times), max(times)
    kind = [e["type"].removeprefix("com.pqt.store.") for e in evs]
    visits: list[_Visit] = []
    open_v: dict[tuple[str, str], tuple[int, int]] = {}
    qvisits: list[_QVisit] = []
    open_q: dict[tuple[str, str], tuple[int, int]] = {}
    qlen_by_queue: dict[str, list[int]] = {}
    other: list[int] = []
    for i, (e, k, t) in enumerate(zip(evs, kind, times)):
        d = e["data"]
        if k == "zone.entered":
            open_v[(d["trackId"], d["zoneId"])] = (i, t)
        elif k == "zone.exited":
            key = (d["trackId"], d["zoneId"])
            if key in open_v:
                i0, t0 = open_v.pop(key)
                visits.append(_Visit(key[0], key[1], i0, t0, i, t))
            else:
                other.append(i)  # an exit without an entry: leave it alone
        elif k == "queue.joined":
            open_q[(d["trackId"], d["queueId"])] = (i, t)
        elif k == "queue.left":
            key = (d["trackId"], d["queueId"])
            if key in open_q:
                i0, t0 = open_q.pop(key)
                qvisits.append(_QVisit(key[0], key[1], i0, t0, i, t, bool(d.get("served", True))))
            else:
                other.append(i)
        elif k == "queue.length":
            qlen_by_queue.setdefault(d["queueId"], []).append(i)
        else:
            other.append(i)
    visits += [_Visit(trk, z, i, t, None, None) for (trk, z), (i, t) in open_v.items()]
    qvisits += [_QVisit(trk, q, i, t, None, None, True) for (trk, q), (i, t) in open_q.items()]
    visits.sort(key=lambda v: (v.t_in, v.trk, v.zone))
    for qv in sorted(qvisits, key=lambda q: (q.t_join, q.trk, q.queue)):
        host = next((v for v in visits if v.trk == qv.trk and v.t_in <= qv.t_join
                     and (v.t_out is None or qv.t_join <= v.t_out)), None)
        if host is not None:
            host.queue.append(qv)
    queue_zone = Counter()
    for v in visits:
        for qv in v.queue:
            queue_zone[(v.zone, qv.queue)] += 1
    zone_queue: dict[str, str] = {}
    for (z, q), _ in queue_zone.most_common():
        zone_queue.setdefault(z, q)
    linked = {id(qv) for v in visits for qv in v.queue}

    b = _Builder(evs)
    for i in other:
        b.keep(i)
    noisy_q: list[tuple[int, int, float, str]] = []  # (t, +1/-1, rank, queue) for queue.length rebuild

    # occupancy of each zone just before each visit starts (clean)
    occ: dict[int, int] = {}
    by_zone: dict[str, list[_Visit]] = {}
    for v in visits:
        by_zone.setdefault(v.zone, []).append(v)
    for z, vs in by_zone.items():
        for v in vs:
            occ[id(v)] = sum(1 for o in vs if o is not v and o.t_in <= v.t_in and (o.t_out is None or o.t_out > v.t_in))

    fr = doc["tracking"]["fragmentation"]
    sw_rate = doc["tracking"]["idSwitchesPerTrackMinute"]
    tim = doc["timing"]
    n_new_id = 0

    def new_id() -> str:
        nonlocal n_new_id
        n_new_id += 1
        return f"trk-n{int(rng.integers(0, 2 ** 40)):010x}"

    for v in visits:
        if rng.random() < _miss_p(doc, v.zone, occ[id(v)]):
            counts["visionDropped"] += 1
            continue
        j_in = int(round(sample(tim["entryJitterMs"], rng)))
        j_out = int(round(sample(tim["exitJitterMs"], rng))) if v.t_out is not None else 0
        t_in = min(max(t_min, v.t_in + j_in), t_max)
        if j_in or j_out:
            counts["visionJittered"] += 1
        t_out = None
        if v.t_out is not None:
            t_out = min(max(v.t_out + j_out, t_in + 1), t_max)
            if t_out <= t_in:
                t_out = t_in  # squeezed against the end of the run
        splits: list[int] = []
        gaps: dict[int, int] = {}  # fragment split time -> how long nobody tracks the person (N5)
        if t_out is not None and t_out - t_in > 2:
            if fr["probability"] > 0 and rng.random() < fr["probability"]:
                extra = max(1, int(round(sample(fr.get("extraTracks"), rng, 1.0))))
                frag_at = [int(t_in + 1 + rng.random() * (t_out - t_in - 2)) for _ in range(extra)]
                splits += frag_at
                if fr.get("gapMs"):  # drawn only for profiles that carry it: older profiles replay unchanged
                    for s_ in frag_at:
                        gaps[s_] = max(gaps.get(s_, 0), max(0, int(round(sample(fr["gapMs"], rng)))))
                counts["visionFragments"] += extra
            if sw_rate > 0:
                n_sw = int(rng.poisson(sw_rate * (t_out - t_in) / 60000.0))
                splits += [int(t_in + 1 + rng.random() * (t_out - t_in - 2)) for _ in range(n_sw)]
                counts["visionIdSwitches"] += n_sw
        bounds = [t_in] + sorted(splits) + ([t_out] if t_out is not None else [])
        ids = [v.trk] + [new_id() for _ in splits]
        base_rank = float(v.i_in)
        for k, trk in enumerate(ids):
            start = bounds[k]
            end = bounds[k + 1] if k + 1 < len(bounds) else None
            if k and gaps.get(start):  # a fragment: the new track starts after the untracked gap
                start = min(start + gaps[start], t_max) if end is None else max(start, min(start + gaps[start], end - 1))
            tpl_in = v.i_in
            # A piece after a split starts inside the zone ("appeared"); one before a split ends inside it
            # ("lost"), as the zone engine reports a track it lost (N4). Whole visits keep the plain shape.
            data_in = {"zoneId": v.zone, "trackId": trk, **({"how": "appeared"} if k else {})}
            b.put(tpl_in, start, type_="zone.entered", subject=f"track:{trk}", data=data_in,
                  rank=base_rank + k * 1e-3, key=f"{evs[v.i_in]['id']}:in:{k}")
            if end is not None:
                tpl_out = v.i_out if v.i_out is not None else v.i_in
                data_out = {"zoneId": v.zone, "trackId": trk, **({"how": "lost"} if k + 1 < len(ids) else {})}
                b.put(tpl_out, end, type_="zone.exited", subject=f"track:{trk}", data=data_out,
                      rank=float(v.i_out if v.i_out is not None else v.i_in) - (len(ids) - k) * 1e-3,
                      key=f"{evs[v.i_in]['id']}:out:{k}")
        for qv in v.queue:
            j0 = max(t_in, min(qv.t_join + j_in, t_out if t_out is not None else t_max))
            j1 = None
            if qv.t_left is not None:
                j1 = qv.t_left + j_out
                if t_out is not None:
                    j1 = min(j1, t_out)
                j1 = max(j1, j0)
            cuts = [s for s in sorted(splits) if j0 < s < (j1 if j1 is not None else t_max + 1)]
            qb = [j0] + cuts + ([j1] if j1 is not None else [])
            for k in range(len(qb) - (0 if j1 is None else 1)):
                piece = ids[sum(1 for s in sorted(splits) if s <= qb[k])]
                t_join = qb[k]
                if k:
                    # A new track joins the queue as the zone engine lets it (N4): at once if it appears within
                    # the hand-over window of the lost member (it takes that member's place), otherwise after
                    # the minimum dwell. A piece too short to join never joins, so it never leaves either.
                    nxt = qb[k + 1] if k + 1 < len(qb) else None
                    gap = gaps.get(qb[k], 0)
                    t_join = qb[k] + gap + (0 if gap <= QUEUE_HANDOVER_MS else QUEUE_MIN_DWELL_MS)
                    if nxt is not None and t_join >= nxt:
                        continue
                    t_join = min(t_join, t_max)
                b.put(qv.i_join, t_join, type_="queue.joined", subject=f"queue:{qv.queue}",
                      data={"queueId": qv.queue, "trackId": piece}, rank=float(qv.i_join) + k * 1e-3,
                      key=f"{evs[qv.i_join]['id']}:join:{k}")
                noisy_q.append((t_join, +1, float(qv.i_join) + k * 1e-3, qv.queue))
                if k + 1 < len(qb):
                    last = k + 1 == len(qb) - 1 and j1 is not None
                    t_left = qb[k + 1]
                    if not last and gaps.get(qb[k + 1], 0) <= QUEUE_HANDOVER_MS:
                        # The next piece takes this member's place when it appears: the zone engine's hand-over
                        # (N4) dates the old member's leave at that moment, so the queue length does not dip.
                        nxt2 = qb[k + 2] if k + 2 < len(qb) else None
                        t_left = qb[k + 1] + gaps.get(qb[k + 1], 0)
                        t_left = min(t_left, t_max) if nxt2 is None else max(qb[k + 1], min(t_left, nxt2 - 1))
                    b.put(qv.i_left if qv.i_left is not None else qv.i_join, t_left, type_="queue.left",
                          subject=f"queue:{qv.queue}",
                          data={"queueId": qv.queue, "trackId": piece, "served": qv.served if last else False},
                          rank=float(qv.i_left if qv.i_left is not None else qv.i_join) - 1e-3 * (len(qb) - k),
                          key=f"{evs[qv.i_join]['id']}:left:{k}")
                    noisy_q.append((t_left, -1, float(qv.i_left or qv.i_join), qv.queue))

    # queue visits whose track had no matching zone visit: keep as they are
    for qv in qvisits:
        if id(qv) not in linked:
            b.keep(qv.i_join)
            noisy_q.append((qv.t_join, +1, float(qv.i_join), qv.queue))
            if qv.i_left is not None:
                b.keep(qv.i_left)
                noisy_q.append((qv.t_left, -1, float(qv.i_left), qv.queue))

    # phantom tracks
    fpm = doc["detection"]["falsePositivesPerMinute"]
    zones = sorted(by_zone)
    if fpm > 0 and zones and t_max > t_min:
        tpl_any = next(v.i_in for v in visits)
        for n in range(int(rng.poisson(fpm * (t_max - t_min) / 60000.0))):
            z = zones[int(rng.integers(len(zones)))]
            start = int(t_min + rng.random() * (t_max - t_min))
            dur = max(1, int(sample(doc["detection"].get("phantomTrackDurationMs") or DEFAULT_PHANTOM, rng)))
            end = min(start + dur, t_max)
            trk = new_id()
            b.put(tpl_any, start, type_="zone.entered", subject=f"track:{trk}",
                  data={"zoneId": z, "trackId": trk, "how": "appeared"}, rank=len(evs) + n, key=f"phantom:{n}:in")
            b.put(tpl_any, end, type_="zone.exited", subject=f"track:{trk}", data={"zoneId": z, "trackId": trk, "how": "lost"},
                  rank=len(evs) + n + 0.5, key=f"phantom:{n}:out")
            q = zone_queue.get(z)
            if q is not None and end - start >= QUEUE_MIN_DWELL_MS:
                b.put(tpl_any, start + QUEUE_MIN_DWELL_MS, type_="queue.joined", subject=f"queue:{q}",
                      data={"queueId": q, "trackId": trk}, rank=len(evs) + n + 0.1, key=f"phantom:{n}:join")
                b.put(tpl_any, end, type_="queue.left", subject=f"queue:{q}",
                      data={"queueId": q, "trackId": trk, "served": False}, rank=len(evs) + n + 0.4,
                      key=f"phantom:{n}:left")
                noisy_q += [(start + QUEUE_MIN_DWELL_MS, +1, len(evs) + n + 0.1, q), (end, -1, len(evs) + n + 0.4, q)]
            counts["visionPhantoms"] += 1

    # queue.length from the noisy membership, with the clean register counts
    for q, idx in qlen_by_queue.items():
        regs = [(times[i], float(i), evs[i]["data"]["openRegisters"]) for i in idx]
        changes = sorted([(t, r, ("m", dn)) for t, dn, r, qq in noisy_q if qq == q] +
                         [(t, r, ("r", o)) for t, r, o in regs])
        length, open_regs, last = 0, regs[0][2], None
        for n, (t, r, (what, val)) in enumerate(changes):
            if what == "m":
                length = max(0, length + val)
            else:
                open_regs = val
            if n + 1 < len(changes) and changes[n + 1][0] == t:
                continue  # one length per instant: a hand-over (one out, one in) does not move the queue (N4)
            if (length, open_regs) != last:
                b.put(idx[0], t, type_="queue.length", subject=f"queue:{q}",
                      data={"queueId": q, "length": length, "openRegisters": open_regs}, rank=r + 5e-4,
                      key=f"qlen:{q}:{n}")
                last = (length, open_regs)
    return b.items


def apply(events: list[dict], profile: NoiseProfile | None,
          rng: np.random.Generator) -> tuple[list[dict], dict]:
    """What the camera pipeline would have reported. Ground truth is not affected (see the module doc)."""
    if profile is None:
        return events, {}
    if is_identity(profile.doc):
        return events, {"visionNoiseApplied": 0}
    counts: Counter = Counter()
    stores: dict[str, list[int]] = {}
    for n, e in enumerate(events):
        stores.setdefault(e.get("storeid", ""), []).append(n)
    items: list[tuple[int, float, dict]] = []
    for sid in sorted(stores):  # fixed order: the RNG stream does not depend on dict ordering
        gidx = stores[sid]
        for t, r, e in _apply_store([events[n] for n in gidx], profile.doc, rng, counts):
            whole = int(r)
            # local rank → global position, so untouched events of every store keep their relative order
            g = gidx[whole] + (r - whole) if whole < len(gidx) else len(events) + r
            items.append((t, g, e))
    items.sort(key=lambda x: (x[0], x[1]))
    out = []
    for n, (_, _, e) in enumerate(items):
        e = dict(e)
        e["sequence"] = f"{n:010d}"
        out.append(e)
    counts["visionNoiseApplied"] = sum(counts[k] for k in ("visionDropped", "visionPhantoms", "visionFragments",
                                                            "visionIdSwitches", "visionJittered"))
    return out, dict(counts)
