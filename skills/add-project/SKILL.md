---
name: add-project
description: Clone a git repo into this team project and point a new squad at it — the one command that turns a fresh install into a team working on YOUR code. Use when the user wants to add a project, add a repo, point the team at their own code, work on a real repository, or onboard a codebase.
allowedTools: Read, Bash, Edit
---

Clone a repo into this team project and give it a squad. Do it now; don't just describe it.

INPUT: `$ARGUMENTS` — `<git-url> [name] [--kind K] [--inspect CMD]`

- `name` defaults to the repo's basename (`git@host:org/thing.git` → `thing`). It becomes the
  directory under this project root **and** the squad's `folder`.
- `--kind` is the product surface: `web` `report` `agent` `api` `cli` `none`.
- `--inspect` is the ONE command a stranger runs to actually SEE that surface.

**This replaces four manual edits** (clone, roster entry, `review_surface`, `.gitignore`) that
previously had to be done by hand and in the right order. Three of the four were documented; the
fourth was not, which is how people ended up with a team pointed at a directory that did not exist.

> **Do not run this while `/standup` or `/work` is running.** Those runs snapshot the roster at
> launch and re-read `standup/team.json` from disk later, so editing it mid-run makes the two
> disagree and the run stops with a misleading `ARM armed the WRONG install`. See the known gap in
> `CHANGELOG.md` (0.4.0).

## Step 0 — decide whether you can ask questions at all

If `--kind` is missing — or `--kind` is anything but `none` and `--inspect` is missing:

**`--kind none` needs no `--inspect`.** `none` declares "this has no inspectable face", so there is
no command to ask for; treating it as missing would refuse a complete invocation with a fix the
caller cannot satisfy. (`--inspect none` is not a magic value — it is stored as the literal string.)

Then, depending on where you are running:

- **Interactive session** → ask for it. Explain what it is (below) and wait.
- **Non-interactive** (`claude -p`, a scheduled run, a workflow agent — anything with no human at
  the keyboard) → **do NOT ask. Refuse, and print a command the user can paste to re-run.** A
  question asked into a headless run is not a question, it is a hang, and the run dies on a timeout
  with nothing to show for it.

```
/add-project <git-url> <name> --kind <web|report|agent|api|cli> --inspect "<command>"
/add-project <git-url> <name> --kind none                      # a surface with no face
```

Say plainly which flag was missing and that nothing was changed. Do not clone first and ask after —
a half-added project is worse than none, because the next command sees a directory with no squad.

## Step 1 — clone

Resolve `ROOT` = the directory holding `standup/team.json` (this project root). Then, **in this
order** — the existence test comes FIRST, and it is not optional:

```
[ -e "$ROOT/<name>" ] && { echo "refusing: $ROOT/<name> already exists"; exit 1; }
git -C "$ROOT" clone <git-url> <name>
```

Git is **not** a backstop for the collision case: cloning into an existing EMPTY directory
succeeds (exit 0), so you would silently adopt whatever was there. And when it does refuse, it
says `fatal: destination path 'x' already exists and is not an empty directory` — which names
neither `name`'s double role (it is the directory AND the squad id) nor the way out. Refuse first,
and say:

```
refusing: <ROOT>/<name> already exists — nothing was changed.
  field  the squad id and the directory name are the SAME string
  fix    pick another:  /add-project <git-url> <name>2 --kind <K> --inspect "<CMD>"
         or, if a previous attempt left it:  /remove-project <name>
         then remove the directory yourself:  rm -rf "<ROOT>/<name>"
```

**If the clone itself fails, stop.** Report git's own error verbatim — not a paraphrase — and
change nothing else.

Confirm the clone has an `origin` remote (`git -C "$ROOT/<name>" remote -v`). The gated SDLC commits
to feature branches and never pushes, but the portal's worktree flow resolves `origin/<default>`,
so a repo with no origin will fail later at a confusing place.

## Step 2 — the review surface, and why it is not optional

The engine **refuses to run** a squad that declares no `review_surface`, and refuses one whose
`kind` is anything but the six above, and — **for every kind except `none`** — refuses one with a
blank `inspect`. That exception is in the engine, not a courtesy: `surface.kind !== 'none' &&
!inspect.trim()`. `none` means there is no face to inspect, so there is nothing to demand. That is
deliberate: a squad with no declared surface is a team nobody can check the output of.

- `kind` is a label. **`inspect` is the load-bearing field** — the deterministic way to actually see
  what a user sees. A URL for `web`; the command that prints the report for `report`; the question
  you ask an `agent` and what a good answer looks like; a `curl` for `api`; the command plus what
  its output should say for `cli`.
- `none` is a legitimate answer for something genuinely faceless, and it is honest. **An invented
  `inspect` that does not run is worse than `none`** — it becomes a promise the review gate keeps
  trying to cash.

