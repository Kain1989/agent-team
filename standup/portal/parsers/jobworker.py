"""The job WORKER — a supervised asyncio task that runs read-only board jobs.

Added BESIDE the scheduler loop in app._lifespan (same supervise + add_done_callback
loud-death pattern), enabled only when STANDUP_JOBWORKER=1 so importing the app in
tests never spawns it. It polls ~2s, atomic-claims queued jobs, runs them under a
concurrency Semaphore, and is the SINGLE WRITER of job-state transitions.

CONCURRENCY (from the pair-challenge)
-------------------------------------
Read-only jobs NEVER acquire control/run.lock — they cannot write, so they can't
double-fire a tick. Concurrency is bounded ONLY by an asyncio.Semaphore
(MAX_PARALLEL, default 3). This is deliberate: a read-only job can neither starve
nor be starved by a future code-task lock — the two are decoupled. (Slice 2's
code-task path is the one that will take run.lock; not built here.)

LIFECYCLE (the single primitive)
--------------------------------
Every transition goes through db.transition(id, from, to) (UPDATE ... WHERE
id=? AND status=from), so any race resolves to one winner:
  - claim:  db.claim_next() == UPDATE ... WHERE status='queued'  (rowcount==1 owns)
  - finish: transition('running' -> 'done'|'failed')
  - cancel: a queued job -> 'cancelled' immediately (HTTP sets the intent; the
            worker / a fast path applies it); a running job that has cancel_requested
            is aborted cooperatively -> 'cancelled'.

ORPHAN RECONCILER (NEW — does NOT reuse actions._sweep_stuck_running; that reads
control/results/*.json and knows nothing of jobs.db). On worker startup and each
poll it sweeps 'running' jobs whose started_at exceeds MAX_RUNTIME_S and whose
worker is gone (a daemon crash mid-job) -> 'failed' with a reason. It NEVER touches
queued/terminal jobs.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as _dt
import importlib.util as _ilu
import json as _json
import logging
import os
from typing import Any, Dict, Optional

from . import agent_run, costs, db, guardrails, job_prompts, notify, paths, worktree

_log = logging.getLogger("standup.jobworker")


# --- R5: the shared run.lock (a code_task is a writer; obey single-flight) ----
# Loaded from the real checkout (paths.run_lock_module resolves it independent of
# STANDUP_ROOT) so the lock SEMANTICS live in ONE place — the same module drain.py
# and the scheduled-tick crons hold. A read-only job NEVER takes the lock.
_RUN_LOCK_MOD = None


def _run_lock_mod():
    """Import (once) the shared control/run_lock.py as a module. Returns the module or
    None if it can't be loaded (then a code_task proceeds without the cross-process
    lock — degraded but the SQLite single-claim still prevents in-process double-run)."""
    global _RUN_LOCK_MOD
    if _RUN_LOCK_MOD is not None:
        return _RUN_LOCK_MOD
    try:
        p = paths.run_lock_module()
        spec = _ilu.spec_from_file_location("standup_run_lock", str(p))
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        _RUN_LOCK_MOD = mod
    except Exception:
        _log.exception("could not load run_lock module — code tasks run WITHOUT the "
                       "cross-process run.lock (SQLite single-claim still applies)")
        _RUN_LOCK_MOD = None
    return _RUN_LOCK_MOD


def _acquire_run_lock(jid: str):
    """R5: take control/run.lock NON-BLOCKING for a code_task before any git/worktree
    work. Returns the held RunLock (call .release() when done) or None if the lock is
    held by a live tick / another code_task (the caller must DEFER — re-queue + retry).
    Also returns None (proceed unlocked) if the module couldn't load."""
    mod = _run_lock_mod()
    if mod is None:
        return None  # degraded: proceed without the cross-process lock
    try:
        lock = mod.RunLock(kind=f"job-{jid}", run_id=jid, holder="portal-jobworker",
                           control_dir=paths.control_dir())
        return lock if lock.acquire() else False
    except Exception:
        _log.exception("run.lock acquire raised for %s — proceeding unlocked", jid)
        return None

