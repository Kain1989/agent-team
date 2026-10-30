#!/usr/bin/env python3
"""story.py — the story state machine. The only writer of state, runs, findings, packets
and human-gate records (guard.py denies every tool those paths).

  new <title> --project P --kind web|cli|lib|service   create STORY-NNN at phase 1
  board | status [ID] | show ID                         read state
  start ID                                              enter/resume the current phase
  gate ID                                               run the next gate of the current phase
  skip ID --reason R                                    skip a phase the story's kind allows skipping
  reopen ID --to PHASE --reason R                       send defects back along a declared loop
  worktree ID                                           create the story worktree + branch
  packet ID [PHASE]                                     build the clean-context packet for a phase
  run ID SUITE                                          run a test suite from env-report.json, record it
  trace ID REQ [--tasks ..] [--tests ..] [--pr N]       set a traceability row;  trace-show ID
  finding ID --file F --symbol S --category C --severity S --claim TEXT [--kind defect|test-flaw]
  escalate ID FP --to supervisor|human [--note]  /  resolve ID FP --note
  verdict ID --verdict approve|changes  (body on stdin) a lead's verdict for the current phase
  human-gate ID GATE approve|reject --by NAME [--note]  HUMAN ONLY: needs an interactive terminal
  waive ID --rule R --file F --reason R --approver NAME HUMAN ONLY: an integrity waiver
"""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib as L  # noqa: E402

PHASE_ORDER = [p["id"] for p in L.phases()]


# ---------------------------------------------------------------- state

def load(sid: str) -> Dict[str, Any]:
    p = L.story_dir(sid) / "state.json"
    if not p.exists():
        L.die(f"no such story: {sid}")
    return L.read_json(p)


def save(st: Dict[str, Any]) -> None:
    L.write_json(L.story_dir(st["id"]) / "state.json", st)


def log(st: Dict[str, Any], event: str, **kw: Any) -> None:
    st.setdefault("history", []).append({"at": L.now(), "event": event, **kw})


def set_active(st: Dict[str, Any]) -> None:
    L.write_json(L.state_dir() / "active.json", {"story": st["id"], "phase": st["phase"],
                                                  "round": st["rounds"].get(st["phase"], 1), "at": L.now()})


def enter(st: Dict[str, Any], phase: str) -> None:
    """Enter a phase. Re-entering counts a round; a round past the cap escalates the story."""
    st["phase"] = phase
    st["rounds"][phase] = st["rounds"].get(phase, 0) + 1
    st["phase_started_at"][phase] = L.now()
    for g in L.phase_by_id(phase)["gates"]:
        st["gates"].pop(g, None)
    cap = L.load_config("pipeline")["round_caps"].get(phase)
    if cap and st["rounds"][phase] > cap:
        st["status"] = "escalated"
        log(st, "round-cap", phase=phase, round=st["rounds"][phase], cap=cap)
    log(st, "enter", phase=phase, round=st["rounds"][phase])


def next_phase(phase: str) -> Optional[str]:
    i = PHASE_ORDER.index(phase)
    return PHASE_ORDER[i + 1] if i + 1 < len(PHASE_ORDER) else None


def advance(st: Dict[str, Any]) -> None:
    nxt = next_phase(st["phase"])
    if nxt is None:
        st["status"] = "done"
        log(st, "done")
    else:
        enter(st, nxt)


def require_active(st: Dict[str, Any]) -> None:
    if st["status"] == "waiting-human":
        pend = [h for h, v in st["human"].items() if v.get("status") == "pending"]
        L.die(f"{st['id']} is waiting on a human gate ({', '.join(pend)}). A person runs: "
              f"python .agentteam/bin/story.py human-gate {st['id']} {pend[0] if pend else '<gate>'} approve --by <name>")
    if st["status"] != "active":
        L.die(f"{st['id']} is {st['status']}; nothing advances until a person resolves it")


