#!/usr/bin/env python3
"""control/drain.py — the runner-side poller's queue step.

WHAT THIS SCRIPT DOES (and HONESTLY does NOT)
---------------------------------------------
The portal can only *ask* for a standup/PM-review — it drops a request file into
control/requests/. It cannot launch a Workflow, because the standup + PM-review
workflows run ONLY inside a live Claude runner session (the Workflow tool), never
as a CLI. There is no synchronous trigger.

So a runner-side 5-minute poller-cron runs this script. This script:
  1. GUARDS single-flight: if control/heartbeat.json says busy -> exit 0 (defer).
  2. Sweeps TTL-expired stale pendings (so a request can't fire hours stale).
  3. Pops the OLDEST `pending` request, transitions it to `running` in
     control/results/<id>.json (the runner is the single writer of results), and
  4. PRINTS, on stdout, the EXACT `Workflow({...})` call the runner's poller-cron
     PROMPT must then launch — plus a machine-readable LAUNCH line the prompt can
     parse.

It does NOT launch the Workflow itself. The actual launch happens in the runner's
Claude session, driven by the poller-cron prompt (see RUNNER_SETUP.md), because
only that session has the Workflow tool. This script guards + prepares the queue;
the session launches and (on completion) writes the `done|failed` result.

EXIT CODES / STDOUT CONTRACT (for the poller-cron prompt to read)
  - prints `DEFER busy` and exits 0     -> a run is in-flight; do nothing.
  - prints `EMPTY` and exits 0          -> no pending request; do nothing.
  - prints a `LAUNCH <id> <action>` line followed by the `Workflow({...})` call
    -> the prompt must (a) set heartbeat busy, (b) launch EXACTLY that Workflow,
       (c) on completion write the done/failed result + clear busy.

Dependency-free (stdlib only). Atomic writes (temp + os.replace).
"""

from __future__ import annotations

import datetime as _dt
import importlib.util
import json
import os
import tempfile
from pathlib import Path

# STANDUP_ROOT-aware (mirrors parsers/paths.py + control/heartbeat.py): seed the
# root from the env at import, keeping the __file__-derived default. This lets the
# env-driven isolation path (test_api's isolated_status fixture -> STANDUP_ROOT)
# Just Work, AND — because the heartbeat/log/team/lock reads below derive from
# CONTROL_DIR at CALL TIME — patching drain.CONTROL_DIR alone redirects the entire
# _sweep_expired transitive read graph (incl. _runner_alive's heartbeat read) into
# the isolated dir, instead of leaking onto the live root.
_DEFAULT_ROOT = Path(__file__).resolve().parent.parent
STANDUP_ROOT = Path(os.environ.get("STANDUP_ROOT", str(_DEFAULT_ROOT))).resolve()
CONTROL_DIR = STANDUP_ROOT / "control"
REQUESTS = CONTROL_DIR / "requests"
RESULTS = CONTROL_DIR / "results"
RUN_LOCK_PY = CONTROL_DIR / "run_lock.py"


def _load_run_lock():
    """Load the shared machine-owned run lock (control/run_lock.py)."""
    try:
        spec = importlib.util.spec_from_file_location("standup_run_lock", str(RUN_LOCK_PY))
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except (OSError, ImportError, SyntaxError):
        return None


_RUN_LOCK = _load_run_lock()

# A pending request older than this (with no live runner) is swept to expired.
# Kept in sync with parsers/actions.PENDING_TTL_S.
PENDING_TTL_S = 30 * 60
# Heartbeat older than this => no live runner (kept in sync with liveness).
HEARTBEAT_MAX_AGE_S = 90

WORKFLOW_FOR = {
    "run-standup": "standup/standup.workflow.js",
    # MVP: both portal buttons run the slim standup workflow — its Staff Pulse already
    # carries the pm_agent lens every tick, so there is no separate product-review flow.
    "pm-review": "standup/standup.workflow.js",
}


def _now() -> _dt.datetime:
    return _dt.datetime.now()


def _iso(dt: _dt.datetime | None = None) -> str:
    # tz-aware emit: stamp started_at/finished_at into control/results/*.json
    # with the system-local offset (.astimezone() at the boundary). These flow
    # VERBATIM through actions._view -> /api/status. _parse_iso normalises
    # aware->naive, so the writer being aware is safe for drain's own sweeps.
    return (dt or _now()).astimezone().isoformat(timespec="seconds")


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
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


def _append_log(line: str) -> None:
    try:
        CONTROL_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONTROL_DIR / "control.log", "a", encoding="utf-8") as fh:
            fh.write(f"{_iso()}  {line}\n")
    except OSError:
        pass


def _heartbeat_busy() -> bool:
    hb = _read_json(CONTROL_DIR / "heartbeat.json")
    return bool(hb and hb.get("busy"))


