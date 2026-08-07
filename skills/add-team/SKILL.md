---
name: add-team
description: Add a new squad to the team roster (standup/team.json). Use when the user wants to add a team, create a squad, or onboard a new group to the agent team.
allowedTools: Read, Bash, Edit
---

Add a new squad to `standup/team.json`. Do it safely (validate the JSON before and after).

INPUT: `$ARGUMENTS` — expected as:

```
/add-team <squad_id> — <one-line mission> --kind <web|report|agent|api|cli|none> [--inspect "<CMD>"]
```

e.g. `payments — own the billing service --kind api --inspect "curl -sS -f localhost:8080/health"`.
If the id or mission is missing, ask for it.

Use this when the code the squad will own is **already here and staying where it is** — the portal
under `standup/portal/`, or a directory that must not become its own repo. When you are bringing a
repo IN, use `/add-project` instead: it clones/creates/adopts the directory, guarantees it is its
own git repo with a baseline commit and an `origin`, and adds the `.gitignore` line, none of which
this command does.

## Step 0 — the surface is not optional, and it is asked for BEFORE anything is written

If `--kind` is missing — or `--kind` is anything but `none` and `--inspect` is missing:

- **Interactive session** → ask for it, and wait.
- **Non-interactive** (`claude -p`, a scheduled run, a workflow agent) → **do NOT ask. Refuse**, and
  print the invocation above filled in as far as you can. A question asked into a headless run is a
  hang, not a question.

Say plainly which flag was missing, and that nothing was changed.

**Why this command refuses rather than defaulting.** The engine STOPS a run on a squad that declares
no `review_surface` — `stopTick("squad … declares no review_surface")` — and stops again on an
unknown `kind`, and again on a blank `inspect` for any kind but `none`. A template that omitted the
field therefore produced a squad that could not run, and the missing field only surfaced at the next
`/standup`, as an error about the engine. Leaving it to a "then add a review_surface by hand" line
in the docs is the same defect one layer up: this repo has already recorded twice what happens to a
required step that exists only as a sentence someone has to remember.

- `kind` is a label. **`inspect` is the load-bearing field** — the one command a stranger runs to
  actually SEE the surface. A URL for `web`; the command that prints the report for `report`; the
  question you ask an `agent` and what a good answer looks like; a `curl` for `api`; the command
  plus what its output should say for `cli`.
- `none` is a legitimate, honest answer for something genuinely faceless — and **an invented
  `inspect` that does not run is worse than `none`**, because it becomes a promise the review gate
  keeps trying to cash. Do not guess one from a README.

## Step 1 — refuse before you touch anything

1. Read `standup/team.json`. Refuse if `<squad_id>` is already a `teams[].id`, compared
   **case-insensitively** (macOS and Windows default to case-insensitive filesystems, so `Payments`
   and `payments` are one directory).
2. Refuse a `<squad_id>` containing a space, `/`, or a leading `-`. The id becomes an agent-type
   name and a filename under `.claude/agents/`, and `/sync-roster` refuses ids that are not
   filenames. The squad's `folder` is a separate field and MAY contain `/` — that is the normal way
   to own a subdirectory, and it is how the portal squad points at `standup/portal`.

## Step 2 — append the entry

Edit surgically (this skill has `Edit`, not `Write`): the file is hand-formatted and carries
`_comment` fields that explain the schema, and a rewrite loses them.

```json
{ "id": "<squad_id>", "name": "<Title-cased name>", "mission": "<mission>",
  "coordination": "Two paired developer-agents who challenge each other's plans and diffs in a FRESH context (pairs critique, they do not debate).",
  "review_surface": { "kind": "<K>", "label": "<what a stranger would call it>", "url": "<if web>",
                      "inspect": "<CMD>", "how": "<any prerequisite, stated inline>" },
  "developers": [] }
```

For `kind: "none"`, drop `inspect` and `url` entirely — not `""`, not a placeholder. Nothing
validates `inspect` when kind is `none`, so a made-up value passes every check and lies forever.

A squad with no developers will not run until you add at least one — that is expected, and step 3
says so.

## Step 3 — write it back, then confirm

Write `standup/team.json` back (preserve formatting and the rest of the file exactly; only insert
the new entry) and **re-parse it**. If it no longer parses, restore it and stop, loudly — a roster
that does not parse stops every command in this plugin.

Put the required next steps FIRST, then the summary:

```
NEXT — required:  /add-role <id> <dev_a> "<role>" [folder]   (twice — a squad needs a PAIR)
                  /sync-roster                                (the devs are not agent types until you run it)

Added squad <id>
  mission  <mission>
  surface  <kind> — inspect: <CMD>
  devs     none yet
```

Two developers, not one: the pair critique in a fresh context is the gate, and a lone developer
would review its own plan and its own diff. Then show the updated roster via the same render as
`/team-structure`.
