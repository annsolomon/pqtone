"""Milestone S4: the HTTP sink run must get an answer for every event, of the expected kind."""
from pip_scorer.cli import check_http

MANIFEST = {"counts": {"cleanEvents": 1000, "emittedEvents": 1050, "duplicates": 50, "malformed": 0}}


def test_every_event_answered_as_expected():
    assert check_http({"accepted": 1000, "duplicate": 50}, MANIFEST) == []


def test_missing_and_unexpected_answers_are_reported():
    problems = check_http({"accepted": 990, "duplicate": 50, "forbidden": 10}, MANIFEST)
    assert "accepted 990 != unique valid events 1000" in problems
    assert any(p.startswith("unexpected results {'forbidden': 10}") for p in problems)


def test_unanswered_events_are_reported():
    problems = check_http({"accepted": 1000, "duplicate": 40}, MANIFEST)
    assert "duplicate 40 != injected duplicates 50" in problems
    assert "answered 1040 of 1050 emitted events" in problems


def test_malformed_events_must_come_back_invalid():
    m = {"counts": {"cleanEvents": 10, "emittedEvents": 12, "duplicates": 0, "malformed": 2}}
    assert check_http({"accepted": 10, "invalid": 2}, m) == []
    assert check_http({"accepted": 12}, m)
