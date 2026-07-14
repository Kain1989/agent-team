"""Read-only job GATE hardening tests (Slice-1 security keystone).

These lock in the airtight gate the security re-review demanded — asserting the
SETTINGS the job subprocess is launched with (allow-list + default mode + strict
empty MCP + credential-stripped env) and that the PreToolUse hook is
DENY-BY-DEFAULT (allows only the read tools; denies Task, Bash, Write, WebFetch,
and every mcp__* incl. Slack/Snowflake), fail-closed on malformed input.

We do NOT spawn the real `claude` binary here (the live end-to-end escape matrix is
re-proven against the running portal). We assert the COMMAND + ENV + hook decisions
— the parts a regression could silently weaken. The agent subprocess is monkeypatched
to capture argv/env without executing it.
"""

import json
import subprocess

import pytest

from parsers import agent_run


# --------------------------------------------------------------------------- #
# The launched command: allow-list, default mode, strict empty MCP (Layers 1/2/4)
# --------------------------------------------------------------------------- #
def _capture_cmd(monkeypatch):
    """Run run_readonly with the subprocess stubbed; return (argv, env) it tried."""
    captured = {}

    class _FakeProc:
        returncode = 0
        stdout = json.dumps({"result": "ok", "is_error": False, "num_turns": 1,
                             "permission_denials": []})
        stderr = ""

    def _fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["env"] = kw.get("env")
        captured["input"] = kw.get("input")
        return _FakeProc()

    monkeypatch.setattr(subprocess, "run", _fake_run)
    agent_run.run_readonly("inspect the thing", cwd="/tmp")
    return captured


def test_command_uses_allowlist_not_blacklist(monkeypatch):
    cap = _capture_cmd(monkeypatch)
    cmd = cap["cmd"]
    # --allowedTools present, immediately followed by EXACTLY the read-only set.
    assert "--allowedTools" in cmd, cmd
    i = cmd.index("--allowedTools")
    allowed = []
    for tok in cmd[i + 1:]:
        if tok.startswith("--"):
            break
        allowed.append(tok)
    assert allowed == ["Read", "Grep", "Glob", "LS", "WebSearch"], allowed
    # NONE of the dangerous tools are allow-listed (excluded by omission).
    for bad in ("Bash", "Write", "Edit", "MultiEdit", "NotebookEdit", "Task",
                "Agent", "WebFetch", "ToolSearch"):
        assert bad not in allowed
    # No --disallowedTools blacklist crutch (the allow-list is the mechanism).
    assert "--disallowedTools" not in cmd and "--disallowed-tools" not in cmd


def test_command_uses_default_mode_not_bypass(monkeypatch):
    cap = _capture_cmd(monkeypatch)
    cmd = cap["cmd"]
    assert "--permission-mode" in cmd
    mode = cmd[cmd.index("--permission-mode") + 1]
    assert mode == "default", f"expected default, got {mode!r}"
    assert "bypassPermissions" not in cmd


def test_command_uses_strict_empty_mcp(monkeypatch):
    cap = _capture_cmd(monkeypatch)
    cmd = cap["cmd"]
    assert "--strict-mcp-config" in cmd
    assert "--mcp-config" in cmd
    mcp_path = cmd[cmd.index("--mcp-config") + 1]
    # The config it points at must be an EMPTY server set.
    with open(mcp_path) as fh:
        cfg = json.load(fh)
    assert cfg.get("mcpServers") == {}, cfg


def test_command_passes_the_gate_settings(monkeypatch):
    cap = _capture_cmd(monkeypatch)
    cmd = cap["cmd"]
    assert "--settings" in cmd
    settings_path = cmd[cmd.index("--settings") + 1]
    with open(settings_path) as fh:
        gate = json.load(fh)
    pretool = gate["hooks"]["PreToolUse"]
    matchers = {entry.get("matcher") for entry in pretool}
    # Catch-all matcher (+ explicit mcp matcher) so the hook fires for EVERY tool.
    assert "*" in matchers, matchers
    assert any(m and m.startswith("mcp__") for m in matchers), matchers


