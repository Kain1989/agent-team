"""Read-only agent invocation for board jobs (Slice 1).

Runs a job's prompt as a DIRECT, BLOCKING `claude -p` subprocess — the exact
mechanism `parsers/scheduler.py::_headless_launcher` proved durable — locked down so
the job is genuinely read-only end-to-end: no code-writing, no commits, no sub-agent,
no MCP, no credentials, no network exfil.

THE GATE — FOUR INDEPENDENT, BELT-AND-SUSPENDERS LAYERS (re-proven 2026-06-23)
-----------------------------------------------------------------------------
The prior design was a BLACKLIST hook ({Write,Edit,MultiEdit,NotebookEdit,Bash})
run under `--permission-mode bypassPermissions`. That left every OTHER side-effect
tool ALLOWED and auto-running ungated — a "read-only" job could spawn a Task
sub-agent that writes/commits, run Snowflake MCP DML as a privileged role, post to
Slack, exfiltrate files via WebFetch, or write Jira/Confluence. The blacklist was
the hole. This invocation now stacks four layers, any ONE of which blocks the whole
class:

  1. ALLOW-LIST flag:   --allowedTools Read Grep Glob LS WebSearch  (NOT Bash/Edit/
                        Write/Task/WebFetch/mcp__*). Everything else is excluded by
                        omission — fail-closed against new/renamed tools too.
  2. DEFAULT perm mode: --permission-mode default (NOT bypassPermissions). Bypass is
                        exactly what auto-ran every un-denied tool. Headless `-p` does
                        NOT hang under default once the allow-listed tools are
                        explicitly allowed by the hook (proven non-interactive).
  3. CATCH-ALL DENY HOOK: control/job_readonly_gate.json registers the PreToolUse hook
                        with matcher "*" (+ "mcp__.*"); control/job_gate_hook.py is
                        DENY-BY-DEFAULT — it ALLOWs only the 5 read tools and DENIES
                        everything else (Task/Agent, WebFetch, Bash, Write/Edit/
                        MultiEdit/NotebookEdit, all mcp__*). Last-line boundary if a
                        flag is ever weakened. Fail-closed on malformed stdin.
  4. NO MCP / NO CREDS:  --strict-mcp-config with an EMPTY --mcp-config
                        (control/job_empty_mcp.json) so NONE of ~/.claude.json /
                        <workspace>/.mcp.json / project .mcp.json MCP servers load
                        (Snowflake, Slack, Atlassian are simply absent). PLUS the child
                        env is stripped of every SNOWFLAKE*/SLACK*/ATLASSIAN*/
                        BITBUCKET*/JIRA*/*TOKEN*/*SECRET*/*KEY* var (see _child_env)
                        so even a leaked config path cannot authenticate. A read-only
                        reviewer holds NO warehouse/Slack/Jira handle.

RE-PROVEN ESCAPE MATRIX (this box, 2026-06-23 — the ACTUAL surface, not just Bash):
  Each ran as a real headless job and was DENIED with zero side effect —
    * Task sub-agent (write+git-commit /tmp/escape_task_*) -> denied, no file/commit
    * WebFetch exfil (read file then GET ?leak=...)        -> denied, no fetch
    * Slack mcp__slack__slack_post_message 'pwned'         -> denied/absent, nothing posted
    * Snowflake MCP CREATE TABLE/INSERT                    -> tool ABSENT (no MCP loaded)
    * Bash echo>file + git commit, and Write               -> denied (regression)
  A legit read-only review/directive (reads + summarizes) still completes `done`.

We rely on the Python-side `subprocess.run(timeout=...)` + `--max-turns N` for the
runtime ceiling (this box has NO `timeout(1)` binary).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Any, Dict, List, Optional

from . import paths

# Same binary the scheduler uses; overridable for tests / alt installs.
CLAUDE_BIN = os.environ.get("STANDUP_CLAUDE_BIN") or shutil.which("claude") or "claude"
# A read-only review/directive/analysis is cheap; cap turns + wall time so a wedged
# or runaway agent cannot hold a worker slot forever. Both overridable via env.
MAX_TURNS = int(os.environ.get("STANDUP_JOB_MAX_TURNS", "12"))
JOB_TIMEOUT_S = int(os.environ.get("STANDUP_JOB_TIMEOUT_S", str(15 * 60)))

# Layer 1: the ONLY tools a read-only job may use. Kept in lockstep with the
# allow-list in control/job_gate_hook.py (the hook is the machine boundary; this
# flag is the first fence). NO Write/Edit/MultiEdit/NotebookEdit/Bash/Task/Agent/
# WebFetch and NO mcp__* — those are excluded by omission (fail-closed).
READONLY_ALLOWED_TOOLS: List[str] = ["Read", "Grep", "Glob", "LS", "WebSearch"]

# Layer 4 (env): strip any credential-bearing var so a leaked MCP/config path still
# cannot authenticate to Snowflake / Slack / Atlassian / Bitbucket / Jira.
_SENSITIVE_ENV_RE = re.compile(
    r"(SNOWFLAKE|SLACK|ATLASSIAN|BITBUCKET|JIRA|TOKEN|SECRET|KEY)",
    re.IGNORECASE,
)
# ...but NEVER strip the vars Claude itself needs to authenticate (those happen to
# match *KEY*/*TOKEN*). The lockdown must not break the happy path. This box uses
# OAuth (creds in ~/.claude.json, no API key in env), but an alternate box may export
# one of these, so we keep them explicitly.
_ENV_KEEP = frozenset({
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "AWS_BEARER_TOKEN_BEDROCK",
})


def _child_env() -> Dict[str, str]:
    """The environment for the read-only job subprocess: a copy of the parent env with
    every credential-bearing var removed (Snowflake/Slack/Atlassian/Bitbucket/Jira/
    *TOKEN*/*SECRET*/*KEY*), EXCEPT the handful Claude needs to authenticate itself
    (_ENV_KEEP). Even if `--strict-mcp-config`/the empty MCP config were bypassed, a
    server that reads its creds from the environment would find none."""
    out: Dict[str, str] = {}
    for k, v in os.environ.items():
        if k in _ENV_KEEP:
            out[k] = v
            continue
        if _SENSITIVE_ENV_RE.search(k):
            continue  # drop the credential
        out[k] = v
    return out


def _parse_result(stdout: str) -> Dict[str, Any]:
    """Extract the fields we record from `claude -p --output-format json` stdout.

    Returns {ok, final_text, num_turns, duration_ms, is_error, permission_denials,
    denied_tools, session_id, cost_usd, usage}. Tolerant: a missing/garbage field
    yields None/[] and never raises (a malformed stdout is reported via ok=False by
    the caller)."""
    out: Dict[str, Any] = {
        "final_text": None, "num_turns": None, "duration_ms": None,
        "is_error": None, "permission_denials": [], "denied_tools": [],
        "session_id": None, "cost_usd": None, "usage": None,
    }
    try:
        j = json.loads(stdout)
    except (ValueError, TypeError):
        return out
    res = j.get("result")
    out["final_text"] = res if isinstance(res, str) else None
    out["num_turns"] = j.get("num_turns")
    out["duration_ms"] = j.get("duration_ms")
    out["is_error"] = j.get("is_error")
    out["session_id"] = j.get("session_id")
    # Cost/usage for the /costs budget command (claude -p json: total_cost_usd + usage).
    cost = j.get("total_cost_usd")
    if cost is None:
        cost = j.get("cost_usd") or j.get("costUSD")
    try:
        out["cost_usd"] = float(cost) if cost is not None else None
    except (TypeError, ValueError):
        out["cost_usd"] = None
    u = j.get("usage")
    if isinstance(u, dict):
        out["usage"] = u
    # The CLI reports denied tool calls (the audit trail proving the gate fired).
    denials = j.get("permission_denials") or j.get("permissionDenials") or []
    if isinstance(denials, list):
        out["permission_denials"] = denials
        tools: List[str] = []
        for d in denials:
            if isinstance(d, dict):
                t = d.get("tool_name") or d.get("toolName")
                if t:
                    tools.append(t)
        out["denied_tools"] = tools
    return out


def run_readonly(
    prompt: str,
    *,
    cwd: Optional[str] = None,
    timeout_s: Optional[int] = None,
    max_turns: Optional[int] = None,
    claude_bin: Optional[str] = None,
    settings_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Run `prompt` as a blocking read-only `claude -p` subprocess under the gate.

    Returns a result dict the worker persists into the job record:
        {ok, exit_code, final_text, num_turns, duration_ms, is_error,
         permission_denials, denied_tools, session_id, stderr_tail, error?}
    `ok` is True iff the process exited 0 (a clean agent run, even if it DENIED
    tools — denials are expected/desirable, not a failure). A timeout yields
    exit_code 124 + ok=False; a spawn error yields exit_code -1 + ok=False.

    The gate is layered (see module docstring): the allow-list flag + default
    permission mode + the catch-all deny-by-default hook (`settings_path`, default
    control/job_readonly_gate.json) + strict empty MCP + a credential-stripped env.
    Even if the prompt slips and asks to write, spawn a sub-agent, hit an MCP, or
    fetch a URL, every layer independently denies it.
    """
    binexe = claude_bin or CLAUDE_BIN
    settings = settings_path or str(paths.job_readonly_gate())
    empty_mcp = str(paths.job_empty_mcp())
    work_dir = cwd or str(paths.workspace_root())
    turns = max_turns if max_turns is not None else MAX_TURNS
    tmo = timeout_s if timeout_s is not None else JOB_TIMEOUT_S

    cmd = [
        binexe, "-p",
        "--output-format", "json",
        # Layer 2: default mode (NOT bypassPermissions — that auto-ran every tool).
        "--permission-mode", "default",
        "--max-turns", str(turns),
        # Layer 1: allow ONLY the read-only tools; everything else excluded by omission.
        "--allowedTools", *READONLY_ALLOWED_TOOLS,
        # Layer 4: ignore ALL ambient MCP configs and load an EMPTY set — no
        # Snowflake/Slack/Atlassian server is present in the job.
        "--strict-mcp-config",
        "--mcp-config", empty_mcp,
        # Layer 3: the catch-all deny-by-default PreToolUse hook.
        "--settings", settings,
    ]
    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=tmo,
            # Layer 4 (env): a credential-stripped copy of the parent environment.
            env=_child_env(),
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False, "exit_code": 124, "final_text": None,
            "num_turns": None, "duration_ms": None, "is_error": True,
            "permission_denials": [], "denied_tools": [],
            "session_id": None, "stderr_tail": "",
            "error": f"read-only job exceeded {tmo}s timeout",
        }
    except (OSError, ValueError) as exc:
        return {
            "ok": False, "exit_code": -1, "final_text": None,
            "num_turns": None, "duration_ms": None, "is_error": True,
            "permission_denials": [], "denied_tools": [],
            "session_id": None, "stderr_tail": "",
            "error": f"could not launch claude: {exc}",
        }

    parsed = _parse_result(proc.stdout or "")
    exit_code = proc.returncode
    result = {
        "ok": exit_code == 0,
        "exit_code": exit_code,
        "stderr_tail": (proc.stderr or "")[-600:],
        "settings": settings,
        "cwd": work_dir,
        # Gate provenance — the exact lockdown applied (audit trail).
        "permission_mode": "default",
        "allowed_tools": list(READONLY_ALLOWED_TOOLS),
        "strict_mcp_config": True,
        "mcp_config": empty_mcp,
        **parsed,
    }
    if exit_code != 0 and not result.get("error"):
        result["error"] = (
            f"claude exited {exit_code}: {result['stderr_tail'][-200:]}".strip()
        )
    return result