def _run_lock_held() -> bool:
    """True iff control/run.lock is held by a LIVE holder (a tick — scheduled OR
    portal-triggered — is running). This is the machine-owned single-flight signal
    that the heartbeat.busy flag never reliably carried for scheduled ticks."""
    if _RUN_LOCK is None:
        return False
    try:
        return bool(_RUN_LOCK.read_holder(path=CONTROL_DIR / "run.lock", now=_now()).get("held"))
    except Exception:
        return False


def _runner_alive(now: _dt.datetime) -> bool:
    hb = _read_json(CONTROL_DIR / "heartbeat.json")
    if not hb:
        return False
    try:
        ts = _dt.datetime.fromisoformat(str(hb.get("ts")).replace("Z", "+00:00"))
        if ts.tzinfo is not None:
            ts = ts.astimezone().replace(tzinfo=None)
    except (ValueError, TypeError):
        return False
    return (now - ts).total_seconds() < HEARTBEAT_MAX_AGE_S


def _result_state(aid: str, req_status: str) -> str:
    res = _read_json(RESULTS / f"{aid}.json")
    if res and res.get("status"):
        return res["status"]
    return req_status


def _list_requests():
    out = []
    if not REQUESTS.exists():
        return out
    for p in sorted(REQUESTS.glob("*.json")):
        if p.name.startswith(".tmp-"):
            continue
        rec = _read_json(p)
        if rec and rec.get("id"):
            out.append(rec)
    return out


def _sweep_expired(now: _dt.datetime) -> list[str]:
    if _runner_alive(now):
        return []
    swept = []
    for req in _list_requests():
        if _result_state(req["id"], req.get("status", "pending")) != "pending":
            continue
        try:
            req_at = _dt.datetime.fromisoformat(str(req.get("requested_at")).replace("Z", "+00:00"))
            if req_at.tzinfo is not None:
                req_at = req_at.astimezone().replace(tzinfo=None)
        except (ValueError, TypeError):
            continue
        age = (now - req_at).total_seconds()
        if age > PENDING_TTL_S:
            _atomic_write_json(RESULTS / f"{req['id']}.json", {
                "id": req["id"], "action": req.get("action"), "status": "expired",
                "run_id": None, "started_at": None, "finished_at": _iso(now),
                "note": f"drain TTL sweep: pending > {PENDING_TTL_S // 60}min ({int(age)}s), no live runner",
            })
            _append_log(f"EXPIRE id={req['id']} action={req.get('action')} age_s={int(age)} (drain)")
            swept.append(req["id"])
    return swept


def _oldest_pending():
    for req in _list_requests():  # already oldest-first by ts-prefixed filename
        if _result_state(req["id"], req.get("status", "pending")) == "pending":
            return req
    return None


def _roster_arg() -> str:
    """The standup workflow takes the roster. We pass the LITERAL team.json so the
    cron prompt can paste a self-contained call; fall back to the embedded roster
    in the workflow if team.json is unreadable (the workflow already does this)."""
    team = _read_json(STANDUP_ROOT / "team.json")
    if team is None:
        return ""  # let the workflow use its EMBEDDED_ROSTER fallback
    # Compact one-line JSON so the printed Workflow call stays a single block.
    return json.dumps(team, separators=(",", ":"))


def _workflow_call(req: dict) -> str:
    """Build the EXACT Workflow({...}) call string the poller-cron prompt launches."""
    action = req.get("action")
    date = _now().date().isoformat()
    script = WORKFLOW_FOR.get(action, "standup/standup.workflow.js")
    if action == "run-standup":
        args = dict(req.get("args") or {})
        # Safe defaults already baked by the portal; never add merge/deploy/pr here.
        roster = _roster_arg()
        arg_obj = {
            "date": date,
            "since": "6 hours ago",
            "work": bool(args.get("work", True)),
            "maxTasks": int(args.get("maxTasks", 2)),
            "pr": False,
            "merge": False,
        }
        if roster:
            arg_obj["roster"] = "<team.json>"  # placeholder; prompt pastes the real roster
        pretty = json.dumps(arg_obj, indent=2)
        return (
            f"Workflow({{\n"
            f"  scriptPath: '{script}',\n"
            f"  args: {pretty}\n"
            f"}})\n"
            f"# NOTE: replace \"roster\": \"<team.json>\" with the literal contents of "
            f"team.json (the workflow also falls back to its embedded roster)."
        )
    # pm-review (MVP): the same slim standup workflow, lighter (pm lens runs every tick).
    args = dict(req.get("args") or {})
    roster = _roster_arg()
    arg_obj = {
        "date": date,
        "since": "6 hours ago",
        "work": bool(args.get("work", False)),
        "maxTasks": int(args.get("maxTasks", 1)),
        "pr": False,
        "merge": False,
    }
    if roster:
        arg_obj["roster"] = "<team.json>"
    pretty = json.dumps(arg_obj, indent=2)
    return (
        f"Workflow({{\n"
        f"  scriptPath: '{script}',\n"
        f"  args: {pretty}\n"
        f"}})\n"
        f"# NOTE: replace \"roster\": \"<team.json>\" with the literal contents of team.json."
    )


