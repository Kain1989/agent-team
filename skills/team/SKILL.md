---
name: team
description: Run your roster as a Claude Code NATIVE agent team (lead + teammates with a shared task list + peer mailbox) instead of via the Workflow tool — governed by the same guardrails + kill switch as the job queue. `/agent-team:team <task>` launches collaborative work; `/agent-team:team status` shows live native teams.
allowedTools: Read, Bash, Write, Task, TaskCreate, TaskList, TaskUpdate, Agent
---

This is the **native-teams** path: rather than the deterministic Workflow run (`/agent-team:standup`), spawn the roster as Claude Code's built-in agent team — independent teammate sessions that share a task list and a peer mailbox — while our governance (input/output guardrails, kill switch) and the portal ride along automatically.

## `/agent-team:team status` — observe live native teams (Bridge ③)

If the argument is `status` (or empty), just report, don't spawn:
`python3 standup/portal/parsers/native_teams.py`
Summarize the live native teams + their members + shared task counts (or "no native team is currently running"). The portal also serves this at `GET /api/native-teams`.

## `/agent-team:team <task>` — launch the roster as a native team (Bridge ④)

1. **Preconditions.**
   - Native teams must be enabled: `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` (in `~/.claude/settings.json` `env`, then **restart Claude Code**). If unset, tell the user to enable it and stop.
   - **Sync the roster → teammate defs** so the agent types exist: `python3 standup/portal/parsers/agents_gen.py .` (same as `/agent-team:sync-roster`). Read `standup/team.json` for the active roles + their pairs.
   - If `demo-app/.git` is missing, initialize it (see `/agent-team:standup` step 1).

2. **You are the team lead.** Create a native agent team and **spawn one teammate per active role** using the generated agent types (e.g. `dev_a`, `dev_b`, `portal_backend`, `portal_frontend`, `pm_agent`, `design_lead` — whatever the roster has). Keep each teammate in its lane (the role's `focus`); honor the pairs (`dev_a`↔`dev_b` challenge each other's plan + diff in fresh context).

3. **Put the work on the shared task list** and drive it through the **gated SDLC** (the same doctrine as the Workflow path): plan → pair challenge (fresh context) → implement + tests → test gate (the suite must actually run green) → 2-lens review → commit on green to an `auto/team-*` feature branch. **Never** push, merge, or deploy — that's the human's gate.

4. **Governance is automatic.** With this plugin enabled, hooks fire on the native lifecycle: `TaskCreated` runs the input guardrail + kill-switch check, `TaskCompleted` secret-scans the task diff (a leak **blocks** completion), `TeammateIdle` stops teammates if the kill switch is set. Don't bypass them. Have Mission Control open (`/agent-team:portal`) to watch the team live.

5. **Close out** like a standup tick: summarize the board + what each teammate did + commits on feature branches; append a `## team (<today>)` section to `standup/log/<today>.md`.

**When to use which:** `/agent-team:standup` (Workflow) = deterministic, repeatable, one top task per tick — best for scheduled runs. `/agent-team:team` (native) = collaborative, teammates talk via the mailbox and self-organize around the shared list — best for an interactive, multi-part task. Both obey the same gates.