# Poll cadence: a queued job starts within ~POLL_S. (The scheduler sleeps to tick
# boundaries; the worker polls fast — two cadences, one process.)
POLL_S = float(os.environ.get("STANDUP_JOB_POLL_S", "2.0"))
# Max concurrent read-only jobs. Reviews/analyses are cheap + side-effect-free.
MAX_PARALLEL = int(os.environ.get("STANDUP_JOB_MAX_PARALLEL", "3"))
# A 'running' job older than this whose worker is gone is an orphan (crashed
# mid-job). Reuse the run-lock dead-holder ceiling so the two never drift; a single
# read-only job's own timeout (agent_run.JOB_TIMEOUT_S) is far shorter, so a job
# that is legitimately running is never swept.
MAX_RUNTIME_S = int(os.environ.get("STANDUP_JOB_MAX_RUNTIME_S", str(70 * 60)))


# --- loop liveness (mirror scheduler._LOOP_STATE so /healthz can prove it lives) -
_LOOP_STATE: Dict[str, Any] = {
    "started_at": None,
    "last_beat_at": None,
    "alive": False,
    "claimed": 0,    # jobs this worker has claimed
    "completed": 0,  # jobs reaching a terminal state via this worker
    "running": 0,    # in-flight right now (under the semaphore)
    "awaiting": 0,   # code_tasks parked at awaiting_approval by this worker
    "committed": 0,  # code_tasks the human approved + this worker committed
    "reconciled": 0, # orphans swept to failed
    "restarts": 0,
}


def loop_state() -> Dict[str, Any]:
    s = dict(_LOOP_STATE)
    beat = s.get("last_beat_at")
    age = None
    if beat:
        try:
            dt = _dt.datetime.fromisoformat(beat)
            if dt.tzinfo is not None:
                dt = dt.astimezone().replace(tzinfo=None)
            age = max(0, int((_now() - dt).total_seconds()))
        except (ValueError, TypeError):
            age = None
    s["last_beat_age_s"] = age
    return s


def _mark(**kw: Any) -> None:
    _LOOP_STATE.update(kw)


def _bump(key: str, by: int = 1) -> None:
    _LOOP_STATE[key] = _LOOP_STATE.get(key, 0) + by


def _now() -> _dt.datetime:
    return _dt.datetime.now()


def _iso(dt: Optional[_dt.datetime] = None) -> str:
    return (dt or _now()).astimezone().isoformat(timespec="seconds")


# --- the orphan reconciler ---------------------------------------------------
def reconcile_orphans(now: Optional[_dt.datetime] = None,
                      max_runtime_s: Optional[int] = None) -> list:
    """Sweep 'running' jobs whose started_at exceeds the max runtime to 'failed'
    (a worker that crashed mid-job leaves a row stuck 'running' forever, which
    nothing else clears — this is NOT actions._sweep_stuck_running; that watches
    the file-queue, not jobs.db). Only touches 'running'; never queued/terminal.
    Uses transition() so it can't race a live worker that is finishing the same job
    (the finish transition out of 'running' wins; our 'running'->'failed' then
    no-ops). Returns the ids reconciled."""
    now = now or _now()
    ceiling = max_runtime_s if max_runtime_s is not None else MAX_RUNTIME_S
    reconciled = []
    for job in db.list_running(now=now):
        started = job.get("started_at")
        sdt = _parse_iso(started)
        if sdt is None:
            continue
        age = (now - sdt).total_seconds()
        if age <= ceiling:
            continue
        result = {
            "ok": False,
            "error": (f"orphan reconciler: running > {ceiling // 60}min "
                      f"({int(age)}s) — the worker is gone (crashed mid-job); "
                      "reconciled to failed so it can't wedge 'running' forever"),
            "reconciled_by": "jobworker_reconciler",
        }
        if db.transition(job["id"], "running", "failed", now=now,
                         fields={"finished_at": _iso(now),
                                 "error": result["error"],
                                 "result_json": _dumps(result)}):
            _bump("reconciled")
            reconciled.append(job["id"])
            _log.warning("reconciled orphan job %s (running %ds, worker gone)",
                         job["id"], int(age))
    return reconciled