# ---------------------------------------------------------------- commands

def cmd_new(a: argparse.Namespace) -> None:
    kinds = L.load_config("pipeline")["story_kinds"]
    if a.kind not in kinds:
        L.die(f"--kind must be one of {kinds}")
    proj = L.workspace() / a.project
    if not (proj / ".git").exists():
        L.die(f"no project repo at workspace/{a.project} — run /add-project first")
    L.stories_dir().mkdir(parents=True, exist_ok=True)
    nums = [int(m.group(1)) for d in L.stories_dir().iterdir() if (m := re.match(r"STORY-(\d+)$", d.name))]
    sid = f"STORY-{(max(nums) + 1) if nums else 1:03d}"
    slug = re.sub(r"[^a-z0-9]+", "-", a.title.lower()).strip("-")[:40] or "story"
    base = L.git_out(["rev-parse", "--abbrev-ref", "HEAD"], proj) or "main"
    st = {"id": sid, "title": a.title, "kind": a.kind, "project": a.project, "created": L.now(),
          "status": "active", "phase": None, "branch": f"story/{sid.lower()}-{slug}", "base": base,
          "rounds": {}, "phase_started_at": {}, "gates": {}, "human": {}, "skips": {}, "history": []}
    enter(st, PHASE_ORDER[0])
    save(st)
    set_active(st)
    print(f"{sid} created: '{a.title}' on workspace/{a.project}, phase {st['phase']}")


def cmd_board(a: argparse.Namespace) -> None:
    if not L.stories_dir().exists():
        print("no stories yet — /story <title> starts one")
        return
    act = L.active().get("story")
    for d in sorted(L.stories_dir().iterdir()):
        if (d / "state.json").exists():
            st = L.read_json(d / "state.json")
            mark = "*" if st["id"] == act else " "
            ph = L.phase_by_id(st["phase"]) if st.get("phase") else {"n": "-", "title": "-"}
            print(f"{mark} {st['id']}  {st['status']:<14} phase {ph['n']:<3} {ph['title']:<13} "
                  f"round {st['rounds'].get(st['phase'], 0)}  {st['title']}")


def cmd_show(a: argparse.Namespace) -> None:
    sid = a.id or L.active().get("story")
    if not sid:
        L.die("no active story")
    st = load(sid)
    ph = L.phase_by_id(st["phase"])
    print(f"{st['id']}  {st['title']}  [{st['kind']}]  project workspace/{st['project']}")
    print(f"status {st['status']}   phase {ph['n']} {ph['title']} (team {ph['team']}, round {st['rounds'].get(st['phase'])})")
    for p in L.phases():
        gs = []
        for g in p["gates"]:
            s = st["gates"].get(g, {}).get("status")
            gs.append(f"{g}:{s or '-'}")
            h = L.human_gate_after(g)
            if h:
                gs.append(f"[{h['id']}:{st['human'].get(h['id'], {}).get('status', '-')}]")
        skip = f"  skipped: {st['skips'][p['id']]}" if p["id"] in st["skips"] else ""
        print(f"  {p['n']:<3} {p['title']:<13} {' '.join(gs)}{skip}")
    open_f = [f for f in latest_findings(sid).values() if f["status"] in ("open", "escalated")]
    if open_f:
        print(f"open findings: {len(open_f)} ({sum(1 for f in open_f if f['severity'] == 'blocking')} blocking)")


cmd_status = cmd_show


def cmd_start(a: argparse.Namespace) -> None:
    st = load(a.id)
    require_active(st)
    ph = L.phase_by_id(st["phase"])
    if ph.get("worktree"):
        ensure_worktree(st)
    st["phase_started_at"][st["phase"]] = st["phase_started_at"].get(st["phase"]) or L.now()
    save(st)
    set_active(st)
    print(f"{st['id']} phase {ph['n']} {ph['title']} — team {ph['team']}, lead {team_lead(ph['team'])}, "
          f"round {st['rounds'][st['phase']]}. Packet: python .agentteam/bin/story.py packet {st['id']}")


