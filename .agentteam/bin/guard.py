#!/usr/bin/env python3
"""guard.py — the HARD layer. A Claude Code PreToolUse hook: a denied call never runs.

Who is calling: the hook input's `agent_type` names the subagent (a roster id such as
`qa-lead`); no `agent_type` means the main session, role `supervisor`; an `agent_type`
that is not in the roster gets role `unknown` (readonly, no write roots). Identity comes
from the event, not from cwd — v1's cwd-based identity is what forced its run-flag dance.

What it enforces (config: .agentteam/config/policy.json + roster.json + pipeline.json):
  * every role: no reads or writes of secrets; no writes to git internals, harness code,
    or audit records; no force push, push to a default branch, PR merge, hook bypass,
    destructive rm/git, curl|sh; no `story.py human-gate|usage|waive` (those are human-only);
  * per role: Bash profile (readonly roles are allow-listed) and write roots (story phase
    dirs of its own team, its scratch dir, repo/worktree — QA test files only, dev never
    acceptance tests); review roles may not read what their packet withholds;
  * deploys: devops only, and beyond dev only after the deploy-beyond-dev human gate.

FAIL-OPEN, AUDITED. An internal error allows the call — a guard bug must not brick a
session — but it is appended to .agentteam/state/audit/guard-errors.jsonl, and every gate
fails while an error is recorded inside its phase. So a broken guard is loud, not silent.

Exit 0 always; a deny is the PreToolUse JSON decision on stdout.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib as L  # noqa: E402

READ_TOOLS = {"Read": "file_path", "Grep": "path", "Glob": "path", "LS": "path"}
WRITE_TOOLS = {"Edit": "file_path", "Write": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}
SEGMENT_OPS = {"&&", "||", ";", "|", "&", "\n", "|&"}
WRITE_VERBS = re.compile(r"(^|\s)(>>?|tee|cp|mv|rm|ln|truncate|chmod|chown|install|rsync|dd)(\s|$)|\bsed\s+(-[a-z]*i|--in-place)|\bpython[0-9.]*(\.exe)?\s+-c\b|\bperl\s+-[a-z]*[ie]")
LEAD_ONLY_SUBCOMMANDS = {"finding", "verdict", "escalate", "resolve", "trace"}
ALLOWED_HARNESS_SCRIPTS = {"story.py", "gate.py", "env.py", "selftest.py", "architecture.py", "integrity.py", "project.py"}
MAINTAINER_TTL = 3600

Decision = Tuple[str, str, str]  # (allow|deny, rule id, reason)
ALLOW: Decision = ("allow", "", "")


def deny(rule: str, reason: str) -> Decision:
    return ("deny", rule, reason)


# ---------------------------------------------------------------- role

def resolve_role(ev: Dict[str, Any]) -> Dict[str, Any]:
    at = ev.get("agent_type")
    if not at:
        return {"id": "supervisor", "team": None, "kind": "supervisor", "bash_profile": "supervisor", "write_roots": []}
    at = str(at).split(":")[-1]          # plugin agents arrive namespaced: "agent-team:qa-lead"
    info = L.roster_index().get(at)
    if info:
        return info
    return {"id": at, "team": None, "kind": "unknown", "bash_profile": "readonly", "write_roots": []}


def maintainer_mode() -> bool:
    p = L.state_dir() / "maintainer"
    try:
        return (time.time() - p.stat().st_mtime) < MAINTAINER_TTL
    except OSError:
        return False


# ---------------------------------------------------------------- policy helpers

def policy() -> Dict[str, Any]:
    return L.load_config("policy")


def profile(name: str) -> Dict[str, Any]:
    profs = policy()["bash_profiles"]
    p = dict(profs.get(name) or profs["readonly"])
    seen = {name}
    while "extends" in p and p["extends"] not in seen:
        parent = profs[p["extends"]]
        seen.add(p.pop("extends"))
        p = {**parent, **p}
    p.pop("extends", None)
    return p


def is_sensitive(rel: str) -> bool:
    pol = policy()
    return L.any_glob(rel, pol["sensitive_paths"]) and not L.any_glob(rel, pol["sensitive_path_exceptions"])


def team_phases(team: Optional[str]) -> List[Dict[str, Any]]:
    return [p for p in L.phases() if p["team"] == team]


def write_globs(role: Dict[str, Any]) -> List[str]:
    if role["kind"] == "supervisor":
        return list(policy()["supervisor_write_roots"])
    out: List[str] = []
    for root in role.get("write_roots", []):
        if root == "story":
            for ph in team_phases(role["team"]):
                out += [f".agentteam/stories/*/{w}" for w in ph["writes"]]
        elif root == "state":
            out.append(f".agentteam/state/scratch/{role['team']}/**")
        elif root == "repo":
            out.append("workspace/*/**")
        elif root == "worktree":
            out.append("workspace/.worktrees/**")
    return out


def withheld_globs(role: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for ph in team_phases(role.get("team")):
        out += [f".agentteam/stories/*/{w}" for w in ph.get("withheld", [])]
    return out


def code_rel(rel: str) -> Optional[str]:
    """For a path in a project repo or story worktree, the path inside that checkout."""
    m = re.match(r"workspace/\.worktrees/[^/]+/(.+)$", rel) or re.match(r"workspace/[^/.][^/]*/(.+)$", rel)
    return m.group(1) if m else None


def in_write_roots(rel: str, role: Dict[str, Any]) -> bool:
    # `workspace/*/**` (repo) would also match `workspace/.worktrees/...`; a worktree is only
    # writable to a role that holds the worktree root.
    if rel.startswith("workspace/.worktrees/") and "worktree" not in role.get("write_roots", []):
        return False
    return L.any_glob(rel, write_globs(role))


# ---------------------------------------------------------------- path tools

def target_path(ev: Dict[str, Any], key: str) -> Optional[Path]:
    raw = (ev.get("tool_input") or {}).get(key)
    if not raw:
        return None
    p = Path(str(raw))
    if not p.is_absolute():
        p = Path(ev.get("cwd") or os.getcwd()) / p
    return p


def check_path_tool(ev: Dict[str, Any], role: Dict[str, Any], tool: str) -> Decision:
    writing = tool in WRITE_TOOLS
    key = WRITE_TOOLS.get(tool) or READ_TOOLS[tool]
    p = target_path(ev, key)
    if p is None:
        return ALLOW if not writing else deny("no-path", f"{tool} without a path")
    rel = L.rel_posix(p)
    name_rel = rel if rel is not None else p.as_posix()
    if is_sensitive(name_rel):
        return deny("sensitive-path", f"{tool} on a secret-bearing path ({p.name}) is never allowed")
    if not writing:
        # `rel + "/"`: a Grep/Glob/LS aimed at a withheld DIRECTORY reads everything under it
        if rel is not None and (L.any_glob(rel, withheld_globs(role)) or L.any_glob(rel + "/", withheld_globs(role))):
            return deny("withheld-read", f"{role['id']} may not read {rel}: it is withheld from the {role['team']} packet. "
                        "If you need the author's reasoning to understand the code, that gap is your finding.")
        return ALLOW
    # ---- writes
    if rel is None:
        if role["kind"] == "supervisor":
            return ALLOW
        return deny("outside-root", f"{role['id']} may not write outside the harness root ({p})")
    if re.search(r"(^|/)\.git(/|$)", rel):
        return deny("git-internals", "writes into .git are never allowed")
    pol = policy()
    if L.any_glob(rel, pol["harness_paths"]):
        if role["kind"] == "supervisor" and maintainer_mode():
            return ALLOW
        return deny("harness-path", f"{rel} is harness code/config; changing it needs maintainer mode (a human creates .agentteam/state/maintainer)")
    if L.any_glob(rel, pol["audit_paths"]):
        return deny("audit-path", f"{rel} is an audit record; only story.py writes it")
    allowed = write_globs(role)
    if not in_write_roots(rel, role):
        hint = "route product work through a lead" if role["kind"] == "supervisor" else f"write roots: {', '.join(allowed) or 'none'}"
        return deny("write-root", f"{role['id']} may not write {rel} ({hint})")
    inner = code_rel(rel)
    if inner is not None:
        if role.get("team") == "qa" and not L.any_glob(inner, pol["test_globs"]):
            return deny("qa-tests-only", f"QA writes tests, never product code: {inner}")
        if role.get("team") == "dev" and L.any_glob(inner, pol["protected_from_dev"]):
            return deny("dev-acceptance-tests", f"dev may not edit {inner}: whoever writes the code never writes the tests that judge it. "
                        "File a test-flaw finding instead.")
    return ALLOW


# ---------------------------------------------------------------- bash

HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1([^\n]*)\n.*?\n[ \t]*\2[ \t]*(?=\n|$)", re.S)


def strip_heredocs(cmd: str) -> str:
    """Heredoc bodies are data (a commit message with an apostrophe is not an unbalanced quote).
    The rest of the opening line — `<<EOF | grep x` — is kept. Hard-deny patterns have already
    been matched against the full text, body included."""
    return HEREDOC.sub(lambda m: "<<HEREDOC" + m.group(3), cmd)


def tokenize(cmd: str) -> Optional[List[str]]:
    cmd = strip_heredocs(cmd)
    try:
        lx = shlex.shlex(cmd, posix=True, punctuation_chars=";&|<>()")
        lx.whitespace_split = True
        lx.commenters = ""
        return list(lx)
    except ValueError:
        return None


def segments(tokens: List[str]) -> List[List[str]]:
    segs, cur = [], []
    for t in tokens:
        if t in SEGMENT_OPS:
            if cur:
                segs.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        segs.append(cur)
    out = []
    for s in segs:
        while s and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", s[0]):
            s = s[1:]
        if s:
            out.append(s)
    return out


def base(tok: str) -> str:
    b = tok.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return b[:-4] if b.endswith(".exe") else b


def harness_script(seg: List[str]) -> Optional[Tuple[str, List[str]]]:
    """(script name, args) when the segment runs a .agentteam/bin script."""
    if not seg or not re.match(r"^(python[0-9.]*|py)$", base(seg[0])):
        return None
    for i, t in enumerate(seg[1:], 1):
        if t.startswith("-"):
            continue
        if t.endswith(".py") and ("agentteam" in t.replace("\\", "/") or base(t) in ALLOWED_HARNESS_SCRIPTS | {"guard.py", "usage_hook.py", "roster.py", "setup.py"}):
            return base(t), seg[i + 1:]
        return None
    return None


def current_branch(cwd: str) -> str:
    try:
        return L.git_out(["rev-parse", "--abbrev-ref", "HEAD"], Path(cwd or "."))
    except OSError:
        return ""


def active_deploy_approved() -> bool:
    sid = L.active().get("story")
    if not sid:
        return False
    st = L.read_json(L.story_dir(sid) / "state.json", {})
    return (st.get("human", {}).get("deploy-beyond-dev", {}) or {}).get("status") == "approved"


def check_bash(ev: Dict[str, Any], role: Dict[str, Any]) -> Decision:
    cmd = str((ev.get("tool_input") or {}).get("command") or "")
    norm = " ".join(cmd.split())
    pol = policy()
    prof = profile(role["bash_profile"])
    defaults = "(" + "|".join(map(re.escape, pol["default_branches"])) + ")"

    # 1. hard denies, every role
    for rule in pol["hard_deny_bash"]:
        if re.search(rule["re"].replace("{default}", defaults), norm, re.I):
            return deny(rule["id"], f"hard-denied ({rule['id']}): {norm[:120]}")

    toks = tokenize(cmd)
    if toks is None:
        return deny("unparseable", "the command could not be tokenised (unbalanced quotes); rewrite it plainly")
    segs = segments(toks)

    for seg in segs:
        b0 = base(seg[0])
        # 2. pushes/merges that only the current branch can judge
        if b0 == "git":
            sub = git_sub(seg)
            rest = [t for t in seg[seg.index(sub) + 1:]] if sub in seg else []
            if sub == "push" and not [t for t in rest if not t.startswith("-")][1:]:
                if current_branch(ev.get("cwd")) in pol["default_branches"]:
                    return deny("push-default-branch", "a bare `git push` from a default branch is a push to it")
            if sub == "merge" and current_branch(ev.get("cwd")) in pol["default_branches"]:
                return deny("merge-default-branch", "merging into a default branch is the pre-merge human gate's call")
        # 3. secrets named anywhere in the command
        for t in seg:
            if ("/" in t or "." in t) and is_sensitive(t.replace("\\", "/").lstrip("./") or t):
                return deny("sensitive-path", f"the command touches a secret-bearing path ({t})")
        # 4. harness scripts
        hs = harness_script(seg)
        if hs:
            script, args = hs
            if script == "story.py":
                d = check_story_sub(role, prof, args)
                if d[0] == "deny":
                    return d
            elif script == "project.py":
                if role["kind"] != "supervisor":
                    return deny("harness-script", "project.py (adding projects) is the main session's, not an agent's")
            elif script not in ALLOWED_HARNESS_SCRIPTS and not (role["kind"] == "supervisor" and maintainer_mode()):
                return deny("harness-script", f"{script} is not run by agents")

    # 5. writes aimed at harness or audit paths (best effort for code-executing profiles)
    if WRITE_VERBS.search(norm) and not (role["kind"] == "supervisor" and maintainer_mode()):
        if re.search(r"\.agentteam[/\\](bin|config|state[/\\]audit)|stories[/\\][^/\\\s]+[/\\](human|runs)[/\\]|state\.json|usage\.jsonl|findings\.jsonl|\.claude[/\\](settings|agents)", norm):
            return deny("harness-write", "the command would modify harness code, config or an audit record")

    # 6. deploys
    for pat in pol["deploy"]["patterns"]:
        m = re.search(pat, norm, re.I)
        if m:
            env = (m.groupdict().get("env") or "").lstrip("-").lower()
            if role.get("team") != "devops":
                return deny("deploy-not-devops", "deploys are run by the devops team only")
            if env not in pol["deploy"]["free_envs"] and not active_deploy_approved():
                return deny("deploy-beyond-dev", f"deploying to '{env}' needs the deploy-beyond-dev human gate approved on the active story")

    # 7. readonly profiles are allow-lists
    if "allow_commands" in prof:
        return check_readonly(cmd, segs, prof)
    return ALLOW


def git_sub(seg: List[str]) -> str:
    i = 1
    while i < len(seg):
        t = seg[i]
        if t in ("-C", "-c", "--git-dir", "--work-tree", "--namespace"):
            i += 2
            continue
        if t.startswith("-"):
            i += 1
            continue
        return t
    return ""


def check_story_sub(role: Dict[str, Any], prof: Dict[str, Any], args: List[str]) -> Decision:
    sub = next((a for a in args if not a.startswith("-")), "")
    if sub in policy()["story_subcommands_never_for_agents"]:
        return deny("human-only", f"`story.py {sub}` is run by a human in their own terminal, never by an agent")
    if sub and sub not in prof.get("story_subcommands", []):
        return deny("story-subcommand", f"{role['id']} ({role['bash_profile']}) may not run `story.py {sub}`")
    if sub in LEAD_ONLY_SUBCOMMANDS and role["kind"] == "ic":
        return deny("lead-only", f"`story.py {sub}` is recorded by your lead; return it to them instead")
    return ALLOW


def check_readonly(cmd: str, segs: List[List[str]], prof: Dict[str, Any]) -> Decision:
    if re.search(r"\$\(|`|<\(|>\(", cmd):
        return deny("readonly-substitution", "command substitution is not allowed in a readonly profile")
    stripped = re.sub(r"\d?>&\d|\d?>\s*/dev/null", "", cmd)
    if re.search(r">", stripped):
        return deny("readonly-redirect", "output redirection is a write; not allowed in a readonly profile")
    for seg in segs:
        line = " ".join(seg)
        b0 = base(seg[0])
        if any(re.search(p, line) for p in prof.get("allow_patterns", [])):
            continue
        if harness_script(seg):
            continue                                   # already judged by check_story_sub / script rule
        if b0 == "git":
            sub = git_sub(seg)
            if sub not in prof["git_subcommands"]:
                return deny("readonly-git", f"`git {sub}` is not a read-only git command")
            bad = [a for a in seg if a in prof["deny_args"].get(sub, [])]
            if bad:
                return deny("readonly-arg", f"`git {sub} {bad[0]}` writes")
            continue
        if b0 not in prof["allow_commands"]:
            return deny("readonly-command", f"`{b0}` is not on the readonly allow-list")
        bad = [a for a in seg[1:] for d in prof["deny_args"].get(b0, []) if a == d or (d.endswith("(") and d in a) or (" " in d and d in line)]
        if bad:
            return deny("readonly-arg", f"`{b0} {bad[0]}` writes or executes")
    return ALLOW


# ---------------------------------------------------------------- entry

def decide(ev: Dict[str, Any]) -> Decision:
    tool = ev.get("tool_name") or ""
    role = resolve_role(ev)
    if tool in READ_TOOLS or tool in WRITE_TOOLS:
        return check_path_tool(ev, role, tool)
    if tool == "Bash":
        return check_bash(ev, role)
    return ALLOW


def main() -> int:
    raw = ""
    try:
        raw = sys.stdin.read()
        ev = json.loads(raw)
        if not isinstance(ev, dict):
            raise ValueError("hook input is not an object")
        verdict, rule, reason = decide(ev)
        if verdict == "deny":
            role = resolve_role(ev)
            L.append_jsonl(L.audit_dir() / "guard-denials.jsonl", {
                "at": L.now(), "role": role["id"], "tool": ev.get("tool_name"), "rule": rule, "reason": reason})
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "permissionDecision": "deny",
                "permissionDecisionReason": f"[agent-team guard: {rule}] {reason}"}}))
        return 0
    except Exception as e:  # fail OPEN, but loudly: every gate reads this file
        try:
            L.append_jsonl(L.audit_dir() / "guard-errors.jsonl", {
                "at": L.now(), "error": f"{type(e).__name__}: {e}", "input": raw[:500],
                "trace": traceback.format_exc()[-1500:]})
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
