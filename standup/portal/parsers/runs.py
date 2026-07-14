"""control/runs/<run_id>.json — the scheduler's run HISTORY (one file per fire).

WHY THIS EXISTS
---------------
The old in-app `CronCreate` scheduler left no durable record of whether a tick
fired, completed, or failed. Mission Control had nothing real to show — the
"next tick" countdown was inferred from artifact mtimes, and a missed fire (like
today's 02:27 + 08:00) was invisible until someone noticed the missing log.

This module is the single writer/reader of a small append-only-ish history: every
scheduled (or portal-triggered, via the same path) fire records ONE file:

    control/runs/<run_id>.json
      { run_id, tick, source, started_at, finished_at, status,
        worked, green, committed, prs, log_ref, note, lock, exit_code }

  status ∈ scheduled|running|done|failed|skipped|error
    scheduled  the fire was recorded but not yet started (rare; transient)
    running    the headless tick process is in flight (lock held)
    skipped    single-flight refused it (another tick already running) — NOT an
               error: this is the lock doing its job
    done       the tick process exited 0 and wrote its artifacts
    failed     the tick process exited non-zero
    error      the scheduler itself could not launch the process

The portal READS these for GET /api/runs (history) and for the live "running"
state on /api/status. Writes are atomic (temp + os.replace) so a reader never
sees a half-written record. Stdlib-only.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import paths


def runs_dir() -> Path:
    """control/runs/ — one JSON file per fire. Created lazily on first write.
    Layout lives in parsers.paths (the single source of the dir layout)."""
    return paths.runs_dir()


def _now() -> _dt.datetime:
    return _dt.datetime.now()


def _iso(dt: Optional[_dt.datetime] = None) -> str:
    # tz-aware emit (local offset at the boundary) so the history timestamps
    # line up with everything else the portal surfaces (results/, BACKLOG).
    return (dt or _now()).astimezone().isoformat(timespec="seconds")


def _run_path(run_id: str) -> Path:
    # run_id is machine-generated (wf_… / sched-…); keep the filename safe.
    safe = "".join(c for c in run_id if c.isalnum() or c in "-_.") or "run"
    return runs_dir() / f"{safe}.json"


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-run-", suffix=".json")
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


def record(
    run_id: str,
    tick: str,
    status: str,
    *,
    source: str = "scheduler",
    started_at: Optional[str] = None,
    finished_at: Optional[str] = None,
    worked: Optional[int] = None,
    green: Optional[int] = None,
    committed: Optional[int] = None,
    prs: Optional[int] = None,
    log_ref: Optional[str] = None,
    note: Optional[str] = None,
    exit_code: Optional[int] = None,
    workflow_run_id: Optional[str] = None,
    lock: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Write (or overwrite) the history record for ``run_id``. Returns the record.

    Called at each lifecycle transition: start (status="running"), completion
    (status="done"|"failed"), or a single-flight refusal (status="skipped"). The
    record is keyed by run_id, so a later transition overwrites the earlier one —
    a run has exactly one file that reflects its latest state.
    """
    existing = _read_json(_run_path(run_id)) or {}
    rec: Dict[str, Any] = {
        "run_id": run_id,
        "tick": tick,
        "source": source,
        "status": status,
        "started_at": started_at if started_at is not None else existing.get("started_at"),
        "finished_at": finished_at if finished_at is not None else existing.get("finished_at"),
        "worked": worked if worked is not None else existing.get("worked"),
        "green": green if green is not None else existing.get("green"),
        "committed": committed if committed is not None else existing.get("committed"),
        "prs": prs if prs is not None else existing.get("prs"),
        "log_ref": log_ref if log_ref is not None else existing.get("log_ref"),
        "note": note if note is not None else existing.get("note"),
        "exit_code": exit_code if exit_code is not None else existing.get("exit_code"),
        # The REAL Workflow-tool run_id the headless claude -p hosted (a pointer to
        # the actual workflow run + its log section), distinct from this record's own
        # scheduler run_id. None until a real fire scrapes it from the claude reply.
        "workflow_run_id": workflow_run_id if workflow_run_id is not None else existing.get("workflow_run_id"),
        "lock": lock if lock is not None else existing.get("lock"),
        "recorded_at": _iso(),
    }
    _atomic_write_json(_run_path(run_id), rec)
    return rec


def get(run_id: str) -> Optional[Dict[str, Any]]:
    return _read_json(_run_path(run_id))


def list_runs(limit: int = 50) -> List[Dict[str, Any]]:
    """All run records, newest-first (by started_at, then recorded_at)."""
    d = runs_dir()
    out: List[Dict[str, Any]] = []
    if not d.exists():
        return out
    for p in sorted(d.glob("*.json")):
        if p.name.startswith(".tmp-"):
            continue
        rec = _read_json(p)
        if rec and rec.get("run_id"):
            out.append(rec)
    out.sort(
        key=lambda r: (r.get("started_at") or "", r.get("recorded_at") or ""),
        reverse=True,
    )
    return out[:limit]


def latest_running() -> Optional[Dict[str, Any]]:
    """The most recent record still in `running` state, if any (live tick)."""
    for rec in list_runs(limit=20):
        if rec.get("status") == "running":
            return rec
    return None
