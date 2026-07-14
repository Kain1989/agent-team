#!/usr/bin/env python3
"""Bridge (2) — TaskCompleted governance hook for native agent teams.

Fires when a teammate task is being marked complete. The payload carries task_diff —
so we run our OUTPUT guardrail (secret-scan) on the produced diff BEFORE the task can
complete, exactly like the worktree-approval gate does for job code-tasks. Exit 2
BLOCKS completion (stderr -> feedback), so a leaked secret can't be marked done.
Fail-CLOSED on a scan error of a non-empty diff (be safe); fail-open on infra errors."""
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
        sys.exit(0)
    diff = payload.get("task_diff") or ""
    if not diff:
        sys.exit(0)  # nothing to scan
    try:
        from parsers import guardrails
    except Exception:
        sys.exit(0)  # can't govern -> don't block
    try:
        out = guardrails.check_output(diff)
    except Exception as exc:
        _block(f"output guardrail could not scan the diff (failing closed): {exc}")
    if not out.get("ok", True):
        _block("output guardrail BLOCKED this task: " + out.get("reason", "secrets in diff")
               + ". Remove the secret(s) before completing.")
    sys.exit(0)

if __name__ == "__main__":
    main()
