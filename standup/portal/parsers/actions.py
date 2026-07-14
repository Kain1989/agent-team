"""Phase 2 — operator ACTIONS (mutating) + single-flight guard.

This is the contract `portal_frontend` + `app.py` build against. It is
intentionally conservative: the portal does NOT itself run a standup or a PM
review — there is NO synchronous trigger, because those workflows run only inside
a live Claude *runner* session (the Workflow tool), never as a CLI. So the portal
drops a *request file* the runner drains, and tracks that request's lifecycle.

MECHANISM (the architect's Option B — a FILE QUEUE the runner polls)
--------------------------------------------------------------------
  control/requests/<ts>-<uuid>.json  the ONLY thing the web tier writes. Shape:
        {id, action, args, workflow, requested_at, requested_by:"portal",
         status:"pending"}  — written atomically (temp + os.replace).
  control/results/<id>.json          the runner is the SINGLE writer here:
        {id, action, status:"running|done|failed", run_id, started_at,
         finished_at, note}. The portal only READS results to resolve lifecycle.
        (Exception: the portal's own TTL safety-sweep writes a `status:"expired"`
        result for a stale pending request — see `_sweep_expired`. That is a
        portal-side safety action, labelled honestly in the note, not a runner
        transition.)
  control/control.log                append-only audit of every request + sweep.

A request's effective lifecycle is therefore:
  pending (request file)  →  running → done|failed  (runner writes results/)
                          →  expired                 (portal TTL sweep)

THE SINGLE-FLIGHT GUARD (the system's worst-ever failure was a dual-runner
double-fire) is enforced in `guard()`. A launch is BLOCKED when ANY of:
  (0) the runner reports `dual_runner` (split-brain already detected) — HARD block;
  (1) THIS portal has an in-flight request (pending/running) in the queue;
  (2) the runner's heartbeat says `busy` (a tick it started itself);
  (3) a scheduled cron tick is imminent (< IMMINENT_TICK_S) — launching races it.
Each blocked launch returns a machine `code` + a `reason` written to be shown
VERBATIM to the operator — it states the CONSEQUENCE of a double-fire, not a
generic "busy".

TTL: a `pending` request older than PENDING_TTL_S with no live runner is swept to
`expired`, so a stale request can NEVER fire hours later when a runner resumes.
"""

from __future__ import annotations

import datetime as _dt
import importlib.util
import json
import os
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import paths

# A scheduled tick within this many seconds is "imminent" — launching now would
# race the cron tick. ~10 minutes per the UX spec.
IMMINENT_TICK_S = 10 * 60

# A `pending` request older than this with NO live runner is swept to `expired`.
# Longer than a poll interval (5 min) so a brief runner gap doesn't expire a
# just-queued request, short enough that it can never fire stale hours later.
PENDING_TTL_S = 30 * 60

# A `running` result with no live busy lock whose started_at is older than this
# is a CRASHED runner — the stuck-RUNNING watchdog reconciles it to `failed` so a
# dead runner can't wedge the buttons forever. Matches control/run_lock.MAX_TICK_S
# (a tick can't legitimately run longer). Imported live below where available so
# the two ceilings can never drift.
MAX_TICK_S = 70 * 60


# --- the shared machine-owned run lock (control/run_lock.py) ------------------
# The lock semantics (acquire on EVERY launch path, read without holding, ignore a
# stale stamp past MAX_TICK_S) live in ONE place: control/run_lock.py. The portal
# is the READ side; drain.py + the scheduled-tick crons are the HOLD side. We load
# that module BY PATH (it lives outside the portal package, under control/) so the
# portal never re-implements the lock test. If it is unavailable we degrade to
# "no machine lock observed" (the heartbeat-busy + queue guards still apply).
def _load_run_lock():
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


