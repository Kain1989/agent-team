---
name: standup
description: Run the WHOLE roster — every squad reports, the EM builds the board, and the top task is worked through the gated SDLC. Use when the user says run the agent team, run the standup, start the team, or do a standup. (For ONE specific task, use /work instead.)
allowedTools: Read, Bash, Edit, Write, Workflow, Task
---

Run the agent team's full standup + gated work cycle now. Do it; don't just describe it. (If there is no `standup/team.json` in the current directory, tell the user to run `/agent-team:init` first.)

1. **Ensure the work repo is ready.** If `demo-app/.git` does not exist (and a `demo-app/` exists), run:
   `git -C demo-app init -b main && git -C demo-app add -A && git -C demo-app -c user.name=demo -c user.email=demo@local commit -m "demo-app: initial import"`
2. **Read `standup/team.json`** (the full roster) and confirm today's date (`date +%Y-%m-%d`).
3. **Run the whole squad via the Workflow tool** — per-dev standup -> squad sync -> EM board -> light staff pulse -> gated work (plan -> pair challenge -> implement+tests -> 2-lens review -> commit-on-green) on the top autoworkable task:
   `Workflow({ scriptPath: "standup/standup.workflow.js", args: { date: "<today>", since: "6 hours ago", roster: <parsed standup/team.json>, work: true, maxTasks: 1 } })`
   If this build has no `Workflow` tool, orchestrate the same gated SDLC yourself with the Task tool (per-dev standup subagents -> EM board -> for the top task: plan -> fresh-context pair challenge -> implement+`pytest` -> 2 fresh-context reviews -> commit on green only).
4. **Close out the tick:** summarize the board, what was worked, green/committed counts, and any commits on feature branches; append a `## standup (<today>)` section to `standup/log/<today>.md` and update `standup/BACKLOG.md`'s "Last updated".

Constraints: commits go onto `auto/standup-*` feature branches only — never push, never merge, never deploy. If a plan is rejected twice or tests fail, stop and report — don't loop. Tip: have Mission Control open (`/agent-team:portal`) to watch it live.
