---
name: runs
description: Show the agent team's run history / timeline — recent jobs and scheduled standup ticks with status, cost, duration, denied tools (the gate firing), commits, plus recent notifications. Use when the user asks what ran, run history, recent activity, the timeline, or what happened.
allowedTools: Read, Bash
---

Show the team's recent run history (read-only). Run from the team project root:

```
cd standup/portal && ../.venv/bin/python -c "
from parsers import db, runs, notify, costs; db.init()
import json
jobs = [{'id': j['id'], 'type': j['type'], 'status': j['status'],
         'created': j.get('created_at'), 'finished': j.get('finished_at'),
         'cost_usd': (j.get('result') or {}).get('cost_usd'),
         'branch': j.get('branch'), 'commit': (j.get('result') or {}).get('commit_sha'),
         'denied_tools': (j.get('result') or {}).get('denied_tools')} for j in db.list_jobs(limit=20)]
ticks = []
try: ticks = runs.list_runs()[:10]
except Exception: pass
print(json.dumps({'jobs': jobs, 'scheduled_ticks': ticks,
                  'notifications': notify.recent(10), 'cost_today': costs.summary()},
                 indent=2, default=str))
"
```

Render it as a single **newest-first timeline** that combines:
- **Job runs** — id, type, status, cost, the branch/commit (for code tasks), and `denied_tools` (proof the gate fired). Call out anything `failed`, `awaiting_approval` (needs the human), or `rejected`.
- **Scheduled standup ticks** — name, health, worked/green/committed counts.
- **Recent notifications** (awaiting-approval / budget-breach pings) and **today's cost** (spend vs cap).

`$ARGUMENTS` may narrow the view (e.g. a status like `awaiting_approval`, a job type, or `today`). For deeper per-event detail, open the portal (`/agent-team:portal`).
