"""API smoke tests via FastAPI TestClient — assert the /api/status shape (the
contract portal_frontend builds against) and the other read endpoints."""

import datetime
import json

import pytest
from fastapi.testclient import TestClient

import app as A

client = TestClient(A.app)


# Top-level keys the contract guarantees.
CONTRACT_KEYS = {
    "org",
    "runner",
    "awaiting_kain",
    "squads",
    "staff",
    "bench",
    "last_tick",
    "comms",
    "landing_queue",
    "updated_at",
    "sources",
    "degraded",
}


def test_status_returns_200_and_full_shape():
    r = client.get("/api/status")
    assert r.status_code == 200
    j = r.json()
    assert CONTRACT_KEYS <= set(j.keys()), f"missing keys: {CONTRACT_KEYS - set(j.keys())}"


def test_status_org_block_shape():
    j = client.get("/api/status").json()
    org = j["org"]
    assert org["health"] in {"green", "yellow", "red"}
    counts = org["counts"]
    for k in ("red", "yellow", "reported", "worked", "green", "committed", "prs"):
        assert k in counts


def test_status_runner_block_shape():
    j = client.get("/api/status").json()
    runner = j["runner"]
    assert runner["state"] in {"alive", "stale", "dead"}
    assert set(runner["next_tick"].keys()) == {"name", "at", "in_seconds"}
    assert set(runner["last_tick"].keys()) == {"id", "name", "at"}
    assert "heartbeat_age_s" in runner


# Canonical severity contract: the backend emits P-style severities; the
# frontend maps them to labels/colors on the UI. Locking the VALUE here so the
# front<->back contract can't drift silently again (the review found no test
# asserted the severity value). Keep in sync with README (~line 74).
CANONICAL_SEVERITIES = {"P0", "P1", "P2", "P3"}


def test_status_awaiting_kain_severity_is_canonical():
    """Every blocker severity MUST be a canonical P-style value {P0,P1,P2,P3}.

    This is the contract portal_frontend maps against; asserting the VALUE (not
    just the key) is what keeps the front<->back severity contract from drifting.
    """
    j = client.get("/api/status").json()
    for item in j["awaiting_kain"]:
        assert (
            item["severity"] in CANONICAL_SEVERITIES
        ), f"non-canonical severity {item['severity']!r} in {item['title']!r}"


def test_status_squads_and_devs_shape(populated_root):
    j = client.get("/api/status").json()
    assert isinstance(j["squads"], list) and j["squads"]
    sq = j["squads"][0]
    assert {"id", "name", "health", "devs"} <= set(sq.keys())
    if sq["devs"]:
        dev = sq["devs"][0]
        assert {"id", "role", "health", "current_task"} <= set(dev.keys())


def test_status_last_tick_detail_shape():
    j = client.get("/api/status").json()
    lt = j["last_tick"]
    assert {"id", "name", "at", "agents", "worked", "green", "committed", "prs", "duration_min"} <= set(lt.keys())


def test_status_comms_block_shape():
    j = client.get("/api/status").json()
    comms = j["comms"]
    assert {"last_pull_at", "stale_hours", "state"} <= set(comms.keys())


# --------------------------------------------------------------------------- #
# Comms = ONE agent, THREE streams (message/email/meeting). The MVP keeps
# comms_triage INACTIVE and ships NO messages/inbox/, so /api/status emits an
# empty streams list here; the stream-content + staleness engine is exercised
# against synthetic inboxes in tests/test_parsers.py instead. (The parent's
# 3-stream/real-count assertions had no MVP analog and were removed.)
# --------------------------------------------------------------------------- #
def test_status_surfaces_degraded_flag():
    j = client.get("/api/status").json()
    # `degraded` must always be present so the UI can show a last-known banner.
    assert "degraded" in j
    assert isinstance(j["degraded"], bool)


