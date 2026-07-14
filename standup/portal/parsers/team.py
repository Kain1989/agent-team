"""Parse team.json -> roster (squads + active devs + staff + bench).

Tolerant: a malformed team.json degrades to an empty roster with a parse
warning rather than raising.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from . import paths


def _dev_view(d: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": d.get("id"),
        "role": d.get("role"),
        "folder": d.get("folder"),
        "stack": d.get("stack"),
        "focus": d.get("focus"),
        "pair": d.get("pair"),
        "active": bool(d.get("active", False)),
        "git": bool(d.get("git", False)),
        "branch": d.get("branch"),
        "context": d.get("context"),
    }


def parse(path=None) -> Dict[str, Any]:
    """Return {squads, staff, bench, manager, _parse_warnings, _ok}.

    squads: [{id, name, mission, devs:[dev_view...]}]
    staff:  [{id, role, folder, note, focus}]
    bench:  [dev_view...]
    """
    p = path or paths.team_json()
    out: Dict[str, Any] = {
        "squads": [],
        "staff": [],
        "bench": [],
        "manager": None,
        "_parse_warnings": [],
        "_ok": True,
        "_path": str(p),
    }

    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as exc:
        out["_ok"] = False
        out["_parse_warnings"].append(f"team.json unreadable: {exc}")
        return out

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        out["_ok"] = False
        out["_parse_warnings"].append(f"team.json invalid JSON: {exc}")
        # Degrade: surface the raw text length so the UI can show "raw/degraded".
        out["_raw_len"] = len(raw)
        return out

    # --- squads ---
    for team in data.get("teams", []) or []:
        devs = [_dev_view(d) for d in (team.get("developers") or [])]
        out["squads"].append(
            {
                "id": team.get("id"),
                "name": team.get("name"),
                "mission": team.get("mission"),
                "devs": devs,
            }
        )
    if not out["squads"]:
        out["_parse_warnings"].append("no teams[] found in team.json")

    # --- staff ---
    for s in data.get("staff", []) or []:
        out["staff"].append(
            {
                "id": s.get("id"),
                "role": s.get("role"),
                "folder": s.get("folder"),
                # staff may scope to specific folders (e.g. the design lead);
                # surface it so the UI can show what surface each staff owns.
                "scope": s.get("scope_folders") or s.get("scope"),
                "active": bool(s.get("active", False)),
                "focus": s.get("focus"),
                "note": s.get("note"),
            }
        )

    # --- bench ---
    out["bench"] = [_dev_view(d) for d in (data.get("bench") or [])]

    # --- manager (lightweight; cadence is useful for the liveness fallback) ---
    mgr = data.get("manager") or {}
    out["manager"] = {
        "name": mgr.get("name"),
        "cadence": (mgr.get("cadence") or {}),
    }

    return out


def active_dev_index(parsed: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Map dev_id -> {folder, role, squad_id} for all squad devs (active or not)."""
    idx: Dict[str, Dict[str, Any]] = {}
    for squad in parsed.get("squads", []):
        for dev in squad.get("devs", []):
            if dev.get("id"):
                idx[dev["id"]] = {
                    "folder": dev.get("folder"),
                    "role": dev.get("role"),
                    "squad_id": squad.get("id"),
                    "active": dev.get("active"),
                }
    # staff also have per-dev .standup files (design_lead, etc.)
    for s in parsed.get("staff", []):
        if s.get("id"):
            idx[s["id"]] = {
                "folder": s.get("folder"),
                "role": s.get("role"),
                "squad_id": None,
                "active": s.get("active"),
            }
    return idx