def main() -> int:
    now = _now()

    # (1) single-flight: defer if a tick is ALREADY running. The AUTHORITATIVE
    # signal is the machine-owned run lock (covers BOTH a scheduled cron tick and a
    # prior portal-triggered run); heartbeat.busy is the legacy secondary signal.
    # A scheduled tick holds run.lock for its whole 40-min duration, so this is what
    # closes the proven double-fire (heartbeat.busy was never set by scheduled ticks).
    if _run_lock_held():
        h = _RUN_LOCK.read_holder(path=CONTROL_DIR / "run.lock", now=now) if _RUN_LOCK else {}
        print(f"DEFER busy  # run.lock held (a tick is running: run_id={h.get('run_id')} "
              f"holder={h.get('holder')} since={h.get('started_at')}); not draining.")
        return 0
    if _heartbeat_busy():
        print("DEFER busy  # a run is in-flight (heartbeat.busy=true); not draining.")
        return 0

    # (2) TTL sweep.
    swept = _sweep_expired(now)
    for sid in swept:
        print(f"EXPIRED {sid}")

    # (3) pop the oldest pending.
    req = _oldest_pending()
    if req is None:
        print("EMPTY  # no pending request to drain.")
        return 0

    aid = req["id"]
    action = req["action"]
    run_id = "wf_req_" + aid.split("-")[-1][:8]

    # (4) ACQUIRE THE MACHINE RUN LOCK before staging the launch. This is the core
    # fix: the portal guard reads run.lock and refuses any 2nd launch for the whole
    # duration of this tick. If another holder grabbed it between (1) and here, we
    # lost a race — DEFER rather than double-fire. drain.py is short-lived (it exits
    # before the Workflow runs in the runner session), so it cannot hold the flock
    # across the launch; instead it STAMPS the lock body (holder/run_id/started_at)
    # which the portal reads, bounded by run_lock.MAX_TICK_S, and the poller-cron
    # PROMPT clears it on completion (step 4 below). The flock taken here also wins
    # any race between two concurrent drains/instances in the instant they overlap.
    lock = None
    if _RUN_LOCK is not None:
        lock = _RUN_LOCK.RunLock(kind=action, run_id=run_id, control_dir=CONTROL_DIR)
        if not lock.acquire():
            h = _RUN_LOCK.read_holder(path=CONTROL_DIR / "run.lock", now=now)
            print(f"DEFER busy  # lost the run.lock race (held by {h.get('holder')}); not draining.")
            return 0
        # Stamp written by acquire(); the FD closes when this process exits, leaving
        # the STAMP as the cross-process signal (cleared on completion via step 4).

    # (5) transition to running (runner is the single writer of results/).
    _atomic_write_json(RESULTS / f"{aid}.json", {
        "id": aid, "action": action, "status": "running", "run_id": run_id,
        "started_at": _iso(now), "finished_at": None,
        "note": "drained by poller; run.lock acquired; launching the Workflow in the runner session",
    })
    _append_log(f"DRAIN id={aid} action={action} run_id={run_id} lock=acquired -> running")

    # (6) print the exact launch the poller-cron prompt must perform.
    print(f"LAUNCH {aid} {action} {run_id}")
    print("# run.lock is ALREADY HELD/stamped by drain.py for this run.")
    print("# 1) (belt-and-suspenders) also set heartbeat busy:  python3 control/heartbeat.py --busy --run-id " + run_id)
    print("# 2) launch EXACTLY this Workflow in THIS session:")
    print(_workflow_call(req))
    print(
        "# 3) on completion, write the result:\n"
        f"#    python3 - <<'PY'\n"
        f"#    import json,os,tempfile,datetime\n"
        f"#    from pathlib import Path\n"
        f"#    r=Path('control/results/{aid}.json')\n"
        f"#    r.write_text(json.dumps({{'id':'{aid}','action':'{action}','status':'done',"
        f"'run_id':'{run_id}','finished_at':datetime.datetime.now().astimezone().isoformat(timespec='seconds'),"
        f"'note':'<one-line outcome>','result':{{}}}}))\n"
        f"#    PY\n"
        "# 4) RELEASE the run lock + clear busy (REQUIRED — without this the portal\n"
        "#    keeps reading busy until run_lock.MAX_TICK_S elapses):\n"
        "#    python3 control/run_lock.py release\n"
        "#    python3 control/heartbeat.py   # clears heartbeat busy (no --busy)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