def test_status_devs_carry_id_and_pair(populated_root):
    """Every dev object in every squad must carry its handle (`id`) AND its
    lanemate (`pair`) so the UI can render the pairing."""
    j = client.get("/api/status").json()
    seen_any = False
    for sq in j["squads"]:
        for dev in sq["devs"]:
            seen_any = True
            assert dev.get("id"), f"dev missing id in squad {sq['id']}"
            assert "pair" in dev, f"dev {dev.get('id')} missing pair key"
            assert "branch" in dev and "next_step" in dev and "last_entry_date" in dev
        # within a squad, the pairs cross-reference each other
        ids = {d["id"] for d in sq["devs"]}
        for d in sq["devs"]:
            if d.get("pair"):
                assert d["pair"] in ids, f"{d['id']}.pair {d['pair']} not in squad {sq['id']}"
    assert seen_any, "no devs found in any squad"


def test_status_carries_all_staff_and_bench():
    """ALL active staff must be in the payload so the UI can render every roster
    member. Validated against the REAL (MVP) team.json: the active staff are the
    Steve-Jobs pm_agent and the Apple-HIG design_lead (comms_triage is inactive in
    the MVP, so it is correctly filtered out of the status spine). The MVP bench is
    intentionally empty (the parent's bench cast was dropped), so we assert the
    bench is present-and-a-list rather than non-empty."""
    j = client.get("/api/status").json()
    staff_ids = {s["id"] for s in j["staff"]}
    assert {"pm_agent", "design_lead"} <= staff_ids, f"missing staff: {staff_ids}"
    for s in j["staff"]:
        assert {"id", "role", "folder", "scope", "note"} <= set(s.keys())

    assert "bench" in j and isinstance(j["bench"], list)
    for m in j["bench"]:
        assert {"id", "role", "folder"} <= set(m.keys())


def test_status_sources_block_shape():
    j = client.get("/api/status").json()
    src = j["sources"]
    assert {"team_json_mtime", "backlog_mtime", "log_mtime"} <= set(src.keys())


# --------------------------------------------------------------------------- #
# Other endpoints
# --------------------------------------------------------------------------- #
def test_team_endpoint(populated_root):
    j = client.get("/api/team").json()
    assert {s["id"] for s in j["squads"]} >= {"portal"}


def test_log_endpoint_default_and_dated():
    # The MVP's standup/log/ is empty on a fresh install, so the endpoint returns
    # an empty tick list rather than the parent's seeded ticks. We assert the
    # endpoint answers with the contract shape (a `ticks` list) for both the
    # default and a dated request; the tick PARSING engine is covered against
    # synthetic log files in tests/test_parsers.py.
    j = client.get("/api/log").json()
    assert "ticks" in j and isinstance(j["ticks"], list)
    j2 = client.get("/api/log", params={"date": "2026-06-19"}).json()
    assert "ticks" in j2 and isinstance(j2["ticks"], list)


def test_heartbeat_endpoint():
    j = client.get("/api/heartbeat").json()
    assert j["state"] in {"alive", "stale", "dead"}
    assert "next_tick" in j


def test_blockers_endpoint():
    j = client.get("/api/blockers").json()
    assert "blockers" in j
    assert "sections" in j


def test_healthz():
    j = client.get("/healthz").json()
    assert j["ok"] is True


def test_status_never_500s_even_if_assembly_breaks(monkeypatch):
    # Force build_status to raise; the route must still return 200 + degraded.
    def boom(*a, **k):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(A, "build_status", boom)
    r = client.get("/api/status")
    assert r.status_code == 200
    assert r.json()["degraded"] is True


# --------------------------------------------------------------------------- #
# tz-awareness of the freshness spine (P0: every ts/at field tz-aware).
#
# The HEADLINE contract: every timestamp STRING /api/status emits must carry a
# UTC offset. This must be tested against REAL drain-written started_at /
# finished_at / requested_at — not just an empty queue — because the dominant
# producer of runner.in_flight.* is control/drain.py (via actions._view echoing
# the result files VERBATIM). An empty-queue test would pass falsely.
# --------------------------------------------------------------------------- #
import importlib
import shutil
import sys
import tempfile
from pathlib import Path

from parsers import paths as _paths

# Field-name suffixes that denote a contract timestamp in the /api/status tree.
_TS_KEYS = ("_at", "_mtime")
_TS_EXACT = ("ts", "at")


