#!/usr/bin/env python3
"""PreToolUse gate hook for READ-ONLY agent jobs (Slice 1 of the interactive board).

DENY-BY-DEFAULT ALLOW-LIST. Claude Code invokes this with the PreToolUse event JSON
on stdin for EVERY tool call (the gate's settings register it with matcher "*"). We
ALLOW only the explicit read-only tool names below and DENY everything else —
including tools that do not exist yet (fail-closed against new/renamed tools) and
every MCP tool (`mcp__*`), the Task/Agent sub-agent spawners, WebFetch, Bash, Write,
Edit, MultiEdit, NotebookEdit.

WHY a DENY-BY-DEFAULT allow-list, not a blacklist (the prior design's hole):
  The earlier hook denied only {Write,Edit,MultiEdit,NotebookEdit,Bash}. That left
  EVERY other side-effect-capable tool ALLOWED — and under `--permission-mode
  bypassPermissions` they auto-ran ungated. A "read-only review" job could therefore:
    * spawn a `Task`/`Agent` sub-agent that writes + git-commits,
    * run Snowflake MCP DML/DROP as a privileged role (mcp__*snowflake*),
    * post to Slack (mcp__slack__slack_post_message),
    * exfiltrate file contents via WebFetch,
    * write Jira/Confluence via the Atlassian MCP.
  A blacklist can never enumerate all of those; an allow-list excludes them by
  omission. This hook is now the LAST-LINE boundary that holds even if the
  --allowedTools flag, --strict-mcp-config, or the env-strip were ever weakened.

PROVEN SURFACE (this box, 2026-06-23, re-proof matrix in agent_run.py docstring):
  DENIED with zero side effect — Task (no sub-agent, no file, no commit), WebFetch
  (no exfil), mcp__slack__slack_post_message (nothing posted), Snowflake MCP DML
  (tool not even present once MCP is stripped), Bash write+commit, Write. A legit
  read-only review/directive still completes `done` (Read/Grep/Glob allowed).

FAIL-CLOSED: any malformed/garbage/empty stdin, or a missing/blank tool name -> deny.
We never fail open on the read-only gate.
"""
import json
import sys

# The ONLY tools a read-only board job may use. Anything not in this set is denied.
# Keep this in lockstep with the --allowedTools list in parsers/agent_run.py.
_ALLOW_TOOLS = frozenset({
    "Read",
    "Grep",
    "Glob",
    "LS",
    "WebSearch",
})


def _emit(decision: str, reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def _deny(reason: str) -> None:
    _emit("deny", reason)


def _allow(reason: str) -> None:
    # Explicitly ALLOW the read-only tool. We do NOT stay silent: under
    # --permission-mode default a silent (no-decision) hook would defer to normal
    # permissioning, which for an interactive prompt could ask a human and hang a
    # headless job. An explicit "allow" lets the whitelisted read tool run
    # non-interactively, while everything else is explicitly denied.
    _emit("allow", reason)


def main() -> None:
    raw = sys.stdin.read()
    try:
        evt = json.loads(raw)
    except (ValueError, TypeError):
        # Fail closed: a malformed event is denied on the read-only gate.
        _deny("READ-ONLY job: malformed hook input — denied (fail-closed)")
        return
    if not isinstance(evt, dict):
        _deny("READ-ONLY job: non-object hook input — denied (fail-closed)")
        return
    tool = (evt.get("tool_name") or evt.get("toolName") or "")
    if not isinstance(tool, str) or not tool.strip():
        _deny("READ-ONLY job: missing tool name — denied (fail-closed)")
        return
    tool = tool.strip()
    if tool in _ALLOW_TOOLS:
        _allow(f"READ-ONLY job: {tool} is a permitted read-only tool")
        return
    # Deny-by-default: Task/Agent, WebFetch, Bash, Write/Edit/MultiEdit/NotebookEdit,
    # and every mcp__* tool (Snowflake/Slack/Atlassian) land here and are blocked.
    _deny(
        f"READ-ONLY job: tool {tool!r} is not on the read-only allow-list "
        f"(allowed: {sorted(_ALLOW_TOOLS)}) — denied"
    )


if __name__ == "__main__":
    main()
