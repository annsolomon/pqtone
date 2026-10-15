"""Zone and queue membership on synthetic foot-point tracks (V4 'Done when')."""
import numpy as np
import pytest

from pqt_vision.camera import Membership, ZoneSpec
from pqt_vision.zones import ZoneEngine, point_in_polygon, signed_distance

SQUARE = np.array([[0, 0], [4, 0], [4, 2], [0, 2]], dtype=float)  # a 4 m × 2 m queue area
QUEUE = ZoneSpec("queue-1", "queue", tuple(map(tuple, SQUARE)))
AISLE = ZoneSpec("aisle-1", "zone", ((10, 0), (14, 0), (14, 6), (10, 6)))
M = Membership(enter_s=0.5, exit_s=1.0, margin=0.15, queue_min_dwell_s=5.0, queue_max_speed=0.5, speed_window_s=2.0)
FPS = 10


def feed(engine: ZoneEngine, path: dict[int, list[tuple[float, float] | None]], *, finish: bool = True):
    """path: track id → per-frame (x, y) or None (not visible). Returns all events."""
    n = max(len(p) for p in path.values())
    events = []
    for k in range(n):
        obs = [(tid, *p[k]) for tid, p in path.items() if k < len(p) and p[k] is not None]
        events += engine.update(k * 1000 // FPS, obs)
    if finish:
        events += engine.finish()
    return events


def kinds(events):
    return [(e.kind, e.t_ms, e.track_id) for e in events]


def test_geometry_helpers():
    assert point_in_polygon(1, 1, SQUARE) and not point_in_polygon(5, 1, SQUARE)
    assert signed_distance(2, 1, SQUARE) == pytest.approx(1.0)
    assert signed_distance(5, 1, SQUARE) == pytest.approx(-1.0)
    assert signed_distance(4, 1, SQUARE) == pytest.approx(0.0)
    concave = np.array([[0, 0], [4, 0], [4, 4], [2, 1], [0, 4]], dtype=float)
    assert point_in_polygon(1, 1, concave) and not point_in_polygon(2, 3, concave)


def test_boundary_jitter_never_toggles_membership():
    rng = np.random.default_rng(1)
    # Standing on the queue's right edge (x = 4) for 60 s, foot point jittering ±10 cm.
    jitter = [(4.0 + float(rng.uniform(-0.10, 0.10)), 1.0) for _ in range(600)]
    events = feed(ZoneEngine([QUEUE], M), {1: jitter})
    assert events == []
    # Same jitter just inside the margin band (x = 3.75 ± 0.10): one entry, no flicker, then one exit.
    inside = [(3.75 + float(rng.uniform(-0.10, 0.10)), 1.0) for _ in range(600)]
    events = feed(ZoneEngine([QUEUE], M), {2: inside})
    assert [e.kind for e in events] == ["zone.entered", "queue.joined", "queue.left", "zone.exited"]


def test_entry_is_dated_when_it_started_not_when_it_was_confirmed():
    path = [(5.0, 1.0)] * 10 + [(3.0, 1.0)] * 20 + [(6.0, 1.0)] * 20
    events = feed(ZoneEngine([QUEUE], M), {7: path})
    assert kinds(events) == [("zone.entered", 1000, 7), ("zone.exited", 3000, 7)]


def test_brief_visits_shorter_than_enter_s_do_not_count():
    path = [(5.0, 1.0)] * 10 + [(3.0, 1.0)] * 3 + [(6.0, 1.0)] * 10  # 0.3 s inside
    assert feed(ZoneEngine([QUEUE], M), {1: path}) == []


def test_walking_through_the_queue_area_is_not_queueing():
    # 1.4 m/s across the 4 m area and well past it: inside for about 3 s, too fast to be waiting.
    path = [(-1.0 + 0.14 * k, 1.0) for k in range(60)]
    events = feed(ZoneEngine([QUEUE], M), {1: path})
    assert [e.kind for e in events] == ["zone.entered", "zone.exited"]


def test_standing_in_the_queue_joins_after_the_minimum_dwell_and_leaves_on_exit():
    stand = [(5.0, 1.0)] * 5 + [(2.0, 1.0)] * 100 + [(2.0 - 0.02 * k, 1.0) for k in range(150)]
    events = feed(ZoneEngine([QUEUE], M), {3: stand})
    names = [(e.kind, e.t_ms) for e in events]
    assert names[0] == ("zone.entered", 500)
    assert names[1] == ("queue.joined", 5500)  # 5 s after entering, standing still
    assert [n for n, _ in names[2:]] == ["queue.left", "zone.exited"]
    left = next(e for e in events if e.kind == "queue.left")
    assert left.queue_id == "queue-1" and left.served is True  # no head configured: served by default


def test_served_depends_on_leaving_near_the_queue_head():
    eng = ZoneEngine([QUEUE], M, queue_heads={"queue-1": (0.0, 1.0)}, head_radius=0.75)
    out_front = [(2.0, 1.0)] * 80 + [(2.0 - 0.1 * k, 1.0) for k in range(40)]
    out_back = [(2.0, 1.0)] * 80 + [(2.0 + 0.1 * k, 1.0) for k in range(40)]
    ev = feed(eng, {1: out_front, 2: out_back})
    served = {e.track_id: e.served for e in ev if e.kind == "queue.left"}
    assert served == {1: True, 2: False}


def test_a_lost_track_is_closed_at_its_last_observation():
    # 9 s of absence: past lost_after_s plus the queue hand-over window (N4), so the events are released
    # without finish().
    path = [(2.0, 1.0)] * 30 + [None] * 90
    events = feed(ZoneEngine([QUEUE], M, lost_after_s=2.0), {1: path}, finish=False)
    assert kinds(events) == [("zone.entered", 0, 1), ("zone.exited", 2900, 1)]


def test_occupancy_and_queue_length_snapshots():
    eng = ZoneEngine([QUEUE, AISLE], M)
    for k in range(80):
        eng.update(k * 100, [(1, 1.0, 1.0), (2, 3.0, 1.5), (3, 12.0, 3.0)])
    assert eng.occupancy() == {"queue-1": 2, "aisle-1": 1}
    assert eng.queue_lengths() == {"queue-1": 2}
    eng.update(8000, [(1, 1.0, 1.0), (3, 12.0, 3.0)])
    assert eng.occupancy()["queue-1"] == 2  # track 2 is not lost until lost_after_s passes


def test_two_zones_are_independent():
    path = [(2.0, 1.0)] * 20 + [(12.0, 3.0)] * 20
    events = feed(ZoneEngine([QUEUE, AISLE], M), {1: path})
    assert [(e.kind, e.zone_id) for e in events] == [
        ("zone.entered", "queue-1"), ("zone.exited", "queue-1"), ("zone.entered", "aisle-1"), ("zone.exited", "aisle-1")]


def test_released_events_are_in_event_time_order():
    rng = np.random.default_rng(5)
    paths = {tid: [(float(x), 1.0) for x in np.clip(np.cumsum(rng.normal(0, 0.3, 400)) + 2, -3, 8)] for tid in range(1, 6)}
    eng = ZoneEngine([QUEUE, AISLE], M)
    events = feed(eng, paths)
    times = [e.t_ms for e in events]
    assert times == sorted(times) and len(events) > 4


# -- N4: how an entry or exit happened, and the queue hand-over -------------------------------------

def hows(events):
    return [(e.kind, e.t_ms, e.track_id, e.how) for e in events if e.kind.startswith("zone.")]


def test_entries_and_exits_say_whether_they_crossed_or_appeared_or_were_lost():
    crossing = [(5.0, 1.0)] * 10 + [(3.0, 1.0)] * 20 + [(6.0, 1.0)] * 20
    appearing_then_lost = [(2.0, 1.0)] * 20
    events = feed(ZoneEngine([QUEUE], M, handover_s=0), {1: crossing, 2: appearing_then_lost})
    assert hows(events) == [("zone.entered", 0, 2, "appeared"), ("zone.entered", 1000, 1, "crossed"),
                            ("zone.exited", 1900, 2, "lost"), ("zone.exited", 3000, 1, "crossed")]


def test_a_track_appearing_where_a_member_was_lost_takes_its_place_at_once():
    """An ID switch in the queue: track 1 (a member) is lost at 8.0 s, track 2 appears at the same spot at
    8.5 s. Without the hand-over the queue would drop by one for over 5 s (minimum dwell)."""
    t1 = [(2.0, 1.0)] * 81                         # 0.0-8.0 s, joins at 5 s
    t2 = [None] * 85 + [(2.0, 1.0)] * 100           # 8.5-18.4 s
    events = feed(ZoneEngine([QUEUE], M, lost_after_s=1.0, handover_s=5.0), {1: t1, 2: t2})
    q = [(e.kind, e.t_ms, e.track_id, e.served) for e in events if e.kind.startswith("queue.")]
    assert q[:3] == [("queue.joined", 5000, 1, None), ("queue.left", 8500, 1, False), ("queue.joined", 8500, 2, None)]
    assert ("zone.exited", 8000, 1, "lost") in hows(events) and ("zone.entered", 8500, 2, "appeared") in hows(events)


def test_without_anyone_to_take_over_the_member_leaves_at_its_last_sighting():
    t1 = [(2.0, 1.0)] * 81
    t2 = [None] * 150 + [(2.0, 1.0)] * 80           # appears 7 s after the loss: too late for a hand-over
    events = feed(ZoneEngine([QUEUE], M, lost_after_s=1.0, handover_s=5.0), {1: t1, 2: t2})
    q = [(e.kind, e.t_ms, e.track_id) for e in events if e.kind.startswith("queue.")]
    assert q[:2] == [("queue.joined", 5000, 1), ("queue.left", 8000, 1)]
    assert ("queue.joined", 20000, 2) in q          # the newcomer waits the minimum dwell like anyone else
    assert [e.t_ms for e in events] == sorted(e.t_ms for e in events)


def test_a_person_walking_in_never_takes_over_a_lost_place():
    t1 = [(2.0, 1.0)] * 81
    walker = [None] * 83 + [(5.0, 1.0)] * 3 + [(2.0, 1.0)] * 80   # crosses in from outside at 8.6 s
    events = feed(ZoneEngine([QUEUE], M, lost_after_s=1.0, handover_s=5.0), {1: t1, 2: walker})
    q = [(e.kind, e.t_ms, e.track_id) for e in events if e.kind.startswith("queue.")]
    assert ("queue.left", 8000, 1) in q and ("queue.joined", 13600, 2) in q
