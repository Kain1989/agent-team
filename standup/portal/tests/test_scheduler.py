"""DAEMON scheduler tests — the Phase-3 in-process tick scheduler.

Every test runs against an ISOLATED temp STANDUP_ROOT (paths.py honors the env
var) so we NEVER touch the real control/ dir, and NEVER spawn a real `claude`
(the launcher is injected as a fake). The contract under test:

  * the schedule math (next_fire) matches the 4-tick table and wraps the day for
    the 02:27 NIGHT tick;
  * a fire RECORDS a run into control/runs/<run_id>.json with the right lifecycle
    (running -> done | failed) + the launcher's worked/green/committed/prs;
  * a fire REUSES control/run.lock for single-flight — it acquires the SAME lock
    drain.py/the crons take, and a fire whose lock is already held is SKIPPED
    (recorded status="skipped"), NOT double-fired;
  * after a successful fire the lock is RELEASED (a 2nd fire can then run);
  * GET /api/runs surfaces the history + the real next_tick;
  * /api/status.runner.next_tick comes from the scheduler;
  * the asyncio run_loop fires at a boundary and stops cleanly.
"""

import asyncio
import datetime
import importlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_STANDUP_ROOT = Path(__file__).resolve().parents[2]
_RUN_LOCK_PY = _STANDUP_ROOT / "control" / "run_lock.py"


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """Throwaway STANDUP_ROOT with an empty control dir + a copy of the REAL
    run_lock.py (so the scheduler exercises the actual lock, not a stub). Reloads
    the parser chain so STANDUP_ROOT binds everywhere. Yields
    (scheduler, runs, paths, control_dir)."""
    root = tmp_path
    (root / "control").mkdir()
    (root / "control" / "requests").mkdir()
    (root / "control" / "results").mkdir()
    (root / "log").mkdir()
    # Drop the REAL run_lock.py into the isolated control dir so paths.run_lock_module()
    # prefers it and the lock file lives under this throwaway root.
    (root / "control" / "run_lock.py").write_text(
        _RUN_LOCK_PY.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (root / "team.json").write_text(json.dumps(
        {"squads": [], "staff": [], "bench": []}), encoding="utf-8")
    (root / "BACKLOG.md").write_text(
        "Last updated: 2026-06-20 (08:00 MORNING tick `wf_x` — clean)\n", encoding="utf-8")

    monkeypatch.setenv("STANDUP_ROOT", str(root))
    import parsers.paths as paths
    importlib.reload(paths)
    import parsers.liveness as liveness
    importlib.reload(liveness)
    import parsers.runs as runs
    importlib.reload(runs)
    import parsers.scheduler as scheduler
    importlib.reload(scheduler)
    yield scheduler, runs, paths, (root / "control")


def _ok_launcher(worked=14, green=11, committed=3, prs=2, exit_code=0):
    """A fake launcher standing in for the headless `claude -p` tick. Records that
    it ran so a test can assert single-flight (it must NOT run for a skipped fire)."""
    calls = []

    def launcher(name, run_id):
        calls.append((name, run_id))
        return {"exit_code": exit_code, "log_ref": f"log/{name}.md",
                "worked": worked, "green": green, "committed": committed, "prs": prs}

    launcher.calls = calls
    return launcher


# --- schedule math -----------------------------------------------------------
def test_next_fire_matches_tick_table_and_wraps_day(isolated):
    scheduler, *_ = isolated
    cases = {
        "2026-06-22T07:30": ("MORNING", "2026-06-22T08:00:00"),
        "2026-06-22T09:50": ("AFTERNOON", "2026-06-22T14:07:00"),
        "2026-06-22T14:07": ("EVENING", "2026-06-22T20:17:00"),
        "2026-06-22T20:17": ("NIGHT", "2026-06-23T02:27:00"),
        "2026-06-22T23:59": ("NIGHT", "2026-06-23T02:27:00"),
        "2026-06-22T02:00": ("NIGHT", "2026-06-22T02:27:00"),
    }
    for ts, (exp_name, exp_at) in cases.items():
        now = datetime.datetime.fromisoformat(ts)
        name, when = scheduler.next_fire(now)
        assert name == exp_name, ts
        assert when.isoformat() == exp_at, ts


# --- fire records a run ------------------------------------------------------
def test_fire_records_done_run_with_metrics(isolated):
    scheduler, runs, paths, control = isolated
    launcher = _ok_launcher(worked=9, green=7, committed=1, prs=0)
    rec = scheduler.fire("AFTERNOON", run_id="r-done", launcher=launcher)

    assert rec["status"] == "done"
    assert rec["tick"] == "AFTERNOON"
    assert rec["source"] == "scheduler"
    assert rec["exit_code"] == 0
    assert (rec["worked"], rec["green"], rec["committed"], rec["prs"]) == (9, 7, 1, 0)
    assert rec["started_at"] and rec["finished_at"]
    assert launcher.calls == [("AFTERNOON", "r-done")]

    # persisted to control/runs/<run_id>.json
    on_disk = json.loads((control / "runs" / "r-done.json").read_text())
    assert on_disk["status"] == "done" and on_disk["run_id"] == "r-done"
    # and surfaced by the history reader
    hist = runs.list_runs()
    assert [r["run_id"] for r in hist] == ["r-done"]


def test_fire_records_failed_on_nonzero_exit(isolated):
    scheduler, runs, *_ = isolated
    rec = scheduler.fire("EVENING", run_id="r-fail",
                         launcher=_ok_launcher(exit_code=2))
    assert rec["status"] == "failed"
    assert rec["exit_code"] == 2
    assert "exited 2" in (rec["note"] or "")


# --- single-flight: the fire REUSES control/run.lock -------------------------
def test_fire_acquires_then_releases_the_shared_run_lock(isolated):
    scheduler, runs, paths, control = isolated
    import importlib.util
    spec = importlib.util.spec_from_file_location("rl", str(paths.run_lock_module()))
    rl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rl)

    # While the launcher runs, the lock must be HELD (single-flight). We assert
    # that from inside the fake launcher.
    seen = {}

    def launcher(name, run_id):
        seen["held_during"] = rl.read_holder(path=paths.run_lock()).get("held")
        seen["holder"] = rl.read_holder(path=paths.run_lock()).get("holder")
        return {"exit_code": 0, "log_ref": "log/x.md"}

    rec = scheduler.fire("MORNING", run_id="r-lock", launcher=launcher)
    assert rec["status"] == "done"
    assert seen["held_during"] is True, "run.lock must be held while the tick runs"
    assert seen["holder"] == "portal-scheduler"
    # After the fire the lock is RELEASED — a 2nd fire can take it.
    assert rl.read_holder(path=paths.run_lock()).get("held") is False
    rec2 = scheduler.fire("MORNING", run_id="r-lock-2", launcher=_ok_launcher())
    assert rec2["status"] == "done"


