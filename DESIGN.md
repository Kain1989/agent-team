# Agent Team Harness v2 — design

This is the merged design: the **Agent Team Harness** architecture (seven teams, eight phases, nine
gates, four human stops), plus what **agent-team v1** (≤ 0.5.2) proved in production, plus the ideas
from comparable systems that held up. Where they disagree, this file says which one won and why.

v1 is archived, not deleted: its last state is tag `v1-final` on this repository (see the migration
section of [README.md](README.md)).

## Why a rebuild, not a refactor

v1 is a **tick-driven board** — a Workflow-tool engine (`standup.workflow.js`, 1,650 lines) whose phase
gates are mostly *model* verdicts (`sup:*` agents), and a FastAPI portal that parses its logs. The new
architecture is a **story-driven state machine** whose phase gates are *deterministic checks over
artifacts on disk*. The two differ in the unit of work (board tick vs. story), the role model (pairs vs.
lead + ICs), and the gate model (model-judged vs. machine-checked). Every v1 release test is bound to
the old shapes. Porting would have meant rewriting the engine, the roster schema, the hooks and the
portal parsers anyway — while keeping the old tests green. Rebuilding was the smaller job; the pieces
of v1 that were right are ported below.

## The shape

```
Supervisor (human) ── owns four human gates
   └── Claude (the main session) ── manages seven leads laterally, produces nothing itself
         ├── product  pm-lead      → 2 ICs     (readonly)
         ├── ux       ux-lead      → 3 ICs     (readonly)
         ├── dev      dev-lead     → 2 ICs     (dev)
         ├── qa       qa-lead      → 5 ICs     (dev, test files only)
         ├── review   review-lead  → 3 ICs     (readonly-git-comment, writes nothing)
         ├── visual   visual-lead  → 2 ICs     (dev)
         └── devops   devops-lead  → 2 ICs     (devops)
```

Leads decide (opus); ICs produce and are checked (sonnet). An IC never reports past its lead.

**Claude Code constraint, stated plainly:** a subagent cannot spawn subagents. So "a lead manages its
ICs" is implemented as two lead calls with the ICs between them: the lead writes an assignment
(`<phase-dir>/assignments.md`), the main session dispatches exactly those ICs with exactly those
packets, and hands their raw outputs back to the lead, who decides. The main session relays; it does
not edit, summarise or judge IC output. The `/story` skill encodes this.

Everything a team needs to know lives in one place: [`.agentteam/config/`](.agentteam/config/)
(`roster.json`, `pipeline.json`, `policy.json`, `budgets.json`). `architecture.py` regenerates
[ARCHITECTURE.md](ARCHITECTURE.md) from those files and from the live check registry, so the counts on the
front page can't drift from the code.

## Pipeline

| Phase | Owner | Artifact | Gate | Human stop after |
|---|---|---|---|---|
| 1 PRD | product | `01-prd.md` | g1-prd | **prd-signoff** |
| 2 UX | ux | `02-ux/spec.md`, `02-ux/decisions.md` | g2-ux | |
| 3 Dev | dev | `03-dev/plan.md` → code on a story branch | g3-plan, g3-impl | **dev-plan-signoff** (after g3-plan) |
| 4a Unit+Int | qa | `04a-qa/report.md` + harness-run results | g4a-qa | |
| 4b Sys+Reg+Perf | qa | `04b-qa/report.md` + harness-run results | g4b-qa | |
| 5 Review | review | `05-review/code-review.md` + `findings.jsonl` | g5-review | **pre-merge** |
| 6 Visual | visual | `06-visual/report.md` + judge results | g6-visual | |
| 7 Deploy | devops | `07-deploy/record.md` | g7-deploy | **deploy-beyond-dev** |

Defects flow back: review → dev (code) or → qa (tests); visual → dev. `story.py reopen` is the only way
back, and it counts rounds. Round caps: ux 3, dev 3, review 2, visual 2. A breached cap stops the story
(`status: escalated`) — the loop ends, it doesn't keep running.

Non-UI stories (`kind: cli|lib|service`) skip phases 2 and 6 with a recorded reason. The gate checks
that the skip is allowed for the story's kind.

## Three layers, named honestly

| Layer | Mechanism | What failing looks like |
|---|---|---|
| **Hard** | `guard.py`, a PreToolUse hook | the tool call never runs |
| **Audited** | `gate.py` reads artifacts off disk and runs the checks | the phase does not advance; failures listed |
| **Instructed** | agent definitions, packets, the `/story` skill | nothing stops it — the weakest layer, never the only one |

A policy violation is stopped by the hook, an incomplete phase by the gate, and a release by a person.
Each failure has its own owner.

## Decisions where the sources disagreed

