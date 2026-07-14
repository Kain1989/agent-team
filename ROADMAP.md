# Agent Team — roadmap (v0.2)

Prioritized from a deep-research gap analysis (2026-06-26) against CrewAI, AutoGen/AG2,
LangGraph, the OpenAI Agents SDK, Microsoft's Agent Framework + Agent Governance Toolkit,
and the Claude Code observability ecosystem. The v0.1 core (roster, gated SDLC, paired
challenge, worktree-isolated human approval queue, Mission Control) is strong on
DEFINE/RUN/GOVERN-the-irreversible-step; these add the **daily-operations** pillars.

Tiers track the build order (A → B → C).

## Tier A — clarity / foot-gun fixes  ✅ DONE (v0.2)
- [x] **Scheduler foot-gun**: the loop now starts whenever the portal runs; firing is gated
  on `control/schedule.json` (default OFF). `/daily-standup` works after a plain `/portal`
  with no `STANDUP_SCHEDULER=1` restart. (was: portal-vs-daily-standup split = "why didn't
  my standup run?")
- [x] **Command clarity**: `/work` (one task) vs `/standup` (whole roster) boundary made
  unmissable; `/portal` job-type roles explained.

## Tier B — high-value daily-ops commands (P1–P2 + notify)  ✅ DONE (v0.2)
- [x] **`/costs`** [P1, MS Agent Governance Toolkit] — parse `total_cost_usd` from each
  `claude -p` JSON result, accumulate per-job + per-day in jobs.db, refuse `claim_next` past
  a daily cap, expose a kill-switch the worker checks each poll. Enforce OUTSIDE the agent
  (the trusted worker), so a runaway run can't bypass its own limit.
- [x] **`/runs`** [P2, OpenAI Agents SDK auto-tracing; disler hooks-observability] — a
  per-run history/timeline from `control/runs/` + jobs.db (+ a lightweight events table fed
  by the existing PreToolUse hooks); the portal renders a per-run timeline.
- [x] **notify-on-approval** — a local/webhook ping on `awaiting_approval` / budget-breach.

## Tier C — bigger lifts (P3–P7)  🟡 STARTED (v0.2): guardrails + intent-auth + /eval done; /resume + handoff are the next iteration
- [x] **`/eval`** [P4, LangSmith/Langfuse] — a regression suite over a gold task-set scoring
  success/latency/cost, run on prompt/roster change.
- [x] **Guardrail validators** [P5, OpenAI SDK guardrails] — programmable input/output
  CONTENT validators on job input/output, beyond the allowlist + deny-hook access control.
- [ ] **`/resume`** [P3, LangGraph checkpointers] — checkpoint a code-task so a crash /
  approval pause continues rather than restarts (re-architects the blocking subprocess).
- [ ] **Handoff / routing** [P6, OpenAI SDK handoffs, CrewAI] — an optional dynamic
  delegate/route step in the SDLC instead of the fixed plan→…→commit pipeline.
- [x] **Intent-based authorization** [P7, MS Declare→Approve→Execute→Verify] — plan-time
  approval + approver≠assigner separation (today: one binary diff approval; code flags
  "ASSIGNER-CAN-APPROVE").

## Tier D — native agent-team integration  ✅ DONE (v0.3)
Ride Claude Code's built-in agent teams (experimental) instead of only the Workflow tool —
the roster supplies the *people*, native supplies the *mechanics* (lead + teammates, shared
task list, peer mailbox), and our governance rides along. Four bridges:
- [x] **Bridge ① roster → teammates** — `/sync-roster` generates `.claude/agents/<role>.md`
  from `team.json` (tools by role, pairs wired in, stale pruned).
- [x] **Bridge ② governance hooks** — `TaskCreated`/`TaskCompleted`/`TeammateIdle` wrap the
  input/output guardrails + kill switch onto the native lifecycle (`hooks/hooks.json`).
- [x] **Bridge ③ portal observes** — `GET /api/native-teams` reads `~/.claude/teams`+`tasks`.
- [x] **Bridge ④ `/team`** — spawn the roster as a native team on a task; `status` lists live ones.

Next iteration: a Mission-Control UI panel for native teams (the data is already served), and
mapping the native plan-approval to our intent-auth gate.

## Why green/pass-fail isn't enough (the WHY behind B + C)
APM-style success codes mask quality failures — agents hallucinate, pick wrong tools, and
degrade silently while dashboards stay green. Hence the dedicated observability (/runs) +
evaluation (/eval) layers rather than trusting the status board + per-task review alone.