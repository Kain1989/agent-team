"""Parse a per-dev <folder>/.standup/<dev>.md file.

The dev's current task = the LAST dated ``## YYYY-MM-DD — <title>`` entry. We
return that entry's title plus a best-effort ``current_task`` / ``next_step``
pulled from ``### Current state`` / ``### Next step`` subsections (with sensible
fallbacks: the first non-empty bullet/line of the entry body).

Tolerant: a missing file / no dated entry degrades to a None current_task and a
warning; never raises.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from . import paths

_DATE_HEAD_RE = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})\s*[—–-]?\s*(.*)$")
_SUB_RE = re.compile(r"^###\s+(.*)$")


def _first_meaningful(body_lines: List[str], limit: int = 240) -> Optional[str]:
    for ln in body_lines:
        s = ln.strip().lstrip("-*0123456789. ").strip()
        if s and not s.startswith("#"):
            return s[:limit]
    return None


def _sub_section_text(body_lines: List[str], name_substrings) -> Optional[str]:
    """Return the joined text of the first ### subsection whose heading contains
    any of ``name_substrings`` (case-insensitive)."""
    want = [w.lower() for w in name_substrings]
    collecting = False
    collected: List[str] = []
    for ln in body_lines:
        m = _SUB_RE.match(ln.strip())
        if m:
            head = m.group(1).lower()
            if collecting:
                break  # next subsection -> stop
            if any(w in head for w in want):
                collecting = True
            continue
        if collecting and ln.strip():
            collected.append(ln.strip().lstrip("-*0123456789. ").strip())
    if not collected:
        return None
    text = " ".join(c for c in collected if c)
    return text[:300] if text else None


def parse_text(text: str) -> Dict[str, Any]:
    lines = text.splitlines()
    entries: List[Dict[str, Any]] = []
    for i, line in enumerate(lines):
        m = _DATE_HEAD_RE.match(line.strip())
        if m:
            entries.append({"i": i, "date": m.group(1), "title": (m.group(2) or "").strip()})

    if not entries:
        return {
            "last_entry": None,
            "current_task": None,
            "current_state": None,
            "next_step": None,
        }

    last = entries[-1]
    start = last["i"]
    end = len(lines)
    body = lines[start + 1 : end]

    current = _sub_section_text(body, ["current state", "state", "task", "what i did"])
    nxt = _sub_section_text(body, ["next step", "next", "handoff"])
    summary = current or _first_meaningful(body)

    return {
        "last_entry": {"date": last["date"], "title": last["title"]},
        "current_task": (last["title"] or summary or "")[:240] or None,
        "current_state": current,
        "next_step": nxt,
    }


def parse(folder: str, dev_id: str, path=None) -> Dict[str, Any]:
    p = path or paths.dev_standup_file(folder, dev_id)
    out: Dict[str, Any] = {
        "dev_id": dev_id,
        "folder": folder,
        "last_entry": None,
        "current_task": None,
        "current_state": None,
        "next_step": None,
        "_parse_warnings": [],
        "_ok": True,
        "_path": str(p),
    }
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        out["_ok"] = False
        out["_parse_warnings"].append(f"dev standup file unreadable: {exc}")
        return out

    parsed = parse_text(text)
    out.update(
        {
            "last_entry": parsed["last_entry"],
            "current_task": parsed["current_task"],
            "current_state": parsed["current_state"],
            "next_step": parsed["next_step"],
        }
    )
    if parsed["last_entry"] is None:
        out["_parse_warnings"].append("no dated entry found")
    return out
