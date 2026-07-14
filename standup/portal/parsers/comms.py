"""Comms intake from messages/inbox/*.json — ONE agent, THREE streams.

PM decision (2026-06-18): comms is a single agent that runs three intake streams
(Kain's "message / email / meeting agents on the portal"). This parser READS the
inbox file CONTENTS (not just stat mtimes) so the portal can show a real count per
stream:

  teams_<date>.json    -> MESSAGE stream  (count = len(activity[]) + len(chats[]))
  outlook_<date>.json  -> EMAIL stream    (count = len(mail[]))
                          MEETING stream  (count = len(calendar[]))

Per-stream freshness is derived from each source file's mtime, using the SAME
fresh/stale/stalled thresholds as the top-level signal. ``signed_in`` is read from
the JSON (a stream whose source says signed_in:false is flagged so the UI can show
the re-login nudge).

Backward compatible: the original top-level ``last_pull_at`` / ``stale_hours`` /
``state`` (derived from the NEWEST inbox file) are preserved exactly, so the
existing frontend keeps working; ``streams`` and ``signed_in`` are additive.

Tolerant: a missing dir / unreadable or malformed file degrades to a stream with
state=missing/empty + a parse warning; it never raises.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import paths

# A healthy puller runs at least every morning tick (~24h). >18h = stale, >48h = stalled.
STALE_HOURS = 18.0
STALLED_HOURS = 48.0


def _state_for_age(age_h: Optional[float]) -> str:
    if age_h is None:
        return "unknown"
    if age_h >= STALLED_HOURS:
        return "stalled"
    if age_h >= STALE_HOURS:
        return "stale"
    return "fresh"


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _list_len(data: Optional[Dict[str, Any]], *keys: str) -> int:
    """Sum the lengths of the named list fields (missing/non-list -> 0)."""
    if not data:
        return 0
    total = 0
    for k in keys:
        v = data.get(k)
        if isinstance(v, list):
            total += len(v)
    return total


def _newest_by_prefix(inbox: Path, prefix: str) -> Optional[Path]:
    """Newest ``<prefix>_*.json`` in the inbox (by filename, which is date-stamped;
    ties broken by mtime). Returns None if none exist."""
    candidates = sorted(
        inbox.glob(f"{prefix}_*.json"),
        key=lambda f: (f.stem, f.stat().st_mtime if f.exists() else 0),
    )
    return candidates[-1] if candidates else None


def parse(now: Optional[_dt.datetime] = None, inbox=None) -> Dict[str, Any]:
    now = now or _dt.datetime.now()
    d = inbox or paths.inbox_dir()
    out: Dict[str, Any] = {
        "last_pull_at": None,
        "stale_hours": None,
        "state": "unknown",
        "signed_in": None,
        "newest_file": None,
        "file_count": 0,
        "streams": [],
        "_parse_warnings": [],
        "_ok": True,
        "_path": str(d),
    }

    if not d.exists():
        out["_ok"] = False
        out["state"] = "missing"
        out["_parse_warnings"].append(f"inbox dir missing: {d}")
        return out

    # --- top-level newest-file freshness (unchanged contract) ----------------
    newest_mtime = None
    newest_name = None
    count = 0
    try:
        for f in d.glob("*.json"):
            count += 1
            try:
                mt = f.stat().st_mtime
            except OSError:
                continue
            if newest_mtime is None or mt > newest_mtime:
                newest_mtime = mt
                newest_name = f.name
    except OSError as exc:
        out["_ok"] = False
        out["_parse_warnings"].append(f"inbox scan failed: {exc}")
        return out

    out["file_count"] = count
    if newest_mtime is not None:
        last_pull = _dt.datetime.fromtimestamp(newest_mtime)
        age_h = (now - last_pull).total_seconds() / 3600.0
        out["last_pull_at"] = last_pull.astimezone().isoformat(timespec="seconds")
        out["stale_hours"] = round(age_h, 1)
        out["newest_file"] = newest_name
        out["state"] = _state_for_age(age_h)
    else:
        out["state"] = "empty"
        out["_parse_warnings"].append("no *.json in inbox")

    # --- per-stream intake (READ contents) -----------------------------------
    teams_file = _newest_by_prefix(d, "teams")
    outlook_file = _newest_by_prefix(d, "outlook")

    teams_data = _load_json(teams_file) if teams_file else None
    outlook_data = _load_json(outlook_file) if outlook_file else None

    if teams_file and teams_data is None:
        out["_parse_warnings"].append(f"teams file unreadable/invalid: {teams_file.name}")
    if outlook_file and outlook_data is None:
        out["_parse_warnings"].append(f"outlook file unreadable/invalid: {outlook_file.name}")

    # ``signed_in`` for the agent = signed-in on every source we have (any source
    # reporting not-signed-in flips it false so the UI can nudge re-login).
    signed_flags = [
        bool(src.get("signed_in"))
        for src in (teams_data, outlook_data)
        if isinstance(src, dict) and "signed_in" in src
    ]
    out["signed_in"] = all(signed_flags) if signed_flags else None

    def _stream(kind: str, label: str, src_file: Optional[Path], src_data, count_keys) -> Dict[str, Any]:
        last_pull_at = None
        age_h = None
        state = "missing"
        if src_file is not None and src_file.exists():
            try:
                mt = src_file.stat().st_mtime
                lp = _dt.datetime.fromtimestamp(mt)
                last_pull_at = lp.astimezone().isoformat(timespec="seconds")
                age_h = (now - lp).total_seconds() / 3600.0
                state = _state_for_age(age_h)
            except OSError:
                state = "missing"
        cnt = _list_len(src_data, *count_keys) if src_data is not None else 0
        return {
            "kind": kind,
            "label": label,
            "count": cnt,
            "last_pull_at": last_pull_at,
            "state": state,
            "signed_in": bool(src_data.get("signed_in")) if isinstance(src_data, dict) and "signed_in" in src_data else None,
            "source": src_file.name if src_file is not None else None,
        }

    out["streams"] = [
        # MESSAGE = Teams activity[] + chats[]
        _stream("message", "Messages", teams_file, teams_data, ("activity", "chats")),
        # EMAIL = Outlook mail[]
        _stream("email", "Email", outlook_file, outlook_data, ("mail",)),
        # MEETING = Outlook calendar[]
        _stream("meeting", "Meetings", outlook_file, outlook_data, ("calendar",)),
    ]

    return out