def team_lead(team: str) -> str:
    for t in L.load_config("roster")["teams"]:
        if t["id"] == team:
            return t["lead"]["id"]
    return "?"


def cmd_gate(a: argparse.Namespace) -> None:
    import gate as G
    st = load(a.id)
    require_active(st)
    ph = L.phase_by_id(st["phase"])
    pending = [g for g in ph["gates"] if st["gates"].get(g, {}).get("status") != "pass"]
    if not pending:
        L.die(f"all gates of {ph['id']} already passed")
    gid = pending[0]
    rec = G.run_gate(st["id"], gid)
    n = len(list((L.story_dir(st["id"]) / "gates").glob(f"{gid}-*.json"))) + 1
    rel = f"gates/{gid}-{n:02d}.json"
    L.write_json(L.story_dir(st["id"]) / rel, rec)
    st = load(a.id)  # the gate may have read state; reload before writing
    st["gates"][gid] = {"status": "pass" if rec["passed"] else "fail", "at": rec["at"], "record": rel}
    log(st, "gate", gate=gid, passed=rec["passed"])
    G.print_record(rec)
    if rec["passed"]:
        h = L.human_gate_after(gid)
        if h:
            st["human"][h["id"]] = {"status": "pending", "reviews": h["reviews"], "since": L.now()}
            st["status"] = "waiting-human"
            print(f"\nHUMAN GATE {h['id']}: a person reviews {h['reviews']} and runs, in their own terminal:\n"
                  f"  python .agentteam/bin/story.py human-gate {st['id']} {h['id']} approve --by <name>")
        elif gid == ph["gates"][-1]:
            advance(st)
            print(f"\n{st['id']} -> {st['phase'] or 'done'}")
    save(st)
    set_active(st)
    sys.exit(0 if rec["passed"] else 2)


def cmd_skip(a: argparse.Namespace) -> None:
    st = load(a.id)
    require_active(st)
    ph = L.phase_by_id(st["phase"])
    if st["kind"] not in ph.get("skippable_for_kinds", []):
        L.die(f"{ph['id']} is not skippable for a '{st['kind']}' story")
    if not a.reason.strip():
        L.die("a skip needs a reason")
    st["skips"][ph["id"]] = a.reason.strip()
    for g in ph["gates"]:
        st["gates"][g] = {"status": "skipped", "at": L.now()}
    log(st, "skip", phase=ph["id"], reason=a.reason)
    advance(st)
    save(st)
    set_active(st)
    print(f"skipped {ph['id']}; {st['id']} -> {st['phase']}")


def cmd_reopen(a: argparse.Namespace) -> None:
    st = load(a.id)
    if st["status"] not in ("active", "waiting-human"):
        L.die(f"{st['id']} is {st['status']}")
    loops = L.load_config("pipeline")["loops"]
    if not any(lp["from"] == st["phase"] and lp["to"] == a.to for lp in loops):
        ok = [f"{lp['from']} -> {lp['to']}" for lp in loops]
        L.die(f"no declared loop {st['phase']} -> {a.to}. Declared: {'; '.join(ok)}")
    frm = st["phase"]
    for p in PHASE_ORDER[PHASE_ORDER.index(a.to):]:
        for g in L.phase_by_id(p)["gates"]:
            st["gates"].pop(g, None)
    for h in L.load_config("pipeline")["human_gates"]:
        if PHASE_ORDER.index(L.phase_of_gate(h["after_gate"])["id"]) >= PHASE_ORDER.index(a.to):
            st["human"].pop(h["id"], None)
    st["status"] = "active"
    log(st, "reopen", frm=frm, to=a.to, reason=a.reason)
    enter(st, a.to)
    # the phase that sent the defects back counts its own round when it is re-entered
    save(st)
    set_active(st)
    print(f"{st['id']} reopened at {a.to} (round {st['rounds'][a.to]}) — {st['status']}")