**1. Guard failure mode: fail-open, but audited.** The deck's guard fails open on internal error so a
guard bug can't brick a session. v1 0.5.2 learned the opposite lesson: a hook that doesn't run is a
boundary that silently isn't there (Claude Code runs the tool, records zero denials). Merged: `guard.py`
fails open, but every internal error goes to `.agentteam/state/audit/guard-errors.jsonl`, and **every
gate fails while a guard error is recorded during its phase**. A broken guard can't brick a session, and
it can't go unnoticed either. `setup.py` and `selftest.py` also smoke-run the *configured* hook command
(v1's `gate_runnable` lesson), so a missing interpreter is caught before work starts.

**2. Role identity: from the hook input, not cwd.** v1 identified the supervisor by cwd, which forced
the `team_run_active` flag dance because subagents inherit cwd (anthropics/claude-code#12748). v2 reads
`agent_type` from the PreToolUse input (present on subagent tool calls). No `agent_type` → the main
session → role `supervisor`. An `agent_type` that is not in the roster (`Explore`, `general-purpose`, …)
gets the `unknown` role: readonly, no write roots. A secure default, not an open one.

**3. Human-gate forgery.** The deck documents that `story.py human-gate` writes whatever it's told.
v2 narrows that without claiming to close it: `guard.py` denies every agent (including the main session)
from running `human-gate` or writing under `stories/*/human/`, and `human-gate` refuses to run without an
interactive terminal and a typed confirmation of the story id — which Claude Code's Bash tool doesn't
have. Someone with shell access can still forge one. This is documented, not hidden.

**4. Clean context: the packet is built by code.** v1's `review_rule` said "reviewers see the diff
and criteria, not the writer's reasoning" — and then the engine pasted `APPROVED PLAN` and
`IMPLEMENTATION REPORT` into every reviewer prompt. A rule only in prose drifts like that. v2:
`story.py packet` copies only the allow-listed artifacts into `packets/<phase>/` with a hashed manifest.
`g5-review` refuses a packet that contains any withheld source or whose hashes don't match, and
`guard.py` denies review roles any read of withheld paths. If a reviewer wants the author's reasoning,
that gap *is* the finding: the code should explain itself.

**5. Who writes acceptance tests (from reward-hacking research).** EvilGenie and *The Verification
Horizon* both report coding agents — Claude Code included — weakening assertions, adding skip/xfail and
editing expected values to turn a suite green. `integrity.py` diffs the test files against the branch
base and refuses on any of these. v2 also makes it structural: **QA ICs may write only test files, and
dev roles may not write `tests/acceptance/**`**, so whoever writes the code never writes the tests that
judge it. There's also an escape hatch that ImpossibleBench found effective: a test the dev thinks is
wrong is filed as a `test-flaw` finding, never edited.

**6. Tests are run by the harness, not reported by agents.** v1 had a supervisor model "verify the
test gate for honesty". v2: `story.py run <story> <suite>` runs the exact argv from `env-report.json`
and records exit code, HEAD sha and an output digest. The QA gates read that record. A claimed pass that
the harness didn't run counts for nothing, and a pass recorded against an older HEAD is stale.

**7. Model tiering.** v1 policy: "never mix a weaker model into the challenge/review of stronger-model
output." The deck: leads on opus, ICs on sonnet. Kept the deck's tiering, with v1's concern honoured:
**a verdict is always a lead's (opus)**. ICs produce findings and drafts; leads accept, reject or merge
them, and only lead verdicts reach a gate.

## Ideas pulled in from comparable systems

| Source | Idea | Where it lives in v2 |
|---|---|---|
| GitHub Spec Kit | a project **constitution** of non-negotiable rules, checked at planning | `constitution.md` per project; g3-plan requires a *Constitution check* section |
| Kiro | requirements as **EARS** acceptance criteria; every task cites its requirement | g1-prd: every REQ has a `SHALL` criterion; g3-plan: every TASK cites REQ ids |
| BMAD-METHOD | self-contained **story files**; a fresh context per agent | the story directory is the unit; per-role packets |
| MetaGPT | agents exchange **documents, not chat**; each role subscribes to the outputs it needs | `pipeline.json` declares each phase's `reads` (its subscriptions) and `withheld` |
| Superpowers | **evidence before claims**; a fresh subagent per task, then a spec review and a quality review | harness-run tests (decision 6); review lenses split spec-compliance from code quality |
| EvilGenie / Verification Horizon | detect test-oracle tampering: assertions, skips, snapshots, golden files | `integrity.py`, including snapshot and golden-file edits |
| ImpossibleBench | an explicit "stop and report if the tests are flawed" channel cuts test hacking sharply | the `test-flaw` finding kind |

Carried over from v1 because they held up in use:

- **E-03, a judge must be shown to fail.** Every check in `gate.py` has a broken fixture in `selftest.py`
  that must turn it red.
- **The design rulebook and its machine judge** (`DESIGN_RULEBOOK.md`,
  `bin/visual/verify_design_quality.js`). The exit code decides; exit 0 proves nothing (E-07). These
  drive phase 6.
- **Secret scanning on every produced diff** (g3-impl).
- **No empty diffs.** v1 lost runs to an empty diff that reviewers read as a quality failure. g3-impl
  fails an empty diff with a message that names it.
- **Absolute interpreter paths baked in at setup, and a smoke run of the configured hook command.**
- **Cross-platform from day one.** v1 parsed Windows paths with POSIX `shlex` and failed 124 tests on
  Windows. v2 is stdlib Python 3.9+, uses argv lists rather than shell strings, and reads and writes
  UTF-8 explicitly.

## Not in v2.0 (roadmap)

- The Mission Control portal. Story state is plain files, so `story.py board` prints the board; a web
  view can be built on top of those files later.
- Red-before-green evidence for new tests (Superpowers' TDD gate).
- Mutation testing as a stronger test-integrity signal than diff heuristics.
- Concurrent stories: the harness tracks one active story at a time (`state/active.json`).