# --------------------------------------------------------------------------- #
# The child env (Layer 4): credentials stripped, Claude auth + PATH preserved
# --------------------------------------------------------------------------- #
def test_child_env_strips_credentials(monkeypatch):
    for k in ("SNOWFLAKE_PASSWORD", "SLACK_BOT_TOKEN", "ATLASSIAN_API_TOKEN",
              "BITBUCKET_TOKEN", "JIRA_API_TOKEN", "MY_SECRET", "SOME_PRIVATE_KEY",
              "GITHUB_TOKEN"):
        monkeypatch.setenv(k, "should-be-stripped")
    env = agent_run._child_env()
    for k in ("SNOWFLAKE_PASSWORD", "SLACK_BOT_TOKEN", "ATLASSIAN_API_TOKEN",
              "BITBUCKET_TOKEN", "JIRA_API_TOKEN", "MY_SECRET", "SOME_PRIVATE_KEY",
              "GITHUB_TOKEN"):
        assert k not in env, f"{k} leaked into child env"


def test_child_env_keeps_claude_auth_and_path(monkeypatch):
    """The strip must NOT remove what Claude needs to run (the happy path)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "keep-me")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "keep-me-too")
    env = agent_run._child_env()
    assert env.get("ANTHROPIC_API_KEY") == "keep-me"
    assert env.get("CLAUDE_CODE_OAUTH_TOKEN") == "keep-me-too"
    assert "PATH" in env  # subprocess must still find node/claude


def test_child_env_is_passed_to_subprocess(monkeypatch):
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", "nope")
    cap = _capture_cmd(monkeypatch)
    assert cap["env"] is not None
    assert "SNOWFLAKE_PASSWORD" not in cap["env"]


# --------------------------------------------------------------------------- #
# The hook itself: deny-by-default allow-list, fail-closed
# --------------------------------------------------------------------------- #
def _hook_decision(payload: str) -> str:
    """Invoke the actual gate hook with `payload` on stdin; return its decision
    (or '<none>' if it emitted nothing)."""
    import sys
    from parsers import paths
    gate = json.loads(paths.job_readonly_gate().read_text())
    cmd = gate["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    # cmd is "<python> <hook.py>"; run it the same way Claude would.
    parts = cmd.split()
    proc = subprocess.run(parts, input=payload, capture_output=True, text=True)
    out = (proc.stdout or "").strip()
    if not out:
        return "<none>"
    try:
        return json.loads(out)["hookSpecificOutput"]["permissionDecision"]
    except (ValueError, KeyError):
        return f"<unparseable:{out[:40]}>"


@pytest.mark.parametrize("tool", ["Read", "Grep", "Glob", "LS", "WebSearch"])
def test_hook_allows_read_tools(tool):
    payload = json.dumps({"tool_name": tool, "hook_event_name": "PreToolUse"})
    assert _hook_decision(payload) == "allow"


@pytest.mark.parametrize("tool", [
    "Task", "Agent", "WebFetch", "Bash", "Write", "Edit", "MultiEdit",
    "NotebookEdit", "ToolSearch",
    "mcp__slack__slack_post_message",
    "mcp__snowflake__run_query",
    "mcp__atlassian__jira_create_issue",
    "SomeBrandNewToolThatDidNotExistYesterday",  # fail-closed against new tools
])
def test_hook_denies_everything_else(tool):
    payload = json.dumps({"tool_name": tool, "hook_event_name": "PreToolUse"})
    assert _hook_decision(payload) == "deny", tool


def test_hook_denies_slack_post_specifically():
    """The exact tool the review called out — Slack post — is denied."""
    payload = json.dumps({"tool_name": "mcp__slack__slack_post_message"})
    assert _hook_decision(payload) == "deny"


@pytest.mark.parametrize("payload", ["not json", "", "   ", "[]", "null",
                                     '{"tool_name": ""}', '{"tool_name": "   "}',
                                     '{"no_tool_name_field": 1}'])
def test_hook_fails_closed_on_bad_input(payload):
    assert _hook_decision(payload) == "deny", payload
