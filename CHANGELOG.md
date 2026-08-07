# Changelog

All notable changes to the **agent-team** plugin. Format: [Keep a Changelog](https://keepachangelog.com/);
this project adheres to [Semantic Versioning](https://semver.org/).

## [0.4.1] — 2026-08-06

**`/add-project` takes three kinds of source, and all three end in a project the team can actually
review.**

### The problem this solves

`/add-project` could only clone. Kain's cases are broader — start something new here, or register a
folder already copied in — and both of those produce a directory that a clone never does: **not a
git repo**, or **a repo with no commit**, or **a repo with no `origin`**. Each of those breaks a
different part of the pipeline, and two of them break it *silently*:

- **A project that is not its own repo does not fail quietly — it writes into your installation.**
  In a cloned agent-team install the plugin root is itself a repo, so `git -C <project> …` resolves
  to the enclosing one. Measured: `checkout -b feature/task-1` moved the **outer** repo's HEAD, and
  `git add -A` staged unrelated in-flight work from elsewhere in the tree. Several sessions share
  that working tree — it is why the daily push job never auto-commits.
- **`git -C` only warns when there is no repo at all** (exit 129). Inside a parent repo it exits
  **0 with empty output**.
- **A repo with an unborn HEAD is the same failure by another route.** Untracked files are
  invisible to `git diff`, so the review ring reads an empty diff whatever the developer changed.
- **No `origin` disables the portal's code-task loop**, which is this plugin's headline feature —
  `worktree.py` resolves origin's default branch and hard-errors without one. The skill file already
  carried that warning; the two new modes would have produced exactly that repo.

### What changed

- **Three modes** — `clone` / `new` / `adopt`, explicit. The bare `<git-url>` form still works
  (0.4.0 shipped it) but only when the first token is genuinely a URL; `^[A-Za-z]:`, `./`, `../`,
  `/` and a lone mode word are all excluded, and anything else refuses with the three forms rather
  than guessing. Guessing a mode is how you clone over a directory somebody meant to adopt.
- **One guarantee for all three**: own git repo, ≥1 commit, an `origin` — with a local bare origin
  created for `new` and for `adopt` when the source has none, generalising the offline mechanism
  `setup.sh` already uses. An existing `origin` is never touched: a dead one still resolves locally
  and fails honestly later, and re-pointing someone's remote is not this command's business.
- **`adopt` scans for secrets before committing** with the plugin's own `guardrails.py`, refuses on
  a hit naming the files, and seeds a `.gitignore` **derived from what is actually present** — an
  adopted folder is *more* likely to hold a `.env` than a clone, precisely because nobody has ever
  written it one. Measured at ~20,000 files/s: a 3,600-file tree costs 0.17s, so this is not a slow
  refusal path. **The seeded `.gitignore` is committed in the baseline and named in the output** —
  a file we wrote into the user's project is something they should be told about, not something to
  discover in `git log`, and one that is not in the baseline cannot protect the baseline commit,
  which is the most dangerous one.
- **`--allow-empty` was considered for the baseline and rejected on measurement.** It solves the
  secret problem and reintroduces the empty-diff one: pre-existing files stay untracked, so an edit
  to the user's source produced `[]` where a tracked baseline produced a normal diff. The judge
  demonstrates both.
- **Author identity is never invented.** `Author identity unknown` is a refusal with the two
  `git config` lines. The bundled sample uses a `demo`/`demo@local` identity because it is a
  throwaway; attributing a commit in someone's own source to a fabricated author is a different
  decision.
- **`new` writes `kind: none` and no `inspect` at all** — not an empty string, not a placeholder.
  Nothing validates `inspect` when kind is `none`, so an invented value passes every check and stays
  wrong. The `how` note records the true behaviour instead of "revisit later": `_touchedFrontend`
  turns on both `DESIGN_LENS` and `VISUAL_DQ` the moment a diff touches frontend paths or
  extensions, regardless of the declaration, so a `new` project is gated the instant it grows a UI.

### Detection rebuilt — `os.path.isdir(.git)` is wrong in both directions

Replaced outright, not supplemented:

- a `git worktree` or submodule checkout has a `.git` **file** (measured: 98 bytes), so `isdir`
  called a directory git handles perfectly "not a repo" — the likely shape of an adopted folder;
- a **symlink** to a repo makes `os.path.isdir(link/.git)` return True, because Python follows it.

The test is now `git -C <dir> rev-parse --show-toplevel` == `realpath(<dir>)`, **then**
`rev-parse --verify HEAD`. **That order is load-bearing** and is written into the code: inside a
non-repo child of a repo, `rev-list --count HEAD` returns 1 and `is-inside-work-tree` returns true
— the parent answers for the child. Only the toplevel comparison discriminates, so asking about
commits first proves nothing about the project.

### Two things the checker could not see

- **Symlinked project directories are refused** — `/name/` in `.gitignore` does **not** match a
  symlink (trailing-slash patterns are directory-only; measured `check-ignore` rc=1 against rc=0 for
  a real directory), so `git add -A` stages it as mode **120000**, a blob holding a path out of the
  tree. Refusing closes the entrance; separately, `gitlinked_paths()` now flags `120000` as well as
  `160000`, because a checker should not keep reporting green about a mode it cannot see.
- **Squad ids compare case-insensitively.** macOS and Windows default to case-insensitive
  filesystems, so `MyApp` and `myapp` are one directory while an exact `==` sees two ids — measured:
  after `mkdir MyApp`, `isdir("myapp")` is True. `adopt MyApp` would have slipped past the duplicate
  check and produced two squads sharing one folder.
- **The management-path deny list is derived at runtime** from `hooks/supervisor_gate.py`'s own
  constants, never transcribed. The hand-written list in the plan already omitted `hooks/` and
  `skills/`.

### Judges

`test_add_project.sh` grows to **18 checker branches**, each with an independent covering case and
its own `if False:` mutation — written in the same edit as the branch, not added after review.
Its `--self-test` now runs the mutations **concurrently** (~24s instead of ~2 minutes); coverage is
unchanged, each still executes the full suite and must redden its own named case.

Group D demonstrates rather than asserts: it drives git into the non-repo shape and shows the
enclosing HEAD move and the unrelated file being staged, and it shows an `--allow-empty` baseline
producing an empty diff where a tracked one produces a real one. Those are the two rules hardest to
argue from a description, and they are the ones a future reader will be tempted to relax.

## [0.4.0] — 2026-08-06

**A fresh install had a broken path at both ends: the team would run a whole tick against a roster
that could dispatch nobody, and there was no single command to give it something to dispatch. This
release fixes the stop and the answer it points at — which is why the two halves ship together.**

Run `/standup` on a new install and it did all of this: polled a roster with no active developer,
ran Comms, Standup, Design, Synthesize and Staff Pulse against it, **armed the supervisor-gate
exemption** — writing into the user's project and switching their gate off for six hours — and then
printed `TICK DONE — 0 task(s)`, which reads like success. The engine now stops before any of that
and says what to do instead: `add a project with /add-project <git-url>`. That command did not
exist; it does now, and it is the one command between "installed" and "working on your own code".

> **RELEASE CONSTRAINT — these two halves ship in one push, never separately.** The empty-roster
> stop names `/add-project`, and `test_sdlc_routing.js` pins that string (`the stop names the
> command that fixes it`) — so if the engine half shipped alone, a judge would be holding a false
> promise in place. An error message naming a command that does not exist is worse than one offering
> no suggestion: it sends the reader searching for something that was never there. If the engine
> half ever has to be reverted alone, that string must change in the same commit.

### The problem this solves — the engine end

- **A run handed no roster worked a different team and reported green.** `EMBEDDED_ROSTER` was a
  hardcoded copy used whenever `args.roster` was absent, falsy, or an unparseable string. Measured
  on five inputs — key absent, `undefined`, `''`, `{}`, a truncated JSON string — every one produced
  a full, clean-looking tick. The asymmetry that made it obvious once seen: three lines above, an
  unparseable `args` **string** already threw; an unparseable `args.roster` string was swallowed.
- **An undispatchable roster ran the whole tick.** `{}`, `{teams:[],staff:[]}` and an
  all-`active:false` roster produce byte-identical output, because the squad filter collapses
  "nobody active" and "no squads" into one state. Arm is the expensive part of that, for the reason
  above.
- **Arm could write the flag into a neighbouring install.** It located its helper by RELATIVE path
  from an inherited cwd. With two agent-team trees on one machine that resolved into the neighbour,
  whose helper truthfully reported `team_run_active PRESENT` — about the wrong repo. Where no such
  tree existed, the `mkdir -p` fallback CREATED the missing directory and the verification (`ls` on
  a path `mkdir -p` had just guaranteed) confirmed it: a check that could not fail.
- **`RULEBOOK_PATHS` had the same bug one function away, already firing.** From the host checkout it
  read the host's `DESIGN_RULEBOOK.md` (has `B-12`, no `F-01`); from the plugin, the plugin's (has
  `F-01`, no `B-12`) — both reporting the identical `rulebook_source: "DESIGN_RULEBOOK.md"`. Read
  the wrong one and this plugin's own `F-01..F-07` are rejected by `E-01` as rules that do not exist.

### The problem this solves — the onboarding end

Pointing the team at your own repo took four edits, in order, and getting any one wrong failed
later, somewhere else, with an error about something different: clone the repo; add a squad with a
paired set of developers whose `folder` is it; declare a `review_surface` with a runnable `inspect`;
add the clone to `.gitignore`. Three were documented in README. **The fourth was not** — and it is
the one that bites quietly: the clone is a git repo inside this one, so a later `git add -A` records
it as a **gitlink** (mode `160000`), a pointer to a commit nobody else can fetch. Nothing breaks at
add time; it surfaces at commit time looking like a git problem.

### What changed

- **No embedded roster.** Missing, non-object, or unparseable `args.roster` stops the run. That and
  the empty-roster case route through one `stopTick`, placed immediately after `stopTick` exists and
  before any phase — not earlier, because `stopTick` is a `const` and reaching it from the
  resolution site is a TDZ `ReferenceError`.
- **Arm resolves an absolute root and the ENGINE asserts identity.** The agent walks up for an
  anchor (`standup/team.json` + `standup.workflow.js`, nearest wins), arms via the absolute path,
  and verifies against `team_run_active PRESENT` — a string `mkdir` cannot fabricate. It reports the
  tree's team/dev ids and the engine compares them with the roster it holds in memory. That
  comparison is the load-bearing half: a neighbour's helper is not lying when it says PRESENT, so
  **no check the writer performs on itself can catch this** — it needs a fact the writer never had.
  A sorted id projection is compared, never a deep equality. The `mkdir -p` fallback is deleted: a
  file written where the gate does not read is worse than no file, because it reports success.
- **`rulebook_source` is the resolved absolute path plus the id count**, and a candidate is accepted
  only once shown to belong to this install. Unverifiable is **rejected**, not accepted-with-a-note.
- **`/add-project <git-url> [name] [--kind K] [--inspect CMD]`** does all four onboarding steps and
  ends by running the checker. `name` is used in three places at once — the directory, the squad id,
  and the developer-id prefix (`-` becomes `_`) — so it has to be free in all three.
- **`/remove-project <name>`** is the inverse and **never deletes your repository**: removing a
  squad is reversible, deleting a working tree is not. It reports `evals` targets, staff
  `scope_folders` and cross-squad `pair` references **before** editing, because a dangling `pair` is
  something the engine stops a run on.
- **Headless is a first-class path.** A missing `--kind`/`--inspect` is a question in an interactive
  session and a **refusal with a paste-ready re-run command** in a non-interactive one. A question
  asked into `claude -p` is not a question, it is a hang that dies on a timeout with nothing to show.
- `/help` leads with **Setup**: whoever types `/help` most likely just installed this and has no
  project, and the answer to their real question is `/add-project`.

### `standup/control/verify_project.py`, and the step that runs it

The four onboarding invariants are checked by code rather than by a checklist in a prompt, for the
same reason `/eval`'s RUN/SKIP decision moved out in 0.3.9: a prompt cannot run `git ls-files -s`.
It exits 1 naming the field to fix, and **2** — not 1 — when `standup/team.json` does not parse,
because that is not "an invariant failed", it is "nothing can be checked and every other command is
broken too".

Both commands run it as their final step. That wiring is the point, and it was missing from the
first cut: the script existed, CI ran it, two judges tested it — and **no skill file mentioned it**,
so from the product path it was unreachable and the headline gitlink invariant was still enforced
only by a sentence in a prompt. The 0.3.9 precedent was cited to justify the design and then not
followed; `/eval` actually calls its checker at step 0. This repo has the same failure already
recorded: `/work`, referenced by three governance documents and backed by nothing.

### `/remove-project` keeps the `.gitignore` line

Found by a user walkthrough, not by a judge. Removing the squad used to also remove the ignore entry
— while deliberately leaving the clone on disk. That does not tidy anything up: it re-arms the
gitlink this release exists to prevent, silently, on the next `git add -A`. The entry now stays as
long as the directory does, the output says so, and the checker's invariant is conditional on the
directory rather than on the roster.

### "Never deletes your code" is now a check that can fail

The single assertion behind that promise — stated in the skill frontmatter, the README table, this
changelog and the commit message — called `_ok` on **both** branches, including the one where the
directory was gone. It printed `ok` at the exact moment the promise broke. It cannot be checked
after the fact, so `/remove-project` records the directory's state before editing and passes
`--code-before`; without it the checker reports NOT CHECKED rather than `ok`.

### Judges

`test_arm_path.sh` (new), `test_add_project.sh` (new, **14** checker branches each with its own
covering case), `test_remove_project.sh` (new), and `C4`/`C5` groups in `test_sdlc_routing.js`
(**111** cases). CI runs every judge, and every judge that has a `--self-test` runs it first.

The arm judge builds its decoy as a real lowercase `standup/` directory rather than relying on
macOS case-folding: CI is `ubuntu-latest` and case-sensitive, so a `STANDUP/` decoy would never
resolve there, "the decoy is untouched" would be vacuously true, and the `E-03` mutation would stay
green on the only machine that runs it automatically.

One coverage hole found at the integration gate and fixed there: `test_arm_path.sh`'s "nearest
wins" check was `grep -qi 'first'`, which matches the prompt's prose in two places — so deleting the
`break` from the shipped walk, making the **outermost** install win (the nested-install hijack that
resolution exists to kill), left the judge at exit 0. It now greps the loop control itself. The
sibling anchor-pair check had been hardened for this exact reason and this one was left loose.

`test_remove_project.sh`'s central assertion is **byte equality** of `team.json` after add → remove
— what makes "surgical edit" testable rather than aspirational. Case B earns it: a parse-and-dump
removal still parses, still carries identical DATA, and is caught only by the byte compare. The
judge's header says outright that normalising before the compare would delete the only thing it
checks.

Every mutation is split **one per branch**, and both first cuts failed that: a single fixture
neutralising `if (ROSTER_ERROR)` reddened only the missing-roster case because the other branch
caught the rest, and `test_add_project.sh` printed "every checker branch has an independent covering
case" while **six** survived — including the `--kind` typo guard and three of the four assertions in
the `/remove-project` checker, on the command whose headline promise is "never deletes your code".
The self-tests now print the number of branches they actually neutralised.

### Known gaps, stated rather than implied

- **The Arm identity assertion does not cover a TWIN checkout.** Resolution and identity cover
  **disjoint** sets, not two layers of one defence: resolution kills the case-folding hijack (the
  one actually observed), identity catches a cross-project decoy whose roster differs, and neither
  catches two checkouts of the *same* repo — identical rosters so identity agrees, valid anchor so
  resolution accepts. It arms the wrong tree and logs `verified`. That is the most likely two-tree
  layout for a published plugin: a marketplace install beside a git clone. Closing it needs a
  **run-scoped** fact — a nonce the engine writes and reads back, or the realpath+size of the
  running `standup.workflow.js`. Not built here.
- **Surgical editing is specified, not enforced.** Both commands declare `allowedTools: Read, Bash,
  Edit` and not `Write`, but `Bash` remains, so a `python3 -c` rewrite is still reachable. The
  `_comment` fields in `standup/team.json` are the only place parts of the schema are documented.
- **Both new judges test a reference implementation, not the commands.** `/add-project` and
  `/remove-project` are prompts; the judges prove the invariants are checkable and that the checker
  has teeth on every branch. Whether a model follows the prompt is judged by a human walkthrough,
  which is the right instrument for that half.
- **One headless run in five returned exit 0 having done nothing** — it printed an intention and
  stopped. 4/5 were correct and 0/5 hung, so the refuse-rather-than-hang requirement holds; an
  exit-0 no-op is the worst shape for an unattended caller. **Observed once, not reproduced.**
- **Editing the roster while a run is in flight produces the WRONG diagnosis and leaves the gate
  open.** `args.roster` is the launcher's snapshot at t=0; the Arm agent re-reads `team.json` from
  **disk** later, in Phase 3.5. Run `/add-project` or `/remove-project` inside that window and the
  two projections diverge, so the identity assertion throws `ARM armed the WRONG install` — a
  diagnosis that is simply wrong for this cause, and whose fix line ("launch the Workflow with a cwd
  inside the install you mean to run") does not apply. Worse, the flag is already set by then, and
  `disarmTeamRunExemption()` is called at top level rather than in a `finally`, so the throw skips
  it: **the user's supervisor gate stays OFF for up to 6h**. The window is short for `/work` and
  **hours** for `/standup`, where Arm follows the whole roster poll.
  It fails **safely** — it stops rather than arming the wrong tree, and not disarming is the
  *correct* behaviour in a genuine wrong-install case, which is why this is not a quick fix: telling
  the two apart is a real engine change, and the 6h TTL is the backstop meanwhile. Direction when it
  is fixed: compare the two id sets, and if the armed set is a superset/subset of `ARM_EXPECT_*`
  with a plausible `resolved_root`, say "the roster changed during this run (added: …) — relaunch"
  instead of "wrong install". Both new commands now carry a "do not run this during `/standup` or
  `/work`" warning.
- **`test_add_project.sh` and `test_remove_project.sh` have no minimum-case floor.** Deleting
  `run_cases` entirely yields `all checks PASS`, exit 0. Their `--self-test` also only asserts that
  the named case goes RED, not that the others stay green — every one of the 14 mutations is
  surgical today (each reddens exactly its own case, spot-checked independently), but a future
  catastrophic mutation would read as a pass.
- **Filesystem access from a workflow script has never been observed either way.** `RULE_IDS_SOURCE`
  only ever entered the return object, so 27 recorded runs say nothing. Rather than spend a run
  finding out, both paths were made correct: Arm needs no `fs` at all, and the rulebook read treats
  no-`fs` as a **degrade** that says so, never a stop. It is now logged, so the next real tick
  becomes the evidence.

### A note on the `[0.3.9]` entry below

Three lines in it were **edited after publication** — `main` is `2d9c3de`, so 0.3.9 was already out.
**All three were wrong on the day they shipped; this release's work surfaced them.** Checked against
the published tree: the quoted code fragment `if (t.folder && !owned.includes(t.folder)) stopTick(...)`
**never** matched the source — at `2d9c3de` it was already a two-line construct squeezed onto one
line — and is now a form that greps; "twelve lines later" was **seven** at `2d9c3de` too
(`setup.sh` is byte-identical between then and now, marker at `:111`, guard at `:118`); and running
the published parity judge on the published tree audits **7** sites, so "five documents" was
conflating two different fives from the start.

Correcting them follows this repo's own rule that a number copied into prose rots like a version
number. It is still a rewrite of published history, which is why it is stated here rather than left
to be found in a diff.

The first draft of this very note got the attribution backwards — it said the engine changes "made
them untrue as written", which turns *we published three false statements and quietly fixed them*
into *our new work invalidated three accurate ones*. Nobody had asked for that framing; it drifted
that way on its own, in the paragraph whose entire purpose was honest disclosure. Recorded because
the mechanism is worth more than the correction: a narrative nobody is checking will drift toward
the flattering version, and it reads perfectly reasonable while it does.

### `/sync-roster` prunes — verified, not assumed

`agents_gen.py` rewrites the agent directory and unlinks any file carrying its own `generated from
team.json` header whose role left the roster; a hand-written def with no header survives. So
`/remove-project` does **not** delete `.claude/agents/*.md` itself — and must not, since doing it by
name would also catch a hand-written file that happened to collide.


## [0.3.9] — 2026-08-06

**Deleting the sample — which the docs invite — broke the installer, the documents that tell you
how to run it, and the eval suite. Found from questions two external teams raised after trying the
plugin.**

### The problem this solves

`demo-app/` is a sample. The README says to point the team at your own repo; deleting the sample
afterwards is the obvious next move. It was never a supported path:

- **`setup.sh` died, exit 128.** `set -euo pipefail` plus `git -C "$ROOT/demo-app" init` on a
  directory that is not there is `fatal: cannot change to .../demo-app`, and the installer aborted
  with nothing installed. Guarding only the `init` block was not enough — the bare-origin, `remote
  add` and `push -u` lines that follow it were outside the block and produced the same fatal twice
  more.
- **One precondition, five documents, three of them wrong.** "If `demo-app/.git` is missing, init
  it" appears in `skills/standup`, `skills/work`, `skills/team`, `.claude/commands/standup.md` and
  `CLAUDE.md`. Two were already correct — `skills/standup` guarded it inline, `skills/team`
  delegated to that copy — and neither is in this diff. The three that stated it unguarded include
  `/work`, the most-used entry point of the three that dispatch work.
- **`/eval` went silent about *which cases it could not run*.** The gold-set hardcoded
  `"target": "demo-app"` and both cases imported `textkit`. With the sample gone there was no
  target, no case could run, and nothing said so. A regression suite that reports nothing is
  indistinguishable from one nobody ran. **This release fixes the reporting, not the running** —
  see the known limitation below.

### What changed

- **`setup.sh`** guards the whole sample section, and says why it skipped. The guard opens *after*
  `DEMO`/`ORIGIN` are assigned: under `set -u`, a `$DEMO` referenced outside its own guarded block
  is an unbound-variable abort — the same closed door one line further down.
- **The three unguarded documents** now carry the existence guard verbatim, joining the two that
  already did. Editing `/standup` or `/portal` means editing both their skill and their
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

### Known limitation — `/eval` still cannot run a case in an isolated copy

Fixed here: `/eval` now says which cases it skipped and why. **Not fixed here:** the copy-based
isolation its own recipe describes does not survive contact with the engine. In
`standup/standup.workflow.js`, the reviewer prompts are built from the roster's folder —
`const folder = t.folder || dev.folder || team.folder || '.'` — and interpolated into
`git -C ${folder} diff -- .` (and `git -C ${folder} show HEAD -- .` for the supervisor's final
read). `git -C` resolves against the process cwd, so the reviewer reads the *real* target while the
work happened in the copy: an empty diff, reported as `review-failed`. Re-pointing `folder` at the
copy is refused by the ownership check that stops on a folder the assignee does not declare
(grep the engine for `!owned.includes(t.folder)`). `skills/eval/SKILL.md` carries this
warning inline with the two workarounds. Do not read a `review-failed` from an eval as a quality
regression without checking which directory was read.

(Those are quoted as code rather than line numbers on purpose. This repo already refuses to print
test counts in prose because "a number copied into prose rots exactly the way a version number
does" — a line number rots faster, and one of the five cited here had already drifted from `diff`
to `show HEAD` by the time it was reviewed.)

### On the judges themselves

Writing and then reviewing these judges caught **six** defects in the judges, every one of which
read as green — the same disease they were written to catch, one level up:

- the parity judge matched line by line and silently found 4 of 5 sites (`skills/work` wraps "is" /
  "missing:" across a newline), then failed a correctly-guarded paragraph because the guard phrase
  straddled a line while discovery did not;
- it walked all 43 markdown files, so a **changelog entry recounting this very bug** would have
  failed it — it passed only because narrative prose happened to repeat the guard phrase two lines
  later. It now reads the instruction surface (`skills/`, `.claude/commands/`, `CLAUDE.md`,
  `README.md`) and leaves records alone;
- it knew exactly one sentence shape, so `/portal` telling users to target `project:demo-app`
  unconditionally was invisible to it — the pair this release's own note promised to keep in sync;
- the setup judge's window stopped at the section-6 marker, excluding a guard **this release
  added** seven lines past it, and passed all-green over code it could not see;
- the eval judge had no fixture where `requires` differed from `target`, so that entire branch
  could be deleted with every check still green;
- and its self-test reported PASS off an unrelated failure while the mutation quietly no-opped.

Every self-test now asserts the **named** checks that must go red, the parity judge prints an
advisory list of instruction-shaped lines no rule classified, and each judge states what it does
not cover. A judge that reports a confident count over a surface it only partly reads is worse
than a hardcoded list, because the list at least shows its coverage.

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