# --- running one job ---------------------------------------------------------
async def _run_one(job: Dict[str, Any], sem: asyncio.Semaphore) -> None:
    """Run a single claimed (status='running') job under the concurrency semaphore,
    then transition it to a terminal state via the single primitive. The worker is
    the only writer of these transitions."""
    async with sem:
        _bump("running")
        jid = job["id"]
        try:
            # Cooperative cancel: if a cancel intent landed between claim and start,
            # honor it before spending a single agent turn.
            fresh = await asyncio.to_thread(db.get, jid)
            if fresh and fresh.get("cancel_requested"):
                applied = await asyncio.to_thread(
                    db.transition, jid, "running", "cancelled",
                    fields={"finished_at": _iso(),
                            "result_json": _dumps({"ok": False,
                                                   "cancelled": True,
                                                   "note": "cancelled before agent start"})})
                if applied:
                    _bump("completed")
                return

            # Resolve target, then dispatch on execution path: read-only (Slice 1) or
            # the worktree-scoped, human-approved code task (Slice 2).
            target = await asyncio.to_thread(_resolve_target_for, job)
            if (job.get("execution_path") or "read_only") == "code_task":
                await _run_code_task_job(jid, job, target)
            else:
                await _run_readonly_job(jid, job, target)
        except Exception as exc:  # a job crash must never kill the worker loop
            _log.exception("job %s raised while running", jid)
            try:
                await asyncio.to_thread(
                    db.transition, jid, "running", "failed",
                    fields={"finished_at": _iso(),
                            "error": f"worker exception: {exc}",
                            "result_json": _dumps({"ok": False, "error": str(exc)})})
                _bump("completed")
            except Exception:
                _log.exception("failed to mark job %s failed after crash", jid)
        finally:
            _bump("running", by=-1)


async def _run_readonly_job(jid: str, job: Dict[str, Any], target: Dict[str, Any]) -> None:
    """Slice 1 path: run the gated READ-ONLY agent, then transition done|failed (or
    cancelled if a cancel arrived mid-run). The agent physically cannot mutate."""
    prompt = job_prompts.build_prompt(job, target)
    cwd = target.get("folder")
    cwd_abs = str(paths.workspace_root() / cwd) if cwd else str(paths.workspace_root())
    result = await asyncio.to_thread(agent_run.run_readonly, prompt, cwd=cwd_abs)

    fresh = await asyncio.to_thread(db.get, jid)
    if fresh and fresh.get("cancel_requested"):
        record = _result_record(job, target, result, cancelled=True)
        applied = await asyncio.to_thread(
            db.transition, jid, "running", "cancelled",
            fields={"finished_at": _iso(), "run_id": result.get("session_id"),
                    "result_json": _dumps(record)})
        if applied:
            _bump("completed")
        return

    record = _result_record(job, target, result)
    to_status = "done" if result.get("ok") else "failed"
    err = None if result.get("ok") else (result.get("error") or "agent run failed")
    applied = await asyncio.to_thread(
        db.transition, jid, "running", to_status,
        fields={"finished_at": _iso(), "run_id": result.get("session_id"),
                "error": err, "result_json": _dumps(record)})
    if applied:
        _bump("completed")