def test_fire_is_skipped_when_lock_already_held(isolated):
    scheduler, runs, paths, control = isolated
    import importlib.util
    spec = importlib.util.spec_from_file_location("rl", str(paths.run_lock_module()))
    rl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rl)

    # Simulate a portal action (or an overlapping tick) ALREADY holding the lock.
    held = rl.RunLock(kind="run-standup", run_id="other-tick",
                      holder="drain.py", control_dir=control)
    assert held.acquire() is True
    try:
        launcher = _ok_launcher()
        rec = scheduler.fire("NIGHT", run_id="r-skip", launcher=launcher)
        # The scheduled fire must NOT launch — it is SKIPPED, not double-fired.
        assert rec["status"] == "skipped"
        assert launcher.calls == [], "launcher must NOT run when the lock is held"
        assert rec["lock"]["acquired"] is False
        assert rec["lock"]["held_run_id"] == "other-tick"
        assert "double-fire" in (rec["note"] or "")
    finally:
        held.release()


def test_fire_releases_lock_even_if_launcher_raises(isolated):
    scheduler, runs, paths, control = isolated
    import importlib.util
    spec = importlib.util.spec_from_file_location("rl", str(paths.run_lock_module()))
    rl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rl)

    def boom(name, run_id):
        raise RuntimeError("launcher blew up")

    rec = scheduler.fire("AFTERNOON", run_id="r-boom", launcher=boom)
    assert rec["status"] == "error"
    # Crash must not wedge the lock — the next fire can acquire it.
    assert rl.read_holder(path=paths.run_lock()).get("held") is False
    rec2 = scheduler.fire("AFTERNOON", run_id="r-after-boom", launcher=_ok_launcher())
    assert rec2["status"] == "done"


# --- the asyncio loop fires at a boundary and stops cleanly ------------------
def test_run_loop_fires_then_stops(isolated, monkeypatch):
    scheduler, runs, paths, control = isolated
    # Firing is gated on control/schedule.json (default OFF) — enable it so the loop fires.
    (control / "schedule.json").write_text(json.dumps({"enabled": True}))
    fired = []

    # Make the next boundary essentially "now" so the loop fires immediately, and
    # stub fire() so we don't spawn a real tick.
    monkeypatch.setattr(scheduler, "next_fire",
                        lambda now=None: ("MORNING", datetime.datetime.now()))

    def fake_fire(name, source="scheduler"):
        fired.append((name, source))
        # stop after the first fire so the test is bounded
        stop_event.set()
        return {"status": "done"}

    monkeypatch.setattr(scheduler, "fire", fake_fire)

    async def drive():
        global stop_event
        stop_event = asyncio.Event()
        await asyncio.wait_for(scheduler.run_loop(stop_event), timeout=5)

    asyncio.run(drive())
    assert fired and fired[0][0] == "MORNING"


