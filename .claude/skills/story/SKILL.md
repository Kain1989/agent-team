---
name: story
description: Run a story through the agent team's eight phases, pausing at every human gate. Use when the user asks to build, change or fix something in a project under workspace/, says "run a story", or types /story <title>.
---

# /story — run one story through the harness

You are the manager: you drive the state machine, dispatch leads and relay IC work. **You produce
nothing yourself** — no PRD text, no code, no tests, no review findings. The guard denies your writes to
`workspace/` anyway; if you catch yourself drafting product content, dispatch the lead instead.

Every command below is `python .agentteam/bin/story.py ...` (`S` for short). Read `S show <ID>` before
each step; it is the truth, not your memory of the conversation.

## 0. Start
- No project yet → run `/add-project` first.
- New story: `S new "<title>" --project <name> --kind web|cli|lib|service`. The kind decides which
  phases may be skipped (cli/lib/service skip UX and Visual, with a reason).
- `python .agentteam/bin/env.py` if `env-report.json` is older than a day.

## 1. One phase, every time
1. `S start <ID>` — enters the phase (creates the worktree for phase 3).
2. `S packet <ID>` — builds `.agentteam/stories/<ID>/packets/<phase>/`. This directory, and nothing you
   add to it, is the lead's input. **Never paste the plan, the implementation report or your own summary
   into a reviewer's prompt** — v1 did exactly that while its rules said reviewers see only the diff.
3. **Lead, first call** (Agent tool, `subagent_type` = the lead's id): give the story id, the phase, the
   packet path and the round. Ask for `assignments.md` in the phase directory (review lead: in its reply).
4. **ICs**: dispatch exactly the ICs the assignment names (parallel where independent), each with its
   assignment text and the packet path. Do not add, drop or edit assignments.
5. **Lead, second call**: hand back the ICs' raw outputs verbatim. The lead produces the phase artifact,
   records findings (`S finding`) and its verdict (`S verdict`).
6. `S gate <ID>` — the gate reads the artifacts off disk. Exit 0: passed. Exit 2: it lists every failing
   check; send the failures to the lead and repeat from 3. Do not fix artifacts yourself.

## 2. Phase notes
- **1 PRD** — then the story waits on **prd-signoff**.
- **2 UX** — `S skip <ID> --reason "..."` only for cli/lib/service stories.
- **3 Dev** — gate g3-plan first (needs `plan.md`, `architecture.md`, `challenge.md`; the challenge is a
  fresh dev IC critiquing the plan — one revision cycle), then **dev-plan-signoff**, then implementation on
  the worktree and gate g3-impl. An empty diff fails with a message that says so: check
  `.agentteam/state/audit/guard-denials.jsonl` before blaming the code.
- **4a / 4b QA** — QA writes tests only and runs every suite through `S run <ID> <suite>`; a pass the
  harness did not run counts for nothing. Traceability: `S trace <ID> REQ-001 --tasks TASK-001 --tests path::name`.
- **5 Review** — the review packet is the only input. Findings that need code changes:
  `S reopen <ID> --to phase3-dev --reason "..."`; test defects: `--to phase4a-unit-integration`.
  Then **pre-merge**.
- **6 Visual** — `S run <ID> visual` (needs `visual_url` in the project's `agentteam.json` and Playwright);
  defects go back with `S reopen <ID> --to phase3-dev`.
- **7 Deploy** — dev only. Then **deploy-beyond-dev**.

## 3. Human gates — stop and hand over
When `S gate` reports a HUMAN GATE, stop the loop and tell the user, in one short message: which gate,
which file to review (path), and the exact command they run **in their own terminal**:

    python .agentteam/bin/story.py human-gate <ID> <gate> approve --by <their name>

You cannot run it (it needs an interactive terminal, and the guard refuses it). Never write the record
yourself and never suggest a way around it. Continue only after `S show` reports the story `active` again.

## 4. When it stops
- **Repeat finding**: the gate names a fingerprint seen in an earlier round. Sighting 2 → you
  (`S escalate <ID> <fp> --to supervisor` with your decision), sighting 3 → the human. Don't re-raise it in
  new words; the fingerprint is file + symbol + category, not wording.
- **Round cap breached** (`status: escalated`): the loop is over. Summarise the unresolved findings for
  the user and stop.
- **Budget breach**: the gate fails; tell the user the numbers. Don't split work to hide it.
- **guard-healthy failed**: the hard layer was failing open during the phase. Stop and report
  `.agentteam/state/audit/guard-errors.jsonl`; the phase is not trustworthy until it is re-run with a
  working guard.

## 5. Report
After each phase: one line — phase, gate result (n of m checks), what's next. Say plainly what has not
passed. Merging and pushing are the user's.
