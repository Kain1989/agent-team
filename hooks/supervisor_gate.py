#!/usr/bin/env python3
"""PreToolUse hook (L3) — the HARD backstop for supervisor mode (AGGRESSIVE).

The Engineering Manager PRODUCES NOTHING. Every project / deliverable under the team
root — the repos you added with /add-project, the Mission Control portal, evals, research
reports, any code — is DONE BY THE TEAM through the gated SDLC (/work, /team, /standup, or
the Workflow). The EM
directly touches ONLY the management / orchestration / governance primitives needed to
RUN the team. This hook mechanically enforces exactly that, with one narrow audited hatch.

Enforced only when the cwd is inside an agent-team checkout (found by walking up to a dir
that holds `standup/team.json`). Edit / Write / NotebookEdit is ALLOWED only for:
  * standup/team.json, standup/BACKLOG.md, standup/PM_GOALS.md  (roster + backlog + goals)
  * standup/log/**        (the record)
  * standup/control/**    (gates, budget, kill switch, schedule)
  * standup/workflows/**  and standup/standup.workflow.js       (the orchestration engine)
  * the plugin's own dirs: .claude/, .claude-plugin/, skills/, hooks/, .github/  (the
    governance framework: commands, native-team agent defs, skills, hooks, CI)
  * any top-level file in the team root (README, CLAUDE.md, CHANGELOG, setup.sh, ...)
Everything else under the team root is a PROJECT and is BLOCKED -> route via the team:
  * <your projects>/   -> the squad that owns them
  * standup/portal/    -> the portal squad
  * evals/, research/, ...  -> their owner or /deep-research
Anything OUTSIDE the team root is left alone (this plugin may be installed on a machine
full of unrelated projects, plus scratch/tmp and ~/.claude — don't police those).

NARROW EXCEPTION (a trivial/urgent one-line hotfix, NOT feature work): if
`standup/control/supervisor_override` exists and is fresh (< 1h old), ONE blocked action
is ALLOWED and appended to `standup/control/hotfix_audit.log` (timestamp + tool + target
+ the reason in the override file). Delete it when done; it also auto-expires after 1h so
a forgotten one can't leave the gate open.

TEAM-RUN EXEMPTION: while `standup/control/team_run_active` exists and is fresh (< 6h),
the gate steps aside — a native-team run's teammates ARE the team doing the project work,
governed by the native-team task lifecycle hooks (TaskCreated/TaskCompleted secret-scan +
kill switch) and the gated SDLC, not by this supervisor gate. Without it, teammates
editing team-internal projects (the portal, your own repos) would be false-blocked because their
cwd is still inside the team tree. The flag auto-expires (6h) so a forgotten one can't
leave the gate off.

Deliberately NOT gated here (differs from the multi-repo private deployment on purpose):
  * Bash `git -C <path> <mutating>` — /add-project legitimately runs `git init`/`commit`
    on a project it is creating; gating it would break onboarding. A multi-repo
    deployment where hand-committing a dev repo bypasses a push/merge gate DOES add that
    check; this single self-contained team folder does not need it.

Exit 2 = block (stderr -> the model). Exit 0 = allow. Fails OPEN on any parse error.
Pure python3, no personal/absolute paths.
"""
import json
import os
import sys
import time

# Dir names directly under standup/ whose whole subtree the EM may write (orchestration).
STANDUP_ALLOW_DIRS = {"log", "control", "workflows"}
# Exact file names directly under standup/ that are writable (roster/backlog/goals/engine).
STANDUP_ALLOW_FILES = {"team.json", "BACKLOG.md", "PM_GOALS.md", "standup.workflow.js"}
# Top-level dirs in the team root that are the plugin's own governance framework.
PLUGIN_DIRS = {".claude", ".claude-plugin", "skills", "hooks", ".github"}

OVERRIDE_TTL = 3600        # 1h — a stale override is ignored so a forgotten one can't linger
TEAM_RUN_TTL = 6 * 3600    # 6h — a stale team-run flag is ignored


def find_team_root(cwd):
    d = os.path.abspath(cwd or ".")
    while d and d != os.path.dirname(d):
        if os.path.isfile(os.path.join(d, "standup", "team.json")):
            return d
        d = os.path.dirname(d)
    return None


