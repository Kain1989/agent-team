#!/usr/bin/env python3
"""UserPromptSubmit hook (L2) — per-turn routing reminder.

Deterministic backstop against mid-session drift out of supervisor mode. The
SessionStart charter sets the mode once; this re-asserts it on EVERY prompt so a long
session can't quietly slide back into "the EM does the dev work itself".

Self-gates to agent-team checkouts (walks up for `standup/team.json`); prints nothing
elsewhere. Always exits 0 — never blocks a prompt. Pure python3, no personal paths.
"""
import json
import os
import sys


def find_team_root(cwd):
    d = os.path.abspath(cwd or ".")
    while d and d != os.path.dirname(d):
        if os.path.isfile(os.path.join(d, "standup", "team.json")):
            return d
        d = os.path.dirname(d)
    return None


def main():
    try:
        cwd = json.load(sys.stdin).get("cwd", "")
    except Exception:
        cwd = os.getcwd()
    if not find_team_root(cwd):
        sys.exit(0)
    print(
        "[supervisor-mode] You are the ENGINEERING MANAGER and you PRODUCE NOTHING "
        "yourself. If this request is to build/change a product, the portal, a report, "
        "an analysis, or any code — including your projects, the portal (standup/portal), "
        "evals, or research — it is a TEAM task: route it through /work, /team, "
        "/standup, or the Workflow (standup/standup.workflow.js). Do it directly ONLY if "
        "it is a management/orchestration/governance primitive: roster (standup/"
        "team.json), BACKLOG, PM_GOALS, log/, control/ (gates/budget/kill/schedule), the "
        "workflow engine, the plugin's own dirs (.claude/, .claude-plugin/, skills/, "
        "hooks/), planning, triage, or a question. Editing project content (your projects, "
        "standup/portal/, evals/, research/) is hook-blocked — that means 'route it "
        "through the team', not 'find another way'. Trivial/urgent one-line hotfix only: "
        "use the standup/control/supervisor_override escape hatch (logged)."
    )


if __name__ == "__main__":
    main()