def ensure_worktree(st: Dict[str, Any]) -> Path:
    wt = L.worktree_dir(st["id"])
    if wt.exists():
        return wt
    proj = L.workspace() / st["project"]
    wt.parent.mkdir(parents=True, exist_ok=True)
    r = L.git(["worktree", "add", "-b", st["branch"], str(wt), st["base"]], proj)
    if r.returncode != 0:
        r = L.git(["worktree", "add", str(wt), st["branch"]], proj)
    if r.returncode != 0:
        L.die(f"git worktree add failed: {r.stderr.strip()}")
    st["worktree"] = wt.relative_to(L.root()).as_posix()
    log(st, "worktree", path=st["worktree"], branch=st["branch"])
    save(st)
    print(f"worktree {st['worktree']} on {st['branch']} (from {st['base']})")
    return wt


def cmd_worktree(a: argparse.Namespace) -> None:
    ensure_worktree(load(a.id))


def code_dir(st: Dict[str, Any]) -> Path:
    wt = L.worktree_dir(st["id"])
    return wt if wt.exists() else L.workspace() / st["project"]


def story_diff(st: Dict[str, Any]) -> str:
    wt = L.worktree_dir(st["id"])
    if not wt.exists():
        return ""
    return L.git(["diff", f"{st['base']}...HEAD"], wt).stdout


# ---------------------------------------------------------------- packet (clean context)

def cmd_packet(a: argparse.Namespace) -> None:
    st = load(a.id)
    pid = a.phase or st["phase"]
    out = build_packet(st, pid)
    print(f"packet {out.relative_to(L.root()).as_posix()} — give the lead ONLY this directory")


def build_packet(st: Dict[str, Any], pid: str) -> Path:
    ph = L.phase_by_id(pid)
    sd = L.story_dir(st["id"])
    out = sd / "packets" / pid
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    sources = []
    for src in ph["reads"]:
        if src == "@diff":
            wt = L.worktree_dir(st["id"])
            diff = story_diff(st)
            files = L.git_out(["diff", "--name-only", f"{st['base']}...HEAD"], wt) if wt.exists() else ""
            L.write_text(out / "diff.patch", diff)
            L.write_text(out / "files.txt", files + ("\n" if files else ""))
            sources.append({"source": "@diff", "dest": "diff.patch", "sha256": L.sha256_text(diff),
                            "head": L.git_out(["rev-parse", "HEAD"], wt) if wt.exists() else ""})
            continue
        p = sd / src
        if not p.exists():
            continue
        dest = src.replace("/", "__")
        shutil.copyfile(p, out / dest)
        sources.append({"source": src, "dest": dest, "sha256": L.sha256_file(p)})
    L.write_json(out / "manifest.json", {"story": st["id"], "phase": pid, "built_at": L.now(), "sources": sources})
    return out


# ---------------------------------------------------------------- runs (evidence, not claims)

def cmd_run(a: argparse.Namespace) -> None:
    st = load(a.id)
    rep = L.read_json(L.state_dir() / "env-report.json", {})
    proj = (rep.get("projects") or {}).get(st["project"]) or {}
    suite = (proj.get("suites") or {}).get(a.suite)
    if not suite:
        L.die(f"no suite '{a.suite}' for {st['project']} in env-report.json — run env.py, or declare it in "
              f"workspace/{st['project']}/agentteam.json")
    if suite.get("na"):
        L.die(f"suite '{a.suite}' is declared N/A: {suite['na']}")
    rec = run_suite(st, a.suite, suite["argv"], a.timeout)
    print(f"{a.suite}: exit {rec['exit']} at {rec['head'][:10]} in {rec['seconds']}s  ({rec['record']})")
    print(rec["tail"])
    sys.exit(0 if rec["exit"] == 0 else 1)


