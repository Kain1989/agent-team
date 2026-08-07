"""Slice-1 interactive-board tests: the SQLite job store, the atomic transition
primitive, the orphan reconciler, and the /api/jobs* API (TestClient).

Every test runs against an ISOLATED jobs.db (STANDUP_JOBS_DB) + an isolated
STANDUP_ROOT so we never touch the real control/. The agent subprocess is NOT
invoked — these test the store + lifecycle + API contract. (The live end-to-end
agent run + the read-only gate are proven separately against the running portal.)
"""

import datetime
import importlib
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from parsers import paths as _paths


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    """Point the job store at a throwaway jobs.db + a STANDUP_ROOT with a real-ish
    team.json (so target resolution works), reload db so its thread-local connection
    rebinds, and yield the fresh db module."""
    root = tmp_path / "STANDUP"
    (root / "control").mkdir(parents=True)
    # A minimal team.json with the squads/devs/staff target resolution needs.
    # An INLINE roster, always — never a copy of the shipped one. standup/team.json ships with
    # `teams: []` (a fresh install has no project until /add-project creates one), so copying it
    # would give these target-resolution tests nothing to resolve.
    (root / "team.json").write_text(json.dumps({
        "teams": [{"id": "portal", "name": "Team Portal Squad", "developers": [
            {"id": "portal_backend", "folder": "standup/portal", "active": True,
             "git": True, "role": "Portal Dev — Backend", "pair": "portal_frontend"},
            {"id": "portal_frontend", "folder": "standup/portal", "active": True,
             "git": True, "role": "Portal Dev — Frontend", "pair": "portal_backend"}]}],
        "staff": [{"id": "pm_agent", "folder": "standup", "active": True,
                   "role": "Product Manager"}],
        "bench": [],
    }), encoding="utf-8")

    monkeypatch.setenv("STANDUP_ROOT", str(root))
    monkeypatch.setenv("STANDUP_JOBS_DB", str(root / "control" / "jobs.db"))
    import parsers.paths as paths
    importlib.reload(paths)
    import parsers.db as db
    importlib.reload(db)
    db._close_thread_conn()
    db.init()
    yield db
    db._close_thread_conn()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A TestClient with an isolated jobs.db + STANDUP_ROOT, worker DISABLED (so the
    app spawns nothing). Reloads the dependency chain so every module binds to the
    isolated root."""
    root = tmp_path / "STANDUP"
    (root / "control").mkdir(parents=True)
    (root / "log").mkdir(parents=True)
    # Same reason as the other fixture above: the shipped roster ships empty, so a copy gives the
    # target-resolution tests nothing to resolve. Write the roster this fixture actually needs.
    _t = json.loads((_paths.standup_root() / "team.json").read_text(encoding="utf-8"))
    _t["teams"] = [{"id": "portal", "name": "Team Portal Squad",
        "review_surface": {"kind": "web", "label": "Mission Control",
                           "inspect": "bash standup/control/inspect_portal.sh"},
        "developers": [
        {"id": "portal_backend", "folder": "standup/portal", "active": True, "git": True,
         "role": "Portal Dev — Backend", "pair": "portal_frontend"},
        {"id": "portal_frontend", "folder": "standup/portal", "active": True, "git": True,
         "role": "Portal Dev — Frontend", "pair": "portal_backend"}]}]
    (root / "team.json").write_text(json.dumps(_t, indent=2), encoding="utf-8")
    (root / "BACKLOG.md").write_text(
        "Last updated: 2026-06-20 (08:00 MORNING tick `wf_test` — clean)\n", encoding="utf-8")

    monkeypatch.setenv("STANDUP_ROOT", str(root))
    monkeypatch.setenv("STANDUP_JOBS_DB", str(root / "control" / "jobs.db"))
    monkeypatch.delenv("STANDUP_JOBWORKER", raising=False)
    monkeypatch.delenv("STANDUP_SCHEDULER", raising=False)
    import parsers.paths as paths
    importlib.reload(paths)
    import parsers.db as db
    importlib.reload(db)
    db._close_thread_conn()
    import parsers.team as team
    importlib.reload(team)
    import parsers.job_prompts as jp
    importlib.reload(jp)
    import api_jobs
    importlib.reload(api_jobs)
    import app as app_module
    importlib.reload(app_module)
    c = TestClient(app_module.app)
    yield c
    db._close_thread_conn()


# --------------------------------------------------------------------------- #
# Store + transition primitive
# --------------------------------------------------------------------------- #
def test_create_job_is_queued(isolated_db):
    db = isolated_db
    job = db.create_job(type="send-directive", target_kind="dev",
                        target_id="portal_backend", target_folder="standup/portal",
                        prompt="ship the thing")
    assert job["status"] == "queued"
    assert job["id"].startswith("job_")
    assert job["execution_path"] == "read_only"
    assert job["attempts"] == 0
    assert job["idempotent"] is False
    # round-trips through get()
    got = db.get(job["id"])
    assert got["id"] == job["id"] and got["status"] == "queued"


def test_lifecycle_create_claim_transition_done(isolated_db):
    """The headline lifecycle: create -> claim (queued->running, attempts++) ->
    transition(running->done)."""
    db = isolated_db
    job = db.create_job(type="assign-analysis-task", target_kind="project",
                        target_id="standup/portal", target_folder="standup/portal",
                        prompt="analyse the telemetry path")
    jid = job["id"]

    claimed = db.claim_next()
    assert claimed is not None and claimed["id"] == jid
    assert claimed["status"] == "running"
    assert claimed["attempts"] == 1
    assert claimed["started_at"] is not None

    ok = db.transition(jid, "running", "done",
                       fields={"finished_at": db._now_iso(),
                               "result_json": json.dumps({"ok": True, "summary": "did it"})})
    assert ok is True
    final = db.get(jid)
    assert final["status"] == "done"
    assert final["result"]["summary"] == "did it"
    assert final["finished_at"] is not None


def test_transition_rejects_wrong_from_status(isolated_db):
    """transition is the race guard: it only applies when the CURRENT status equals
    `from`. A wrong `from` returns False and changes nothing (rowcount==0)."""
    db = isolated_db
    job = db.create_job(type="send-directive", target_kind="broadcast",
                        target_id=None, target_folder=None, prompt="hi all")
    jid = job["id"]
    # job is 'queued' — a running->done transition must NOT apply.
    assert db.transition(jid, "running", "done") is False
    assert db.get(jid)["status"] == "queued"
    # the correct claim works...
    assert db.claim_next()["id"] == jid
    # ...and now a second claim of the same job loses (already running).
    assert db.claim(jid) is False


def test_claim_next_is_atomic_single_winner(isolated_db):
    """Two claim attempts on the same queued job: exactly one wins (the WHERE
    status='queued' clause). The loser gets False."""
    db = isolated_db
    job = db.create_job(type="send-directive", target_kind="broadcast",
                        target_id=None, target_folder=None, prompt="x")
    jid = job["id"]
    first = db.claim(jid)
    second = db.claim(jid)
    assert first is True and second is False
    assert db.get(jid)["attempts"] == 1  # only the winner incremented


def test_idempotency_dedup_returns_existing(isolated_db):
    """A duplicate idempotency_key returns the EXISTING job (idempotent=True), never
    a 2nd row."""
    db = isolated_db
    a = db.create_job(type="send-directive", target_kind="broadcast", target_id=None,
                      target_folder=None, prompt="once", idempotency_key="KEY-1")
    b = db.create_job(type="send-directive", target_kind="broadcast", target_id=None,
                      target_folder=None, prompt="twice (ignored)", idempotency_key="KEY-1")
    assert a["idempotent"] is False
    assert b["idempotent"] is True
    assert a["id"] == b["id"]
    assert b["prompt"] == "once"  # the original, not the duplicate's text
    assert len(db.list_jobs()) == 1


def test_list_and_counts_filter(isolated_db):
    db = isolated_db
    j1 = db.create_job(type="send-directive", target_kind="broadcast", target_id=None,
                       target_folder=None, prompt="d1")
    j2 = db.create_job(type="trigger-review", target_kind="project", target_id="standup/portal",
                       target_folder="standup/portal", prompt="r1", review_kind="pm")
    db.claim(j2["id"])
    db.transition(j2["id"], "running", "done")
    # newest-first
    allj = db.list_jobs()
    assert [j["id"] for j in allj][:2] == [j2["id"], j1["id"]]
    # filter by status
    assert {j["id"] for j in db.list_jobs(status="queued")} == {j1["id"]}
    assert {j["id"] for j in db.list_jobs(status="done")} == {j2["id"]}
    # filter by comma set + type
    assert {j["id"] for j in db.list_jobs(status="queued,done")} == {j1["id"], j2["id"]}
    assert {j["id"] for j in db.list_jobs(type="trigger-review")} == {j2["id"]}
    counts = db.counts()
    assert counts.get("queued") == 1 and counts.get("done") == 1


# --------------------------------------------------------------------------- #
# Cancel (intent + queued fast path)
# --------------------------------------------------------------------------- #
def test_request_cancel_sets_intent_on_running(isolated_db):
    db = isolated_db
    job = db.create_job(type="assign-analysis-task", target_kind="project",
                        target_id="standup/portal", target_folder="standup/portal", prompt="t")
    db.claim(job["id"])
    updated = db.request_cancel(job["id"])
    assert updated["cancel_requested"] is True
    assert updated["status"] == "running"  # intent only; worker honors it


def test_request_cancel_noop_on_terminal(isolated_db):
    db = isolated_db
    job = db.create_job(type="send-directive", target_kind="broadcast", target_id=None,
                        target_folder=None, prompt="t")
    db.claim(job["id"])
    db.transition(job["id"], "running", "done")
    updated = db.request_cancel(job["id"])
    assert updated["cancel_requested"] is False  # untouched — already terminal


# --------------------------------------------------------------------------- #
# Orphan reconciler (the NEW sweep — jobs.db, NOT actions._sweep_stuck_running)
# --------------------------------------------------------------------------- #
def test_reconciler_sweeps_stuck_running_to_failed(isolated_db):
    from parsers import jobworker
    db = isolated_db
    job = db.create_job(type="assign-analysis-task", target_kind="project",
                        target_id="standup/portal", target_folder="standup/portal", prompt="t")
    jid = job["id"]
    # claim it, then back-date started_at well past the ceiling.
    db.claim(jid)
    old = (datetime.datetime.now() - datetime.timedelta(hours=2)).astimezone().isoformat(timespec="seconds")
    db._conn().execute("UPDATE jobs SET started_at=? WHERE id=?;", (old, jid))
    db._conn().commit()

    reconciled = jobworker.reconcile_orphans(max_runtime_s=70 * 60)
    assert jid in reconciled
    final = db.get(jid)
    assert final["status"] == "failed"
    assert "orphan reconciler" in (final["error"] or "")
    assert final["result"]["reconciled_by"] == "jobworker_reconciler"


def test_reconciler_leaves_fresh_running_alone(isolated_db):
    from parsers import jobworker
    db = isolated_db
    job = db.create_job(type="assign-analysis-task", target_kind="project",
                        target_id="standup/portal", target_folder="standup/portal", prompt="t")
    db.claim(job["id"])  # started_at = now
    reconciled = jobworker.reconcile_orphans(max_runtime_s=70 * 60)
    assert reconciled == []
    assert db.get(job["id"])["status"] == "running"


def test_reconciler_leaves_queued_and_terminal_alone(isolated_db):
    """The reconciler ONLY touches 'running'. A queued job (even an old one) and a
    terminal job are never swept."""
    from parsers import jobworker
    db = isolated_db
    q = db.create_job(type="send-directive", target_kind="broadcast", target_id=None,
                      target_folder=None, prompt="queued old")
    d = db.create_job(type="send-directive", target_kind="broadcast", target_id=None,
                      target_folder=None, prompt="done old")
    db.claim(d["id"]); db.transition(d["id"], "running", "done")
    # back-date BOTH (queued has no started_at; set updated_at far back as a red herring)
    old = (datetime.datetime.now() - datetime.timedelta(hours=5)).astimezone().isoformat(timespec="seconds")
    db._conn().execute("UPDATE jobs SET updated_at=? WHERE id IN (?,?);", (old, q["id"], d["id"]))
    db._conn().commit()
    reconciled = jobworker.reconcile_orphans(max_runtime_s=1)  # ceiling=1s, aggressive
    assert reconciled == []  # neither queued nor done is a 'running' orphan
    assert db.get(q["id"])["status"] == "queued"
    assert db.get(d["id"])["status"] == "done"


# --------------------------------------------------------------------------- #
# Worker _run_one path — drive the FULL transition path with a stubbed agent so
# the worker's own db.transition(...) calls are exercised (a positional-vs-keyword
# arg mismatch there is invisible to tests that call db.transition directly; this
# closes that gap — it is the bug the live run caught).
# --------------------------------------------------------------------------- #
def test_worker_run_one_drives_running_to_done(isolated_db, monkeypatch):
    import asyncio
    from parsers import agent_run, jobworker
    db = isolated_db
    # Stub the blocking agent call so no real claude spawns; return a clean result
    # with a denial (so the audit-trail plumbing is exercised too).
    monkeypatch.setattr(agent_run, "run_readonly", lambda prompt, **kw: {
        "ok": True, "exit_code": 0, "final_text": "Acknowledged: the directive is recorded.",
        "num_turns": 2, "duration_ms": 1234, "is_error": False,
        "permission_denials": [{"tool_name": "Bash", "tool_input": {"command": "echo x"}}],
        "denied_tools": ["Bash"], "session_id": "sess_abc", "stderr_tail": "",
    })
    job = db.create_job(type="send-directive", target_kind="broadcast", target_id=None,
                        target_folder=None, prompt="record this")
    claimed = db.claim_next()
    assert claimed["status"] == "running"

    async def drive():
        sem = asyncio.Semaphore(1)
        await jobworker._run_one(claimed, sem)

    asyncio.run(drive())

    final = db.get(job["id"])
    assert final["status"] == "done", final
    assert final["finished_at"] is not None
    assert final["run_id"] == "sess_abc"
    res = final["result"]
    assert res["ok"] is True
    assert res["summary"].startswith("Acknowledged")
    assert res["final_text"].startswith("Acknowledged")
    # the gate audit-trail is persisted in the record
    assert res["denied_tools"] == ["Bash"]
    assert res["gate"] == "read_only"


def test_worker_run_one_failed_agent_marks_failed(isolated_db, monkeypatch):
    import asyncio
    from parsers import agent_run, jobworker
    db = isolated_db
    monkeypatch.setattr(agent_run, "run_readonly", lambda prompt, **kw: {
        "ok": False, "exit_code": 124, "final_text": None, "num_turns": None,
        "duration_ms": None, "is_error": True, "permission_denials": [],
        "denied_tools": [], "session_id": None, "stderr_tail": "",
        "error": "read-only job exceeded 900s timeout",
    })
    job = db.create_job(type="assign-analysis-task", target_kind="project",
                        target_id="standup/portal", target_folder="standup/portal", prompt="x")
    claimed = db.claim_next()

    async def drive():
        import asyncio as a
        await jobworker._run_one(claimed, a.Semaphore(1))

    asyncio.run(drive())
    final = db.get(job["id"])
    assert final["status"] == "failed"
    assert "timeout" in (final["error"] or "")


def test_worker_run_one_honors_cancel_before_start(isolated_db, monkeypatch):
    """A cancel intent set before the agent starts -> the job is cancelled without
    ever invoking the agent (run_readonly is never called)."""
    import asyncio
    from parsers import agent_run, jobworker
    db = isolated_db
    called = {"n": 0}

    def _should_not_run(prompt, **kw):
        called["n"] += 1
        return {"ok": True, "exit_code": 0, "final_text": "", "denied_tools": [],
                "permission_denials": [], "session_id": None, "stderr_tail": ""}

    monkeypatch.setattr(agent_run, "run_readonly", _should_not_run)
    job = db.create_job(type="send-directive", target_kind="broadcast", target_id=None,
                        target_folder=None, prompt="x")
    claimed = db.claim_next()
    db.request_cancel(job["id"])  # intent set while running, before agent start

    async def drive():
        await jobworker._run_one(claimed, asyncio.Semaphore(1))

    asyncio.run(drive())
    final = db.get(job["id"])
    assert final["status"] == "cancelled"
    assert called["n"] == 0  # agent never ran


# --------------------------------------------------------------------------- #
# API (TestClient) — create / list / get / cancel + CSRF + idempotency
# --------------------------------------------------------------------------- #
_HDR = {"X-Requested-By": "portal"}


def test_api_create_returns_202_and_id(client):
    r = client.post("/api/jobs", headers=_HDR, json={
        "type": "send-directive", "target": "broadcast", "prompt": "all hands at 3pm"})
    assert r.status_code == 202, r.text
    j = r.json()
    assert j["queued"] is True and j["id"].startswith("job_")
    assert j["job"]["status"] == "queued"
    assert j["job"]["type"] == "send-directive"


def test_api_create_resolves_dev_target_folder(client):
    # dev target: portal_backend is a portal developer whose folder is standup/portal.
    r = client.post("/api/jobs", headers=_HDR, json={
        "type": "assign-analysis-task", "target": "dev:portal_backend",
        "prompt": "look at the portal"})
    assert r.status_code == 202, r.text
    job = r.json()["job"]
    assert job["target_id"] == "portal_backend"
    assert job["target_folder"] == "standup/portal"


def test_api_create_rejects_unknown_type(client):
    r = client.post("/api/jobs", headers=_HDR, json={
        "type": "definitely-not-a-real-type", "target": "broadcast", "prompt": "x"})
    assert r.status_code == 409
    assert r.json()["code"] == "bad_type"


def test_api_code_task_rejects_non_git_target(client):
    """Slice 2: assign-task (code_task) against a non-repo target (broadcast) -> 409
    target_not_git — surfaced honestly at create, never a silent no-op (spec §0)."""
    r = client.post("/api/jobs", headers=_HDR, json={
        "type": "assign-task", "target": "broadcast", "prompt": "do a thing"})
    assert r.status_code == 409
    assert r.json()["code"] == "target_not_git"


def test_api_create_rejects_empty_prompt(client):
    r = client.post("/api/jobs", headers=_HDR, json={
        "type": "send-directive", "target": "broadcast", "prompt": "   "})
    assert r.status_code == 409
    assert r.json()["code"] == "empty_prompt"


def test_api_create_rejects_bad_target(client):
    r = client.post("/api/jobs", headers=_HDR, json={
        "type": "send-directive", "target": "dev:nope_not_a_dev", "prompt": "x"})
    assert r.status_code == 409
    assert r.json()["code"] == "bad_target"


def test_api_create_rejects_bad_review_kind(client):
    r = client.post("/api/jobs", headers=_HDR, json={
        "type": "trigger-review", "target": "project:standup/portal",
        "review_kind": "security", "prompt": "x"})
    assert r.status_code == 409
    assert r.json()["code"] == "bad_review_kind"


def test_api_create_csrf_rejects_foreign_origin(client):
    r = client.post("/api/jobs",
                    headers={"Origin": "https://evil.example"},
                    json={"type": "send-directive", "target": "broadcast", "prompt": "x"})
    assert r.status_code == 403
    assert r.json()["code"] == "forbidden_origin"


def test_api_idempotent_create_dedups(client):
    h = dict(_HDR); h["X-Idempotency-Key"] = "abc-123"
    body = {"type": "send-directive", "target": "broadcast", "prompt": "dedupe me"}
    r1 = client.post("/api/jobs", headers=h, json=body)
    r2 = client.post("/api/jobs", headers=h, json=body)
    assert r1.status_code == 202 and r2.status_code == 202
    assert r1.json()["id"] == r2.json()["id"]
    assert r1.json()["idempotent"] is False
    assert r2.json()["idempotent"] is True
    # only one job exists
    lst = client.get("/api/jobs").json()
    assert len(lst["jobs"]) == 1


def test_api_list_and_get_and_counts(client):
    client.post("/api/jobs", headers=_HDR, json={
        "type": "send-directive", "target": "broadcast", "prompt": "a"})
    rid = client.post("/api/jobs", headers=_HDR, json={
        "type": "trigger-review", "target": "project:standup/portal",
        "review_kind": "pm", "prompt": "b"}).json()["id"]
    lst = client.get("/api/jobs").json()
    assert len(lst["jobs"]) == 2
    assert lst["counts"]["queued"] == 2
    # newest-first: the review (created 2nd) is first
    assert lst["jobs"][0]["id"] == rid
    # filter
    only_rev = client.get("/api/jobs", params={"type": "trigger-review"}).json()
    assert {j["id"] for j in only_rev["jobs"]} == {rid}
    # get one (full record)
    one = client.get(f"/api/jobs/{rid}").json()
    assert one["id"] == rid and one["review_kind"] == "pm"
    # unknown id -> 404
    assert client.get("/api/jobs/job_nope").status_code == 404


def test_api_cancel_queued_immediately(client):
    """A still-queued job cancels immediately (the worker is off, so it stays
    queued, and cancel flips it straight to cancelled)."""
    jid = client.post("/api/jobs", headers=_HDR, json={
        "type": "send-directive", "target": "broadcast", "prompt": "cancel me"}).json()["id"]
    r = client.post(f"/api/jobs/{jid}/cancel", headers=_HDR)
    assert r.status_code == 202, r.text
    assert r.json()["ok"] is True
    assert client.get(f"/api/jobs/{jid}").json()["status"] == "cancelled"


def test_api_cancel_terminal_is_409(client):
    from parsers import db
    jid = client.post("/api/jobs", headers=_HDR, json={
        "type": "send-directive", "target": "broadcast", "prompt": "x"}).json()["id"]
    # drive it to done directly via the store (simulating the worker)
    db.claim(jid); db.transition(jid, "running", "done")
    r = client.post(f"/api/jobs/{jid}/cancel", headers=_HDR)
    assert r.status_code == 409
    assert r.json()["code"] == "terminal"


def test_api_cancel_unknown_is_404(client):
    r = client.post("/api/jobs/job_nope/cancel", headers=_HDR)
    assert r.status_code == 404


# --------------------------------------------------------------------------- #
# No regression: the existing read endpoints + actions still mount/answer.
# --------------------------------------------------------------------------- #
def test_existing_status_endpoint_still_works(client):
    assert client.get("/api/status").status_code == 200


def test_existing_actions_guard_still_works(client):
    r = client.get("/api/actions/guard")
    assert r.status_code == 200
    assert "ok" in r.json()


def test_healthz_reports_jobworker_disabled(client):
    j = client.get("/healthz").json()
    assert j["ok"] is True
    assert j["jobworker"] is False  # worker off in tests


# --------------------------------------------------------------------------- #
# SLICE 2 — code-task path: DB intents + the worker gate-held → awaiting →
# approve → commit flow against a REAL temp git repo (stubbed agent does the edit).
# --------------------------------------------------------------------------- #
import subprocess


def _git(repo, *args, **kw):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                          timeout=60, **kw)


@pytest.fixture()
def demo_repo(tmp_path):
    """A throwaway git repo with a local bare 'origin' (so R3 fetch + origin/HEAD
    resolve), wired under a fake workspace so paths.workspace_root()/<folder> hits it."""
    ws = tmp_path / "ws"
    repo = ws / "demo_proj"
    repo.mkdir(parents=True)
    _git(str(repo), "init", "-q", "-b", "main")
    _git(str(repo), "config", "user.name", "T")
    _git(str(repo), "config", "user.email", "t@local")
    (repo / "greet.py").write_text("def greet(n):\n    return 'hi ' + n\n", encoding="utf-8")
    _git(str(repo), "add", "-A")
    _git(str(repo), "commit", "-qm", "init")
    origin = tmp_path / "demo_origin.git"
    _git(str(repo), "clone", "-q", "--bare", str(repo), str(origin))
    _git(str(repo), "remote", "add", "origin", str(origin))
    _git(str(repo), "fetch", "-q", "origin")
    _git(str(repo), "remote", "set-head", "origin", "-a")
    return {"workspace": ws, "repo": repo, "origin": origin, "folder": "demo_proj"}


def test_db_approve_reject_intents(isolated_db):
    """request_approve flips awaiting_approval->committing (R6 intent); request_reject
    flips awaiting_approval->rejected. Both are no-ops from any other status."""
    db = isolated_db
    job = db.create_job(type="assign-task", target_kind="project", target_id="demo_proj",
                        target_folder="demo_proj", prompt="x", execution_path="code_task")
    jid = job["id"]
    # not awaiting yet -> approve/reject are no-ops
    assert db.request_approve(jid) is False
    assert db.request_reject(jid) is False
    # move it to awaiting_approval manually, then approve
    db.claim(jid)
    db.transition(jid, "running", "awaiting_approval", fields={"diff_text": "DIFF"})
    assert db.request_approve(jid, approved_by="kain") is True
    got = db.get(jid)
    assert got["status"] == "committing"
    assert got["approved_by"] == "kain" and got["approved_at"] is not None
    # a second approve loses the race (now committing, not awaiting)
    assert db.request_approve(jid) is False
    assert db.list_committing()[0]["id"] == jid


def test_worker_code_task_parks_at_awaiting_with_diff_no_commit(isolated_db, monkeypatch,
                                                                demo_repo):
    """The headline gate-held proof: the worker runs a code task, the (stubbed) agent
    EDITS a file in the worktree, the worker STAGES + DIFFS but does NOT commit — the
    job lands at awaiting_approval with a real diff, and NO new commit exists on the
    branch (the branch tip == origin/main)."""
    import asyncio
    from parsers import agent_run, jobworker, paths

    db = isolated_db
    monkeypatch.setenv("STANDUP_ROOT", str(demo_repo["workspace"] / "STANDUP"))
    (demo_repo["workspace"] / "STANDUP" / "control").mkdir(parents=True, exist_ok=True)
    # workspace_root() = STANDUP_ROOT.parent = demo_repo["workspace"]; folder=demo_proj
    importlib.reload(paths)
    monkeypatch.setattr(jobworker, "paths", paths)
    monkeypatch.setattr(jobworker.worktree, "DIFF_MAX", 200_000, raising=False)

    # Stub the agent: instead of spawning claude, EDIT a file in the worktree (what the
    # gated agent would do) and report ok. The worktree path is passed as kw `worktree`.
    def fake_agent(prompt, *, worktree, **kw):
        p = Path(worktree) / "greet.py"
        p.write_text("def greet(n):\n    return 'HELLO ' + n  # changed\n", encoding="utf-8")
        return {"ok": True, "exit_code": 0, "final_text": "Edited greet.py to shout.",
                "num_turns": 3, "duration_ms": 999, "is_error": False,
                "permission_denials": [{"tool_name": "Bash"}], "denied_tools": ["Bash"],
                "session_id": "sess_code", "stderr_tail": ""}
    monkeypatch.setattr(agent_run, "run_code_task", fake_agent)

    job = db.create_job(type="assign-task", target_kind="project", target_id="demo_proj",
                        target_folder="demo_proj", prompt="make greet shout",
                        execution_path="code_task")
    jid = job["id"]
    claimed = db.claim_next()
    assert claimed["id"] == jid

    asyncio.run(jobworker._run_one(claimed, asyncio.Semaphore(1)))

    final = db.get(jid)
    assert final["status"] == "awaiting_approval", final
    assert final["branch"] == f"auto/standup-{jid}"
    assert final["worktree_path"] and Path(final["worktree_path"]).is_dir()
    # the diff is captured in its OWN column
    assert "HELLO" in (final["diff_text"] or "")
    assert "greet.py" in (final["diff_text"] or "")
    # GATE HELD: NO commit exists on the branch yet — its tip == origin/main.
    branch_tip = _git(final["worktree_path"], "rev-parse", "HEAD").stdout.strip()
    origin_main = _git(str(demo_repo["repo"]), "rev-parse", "origin/main").stdout.strip()
    assert branch_tip == origin_main, "a commit was made before approval — gate FAILED"


def test_worker_approve_commits_to_branch_then_done(isolated_db, monkeypatch, demo_repo):
    """After awaiting_approval, the approve intent (committing) is picked up by the
    worker which COMMITS to the job branch — main untouched, sha recorded, status done."""
    import asyncio
    from parsers import agent_run, jobworker, paths

    db = isolated_db
    monkeypatch.setenv("STANDUP_ROOT", str(demo_repo["workspace"] / "STANDUP"))
    (demo_repo["workspace"] / "STANDUP" / "control").mkdir(parents=True, exist_ok=True)
    importlib.reload(paths)
    monkeypatch.setattr(jobworker, "paths", paths)

    def fake_agent(prompt, *, worktree, **kw):
        (Path(worktree) / "greet.py").write_text(
            "def greet(n):\n    return 'HELLO ' + n\n", encoding="utf-8")
        return {"ok": True, "exit_code": 0, "final_text": "done", "denied_tools": ["Bash"],
                "permission_denials": [{"tool_name": "Bash"}], "session_id": "s", "stderr_tail": ""}
    monkeypatch.setattr(agent_run, "run_code_task", fake_agent)

    job = db.create_job(type="assign-task", target_kind="project", target_id="demo_proj",
                        target_folder="demo_proj", prompt="shout", execution_path="code_task")
    jid = job["id"]
    db.claim_next()
    asyncio.run(jobworker._run_one(db.get(jid), asyncio.Semaphore(1)))
    assert db.get(jid)["status"] == "awaiting_approval"

    main_before = _git(str(demo_repo["repo"]), "rev-parse", "main").stdout.strip()

    # APPROVE: HTTP handler only sets the committing intent (R6).
    assert db.request_approve(jid, approved_by="kain") is True
    assert db.get(jid)["status"] == "committing"

    # WORKER picks up the committing job and does the REAL commit.
    asyncio.run(jobworker._commit_one(db.get(jid)))

    final = db.get(jid)
    assert final["status"] == "done", final
    res = final["result"]
    sha = res["commit_sha"]
    assert sha, "no commit sha recorded"
    # the commit is on the JOB BRANCH; main is UNTOUCHED.
    main_after = _git(str(demo_repo["repo"]), "rev-parse", "main").stdout.strip()
    assert main_after == main_before, "main moved — the commit leaked off the job branch"
    branch_sha = _git(str(demo_repo["repo"]), "rev-parse",
                      f"auto/standup-{jid}").stdout.strip()
    assert branch_sha == sha, "the recorded sha is not the job branch tip"
    # the committed diff is on the branch
    show = _git(str(demo_repo["repo"]), "show", sha).stdout
    assert "HELLO" in show


def test_worker_reject_discards_worktree(isolated_db, monkeypatch, demo_repo):
    """Reject flips awaiting_approval->rejected; the API-style cleanup removes the
    worktree + branch."""
    import asyncio
    from parsers import agent_run, jobworker, paths, worktree

    db = isolated_db
    monkeypatch.setenv("STANDUP_ROOT", str(demo_repo["workspace"] / "STANDUP"))
    (demo_repo["workspace"] / "STANDUP" / "control").mkdir(parents=True, exist_ok=True)
    importlib.reload(paths)
    monkeypatch.setattr(jobworker, "paths", paths)

    def fake_agent(prompt, *, worktree, **kw):
        (Path(worktree) / "greet.py").write_text("def greet(n):\n    return 'X'\n", encoding="utf-8")
        return {"ok": True, "exit_code": 0, "final_text": "d", "denied_tools": [],
                "permission_denials": [], "session_id": "s", "stderr_tail": ""}
    monkeypatch.setattr(agent_run, "run_code_task", fake_agent)

    job = db.create_job(type="assign-task", target_kind="project", target_id="demo_proj",
                        target_folder="demo_proj", prompt="x", execution_path="code_task")
    jid = job["id"]
    db.claim_next()
    asyncio.run(jobworker._run_one(db.get(jid), asyncio.Semaphore(1)))
    awaiting = db.get(jid)
    assert awaiting["status"] == "awaiting_approval"
    wt = awaiting["worktree_path"]
    assert Path(wt).is_dir()

    # REJECT (the db intent) + the worktree teardown the API would run.
    assert db.request_reject(jid) is True
    assert db.get(jid)["status"] == "rejected"
    worktree.remove(str(demo_repo["repo"]), wt, awaiting["branch"])
    assert not Path(wt).exists()
    # the branch is gone too
    assert _git(str(demo_repo["repo"]), "rev-parse", "--verify",
                f"auto/standup-{jid}").returncode != 0


def test_approve_requires_separate_approver_when_policy_on(client):
    """Opt-in separation of duties: with require_separate_approver, the creator can't
    self-approve (409 same_approver); a different approver succeeds (202)."""
    import parsers.db as db
    from parsers import paths
    job = db.create_job(type="assign-task", target_kind="project", target_id="standup/portal",
                        target_folder="standup/portal", prompt="add a helper", execution_path="code_task")
    jid = job["id"]
    assert (job.get("created_by") or "portal") == "portal"
    db.claim_next()  # queued -> running
    db.transition(jid, "running", "awaiting_approval", fields={"diff_text": "DIFF"})
    (paths.control_dir() / "policy.json").write_text('{"require_separate_approver": true}')

    HDR = {"X-Requested-By": "portal"}
    r = client.post(f"/api/jobs/{jid}/approve", headers=HDR, json={})  # creator == approver
    assert r.status_code == 409 and r.json()["code"] == "same_approver", r.text
    r = client.post(f"/api/jobs/{jid}/approve", headers=HDR, json={"approved_by": "reviewer"})
    assert r.status_code == 202, r.text


def test_approve_self_ok_when_policy_off(client):
    """Default (no policy): the single operator can approve their own job."""
    import parsers.db as db
    job = db.create_job(type="assign-task", target_kind="project", target_id="standup/portal",
                        target_folder="standup/portal", prompt="x", execution_path="code_task")
    jid = job["id"]
    db.claim_next()
    db.transition(jid, "running", "awaiting_approval", fields={"diff_text": "DIFF"})
    r = client.post(f"/api/jobs/{jid}/approve", headers={"X-Requested-By": "portal"}, json={})
    assert r.status_code == 202, r.text
