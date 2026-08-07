"""Filesystem layout for the portal.

All artifact paths are resolved relative to the STANDUP root so the portal works
no matter what CWD uvicorn is launched from. STANDUP root can be overridden with
the ``STANDUP_ROOT`` env var (used by tests / alternate checkouts).
"""

from __future__ import annotations

import os
from pathlib import Path

# portal/ lives directly under the STANDUP root: .../STANDUP/portal/parsers/paths.py
_DEFAULT_ROOT = Path(__file__).resolve().parents[2]

STANDUP_ROOT = Path(os.environ.get("STANDUP_ROOT", str(_DEFAULT_ROOT))).resolve()

# The workspace root is the parent of STANDUP — the per-dev .standup folders live
# under sibling project directories (your projects, standup/portal, etc.), and the
# comms inbox lives at <workspace>/messages/inbox.
WORKSPACE_ROOT = STANDUP_ROOT.parent


def standup_root() -> Path:
    """Re-read the root each call so tests can monkeypatch STANDUP_ROOT via env."""
    return Path(os.environ.get("STANDUP_ROOT", str(_DEFAULT_ROOT))).resolve()


def workspace_root() -> Path:
    return standup_root().parent


# --- Top-level artifacts ----------------------------------------------------
def team_json() -> Path:
    return standup_root() / "team.json"


def backlog_md() -> Path:
    return standup_root() / "BACKLOG.md"


def log_dir() -> Path:
    return standup_root() / "log"


def log_for(date_str: str) -> Path:
    return log_dir() / f"{date_str}.md"


def control_dir() -> Path:
    return standup_root() / "control"


def heartbeat_json() -> Path:
    return control_dir() / "heartbeat.json"


def run_lock() -> Path:
    """control/run.lock — the machine-owned exclusive lock held for the entire
    duration of ANY tick (scheduled cron OR portal-triggered). The portal READS
    it (never holds it) to know whether a tick is running; drain.py + the
    scheduled-tick crons HOLD it. See control/run_lock.py."""
    return control_dir() / "run.lock"


def tick_active_marker() -> Path:
    """control/tick_active.marker — the tiny machine signal a scheduled-tick launch
    drops so the heartbeat reconciler can detect "a tick is RUNNING" independently
    of whether the lock was taken. The portal does NOT read this for its busy
    decision (it reads run.lock only); the marker feeds the runner-side reconciler
    that stamps run.lock for a lock-free running tick. See control/run_lock.py."""
    return control_dir() / "tick_active.marker"


def run_lock_module() -> Path:
    """The shared control/run_lock.py implementation (acquire + read sides).
    Imported by the portal's read path so the lock semantics live in ONE place.

    The lock *file* (run.lock) is STANDUP_ROOT-relative (isolated per test root),
    but the *module* is CODE — it loads from the real checkout next to the portal
    package, independent of STANDUP_ROOT, so the lock semantics are available even
    when tests point STANDUP_ROOT at a throwaway dir that has no control/run_lock.py.
    A test that wants to exercise a CUSTOM module can still drop one in its isolated
    control dir; we prefer that if present, else fall back to the real checkout."""
    isolated = control_dir() / "run_lock.py"
    if isolated.exists():
        return isolated
    return _DEFAULT_ROOT / "control" / "run_lock.py"


def requests_dir() -> Path:
    """control/requests/ — the ONLY directory the web tier ever writes to.

    Each file is requests/<ts>-<uuid>.json (a pending action request)."""
    return control_dir() / "requests"


def results_dir() -> Path:
    """control/results/<id>.json — the runner's state-transition records
    (running|done|failed). The runner is the single writer here."""
    return control_dir() / "results"


def runs_dir() -> Path:
    """control/runs/<run_id>.json — the DAEMON scheduler's fire HISTORY (one file
    per scheduled/portal-triggered fire: running|done|failed|skipped). Written by
    parsers/scheduler.py via parsers/runs.py; read by GET /api/runs + /api/status.
    Distinct from results/ (that is the action-QUEUE lifecycle); runs/ is the
    scheduler's tick timeline that feeds Mission Control."""
    return control_dir() / "runs"