async def _run_code_task_job(jid: str, job: Dict[str, Any], target: Dict[str, Any]) -> None:
    """Slice 2 path: under the run.lock (R5), create an ISOLATED worktree off origin's
    default branch (R2/R3), run the worktree-scoped WRITE-capable agent in it, then
    STAGE + DIFF the edits (NO commit — R6) and park at 'awaiting_approval' with the
    captured diff for a human decision. The COMMIT happens only later, in the worker's
    `committing` pickup, after the human approves on the board.

    On no-target / origin-unreachable / worktree error / agent failure / cancel / no
    changes the worktree is torn down and the job goes failed|cancelled|done. The agent
    NEVER commits/merges and nothing here runs its edited code (no tests in the trusted
    path)."""
    folder = target.get("folder")
    if not folder:
        await _finish_code(jid, "failed",
                           {"ok": False, "gate": "code_task", "changed": False,
                            "error": "a code task needs a project/dev target with a repo folder",
                            "summary": "no target repo — cannot run a code task"},
                           error="code task target has no repo folder")
        return

    # R5: take the shared run.lock NON-BLOCKING before any git/worktree/agent work. If a
    # standup tick or another code_task holds it, DEFER: re-queue (running->queued) and
    # let the next poll retry — the job waits, never double-writes.
    lock = await asyncio.to_thread(_acquire_run_lock, jid)
    if lock is False:
        re_q = await asyncio.to_thread(db.transition, jid, "running", "queued")
        if re_q:
            _log.info("code task %s deferred — run.lock held by a live writer; re-queued", jid)
        return
    try:
        await _run_code_task_locked(jid, job, target, folder)
    finally:
        if lock is not None and lock is not False:
            with contextlib.suppress(Exception):
                lock.release()


async def _run_code_task_locked(jid: str, job: Dict[str, Any], target: Dict[str, Any],
                                folder: str) -> None:
    repo_root = str(paths.workspace_root() / folder)
    branch = f"auto/standup-{jid}"
    dest = str(paths.code_worktrees_dir() / jid)

    # R2/R3: the WORKER creates the worktree (the Work-phase does NOT auto-worktree),
    # cut from origin/<default> after a `git fetch origin`. An unreachable origin / a
    # non-git target fails HONESTLY with a code (never a silent local fall-back).
    wt = await asyncio.to_thread(worktree.create, repo_root, branch, dest)
    if not wt.get("ok"):
        code = wt.get("code") or "worktree"
        await _finish_code(jid, "failed",
                           {"ok": False, "gate": "code_task", "changed": False,
                            "code": code, "error": wt.get("error"),
                            "summary": f"could not create worktree [{code}]: {wt.get('error')}"},
                           error=f"{code}: {wt.get('error')}")
        return
    base_sha = wt.get("base_sha")
    # Record the worktree handle immediately so a crash mid-run still leaves a trail.
    await asyncio.to_thread(db.transition, jid, "running", "running",
                            fields={"worktree_path": dest, "branch": branch,
                                    "base_sha": base_sha})

    prompt = job_prompts.build_prompt(job, target)
    result = await asyncio.to_thread(agent_run.run_code_task, prompt, worktree=dest)

    fresh = await asyncio.to_thread(db.get, jid)
    cancelled = bool(fresh and fresh.get("cancel_requested"))
    if cancelled or not result.get("ok"):
        await asyncio.to_thread(worktree.remove, repo_root, dest, branch)
        rec = _code_result_record(job, target, result, repo_root, branch, dest,
                                  harvest=None, cancelled=cancelled)
        await _finish_code(jid, "cancelled" if cancelled else "failed", rec,
                           error=None if cancelled else (result.get("error") or "code agent failed"),
                           run_id=result.get("session_id"),
                           fields={"worktree_path": None})
        return

    # Trusted STAGE + DIFF — NO commit (R6: the gate held, nothing is committed yet).
    h = await asyncio.to_thread(worktree.stage_and_diff, dest, base_sha)
    if not h.get("ok"):
        await asyncio.to_thread(worktree.remove, repo_root, dest, branch)
        rec = _code_result_record(job, target, result, repo_root, branch, dest, harvest=h)
        await _finish_code(jid, "failed", rec, error=f"stage_and_diff: {h.get('error')}",
                           run_id=result.get("session_id"), fields={"worktree_path": None})
        return

    if not h.get("changed"):
        # No edits produced — nothing to approve. Tear down; terminal 'done'.
        await asyncio.to_thread(worktree.remove, repo_root, dest, branch)
        rec = _code_result_record(job, target, result, repo_root, branch, dest, harvest=h)
        rec["summary"] = "code task completed with no changes"
        await _finish_code(jid, "done", rec, run_id=result.get("session_id"),
                           fields={"worktree_path": None})
        return

    # OUTPUT GUARDRAIL (P5): scan the produced diff for apparent secrets BEFORE parking
    # it for human approval, so a leaked credential is FAILED here (not quietly queued
    # for a click-through approve). Tear down the worktree on a hard violation.
    g = await asyncio.to_thread(guardrails.check_output, h.get("diff", ""))
    if not g["ok"]:
        await asyncio.to_thread(worktree.remove, repo_root, dest, branch)
        rec = _code_result_record(job, target, result, repo_root, branch, dest, harvest=h)
        rec["summary"] = f"BLOCKED by output guardrail: {g['reason']}"
        rec["guardrail_findings"] = g["findings"]
        await _finish_code(jid, "failed", rec, error=g["reason"],
                           run_id=result.get("session_id"), fields={"worktree_path": None})
        try:
            notify.notify("guardrail_block",
                          f"job {jid} diff blocked — {g['reason']}", {"job_id": jid})
        except Exception:
            pass
        return

    # Changes present + clean -> park at awaiting_approval; KEEP the worktree + branch so
    # the human can approve (worker commits) or reject (worker discards). Store the diff in
    # its OWN column so /approve never depends on the worktree still existing.
    rec = _code_result_record(job, target, result, repo_root, branch, dest, harvest=h)
    applied = await asyncio.to_thread(
        db.transition, jid, "running", "awaiting_approval",
        fields={"run_id": result.get("session_id"), "result_json": _dumps(rec),
                "diff_text": h.get("diff", ""), "worktree_path": dest, "branch": branch})
    if applied:
        _bump("awaiting")
        # notify-on-approval: surface that a diff is parked waiting for a human (P2/notify).
        try:
            notify.notify("awaiting_approval",
                          f"job {jid} is awaiting your approval — review the diff in the portal",
                          {"job_id": jid, "branch": branch,
                           "files": [f.get("path") for f in (h.get("files") or [])]})
        except Exception:
            pass


