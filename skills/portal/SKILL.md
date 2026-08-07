---
name: portal
description: Start the Mission Control portal — the team's visualization, monitoring, and management/approval surface. Use when the user wants to open the dashboard, watch the team, monitor agents, or review/approve work in the browser.
allowedTools: Read, Bash
---

Start the Mission Control portal so the human can watch and manage the agent team. Do it now. (Needs a team project in the current directory — run `/agent-team:init` if `standup/portal` is absent.)

1. **Install once if needed.** If `standup/.venv` does not exist, run `./setup.sh`.
2. **Launch in the background** (binds to 127.0.0.1 only):
   `cd standup/portal && (STANDUP_JOBWORKER=1 ./run_local.sh >/tmp/agent-portal.log 2>&1 &)`
   Wait ~2s, then confirm: `curl -s http://127.0.0.1:${PORT:-8770}/healthz`.
3. **Tell the human the URL** (http://127.0.0.1:8770 or the `PORT` from `.env`) and what they'll see: squads + dev health, the EM board, the live tick log, and the **job queue** — submit a code task (target any developer's `folder` from `standup/team.json`), watch it run in an isolated worktree, review the diff, and **Approve** to commit.

The portal reflects the same files the team writes, so any `/agent-team:standup` you run shows up live. (The runner hero reads "on-demand" unless a recurring standup is scheduled — that's expected.)
