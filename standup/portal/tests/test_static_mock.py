"""Judges over static/app.js's embedded MOCK — the fixture the page renders off disk.

WHY THIS FILE EXISTS. The mock is not placeholder data; it is a RENDERING FIXTURE, and
app.js says so in a comment recording a real incident: the staff section once broke on
REAL data because the mock carried SHORT role strings while the live API emits the long
roster strings from team.json, so the layout break was invisible until a user hit it. The
mock was then deliberately loaded with long, descriptive multi-line strings.

That was a promise with no mechanism. Nothing stopped a later "tidy up the sample data"
pass from shortening those strings and silently reopening the same bug — and the class of
failure this repo keeps recording is exactly that: an apparatus pointed the wrong way
never errors, it just quietly stops catching things. So the length distribution is pinned
to a floor here, and two other one-line regressions that had already happened are pinned
next to it.

Every checker takes SOURCE TEXT rather than reading the file itself, so each one is also
run against a deliberately broken copy that it must reject (`E-03`: a judge that has not
been shown to fail is not a judge). Those are the `*_rejects_*` tests below.
"""

import re
from pathlib import Path

import pytest

APP_JS = Path(__file__).resolve().parents[1] / "static" / "app.js"

# --------------------------------------------------------------------------------------- #
# The floor.
#
# Measured on the shipped app.js at main@d596450 — i.e. the distribution that ALREADY
# existed before this file was written, not one tuned to fit a diff. It is a ratchet: the
# mock may get longer, never shorter. If you deliberately restructure the mock and a number
# below no longer holds, re-derive it and say in the commit why the fixture is still able to
# expose a long-string layout break — do not just lower it, that is the cleanup this guards
# against.
#
# Four metrics rather than one, because any single number is easy to satisfy by accident:
# a count of long strings, a count of very long ones, the longest single string (the one
# that actually wraps), and the total, so trading one long string for several short ones
# does not read as "unchanged".
# --------------------------------------------------------------------------------------- #
FLOOR_GE40 = 15      # literals at least 40 chars
FLOOR_GE80 = 3       # literals at least 80 chars — the multi-line wrap band
FLOOR_LONGEST = 103  # the single longest literal
FLOOR_TOTAL = 2091   # total characters across every literal in the mock


def _read() -> str:
    return APP_JS.read_text(encoding="utf-8")


def _js_string_literals(text: str):
    """Every JS string literal in ``text``, comments skipped.

    Deliberately a small scanner rather than a regex: the mock region carries `//` comments,
    apostrophes inside comments, and `http://` inside strings, and a regex gets at least one
    of those wrong. Comments are recognised BEFORE quotes, so a `//` inside a string is read
    as string content and an apostrophe inside a comment never opens one.
    """
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            i = n if j < 0 else j + 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c in "\"'`":
            quote, j, buf = c, i + 1, []
            while j < n:
                d = text[j]
                if d == "\\":
                    buf.append(text[j:j + 2])
                    j += 2
                    continue
                if d == quote:
                    break
                buf.append(d)
                j += 1
            out.append("".join(buf))
            i = j + 1
            continue
        i += 1
    return out


def _mock_region(src: str) -> str:
    """The embedded MOCK_STATUS + MOCK_TEAM block, and nothing else.

    Bounded so the floor measures the FIXTURE, not the whole file: app.js's own UI copy is
    long too, and letting it count would make the floor satisfiable while the mock itself
    was gutted.
    """
    try:
        start = src.index("const MOCK_STATUS = {")
        end = src.index("// ---- in-memory state")
    except ValueError as exc:                                   # pragma: no cover - guard
        raise AssertionError(
            "cannot locate the embedded mock block in app.js (%s). The judge is broken, not "
            "the page — repair the markers rather than deleting the check." % exc)
    assert end > start, "the mock block markers are out of order in app.js"
    return src[start:end]


def _distribution(src: str) -> dict:
    lits = _js_string_literals(_mock_region(src))
    return {
        "count": len(lits),
        "ge40": sum(1 for v in lits if len(v) >= 40),
        "ge80": sum(1 for v in lits if len(v) >= 80),
        "longest": max((len(v) for v in lits), default=0),
        "total": sum(len(v) for v in lits),
    }


def _floor_violations(src: str):
    d = _distribution(src)
    checks = (("ge40", FLOOR_GE40, "literals of at least 40 chars"),
              ("ge80", FLOOR_GE80, "literals of at least 80 chars"),
              ("longest", FLOOR_LONGEST, "longest single literal"),
              ("total", FLOOR_TOTAL, "total characters across the mock's literals"))
    return ["%s: %d, floor is %d (%s)" % (key, d[key], floor, label)
            for key, floor, label in checks if d[key] < floor]


# --------------------------------------------------------------------------------------- #
# the length floor
# --------------------------------------------------------------------------------------- #
def test_mock_still_carries_long_strings():
    violations = _floor_violations(_read())
    assert not violations, (
        "static/app.js's embedded mock has been SHORTENED below the distribution that made it "
        "able to expose a long-string layout break:\n  " + "\n  ".join(violations) +
        "\nThe mock is a rendering fixture, not sample copy — see the comment above MOCK_STATUS.")


