"""Bridge (3): observe Claude Code NATIVE agent teams from the portal.

Reads the local state Claude Code writes for native agent teams so Mission Control can
show live teams + their shared task list alongside the job queue:
  - ~/.claude/teams/<team>/config.json  -> the team's members (name, agent_type, id)
  - ~/.claude/tasks/<team>/             -> the shared task list (one record per task)

The on-disk format is experimental + lightly documented, so every read is best-effort
and tolerant of missing/renamed fields. Read-only; never writes the native dirs.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List


def _claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"))


def _read_json(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _tasks_for(team: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    tdir = _claude_home() / "tasks" / team
    if not tdir.exists():
        return out
    for f in sorted(tdir.glob("*.json")):
        t = _read_json(f)
        if not isinstance(t, dict):
            continue
        assignee = t.get("assignee")
        at = t.get("assigned_teammate")
        if isinstance(at, dict):
            assignee = at.get("agent_type") or at.get("name") or assignee
        desc = (t.get("description") or t.get("task_description") or "").strip()
        out.append({
            "id": t.get("id") or f.stem,
            "status": t.get("status"),
            "description": desc[:140],
            "assignee": assignee,
            "depends_on": t.get("depends_on") or t.get("dependencies") or [],
        })
    return out


def list_teams() -> List[Dict[str, Any]]:
    """Live native agent teams + their members + shared tasks (newest dir first)."""
    teams: List[Dict[str, Any]] = []
    tdir = _claude_home() / "teams"
    if not tdir.exists():
        return teams
    for d in sorted(tdir.iterdir(), reverse=True):
        cfg = _read_json(d / "config.json")
        if not isinstance(cfg, dict):
            continue
        members = []
        for m in cfg.get("members", []) or []:
            if isinstance(m, dict):
                members.append({"name": m.get("name"), "agent_type": m.get("agent_type"),
                                "agent_id": m.get("agent_id") or m.get("id")})
        tasks = _tasks_for(d.name)
        teams.append({
            "team": d.name,
            "lead": cfg.get("lead") or cfg.get("session_id"),
            "members": members,
            "member_count": len(members),
            "tasks": tasks,
            "task_counts": {
                "total": len(tasks),
                "pending": sum(1 for t in tasks if t.get("status") == "pending"),
                "in_progress": sum(1 for t in tasks if t.get("status") in ("in_progress", "in-progress")),
                "completed": sum(1 for t in tasks if t.get("status") == "completed"),
            },
        })
    return teams


def summary() -> Dict[str, Any]:
    teams = list_teams()
    return {
        "teams": teams,
        "team_count": len(teams),
        "total_members": sum(t["member_count"] for t in teams),
        "total_tasks": sum(t["task_counts"]["total"] for t in teams),
    }


if __name__ == "__main__":  # `python3 native_teams.py` -> portal-independent status dump
    print(json.dumps(summary(), indent=2))