def _walk_timestamps(obj, path=""):
    """Yield (json_path, value) for every contract ts/at/_at/_mtime STRING."""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            np = f"{path}.{k}"
            if isinstance(v, str) and (k.endswith(_TS_KEYS) or k in _TS_EXACT):
                out.append((np, v))
            out += _walk_timestamps(v, np)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out += _walk_timestamps(v, f"{path}[{i}]")
    return out


def _assert_all_tz_aware(j, label):
    found = _walk_timestamps(j)
    assert found, f"{label}: no timestamp fields found — walk is broken"
    naive = []
    for p, v in found:
        try:
            dt = datetime.datetime.fromisoformat(v)
        except ValueError:
            naive.append((p, v, "UNPARSEABLE"))
            continue
        if dt.tzinfo is None:
            naive.append((p, v, "naive"))
    assert not naive, f"{label}: naive/unparseable timestamps: {naive}"
    return found


def _load_drain():
    """Import control/drain.py (the dominant producer of the spine fields)."""
    root = _paths.standup_root()
    spec = importlib.util.spec_from_file_location(
        "drain_under_test", str(root / "control" / "drain.py")
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _seed_clean_backlog(dest):
    """Write a minimal, warning-FREE BACKLOG.md: a `Last updated:` header the
    backlog parser can read plus a `BLOCKERS FOR KAIN` list so parse_blockers
    returns a non-empty list. This keeps build_status' `degraded` flag driven only
    by the LOG path under test (the MVP's real seed BACKLOG has neither, which
    would otherwise add a benign parse warning and flip degraded)."""
    dest.write_text(
        "# Team Backlog\n\n"
        "Last updated: 2026-06-22 14:07 · run `wf_fixture` · 1 agents · "
        "GREEN (0 red / 0 yellow / 1 reported) · "
        "**0 worked / 0 green / 0 committed / 0 PRs**\n\n"
        "### \U0001f534 BLOCKERS FOR KAIN (gated)\n"
        "1. **EM MERGE GATE** — adopt the canonical writer.\n",
        encoding="utf-8",
    )


def _seed_clean_inbox(inbox):
    """Create a messages/inbox with the teams_/outlook_ files the comms parser
    expects, so comms emits zero parse warnings (the MVP ships no inbox because
    comms_triage is inactive, which would otherwise flip degraded)."""
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "teams_2026-06-22.json").write_text(
        json.dumps({"signed_in": True, "activity": [1], "chats": [1]})
    )
    (inbox / "outlook_2026-06-22.json").write_text(
        json.dumps({"signed_in": True, "mail": [1], "calendar": [1]})
    )


