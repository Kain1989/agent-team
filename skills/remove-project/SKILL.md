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
- **leave the `/<name>/` line in `.gitignore` alone** — as long as the clone is still on disk, that
  line is the only thing stopping the next `git add -A` from recording it as a gitlink. Removing
  it does not tidy up; it re-arms the hazard `/add-project` exists to prevent, silently. Remove the
  line only if the directory is already gone.

Change nothing else. **Re-parse `standup/team.json`** and stop loudly if it no longer parses.

If `<name>` matches no squad, say so and list the squad ids that do exist. Do not guess at a near
match — silently acting on a different squad than the one named is worse than doing nothing.

## Step 3 — verify, with the checker

Before editing, record whether the code directory is there; after editing, hand that to the checker
so the "never deletes your code" promise is actually tested rather than asserted:

```
[ -d "$ROOT/<name>" ] && CODE=present || CODE=absent      # BEFORE the edits
python3 standup/control/verify_project.py removed "<name>" --root "$ROOT" --code-before "$CODE"
```

Non-zero → print the FAIL lines verbatim. The `--code-before` value is what lets the checker fail
when the directory that was there is gone: without it the check can only ever say `ok`, which is
decoration wearing the costume of verification.

## Step 4 — report

Lead with the action the user still has to take. Substitute every `<…>` — print the real absolute
path, never the placeholder.

```
Next:  /sync-roster      (removes the generated .claude/agents/<a>.md and <b>.md)

Removed <name>: squad + developers <a>, <b> from standup/team.json.
  code       /abs/path/<name> is still on disk — delete it yourself if you want it gone:
                 rm -rf "/abs/path/<name>"
  .gitignore /<name>/ KEPT, on purpose: while that directory exists, the line is what stops
             `git add -A` recording it as a gitlink. Delete the line after you delete the dir.
  <plus any evals / scope_folders references found in step 1>
```

`/sync-roster` prunes: the generator rewrites the whole directory and unlinks any file carrying its
own `generated from team.json` header whose role is no longer active. Hand-written agent defs do not
carry that header and are left alone. **Verified, not assumed** — so this command does not delete
those files itself, and must not: doing it by hand would also catch a hand-written file with a
colliding name.
