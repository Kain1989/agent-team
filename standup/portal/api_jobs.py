"""Job API for the interactive Mission Control board (Slice 1).

The 4 endpoints the frontend's Jobs controller posts to, mounted by app.py on the
SAME FastAPI app so they inherit the loopback bind, TrustedHost allow-list, and the
`_csrf_ok` guard. Conventions are lifted verbatim from the existing /api/actions/*
handlers: the `X-Requested-By: portal` / `X-Idempotency-Key` headers, the 202|409
shapes, JSONResponse.

  POST /api/jobs            {type, target, prompt[, review_kind]}  -> 202 {id, job}
  GET  /api/jobs            ?status=&type=&limit=                  -> 200 {jobs, counts}
  GET  /api/jobs/{id}                                              -> 200 job (+diff_text) | 404
  POST /api/jobs/{id}/cancel                                       -> 202 {ok, job} | 409|404
  POST /api/jobs/{id}/approve  (code_task HITL)                    -> 202 {ok, job} | 409|404
  POST /api/jobs/{id}/reject   (code_task HITL)                    -> 202 {ok, job} | 409|404

HTTP handlers only ENQUEUE (create) or set an INTENT. The WORKER is the single writer
of consequential state transitions:
  - /approve sets awaiting_approval->committing (R6); the WORKER does the actual git
    commit on pickup. The handler NEVER commits.
  - /reject sets awaiting_approval->rejected (terminal) and tears down the worktree (a
    safe, idempotent git op — not a job-state write).
  - the one safe fast path: cancelling a job that is still 'queued' (never claimed) is
    applied immediately via the atomic transition(queued->cancelled), which races
    cleanly against a worker claim (whoever wins the WHERE-status clause wins).
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Header, Query, Request
from fastapi.responses import JSONResponse

from parsers import db, job_prompts, paths
from parsers import team as team_mod

router = APIRouter()


# --- CSRF (the SAME guard app.py applies to /api/actions) --------------------
# Imported lazily from app at call time to avoid an import cycle (app imports this
# router). We re-implement the tiny accept rule here so the router is self-contained
# and testable, matching app._csrf_ok exactly.
_ALLOWED_CSRF_HOSTS = {"127.0.0.1", "localhost"}


def _host_of(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    from urllib.parse import urlsplit
    try:
        return (urlsplit(url).hostname or "").lower() or None
    except ValueError:
        return None


def _csrf_ok(request: Request) -> bool:
    if (request.headers.get("x-requested-by") or "").strip().lower() == "portal":
        return True
    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
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
         "reason": ("Rejected — this POST came from a foreign origin. The job "
                    "endpoints accept same-origin (127.0.0.1/localhost) requests only.")},
        status_code=403,
    )


def _idem_key(header: Optional[str], body: Optional[Dict[str, Any]]) -> Optional[str]:
    if header:
        return header.strip() or None
    if isinstance(body, dict) and body.get("idempotency_key"):
        return str(body["idempotency_key"]).strip() or None
    return None


def _team():
    return team_mod.parse(paths.team_json())


def _require_separate_approver() -> bool:
    """Opt-in separation of duties: control/policy.json {"require_separate_approver":
    true} or env STANDUP_REQUIRE_SEPARATE_APPROVER=1. Default False."""
    import json
    import os
    try:
        raw = json.loads((paths.control_dir() / "policy.json").read_text(encoding="utf-8"))
        if isinstance(raw, dict) and "require_separate_approver" in raw:
            return bool(raw["require_separate_approver"])
    except (OSError, ValueError):
        pass
    return os.environ.get("STANDUP_REQUIRE_SEPARATE_APPROVER") == "1"


# --- POST /api/jobs ----------------------------------------------------------
@router.post("/api/jobs")
def create(
    request: Request,
    x_idempotency_key: Optional[str] = Header(None),
    body: Optional[Dict[str, Any]] = Body(None),
):
    """Enqueue a read-only job. 202 + {queued, id, job, idempotent?} on accept; 409
    (validation/guard) or 403 (CSRF) otherwise. Idempotent on X-Idempotency-Key."""
    if not _csrf_ok(request):
        return _csrf_block()
    body = body or {}

    jtype = (body.get("type") or "").strip()
    if not job_prompts.is_known_type(jtype):
        return JSONResponse(
            {"queued": False, "code": "bad_type",
             "reason": f"unknown job type {jtype!r}; "
                       f"expected one of {sorted(job_prompts.JOB_TYPES)}"},
            status_code=409,
        )

    prompt = (body.get("prompt") or "").strip()
    # A directive / review focus / analysis task must carry operator text. (A review
    # can default its focus, but we still require SOME instruction so an empty submit
    # never spins an agent on nothing.)
    if not prompt:
        return JSONResponse(
            {"queued": False, "code": "empty_prompt",
             "reason": "prompt is required (the directive body / review focus / task)."},
            status_code=409,
        )

    # INPUT GUARDRAIL (P5): reject oversized / configured-denied input before it enqueues.
    from parsers import guardrails
    gin = guardrails.check_input(prompt)
    if not gin["ok"]:
        return JSONResponse(
            {"queued": False, "code": gin.get("code", "input_rejected"), "reason": gin["reason"]},
            status_code=409,
        )

    # `target` may be given as a flat string ("dev:<id>" / "project:<folder>"
    # / "broadcast") OR as the explicit fields target_kind+target_id. Resolve both.
    target_kind, target_id = _parse_target(body)
    review_kind = (body.get("review_kind") or None)
    if jtype == "trigger-review":
        review_kind = (review_kind or "pm").lower()
        if review_kind not in job_prompts.JOB_TYPES["trigger-review"]["review_kinds"]:
            return JSONResponse(
                {"queued": False, "code": "bad_review_kind",
                 "reason": f"review_kind must be one of "
                           f"{job_prompts.JOB_TYPES['trigger-review']['review_kinds']}"},
                status_code=409,
            )
    else:
        review_kind = None

    # Resolve + validate the target against team.json (a typo never silently runs).
    target = job_prompts.resolve_target(_team(), target_kind, target_id)
    if not target.get("ok"):
        return JSONResponse(
            {"queued": False, "code": "bad_target",
             "reason": target.get("reason") or "target could not be resolved"},
            status_code=409,
        )

    exec_path = job_prompts.execution_path_for(jtype)
    # CODE-TASK precondition (§0/§D): the target must resolve to a real git repo so a
    # branch+diff+commit is possible. Surface it HONESTLY at create (never a silent
    # no-op): a non-git / repo-less target -> 409 target_not_git. (The worker re-checks
    # + fetches origin; this is the fast fail so the operator gets an immediate reason.)
    if exec_path == "code_task":
        gate = _code_task_target_ok(target)
        if not gate["ok"]:
            return JSONResponse(
                {"queued": False, "code": gate["code"], "reason": gate["reason"]},
                status_code=409,
            )

    idem = _idem_key(x_idempotency_key, body)
    job = db.create_job(
        type=jtype,
        target_kind=target.get("kind"),
        target_id=target.get("id"),
        target_folder=target.get("folder"),
        prompt=prompt,
        review_kind=review_kind,
        execution_path=exec_path,
        idempotency_key=idem,
    )
    return JSONResponse(
        {"queued": True, "idempotent": bool(job.get("idempotent")),
         "id": job["id"], "job": job},
        status_code=202,
    )


def _code_task_target_ok(target: Dict[str, Any]) -> Dict[str, Any]:
    """A code_task needs a resolved folder that is a real git repo (so a branch + diff
    + commit is possible). Returns {ok:True} or {ok:False, code, reason}. `code` is
    'target_not_git' (mirrors the workflow's isGit branch + spec §0). The worker also
    fetches origin + resolves the default branch; this is the FAST create-time check."""
    import os
    import subprocess
    folder = target.get("folder")
    if not folder:
        return {"ok": False, "code": "target_not_git",
                "reason": ("a code task needs a project/dev target with a repo folder — "
                           "broadcast / squad targets have no repo to branch from")}
    repo_root = str(paths.workspace_root() / folder)
    if not os.path.isdir(repo_root):
        return {"ok": False, "code": "target_not_git",
                "reason": f"target folder does not exist on this box: {folder!r}"}
    try:
        p = subprocess.run(["git", "-C", repo_root, "rev-parse", "--is-inside-work-tree"],
                           capture_output=True, text=True, timeout=15)
        if p.returncode != 0 or p.stdout.strip() != "true":
            return {"ok": False, "code": "target_not_git",
                    "reason": (f"target {folder!r} is not a git repo — a code task "
                               "produces a branch/diff/commit, which needs a repo + "
                               "remote (run `git init` + add an origin to enable)")}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "code": "target_not_git",
                "reason": f"could not check {folder!r} is a git repo: {exc}"}
    return {"ok": True}


def _parse_target(body: Dict[str, Any]):
    """Accept either explicit target_kind/target_id or a flat `target` string.

    Flat forms: 'broadcast' | 'project:<folder>' | 'squad:<id>' | 'dev:<id>' |
    'staff:<id>' | '<id>' (kind inferred). Returns (kind, id)."""
    tk = body.get("target_kind")
    ti = body.get("target_id")
    if tk or ti:
        return (tk, ti)
    raw = body.get("target")
    if not raw:
        return (None, None)
    raw = str(raw).strip()
    if raw == "broadcast":
        return ("broadcast", None)
    if ":" in raw:
        kind, _, rest = raw.partition(":")
        return (kind.strip() or None, rest.strip() or None)
    return (None, raw)  # bare id; resolver infers dev/staff


# --- GET /api/jobs -----------------------------------------------------------
@router.get("/api/jobs")
def list_jobs(
    status: Optional[str] = Query(None, description="filter; comma-separated set ok"),
    type: Optional[str] = Query(None, description="filter by job type"),
    limit: int = Query(100, ge=1, le=500),
):
    """Jobs newest-first, optionally filtered, plus a per-status counts block."""
    jobs = db.list_jobs(status=status, type=type, limit=limit)
    # Trim the heavy result_json/final_text/diff_text out of the LIST view (the per-job
    # GET returns the full record incl. the diff); keep the summary + the small
    # code-task fields the board needs to render an approval card without a per-job
    # fetch (branch, commit_sha, has_diff, denied_tools).
    for j in jobs:
        res = j.get("result") or {}
        j["summary"] = res.get("summary")
        j["denied_tools"] = res.get("denied_tools", [])
        j["commit_sha"] = res.get("commit_sha")
        j["has_diff"] = bool(j.get("diff_text"))
        j.pop("diff_text", None)        # the diff body is fetched on expand, not in the list
        j.pop("result_json", None)
        j.pop("result", None)
    return JSONResponse({"jobs": jobs, "counts": db.counts()})


# --- GET /api/jobs/{id} ------------------------------------------------------
@router.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    """Poll one job — the FULL record incl. result (verdict + permission_denials)."""
    job = db.get(job_id)
    if job is None:
        return JSONResponse({"error": "no such job", "id": job_id}, status_code=404)
    return JSONResponse(job)


# --- POST /api/jobs/{id}/cancel ----------------------------------------------
@router.post("/api/jobs/{job_id}/cancel")
def cancel(job_id: str, request: Request):
    """Request cancellation. A 'queued' job is cancelled IMMEDIATELY (atomic
    queued->cancelled, racing a worker claim cleanly); a 'running' job gets a
    cooperative cancel intent the worker honors at its next checkpoint. A terminal
    job -> 409. Unknown -> 404."""
    if not _csrf_ok(request):
        return _csrf_block()
    job = db.get(job_id)
    if job is None:
        return JSONResponse({"error": "no such job", "id": job_id}, status_code=404)
    if job["status"] in db.TERMINAL:
        return JSONResponse(
            {"ok": False, "code": "terminal",
             "reason": f"job is already {job['status']}; cannot cancel a finished job",
             "job": job},
            status_code=409,
        )

    now = _dt.datetime.now()
    # Fast path: a still-queued job was never claimed — cancel it now (atomic; if a
    # worker claims it in the same instant, its claim flips queued->running and our
    # queued->cancelled no-ops, so we re-read and fall through to the intent path).
    if job["status"] == "queued":
        if db.transition(job_id, "queued", "cancelled", now=now,
                         fields={"finished_at": now.astimezone().isoformat(timespec="seconds"),
                                 "result_json": '{"ok": false, "cancelled": true, '
                                                '"note": "cancelled while queued (never started)"}'}):
            return JSONResponse({"ok": True, "job": db.get(job_id)}, status_code=202)

    # Running (or just-claimed): set the cooperative cancel intent; the worker
    # transitions it to cancelled at its next checkpoint.
    updated = db.request_cancel(job_id, now=now)
    return JSONResponse({"ok": True, "cancel_requested": True, "job": updated}, status_code=202)


# --- POST /api/jobs/{id}/approve (code_task HITL) ----------------------------
@router.post("/api/jobs/{job_id}/approve")
def approve(job_id: str, request: Request, body: Optional[Dict[str, Any]] = Body(None)):
    """HUMAN-GATED APPROVE for a code_task awaiting approval (R6). This handler ONLY
    sets the committing INTENT (awaiting_approval -> committing) + stamps who/when; the
    WORKER is the single writer of the actual git commit (it picks up 'committing' rows
    and commits the staged edits on the job branch — never pushes, never merges).

    202 {ok, job} on accept (now committing); 409 if the job is not awaiting_approval
    (idempotent — a double-approve loses the atomic race and reports the current state);
    404 if unknown. Separation of duties (approver≠creator) is OPT-IN via the
    require_separate_approver policy (control/policy.json / env); default off so the
    single-operator setup works, on for orgs that need no self-approval (409 same_approver)."""
    if not _csrf_ok(request):
        return _csrf_block()
    job = db.get(job_id)
    if job is None:
        return JSONResponse({"error": "no such job", "id": job_id}, status_code=404)
    if job["status"] != "awaiting_approval":
        return JSONResponse(
            {"ok": False, "code": "not_awaiting",
             "reason": (f"job is {job['status']}, not awaiting_approval — only a code "
                        "task parked for review can be approved"),
             "job": job},
            status_code=409,
        )
    approved_by = "portal"
    if isinstance(body, dict) and body.get("approved_by"):
        approved_by = str(body["approved_by"])[:64]
    # INTENT-AUTH (P7, opt-in): when separation of duties is required, the approver must
    # NOT be the job's creator. Policy: control/policy.json {"require_separate_approver":
    # true} or env STANDUP_REQUIRE_SEPARATE_APPROVER=1. Default OFF (single-operator works).
    if _require_separate_approver() and approved_by == (job.get("created_by") or ""):
        return JSONResponse(
            {"ok": False, "code": "same_approver",
             "reason": (f"approver '{approved_by}' is the job's creator — separation of "
                        "duties is required (pass a different approved_by, or disable the "
                        "require_separate_approver policy)"),
             "job": job},
            status_code=409,
        )
    ok = db.request_approve(job_id, approved_by=approved_by)
    if not ok:
        # Lost the race (a concurrent approve/reject already moved it).
        return JSONResponse(
            {"ok": False, "code": "not_awaiting",
             "reason": "job is no longer awaiting_approval (already approved/rejected)",
             "job": db.get(job_id)},
            status_code=409,
        )
    return JSONResponse({"ok": True, "committing": True, "job": db.get(job_id)},
                        status_code=202)


# --- POST /api/jobs/{id}/reject (code_task HITL) -----------------------------
@router.post("/api/jobs/{job_id}/reject")
def reject(job_id: str, request: Request):
    """HUMAN REJECT for a code_task awaiting approval: discard the diff. Atomically
    flips awaiting_approval -> rejected (terminal), then tears down the isolated
    worktree + deletes the branch (a safe, idempotent git op). 202 {ok, job}; 409 if
    not awaiting_approval; 404 if unknown. Idempotent — a second reject loses the race
    and reports the current state."""
    if not _csrf_ok(request):
        return _csrf_block()
    job = db.get(job_id)
    if job is None:
        return JSONResponse({"error": "no such job", "id": job_id}, status_code=404)
    if job["status"] != "awaiting_approval":
        return JSONResponse(
            {"ok": False, "code": "not_awaiting",
             "reason": (f"job is {job['status']}, not awaiting_approval — only a code "
                        "task parked for review can be rejected"),
             "job": job},
            status_code=409,
        )
    ok = db.request_reject(job_id)
    if not ok:
        return JSONResponse(
            {"ok": False, "code": "not_awaiting",
             "reason": "job is no longer awaiting_approval (already approved/rejected)",
             "job": db.get(job_id)},
            status_code=409,
        )
    # Tear down the worktree + branch (best-effort; the job is already rejected). The
    # repo_root lives in result_json; worktree_path/branch are columns.
    res = job.get("result") or {}
    repo_root = res.get("repo_root") if isinstance(res, dict) else None
    wt = job.get("worktree_path")
    branch = job.get("branch")
    if repo_root and wt:
        from parsers import worktree as _wt
        cleanup = _wt.remove(repo_root, wt, branch)
        # Clear the worktree pointer now that it's gone (best-effort; non-fatal).
        try:
            db._conn().execute(
                "UPDATE jobs SET worktree_path=NULL, updated_at=? WHERE id=?;",
                (_dt.datetime.now().astimezone().isoformat(timespec="seconds"), job_id))
            db._conn().commit()
        except Exception:
            pass
        return JSONResponse({"ok": True, "rejected": True, "cleanup": cleanup,
                             "job": db.get(job_id)}, status_code=202)
    return JSONResponse({"ok": True, "rejected": True, "job": db.get(job_id)},
                        status_code=202)
