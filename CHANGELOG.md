# Changelog

All notable changes to the **agent-team** plugin. Format: [Keep a Changelog](https://keepachangelog.com/);
this project adheres to [Semantic Versioning](https://semver.org/).

## [0.3.3] — 2026-07-14

Add a **greenfield-aware INVESTIGATE phase** to the gated SDLC. Previously the Work
pipeline went straight to PLAN, so a plan could be built on imagination or a wrong
assumption (the #1 failure mode) instead of on observed reality.

### Added
- **INVESTIGATE (read-only) before PLAN** in `standup/standup.workflow.js`: an Explore
  agent gathers real evidence (findings, files in play, risks) and classifies the task
  as `brownfield` (modifies existing code) or `greenfield` (builds something new). PLAN
  is now grounded in that evidence instead of re-imagining the code.

### Fixed
- **Greenfield / from-scratch tasks no longer short-circuit.** INVESTIGATE judges
  FEASIBILITY, not readiness: a zero baseline, "it doesn't exist yet", or a dirty branch
  is the EXPECTED start of a from-scratch task, not a blocker. It stops only when the task
  is genuinely infeasible (the data/API/permission it needs doesn't exist, or the task
  contradicts reality).

## [0.3.2] — 2026-07-14

Tighten **supervisor mode** from lenient to the **aggressive** model. In 0.3.1 the gate
blocked only the dev target (`demo-app/`) — the EM could still hand-write the portal,
evals, research, or any other in-repo project. Now the EM **produces nothing**: every
project/deliverable goes to the team through the gated SDLC, and the EM directly touches
only the management / orchestration / governance primitives that run the team.

### Changed
- **PreToolUse gate** (`hooks/supervisor_gate.py`) inverted from a block-list to an
  allow-list. Directly editable now = ONLY: `standup/team.json`, `standup/BACKLOG.md`,
  `standup/PM_GOALS.md`, `standup/log/`, `standup/control/`, `standup/workflows/`, the
  orchestration engine `standup/standup.workflow.js`, the plugin's own governance dirs
  (`.claude/`, `.claude-plugin/`, `skills/`, `hooks/`, `.github/`), and any top-level doc.
  Everything else under the team root is a PROJECT and is **blocked** — notably
  `demo-app/`, `standup/portal/`, `evals/`, and `research/`. Outside the team root is
  still left alone; the `git -C demo-app …` init/commit is still **not** gated (first-run
  setup needs it).
- **SessionStart charter** (`hooks/supervisor_charter.py`) and **per-prompt reminder**
  (`hooks/route_reminder.py`) rewritten to the aggressive contract ("you produce nothing;
  all projects → the team; only management/orchestration/governance done directly").
- `CLAUDE.md` boundary + enforcement sections rewritten to match.

### Added
- **Escape hatch** — a fresh (< 1h) `standup/control/supervisor_override` with a one-line
  reason allows ONE blocked action and appends `standup/control/hotfix_audit.log`
  (timestamp + tool + target + reason); auto-expires so a forgotten one can't linger.
  For a trivial/urgent one-line hotfix only, never feature work.
- **Team-run exemption** — while a fresh (< 6h) `standup/control/team_run_active` flag is
  present, the gate steps aside: a native-team run's teammates ARE the team doing the
  project work, already governed by the `TaskCreated`/`TaskCompleted`/`TeammateIdle`
  lifecycle hooks and the gated SDLC.

## [0.3.1] — 2026-07-14

Make **supervisor mode** the enforced default. The EM role was advisory (a line in
`CLAUDE.md`, read as conditional), so a plain task request could slip into the EM doing
the squad's dev work itself — no gated SDLC, no workflow. Three cwd-gated hooks close
that gap; they fire only inside an agent-team checkout (found by walking up to
`standup/team.json`), so unrelated projects on the same machine are untouched.

### Added
- **SessionStart charter** (`hooks/supervisor_charter.py`) — injects the "you are the
  Engineering Manager, default = delegate" operating contract every session, so
  supervisor mode is the loud default instead of a conditional aside.
- **Per-prompt routing reminder** (`hooks/route_reminder.py`, `UserPromptSubmit`) —
  re-asserts supervisor mode every turn; a deterministic backstop against mid-session
  drift (same shape as the language-mirror reminder pattern).
- **PreToolUse gate** (`hooks/supervisor_gate.py`, matcher `Write|Edit|NotebookEdit`) —
  **hard-blocks** hand-editing the dev target (`demo-app/`, or any dev folder outside the
  `standup/` management area). The team's own files (`standup/`, `evals/`, the plugin
  dirs, top-level docs) stay directly editable; `git -C demo-app …` is left alone so the
  first-run init/commit of the squad's output still works.
- All three registered in `hooks/hooks.json`; pure `python3`, no external deps.

### Notes
- Scope: the gate enforces the one unambiguous boundary (don't hand-write the squad's
  code); fuzzy "is this squad-owned?" judgement relies on the soft charter + reminder.
  A multi-repo deployment additionally gates `git -C <dev> <mutating>` (hand-commit
  bypass); this single self-contained demo repo doesn't need that.

## [0.3.0] — 2026-07-01

Integrate with Claude Code's **native agent teams** (experimental). The roster + governance
now ride on the built-in team mechanics (lead + teammates, shared task list, peer mailbox) in
addition to the deterministic Workflow path. Four bridges:

### Added
- **`/sync-roster`** — generate native-team teammate definitions (`.claude/agents/<role>.md`)
  from `standup/team.json` (Bridge ①). Active devs get edit+bash tools, read-only staff get
  read-only tools; pairs are wired into each teammate's prompt. Stale generated files are
  pruned; hand-written agents are left alone.
- **`/team`** — run the roster as a native agent team (Bridge ④): `/agent-team:team <task>`
  spawns one teammate per active role and drives the same gated SDLC; `/agent-team:team status`
  lists live native teams.
- **Governance hooks** (Bridge ②) — `TaskCreated` (input guardrail + kill-switch),
  `TaskCompleted` (secret-scans the task diff; a leak **blocks** completion), `TeammateIdle`
  (stops teammates when the kill switch is set), registered in `hooks/hooks.json` so any native
  team running with this plugin enabled is governed like the job queue.
- **Portal observes native teams** (Bridge ③) — `GET /api/native-teams` + a parser reading
  `~/.claude/teams` + `~/.claude/tasks`, so Mission Control can show live native teams + their
  shared task list next to the job queue.

## [0.2.0] — 2026-06-27

Daily-operations pillars from a deep-research gap analysis (CrewAI, AutoGen/AG2, LangGraph,
OpenAI Agents SDK, Microsoft Agent Governance Toolkit).

### Added
- **`/costs`** — per-job cost capture (`total_cost_usd`) + per-day aggregation; the worker
  refuses new work past a daily cap (`control/budget.json`) and a kill switch
  (`control/kill_switch`), enforced outside the agent.
- **`/runs`** — run history / timeline (jobs + scheduled ticks + notifications + cost).
- **`/eval`** — first-cut regression suite over a gold task-set (`evals/cases.json`).
- **`/help`** — lists every command (read dynamically so it never drifts).
- **Content guardrails** — input validation at create + output secret-scanning on the staged
  diff before it reaches human approval (a leaked secret is failed, not click-through-approved).
- **Separation of duties** — opt-in `require_separate_approver` policy (`control/policy.json`).
- **Notifications** — `awaiting_approval` / budget-breach / guardrail-block pings (local log +
  bell + optional `STANDUP_NOTIFY_WEBHOOK`).

### Changed
- **Scheduler foot-gun fixed** — firing is decoupled from loop-start: the loop runs whenever
  the portal runs, firing is gated on `control/schedule.json` (default OFF), so `/daily-standup`
  arms recurring runs with no restart and a pause holds.
- `/standup` vs `/work` descriptions clarified (whole roster vs one task).

## [0.1.0] — 2026-06-25

### Added
- Initial publishable plugin: paired-squad team, gated SDLC (plan→challenge→implement→test→
  review→commit), Mission Control portal with a worktree-isolated code-task approval queue,
  and the roster/scheduling command set (`/standup`, `/work`, `/portal`, `/team-structure`,
  `/add-team`, `/add-role`, `/daily-standup`, `/stop-daily-standup`, `/standup-status`, `/init`).
