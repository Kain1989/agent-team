#!/usr/bin/env python3
"""PreToolUse gate hook for CODE-TASK agent jobs (Slice 2 of the interactive board).

DENY-BY-DEFAULT ALLOW-LIST + WORKTREE-SCOPED I/O — the code-task sibling of
job_gate_hook.py (the read-only gate). A code task must EDIT files to do real work,
so it may read and Edit/Write/MultiEdit — but ONLY inside its git worktree — and is
denied everything else (Bash, Task/Agent, WebFetch, WebSearch, NotebookEdit, mcp__*).

THREAT MODEL + WHY EACH RESTRICTION (hardened after an adversarial pair-review)
------------------------------------------------------------------------------
* NO Bash  -> no arbitrary code execution. The agent could Write a script into the
  worktree and run it otherwise, and the project's own pytest/npm ARE arbitrary
  execution, so no shell allow-list is safe. Tests + the git commit are run by the
  TRUSTED worker (parsers/jobworker), never the model; a human approves the merge.
* WORKTREE-SCOPED WRITES  -> Edit/Write/MultiEdit only when file_path resolves
  (realpath + normcase) INSIDE STANDUP_CODE_WORKTREE — never another repo,
  ~/.claude.json, ~/.ssh, /etc.
* WORKTREE-SCOPED READS  -> Read/Grep/Glob/LS are ALSO scoped. LOAD-BEARING: a
  write-and-commit agent with unrestricted Read is a turnkey exfil machine — it could
  Read ~/.ssh/id_rsa or a sibling .env, Write the bytes into the worktree, and the
  trusted commit + human review would launder the secret into the merged repo (the
  COMMIT is the exfil channel; blocking the network does NOT help). So reads are
  confined to the worktree too — the agent has a full checkout there, nothing outside
  it is needed.
* NO WebSearch  -> a search query string is itself a (low-bandwidth) exfil channel,
  and the agent needs no web to edit a checkout.

PATH-KEY TABLE: every path-bearing tool declares its tool_input path key in
_PATH_KEYS, so scope-checking is uniform and adding a tool forces declaring its key
(an ad-hoc per-tool getter is exactly how NotebookEdit's `notebook_path` slips by).

FAIL-CLOSED: malformed/empty stdin, missing tool name, unset/unresolvable worktree
env, or a required path missing/outside the worktree -> deny. Never fail open.

RESIDUAL (closed at the WORKER, not here): a check-then-write TOCTOU through a
symlinked parent cannot be fully closed by a stateless hook. The worker harvests the
result with git — which stages only worktree paths and stores a symlink as a link,
not its target's bytes — and re-validates scope before committing.
"""
import json
import os
import sys

# Every path-bearing tool -> the tool_input key holding its path. Membership here is
# for SCOPE-CHECKING ONLY; it does NOT grant use (the allow-set below does). Keep it
# complete: a path-bearing tool missing from this table is scope-checked on a None
# path (-> fail-closed for required-path tools — safe, but it would break the tool).
_PATH_KEYS = {
    "Read": "file_path",
    "Grep": "path",
    "Glob": "path",
    "LS": "path",
    "Edit": "file_path",
    "Write": "file_path",
    "MultiEdit": "file_path",
    "NotebookEdit": "notebook_path",
}
# Tools the code-task agent may use — ALL worktree-scoped via _PATH_KEYS. NO
# WebSearch/Bash/Task/Agent/WebFetch/NotebookEdit and NO mcp__* (excluded by omission).
# Keep in lockstep with CODE_TASK_ALLOWED_TOOLS in parsers/agent_run.py.
_ALLOWED = frozenset({"Read", "Grep", "Glob", "LS", "Edit", "Write", "MultiEdit"})
# Tools whose path is MANDATORY (a missing path -> fail-closed deny). Grep/Glob/LS may
# omit the path (then they operate on cwd, which the worker sets to the worktree).
_REQUIRE_PATH = frozenset({"Read", "Edit", "Write", "MultiEdit"})


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
    _emit("allow", reason)


def _worktree_root() -> "str | None":
    root = (os.environ.get("STANDUP_CODE_WORKTREE") or "").strip()
    if not root:
        return None
    try:
        return os.path.normcase(os.path.realpath(root))
    except OSError:
        return None


def _inside(path: object, root: str) -> bool:
    """True iff file `path` resolves INSIDE `root`. realpath resolves `..`/symlinks
    (neither can escape) and the existing prefix of a not-yet-created file; normcase
    makes the compare correct on case-insensitive filesystems (macOS) without opening
    an escape (the worker controls `root`)."""
    if not path or not isinstance(path, str):
        return False
    try:
        rp = os.path.normcase(os.path.realpath(path))
    except OSError:
        return False
    root = root.rstrip(os.sep)
    return rp == root or rp.startswith(root + os.sep)


def main() -> None:
    raw = sys.stdin.read()
    try:
        evt = json.loads(raw)
    except (ValueError, TypeError):
        _deny("CODE-TASK job: malformed hook input — denied (fail-closed)")
        return
    if not isinstance(evt, dict):
        _deny("CODE-TASK job: non-object hook input — denied (fail-closed)")
        return

    tool = (evt.get("tool_name") or evt.get("toolName") or "")
    if not isinstance(tool, str) or not tool.strip():
        _deny("CODE-TASK job: missing tool name — denied (fail-closed)")
        return
    tool = tool.strip()

    tool_input = evt.get("tool_input") or evt.get("toolInput") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}

    # Deny-by-default: only the allow-set proceeds (Bash/Task/WebFetch/WebSearch/
    # NotebookEdit/mcp__* all land here and are denied).
    if tool not in _ALLOWED:
        _deny(f"CODE-TASK job: tool {tool!r} is not permitted — a code task may only "
              f"read + Edit/Write/MultiEdit INSIDE its worktree; Bash, Task/Agent, "
              f"WebFetch, WebSearch and all MCP tools are denied (tests + commit are "
              f"run by the trusted worker, not the agent).")
        return

    # Allowed tool — enforce worktree scope on its path.
    root = _worktree_root()
    if root is None:
        _deny("CODE-TASK job: STANDUP_CODE_WORKTREE unset/unresolvable — cannot "
              "confirm scope — denied (fail-closed)")
        return

    key = _PATH_KEYS.get(tool)  # always present for an _ALLOWED member
    p = tool_input.get(key) if key else None
    if p is None:
        if tool in _REQUIRE_PATH:
            _deny(f"CODE-TASK job: {tool} missing {key!r} — denied (fail-closed)")
            return
        # Grep/Glob/LS with no path operate on cwd (= the worktree, set by the worker).
        _allow(f"CODE-TASK job: {tool} on worktree cwd — permitted")
        return
    if not _inside(p, root):
        _deny(f"CODE-TASK job: {tool} path {p!r} is OUTSIDE the worktree {root!r} — "
              f"denied (code-task I/O is worktree-scoped)")
        return
    _allow(f"CODE-TASK job: {tool} within the worktree — permitted")


if __name__ == "__main__":
    main()
