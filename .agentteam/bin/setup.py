#!/usr/bin/env python3
"""setup.py — one-time install. Idempotent; safe to re-run. Stdlib only, any OS.

  1. creates the runtime dirs (.agentteam/state/{audit,scratch}, workspace/)
  2. writes .claude/settings.local.json wiring guard.py (PreToolUse) and usage_hook.py
     (SubagentStop) with THIS machine's absolute interpreter and script paths
  3. smoke-runs the configured hook command with a synthetic event that must be DENIED —
     Claude Code does not fail closed on a hook it cannot execute: it runs the tool and
     records zero denials, so a hook pointing at a missing interpreter is a boundary that
     silently is not there (v1, 0.5.2). Setup refuses to finish in that state.
  4. generates the agent files and runs env.py

Re-run it whenever the interpreter moves (a deleted venv, a Python upgrade).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib as L  # noqa: E402

TOOL_MATCHER = "Bash|Read|Write|Edit|MultiEdit|NotebookEdit|Grep|Glob|LS"
SMOKE_EVENT = {"tool_name": "Bash", "tool_input": {"command": "git push --force origin main"}, "hook_event_name": "PreToolUse"}


def hook_command(script: str) -> str:
    py = Path(sys.executable).resolve().as_posix()
    return f'"{py}" "{(L.BIN / script).resolve().as_posix()}"'


def settings() -> Dict[str, Any]:
    return {"hooks": {
        "PreToolUse": [{"matcher": TOOL_MATCHER, "hooks": [{"type": "command", "command": hook_command("guard.py"), "timeout": 30}]}],
        "SubagentStop": [{"hooks": [{"type": "command", "command": hook_command("usage_hook.py"), "timeout": 30}]}],
    }}


def settings_path() -> Path:
    return L.root() / ".claude" / "settings.local.json"


def smoke(cmd: str) -> str:
    """'' when the configured command denies the smoke event; otherwise what went wrong."""
    try:
        r = subprocess.run(cmd, shell=True, input=json.dumps(SMOKE_EVENT), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60, cwd=str(L.root()))
    except Exception as e:
        return f"could not execute: {e}"
    if r.returncode not in (0, 2):
        return f"exit {r.returncode}: {(r.stderr or r.stdout).strip()[:300]}"
    try:
        dec = json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"]
    except Exception:
        return f"no decision on stdout (exit {r.returncode}): {(r.stdout or r.stderr).strip()[:300]}"
    return "" if dec == "deny" else f"decision was '{dec}', expected deny"


def configured_command() -> str:
    s = L.read_json(settings_path(), {})
    try:
        return s["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    except (KeyError, IndexError, TypeError):
        return ""


def main() -> int:
    for d in (L.audit_dir(), L.state_dir() / "scratch", L.workspace()):
        d.mkdir(parents=True, exist_ok=True)
    existing = L.read_json(settings_path(), {})
    existing.setdefault("hooks", {}).update(settings()["hooks"])
    L.write_json(settings_path(), existing)
    print(f"wrote {settings_path().relative_to(L.root()).as_posix()} (guard + usage hooks, {Path(sys.executable).as_posix()})")
    err = smoke(configured_command())
    if err:
        print(f"!! the configured guard hook does not block: {err}\n   Fix the interpreter path and re-run setup.", file=sys.stderr)
        return 1
    print("guard smoke run: denied a force push to main, as it must")
    import roster
    roster.main([])
    import env
    env.main()
    print("\nnext: /add-project, then /story <title>.  Prove the gates: python .agentteam/bin/selftest.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
