---
name: daily-standup
description: Start a recurring (scheduled) daily standup that runs the team automatically on an interval. Use when the user wants to schedule standups, run the team every N hours, automate the daily standup, or turn on recurring runs.
allowedTools: Read, Bash, Write
---

Start a RECURRING standup so the team runs automatically. Do it now.

INTERVAL: $ARGUMENTS — a number = interval IN HOURS (e.g. `5` = every 5 hours), or `daily` = the built-in 4×/day cadence (08:00 / 14:07 / 20:17 / 02:27). Default if empty: `daily`.

1. Make sure a team project exists here (`standup/team.json`) — else tell the user to `/agent-team:init`.
2. Write the runtime schedule config `standup/control/schedule.json` (create `control/` if needed):
   ```json
   { "enabled": true, "interval_hours": <N or null for daily>, "work": true, "maxTasks": 1, "updated_at": "<now ISO>" }
   ```
   (The portal's scheduler reads this every cycle; `enabled:false` pauses without a restart.)
3. Ensure the portal is running — `curl -s http://127.0.0.1:${PORT:-8770}/healthz`. If it's down, start it with `/agent-team:portal` (or `cd standup/portal && (./run_local.sh >/tmp/agent-portal.log 2>&1 &)`). The scheduler LOOP runs by default whenever the portal runs, so NO special restart is needed — writing `schedule.json` in step 2 is enough to arm it.
4. Confirm the next fire time (from `/api/status` `runner.next_tick`, and `runner.scheduler.enabled` should now read true) and tell the user: recurring standup is ON (`<interval>`); pause it any time with `/agent-team:stop-daily-standup` (runtime, no restart); watch it in the portal. Each scheduled run does a full gated standup + work on the top task, exactly like `/agent-team:standup`.
