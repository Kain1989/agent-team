#!/usr/bin/env python3
"""Decide which eval cases can run here, and print the plan.

    python3 evals/resolve_cases.py                  # human-readable plan
    python3 evals/resolve_cases.py --json           # machine-readable plan

WHY THIS EXISTS AS CODE AND NOT AS A SENTENCE IN THE SKILL.

`evals/cases.json` used to hardcode `"target": "demo-app"` with both cases importing `textkit`.
demo-app is an OPTIONAL sample the docs invite you to delete once you have your own repo — and
once it was gone, `/eval` had no target, no case could run, and nothing said so. The suite did not
report zero; it reported nothing, which reads exactly like a suite that has not been run yet.

The skip decision is therefore made HERE, deterministically, rather than being requested of the
model in the skill prompt. A skip is a first-class outcome with a reason attached: it is never
counted as a pass, and it always names the directory that would make the case runnable.

Exit codes:
  0  a plan was produced (even if every case is skipped — "nothing to run" is a valid answer)
  1  cases.json is missing, malformed, or a case is missing a required field
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CASES_PATH = os.path.join(HERE, "cases.json")

REQUIRED_FIELDS = ("id", "prompt", "check")


def die(msg: str) -> "None":
    print("!! %s" % msg, file=sys.stderr)
    raise SystemExit(1)


def load(path: str) -> dict:
    if not os.path.exists(path):
        die("no eval gold-set at %s — nothing to resolve." % path)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        die("%s is not readable JSON: %s" % (path, exc))
    if not isinstance(data, dict) or not isinstance(data.get("cases"), list):
        die("%s must be an object with a `cases` array." % path)
    return data


def resolve(data: dict, repo: str) -> list:
    """One entry per case: {id, target, requires, run, reason}."""
    default_target = data.get("target")
    plan = []
    for idx, case in enumerate(data["cases"]):
        if not isinstance(case, dict):
            die("cases[%d] is not an object." % idx)
        missing = [f for f in REQUIRED_FIELDS if not case.get(f)]
        if missing:
            die("case %r is missing required field(s): %s"
                % (case.get("id", "<no id>"), ", ".join(missing)))

        target = case.get("target") or default_target
        # `requires` defaults to the target: a case always needs the directory it runs in. Stating
        # it separately only matters when a case needs something its target does not imply.
        requires = case.get("requires") or target

        if not target:
            run, reason = False, (
                "no target: cases.json sets no top-level `target` and this case declares none. "
                "Add one, or point the case at a project directory from standup/team.json.")
        elif not os.path.isdir(os.path.join(repo, target)):
            run, reason = False, (
                "target directory %r is not here. If this was the optional demo-app sample, that "
                "is expected — delete or re-point this case." % target)
        elif requires and not os.path.isdir(os.path.join(repo, requires)):
            run, reason = False, (
                "requires %r, which is not here." % requires)
        else:
            run, reason = True, "target %r is present" % target

        plan.append({"id": case["id"], "target": target, "requires": requires,
                     "run": run, "reason": reason})
    return plan


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True, description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="emit the plan as JSON")
    ap.add_argument("--cases", default=CASES_PATH, help="path to cases.json")
    ap.add_argument("--repo", default=REPO, help="directory the targets are resolved against")
    args = ap.parse_args()

    data = load(args.cases)
    plan = resolve(data, args.repo)
    runnable = [p for p in plan if p["run"]]
    skipped = [p for p in plan if not p["run"]]

    if args.json:
        print(json.dumps({"runnable": len(runnable), "skipped": len(skipped), "plan": plan},
                         indent=2))
        return 0

    print("eval plan — %d case(s): %d runnable, %d skipped"
          % (len(plan), len(runnable), len(skipped)))
    for p in plan:
        mark = "run " if p["run"] else "SKIP"
        print("  %s %-22s %s" % (mark, p["id"], p["reason"]))

    if not runnable:
        print("")
        print("Nothing to run here. This is a stated result, not a silent one:")
        print("  * every case is bound to a directory that is not in this checkout;")
        print("  * add a case for your own project (copy one and set `target` + `requires`), or")
        print("  * restore the sample with /agent-team:init if you want the bundled gold-set.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