def allowed_target(team_root, target):
    """True iff target is a management/orchestration/governance primitive (not a project)."""
    rel = os.path.relpath(target, team_root)
    if rel.startswith(".."):
        return True                          # outside the team root — not ours to police
    parts = rel.split(os.sep)
    if len(parts) == 1:
        return True                          # a top-level file in the team root — allow
    if parts[0] in PLUGIN_DIRS:
        return True                          # the plugin's own governance dirs — allow
    if parts[0] == "standup":
        sub = parts[1]
        if sub in STANDUP_ALLOW_DIRS:
            return True                      # standup/log|control|workflows/**
        if len(parts) == 2 and sub in STANDUP_ALLOW_FILES:
            return True                      # standup/team.json|BACKLOG.md|PM_GOALS.md|engine
        return False                         # standup/portal/ and any other standup/ path
    return False                             # any project dir, evals/, research/, ... = project


def _mtime_fresh(path, ttl):
    try:
        return (time.time() - os.stat(path).st_mtime) < ttl
    except OSError:
        return False


def override_reason(control_dir):
    """A fresh (< TTL) override file lets one blocked action through, audited."""
    p = os.path.join(control_dir, "supervisor_override")
    if not _mtime_fresh(p, OVERRIDE_TTL):
        return None
    try:
        with open(p) as f:
            reason = f.read().strip()
    except OSError:
        reason = ""
    return reason or "(no reason given)"


def audit(control_dir, tool, target, reason):
    try:
        line = "%s\t%s\t%s\t%s\n" % (
            time.strftime("%Y-%m-%d %H:%M:%S"), tool, target, reason)
        with open(os.path.join(control_dir, "hotfix_audit.log"), "a") as f:
            f.write(line)
    except OSError:
        pass


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)  # fail open

    tool = payload.get("tool_name", "")
    if tool not in ("Edit", "Write", "NotebookEdit"):
        sys.exit(0)

    cwd = payload.get("cwd", "") or os.getcwd()
    team_root = find_team_root(cwd)
    if not team_root:
        sys.exit(0)  # not an agent-team session

    control_dir = os.path.join(team_root, "standup", "control")

    # TEAM-RUN EXEMPTION: a fresh team_run_active flag means a native-team run is in
    # progress — the teammates ARE the team, governed by the native-team lifecycle hooks
    # + gated SDLC, not by this gate. Step aside.
    if _mtime_fresh(os.path.join(control_dir, "team_run_active"), TEAM_RUN_TTL):
        sys.exit(0)

    ti = payload.get("tool_input", {}) or {}
    target = ti.get("file_path") or ti.get("notebook_path") or ""
    if not target:
        sys.exit(0)
    if not os.path.isabs(target):
        target = os.path.join(cwd, target)
    target = os.path.normpath(target)

    if allowed_target(team_root, target):
        sys.exit(0)

    # Blocked — unless a fresh, one-shot override is present (logged).
    reason = override_reason(control_dir)
    if reason is not None:
        audit(control_dir, tool, target, reason)
        sys.exit(0)

    sys.stderr.write(
        "\U0001F6D1 supervisor-mode: blocked a direct edit of PROJECT content.\n"
        f"  target: {target}\n"
        "You are the ENGINEERING MANAGER — you produce nothing yourself. This belongs to "
        "the team (a project directory -> its squad; the portal under standup/portal -> the "
        "portal squad; evals/research/reports -> their owner or /deep-research). Route it:\n"
        "  * /work <task>   * /team <task>   * /standup   * Workflow (standup/standup.workflow.js)\n"
        "You may directly edit ONLY management/orchestration/governance: standup/team.json, "
        "BACKLOG.md, PM_GOALS.md, log/, control/, workflows/, standup.workflow.js, the "
        "plugin's own dirs (.claude/, .claude-plugin/, skills/, hooks/), and top-level docs.\n"
        "Narrow exception (trivial/urgent one-line hotfix ONLY, not feature work): write "
        "standup/control/supervisor_override with a one-line reason, then retry — it allows "
        "this once and logs it to control/hotfix_audit.log. Delete it when done."
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
