#!/usr/bin/env python3
"""control/run_lock.py — the ONE machine-owned exclusive lock that covers EVERY
tick launch path (scheduled cron OR portal-triggered).

WHY THIS EXISTS (the proven double-fire the whole design must prevent)
----------------------------------------------------------------------
The invariant: **at most one standup/PM-review Workflow may run at a time.** The
old design tried to enforce that with a "prompt-owned" `heartbeat.busy` flag —
the cron PROMPT had to remember to stamp `busy:true` before launching. Two holes
followed:

  1. The 4 *scheduled* tick crons never set `busy` at all. During a 40-min
     scheduled run the 1-min heartbeat cron kept stamping `busy:false`, so the
     portal guard saw `busy:false` + `tick_imminent:false` (next_tick had rolled
     hours out) and ACCEPTED a `run-standup` → the poller drained it → a 2nd
     Workflow launched CONCURRENTLY with the scheduled tick. PROVEN live.
  2. Even for portal runs, `busy` was set by the prompt AFTER drain.py staged the
     request — a window where a 2nd poll could double-launch.

The fix: make "is a tick running" a **machine fact**, not a flag a prompt has to
remember. An OS-level exclusive lock (`fcntl.flock` on `control/run.lock`) is
HELD for the entire duration of ANY tick. Whoever wants to launch must acquire it
first; it is released only when the Workflow completes (or the holder process
dies — the OS drops the flock automatically). The portal guard reads the lock and
treats "held by a live holder" as busy.

DESIGN
------
* `control/run.lock` is a regular file. The exclusivity comes from an advisory
  `flock(LOCK_EX | LOCK_NB)` held on an OPEN FD for the lifetime of the holder.
  Advisory flock is honored cooperatively by every launch path here (drain.py and
  the scheduled-tick crons) and — crucially — is **released by the kernel when the
  holding process exits or crashes**, so a dead runner cannot wedge the lock.

* On acquire we STAMP the lock file body with JSON
  `{holder, pid, run_id, started_at, kind}` so the portal (a *different* process
  that does NOT hold the flock) can read WHO holds it and SINCE WHEN, and render
  an honest "tick running" message. The stamp is data; the flock is the truth.

* STALE-LOCK SAFETY (defense in depth): the portal cannot take the flock to test
  it without blocking, and a crashed holder's flock is already gone — but a
  half-written or orphaned *stamp* could linger. So the portal treats a lock as
  "live" only if (a) the flock is actually held by some process (we probe with a
  non-blocking flock attempt: if WE can take it, nobody holds it) AND/OR
  (b) the stamped `started_at` is within `MAX_TICK_S`. A stamp older than
  `MAX_TICK_S` (a tick can't legitimately run that long) is treated as a dead
  holder and IGNORED — `read_holder()` returns `held=False` for it.

Stdlib-only. Safe to import from the portal (read path) and from the runner-side
scripts (acquire/release path). On non-POSIX (no fcntl) it degrades to a
best-effort stamp-file check (documented; the runner is POSIX).
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

try:  # POSIX advisory locking. The runner + dev box are POSIX.
    import fcntl  # type: ignore
    _HAVE_FCNTL = True
except ImportError:  # pragma: no cover - Windows fallback (stamp-only)
    fcntl = None  # type: ignore
    _HAVE_FCNTL = False

# A single tick (standup or PM review) takes ~30-50 min. Past this a held lock /
# stamp is considered a dead holder and is ignored (so a crashed runner that
# somehow left a stamp can't wedge the buttons forever). Kept > the longest real
# tick, < the time it takes a human to notice. The portal's stuck-RUNNING
# watchdog uses the SAME ceiling.
MAX_TICK_S = 70 * 60  # 70 minutes


def _now() -> _dt.datetime:
    return _dt.datetime.now()


def _iso(dt: Optional[_dt.datetime] = None) -> str:
    return (dt or _now()).isoformat(timespec="seconds")


def _parse_iso(s: Any) -> Optional[_dt.datetime]:
    if not isinstance(s, str):
        return None
    try:
        dt = _dt.datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt
    except ValueError:
        return None


def lock_path(control_dir: Optional[Path] = None) -> Path:
    """control/run.lock. ``control_dir`` lets tests point at an isolated root;
    defaults to this file's own control/ dir (the real one for the runner)."""
    cd = control_dir or Path(__file__).resolve().parent
    return Path(cd) / "run.lock"


