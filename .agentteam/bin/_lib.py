"""Shared plumbing for the harness scripts. Stdlib only, Python 3.9+, any OS.

Two rules this file exists to enforce, both learned from v1:
  * every file is read and written as UTF-8 explicitly (v1 broke on a GBK locale), and
  * every external command is an argv list, never a shell string (v1 parsed Windows paths
    with POSIX shlex and refused every job on Windows).
"""
from __future__ import annotations

import datetime as _dt
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

BIN = Path(__file__).resolve().parent


def root() -> Path:
    """The harness root: $AGENTTEAM_ROOT (tests, selftest) or the repo holding this file."""
    env = os.environ.get("AGENTTEAM_ROOT")
    return Path(env).resolve() if env else BIN.parents[1]


def at() -> Path:
    return root() / ".agentteam"


def config_dir() -> Path:
    # Config is read from the harness root when it has one, else from the shipped copy, so a
    # temp root in a test can carry only the files it overrides.
    own = at() / "config"
    return own if own.is_dir() else BIN.parent / "config"


def load_config(name: str) -> Dict[str, Any]:
    return read_json(config_dir() / f"{name}.json")


def stories_dir() -> Path:
    return at() / "stories"


def story_dir(story_id: str) -> Path:
    return stories_dir() / story_id


def state_dir() -> Path:
    return at() / "state"


def audit_dir() -> Path:
    return state_dir() / "audit"


def workspace() -> Path:
    return root() / "workspace"


def worktree_dir(story_id: str) -> Path:
    return workspace() / ".worktrees" / story_id


# ---------------------------------------------------------------- io

def now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(s: str) -> _dt.datetime:
    return _dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)


def read_text(p: Path) -> str:
    return Path(p).read_text(encoding="utf-8")


def write_text(p: Path, text: str) -> None:
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    # Windows: a file git or an antivirus scanner just touched can refuse a replace for a few
    # milliseconds (WinError 5). Retry briefly instead of failing a gate on a transient lock.
    for attempt in range(20):
        try:
            os.replace(tmp, p)
            return
        except PermissionError:
            if attempt == 19:
                raise
            import time
            time.sleep(0.05 * (attempt + 1))


def read_json(p: Path, default: Any = None) -> Any:
    try:
        return json.loads(read_text(p))
    except FileNotFoundError:
        if default is not None:
            return default
        raise


def write_json(p: Path, data: Any) -> None:
    write_text(p, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def append_jsonl(p: Path, rec: Dict[str, Any]) -> None:
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def read_jsonl(p: Path) -> List[Dict[str, Any]]:
    try:
        lines = read_text(p).splitlines()
    except FileNotFoundError:
        return []
    out = []
    for ln in lines:
        ln = ln.strip()
        if ln:
            try:
                out.append(json.loads(ln))
            except ValueError:
                out.append({"_corrupt": ln})
    return out


def sha256_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- paths + globs

def rel_posix(p: Path, base: Optional[Path] = None) -> Optional[str]:
    """`p` relative to `base` (default: root) as a '/' path, or None when outside it.
    Case-folded comparison on Windows, where the filesystem is case-insensitive."""
    base = (base or root()).resolve()
    try:
        rp = Path(os.path.realpath(p))
    except (OSError, ValueError):
        return None
    try:
        r = rp.relative_to(base)
    except ValueError:
        if os.name == "nt":
            a, b = str(rp).lower(), str(base).lower().rstrip("\\/")
            if a == b or a.startswith(b + os.sep):
                return a[len(b):].lstrip("\\/").replace("\\", "/")
        return None
    return r.as_posix()


def glob_match(path: str, pattern: str) -> bool:
    """Match a '/' path against a glob where '**' spans directories and '*' does not.
    A leading '**/' also matches at the top level."""
    path = path.replace("\\", "/")
    if os.name == "nt":
        path, pattern = path.lower(), pattern.lower()
    return re.fullmatch(_glob_re(pattern), path) is not None


_GLOB_CACHE: Dict[str, str] = {}


def _glob_re(pattern: str) -> str:
    if pattern in _GLOB_CACHE:
        return _GLOB_CACHE[pattern]
    i, out = 0, []
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    _GLOB_CACHE[pattern] = "".join(out)
    return _GLOB_CACHE[pattern]


def any_glob(path: str, patterns: Iterable[str]) -> bool:
    return any(glob_match(path, g) for g in patterns)


# ---------------------------------------------------------------- git

def git(args: List[str], cwd: Path, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=check)


def git_out(args: List[str], cwd: Path) -> str:
    r = git(args, cwd)
    return r.stdout.strip() if r.returncode == 0 else ""


# ---------------------------------------------------------------- roster + pipeline

def roster_index() -> Dict[str, Dict[str, Any]]:
    """agent id -> {id, team, kind: lead|ic, bash_profile, write_roots, model, charter}."""
    r = load_config("roster")
    idx: Dict[str, Dict[str, Any]] = {}
    for t in r["teams"]:
        base = {"team": t["id"], "bash_profile": t["bash_profile"], "write_roots": t["write_roots"]}
        idx[t["lead"]["id"]] = {**base, "id": t["lead"]["id"], "kind": "lead",
                                "model": r["models"]["lead"], "charter": t["lead"]["charter"]}
        for ic in t["ics"]:
            idx[ic["id"]] = {**base, "id": ic["id"], "kind": "ic",
                             "model": r["models"]["ic"], "charter": ic["charter"]}
    return idx


def phases() -> List[Dict[str, Any]]:
    return load_config("pipeline")["phases"]


def phase_by_id(pid: str) -> Dict[str, Any]:
    for p in phases():
        if p["id"] == pid:
            return p
    raise KeyError(pid)


def phase_of_gate(gid: str) -> Dict[str, Any]:
    for p in phases():
        if gid in p["gates"]:
            return p
    raise KeyError(gid)


def human_gate_after(gid: str) -> Optional[Dict[str, Any]]:
    for h in load_config("pipeline")["human_gates"]:
        if h["after_gate"] == gid:
            return h
    return None


def active() -> Dict[str, Any]:
    return read_json(state_dir() / "active.json", {})


def die(msg: str, code: int = 1) -> None:
    print(msg, file=sys.stderr)
    sys.exit(code)
