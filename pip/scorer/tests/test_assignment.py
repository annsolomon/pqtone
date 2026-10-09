"""Milestone Q5: Hungarian matching, checked against brute force on every small case we can afford."""
from __future__ import annotations

import itertools
import random

import pytest

from pip_scorer.assignment import hungarian, max_matching_min_distance
from pip_scorer.match import score

WINDOW = (30_000, 120_000)   # tolerance before, max latency after (thresholds.yaml)


def brute_force(n_rows: int, n_cols: int, allowed: dict) -> tuple[int, int]:
    """(most pairs, least total distance) over every partial one-to-one matching."""
    best = (0, 0)
    edges = sorted(allowed)
    for k in range(min(n_rows, n_cols), 0, -1):
        found = None
        for combo in itertools.combinations(edges, k):
            rows = {r for r, _ in combo}
            cols = {c for _, c in combo}
            if len(rows) == k and len(cols) == k:
                total = sum(allowed[e] for e in combo)
                found = total if found is None else min(found, total)
        if found is not None:
            return (k, found)
    return best


def test_square_and_rectangular_minimum_cost():
    assert hungarian([[4, 1, 3], [2, 0, 5], [3, 2, 2]]) == [1, 0, 2]
    assert hungarian([[7, 3, 9, 1]]) == [3]
    assert hungarian([]) == []
    with pytest.raises(ValueError):
        hungarian([[1], [2]])


def test_greedy_would_lose_a_match_here():
    # Ground truth at 0 s and 60 s; incidents at 50 s and 130 s. Closest-first pairs 60<->50 (10 s)
    # and then nothing is left in range for 0 s, so greedy finds 1 match. The optimum finds 2.
    gts = [{"ruleId": "R", "storeId": "s", "key": "k", "atMs": t} for t in (0, 60_000)]
    incs = [{"ruleId": "R", "storeId": "s", "key": "k", "detectedMs": t} for t in (50_000, 130_000)]
    s = score(gts, incs, tolerance_before_ms=WINDOW[0], max_latency_ms=WINDOW[1], rule_ids=["R"])["R"]
    assert (s.tp, s.fp, s.fn) == (2, 0, 0)
    assert sorted(s.latencies_ms) == [50_000, 70_000]


def test_ties_between_equal_matchings_are_deterministic():
    allowed = {(0, 0): 5, (0, 1): 5, (1, 0): 5, (1, 1): 5}
    first = max_matching_min_distance(2, 2, allowed)
    assert all(max_matching_min_distance(2, 2, dict(allowed)) == first for _ in range(20))


@pytest.mark.parametrize("seed", range(300))
def test_matches_brute_force_on_random_small_cases(seed):
    rnd = random.Random(seed)
    n, m = rnd.randint(0, 5), rnd.randint(0, 5)
    gts = sorted(rnd.randint(0, 600) * 1000 for _ in range(n))
    incs = sorted(rnd.randint(0, 600) * 1000 for _ in range(m))
    allowed = {}
    for gi, g in enumerate(gts):
        for ii, t in enumerate(incs):
            d = t - g
            if -WINDOW[0] <= d <= WINDOW[1]:
                allowed[(gi, ii)] = abs(d)
    pairs = max_matching_min_distance(n, m, allowed)
    # a valid matching: allowed edges only, no row or column twice
    assert all(p in allowed for p in pairs)
    assert len({r for r, _ in pairs}) == len(pairs) == len({c for _, c in pairs})
    assert (len(pairs), sum(allowed[p] for p in pairs)) == brute_force(n, m, allowed)


@pytest.mark.parametrize("seed", range(100))
def test_never_fewer_matches_than_greedy(seed):
    rnd = random.Random(10_000 + seed)
    gts = [rnd.randint(0, 900) * 1000 for _ in range(rnd.randint(0, 8))]
    incs = [rnd.randint(0, 900) * 1000 for _ in range(rnd.randint(0, 8))]
    pairs = sorted((abs(t - g), gi, ii) for gi, g in enumerate(gts) for ii, t in enumerate(incs)
                   if -WINDOW[0] <= t - g <= WINDOW[1])
    used_g, used_i, greedy = set(), set(), 0
    for _, gi, ii in pairs:
        if gi not in used_g and ii not in used_i:
            used_g.add(gi), used_i.add(ii)
            greedy += 1
    G = [{"ruleId": "R", "storeId": "s", "key": "k", "atMs": t} for t in gts]
    I = [{"ruleId": "R", "storeId": "s", "key": "k", "detectedMs": t} for t in incs]
    s = score(G, I, tolerance_before_ms=WINDOW[0], max_latency_ms=WINDOW[1], rule_ids=["R"])["R"]
    assert s.tp >= greedy
    assert s.tp + s.fn == len(gts) and s.tp + s.fp == len(incs)