# --- R6: the human-gated COMMIT pickup (worker is the single commit writer) ---
async def _commit_one(job: Dict[str, Any]) -> None:
    """Perform the actual git commit for a job the human APPROVED (status='committing').
    The HTTP /approve handler only set the intent (awaiting_approval->committing); the
    WORKER (here) is the single writer of the commit — it commits the staged edits on
    the job branch (NEVER pushes, NEVER merges to mainline) and transitions
    committing->done with the commit sha, or committing->failed on a git error."""
    jid = job["id"]
    dest = job.get("worktree_path")
    branch = job.get("branch")
    base_sha = job.get("base_sha")
    repo_root = None
    res = job.get("result") or {}
    if isinstance(res, dict):
        repo_root = res.get("repo_root")
    try:
        if not dest or not os.path.isdir(dest):
            await asyncio.to_thread(
                db.transition, jid, "committing", "failed",
                fields={"finished_at": _iso(),
                        "error": "approved but the worktree is gone — cannot commit "
                                 "(a reboot likely GC'd /tmp; re-run the task)",
                        "result_json": _dumps({**res, "ok": False,
                                               "error": "worktree missing at commit"})})
            _bump("completed")
            return
        c = await asyncio.to_thread(worktree.commit, dest, base_sha, _commit_message(job, jid))
        if not c.get("ok"):
            await asyncio.to_thread(
                db.transition, jid, "committing", "failed",
                fields={"finished_at": _iso(), "error": f"commit: {c.get('error')}",
                        "result_json": _dumps({**res, "ok": False,
                                               "commit_error": c.get("error")})})
            _bump("completed")
            return
        sha = c.get("commit_sha")
        new_res = {**res, "ok": True, "committed": True, "commit_sha": sha,
                   "branch": branch,
                   "summary": (f"approved + committed to {branch} @ {(sha or '')[:10]}"
                               if sha else "approved + committed")}
        await asyncio.to_thread(
            db.transition, jid, "committing", "done",
            fields={"finished_at": _iso(), "diff_text": c.get("diff", job.get("diff_text") or ""),
                    "result_json": _dumps(new_res)})
        _bump("committed")
        _bump("completed")
        _log.info("code task %s approved + committed %s on %s", jid, (sha or "")[:10], branch)
        # Tear down the worktree now that the branch carries the commit (the branch +
        # its commit remain in the target repo for the human to merge/push).
        if repo_root:
            await asyncio.to_thread(worktree.remove, repo_root, dest, None)
    except Exception as exc:  # never let a commit crash kill the worker
        _log.exception("commit pickup for %s raised", jid)
        with contextlib.suppress(Exception):
            await asyncio.to_thread(
                db.transition, jid, "committing", "failed",
                fields={"finished_at": _iso(), "error": f"commit pickup exception: {exc}"})
            _bump("completed")