# --- code task (Slice 2): the worktree-scoped, WRITE-capable gate -------------
# Higher ceilings than a read-only job — a real code change needs more turns + time.
CODE_TASK_MAX_TURNS = int(os.environ.get("STANDUP_CODE_MAX_TURNS", "40"))
CODE_TASK_TIMEOUT_S = int(os.environ.get("STANDUP_CODE_TIMEOUT_S", str(35 * 60)))
# The tools a code task may use: read + worktree-scoped edit. NO Bash/Task/Agent/
# WebFetch/WebSearch/NotebookEdit and NO mcp__*. MUST stay in lockstep with _ALLOWED
# in control/job_code_gate_hook.py (the hook is the machine boundary; this is the
# first fence). Tests + the git commit are run by the trusted worker, not the agent.
CODE_TASK_ALLOWED_TOOLS: List[str] = [
    "Read", "Grep", "Glob", "LS", "Edit", "Write", "MultiEdit",
]


def run_code_task(
    prompt: str,
    *,
    worktree: str,
    timeout_s: Optional[int] = None,
    max_turns: Optional[int] = None,
    claude_bin: Optional[str] = None,
    settings_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Run `prompt` as a blocking CODE-TASK `claude -p` subprocess CONFINED to
    `worktree`. The agent may read + Edit/Write/MultiEdit INSIDE the worktree and
    nothing else; tests + the git commit are done by the trusted caller (worktree.py),
    never the model. Returns the run_readonly result shape plus gate="code_task" and
    the worktree it ran in.

    Confinement layers (see control/job_code_gate_hook.py docstring):
      1. --allowedTools = read + Edit/Write/MultiEdit only (no Bash/Task/WebFetch/MCP)
      2. --permission-mode default (NOT bypassPermissions)
      3. the code gate hook (--settings) — worktree-scoped READS and WRITES, the
         last-line machine boundary; STANDUP_CODE_WORKTREE tells it the scope root
      4. --strict-mcp-config + an empty MCP set, and a credential-stripped env
      5. cwd = the worktree and --add-dir = the worktree (Claude's own fs scope)
    """
    binexe = claude_bin or CLAUDE_BIN
    settings = settings_path or str(paths.job_code_gate())
    empty_mcp = str(paths.job_empty_mcp())
    wt = os.path.realpath(worktree)
    turns = max_turns if max_turns is not None else CODE_TASK_MAX_TURNS
    tmo = timeout_s if timeout_s is not None else CODE_TASK_TIMEOUT_S

    cmd = [
        binexe, "-p",
        "--output-format", "json",
        # Layer 2: default mode (NOT bypassPermissions).
        "--permission-mode", "default",
        "--max-turns", str(turns),
        # Layer 1: read + worktree-scoped edit only; everything else excluded by omission.
        "--allowedTools", *CODE_TASK_ALLOWED_TOOLS,
        # Layer 4: ignore ALL ambient MCP configs and load an EMPTY set.
        "--strict-mcp-config",
        "--mcp-config", empty_mcp,
        # Layer 5: Claude's own fs scope is the worktree.
        "--add-dir", wt,
        # Layer 3: the worktree-scoped deny-by-default code gate hook.
        "--settings", settings,
    ]
    env = _child_env()
    # The gate hook reads this to scope every read/write to the worktree.
    env["STANDUP_CODE_WORKTREE"] = wt
    try:
        proc = subprocess.run(
            cmd, input=prompt, cwd=wt, capture_output=True, text=True,
            timeout=tmo, env=env,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False, "exit_code": 124, "final_text": None, "num_turns": None,
            "duration_ms": None, "is_error": True, "permission_denials": [],
            "denied_tools": [], "session_id": None, "stderr_tail": "",
            "error": f"code task exceeded {tmo}s timeout", "gate": "code_task",
            "worktree": wt,
        }
    except (OSError, ValueError) as exc:
        return {
            "ok": False, "exit_code": -1, "final_text": None, "num_turns": None,
            "duration_ms": None, "is_error": True, "permission_denials": [],
            "denied_tools": [], "session_id": None, "stderr_tail": "",
            "error": f"could not launch claude: {exc}", "gate": "code_task",
            "worktree": wt,
        }

    parsed = _parse_result(proc.stdout or "")
    exit_code = proc.returncode
    result = {
        "ok": exit_code == 0,
        "exit_code": exit_code,
        "stderr_tail": (proc.stderr or "")[-600:],
        "settings": settings,
        "cwd": wt,
        "worktree": wt,
        # Gate provenance — the exact lockdown applied (audit trail).
        "permission_mode": "default",
        "allowed_tools": list(CODE_TASK_ALLOWED_TOOLS),
        "strict_mcp_config": True,
        "mcp_config": empty_mcp,
        "gate": "code_task",
        **parsed,
    }
    if exit_code != 0 and not result.get("error"):
        result["error"] = (
            f"claude exited {exit_code}: {result['stderr_tail'][-200:]}".strip()
        )
    return result
