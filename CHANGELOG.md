# Changelog

All notable changes to the **agent-team** plugin. Format: [Keep a Changelog](https://keepachangelog.com/);
this project adheres to [Semantic Versioning](https://semver.org/).

## [0.3.9] — 2026-08-06

**Deleting the sample — which the docs invite — broke the installer, five documents, and the eval
suite. Two external teams stopped here.**

### The problem this solves

`demo-app/` is a sample. The README says to point the team at your own repo; deleting the sample
afterwards is the obvious next move. It was never a supported path:

- **`setup.sh` died, exit 128.** `set -euo pipefail` plus `git -C "$ROOT/demo-app" init` on a
  directory that is not there is `fatal: cannot change to .../demo-app`, and the installer aborted
  with nothing installed. Guarding only the `init` block was not enough — the bare-origin, `remote
  add` and `push -u` lines that follow it were outside the block and produced the same fatal twice
  more.
- **One precondition, five documents, one of them right.** "If `demo-app/.git` is missing, init it"
  appears in `skills/standup`, `skills/work`, `skills/team`, `.claude/commands/standup.md` and
  `CLAUDE.md`. Exactly one — `skills/standup` — also said *"(and a `demo-app/` exists)"*. The four
  that did not include `/work`, the most-used entry point of the three that dispatch work.
- **`/eval` went silent.** The gold-set hardcoded `"target": "demo-app"` and both cases imported
  `textkit`. With the sample gone there was no target, no case could run, and nothing said so. A
  regression suite that reports nothing is indistinguishable from one nobody ran.

### What changed

- **`setup.sh`** guards the whole sample section, and says why it skipped. The guard opens *after*
  `DEMO`/`ORIGIN` are assigned: under `set -u`, a `$DEMO` referenced outside its own guarded block
  is an unbound-variable abort — the same closed door one line further down.
- **All five documents** now either carry the existence guard verbatim or delegate to the one copy
  that does. Editing `/standup` or `/portal` means editing both their skill and their
  `.claude/commands/` file; that pair had already drifted, which is how this was found.
- **`evals/resolve_cases.py`** decides RUN vs SKIP in code rather than in a prompt. Cases declare
  `requires`; a missing directory is a stated skip with a reason, never a pass, and "0 runnable" is
  printed as a complete answer with instructions for making it runnable.

### Judges (each proved it could fail before it was trusted)

- `standup/control/tests/test_setup_guard.sh` — slices section 5 out of the real `setup.sh` by its
  own marker comments, so it tests shipped text rather than a copy that drifts; a missing marker is
  exit 3, never a skip.
- `standup/control/tests/test_precondition_parity.sh` — **discovers** the sites by walking the tree
  instead of holding a list, because the sixth document, written next month by someone who never
  read the judge, is exactly the one a list cannot see (`E-05`, aimed at documentation).
- `standup/control/tests/test_eval_resolver.sh` — fixtures only; the repo tree is read, never written.
- CI now runs all three (each `--self-test` first) **and `test_sdlc_routing.js`**, which README had
  listed under Tests since it was written while CI never ran it — the one judge covering "the machine
  is aimed at the wrong thing" was itself unenforced.

Writing the judges caught three defects in the judges themselves, each the same shape as the bug
being fixed: the parity judge matched line by line and silently found 4 of 5 sites (`skills/work`
wraps "is" / "missing:" across a newline); it then failed a correctly-guarded paragraph because the
guard phrase straddled a line while discovery did not; and the eval judge's self-test reported PASS
off an unrelated failure while its mutation quietly no-opped. Under-discovery and false-attribution
in a judge read as green, which is why each one is now asserted by name.

## [0.3.8] — 2026-08-05

**The exemption that let dev agents write at all was documented in three places and armed by none.**

### The problem this solves

The Task/agent tool has **no `cwd` parameter**
([anthropics/claude-code#12748](https://github.com/anthropics/claude-code/issues/12748)), so every
subagent inherits the parent session's working directory. `hooks/supervisor_gate.py` identifies the
supervisor (EM) by exactly that cwd. Put together: every dev agent the engine dispatches is
classified as the EM, and its `Edit`/`Write` on the project folder it was sent to is **hard-blocked**.

The roster gives each developer a `folder`, and that data is correct — but a folder string cannot
become a process cwd. It can only be interpolated into a prompt, and a prompt cannot govern a hook.

`standup/control/team_run_active` has always been the exemption for this, and the gate has always
read it. **Nothing ever wrote it.** The gate's docstring said "the EM creates it before a team run";
`CLAUDE.md` repeated it; no code did it, and the plugin shipped no tool to do it by hand either. A
mechanism claimed in three places and wired in none is the same false-promise defect this project
keeps finding elsewhere — this time in the path that decides whether *any* code can be produced.

The failure mode is the expensive kind: silent. The dev agent plans, investigates, writes its patch,
passes its own test gate — and the fresh-context reviewer then correctly fails it for an **empty
diff**. The run reports `review-failed`, which reads as a code-quality problem and sends you looking
in the wrong place. On the system this plugin is distilled from, one such run cost 3h22m / 5.6M
tokens / 59 agents for zero commits.

### What changed

- **The engine arms the exemption itself.** `standup/standup.workflow.js` runs a `phase('Arm')`
  before Work on any run that writes code, and tears it down at the end. Read-only ticks skip it:
  they touch no project folder, and needlessly switching the gate off for 6h is its own cost.
- **A failed arm STOPS the run.** Continuing would burn the whole gated pipeline on a structurally
  guaranteed empty diff and then mislabel it `review-failed`. The error says what to do instead.
- **`standup/control/team_run_flag.sh`** — new. `status` / `set <run-id>` / `clear <run-id>` for
  hand-driven sessions. It **appends** rather than overwrites (runs can share the flag) and
  **refuses to clear** while another run's record is present — clearing it mid-run switches the gate
  back on and blocks every write that run has left to make. `clear` is never the safety mechanism
  anyway: a crashed run never reaches its teardown, which is why the gate's **6h TTL** exists.
- Why an agent runs a shell one-liner: workflow scripts have no filesystem access, and the agents
  they spawn have Bash. One cheap agent against a whole run that would otherwise produce nothing.

### Judge

`node standup/control/tests/test_sdlc_routing.js` — 5 new cases (96 total): the arm happens, it
happens **before** the first dev agent (arming afterwards is not arming), the teardown runs, and a
failed or null arm stops the run. Two new `--self-test` fixtures (14 total) prove those cases can go
RED: removing the arm step, and making an arm failure non-fatal.

### Also

- `.claude-plugin/marketplace.json` said `0.3.5` while `plugin.json` said `0.3.7`, so installed users
  were never offered 0.3.7's gate fixes. Both now read `0.3.8`.

## [0.3.7] — 2026-08-03

**Three gates that could not open, and one parse failure that silently changed which pipeline ran.**

### The problem this solves

A gated SDLC is supposed to stop bad work. These three defects stopped *all* work, and two of
them did it without ever reporting an error.

**A gate that stops on any reserve is a gate that never opens.** Both the INTAKE checkpoint and
the pair's plan challenge treated `approve=false` / `approved=false` as fatal. But a
conscientious reviewer sets that flag the moment it sees *anything* improvable — so the more
diligent the reviewer, the less could ever ship. Observed on the live system this day: three
consecutive runs on one task, ~4.6M tokens, **zero lines of code**, while the supervisor's own
verdicts read *"Fix the eight below and this ships — about a page of work, not a rewrite"*, then
*"DIRECTION, GRAIN AND DATA PATH: APPROVED"*, and finally *"DO NOT RE-PLAN. BUILD proceeds with
the must_fix applied."* It said go three times; the gate stopped it three times.

**The pair critiques; it does not veto.** Doctrine has always said pairs *critique*. The code
said otherwise: a critique of the form "direction is right, fix these four things" ended the task
as hard as one finding a fatal design error, and the `required_changes` already written never
reached the implementer.

**Unparseable args silently changed which pipeline ran.** `catch (e) { A = null }` looked like
"a few missing parameters". It is not: `args.task` disappears, so a single-task dispatch falls
into the whole-roster standup shape; `DO_WORK` goes false, so there is no Work phase at all and
the run is *structurally incapable* of producing code; the roster falls back to the embedded copy,
so every squad gets polled. One unescaped double-quote inside a task string was enough. Nothing
errored — the run just spent 38 agents standing up nine squads nobody had asked about.

### What changed

- `approve=false` / `approved=false` must now answer **`blocking`**: is this a real blocker —
  would work done against it have to be *thrown away*, not amended? Wording tightenings, optional
  hardening, "one amendment away" are all `blocking=false`, and the run continues carrying the
  `must_fix` / `required_changes`. A **missing** `blocking` field still stops: silence is not
  consent to proceed. A null verdict (dead agent) still stops: no verdict is not approval.
- A reserve still earns its **revision round** either way — that is how the objection gets
  absorbed into the contract rather than dropped. `blocking` decides only whether the run *stops*
  afterwards, never whether the objection is heard.
- Unparseable `args` now **throws**, with the byte offset and the offending text quoted. The real
  cure is at the call site: hand the Workflow tool an **object**, not a JSON string — objects
  never pass through hand-written escaping, so the failure mode disappears at source.

### Judge

`standup/control/tests/test_sdlc_routing.js` — **91 cases**, and `--self-test` drives **12 named
engine mutations** each of which must turn a *named* case red (`E-03`). Four of those fixtures are
new and cover exactly the reversals above, including one that restores `A = null` and one that
gives the pair its veto back. A fixture whose anchor stops matching the source is a hard error,
never a skip: a mutation that silently no-ops reads as a pass.

This release also lands the previously-uncommitted SDLC-entry port — INTAKE gated on *both*
entry paths, and a pipeline that refuses to run against an assignee, pair, folder or review
surface nobody declared.

## [0.3.6] — 2026-07-24

**The team could run every gate and still find nothing wrong — because no role was structurally able to.**

### The problem this solves

A gated SDLC with many agents can still miss an obvious, visible product defect. Not from
negligence — from role definition:

1. **A developer's standup schema only asked about progress.** Its required fields were all
   progress (project / health / done / next / blockers); none asked "what did you see that's
   wrong". Worse, the standup prompt told the dev to scope the report to their own lane, so
   cross-lane observation was explicitly *forbidden*. A real company's developer catches a defect
   the PM missed *because they use the product*; ours cold-started each tick, read its own
   progress file, and was asked one thing: how is your task going.
2. **The PM/UX persona was a name tag, never an instruction.** The only "personality" a staff
   agent actually received was the ~40-character string in its `role` field, buried under a much
   longer charter + rubric and a schema whose required fields were all rule ids. A Jobs-style
   judgment like "this whole page shouldn't exist" has no rule id, so under the team's own `E-01`
   ("a finding without a rule id doesn't enter the queue") it was dropped — the persona was
   structurally excluded from the one output that counted.
3. **The rulebook was authored entirely by the supervisor.** Supervisor writes the rules → the
   rules admit only findings citing those rules → agents can only find defects the supervisor
   already named. The team's cognition was bounded by the supervisor's.
4. **No role ever USED the product.** The design lead judged expert standards, the PM judged
   keep/kill, reviewers read the diff. None of them opened the product and tried to get a real task
   done — which is exactly why a visible defect could sit until a human happened to open it.

### Added
- **A discovery channel for developers** — `observations[]` on the standup report schema
  (`what` / `where` / `why_it_matters` / `outside_my_lane`). The dev prompt now explicitly asks
  for it and declares it **not lane-limited**; the old "scope this report to your lane" rule is
  narrowed to the *progress* part only. Reverse constraint: saw nothing → empty array, never
  invent to fill it.
- **`judgments[]`** on the design/PM schema — an independent-judgment channel that needs **no
  rule id**. Whole-surface calls ("this shouldn't exist", "these pages aren't one product") go
  here, scoped `view` / `surface` / `product` / `company`, on the one condition that they state
  what *should* be. `E-01` gains a branch: judgment-level conclusions are not bound by the
  cite-a-rule rule. Every `[MACHINE]` rule is single-page in scope, so a cross-page judgment is
  one only a human role can make.
- **A `product_qa` role** — the one role whose whole job is to USE the product as a user every
  tick (Playwright/curl), running real end-to-end tasks and reporting where it breaks. Its only
  criterion is "can I get the thing done"; "I don't understand this screen" is a complete report
  on its own; it spans every UI and belongs to no lane; it is forbidden from inventing problems
  to hand something in. Wired into the every-tick staff pulse with its own lens, so it actually
  runs instead of being a paper role. A `needs_bash` tools tier was added so it can operate the
  product without edit rights.
- **A distinct exit code for "the judge itself can't run."** `verify_design_quality.js` now exits
  **4** (Playwright/Chromium unavailable — the gate is broken, not the page), separate from 1
  (violations) and 2 (page could not load). Before, a missing dependency and a real defect sent
  the caller the same signal. It is wired into the review schema, the prompts, and the `E-07`
  veto: still fail-closed, but the wording points at the environment, not a phantom design violation.

### Changed
- **Personas are injected, and injected FIRST.** A new `persona` field (second-person *behavior*,
  not a research footnote) on `pm_agent`, `design_lead`, and `product_qa` is prepended to every
  staff prompt *before* the charter/rubric — a persona placed after a checklist is a persona that
  does not exist. The same injection lands on the `/team` dispatch path: the generated
  `.claude/agents/*.md` now carry the persona at the top of the body.
- **Rulebook ownership handed back to UX.** `DESIGN_RULEBOOK.md` now says it explicitly: the
  **design lead** owns the A–D rules (what good design is); the **supervisor** owns only the E
  meta-rules (a finding must be citable, verifiable, assigned). The supervisor defines that design
  judgment must be executable — not what good design is.
- **The board requires `acceptance` and `serves_goal` on every item.** `acceptance` is how the
  item is verified done (machine-checkable where possible; restating the task is not an
  acceptance); `serves_goal` is the goal it serves, with an honest `"NONE — <why / which goal is
  missing>"` allowed and a manufactured goal forbidden. This is the goal→execution link.

### Notes for adopters
- The judge's "cannot run" exit is now **4**, not 2 (2 is reserved for a page that could not
  load). Update any wrapper that keyed on 2 meaning "not installed".
- `product_qa` needs a running instance to use. Point it at your own app via `args.designUrl` or
  let it derive the URL from your project's run method — the persona uses a placeholder, no host
  is baked in.

## [0.3.5] — 2026-07-23

**A design gate — so someone in the pipeline is responsible for whether the screen is any good.**

### The problem this solves

You can run every gate in this plugin, pass all of them, and still ship an ugly, misleading
screen. Three failures compound:

1. **Every review lens was an engineering-correctness lens.** The ring was pair-diff +
   correctness + conventions-and-tests. All three ask "is this code right?" None asks "is this
   screen any good?" So UI quality was never a condition of green — not because anyone decided
   it shouldn't be, but because no role owned it.
2. **The design critique ran *after* the commit.** It was the last phase, downstream of Work.
   By the time it produced a verdict the code was already committed, so it was physically
   incapable of blocking anything. It was a report, not a gate.
3. **Its output went to a file the only dev who could act on it never read.** Findings were
   appended to the design lead's progress file as prose. Prose can't be cited, so a finding
   couldn't become a queue item — the same defects were re-discovered tick after tick and
   nothing landed. Anthropic calls this *quiet divergence*.

The fix is not "review harder". A **rubric** is a lens; a **rulebook** is a language. Findings
now cite numbered rules, machine-checkable rules are decided by a script's exit code, and the
whole thing runs where it can still say no.

### Added
- **[`DESIGN_RULEBOOK.md`](DESIGN_RULEBOOK.md)** at the repo root — numbered, citable rules:
  A (accessibility/operability), B (data-viz integrity), C (layout/hierarchy), D (typography),
  plus `E-01`–`E-07` meta-rules that govern the loop itself. Each rule is marked `[MACHINE]`
  (decided by a script) or `[JUDGMENT]` (decided by the design lens). Every rule came from a
  real recorded violation, not from theory.
- **A deterministic judge** — `standup/control/verify_design_quality.js`. Drives the live page
  with Playwright and returns an **exit code**: 0 no violations · 1 violations · 2 could not run.
  Implements all ten `[MACHINE]` rules (focus visibility, touch targets, contrast, error
  boundaries, chart axes, isotropic rendering, panel content fill, type scale, emoji headings).
  `--rule-ids` prints the citable ids; `--rules` hard-fails on an id that isn't in the rulebook
  (a typo used to silently skip the rule and report a false green).
- **`--self-test` and its deliberately broken fixture** (`standup/control/fixtures/`) — proves
  the judge FAILS on planted violations, covering every `[MACHINE]` rule. Required by `E-03`:
  *a judge that can't catch breakage isn't a judge*, and every design verdict is unreliable
  until the judge can fail. Wired into CI.
- **A 4th review lens, `design-quality`**, added to the review ring whenever the change has an
  **observable surface** (detected from the lane/task text *and* the files actually changed, so
  a backend lane whose change alters what renders can't opt out). It runs the judge first, then
  applies the `[JUDGMENT]` rules to a real screenshot.
  - **`pass` is bound to the judge's exit code in code, not in the prompt.** A non-zero exit
    forces `pass=false` even when the lens argues otherwise — the point of a mechanical referee
    is that its verdict isn't relayed by something that can decide to be lenient.
  - **`E-07`: exit 0 proves nothing.** The judge catches "looks wrong" and is blind to "looks
    right, is lying" — a page whose small-multiple charts are each normalized *per card* renders
    flawlessly while inverting the true ranking. So exit 0 is deliberately *not* forced to pass;
    an independent UX judgment still has to agree.
- **`E-01` enforced by existence, not presence.** Every design finding must cite a rule id *and*
  that id must exist in `DESIGN_RULEBOOK.md`. Ids cited but undefined are dropped from the queue
  and reported as `rulebook_proposals` — a new rule is proposed, queued and landed, never minted
  at the point of use.
- **Effort tiering** (`opts.effort`, previously used zero times — every agent in a run executed at
  one depth): `low` for mechanical evidence-gathering (standup reports, pulse, investigate,
  commit), `high` for judgment (all reviews, design, board synthesis, plan/challenge, the
  test-gate honesty check), `xhigh` for implementation. Squad sync, comms triage and the test gate
  are documented in-file as deliberately left inheriting, so the omissions don't read as oversights.
- **Conclusion-carrying narration.** The run was near-silent on the happy path — an hour of
  progress tree with no content. Every phase boundary now logs its **conclusion with the number
  that matters**: squad health, board size and P0 count, design score + violation count, the work
  queue, per-task plan/test-gate/review outcomes with the blocking verdict quoted, and the final
  tally. Never "starting X".
- **`standup/control/check_workflow_parse.js`** — catches the breakage class `node --check` misses.
  An unescaped backtick in a prompt ends the template literal early; the remainder often still
  parses, so `node --check` passes while the Workflow engine refuses to load the file and the next
  scheduled run dies silently at startup. This simulates the real harness instead.

### Changed
- **The design phase moved from last to before board synthesis.** Its tasks now land on *this*
  tick's board as queue items carrying their rule ids, instead of in a progress file. The design
  lead's every-tick pulse switched from re-reviewing the UI to tracking **delivery** — which
  rule-cited task is on its Nth tick without a commit.
- **`green` is derived from the lenses actually planned for the task**, not a hardcoded count of 3
  — which is exactly how a 4th lens gets added and silently ignored.
- **The design lead's deliverable is a design, not a defect list.** The schema now requires purpose,
  layout, states and *what to delete*: a PM/UX who only vetoes at a checkpoint adds nothing.
- **The gate binds on every dispatch path**, not just the workflow: `standup/team.json`'s
  `sdlc_pipeline` step 5 (the canonical review contract, which `/standup` and `/work` receive
  verbatim and `/team`'s teammate definitions are generated from) now carries the design lens, and
  the generated `.claude/agents/*.md` carry it too. An improvement that lands on one path while the
  others run the old shape is the quiet divergence above, wearing a different mask.
- The **visual/E2E** requirement in the test gate is now mandatory (not conditional prose) for an
  observable change, and the supervisor rejects unit tests, HTTP 200s or a prior screenshot offered
  as visual proof.

### Notes for adopters
- The judge needs Playwright (`npm i -D playwright && npx playwright install chromium`). If it
  isn't importable the judge exits **2**, never 0 — an unrunnable gate must not report "no violations".
- **The URL is a parameter**, never a baked-in default: pass `args.designUrl`, or let the agent
  derive it from your project's run method. Point it at your own instance.
- Keep the rule ids, replace the examples. Ids are the shared vocabulary of your reviewers, your
  board and the judge — renumber them everywhere at once or not at all.

## [0.3.4] — 2026-07-14

Complete the gated SDLC — add the gates the Work pipeline was missing (it went
IMPLEMENT → 2-lens review → commit, with the test only self-reported and no supervisor
sign-off).

### Added
- **Independent TEST GATE** after IMPLEMENT (`standup/standup.workflow.js`): a dev runs
  the checks and a SUPERVISOR verifies their HONESTY (commands really ran, evidence
  supports pass). unit/dev tests always; **integration** tests when the project has a
  suite; **visual/E2E** live verification when the task changes UI (real running
  instance, not an HTTP 200 or a screenshot).
- **Pair review of the DIFF**: the lanemate who challenged the plan now also reviews the
  actual change — so REVIEW is pair + two fresh-context lenses (3 reviewers), not 2.
- **Supervisor final review** of the COMMITTED diff — the last gate before it's called
  done (catches a rubber-stamped or wrong-files commit).

Full sequence now: INVESTIGATE → PLAN → pair CHALLENGE → IMPLEMENT → TEST GATE (+
supervisor honesty check) → REVIEW (pair-diff + 2 lenses) → COMMIT → SUPERVISOR final.

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
