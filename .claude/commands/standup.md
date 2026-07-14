---
description: Run the full agent-team standup + gated work cycle on demo-app (the whole squad)
---

Run the agent team's full standup + gated work cycle now. Do it; don't just describe it.

1. **Ensure the work repo is ready.** If `demo-app/.git` does not exist, run:
   ```
   git -C demo-app init -b main && git -C demo-app add -A && \
   git -C demo-app -c user.name=demo -c user.email=demo@local commit -m "demo-app: initial import"
   ```

2. **Read `standup/team.json`** (the full roster) and confirm today's date (`date +%Y-%m-%d`).

3. **Run the whole squad via the Workflow tool** — per-dev standup → squad sync → EM board →
   light staff pulse → gated work (plan → pair challenge → implement+tests → 2-lens review →
   commit-on-green) on the top autoworkable task:
   ```
   Workflow({ scriptPath: "standup/standup.workflow.js",
              args: { date: "<today>", since: "6 hours ago",
                      roster: <the parsed contents of standup/team.json>,
                      work: true, maxTasks: 1 } })
   ```
   If this build has no `Workflow` tool, orchestrate the same gated SDLC yourself with the
   Task tool (per-dev standup subagents → an EM board → for the top task: plan → fresh-context
   pair challenge → implement+`pytest` → 2 fresh-context reviews → commit on green only).

4. **Close out the tick:** summarize the EM board, what was worked, the green/committed counts,
   and any commits on `demo-app` feature branches. Append a `## standup (<today>)` section to
   `standup/log/<today>.md` (create it if missing) and update the "Last updated" line in
   `standup/BACKLOG.md`.

Constraints: commits go onto `auto/standup-*` feature branches only — never push, never merge
to a mainline, never deploy. If a plan is rejected twice or tests fail, stop and report — don't loop.

Tip: have the Mission Control portal open (run `/portal`) so the human can watch the squads,
board, and dev progress update live as this runs.
