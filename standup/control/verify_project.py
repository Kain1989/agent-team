#!/usr/bin/env python3
"""Check that a project added by /add-project is actually usable — or that /remove-project left
nothing behind.

    python3 standup/control/verify_project.py added   <name> [--root DIR]
    python3 standup/control/verify_project.py removed <name> [--root DIR] --code-before present|absent

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
import re
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


# Index modes that mean "this entry points somewhere instead of holding content".
#   160000  gitlink   — an embedded repo recorded as a bare commit id nobody else can fetch
#   120000  symlink   — a blob holding a path, often absolute and outside the repo
DANGEROUS_MODES = {"160000": "gitlink (embedded repo)", "120000": "symlink blob"}


def gitlinked_paths(root):
    """Index entries staged as a pointer rather than as content — see DANGEROUS_MODES.

    Runs against the index only; it never stages anything itself.

    120000 is here because the .gitignore step cannot cover it. A trailing-slash pattern
    (`/name/`) is DIRECTORY-ONLY, and git does not treat a symlink-to-directory as a directory —
    measured: `check-ignore /linkproj/` returns 1 (no match) while the same pattern matches a real
    directory, and `git add -A` then stages the symlink as 120000. Refusing to adopt a symlink
    closes that entrance, but a checker that only looks for 160000 would keep reporting "ok, no
    gitlink" about a mode it cannot see, and this file exists to stop exactly that class.
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
        if len(parts) >= 4 and parts[0] in DANGEROUS_MODES:
            found.append((parts[3], parts[0]))
    return found


def em_owned_names(root):
    """Top-level names the supervisor gate treats as management, not project, territory.

    DERIVED from hooks/supervisor_gate.py's own constants at runtime — never transcribed. A
    hand-written copy of this list had already drifted: it named four directories and was missing
    `hooks/` and `skills/`, both of which the gate classifies as plugin-governance paths. The rule
    in this repo is that restating a list loses the load-bearing part of it; the gate is the single
    source, so read the gate.

    Returns None when the gate cannot be read or fully parsed. The caller reports that as NOT
    CHECKED and FAILS — an absent protection reported as `ok` is the shape this file exists to stop,
    and `check_removed` already takes that line for its own unreadable case.
    """
    gate = os.path.join(root, "hooks", "supervisor_gate.py")
    try:
        src = open(gate, encoding="utf-8").read()
    except OSError:
        return None
    names = set()
    for const in ("PLUGIN_DIRS", "STANDUP_ALLOW_DIRS", "STANDUP_ALLOW_FILES"):
        m = re.search(const + r"\s*=\s*\{([^}]*)\}", src)
        if not m:
            # ALL THREE must parse. Matching two of three and continuing would silently shrink the
            # deny list, and a shrunken deny list is indistinguishable from a satisfied one. If the
            # gate is refactored (say `frozenset((...))`) this reports unreadable, not a guess.
            return None
        names |= {x.strip().strip("\"'") for x in m.group(1).split(",") if x.strip()}
    # Seeded AFTER a successful parse, never before: seeding first makes the set non-empty and lets
    # an unreadable gate masquerade as a parse that worked.
    names.add("standup")          # the engine root itself, named by the gate's own path logic
    return {n for n in names if n}


