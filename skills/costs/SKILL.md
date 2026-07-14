---
name: costs
description: Show the agent team's token/dollar spend — today's total, per-job costs, the daily budget cap, and the kill switch; and set the cap or stop spend. Use when the user asks about cost, budget, spend, how much it's costing, or wants to cap or stop the team.
allowedTools: Read, Bash, Write
---

Report (and optionally adjust) the team's cost/budget. Each job's cost comes from the `claude -p` result (`total_cost_usd`), captured by `agent_run` and stored in jobs.db; enforcement lives in the trusted worker (outside the agent), so a runaway run can't exceed the cap.

1. **REPORT** — run from the team project root:
   ```
   cd standup/portal && ../.venv/bin/python -c "from parsers import costs, db; db.init(); import json; print(json.dumps({'today': costs.summary(), 'recent': costs.per_job(limit=15)}, indent=2, default=str))"
   ```
   Render it clearly: today's spend vs the daily cap (and remaining), how many jobs ran today, whether the worker is currently **BLOCKED** (over cap or kill switch on), and the recent per-job costs. If `cap_usd` is null, say there is no cap set.

2. **SET / CHANGE THE DAILY CAP** (if asked) — write `standup/control/budget.json`:
   ```json
   { "daily_cap_usd": 5.00 }
   ```
   This is read at runtime (no restart). When today's spend ≥ cap, the worker stops claiming NEW jobs (already-approved commits still finish). Remove the file (or set null) for no cap.

3. **KILL SWITCH** (hard emergency stop) — create the empty file `standup/control/kill_switch` to immediately stop the worker from claiming ANY new jobs; delete it to resume. (The worker re-checks every poll, so both the cap and the switch take effect within a couple of seconds.)

Note: the cap/kill-switch block NEW claims; they do not interrupt a job already running. For per-run history use `/agent-team:runs`.
