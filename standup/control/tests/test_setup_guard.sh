#!/usr/bin/env bash
# setup.sh demo-app-guard judge.
#
#     bash standup/control/tests/test_setup_guard.sh
#     bash standup/control/tests/test_setup_guard.sh --self-test
#
# WHAT IT GUARDS. `demo-app/` is a SAMPLE. Deleting it is something the docs invite, and it used
# to make the installer die: `set -euo pipefail` + `git -C "$ROOT/demo-app" init` = `fatal: cannot
# change to .../demo-app`, exit 128, with nothing installed. An installer that hard-fails on the
# supported "I removed the sample" path is a closed door on the way in.
#
# WHY IT EXTRACTS SECTION 5 INSTEAD OF RUNNING setup.sh WHOLE. The real script creates a venv and
# pip-installs from the network in sections 1 and 4; a judge that needs both is a judge nobody runs.
# So this reads the REAL setup.sh and slices out section 5 BY ITS OWN MARKER COMMENTS, then runs
# that slice in a throwaway dir. It is the shipped text being tested, not a hand-kept copy — and if
# the markers ever stop matching, that is exit 3 (the judge is broken), never a silent skip. A
# fixture that quietly stops covering the code reads as a pass, which is the whole disease.
#
# Exit codes (same vocabulary as verify_design_quality.js / test_sdlc_routing.js):
#   0  pass
#   1  failures
#   3  the judge itself is broken (the section markers no longer match setup.sh)
#  64  usage
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
SETUP="$REPO/setup.sh"

BEGIN_MARK='# --- 5. demo-app: local git + local bare origin (offline) ---'
END_MARK='# --- 6. runtime state dirs ---'

fails=0
check() { # name ok detail
  local name="$1" ok="$2" detail="${3:-}"
  [[ "$ok" == "1" ]] || fails=$((fails + 1))
  printf '  %s → %s%s\n' "$name" "$([[ "$ok" == 1 ]] && echo PASS || echo FAIL)" \
    "${detail:+  $detail}"
}
section() { printf '\n%s\n' "$1"; }

die_judge() { printf '\n!! JUDGE BROKEN — %s\n' "$1" >&2; exit 3; }

# ---------- slice section 5 out of the real setup.sh ----------
# `sed -n '/BEGIN/,/END/p'` on literal comment lines; both must be present exactly once.
extract_section5() {
  local src="$1"
  grep -qF "$BEGIN_MARK" "$src" || die_judge "begin marker not found in setup.sh: $BEGIN_MARK"
  grep -qF "$END_MARK"   "$src" || die_judge "end marker not found in setup.sh: $END_MARK"
  awk -v b="$BEGIN_MARK" -v e="$END_MARK" '
    index($0, b) { inside = 1 }
    index($0, e) { inside = 0 }
    inside { print }
  ' "$src"
}

# Build a runnable script: the same `set` line the installer uses, the ROOT/PY preamble section 5
# depends on, then the extracted section verbatim.
build_runner() { # <section5-text> <outfile>
  local body="$1" out="$2"
  {
    echo 'set -euo pipefail'
    echo 'ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"'
    echo "$body"
    echo 'echo "SECTION5-COMPLETED"'
  } > "$out"
}

run_case() { # <label> <make-demo:yes|no> <section5-text>  -> prints "exit=<n>" and the output
  local make_demo="$1" body="$2"
  local dir; dir="$(mktemp -d)"
  build_runner "$body" "$dir/run.sh"
  if [[ "$make_demo" == "yes" ]]; then
    mkdir -p "$dir/demo-app"
    printf 'sample\n' > "$dir/demo-app/README.md"
  fi
  local out rc
  out="$(cd "$dir" && bash run.sh 2>&1)"; rc=$?
  # hand both back to the caller through globals (bash has no tuple return)
  LAST_OUT="$out"; LAST_RC="$rc"; LAST_DIR="$dir"
}

