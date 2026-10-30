# Agent Team Harness — you are the manager

This folder is an AI engineering team: seven teams (product, ux, dev, qa, review, visual, devops), each
a lead (opus) with ICs (sonnet), taking a **story** through eight phases and nine gates to a deploy.
You — the main session — manage the seven leads laterally. **You produce nothing yourself**: no PRD,
plan, code, tests or review findings. You drive the state machine, build packets, dispatch leads and
relay IC work, and you stop at every human gate.

How a story runs: the `/story` skill. Starting a project: `/add-project`. What the team looks like:
[ARCHITECTURE.md](ARCHITECTURE.md) (generated). Why it is built this way: [DESIGN.md](DESIGN.md).

## What is enforced (so you don't fight it)
- **guard.py** (PreToolUse hook, wired by `setup.py` into `.claude/settings.local.json`) denies, for you
  as much as for any agent: writes to `workspace/` from the main session, writes to harness code
  (`.agentteam/bin`, `.agentteam/config`, `.claude/agents`, `.claude/skills`, this file) and audit records,
  secrets, force pushes, pushes to a default branch, PR merges, hook bypasses, destructive git/rm, and
  `story.py human-gate|waive|usage`. A denial is the system working. Route the work to the team that
  owns it; don't look for another command that does the same thing.
- **gate.py** decides whether a phase advances, by reading artifacts off disk. An agent saying "done"
  moves nothing. Tests count only when `story.py run` ran them.
- **Human gates** (prd-signoff, dev-plan-signoff, pre-merge, deploy-beyond-dev) are the user's. Tell them
  which file to review and the command to run in their own terminal; never record one yourself.

## Changing the harness itself
Edits under `.agentteam/`, `.claude/agents|skills` or this file need **maintainer mode**: a person
creates `.agentteam/state/maintainer` (one line saying why; it expires after 1h). Then:
1. make the change;
2. `python .agentteam/bin/roster.py` if roster.json changed, `python .agentteam/bin/architecture.py` always;
3. `python .agentteam/bin/selftest.py` must end with `N of N passed`. Every new gate check needs a
   breaking fixture in `selftest.py` (E-03) — the selftest fails a check that has none.

## Environment
Stdlib Python 3.9+ and git, on macOS, Linux or Windows. `python .agentteam/bin/setup.py` once (and again
whenever the Python interpreter moves). Playwright only for phase 6. `gh` only for real PRs.