@pytest.fixture
def isolated_status(monkeypatch):
    """Redirect STANDUP_ROOT to a tmp copy of the real artifacts, then hand back
    a TestClient + helpers so a test can seed real drain-written request/result
    files BEFORE calling GET /api/status."""
    real_root = _paths.standup_root()
    tmp = Path(tempfile.mkdtemp())
    root = tmp / "STANDUP"
    root.mkdir()
    # The shipped roster has `teams: []` by design, and an empty roster makes /api/status report
    # degraded — which is correct behaviour and useless for a timestamp test. Copy it, then give it
    # a squad so the endpoint has something to serve.
    _t = json.loads((real_root / "team.json").read_text(encoding="utf-8"))
    _t["teams"] = [{"id": "portal", "name": "Team Portal Squad",
                    "review_surface": {"kind": "web", "label": "Mission Control",
                                       "inspect": "bash standup/control/inspect_portal.sh"},
                    "developers": [
                        {"id": "portal_backend", "folder": "standup/portal", "active": True,
                         "git": True, "pair": "portal_frontend", "role": "Backend"},
                        {"id": "portal_frontend", "folder": "standup/portal", "active": True,
                         "git": True, "pair": "portal_backend", "role": "Frontend"}]}]
    (root / "team.json").write_text(json.dumps(_t, indent=2), encoding="utf-8")
    for d in ("log", "control"):
        s = real_root / d
        if s.exists():
            shutil.copytree(s, root / d, dirs_exist_ok=True)
    # Seed a CLEAN BACKLOG.md + messages/inbox so the backlog and comms parsers
    # emit zero warnings. The MVP's real seed BACKLOG.md has no blocker list and
    # there is no messages/inbox/ (comms_triage is inactive), which would make
    # build_status report degraded=True for benign reasons — masking the genuine
    # parse-failure / fallback signals these tests pin. (Fixture-only data; the
    # blocker + comms PARSING engines are covered in tests/test_parsers.py.)
    _seed_clean_backlog(root / "BACKLOG.md")
    _seed_clean_inbox(tmp / "messages" / "inbox")
    # make sure no real run.lock / heartbeat busy leaks in
    (root / "control").mkdir(parents=True, exist_ok=True)
    # Seed TODAY's log so parsers/log.py::parse() finds it and never falls back.
    # The filename MUST be computed with datetime.date.today().isoformat() — the
    # SAME stdlib call parsers/log.py:142 uses to request the log — so the seed
    # always matches what the parser asks for, regardless of the calendar day.
    # That removes the date fragility permanently (was: newest log on disk was
    # yesterday's, parse() fell back, _fell_back=True flipped degraded=True even
    # with error=None). Body uses the real verified tick shape (header + NIGHT
    # heading + **Run:** line) so parse_text detects a tick and emits no warning.
    today_iso = datetime.date.today().isoformat()
    log_dir = root / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{today_iso}.md").write_text(
        f"# Standup — {today_iso}\n"
        "\n"
        "## NIGHT (00:00 fixture)\n"
        "\n"
        "**Run:** workflow `wf_0` · 1 agents · fixture\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("STANDUP_ROOT", str(root))

    import app as _A
    importlib.reload(_A)
    iso_client = TestClient(_A.app)
    yield {"root": root, "client": iso_client}
    shutil.rmtree(tmp, ignore_errors=True)


def test_status_all_timestamps_are_tz_aware_with_real_drain_files(isolated_status):
    """HEADLINE: seed a REAL drain-written running action (started_at/requested_at
    via the actual control/drain.py producer), then assert EVERY ts/at field in
    /api/status — including runner.in_flight.started_at — is tz-aware."""
    root = isolated_status["root"]
    client_iso = isolated_status["client"]
    drain = _load_drain()
    # point drain's module-level dirs at the isolated control dir
    ctrl = root / "control"
    reqs = ctrl / "requests"
    results = ctrl / "results"
    reqs.mkdir(parents=True, exist_ok=True)
    results.mkdir(parents=True, exist_ok=True)
    drain.CONTROL_DIR = ctrl
    drain.REQUESTS = reqs
    drain.RESULTS = results

    aid = "act_tzaware_test"
    now = drain._now()
    # requested_at + started_at written by the REAL drain producer (drain._iso)
    drain._atomic_write_json(reqs / f"{aid}.json", {
        "id": aid, "action": "run-standup", "workflow": "wf",
        "args": {}, "requested_at": drain._iso(now),
        "requested_by": "portal", "status": "pending",
    })
    drain._atomic_write_json(results / f"{aid}.json", {
        "id": aid, "action": "run-standup", "status": "running",
        "run_id": "wf_test_run", "started_at": drain._iso(now),
        "finished_at": None, "note": "drain-written running (test)",
    })

    j = client_iso.get("/api/status").json()
    assert j.get("degraded") is not True, f"status degraded: {j.get('error')}"
    # Pin that degraded=False is because the SEEDED today's log was used, not a
    # masked fallback: a future unrelated degraded-flip cannot hide a
    # re-introduced date fallback. (app.py:339-340 emits both fields.)
    assert j.get("fell_back") is False, f"unexpected fallback: {j.get('fell_back')}"
    assert j.get("shown_log_date") == datetime.date.today().isoformat(), \
        f"status showing wrong log day: {j.get('shown_log_date')}"
    # the seeded running action must actually surface in the spine
    inflight = j["runner"]["in_flight"]
    assert inflight is not None and inflight.get("started_at"), \
        f"seeded running action did not surface in runner.in_flight: {inflight}"
    assert inflight["state"] == "running"

    found = _assert_all_tz_aware(j, "/api/status (with real drain files)")
    # explicit belt-and-suspenders: the drain-produced started_at is in the walk
    assert any(p.endswith("started_at") for p, _ in found), \
        f"started_at missing from walked fields: {[p for p, _ in found]}"
    assert datetime.datetime.fromisoformat(inflight["started_at"]).tzinfo is not None


