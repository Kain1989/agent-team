#!/usr/bin/env python3
"""control/heartbeat.py — stamp control/heartbeat.json so the portal's liveness
is REAL, not inferred.

The runner runs this on a 1-minute session-cron (see RUNNER_SETUP.md) whenever it
is idle. Each run writes:

    control/heartbeat.json = {
      "ts":          "<now ISO>",          # the portal reads (now - ts) < 90s => alive
      "next_tick":   "<next tick ISO>",    # computed from the 4-tick schedule
      "next_tick_name": "MORNING|AFTERNOON|EVENING|NIGHT",
      "last_run_id": "wf_…",               # from BACKLOG.md "Last updated:" line
      "busy":        false,                # true ONLY while a tick/poller run is active
      "session_id":  "<runner session id>",
      "dual_runner": false                 # true if a 2nd live session is detected
    }

It is dependency-free (stdlib only) and writes atomically (temp + os.replace) so
the portal never reads a half-written file.

BUSY / DUAL-RUNNER
------------------
`busy` here is now a SECONDARY single-flight signal — the AUTHORITATIVE "a tick is
running" fact is the machine-owned `control/run.lock` (see control/run_lock.py),
which is held/stamped for the WHOLE duration of EVERY tick (the 4 scheduled crons
AND any portal-triggered run) and which the portal reads directly. `busy` defaults
to false; a launch path MAY still stamp it true around a Workflow launch (pass
``--busy`` + ``--run-id``, clear with a plain call after) as belt-and-suspenders,
but the run lock is what closes the proven scheduled-tick double-fire (the old
prompt-owned `busy` was never set by the scheduled ticks). `dual_runner` is set
true (``--dual``) only when the operator has confirmed two runner sessions are live
at once; the portal HARD-BLOCKS all launches while it is true.

USAGE
  python3 control/heartbeat.py
  python3 control/heartbeat.py --session-id "$SESSION_ID"
  python3 control/heartbeat.py --busy --run-id wf_abc123   # poller sets while launching
  python3 control/heartbeat.py --dual                      # operator: split-brain seen
"""

from __future__ import annotations

import argparse
import datetime as _dt
import importlib.util
import json
import os
import re
import tempfile
from pathlib import Path

# control/ lives directly under the STANDUP root. These are the DEFAULTS used when
# STANDUP_ROOT is unset (the real cron): a clean-env run resolves byte-for-byte to
# the old __file__-relative constants, so the deployed cron is unchanged.
_DEFAULT_CONTROL_DIR = Path(__file__).resolve().parent
_DEFAULT_ROOT = _DEFAULT_CONTROL_DIR.parent

# Back-compat module-level constants (some importers/tests reference these). They
# reflect the DEFAULT (clean-env) layout; every WRITE site below resolves the path
# at CALL time via the _*_path() helpers so STANDUP_ROOT is honored per-call,
# mirroring portal/parsers/paths.py (standup_root() re-reads env each call).
CONTROL_DIR = _DEFAULT_CONTROL_DIR
STANDUP_ROOT = _DEFAULT_ROOT
HEARTBEAT = _DEFAULT_CONTROL_DIR / "heartbeat.json"
BACKLOG = _DEFAULT_ROOT / "BACKLOG.md"
CONTROL_LOG = _DEFAULT_CONTROL_DIR / "control.log"
RUN_LOCK_PY = _DEFAULT_CONTROL_DIR / "run_lock.py"


def _resolve_root() -> Path:
    """Re-read STANDUP_ROOT each call so tests/alternate checkouts can redirect every
    WRITE the heartbeat does, exactly like portal/parsers/paths.standup_root()."""
    return Path(os.environ.get("STANDUP_ROOT", str(_DEFAULT_ROOT))).resolve()


def _control_dir() -> Path:
    return _resolve_root() / "control"


def _heartbeat_path() -> Path:
    return _control_dir() / "heartbeat.json"


def _control_log_path() -> Path:
    return _control_dir() / "control.log"


def _backlog_path() -> Path:
    return _resolve_root() / "BACKLOG.md"


def _run_lock_py_path() -> Path:
    """The shared run_lock.py implementation (CODE, not state). Mirror
    paths.run_lock_module(): prefer an isolated control/run_lock.py if one exists
    under STANDUP_ROOT (a test that wants a custom module), else fall back to the
    real checkout next to this file — so an isolated tmp root with no run_lock.py
    still loads the real reconciler instead of silently no-op'ing the backstop."""
    isolated = _control_dir() / "run_lock.py"
    if isolated.exists():
        return isolated
    return _DEFAULT_CONTROL_DIR / "run_lock.py"


def _load_run_lock(run_lock_py: Path | None = None):
    """Load control/run_lock.py so the per-minute heartbeat can run the unlocked-tick
    reconciler (the machine backstop for a scheduled-tick prompt that dropped its
    `acquire` line). Resolved + loaded at CALL time (not cached at import) so a
    subprocess/importer whose STANDUP_ROOT is set picks up the right module. Best-
    effort: a load failure must never break the heartbeat."""
    path = run_lock_py or _run_lock_py_path()
    try:
        spec = importlib.util.spec_from_file_location("standup_run_lock_hb", str(path))
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except (OSError, ImportError, SyntaxError):
        return None


