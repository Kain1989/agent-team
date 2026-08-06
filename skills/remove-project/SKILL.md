---
name: remove-project
description: Remove a project's squad from the roster and undo its .gitignore entry — the inverse of /add-project. Never deletes your code. Use when the user wants to remove a project, drop a squad, stop working on a repo, or undo an /add-project.
allowedTools: Read, Bash, Edit
---

Remove a project's roster entry. Do it now; don't just describe it.

INPUT: `$ARGUMENTS` — `<name>` (the squad id / directory name `/add-project` used).

**This command never deletes your repository.** Removing a squad is a roster edit and is
reversible; deleting a working tree is neither, and an agent should not do it on your behalf. The
directory is left exactly where it is and the path is printed so you can remove it yourself if you
want to.

## Step 1 — check what else points at it, BEFORE changing anything

A squad is referenced from more places than its own entry. Report every hit and let the user decide;
do not silently repair them.

- **`evals/cases.json`** — is `<name>` the top-level `target`, or any case's `target`/`requires`?
  Those cases will resolve to SKIP after this. That is correct behaviour, not a failure, but it is
  surprising if nobody says it in advance.
- **`scope_folders`** on any staff member in `standup/team.json` — a staff role scoped to a folder
  that no longer has a squad still reads that folder.
- **A developer of this squad named as another squad's `pair`** — that would leave a developer with
  a `pair` that does not resolve, and the engine **stops the run** on exactly that. Refuse and name
  both ids rather than creating a roster that cannot dispatch.

## Step 2 — remove, surgically

Read `standup/team.json`, then `Edit` (not `Write` — the file is hand-formatted and its `_comment`
fields carry the schema documentation):

- delete the `teams[]` entry whose `id` is `<name>`, with its developers;
- delete the `/<name>/` line from `.gitignore`.

Change nothing else. **Re-parse `standup/team.json`** and stop loudly if it no longer parses.

If `<name>` matches no squad, say so and list the squad ids that do exist. Do not guess at a near
match — silently acting on a different squad than the one named is worse than doing nothing.

## Step 3 — report

```
Removed <name> from the roster.
  squad     <name> (developers <a>, <b>) removed from standup/team.json
  ignored   /<name>/ removed from .gitignore
  code      <ROOT>/<name> is STILL ON DISK — delete it yourself if you want it gone:
                rm -rf "<ROOT>/<name>"
  <plus any evals/scope_folders references found in step 1>

Next:
  /sync-roster      regenerate the native-team agent defs — this DELETES the generated
                    .claude/agents/<a>.md and <b>.md for the removed developers
```

`/sync-roster` prunes: the generator rewrites the whole directory and unlinks any file carrying its
own `generated from team.json` header whose role is no longer active. Hand-written agent defs do not
carry that header and are left alone. **Verified, not assumed** — so this command does not delete
those files itself, and must not: doing it by hand would also catch a hand-written file with a
colliding name.