cleanup_case() { [[ -n "${LAST_DIR:-}" && "$LAST_DIR" == /*/* ]] && rm -rf "$LAST_DIR"; }

# ---------- the cases ----------
run_cases() { # <section5-text> <label-prefix>
  local body="$1" pfx="${2:-}"

  section "${pfx}A. the sample is present — the installer still sets it up"
  run_case yes "$body"
  check "${pfx}exits 0" "$([[ $LAST_RC -eq 0 ]] && echo 1 || echo 0)" "exit=$LAST_RC"
  check "${pfx}reaches the end of section 5" \
    "$(grep -q 'SECTION5-COMPLETED' <<<"$LAST_OUT" && echo 1 || echo 0)"
  check "${pfx}demo-app becomes a git repo" \
    "$([[ -d "$LAST_DIR/demo-app/.git" ]] && echo 1 || echo 0)"
  check "${pfx}the local bare origin is created" \
    "$([[ -d "$LAST_DIR/.demo-app-origin.git" ]] && echo 1 || echo 0)"
  cleanup_case

  section "${pfx}B. the sample was deleted — the installer must NOT die"
  run_case no "$body"
  check "${pfx}exits 0 (not 128)" "$([[ $LAST_RC -eq 0 ]] && echo 1 || echo 0)" "exit=$LAST_RC"
  check "${pfx}reaches the end of section 5" \
    "$(grep -q 'SECTION5-COMPLETED' <<<"$LAST_OUT" && echo 1 || echo 0)"
  check "${pfx}says WHY it skipped, rather than skipping silently" \
    "$(grep -qi 'demo-app' <<<"$LAST_OUT" && grep -qi 'skip' <<<"$LAST_OUT" && echo 1 || echo 0)" \
    "output: $(head -c 120 <<<"$LAST_OUT" | tr '\n' ' ')"
  check "${pfx}never emits git's fatal" \
    "$(grep -q 'fatal: cannot change to' <<<"$LAST_OUT" && echo 0 || echo 1)"
  check "${pfx}creates no stray demo-app/" \
    "$([[ -e "$LAST_DIR/demo-app" ]] && echo 0 || echo 1)"
  cleanup_case
}

# ---------- --self-test: prove case B can FAIL (E-03) ----------
# One NAMED mutation: neutralize the existence guard, in memory. Case B must go red. A mutation
# whose anchor no longer matches is a HARD ERROR, never a skip.
self_test() {
  local body; body="$(extract_section5 "$SETUP")"
  local anchor='if [[ -d "$DEMO" ]]; then'
  if ! grep -qF "$anchor" <<<"$body"; then
    die_judge "self-test mutation anchor not found in section 5: $anchor
      The guard was renamed or removed. Re-anchor this mutation or delete it — a mutation that
      silently no-ops reads as a pass, which is the gate-that-never-fires this judge deletes."
  fi
  local mutated; mutated="$(sed 's|if \[\[ -d "\$DEMO" \]\]; then|if true; then|' <<<"$body")"
  printf '\n=== --self-test: guard neutralized (`if [[ -d "$DEMO" ]]` -> `if true`) ===\n'
  printf 'Case B MUST go red below. If it stays green, this judge is not a judge.\n'
  fails=0
  run_cases "$mutated" "[mutated] "
  if [[ $fails -eq 0 ]]; then
    printf '\n!! SELF-TEST FAILED — the guard was neutralized and NOTHING went red.\n' >&2
    return 3
  fi
  printf '\n--self-test → PASS  (%d check(s) went red under the mutation, as required)\n' "$fails"
  return 0
}

main() {
  [[ -f "$SETUP" ]] || die_judge "setup.sh not found at $SETUP"
  case "${1:-}" in
    --self-test) self_test; exit $? ;;
    "") ;;
    *) printf 'usage: test_setup_guard.sh [--self-test]\n' >&2; exit 64 ;;
  esac

  local body; body="$(extract_section5 "$SETUP")"
  printf 'setup.sh demo-app-guard judge — section 5 extracted from %s\n' "${SETUP#"$REPO"/}"
  run_cases "$body"

  printf '\n%s\n' "$([[ $fails -eq 0 ]] && echo "all checks PASS" || echo "$fails check(s) FAILED")"
  printf 'Run --self-test to prove case B can fail; a case that cannot fail is not a check (E-03).\n'
  [[ $fails -eq 0 ]] || exit 1
}

main "$@"