async def _finish_code(jid: str, status: str, record: Dict[str, Any], *,
                       error: Optional[str] = None, run_id: Optional[str] = None,
                       fields: Optional[Dict[str, Any]] = None) -> None:
    """Transition a code-task job to a TERMINAL status with its record + bump completed
    (awaiting_approval is handled inline since it is non-terminal)."""
    f: Dict[str, Any] = {"finished_at": _iso(), "result_json": _dumps(record)}
    if error is not None:
        f["error"] = error
    if run_id is not None:
        f["run_id"] = run_id
    if fields:
        f.update(fields)
    applied = await asyncio.to_thread(db.transition, jid, "running", status, fields=f)
    if applied:
        _bump("completed")


def _commit_message(job: Dict[str, Any], jid: str) -> str:
    first = ((job.get("prompt") or "").strip().splitlines() or [""])[0][:72]
    return f"standup code task {jid}: {first}".strip()


def _code_result_record(job: Dict[str, Any], target: Dict[str, Any],
                        result: Dict[str, Any], repo_root: str, branch: str,
                        worktree_path: str, *, harvest: Optional[Dict[str, Any]],
                        cancelled: bool = False) -> Dict[str, Any]:
    """The result_json for a code-task job: the agent run + the harvested review diff +
    the merge handle (/approve reads repo_root/branch/worktree to merge, /reject to
    discard). The agent's final_text is its own change summary for the human."""
    h = harvest or {}
    rec: Dict[str, Any] = {
        "ok": bool(result.get("ok")) and not cancelled,
        "cancelled": cancelled,
        "gate": "code_task",
        "type": job.get("type"),
        "target": {"kind": target.get("kind"), "id": target.get("id"),
                   "folder": target.get("folder"), "label": target.get("label")},
        "repo_root": repo_root,
        "branch": branch,
        "worktree": worktree_path,
        # No commit at awaiting_approval time (R6: the commit is the human-gated step).
        # commit_sha is populated by the worker's `committing` pickup on approval.
        "commit_sha": h.get("commit_sha"),
        "changed": bool(h.get("changed")),
        "files": h.get("files", []),
        "diff": h.get("diff", ""),
        "diff_truncated": bool(h.get("diff_truncated")),
        "summary": _summarize(job, result),
        "final_text": result.get("final_text"),
        "exit_code": result.get("exit_code"),
        "num_turns": result.get("num_turns"),
        "duration_ms": result.get("duration_ms"),
        "denied_tools": result.get("denied_tools", []),
        "permission_denials": result.get("permission_denials", []),
        # Gate provenance — the exact lockdown the agent ran under (audit trail proving
        # the code-task gate was applied: default perm mode, allow-list, empty MCP).
        "permission_mode": result.get("permission_mode"),
        "allowed_tools": result.get("allowed_tools"),
        "strict_mcp_config": result.get("strict_mcp_config"),
        "mcp_config": result.get("mcp_config"),
        "cwd": result.get("cwd"),
    }
    if result.get("error"):
        rec["error"] = result["error"]
    if h.get("error"):
        rec["harvest_error"] = h["error"]
    if result.get("stderr_tail"):
        rec["stderr_tail"] = result["stderr_tail"]
    return rec


