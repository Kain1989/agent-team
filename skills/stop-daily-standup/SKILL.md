---
name: stop-daily-standup
description: Stop the recurring (scheduled) daily standup. Use when the user wants to pause, stop, disable, or turn off the automatic/recurring standup.
allowedTools: Read, Bash, Write
---

Stop the recurring standup (runtime pause — no process restart needed). Do it now.

1. Write `standup/control/schedule.json` with `enabled:false` (preserve the other fields if the file exists; create it as `{ "enabled": false, "updated_at": "<now ISO>" }` otherwise). The scheduler reads this each cycle and will not fire while disabled.
2. Confirm via `curl -s http://127.0.0.1:${PORT:-8770}/api/status` that the runner shows no imminent fire (or note the portal isn't running, in which case nothing was scheduled anyway).
3. Tell the user: recurring standup is now OFF; the portal keeps running for monitoring + the on-demand job queue; resume any time with `/agent-team:daily-standup`. (This does NOT kill the portal — it just pauses the automatic runs.)
