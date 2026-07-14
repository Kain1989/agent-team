"""Git worktree lifecycle for CODE-TASK jobs (Slice 2 of the interactive board).

A code task runs in an ISOLATED git worktree of the target repo on a throwaway
branch, so the agent's edits never touch the user's checkout and land as a reviewable
branch diff before a human-approved commit. This module is the TRUSTED git side —
plain `subprocess` git, NO model involved:

  create(repo, branch, dest)        -> fetch origin, resolve the default branch, add a
                                       worktree at `dest` on a new `branch` cut from
                                       origin/<default> (R3: honest fail if unreachable)
  stage_and_diff(worktree, base)    -> stage the agent's edits + capture the diff;
                                       does NOT commit (the awaiting-approval artifact)
  commit(worktree, base, msg)       -> the ACTUAL git commit on the job branch — run by
                                       the worker ONLY after the human approves (R6)
  remove(repo, worktree, branch)    -> tear down the worktree + delete the branch (reject/cleanup)

THE HUMAN-GATE SPLIT (R6, the core of the demo)
-----------------------------------------------
The agent (under control/job_code_gate_hook.py) has NO Bash and CANNOT commit — it
only Edits files in the worktree. The worker then STAGES + DIFFS (no commit) and parks
the job at 'awaiting_approval' with the captured diff. NOTHING is committed yet — the
gate-held proof is that `git log` on the branch shows no new commit. Only when the
human clicks Approve does the worker call commit() — the single, human-gated write.
Nothing is ever pushed; merge into a mainline is left to the human in their checkout.

SAFETY
------
* git is always invoked as an argv LIST (never a shell string) so a crafted branch
  name or path cannot inject a command.
* `branch` is validated against a strict charset; `dest` must not already exist.
* commit uses `--no-verify`: a repo's own git hooks never execute on agent-edited code
  in the trusted worker. (The agent cannot plant a hook anyway — `.git` is outside the
  worktree's writable scope — but we skip hooks belt-and-suspenders.)
* NOTHING here runs the agent's edited code (no tests): execution never enters the
  trusted, credentialed path. The human reviews the diff (and runs tests in their own
  checkout) before committing/merging.
"""
from __future__ import annotations

import os
import re
import subprocess
from typing import Any, Dict, List, Optional

# A branch/ref name we are willing to create or act on. Deliberately strict: no spaces,
# no shell metacharacters, no leading dash (so it can't be read as a git flag).
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,119}$")
# Cap the stored diff so a runaway change can't bloat the job record / UI payload.
DIFF_MAX = 200_000


def _git(repo: str, args: List[str], timeout: int = 120) -> subprocess.CompletedProcess:
    """Run `git -C repo <args>` as an argv list (no shell). Caller checks returncode."""
    return subprocess.run(
        ["git", "-C", repo, *args], capture_output=True, text=True, timeout=timeout
    )


def _is_repo(repo: str) -> bool:
    try:
        p = _git(repo, ["rev-parse", "--is-inside-work-tree"], timeout=20)
        return p.returncode == 0 and p.stdout.strip() == "true"
    except (OSError, subprocess.SubprocessError):
        return False


