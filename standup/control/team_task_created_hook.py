#!/usr/bin/env python3
"""Bridge (2) — TaskCreated governance hook for native agent teams.

Fires when a teammate task is being created. Runs our INPUT guardrail on the task
description + a kill-switch check. Exit 2 BLOCKS creation (stderr -> feedback to the
lead). Reuses parsers/guardrails (the same content validators the job queue uses).
Fail-open on infra errors (a broken hook must not wedge the whole team)."""
import json
import os
import sys

_PORTAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "portal")
if _PORTAL not in sys.path:
    sys.path.insert(0, _PORTAL)

def _block(msg):
    sys.stderr.write(msg + "\n"); sys.exit(2)

def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)  # can't parse -> don't block
    desc = payload.get("task_description") or ""
    try:
        from parsers import guardrails, paths
    except Exception:
        sys.exit(0)  # can't govern -> don't block
    # kill switch: refuse to create new teammate work when the team is hard-stopped.
    try:
        if (paths.control_dir() / "kill_switch").exists():
            _block("agent-team kill switch is ON (control/kill_switch) — task creation blocked.")
    except Exception:
        pass
    # input guardrail: oversized / configured-denied task descriptions.
    g = guardrails.check_input(desc)
    if not g.get("ok", True):
        _block(f"task rejected by input guardrail: {g.get('reason')}")
    sys.exit(0)

if __name__ == "__main__":
    main()
