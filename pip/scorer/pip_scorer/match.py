"""One-to-one matching of detected incidents to ground-truth episodes."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class RuleScore:
    rule_id: str
    tp: int = 0
    fp: int = 0
    fn: int = 0
    latencies_ms: list[int] = field(default_factory=list)
    unmatched_gt: list[dict] = field(default_factory=list)
    unmatched_incidents: list[dict] = field(default_factory=list)

    def merge(self, other: "RuleScore") -> None:
        self.tp += other.tp
        self.fp += other.fp
        self.fn += other.fn
        self.latencies_ms += other.latencies_ms
        self.unmatched_gt += other.unmatched_gt
        self.unmatched_incidents += other.unmatched_incidents

    @property
    def precision(self) -> float:
        return 1.0 if self.tp + self.fp == 0 else self.tp / (self.tp + self.fp)

    @property
    def recall(self) -> float:
        return 1.0 if self.tp + self.fn == 0 else self.tp / (self.tp + self.fn)

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 0.0 if p + r == 0 else 2 * p * r / (p + r)

    def latency_pct(self, pct: float) -> int | None:
        if not self.latencies_ms:
            return None
        xs = sorted(self.latencies_ms)
        idx = min(len(xs) - 1, max(0, int(round(pct / 100.0 * (len(xs) - 1)))))
        return xs[idx]

    def to_dict(self, z: float = 1.96) -> dict:
        from .gates import wilson  # local import: gates has no dependency on match
        n_p, n_r = self.tp + self.fp, self.tp + self.fn
        return {
            "ruleId": self.rule_id, "tp": self.tp, "fp": self.fp, "fn": self.fn,
            "precision": round(self.precision, 4), "recall": round(self.recall, 4), "f1": round(self.f1, 4),
            "nPrecision": n_p, "nRecall": n_r,
            "precisionCI": [round(x, 4) for x in wilson(self.tp, n_p, z)],
            "recallCI": [round(x, 4) for x in wilson(self.tp, n_r, z)],
            "latencyMs": {"p50": self.latency_pct(50), "p95": self.latency_pct(95),
                          "max": max(self.latencies_ms) if self.latencies_ms else None},
        }


def score(ground_truth: list[dict], incidents: list[dict], *, tolerance_before_ms: int,
          max_latency_ms: int, rule_ids: list[str]) -> dict[str, RuleScore]:
    """Greedy one-to-one assignment by smallest time distance within the match window.

    Only OPENED incidents count as detections. Groups are (ruleId, storeId, key).
    """
    gt_groups: dict[tuple, list[dict]] = defaultdict(list)
    inc_groups: dict[tuple, list[dict]] = defaultdict(list)
    for g in ground_truth:
        gt_groups[(g["ruleId"], g["storeId"], g["key"])].append(g)
    for i in incidents:
        if i.get("kind", "OPENED") != "OPENED":
            continue
        inc_groups[(i["ruleId"], i["storeId"], i["key"])].append(i)

    scores = {r: RuleScore(r) for r in rule_ids}
    for group in sorted(set(gt_groups) | set(inc_groups)):
        rule = group[0]
        s = scores.setdefault(rule, RuleScore(rule))
        gts, incs = gt_groups.get(group, []), inc_groups.get(group, [])
        pairs = []
        for gi, g in enumerate(gts):
            for ii, inc in enumerate(incs):
                delta = inc["detectedMs"] - g["atMs"]
                if -tolerance_before_ms <= delta <= max_latency_ms:
                    pairs.append((abs(delta), g["atMs"], gi, ii, delta))
        pairs.sort()
        used_g, used_i = set(), set()
        for _, _, gi, ii, delta in pairs:
            if gi in used_g or ii in used_i:
                continue
            used_g.add(gi)
            used_i.add(ii)
            s.tp += 1
            s.latencies_ms.append(delta)
        for gi, g in enumerate(gts):
            if gi not in used_g:
                s.fn += 1
                s.unmatched_gt.append(g)
        for ii, inc in enumerate(incs):
            if ii not in used_i:
                s.fp += 1
                s.unmatched_incidents.append(inc)
    return scores
