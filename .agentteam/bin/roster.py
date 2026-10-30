#!/usr/bin/env python3
"""roster.py — generate .claude/agents/<id>.md for every lead and IC in roster.json.

The agent files are the INSTRUCTED layer: the weakest one. They say what each role does;
guard.py and gate.py are what make it true. Generated, so a roster change is one edit in
roster.json and a re-run — never 26 hand-edited files drifting apart.

  python roster.py          write the agent files (needs maintainer mode under the guard)
  python roster.py --check  exit 1 if any agent file is missing or stale (CI / selftest)
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib as L  # noqa: E402

TOOLS = {
    ("readonly", "lead"): "Read, Grep, Glob, Bash, Write, Edit",
    ("readonly", "ic"): "Read, Grep, Glob, Bash",
    ("readonly-git-comment", "lead"): "Read, Grep, Glob, Bash",
    ("readonly-git-comment", "ic"): "Read, Grep, Glob",
    ("dev", "lead"): "Read, Grep, Glob, Bash, Write, Edit, MultiEdit",
    ("dev", "ic"): "Read, Grep, Glob, Bash, Write, Edit, MultiEdit",
    ("devops", "lead"): "Read, Grep, Glob, Bash, Write, Edit",
    ("devops", "ic"): "Read, Grep, Glob, Bash, Write, Edit",
}

TEAM_RULES = {
    "dev": ["Work only in the story worktree `workspace/.worktrees/<STORY>/`, on the story branch. Commit there; never push.",
            "Never edit acceptance tests, snapshots or golden files. If a test is wrong, STOP and file it: "
            "`story.py finding <STORY> --kind test-flaw ...` (your lead records it). Editing the test to pass is the one thing this harness exists to catch.",
            "Check off each TASK in `03-dev/tasks.md` (`- [x] TASK-001`) only when its code is committed."],
    "qa": ["Write tests, never product code. Run suites only through `story.py run <STORY> <suite>` — a pass you did not run through the harness counts for nothing.",
           "Copy commands from `.agentteam/state/env-report.json`; do not compose them from a tool's name.",
           "Every REQ gets a traceability row with at least one test that resolves on disk (`path::test_name`)."],
    "review": ["Read ONLY the packet directory you are given. If you need the author's reasoning to understand the code, that gap is a finding: the code should explain itself.",
               "Write no files. Findings are structured: file, symbol, category, severity (blocking|major|minor), claim."],
    "visual": ["Judge against DESIGN_RULEBOOK.md; every finding cites a rule id that exists there (E-01).",
               "Run the machine judge through `story.py run <STORY> visual`. A non-zero exit always fails; exit 0 proves nothing on its own (E-07)."],
    "devops": ["Deploy to dev only. Anything beyond dev is refused by the guard until a person approves deploy-beyond-dev.",
               "Run the smoke check through `story.py run <STORY> smoke`; write rollback steps before you deploy."],
    "product": ["Every requirement is `REQ-NNN` with a SHALL acceptance criterion (EARS: `WHEN <trigger> THE SYSTEM SHALL <response>`).",
                "`Out of scope` names what this story will not do. A PRD that excludes nothing has not decided anything."],
    "ux": ["Every REQ is designed for, or marked `REQ-NNN: N/A — <reason>`.",
           "Every decision in `02-ux/decisions.md` has `Decision:` and `Because:`."],
}


def body(a: Dict[str, Any], team: Dict[str, Any], phases: List[Dict[str, Any]]) -> str:
    owned = ", ".join(f"{p['n']} {p['title']}" for p in phases)
    dirs = ", ".join(f"`{w}`" for p in phases for w in p["writes"]) or "none — you write no files"
    lines = [f"# {a['id']} — {team['title']} {'lead' if a['kind'] == 'lead' else 'IC'}", "",
             a["charter"], "", f"Team **{team['id']}** owns phase(s) {owned}.", ""]
    if a["kind"] == "lead":
        ics = ", ".join(i["id"] for i in team["ics"])
        lines += ["## How you lead",
                  f"- Your ICs: {ics}. They report to you and never past you.",
                  "- You are called twice per round. First call: write `assignments.md` in your phase directory "
                  "naming which IC does what, with which inputs. Second call: you receive their raw outputs; you accept, "
                  "reject or merge them and produce the phase artifact. Only your verdict reaches a gate.",
                  "- Record findings and your verdict through `python .agentteam/bin/story.py finding|verdict ...`.",
                  "- A finding the gate calls a repeat must be escalated (`story.py escalate`), not re-raised in new words — "
                  "fingerprints are computed on file + symbol + category, not wording.", ""]
    else:
        lines += ["## How you work",
                  f"- You report to {team['lead']['id']} only. Return your output to the lead; do not record verdicts.",
                  "- Do exactly the assignment you were given, with the inputs you were given. No unrelated cleanup.", ""]
    lines += ["## Boot", "- Your inputs are the packet directory named in your prompt "
              "(`.agentteam/stories/<STORY>/packets/<phase>/`) plus the story state (`story.py show <STORY>`). Read nothing withheld.",
              f"- You may write: {dirs}" + (" and the story worktree" if "worktree" in team["write_roots"] else "") + ".",
              "", "## Rules"]
    lines += [f"- {r}" for r in TEAM_RULES.get(team["id"], [])]
    lines += ["- If the tests or the spec are wrong, STOP and report it. Do not work around it.",
              "- Never run `story.py human-gate`, `waive` or `usage` — those are a person's or the harness's. The guard refuses them anyway.",
              "", "## Close",
              "- End with: what you produced (paths), what you could not do and why, and open risks. "
              "Say plainly what is not done or not verified.", ""]
    return "\n".join(lines)


def render() -> Dict[Path, str]:
    r = L.load_config("roster")
    out: Dict[Path, str] = {}
    agents_dir = L.root() / ".claude" / "agents"
    for t in r["teams"]:
        phases = [p for p in L.phases() if p["team"] == t["id"]]
        for a in [{"id": t["lead"]["id"], "charter": t["lead"]["charter"], "kind": "lead"}] + \
                 [{"id": i["id"], "charter": i["charter"], "kind": "ic"} for i in t["ics"]]:
            model = r["models"][a["kind"]]
            desc = f"{t['title']} {'lead' if a['kind'] == 'lead' else 'IC'} ({model}). {a['charter']}".replace("\n", " ")
            fm = ["---", f"name: {a['id']}", f"description: {desc}", f"model: {model}",
                  f"tools: {TOOLS[(t['bash_profile'], a['kind'])]}", "---", ""]
            out[agents_dir / f"{a['id']}.md"] = "\n".join(fm) + body(a, t, phases)
    return out


def main(argv: List[str]) -> int:
    files = render()
    if "--check" in argv:
        stale = [p.name for p, txt in files.items() if not p.exists() or L.read_text(p) != txt]
        print(f"{len(files)} agent files; " + (f"stale or missing: {', '.join(stale)}" if stale else "all current"))
        return 1 if stale else 0
    for p, txt in files.items():
        L.write_text(p, txt)
    print(f"wrote {len(files)} agent files to .claude/agents/")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
