"""Lightweight notifications (v0.2). On a notable event — a code task parked at
awaiting_approval, or a budget breach — emit a local record (control/notifications.log),
ring the terminal bell, and POST to an optional webhook (STANDUP_NOTIFY_WEBHOOK). No
external service is required; the webhook is opt-in. Runs in the TRUSTED worker (not the
locked-down job subprocess), so network for the webhook is fine.

Best-effort: every path is wrapped so a notification failure never affects the job.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sys
from typing import Any, Dict, Optional

from . import paths


def _iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def notify(event: str, message: str, data: Optional[Dict[str, Any]] = None) -> None:
    """Record + surface a notification. `event` is a short tag (e.g. 'awaiting_approval',
    'budget_breach'); `message` is human text; `data` is optional structured context."""
    line = f"{_iso()}  [{event}] {message}"
    # 1. append to control/notifications.log (always-available local record)
    try:
        cd = paths.control_dir()
        cd.mkdir(parents=True, exist_ok=True)
        with open(cd / "notifications.log", "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    # 2. terminal bell + stderr line (visible if the portal runs in a foreground term)
    try:
        sys.stderr.write("\a" + line + "\n")
        sys.stderr.flush()
    except Exception:
        pass
    # 3. optional webhook (Slack-compatible: posts {text} plus the structured payload)
    url = os.environ.get("STANDUP_NOTIFY_WEBHOOK")
    if url:
        try:
            import urllib.request
            payload = json.dumps({"text": f"[{event}] {message}", "event": event,
                                  "message": message, "data": data or {}}).encode("utf-8")
            req = urllib.request.Request(url, data=payload,
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5)  # noqa: S310 (opt-in operator URL)
        except Exception:
            pass


def recent(limit: int = 20) -> list:
    """The last `limit` notification lines (newest last), for /runs or the portal."""
    try:
        lines = (paths.control_dir() / "notifications.log").read_text(encoding="utf-8").splitlines()
        return lines[-limit:]
    except OSError:
        return []