def read_run_lock(now: Optional[_dt.datetime] = None) -> Dict[str, Any]:
    """Read control/run.lock holder info WITHOUT taking the lock. Returns the
    shared module's {held, holder, run_id, kind, started_at, age_s, reason} (or a
    safe held:False default if the module/file is unavailable). ``held`` is True
    only for a LIVE holder (flock physically held, or stamp within MAX_TICK_S)."""
    mod = _load_run_lock()
    if mod is None:
        return {"held": False, "reason": "run_lock module unavailable"}
    try:
        return mod.read_holder(path=paths.run_lock(), now=now)
    except Exception:  # never let a lock-read crash a guard/launch
        return {"held": False, "reason": "run_lock read failed"}

# Whitelisted actions -> their FIXED workflow binding + SAFE default args. The web
# tier never supplies merge/deploy/pr/promote; those are not settable here.
_KIND_LABEL = {"run-standup": "Run standup", "pm-review": "PM review"}
_WORKFLOW = {
    # Both portal buttons run the MVP's one slim standup workflow — its Staff Pulse
    # already carries the pm_agent lens every tick, so there is no separate
    # product-review flow. Kept in lockstep with control/drain.py's WORKFLOW_FOR.
    "run-standup": "standup/standup.workflow.js",
    "pm-review": "standup/standup.workflow.js",
}
_DEFAULT_ARGS = {
    # NEVER expose merge/deploy/pr toggles to the web tier. Safe standup defaults.
    "run-standup": {"work": True, "maxTasks": 2, "pr": False, "merge": False},
    "pm-review": {},
}

# Serialize the read-guard-write critical section so two concurrent POSTs can't
# both pass the single-flight guard (in-process). Cross-process safety comes from
# the queue itself: the runner's drain is the only consumer and is single-flight.
_LOCK = threading.RLock()


# --- time helpers ------------------------------------------------------------
def _now() -> _dt.datetime:
    return _dt.datetime.now()


def _iso(dt: Optional[_dt.datetime]) -> Optional[str]:
    # tz-aware emit: attach the system-local offset at the boundary
    # (.astimezone() is idempotent on an already-aware dt). Math operands stay
    # naive; readers normalise aware->naive (see _parse_iso).
    return dt.astimezone().isoformat(timespec="seconds") if dt else None


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


def _clock(iso: Optional[str]) -> str:
    if not iso:
        return "—"
    try:
        return _dt.datetime.fromisoformat(iso).strftime("%H:%M")
    except (ValueError, TypeError):
        return "—"


# --- low-level fs (atomic, tolerant) -----------------------------------------
def _ensure_dirs() -> None:
    for d in (paths.control_dir(), paths.requests_dir(), paths.results_dir()):
        d.mkdir(parents=True, exist_ok=True)


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    """Write JSON atomically: temp in the SAME dir, fsync, then os.replace
    (atomic on POSIX) — a reader never sees a half-written file."""
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


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _append_log(line: str) -> None:
    """Append-only audit. Best-effort: a logging failure never breaks a write."""
    try:
        paths.control_dir().mkdir(parents=True, exist_ok=True)
        with open(paths.control_log(), "a", encoding="utf-8") as fh:
            fh.write(f"{_iso(_now())}  {line}\n")
    except OSError:
        pass


def _request_path(aid: str) -> Path:
    return paths.requests_dir() / f"{aid}.json"


def _result_path(aid: str) -> Path:
    return paths.results_dir() / f"{aid}.json"


# --- liveness (lazy import to avoid a cycle: app imports both) ----------------
def _runner_alive(now: Optional[_dt.datetime] = None) -> bool:
    from . import liveness
    return liveness.assess(now=now).get("state") == "alive"


def _read_heartbeat() -> Optional[Dict[str, Any]]:
    from . import liveness
    return liveness.read_heartbeat()


# --- queue inspection --------------------------------------------------------
def _list_requests() -> List[Dict[str, Any]]:
    """All request files, oldest-first (filenames are ts-prefixed)."""
    out: List[Dict[str, Any]] = []
    d = paths.requests_dir()
    if not d.exists():
        return out
    for p in sorted(d.glob("*.json")):
        if p.name.startswith(".tmp-"):
            continue
        rec = _read_json(p)
        if rec is not None and rec.get("id"):
            out.append(rec)
    return out


def _result_for(aid: str) -> Optional[Dict[str, Any]]:
    return _read_json(_result_path(aid))