def _resolve_target_for(job: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve the job's target from the live team.json (runs in a thread)."""
    from . import team as team_mod
    parsed = team_mod.parse(paths.team_json())
    return job_prompts.resolve_target(parsed, job.get("target_kind"), job.get("target_id"))


def _result_record(job: Dict[str, Any], target: Dict[str, Any],
                   result: Dict[str, Any], *, cancelled: bool = False) -> Dict[str, Any]:
    """The result_json the UI renders + the audit trail. Includes the agent's final
    text (the verdict/acknowledgement/analysis) AND the permission_denials proving
    the read-only gate fired."""
    rec = {
        "ok": bool(result.get("ok")) and not cancelled,
        "cancelled": cancelled,
        "type": job.get("type"),
        "review_kind": job.get("review_kind"),
        "target": {"kind": target.get("kind"), "id": target.get("id"),
                   "folder": target.get("folder"), "label": target.get("label")},
        "summary": _summarize(job, result),
        "final_text": result.get("final_text"),
        "exit_code": result.get("exit_code"),
        "num_turns": result.get("num_turns"),
        "duration_ms": result.get("duration_ms"),
        # The gate audit: which mutating tools the agent tried and got DENIED.
        "denied_tools": result.get("denied_tools", []),
        "permission_denials": result.get("permission_denials", []),
        "gate": "read_only",
    }
    if result.get("error"):
        rec["error"] = result["error"]
    if result.get("stderr_tail"):
        rec["stderr_tail"] = result["stderr_tail"]
    return rec


def _summarize(job: Dict[str, Any], result: Dict[str, Any]) -> str:
    label = job_prompts.JOB_TYPES.get(job.get("type"), {}).get("label", job.get("type"))
    if not result.get("ok"):
        return f"{label} failed: {result.get('error') or 'agent error'}"
    text = (result.get("final_text") or "").strip()
    first = text.splitlines()[0] if text else ""
    return (first[:200] or f"{label} complete")


# --- the loop ----------------------------------------------------------------
async def run_loop(stop: "asyncio.Event") -> None:
    """Poll ~POLL_S, atomic-claim the oldest queued job, dispatch it under the
    semaphore. Reconciles orphans on startup + each idle poll. Offloads the
    blocking claim/agent calls to threads so the event loop stays live."""
    db.init()
    sem = asyncio.Semaphore(MAX_PARALLEL)
    _mark(alive=True, started_at=_iso(), last_beat_at=_iso())
    # Startup reconcile: a job left 'running' by a previous crashed worker.
    try:
        await asyncio.to_thread(reconcile_orphans)
    except Exception:
        _log.exception("startup orphan reconcile failed (continuing)")

    tasks: set = set()
    while not stop.is_set():
        _mark(last_beat_at=_iso())
        claimed_any = False

        # R6: pick up any HUMAN-APPROVED code tasks (status='committing') and perform
        # the real commit. The HTTP /approve handler only set the intent; the worker is
        # the single writer of the commit. Done synchronously each tick (commits are
        # fast and serialized) BEFORE claiming new queued work.
        try:
            for cj in await asyncio.to_thread(db.list_committing):
                claimed_any = True
                await _commit_one(cj)
        except Exception:
            _log.exception("commit pickup pass failed (continuing)")

        # COST/BUDGET GATE (P1): refuse to claim NEW work when over the daily cap or
        # the kill switch is on. Enforced HERE (the trusted worker, outside the agent)
        # so a runaway run cannot bypass its own limit. Already-approved commits above
        # still proceed — they are human-gated. The gate is re-read each poll, so
        # raising the cap / removing the kill switch resumes claiming within ~POLL_S.
        gate = await asyncio.to_thread(costs.claim_gate)
        if gate["blocked"]:
            if not getattr(run_loop, "_gate_warned", False):
                _log.warning("cost gate: NOT claiming new jobs — %s", gate["reason"])
                run_loop._gate_warned = True  # type: ignore[attr-defined]
                try:
                    notify.notify("budget_breach",
                                  f"worker paused claiming new jobs — {gate['reason']}")
                except Exception:
                    pass
        else:
            if getattr(run_loop, "_gate_warned", False):
                _log.info("cost gate: cleared — resuming claims")
                run_loop._gate_warned = False  # type: ignore[attr-defined]
            # Claim up to the free semaphore capacity this tick (so a burst of queued
            # jobs all start within one poll instead of one-per-poll). `sem._value` is
            # the current free permit count; cap claims to it so we never over-claim
            # past MAX_PARALLEL.
            free = max(1, getattr(sem, "_value", MAX_PARALLEL))
            for _ in range(free):
                job = await asyncio.to_thread(db.claim_next)
                if job is None:
                    break
                claimed_any = True
                _bump("claimed")
                t = asyncio.create_task(_run_one(job, sem))
                tasks.add(t)
                t.add_done_callback(tasks.discard)

        # Periodic reconcile for long-lived orphans (cheap; a single query).
        try:
            await asyncio.to_thread(reconcile_orphans)
        except Exception:
            _log.exception("periodic orphan reconcile failed (continuing)")

        if not claimed_any:
            # Idle — wait a poll or until stop.
            try:
                await asyncio.wait_for(stop.wait(), timeout=POLL_S)
                break
            except asyncio.TimeoutError:
                pass
        else:
            # Busy — short yield so we re-poll promptly but don't hot-spin.
            try:
                await asyncio.wait_for(stop.wait(), timeout=min(POLL_S, 0.5))
                break
            except asyncio.TimeoutError:
                pass

    # Drain in-flight jobs on shutdown so they reach a terminal state (best-effort).
    if tasks:
        with contextlib.suppress(Exception):
            await asyncio.wait(tasks, timeout=5.0)
    _mark(alive=False, last_beat_at=_iso())


# --- supervisor (restart the loop if it dies under a live uvicorn) -----------
SUPERVISOR_BACKOFF_S = float(os.environ.get("STANDUP_JOB_SUPERVISOR_BACKOFF_S", "5"))
SUPERVISOR_BACKOFF_MAX_S = float(os.environ.get("STANDUP_JOB_SUPERVISOR_BACKOFF_MAX_S", "60"))


async def supervise(stop: "asyncio.Event") -> None:
    """Run run_loop under supervision: a clean shutdown (stop set) ends it; a
    return/raise while stop is NOT set means it died — log, count, restart with
    backoff. Mirrors scheduler.supervise so a dead worker under a live uvicorn is
    revived, not silently gone."""
    backoff = SUPERVISOR_BACKOFF_S
    while not stop.is_set():
        try:
            await run_loop(stop)
            if stop.is_set():
                return
            _log.error("jobworker run_loop returned unexpectedly (stop not set) — restarting")
        except asyncio.CancelledError:
            _mark(alive=False, last_beat_at=_iso())
            raise
        except Exception:
            _log.exception("jobworker run_loop CRASHED — restarting under supervisor")
        _mark(alive=False)
        if stop.is_set():
            return
        _bump("restarts")
        try:
            await asyncio.wait_for(stop.wait(), timeout=backoff)
            return
        except asyncio.TimeoutError:
            pass
        backoff = min(backoff * 2, SUPERVISOR_BACKOFF_MAX_S)


# --- small helpers -----------------------------------------------------------
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


def _dumps(obj: Dict[str, Any]) -> str:
    return _json.dumps(obj, ensure_ascii=False)
