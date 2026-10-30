#!/usr/bin/env python3
"""integrity.py — fix the code, never the assertion.

Diffs the test files of a checkout against the merge base of its branch and reports every
way a red suite gets turned green without fixing anything:

  test-file-deleted      a test file removed
  assertion-deleted      an assertion removed with no replacement in its hunk and not moved elsewhere in the file
  skip-added             skip / xfail / .only / xit / t.Skip / @Disabled markers added
  always-true            an assertion that cannot fail (assert True, expect(true).toBe(true), assert x == x)
  assertion-commented    an assertion commented out
  comparison-loosened    a strict comparison replaced by a looser one (== -> in / >= / approx / toContain ...)
  expected-value-changed the assertion keeps its shape and only a literal changes — the most common
                         way a red suite turns green
  snapshot-changed       a snapshot or golden file edited (snapshots are expected values too)

Detection is heuristic on purpose: it is a diff rule, so it is cheap, deterministic and
explainable. A legitimate change (the spec really changed the expected value) is waived by
a person with a written reason — and the waiver is reported by the gate, never absorbed.

  python integrity.py <checkout> <base-ref>     exit 1 on any violation
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _lib as L  # noqa: E402

ASSERT_RX = re.compile(r"\bassert\b|\bself\.assert\w*\s*\(|\bexpect\s*\(|\bassertThat\s*\(|\bt\.(?:Error|Fatal)f?\s*\(|"
                       r"\bassert_\w+!?\s*\(|\bassert\w*\s*\(|\.should\b|\brequire\.\w+\s*\(")
SKIP_RX = re.compile(r"@pytest\.mark\.(skip|skipif|xfail)\b|\bpytest\.(skip|xfail)\s*\(|@unittest\.(skip\w*|expectedFailure)\b|"
                     r"\bself\.skipTest\s*\(|\b(it|test|describe|context)\.(only|skip|todo)\s*\(|\b(xit|xdescribe|xtest|fit|fdescribe)\s*\(|"
                     r"\bt\.Skip(?:Now|f)?\s*\(|@(Disabled|Ignore)\b|#\[ignore\]")
ALWAYS_TRUE_RX = re.compile(r"\bassert\s+(True|1|not\s+(False|None|0))\s*(#.*)?$|\bself\.assertTrue\s*\(\s*(True|1)\s*\)|"
                            r"\bexpect\s*\(\s*(true|1)\s*\)\s*\.\s*(toBe|toEqual|toBeTruthy)\s*\(\s*(true|1)?\s*\)|"
                            r"\bassert\b.*\bor\s+True\b")
SELF_EQ_RX = re.compile(r"\bassert\s+([\w.\[\]'\"]+)\s*==\s*\1\s*$|\bassertEqual\s*\(\s*([\w.]+)\s*,\s*\2\s*\)|"
                        r"\bexpect\s*\(\s*([\w.]+)\s*\)\s*\.\s*to(?:Be|Equal)\s*\(\s*\3\s*\)")
COMMENTED_RX = re.compile(r"^\s*(#|//|/\*|\*)\s*(assert\b|self\.assert|expect\s*\(|assertThat|t\.(Error|Fatal)|assert_eq!)")
STRICT_RX = re.compile(r"===?|\bassertEqual\b|\bassertIs\b|\.toBe\s*\(|\.toEqual\s*\(|\.toStrictEqual\s*\(|\bassert_eq!|\bassertEquals\b")
LOOSE_RX = re.compile(r"\sin\s|\bassertIn\b|\bassertAlmostEqual\b|\bapprox\s*\(|\.toContain\s*\(|\.toBeTruthy\s*\(|\.toBeDefined\s*\(|"
                      r"\.toMatch\s*\(|>=|<=|\bassertTrue\b|\bassertIsNotNone\b|\.toBeGreaterThan|\.toBeLessThan|is\s+not\s+None|!=\s*None|\.toBeCloseTo\s*\(")
SNAPSHOT_GLOBS = ["**/__snapshots__/**", "**/*.snap", "**/golden/**", "**/*.golden", "**/testdata/**"]


def test_globs() -> List[str]:
    return L.load_config("policy")["test_globs"]


def mask(line: str) -> str:
    s = re.sub(r"(['\"`])(?:\\.|(?!\1).)*\1", "S", line)
    s = re.sub(r"\b\d+(?:\.\d+)?(?:e[-+]?\d+)?\b", "N", s)
    s = re.sub(r"\b(True|False|None|true|false|null|undefined|nil)\b", "K", s)
    return re.sub(r"\s+", "", s)


def subject(line: str) -> str:
    """What an assertion is ABOUT — the left side of the comparison, the expect() argument, or
    the first argument of assertX(a, b). Two assertions pair only when their subjects match,
    so an unrelated assertion added in the same hunk cannot stand in for a deleted one."""
    s = line.strip()
    m = re.match(r"assert\s+(.+?)\s*(===?|!==?|>=|<=|<|>|\bnot\s+in\b|\bin\b|\bis\b)", s)
    if not m:
        m = re.search(r"\bexpect\s*\((.*?)\)\s*\.", s) or re.search(r"\b(?:self\.)?assert\w*!?\s*\(\s*([^,]+?)\s*,", s)
    return re.sub(r"\s+", "", m.group(1)) if m else mask(s)


def is_assert(line: str) -> bool:
    return bool(ASSERT_RX.search(line)) and not COMMENTED_RX.match(line)


def hunks(diff: str) -> List[Dict[str, Any]]:
    """[{file, start, removed:[(n, text)], added:[(n, text)]}] from a -U0 diff."""
    out, cur, f, new_n = [], None, None, 0
    for ln in diff.splitlines():
        if ln.startswith("+++ "):
            f = ln[6:] if ln.startswith("+++ b/") else None
        elif ln.startswith("@@"):
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)", ln)
            new_n = int(m.group(1)) if m else 0
            cur = {"file": f, "start": new_n, "removed": [], "added": []}
            out.append(cur)
        elif cur is not None and ln.startswith("-") and not ln.startswith("---"):
            cur["removed"].append((new_n, ln[1:]))
        elif cur is not None and ln.startswith("+") and not ln.startswith("+++"):
            cur["added"].append((new_n, ln[1:]))
            new_n += 1
    return out


def check(checkout: Path, base: str) -> List[Dict[str, Any]]:
    mb = L.git_out(["merge-base", base, "HEAD"], checkout) or base
    status = L.git_out(["diff", "--name-status", "--no-renames", mb], checkout)
    globs = test_globs()
    vio: List[Dict[str, Any]] = []
    files = []
    for ln in status.splitlines():
        parts = ln.split("\t")
        if len(parts) < 2:
            continue
        st, path = parts[0], parts[-1]
        if not L.any_glob(path, globs + SNAPSHOT_GLOBS):
            continue
        if st.startswith("D"):
            vio.append({"rule": "test-file-deleted", "file": path, "line": 0, "detail": "test file removed"})
        elif L.any_glob(path, SNAPSHOT_GLOBS) and st.startswith("M"):
            vio.append({"rule": "snapshot-changed", "file": path, "line": 0, "detail": "snapshot / golden file edited"})
        elif st.startswith(("M", "A")):
            files.append(path)
    if not files:
        return vio
    diff = L.git(["diff", "-U0", "--no-renames", mb, "--", *files], checkout).stdout
    hs = hunks(diff)
    # every assertion text added anywhere in a file: a removed assertion found here was MOVED, not deleted
    added_text: Dict[str, set] = {}
    for h in hs:
        added_text.setdefault(h["file"], set()).update(t.strip() for _, t in h["added"] if is_assert(t))
    for h in hs:
        f = h["file"]
        if not f or L.any_glob(f, SNAPSHOT_GLOBS):
            continue
        for n, t in h["added"]:
            if SKIP_RX.search(t):
                vio.append({"rule": "skip-added", "file": f, "line": n, "detail": t.strip()})
            if ALWAYS_TRUE_RX.search(t) or SELF_EQ_RX.search(t):
                vio.append({"rule": "always-true", "file": f, "line": n, "detail": t.strip()})
            if COMMENTED_RX.match(t):
                vio.append({"rule": "assertion-commented", "file": f, "line": n, "detail": t.strip()})
        rem = [(n, t) for n, t in h["removed"] if is_assert(t)]
        add = [(n, t) for n, t in h["added"] if is_assert(t)]
        # pair each removed assertion with an added one ABOUT THE SAME SUBJECT in this hunk; an
        # unpaired removal that does not reappear elsewhere in the file is a deletion — an
        # unrelated assertion added nearby must not cancel it out
        pairs, free = [], list(add)
        for n, t in rem:
            mate = next((a for a in free if subject(a[1]) == subject(t)), None)
            if mate:
                free.remove(mate)
                pairs.append(((n, t), mate))
            elif t.strip() not in added_text.get(f, set()):
                vio.append({"rule": "assertion-deleted", "file": f, "line": n, "detail": t.strip()})
        for (rn, rt), (an, at) in pairs:
            if mask(rt) == mask(at) and rt.strip() != at.strip():
                vio.append({"rule": "expected-value-changed", "file": f, "line": an,
                            "detail": f"{rt.strip()}  ->  {at.strip()}"})
            elif STRICT_RX.search(rt) and LOOSE_RX.search(at) and not LOOSE_RX.search(rt):
                vio.append({"rule": "comparison-loosened", "file": f, "line": an,
                            "detail": f"{rt.strip()}  ->  {at.strip()}"})
    return vio


def apply_waivers(vio: List[Dict[str, Any]], waivers: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    open_v, waived = [], []
    for v in vio:
        w = next((w for w in waivers if w.get("rule") == v["rule"] and L.glob_match(v["file"], w.get("file", ""))
                  and str(w.get("reason", "")).strip() and str(w.get("approver", "")).strip()), None)
        (waived if w else open_v).append({**v, "waiver": w} if w else v)
    return open_v, waived


def main(argv: List[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    vio = check(Path(argv[0]), argv[1])
    for v in vio:
        print(f"{v['rule']:<24} {v['file']}:{v['line']}  {v['detail']}")
    print(f"{len(vio)} violation(s)")
    return 1 if vio else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