def _resolve_origin_default(repo_root: str) -> Dict[str, Any]:
    """R3: fetch origin + resolve origin's default branch, returning a base ref the
    worktree is cut from. Returns {ok, base_ref, base_sha, default_branch} on success,
    or {ok:False, code, error} with an HONEST code if origin is unreachable / has no
    resolvable HEAD (never a silent fall-back to a stale local branch).

    Order: (1) `git fetch origin` — fail `origin_unreachable` if it errors (no remote,
    network/auth down). (2) resolve origin/HEAD via `symbolic-ref refs/remotes/origin/HEAD`;
    if that symref isn't set (common for a bare/clone origin), fall back to `git remote
    show origin`'s "HEAD branch"; finally to origin/main|origin/master if present. The
    base is the REMOTE ref (origin/<default>) so the agent always works off what the
    remote considers mainline, matching the spec's origin/main isolation requirement."""
    if not _is_repo(repo_root):
        return {"ok": False, "code": "target_not_git",
                "error": f"not a git repo: {repo_root!r}"}
    # (1) Must have an 'origin' remote.
    remotes = _git(repo_root, ["remote"], timeout=20)
    if remotes.returncode != 0 or "origin" not in remotes.stdout.split():
        return {"ok": False, "code": "origin_unreachable",
                "error": "target repo has no 'origin' remote — a code task cuts its "
                         "branch from origin's default branch (set one to enable)"}
    # (2) Fetch origin — the reachability gate.
    fetched = _git(repo_root, ["fetch", "origin", "--quiet"], timeout=120)
    if fetched.returncode != 0:
        return {"ok": False, "code": "origin_unreachable",
                "error": ("git fetch origin failed — origin is unreachable "
                          f"(network/auth/remote gone): {fetched.stderr.strip()[:200]}")}
    # (3) Resolve the default branch name off origin.
    default_branch = None
    sym = _git(repo_root, ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
               timeout=20)
    if sym.returncode == 0 and sym.stdout.strip():
        # e.g. "origin/main" -> "main"
        default_branch = sym.stdout.strip().split("/", 1)[-1] or None
    if not default_branch:
        show = _git(repo_root, ["remote", "show", "origin"], timeout=60)
        if show.returncode == 0:
            for ln in show.stdout.splitlines():
                ln = ln.strip()
                if ln.lower().startswith("head branch:"):
                    default_branch = ln.split(":", 1)[1].strip() or None
                    break
    if not default_branch:
        for cand in ("main", "master"):
            chk = _git(repo_root, ["rev-parse", "--verify", "--quiet",
                                   f"refs/remotes/origin/{cand}"], timeout=20)
            if chk.returncode == 0:
                default_branch = cand
                break
    if not default_branch:
        return {"ok": False, "code": "origin_unreachable",
                "error": "could not resolve origin's default branch (no origin/HEAD, "
                         "no HEAD branch in `remote show origin`, no origin/main|master)"}
    base_ref = f"origin/{default_branch}"
    rev = _git(repo_root, ["rev-parse", "--verify", "--quiet", base_ref], timeout=20)
    if rev.returncode != 0 or not rev.stdout.strip():
        return {"ok": False, "code": "origin_unreachable",
                "error": f"resolved default {base_ref!r} does not exist after fetch"}
    return {"ok": True, "base_ref": base_ref, "base_sha": rev.stdout.strip(),
            "default_branch": default_branch}


def create(repo_root: str, branch: str, dest: str) -> Dict[str, Any]:
    """R2/R3: add a worktree at `dest` on a NEW `branch` cut from origin's default
    branch (after fetching origin). The WORKER owns this — the Work-phase does NOT
    auto-worktree. Returns {ok, worktree, branch, base_sha, base_ref, default_branch}
    or {ok:False, code, error} (code='target_not_git' | 'origin_unreachable' |
    'dest_exists' | 'unsafe_branch' | 'worktree_add')."""
    if not _BRANCH_RE.match(branch or ""):
        return {"ok": False, "code": "unsafe_branch",
                "error": f"unsafe branch name {branch!r}"}
    if not repo_root or not os.path.isdir(repo_root) or not _is_repo(repo_root):
        return {"ok": False, "code": "target_not_git",
                "error": f"not a git repo: {repo_root!r}"}
    if os.path.exists(dest):
        return {"ok": False, "code": "dest_exists",
                "error": f"worktree dest already exists: {dest!r}"}
    # R3: fetch origin + resolve the default branch BEFORE adding the worktree.
    base = _resolve_origin_default(repo_root)
    if not base.get("ok"):
        return base
    base_ref = base["base_ref"]
    base_sha = base["base_sha"]
    try:
        parent = os.path.dirname(dest)
        if parent:
            os.makedirs(parent, exist_ok=True)
        p = _git(repo_root, ["worktree", "add", "-b", branch, dest, base_ref], timeout=120)
        if p.returncode != 0:
            return {"ok": False, "code": "worktree_add",
                    "error": f"worktree add failed: {p.stderr.strip()[:300]}"}
        return {"ok": True, "worktree": dest, "branch": branch, "base_sha": base_sha,
                "base_ref": base_ref, "default_branch": base["default_branch"]}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "code": "worktree_add", "error": f"create: {exc}"}


def _diff_files(worktree: str, rng: str) -> List[Dict[str, str]]:
    names = _git(worktree, ["diff", "--name-status", rng], timeout=30).stdout.strip()
    files = []
    for ln in names.splitlines():
        if not ln.strip():
            continue
        parts = ln.split("\t", 1)
        files.append({"status": parts[0], "path": parts[1] if len(parts) > 1 else ""})
    return files


