#!/usr/bin/env bash
# Release-invariants judge — what SHIPS in this repo must be consistent with itself.
#
#     bash standup/control/tests/test_release_invariants.sh
#     bash standup/control/tests/test_release_invariants.sh --self-test
#
# WHY THIS EXISTS. v0.5.0 shipped two defects that no existing judge could see, because every one of
# them judges BEHAVIOUR and these are about the CONTENT of the release:
#
#   A. The README told people to run `/add-project adopt standup/portal`. `/add-project`'s own Step
#      1a refuses a name containing `/`, and refuses management territory — so the one command the
#      docs gave for the one product the plugin ships was a command that cannot run. A documented
#      command nobody executes in CI is prose, and prose does not fail.
#
#      The first version of this judge matched only `/add-project`, and a SECOND dead command
#      survived it in the same README: `/add-team <id> — <mission>`, a pre-0.5.1 signature carrying
#      no `--kind`, three tables below a `/add-project` row that does carry its flags. It was not
#      missed by the judge, it was invisible to it BY CONSTRUCTION — which is this project's
#      recurring shape, and the reason the fix was to widen the judge first and edit the README
#      second. Every argument for letting that row through (it is a signature, not a recipe; the
#      real recipe is elsewhere and verified; the command self-corrects when called) was equally
#      true of `/add-project adopt standup/portal`, and that one shipped.
#
#   C. "the released roster ships no project squad" was asserted by a UNIT TEST against the LIVE
#      roster (`portal/tests/test_parsers.py`, `assert t["squads"] == []`). That made the factory
#      test suite go RED for anyone who followed the README and ran `/add-team portal` + `/add-role`
#      twice — the documented way to get a squad for the one product this repo ships. A release
#      whose own docs redden its own suite is a release-content defect, and it is the same shape as
#      A and B: nothing in the pipeline was looking at what the RELEASE says versus what it does.
#      The fact is real and stays guarded; it just belongs here, judged against what the repo
#      DISTRIBUTES rather than against whatever roster is on a user's disk.
#
#   B. `.claude/agents/portal_backend.md` and `portal_frontend.md` were tracked while the roster had
#      no squad owning them. Running `/sync-roster` on the released tag PRUNES both — which is the
#      proof that `/sync-roster` was never run before the release. "Remember to run /sync-roster
#      before shipping" is a step that exists only as a sentence someone has to remember, and this
#      repo has already recorded what happens to those.
#
#      B then made C's mistake in the opposite direction: it read the roster and the definitions off
#      DISK, so a user standing between README step 3 (`/add-team` + `/add-role`) and step 4
#      (`/sync-roster`) got two MISSING lines and EXIT=1 for following the documentation in order.
#      It now reads the committed blobs on both sides. See the block above `scan_agents`.
#
# Every one of these is a release-time invariant: cheap to check, invisible to every other gate, and
# the kind of thing that is only ever noticed by a user.
#
# SCOPE, stated rather than assumed. Group A reads instructional documents only. CHANGELOG.md is
# deliberately EXCLUDED: it is a historical record, and a record that may not quote a command which
# was wrong at the time is not a record. Nothing in it is an instruction.
#
# Read-only against the tree. `--self-test` works on a staged COPY (STANDUP_RELEASE_ROOT), never the
# real repo.
#
# Exit codes: 0 pass · 1 failures · 3 the judge itself is broken · 64 usage
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Where the JUDGING TOOLS live. Normally derived from this script's own location; overridable so
# --self-test can run a deliberately PRE-FIX copy of this file out of a temp dir and still reach
# verify_project.py + portal/parsers. Only the self-test sets it.
REPO="${STANDUP_RELEASE_REPO:-$(cd "$HERE/../../.." && pwd)}"
# The tree being JUDGED. The tools doing the judging always come from $REPO — mutating the data is
# how --self-test proves the cases have teeth; mutating the tools is what test_add_project.sh does.
ROOT="${STANDUP_RELEASE_ROOT:-$REPO}"
VERIFY="$REPO/standup/control/verify_project.py"
PORTAL="$REPO/standup/portal"

fails=0
nas=0      # cases that could not be ANSWERED. Not failures, and emphatically not passes.
check() { # name ok detail
  local name="$1" ok="$2" detail="${3:-}"
  [[ "$ok" == "1" ]] || fails=$((fails + 1))
  printf '  %s → %s%s\n' "$name" "$([[ "$ok" == 1 ]] && echo PASS || echo FAIL)" "${detail:+  $detail}"
}
section() { printf '\n%s\n' "$1"; }
die_judge() { printf '\n!! JUDGE BROKEN — %s\n' "$1" >&2; exit 3; }

[[ -f "$VERIFY" ]] || die_judge "verify_project.py not found at $VERIFY"
[[ -f "$PORTAL/parsers/agents_gen.py" ]] || die_judge "agents_gen.py not found under $PORTAL"

