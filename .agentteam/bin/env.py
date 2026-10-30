#!/usr/bin/env python3
"""env.py — detect the toolchain, never assume it. Writes .agentteam/state/env-report.json.

Agents copy the argv for a suite from this report (story.py run does it for them) instead of
composing one from a tool's name, because a bare `pytest` or `python3` is often not on PATH —
on Windows `python3` may be a Store stub, and `npm` is `npm.cmd`. Every command here is an
absolute-path argv list.

Per project, `workspace/<p>/agentteam.json` overrides detection:
  {"suites": {"unit": {"argv": ["{python}", "-m", "pytest", "-q", "tests/unit"]},
              "perf": {"na": "no latency requirement in the PRD"}},
   "visual_url": "http://127.0.0.1:5173"}
`{python}` and `{node}` expand to the detected interpreters.
"""
from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib as L  # noqa: E402

SUITES = ["unit", "integration", "system", "regression", "perf", "visual", "smoke"]


def version(argv: List[str]) -> Optional[str]:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = (r.stdout or r.stderr).strip().splitlines()
    return out[0] if r.returncode == 0 and out else None


def tools() -> Dict[str, Any]:
    py = sys.executable
    node = shutil.which("node")
    npm = shutil.which("npm")
    gh = shutil.which("gh")
    t: Dict[str, Any] = {
        "python": {"argv0": py, "version": platform.python_version()},
        "pytest": version([py, "-m", "pytest", "--version"]),
        "git": version(["git", "--version"]),
        "node": {"argv0": node, "version": version([node, "--version"])} if node else None,
        "npm": {"argv0": npm} if npm else None,
        "gh": {"argv0": gh, "authenticated": bool(gh) and subprocess.run([gh, "auth", "status"], capture_output=True).returncode == 0} if gh else None,
    }
    t["playwright"] = bool(node) and subprocess.run(
        [node, "-e", "require.resolve('playwright')"], capture_output=True, cwd=str(L.root())).returncode == 0 if node else False
    return t


def detect_project(p: Path, t: Dict[str, Any]) -> Dict[str, Any]:
    py = t["python"]["argv0"]
    node = (t.get("node") or {}).get("argv0")
    npm = (t.get("npm") or {}).get("argv0")
    suites: Dict[str, Any] = {}
    kind = "unknown"
    if (p / "pyproject.toml").exists() or (p / "pytest.ini").exists() or (p / "setup.cfg").exists() or list(p.glob("tests/**/*.py")):
        kind = "python"
        runner = [py, "-m", "pytest", "-q"] if t["pytest"] else [py, "-m", "unittest", "discover", "-s", "tests", "-t", "."]
        has = lambda d: (p / "tests" / d).is_dir()  # noqa: E731
        suites["unit"] = {"argv": runner + (["tests/unit"] if has("unit") and t["pytest"] else [])}
        for s in ("integration", "system", "perf"):
            suites[s] = {"argv": runner + [f"tests/{s}"]} if has(s) and t["pytest"] else {"na": f"no tests/{s} directory"}
        if has("acceptance") and t["pytest"]:
            suites["system"] = {"argv": runner + ["tests/acceptance"]}
        suites["regression"] = {"argv": runner}
    elif (p / "package.json").exists() and npm:
        kind = "node"
        scripts = (L.read_json(p / "package.json", {}) or {}).get("scripts", {})
        suites["unit"] = {"argv": [npm, "test", "--silent"]} if "test" in scripts else {"na": "no npm test script"}
        for s in ("integration", "system", "perf"):
            key = next((k for k in (f"test:{s}", f"test:{s[:3]}") if k in scripts), None)
            suites[s] = {"argv": [npm, "run", key, "--silent"]} if key else {"na": f"no test:{s} script"}
        suites["regression"] = suites["unit"]
    elif (p / "go.mod").exists() and shutil.which("go"):
        kind = "go"
        go = shutil.which("go")
        suites["unit"] = {"argv": [go, "test", "./..."]}
        suites["regression"] = {"argv": [go, "test", "-count=1", "./..."]}
        for s in ("integration", "system", "perf"):
            suites[s] = {"na": "declare it in agentteam.json"}
    over = L.read_json(p / "agentteam.json", {})
    for name, s in (over.get("suites") or {}).items():
        if "argv" in s:
            s = {"argv": [str(a).replace("{python}", py).replace("{node}", node or "node") for a in s["argv"]]}
        suites[name] = s
    if over.get("visual_url") and node:
        suites["visual"] = {"argv": [node, str(L.BIN / "visual" / "verify_design_quality.js"), over["visual_url"]]}
    return {"path": p.relative_to(L.root()).as_posix(), "detected": kind, "suites": suites}


def build() -> Dict[str, Any]:
    t = tools()
    projects = {}
    if L.workspace().exists():
        for p in sorted(L.workspace().iterdir()):
            if p.is_dir() and not p.name.startswith(".") and (p / ".git").exists():
                projects[p.name] = detect_project(p, t)
    decisions = {
        "testRunner": {"choice": "pytest" if t["pytest"] else "unittest", "argv": [t["python"]["argv0"], "-m", "pytest" if t["pytest"] else "unittest"]},
        "visualDriver": {"choice": "playwright" if t["playwright"] else "none",
                         "note": "" if t["playwright"] else "npm i -D playwright && npx playwright install chromium — phase 6 cannot pass without it"},
        "prMode": {"choice": "gh" if (t.get("gh") or {}).get("authenticated") else "local",
                   "note": "gh authenticated" if (t.get("gh") or {}).get("authenticated") else "no authenticated gh: PRs are local branches"},
    }
    return {"generated": L.now(), "platform": f"{platform.system()} {platform.release()}", "tools": t,
            "decisions": decisions, "projects": projects}


def main() -> int:
    rep = build()
    L.write_json(L.state_dir() / "env-report.json", rep)
    print(f"env-report.json ({rep['platform']}, python {rep['tools']['python']['version']})")
    for k, d in rep["decisions"].items():
        print(f"  {k:<13} {d['choice']:<11} {d.get('note', '')}")
    for name, p in rep["projects"].items():
        print(f"  project {name} [{p['detected']}]")
        for s in SUITES:
            v = p["suites"].get(s)
            print(f"    {s:<12} " + (" ".join(v["argv"]) if v and "argv" in v else f"N/A — {v['na']}" if v else "(not declared)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
