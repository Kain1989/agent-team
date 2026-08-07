---
name: standup
description: Run the WHOLE roster — every squad reports, the EM builds the board, and the top task is worked through the gated SDLC. Use when the user says run the agent team, run the standup, start the team, or do a standup. (For ONE specific task, use /work instead.)
allowedTools: Read, Bash, Edit, Write, Workflow, Task
---

Run the agent team's full standup + gated work cycle now. Do it; don't just describe it. (If there is no `standup/team.json` in the current directory, tell the user to run `/agent-team:init` first.)

1. **Read `standup/team.json`** (the full roster) and confirm today's date (`date +%Y-%m-%d`).
3. **Run the whole squad via the Workflow tool** — per-dev standup -> squad sync -> design pass -> EM board -> light staff pulse -> the gated SDLC on the top autoworkable task:
   `Workflow({ scriptPath: "standup/standup.workflow.js", args: { date: "<today>", since: "6 hours ago", roster: <parsed standup/team.json>, work: true, maxTasks: 1 } })`
   The gated steps are **not restated here**: `standup/team.json` -> `manager.policy.sdlc_pipeline` is the one canonical list, and the engine is its only implementation. (This line used to carry a second, hand-maintained copy of the pipeline as a no-`Workflow` fallback. Two hand-maintained definitions drift by construction — that copy said "2-lens review" while the engine derives green from the lenses actually planned, and it never mentioned INTAKE at all. If your build has no `Workflow` tool, read the canonical list and run those steps; do not re-fork it into this file.)
4. **Close out the tick:** summarize the board, what was worked, green/committed counts, and any commits on feature branches; append a `## standup (<today>)` section to `standup/log/<today>.md` and update `standup/BACKLOG.md`'s "Last updated".

Constraints: commits go onto `auto/standup-*` feature branches only — never push, never merge, never deploy. If a plan is rejected twice or tests fail, stop and report — don't loop. Tip: have Mission Control open (`/agent-team:portal`) to watch it live.