def marker_path(control_dir: Optional[Path] = None) -> Path:
    """control/tick_active.marker — a tiny machine signal a scheduled-tick launch
    drops so the heartbeat reconciler can detect "a tick is RUNNING" independently
    of whether the lock was taken. See ``mark()`` / ``reconcile_unlocked_tick()``."""
    cd = control_dir or Path(__file__).resolve().parent
    return Path(cd) / "tick_active.marker"


# --------------------------------------------------------------------------- #
# TICK-ACTIVE MARKER  (the heartbeat reconciler's "a tick is running" signal)
# --------------------------------------------------------------------------- #
# WHY: the 4 scheduled-tick crons take the lock via PROMPT discipline. If a prompt
# slip drops the `acquire` line, the tick runs LOCK-FREE → the portal reads
# not-busy → the proven double-fire reopens. A Workflow script can't self-lock, so
# the prompt is the only place the lock can be taken — and a single prompt is a
# single point of failure.
#
# The backstop: every scheduled-tick launch ALSO drops a tiny marker file at start
# (the prompt's literal first step) AND `acquire()` drops it automatically as a
# side effect, so the marker survives even some forms of slip. The 1-minute
# heartbeat cron then runs ``reconcile_unlocked_tick()``: if the marker says a tick
# is running but the lock is NOT held, it STAMPS the lock on the tick's behalf
# (kind="scheduled-recovered") — so a lock-free running tick reads as busy to the
# portal within ≤1 min, closing the hole WITHOUT relying on the prompt remembering
# the acquire line. The marker is bounded by the SAME MAX_TICK_S dead-holder
# ceiling as the lock, so a leftover marker from a finished/crashed tick self-heals
# and can't wedge the portal forever.
def mark(run_id: Optional[str] = None, kind: str = "scheduled-tick",
         holder: Optional[str] = None, control_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Drop control/tick_active.marker = {run_id, kind, holder, started_at}. Atomic
    (temp + os.replace). Idempotent-ish: a re-mark refreshes started_at — pass the
    SAME run_id at tick start only (the reconciler relies on started_at being the
    tick's real start for the MAX_TICK_S ceiling)."""
    p = marker_path(control_dir)
    payload = {
        "run_id": run_id,
        "kind": kind,
        "holder": holder or os.environ.get("STANDUP_SESSION_ID") or f"pid:{os.getpid()}",
        "started_at": _iso(),
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".tmp-marker-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False, indent=2))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return payload


def unmark(control_dir: Optional[Path] = None) -> bool:
    """Remove the tick-active marker (called at tick completion alongside release).
    Idempotent — returns True if a marker was removed, False if none existed."""
    p = marker_path(control_dir)
    try:
        p.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def read_marker(control_dir: Optional[Path] = None,
                now: Optional[_dt.datetime] = None) -> Dict[str, Any]:
    """Read the tick-active marker WITHOUT blocking. Returns
    ``{active, run_id, kind, holder, started_at, age_s, reason}``.

    ``active`` is True only for a FRESH marker (started_at within MAX_TICK_S). A
    marker older than MAX_TICK_S is a leftover from a finished/crashed tick →
    ``active: False`` (so it self-heals and can't wedge the portal forever). A
    missing/garbage marker → ``active: False``."""
    now = now or _now()
    p = marker_path(control_dir)
    out: Dict[str, Any] = {
        "active": False, "run_id": None, "kind": None, "holder": None,
        "started_at": None, "age_s": None, "reason": None,
    }
    if not p.exists():
        out["reason"] = "no marker"
        return out
    data: Dict[str, Any] = {}
    try:
        raw = p.read_text(encoding="utf-8").strip()
        if raw:
            data = json.loads(raw)
    except (OSError, json.JSONDecodeError, ValueError):
        data = {}
    started = _parse_iso(data.get("started_at")) if data else None
    age_s = int((now - started).total_seconds()) if started else None
    out.update({
        "run_id": data.get("run_id"), "kind": data.get("kind"),
        "holder": data.get("holder"), "started_at": data.get("started_at"),
        "age_s": age_s,
    })
    if started is None:
        out["reason"] = "marker present but started_at unparseable -> ignored"
        return out
    if age_s is not None and age_s < MAX_TICK_S:
        out["active"] = True
        out["reason"] = "fresh tick-active marker"
    else:
        out["reason"] = f"marker stale (>= {MAX_TICK_S // 60}min) -> finished/dead tick ignored"
    return out


def reconcile_unlocked_tick(control_dir: Optional[Path] = None,
                            now: Optional[_dt.datetime] = None) -> Dict[str, Any]:
    """THE BACKSTOP (heartbeat cron, every minute). If a tick is CURRENTLY running
    (a fresh tick-active marker) but control/run.lock is NOT held by a live holder,
    STAMP the lock on the tick's behalf (kind="scheduled-recovered") so the portal
    reads busy within ≤1 min — even though the scheduled-tick prompt forgot/dropped
    its `run_lock.py acquire` line.

    Returns ``{reconciled: bool, reason, run_id, marker, lock_before}``. Safe to run
    every minute: a no-op when there is no marker, when the marker is stale, or when
    the lock is already held (the common, healthy case)."""
    now = now or _now()
    marker = read_marker(control_dir=control_dir, now=now)
    out: Dict[str, Any] = {
        "reconciled": False, "reason": None, "run_id": marker.get("run_id"),
        "marker": marker, "lock_before": None,
    }
    if not marker.get("active"):
        out["reason"] = f"no running tick to cover ({marker.get('reason')})"
        return out
    holder = read_holder(path=lock_path(control_dir), now=now)
    out["lock_before"] = holder
    if holder.get("held"):
        out["reason"] = "lock already held by a live holder -> nothing to recover"
        return out
    # A tick is running (fresh marker) but the lock is NOT held: the scheduled-tick
    # prompt dropped/forgot its acquire line. Stamp the lock on the tick's behalf so
    # the portal reads busy. We acquire the flock briefly to stamp atomically, then
    # close the FD — the STAMP (bounded by MAX_TICK_S) is the cross-process signal
    # the portal reads, exactly like a cron-prompt acquire. started_at is anchored
    # to the MARKER's start so the recovered lock and the real tick share a clock
    # and the MAX_TICK_S ceiling fires at the right time.
    lock = RunLock(kind="scheduled-recovered", run_id=marker.get("run_id"),
                   holder="heartbeat-reconciler", control_dir=control_dir)
    if not lock.acquire():
        # Lost a race to a real holder between the read and here — that holder now
        # covers the tick, which is the goal. Treat as success-by-other-means.
        out["reason"] = "lock taken by another holder during reconcile (covered)"
        return out
    # Anchor the recovered stamp's started_at to the marker's real tick start so the
    # dead-holder ceiling matches the tick, not the reconcile instant.
    lock._stamp(started_at=marker.get("started_at"))
    # Drop the flock FD (the stamp is the cross-process signal the portal reads).
    if lock._fd is not None:
        try:
            os.close(lock._fd)
        except OSError:
            pass
        lock._fd = None
    out["reconciled"] = True
    out["reason"] = ("tick running (fresh marker) with NO lock held -> stamped "
                     "run.lock kind=scheduled-recovered on the tick's behalf")
    return out


# --------------------------------------------------------------------------- #
# ACQUIRE / RELEASE  (held by drain.py and the scheduled-tick crons)
# --------------------------------------------------------------------------- #
class RunLock:
    """Hold the exclusive run lock for the lifetime of a tick.

    Usage (drain.py / a scheduled-tick wrapper):

        lock = RunLock(kind="run-standup", run_id="wf_…")
        if not lock.acquire():
            ...   # someone else is running -> DEFER
        try:
            ...   # launch + run the Workflow
        finally:
            lock.release()

    The flock is held on an OPEN FD stored on the instance; releasing closes it.
    If the process dies without calling release(), the kernel drops the flock.
    """

    def __init__(self, kind: str = "tick", run_id: Optional[str] = None,
                 holder: Optional[str] = None, control_dir: Optional[Path] = None):
        self.kind = kind
        self.run_id = run_id
        self.holder = holder or os.environ.get("STANDUP_SESSION_ID") or f"pid:{os.getpid()}"
        self.path = lock_path(control_dir)
        self._fd: Optional[int] = None

    def acquire(self) -> bool:
        """Take the exclusive lock NON-BLOCKING. Returns True on success, False if
        another live holder already holds it (caller must DEFER, never launch)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o644)
        if _HAVE_FCNTL:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(fd)
                return False
        else:  # pragma: no cover - stamp-only fallback
            holder = read_holder(self.path)
            if holder.get("held"):
                os.close(fd)
                return False
        self._fd = fd
        self._stamp()
        return True

    def _stamp(self, started_at: Optional[str] = None) -> None:
        """Write WHO holds the lock + SINCE WHEN into the lock file body so the
        portal (which does NOT hold the flock) can render an honest message.

        ``started_at`` lets a recovery path (the heartbeat reconciler) anchor the
        stamp to the RUNNING tick's real start (from the tick-active marker) instead
        of the reconcile instant, so the recovered lock and the real tick share a
        clock and the MAX_TICK_S dead-holder ceiling fires at the right time."""
        payload = {
            "holder": self.holder,
            "pid": os.getpid(),
            "run_id": self.run_id,
            "kind": self.kind,
            "started_at": started_at or _iso(),
        }
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        if self._fd is not None:
            os.ftruncate(self._fd, 0)
            os.lseek(self._fd, 0, os.SEEK_SET)
            os.write(self._fd, body)
            os.fsync(self._fd)

    def release(self) -> None:
        """Release the lock and clear the stamp. Idempotent."""
        if self._fd is None:
            return
        try:
            # Blank the stamp so a stale body can't be misread as a live holder.
            try:
                os.ftruncate(self._fd, 0)
                os.fsync(self._fd)
            except OSError:
                pass
            if _HAVE_FCNTL:
                try:
                    fcntl.flock(self._fd, fcntl.LOCK_UN)
                except OSError:
                    pass
        finally:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None

    def __enter__(self) -> "RunLock":
        if not self.acquire():
            raise RuntimeError("run.lock is already held by a live tick")
        return self

    def __exit__(self, *exc) -> None:
        self.release()


# --------------------------------------------------------------------------- #
# READ  (the portal guard — a DIFFERENT process that must NOT block / hold)
# --------------------------------------------------------------------------- #
def read_holder(path: Optional[Path] = None, now: Optional[_dt.datetime] = None) -> Dict[str, Any]:
    """Read the run lock WITHOUT blocking and WITHOUT taking it.

    Returns ``{held: bool, holder, pid, run_id, kind, started_at, age_s, reason}``.

    ``held`` is True when EITHER live-holder signal is present:
      * the flock is physically held by some process (we probe non-blocking: if WE
        can take it, nobody holds it), OR
      * a FRESH stamp exists — ``started_at`` within MAX_TICK_S. The stamp is the
        signal for a launch path that CANNOT keep an FD open across the launch
        (a cron PROMPT, and drain.py which exits before the runner session launches
        the Workflow): it stamps the lock body and the portal reads THAT, bounded by
        the MAX_TICK_S ceiling.

    A stamp OLDER than MAX_TICK_S is a dead/orphaned holder (a tick can't run that
    long) → IGNORED, so a crashed runner can't wedge the lock forever. A missing/
    empty/garbage lock file → ``held: False``. The portal's single-flight guard
    treats ``held: True`` as busy.
    """
    now = now or _now()
    p = path or lock_path()
    out: Dict[str, Any] = {
        "held": False, "holder": None, "pid": None, "run_id": None,
        "kind": None, "started_at": None, "age_s": None, "reason": None,
    }
    if not p.exists():
        out["reason"] = "no lock file"
        return out

    # Read the stamp (data, not truth).
    stamp: Dict[str, Any] = {}
    try:
        raw = p.read_text(encoding="utf-8").strip()
        if raw:
            stamp = json.loads(raw)
    except (OSError, json.JSONDecodeError, ValueError):
        stamp = {}

    started = _parse_iso(stamp.get("started_at")) if stamp else None
    age_s = int((now - started).total_seconds()) if started else None
    out.update({
        "holder": stamp.get("holder"), "pid": stamp.get("pid"),
        "run_id": stamp.get("run_id"), "kind": stamp.get("kind"),
        "started_at": stamp.get("started_at"), "age_s": age_s,
    })

    # (1) Is the flock physically held right now? Probe non-blocking: open the
    # file, try to take LOCK_EX|LOCK_NB. If we SUCCEED, nobody holds it (release
    # immediately). If we get EWOULDBLOCK, a live process holds it.
    flock_held: Optional[bool] = None
    if _HAVE_FCNTL:
        try:
            fd = os.open(str(p), os.O_RDWR)
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(fd, fcntl.LOCK_UN)  # we got it -> nobody held it
                    flock_held = False
                except OSError:
                    flock_held = True  # someone else holds it
            finally:
                os.close(fd)
        except OSError:
            flock_held = None  # couldn't probe; fall back to the stamp

    # (2) Stale-stamp ceiling: a tick can't legitimately run > MAX_TICK_S. A stamp
    # past it is a DEAD holder and must NOT count as held (the watchdog reconciles).
    stamp_fresh = (age_s is not None) and (age_s < MAX_TICK_S)
    stamp_stale = (age_s is not None) and (age_s >= MAX_TICK_S)

    # A holder is LIVE if the flock is physically held OR a fresh stamp exists.
    # BUT a stale stamp overrides a *free* flock to "dead holder ignored" (the
    # common crash case: holder died, flock auto-dropped, stamp lingers old).
    if flock_held is True:
        out["held"] = True
        out["reason"] = "flock held by a live holder"
    elif stamp_fresh:
        out["held"] = True
        out["reason"] = ("flock held + fresh stamp" if flock_held else
                         "fresh stamp (flock not held by a resident process)")
    elif stamp_stale:
        out["held"] = False
        out["reason"] = f"stamp stale (> {MAX_TICK_S // 60}min) -> dead holder ignored"
    elif flock_held is False:
        out["held"] = False
        out["reason"] = "flock free, no stamp"
    else:
        out["held"] = False
        out["reason"] = "empty/garbage lock"
    return out


def is_held(path: Optional[Path] = None, now: Optional[_dt.datetime] = None) -> bool:
    """Convenience: True iff a live holder holds the run lock."""
    return bool(read_holder(path=path, now=now).get("held"))


# --------------------------------------------------------------------------- #
# CLI — so a scheduled-tick cron PROMPT (which has no python wrapper) can stamp
# the lock at start and clear it at completion WITHOUT importing anything.
#   python3 control/run_lock.py acquire --kind run-standup --run-id wf_… [--hold]
#   python3 control/run_lock.py release
#   python3 control/run_lock.py status
#   python3 control/run_lock.py mark   --kind scheduled-tick --run-id wf_…  (drop marker)
#   python3 control/run_lock.py unmark                                       (clear marker)
#   python3 control/run_lock.py reconcile                                    (heartbeat backstop)
# `acquire` without --hold writes a STAMP and exits (the flock is dropped on exit,
# which is correct for a prompt that cannot hold an FD open across tool calls — it
# relies on the stamp + MAX_TICK_S ceiling, and on the busy-stamp discipline in
# RUNNER_SETUP.md). With --hold it blocks holding the flock until killed (for a
# wrapper that CAN stay resident). `release` blanks the stamp.
#
# `acquire` ALSO drops the tick-active marker as a side effect, and `release`
# clears it — so the heartbeat reconciler's backstop is armed by the SAME single
# line the prompt already runs. `mark`/`unmark` exist so the scheduled-tick prompt
# can drop the marker as its LITERAL FIRST STEP (before acquire), arming the
# backstop even if the acquire line itself is later dropped. `reconcile` is the
# heartbeat cron's per-minute step that stamps the lock for a marked-but-unlocked
# running tick.
# --------------------------------------------------------------------------- #
def _cli_acquire(kind: str, run_id: Optional[str], hold: bool) -> int:
    lock = RunLock(kind=kind, run_id=run_id)
    if not lock.acquire():
        h = read_holder()
        print(f"BUSY held_by={h.get('holder')} run_id={h.get('run_id')} since={h.get('started_at')}")
        return 1
    # Arm the heartbeat reconciler backstop: drop the tick-active marker so that if
    # this stamp is ever lost (e.g. a later prompt step clears the lock early), the
    # heartbeat cron re-covers the running tick within ≤1 min. Best-effort — a
    # marker failure must never fail the acquire (the lock is the primary signal).
    try:
        mark(run_id=run_id, kind=kind, holder=lock.holder)
    except OSError:
        pass
    print(f"ACQUIRED kind={kind} run_id={run_id} pid={os.getpid()} started_at={_iso()} (tick-active marker dropped)")
    if hold:
        try:
            import signal
            signal.pause()  # block holding the flock until the process is killed
        except (KeyboardInterrupt, AttributeError):
            pass
        finally:
            lock.release()
            unmark()
    # Without --hold we intentionally return WITHOUT releasing the flock: the FD
    # closes on process exit, but the STAMP remains as the cross-process signal
    # the portal reads (bounded by MAX_TICK_S). release() must be called to clear.
    return 0


def _cli_release() -> int:
    # Open + truncate the stamp body so the portal stops seeing a holder. (We
    # can't un-flock a lock we never held in this short-lived process; blanking
    # the stamp is the cross-process clear, and the MAX_TICK_S ceiling is the
    # backstop.)
    p = lock_path()
    try:
        fd = os.open(str(p), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            os.ftruncate(fd, 0)
            os.fsync(fd)
        finally:
            os.close(fd)
        print("RELEASED (stamp cleared)")
    except OSError as e:
        print(f"RELEASE-NOOP ({e})")
    # Clear the tick-active marker too — the tick is done, so the reconciler must
    # stop treating it as running.
    unmark()
    return 0


def _cli_status() -> int:
    h = read_holder()
    print(json.dumps(h, ensure_ascii=False))
    return 0 if not h.get("held") else 0


def _cli_mark(kind: str, run_id: Optional[str]) -> int:
    m = mark(run_id=run_id, kind=kind)
    print(f"MARKED tick-active run_id={run_id} kind={kind} started_at={m.get('started_at')}")
    return 0


def _cli_unmark() -> int:
    removed = unmark()
    print("UNMARKED" if removed else "UNMARK-NOOP (no marker)")
    return 0


def _cli_reconcile() -> int:
    r = reconcile_unlocked_tick()
    print(json.dumps(r, ensure_ascii=False))
    if r.get("reconciled"):
        # Loud, parseable line so the heartbeat cron prompt / operator sees it.
        print(f"RECONCILED run.lock stamped kind=scheduled-recovered run_id={r.get('run_id')} "
              f"(a running tick had NO lock — backstop covered it)")
    return 0


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Machine-owned run lock for tick launches")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("acquire")
    a.add_argument("--kind", default="tick")
    a.add_argument("--run-id", default=None)
    a.add_argument("--hold", action="store_true",
                   help="block holding the flock until killed (for a resident wrapper)")
    sub.add_parser("release")
    sub.add_parser("status")
    m = sub.add_parser("mark", help="drop the tick-active marker (arms the heartbeat reconciler)")
    m.add_argument("--kind", default="scheduled-tick")
    m.add_argument("--run-id", default=None)
    sub.add_parser("unmark", help="clear the tick-active marker (at tick completion)")
    sub.add_parser("reconcile", help="heartbeat backstop: stamp the lock for a marked-but-unlocked running tick")
    args = ap.parse_args(argv)
    if args.cmd == "acquire":
        return _cli_acquire(args.kind, args.run_id, args.hold)
    if args.cmd == "release":
        return _cli_release()
    if args.cmd == "status":
        return _cli_status()
    if args.cmd == "mark":
        return _cli_mark(args.kind, args.run_id)
    if args.cmd == "unmark":
        return _cli_unmark()
    if args.cmd == "reconcile":
        return _cli_reconcile()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
