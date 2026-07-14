"""Liveness-logic tests: alive vs stale vs dead across heartbeat ages + the
no-heartbeat fallback."""

import datetime
import json

import pytest

from parsers import liveness


def _write_hb(tmp_path, ts, **extra):
    hb = {"ts": ts, "last_run_id": "wf_test", "busy": False, "session_id": "s1"}
    hb.update(extra)
    p = tmp_path / "heartbeat.json"
    p.write_text(json.dumps(hb), encoding="utf-8")
    return p


# --------------------------------------------------------------------------- #
# Heartbeat present
# --------------------------------------------------------------------------- #
def test_alive_when_heartbeat_fresh(tmp_path):
    now = datetime.datetime(2026, 6, 19, 12, 0, 0)
    p = _write_hb(tmp_path, (now - datetime.timedelta(seconds=10)).isoformat())
    r = liveness.assess(now=now, heartbeat_path=p)
    assert r["state"] == "alive"
    assert r["source"] == "heartbeat"
    assert r["heartbeat_age_s"] == 10
    assert r["last_run_id"] == "wf_test"


def test_stale_when_heartbeat_just_over_threshold(tmp_path):
    now = datetime.datetime(2026, 6, 19, 12, 0, 0)
    # 120s old > 90s max -> stale (but not yet the dead grace window)
    p = _write_hb(tmp_path, (now - datetime.timedelta(seconds=120)).isoformat())
    r = liveness.assess(now=now, heartbeat_path=p)
    assert r["state"] == "stale"


def test_dead_when_heartbeat_ancient(tmp_path):
    now = datetime.datetime(2026, 6, 19, 12, 0, 0)
    # 5h old > the 3h dead-grace window -> dead
    p = _write_hb(tmp_path, (now - datetime.timedelta(hours=5)).isoformat())
    r = liveness.assess(now=now, heartbeat_path=p)
    assert r["state"] == "dead"


def test_heartbeat_epoch_seconds_ts(tmp_path):
    now = datetime.datetime(2026, 6, 19, 12, 0, 0)
    ts = (now - datetime.timedelta(seconds=5)).timestamp()
    p = _write_hb(tmp_path, ts)
    r = liveness.assess(now=now, heartbeat_path=p)
    assert r["state"] == "alive"


# --------------------------------------------------------------------------- #
# Fallback (no heartbeat file)
# --------------------------------------------------------------------------- #
def test_fallback_dead_when_artifacts_far_behind_boundary(tmp_path):
    # No heartbeat. It's 18:00; the last tick boundary was 14:07. The newest
    # artifact is from yesterday -> well behind -> dead.
    now = datetime.datetime(2026, 6, 19, 18, 0, 0)
    missing = tmp_path / "nope.json"
    backlog_dt = datetime.datetime(2026, 6, 18, 20, 17)
    log_dt = datetime.datetime(2026, 6, 18, 20, 17)
    r = liveness.assess(
        now=now,
        heartbeat_path=missing,
        backlog_updated_at=backlog_dt,
        newest_log_tick_at=log_dt,
    )
    assert r["source"] == "fallback"
    assert r["state"] == "dead"


def test_fallback_stale_when_artifacts_recent(tmp_path):
    # No heartbeat, but the artifacts moved at/after the last boundary -> a run
    # happened recently; we can't prove alive, so stale (not dead).
    now = datetime.datetime(2026, 6, 19, 21, 0, 0)
    missing = tmp_path / "nope.json"
    # last boundary before 21:00 is 20:17; artifacts stamped 20:17.
    dt = datetime.datetime(2026, 6, 19, 20, 17)
    r = liveness.assess(
        now=now,
        heartbeat_path=missing,
        backlog_updated_at=dt,
        newest_log_tick_at=dt,
    )
    assert r["source"] == "fallback"
    assert r["state"] == "stale"


def test_fallback_stale_when_no_artifact_info(tmp_path):
    now = datetime.datetime(2026, 6, 19, 21, 0, 0)
    missing = tmp_path / "nope.json"
    r = liveness.assess(now=now, heartbeat_path=missing)
    # not enough info to assert dead -> stale, never a crash
    assert r["state"] == "stale"