def test_floor_rejects_a_shortened_mock():
    """E-03: prove the floor catches the exact cleanup it exists to stop.

    Every long literal in the mock is replaced with a short one — a plausible "make the
    sample data tidier" edit — and the checker must reject the result. A floor nobody has
    watched fail is just a number in a file.
    """
    src = _read()
    region = _mock_region(src)
    shortened = region
    for lit in sorted(set(_js_string_literals(region)), key=len, reverse=True):
        if len(lit) >= 40:
            shortened = shortened.replace(lit, lit[:20])
    mutated = src.replace(region, shortened)
    assert mutated != src, "the mutation changed nothing — the scanner found no long literals"
    violations = _floor_violations(mutated)
    assert violations, "a mock with every long string truncated to 20 chars passed the floor"
    assert any(v.startswith("longest") for v in violations)
    assert any(v.startswith("ge80") for v in violations)


def test_floor_reads_the_mock_not_the_whole_file():
    """The region bound is load-bearing: app.js's own UI copy must not prop the floor up."""
    src = _read()
    region = _mock_region(src)
    assert len(region) < len(src) / 4, "the mock region bound is far too wide to be the mock"
    assert "MOCK_STATUS" in region and "awaiting_kain" in region
    assert "function pollStatus" not in region


# --------------------------------------------------------------------------------------- #
# the first-paint backstop must never pre-empt a slow-but-alive backend
# --------------------------------------------------------------------------------------- #
def _timer_violations(src: str):
    bad = []
    m = re.search(r"const\s+FIRST_PAINT_FALLBACK_MS\s*=\s*([^;]+);", src)
    if not m:
        bad.append("FIRST_PAINT_FALLBACK_MS is not defined")
    elif "REQUEST_TIMEOUT_MS" not in m.group(1):
        bad.append("FIRST_PAINT_FALLBACK_MS is %r — a bare number can silently be shorter than "
                   "the request timeout; derive it from REQUEST_TIMEOUT_MS" % m.group(1).strip())
    if not re.search(r"const\s+REQUEST_TIMEOUT_MS\s*=\s*\d+\s*;", src):
        bad.append("REQUEST_TIMEOUT_MS is not defined as a plain number")
    if not re.search(r"ctrl\.abort\(\)\s*,\s*REQUEST_TIMEOUT_MS\s*\)", src):
        bad.append("getJSON's abort no longer uses REQUEST_TIMEOUT_MS, so the constant and the "
                   "real timeout can drift apart")
    boot = re.search(r"bootstrapMockIfNeeded\(\);\s*\}\s*,\s*([A-Za-z_0-9 +*]+)\)\s*;", src)
    if not boot:
        bad.append("cannot find the first-paint backstop setTimeout")
    elif "FIRST_PAINT_FALLBACK_MS" not in boot.group(1):
        bad.append("the first-paint backstop fires after %r instead of FIRST_PAINT_FALLBACK_MS — "
                   "a delay shorter than the request timeout flashes sample data at a user whose "
                   "backend is merely slow" % boot.group(1).strip())
    return bad


def test_mock_fallback_cannot_pre_empt_a_slow_backend():
    violations = _timer_violations(_read())
    assert not violations, "\n  ".join(["first-paint backstop:"] + violations)


def test_timer_check_rejects_a_hardcoded_delay():
    """E-03: restore the flat 1200ms and the check must go red."""
    src = _read()
    mutated = re.sub(r"(bootstrapMockIfNeeded\(\); \}, )FIRST_PAINT_FALLBACK_MS\)",
                     r"\g<1>1200)", src)
    assert mutated != src, "the mutation did not apply — the backstop call shape changed"
    bad = _timer_violations(mutated)
    assert any("1200" in b for b in bad), "a hardcoded 1200ms backstop passed the check"


# --------------------------------------------------------------------------------------- #
# names the mock must not carry
# --------------------------------------------------------------------------------------- #
def _stale_name_violations(src: str):
    region = _mock_region(src)
    bad = []
    # The bundled demo-app and its squad were deleted in 0.5.0. The mock kept naming them, so
    # the one screen a user sees before the backend answers pointed at a squad that does not
    # exist. Neutral placeholders replaced them, at IDENTICAL length so the floor above is
    # untouched — that pairing is the point: a rename must not become a shortening.
    for dead in ("demo_squad", "Demo Dev Squad", "demo-app", "dev_a", "dev_b"):
        if dead in region:
            bad.append("the mock still names %r — that squad was removed in 0.5.0" % dead)
    # Staff are LENSES, not paired developers. The roster's design_lead claimed to pair with
    # portal_frontend, an agent no shipped roster contains; that was corrected in the roster
    # and the mock kept the second copy of the same sentence.
    for note in re.findall(r"note:\s*\"([^\"]*)\"", region):
        if "pairs with" in note:
            bad.append("a staff note claims a pairing (%r) — staff are review lenses and have "
                       "no pair" % note[:60])
    return bad


def test_mock_carries_no_deleted_squad_or_pairing_claim():
    assert not _stale_name_violations(_read())


@pytest.mark.parametrize("planted", [
    ('{ id: "your_squad"', '{ id: "demo_squad"'),
    ('note: "runs a light design read', 'note: "pairs with portal_frontend; a light design read'),
])
def test_stale_name_check_rejects_each_regression(planted):
    """E-03, once per regression: each defect must redden the check on its own."""
    src = _read()
    mutated = src.replace(planted[0], planted[1], 1)
    assert mutated != src, "the mutation did not apply — %r is no longer in app.js" % planted[0]
    assert _stale_name_violations(mutated), "%r passed the stale-name check" % planted[1]