def _effective_state(req: Dict[str, Any]) -> str:
    """Authoritative lifecycle state = the runner's result transition if one
    exists, else the request file's own status (`pending`)."""
    res = _result_for(req.get("id", ""))
    if res and res.get("status"):
        return res["status"]
    return req.get("status", "pending")


def _view(req: Dict[str, Any], res: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Merged lifecycle view of one action (the shape the UI + tests consume).

    `state` ∈ pending|running|done|failed|expired. Identity from the request,
    transition fields from the runner's result."""
    res = res if res is not None else _result_for(req.get("id", ""))
    res = res or {}
    state = res.get("status") or req.get("status", "pending")
    kind = req.get("action")
    return {
        "id": req.get("id"),
        "kind": kind,
        "action": kind,
        "label": _KIND_LABEL.get(kind, kind),
        "state": state,
        "workflow": req.get("workflow"),
        "args": req.get("args"),
        "run_id": res.get("run_id"),
        "created_at": req.get("requested_at"),
        "requested_at": req.get("requested_at"),
        "requested_by": req.get("requested_by"),
        "started_at": res.get("started_at"),
        "finished_at": res.get("finished_at"),
        "note": res.get("note"),
        "result": res.get("result"),
        "error": res.get("error") or (res.get("note") if state == "failed" else None),
        "source": "queue",
    }


# --- TTL safety sweep --------------------------------------------------------
def _sweep_expired(now: Optional[_dt.datetime] = None, runner_alive: Optional[bool] = None) -> List[str]:
    """Sweep stale `pending` requests to `expired` so one can never fire hours
    later when a runner resumes. Writes an `expired` result (honest note).
    Returns swept ids."""
    now = now or _now()
    if runner_alive is None:
        runner_alive = _runner_alive(now)
    swept: List[str] = []
    for req in _list_requests():
        if _effective_state(req) != "pending":
            continue
        req_at = _parse_iso(req.get("requested_at"))
        if req_at is None:
            continue
        age = (now - req_at).total_seconds()
        if age > PENDING_TTL_S and not runner_alive:
            aid = req.get("id")
            _atomic_write_json(
                _result_path(aid),
                {
                    "id": aid,
                    "action": req.get("action"),
                    "status": "expired",
                    "run_id": None,
                    "started_at": None,
                    "finished_at": _iso(now),
                    "note": (
                        f"portal safety sweep: pending > {PENDING_TTL_S // 60}min "
                        f"({int(age)}s) with no live runner; expired so it can't fire "
                        f"stale when a runner resumes"
                    ),
                },
            )
            _append_log(f"EXPIRE id={aid} action={req.get('action')} age_s={int(age)}")
            swept.append(aid)
    return swept


# --- stuck-RUNNING watchdog --------------------------------------------------
def _sweep_stuck_running(now: Optional[_dt.datetime] = None,
                         lock_held: Optional[bool] = None) -> List[str]:
    """Reconcile a CRASHED runner: a `running` result with NO live busy lock whose
    `started_at` is older than MAX_TICK_S is a runner that died mid-tick. Left
    alone it stays `running` forever → `in_flight` never clears → the action
    buttons are wedged permanently. We reconcile it to `failed` so a crashed runner
    can't wedge the buttons. (Before this, `_sweep_expired` only touched `pending`.)

    `lock_held` short-circuits the run-lock read (the live machine lock is the
    authority: if a tick really IS running, the lock is held and we must NOT
    reconcile). Returns reconciled ids.
    """
    now = now or _now()
    if lock_held is None:
        lock_held = bool(read_run_lock(now=now).get("held"))
    # If a tick is genuinely running, the machine lock is held — never reconcile.
    if lock_held:
        return []
    reconciled: List[str] = []
    for req in _list_requests():
        aid = req.get("id")
        res = _result_for(aid)
        if not res or res.get("status") != "running":
            continue
        started = _parse_iso(res.get("started_at"))
        if started is None:
            continue
        age = (now - started).total_seconds()
        if age <= MAX_TICK_S:
            continue
        _atomic_write_json(
            _result_path(aid),
            {
                "id": aid,
                "action": req.get("action") or res.get("action"),
                "status": "failed",
                "run_id": res.get("run_id"),
                "started_at": res.get("started_at"),
                "finished_at": _iso(now),
                "note": (
                    f"portal stuck-RUNNING watchdog: running > {MAX_TICK_S // 60}min "
                    f"({int(age)}s) with NO live run.lock holder — the runner crashed "
                    "mid-tick; reconciled to failed so it can't wedge the buttons forever"
                ),
                "error": "runner crashed mid-tick (no live lock; exceeded max tick TTL)",
                "reconciled_by": "portal_watchdog",
            },
        )
        _append_log(f"WATCHDOG-FAIL id={aid} action={req.get('action')} age_s={int(age)} (stuck running, no lock)")
        reconciled.append(aid)
    return reconciled


# --- public interface (consumed by app.py) -----------------------------------
def in_flight(now: Optional[_dt.datetime] = None) -> Optional[Dict[str, Any]]:
    """The action in the queue that is still pending/running, if any. Sweeps
    TTL-expired pendings AND stuck-RUNNING orphans first so neither a stale
    request nor a crashed runner can wedge the queue / lock the buttons."""
    now = now or _now()
    _sweep_expired(now=now)
    _sweep_stuck_running(now=now)
    for req in sorted(_list_requests(), key=lambda r: r.get("requested_at") or "", reverse=True):
        if _effective_state(req) in ("pending", "running"):
            return _view(req)
    return None


def get(action_id: str) -> Optional[Dict[str, Any]]:
    """One action's lifecycle for GET /api/actions/{id}. Returns None if unknown."""
    req = next((r for r in _list_requests() if r.get("id") == action_id), None)
    res = _result_for(action_id)
    if req is None and res is None:
        return None
    if req is None:
        # Only a result exists (e.g. request file pruned) — synthesize identity.
        req = {"id": action_id, "action": (res or {}).get("action"),
               "status": (res or {}).get("status", "pending")}
    return _view(req, res)


def list_actions(limit: int = 25) -> List[Dict[str, Any]]:
    """Recent actions, newest-first — for GET /api/actions."""
    views = [_view(r) for r in _list_requests()]
    views.sort(key=lambda v: v.get("created_at") or "", reverse=True)
    return views[:limit]


def guard(live: Dict[str, Any], now: Optional[_dt.datetime] = None) -> Dict[str, Any]:
    """Decide whether a launch is safe RIGHT NOW.

    `live` is parsers.liveness.assess output so the runner's own `busy` +
    `dual_runner` are honored alongside our own queue + the schedule.

    Returns {ok: bool, reason: str|None, code: str|None, detail: {...}} — `reason`
    is shown VERBATIM to the operator and states the CONSEQUENCE of double-firing.
    """
    now = now or _now()
    detail: Dict[str, Any] = {}

    # (0) Two runners already detected — the worst case. Hard block everything.
    if live.get("dual_runner") is True:
        return {
            "ok": False,
            "code": "dual_runner",
            "reason": (
                "DUAL RUNNER DETECTED — two runner processes are live at once. "
                "Launching anything now compounds the split-brain. All launches "
                "are disabled until one runner is killed."
            ),
            "detail": {"dual_runner": True},
        }

    # (1) Our own in-flight request (pending/running) in the queue.
    mine = in_flight(now=now)
    if mine is not None:
        started = mine.get("started_at") or mine.get("created_at")
        try:
            ago_m = int((now - _dt.datetime.fromisoformat(started)).total_seconds() // 60) if started else None
        except (ValueError, TypeError):
            ago_m = None
        wf = mine.get("run_id") or mine.get("id")
        return {
            "ok": False,
            "code": "in_flight",
            "reason": (
                f"Can't run — a {_KIND_LABEL.get(mine.get('kind'), 'run')} is already "
                f"in flight ({wf}"
                + (f", started {ago_m}m ago" if ago_m is not None else "")
                + "). A 2nd would double-fire: duplicate commits, racing deploys, 2× spend."
            ),
            "detail": {"in_flight": dict(mine), "started_min_ago": ago_m},
        }

    # (2) MACHINE-OWNED RUN LOCK (the core fix). control/run.lock is held for the
    # ENTIRE duration of ANY tick — scheduled cron OR portal-triggered. This is the
    # ONE signal that closes the proven double-fire: a 40-min SCHEDULED tick now
    # reads as busy even though the heartbeat cron keeps stamping busy:false and the
    # next_tick has rolled hours out. "Is a tick running" is a machine fact (a live
    # flock holder / a fresh stamp), never a stale heartbeat flag. A stale lock from
    # a DEAD holder (stamp older than MAX_TICK_S) is ignored by read_run_lock().
    lock = read_run_lock(now=now)
    if lock.get("held") is True:
        wf = lock.get("run_id") or live.get("last_run_id") or "wf_…"
        who = lock.get("holder") or "a runner"
        return {
            "ok": False,
            "code": "busy",
            "reason": (
                f"Can't run — a tick is already running ({wf}, held by {who}); a 2nd "
                "would double-fire: duplicate commits, racing deploys, 2× spend."
            ),
            "detail": {"busy": True, "run_id": wf, "lock": lock},
        }

    # (3) Runner's own heartbeat busy flag (a tick it set busy on, not via this
    # portal). Kept as a SECONDARY signal behind the machine lock: the lock is
    # authoritative, but if a path stamped heartbeat.busy without (yet) holding the
    # lock we still block. Belt-and-suspenders with (2).
    if live.get("busy") is True:
        wf = live.get("last_run_id") or "wf_…"
        return {
            "ok": False,
            "code": "busy",
            "reason": (
                f"Can't run — a tick is already running ({wf}); a 2nd would double-fire: "
                "duplicate commits, racing deploys, 2× spend."
            ),
            "detail": {"busy": True, "run_id": live.get("last_run_id")},
        }

    # (4) A scheduled tick is imminent, FIRING, or OVERDUE — launching now races
    # cron. We block when in_seconds < IMMINENT_TICK_S with NO lower bound: a tick
    # that is firing right now or is overdue has NEGATIVE in_seconds, and that
    # instant — between "cron fired" and "the tick took run.lock" — is the exact
    # open hole a `0 <=` lower bound left. Negative seconds now BLOCK.
    nt = live.get("next_tick") or {}
    in_s = nt.get("in_seconds")
    if isinstance(in_s, (int, float)) and in_s < IMMINENT_TICK_S:
        mins = int(in_s // 60)
        firing = in_s < 0
        if firing:
            when_phrase = (
                f"the scheduled {nt.get('name') or 'next'} tick is firing now / overdue "
                f"({_clock(nt.get('at'))})"
            )
        else:
            when_phrase = (
                f"the scheduled {nt.get('name') or 'next'} tick fires in ~{max(0, mins)}m "
                f"({_clock(nt.get('at'))})"
            )
        return {
            "ok": False,
            "code": "tick_imminent",
            "reason": (
                f"Can't run — {when_phrase}. Launching now would race it and "
                "double-fire (duplicate commits, racing deploys, 2× spend). Wait for the tick."
            ),
            "detail": {"next_tick": nt, "in_seconds": in_s, "firing": firing},
        }

    return {"ok": True, "reason": None, "code": None, "detail": detail}


def launch(
    kind: str,
    live: Dict[str, Any],
    now: Optional[_dt.datetime] = None,
    req_id: Optional[str] = None,
    extra_args: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Attempt to launch (queue) an action. Returns either:
        {"ok": True,  "action": {...}}              (202 Accepted; request written)
      | {"ok": False, "code": ..., "reason": ...}    (409 Conflict; guard blocked)
      | {"ok": False, "code": "bad_kind", ...}       (unknown action)

    Idempotent on `req_id`: a re-POST of the same id whose request file already
    exists is a no-op that returns the existing action (ok:True, idempotent:True).
    The guard is re-checked under the lock so concurrent POSTs can't both pass.
    """
    now = now or _now()
    if kind not in _KIND_LABEL:
        return {"ok": False, "code": "bad_kind", "reason": f"unknown action '{kind}'", "detail": {}}

    with _LOCK:
        _ensure_dirs()

        # Idempotency: a re-POST of the SAME id is a no-op.
        if req_id and _request_path(req_id).exists():
            existing = _read_json(_request_path(req_id)) or {}
            return {"ok": True, "idempotent": True, "action": _view(existing)}

        g = guard(live, now=now)
        if not g["ok"]:
            return {"ok": False, "code": g["code"], "reason": g["reason"], "detail": g.get("detail")}

        aid = req_id or (now.strftime("%Y%m%dT%H%M%S") + "-" + str(uuid.uuid4()))
        # Build SAFE args: backend defaults only; reject any forbidden toggle.
        args = dict(_DEFAULT_ARGS.get(kind, {}))
        if extra_args:
            for k, v in extra_args.items():
                if k in {"merge", "deploy", "pr", "promote"}:
                    return {"ok": False, "code": "forbidden_arg",
                            "reason": f"arg {k!r} is not permitted from the web tier", "detail": {}}
                if k == "maxTasks" and isinstance(v, int) and 1 <= v <= 4:
                    args["maxTasks"] = v

        record = {
            "id": aid,
            "action": kind,
            "workflow": _WORKFLOW[kind],
            "args": args,
            "requested_at": _iso(now),
            "requested_by": "portal",
            "status": "pending",
        }
        # The ONLY write the web tier makes — atomic, into control/requests/.
        _atomic_write_json(_request_path(aid), record)
        _append_log(
            f"QUEUE id={aid} action={kind} by=portal "
            f"args={json.dumps(args, separators=(',', ':'))}"
        )

        if os.environ.get("STANDUP_ACTIONS_SIM") == "1":
            _start_simulator(aid, kind)

        return {"ok": True, "action": _view(record)}


# --- verification simulator (only when STANDUP_ACTIONS_SIM=1) -----------------
# Drives pending -> running -> done|failed by WRITING result files (exactly like
# the runner would) on a compressed clock, so the frontend polling state machine
# can be exercised end-to-end without a real 50-min runner. Timings from env.
def _envf(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _start_simulator(aid: str, kind: str) -> None:
    to_running = _envf("STANDUP_SIM_RUNNING_S", 2.0)
    to_done = _envf("STANDUP_SIM_DONE_S", 5.0)
    fail = os.environ.get("STANDUP_SIM_FAIL") == "1"
    run_id = "wf_req_" + uuid.uuid4().hex[:8]

    def _advance_running():
        if _effective_state({"id": aid, "status": "pending"}) != "pending":
            return
        _atomic_write_json(_result_path(aid), {
            "id": aid, "action": kind, "status": "running", "run_id": run_id,
            "started_at": _iso(_now()), "finished_at": None,
            "note": "SIM: runner drained the request; Phase 1 of 4 — squad ticks (parallel)",
        })

    def _advance_done():
        if _effective_state({"id": aid, "status": "pending"}) != "running":
            return
        if fail:
            _atomic_write_json(_result_path(aid), {
                "id": aid, "action": kind, "status": "failed", "run_id": run_id,
                "started_at": _iso(_now()), "finished_at": _iso(_now()),
                "note": ("runner exited non-zero — a sub-agent hit a Snowflake auth "
                         "error; nothing was committed or posted."),
            })
            return
        _atomic_write_json(_result_path(aid), {
            "id": aid, "action": kind, "status": "done", "run_id": run_id,
            "started_at": _iso(_now()), "finished_at": _iso(_now()),
            "note": "complete",
            "result": {"worked": 14, "green": 11, "committed": 3, "prs": 2,
                       "summary": "14 worked · 11 green · 3 commits · 2 PRs"},
        })

    threading.Timer(to_running, _advance_running).start()
    threading.Timer(to_done, _advance_done).start()


# --- test/verification hooks -------------------------------------------------
def _reset_for_tests() -> None:
    """Clear the file queue (requests/ + results/) — used by tests only."""
    with _LOCK:
        for d in (paths.requests_dir(), paths.results_dir()):
            if d.exists():
                for p in d.glob("*.json"):
                    try:
                        p.unlink()
                    except OSError:
                        pass