def test_status_in_seconds_still_int_and_consistent(isolated_status):
    """The authoritative integer countdown survives the tz-aware emit unchanged."""
    j = isolated_status["client"].get("/api/status").json()
    nt = j["runner"]["next_tick"]
    # in_seconds stays a plain int (countdown math), regardless of the aware emit.
    # (sign depends on whether the copied heartbeat carries a past/future boundary;
    # the contract here is "still an int + consistent with the aware `at`", which is
    # asserted byte-for-byte in test_liveness's pinned 67*60 case.)
    assert isinstance(nt["in_seconds"], int)
    # next_tick.at is aware but in_seconds is plain int math
    assert datetime.datetime.fromisoformat(nt["at"]).tzinfo is not None
    # the at + in_seconds agree: rebuilding in_seconds from the aware `at` (after
    # the readers' aware->naive normalisation) matches the emitted integer.
    at_dt = datetime.datetime.fromisoformat(nt["at"]).astimezone().replace(tzinfo=None)
    recomputed = int((at_dt - datetime.datetime.now()).total_seconds())
    assert abs(recomputed - nt["in_seconds"]) <= 5


def test_actions_endpoint_timestamps_tz_aware(isolated_status):
    """GET /api/actions also echoes drain-written started_at/finished_at — walk it."""
    root = isolated_status["root"]
    client_iso = isolated_status["client"]
    drain = _load_drain()
    ctrl = root / "control"
    reqs = ctrl / "requests"
    results = ctrl / "results"
    reqs.mkdir(parents=True, exist_ok=True)
    results.mkdir(parents=True, exist_ok=True)
    drain.CONTROL_DIR, drain.REQUESTS, drain.RESULTS = ctrl, reqs, results
    aid = "act_done_test"
    now = drain._now()
    drain._atomic_write_json(reqs / f"{aid}.json", {
        "id": aid, "action": "run-standup", "args": {},
        "requested_at": drain._iso(now), "requested_by": "portal", "status": "pending",
    })
    drain._atomic_write_json(results / f"{aid}.json", {
        "id": aid, "action": "run-standup", "status": "done", "run_id": "wf_x",
        "started_at": drain._iso(now), "finished_at": drain._iso(now), "note": "done",
    })
    j = client_iso.get("/api/actions").json()
    _assert_all_tz_aware(j, "/api/actions (with real drain files)")


# --------------------------------------------------------------------------- #
# drain.py producer test: the DOMINANT real-world writer of started_at /
# finished_at must emit tz-aware ISO. Drive the real _sweep_expired transition
# and assert the written finished_at parses tz-aware.
# --------------------------------------------------------------------------- #
def test_drain_iso_is_tz_aware():
    drain = _load_drain()
    s = drain._iso()
    assert datetime.datetime.fromisoformat(s).tzinfo is not None, f"drain._iso naive: {s!r}"
    # idempotent / correct on an explicit naive arg too
    s2 = drain._iso(datetime.datetime(2026, 6, 21, 12, 0, 0))
    assert datetime.datetime.fromisoformat(s2).tzinfo is not None


def test_drain_sweep_expired_writes_tz_aware_finished_at(tmp_path):
    """_sweep_expired (drain's real TTL sweep) writes finished_at into a result
    file — assert that producer stamps a tz-aware string."""
    drain = _load_drain()
    ctrl = tmp_path / "control"
    reqs = ctrl / "requests"
    results = ctrl / "results"
    reqs.mkdir(parents=True)
    results.mkdir(parents=True)
    drain.CONTROL_DIR, drain.REQUESTS, drain.RESULTS = ctrl, reqs, results
    now = datetime.datetime(2026, 6, 21, 12, 0, 0)
    # an expired pending (requested well over PENDING_TTL_S ago)
    old = (now - datetime.timedelta(seconds=drain.PENDING_TTL_S + 600)).isoformat()
    drain._atomic_write_json(reqs / "exp1.json", {
        "id": "exp1", "action": "run-standup", "requested_at": old, "status": "pending",
    })
    swept = drain._sweep_expired(now=now)
    assert "exp1" in swept
    res = json.loads((results / "exp1.json").read_text())
    assert res["status"] == "expired"
    assert datetime.datetime.fromisoformat(res["finished_at"]).tzinfo is not None, \
        f"drain _sweep_expired finished_at naive: {res['finished_at']!r}"


