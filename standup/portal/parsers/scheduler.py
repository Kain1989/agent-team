"""In-process DAEMON scheduler — fires the 4 daily standup ticks from the portal.

WHY HOST THE SCHEDULER IN THE PORTAL DAEMON
-------------------------------------------
Session-scoped crons (scheduled inside a live Claude *runner* session) die with
the session / sleep with the app and leave no record — a whole class of "the tick
never fired and nothing logged it" failures. A persistent daemon does not: the
portal uvicorn stays up for as long as it is running. So we host the scheduler IN
the portal backend, the already-durable daemon.

THE FIRE MECHANISM
------------------
A scheduled fire runs a HEADLESS, BLOCKING `claude -p` (--permission-mode
bypassPermissions) whose prompt is the real RUNNER DUTY: it launches the REAL
standup workflow (`standup/standup.workflow.js`) via the **Workflow TOOL in the
BACKGROUND** (per-agent isolation is load-bearing — the script fans out to many
sub-agents, and running them inline would overflow context), then POLLS the
workflow's Task status until it COMPLETES, then does launcher duties (append the
log section, update BACKLOG, post the run summary), and ONLY THEN ends the turn.
The daemon `subprocess.run` WAITS for that process to exit.

The crux this design depends on — does a daemon-launched `claude -p` BLOCK until a
background Workflow completes, or EXIT EARLY (the one-shot trap)? — is handled by
the prompt: it explicitly instructs "launch in background, poll Task status to
completion, THEN do launcher duties", so the session blocks past the launch. We
use the REAL Workflow tool (NOT an inline-improv prompt).

SINGLE-FLIGHT — REUSE control/run.lock (NEVER reinvent)
-------------------------------------------------------
EVERY fire goes through the EXACT same machine-owned `control/run.lock` that
drain.py (portal-triggered actions) and the legacy scheduled-tick crons use. The
fire ACQUIRES the lock before launching and RELEASES it after the process exits.
If the lock is already held — a portal action is in flight, or (defensively) an
overlapping fire — the fire is SKIPPED (recorded status="skipped"), so a
scheduled tick and a portal action can NEVER double-fire. This is the whole point
of the lock: "is a tick running" is a machine fact, shared across every path.

HISTORY — control/runs/<run_id>.json
-------------------------------------
Every fire records a run (parsers.runs): running → done|failed (or skipped). The
portal reads these for /api/runs and the live "running" state on /api/status.

DESIGN
------
* `next_fire(now)` — the next (name, datetime) boundary from the SAME TICKS table
  parsers.liveness uses (08:00 / 14:07 / 20:17 / 02:27). One source of schedule
  truth; liveness imports nothing from here, here we read liveness.TICKS.
* `fire(name, ...)` — the synchronous fire: acquire lock → record running →
  launch the headless tick (injectable `launcher` for tests) → record done/failed
  → release lock. Returns the run record. Pure-ish: all side effects go through
  runs.record + the injected launcher, so a test drives it with a fake launcher
  and asserts the lock interaction + the run record WITHOUT a real 40-min claude.
* `run_loop(stop)` — the asyncio loop: sleep until the next boundary, fire, repeat.
  Started from app's FastAPI startup; cancelled on shutdown.

Stdlib + asyncio only (no APScheduler dependency — the loop is ~30 lines and
keeps the daemon's footprint identical to the rock-solid plain daemons here).
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import importlib.util
import json as _json
import logging
import os
import re as _re
import subprocess
import shutil
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

_log = logging.getLogger("standup.scheduler")

from . import liveness, paths, runs

# The headless tick mechanism proven in Step 1. A real fire blocks for ~40 min.
# Overridable via env for a faster smoke prompt.
CLAUDE_BIN = os.environ.get("STANDUP_CLAUDE_BIN") or shutil.which("claude") or "claude"
# A fire that has not produced an exit within this is killed (matches the run-lock
# dead-holder ceiling so a wedged claude can't hold the lock forever).
FIRE_TIMEOUT_S = int(os.environ.get("STANDUP_FIRE_TIMEOUT_S", str(70 * 60)))


# --- loop liveness (so a dead loop under a live uvicorn is DETECTABLE) --------
# The review's reincarnated failure mode is "looks alive, fires nothing": uvicorn
# is up, /healthz says ok, but the scheduler loop died and no tick will ever fire.
# We stamp these on every loop iteration so /healthz can surface loop_alive + the
# age of the last loop heartbeat, and a supervisor can restart a dead loop.
_LOOP_STATE: Dict[str, Any] = {
    "started_at": None,     # ISO when run_loop last (re)started
    "last_beat_at": None,   # ISO of the most recent loop iteration (sleep boundary set)
    "last_fire_at": None,   # ISO of the most recent fire() the loop launched
    "last_fire_tick": None, # name of that tick
    "fires": 0,             # total fires this loop has launched
    "restarts": 0,          # times the supervisor restarted a dead loop
    "alive": False,         # True between run_loop start and its return/death
}


def loop_state() -> Dict[str, Any]:
    """A snapshot of the scheduler loop's liveness for /healthz. `last_beat_age_s`
    is how long since the loop last took a step — a loop that died under a live
    uvicorn shows a growing age while `alive` may still read True until the
    supervisor flips it, so readers should treat a large age as 'loop wedged/dead'."""
    s = dict(_LOOP_STATE)
    beat = s.get("last_beat_at")
    age = None
    if beat:
        try:
            dt = _dt.datetime.fromisoformat(beat)
            # _iso() emits a tz-aware stamp; _now() is naive local. Normalise the
            # aware stamp to naive local so the subtraction doesn't raise.
            if dt.tzinfo is not None:
                dt = dt.astimezone().replace(tzinfo=None)
            age = max(0, int((_now() - dt).total_seconds()))
        except (ValueError, TypeError):
            age = None
    s["last_beat_age_s"] = age
    return s


def _mark_loop(**kw: Any) -> None:
    _LOOP_STATE.update(kw)


def _now() -> _dt.datetime:
    return _dt.datetime.now()


def _iso(dt: Optional[_dt.datetime] = None) -> str:
    return (dt or _now()).astimezone().isoformat(timespec="seconds")


# --- shared run lock (control/run_lock.py) — loaded BY PATH, same as actions.py -
def _load_run_lock():
    """Load the shared machine-owned run lock so the scheduler takes the SAME lock
    every other launch path takes. Returns the module or None (degraded: no lock —
    the loop still records history, but single-flight relies on the lock so this is
    logged loudly by the caller)."""
    mod_path = paths.run_lock_module()
    try:
        spec = importlib.util.spec_from_file_location("standup_run_lock", str(mod_path))
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except (OSError, ImportError, SyntaxError):
        return None


# --- schedule (one source of truth: liveness.TICKS) --------------------------
def next_fire(now: Optional[_dt.datetime] = None) -> Tuple[str, _dt.datetime]:
    """The next (tick_name, datetime) boundary at or after ``now`` — from the SAME
    TICKS table parsers.liveness uses (08:00 / 14:07 / 20:17 / 02:27), covering the
    day wrap for the 02:27 NIGHT tick."""
    now = now or _now()
    candidates: List[Tuple[_dt.datetime, str]] = []
    for day_offset in (0, 1):
        day = (now + _dt.timedelta(days=day_offset)).date()
        for name, hh, mm in liveness.TICKS:
            candidates.append((_dt.datetime(day.year, day.month, day.day, hh, mm), name))
    candidates.sort(key=lambda c: c[0])
    for dt, name in candidates:
        if dt > now:
            return name, dt
    # Unreachable (day+1 always has a future boundary), but be safe.
    dt, name = candidates[-1]
    return name, dt


def seconds_until_next(now: Optional[_dt.datetime] = None) -> float:
    now = now or _now()
    _, dt = next_fire(now)
    return max(0.0, (dt - now).total_seconds())


# --- the headless tick launcher (the REAL Workflow-tool mechanism) ------------
# The absolute path to the real 7-phase standup workflow script the runner duty
# launches. Same scriptPath the RUNNER_SETUP cron prompts + actions._WORKFLOW use.
WORKFLOW_SCRIPT = str(paths.standup_root() / "standup.workflow.js")
# Per-tick args, matching the RUNNER_SETUP cron table. MORNING is the full
# standup+plan+design pass; the other three are continue-work ticks. pr/merge are
# HARD false on every scheduled fire (the daemon never pushes/merges/deploys).
# `work` defaults True (a real continue-work tick); override to False (e.g. for the
# cheaper ~10-15min real-workflow proof) via STANDUP_TICK_WORK=0.
_TICK_WORK = os.environ.get("STANDUP_TICK_WORK", "1") != "0"


def _tick_args(name: str, date_str: str) -> Dict[str, Any]:
    """The args object handed to the REAL standup workflow for this tick.
    MORNING adds planning+design; all four share work/maxTasks and HARD-false
    pr/merge/deploy. roster is injected by the prompt from team.json at launch."""
    a: Dict[str, Any] = {
        "date": date_str,
        "since": "6 hours ago",
        "work": _TICK_WORK,
        "maxTasks": 2,
        "pr": False,
        "merge": False,
    }
    if name == "MORNING":
        a["planning"] = True
        a["design"] = True
    return a


def _tick_prompt(name: str, run_id: str, date_str: str, args: Dict[str, Any]) -> str:
    """The prompt a scheduled fire hands to `claude -p`. It is the real RUNNER
    DUTY: read team.json as the roster, launch the REAL standup workflow
    via the **Workflow tool in the BACKGROUND**, WAIT for it to complete (poll Task
    status — this is what makes `claude -p` block past the launch), then do launcher
    duties. NOT an inline-improv prompt.

    `args` is the per-tick args object MINUS roster (the prompt injects roster from
    team.json itself, so the huge JSON isn't duplicated into this string)."""
    args_json = _json.dumps(args, separators=(",", ":"))
    return (
        f"You are the daemon-launched headless runner for the daily standup "
        f"{name} tick on {date_str}. Run id: {run_id}. You have all permissions; "
        "do not ask any questions.\n"
        "Do EXACTLY this, in order:\n"
        "1. Run `date +%Y-%m-%d` to confirm today's date.\n"
        "2. Read team.json (its FULL contents) — you will pass it as "
        "args.roster.\n"
        "3. Launch the REAL standup workflow IN THE BACKGROUND with the Workflow "
        f"tool: Workflow({{ scriptPath: '{WORKFLOW_SCRIPT}', background: true, "
        f"args: {{ ...{args_json}, roster: <the parsed team.json object> }} }}). "
        "Do NOT improvise a standup inline and do NOT run the fan-out sub-agents yourself — "
        "the Workflow tool's per-agent isolation is load-bearing; launch the script "
        "and let it run.\n"
        "4. WAIT for that background workflow to COMPLETE: poll its Task status with "
        "the Task tools until it reaches a terminal state (completed/failed). Do NOT "
        "end your turn before it finishes — this blocking wait is required.\n"
        "5. ONLY AFTER it completes, do the launcher duties from the workflow's "
        f"returned result: (a) append a '## {name} ({date_str})' section to "
        f"log/{date_str}.md (create the file if missing) summarizing the run "
        "(run_id, health, worked/green/committed/prs, blockers); (b) update "
        "BACKLOG.md 'Last updated' + a dated blockquote.\n"
        "HARD constraints: never push, never open a PR, never merge or deploy "
        "(args already set pr:false, merge:false; do not add merge/deploy/promote).\n"
        "When all launcher duties are done, reply with the single word DONE."
    )


def _parse_claude_result(stdout: str) -> Dict[str, Any]:
    """Pull the workflow run_id + metrics out of `claude -p --output-format json`
    stdout if present, so the run record points at the REAL workflow run. Tolerant:
    a missing/garbage field just yields None — never raises."""
    out: Dict[str, Any] = {}
    try:
        j = _json.loads(stdout)
    except (ValueError, TypeError):
        return out
    res = j.get("result")
    out["claude_result"] = res if isinstance(res, str) else None
    out["num_turns"] = j.get("num_turns")
    out["duration_ms"] = j.get("duration_ms")
    # The runner duty reply (`result`) is prose; the authoritative workflow run_id
    # is best-effort scraped from it (wf_…) so /api/runs can point at the real log.
    if isinstance(res, str):
        m = _re.search(r"\bwf_[0-9a-fA-F]{6,}[0-9a-fA-F\-]*", res)
        if m:
            out["workflow_run_id"] = m.group(0)
    return out


def _headless_launcher(name: str, run_id: str) -> Dict[str, Any]:
    """Launch the REAL headless tick (claude -p hosting the background Workflow)
    and BLOCK until it exits. Returns {exit_code, log_ref, ...}. This is the default
    `launcher` for fire(); tests inject a fake so they never spawn a real claude.

    The mechanism: a daemon
    `subprocess.run` of `claude -p` whose prompt launches the background Workflow
    and polls it to completion BLOCKS until the workflow + launcher duties finish.
    """
    date_str = _now().date().isoformat()
    args = _tick_args(name, date_str)
    prompt = _tick_prompt(name, run_id, date_str, args)
    log_ref = f"log/{date_str}.md"
    try:
        proc = subprocess.run(
            [
                CLAUDE_BIN, "-p",
                "--output-format", "json",
                "--permission-mode", "bypassPermissions",
            ],
            input=prompt,
            cwd=str(paths.standup_root()),
            capture_output=True,
            text=True,
            timeout=FIRE_TIMEOUT_S,
        )
        parsed = _parse_claude_result(proc.stdout or "")
        return {
            "exit_code": proc.returncode,
            "log_ref": log_ref,
            "workflow_run_id": parsed.get("workflow_run_id"),
            "claude_result": parsed.get("claude_result"),
            "stderr_tail": (proc.stderr or "")[-400:],
        }
    except subprocess.TimeoutExpired:
        return {"exit_code": 124, "log_ref": log_ref,
                "stderr_tail": f"fire exceeded {FIRE_TIMEOUT_S}s timeout"}
    except (OSError, ValueError) as exc:
        return {"exit_code": -1, "log_ref": log_ref, "stderr_tail": str(exc)}


# --- the fire (synchronous; reuses run.lock; records history) -----------------
def fire(
    name: str,
    *,
    source: str = "scheduler",
    run_id: Optional[str] = None,
    now: Optional[_dt.datetime] = None,
    launcher: Optional[Callable[[str, str], Dict[str, Any]]] = None,
    run_lock_mod: Optional[Any] = None,
) -> Dict[str, Any]:
    """Fire ONE tick synchronously and return its run record.

    Sequence (the single-flight contract):
      1. ACQUIRE control/run.lock (the SAME lock drain.py + the legacy crons take).
         If it is already held → record status="skipped" and return WITHOUT
         launching. This is the lock refusing a double-fire, not an error.
      2. Record status="running" (lock acquired, about to launch).
      3. Run the headless tick (default: the Step-1 `claude -p` mechanism;
         `launcher` is injectable so tests drive it without a real claude).
      4. Record done (exit 0) | failed (non-zero), then RELEASE the lock.

    `launcher(name, run_id) -> {exit_code, log_ref[, worked/green/committed/prs]}`.
    """
    now = now or _now()
    run_id = run_id or f"sched-{now.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
    launcher = launcher or _headless_launcher
    rl = run_lock_mod if run_lock_mod is not None else _load_run_lock()

    # (1) single-flight via the shared run lock — REUSE, do not reinvent.
    lock = None
    if rl is not None:
        lock = rl.RunLock(kind=f"scheduled-{name.lower()}", run_id=run_id,
                          holder=f"portal-scheduler", control_dir=paths.control_dir())
        if not lock.acquire():
            held = rl.read_holder(path=paths.run_lock(), now=now)
            rec = runs.record(
                run_id, name, "skipped", source=source,
                started_at=_iso(now), finished_at=_iso(now),
                note=(f"single-flight: run.lock already held by "
                      f"{held.get('holder')} (run_id={held.get('run_id')}); "
                      "scheduled fire SKIPPED to avoid a double-fire"),
                lock={"acquired": False, "held_by": held.get("holder"),
                      "held_run_id": held.get("run_id")},
            )
            return rec
    # (2) record running (lock held — or degraded-no-lock, noted).
    runs.record(
        run_id, name, "running", source=source, started_at=_iso(now),
        note=("lock acquired; launching headless tick"
              if lock is not None else
              "WARNING: run_lock module unavailable — firing WITHOUT single-flight lock"),
        lock={"acquired": lock is not None},
    )
    # (3) run the tick synchronously (blocks until the process exits).
    try:
        result = launcher(name, run_id) or {}
    except Exception as exc:  # a launcher crash must not wedge the lock
        if lock is not None:
            lock.release()
            try:
                rl.unmark(control_dir=paths.control_dir())
            except Exception:
                pass
        return runs.record(
            run_id, name, "error", source=source, finished_at=_iso(),
            note=f"scheduler could not run the tick launcher: {exc}",
            lock={"acquired": lock is not None, "released": True},
        )
    # (4) record outcome + release the lock.
    exit_code = result.get("exit_code")
    status = "done" if exit_code == 0 else "failed"
    # The REAL workflow run_id the claude -p hosted (scraped from its reply), so the
    # history points at the actual Workflow run + its log section, not just the
    # scheduler's own sched-… id. None for a fake-launcher test or an early failure.
    wf_run_id = result.get("workflow_run_id")
    done_note = ("tick completed"
                 + (f" (workflow {wf_run_id})" if wf_run_id else "")) if status == "done" else (
        f"headless tick exited {exit_code}: {result.get('stderr_tail', '')}".strip())
    rec = runs.record(
        run_id, name, status, source=source, finished_at=_iso(),
        worked=result.get("worked"), green=result.get("green"),
        committed=result.get("committed"), prs=result.get("prs"),
        log_ref=result.get("log_ref"), exit_code=exit_code,
        workflow_run_id=wf_run_id,
        note=(result.get("note") or done_note),
        lock={"acquired": lock is not None, "released": True},
    )
    if lock is not None:
        lock.release()
        # Clear the tick-active marker acquire() dropped, mirroring drain.py/cron.
        try:
            rl.unmark(control_dir=paths.control_dir())
        except Exception:
            pass
    return rec


# --- runtime schedule control (control/schedule.json) ------------------------
# A small config the loop re-reads EVERY cycle so a human can START/STOP the
# recurring standup or change its interval WITHOUT restarting the process — the
# gap the env-only STANDUP_SCHEDULER flag left (written by the /daily-standup +
# /stop-daily-standup commands). Missing/invalid file => the legacy env flag is
# the default, so existing behavior is unchanged.
SCHEDULE_POLL_S = float(os.environ.get("STANDUP_SCHEDULE_POLL_S", "60"))


def schedule_state() -> Dict[str, Any]:
    """{enabled, interval_hours, work, maxTasks} from control/schedule.json.
    FIRING is decoupled from loop-start: STANDUP_SCHEDULER=1 only START the loop
    (run_local sets it so /portal always runs the loop); whether the loop actually
    FIRES is controlled at runtime by control/schedule.json, which DEFAULTS OFF.
    So a plain /portal runs the loop idle, and /daily-standup turns firing on with
    NO restart (and /stop-daily-standup turns it off) — closing the old foot-gun
    where /daily-standup silently did nothing unless the portal was restarted."""
    default_enabled = False
    try:
        raw = _json.loads((paths.control_dir() / "schedule.json").read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raw = {}
    except (OSError, ValueError):
        raw = {}
    iv = raw.get("interval_hours")
    try:
        iv = float(iv) if iv is not None else None
    except (TypeError, ValueError):
        iv = None
    return {
        "enabled": bool(raw.get("enabled", default_enabled)),
        "interval_hours": iv,
        "work": bool(raw.get("work", _TICK_WORK)),
        "maxTasks": int(raw.get("maxTasks", 2) or 2),
    }


def _next_interval_fire(now: _dt.datetime, interval_hours: float) -> Tuple[str, _dt.datetime]:
    """Deterministic interval slots aligned to local midnight: the next t > now with
    t = midnight + k*interval_hours. Interval 5 => 00:00/05:00/10:00/15:00/20:00."""
    interval = max(0.25, float(interval_hours))  # floor at 15 min
    midnight = _dt.datetime(now.year, now.month, now.day)
    elapsed_h = (now - midnight).total_seconds() / 3600.0
    k = int(elapsed_h // interval) + 1
    return ("STANDUP", midnight + _dt.timedelta(hours=k * interval))


# --- the asyncio loop (started by FastAPI startup) ---------------------------
async def run_loop(stop: "asyncio.Event") -> None:
    """Sleep until the next tick boundary, fire it, repeat — until ``stop`` is set.

    Runs the synchronous `fire()` in a thread (it blocks for ~40 min on a real
    tick) so the event loop — and the rest of the portal — stays responsive. The
    fire's single-flight is the run lock, so even if a boundary is hit while a
    portal action holds the lock, the fire is SKIPPED, never doubled.

    Stamps `_LOOP_STATE` on each iteration so /healthz can prove the loop is live
    (a dead loop under a live uvicorn is the "looks alive, fires nothing" failure
    this whole daemon exists to kill). The supervisor (`supervise`, started by the
    app) restarts this if it ever returns unexpectedly / raises.
    """
    _mark_loop(alive=True, started_at=_iso(), last_beat_at=_iso())
    while not stop.is_set():
        sched = schedule_state()
        _mark_loop(last_beat_at=_iso())
        # Runtime PAUSE: firing disabled -> poll for a re-enable, never fire.
        if not sched["enabled"]:
            try:
                await asyncio.wait_for(stop.wait(), timeout=SCHEDULE_POLL_S)
                break  # stop was set
            except asyncio.TimeoutError:
                continue
        # Enabled: next boundary from the interval (if set) or the fixed TICKS table.
        if sched["interval_hours"]:
            name, when = _next_interval_fire(_now(), sched["interval_hours"])
        else:
            name, when = next_fire()
        delay = max(0.0, (when - _now()).total_seconds())
        # Cap the sleep at POLL_S so a runtime disable / interval change is picked
        # up promptly (within ~POLL_S) instead of only at the next boundary.
        try:
            await asyncio.wait_for(stop.wait(), timeout=min(delay, SCHEDULE_POLL_S))
            break  # stop was set during the sleep
        except asyncio.TimeoutError:
            pass
        if stop.is_set():
            break
        # Fire ONLY if the boundary actually arrived AND firing is still enabled
        # (re-read so a /stop during the sleep is honored before launching).
        if _now() >= when and schedule_state()["enabled"]:
            try:
                # fire() blocks (subprocess.run); offload so the loop stays live.
                await asyncio.to_thread(fire, name, source="scheduler")
                _mark_loop(last_fire_at=_iso(), last_fire_tick=name,
                           fires=_LOOP_STATE.get("fires", 0) + 1)
            except Exception:
                # A fire must never kill the loop — the next boundary still fires.
                _log.exception("scheduler fire plumbing raised for tick %s", name)
            # Tiny sleep so a sub-second clock doesn't double-hit the same boundary.
            _mark_loop(last_beat_at=_iso())
            try:
                await asyncio.wait_for(stop.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
    _mark_loop(alive=False, last_beat_at=_iso())


# --- supervisor: restart the loop if it dies under a live uvicorn -------------
# Backoff between restarts so a hard-crashing loop doesn't spin a hot restart
# storm. The first restart is immediate-ish; later ones back off up to the cap.
SUPERVISOR_BACKOFF_S = float(os.environ.get("STANDUP_SUPERVISOR_BACKOFF_S", "5"))
SUPERVISOR_BACKOFF_MAX_S = float(os.environ.get("STANDUP_SUPERVISOR_BACKOFF_MAX_S", "60"))


async def supervise(stop: "asyncio.Event") -> None:
    """Run `run_loop` under supervision: if it returns or RAISES while ``stop`` is
    NOT set (i.e. it died, not a clean shutdown), LOG it and RESTART it with backoff.

    This closes the "looks alive, fires nothing" failure: previously a single
    unhandled exception in run_loop would kill the only scheduler task while uvicorn
    stayed up, and nothing would ever fire again — invisibly. Now a dead loop is
    logged, counted (`_LOOP_STATE['restarts']`), and revived; only a set ``stop``
    (app shutdown) ends supervision.
    """
    backoff = SUPERVISOR_BACKOFF_S
    while not stop.is_set():
        try:
            await run_loop(stop)
            if stop.is_set():
                return  # clean shutdown — run_loop returned because stop was set.
            # run_loop returned WITHOUT stop set → it died unexpectedly. Restart.
            _log.error("scheduler run_loop returned unexpectedly (stop not set) — restarting")
        except asyncio.CancelledError:
            _mark_loop(alive=False, last_beat_at=_iso())
            raise  # cancellation is a real shutdown signal — propagate.
        except Exception:
            _log.exception("scheduler run_loop CRASHED — restarting under supervisor")
        _mark_loop(alive=False)
        if stop.is_set():
            return
        _LOOP_STATE["restarts"] = _LOOP_STATE.get("restarts", 0) + 1
        # Backoff so a hard-failing loop can't hot-spin; bail early if stop fires.
        try:
            await asyncio.wait_for(stop.wait(), timeout=backoff)
            return  # stop set during backoff — done.
        except asyncio.TimeoutError:
            pass
        backoff = min(backoff * 2, SUPERVISOR_BACKOFF_MAX_S)


def status(now: Optional[_dt.datetime] = None) -> Dict[str, Any]:
    """Scheduler view for /api/status: the REAL next tick (name, at, in_seconds)
    from the in-process schedule, plus the live running record if a tick is in
    flight right now."""
    now = now or _now()
    name, when = next_fire(now)
    running = runs.latest_running()
    return {
        "enabled": True,
        "next_tick": {
            "name": name,
            "at": when.astimezone().isoformat(timespec="seconds"),
            "in_seconds": int((when - now).total_seconds()),
        },
        "running": running,
    }
