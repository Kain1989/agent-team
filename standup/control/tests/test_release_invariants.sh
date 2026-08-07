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
#   B. `.claude/agents/portal_backend.md` and `portal_frontend.md` were tracked while the roster had
#      no squad owning them. Running `/sync-roster` on the released tag PRUNES both — which is the
#      proof that `/sync-roster` was never run before the release. "Remember to run /sync-roster
#      before shipping" is a step that exists only as a sentence someone has to remember, and this
#      repo has already recorded what happens to those.
#
# Both are release-time invariants: cheap to check, invisible to every other gate, and the kind of
# thing that is only ever noticed by a user.
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
REPO="$(cd "$HERE/../../.." && pwd)"
# The tree being JUDGED. The tools doing the judging always come from $REPO — mutating the data is
# how --self-test proves the cases have teeth; mutating the tools is what test_add_project.sh does.
ROOT="${STANDUP_RELEASE_ROOT:-$REPO}"
VERIFY="$REPO/standup/control/verify_project.py"
PORTAL="$REPO/standup/portal"

fails=0
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
# A. no instructional document prints an /add-project invocation that /add-project refuses.
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
INVOKE = re.compile(r"/(?:agent-team:)?add-project\s+([^\n`]*)")

bad = []
for rel in DOCS:
    path = os.path.join(root, rel)
    if not os.path.isfile(path):
        continue
    for lineno, line in enumerate(open(path, encoding="utf-8"), 1):
        for m in INVOKE.finditer(line):
            toks = m.group(1).split()
            if not toks:
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
# B. the tracked teammate definitions ARE what /sync-roster generates from the shipped roster.
#
# Scoped to files carrying the generated header, because a HAND-WRITTEN agent definition is
# explicitly allowed to survive a regen (agents_gen prunes on that header, and a portal test asserts
# it). Judging every *.md would make this judge contradict the behaviour it is judging.
# --------------------------------------------------------------------------------------------
scan_agents() { # -> prints "EXTRA <name>" / "MISSING <name>" / "STALE <name>"
  ROOT="$ROOT" PORTAL="$PORTAL" python3 <<'PY'
import os, sys, tempfile, shutil
from pathlib import Path

root = Path(os.environ["ROOT"])
sys.path.insert(0, os.environ["PORTAL"])
from parsers import agents_gen                                     # noqa: E402

tj = root / "standup" / "team.json"
tracked_dir = root / ".claude" / "agents"
if not tj.exists():
    print("!!JUDGE no standup/team.json under %s" % root); raise SystemExit(0)

tmp = Path(tempfile.mkdtemp())
try:
    agents_gen.generate(tj, tmp)                                   # NEVER the real directory
    gen = {p.name: p.read_bytes() for p in tmp.glob("*.md")}
    tracked = {}
    if tracked_dir.is_dir():
        for p in sorted(tracked_dir.glob("*.md")):
            body = p.read_bytes()
            if agents_gen._HEADER.encode("utf-8") in body:         # generated, not hand-written
                tracked[p.name] = body
    for name in sorted(set(tracked) - set(gen)):
        print("EXTRA %s — generated by an earlier roster and no longer in it; /sync-roster prunes it"
              % name)
    for name in sorted(set(gen) - set(tracked)):
        print("MISSING %s — the roster has this role and no definition ships for it" % name)
    for name in sorted(set(gen) & set(tracked)):
        if gen[name] != tracked[name]:
            print("STALE %s — differs from what the current roster generates" % name)
finally:
    shutil.rmtree(tmp, ignore_errors=True)
PY
}

judge() { # <case name> <findings>   — empty findings = pass; otherwise print each on its own line
  local name="$1" out="$2"
  grep -q '^!!JUDGE' <<<"$out" && die_judge "$(grep '^!!JUDGE' <<<"$out")"
  check "$name" "$([[ -z "$out" ]] && echo 1 || echo 0)"
  [[ -z "$out" ]] || sed 's/^/      /' <<<"$out"
}

run_cases() {
  section "A. no instructional document prints an /add-project the command refuses"
  judge "every literal /add-project name in the docs passes /add-project's own rules" "$(scan_docs)"

  section "B. the tracked teammate definitions match what /sync-roster generates"
  judge "no generated .claude/agents file is orphaned, missing, or stale" "$(scan_agents)"
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
  cp "$REPO/hooks/supervisor_gate.py" "$d/hooks/"
  cp -R "$REPO/skills" "$d/skills"
  [[ -d "$REPO/.claude/commands" ]] && cp -R "$REPO/.claude/commands" "$d/.claude/commands"
  cp -R "$REPO/.claude/agents" "$d/.claude/agents"
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
  local rc=0 d out

  # A: put the exact v0.5.0 line back into the README copy.
  d="$(stage_root)"
  printf '\nSee (`/add-project adopt standup/portal`) for the portal.\n' >> "$d/README.md"
  out="$(STANDUP_RELEASE_ROOT="$d" bash "$0" 2>&1)"
  if grep -q "passes /add-project's own rules → FAIL" <<<"$out"; then
    printf '  %-46s → correctly went RED\n' "docs-dead-command"
  else
    printf '  %-46s → ERROR  its own case stayed green\n' "docs-dead-command" >&2; rc=3
  fi
  rm -rf "$d"

  # B: leave behind a generated definition for a role the roster does not have — exactly the shape
  # v0.5.0 shipped.
  d="$(stage_root)"
  cp "$(ls "$d/.claude/agents/"*.md | head -1)" "$d/.claude/agents/ghost_dev.md"
  out="$(STANDUP_RELEASE_ROOT="$d" bash "$0" 2>&1)"
  if grep -q "orphaned, missing, or stale → FAIL" <<<"$out"; then
    printf '  %-46s → correctly went RED\n' "agents-orphan"
  else
    printf '  %-46s → ERROR  its own case stayed green\n' "agents-orphan" >&2; rc=3
  fi
  rm -rf "$d"

  # B again, the other direction: a role in the roster with no definition shipped.
  d="$(stage_root)"
  rm -f "$(ls "$d/.claude/agents/"*.md | head -1)"
  out="$(STANDUP_RELEASE_ROOT="$d" bash "$0" 2>&1)"
  if grep -q "orphaned, missing, or stale → FAIL" <<<"$out"; then
    printf '  %-46s → correctly went RED\n' "agents-missing"
  else
    printf '  %-46s → ERROR  its own case stayed green\n' "agents-missing" >&2; rc=3
  fi
  rm -rf "$d"

  [[ $rc -eq 0 ]] && printf '\n--self-test → PASS  (each broken invariant drove its OWN case red)\n'
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
  printf '\n%s\n' "$([[ $fails -eq 0 ]] && echo "all checks PASS" || echo "$fails check(s) FAILED")"
  printf 'Run --self-test to prove each invariant can fail on its own (E-03).\n'
  [[ $fails -eq 0 ]] || exit 1
}

main "$@"
