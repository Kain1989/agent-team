---
name: work
description: Run ONE specific task end-to-end through the gated SDLC (plan→challenge→implement→test→review→commit) with a pair of sub-agents. Use for a single task or backlog item. (For the WHOLE team's standup over the whole roster, use /standup instead.)
allowedTools: Read, Bash, Edit, Write, Task
---

Run ONE task end-to-end through the gated SDLC, using the Task tool (paired agents). Do it now.

TASK: $ARGUMENTS
(If empty, pick the top unchecked item from `demo-app/BACKLOG.md`.)

1. **Ensure the target is a git repo** (default `demo-app`): if `demo-app/.git` is missing, `git -C demo-app init -b main && git -C demo-app add -A && git -C demo-app -c user.name=demo -c user.email=demo@local commit -m "demo-app: initial import"`.
2. **PLAN** (subagent as `dev_a`): read the repo, write a step-by-step plan — no code — plus the tests to write.
3. **CHALLENGE** (subagent as `dev_b`, fresh context): critique the plan for direction/scope/risks/tests; one revision cycle, then escalate.
4. **IMPLEMENT** (subagent as `dev_a`): make the change + write/extend tests; run the test gate (`pytest` for demo-app); record the exact commands + results.
5. **REVIEW** (2 subagents, fresh context — a correctness lens + a conventions/tests lens) on the ACTUAL working-tree diff.
6. **COMMIT ON GREEN**: only if the tests pass AND both reviews pass, commit to `auto/standup-<slug>` (stage only the task's files; never `git add -A`). Otherwise report what failed — do not loop.
7. **Summarize**: the diff, test results, both review verdicts, and the commit sha (or why not).
