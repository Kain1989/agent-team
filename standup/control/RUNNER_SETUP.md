# Runner setup — the headless cron that drives the standup

The portal can only *ask* for a standup or PM review: it drops a request file into
`control/requests/`. Something has to actually *run* the gated workflow, because a
standup launches the Workflow tool, which only exists inside a live Claude session
— never as a plain CLI. That "something" is a **runner**: a headless `claude`
session driven by two small crons.

## The two crons

1. **Heartbeat** — `python3 control/heartbeat.py` on a 1-minute cron. It stamps
   `control/heartbeat.json` (so the portal knows a runner is alive and when the
   next tick is due), drains any queued portal request, and reconciles a
   running-but-unlocked tick onto `control/run.lock` so the portal always reads an
   in-flight tick as busy.
2. **Scheduled ticks** — four daily boundaries (morning / afternoon / evening /
   night). Each fires a headless, blocking `claude -p --permission-mode
   bypassPermissions` whose prompt is the runner duty:
   - read `team.json` as the roster;
   - launch the real workflow **in the background** with the Workflow tool
     (`scriptPath: "standup/standup.workflow.js"`);
   - poll its Task status to completion (this blocking wait is what keeps
     `claude -p` alive past the launch);
   - do the launcher duties — append `log/<date>.md`, update `BACKLOG.md`, post the
     run summary to your team channel;
   - never push, open a PR, merge, or deploy (those stay human-gated).

## Single-flight

Every launch path — a scheduled tick, a portal-triggered drain, and the portal's
own in-process scheduler loop — acquires the same machine-owned `control/run.lock`
before launching and releases it after. If the lock is already held the launch is
skipped, so a scheduled tick and a portal action can never double-fire.

## Two ways to fire the schedule (pick one)

- **Portal-hosted loop** — the portal daemon runs the scheduler loop itself (see
  `portal/parsers/scheduler.py`); firing is toggled at runtime via
  `control/schedule.json` (`enabled: true|false`).
- **External cron** — the four ticks are registered with your OS/agent scheduler
  and the portal just observes.

Run only one of the two so they don't race the same `run.lock`.
