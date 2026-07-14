---
name: eval
description: Run the regression eval suite (evals/cases.json) — each gold task through the gated pipeline, then its check — and score pass/fail + duration. Use to catch quality regressions when prompts/roster/templates change, or to validate the team before shipping.
allowedTools: Read, Bash, Edit, Write, Task
---

Run the regression eval suite. Read `evals/cases.json` (a `target` repo + a list of `cases`, each with `id`, `prompt`, and a `check` shell command). For EACH case, in order:

1. Make an **isolated copy** of the target repo (e.g. `cp -r <target> /tmp/eval-<id>` or a git worktree) so cases never contaminate each other or the real repo.
2. Run the case `prompt` through the **gated SDLC** in that copy — like `/agent-team:work`: plan → fresh-context pair challenge → implement + tests → 2-lens review. (You may SKIP the commit — eval scores the working result, not a commit.)
3. Run the case `check` shell command in that copy. Record **pass/fail** (check exit 0) + **duration** (and per-run cost if the run surfaced it).
4. Tear down the throwaway copy.

After all cases, print a **SCORECARD**: per-case `✓/✗ id (duration)` then totals (`N/M passed`, total duration). Explicitly flag any **regression** (a case that fails) — that's the signal this suite exists for. If `$ARGUMENTS` names a case id, run only that one.

Note (v0.2 first cut): scoring is pass/fail + duration. Per-case dollar-cost scoring and a fully non-interactive runner are the next iteration (see ROADMAP.md). Pair this with `/agent-team:costs` (spend) and `/agent-team:runs` (history) for the full observe-and-evaluate picture.
