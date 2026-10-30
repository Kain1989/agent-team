#!/usr/bin/env python3
"""project.py — bring a project repo into workspace/ (behind /add-project).

  python project.py clone <git-url> [--name N]   clone it
  python project.py new <name>                   start an empty repo
  python project.py adopt <name>                 a folder already at workspace/<name>

Whatever the route, the result is guaranteed to be (v1's onboarding invariants):
  * its own git repository (never the harness repo's subtree),
  * with a baseline commit on a default branch,
  * with an `origin` (a local bare repo under workspace/.origins/ when it has none, so
    pushes of story branches work offline and never reach a real remote by accident),
  * with `constitution.md` — the project's non-negotiables, checked at planning (Spec Kit),
  * and its smoke suite verified: env.py detects the suites and the unit suite must RUN.
    A project whose tests cannot run is reported, not silently accepted.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib as L  # noqa: E402

CONSTITUTION = """# Constitution — {name}

The non-negotiables for every story on this project. g3-plan requires the dev plan to have a
`## Constitution check` section saying how the plan honours each of these.

1. Every requirement ships with a test that fails without it.
2. No new runtime dependency without a line in the plan saying why the standard library is not enough.
3. Errors are reported to the caller, never swallowed.
4. Nothing secret is committed; configuration comes from the environment.

Edit this file to fit the project. It is yours, not the harness's.
"""


def ok_name(name: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", name):
        L.die(f"project name '{name}' must be lowercase letters, digits, '-' or '_' (it is a directory and a branch prefix)")
    if (L.workspace() / name).exists():
        L.die(f"workspace/{name} already exists — use `adopt {name}`")
    return name


def git(args: List[str], cwd: Path) -> subprocess.CompletedProcess:
    return L.git(args, cwd)


def ensure_invariants(p: Path) -> List[str]:
    notes = []
    if not (p / ".git").exists():
        git(["init", "-q", "-b", "main"], p)
        notes.append("initialised a git repository")
    if not L.git_out(["rev-parse", "--verify", "HEAD"], p):
        if not (p / "constitution.md").exists():
            L.write_text(p / "constitution.md", CONSTITUTION.format(name=p.name))
        git(["add", "-A"], p)
        r = git(["-c", "user.name=agent-team", "-c", "user.email=agent-team@localhost", "commit", "-q", "-m", "baseline"], p)
        if r.returncode != 0:
            L.die(f"baseline commit failed: {r.stderr.strip()}")
        notes.append("made a baseline commit")
    elif not (p / "constitution.md").exists():
        L.write_text(p / "constitution.md", CONSTITUTION.format(name=p.name))
        notes.append("wrote constitution.md (uncommitted — review and commit it)")
    if not L.git_out(["remote", "get-url", "origin"], p):
        bare = L.workspace() / ".origins" / f"{p.name}.git"
        bare.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        git(["remote", "add", "origin", bare.as_posix()], p)
        br = L.git_out(["rev-parse", "--abbrev-ref", "HEAD"], p)
        git(["push", "-q", "origin", br], p)
        notes.append(f"added a LOCAL bare origin ({bare.relative_to(L.root()).as_posix()})")
    return notes


def verify(name: str) -> int:
    import env
    rep = env.build()
    L.write_json(L.state_dir() / "env-report.json", rep)
    proj = rep["projects"].get(name)
    if not proj:
        print(f"!! env.py does not see workspace/{name} as a project")
        return 1
    unit = proj["suites"].get("unit")
    print(f"detected: {proj['detected']}")
    if not unit or "argv" not in unit:
        print(f"!! no unit suite detected. Declare one in workspace/{name}/agentteam.json:\n"
              '   {"suites": {"unit": {"argv": ["{python}", "-m", "pytest", "-q"]}}}')
        return 1
    r = subprocess.run(unit["argv"], cwd=str(L.workspace() / name), capture_output=True, text=True, encoding="utf-8", errors="replace")
    tail = "\n".join(((r.stdout or "") + (r.stderr or "")).strip().splitlines()[-5:])
    print(f"smoke (unit suite): {' '.join(unit['argv'])} -> exit {r.returncode}\n{tail}")
    if r.returncode != 0:
        print("!! the unit suite does not pass on the baseline. Fix that first: every QA gate compares against it.")
        return 1
    return 0


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(prog="project.py")
    sub = ap.add_subparsers(dest="how", required=True)
    s = sub.add_parser("clone"); s.add_argument("url"); s.add_argument("--name")
    s = sub.add_parser("new"); s.add_argument("name")
    s = sub.add_parser("adopt"); s.add_argument("name")
    a = ap.parse_args(argv)
    L.workspace().mkdir(parents=True, exist_ok=True)
    if a.how == "clone":
        if re.match(r"^[A-Za-z]:[\\/]", a.url) or a.url.startswith(("/", ".")):
            src = Path(a.url).resolve()
            if not src.exists():
                L.die(f"{a.url} does not exist")
        name = ok_name(a.name or re.sub(r"\.git$", "", re.split(r"[/:\\]", a.url.rstrip("/\\"))[-1]).lower())
        r = subprocess.run(["git", "clone", "-q", a.url, str(L.workspace() / name)], capture_output=True, text=True)
        if r.returncode != 0:
            L.die(f"clone failed: {r.stderr.strip()}")
    elif a.how == "new":
        name = ok_name(a.name)
        (L.workspace() / name).mkdir()
        L.write_text(L.workspace() / name / "README.md", f"# {name}\n")
    else:
        name = a.name
        if not (L.workspace() / name).is_dir():
            L.die(f"nothing at workspace/{name} to adopt")
    for n in ensure_invariants(L.workspace() / name):
        print(f"  {n}")
    print(f"workspace/{name} ready")
    return verify(name)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
