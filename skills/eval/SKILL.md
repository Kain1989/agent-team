---
name: eval
description: Run the regression eval suite (evals/cases.json) — each gold task through the gated pipeline, then its check — and score pass/fail + duration. Use to catch quality regressions when prompts/roster/templates change, or to validate the team before shipping.
allowedTools: Read, Bash, Edit, Write, Task
---

Run the regression eval suite.

0. **Resolve which cases can run here** — `python3 evals/resolve_cases.py`. It reads
   `evals/cases.json` (a default `target` + a list of `cases`, each with `id`, `prompt`, `check`,
   and a `requires` directory) and prints a RUN/SKIP plan with a reason per case. **Do not make
   this judgement yourself.** `demo-app` is an optional sample; when it has been deleted, its
   cases must SKIP with the reason stated, and a skip is **never** a pass. If the plan is `0
   runnable`, print its explanation and stop — that is a complete, correct answer, not a failure.
   Exit 1 means the gold-set itself is broken (malformed, or a case missing a field); report that
   verbatim rather than working around it.

For EACH case the plan marked `run`, in order:

1. Make an **isolated copy** of the target repo (e.g. `cp -r <target> /tmp/eval-<id>` or a git worktree) so cases never contaminate each other or the real repo.

   > ⚠️ **KNOWN LIMITATION — the isolation and the pipeline disagree, and the pipeline wins.**
   > The engine builds its review commands as `git -C ${folder} diff -- .` where `folder` comes from
   > the roster (`standup/standup.workflow.js` `:573`, used at `:1299`, `:1307`, `:1368`). `git -C`
   > resolves relative to the process cwd, so a run whose `folder` is `demo-app` reviews the **real**
   > directory while the work happened in your copy — an empty diff, reported as `review-failed`.
   > Pointing `folder` at the copy does not work either: the engine hard-stops on a folder the
   > assignee does not own (`:567-572`), and `/tmp/eval-<id>` is not in anyone's `also_owns`.
   > **Until that is fixed, prefer one of:** run the case on a throwaway *branch inside the real
   > target* and reset afterwards, or add the copy's path to the assignee's `also_owns` in
   > `standup/team.json` for the duration of the run. Do **not** report `review-failed` from an eval
   > as a quality regression without first checking which directory the reviewer actually read.

2. Run the case `prompt` through the **gated SDLC** in that copy — like `/agent-team:work`: the canonical steps in `standup/team.json` → `manager.policy.sdlc_pipeline`, through review. (You may SKIP the commit — eval scores the working result, not a commit.) No lens count is named on purpose: green is derived from the lenses actually planned for the task.
3. Run the case `check` shell command in that copy. Record **pass/fail** (check exit 0) + **duration** (and per-run cost if the run surfaced it).
4. Tear down the throwaway copy.

After all cases, print a **SCORECARD**: per-case `✓/✗ id (duration)`, plus `- id (skipped: <reason>)` for every case the plan skipped, then totals (`N/M passed`, `K skipped`, total duration). A skipped case is counted in neither the numerator nor the denominator of "passed" — reporting `2/2 passed` when both were skipped is the failure this whole step exists to prevent. Explicitly flag any **regression** (a case that fails) — that's the signal this suite exists for. If `$ARGUMENTS` names a case id, run only that one.

Note: scoring is pass/fail + duration — there is no per-case dollar-cost score, and the runner is interactive rather than fully non-interactive. Pair this with `/agent-team:costs` (spend) and `/agent-team:runs` (history) for the full observe-and-evaluate picture.
