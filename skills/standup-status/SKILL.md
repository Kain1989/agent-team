---
name: standup-status
description: Show whether a recurring standup is scheduled, when it next runs, and the recent run history. Use when the user asks is the standup running, when does it run next, the schedule status, or recent runs.
allowedTools: Read, Bash
---

Report the standup schedule + recent history. Read-only.

1. Read `standup/control/schedule.json` if present — report `enabled`, the interval (hours or the daily cadence), `work`/`maxTasks`.
2. If the portal is up (`curl -s http://127.0.0.1:${PORT:-8770}/api/status`), report `runner.scheduler.enabled/running` and `runner.next_tick` (name + when). If it's not up, say recurring standups can't fire until the portal runs (`/agent-team:portal` or `/agent-team:daily-standup`).
3. List the last few runs: read `standup/log/` (the most recent dated files' tick sections) and/or `standup/control/runs/` if present — show date, health, worked/committed counts.
4. One-line verdict: scheduled (interval, next at X) or on-demand only; and how to change it (`/agent-team:daily-standup` / `/agent-team:stop-daily-standup`).