# --- the supervisor restarts a loop that dies (looks-alive-fires-nothing fix) -
def test_supervisor_restarts_a_loop_that_raises(isolated, monkeypatch):
    scheduler, *_ = isolated
    # First run_loop call raises (a crash); second call honors stop and returns —
    # so the supervisor must have RESTARTED after the crash. No real backoff wait.
    monkeypatch.setattr(scheduler, "SUPERVISOR_BACKOFF_S", 0.0)
    monkeypatch.setattr(scheduler, "SUPERVISOR_BACKOFF_MAX_S", 0.0)
    calls = {"n": 0}

    async def flaky_loop(stop):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("loop blew up")
        stop.set()  # 2nd run: shut down cleanly so the supervisor exits.
        return

    monkeypatch.setattr(scheduler, "run_loop", flaky_loop)

    async def drive():
        stop = asyncio.Event()
        await asyncio.wait_for(scheduler.supervise(stop), timeout=5)

    asyncio.run(drive())
    assert calls["n"] == 2, "supervisor must restart run_loop after it crashed"
    assert scheduler._LOOP_STATE.get("restarts", 0) >= 1


def test_supervisor_restarts_a_loop_that_returns_early(isolated, monkeypatch):
    scheduler, *_ = isolated
    monkeypatch.setattr(scheduler, "SUPERVISOR_BACKOFF_S", 0.0)
    monkeypatch.setattr(scheduler, "SUPERVISOR_BACKOFF_MAX_S", 0.0)
    calls = {"n": 0}

    async def early_return_loop(stop):
        # run_loop returns WITHOUT stop set (the silent-death case) the first time.
        calls["n"] += 1
        if calls["n"] >= 2:
            stop.set()
        return

    monkeypatch.setattr(scheduler, "run_loop", early_return_loop)

    async def drive():
        stop = asyncio.Event()
        await asyncio.wait_for(scheduler.supervise(stop), timeout=5)

    asyncio.run(drive())
    assert calls["n"] == 2, "supervisor must restart a loop that returned with stop unset"


def test_supervisor_stops_cleanly_when_loop_returns_on_stop(isolated, monkeypatch):
    scheduler, *_ = isolated
    calls = {"n": 0}

    async def clean_loop(stop):
        calls["n"] += 1
        stop.set()  # clean shutdown — supervisor must NOT restart.
        return

    monkeypatch.setattr(scheduler, "run_loop", clean_loop)

    async def drive():
        stop = asyncio.Event()
        await asyncio.wait_for(scheduler.supervise(stop), timeout=5)

    asyncio.run(drive())
    assert calls["n"] == 1, "a clean (stop-set) return must NOT be restarted"


def test_run_loop_stamps_liveness_then_healthz_surfaces_it(isolated, monkeypatch):
    scheduler, _runs, _paths, control = isolated
    # Firing is gated on control/schedule.json (default OFF) — enable it so the loop fires.
    (control / "schedule.json").write_text(json.dumps({"enabled": True}))
    monkeypatch.setattr(scheduler, "next_fire",
                        lambda now=None: ("MORNING", datetime.datetime.now()))

    def fake_fire(name, source="scheduler"):
        stop_event.set()
        return {"status": "done"}

    monkeypatch.setattr(scheduler, "fire", fake_fire)

    async def drive():
        global stop_event
        stop_event = asyncio.Event()
        await asyncio.wait_for(scheduler.run_loop(stop_event), timeout=5)

    asyncio.run(drive())
    ls = scheduler.loop_state()
    assert ls["fires"] >= 1 and ls["last_fire_tick"] == "MORNING"
    assert ls["last_beat_at"] is not None
    assert isinstance(ls["last_beat_age_s"], int)

    # /healthz surfaces loop liveness when the scheduler is enabled in the app.
    monkeypatch.setenv("STANDUP_SCHEDULER", "1")
    import app as app_module
    importlib.reload(app_module)
    client = TestClient(app_module.app)
    hz = client.get("/healthz").json()
    assert hz["scheduler"] is True
    assert "scheduler_loop" in hz
    sl = hz["scheduler_loop"]
    assert set(["loop_alive", "scheduler_task_alive", "last_beat_age_s",
                "fires", "restarts"]).issubset(sl.keys())


