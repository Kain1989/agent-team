#!/usr/bin/env python3
"""usage_hook.py — SubagentStop hook: count one agent run against the active story's phase.

Tokens come from the subagent's own transcript (`agent_transcript_path` in the hook input):
the sum of input, cache and output tokens over its assistant messages. When this Claude
Code build does not pass that path, the run is still counted as an agent but recorded as
`measured: false`, and the budget check says so — an unmeasured budget is named, not
reported as zero.

Agents cannot write usage.jsonl (guard.py: audit path) or run this script (harness-script),
so the only writer is the hook process. Never blocks; exit 0 always.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib as L  # noqa: E402


def transcript_tokens(path: str) -> int:
    total = 0
    for rec in L.read_jsonl(Path(path)):
        msg = rec.get("message") if isinstance(rec.get("message"), dict) else {}
        u = msg.get("usage") or {}
        total += sum(int(u.get(k) or 0) for k in ("input_tokens", "output_tokens",
                                                  "cache_creation_input_tokens", "cache_read_input_tokens"))
    return total


def main() -> int:
    try:
        ev = json.loads(sys.stdin.read() or "{}")
        act = L.active()
        if not act.get("story"):
            return 0
        tp = ev.get("agent_transcript_path")
        measured = bool(tp) and Path(tp).exists()
        L.append_jsonl(L.story_dir(act["story"]) / "usage.jsonl", {
            "at": L.now(), "phase": act.get("phase"), "round": act.get("round"),
            "agent_type": ev.get("agent_type") or ev.get("subagent_type") or "?",
            "tokens": transcript_tokens(tp) if measured else 0, "measured": measured})
    except Exception as e:  # never block; but leave a trace the gate can see
        try:
            L.append_jsonl(L.audit_dir() / "guard-errors.jsonl", {"at": L.now(), "error": f"usage_hook: {type(e).__name__}: {e}"})
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