Do not guess `inspect` from the repo's README. If you cannot get a real one, use `kind: none` and
say so in the summary.

## Step 3 — the roster entry

Edit `standup/team.json` **surgically** — this is why this skill has `Edit` and not `Write`. The
file is hand-formatted and carries `_comment` fields that explain the schema; a rewrite loses them.

Read it first, refuse a duplicate squad id, then insert into `teams[]`:

```json
{ "id": "<name>", "name": "<Title Case>", "mission": "<one line — ask if not obvious>",
  "coordination": "Two paired developer-agents who challenge each other's plans and diffs in a FRESH context (pairs critique, they do not debate).",
  "review_surface": { "kind": "<K>", "label": "<what a stranger would call it>", "url": "<if web>",
                      "inspect": "<CMD>", "how": "<any prerequisite, stated inline>" },
  // for kind "none": drop `inspect` and `url`. Do NOT invent a placeholder — nothing validates
  // `inspect` when kind is "none", so a made-up value passes every check and lies forever after.
  "developers": [
    { "id": "<name>_a", "folder": "<name>", "role": "Developer — Builder", "stack": "<infer from the repo>",
      "git": true, "active": true, "pair": "<name>_b", "focus": "implement backlog items with tests",
      "tests": "<the repo's test command>" },
    { "id": "<name>_b", "folder": "<name>", "role": "Developer — Reviewer & Tests", "stack": "<infer>",
      "git": true, "active": true, "pair": "<name>_a", "focus": "fresh-context plan/diff review, coverage, edge cases",
      "tests": "<the repo's test command>" }
  ] }
```

Two developers, not one: the pair critique in a **fresh context** is the gate, and a lone developer
would review its own plan and its own diff. Sanitise `<name>` for the ids if it contains `-` or `.`.

**Re-parse `standup/team.json` after writing.** If it no longer parses, restore it and stop —
say so loudly. A roster that does not parse stops every command in this plugin.

## Step 4 — `.gitignore`

Append `/<name>/` to this project's `.gitignore`.

Not cosmetic: the cloned repo is a git repo **inside** this one, and without the ignore a
`git add -A` here records it as a gitlink (mode `160000`) — a pointer to a commit nobody else can
fetch. Verified behaviour, not a precaution.

## Step 5 — tell them what is now true

**Put the required next step FIRST**, then the summary. A four-line success block followed by a
paragraph of caveats reads as "done" and the last line gets skipped — which is exactly how
`/sync-roster` gets missed.

**Substitute every `<…>`.** Print the resolved absolute path and the real origin URL; a literal
`<ROOT>/<name>` on screen means the one line whose whole job is telling the user where their code
went has not done it.

Use the SAME command prefix the user typed: `/sync-roster` in a checkout, `/agent-team:sync-roster`
when the plugin is installed. Alternating between the two guarantees one of them is dead in
whichever install they have.

```
NEXT — required:  /sync-roster
       The two new developers do not exist as agent types until you run it, so /team
       cannot spawn them.

Added my-app
  repo     /Users/you/agent-team/my-app  (origin: git@github.com:you/my-app.git)
  squad    my-app — developers my_app_a, my_app_b
  surface  cli — inspect: cd my-app && pytest -q
  ignored  /my-app/ added to .gitignore

Then:  /work "<task>"   one task through the gated SDLC
       /standup         the whole roster
```

Keep the summary to those lines. The rules above (why `inspect` must terminate, why an invented one
is worse than `none`) belong in this document, not repeated in every run's output — a reader who
has to skim to find the actionable line will skim past it.

## Step 6 — verify, with the checker, not by eye

```
python3 standup/control/verify_project.py added "<name>" --root "$ROOT"
```

**Run it. Do not skip it and do not summarise it.** If it exits non-zero, print its FAIL lines
**verbatim** and say plainly what was and was not changed — the clone landed, the roster entry may
have, and the user needs to know which.

This step is the whole reason the four invariants are enforceable at all. The `.gitignore` one in
particular cannot be checked by reading: it is `git ls-files -s` looking for mode `160000`, and a
checklist in a prompt cannot run that. A gate promised in a document and backed by nothing is the
failure this project has already recorded once, when `/work` was referenced by three governance
files and did not exist.

## Failure contract

Every refusal names **the field in `standup/team.json`** it is about and **one command that fixes
it** — the same contract the engine's own `STOP —` blocks follow. "Something went wrong" is not a
report. If you changed nothing, say that too: the reader's next question is always whether they
need to clean something up.

**Keep refusals to those three things** — what is wrong, the field, the fix. They are already the
strongest output this command produces; padding them with the reasoning behind the rule is what
turns a three-line answer into twenty-five lines of prose that gets skimmed.