# --- the REAL Workflow-tool prompt (NOT the prose-improv placeholder) ---------
def test_tick_prompt_launches_the_real_workflow_not_inline_improv(isolated):
    scheduler, *_ = isolated
    args = scheduler._tick_args("MORNING", "2026-06-22")
    prompt = scheduler._tick_prompt("MORNING", "wf_test", "2026-06-22", args)
    # It must invoke the REAL workflow script via the Workflow tool in BACKGROUND,
    # and must NOT tell claude to improvise/run the agents inline. (The MVP's
    # workflow file is `standup.workflow.js`; the parent's was daily_standup.*.)
    assert "standup.workflow.js" in prompt
    assert "Workflow(" in prompt and "background: true" in prompt
    assert "team.json" in prompt and "roster" in prompt
    assert "WAIT for that background workflow to COMPLETE" in prompt
    # the failed placeholder language must be GONE
    assert "INLINE with the Task tool" not in prompt
    assert "Do NOT use any background/Workflow tool" not in prompt


def test_tick_args_per_tick_and_hard_safe(isolated):
    scheduler, *_ = isolated
    m = scheduler._tick_args("MORNING", "2026-06-22")
    a = scheduler._tick_args("AFTERNOON", "2026-06-22")
    # MORNING is the full pass; the others are continue-work.
    assert m.get("planning") is True and m.get("design") is True
    assert "planning" not in a and "design" not in a
    # pr/merge HARD false on every scheduled fire; maxTasks bounded.
    for x in (m, a):
        assert x["pr"] is False and x["merge"] is False
        assert x["maxTasks"] == 2
        assert "deploy" not in x and "promote" not in x


def test_parse_claude_result_scrapes_workflow_run_id(isolated):
    scheduler, *_ = isolated
    import json as _j
    stdout = _j.dumps({"result": "Done — workflow wf_abc1234-99 completed; log written.",
                       "num_turns": 12, "duration_ms": 600000})
    parsed = scheduler._parse_claude_result(stdout)
    assert parsed["workflow_run_id"] == "wf_abc1234-99"
    # tolerant of garbage
    assert scheduler._parse_claude_result("not json") == {}


# --- API surface -------------------------------------------------------------
def test_api_runs_and_status_use_the_scheduler(isolated, monkeypatch):
    scheduler, runs, paths, control = isolated
    # Record a couple of fires.
    scheduler.fire("MORNING", run_id="h1", launcher=_ok_launcher(worked=5, green=5))
    scheduler.fire("AFTERNOON", run_id="h2", launcher=_ok_launcher(worked=8, green=6))

    import app as app_module
    importlib.reload(app_module)
    client = TestClient(app_module.app)

    r = client.get("/api/runs")
    assert r.status_code == 200
    body = r.json()
    ids = [x["run_id"] for x in body["runs"]]
    assert set(ids) == {"h1", "h2"}
    assert body["next_tick"]["name"] in {"MORNING", "AFTERNOON", "EVENING", "NIGHT"}
    assert "in_seconds" in body["next_tick"]

    s = client.get("/api/status").json()
    nt = s["runner"]["next_tick"]
    assert nt and nt.get("name") in {"MORNING", "AFTERNOON", "EVENING", "NIGHT"}
    # scheduler view is exposed on the runner block
    assert "scheduler" in s["runner"]


def test_run_loop_paused_does_not_fire_when_schedule_disabled(isolated, monkeypatch):
    """New behavior: with firing DISABLED (no STANDUP_SCHEDULER, no schedule.json),
    the loop polls and NEVER fires — the runtime pause /stop-daily-standup relies on."""
    scheduler, *_ = isolated
    monkeypatch.delenv("STANDUP_SCHEDULER", raising=False)
    monkeypatch.setattr(scheduler, "SCHEDULE_POLL_S", 0.05)  # fast poll for the test
    monkeypatch.setattr(scheduler, "next_fire",
                        lambda now=None: ("MORNING", datetime.datetime.now()))
    fired = []
    monkeypatch.setattr(scheduler, "fire",
                        lambda name, source="scheduler": fired.append(name))

    async def drive():
        stop = asyncio.Event()
        task = asyncio.create_task(scheduler.run_loop(stop))
        await asyncio.sleep(0.2)   # several poll cycles
        stop.set()
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(drive())
    assert fired == [], "a disabled schedule must never fire"


def test_next_interval_fire_aligns_to_midnight_slots(isolated):
    scheduler, *_ = isolated
    # interval 5h -> slots 00:00/05:00/10:00/15:00/20:00/(next-day 01:00)
    _, w1 = scheduler._next_interval_fire(datetime.datetime(2026, 6, 26, 13, 30), 5)
    assert (w1.hour, w1.minute) == (15, 0)
    _, w2 = scheduler._next_interval_fire(datetime.datetime(2026, 6, 26, 20, 1), 5)
    assert w2.day == 27 and w2.hour == 1   # 25h slot after 20:00 wraps to next day 01:00
    # interval 2h at 09:10 -> next slot 10:00
    _, w3 = scheduler._next_interval_fire(datetime.datetime(2026, 6, 26, 9, 10), 2)
    assert (w3.hour, w3.minute) == (10, 0)
