"""Bridge (1): roster -> native-team teammate definitions.

Generates `.claude/agents/<role>.md` subagent definitions from team.json so Claude
Code's NATIVE agent teams can spawn our roster's roles as teammates (the docs:
"Spawn a teammate using the <agent-type> agent type"). A native teammate honors the
definition's `tools` allowlist + `model`, and the body is appended to its system
prompt. Run via the /sync-roster command (or `python -m parsers.agents_gen`).

Idempotent: rewrites the generated files each run so the agents track team.json.
Only ACTIVE developers + active staff become teammates (matches the workflow filter).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_HEADER = "<!-- generated from team.json by /sync-roster — do not edit by hand; re-run /sync-roster -->"


class RosterIdError(ValueError):
    """A role id in team.json cannot be used as a filename.

    A ValueError subclass so callers that already catch ValueError keep working, but named, because
    `json.JSONDecodeError` is ALSO a ValueError — and the two need different answers. An unusable id
    is one field to fix; an unparseable roster is "nothing can be read at all, and every other
    command in this plugin is broken too". verify_project.py draws exactly this line (exit 1 vs
    exit 2); reporting one as the other sends the reader to the wrong file.
    """


def _bad_agent_id(value: Any) -> Optional[str]:
    """Why `value` cannot be a role id — or None if it can.

    A role id is TWO things: the `name:` a native teammate is spawned by, and the FILENAME this
    module writes it to. Nothing validated the second, so the roster's data reached `Path()` raw.

    Measured, on a roster whose squad was added as `standup/portal` (which /add-project's prose
    refuses and its checker did not): `generate()` raised
    `FileNotFoundError: .../.claude/agents/standup/portal_a.md`. That is the first command
    /add-project tells the user to run, and the error names a path instead of the roster field that
    produced it. An id containing `..` is the same defect with a worse ending — the write lands
    OUTSIDE the output directory.

    WHY REFUSE RATHER THAN SANITISE. Sanitising the filename alone breaks the prune step, which
    matches on `f.stem`, so the file it just wrote is deleted on the next run. Sanitising the id
    too makes the agent type name diverge from the roster id the engine dispatches to — the run
    then fails on an unresolvable assignee, one layer further from the cause. Both repairs invent a
    promise; the id is simply wrong, and the roster is where it gets fixed.

    Deliberately NOT refused: a `.` inside the id. Measured — `Path("a.b.md").stem` is `"a.b"` —
    so a dotted id survives the prune step intact. Refusing it would be a guess.
    """
    if not isinstance(value, str) or not value.strip():
        return "it is empty or not a string"
    if "/" in value or "\\" in value:
        return "it contains a path separator, so it is not a filename"
    if value in (".", ".."):
        return "it is a relative-path component, not a name"
    if "\n" in value or "\0" in value:
        return "it contains a newline or a null byte"
    return None


def _tools_for(member: Dict[str, Any]) -> str:
    """Developers (git) get edit + bash; a role that must OPERATE the product (needs_bash, e.g.
    product_qa driving a browser) gets read + bash but not edit; everyone else gets read-only."""
    if member.get("git", False):
        return "Read, Grep, Glob, LS, Edit, Write, Bash"
    if member.get("needs_bash", False):
        return "Read, Grep, Glob, LS, Bash"
    return "Read, Grep, Glob, LS"


def _agent_md(member: Dict[str, Any], squad: Optional[Dict[str, Any]]) -> str:
    name = member["id"]
    role = member.get("role", "team member")
    focus = (member.get("focus") or "").strip()
    squad_name = squad.get("name") if squad else "staff"
    mission = (squad.get("mission") if squad else "") or ""
    desc = f"{role} on {squad_name}. {focus}".strip()
    if len(desc) > 200:
        desc = desc[:197] + "..."

    fm = [f"name: {name}", f"description: {desc}", f"tools: {_tools_for(member)}"]
    if member.get("model"):
        fm.append(f"model: {member['model']}")

    body: List[str] = [_HEADER, "", f'You are "{name}" — {role} on the {squad_name}.']
    # Persona FIRST — a second-person identity instruction goes BEFORE the operational checklist
    # (squad/lane/pair/SDLC/design-gate/charter below), because a persona placed after a checklist is
    # a persona that does not exist. This is the same contract the workflow's personaOf() enforces, so
    # the /team dispatch path does not run a persona-less shape while the workflow injects one.
    if member.get("persona"):
        body.append("")
        body.append(str(member["persona"]))
        body.append("")
    if mission:
        body.append(f"Squad mission: {mission}")
    if focus:
        body.append(f"Your lane: {focus}")
    if member.get("pair"):
        body.append(f'Your pair is "{member["pair"]}" — you challenge each other\'s plans and '
                    "diffs in a FRESH context (structured critique, never free-form debate).")
    if member.get("context"):
        body.append(f"Read {member['context']} and the project's README / CLAUDE.md before planning.")
    if member.get("tests"):
        body.append(f"Test gate: {member['tests']} — the suite must actually RUN and pass before "
                    "you call work done.")
    body.append("Follow the gated SDLC: plan first (no code), let your pair challenge it, implement + "
                "write/extend tests, then the fresh-context review ring, and commit on green to a "
                "feature branch. Never push, merge, or deploy.")
    # The design gate has to reach THIS dispatch path too. An improvement that lands only in the
    # workflow, while /team keeps running the old shape, is the quiet divergence the rulebook's
    # E-02/E-05 exist to stop — so the teammate definition carries the same contract.
    body.append(
        "DESIGN GATE — if your change has an OBSERVABLE surface (a page, chart, panel, or rendered "
        "output), it is NOT green until a design-quality review passes as well: run "
        "`node standup/control/verify_design_quality.js <url of the affected page>` against the live "
        "instance (the exit code is the verdict), then judge the [JUDGMENT] rules of "
        "DESIGN_RULEBOOK.md. Cite a rule id on every finding (E-01). A non-zero exit always fails; "
        "exit 0 proves NOTHING on its own (E-07) — the judge catches 'looks wrong' and is blind to "
        "'looks right, is lying'. One rule cited twice is a shared-component fix, not two tickets (E-02)."
    )
    for extra in ("charter", "rubric"):
        if member.get(extra):
            body.append(str(member[extra]))
    return "---\n" + "\n".join(fm) + "\n---\n" + "\n".join(body) + "\n"


def generate(team_json: Path, out_dir: Path) -> List[str]:
    """Write .claude/agents/<id>.md for every active dev + active staff in team_json.
    Returns the list of role ids written. Removes stale generated files no longer in the roster."""
    data = json.loads(team_json.read_text(encoding="utf-8"))
    written: List[str] = []

    members: List[tuple] = []
    for squad in data.get("teams", []) or []:
        for dev in squad.get("developers", []) or []:
            if dev.get("active", False):
                members.append((dev, squad))
    for st in data.get("staff", []) or []:
        if st.get("active", False):
            members.append((st, None))

    # Validate EVERY id before writing ANY file. Checking inside the write loop would leave a
    # half-synced .claude/agents/ behind — some roles present, the rest missing — and /team would
    # happily spawn from it, which is a worse state than the crash this replaces.
    bad = []
    for member, squad in members:
        why = _bad_agent_id(member.get("id"))
        if why:
            where = ("teams[%r].developers" % (squad.get("id") or squad.get("name") or "?")
                     if squad else "staff")
            bad.append("  %r in %s — %s" % (member.get("id"), where, why))
    if bad:
        raise RosterIdError(
            "cannot generate teammate definitions: a role id is also the FILENAME it is written "
            "to, and these are not usable as one:\n" + "\n".join(bad) +
            "\nFix the `id` field in %s and re-run /sync-roster. Nothing was written."
            % team_json)

    out_dir.mkdir(parents=True, exist_ok=True)
    for member, squad in members:
        (out_dir / f"{member['id']}.md").write_text(_agent_md(member, squad), encoding="utf-8")
        written.append(member["id"])

    # prune stale generated agent files (ones we wrote before but are no longer active)
    keep = set(written)
    for f in out_dir.glob("*.md"):
        try:
            if _HEADER in f.read_text(encoding="utf-8") and f.stem not in keep:
                f.unlink()
        except OSError:
            pass
    return written


def _find_team_json(start: Path) -> Optional[Path]:
    for cand in (start / "standup" / "team.json", start / "team.json"):
        if cand.exists():
            return cand
    return None


if __name__ == "__main__":
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    tj = _find_team_json(root)
    if not tj:
        print(f"no team.json under {root} (looked for standup/team.json + team.json)", file=sys.stderr)
        raise SystemExit(1)
    out = root / ".claude" / "agents"
    try:
        ids = generate(tj, out)
    except RosterIdError as exc:
        # A refusal is a report, not a traceback: it names the offending value, the field, and the
        # one edit that fixes it — the same contract the engine's own STOP blocks follow.
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
    print(f"wrote {len(ids)} teammate definitions to {out}: {', '.join(ids)}")
