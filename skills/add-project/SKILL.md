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

## Step 0 — decide whether you can ask questions at all

If `--kind` or `--inspect` is missing:

- **Interactive session** → ask for it. Explain what it is (below) and wait.
- **Non-interactive** (`claude -p`, a scheduled run, a workflow agent — anything with no human at
  the keyboard) → **do NOT ask. Refuse, and print a command the user can paste to re-run.** A
  question asked into a headless run is not a question, it is a hang, and the run dies on a timeout
  with nothing to show for it.

```
/add-project <git-url> <name> --kind <web|report|agent|api|cli|none> --inspect "<command>"
```

Say plainly which flag was missing and that nothing was changed. Do not clone first and ask after —
a half-added project is worse than none, because the next command sees a directory with no squad.

## Step 1 — clone

Resolve `ROOT` = the directory holding `standup/team.json` (this project root). Then:

```
git -C "$ROOT" clone <git-url> <name>
```

**If the clone fails, stop.** Report git's own error verbatim — not a paraphrase — and change
nothing else. Refuse if `<ROOT>/<name>` already exists: name the existing path and suggest a
different `name`, or `/remove-project <name>` first if it was a previous attempt.

Confirm the clone has an `origin` remote (`git -C "$ROOT/<name>" remote -v`). The gated SDLC commits
to feature branches and never pushes, but the portal's worktree flow resolves `origin/<default>`,
so a repo with no origin will fail later at a confusing place.

## Step 2 — the review surface, and why it is not optional

The engine **refuses to run** a squad that declares no `review_surface`, and refuses one whose
`kind` is anything but the six above, and refuses one with a blank `inspect`. That is deliberate:
a squad with no declared surface is a team nobody can check the output of.

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

```
Added <name>.
  repo      <ROOT>/<name>   (origin: <url>)
  squad     <name> — developers <name>_a, <name>_b
  surface   <kind> — inspect: <CMD>
  ignored   /<name>/ added to .gitignore

Next:
  /sync-roster      regenerate the native-team agent defs (REQUIRED — the two new
                    developers do not exist as agent types until you run it)
  /work <task>      run one task through the gated SDLC against <name>
  /standup          run the whole roster
```

`/sync-roster` is not a nicety: `/team` spawns teammates from `.claude/agents/*.md`, which are
generated from the roster. Skip it and the new developers cannot be spawned.

## Failure contract

Every refusal names **the field in `standup/team.json`** it is about and **one command that fixes
it** — the same contract the engine's own `STOP —` blocks follow. "Something went wrong" is not a
report. If you changed nothing, say that too: the reader's next question is always whether they
need to clean something up.
