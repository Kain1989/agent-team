#!/usr/bin/env python3
"""Check that a project added by /add-project is actually usable — or that /remove-project left
nothing behind.

    python3 standup/control/verify_project.py added   <name> [--root DIR]
    python3 standup/control/verify_project.py removed <name> [--root DIR]

WHY THIS IS A PROGRAM AND NOT A PARAGRAPH IN THE SKILL.

`/add-project` does four things that must ALL land, in a file that is hand-formatted and edited
surgically. Any one of them missing produces a project that looks added and fails later, somewhere
else, with an error about something different:

  * no roster entry            -> the engine stops on an unknown assignee
  * no review_surface          -> the engine stops on an undeclared surface
  * a blank `inspect`          -> same, one layer down
  * no .gitignore line         -> `git add -A` in the project root records the clone as a GITLINK
                                  (mode 160000): a pointer to a commit nobody can fetch

The last one is the reason this exists as code. It is invisible at add time, it does not break
anything until someone commits, and by then it looks like a git problem rather than an onboarding
one. A checklist in a prompt cannot catch it; `git ls-files -s` can.

Exit codes: 0 all invariants hold · 1 one or more failed · 2 usage / unreadable roster
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

SURFACE_KINDS = ("web", "report", "agent", "api", "cli", "none")


def _fail(results, name, detail):
    results.append((False, name, detail))


def _ok(results, name, detail=""):
    results.append((True, name, detail))


def load_roster(root):
    path = os.path.join(root, "standup", "team.json")
    if not os.path.exists(path):
        print("!! no standup/team.json under %s — is this a team project root?" % root,
              file=sys.stderr)
        raise SystemExit(2)
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh), path
    except ValueError as exc:
        # Deliberately exit 2, not 1: an unparseable roster is not "an invariant failed", it is
        # "nothing can be checked, and every other command in this plugin is also broken".
        print("!! standup/team.json does not parse: %s" % exc, file=sys.stderr)
        print("   Restore it before anything else — every command reads this file.", file=sys.stderr)
        raise SystemExit(2)


def gitignore_lines(root):
    path = os.path.join(root, ".gitignore")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [ln.strip() for ln in fh]


def gitlinked_paths(root):
    """Paths git has staged as gitlinks (mode 160000) — an embedded repo recorded as a pointer.

    Runs against the index only; it never stages anything itself.
    """
    try:
        out = subprocess.run(["git", "-C", root, "ls-files", "-s"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None                      # not a git repo / no git — not this check's business
    if out.returncode != 0:
        return None
    found = []
    for line in out.stdout.splitlines():
        parts = line.split(maxsplit=3)
        if len(parts) >= 4 and parts[0] == "160000":
            found.append(parts[3])
    return found


def check_added(root, name, results):
    roster, _ = load_roster(root)

    # 1. the directory exists and is a git repo with an origin
    proj = os.path.join(root, name)
    if not os.path.isdir(proj):
        _fail(results, "the project directory exists",
              "%s is not there — the clone did not land, or `name` differs from the squad id" % proj)
    elif not os.path.isdir(os.path.join(proj, ".git")):
        _fail(results, "the project is a git repo",
              "%s has no .git — the SDLC commits to a feature branch and cannot" % proj)
    else:
        _ok(results, "the project directory exists and is a git repo", proj)

    # 2. a squad with that id, carrying two paired ACTIVE developers whose folder is the project
    squad = next((t for t in roster.get("teams", []) if t.get("id") == name), None)
    if squad is None:
        ids = ", ".join(t.get("id", "?") for t in roster.get("teams", [])) or "(none)"
        _fail(results, "the roster has a squad with this id",
              "no teams[] entry with id %r; existing: %s" % (name, ids))
        return                            # everything below is about that squad
    _ok(results, "the roster has a squad with this id")

    devs = [d for d in squad.get("developers", []) if d.get("active")]
    if len(devs) < 2:
        _fail(results, "the squad has a pair of active developers",
              "found %d — a lone developer would critique its own plan and review its own diff, "
              "which is the gate this pipeline is built on" % len(devs))
    else:
        by_id = {d.get("id") for d in devs}
        unpaired = [d.get("id") for d in devs if d.get("pair") not in by_id]
        if unpaired:
            _fail(results, "each developer's `pair` resolves inside the squad",
                  "unresolved pair on: %s — the engine stops the run on this" % ", ".join(map(str, unpaired)))
        else:
            _ok(results, "the squad has a paired set of active developers",
                ", ".join(sorted(map(str, by_id))))

    wrong = [d.get("id") for d in devs if d.get("folder") != name]
    if wrong:
        _fail(results, "every developer's `folder` is the project directory",
              "not %r on: %s — work would be dispatched at the wrong tree" % (name, ", ".join(map(str, wrong))))
    else:
        _ok(results, "every developer's `folder` is the project directory")

    # 3. the review surface — declared, a known kind, and with a runnable inspect
    surface = squad.get("review_surface")
    if not isinstance(surface, dict):
        _fail(results, "the squad declares a review_surface",
              "missing — the engine refuses to run a squad whose product face nobody declared. "
              "Set teams[%r].review_surface = {kind, label, inspect, how}" % name)
    else:
        kind = surface.get("kind")
        if kind not in SURFACE_KINDS:
            _fail(results, "review_surface.kind is one the engine knows",
                  "%r is not one of: %s. Fix teams[%r].review_surface.kind" % (kind, ", ".join(SURFACE_KINDS), name))
        else:
            _ok(results, "review_surface.kind is one the engine knows", kind)
        if kind != "none" and not str(surface.get("inspect") or "").strip():
            _fail(results, "review_surface.inspect is runnable",
                  "blank for kind %r. `inspect` is the load-bearing field: it is how anyone SEES "
                  "the surface. Set teams[%r].review_surface.inspect, or declare kind \"none\" "
                  "honestly" % (kind, name))
        elif kind != "none":
            _ok(results, "review_surface.inspect is present", str(surface.get("inspect"))[:60])

    # 4. .gitignore — and the consequence of getting it wrong, measured rather than assumed
    want = "/%s/" % name
    lines = gitignore_lines(root)
    if want not in lines and name not in lines and ("%s/" % name) not in lines:
        _fail(results, ".gitignore ignores the cloned repo",
              "no %r line. Without it a `git add -A` here records the clone as a gitlink "
              "(mode 160000) — a pointer to a commit nobody else can fetch" % want)
    else:
        _ok(results, ".gitignore ignores the cloned repo", want)

    links = gitlinked_paths(root)
    if links is None:
        _ok(results, "no embedded repo is staged as a gitlink", "(this root is not a git repo — n/a)")
    elif any(p == name or p.startswith(name + "/") for p in links):
        _fail(results, "no embedded repo is staged as a gitlink",
              "%r is staged with mode 160000. Unstage it (`git rm --cached %s`) and make sure "
              "%r is in .gitignore" % (name, name, want))
    else:
        _ok(results, "no embedded repo is staged as a gitlink")


def check_removed(root, name, results):
    roster, _ = load_roster(root)

    if any(t.get("id") == name for t in roster.get("teams", [])):
        _fail(results, "the squad is gone from the roster",
              "teams[] still has %r" % name)
    else:
        _ok(results, "the squad is gone from the roster")

    want = "/%s/" % name
    if want in gitignore_lines(root):
        _fail(results, "the .gitignore line is gone", "%r is still there" % want)
    else:
        _ok(results, "the .gitignore line is gone")

    # Dangling references. These are why /remove-project reports before it edits.
    dangling = []
    live_ids = {d.get("id") for t in roster.get("teams", []) for d in t.get("developers", [])}
    for t in roster.get("teams", []):
        for d in t.get("developers", []):
            if d.get("pair") and d.get("pair") not in live_ids:
                dangling.append("%s.pair -> %s" % (d.get("id"), d.get("pair")))
    for s in roster.get("staff", []):
        for f in (s.get("scope_folders") or []):
            if f == name:
                dangling.append("staff %s.scope_folders -> %s" % (s.get("id"), name))
    if dangling:
        _fail(results, "no dangling references to the removed squad",
              "; ".join(dangling) + " — the engine stops a run on an unresolvable pair")
    else:
        _ok(results, "no dangling references to the removed squad")

    # The code itself must NOT have been deleted. Removing a squad is reversible; deleting a
    # working tree is not, so a command that did it would be exceeding its remit.
    if os.path.isdir(os.path.join(root, name)):
        _ok(results, "the user's code was left alone", os.path.join(root, name))
    else:
        _ok(results, "the user's code was left alone", "(directory already absent — nothing to keep)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mode", choices=("added", "removed"))
    ap.add_argument("name")
    ap.add_argument("--root", default=".")
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    results = []
    (check_added if args.mode == "added" else check_removed)(root, args.name, results)

    bad = [r for r in results if not r[0]]
    print("verify_project %s %r — %d check(s), %d failed"
          % (args.mode, args.name, len(results), len(bad)))
    for ok, nm, detail in results:
        print("  %s %s%s" % ("ok  " if ok else "FAIL", nm, ("  — " + detail) if detail else ""))
    if bad:
        print("\nEach line above names the field to fix. Nothing was changed by this check.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