def run_suite(st: Dict[str, Any], suite: str, argv: List[str], timeout: int = 1800) -> Dict[str, Any]:
    cwd = code_dir(st)
    t0 = time.time()
    try:
        r = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        code, output = r.returncode, (r.stdout or "") + (r.stderr or "")
    except FileNotFoundError as e:
        code, output = 127, f"not found: {e}"
    except subprocess.TimeoutExpired as e:
        code, output = 124, f"timeout after {timeout}s\n{e.stdout or ''}"
    runs = L.story_dir(st["id"]) / "runs"
    n = len(list(runs.glob(f"{suite}-*.json"))) + 1 if runs.exists() else 1
    L.write_text(runs / f"{suite}-{n:02d}.log", output)
    rec = {"suite": suite, "argv": argv, "cwd": cwd.relative_to(L.root()).as_posix() if L.rel_posix(cwd) else str(cwd),
           "exit": code, "head": L.git_out(["rev-parse", "HEAD"], cwd), "dirty": bool(L.git_out(["status", "--porcelain"], cwd)),
           "phase": st["phase"], "round": st["rounds"].get(st["phase"]), "at": L.now(),
           "seconds": round(time.time() - t0, 1), "output_sha256": L.sha256_text(output),
           "tail": "\n".join(output.strip().splitlines()[-15:]), "record": f"runs/{suite}-{n:02d}.json"}
    L.write_json(runs / f"{suite}-{n:02d}.json", rec)
    return rec


# ---------------------------------------------------------------- traceability

def cmd_trace(a: argparse.Namespace) -> None:
    st = load(a.id)
    p = L.story_dir(st["id"]) / "trace.json"
    tr = L.read_json(p, {"rows": {}})
    row = tr["rows"].setdefault(a.req, {"tasks": [], "tests": [], "pr": ""})
    if a.tasks is not None:
        row["tasks"] = a.tasks
    if a.tests is not None:
        row["tests"] = a.tests
    if a.pr is not None:
        row["pr"] = a.pr
    L.write_json(p, tr)
    print(f"{a.req}: tasks {row['tasks']} tests {row['tests']} pr {row['pr'] or '-'}")


def cmd_trace_show(a: argparse.Namespace) -> None:
    import gate as G
    st = load(a.id)
    for req, row, status in G.trace_rows(st):
        print(f"{req:<9} {','.join(row.get('tasks', [])) or '-':<20} {','.join(row.get('tests', [])) or '-':<50} "
              f"{row.get('pr') or '-':<6} {status}")


# ---------------------------------------------------------------- findings (anti-thrash)

def fingerprint(file: str, symbol: str, category: str) -> str:
    """Computed on the CLAIM'S SUBJECT (where + what kind), not its wording, so a reworded
    repeat of the same finding is still the same finding."""
    norm = lambda s: re.sub(r"^(\./|/)+", "", re.sub(r"[^a-z0-9_./]+", "", (s or "").lower().replace("\\", "/")))  # noqa: E731
    return hashlib.sha256(f"{norm(file)}|{norm(symbol)}|{norm(category)}".encode()).hexdigest()[:12]


def findings_path(sid: str) -> Path:
    return L.story_dir(sid) / "findings.jsonl"


def latest_findings(sid: str) -> Dict[str, Dict[str, Any]]:
    """fingerprint -> the latest record for it (status changes are appended, never edited)."""
    out: Dict[str, Dict[str, Any]] = {}
    for r in L.read_jsonl(findings_path(sid)):
        if "fingerprint" in r:
            out[r["fingerprint"]] = {**out.get(r["fingerprint"], {}), **r}
    return out


