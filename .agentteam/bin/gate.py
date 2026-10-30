#!/usr/bin/env python3
"""gate.py — the AUDITED layer. Reads a story's artifacts off disk and asserts against them.

Deterministic, re-runnable, and independent of what any agent claims: nothing here asks a
model anything. A gate passes only when every one of its checks passes; the record lists
each check and why it failed. Every check has a broken fixture in selftest.py that must
turn it red (E-03: a judge that cannot be shown to fail is not a judge).

  python gate.py list                 every registered check, by gate
  python gate.py run STORY-001 g1-prd run one gate without recording it (story.py gate records)
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib as L  # noqa: E402
import integrity  # noqa: E402

Result = Tuple[bool, str]


@dataclass
class Ctx:
    st: Dict[str, Any]
    sd: Path
    gate: str
    phase: Dict[str, Any]

    def text(self, rel: str) -> str:
        p = self.sd / rel
        return L.read_text(p) if p.exists() else ""

    @property
    def wt(self) -> Path:
        return L.worktree_dir(self.st["id"])

    @property
    def code(self) -> Path:
        return self.wt if self.wt.exists() else L.workspace() / self.st["project"]

    @property
    def head(self) -> str:
        return L.git_out(["rev-parse", "HEAD"], self.code)


@dataclass
class Check:
    id: str
    gate: str
    title: str
    fn: Callable[[Ctx], Result]


REGISTRY: List[Check] = []

ARTIFACTS = {
    "g1-prd": ["01-prd.md"],
    "g2-ux": ["02-ux/spec.md", "02-ux/decisions.md"],
    "g3-plan": ["03-dev/plan.md", "03-dev/architecture.md", "03-dev/challenge.md"],
    "g3-impl": ["03-dev/tasks.md"],
    "g4a-qa": ["04a-qa/report.md", "trace.json"],
    "g4b-qa": ["04b-qa/report.md"],
    "g5-review": ["05-review/code-review.md", "packets/phase5-code-review/manifest.json"],
    "g6-visual": ["06-visual/report.md"],
    "g7-deploy": ["07-deploy/record.md"],
}
GATES = list(ARTIFACTS)


def check(gate: str, cid: str, title: str):
    def deco(fn: Callable[[Ctx], Result]):
        REGISTRY.append(Check(f"{gate}.{cid}", gate, title, fn))
        return fn
    return deco


# ---------------------------------------------------------------- text helpers

REQ_DEF = re.compile(r"^(?:#{1,6}\s*|[-*]\s*|\|\s*)\**\s*(REQ-\d{3})\b", re.M)
TASK_DEF = re.compile(r"^(?:#{1,6}\s*|[-*]\s*(?:\[[ xX]\]\s*)?|\|\s*)\**\s*(TASK-\d{3})\b", re.M)
OPEN_Q = re.compile(r"\bTBD\b|\?\?|\bTODO:|\bOPEN:|^\s*[-*]\s*\[ \]\s*(?:Q\b|Question)", re.M)


def sections(text: str) -> List[str]:
    return [m.group(1).strip().lower() for m in re.finditer(r"^#{1,6}\s+(.+?)\s*#*\s*$", text, re.M)]


def section_body(text: str, name: str) -> str:
    m = re.search(rf"^(#{{1,6}})\s+{re.escape(name)}\s*#*\s*$", text, re.M | re.I)
    if not m:
        return ""
    level = len(m.group(1))
    rest = text[m.end():]
    nxt = re.search(rf"^#{{1,{level}}}\s+\S", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def missing_sections(text: str, names: List[str]) -> List[str]:
    have = sections(text)
    return [n for n in names if not any(h == n.lower() or h.startswith(n.lower()) for h in have)]


def blocks(text: str, rx: re.Pattern) -> Dict[str, str]:
    """id -> the text from its definition to the next definition or heading."""
    ms = list(rx.finditer(text))
    out: Dict[str, str] = {}
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(text)
        nxt = re.search(r"^#{1,6}\s", text[m.end():end], re.M)
        out.setdefault(m.group(1), text[m.start(): m.end() + (nxt.start() if nxt else end - m.end())])
    return out


def req_ids(ctx: Ctx) -> List[str]:
    return [m.group(1) for m in REQ_DEF.finditer(ctx.text("01-prd.md"))]


def last_verdict(text: str) -> str:
    vs = re.findall(r"^\s*\**Verdict\**\s*:\s*\**\s*([A-Za-z-]+)", text, re.M | re.I)
    return vs[-1].lower() if vs else ""


def need_sections(rel: str, names: List[str]) -> Callable[[Ctx], Result]:
    def fn(ctx: Ctx) -> Result:
        miss = missing_sections(ctx.text(rel), names)
        return (not miss, f"{rel} is missing section(s): {', '.join(miss)}" if miss else f"{rel}: all {len(names)} sections")
    return fn


# ---------------------------------------------------------------- common checks (every gate)

def _artifacts(ctx: Ctx) -> Result:
    miss = [a for a in ARTIFACTS[ctx.gate] if not (ctx.sd / a).exists() or not (ctx.sd / a).read_bytes().strip()]
    return (not miss, f"missing or empty: {', '.join(miss)}" if miss else "all artifacts present")


def _open_questions(ctx: Ctx) -> Result:
    hits = []
    for a in ARTIFACTS[ctx.gate]:
        if a.endswith(".md"):
            t = ctx.text(a)
            for m in OPEN_Q.finditer(t):
                hits.append(f"{a}:{t[: m.start()].count(chr(10)) + 1} '{m.group(0).strip()}'")
            oq = section_body(t, "Open questions").strip()
            if oq and not re.fullmatch(r"(?is)[-*\s]*(none|n/?a|-)\.?\s*", oq):
                hits.append(f"{a}: 'Open questions' is not empty")
    return (not hits, "unresolved: " + "; ".join(hits[:5]) if hits else "no open questions")


def _budget(ctx: Ctx) -> Result:
    b = L.load_config("budgets")["phases"].get(ctx.phase["id"])
    if not b:
        return True, "no budget declared"
    use = [u for u in L.read_jsonl(ctx.sd / "usage.jsonl") if u.get("phase") == ctx.phase["id"]]
    agents, tokens = len(use), sum(int(u.get("tokens") or 0) for u in use)
    unmeasured = sum(1 for u in use if not u.get("measured"))
    over = []
    if agents > b["agents"]:
        over.append(f"agents {agents} > {b['agents']}")
    if tokens > b["tokens"]:
        over.append(f"tokens {tokens:,} > {b['tokens']:,}")
    note = f" ({unmeasured} agent run(s) with UNMEASURED tokens)" if unmeasured else ""
    return (not over, ("over budget: " + ", ".join(over)) if over else f"agents {agents}/{b['agents']}, tokens {tokens:,}/{b['tokens']:,}{note}")


def _rounds(ctx: Ctx) -> Result:
    cap = L.load_config("pipeline")["round_caps"].get(ctx.phase["id"])
    r = ctx.st["rounds"].get(ctx.phase["id"], 1)
    if cap is None:
        return True, f"round {r} (no cap)"
    return (r <= cap, f"round {r} of cap {cap}" + ("" if r <= cap else " — the loop is over; a person decides"))


def _guard_healthy(ctx: Ctx) -> Result:
    since = ctx.st["phase_started_at"].get(ctx.phase["id"], "")
    errs = [e for e in L.read_jsonl(L.audit_dir() / "guard-errors.jsonl") if e.get("at", "") >= since]
    return (not errs, f"{len(errs)} guard internal error(s) during this phase — the hard layer was failing OPEN: "
            f"{errs[0].get('error')}" if errs else "guard ran clean during this phase")


for _g in GATES:
    check(_g, "artifacts-present", "required artifacts exist and are non-empty")(_artifacts)
    check(_g, "no-open-questions", "no TBD / ?? / TODO: / open questions left")(_open_questions)
    check(_g, "within-budget", "phase agents and tokens under budgets.json")(_budget)
    # only where a cap exists: a check that cannot fail is not a check (E-03)
    if L.phase_of_gate(_g)["id"] in L.load_config("pipeline")["round_caps"]:
        check(_g, "within-round-cap", "phase round within its cap")(_rounds)
    check(_g, "guard-healthy", "no guard internal errors during the phase")(_guard_healthy)


# ---------------------------------------------------------------- g1-prd

check("g1-prd", "sections", "PRD has Problem, Users, Requirements, Out of scope, Success metrics")(
    need_sections("01-prd.md", ["Problem", "Users", "Requirements", "Out of scope", "Success metrics"]))


@check("g1-prd", "req-ids", "requirements are numbered REQ-NNN and unique")
def _req_ids(ctx: Ctx) -> Result:
    ids = req_ids(ctx)
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if not ids:
        return False, "no REQ-NNN requirements defined"
    return (not dup, f"duplicate ids: {', '.join(dup)}" if dup else f"{len(ids)} requirements")


@check("g1-prd", "ears", "every REQ has a SHALL acceptance criterion (EARS)")
def _ears(ctx: Ctx) -> Result:
    bad = [r for r, b in blocks(ctx.text("01-prd.md"), REQ_DEF).items() if not re.search(r"\bSHALL\b", b)]
    return (not bad, f"no SHALL criterion: {', '.join(bad)}" if bad else "every REQ has a SHALL criterion")


@check("g1-prd", "out-of-scope", "Out of scope names at least one thing")
def _oos(ctx: Ctx) -> Result:
    body = section_body(ctx.text("01-prd.md"), "Out of scope").strip()
    ok = bool(re.search(r"^\s*[-*]\s*\S", body, re.M))
    return ok, "out of scope is listed" if ok else "Out of scope is empty — a PRD that excludes nothing has not decided anything"


# ---------------------------------------------------------------- g2-ux

check("g2-ux", "sections", "UX spec has Flows, States, Accessibility")(
    need_sections("02-ux/spec.md", ["Flows", "States", "Accessibility"]))


@check("g2-ux", "req-coverage", "every REQ is designed for or marked N/A with a reason")
def _ux_cov(ctx: Ctx) -> Result:
    spec = ctx.text("02-ux/spec.md")
    miss = [r for r in req_ids(ctx) if r not in spec]
    bare_na = [r for r in req_ids(ctx) if re.search(rf"{r}\s*[:\-—]+\s*N/?A\s*$", spec, re.M)]
    bad = miss + [f"{r} (N/A without a reason)" for r in bare_na]
    return (not bad, f"not covered: {', '.join(bad)}" if bad else "every REQ covered")


@check("g2-ux", "states", "spec covers empty, loading and error states")
def _ux_states(ctx: Ctx) -> Result:
    body = section_body(ctx.text("02-ux/spec.md"), "States").lower()
    miss = [s for s in ("empty", "loading", "error") if s not in body]
    return (not miss, f"States does not cover: {', '.join(miss)}" if miss else "empty, loading, error covered")


@check("g2-ux", "decision-log", "decision log records at least one decision with its reason")
def _ux_dec(ctx: Ctx) -> Result:
    t = ctx.text("02-ux/decisions.md")
    n = len(re.findall(r"^\s*[-*#]*\s*\**Decision\**\s*:", t, re.M | re.I))
    why = len(re.findall(r"^\s*[-*]?\s*\**(Because|Why|Reason)\**\s*:", t, re.M | re.I))
    ok = n >= 1 and why >= n
    return ok, f"{n} decision(s), {why} reason(s)" + ("" if ok else " — every Decision: needs a Because:/Why:")


# ---------------------------------------------------------------- g3-plan

check("g3-plan", "sections", "plan has Approach, Tasks, Risks, Test plan, Constitution check")(
    need_sections("03-dev/plan.md", ["Approach", "Tasks", "Risks", "Test plan", "Constitution check"]))


@check("g3-plan", "task-ids", "tasks are numbered TASK-NNN and unique")
def _task_ids(ctx: Ctx) -> Result:
    ids = [m.group(1) for m in TASK_DEF.finditer(ctx.text("03-dev/plan.md"))]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if not ids:
        return False, "no TASK-NNN tasks defined"
    return (not dup, f"duplicate ids: {', '.join(dup)}" if dup else f"{len(ids)} tasks")


@check("g3-plan", "tasks-cite-reqs", "every TASK cites a REQ that exists in the PRD")
def _task_reqs(ctx: Ctx) -> Result:
    reqs = set(req_ids(ctx))
    bad = []
    for t, b in blocks(ctx.text("03-dev/plan.md"), TASK_DEF).items():
        cited = set(re.findall(r"REQ-\d{3}", b))
        if not cited or cited - reqs:
            bad.append(f"{t}" + (f" cites unknown {','.join(sorted(cited - reqs))}" if cited - reqs else " cites no REQ"))
    return (not bad, "; ".join(bad) if bad else "every task cites a real REQ")


@check("g3-plan", "reqs-covered", "every REQ is covered by at least one TASK")
def _reqs_cov(ctx: Ctx) -> Result:
    cited = set()
    for b in blocks(ctx.text("03-dev/plan.md"), TASK_DEF).values():
        cited |= set(re.findall(r"REQ-\d{3}", b))
    miss = [r for r in req_ids(ctx) if r not in cited]
    return (not miss, f"no task delivers: {', '.join(miss)}" if miss else "every REQ has a task")


@check("g3-plan", "challenge-approved", "the plan challenge ended in Verdict: approve")
def _challenge(ctx: Ctx) -> Result:
    v = last_verdict(ctx.text("03-dev/challenge.md"))
    return v == "approve", f"challenge verdict: {v or 'none'}"


@check("g3-plan", "architecture-approved", "the architecture verdict is approve")
def _arch(ctx: Ctx) -> Result:
    v = last_verdict(ctx.text("03-dev/architecture.md"))
    return v == "approve", f"architecture verdict: {v or 'none'}"


# ---------------------------------------------------------------- g3-impl

def plan_tasks(ctx: Ctx) -> List[str]:
    return list(dict.fromkeys(m.group(1) for m in TASK_DEF.finditer(ctx.text("03-dev/plan.md"))))


def diff_files(ctx: Ctx) -> List[str]:
    out = L.git_out(["diff", "--name-only", f"{ctx.st['base']}...HEAD"], ctx.wt) if ctx.wt.exists() else ""
    return [f for f in out.splitlines() if f.strip()]


@check("g3-impl", "on-story-branch", "the worktree exists on the story branch, not a default branch")
def _branch(ctx: Ctx) -> Result:
    if not ctx.wt.exists():
        return False, "no story worktree (story.py worktree)"
    br = L.git_out(["rev-parse", "--abbrev-ref", "HEAD"], ctx.wt)
    if br in L.load_config("policy")["default_branches"]:
        return False, f"worktree is on default branch {br}"
    return br == ctx.st["branch"], f"on {br}" + ("" if br == ctx.st["branch"] else f", expected {ctx.st['branch']}")


@check("g3-impl", "non-empty-diff", "the story branch differs from its base")
def _nonempty(ctx: Ctx) -> Result:
    files = diff_files(ctx)
    return (bool(files), f"{len(files)} file(s) changed" if files else
            "EMPTY DIFF: nothing was committed on the story branch. This is a process failure (writes blocked, "
            "or never committed), not a code-quality one — check guard-denials.jsonl before re-running dev.")


@check("g3-impl", "committed", "the worktree has no uncommitted changes")
def _clean(ctx: Ctx) -> Result:
    if not ctx.wt.exists():
        return False, "no worktree"
    dirty = L.git_out(["status", "--porcelain"], ctx.wt)
    return (not dirty, "uncommitted: " + ", ".join(l[3:] for l in dirty.splitlines()[:5]) if dirty else "clean")


@check("g3-impl", "tasks-done", "every plan TASK is checked off in 03-dev/tasks.md")
def _tasks_done(ctx: Ctx) -> Result:
    done = set(re.findall(r"^\s*[-*]\s*\[[xX]\]\s*\**(TASK-\d{3})", ctx.text("03-dev/tasks.md"), re.M))
    miss = [t for t in plan_tasks(ctx) if t not in done]
    return (not miss, f"not done: {', '.join(miss)}" if miss else f"all {len(done)} tasks done")


SECRET_PATTERNS = [
    ("aws-access-key-id", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private-key-block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    ("aws-secret", re.compile(r"(?i)aws_secret_access_key\s*[=:]\s*['\"]?[A-Za-z0-9/+=]{40}")),
    ("slack-token", re.compile(r"xox[baprs]-[0-9A-Za-z-]{10,}")),
    ("github-token", re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}")),
    ("anthropic-key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("openai-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}")),
    ("generic-secret", re.compile(r"(?i)(api[_-]?key|secret|password|access[_-]?token|auth[_-]?token)\s*[=:]\s*['\"][A-Za-z0-9_\-/+=]{20,}['\"]")),
]


def scan_secrets(text: str) -> List[str]:
    return [name for name, rx in SECRET_PATTERNS if rx.search(text)]


@check("g3-impl", "no-secrets", "no secret-shaped strings in the added lines")
def _secrets(ctx: Ctx) -> Result:
    if not ctx.wt.exists():
        return False, "no worktree"
    diff = L.git(["diff", f"{ctx.st['base']}...HEAD"], ctx.wt).stdout
    added = "\n".join(l[1:] for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))
    hits = scan_secrets(added)
    return (not hits, f"secret-shaped content: {', '.join(hits)}" if hits else "no secrets in the diff")


@check("g3-impl", "no-sensitive-files", "no secret-bearing files added or changed")
def _sensitive(ctx: Ctx) -> Result:
    pol = L.load_config("policy")
    bad = [f for f in diff_files(ctx) if L.any_glob(f, pol["sensitive_paths"]) and not L.any_glob(f, pol["sensitive_path_exceptions"])]
    return (not bad, f"sensitive files in the diff: {', '.join(bad)}" if bad else "no sensitive files")


def latest_findings(ctx: Ctx) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for r in L.read_jsonl(ctx.sd / "findings.jsonl"):
        if "fingerprint" in r:
            out[r["fingerprint"]] = {**out.get(r["fingerprint"], {}), **r}
    return out


def _repeats(ctx: Ctx) -> Result:
    bad = []
    for fp, f in latest_findings(ctx).items():
        need = f.get("needs_escalation_to")
        if need and f.get("status") == "open":
            bad.append(f"{fp} (sighting {f.get('sighting')}, needs {need})")
        elif need == "human" and f.get("status") == "escalated" and f.get("escalated_to") != "human":
            bad.append(f"{fp} (sighting {f.get('sighting')} escalated to {f.get('escalated_to')}, needs human)")
    return (not bad, "repeat finding(s) unescalated: " + ", ".join(bad) if bad else "no unescalated repeats")


for _g in ("g3-impl", "g4a-qa", "g5-review", "g6-visual"):
    check(_g, "no-unescalated-repeats", "a finding seen in an earlier round has been escalated")(_repeats)


# ---------------------------------------------------------------- QA (4a, 4b)

def env_project(ctx: Ctx) -> Dict[str, Any]:
    return (L.read_json(L.state_dir() / "env-report.json", {}).get("projects") or {}).get(ctx.st["project"]) or {}


def latest_run(ctx: Ctx, suite: str) -> Optional[Dict[str, Any]]:
    runs = sorted((ctx.sd / "runs").glob(f"{suite}-*.json")) if (ctx.sd / "runs").exists() else []
    return L.read_json(runs[-1]) if runs else None


def run_ok(suite: str, optional: bool = False) -> Callable[[Ctx], Result]:
    def fn(ctx: Ctx) -> Result:
        decl = (env_project(ctx).get("suites") or {}).get(suite)
        if optional and decl and decl.get("na"):
            return True, f"{suite}: N/A — {decl['na']}"
        r = latest_run(ctx, suite)
        if not r:
            return False, f"{suite}: never run by the harness (story.py run {ctx.st['id']} {suite}); a reported pass counts for nothing"
        head = ctx.head
        if r["exit"] != 0:
            return False, f"{suite}: exit {r['exit']} ({r['record']})"
        if r["head"] != head:
            return False, f"{suite}: stale — run at {r['head'][:10]}, HEAD is {head[:10]}"
        if r.get("dirty"):
            return False, f"{suite}: ran on a dirty tree; commit, then re-run"
        return True, f"{suite}: exit 0 at {head[:10]} ({r['record']})"
    return fn


@check("g4a-qa", "env-report", "env-report.json exists, is under 24h old, and covers the project")
def _env(ctx: Ctx) -> Result:
    rep = L.read_json(L.state_dir() / "env-report.json", {})
    if not rep:
        return False, "no env-report.json (python .agentteam/bin/env.py)"
    import datetime as dt
    age = (dt.datetime.now(dt.timezone.utc) - L.parse_time(rep["generated"])).total_seconds() / 3600
    if age > 24:
        return False, f"env-report.json is {age:.0f}h old"
    return (bool(env_project(ctx)), "fresh" if env_project(ctx) else f"env-report.json has no entry for {ctx.st['project']}")


check("g4a-qa", "unit-run", "unit suite run by the harness, exit 0 at HEAD")(run_ok("unit"))
check("g4a-qa", "integration-run", "integration suite run at HEAD, or declared N/A with a reason")(run_ok("integration", optional=True))


def cites_commands(report: str, suites: List[str]) -> Callable[[Ctx], Result]:
    def fn(ctx: Ctx) -> Result:
        text = ctx.text(report)
        miss = []
        for s in suites:
            r = latest_run(ctx, s)
            if r and " ".join(r["argv"]) not in text and r["record"] not in text:
                miss.append(s)
        return (not miss, f"{report} does not name what actually ran for: {', '.join(miss)}" if miss
                else f"{report} names the commands that ran")
    return fn


check("g4a-qa", "report-cites-runs", "the report names the exact command or run record of each suite")(
    cites_commands("04a-qa/report.md", ["unit", "integration"]))


def _integrity(ctx: Ctx) -> Result:
    if not ctx.wt.exists():
        return False, "no worktree"
    vio = integrity.check(ctx.wt, ctx.st["base"])
    waivers = L.read_json(ctx.sd / "human" / "waivers.json", [])
    open_v, waived = integrity.apply_waivers(vio, waivers)
    msg = f"{len(open_v)} violation(s): " + "; ".join(f"{v['rule']} {v['file']}:{v.get('line', '?')}" for v in open_v[:4]) if open_v \
        else "test files clean"
    if waived:
        msg += f" | WAIVED (reported, not absorbed): " + "; ".join(f"{v['rule']} {v['file']} by {v['waiver']['approver']}: {v['waiver']['reason']}" for v in waived)
    return not open_v, msg


check("g4a-qa", "test-integrity", "no weakened, skipped, disabled or re-valued assertions vs the base")(_integrity)


def trace_rows(st: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any], str]]:
    ctx = Ctx(st, L.story_dir(st["id"]), "g4a-qa", L.phase_by_id("phase4a-unit-integration"))
    rows = L.read_json(ctx.sd / "trace.json", {"rows": {}})["rows"]
    out = []
    for req in req_ids(ctx):
        row = rows.get(req, {})
        unresolved = [t for t in row.get("tests", []) if not resolve_test(ctx.code, t)[0]]
        status = "missing row" if req not in rows else "blocks sign-off" if not row.get("tests") else \
            f"unresolved: {', '.join(unresolved)}" if unresolved else "covered"
        out.append((req, row, status))
    return out


def resolve_test(code: Path, ref: str) -> Tuple[bool, str]:
    """`path::name` (or `path::Class::name`): the file exists and each name is defined in it."""
    parts = ref.split("::")
    f = code / parts[0]
    if not parts[0] or not f.is_file():
        return False, f"{parts[0]} does not exist"
    if len(parts) == 1:
        return True, "file"
    src = L.read_text(f)
    for name in parts[1:]:
        n = re.escape(name)
        pats = [rf"\b(def|class)\s+{n}\b", rf"\bfunc\s+{n}\b", rf"\bfunction\s+{n}\b",
                rf"\b(it|test|describe)\s*\(\s*['\"`]{n}['\"`]", rf"\bfn\s+{n}\b"]
        if not any(re.search(p, src) for p in pats):
            return False, f"{name} is not defined in {parts[0]}"
    return True, "resolved"


@check("g4a-qa", "trace-complete", "every REQ has a traceability row")
def _trace_complete(ctx: Ctx) -> Result:
    miss = [r for r, _, s in trace_rows(ctx.st) if s == "missing row"]
    return (not miss, f"no row: {', '.join(miss)}" if miss else "every REQ has a row")


@check("g4a-qa", "trace-tests", "no REQ has an empty Tests cell")
def _trace_tests(ctx: Ctx) -> Result:
    empty = [r for r, _, s in trace_rows(ctx.st) if s == "blocks sign-off"]
    return (not empty, f"no tests for: {', '.join(empty)} — QA sign-off is blocked" if empty else "every REQ has tests")


@check("g4a-qa", "trace-resolves", "every cited test resolves on disk (file present, name present)")
def _trace_resolve(ctx: Ctx) -> Result:
    bad = [f"{r}: {s}" for r, _, s in trace_rows(ctx.st) if s.startswith("unresolved")]
    return (not bad, "; ".join(bad) + " — claiming coverage is not having it" if bad else "every cited test resolves")


check("g4b-qa", "system-run", "system suite run at HEAD, exit 0")(run_ok("system"))
check("g4b-qa", "regression-run", "full regression suite run at HEAD, exit 0")(run_ok("regression"))
check("g4b-qa", "perf-run", "perf suite run at HEAD, or declared N/A with a reason")(run_ok("perf", optional=True))
check("g4b-qa", "test-integrity", "test files still clean after system tests were added")(_integrity)
check("g4b-qa", "report-cites-runs", "the report names what actually ran")(
    cites_commands("04b-qa/report.md", ["system", "regression", "perf"]))


# ---------------------------------------------------------------- g5-review (clean context)

def manifest(ctx: Ctx) -> Dict[str, Any]:
    return L.read_json(ctx.sd / "packets" / "phase5-code-review" / "manifest.json", {})


@check("g5-review", "packet-clean", "the review packet holds only the phase's declared reads, nothing withheld")
def _packet_clean(ctx: Ctx) -> Result:
    ph = L.phase_by_id("phase5-code-review")
    m = manifest(ctx)
    pdir = ctx.sd / "packets" / "phase5-code-review"
    bad = [s["source"] for s in m.get("sources", []) if s["source"] not in ph["reads"] or L.any_glob(s["source"], ph["withheld"])]
    allowed = {s["dest"] for s in m.get("sources", [])} | {"manifest.json", "files.txt"}
    extra = [p.name for p in pdir.iterdir() if p.name not in allowed] if pdir.exists() else []
    bad += [f"unlisted file {e}" for e in extra]
    return (not bad and bool(m), "contaminated packet: " + ", ".join(bad) if bad else
            ("no packet manifest" if not m else f"{len(m['sources'])} declared source(s), nothing withheld"))


@check("g5-review", "packet-fresh", "the packet matches the current artifacts and HEAD")
def _packet_fresh(ctx: Ctx) -> Result:
    m = manifest(ctx)
    if not m:
        return False, "no packet manifest"
    stale = []
    for s in m["sources"]:
        if s["source"] == "@diff":
            if s.get("head") != ctx.head:
                stale.append(f"diff (packet at {str(s.get('head'))[:10]}, HEAD {ctx.head[:10]})")
        elif not (ctx.sd / s["source"]).exists() or L.sha256_file(ctx.sd / s["source"]) != s["sha256"]:
            stale.append(s["source"])
    return (not stale, "stale: " + ", ".join(stale) + " — rebuild with story.py packet" if stale else "packet is current")


@check("g5-review", "lenses", "the review covers every review lens")
def _lenses(ctx: Ctx) -> Result:
    lenses = L.load_config("pipeline")["review_lenses"]
    miss = missing_sections(ctx.text("05-review/code-review.md"), lenses)
    return (not miss, f"no section for lens(es): {', '.join(miss)}" if miss else f"{len(lenses)} lenses covered")


def lead_verdict(phase_dir: str) -> Callable[[Ctx], Result]:
    def fn(ctx: Ctx) -> Result:
        v = L.read_json(ctx.sd / phase_dir / "verdict.json", {})
        r = ctx.st["rounds"].get(ctx.phase["id"])
        if v.get("round") != r:
            return False, f"no lead verdict for round {r}"
        return v.get("verdict") == "approve", f"lead verdict: {v.get('verdict')}"
    return fn


check("g5-review", "lead-verdict", "the review lead's verdict for this round is approve")(lead_verdict("05-review"))


def no_open_blocking(phases: Optional[List[str]] = None) -> Callable[[Ctx], Result]:
    def fn(ctx: Ctx) -> Result:
        bad = [fp for fp, f in latest_findings(ctx).items()
               if f.get("severity") == "blocking" and f.get("status") != "resolved" and (phases is None or f.get("phase") in phases)]
        return (not bad, f"{len(bad)} blocking finding(s) not resolved: {', '.join(bad)}" if bad else "no open blocking findings")
    return fn


check("g5-review", "no-open-blocking", "every blocking finding is resolved")(no_open_blocking())


# ---------------------------------------------------------------- g6-visual

@check("g6-visual", "judge-run", "the design judge ran at HEAD and exited 0 (required, not sufficient: E-07)")
def _judge(ctx: Ctx) -> Result:
    ok, msg = run_ok("visual")(ctx)
    return ok, msg + (" — exit 0 proves nothing on its own; the lead still owes a judgment" if ok else "")


@check("g6-visual", "screenshots", "every screenshot the report cites exists")
def _shots(ctx: Ctx) -> Result:
    cited = re.findall(r"[\w./-]+\.(?:png|jpe?g|webp)", ctx.text("06-visual/report.md"), re.I)
    if not cited:
        return False, "the report cites no screenshots — a visual verdict needs captured evidence"
    miss = [c for c in cited if not (ctx.sd / "06-visual" / c).exists() and not (ctx.sd / c).exists()]
    return (not miss, f"missing: {', '.join(miss)}" if miss else f"{len(cited)} screenshot(s)")


check("g6-visual", "lead-verdict", "the visual lead's verdict for this round is approve")(lead_verdict("06-visual"))
check("g6-visual", "no-open-blocking", "no blocking visual finding is open")(no_open_blocking(["phase6-visual"]))


# ---------------------------------------------------------------- g7-deploy

check("g7-deploy", "sections", "record has Environment, Version, Verification, Rollback")(
    need_sections("07-deploy/record.md", ["Environment", "Version", "Verification", "Rollback"]))


@check("g7-deploy", "dev-only", "this phase deployed to dev only; beyond dev waits for a person")
def _dev_only(ctx: Ctx) -> Result:
    env = section_body(ctx.text("07-deploy/record.md"), "Environment").strip().split()
    env0 = env[0].strip("`*-:").lower() if env else ""
    free = L.load_config("policy")["deploy"]["free_envs"]
    return env0 in free, f"environment: {env0 or 'none'}" + ("" if env0 in free else f" — only {free} before deploy-beyond-dev")


check("g7-deploy", "smoke-run", "the smoke check against dev was run by the harness, exit 0")(run_ok("smoke"))


@check("g7-deploy", "rollback", "rollback steps are written down")
def _rollback(ctx: Ctx) -> Result:
    body = section_body(ctx.text("07-deploy/record.md"), "Rollback").strip()
    ok = len(re.findall(r"^\s*(?:[-*]|\d+\.)\s*\S", body, re.M)) >= 1
    return ok, "rollback steps present" if ok else "no rollback steps"


# ---------------------------------------------------------------- run

def checks_for(gate: str) -> List[Check]:
    return [c for c in REGISTRY if c.gate == gate]


def run_gate(sid: str, gid: str) -> Dict[str, Any]:
    st = L.read_json(L.story_dir(sid) / "state.json")
    ctx = Ctx(st, L.story_dir(sid), gid, L.phase_of_gate(gid))
    results = []
    for c in checks_for(gid):
        try:
            ok, msg = c.fn(ctx)
        except Exception as e:  # a crashing check FAILS — the audited layer fails closed
            ok, msg = False, f"check crashed: {type(e).__name__}: {e}"
        results.append({"id": c.id, "ok": bool(ok), "msg": msg})
    return {"story": sid, "gate": gid, "phase": ctx.phase["id"], "round": st["rounds"].get(ctx.phase["id"]),
            "at": L.now(), "head": ctx.head, "passed": all(r["ok"] for r in results), "checks": results}


def print_record(rec: Dict[str, Any]) -> None:
    n_ok = sum(r["ok"] for r in rec["checks"])
    print(f"{rec['gate']} ({rec['story']}, round {rec['round']}): {'PASS' if rec['passed'] else 'FAIL'} "
          f"— {n_ok} of {len(rec['checks'])} checks")
    for r in rec["checks"]:
        print(f"  {'ok  ' if r['ok'] else 'FAIL'} {r['id']:<34} {r['msg']}")


def main(argv: List[str]) -> int:
    if argv[:1] == ["list"]:
        for g in GATES:
            print(f"{g} ({len(checks_for(g))})")
            for c in checks_for(g):
                print(f"  {c.id:<34} {c.title}")
        print(f"{len(REGISTRY)} checks across {len(GATES)} gates")
        return 0
    if len(argv) == 3 and argv[0] == "run":
        rec = run_gate(argv[1], argv[2])
        print_record(rec)
        return 0 if rec["passed"] else 2
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
