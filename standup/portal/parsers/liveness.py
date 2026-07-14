"""Runner liveness.

Primary signal: control/heartbeat.json ({ts, next_tick, last_run_id, busy,
session_id}). The runner is ``alive`` if (now - ts) < HEARTBEAT_MAX_AGE_S (90s).

Fallback (no heartbeat file): infer "probably down" if BOTH the BACKLOG
``Last updated`` and the newest log tick are older than the 4-tick cadence. The
4 ticks fire at 08:00 / 14:07 / 20:17 / 02:27; if the most recent expected tick
boundary has passed by more than a grace window and neither artifact moved, we
call it ``dead``; if only mildly behind, ``stale``.

Output state ∈ {alive, stale, dead}, plus next_tick {name, at, in_seconds} and
the heartbeat age.
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import Any, Dict, List, Optional, Tuple

from . import paths

HEARTBEAT_MAX_AGE_S = 90

# (name, hour, minute) for the 4 daily ticks.
TICKS: List[Tuple[str, int, int]] = [
    ("MORNING", 8, 0),
    ("AFTERNOON", 14, 7),
    ("EVENING", 20, 17),
    ("NIGHT", 2, 27),
]
# Grace beyond a tick boundary before "no movement" counts as dead, in seconds.
# A tick run can take ~40 min; allow a generous window before declaring dead.
FALLBACK_DEAD_GRACE_S = 3 * 3600  # 3h past the boundary with no artifact movement


def _tick_datetimes_around(now: _dt.datetime) -> Dict[str, _dt.datetime]:
    """Return {'prev': dt, 'next': dt, 'prev_name', 'next_name'} for the tick
    schedule surrounding ``now`` (covering the day wrap for the 02:27 tick)."""
    candidates: List[Tuple[_dt.datetime, str]] = []
    for day_offset in (-1, 0, 1):
        day = (now + _dt.timedelta(days=day_offset)).date()
        for name, hh, mm in TICKS:
            candidates.append((_dt.datetime(day.year, day.month, day.day, hh, mm), name))
    candidates.sort(key=lambda c: c[0])

    prev = None
    nxt = None
    for dt, name in candidates:
        if dt <= now:
            prev = (dt, name)
        elif nxt is None:
            nxt = (dt, name)
    return {
        "prev": prev[0] if prev else None,
        "prev_name": prev[1] if prev else None,
        "next": nxt[0] if nxt else None,
        "next_name": nxt[1] if nxt else None,
    }


def next_tick(now: Optional[_dt.datetime] = None) -> Dict[str, Any]:
    now = now or _dt.datetime.now()
    around = _tick_datetimes_around(now)
    nxt = around["next"]
    if nxt is None:
        return {"name": None, "at": None, "in_seconds": None}
    return {
        "name": around["next_name"],
        # tz-aware emit (attach local offset at the boundary); nxt/now stay
        # naive so in_seconds is byte-for-byte authoritative.
        "at": nxt.astimezone().isoformat(timespec="seconds"),
        "in_seconds": int((nxt - now).total_seconds()),
    }


def read_heartbeat(path=None) -> Optional[Dict[str, Any]]:
    p = path or paths.heartbeat_json()
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None


def _parse_ts(ts: Any) -> Optional[_dt.datetime]:
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        try:
            return _dt.datetime.fromtimestamp(float(ts))
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(ts, str):
        s = ts.strip().replace("Z", "+00:00")
        try:
            dt = _dt.datetime.fromisoformat(s)
            # normalise tz-aware to naive local for age math
            if dt.tzinfo is not None:
                dt = dt.astimezone().replace(tzinfo=None)
            return dt
        except ValueError:
            return None
    return None


def assess(
    now: Optional[_dt.datetime] = None,
    heartbeat_path=None,
    backlog_updated_at: Optional[_dt.datetime] = None,
    newest_log_tick_at: Optional[_dt.datetime] = None,
) -> Dict[str, Any]:
    """Compute the runner liveness state.

    ``backlog_updated_at`` / ``newest_log_tick_at`` feed the fallback when there
    is no heartbeat file. Either may be None (then the fallback degrades to
    ``stale`` rather than asserting dead).
    """
    now = now or _dt.datetime.now()
    nt = next_tick(now)
    out: Dict[str, Any] = {
        "state": "stale",
        "source": "fallback",
        "heartbeat_age_s": None,
        "last_run_id": None,
        "busy": None,
        # `dual_runner` is the worst-case split-brain flag the runner sets when it
        # detects a second live runner (two heartbeats / two pids). Default False
        # in the fallback (no heartbeat → we can't observe a second runner, so we
        # don't assert one). Phase 2 actions HARD-BLOCK all launches when true.
        "dual_runner": False,
        "session_id": None,
        "next_tick": nt,
        "last_tick": {"id": None, "name": None, "at": None},
        "_notes": [],
    }

    hb = read_heartbeat(heartbeat_path)
    if hb is not None:
        out["source"] = "heartbeat"
        ts = _parse_ts(hb.get("ts"))
        if ts is not None:
            age = (now - ts).total_seconds()
            out["heartbeat_age_s"] = int(age)
            out["state"] = "alive" if age < HEARTBEAT_MAX_AGE_S else "stale"
            # Wildly old heartbeat -> dead.
            if age >= FALLBACK_DEAD_GRACE_S:
                out["state"] = "dead"
        else:
            out["_notes"].append("heartbeat present but ts unparseable")
            out["state"] = "stale"
        out["last_run_id"] = hb.get("last_run_id")
        out["busy"] = hb.get("busy")
        out["dual_runner"] = bool(hb.get("dual_runner"))
        out["session_id"] = hb.get("session_id")
        # Heartbeat may carry its own next_tick; prefer it if present & parseable.
        hb_next = hb.get("next_tick")
        if isinstance(hb_next, str) and _parse_ts(hb_next):
            dt = _parse_ts(hb_next)
            out["next_tick"] = {
                "name": nt.get("name"),
                # tz-aware emit; dt/now stay naive for in_seconds.
                "at": dt.astimezone().isoformat(timespec="seconds"),
                "in_seconds": int((dt - now).total_seconds()),
            }
        out["last_tick"]["id"] = hb.get("last_run_id")
        return out

    # --- Fallback: no heartbeat file ---
    out["_notes"].append("no heartbeat.json; inferring from BACKLOG + log freshness")
    around = _tick_datetimes_around(now)
    prev_boundary = around["prev"]

    # Newest evidence of a run = max(backlog_updated_at, newest_log_tick_at).
    evidence: List[_dt.datetime] = [d for d in (backlog_updated_at, newest_log_tick_at) if d]
    newest_evidence = max(evidence) if evidence else None
    if newest_evidence is not None:
        # tz-aware emit (attach local offset at the boundary).
        out["last_tick"]["at"] = newest_evidence.astimezone().isoformat(timespec="seconds")

    if prev_boundary is None or newest_evidence is None:
        # Not enough info — say stale (don't over-assert dead).
        out["state"] = "stale"
        return out

    # If the most recent expected tick boundary has passed and neither artifact
    # has been touched since (within grace), the runner is probably down.
    behind_s = (now - prev_boundary).total_seconds()
    artifact_behind_boundary = newest_evidence < prev_boundary

    if artifact_behind_boundary and behind_s > FALLBACK_DEAD_GRACE_S:
        out["state"] = "dead"
        out["_notes"].append(
            f"newest artifact {newest_evidence.isoformat()} predates last tick boundary "
            f"{prev_boundary.isoformat()} by >{FALLBACK_DEAD_GRACE_S//3600}h"
        )
    elif artifact_behind_boundary:
        out["state"] = "stale"
    else:
        # Artifacts moved at/after the last boundary — recent run, treat as stale
        # (we can't prove alive without a heartbeat, but it's not down).
        out["state"] = "stale"
    return out