# --------------------------------------------------------------------------- #
# next_tick scheduling
# --------------------------------------------------------------------------- #
def test_next_tick_picks_correct_upcoming_boundary():
    # at 21:00 the next tick is NIGHT 02:27 (next day)
    now = datetime.datetime(2026, 6, 19, 21, 0, 0)
    nt = liveness.next_tick(now)
    assert nt["name"] == "NIGHT"
    assert nt["at"].startswith("2026-06-20T02:27")
    assert nt["in_seconds"] > 0


def test_next_tick_morning_after_night():
    # at 03:00 the next tick is MORNING 08:00 same day
    now = datetime.datetime(2026, 6, 19, 3, 0, 0)
    nt = liveness.next_tick(now)
    assert nt["name"] == "MORNING"
    assert nt["at"].startswith("2026-06-19T08:00")
    assert 0 < nt["in_seconds"] <= 6 * 3600


def test_countdown_in_seconds_is_consistent():
    now = datetime.datetime(2026, 6, 19, 13, 0, 0)
    nt = liveness.next_tick(now)
    # next boundary is AFTERNOON 14:07 -> 67 minutes
    assert nt["name"] == "AFTERNOON"
    assert nt["in_seconds"] == 67 * 60


# --------------------------------------------------------------------------- #
# tz-awareness (freshness spine): every emitted at/ts string must carry a UTC
# offset, while integer in_seconds / age math stays byte-for-byte authoritative.
# --------------------------------------------------------------------------- #
def test_next_tick_at_is_tz_aware_in_seconds_unchanged():
    now = datetime.datetime(2026, 6, 19, 13, 0, 0)  # naive (math operand)
    nt = liveness.next_tick(now)
    parsed = datetime.datetime.fromisoformat(nt["at"])
    assert parsed.tzinfo is not None, f"next_tick.at must be tz-aware: {nt['at']!r}"
    # the wall-clock boundary + the authoritative countdown are unchanged
    assert nt["at"].startswith("2026-06-19T14:07")
    assert nt["in_seconds"] == 67 * 60


def test_heartbeat_branch_at_tz_aware_for_naive_and_aware_hb(tmp_path):
    """A heartbeat carrying its own next_tick — whether the stored string is
    naive OR aware — must surface an aware `at`, with identical in_seconds/age."""
    now = datetime.datetime(2026, 6, 19, 13, 0, 0)
    ts = (now - datetime.timedelta(seconds=10)).isoformat()
    boundary = datetime.datetime(2026, 6, 19, 14, 7, 0)

    # (a) heartbeat next_tick stored NAIVE
    p_naive = _write_hb(tmp_path, ts, next_tick=boundary.isoformat())
    r_naive = liveness.assess(now=now, heartbeat_path=p_naive)
    # (b) heartbeat next_tick stored AWARE (e.g. from the new emitter)
    aware_boundary = boundary.astimezone().isoformat()
    p_aware = tmp_path / "hb_aware.json"
    p_aware.write_text(
        json.dumps({"ts": ts, "last_run_id": "wf_test", "busy": False,
                    "session_id": "s1", "next_tick": aware_boundary}),
        encoding="utf-8",
    )
    r_aware = liveness.assess(now=now, heartbeat_path=p_aware)

    for r in (r_naive, r_aware):
        at = r["next_tick"]["at"]
        assert datetime.datetime.fromisoformat(at).tzinfo is not None, \
            f"heartbeat-branch next_tick.at must be tz-aware: {at!r}"
        assert r["next_tick"]["in_seconds"] == 67 * 60
        assert r["heartbeat_age_s"] == 10
    # both inputs yield the SAME wall-clock + countdown regardless of input tz
    assert r_naive["next_tick"]["in_seconds"] == r_aware["next_tick"]["in_seconds"]


def test_fallback_last_tick_at_is_tz_aware(tmp_path):
    now = datetime.datetime(2026, 6, 19, 21, 0, 0)
    p = tmp_path / "absent.json"  # no heartbeat -> fallback path
    log_dt = datetime.datetime(2026, 6, 19, 20, 17)
    r = liveness.assess(now=now, heartbeat_path=p, newest_log_tick_at=log_dt)
    at = r["last_tick"]["at"]
    assert at is not None
    assert datetime.datetime.fromisoformat(at).tzinfo is not None, \
        f"fallback last_tick.at must be tz-aware: {at!r}"