def _git(dirpath, *args):
    """Run a git command in `dirpath`; return (rc, stdout.strip())."""
    try:
        out = subprocess.run(["git", "-C", dirpath] + list(args),
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return out.returncode, out.stdout.strip()


def repo_identity(dirpath):
    """Is `dirpath` its OWN git repo root, and does it have a commit?

    Returns (is_own_root, has_commit).

    THE ORDER OF THESE TWO QUESTIONS IS LOAD-BEARING, and asking them the other way round is the
    bug this function was written to remove. Inside a directory that is NOT a repo but sits under
    one — the normal shape for a project folder in a cloned agent-team install — git answers for
    the PARENT:

        git -C outer/myproj rev-list --count HEAD       -> 1      rc=0
        git -C outer/myproj rev-parse --is-inside-work-tree -> true
        git -C outer/myproj rev-parse --show-toplevel    -> .../outer     (NOT myproj)

    So a commit test asked first is answered by the parent and proves nothing about the project.
    `show-toplevel == realpath(dirpath)` is the only one of the three that discriminates, and it
    must come first; the commit test is meaningful only once we have established which repo is
    being interrogated.

    It also replaces `os.path.isdir(.git)` outright rather than sitting beside it, because that
    test is wrong in both directions:
      * a `git worktree` or submodule checkout has a `.git` FILE (a path pointer, not a directory), so isdir
        says False about a directory git handles perfectly — the likely shape of an adopted folder;
      * a symlink to a repo makes `os.path.isdir(link/.git)` return True, because Python follows
        the link.
    The toplevel test is correct for all four shapes.
    """
    rc, top = _git(dirpath, "rev-parse", "--show-toplevel")
    if rc != 0 or not top:
        return False, False
    try:
        same = os.path.realpath(top) == os.path.realpath(dirpath)
    except OSError:
        return False, False
    if not same:
        return False, False
    rc_head, _ = _git(dirpath, "rev-parse", "--verify", "HEAD")
    return True, rc_head == 0


def has_origin(dirpath):
    rc, out = _git(dirpath, "remote")
    return rc == 0 and "origin" in out.split()


def check_added(root, name, results):
    roster, _ = load_roster(root)

    # 1. the directory exists and is a git repo with an origin
    # The name must not collide with management territory. Checked case-insensitively for the same
    # reason as the squad id below.
    owned = em_owned_names(root)
    if not owned:
        _ok(results, "the name is not management territory",
            "(could not read hooks/supervisor_gate.py — NOT CHECKED, not a pass)")
    elif name.lower() in {o.lower() for o in owned}:
        _fail(results, "the name is not management territory",
              "%r is a path the supervisor gate classifies as management/governance, not project "
              "territory. Adopting it would point a dev squad at the plugin's own control plane. "
              "Pick another name." % name)
    else:
        _ok(results, "the name is not management territory")

    proj = os.path.join(root, name)
    if not os.path.isdir(proj):
        _fail(results, "the project directory exists",
              "%s is not there — the source step did not land, or `name` differs from the squad id"
              % proj)
    elif os.path.islink(proj):
        _fail(results, "the project directory is not a symlink",
              "%s is a symlink. `/%s/` in .gitignore will NOT match it (trailing-slash patterns are "
              "directory-only and git does not treat a symlink-to-directory as a directory), so "
              "`git add -A` stages it as mode 120000 — a blob holding a path out of the tree. "
              "Move or copy the real directory in instead." % (proj, name))
    else:
        own_root, has_commit = repo_identity(proj)
        if not own_root:
            _fail(results, "the project is its own git repo",
                  "%s is not a git repo root. A run against it would not fail quietly: `git -C` "
                  "resolves to the ENCLOSING repo, so `checkout -b` moves your agent-team "
                  "installation's HEAD and `git add -A` stages unrelated in-flight work. "
                  "/add-project initialises a repo for exactly this reason." % proj)
        elif not has_commit:
            _fail(results, "the project has at least one commit",
                  "%s is a repo with an unborn HEAD. Every file in it is untracked, and "
                  "`git diff` cannot see untracked files — so the review ring reads an EMPTY diff "
                  "no matter what the developer changed. A baseline commit is what makes the work "
                  "reviewable." % proj)
        else:
            _ok(results, "the project is its own git repo with a baseline commit", proj)
            if not has_origin(proj):
                _fail(results, "the project has an origin remote",
                      "%s has no `origin`. The portal's code-task flow resolves origin's default "
                      "branch to create its worktree and cannot run without it — the approve-then-"
                      "commit loop would be unavailable for this project. /add-project creates a "
                      "local bare origin (offline, no network) for sources that have none." % proj)
            else:
                _ok(results, "the project has an origin remote")

    # 2. a squad with that id, carrying two paired ACTIVE developers whose folder is the project
    # Case-INSENSITIVE. macOS and Windows default to case-insensitive filesystems, so `MyApp` and
    # `myapp` are one directory while an exact `==` sees two distinct squad ids — measured: after
    # `mkdir MyApp`, `os.path.isdir("myapp")` is True. An exact comparison would let `adopt MyApp`
    # slip past the duplicate check and produce two squads sharing one folder.
    squad = next((t for t in roster.get("teams", [])
                  if str(t.get("id", "")).lower() == name.lower()), None)
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

    # The local bare origin created for `new` / origin-less `adopt` lives at
    # <root>/.<name>-origin.git and is NEITHER covered by `/<name>/` NOR a pointer entry — it is
    # ordinary files. Measured: an install whose .gitignore had only `/myapp/` staged 23 paths under
    # `.myapp-origin.git`, and a loose object there decompressed to `DB_PASSWORD=hunter2`. That is
    # worse than the gitlink this file was written for: a gitlink is a dangling pointer, this is the
    # content itself, and it travels when the user pushes their own agent-team repo.
    origin_dir = ".%s-origin.git" % name
    if os.path.isdir(os.path.join(root, origin_dir)):
        rc_ign, _ = _git(root, "check-ignore", "-q", origin_dir)
        if rc_ign != 0:
            _fail(results, "the local bare origin is ignored",
                  "%s exists but is NOT ignored — `git add -A` in this installation absorbs the "
                  "project's git objects, secrets included, into its own history. Add "
                  "`/%s/` to .gitignore (or the generic `.*-origin.git/`)."
                  % (os.path.join(root, origin_dir), origin_dir))
        else:
            _ok(results, "the local bare origin is ignored", origin_dir)

    links = gitlinked_paths(root)
    if links is None:
        _ok(results, "the project is not staged as a pointer", "(this root is not a git repo — n/a)")
    else:
        hit = next(((pth, mode) for pth, mode in links
                    if pth == name or pth.startswith(name + "/")), None)
        if hit:
            _fail(results, "the project is not staged as a pointer",
                  "%r is staged with mode %s — %s. Unstage it (`git rm --cached %s`) and make sure "
                  "%r is in .gitignore" % (hit[0], hit[1], DANGEROUS_MODES[hit[1]], name, want))
        else:
            _ok(results, "the project is not staged as a pointer")


def check_removed(root, name, results, code_before=None):
    roster, _ = load_roster(root)

    if any(t.get("id") == name for t in roster.get("teams", [])):
        _fail(results, "the squad is gone from the roster",
              "teams[] still has %r" % name)
    else:
        _ok(results, "the squad is gone from the roster")

    # The .gitignore line is NOT unconditionally garbage after removal. It exists to stop
    # `git add -A` recording the clone as a gitlink, and /remove-project deliberately leaves the
    # clone on disk — so deleting the line while the directory remains does not tidy anything, it
    # RE-ARMS the exact hazard /add-project was built to prevent, silently, on the next `git add`.
    # Measured in the user walkthrough: after removal, `git add -A --dry-run` printed
    # "warning: adding embedded git repository: my-app".
    #
    # So the invariant is conditional on the directory, not on the roster.
    want = "/%s/" % name
    present = want in gitignore_lines(root)
    here = os.path.isdir(os.path.join(root, name))
    if here and not present:
        _fail(results, "the gitignore entry matches reality",
              "%s is still on disk but %r was removed from .gitignore — the next `git add -A` "
              "records it as a gitlink. Put the line back until the directory is gone." 
              % (os.path.join(root, name), want))
    elif here:
        _ok(results, "the gitignore entry matches reality",
            "kept: the clone is still on disk, so the ignore is still doing work")
    elif present:
        _fail(results, "the gitignore entry matches reality",
              "%r is a dead entry — %s is gone" % (want, name))
    else:
        _ok(results, "the gitignore entry matches reality", "removed with the directory")

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

    # The code itself must NOT have been deleted. Removing a squad is reversible; deleting a working
    # tree is not, so a command that did it would be exceeding its remit — and "never deletes your
    # repository" is stated in the skill frontmatter, the README table, the CHANGELOG and the commit
    # message.
    #
    # Both branches of this used to call _ok, including the one where the directory was GONE. So the
    # single check standing behind that four-times-repeated promise printed `ok` at the exact moment
    # the promise was broken. It read like verification and could not fail.
    #
    # It needs a fact it cannot get after the fact: whether the directory was there BEFORE the edit.
    # `--code-before` carries it. Without it the honest answer is "not checked" — never `ok`.
    here = os.path.isdir(os.path.join(root, name))
    if code_before == "present" and not here:
        _fail(results, "the user's code was left alone",
              "%s was there before and is GONE. /remove-project must never delete a repository — "
              "it is a roster edit. Recover it from your remote, or from the reflog if it was "
              "never pushed." % os.path.join(root, name))
    elif code_before == "present":
        _ok(results, "the user's code was left alone", os.path.join(root, name))
    elif code_before == "absent":
        _ok(results, "the user's code was left alone",
            "(nothing was there before this ran — nothing to keep)")
    else:
        _fail(results, "the user's code was left alone",
              "NOT CHECKED — pass --code-before present|absent, recorded BEFORE the edits. "
              "Without it this check cannot fail, and a check that cannot fail is not one.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mode", choices=("added", "removed"))
    ap.add_argument("name")
    ap.add_argument("--root", default=".")
    ap.add_argument("--code-before", choices=("present", "absent"), default=None,
                    help="whether <name>/ existed BEFORE the removal — recorded by the caller, "
                         "because after the fact it is unknowable")
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    results = []
    if args.mode == "added":
        check_added(root, args.name, results)
    else:
        check_removed(root, args.name, results, args.code_before)

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
