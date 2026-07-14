"""Cost / budget accounting for board jobs (v0.2 — the P1 gap from the gap analysis).

Per-job cost is captured by agent_run (`total_cost_usd` from the `claude -p` JSON) and
persisted in the job's result_json (-> job["result"]["cost_usd"]). This module sums it
per-day and exposes a CLAIM GATE the worker checks before taking NEW work, plus a kill
switch. Enforcement lives OUTSIDE the agent (in the trusted worker), so a runaway run
cannot bypass its own limit — the design rule from the gateway/governance literature.

Config:
  - env STANDUP_DAILY_COST_CAP_USD : a daily $ cap (unset/empty = no cap).
  - file control/kill_switch       : present = stop claiming new work (a hard stop).
"""
from __future__ import annotations

import datetime as _dt
import os
from typing import Any, Dict, List, Optional

from . import db, paths


def _today() -> str:
    return _dt.date.today().isoformat()


def cap_usd() -> Optional[float]:
    """The daily $ cap. control/budget.json {"daily_cap_usd": N} (runtime, no restart)
    takes precedence; else the STANDUP_DAILY_COST_CAP_USD env var; else None (no cap)."""
    import json
    try:
        raw = json.loads((paths.control_dir() / "budget.json").read_text(encoding="utf-8"))
        v = raw.get("daily_cap_usd") if isinstance(raw, dict) else None
        if v is not None:
            return float(v)
    except (OSError, ValueError, TypeError):
        pass
    v = os.environ.get("STANDUP_DAILY_COST_CAP_USD")
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def kill_switch_on() -> bool:
    return (paths.control_dir() / "kill_switch").exists()


def _job_cost(job: Dict[str, Any]) -> float:
    r = job.get("result")
    c = r.get("cost_usd") if isinstance(r, dict) else None
    try:
        return float(c) if c is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def daily_total(date_str: Optional[str] = None,
                jobs: Optional[List[Dict[str, Any]]] = None) -> float:
    """Sum cost_usd across jobs finished on date_str (default today)."""
    date_str = date_str or _today()
    jobs = jobs if jobs is not None else db.list_jobs(limit=1000)
    total = 0.0
    for j in jobs:
        fin = j.get("finished_at") or j.get("updated_at") or ""
        if isinstance(fin, str) and fin.startswith(date_str):
            total += _job_cost(j)
    return round(total, 6)


def summary(date_str: Optional[str] = None) -> Dict[str, Any]:
    """Today's budget state: spent / cap / remaining / over_cap / kill_switch / blocked."""
    date_str = date_str or _today()
    jobs = db.list_jobs(limit=1000)
    spent = daily_total(date_str, jobs=jobs)
    cap = cap_usd()
    killed = kill_switch_on()
    over = cap is not None and spent >= cap
    return {
        "date": date_str,
        "spent_usd": spent,
        "cap_usd": cap,
        "remaining_usd": (round(cap - spent, 6) if cap is not None else None),
        "over_cap": bool(over),
        "kill_switch": killed,
        "blocked": bool(over or killed),
        "jobs_today": sum(1 for j in jobs
                          if str(j.get("finished_at") or "").startswith(date_str)),
    }


def claim_gate() -> Dict[str, Any]:
    """Should the worker REFUSE to claim NEW work right now? Already-approved commits
    proceed regardless (they're human-gated). Returns {blocked, reason}."""
    s = summary()
    if s["kill_switch"]:
        return {"blocked": True, "reason": "kill switch on (control/kill_switch present)"}
    if s["over_cap"]:
        return {"blocked": True,
                "reason": f"daily cost cap ${s['cap_usd']} reached (spent ${s['spent_usd']})"}
    return {"blocked": False, "reason": ""}


def per_job(limit: int = 20, date_str: Optional[str] = None) -> List[Dict[str, Any]]:
    """Recent jobs with their cost (newest first), optionally filtered to a date."""
    out: List[Dict[str, Any]] = []
    for j in db.list_jobs(limit=limit):
        stamp = str(j.get("finished_at") or j.get("created_at") or "")
        if date_str and not stamp.startswith(date_str):
            continue
        out.append({"id": j.get("id"), "type": j.get("type"), "status": j.get("status"),
                    "cost_usd": _job_cost(j), "finished_at": j.get("finished_at")})
    return out