def test_drain_runner_completion_heredoc_emits_tz_aware_finished_at():
    """The runner-completion snippet drain.py PRINTS (a generated PY heredoc the
    runner executes) is the dominant production writer of finished_at. Extract the
    finished_at expression, eval it, and assert it is tz-aware + syntactically valid."""
    # Eval the exact expression the heredoc embeds -> must be tz-aware + valid.
    expr_val = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    assert datetime.datetime.fromisoformat(expr_val).tzinfo is not None
    # Guard the literal in the generated heredoc carries the offset call.
    drain_src = (_paths.standup_root() / "control" / "drain.py").read_text(encoding="utf-8")
    assert "datetime.datetime.now().astimezone().isoformat(timespec='seconds')" in drain_src, \
        "runner-completion heredoc must emit .astimezone() (tz-aware finished_at)"


# --------------------------------------------------------------------------- #
# heartbeat.py stamp() producer: writes ts + next_tick into control/heartbeat.json
# --------------------------------------------------------------------------- #
def test_heartbeat_stamp_writes_tz_aware(tmp_path, monkeypatch):
    # heartbeat.py CODE lives in the real checkout; only its OUTPUT (heartbeat.json)
    # is redirected to tmp via STANDUP_ROOT (re-read per call inside the module).
    code_root = _paths.standup_root()
    spec = importlib.util.spec_from_file_location(
        "heartbeat_under_test", str(code_root / "control" / "heartbeat.py")
    )
    hb_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hb_mod)
    monkeypatch.setenv("STANDUP_ROOT", str(tmp_path))
    hb = hb_mod.stamp(session_id="s1")
    assert datetime.datetime.fromisoformat(hb["ts"]).tzinfo is not None, \
        f"heartbeat ts naive: {hb['ts']!r}"
    assert datetime.datetime.fromisoformat(hb["next_tick"]).tzinfo is not None, \
        f"heartbeat next_tick naive: {hb['next_tick']!r}"
    # next_tick_name unchanged (a plain label, never a timestamp)
    assert hb["next_tick_name"] in {"MORNING", "AFTERNOON", "EVENING", "NIGHT"}


# --------------------------------------------------------------------------- #
# degraded cry-wolf fix (app.py): a pre-first-tick fallback to yesterday's log
# is BENIGN — it must NOT flip the red `degraded` banner — while a GENUINE
# prior-day parse failure still must. See app.py degraded block.
# --------------------------------------------------------------------------- #
_VALID_TICK_BODY = (
    "# Standup — {ds}\n"
    "\n"
    "## NIGHT (00:00 fixture)\n"
    "\n"
    "**Run:** workflow `wf_0` · 1 agents · fixture\n"
)


def _build_isolated_root(monkeypatch, log_files):
    """Build an isolated STANDUP_ROOT mirroring isolated_status' wiring (team.json,
    BACKLOG.md, control copied so build_status assembles) but with a HAND-PICKED set
    of log files instead of the real log dir — so we can omit today's log and force
    parse() down its real fallback path. ``log_files`` maps {date_iso: body_or_None}
    (None => create an empty file). Returns a reloaded TestClient + the root."""
    real_root = _paths.standup_root()
    tmp = Path(tempfile.mkdtemp())
    root = tmp / "STANDUP"
    root.mkdir()
    shutil.copy(real_root / "team.json", root / "team.json")
    csrc = real_root / "control"
    if csrc.exists():
        shutil.copytree(csrc, root / "control", dirs_exist_ok=True)
    (root / "control").mkdir(parents=True, exist_ok=True)
    # Clean BACKLOG.md + messages/inbox so only the LOG path drives degraded (see
    # the isolated_status fixture note). Without these the MVP's blocker-less
    # BACKLOG and absent inbox would flip degraded=True and mask the fallback
    # signal this helper's tests pin.
    _seed_clean_backlog(root / "BACKLOG.md")
    _seed_clean_inbox(tmp / "messages" / "inbox")
    # Fresh log dir with ONLY the files this test wants — no copytree of the real
    # log dir, so a real today-dated log can never leak in and defeat the fallback.
    log_dir = root / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    for ds, body in log_files.items():
        (log_dir / f"{ds}.md").write_text("" if body is None else body, encoding="utf-8")
    monkeypatch.setenv("STANDUP_ROOT", str(root))
    import app as _A
    importlib.reload(_A)
    return {"root": root, "client": TestClient(_A.app), "tmp": tmp}


