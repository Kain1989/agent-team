"""Parse log/<date>.md into per-tick sections.

Each tick is a ``## MORNING|AFTERNOON|EVENING|NIGHT (HH:MM …)`` heading followed
by a ``**Run** wf_… · N agents · …tokens · …min`` line, a
``Team health: … (0 red / N yellow / M reported)`` line, and a
``Worked X / green Y / committed Z / PRs W`` line.

Sections appear newest-first within a day file (EVENING at the top, NIGHT at the
bottom). We return them in file order and expose the first (newest) as latest.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Dict, List, Optional

from . import paths

_TICK_NAMES = ("MORNING", "AFTERNOON", "EVENING", "NIGHT")

# "## EVENING (20:17 cron continue) — ..."
_TICK_HEAD_RE = re.compile(
    r"^##\s+(MORNING|AFTERNOON|EVENING|NIGHT)\b(?:[^()\n]*\((\d{1,2}:\d{2})[^)]*\))?",
    re.IGNORECASE,
)
# NB: we match against text with **bold** and `code` markers already stripped,
# so the marker is a plain "Run wf_… · N agents".
_RUN_RE = re.compile(
    r"\bRun\b\s+(wf_[0-9a-f-]+).*?(\d+)\s+agents?", re.IGNORECASE
)
_TOKENS_RE = re.compile(r"([0-9.]+[KM]?)\s+(?:subagent\s+)?tokens", re.IGNORECASE)
_MIN_RE = re.compile(r"~?\s*([0-9.]+)\s*min", re.IGNORECASE)
_HEALTH_RE = re.compile(
    r"Team health:\s*[^()\n]*\((\d+)\s*red\s*/\s*(\d+)\s*yellow\s*/\s*(\d+)\s*reported\)",
    re.IGNORECASE,
)
_COLOR_RE = re.compile(r"Team health:\s*[^\n]*?\b(GREEN|YELLOW|RED)\b", re.IGNORECASE)
_WGCP_RE = re.compile(
    r"Worked\s+(\d+)\s*/\s*green\s+(\d+)\s*/\s*committed\s+(\d+)\s*/\s*PRs?\s+(\d+)",
    re.IGNORECASE,
)


def _strip(s: str) -> str:
    return s.replace("**", "").replace("`", "")


def parse_text(text: str) -> Dict[str, Any]:
    lines = text.splitlines()
    # locate tick headings
    heads: List[Dict[str, Any]] = []
    for i, line in enumerate(lines):
        m = _TICK_HEAD_RE.match(line.strip())
        if m:
            heads.append(
                {
                    "i": i,
                    "name": m.group(1).upper(),
                    "time": m.group(2),
                    "header": line.strip(),
                }
            )

    ticks: List[Dict[str, Any]] = []
    for k, h in enumerate(heads):
        start = h["i"]
        end = heads[k + 1]["i"] if k + 1 < len(heads) else len(lines)
        body = "\n".join(lines[start:end])
        flat = _strip(body)

        tick: Dict[str, Any] = {
            "name": h["name"],
            "time": h["time"],
            "header": h["header"],
            "run_id": None,
            "agents": None,
            "tokens": None,
            "duration_min": None,
            "color": None,
            "counts": {"red": None, "yellow": None, "reported": None},
            "worked": None,
            "green": None,
            "committed": None,
            "prs": None,
        }

        m = _RUN_RE.search(flat)
        if m:
            tick["run_id"] = m.group(1)
            tick["agents"] = int(m.group(2))
        m = _TOKENS_RE.search(flat)
        if m:
            tick["tokens"] = m.group(1)
        m = _MIN_RE.search(flat)
        if m:
            try:
                tick["duration_min"] = float(m.group(1))
            except ValueError:
                pass
        m = _HEALTH_RE.search(flat)
        if m:
            tick["counts"] = {
                "red": int(m.group(1)),
                "yellow": int(m.group(2)),
                "reported": int(m.group(3)),
            }
        m = _COLOR_RE.search(flat)
        if m:
            tick["color"] = m.group(1).lower()
        m = _WGCP_RE.search(flat)
        if m:
            tick["worked"] = int(m.group(1))
            tick["green"] = int(m.group(2))
            tick["committed"] = int(m.group(3))
            tick["prs"] = int(m.group(4))

        ticks.append(tick)

    return {"ticks": ticks}


def parse(date_str: Optional[str] = None, path=None) -> Dict[str, Any]:
    """Parse one day's log. ``date_str`` defaults to today; if today's file is
    missing we fall back to the newest log file on disk so the portal still shows
    the last-known tick when the runner hasn't produced today's file yet.
    """
    out: Dict[str, Any] = {
        "date": date_str,
        "ticks": [],
        "latest": None,
        "_parse_warnings": [],
        "_ok": True,
        "_path": None,
        "_fell_back": False,
    }

    if path is not None:
        p = path
        out["date"] = date_str
    else:
        ds = date_str or _dt.date.today().isoformat()
        p = paths.log_for(ds)
        out["date"] = ds
        if not p.exists():
            newest = _newest_log()
            if newest is not None:
                p = newest
                out["_fell_back"] = True
                out["date"] = newest.stem
                out["_parse_warnings"].append(
                    f"requested log {ds} missing; fell back to {newest.name}"
                )

    out["_path"] = str(p)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        out["_ok"] = False
        out["_parse_warnings"].append(f"log unreadable: {exc}")
        return out

    parsed = parse_text(text)
    out["ticks"] = parsed["ticks"]
    if parsed["ticks"]:
        # File order is newest-first, so the first tick is the latest.
        out["latest"] = parsed["ticks"][0]
    else:
        out["_parse_warnings"].append("no tick sections found in log")
    return out


def _newest_log() -> Optional["paths.Path"]:
    d = paths.log_dir()
    if not d.exists():
        return None
    files = sorted(
        (f for f in d.glob("*.md") if re.match(r"^\d{4}-\d{2}-\d{2}$", f.stem)),
        key=lambda f: f.stem,
    )
    return files[-1] if files else None


def newest_tick_datetime(parsed: Dict[str, Any]) -> Optional[_dt.datetime]:
    """Best-effort datetime of the newest tick (date + HH:MM), for liveness
    fallback. Returns None if either piece is missing."""
    latest = parsed.get("latest")
    date_str = parsed.get("date")
    if not latest or not date_str or not latest.get("time"):
        return None
    try:
        d = _dt.date.fromisoformat(date_str)
        hh, mm = latest["time"].split(":")
        return _dt.datetime(d.year, d.month, d.day, int(hh), int(mm))
    except (ValueError, TypeError):
        return None
