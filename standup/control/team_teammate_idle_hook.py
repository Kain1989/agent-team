#!/usr/bin/env python3
"""Bridge (2) — TeammateIdle governance hook for native agent teams.

Fires when a teammate is about to go idle. If the kill switch is set, STOP the
teammate entirely (JSON continue:false). Otherwise let it idle normally. (Native
teams don't expose per-task cost to hooks, so the daily budget CAP can't be enforced
here; the kill switch is the available hard stop.)"""
import json
import os
import sys

_PORTAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "portal")
if _PORTAL not in sys.path:
    sys.path.insert(0, _PORTAL)

def main():
    try:
        from parsers import paths
        if (paths.control_dir() / "kill_switch").exists():
            print(json.dumps({"continue": False,
                              "stopReason": "agent-team kill switch is ON (control/kill_switch)"}))
            sys.exit(0)
    except Exception:
        pass
    sys.exit(0)

if __name__ == "__main__":
    main()
