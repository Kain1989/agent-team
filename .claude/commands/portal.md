---
description: Start the Mission Control portal (the team's visualization + management surface)
---

Start the Mission Control portal so the human can watch and manage the agent team. Do it now.

1. **Install once if needed.** If `standup/.venv` does not exist, run `./setup.sh` (venv, deps,
   gate config, runtime dirs).

2. **Launch the portal in the background** (it binds to 127.0.0.1 only) and capture the URL:
   ```
   cd standup/portal && (STANDUP_JOBWORKER=1 STANDUP_SCHEDULER=0 ./run_local.sh >/tmp/agent-portal.log 2>&1 &)
   ```
   Wait a couple of seconds, then confirm it's up:
   ```
   curl -s http://127.0.0.1:${PORT:-8770}/healthz
   ```

3. **Tell the human the URL** to open (e.g. http://127.0.0.1:8770 — or the `PORT` from `.env`).
   Explain what they'll see: the squads + dev health, the EM board, the live tick log, and the
   **job queue** — where they can submit a code task (target any developer's `folder`
   from `standup/team.json`), watch it run in an
   isolated worktree, review the diff, and **Approve** it to commit.

The portal reflects the same files the team writes, so any standup you run (via `/standup`) shows
up here live. Note: the runner hero will read "on-demand" — that's expected (the optional tick
scheduler is off; the job worker is what's running).