# --------------------------------------------------------------------------------------------
# A. no instructional document prints an /add-project or /add-team invocation that the command
#    itself refuses, or that produces something the engine then refuses to run.
#
# The name rules are IMPORTED from verify_project.py, never restated here. That file is the
# executable form of Step 1a, so there is exactly one implementation and this judge cannot drift
# away from the checker the way the checker had drifted away from the prose.
# --------------------------------------------------------------------------------------------
scan_docs() { # -> prints "file:line: name — why" for each bad literal invocation
  ROOT="$ROOT" VERIFY="$VERIFY" python3 <<'PY'
import importlib.util, os, re, sys

root = os.environ["ROOT"]
spec = importlib.util.spec_from_file_location("vp", os.environ["VERIFY"])
vp = importlib.util.module_from_spec(spec); spec.loader.exec_module(vp)

owned = vp.em_owned_names(root)
if owned is None:
    print("!!JUDGE hooks/supervisor_gate.py under %s could not be parsed, so the deny list is "
          "unknown and this check cannot run" % root)
    raise SystemExit(0)
owned_lc = {o.lower() for o in owned}

DOCS = ["README.md", "CLAUDE.md", "ARCHITECTURE.md", "SECURITY.md"]
for base, _dirs, files in os.walk(os.path.join(root, "skills")):
    DOCS += [os.path.relpath(os.path.join(base, f), root) for f in files if f == "SKILL.md"]
cmds = os.path.join(root, ".claude", "commands")
if os.path.isdir(cmds):
    DOCS += [os.path.join(".claude", "commands", f) for f in sorted(os.listdir(cmds))
             if f.endswith(".md")]

# A token is a PLACEHOLDER, not a name, if it carries any of the metacharacters this repo's docs
# use for one. Judging those would make every usage block a violation, which is how a lint gets
# switched off. `-` leads a flag; `#` starts a trailing comment.
PLACEHOLDER = set("<>[]{}|…\"'`")
MODES = ("clone", "new", "adopt")
# BOTH squad-creating commands. The first version of this judge matched only /add-project — and
# the SECOND dead command in the same README (`/add-team <id> — <mission>`, a pre-0.5.1 signature
# with no --kind) was therefore invisible BY CONSTRUCTION. Half-covered is the shape this repo has
# already been caught by: the half that was missed is the half that shipped.
INVOKE = re.compile(r"/(?:agent-team:)?add-(project|team)\s+([^\n`]*)")

bad = []
for rel in DOCS:
    path = os.path.join(root, rel)
    if not os.path.isfile(path):
        continue
    in_fence = False
    for lineno, line in enumerate(open(path, encoding="utf-8"), 1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        for m in INVOKE.finditer(line):
            # Judge only occurrences written AS A COMMAND — inside a fenced block, or opening an
            # inline code span. A bare prose mention ("run /add-team then /add-role") is not an
            # invocation, and reading its trailing words as arguments is how a lint starts crying
            # wolf. A checker that cries wolf is worse than no checker; this project threw one
            # away for exactly that once already.
            if not (in_fence or (m.start() > 0 and line[m.start() - 1] == "`")):
                continue
            cmd, toks = m.group(1), m.group(2).split()
            if not toks:
                continue

            if cmd == "team":
                # `--kind` is not optional and not decoration: the engine STOPS a run on a squad
                # that declares no review_surface, so a signature printed without it teaches a
                # call that produces a squad which cannot run.
                if not any(t.startswith("--kind") for t in toks):
                    bad.append("%s:%d: /add-team %s — no --kind. The engine refuses to run a squad "
                               "that declares no review_surface, so this signature documents a call "
                               "that cannot produce a runnable squad."
                               % (rel, lineno, " ".join(toks[:3])))
                sid = toks[0]
                if not (sid[0] in "-#" or (set(sid) & PLACEHOLDER)):
                    why = vp.illegal_project_name(sid)
                    if why:
                        bad.append("%s:%d: %s — %s (a squad id becomes an agent-type name and a "
                                   "filename under .claude/agents/)" % (rel, lineno, sid, why))
                continue

            if toks[0] in MODES:
                mode, rest = toks[0], toks[1:]
            else:
                mode, rest = "clone", toks       # the bare `/add-project <git-url> [name]` form
            # for `clone` the first token is the source; the name, if any, is the one after it
            cand = (rest[1] if len(rest) > 1 else None) if mode == "clone" else (rest[0] if rest else None)
            if cand is None or cand[0] in "-#" or (set(cand) & PLACEHOLDER):
                continue
            why = vp.illegal_project_name(cand)
            if why:
                bad.append("%s:%d: %s — %s" % (rel, lineno, cand, why))
            elif vp.management_head(cand).lower() in owned_lc:
                bad.append("%s:%d: %s — %r is management territory; /add-project refuses it"
                           % (rel, lineno, cand, vp.management_head(cand)))
for b in bad:
    print(b)
PY
}

# --------------------------------------------------------------------------------------------
# B. the teammate definitions this repo SHIPS are what /sync-roster generates from the roster it
#    SHIPS. Both sides read from the committed blobs (`git show HEAD:…`), never the working tree.
#
# That distinction is the case, not a detail — the same one C was moved here for, and B had it
# wrong in the opposite direction for just as long. README step 3 is `/add-team portal` +
# `/add-role` twice; step 4 is `/sync-roster`. A user standing between them has two roles in their
# roster and no definitions yet, and B answered:
#
#     MISSING portal_backend.md — the roster has this role and no definition ships for it
#     MISSING portal_frontend.md                                                  EXIT=1
#
# Following the documented sequence turned the shipped judge red mid-sequence, and running step 4
# turned it green again — so the judge was reporting on the reader's PROGRESS THROUGH THE README,
# not on the release. (Pre-existing on main; not introduced by the batch that added C.)
#
# The invariant B was written for is untouched and still true: `.claude/agents/portal_backend.md`
# and `portal_frontend.md` were TRACKED while the shipped roster had no squad owning them, which is
# the proof `/sync-roster` was never run before v0.5.0. "Tracked" was always the operative word;
# reading the directory off disk was the mistake.
#
# BOTH halves are cases, because a check that only goes red is half a check: committed drift must
# FAIL, and a user's uncommitted customisation must PASS.
#
# Scoped to files carrying the generated header, because a HAND-WRITTEN agent definition is
# explicitly allowed to survive a regen (agents_gen prunes on that header, and a portal test asserts
# it). Judging every *.md would make this judge contradict the behaviour it is judging.
# --------------------------------------------------------------------------------------------
scan_agents() { # -> prints "EXTRA <name>" / "MISSING <name>" / "STALE <name>"
  ROOT="$ROOT" PORTAL="$PORTAL" python3 <<'PY'
import os, subprocess, sys, tempfile, shutil
from pathlib import Path

root = os.environ["ROOT"]
sys.path.insert(0, os.environ["PORTAL"])
from parsers import agents_gen                                     # noqa: E402

AGENTS_DIR = ".claude/agents"


def git(*args):
    """Raw stdout bytes of a git command, or None if it failed / git is unusable."""
    try:
        p = subprocess.run(["git", "-C", root, *args], capture_output=True)
    except OSError:
        return None
    return p.stdout if p.returncode == 0 else None


roster = git("show", "HEAD:standup/team.json")
if roster is None:
    print("!!NA %s has no committed standup/team.json (not a git checkout?) — this case judges the "
          "definitions the repo SHIPS against the roster it SHIPS, and only a checkout can answer "
          "that" % root)
    raise SystemExit(0)

listing = git("ls-tree", "-r", "--name-only", "-z", "HEAD", "--", AGENTS_DIR)
if listing is None:
    print("!!NA git could not list %s at HEAD under %s" % (AGENTS_DIR, root))
    raise SystemExit(0)

tmp = Path(tempfile.mkdtemp())
try:
    committed_roster = tmp / "team.json"
    committed_roster.write_bytes(roster)
    out = tmp / "agents"
    agents_gen.generate(committed_roster, out)                     # NEVER the real directory
    gen = {p.name: p.read_bytes() for p in out.glob("*.md")}

    shipped = {}
    for rel in listing.decode("utf-8", "replace").split("\0"):
        if not rel.endswith(".md"):
            continue
        body = git("show", "HEAD:" + rel)
        if body is None:
            continue
        if agents_gen._HEADER.encode("utf-8") in body:             # generated, not hand-written
            shipped[os.path.basename(rel)] = body

    for name in sorted(set(shipped) - set(gen)):
        print("EXTRA %s — this repo SHIPS a generated definition for a role the SHIPPED roster no "
              "longer has; /sync-roster prunes it, so shipping it is the proof it was not run"
              % name)
    for name in sorted(set(gen) - set(shipped)):
        print("MISSING %s — the SHIPPED roster declares this role and no definition ships for it, "
              "so /team cannot spawn it out of the box" % name)
    for name in sorted(set(gen) & set(shipped)):
        if gen[name] != shipped[name]:
            print("STALE %s — the shipped definition differs from what the shipped roster generates"
                  % name)
finally:
    shutil.rmtree(tmp, ignore_errors=True)
PY
}

# --------------------------------------------------------------------------------------------
# C. the RELEASED roster ships no project squad.
#
# Read from `git show HEAD:standup/team.json`, NOT from the working tree, and that is the whole
# design of this case. `/add-team` and `/add-project` edit the working tree — that is a user
# customising their install, not this repo changing what it distributes. A check that read the file
# on disk would go red for every user who followed the README, which is precisely the defect being
# fixed; relocating it into this judge unchanged would just move the red from one file to another.
#
# When there is no committed blob to read (a tarball/marketplace install with no .git) the case
# reports N/A **out loud** and counts as neither pass nor fail. That is a real hole — this repo has
# recorded what happens to checks that skip quietly — so it is mitigated rather than waved away: CI
# runs this judge on a git checkout, so the branch that matters is exercised on every push and PR,
# and --self-test drives the real branch too.
# --------------------------------------------------------------------------------------------
scan_shipped_roster() { # -> prints one line per project squad in the RELEASED roster
  ROOT="$ROOT" python3 <<'PY'
import json, os, subprocess

root = os.environ["ROOT"]
REL = "standup/team.json"
try:
    blob = subprocess.run(["git", "-C", root, "show", "HEAD:" + REL],
                          capture_output=True, text=True)
except OSError as exc:
    print("!!NA git is not runnable here (%s), so the released roster cannot be read" % exc)
    raise SystemExit(0)
if blob.returncode != 0:
    print("!!NA %s has no committed %s (not a git checkout?) — this case judges what the repo "
          "DISTRIBUTES, and only a checkout can answer that" % (root, REL))
    raise SystemExit(0)

try:
    data = json.loads(blob.stdout)
except json.JSONDecodeError as exc:
    print("the committed %s is not valid JSON (%s) — the release ships a roster nothing can "
          "load" % (REL, exc))
    raise SystemExit(0)

for t in (data.get("teams") or []):
    devs = [d.get("id") for d in (t.get("developers") or [])]
    print("%s — the COMMITTED roster declares this project squad. The release ships `teams: []` on "
          "purpose: a fresh install has nothing to work on until /add-project creates it, and "
          "/standup stops with that instruction rather than polling an empty board. A squad "
          "committed here ships pointed at a repo the user does not have (devs: %s)."
          % (t.get("id"), ", ".join(str(d) for d in devs) or "none"))
PY
}

# --------------------------------------------------------------------------------------------
# D. the off-disk mock in static/app.js carries every staff role the RELEASED roster declares.
#
# `static/app.js` embeds a MOCK_STATUS the page renders when no fetch has ever succeeded. It is a
# RENDERING FIXTURE, not sample copy — app.js's own comment records why: the staff section once
# broke on REAL data because the mock carried SHORT role strings while the live API emits the long
# roster strings from team.json, and the break was invisible until a user hit it.
# `portal/tests/test_static_mock.py` pins the resulting length distribution to a floor.
#
# A floor cannot see an ABSENCE. The mock shipped three staff while the roster declared four:
# `product_qa` was missing, and its note — 702 chars, 3.5x the mock's next-longest string — is the
# longest string the live payload can produce. So the fixture whose entire purpose is "a short mock
# can never again hide a long-string layout break" did not contain the longest string it was
# standing in for, and every length metric read healthy the whole time. The floor was pointed at
# the strings that were there; nothing was pointed at the one that was not.
#
# Set EQUALITY, not a superset either way: a roster role missing from the mock is the bug above, and
# a mock role the roster does not declare is a card for an agent that does not exist. INACTIVE staff
# count (comms_triage ships `active:false`): `active` is a per-install toggle, and the mock must
# carry comms_triage regardless because `renderCommsStreams()` only runs inside its card — keying
# the fixture on a flag would delete the comms rendering path the moment someone flipped it.
#
# Judged against the COMMITTED blobs, like C and B, and for C's exact reason: `/add-role --staff`
# adds to `staff[]`, so a user who has run it has a roster the shipped mock legitimately does not
# match. Asserting this in the pytest suite against the live team.json would redden the factory
# suite for that user — cf8ae07's defect, in a new file.
# --------------------------------------------------------------------------------------------
scan_mock_staff() { # -> prints one line per staff id that is in one side and not the other
  ROOT="$ROOT" python3 <<'PY'
import json, os, re, subprocess

root = os.environ["ROOT"]
ROSTER, APPJS = "standup/team.json", "standup/portal/static/app.js"


def blob(rel):
    """The COMMITTED content of `rel`, or (None, why-not)."""
    try:
        p = subprocess.run(["git", "-C", root, "show", "HEAD:" + rel],
                           capture_output=True, text=True)
    except OSError as exc:
        return None, "git is not runnable here (%s), so the released files cannot be read" % exc
    if p.returncode != 0:
        return None, ("%s has no committed %s (not a git checkout?) — this case judges what the "
                      "repo DISTRIBUTES, and only a checkout can answer that" % (root, rel))
    return p.stdout, None


def masked(text):
    """`text` with comment bodies and string CONTENTS blanked, character for character.

    The result is the SAME LENGTH as the input, so an offset found in it indexes the original
    unchanged — which is the whole point: the anchor and the bracket walk below need to see
    code only, while `mock_staff_ids` still needs the real string contents to read the ids.

    Both of those used to run on the raw region. The anchor `staff:\\s*\\[` therefore matched
    inside COMMENTS — ordinary prose, in a file whose comments exist to explain the mock —
    and the first match wins, so the judge walked a bracket pair in a sentence and reported
    four MISSING staff. It went red, which is the right direction, but it named the wrong
    file: the mock was intact and a comment was the defect. The bracket walk has the identical
    exposure one layer down, where a lone `[` in a comment or a `note` string skews the depth.
    """
    out, i, n = list(text), 0, len(text)
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            j = n if j < 0 else j
            out[i:j] = " " * (j - i)
            i = j
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out[i:j] = [" " if ch != "\n" else "\n" for ch in text[i:j]]
            i = j
            continue
        if c in "\"'`":
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2; continue
                if text[j] == c:
                    break
                j += 1
            end_of_body = min(j, n)
            out[i + 1:end_of_body] = [" " if ch != "\n" else "\n"
                                      for ch in text[i + 1:end_of_body]]
            i = end_of_body + 1
            continue
        i += 1
    return "".join(out)


def mock_staff_ids(block):
    """Every `id:` value in `block`, skipping comments and string contents.

    A bare regex is wrong here on purpose: the block's `note` strings are long English prose,
    and one containing the characters `id: "x"` would be read as a fourth staff member. Scanning
    with string/comment awareness also means a reordered entry (`{ role: ..., id: ... }`) still
    resolves, which a `\\{\\s*id:` anchor would silently miss and report as MISSING.
    """
    ids, i, n = [], 0, len(block)
    while i < n:
        c = block[i]
        if c == "/" and i + 1 < n and block[i + 1] == "/":
            j = block.find("\n", i); i = n if j < 0 else j + 1; continue
        if c == "/" and i + 1 < n and block[i + 1] == "*":
            j = block.find("*/", i + 2); i = n if j < 0 else j + 2; continue
        if c in "\"'`":
            j = i + 1
            while j < n:
                if block[j] == "\\":
                    j += 2; continue
                if block[j] == c:
                    break
                j += 1
            i = j + 1
            continue
        if block.startswith("id:", i) and not (i and (block[i - 1].isalnum() or block[i - 1] in "_$")):
            j = i + 3
            while j < n and block[j] in " \t":
                j += 1
            if j < n and block[j] in "\"'":
                quote, k, buf = block[j], j + 1, []
                while k < n and block[k] != quote:
                    if block[k] == "\\":
                        buf.append(block[k + 1]); k += 2; continue
                    buf.append(block[k]); k += 1
                ids.append("".join(buf))
                i = k + 1
                continue
        i += 1
    return ids


roster_src, why = blob(ROSTER)
if why:
    print("!!NA " + why); raise SystemExit(0)
app_src, why = blob(APPJS)
if why:
    print("!!NA " + why); raise SystemExit(0)

try:
    data = json.loads(roster_src)
except json.JSONDecodeError as exc:
    print("the committed %s is not valid JSON (%s) — the release ships a roster nothing can load"
          % (ROSTER, exc))
    raise SystemExit(0)
roster_ids = [s.get("id") for s in (data.get("staff") or []) if s.get("id")]

start, end = app_src.find("const MOCK_STATUS = {"), app_src.find("// ---- in-memory state")
if start < 0 or end <= start:
    print("!!JUDGE cannot locate the embedded mock block in %s — the judge is broken, not the "
          "page; repair the markers rather than deleting the check" % APPJS)
    raise SystemExit(0)
region = app_src[start:end]
scan = masked(region)   # same length, so every offset below indexes `region` unchanged
m = re.search(r"(?<![A-Za-z0-9_$])staff:\s*\[", scan)
if not m:
    print("!!JUDGE the embedded mock in %s declares no staff[] — the judge cannot answer" % APPJS)
    raise SystemExit(0)
open_at, depth, close_at = m.end() - 1, 0, -1
for j in range(open_at, len(scan)):
    if scan[j] == "[":
        depth += 1
    elif scan[j] == "]":
        depth -= 1
        if depth == 0:
            close_at = j
            break
if close_at < 0:
    print("!!JUDGE the mock's staff[] bracket never closes inside the mock block in %s" % APPJS)
    raise SystemExit(0)
mock_ids = mock_staff_ids(region[open_at:close_at + 1])

for sid in sorted(set(mock_ids)):
    if mock_ids.count(sid) > 1:
        print("DUPLICATE %s — the mock lists this staff id %d times; the page would render two "
              "cards for one agent" % (sid, mock_ids.count(sid)))
for sid in sorted(set(roster_ids) - set(mock_ids)):
    print("MISSING %s — the released roster declares this staff role and the off-disk mock does "
          "not carry it. The mock is a RENDERING FIXTURE: whatever it omits, opening the page off "
          "disk never renders, so a layout break that only that role's strings can cause stays "
          "invisible until a user hits it on live data. That is the exact incident recorded above "
          "MOCK_STATUS in app.js." % sid)
for sid in sorted(set(mock_ids) - set(roster_ids)):
    print("EXTRA %s — the mock renders a staff card for a role the released roster does not "
          "declare, so the first screen a user sees names an agent this install does not have "
          "(the shape db5ba38 fixed for demo_squad)." % sid)
PY
}

judge() { # <case name> <findings>   — empty findings = pass; otherwise print each on its own line
  local name="$1" out="$2"
  grep -q '^!!JUDGE' <<<"$out" && die_judge "$(grep '^!!JUDGE' <<<"$out")"
  # N/A is printed, never counted as a pass or a failure — but it IS tallied, because the summary
  # line has to carry it too. `judge()` got this right from the start and `main()` did not: a
  # tarball user (no .git, so group C is unanswerable) read a clean `all checks PASS` off a run that
  # never asked the question. The per-case line said N/A four lines above; the last line of output
  # is the one people quote.
  if grep -q '^!!NA' <<<"$out"; then
    nas=$((nas + 1))
    printf '  %s → N/A  (not judged)\n' "$name"
    sed 's/^!!NA /      /' <<<"$out"
    return 0
  fi
  check "$name" "$([[ -z "$out" ]] && echo 1 || echo 0)"
  [[ -z "$out" ]] || sed 's/^/      /' <<<"$out"
}

run_cases() {
  section "A. no instructional document prints an /add-project or /add-team the command refuses"
  judge "every documented /add-project and /add-team invocation is one that runs" "$(scan_docs)"

  section "B. the SHIPPED teammate definitions (the committed blobs) match what /sync-roster generates"
  judge "no generated .claude/agents file is orphaned, missing, or stale" "$(scan_agents)"

  section "C. the RELEASED roster (the committed blob, not your working tree) ships no project squad"
  judge "the roster this repo distributes declares no project squad" "$(scan_shipped_roster)"

  section "D. the off-disk mock in static/app.js carries exactly the RELEASED roster's staff"
  judge "the shipped mock's staff ids equal the shipped roster's staff ids" "$(scan_mock_staff)"
}

# Stage the DATA the judge reads into a throwaway root, so a mutation never touches the real tree.
stage_root() { # -> echoes the staged root
  local d; d="$(mktemp -d)"
  mkdir -p "$d/standup" "$d/hooks" "$d/.claude"
  local f
  for f in README.md CLAUDE.md ARCHITECTURE.md SECURITY.md; do
    [[ -f "$REPO/$f" ]] && cp "$REPO/$f" "$d/$f"
  done
  cp "$REPO/standup/team.json" "$d/standup/team.json"
  mkdir -p "$d/standup/portal/static"
  cp "$REPO/standup/portal/static/app.js" "$d/standup/portal/static/app.js"   # case D reads this
  cp "$REPO/hooks/supervisor_gate.py" "$d/hooks/"
  cp -R "$REPO/skills" "$d/skills"
  [[ -d "$REPO/.claude/commands" ]] && cp -R "$REPO/.claude/commands" "$d/.claude/commands"
  cp -R "$REPO/.claude/agents" "$d/.claude/agents"
  # Cases B, C and D read COMMITTED blobs, so the staged root has to be a real checkout or they can
  # only ever report N/A and their mutations would prove nothing. It also splits the mutation set in
  # two on purpose: a mutation that COMMITS is a change to what the repo distributes and must go
  # red, while one that only edits the working tree is a user customising an install and must stay
  # green. Group A still reads files off disk (it judges documents, which have no shipped/local
  # distinction), so its mutations do not commit.
  git -C "$d" init -q -b main 2>/dev/null || git -C "$d" init -q
  git -C "$d" add -A
  git -C "$d" -c user.name=judge -c user.email=judge@invalid commit -q -m "staged release copy"
  echo "$d"
}

self_test() {
  # E-03: a judge that has not been shown to fail is not a judge. One mutation per case, each on
  # the DATA (that is what this judge is about), each required to redden its OWN case.
  if ! bash "$0" >/dev/null 2>&1; then
    printf '!! SELF-TEST ABORTED — the unmutated judge is not green.\n' >&2
    printf '   Every check below asks "did this case go red"; a case already red answers yes for\n' >&2
    printf '   the wrong reason. Fix the baseline first.\n' >&2
    return 3
  fi
  printf '=== --self-test: break ONE release invariant at a time ===\n'
  local rc=0 d out victim

  # A: put the exact v0.5.0 line back into the README copy.
  d="$(stage_root)"
  printf '\nSee (`/add-project adopt standup/portal`) for the portal.\n' >> "$d/README.md"
  out="$(STANDUP_RELEASE_ROOT="$d" bash "$0" 2>&1)"
  if grep -q "invocation is one that runs → FAIL" <<<"$out"; then
    printf '  %-46s → correctly went RED\n' "docs-dead-add-project"
  else
    printf '  %-46s → ERROR  its own case stayed green\n' "docs-dead-add-project" >&2; rc=3
  fi
  rm -rf "$d"

  # A, the /add-team half — its OWN mutation, because the /add-project one above went red for
  # weeks while this branch did not exist at all. Planted in a DIFFERENT document from the
  # /add-project mutation so neither can be passing on the other's behalf.
  d="$(stage_root)"
  printf '\n| `/add-team demo — do a thing` | add a squad |\n' >> "$d/CLAUDE.md"
  out="$(STANDUP_RELEASE_ROOT="$d" bash "$0" 2>&1)"
  if grep -q "invocation is one that runs → FAIL" <<<"$out" \
     && grep -q "CLAUDE.md.*no --kind" <<<"$out"; then
    printf '  %-46s → correctly went RED\n' "docs-add-team-no-kind"
  else
    printf '  %-46s → ERROR  its own case stayed green\n' "docs-add-team-no-kind" >&2
    printf '     a documented /add-team without --kind builds a squad the engine refuses to run\n' >&2
    rc=3
  fi
  rm -rf "$d"

  # ...and the counter-case: a CORRECT /add-team must NOT be flagged, or the lint is just noise
  # and gets switched off. This is the half a "does it go red" mutation cannot prove.
  d="$(stage_root)"
  printf '\n| `/add-team demo — do a thing --kind cli --inspect "pytest -q"` | add a squad |\n' >> "$d/CLAUDE.md"
  out="$(STANDUP_RELEASE_ROOT="$d" bash "$0" 2>&1)"
  if grep -q "invocation is one that runs → PASS" <<<"$out"; then
    printf '  %-46s → correctly stayed GREEN\n' "docs-add-team-well-formed"
  else
    printf '  %-46s → ERROR  a well-formed /add-team was flagged\n' "docs-add-team-well-formed" >&2
    grep -A3 "invocation is one that runs" <<<"$out" >&2
    rc=3
  fi
  rm -rf "$d"

  # B: SHIP a generated definition for a role the roster does not have — exactly the shape v0.5.0
  # shipped. Committed, because "ship" is what the case is about: an orphan sitting only in a
  # working tree is a local leftover, and reddening on it is what made B report on the reader's
  # progress through the README instead of on the release.
  d="$(stage_root)"
  cp "$(ls "$d/.claude/agents/"*.md | head -1)" "$d/.claude/agents/ghost_dev.md"
  git -C "$d" add -A
  git -C "$d" -c user.name=judge -c user.email=judge@invalid commit -qm "ship an orphaned definition"
  out="$(STANDUP_RELEASE_ROOT="$d" bash "$0" 2>&1)"
  if grep -q "orphaned, missing, or stale → FAIL" <<<"$out" && grep -q "EXTRA ghost_dev.md" <<<"$out"; then
    printf '  %-46s → correctly went RED\n' "agents-orphan-shipped"
  else
    printf '  %-46s → ERROR  its own case stayed green\n' "agents-orphan-shipped" >&2
    grep -A3 "orphaned, missing, or stale" <<<"$out" >&2
    rc=3
  fi
  rm -rf "$d"

  # B again, the other direction: a role in the SHIPPED roster with no definition shipped.
  d="$(stage_root)"
  victim="$(basename "$(ls "$d/.claude/agents/"*.md | head -1)")"
  rm -f "$d/.claude/agents/$victim"
  git -C "$d" -c user.name=judge -c user.email=judge@invalid commit -qam "ship a roster role with no definition"
  out="$(STANDUP_RELEASE_ROOT="$d" bash "$0" 2>&1)"
  if grep -q "orphaned, missing, or stale → FAIL" <<<"$out" && grep -q "MISSING $victim" <<<"$out"; then
    printf '  %-46s → correctly went RED\n' "agents-missing-shipped"
  else
    printf '  %-46s → ERROR  its own case stayed green\n' "agents-missing-shipped" >&2
    grep -A3 "orphaned, missing, or stale" <<<"$out" >&2
    rc=3
  fi
  rm -rf "$d"

  # B's control, and the half that was broken on main: README step 3 is `/add-team portal` +
  # `/add-role` twice, step 4 is `/sync-roster`. Standing between them — roster edited, definitions
  # not yet generated — is a legitimate state of a user's install, and it printed
  # `MISSING portal_backend.md / MISSING portal_frontend.md  EXIT=1`. A judge that reddens on where
  # someone has got to in the README is not judging the release.
  d="$(stage_root)"
  python3 - "$d/standup/team.json" <<'PY'
import json, sys
p = sys.argv[1]
data = json.load(open(p, encoding="utf-8"))
data["teams"] = [{"id": "portal", "name": "portal squad",
                  "review_surface": {"kind": "web", "label": "Mission Control",
                                     "inspect": "bash standup/control/inspect_portal.sh"},
                  "developers": [
                      {"id": "portal_backend", "role": "Portal Dev — Backend & Jobs",
                       "folder": "standup/portal", "active": True, "pair": "portal_frontend"},
                      {"id": "portal_frontend", "role": "Portal Dev — Frontend",
                       "folder": "standup/portal", "active": True, "pair": "portal_backend"}]}]
json.dump(data, open(p, "w", encoding="utf-8"), indent=2)
PY
  out="$(STANDUP_RELEASE_ROOT="$d" bash "$0" 2>&1)"
  if grep -q "orphaned, missing, or stale → PASS" <<<"$out"; then
    printf '  %-46s → correctly stayed GREEN\n' "agents-user-mid-readme-uncommitted"
  else
    printf '  %-46s → ERROR  a user between README steps 3 and 4 was flagged\n' "agents-user-mid-readme-uncommitted" >&2
    grep -A3 "orphaned, missing, or stale" <<<"$out" >&2
    rc=3
  fi
  rm -rf "$d"

  # C: COMMIT a project squad into the staged roster — the shape a release must not distribute.
  d="$(stage_root)"
  python3 - "$d/standup/team.json" <<'PY'
import json, sys
p = sys.argv[1]
data = json.load(open(p, encoding="utf-8"))
data["teams"] = [{"id": "leaked_squad", "name": "Leaked Squad",
                  "developers": [{"id": "leaked_dev", "folder": "not-your-repo"}]}]
json.dump(data, open(p, "w", encoding="utf-8"), indent=2)
PY
  git -C "$d" -c user.name=judge -c user.email=judge@invalid commit -qam "leak a squad into the release"
  out="$(STANDUP_RELEASE_ROOT="$d" bash "$0" 2>&1)"
  if grep -q "distributes declares no project squad → FAIL" <<<"$out"; then
    printf '  %-46s → correctly went RED\n' "roster-ships-a-squad"
  else
    printf '  %-46s → ERROR  its own case stayed green\n' "roster-ships-a-squad" >&2; rc=3
  fi
  rm -rf "$d"

  # C's control, and the reason this case exists at all: a user running the README's `/add-team` +
  # `/add-role` edits the WORKING TREE. That is customising an install, not changing what the repo
  # distributes, and it must NOT redden anything. The old home for this fact — a unit test on the
  # live roster — failed exactly here, so a mutation that only proves "it can go red" would be
  # proving the wrong half.
  d="$(stage_root)"
  python3 - "$d/standup/team.json" <<'PY'
import json, sys
p = sys.argv[1]
data = json.load(open(p, encoding="utf-8"))
data["teams"] = [{"id": "portal", "name": "portal squad",
                  "review_surface": {"kind": "web", "label": "Mission Control",
                                     "inspect": "bash standup/control/inspect_portal.sh"},
                  "developers": [{"id": "portal_backend", "folder": "standup/portal"},
                                 {"id": "portal_frontend", "folder": "standup/portal"}]}]
json.dump(data, open(p, "w", encoding="utf-8"), indent=2)
PY
  out="$(STANDUP_RELEASE_ROOT="$d" bash "$0" 2>&1)"
  if grep -q "distributes declares no project squad → PASS" <<<"$out"; then
    printf '  %-46s → correctly stayed GREEN\n' "roster-user-added-a-squad-uncommitted"
  else
    printf '  %-46s → ERROR  a user following the README was flagged\n' "roster-user-added-a-squad-uncommitted" >&2
    grep -A3 "distributes declares no project squad" <<<"$out" >&2
    rc=3
  fi
  rm -rf "$d"

  # D: COMMIT a staff role the mock does not carry — the exact shape `product_qa` shipped in. The
  # length floor in portal/tests/test_static_mock.py stays green through this mutation, which is
  # the point of the case existing separately: a floor measures the strings that ARE there.
  d="$(stage_root)"
  python3 - "$d/standup/team.json" <<'PY'
import json, sys
p = sys.argv[1]
data = json.load(open(p, encoding="utf-8"))
data.setdefault("staff", []).append(
    {"id": "release_qa", "role": "Release QA — a staff role the mock never heard of",
     "active": True, "note": "a" * 400})
json.dump(data, open(p, "w", encoding="utf-8"), indent=2)
PY
  git -C "$d" -c user.name=judge -c user.email=judge@invalid commit -qam "add a staff role, forget the mock"
  out="$(STANDUP_RELEASE_ROOT="$d" bash "$0" 2>&1)"
  if grep -q "equal the shipped roster's staff ids → FAIL" <<<"$out" \
     && grep -q "MISSING release_qa" <<<"$out"; then
    printf '  %-46s → correctly went RED\n' "mock-missing-a-roster-staff"
  else
    printf '  %-46s → ERROR  its own case stayed green\n' "mock-missing-a-roster-staff" >&2
    grep -A3 "equal the shipped roster's staff ids" <<<"$out" >&2
    rc=3
  fi
  rm -rf "$d"

  # D, the other direction: a mock card for a role the release does not declare — the demo_squad
  # shape db5ba38 fixed, which was noticed by a human rather than caught by anything.
  d="$(stage_root)"
  python3 - "$d/standup/portal/static/app.js" <<'PY'
import sys
p = sys.argv[1]
src = open(p, encoding="utf-8").read()
anchor = '    staff: ['
assert anchor in src, "the mock's staff[] anchor moved; this mutation needs updating"
src = src.replace(anchor, anchor + '\n      { id: "ghost_lead", role: "Ghost Lead", note: "not in any roster" },', 1)
open(p, "w", encoding="utf-8").write(src)
PY
  git -C "$d" -c user.name=judge -c user.email=judge@invalid commit -qam "ship a mock card for a role nobody declares"
  out="$(STANDUP_RELEASE_ROOT="$d" bash "$0" 2>&1)"
  if grep -q "equal the shipped roster's staff ids → FAIL" <<<"$out" \
     && grep -q "EXTRA ghost_lead" <<<"$out"; then
    printf '  %-46s → correctly went RED\n' "mock-names-a-role-nobody-declares"
  else
    printf '  %-46s → ERROR  its own case stayed green\n' "mock-names-a-role-nobody-declares" >&2
    grep -A3 "equal the shipped roster's staff ids" <<<"$out" >&2
    rc=3
  fi
  rm -rf "$d"

  # D's control — the same half C needs, for the same reason. `/add-role --staff` writes to
  # `staff[]`, so a user who has run it has a roster the shipped mock legitimately does not match.
  # That must stay GREEN, or this case is cf8ae07's defect wearing a new filename.
  d="$(stage_root)"
  python3 - "$d/standup/team.json" <<'PY'
import json, sys
p = sys.argv[1]
data = json.load(open(p, encoding="utf-8"))
data.setdefault("staff", []).append(
    {"id": "my_own_analyst", "role": "Analyst I added with /add-role --staff", "active": True})
json.dump(data, open(p, "w", encoding="utf-8"), indent=2)
PY
  out="$(STANDUP_RELEASE_ROOT="$d" bash "$0" 2>&1)"
  if grep -q "equal the shipped roster's staff ids → PASS" <<<"$out"; then
    printf '  %-46s → correctly stayed GREEN\n' "mock-vs-user-added-staff-uncommitted"
  else
    printf '  %-46s → ERROR  a user running /add-role --staff was flagged\n' "mock-vs-user-added-staff-uncommitted" >&2
    grep -A3 "equal the shipped roster's staff ids" <<<"$out" >&2
    rc=3
  fi
  rm -rf "$d"

  # D's JUDGE-integrity control. The two cases above prove D can go red for the right reasons; this
  # one proves it cannot be aimed at the wrong text. A COMMENT mentioning `staff: [` — prose, in a
  # file whose comments exist to explain the mock — used to out-anchor the real declaration, and the
  # run then reported four MISSING staff. Red is the right direction and the wrong file: the mock
  # was intact. A judge that fails loudly while blaming the wrong thing still sends the reader to
  # the wrong place, which is the same cost as failing silently, paid later.
  d="$(stage_root)"
  python3 - "$d/standup/portal/static/app.js" <<'PY'
import sys
p = sys.argv[1]
src = open(p, encoding="utf-8").read()
anchor = "const MOCK_STATUS = {"
assert anchor in src, "the mock block marker moved; this mutation needs updating"
src = src.replace(anchor, anchor + "\n    // the roster's staff: [ ... ] is mirrored below", 1)
open(p, "w", encoding="utf-8").write(src)
PY
  git -C "$d" -c user.name=judge -c user.email=judge@invalid commit -qam "explain the mock in a comment"
  out="$(STANDUP_RELEASE_ROOT="$d" bash "$0" 2>&1)"
  if grep -q "equal the shipped roster's staff ids → PASS" <<<"$out"; then
    printf '  %-46s → correctly stayed GREEN\n' "comment-naming-staff-does-not-move-anchor"
  else
    printf '  %-46s → ERROR  a comment out-anchored the real staff[]\n' "comment-naming-staff-does-not-move-anchor" >&2
    grep -A3 "equal the shipped roster's staff ids" <<<"$out" >&2
    rc=3
  fi

  # ...and E-03 on that green, for the reason the summary control below is written: an assertion
  # that something stayed GREEN is satisfied by a judge that says PASS to everything. A PRE-FIX
  # copy — the anchor searched in the raw region, a ONE-token sed so the mutation cannot drift
  # into rewriting the logic — judges the very same staged root and must report the mock as
  # missing every staff role it in fact carries.
  local raw_anchor_copy; raw_anchor_copy="$(mktemp -d)/judge_raw_anchor.sh"
  sed 's/masked(region)/region/' "$0" > "$raw_anchor_copy"
  out="$(STANDUP_RELEASE_REPO="$REPO" STANDUP_RELEASE_ROOT="$d" bash "$raw_anchor_copy" 2>&1)"
  if grep -q "equal the shipped roster's staff ids → FAIL" <<<"$out" \
     && grep -q "^ *MISSING " <<<"$out"; then
    printf '  %-46s → the pre-fix copy still mis-anchors\n' "comment-anchor-prefix-copy-is-caught"
  else
    printf '  %-46s → ERROR  the green above proves nothing\n' "comment-anchor-prefix-copy-is-caught" >&2
    grep -A3 "equal the shipped roster's staff ids" <<<"$out" >&2
    rc=3
  fi
  rm -rf "$d"

  # THE SUMMARY LINE. Every case above asks "did the right case go red"; this one asks whether the
  # LAST LINE tells the truth, which is the only line most readers see. A tarball/marketplace
  # install has no .git, so C and D are unanswerable — and the run used to end in a bare
  # `all checks PASS`, reporting a clean bill of health on questions it never asked.
  d="$(stage_root)"; rm -rf "$d/.git"
  out="$(STANDUP_RELEASE_ROOT="$d" bash "$0" 2>&1)"
  if grep -qE '^all checks PASS \([0-9]+ not judged\)$' <<<"$out" \
     && grep -q '→ N/A' <<<"$out"; then
    printf '  %-46s → correctly declared the unjudged\n' "summary-declares-unanswered-cases"
  else
    printf '  %-46s → ERROR  the summary hid an unanswered case\n' "summary-declares-unanswered-cases" >&2
    tail -4 <<<"$out" >&2
    rc=3
  fi

  # ...and E-03 on THAT case, because a positive assertion on output can be satisfied by output
  # that was already correct. A PRE-FIX copy of this very script — the summary built without the
  # unjudged suffix, which is exactly the code that shipped — must make the case above go red. The
  # mutation targets one variable name, so it cannot drift into rewriting the logic. The copy runs
  # out of a temp dir and is pointed back at the real tools via STANDUP_RELEASE_REPO.
  local prefix_copy; prefix_copy="$(mktemp -d)/judge_prefix.sh"
  sed 's/\$unjudged//g' "$0" > "$prefix_copy"
  out="$(STANDUP_RELEASE_REPO="$REPO" STANDUP_RELEASE_ROOT="$d" bash "$prefix_copy" 2>&1)"
  # Anchored on the SUMMARY line, not on the words: the per-case rows legitimately end in
  # "(not judged)" too, and matching those made this control pass for the wrong reason once.
  if grep -qE '^all checks PASS$' <<<"$out" && ! grep -qE '^all checks PASS \(' <<<"$out"; then
    printf '  %-46s → the pre-fix copy still prints the lie\n' "summary-prefix-copy-is-caught"
  else
    printf '  %-46s → ERROR  the mutation did not reproduce the defect,\n' "summary-prefix-copy-is-caught" >&2
    printf '     so the case above proves nothing. Check that the summary still uses $unjudged.\n' >&2
    tail -4 <<<"$out" >&2
    rc=3
  fi
  rm -rf "$(dirname "$prefix_copy")" "$d"

  # "each drove its own case red" would be a lie now: three of these deliberately plant a CORRECT
  # input and require the case to stay green. A lint only has teeth if it fires on the bad input
  # AND holds its tongue on the good one; a mutation set proves only the first half.
  [[ $rc -eq 0 ]] && printf '\n--self-test → PASS  (each planted defect reddened its OWN case; every well-formed control stayed green)\n'
  return $rc
}

main() {
  case "${1:-}" in
    --self-test) self_test; exit $? ;;
    "") ;;
    *) printf 'usage: test_release_invariants.sh [--self-test]\n' >&2; exit 64 ;;
  esac
  printf 'release-invariants judge — read-only; judging %s\n' "$ROOT"
  run_cases
  # The summary must not describe a run as clean when part of it was never judged. Exit status is
  # deliberately UNCHANGED — N/A stays neither pass nor fail, per the reasoning above case C, and CI
  # runs on a checkout where nothing is N/A — so this line is the only place the reader learns it.
  # That is exactly why it has to say so.
  local unjudged=""
  [[ $nas -gt 0 ]] && unjudged=" ($nas not judged)"
  printf '\n%s\n' "$([[ $fails -eq 0 ]] && echo "all checks PASS$unjudged" || echo "$fails check(s) FAILED$unjudged")"
  printf 'Run --self-test to prove each invariant can fail on its own (E-03).\n'
  [[ $fails -eq 0 ]] || exit 1
}

main "$@"