def _append_log(line: str) -> None:
    """Best-effort append to control/control.log (audit of a backstop recovery)."""
    try:
        cd = _control_dir()
        cd.mkdir(parents=True, exist_ok=True)
        with open(cd / "control.log", "a", encoding="utf-8") as fh:
            fh.write(f"{_dt.datetime.now().astimezone().isoformat(timespec='seconds')}  {line}\n")
    except OSError:
        pass


def reconcile_unlocked_tick() -> dict:
    """THE BACKSTOP (runs every minute via the heartbeat cron). If a scheduled tick
    is CURRENTLY running (a fresh control/tick_active.marker) but control/run.lock is
    NOT held, stamp the lock on the tick's behalf so the portal reads busy within
    ≤1 min — closing the proven double-fire even when the scheduled-tick prompt
    dropped/forgot its `run_lock.py acquire` line. Delegates to the shared lock
    module so the marker/lock semantics live in ONE place. Best-effort; a no-op when
    there is no running tick to cover, or when the lock is already held."""
    run_lock = _load_run_lock()
    if run_lock is None:
        return {"reconciled": False, "reason": "run_lock module unavailable"}
    try:
        r = run_lock.reconcile_unlocked_tick(control_dir=_control_dir(), now=_dt.datetime.now())
    except Exception as exc:  # never let the backstop crash the heartbeat
        return {"reconciled": False, "reason": f"reconcile error: {exc}"}
    if r.get("reconciled"):
        _append_log(
            f"RECONCILE-LOCK run_id={r.get('run_id')} kind=scheduled-recovered "
            "(running tick had NO lock — heartbeat backstop stamped run.lock so the "
            "portal reads busy; a scheduled-tick prompt likely dropped its acquire line)"
        )
    return r

# The 4 daily ticks (name, hour, minute) — MUST match parsers/liveness.py TICKS
# and the cron schedule in RUNNER_SETUP.md.
TICKS = [
    ("MORNING", 8, 0),
    ("AFTERNOON", 14, 7),
    ("EVENING", 20, 17),
    ("NIGHT", 2, 27),
]

_RUN_ID_RE = re.compile(r"`?(wf_[0-9a-f]{3,}(?:-[0-9a-z]+)*)`?", re.IGNORECASE)


def next_tick(now: _dt.datetime):
    """Return (name, datetime) of the next tick at/after ``now`` across the day
    wrap (the 02:27 NIGHT tick belongs to the next calendar day)."""
    candidates = []
    for day_offset in (0, 1):
        day = (now + _dt.timedelta(days=day_offset)).date()
        for name, hh, mm in TICKS:
            candidates.append((name, _dt.datetime(day.year, day.month, day.day, hh, mm)))
    candidates.sort(key=lambda c: c[1])
    for name, dt in candidates:
        if dt > now:
            return name, dt
    # Fallback (shouldn't happen): the first tick tomorrow.
    name, dt = candidates[0]
    return name, dt


def last_run_id() -> str | None:
    """Read the most recent run id from BACKLOG.md's ``Last updated:`` line."""
    try:
        for line in _backlog_path().read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("last updated:"):
                m = _RUN_ID_RE.search(line)
                return m.group(1) if m else None
    except OSError:
        return None
    return None


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def stamp(session_id: str | None = None, busy: bool = False,
          run_id: str | None = None, dual_runner: bool = False) -> dict:
    now = _dt.datetime.now()
    nt_name, nt_dt = next_tick(now)
    hb = {
        # tz-aware emit: attach the system-local offset at the .isoformat()
        # boundary (now/nt_dt stay naive for any math). Readers normalise
        # aware->naive (see parsers.liveness._parse_ts).
        "ts": now.astimezone().isoformat(timespec="seconds"),
        "next_tick": nt_dt.astimezone().isoformat(timespec="seconds"),
        "next_tick_name": nt_name,
        "last_run_id": run_id or last_run_id(),
        "busy": bool(busy),
        "session_id": session_id or os.environ.get("STANDUP_SESSION_ID") or os.environ.get("SESSION_ID"),
        "dual_runner": bool(dual_runner),
    }
    _atomic_write_json(_heartbeat_path(), hb)
    return hb


def main() -> None:
    ap = argparse.ArgumentParser(description="Stamp control/heartbeat.json")
    ap.add_argument("--session-id", default=None)
    ap.add_argument("--run-id", default=None, help="override last_run_id (the run currently launching)")
    ap.add_argument("--busy", action="store_true", help="mark the runner busy (a tick is running)")
    ap.add_argument("--dual", action="store_true", help="mark dual-runner split-brain (HARD blocks launches)")
    ap.add_argument("--no-reconcile", action="store_true",
                    help="skip the unlocked-tick backstop (for tests / manual stamps)")
    args = ap.parse_args()
    # THE BACKSTOP runs FIRST, every minute: if a tick is running with no lock held
    # (a scheduled-tick prompt dropped its acquire line), stamp the lock on its
    # behalf so the portal reads busy before the next launch decision. Runs before
    # stamping the heartbeat so a recovered run.lock is in place the instant the
    # portal next reads it. Best-effort; never aborts the heartbeat.
    recon = {"reconciled": False, "reason": "skipped"}
    if not args.no_reconcile:
        recon = reconcile_unlocked_tick()
    hb = stamp(session_id=args.session_id, busy=args.busy, run_id=args.run_id, dual_runner=args.dual)
    if recon.get("reconciled"):
        hb = dict(hb)
        hb["reconciled_unlocked_tick"] = recon.get("run_id") or True
    print(json.dumps(hb))


if __name__ == "__main__":
    main()