def cmd_finding(a: argparse.Namespace) -> None:
    st = load(a.id)
    fp = fingerprint(a.file, a.symbol, a.category)
    prior = [r for r in L.read_jsonl(findings_path(st["id"])) if r.get("fingerprint") == fp and r.get("event") == "raised"]
    rounds_seen = {(r["phase"], r["round"]) for r in prior}
    here = (st["phase"], st["rounds"].get(st["phase"]))
    sighting = len(rounds_seen | {here})
    need = "human" if sighting >= 3 else "supervisor" if sighting == 2 else None
    rec = {"event": "raised", "at": L.now(), "fingerprint": fp, "phase": st["phase"], "round": here[1],
           "file": a.file, "symbol": a.symbol, "category": a.category, "severity": a.severity,
           "kind": a.kind, "claim": a.claim, "status": "open", "sighting": sighting, "needs_escalation_to": need}
    L.append_jsonl(findings_path(st["id"]), rec)
    msg = f"finding {fp} ({a.severity}, sighting {sighting})"
    if need:
        msg += f" — SAME FINDING SEEN BEFORE: escalate to the {need} (story.py escalate {st['id']} {fp} --to {need})." \
               f" The gate fails while it sits unescalated."
    if a.kind == "test-flaw":
        msg += " — test-flaw: QA owns the fix; dev must not edit the test."
    print(msg)


def cmd_escalate(a: argparse.Namespace) -> None:
    st = load(a.id)
    if a.fp not in latest_findings(st["id"]):
        L.die(f"no finding {a.fp}")
    L.append_jsonl(findings_path(st["id"]), {"event": "escalated", "at": L.now(), "fingerprint": a.fp,
                                             "status": "escalated", "escalated_to": a.to, "note": a.note or ""})
    print(f"{a.fp} escalated to {a.to}")


def cmd_resolve(a: argparse.Namespace) -> None:
    st = load(a.id)
    if a.fp not in latest_findings(st["id"]):
        L.die(f"no finding {a.fp}")
    L.append_jsonl(findings_path(st["id"]), {"event": "resolved", "at": L.now(), "fingerprint": a.fp,
                                             "status": "resolved", "note": a.note})
    print(f"{a.fp} resolved")


def cmd_verdict(a: argparse.Namespace) -> None:
    st = load(a.id)
    ph = L.phase_by_id(st["phase"])
    body = "" if sys.stdin.isatty() else sys.stdin.read()
    d = L.story_dir(st["id"]) / ph["dir"]
    rec = {"phase": ph["id"], "round": st["rounds"].get(ph["id"]), "verdict": a.verdict, "at": L.now()}
    L.write_json(d / "verdict.json", rec)
    if ph["id"] == "phase5-code-review" and body.strip():
        L.write_text(d / "code-review.md", body.rstrip() + f"\n\nVerdict: {a.verdict}\n")
    print(f"{ph['id']} verdict: {a.verdict}")


# ---------------------------------------------------------------- human-only

def require_human_terminal(sid: str) -> None:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        L.die("this command records a human decision and needs an interactive terminal. "
              "Agents cannot run it; a person runs it in their own shell.")
    typed = input(f"Type the story id ({sid}) to confirm: ").strip()
    if typed != sid:
        L.die("confirmation did not match; nothing recorded")


def record_human_gate(sid: str, gate_id: str, decision: str, by: str, note: str, tty: bool) -> Dict[str, Any]:
    st = load(sid)
    h = st["human"].get(gate_id)
    if not h or h.get("status") != "pending":
        L.die(f"human gate {gate_id} is not pending on {sid}")
    if not by.strip():
        L.die("--by names the person deciding")
    h.update({"status": "approved" if decision == "approve" else "rejected", "by": by, "at": L.now(),
              "note": note, "tty": tty, "git_user": L.git_out(["config", "user.email"], L.root())})
    L.write_json(L.story_dir(sid) / "human" / f"{gate_id}.json", h)
    log(st, "human-gate", gate=gate_id, decision=h["status"], by=by)
    st["status"] = "active"
    if decision == "approve":
        g = next(x["after_gate"] for x in L.load_config("pipeline")["human_gates"] if x["id"] == gate_id)
        if L.phase_of_gate(g)["gates"][-1] == g:
            advance(st)
    else:
        g = next(x["after_gate"] for x in L.load_config("pipeline")["human_gates"] if x["id"] == gate_id)
        st["gates"].pop(g, None)
        enter(st, L.phase_of_gate(g)["id"])
    save(st)
    set_active(st)
    return st