def stage_and_diff(worktree: str, base_sha: Optional[str]) -> Dict[str, Any]:
    """Stage ALL of the agent's edits in `worktree` and capture the diff — WITHOUT
    committing. This is the awaiting-approval artifact: the human reviews this diff and
    decides. The gate-held proof is that NO commit exists on the branch at this point.

    Returns {ok, changed, diff, diff_truncated, files}. `changed` is False (clean tree)
    when the agent produced no edits — a no-op task, not a failure. The diff is taken
    against the base (origin/<default> at create) including staged + unstaged so a
    new file shows too (`git add -A` first, then `git diff --staged` vs base)."""
    try:
        st = _git(worktree, ["status", "--porcelain"], timeout=60)
        if st.returncode != 0:
            return {"ok": False, "error": f"status failed: {st.stderr.strip()[:200]}"}
        if not st.stdout.strip():
            return {"ok": True, "changed": False, "diff": "",
                    "diff_truncated": False, "files": []}
        add = _git(worktree, ["add", "-A"], timeout=60)
        if add.returncode != 0:
            return {"ok": False, "error": f"add failed: {add.stderr.strip()[:200]}"}
        # Diff the staged tree against the base sha (no commit made yet). Falls back to
        # `diff --staged` (vs HEAD) if base_sha is unknown.
        rng = base_sha if base_sha else None
        if rng:
            files = _diff_files(worktree, rng)
            diff = _git(worktree, ["diff", "--staged", rng], timeout=60).stdout or ""
        else:
            files = _diff_files(worktree, "--staged")
            diff = _git(worktree, ["diff", "--staged"], timeout=60).stdout or ""
        return {"ok": True, "changed": True,
                "diff": diff[:DIFF_MAX], "diff_truncated": len(diff) > DIFF_MAX,
                "files": files}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": f"stage_and_diff: {exc}"}


def commit(worktree: str, base_sha: Optional[str], message: str) -> Dict[str, Any]:
    """The HUMAN-GATED commit (R6): run by the worker ONLY after the human approves.
    Commits the already-staged edits on the job branch (--no-verify, identity stamped),
    NEVER pushes, NEVER merges to mainline. Returns {ok, committed, commit_sha, diff,
    files} (committed=False if the tree was clean — nothing to commit, a no-op)."""
    try:
        # Stage anything not yet staged (idempotent — stage_and_diff already added).
        _git(worktree, ["add", "-A"], timeout=60)
        # Nothing staged vs HEAD -> nothing to commit.
        diff_idx = _git(worktree, ["diff", "--cached", "--quiet"], timeout=30)
        if diff_idx.returncode == 0:
            return {"ok": True, "committed": False, "commit_sha": None,
                    "diff": "", "files": []}
        cm = _git(worktree, ["-c", "user.name=standup-codejob",
                             "-c", "user.email=standup@local",
                             "commit", "--no-verify", "-m", message], timeout=60)
        if cm.returncode != 0:
            return {"ok": False, "error": f"commit failed: {cm.stderr.strip()[:200]}"}
        sha = _git(worktree, ["rev-parse", "HEAD"], timeout=20).stdout.strip()
        rng = f"{base_sha}..HEAD" if base_sha else "HEAD~1..HEAD"
        files = _diff_files(worktree, rng)
        diff = _git(worktree, ["diff", rng], timeout=60).stdout or ""
        return {"ok": True, "committed": True, "commit_sha": sha,
                "diff": diff[:DIFF_MAX], "diff_truncated": len(diff) > DIFF_MAX,
                "files": files}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": f"commit: {exc}"}


def remove(repo_root: str, worktree: str, branch: Optional[str]) -> Dict[str, Any]:
    """Tear down the worktree and delete its branch (reject / cleanup). Best-effort:
    reports what failed but never raises. Returns {ok, errors}."""
    errors: List[str] = []
    try:
        p = _git(repo_root, ["worktree", "remove", "--force", worktree], timeout=60)
        if p.returncode != 0:
            errors.append(f"worktree remove: {p.stderr.strip()[:150]}")
    except (OSError, subprocess.SubprocessError) as exc:
        errors.append(f"worktree remove: {exc}")
    if branch and _BRANCH_RE.match(branch):
        try:
            p = _git(repo_root, ["branch", "-D", branch], timeout=30)
            if p.returncode != 0:
                errors.append(f"branch -D: {p.stderr.strip()[:150]}")
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(f"branch -D: {exc}")
    return {"ok": not errors, "errors": errors}