def control_log() -> Path:
    """control/control.log — append-only audit of every request + transition."""
    return control_dir() / "control.log"


def jobs_db() -> Path:
    """control/jobs.db — the SQLite system-of-record for the interactive board's
    job lifecycle (Slice 1: read-only agent jobs). STANDUP_JOBS_DB overrides the
    path for tests (mirrors the STANDUP_ROOT override pattern); default is under
    the control dir so it sits beside the file-queue + run.lock it coexists with."""
    override = os.environ.get("STANDUP_JOBS_DB")
    if override:
        return Path(override)
    return control_dir() / "jobs.db"


def job_readonly_gate() -> Path:
    """control/job_readonly_gate.json — the --settings file whose PreToolUse hook
    DENIES every mutation-capable tool, making a read-only agent job physically
    unable to write/edit/commit (holds even under --permission-mode
    bypassPermissions; proven). The hook is the security boundary, not
    --disallowedTools. CODE is loaded from the real checkout (independent of
    STANDUP_ROOT, like run_lock_module) so tests pointing at a throwaway root still
    find the gate; an isolated control/ copy is preferred if present."""
    isolated = control_dir() / "job_readonly_gate.json"
    if isolated.exists():
        return isolated
    return _DEFAULT_ROOT / "control" / "job_readonly_gate.json"


def job_empty_mcp() -> Path:
    """control/job_empty_mcp.json — an EMPTY MCP server set ({"mcpServers": {}}).
    Passed to the read-only job subprocess via `--mcp-config <this> --strict-mcp-config`
    so NONE of the ambient MCP configs (~/.claude.json / <workspace>/.mcp.json /
    project .mcp.json) load — the Snowflake, Slack, and Atlassian servers are simply
    not present in the job. CODE-side resolution mirrors job_readonly_gate(): prefer an
    isolated control/ copy (so tests pointing at a throwaway root still find it), else
    fall back to the real checkout next to the portal package."""
    isolated = control_dir() / "job_empty_mcp.json"
    if isolated.exists():
        return isolated
    return _DEFAULT_ROOT / "control" / "job_empty_mcp.json"


def job_code_gate() -> Path:
    """control/job_code_gate.json — the --settings file for a CODE-TASK job (Slice 2):
    a PreToolUse hook that allows ONLY worktree-scoped Read/Grep/Glob/LS/Edit/Write/
    MultiEdit and denies everything else (Bash, Task/Agent, WebFetch, WebSearch,
    NotebookEdit, all mcp__*). Reads AND writes are confined to STANDUP_CODE_WORKTREE
    so the agent cannot exfiltrate host secrets into the committed diff. CODE-side
    resolution mirrors job_readonly_gate(): prefer an isolated control/ copy (tests),
    else the real checkout next to the portal package."""
    isolated = control_dir() / "job_code_gate.json"
    if isolated.exists():
        return isolated
    return _DEFAULT_ROOT / "control" / "job_code_gate.json"


def code_worktrees_dir() -> Path:
    """control/worktrees/ — the parent dir under which each code-task job's isolated
    git worktree (control/worktrees/<job_id>) is created, beside jobs.db + the file
    queue. Gitignored (a worktree of the TARGET repo, tracked by that repo, not this
    one). STANDUP_CODE_WORKTREES_DIR overrides for tests."""
    override = os.environ.get("STANDUP_CODE_WORKTREES_DIR")
    if override:
        return Path(override)
    return control_dir() / "worktrees"


def inbox_dir() -> Path:
    """messages/inbox lives under the workspace root, not under STANDUP."""
    return workspace_root() / "messages" / "inbox"


def dev_standup_file(folder: str, dev_id: str) -> Path:
    """<workspace>/<folder>/.standup/<dev_id>.md.

    ``folder`` comes straight from team.json (e.g. ``my-app``,
    ``standup/portal``). A folder under ``standup`` (like the portal's own)
    resolves inside the STANDUP root, since that root is ``<workspace>/standup``.
    """
    folder = (folder or "").strip()
    base = workspace_root()
    return base / folder / ".standup" / f"{dev_id}.md"