def cmd_human_gate(a: argparse.Namespace) -> None:
    require_human_terminal(a.id)
    st = record_human_gate(a.id, a.gate, a.decision, a.by, a.note or "", True)
    print(f"{a.gate}: {st['human'][a.gate]['status']} by {a.by}; {a.id} now {st['status']} at {st['phase']}")


def cmd_waive(a: argparse.Namespace) -> None:
    require_human_terminal(a.id)
    record_waiver(a.id, a.rule, a.file, a.reason, a.approver)
    print("waiver recorded; it will be REPORTED by the gate, not absorbed")


def record_waiver(sid: str, rule: str, file: str, reason: str, approver: str) -> None:
    if not reason.strip() or not approver.strip():
        L.die("a waiver needs a written reason and a named approver")
    p = L.story_dir(sid) / "human" / "waivers.json"
    w = L.read_json(p, [])
    w.append({"rule": rule, "file": file, "reason": reason, "approver": approver, "at": L.now()})
    L.write_json(p, w)


# ---------------------------------------------------------------- cli

def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(prog="story.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("new"); s.add_argument("title"); s.add_argument("--project", required=True); s.add_argument("--kind", default="web")
    sub.add_parser("board")
    for name in ("status", "show"):
        s = sub.add_parser(name); s.add_argument("id", nargs="?")
    for name in ("start", "gate", "worktree"):
        s = sub.add_parser(name); s.add_argument("id")
    s = sub.add_parser("skip"); s.add_argument("id"); s.add_argument("--reason", required=True)
    s = sub.add_parser("reopen"); s.add_argument("id"); s.add_argument("--to", required=True); s.add_argument("--reason", required=True)
    s = sub.add_parser("packet"); s.add_argument("id"); s.add_argument("phase", nargs="?")
    s = sub.add_parser("run"); s.add_argument("id"); s.add_argument("suite"); s.add_argument("--timeout", type=int, default=1800)
    s = sub.add_parser("trace"); s.add_argument("id"); s.add_argument("req")
    s.add_argument("--tasks", nargs="*"); s.add_argument("--tests", nargs="*"); s.add_argument("--pr")
    s = sub.add_parser("trace-show"); s.add_argument("id")
    s = sub.add_parser("finding"); s.add_argument("id")
    for k in ("--file", "--symbol", "--category", "--claim"):
        s.add_argument(k, required=True)
    s.add_argument("--severity", choices=["blocking", "major", "minor"], required=True)
    s.add_argument("--kind", choices=["defect", "test-flaw"], default="defect")
    s = sub.add_parser("escalate"); s.add_argument("id"); s.add_argument("fp"); s.add_argument("--to", choices=["supervisor", "human"], required=True); s.add_argument("--note")
    s = sub.add_parser("resolve"); s.add_argument("id"); s.add_argument("fp"); s.add_argument("--note", required=True)
    s = sub.add_parser("verdict"); s.add_argument("id"); s.add_argument("--verdict", choices=["approve", "changes"], required=True)
    s = sub.add_parser("human-gate"); s.add_argument("id"); s.add_argument("gate"); s.add_argument("decision", choices=["approve", "reject"])
    s.add_argument("--by", required=True); s.add_argument("--note")
    s = sub.add_parser("waive"); s.add_argument("id")
    for k in ("--rule", "--file", "--reason", "--approver"):
        s.add_argument(k, required=True)
    a = ap.parse_args(argv)
    globals()["cmd_" + a.cmd.replace("-", "_")](a)


if __name__ == "__main__":
    main()
