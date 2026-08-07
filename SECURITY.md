# Security

## What the plugin runs

The `agent-team` plugin orchestrates an AI engineering team locally. Be aware it:
- spawns headless `claude -p` subprocesses to do work (read-only jobs + code tasks);
- runs a local **FastAPI portal bound to `127.0.0.1` only** (never a public interface);
- creates **isolated git worktrees** of a target repo for code tasks;
- reads/writes only within the team project + the target repos you configure.

## Trust boundaries / the gate

A code-task agent runs under four independent, belt-and-suspenders layers (see
`standup/portal/parsers/agent_run.py` + `standup/control/job_code_gate_hook.py`):
1. **allow-list** — read + worktree-scoped edit only (no Bash, sub-agents, WebFetch, or MCP);
2. **`--permission-mode default`** (not bypass);
3. a **deny-by-default PreToolUse hook** confining every read AND write to the job's worktree;
4. **empty MCP config + a credential-stripped child env** — no Snowflake/Slack/etc. handle
   exists in the job, and `*KEY*/*TOKEN*/*SECRET*` vars are stripped.

On top of that:
- **Human approval gate** — a code task parks at `awaiting_approval` with a diff; the trusted
  worker commits ONLY after a human approves. Nothing is ever pushed or merged.
- **Output guardrail** — the produced diff is scanned for apparent secrets BEFORE it reaches
  the approval queue; a leak fails the job instead of being queued for a click-through.
- **Budget cap + kill switch** — enforced in the trusted worker (outside the agent), so a
  runaway run cannot exceed its own limit.
- **Separation of duties** (opt-in) — the approver can be required to differ from the creator.

## No secrets in the repo

The repo tracks source only — `.venv/`, `control/jobs.db`, `.env`, generated gate configs,
and each project's local bare origin are gitignored. Do not commit credentials; the portal needs none.

## Platform support

macOS and Linux. Requires **bash**, **Python 3.9+**, **git**, and the **`claude` CLI** on
`PATH`. `/standup` uses the Claude Code **Workflow tool** (with a Task-tool fallback). Windows
is not currently supported (the `setup.sh` installer + the portal launcher are POSIX shell).

## Reporting a vulnerability

Open a private security advisory on the GitHub repo, or contact the maintainer
(https://github.com/Kain1989). Please do not open a public issue for a vulnerability.
