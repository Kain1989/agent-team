"""Phase 2 ACTION-mechanism tests (the file queue + single-flight guard).

Every test runs against an ISOLATED temp ``STANDUP_ROOT`` (paths.py honors the
env var) so we NEVER touch the real ``control/`` dir. The control dir is the only
thing the web tier writes, so these tests assert the full safety contract:

  * a POST writes a valid pending request file into control/requests/
  * the single-flight guard REFUSES a 2nd action (409) while one is in-flight
  * a stale pending with no live runner is swept to ``expired`` (TTL)
  * control/heartbeat.py output flips /api/status runner to ``alive``
  * control.log appends an audit line per request
  * the lifecycle pending -> running -> done is reflected in /api/actions/<id>
"""

import datetime
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# The runner-side scripts live under STANDUP/control (two levels up from tests/).
_STANDUP_ROOT = Path(__file__).resolve().parents[2]
_HEARTBEAT_PY = _STANDUP_ROOT / "control" / "heartbeat.py"


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """Point the portal at a throwaway STANDUP_ROOT with an empty control dir and
    a minimal team.json/BACKLOG.md, then (re)import the app fresh so its module
    state binds to this root. Yields (app_module, client, control_dir)."""
    root = tmp_path
    (root / "control").mkdir()
    (root / "control" / "requests").mkdir()
    (root / "control" / "results").mkdir()
    (root / "log").mkdir()
    # Minimal artifacts so build_status() doesn't choke (it degrades gracefully,
    # but a BACKLOG header lets last_run_id resolve).
    (root / "team.json").write_text(json.dumps({
        "squads": [{"id": "s1", "name": "Squad 1", "devs": []}],
        "staff": [], "bench": [],
    }), encoding="utf-8")
    (root / "BACKLOG.md").write_text(
        "Last updated: 2026-06-20 (08:00 MORNING tick `wf_test123-abc` — clean)\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("STANDUP_ROOT", str(root))
    # Reload the dependency chain so STANDUP_ROOT takes effect everywhere.
    import parsers.paths as paths
    importlib.reload(paths)
    import parsers.liveness as liveness
    importlib.reload(liveness)
    import parsers.actions as actions
    importlib.reload(actions)
    import app as app_module
    importlib.reload(app_module)

    # --- PIN THE TICK SCHEDULE: "no tick is imminent" is a FACT here, not an assumption.
    #
    # actions.guard() rule (4) refuses a launch when the next SCHEDULED tick is less than
    # IMMINENT_TICK_S (600s) away. That is correct in production — it is what stops a portal
    # launch racing cron into a double-fire — and it reads the real local WALL CLOCK, through
    # liveness.next_tick() over the hardcoded 08:00 / 14:07 / 20:17 / 02:27 table.
    #
    # So every test below that asserts a launch is ACCEPTED was silently conditional on what
    # time of day the suite happened to run. Inside any of those four windows it came back
    # `409 tick_imminent` where the test expected 202, plus the knock-on
    # `FileNotFoundError: control.log` — a SYMPTOM, not a second bug: the blocked POST never
    # reached _append_log, so the file was never created.
    #
    # Measured on this tree with only TZ shifted and not one byte of source changed:
    #     11:00, 16:30                    -> 30 passed
    #     07:55, 13:59, 20:12, 02:22      -> 18 failed, 12 passed
    # The 12 survivors are exactly the tests that assert a launch is BLOCKED. The gate was not
    # flaky; it was stuck closed, for 4 x 600s = 40 minutes a day, in a suite the README tells
    # people to run.
    #
    # The fix pins the ONE input that made this file clock-dependent. Deliberately NOT a frozen
    # global clock: the TTL/expire and stuck-RUNNING watchdog tests build "N seconds ago" from a
    # real now() against real ceilings (PENDING_TTL_S, MAX_TICK_S), and freezing datetime.now()
    # breaks those boundaries. Pin the schedule, leave the clock alone.
    #
    # Two shortcuts were considered and rejected. Relaxing the assertions to "202 or 409" would
    # leave 18 tests unable to tell "launch works" from "launch is completely broken". Making
    # guard()'s clock injectable over HTTP would hand production a supported way to switch
    # double-fire protection off; the now= parameters on guard()/launch() exist for callers
    # inside this process and are reachable from no route.
    #
    # This does NOT blind rule (4) — it now has MORE coverage than the accident gave it:
    #   * test_tick_imminent_blocks_negative_in_seconds injects `live` directly (no clock);
    #   * test_imminent_window_is_exactly_bounded_against_the_real_tick_table composes the REAL
    #     next_tick() with the REAL guard() at T-601/600/599/1s around every entry in
    #     liveness.TICKS;
    #   * test_tick_imminent_blocks_a_real_POST_at_the_http_layer re-pins this same seam to 180s
    #     and requires a 409 through the full app.py path.
    #
    # `at` is None on purpose: what is being pinned is the INTERVAL, the only thing the guard
    # decides on. Inventing a timestamp that contradicted it would be a worse fixture.
    #
    # SCOPE, stated honestly: this pins the SCHEDULE-computed next_tick. liveness.assess() will
    # prefer a `next_tick` ISO string carried in heartbeat.json when one is present, and
    # control/heartbeat.py writes one from the real clock. Today only the tests that run the
    # real heartbeat.py subprocess take that path, and they assert a launch is BLOCKED via an
    # EARLIER rule (the run lock), so none of them can flip. A future test that ran the real
    # heartbeat.py AND asserted a launch is ALLOWED would reopen the dependency through that
    # door — and control/tests/test_clock_independence.sh, which replays this whole file inside
    # all four windows, is what would catch it.
    monkeypatch.setattr(
        liveness, "next_tick",
        lambda now=None: {"name": "AFTERNOON", "at": None, "in_seconds": 6 * 3600},
    )

    client = TestClient(app_module.app)
    yield app_module, client, root / "control"


# --------------------------------------------------------------------------- #
# 1) A POST writes a valid pending request file (the ONLY web-tier write).
# --------------------------------------------------------------------------- #
def test_post_run_standup_writes_a_valid_request(isolated):
    app_module, client, control = isolated
    r = client.post("/api/actions/run-standup")
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["queued"] is True
    aid = body["id"]
    assert aid

    files = list((control / "requests").glob("*.json"))
    assert len(files) == 1, "exactly one request file must be written"
    rec = json.loads(files[0].read_text())
    # the request shape the runner drains
    assert rec["id"] == aid
    assert rec["action"] == "run-standup"
    assert rec["status"] == "pending"
    assert rec["requested_by"] == "portal"
    assert "requested_at" in rec
    assert rec["workflow"] == "standup/standup.workflow.js"
    # SAFE defaults: work/maxTasks set; merge/pr FALSE; deploy/promote never present
    assert rec["args"] == {"work": True, "maxTasks": 2, "pr": False, "merge": False}


def test_pm_review_binds_the_right_workflow(isolated):
    app_module, client, control = isolated
    r = client.post("/api/actions/pm-review")
    assert r.status_code == 202
    rec = json.loads(next((control / "requests").glob("*.json")).read_text())
    assert rec["action"] == "pm-review"
    assert rec["workflow"] == "standup/standup.workflow.js"


# --------------------------------------------------------------------------- #
# 2) Single-flight: a 2nd action is REFUSED (409) while one is in-flight.
# --------------------------------------------------------------------------- #
def test_single_flight_409_when_a_request_is_in_flight(isolated):
    app_module, client, control = isolated
    r1 = client.post("/api/actions/run-standup")
    assert r1.status_code == 202
    # second action while the first is still pending -> 409, NOT a 2nd file
    r2 = client.post("/api/actions/pm-review")
    assert r2.status_code == 409, r2.text
    body = r2.json()
    assert body["queued"] is False
    assert body["code"] == "in_flight"
    # the reason states the CONSEQUENCE (double-fire), not a generic "busy"
    assert "double-fire" in body["reason"].lower()
    assert len(list((control / "requests").glob("*.json"))) == 1


def test_single_flight_409_when_heartbeat_says_busy(isolated):
    app_module, client, control = isolated
    # No queued request, but the heartbeat says the runner itself is busy.
    fresh = datetime.datetime.now().isoformat(timespec="seconds")
    (control / "heartbeat.json").write_text(json.dumps({
        "ts": fresh, "busy": True, "last_run_id": "wf_busy", "session_id": "s",
    }), encoding="utf-8")
    r = client.post("/api/actions/run-standup")
    assert r.status_code == 409
    assert r.json()["code"] == "busy"
    assert len(list((control / "requests").glob("*.json"))) == 0


def test_dual_runner_hard_blocks(isolated):
    app_module, client, control = isolated
    fresh = datetime.datetime.now().isoformat(timespec="seconds")
    (control / "heartbeat.json").write_text(json.dumps({
        "ts": fresh, "busy": False, "dual_runner": True, "session_id": "s",
    }), encoding="utf-8")
    r = client.post("/api/actions/run-standup")
    assert r.status_code == 409
    assert r.json()["code"] == "dual_runner"


# --------------------------------------------------------------------------- #
# 3) TTL: a stale pending with no live runner is swept to `expired`.
# --------------------------------------------------------------------------- #
def test_ttl_sweeps_stale_pending_to_expired(isolated):
    app_module, client, control = isolated
    actions = sys.modules["parsers.actions"]
    # queue one (no heartbeat file => runner not alive)
    r = client.post("/api/actions/run-standup")
    aid = r.json()["id"]
    # rewrite its requested_at to be older than the TTL
    reqf = control / "requests" / f"{aid}.json"
    rec = json.loads(reqf.read_text())
    old = datetime.datetime.now() - datetime.timedelta(seconds=actions.PENDING_TTL_S + 120)
    rec["requested_at"] = old.isoformat(timespec="seconds")
    reqf.write_text(json.dumps(rec), encoding="utf-8")

    swept = actions._sweep_expired()
    assert aid in swept
    res = json.loads((control / "results" / f"{aid}.json").read_text())
    assert res["status"] == "expired"
    # and because it is no longer in-flight, a NEW launch is allowed
    r2 = client.post("/api/actions/pm-review")
    assert r2.status_code == 202


def test_ttl_does_not_expire_while_runner_is_alive(isolated):
    app_module, client, control = isolated
    actions = sys.modules["parsers.actions"]
    r = client.post("/api/actions/run-standup")
    aid = r.json()["id"]
    reqf = control / "requests" / f"{aid}.json"
    rec = json.loads(reqf.read_text())
    old = datetime.datetime.now() - datetime.timedelta(seconds=actions.PENDING_TTL_S + 120)
    rec["requested_at"] = old.isoformat(timespec="seconds")
    reqf.write_text(json.dumps(rec), encoding="utf-8")
    # a FRESH heartbeat => runner alive => the stale pending must NOT be expired
    (control / "heartbeat.json").write_text(json.dumps({
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "busy": True, "session_id": "s",
    }), encoding="utf-8")
    assert actions._sweep_expired() == []
    assert not (control / "results" / f"{aid}.json").exists()


# --------------------------------------------------------------------------- #
# 4) control/heartbeat.py output flips /api/status runner to `alive`.
# --------------------------------------------------------------------------- #
def test_heartbeat_script_flips_status_to_alive(isolated):
    app_module, client, control = isolated
    # before any heartbeat: not alive (fallback inference)
    assert client.get("/api/status").json()["runner"]["state"] != "alive"

    # run the REAL control/heartbeat.py against this ISOLATED root. heartbeat.py now
    # honors STANDUP_ROOT (like parsers/paths.py), so it writes into the isolated
    # control dir — never the live one.
    out = subprocess.run(
        [sys.executable, str(_HEARTBEAT_PY), "--session-id", "pytest"],
        capture_output=True, text=True, cwd=str(_STANDUP_ROOT.parent),
        env={**_env_with_root(control)},
    )
    assert out.returncode == 0, out.stderr
    # The script wrote control/heartbeat.json itself (into THIS isolated dir); no
    # copy hack needed. Assert the file exists and the portal flips to alive.
    hb_file = control / "heartbeat.json"
    assert hb_file.exists(), "heartbeat.py must write into the isolated control dir"
    hb = json.loads(hb_file.read_text(encoding="utf-8"))

    state = client.get("/api/status").json()["runner"]["state"]
    assert state == "alive", f"expected alive after fresh heartbeat, got {state}"
    # the heartbeat carries the next_tick + last_run_id the portal surfaces
    assert hb["next_tick_name"] in {"MORNING", "AFTERNOON", "EVENING", "NIGHT"}


def _env_with_root(control_dir: Path):
    import os
    # heartbeat.py now honors STANDUP_ROOT for every path it writes (heartbeat.json,
    # control.log, run.lock via the reconciler) — mirroring parsers/paths.py. Point
    # it at the isolated root so the real script writes ONLY under tmp_path. The
    # control dir lives at <root>/control, so STANDUP_ROOT is its parent.
    return {**os.environ, "STANDUP_ROOT": str(control_dir.parent)}


def test_heartbeat_subprocess_never_touches_live_control_dir(tmp_path):
    """ISOLATION-BREACH REGRESSION (the Done criterion). Running the REAL
    control/heartbeat.py with STANDUP_ROOT pointed at a throwaway root must write
    ONLY under that root — the LIVE control/ dir (heartbeat.json, control.log,
    run.lock) must be byte-for-byte untouched (no mtime change, no new files). This
    is the assertion that catches any WRITE site that still hardcodes the __file__
    path instead of resolving STANDUP_ROOT."""
    import os
    live = _STANDUP_ROOT / "control"

    # Snapshot the live dir BEFORE: mtime_ns of each tracked file that exists, plus
    # the full directory listing (to catch a NEW file being created).
    tracked = ("heartbeat.json", "control.log", "run.lock")
    before_mtime = {
        name: (live / name).stat().st_mtime_ns
        for name in tracked if (live / name).exists()
    }
    before_listing = set(os.listdir(live)) if live.exists() else set()

    # Pre-create the isolated control dir and run the real cron command against it.
    iso_root = tmp_path
    (iso_root / "control").mkdir(parents=True, exist_ok=True)
    out = subprocess.run(
        [sys.executable, str(_HEARTBEAT_PY), "--session-id", "pytest-iso"],
        capture_output=True, text=True, cwd=str(_STANDUP_ROOT.parent),
        env={**os.environ, "STANDUP_ROOT": str(iso_root)},
    )
    assert out.returncode == 0, out.stderr

    # The write landed in the ISOLATED dir.
    assert (iso_root / "control" / "heartbeat.json").exists(), \
        "heartbeat.py must write into the isolated control dir under STANDUP_ROOT"

    # The LIVE dir is untouched: every snapshotted file's mtime_ns is unchanged...
    for name, mtime in before_mtime.items():
        assert (live / name).stat().st_mtime_ns == mtime, \
            f"live control/{name} mtime changed — heartbeat leaked a write into the live dir"
    # ...and NO new file appeared (e.g. a freshly-created control.log/run.lock).
    after_listing = set(os.listdir(live)) if live.exists() else set()
    new_files = after_listing - before_listing
    # __pycache__ entries can appear from importing run_lock.py — those are CODE
    # caches under the real checkout, not state writes; ignore them.
    new_state = {f for f in new_files if not f.startswith("__pycache__") and not f.startswith(".")}
    assert not new_state, f"heartbeat created new files in the live control dir: {new_state}"
    # Explicit: heartbeat.json must NOT have been created under live if it was absent.
    if "heartbeat.json" not in before_listing:
        assert "heartbeat.json" not in after_listing, \
            "heartbeat.py created live control/heartbeat.json (isolation breach)"


# --------------------------------------------------------------------------- #
# 5) control.log appends an audit line per request.
# --------------------------------------------------------------------------- #
def test_control_log_appends_on_queue(isolated):
    app_module, client, control = isolated
    client.post("/api/actions/run-standup")
    log = (control / "control.log").read_text(encoding="utf-8")
    assert "QUEUE" in log
    assert "action=run-standup" in log
    assert "by=portal" in log


# --------------------------------------------------------------------------- #
# 6) Lifecycle pending -> running -> done reflected in /api/actions/<id>.
# --------------------------------------------------------------------------- #
def test_action_lifecycle_pending_running_done(isolated):
    app_module, client, control = isolated
    r = client.post("/api/actions/run-standup")
    aid = r.json()["id"]

    a = client.get(f"/api/actions/{aid}").json()
    assert a["state"] == "pending"

    # the runner transitions it (single writer of results/)
    now = datetime.datetime.now().isoformat(timespec="seconds")
    (control / "results" / f"{aid}.json").write_text(json.dumps({
        "id": aid, "action": "run-standup", "status": "running", "run_id": "wf_x",
        "started_at": now, "finished_at": None, "note": "phase 1 of 4",
    }), encoding="utf-8")
    a = client.get(f"/api/actions/{aid}").json()
    assert a["state"] == "running"
    assert a["run_id"] == "wf_x"

    (control / "results" / f"{aid}.json").write_text(json.dumps({
        "id": aid, "action": "run-standup", "status": "done", "run_id": "wf_x",
        "started_at": now, "finished_at": now, "note": "complete",
        "result": {"worked": 14, "green": 11},
    }), encoding="utf-8")
    a = client.get(f"/api/actions/{aid}").json()
    assert a["state"] == "done"
    assert a["result"]["worked"] == 14

    # once done it is no longer in-flight: a fresh launch is allowed
    assert client.post("/api/actions/pm-review").status_code == 202


def test_unknown_action_id_404(isolated):
    app_module, client, control = isolated
    assert client.get("/api/actions/nope").status_code == 404


def test_idempotent_repost_same_key_is_noop(isolated):
    app_module, client, control = isolated
    key = "my-fixed-key-001"
    r1 = client.post("/api/actions/run-standup", headers={"X-Idempotency-Key": key})
    assert r1.status_code == 202
    r2 = client.post("/api/actions/run-standup", headers={"X-Idempotency-Key": key})
    assert r2.status_code == 202
    assert r2.json().get("idempotent") is True
    assert r1.json()["id"] == r2.json()["id"]
    # only ONE request file despite two POSTs
    assert len(list((control / "requests").glob("*.json"))) == 1


# --------------------------------------------------------------------------- #
# in-flight is exposed on /api/status + /api/heartbeat for the frontend.
# --------------------------------------------------------------------------- #
def test_status_and_heartbeat_expose_in_flight(isolated):
    app_module, client, control = isolated
    assert client.get("/api/status").json()["runner"]["in_flight"] is None
    r = client.post("/api/actions/run-standup")
    aid = r.json()["id"]
    mine = client.get("/api/status").json()["runner"]["in_flight"]
    assert mine is not None and mine["id"] == aid
    # heartbeat mirrors it so a 5s-poll UI can lock without /api/status
    assert client.get("/api/heartbeat").json()["in_flight"]["id"] == aid


def test_guard_endpoint_reports_safe_then_blocked(isolated):
    app_module, client, control = isolated
    g = client.get("/api/actions/guard").json()
    # with no run in-flight and the next tick far off this should be safe
    assert "ok" in g
    client.post("/api/actions/run-standup")
    g2 = client.get("/api/actions/guard").json()
    assert g2["ok"] is False
    assert g2["code"] == "in_flight"


# =========================================================================== #
# ADVERSARIAL-REVIEW FIXES — each test PROVES one closed double-fire hole.     #
# =========================================================================== #

def _stamp_run_lock(control: Path, *, holder: str, run_id: str,
                    age_s: int = 0, kind: str = "run-standup") -> Path:
    """Simulate a NON-portal path (a scheduled cron tick / drain.py) stamping
    control/run.lock — exactly the body run_lock.py writes on acquire. No flock is
    held (a cron PROMPT can't keep an FD open), so this exercises the stamp signal
    the portal must honor. ``age_s`` ages started_at into the past."""
    started = (datetime.datetime.now() - datetime.timedelta(seconds=age_s)).isoformat(timespec="seconds")
    lock = control / "run.lock"
    lock.write_text(json.dumps({
        "holder": holder, "pid": 999999, "run_id": run_id, "kind": kind,
        "started_at": started,
    }), encoding="utf-8")
    return lock


# --------------------------------------------------------------------------- #
# (a) THE PROVEN DOUBLE-FIRE: a SCHEDULED tick running (run.lock held/stamped by
#     a NON-portal path) BLOCKS a portal launch — even with heartbeat busy:false
#     and the next_tick rolled hours out (the exact live-proven hole).
# --------------------------------------------------------------------------- #
def test_scheduled_tick_lock_blocks_portal_launch_even_when_heartbeat_says_not_busy(isolated):
    app_module, client, control = isolated
    # Heartbeat says busy:FALSE (the scheduled-tick crons never set busy) and is
    # fresh; next_tick is the schedule default (hours out, not imminent).
    (control / "heartbeat.json").write_text(json.dumps({
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "busy": False, "dual_runner": False, "last_run_id": "wf_sched", "session_id": "runner",
    }), encoding="utf-8")
    # A SCHEDULED tick is running: a non-portal path holds the machine run lock.
    _stamp_run_lock(control, holder="cron-MORNING", run_id="wf_sched_morning")

    # The guard MUST block — the machine lock is the authority, not heartbeat.busy.
    g = client.get("/api/actions/guard").json()
    assert g["ok"] is False, "a running scheduled tick (lock held) must block"
    assert g["code"] == "busy"
    assert "double-fire" in g["reason"].lower()
    assert g["detail"]["lock"]["held"] is True

    # And a real POST is REFUSED with 409 — no 2nd request file written.
    r = client.post("/api/actions/run-standup")
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "busy"
    assert len(list((control / "requests").glob("*.json"))) == 0


def test_run_lock_stale_stamp_from_dead_holder_does_not_block(isolated):
    app_module, client, control = isolated
    # A stamp older than the max tick TTL = a dead holder; it must be IGNORED so a
    # crashed runner can't wedge the buttons forever.
    actions = sys.modules["parsers.actions"]
    _stamp_run_lock(control, holder="dead-runner", run_id="wf_dead",
                    age_s=actions.MAX_TICK_S + 600)
    g = client.get("/api/actions/guard").json()
    assert g["ok"] is True, "a stale lock from a dead holder must NOT block"
    r = client.post("/api/actions/run-standup")
    assert r.status_code == 202


# --------------------------------------------------------------------------- #
# (b) tick_imminent blocks NEGATIVE in_seconds (a tick firing / overdue).
# --------------------------------------------------------------------------- #
def test_tick_imminent_blocks_negative_in_seconds(isolated):
    actions = sys.modules["parsers.actions"]
    # A tick that is FIRING / overdue has negative in_seconds — the instant between
    # "cron fired" and "the tick took run.lock". That instant must BLOCK.
    live = {"busy": False, "dual_runner": False,
            "next_tick": {"name": "AFTERNOON", "at": "2026-06-20T14:07:00", "in_seconds": -30}}
    g = actions.guard(live)
    assert g["ok"] is False
    assert g["code"] == "tick_imminent"
    assert g["detail"]["firing"] is True

    # boundary: exactly at IMMINENT_TICK_S is NOT imminent (still safe);
    # one second under it blocks.
    safe = dict(live); safe["next_tick"] = {"name": "X", "at": None, "in_seconds": actions.IMMINENT_TICK_S}
    assert actions.guard(safe)["ok"] is True
    block = dict(live); block["next_tick"] = {"name": "X", "at": None, "in_seconds": actions.IMMINENT_TICK_S - 1}
    assert actions.guard(block)["ok"] is False
    assert actions.guard(block)["code"] == "tick_imminent"


def test_imminent_window_is_exactly_bounded_against_the_real_tick_table():
    """The window is EXACTLY [T-599s, T), for every tick in the real table.

    The test above injects `in_seconds` by hand, so it proves the comparison and nothing
    about the schedule that feeds it. This one composes the two REAL functions —
    liveness.next_tick() over liveness.TICKS, then actions.guard() — with an explicit `now`,
    so it reads no clock while still exercising the arithmetic that made the suite
    time-of-day dependent in the first place.

    Two off-by-ones live in that seam and both are pinned here. The comparison is `<`, so
    in_seconds == IMMINENT_TICK_S is still SAFE; and in_seconds is `int()`-truncated, so
    T-600.9s reports 600 and is allowed while T-599.9s reports 599 and blocks. The window is
    therefore 600 seconds of wall time reported as the integers 599..0 — which is where
    `4 x 600s = 40 minutes a day` comes from.

    Takes no fixture on purpose: nothing here touches STANDUP_ROOT, and `isolated` monkeypatches
    the very function under test.
    """
    from parsers import actions, liveness

    assert actions.IMMINENT_TICK_S == 600, "the expectations below are written against 600s"
    assert liveness.TICKS, "an empty tick table would make every case below vacuous"

    base = datetime.datetime(2026, 6, 20)   # date is irrelevant; TICKS is time-of-day only
    for name, hh, mm in liveness.TICKS:
        tick = base.replace(hour=hh, minute=mm)
        for offset, want_ok in ((601, True), (600, True), (599, False), (1, False)):
            now = tick - datetime.timedelta(seconds=offset)
            nt = liveness.next_tick(now=now)
            assert nt["name"] == name and nt["in_seconds"] == offset, (
                f"{name}: next_tick at T-{offset}s should be this tick, {offset}s out; got {nt}"
            )
            g = actions.guard(
                {"busy": False, "dual_runner": False, "next_tick": nt}, now=now)
            assert g["ok"] is want_ok, (
                f"{name} at T-{offset}s: expected ok={want_ok}, got {g}"
            )
            if not want_ok:
                assert g["code"] == "tick_imminent"
                assert g["detail"]["firing"] is False, "not yet firing — still counting down"


# --------------------------------------------------------------------------- #
# (b2) tick_imminent blocks a REAL POST through the whole HTTP path.
#
# The rule used to be covered ONLY by the direct guard() calls above, with a hand-built
# `live` dict — nothing proved it survives _assess_live -> actions.launch -> 409 body ->
# no request file. It LOOKED covered because for ~40 min a day the real clock enforced it
# by accident on every OTHER test in this file. That accident is what the `isolated`
# fixture now pins away, and removing an accident has to be paid for with a deliberate
# test or the rule quietly loses its HTTP coverage.
# --------------------------------------------------------------------------- #
def test_tick_imminent_blocks_a_real_POST_at_the_http_layer(isolated, monkeypatch):
    """FAR from a tick a POST is accepted (202 — every launch test in this file); NEAR one
    it is refused (409 — here). Same seam, opposite fact, both at the HTTP boundary."""
    app_module, client, control = isolated
    liveness = sys.modules["parsers.liveness"]
    # 180s out: inside IMMINENT_TICK_S. Re-pins the seam the fixture set to 6h, so this test
    # differs from the accepting ones by exactly one input.
    monkeypatch.setattr(
        liveness, "next_tick",
        lambda now=None: {"name": "MORNING", "at": "2026-06-20T08:00:00", "in_seconds": 180},
    )

    r = client.post("/api/actions/run-standup")
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["queued"] is False
    assert body["code"] == "tick_imminent"
    # the operator-facing reason names the tick and states the CONSEQUENCE, not a generic "busy"
    reason = body["reason"].lower()
    assert "morning" in reason and "race" in reason and "double-fire" in reason, reason
    assert body["detail"]["in_seconds"] == 180
    assert body["detail"]["firing"] is False
    # refused BEFORE any write: the queue must be untouched
    assert len(list((control / "requests").glob("*.json"))) == 0

    # the read-only pre-check the UI calls must agree with the POST it gates
    g = client.get("/api/actions/guard").json()
    assert g["ok"] is False
    assert g["code"] == "tick_imminent"


# --------------------------------------------------------------------------- #
# (c) CSRF / Origin: a cross-Origin POST is rejected 403; same-origin passes.
# --------------------------------------------------------------------------- #
def test_cross_origin_post_is_rejected_403(isolated):
    app_module, client, control = isolated
    # A cross-site form POST carries a FOREIGN Origin -> 403, no request written.
    r = client.post("/api/actions/run-standup", headers={"Origin": "http://evil.example.com"})
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "forbidden_origin"
    assert len(list((control / "requests").glob("*.json"))) == 0

    # A foreign Referer is likewise rejected.
    r2 = client.post("/api/actions/pm-review", headers={"Referer": "http://evil.example.com/x"})
    assert r2.status_code == 403
    assert len(list((control / "requests").glob("*.json"))) == 0


def test_same_origin_post_is_allowed(isolated):
    app_module, client, control = isolated
    # A same-origin loopback Origin is allowed (the real browser case).
    r = client.post("/api/actions/run-standup", headers={"Origin": "http://127.0.0.1:8770"})
    assert r.status_code == 202, r.text
    # And the explicit custom-header assertion (which a cross-site form can't set).
    actions = sys.modules["parsers.actions"]
    actions._reset_for_tests()
    r2 = client.post("/api/actions/run-standup", headers={"X-Requested-By": "portal"})
    assert r2.status_code == 202
    # No Origin + no Referer (curl / the runner) is allowed (not a browser vector).
    actions._reset_for_tests()
    r3 = client.post("/api/actions/run-standup")
    assert r3.status_code == 202


def test_foreign_host_header_rejected_by_trustedhost(isolated):
    app_module, client, control = isolated
    # TrustedHostMiddleware rejects a foreign Host (DNS-rebinding defense) with 400.
    r = client.get("/api/status", headers={"Host": "attacker.example.com"})
    assert r.status_code == 400


# --------------------------------------------------------------------------- #
# (d) Stuck-RUNNING watchdog: an orphan `running` (no live lock, old started_at)
#     is reconciled to `failed` and stops wedging launches.
# --------------------------------------------------------------------------- #
def test_stuck_running_orphan_is_reconciled_and_unwedges_launches(isolated):
    app_module, client, control = isolated
    actions = sys.modules["parsers.actions"]
    # Queue + drive to running, then make it a CRASHED runner: a `running` result
    # with an old started_at and NO live run.lock.
    r = client.post("/api/actions/run-standup")
    aid = r.json()["id"]
    old = (datetime.datetime.now() - datetime.timedelta(seconds=actions.MAX_TICK_S + 300)).isoformat(timespec="seconds")
    (control / "results" / f"{aid}.json").write_text(json.dumps({
        "id": aid, "action": "run-standup", "status": "running", "run_id": "wf_crashed",
        "started_at": old, "finished_at": None, "note": "phase 1 of 4",
    }), encoding="utf-8")
    # No run.lock file => no live holder. Before the watchdog this wedges forever.
    assert not (control / "run.lock").exists()

    # in_flight() runs the watchdog: the orphan reconciles to failed.
    mine = actions.in_flight()
    assert mine is None, "a stuck-running orphan with no live lock must be reconciled"
    res = json.loads((control / "results" / f"{aid}.json").read_text())
    assert res["status"] == "failed"
    assert res["reconciled_by"] == "portal_watchdog"

    # The buttons are no longer wedged: a fresh launch is allowed.
    r2 = client.post("/api/actions/run-standup")
    assert r2.status_code == 202


def test_stuck_running_NOT_reconciled_while_lock_is_held(isolated):
    app_module, client, control = isolated
    actions = sys.modules["parsers.actions"]
    r = client.post("/api/actions/run-standup")
    aid = r.json()["id"]
    old = (datetime.datetime.now() - datetime.timedelta(seconds=actions.MAX_TICK_S + 300)).isoformat(timespec="seconds")
    (control / "results" / f"{aid}.json").write_text(json.dumps({
        "id": aid, "action": "run-standup", "status": "running", "run_id": "wf_live",
        "started_at": old, "finished_at": None, "note": "still going",
    }), encoding="utf-8")
    # A FRESH run.lock => a tick really IS running; the watchdog must NOT reconcile
    # even though started_at is old (the result clock and lock clock can differ).
    _stamp_run_lock(control, holder="runner", run_id="wf_live", age_s=60)
    assert actions._sweep_stuck_running() == []
    res = json.loads((control / "results" / f"{aid}.json").read_text())
    assert res["status"] == "running", "must not reconcile a genuinely-running tick"


def test_running_within_ttl_is_not_reconciled(isolated):
    app_module, client, control = isolated
    actions = sys.modules["parsers.actions"]
    r = client.post("/api/actions/run-standup")
    aid = r.json()["id"]
    # started_at only a few minutes ago (well within MAX_TICK_S) — a NORMAL run.
    recent = (datetime.datetime.now() - datetime.timedelta(minutes=5)).isoformat(timespec="seconds")
    (control / "results" / f"{aid}.json").write_text(json.dumps({
        "id": aid, "action": "run-standup", "status": "running", "run_id": "wf_ok",
        "started_at": recent, "finished_at": None, "note": "phase 2 of 4",
    }), encoding="utf-8")
    assert actions._sweep_stuck_running() == []
    mine = actions.in_flight()
    assert mine is not None and mine["state"] == "running"


# =========================================================================== #
# (e) HEARTBEAT RECONCILER BACKSTOP — the machine guard for a scheduled tick    #
#     that runs WITHOUT taking run.lock (a prompt slip dropped the acquire line).#
#     A `tick_active.marker` is the launch's machine signal "a tick is running"; #
#     the 1-min heartbeat reconciler stamps run.lock on the tick's behalf, so a  #
#     lock-free running tick reads as BUSY to the portal within ≤1 min and the   #
#     proven double-fire CANNOT reopen on a single prompt slip.                  #
# =========================================================================== #
import importlib.util as _ilu  # noqa: E402

_RUN_LOCK_PY = _STANDUP_ROOT / "control" / "run_lock.py"


def _load_real_run_lock():
    """Load the REAL control/run_lock.py (the shared lock + marker + reconciler
    impl) so tests drive the exact code the runner runs — pointed at the isolated
    control dir via control_dir=."""
    spec = _ilu.spec_from_file_location("standup_run_lock_test", str(_RUN_LOCK_PY))
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _drop_marker(control: Path, *, run_id: str, age_s: int = 0,
                 kind: str = "scheduled-tick") -> Path:
    """Simulate a scheduled-tick launch dropping control/tick_active.marker at start
    — the machine signal "a tick is RUNNING". ``age_s`` ages started_at into the
    past so a finished/dead tick's leftover marker can be exercised."""
    started = (datetime.datetime.now() - datetime.timedelta(seconds=age_s)).isoformat(timespec="seconds")
    m = control / "tick_active.marker"
    m.write_text(json.dumps({
        "run_id": run_id, "kind": kind, "holder": "cron-MORNING", "started_at": started,
    }), encoding="utf-8")
    return m


def test_backstop_covers_running_tick_with_no_lock(isolated):
    """THE CORE PROOF. A scheduled tick is RUNNING (fresh marker) but its prompt
    dropped the `run_lock.py acquire` line → run.lock is NOT held → the portal reads
    NOT busy and would accept an off-cadence launch (the reopened double-fire). The
    heartbeat reconciler stamps run.lock on the tick's behalf → the portal now reads
    BUSY and REFUSES the launch (409). Machine mechanism, no prompt text relied on."""
    app_module, client, control = isolated
    rl = _load_real_run_lock()

    # Fresh heartbeat, busy:FALSE (scheduled crons never set busy), next_tick hours
    # out — the exact live-proven hole. NO run.lock held.
    (control / "heartbeat.json").write_text(json.dumps({
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "busy": False, "dual_runner": False, "last_run_id": "wf_sched", "session_id": "runner",
    }), encoding="utf-8")
    # The scheduled tick LAUNCHED (dropped the marker) but FAILED to take the lock.
    _drop_marker(control, run_id="wf_sched_morning")
    assert not rl.is_held(path=control / "run.lock"), "precondition: lock-free running tick"

    # BEFORE the backstop: the portal sees not-busy and would ACCEPT a launch — the
    # reopened double-fire hole. (We assert the hole exists so the test proves the
    # backstop is what closes it, not some unrelated guard.)
    assert client.get("/api/actions/guard").json()["ok"] is True, (
        "precondition: a lock-free running tick is the hole — portal reads not-busy"
    )

    # THE BACKSTOP runs (this is what the 1-min heartbeat cron does every minute).
    r = rl.reconcile_unlocked_tick(control_dir=control)
    assert r["reconciled"] is True, r
    assert r["run_id"] == "wf_sched_morning"

    # AFTER the backstop: run.lock is stamped (kind=scheduled-recovered) → the portal
    # reads BUSY and the hole is closed.
    holder = rl.read_holder(path=control / "run.lock")
    assert holder["held"] is True
    assert holder["kind"] == "scheduled-recovered"
    g = client.get("/api/actions/guard").json()
    assert g["ok"] is False, "after the backstop, the portal must read the tick as busy"
    assert g["code"] == "busy"
    assert "double-fire" in g["reason"].lower()

    # And a real off-cadence POST is now REFUSED 409 — no 2nd request file written.
    resp = client.post("/api/actions/run-standup")
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "busy"
    assert len(list((control / "requests").glob("*.json"))) == 0


def test_backstop_runs_via_real_heartbeat_subprocess(isolated):
    """Full integration: the ACTUAL cron command — `python3 control/heartbeat.py` —
    runs the reconciler. A running tick with no lock is covered by invoking the real
    script (not just the library), proving the wiring the runner actually executes."""
    app_module, client, control = isolated
    rl = _load_real_run_lock()
    _drop_marker(control, run_id="wf_hb_subproc")
    assert not rl.is_held(path=control / "run.lock")

    # heartbeat.py now honors STANDUP_ROOT, so point the real cron command at THIS
    # isolated dir. The subprocess itself runs the reconciler against the isolated
    # control dir: it finds the fresh wf_hb_subproc marker with NO lock held and
    # stamps run.lock (kind=scheduled-recovered) on the tick's behalf — proving the
    # exact wiring the runner executes closes the hole end-to-end.
    import os
    out = subprocess.run(
        [sys.executable, str(_HEARTBEAT_PY), "--session-id", "pytest-hb"],
        capture_output=True, text=True, cwd=str(_STANDUP_ROOT.parent),
        env={**os.environ, "STANDUP_ROOT": str(control.parent)},
    )
    assert out.returncode == 0, out.stderr
    hb = json.loads(out.stdout.strip().splitlines()[-1])
    assert "ts" in hb
    # The SUBPROCESS reconciled the isolated tick: main() adds reconciled_unlocked_tick
    # when the backstop fired. Assert it covered our marker.
    assert hb.get("reconciled_unlocked_tick"), hb

    # The subprocess stamped the isolated run.lock; read_holder treats a fresh stamp
    # as held=True even after the subprocess exited and its flock dropped (pid is
    # ignored), so is_held() is True from the cross-process stamp — and the portal
    # guard reads busy from it exactly as in production.
    assert rl.is_held(path=control / "run.lock"), "subprocess must stamp the isolated run.lock"
    assert client.get("/api/actions/guard").json()["code"] == "busy"


def test_backstop_noop_when_lock_already_held(isolated):
    """The healthy scheduled-tick state: the prompt DID take the lock and the marker
    is present. The reconciler must be a NO-OP (it must not double-stamp or disturb a
    genuinely-held lock)."""
    app_module, client, control = isolated
    rl = _load_real_run_lock()
    lock = rl.RunLock(kind="run-standup", run_id="wf_held", control_dir=control)
    assert lock.acquire()
    try:
        _drop_marker(control, run_id="wf_held")
        r = rl.reconcile_unlocked_tick(control_dir=control)
        assert r["reconciled"] is False
        assert "already held" in r["reason"]
        # The original holder/kind is untouched (not overwritten to scheduled-recovered).
        assert rl.read_holder(path=control / "run.lock")["kind"] == "run-standup"
    finally:
        lock.release()


def test_backstop_noop_when_no_running_tick(isolated):
    """No marker at all (idle runner) → nothing to recover → no lock is stamped, so
    the portal stays launch-safe. The backstop must not invent busyness."""
    app_module, client, control = isolated
    rl = _load_real_run_lock()
    assert not (control / "tick_active.marker").exists()
    r = rl.reconcile_unlocked_tick(control_dir=control)
    assert r["reconciled"] is False
    assert not rl.is_held(path=control / "run.lock")
    assert client.get("/api/actions/guard").json()["ok"] is True


def test_backstop_ignores_stale_marker_self_heals(isolated):
    """A leftover marker from a FINISHED/CRASHED tick (started_at older than
    MAX_TICK_S) must NOT be treated as running — the reconciler ignores it, so a
    stale marker can't wedge the portal into permanent busy. Self-healing, exactly
    like the lock's dead-holder ceiling."""
    app_module, client, control = isolated
    rl = _load_real_run_lock()
    _drop_marker(control, run_id="wf_finished", age_s=rl.MAX_TICK_S + 600)
    r = rl.reconcile_unlocked_tick(control_dir=control)
    assert r["reconciled"] is False
    assert "stale" in r["reason"]
    assert not rl.is_held(path=control / "run.lock")
    # Portal stays launch-safe — a stale marker does not block.
    assert client.get("/api/actions/guard").json()["ok"] is True


def test_recovered_lock_ages_out_at_max_tick_s_like_a_real_holder(isolated):
    """The recovered stamp is anchored to the tick's REAL start (the marker's
    started_at), so when the tick eventually exceeds MAX_TICK_S the recovered lock is
    treated as a dead holder and the portal unwedges — the recovered lock must not
    outlive a real one."""
    app_module, client, control = isolated
    rl = _load_real_run_lock()
    # A tick that started just under the ceiling ago, running lock-free.
    _drop_marker(control, run_id="wf_long", age_s=rl.MAX_TICK_S - 30)
    assert rl.reconcile_unlocked_tick(control_dir=control)["reconciled"] is True
    holder = rl.read_holder(path=control / "run.lock")
    assert holder["held"] is True  # still within the ceiling
    # Now age the marker past the ceiling and re-derive the holder age: the stamp's
    # started_at (anchored to the marker) is past MAX_TICK_S → dead holder ignored.
    _drop_marker(control, run_id="wf_long", age_s=rl.MAX_TICK_S + 120)
    # Re-stamp via reconcile so the lock body carries the aged started_at, then read.
    # (reconcile is a no-op now because the lock is still held with the OLD stamp; we
    # assert via a direct read using a future `now` that the ceiling fires.)
    future = datetime.datetime.now() + datetime.timedelta(seconds=200)
    aged = rl.read_holder(path=control / "run.lock", now=future)
    # The first stamp was anchored MAX_TICK_S-30 ago; 200s into the future pushes it
    # past the ceiling → not held.
    assert aged["held"] is False, "recovered lock must age out at MAX_TICK_S like a real one"
