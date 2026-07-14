"""Team-status portal backend.

FastAPI app that parses the standup artifacts live (stat-checking mtimes and only
re-parsing changed files) and serves the JSON contract the frontend builds
against. No DB. Bound to 127.0.0.1 only by run_local.sh.

Read endpoints (Phase 1):
  GET  /api/status     — the full aggregate contract (see README).
  GET  /api/team       — roster (squads + staff + bench).
  GET  /api/log?date=  — parsed ticks for one day (default today).
  GET  /api/heartbeat  — raw runner liveness (+ in_flight mirror).
  GET  /api/blockers   — the awaiting-Kain blocker list.
  GET  /healthz        — portal self-check.

Action endpoints (Phase 2 — mutating, single-flight guarded; see parsers/actions.py):
  POST /api/actions/run-standup — request an off-cadence standup tick (202|409).
  POST /api/actions/pm-review    — request a PM review pass (202|409).
  GET  /api/actions/guard        — is a launch safe right now?
  GET  /api/actions/{id}         — poll an action: pending→running→done|failed.

Scheduler endpoints (Phase 3 — the in-process DAEMON scheduler; see parsers/scheduler.py):
  GET  /api/runs                 — tick fire HISTORY (newest-first, from control/runs/).
  (the live next-tick countdown + running state also feed /api/status.runner)

The DAEMON scheduler replaces the old in-app CronCreate model: it fires the 4
daily ticks (08:00/14:07/20:17/02:27) FROM this persistent process, every fire
going through the same control/run.lock single-flight, recording history into
control/runs/. Enabled when STANDUP_SCHEDULER=1 (kept off by default so importing
the app — e.g. in tests — never spawns a real claude). See control/RUNNER_SETUP.md.

Static frontend served from portal/static/ at /.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as _dt
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from urllib.parse import urlsplit

from fastapi import Body, FastAPI, Header, Query, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from parsers import actions, backlog, comms, devlog, jobworker, liveness, log, native_teams, paths, runs, scheduler, team

import api_jobs


# --- DAEMON scheduler lifecycle ----------------------------------------------
# The scheduler loop runs as an asyncio task for the lifetime of this process. It
# is started ONLY when STANDUP_SCHEDULER=1 so that merely importing the app (tests,
# `python -c "import app"`) never spawns the loop / a real claude. run_local.sh sets
# the flag on the single host where the daemon owns the schedule.
_SCHED_ENABLED = os.environ.get("STANDUP_SCHEDULER") == "1"

# The JOB WORKER (interactive board, Slice 1) is a SECOND supervised asyncio task,
# enabled independently via STANDUP_JOBWORKER=1 (mirrors STANDUP_SCHEDULER). Default
# OFF so importing the app in tests never spawns a worker / a real claude. It is the
# single writer of job-state transitions; HTTP handlers only enqueue / set cancel.
_JOBWORKER_ENABLED = os.environ.get("STANDUP_JOBWORKER") == "1"


_log = logging.getLogger("standup.portal")


def _scheduler_task_done(task: "asyncio.Task") -> None:
    """add_done_callback for the supervisor task: a clean stop is expected on
    shutdown, but if the SUPERVISOR task itself ends while the app is still up, log
    it LOUDLY — that is the "looks alive, fires nothing" failure (uvicorn live, no
    scheduler). The supervisor restarts run_loop internally; this guards the rare
    case the supervisor coroutine itself dies/cancels."""
    if task.cancelled():
        _log.info("scheduler supervisor task cancelled (shutdown)")
        return
    exc = task.exception()
    if exc is not None:
        _log.error("scheduler supervisor task DIED with %r — the daemon is no longer "
                   "scheduling ticks; the loop must be restarted (restart the portal)", exc)
    else:
        _log.warning("scheduler supervisor task ended without an exception while the app "
                     "is up — no further ticks will fire until the portal is restarted")


def _jobworker_task_done(task: "asyncio.Task") -> None:
    """add_done_callback for the job-worker supervisor — same loud-death logging as
    the scheduler's: a clean stop is expected on shutdown, but a supervisor task that
    ends while the app is up is the "looks alive, runs nothing" failure (uvicorn live,
    no job worker). The supervisor restarts run_loop internally; this guards the rare
    case the supervisor coroutine itself dies/cancels."""
    if task.cancelled():
        _log.info("jobworker supervisor task cancelled (shutdown)")
        return
    exc = task.exception()
    if exc is not None:
        _log.error("jobworker supervisor task DIED with %r — board jobs will no longer "
                   "run; the worker must be restarted (restart the portal)", exc)
    else:
        _log.warning("jobworker supervisor task ended without an exception while the app "
                     "is up — no further jobs will run until the portal is restarted")


@contextlib.asynccontextmanager
async def _lifespan(app: "FastAPI"):
    stop = asyncio.Event()
    task = None
    job_stop = asyncio.Event()
    job_task = None
    if _SCHED_ENABLED:
        app.state.scheduler_stop = stop
        # Run the loop UNDER A SUPERVISOR (restarts it if it dies) instead of bare —
        # a single unhandled exception used to kill the only scheduler task silently.
        task = asyncio.create_task(scheduler.supervise(stop))
        task.add_done_callback(_scheduler_task_done)
        app.state.scheduler_task = task
    if _JOBWORKER_ENABLED:
        app.state.jobworker_stop = job_stop
        # The board job worker, beside the scheduler — same supervise pattern, its
        # own stop event, started only when explicitly enabled.
        job_task = asyncio.create_task(jobworker.supervise(job_stop))
        job_task.add_done_callback(_jobworker_task_done)
        app.state.jobworker_task = job_task
    try:
        yield
    finally:
        for ev, tk in ((stop, task), (job_stop, job_task)):
            if tk is not None:
                ev.set()
                tk.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await tk


app = FastAPI(title="Team Status Portal", version="0.2.0", lifespan=_lifespan)

# --- interactive board job API (Slice 1) -------------------------------------
# The /api/jobs* router is mounted on THIS app so it inherits the loopback bind,
# the TrustedHost allow-list, and the CSRF guard below. The worker that runs the
# jobs is started in _lifespan only when STANDUP_JOBWORKER=1.
app.include_router(api_jobs.router)

# --- Host allow-list (defense in depth alongside the 127.0.0.1 bind) ----------
# The portal is bound to loopback by run_local.sh, but TrustedHostMiddleware adds
# a Host-header allow-list so a request that reaches the app with a foreign Host
# (e.g. a DNS-rebinding attempt) is rejected with 400 before any handler runs.
# Single-process, single-instance, loopback-only — see README.
_ALLOWED_HOSTS = ["127.0.0.1", "localhost", "127.0.0.1:8770", "localhost:8770",
                  "testserver"]  # 'testserver' = Starlette TestClient default Host
app.add_middleware(TrustedHostMiddleware, allowed_hosts=_ALLOWED_HOSTS)


# --- no-cache for the HTML/JS/CSS bundle --------------------------------------
# The static assets are served by StaticFiles, which sets ETag + Last-Modified but
# NO Cache-Control. With no Cache-Control a browser HEURISTICALLY caches the file
# (typically ~10% of (now - Last-Modified)) and serves a STALE app.js/app.css after
# we edit the frontend — users see an old/empty board. We force a revalidation on
# every load by stamping `Cache-Control: no-store` on exactly the HTML/JS/CSS entry
# paths. The hashed-content fonts under /fonts/ keep their normal caching (stable),
# and /api/* responses are untouched, so no API behavior changes.
_NO_CACHE_PATHS = frozenset({"/", "/index.html", "/app.js", "/app.css"})


@app.middleware("http")
async def _no_cache_static(request: Request, call_next):
    response = await call_next(request)
    if request.url.path in _NO_CACHE_PATHS:
        response.headers["Cache-Control"] = "no-store"
    return response


# Origin/Referer hosts a same-origin loopback request may carry. A cross-Origin
# POST (a simple-CSRF form from another site) carries a foreign Origin/Referer and
# is rejected by `_csrf_ok` below.
_ALLOWED_CSRF_HOSTS = {"127.0.0.1", "localhost"}


def _host_of(url: Optional[str]) -> Optional[str]:
    """Hostname (no port) of an Origin/Referer URL, or None if unparseable."""
    if not url:
        return None
    try:
        return (urlsplit(url).hostname or "").lower() or None
    except ValueError:
        return None


def _csrf_ok(request: Request) -> bool:
    """Reject a cross-site POST (simple-CSRF defense for the two mutating action
    endpoints). A browser sets Origin (and/or Referer) on a cross-site POST that
    simple-CSRF cannot forge. We REQUIRE that, when an Origin/Referer is present,
    its host is 127.0.0.1/localhost. A non-browser client (curl/the runner) sends
    NEITHER header — that is allowed (it isn't a browser CSRF vector). A custom
    header `X-Requested-By: portal` that a cross-site simple form also cannot set
    is accepted as an explicit same-origin assertion."""
    # Explicit same-origin marker a cross-site <form> cannot set.
    if (request.headers.get("x-requested-by") or "").strip().lower() == "portal":
        return True
    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    # No Origin AND no Referer → not a browser cross-site form post (e.g. curl,
    # the runner, fetch with no-referrer same-origin). Allowed.
    if not origin and not referer:
        return True
    for raw in (origin, referer):
        host = _host_of(raw)
        if host is not None and host not in _ALLOWED_CSRF_HOSTS:
            return False
    return True


def _csrf_block() -> JSONResponse:
    return JSONResponse(
        {"queued": False, "code": "forbidden_origin",
         "reason": ("Rejected — this POST came from a foreign origin. The action "
                    "endpoints accept same-origin (127.0.0.1/localhost) requests only.")},
        status_code=403,
    )

# --- tiny mtime-keyed cache: only re-parse a file when its mtime changes ------
# Each entry: key -> (mtime_signature, parsed_value). Offline-safe: if a stat
# fails we keep the last-known value and mark it stale.
_CACHE: Dict[str, Any] = {}


def _mtime(path: Path) -> Optional[float]:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _cached(key: str, signature: Any, producer):
    entry = _CACHE.get(key)
    if entry is not None and entry[0] == signature:
        return entry[1]
    value = producer()
    _CACHE[key] = (signature, value)
    return value


# --- cached parser wrappers --------------------------------------------------
def get_team() -> Dict[str, Any]:
    p = paths.team_json()
    return _cached("team", _mtime(p), lambda: team.parse(p))


def get_backlog(today: Optional[_dt.date] = None) -> Dict[str, Any]:
    p = paths.backlog_md()
    # include `today` in the signature so days-remaining recompute on a day roll
    sig = (_mtime(p), (today or _dt.date.today()).isoformat())
    return _cached("backlog", sig, lambda: backlog.parse(p, today=today))


def get_log(date_str: Optional[str] = None) -> Dict[str, Any]:
    ds = date_str or _dt.date.today().isoformat()
    p = paths.log_for(ds)
    sig = (ds, _mtime(p))
    # parse() itself handles the missing-today fallback to newest log.
    return _cached(f"log:{ds}", sig, lambda: log.parse(ds))


def get_comms() -> Dict[str, Any]:
    # comms freshness is mtime-derived and time-relative; recompute each call
    # (cheap: a directory glob), so the age stays accurate.
    return comms.parse()


# --- assembly helpers --------------------------------------------------------
def _dev_view_for_status(dev: Dict[str, Any], folder: str) -> Dict[str, Any]:
    d = devlog.parse(folder, dev["id"])
    last_entry = d.get("last_entry") or {}
    return {
        "id": dev["id"],
        "role": dev.get("role"),
        # team.json identity fields the UI renders per dev row.
        "pair": dev.get("pair"),
        "branch": dev.get("branch"),
        "folder": dev.get("folder"),
        # Per-dev colour is not separately tracked in the artifacts; default to
        # the org tint unless the dev file later carries one. Phase 1: inherit.
        "health": None,
        "current_task": d.get("current_task"),
        # next_step + the date of the entry it came from (parsed from the 'next'
        # half of the last dated entry in <folder>/.standup/<id>.md).
        "next_step": d.get("next_step"),
        "last_entry": last_entry or None,
        "last_entry_date": last_entry.get("date"),
        "stale": not d.get("_ok", True),
    }


def _squad_health(devs, org_color: Optional[str]) -> Optional[str]:
    # Phase 1 has no per-squad colour in the artifacts; squads inherit org tint.
    return org_color


def build_status(now: Optional[_dt.datetime] = None) -> Dict[str, Any]:
    now = now or _dt.datetime.now()
    today = now.date()

    t = get_team()
    b = get_backlog(today=today)
    daily = get_log(today.isoformat())
    cm = get_comms()

    header = b.get("header", {})
    latest_tick = daily.get("latest") or {}

    # --- org health: prefer the BACKLOG header, fall back to the latest tick ---
    org_color = header.get("color") or latest_tick.get("color") or "yellow"
    counts_src = header.get("counts") or {}
    tick_counts = latest_tick.get("counts") or {}
    org_counts = {
        "red": _first_num(header.get("counts", {}).get("red"), tick_counts.get("red")),
        "yellow": _first_num(counts_src.get("yellow"), tick_counts.get("yellow")),
        "reported": _first_num(counts_src.get("reported"), tick_counts.get("reported")),
        "worked": _first_num(header.get("worked"), latest_tick.get("worked")),
        "green": _first_num(header.get("green"), latest_tick.get("green")),
        "committed": _first_num(header.get("committed"), latest_tick.get("committed")),
        "prs": _first_num(header.get("prs"), latest_tick.get("prs")),
    }

    # --- runner liveness ---
    backlog_dt = _dt.datetime(today.year, today.month, today.day) if header.get("raw") else None
    log_dt = log.newest_tick_datetime(daily)
    live = liveness.assess(
        now=now,
        backlog_updated_at=backlog_dt,
        newest_log_tick_at=log_dt,
    )

    last_tick = {
        "id": latest_tick.get("run_id") or live.get("last_run_id"),
        "name": latest_tick.get("name"),
        "at": _tick_at(daily.get("date"), latest_tick.get("time")),
    }
    # Single-flight signals the UI reads to lock the action buttons: `busy` (the
    # runner's own in-flight signal), `dual_runner` (split-brain — hard block),
    # and `in_flight` (an action THIS portal launched that is still pending /
    # running). These three together drive the Phase 2 guard on the frontend.
    mine = actions.in_flight()
    # The DAEMON scheduler is the REAL source of the next-tick countdown now (the
    # liveness fallback inferred it from artifact mtimes). When the scheduler is
    # enabled in THIS process we read its in-process schedule + live running record;
    # `sched.running` is a fire that is in flight right now (the live tick).
    sched = scheduler.status(now=now)
    # `enabled` reflects whether the recurring standup WILL FIRE: the loop must be
    # running (started with STANDUP_SCHEDULER=1) AND the runtime schedule config
    # (control/schedule.json, set by /daily-standup + /stop-daily-standup) must be
    # enabled. So /stop reads calm/on-demand here without a process restart.
    _sstate = scheduler.schedule_state()
    runner = {
        "state": live.get("state"),
        "last_tick": last_tick,
        # Real next tick from the scheduler; fall back to the liveness estimate if
        # the scheduler view is somehow empty (defensive — it never is).
        "next_tick": sched.get("next_tick") or live.get("next_tick"),
        "scheduler": {
            "enabled": bool(_SCHED_ENABLED and _sstate["enabled"]),
            "loop_running": _SCHED_ENABLED,
            "interval_hours": _sstate.get("interval_hours"),
            "running": sched.get("running"),
        },
        "heartbeat_age_s": live.get("heartbeat_age_s"),
        # `busy` is true if the runner heartbeat says so OR a scheduled fire is live.
        "busy": bool(live.get("busy")) or sched.get("running") is not None,
        "dual_runner": bool(live.get("dual_runner")),
        "in_flight": mine,
        "source": live.get("source"),
    }

    # --- awaiting Kain ---
    awaiting = [
        {
            "title": bl.get("title"),
            "severity": bl.get("severity"),
            "days_remaining": bl.get("days_remaining"),
            "leverage": bl.get("leverage"),
        }
        for bl in b.get("blockers", [])
    ]

    # --- squads + devs ---
    squads = []
    for squad in t.get("squads", []):
        devs = [
            _dev_view_for_status(dev, dev.get("folder") or "")
            for dev in squad.get("devs", [])
            if dev.get("active", True)
        ]
        squads.append(
            {
                "id": squad.get("id"),
                "name": squad.get("name"),
                "health": _squad_health(devs, org_color),
                "devs": devs,
            }
        )

    # --- staff (ALL active staff: comms_triage, pm_agent, design_lead) with full
    #     identity so the UI can render every roster member. Validated against the
    #     REAL team.json (long role strings), never a mock with short labels. ---
    staff = [
        {
            "id": s.get("id"),
            "role": s.get("role"),
            "folder": s.get("folder"),
            "scope": s.get("scope"),
            "note": (s.get("note") or _truncate(s.get("focus"), 160)),
        }
        for s in t.get("staff", [])
        if s.get("active", True)
    ]

    # --- bench (inactive roster members the UI can list) ---
    bench = [
        {
            "id": m.get("id"),
            "role": m.get("role"),
            "folder": m.get("folder"),
        }
        for m in t.get("bench", [])
    ]

    # --- last_tick detailed ---
    last_tick_detail = {
        "id": latest_tick.get("run_id"),
        "name": latest_tick.get("name"),
        "at": _tick_at(daily.get("date"), latest_tick.get("time")),
        "agents": latest_tick.get("agents"),
        "worked": latest_tick.get("worked"),
        "green": latest_tick.get("green"),
        "committed": latest_tick.get("committed"),
        "prs": latest_tick.get("prs"),
        "duration_min": latest_tick.get("duration_min"),
    }

    # --- comms: ONE agent, THREE streams (message/email/meeting). The top-level
    #     last_pull_at/stale_hours/state stay (newest-file freshness, back-compat);
    #     streams[] + signed_in are additive, read from the inbox file CONTENTS. ---
    comms_view = {
        "last_pull_at": cm.get("last_pull_at"),
        "stale_hours": cm.get("stale_hours"),
        "state": cm.get("state"),
        "signed_in": cm.get("signed_in"),
        "streams": cm.get("streams", []),
    }

    # --- landing queue (Phase 1: derived from committed-unpushed mentions in the
    #     BACKLOG header; full git inspection is Phase 2). Best-effort, never the
    #     source of an error. ---
    landing_queue = _landing_queue_from_backlog(header.get("raw"))

    # --- sources / freshness ---
    sources = {
        "team_json_mtime": _iso_mtime(paths.team_json()),
        "backlog_mtime": _iso_mtime(paths.backlog_md()),
        "log_mtime": _iso_mtime(Path(daily.get("_path")) if daily.get("_path") else None),
    }

    # collect any degradation flags so the UI can show a "last-known" banner.
    # NOTE: `warnings` is the FULL list (incl. the benign fallback notice) and is
    # emitted verbatim in the payload — the UI still wants to SEE that a fallback
    # happened. What it MUST NOT do is flip the red `degraded` banner; see below.
    daily_warnings = list(daily.get("_parse_warnings", []))
    warnings = []
    warnings += b.get("_parse_warnings", [])
    warnings += daily_warnings
    warnings += t.get("_parse_warnings", [])
    warnings += cm.get("_parse_warnings", [])

    # A pre-first-tick fallback to yesterday's log is BENIGN, not degradation:
    # every morning the runner hasn't written today's file yet, so parse() falls
    # back to the newest prior log and (a) sets _fell_back=True and (b) appends a
    # single fallback notice to the daily warnings. Neither should cry wolf.
    #
    # `degraded` is therefore reserved for TRUE parse failures — an unreadable
    # file, no tick sections found, or any non-fallback parser warning. The
    # fallback itself is communicated to the UI ONLY via fell_back/shown_log_date
    # (both still emitted below) and via the unfiltered `warnings` list.
    #
    # We exclude the fallback notice BY SOURCE, not by substring-matching log.py's
    # exact wording, to avoid app.py<->log.py text coupling. The source is the
    # parser's control flow: when _fell_back is set, parsers/log.py appends the
    # fallback notice as the FIRST daily warning, BEFORE it reads/parses the file —
    # so any genuine prior-day failure ("log unreadable", "no tick sections found
    # in log") is a LATER entry. We therefore drop only that first daily warning
    # when fell_back is True; every other warning (incl. real prior-day failures)
    # still flips degraded, so degradation is narrowed, never disabled.
    fell_back = bool(daily.get("_fell_back"))
    degrading_daily = daily_warnings[1:] if fell_back and daily_warnings else daily_warnings
    real_warnings = (
        b.get("_parse_warnings", [])
        + list(degrading_daily)
        + t.get("_parse_warnings", [])
        + cm.get("_parse_warnings", [])
    )
    degraded = bool(real_warnings)

    return {
        "degraded": degraded,
        "fell_back": fell_back,
        "shown_log_date": daily.get("date"),
        "org": {"health": org_color, "counts": org_counts},
        "runner": runner,
        "awaiting_kain": awaiting,
        "squads": squads,
        "staff": staff,
        "bench": bench,
        "last_tick": last_tick_detail,
        "comms": comms_view,
        "landing_queue": landing_queue,
        "updated_at": now.astimezone().isoformat(timespec="seconds"),
        "sources": sources,
        "warnings": warnings,
    }


# --- small utilities ---------------------------------------------------------
def _first_num(*vals):
    for v in vals:
        if v is not None:
            return v
    return None


def _truncate(s: Optional[str], n: int) -> Optional[str]:
    if not s:
        return None
    return s if len(s) <= n else s[: n - 1] + "…"


def _iso_mtime(path: Optional[Path]):
    if not path:
        return None
    mt = _mtime(path)
    if mt is None:
        return None
    return _dt.datetime.fromtimestamp(mt).astimezone().isoformat(timespec="seconds")


def _tick_at(date_str: Optional[str], time_str: Optional[str]) -> Optional[str]:
    if not date_str or not time_str:
        return None
    try:
        d = _dt.date.fromisoformat(date_str)
        hh, mm = time_str.split(":")
        return _dt.datetime(d.year, d.month, d.day, int(hh), int(mm)).astimezone().isoformat(timespec="minutes")
    except (ValueError, TypeError):
        return None


def _landing_queue_from_backlog(raw: Optional[str]):
    """Best-effort: surface committed-unpushed branch/commit hashes mentioned in
    the BACKLOG header as a provisional landing queue. Phase 2 replaces this with
    real git inspection."""
    import re

    if not raw:
        return []
    out = []
    # e.g. "committed-unpushed work (`907c4f0`, `02da0ce5`)"
    for m in re.finditer(r"`([0-9a-f]{7,40})`", raw):
        out.append({"branch": None, "commit": m.group(1), "status": "committed-unpushed"})
        if len(out) >= 8:
            break
    return out


# --- routes ------------------------------------------------------------------
@app.get("/api/status")
def api_status():
    try:
        return JSONResponse(build_status())
    except Exception as exc:  # never hang / 500-bare: degrade with a flag
        return JSONResponse(
            {
                "org": {"health": "red", "counts": {}},
                "degraded": True,
                "error": f"status assembly failed: {exc}",
                "updated_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            },
            status_code=200,
        )


@app.get("/api/team")
def api_team():
    return JSONResponse(get_team())


@app.get("/api/log")
def api_log(date: Optional[str] = Query(None, description="YYYY-MM-DD; default today")):
    return JSONResponse(get_log(date))


def _assess_live(now: Optional[_dt.datetime] = None) -> Dict[str, Any]:
    """Current runner-liveness assessment (heartbeat-primary, schedule-fallback).
    Shared by /api/heartbeat and the action guard so they agree on busy/next_tick."""
    now = now or _dt.datetime.now()
    today = now.date()
    daily = get_log(today.isoformat())
    b = get_backlog(today=today)
    backlog_dt = _dt.datetime(today.year, today.month, today.day) if b.get("header", {}).get("raw") else None
    return liveness.assess(
        now=now,
        backlog_updated_at=backlog_dt,
        newest_log_tick_at=log.newest_tick_datetime(daily),
    )


@app.get("/api/heartbeat")
def api_heartbeat():
    live = _assess_live()
    # mirror the same single-flight signals the status route surfaces so a UI that
    # polls /api/heartbeat (5s) can lock the action buttons without /api/status.
    live = dict(live)
    live["in_flight"] = actions.in_flight()
    return JSONResponse(live)


# ── Phase 2 ACTIONS (mutating) ────────────────────────────────────────────────
# POST /api/actions/run-standup  — request an off-cadence standup tick.
# POST /api/actions/pm-review     — request a PM review pass.
#   202 {queued:true, id, action}   on launch
#   409 {queued:false, code, reason} when the single-flight guard blocks (a run
#       is in flight / runner busy / a tick is imminent / dual-runner detected).
# GET  /api/actions/{id}          — poll lifecycle: pending → running → done|failed.
# GET  /api/actions/guard         — current launch-safety (UI pre-checks before confirm).
def _launch(kind: str, req_id: Optional[str] = None):
    live = _assess_live()
    res = actions.launch(kind, live, req_id=req_id)
    if res.get("ok"):
        a = res["action"]
        # A re-POST of the same request id is a no-op (idempotent) — still 202 so
        # the client retry path is simple; `idempotent:true` tells it nothing new
        # was queued.
        return JSONResponse(
            {"queued": True, "idempotent": bool(res.get("idempotent")),
             "id": a["id"], "action": a},
            status_code=202,
        )
    return JSONResponse(
        {"queued": False, "code": res.get("code"), "reason": res.get("reason"),
         "detail": res.get("detail")},
        status_code=409,
    )


@app.post("/api/actions/run-standup")
def api_action_run_standup(
    request: Request,
    x_idempotency_key: Optional[str] = Header(None),
    body: Optional[Dict[str, Any]] = Body(None),
):
    if not _csrf_ok(request):
        return _csrf_block()
    return _launch("run-standup", req_id=_idem_key(x_idempotency_key, body))


@app.post("/api/actions/pm-review")
def api_action_pm_review(
    request: Request,
    x_idempotency_key: Optional[str] = Header(None),
    body: Optional[Dict[str, Any]] = Body(None),
):
    if not _csrf_ok(request):
        return _csrf_block()
    return _launch("pm-review", req_id=_idem_key(x_idempotency_key, body))


def _idem_key(header: Optional[str], body: Optional[Dict[str, Any]]) -> Optional[str]:
    """Optional idempotency key: header X-Idempotency-Key wins, else body
    {"request_id": "..."}. When set, a re-POST of the same key is a no-op."""
    if header:
        return header.strip() or None
    if isinstance(body, dict) and body.get("request_id"):
        return str(body["request_id"]).strip() or None
    return None


@app.get("/api/actions")
def api_actions_list():
    """List recent actions (newest-first) + the current in-flight run, so the UI
    can render the action history and lock the buttons."""
    return JSONResponse(
        {"in_flight": actions.in_flight(), "actions": actions.list_actions()}
    )


@app.get("/api/actions/guard")
def api_actions_guard():
    """Is a launch safe RIGHT NOW? The UI calls this BEFORE opening the confirm
    dialog so it can BLOCK (instead of confirm) when a double-fire would result."""
    g = actions.guard(_assess_live())
    return JSONResponse(g)


@app.get("/api/actions/{action_id}")
def api_action_get(action_id: str):
    a = actions.get(action_id)
    if a is None:
        return JSONResponse({"error": "no such action", "id": action_id}, status_code=404)
    return JSONResponse(a)


@app.get("/api/runs")
def api_runs(limit: int = Query(50, ge=1, le=200, description="max records, newest-first")):
    """Tick fire HISTORY — one record per scheduled (or portal-triggered) fire,
    newest-first, from control/runs/. Each: {run_id, tick, source, started_at,
    finished_at, status, worked, green, committed, prs, log_ref, note}. Feeds
    Mission Control's run timeline. `next_tick` is the REAL next fire from the
    in-process scheduler; `running` is the live tick if one is in flight."""
    s = scheduler.status()
    return JSONResponse(
        {
            "runs": runs.list_runs(limit=limit),
            "next_tick": s.get("next_tick"),
            "running": s.get("running"),
            "scheduler_enabled": _SCHED_ENABLED,
        }
    )


@app.get("/api/native-teams")
def api_native_teams():
    """Bridge (3) — OBSERVE Claude Code NATIVE agent teams. Reads ~/.claude/teams/*
    (members) + ~/.claude/tasks/* (the shared task list) that Claude Code writes when a
    native team runs, so Mission Control can show live teams + their tasks next to the
    job queue. Read-only + best-effort (the native on-disk format is experimental).
    Empty `teams` just means no native team is currently running on this machine."""
    return JSONResponse(native_teams.summary())


@app.get("/api/blockers")
def api_blockers():
    b = get_backlog()
    return JSONResponse(
        {
            "blockers": b.get("blockers", []),
            "sections": b.get("sections", {}),
            "warnings": b.get("_parse_warnings", []),
        }
    )


@app.get("/healthz")
def healthz():
    """Portal self-check + scheduler-loop LIVENESS, so a dead loop under a live
    uvicorn ("looks alive, fires nothing") is detectable. `loop_alive` is the loop's
    own flag AND the supervisor task being live; `last_beat_age_s` is how long since
    the loop took a step (a large age means it is wedged/dead even if uvicorn
    answers). `scheduler_task_alive`
    is whether the supervisor task is still running. When the scheduler is disabled
    in THIS process these are reported as not-applicable, not as a failure."""
    body: Dict[str, Any] = {
        "ok": True, "service": "team-status-portal", "phase": 3,
        "scheduler": _SCHED_ENABLED,
        "jobworker": _JOBWORKER_ENABLED,
    }
    if _JOBWORKER_ENABLED:
        jls = jobworker.loop_state()
        jtask = getattr(app.state, "jobworker_task", None)
        jtask_alive = bool(jtask is not None and not jtask.done())
        jloop_alive = bool(jls.get("alive")) and jtask_alive
        body["jobworker_loop"] = {
            "loop_alive": jloop_alive,
            "worker_task_alive": jtask_alive,
            "last_beat_age_s": jls.get("last_beat_age_s"),
            "last_beat_at": jls.get("last_beat_at"),
            "claimed": jls.get("claimed"),
            "completed": jls.get("completed"),
            "running": jls.get("running"),
            "reconciled": jls.get("reconciled"),
            "restarts": jls.get("restarts"),
            "started_at": jls.get("started_at"),
        }
        if not jloop_alive:
            body["jobworker_loop"]["warning"] = (
                "job worker loop is NOT alive while the app is up — no board jobs "
                "will run until it restarts (supervisor should revive it)"
            )
    if _SCHED_ENABLED:
        ls = scheduler.loop_state()
        task = getattr(app.state, "scheduler_task", None)
        task_alive = bool(task is not None and not task.done())
        age = ls.get("last_beat_age_s")
        # A loop that hasn't beaten in a long time is wedged even if `alive` is True.
        # The boundary sleeps can be hours, so only flag clearly-dead: task gone, or
        # the loop flag is down. (Age is surfaced raw for an operator/alert to judge.)
        loop_alive = bool(ls.get("alive")) and task_alive
        body["scheduler_loop"] = {
            "loop_alive": loop_alive,
            "scheduler_task_alive": task_alive,
            "last_beat_age_s": age,
            "last_beat_at": ls.get("last_beat_at"),
            "last_fire_at": ls.get("last_fire_at"),
            "last_fire_tick": ls.get("last_fire_tick"),
            "fires": ls.get("fires"),
            "restarts": ls.get("restarts"),
            "started_at": ls.get("started_at"),
        }
        # `ok` stays True (the portal itself is healthy); a dead loop is surfaced via
        # loop_alive=False so a monitor can alert without the HTTP check 500-ing.
        if not loop_alive:
            body["scheduler_loop"]["warning"] = (
                "scheduler loop is NOT alive while the app is up — no ticks will fire "
                "until it restarts (supervisor should revive it; if not, restart the portal)"
            )
    return body


# --- static frontend (served last so /api/* wins) ----------------------------
_STATIC_DIR = Path(__file__).resolve().parent / "static"
if _STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")