def test_pre_first_tick_fallback_is_not_degraded(populated_root, monkeypatch):
    """REGRESSION (cry-wolf): today's log absent, only a deterministically-OLDER
    log on disk with a valid tick body. parse() falls back -> fell_back True,
    shown_log_date == that prior date. The fix: degraded MUST be False, while the
    fallback notice STILL appears in warnings (kept for the UI)."""
    today = datetime.date.today()
    yesterday = (today - datetime.timedelta(days=1)).isoformat()
    iso = _build_isolated_root(
        monkeypatch, {yesterday: _VALID_TICK_BODY.format(ds=yesterday)}
    )
    try:
        j = iso["client"].get("/api/status").json()
        assert j.get("fell_back") is True, f"expected fallback, got {j.get('fell_back')}"
        assert j.get("shown_log_date") == yesterday, \
            f"expected shown_log_date={yesterday}, got {j.get('shown_log_date')}"
        # THE pin: a benign pre-first-tick fallback must not cry wolf.
        assert j.get("degraded") is False, \
            f"benign fallback wrongly flagged degraded (error={j.get('error')})"
        # We kept the fallback notice for the UI — only decoupled it from the banner.
        assert any("fell back to" in w for w in j.get("warnings", [])), \
            f"fallback notice should remain in warnings: {j.get('warnings')}"
    finally:
        shutil.rmtree(iso["tmp"], ignore_errors=True)


def test_genuine_today_parse_failure_still_degrades(monkeypatch):
    """NEGATIVE pin (degradation NARROWED, not disabled): today's log PRESENT but
    with no tick sections -> parser emits "no tick sections found in log". No
    fallback occurs (today's file exists), and degraded MUST still be True."""
    today = datetime.date.today().isoformat()
    iso = _build_isolated_root(
        monkeypatch, {today: "# just a header, no tick sections\n"}
    )
    try:
        j = iso["client"].get("/api/status").json()
        assert j.get("fell_back") is False, f"unexpected fallback: {j.get('fell_back')}"
        assert j.get("shown_log_date") == today
        assert j.get("degraded") is True, \
            "a today's-log parse failure (no tick sections) must still degrade"
        assert any("no tick sections" in w for w in j.get("warnings", [])), \
            f"expected the parse-failure warning: {j.get('warnings')}"
    finally:
        shutil.rmtree(iso["tmp"], ignore_errors=True)


def test_fallback_to_broken_prior_log_still_degrades(monkeypatch):
    """NEGATIVE pin (source-based, not blanket): today's log absent AND the only
    prior log we fall back to is itself broken (empty -> "no tick sections found
    in log"). fell_back is True, but because we drop ONLY the fallback notice (the
    first daily warning) and keep the genuine failure, degraded MUST be True."""
    today = datetime.date.today()
    yesterday = (today - datetime.timedelta(days=1)).isoformat()
    iso = _build_isolated_root(monkeypatch, {yesterday: ""})  # empty == broken
    try:
        j = iso["client"].get("/api/status").json()
        assert j.get("fell_back") is True, f"expected fallback, got {j.get('fell_back')}"
        assert j.get("shown_log_date") == yesterday
        assert j.get("degraded") is True, \
            "fallback to a genuinely-broken prior log must still degrade"
    finally:
        shutil.rmtree(iso["tmp"], ignore_errors=True)
