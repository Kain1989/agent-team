---
name: work
description: Run ONE specific task end-to-end through the gated SDLC (intake → investigate → plan → pair challenge → implement → test gate → review → commit on green) with a pair of sub-agents. Use for a single task or backlog item. (For the WHOLE team's standup over the whole roster, use /standup instead.)
allowedTools: Read, Bash, Edit, Write, Workflow, Task
---

Run ONE task end-to-end through the gated SDLC. Do it now; don't just describe it.

TASK: $ARGUMENTS
(If empty, pick the top unchecked item from `demo-app/BACKLOG.md`.)

This command does **not** carry its own copy of the pipeline. It dispatches to the same engine
`/standup` uses, with a single-task argument, so the plugin holds exactly one SDLC definition and
there is no second recipe to drift. The canonical step list lives in `standup/team.json` →
`manager.policy.sdlc_pipeline`.

1. **The target must be a git repo with a commit.** `/add-project` guarantees that for every
   project it creates; if you are pointing at something it did not create, check first.
2. **Read `standup/team.json`** and pick the `assignee` — a developer id from the squad that owns the
   work (e.g. `dev_a` for `demo-app`, `portal_backend` for the portal). Pass the roster **verbatim**;
   a trimmed roster silently degrades the run.
3. **Run the task through the engine:**
   ```
   Workflow({ scriptPath: "standup/standup.workflow.js",
              args: { date: "<today>", roster: <parsed standup/team.json>,
                      task: { task: "<the task>", assignee: "<dev id>", priority: "P1" } } })
   ```
   Optional: add `folder: "<dir>"` to `task` to run in a directory the assignee also owns (its own
   `folder`, its squad's `folder`, or one listed in its `also_owns`). Anything else stops the run and
   names what that dev does own. Omit it and the behaviour is unchanged.
4. **Read the result.** `mode` is `work`, and every gate the board path runs also ran here — INTAKE
   included. A run can legitimately end without a commit: `escalated-intake` (the outcome contract
   was still unclear after one revision), `escalated-plan-rejected`, `test-gate-failed`,
   `review-failed`. Report which, and why. Do **not** re-run hoping for a different answer.

**If the run stops before any agent work**, it printed a three-line `STOP —` block naming the
offending value, the valid set, and the one edit that fixes it — a mistyped assignee, a developer
with no declared pair, a folder that dev does not own, or a squad with no `review_surface`. That is
the command working: it refuses to aim the team at something nobody declared. Fix the named field in
`standup/team.json` (or the argument) and run it again.

Constraints: commits land on `auto/standup-*` feature branches only — never push, never merge, never
deploy. If a plan is rejected twice or the tests fail, stop and report — don't loop.
