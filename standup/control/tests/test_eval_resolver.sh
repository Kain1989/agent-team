#!/usr/bin/env bash
# eval-target resolver judge.
#
#     bash standup/control/tests/test_eval_resolver.sh
#     bash standup/control/tests/test_eval_resolver.sh --self-test
#
# WHAT IT GUARDS. `/eval` scores the team against a gold-set bound to `demo-app` — an OPTIONAL
# sample the docs invite you to delete. Once deleted, every case had a target that was not there,
# and the suite produced NOTHING: no cases, no zero, no explanation. A regression suite that goes
# quiet when its target vanishes is indistinguishable from one that has not been run, which is the
# worst thing a suite can be.
#
# The fix moved the RUN-vs-SKIP decision out of the skill prompt and into `evals/resolve_cases.py`,
# so it is decided by code and can be judged. This judge is that judging.
#
# Every case runs against a throwaway fixture cases.json in a mktemp dir; the repo's own
# evals/cases.json is only ever READ, and only in case F.
#
# Exit codes: 0 pass · 1 failures · 3 the judge itself is broken · 64 usage
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
RESOLVER="$REPO/evals/resolve_cases.py"

fails=0
FAILED_NAMES=""
NA_COUNT=0
check() { # name ok detail
  local name="$1" ok="$2" detail="${3:-}"
  if [[ "$ok" != "1" ]]; then
    fails=$((fails + 1))
    FAILED_NAMES="$FAILED_NAMES
$name"
  fi
  printf '  %s → %s%s\n' "$name" "$([[ "$ok" == 1 ]] && echo PASS || echo FAIL)" \
    "${detail:+  $detail}"
}
section() { printf '\n%s\n' "$1"; }
die_judge() { printf '\n!! JUDGE BROKEN — %s\n' "$1" >&2; exit 3; }

# Writes a fixture cases.json and runs the resolver against a fixture repo root.
# Sets LAST_OUT / LAST_RC.
run_resolver() { # <resolver-path> <fixture-dir> <cases-json-text> [dirs-to-create...]
  local resolver="$1" dir="$2" cases="$3"; shift 3
  mkdir -p "$dir/evals"
  printf '%s' "$cases" > "$dir/evals/cases.json"
  local d; for d in "$@"; do mkdir -p "$dir/$d"; done
  LAST_OUT="$(python3 "$resolver" --cases "$dir/evals/cases.json" --repo "$dir" 2>&1)"
  LAST_RC=$?
}

# Case `c` is the one that makes `requires` mean anything: its target IS present and its `requires`
# is NOT. Every other fixture has requires == target, and `requires` DEFAULTS to target — so
# without `c` the entire requires branch could be deleted and this judge stayed green (measured:
# `elif False:` on that branch alone, all checks PASS, exit 0, while the resolver flipped a real
# case from SKIP to run). `requires` is a field this batch introduced and the gold-set's own
# `_targets` note invites users to write; shipping it with no covering case is shipping a promise.
CASES_BOTH='{"target":"demo-app","cases":[
  {"id":"a","requires":"demo-app","prompt":"p","check":"true"},
  {"id":"b","target":"my-repo","requires":"my-repo","prompt":"p","check":"true"},
  {"id":"c","target":"my-repo","requires":"golden-fixtures","prompt":"p","check":"true"}]}'
CASES_NO_TARGET='{"cases":[{"id":"a","prompt":"p","check":"true"}]}'
CASES_MISSING_FIELD='{"target":"demo-app","cases":[{"id":"a","prompt":"p"}]}'
CASES_MALFORMED='{"target":"demo-app","cases":[  '

run_cases() { # <resolver-path> <label-prefix>
  local R="$1" pfx="${2:-}"
  local dir

  section "${pfx}A. targets present — cases run, EXCEPT the one whose \`requires\` is absent"
  dir="$(mktemp -d)"; run_resolver "$R" "$dir" "$CASES_BOTH" demo-app my-repo
  check "${pfx}exit 0" "$([[ $LAST_RC -eq 0 ]] && echo 1 || echo 0)" "exit=$LAST_RC"
  check "${pfx}2 runnable, 1 skipped" \
    "$(grep -q '2 runnable, 1 skipped' <<<"$LAST_OUT" && echo 1 || echo 0)" \
    "$(head -1 <<<"$LAST_OUT")"
  check "${pfx}\`requires\` skips a case whose TARGET is present" \
    "$(grep -qE '^ +SKIP +c ' <<<"$LAST_OUT" && echo 1 || echo 0)" \
    "without this, the whole requires branch is dead code no fixture reaches"
  check "${pfx}and names the missing requirement, not the target" \
    "$(grep -E '^ +SKIP +c ' <<<"$LAST_OUT" | grep -q "golden-fixtures" && echo 1 || echo 0)"
  rm -rf "$dir"

  section "${pfx}B. the sample was deleted — its case SKIPS, the other still runs"
  dir="$(mktemp -d)"; run_resolver "$R" "$dir" "$CASES_BOTH" my-repo
  check "${pfx}exit 0 (a missing sample is not an error)" \
    "$([[ $LAST_RC -eq 0 ]] && echo 1 || echo 0)" "exit=$LAST_RC"
  check "${pfx}1 runnable, 2 skipped" \
    "$(grep -q '1 runnable, 2 skipped' <<<"$LAST_OUT" && echo 1 || echo 0)" \
    "$(head -1 <<<"$LAST_OUT")"
  check "${pfx}the skip is marked SKIP, never counted as a pass" \
    "$(grep -qE '^ +SKIP +a ' <<<"$LAST_OUT" && echo 1 || echo 0)"
  check "${pfx}the skip NAMES the missing directory" \
    "$(grep -E '^ +SKIP +a ' <<<"$LAST_OUT" | grep -q "demo-app" && echo 1 || echo 0)"
  check "${pfx}the surviving case is unaffected" \
    "$(grep -qE '^ +run +b ' <<<"$LAST_OUT" && echo 1 || echo 0)"
  rm -rf "$dir"

  section "${pfx}C. everything is gone — 'nothing to run' is SAID, not implied"
  dir="$(mktemp -d)"; run_resolver "$R" "$dir" "$CASES_BOTH"
  check "${pfx}exit 0" "$([[ $LAST_RC -eq 0 ]] && echo 1 || echo 0)" "exit=$LAST_RC"
  check "${pfx}0 runnable, 3 skipped" \
    "$(grep -q '0 runnable, 3 skipped' <<<"$LAST_OUT" && echo 1 || echo 0)" \
    "$(head -1 <<<"$LAST_OUT")"
  check "${pfx}prints an explicit 'nothing to run' explanation" \
    "$(grep -qi 'nothing to run here' <<<"$LAST_OUT" && echo 1 || echo 0)"
  check "${pfx}tells the reader how to make it runnable" \
    "$(grep -qi 'add a case for your own project' <<<"$LAST_OUT" && echo 1 || echo 0)"
  rm -rf "$dir"

  section "${pfx}D. no target declared anywhere — stated, not crashed"
  dir="$(mktemp -d)"; run_resolver "$R" "$dir" "$CASES_NO_TARGET"
  check "${pfx}exit 0" "$([[ $LAST_RC -eq 0 ]] && echo 1 || echo 0)" "exit=$LAST_RC"
  check "${pfx}says the case has no target" \
    "$(grep -qi 'no target' <<<"$LAST_OUT" && echo 1 || echo 0)"
  rm -rf "$dir"

  section "${pfx}E. a broken gold-set is LOUD (exit 1), never an empty plan"
  dir="$(mktemp -d)"; run_resolver "$R" "$dir" "$CASES_MALFORMED" demo-app
  check "${pfx}malformed JSON exits 1" "$([[ $LAST_RC -eq 1 ]] && echo 1 || echo 0)" "exit=$LAST_RC"
  rm -rf "$dir"
  dir="$(mktemp -d)"; run_resolver "$R" "$dir" "$CASES_MISSING_FIELD" demo-app
  check "${pfx}a case missing \`check\` exits 1" \
    "$([[ $LAST_RC -eq 1 ]] && echo 1 || echo 0)" "exit=$LAST_RC"
  check "${pfx}and names the missing field" \
    "$(grep -qi 'check' <<<"$LAST_OUT" && echo 1 || echo 0)"
  rm -rf "$dir"

  # Case F is about the file this repo ships, not about resolver logic, and it resolves paths
  # relative to the resolver's OWN location — so a mutated copy living in /tmp would fail it for a
  # reason that has nothing to do with the mutation. Letting that count as "the mutation worked"
  # is how a self-test certifies itself on unrelated noise; it happened here on the first cut.
  [[ -n "$pfx" ]] && return 0

  section "${pfx}F. the SHIPPED gold-set resolves (read-only)"
  LAST_OUT="$(python3 "$R" --json 2>&1)"; LAST_RC=$?
  check "${pfx}evals/cases.json resolves cleanly" \
    "$([[ $LAST_RC -eq 0 ]] && echo 1 || echo 0)" "exit=$LAST_RC"
  # `all(...)` over an EMPTY list is True. With `"cases": []` this check passed while asserting
  # nothing at all — and an empty gold-set is not hypothetical: it is what ships the moment the
  # bundled sample goes away. A vacuous pass is the same defect as a gate that never fires, so the
  # emptiness is reported as its own distinct outcome rather than being silently absorbed.
  local shipped_rc; python3 - "$REPO/evals/cases.json" <<'PYEOF'
import json, sys
d = json.load(open(sys.argv[1]))
cases = d.get("cases") or []
if not cases:
    sys.exit(2)                                    # empty: not a pass, not a failure — say so
sys.exit(0 if all(c.get("requires") for c in cases) else 1)
PYEOF
  shipped_rc=$?
  if [[ $shipped_rc -eq 2 ]]; then
    NA_COUNT=$((NA_COUNT + 1))
    printf '  %s → n/a   the shipped gold-set has no cases; nothing to assert (this is NOT a pass)\n' \
      "${pfx}every shipped case declares \`requires\`"
  else
    check "${pfx}every shipped case declares \`requires\`" \
      "$([[ $shipped_rc -eq 0 ]] && echo 1 || echo 0)" \
      "a case without it silently inherits the default target"
  fi
}

self_test() {
  [[ -f "$RESOLVER" ]] || die_judge "resolver not found at $RESOLVER"
  # BOTH skip branches must be neutralized. Killing only the `target` one changes nothing, because
  # `requires` defaults to `target` and the second branch catches the same case — the first cut of
  # this self-test did exactly that, watched B and C stay green, and still reported PASS off an
  # unrelated failure in case F. A mutation has to actually remove the behaviour it claims to.
  local a1='elif not os.path.isdir(os.path.join(repo, target)):'
  local a2='elif requires and not os.path.isdir(os.path.join(repo, requires)):'
  for a in "$a1" "$a2"; do
    grep -qF "$a" "$RESOLVER" || die_judge "self-test anchor not found in resolve_cases.py:
      $a
      Re-anchor it or delete the fixture — a mutation that silently no-ops reads as a pass."
  done

  local dir; dir="$(mktemp -d)"
  # shellcheck disable=SC2064
  trap "rm -rf '$dir'" EXIT
  # NAMED mutation: a missing directory no longer skips — the exact regression this judge exists for.
  sed -e "s|$a1|elif False:|" -e "s|$a2|elif False:|" "$RESOLVER" > "$dir/mutated.py"
  [[ "$(grep -c 'elif False:' "$dir/mutated.py")" == "2" ]] \
    || die_judge "expected both skip branches to be neutralized; re-anchor the mutation."

  printf '=== --self-test: BOTH missing-directory skips neutralized (`elif False:`) ===\n'
  printf 'Cases B and C MUST go red below.\n'
  fails=0; FAILED_NAMES=""
  run_cases "$dir/mutated.py" "[mutated] "

  # Assert the NAMED cases went red, not merely that something did.
  local missed=""
  local want_a='[mutated] `requires` skips a case whose TARGET is present'
  local want_b="[mutated] 1 runnable, 2 skipped"
  local want_c="[mutated] 0 runnable, 3 skipped"
  # A is listed separately from B/C on purpose: it is the ONLY check that fails when the `requires`
  # branch alone is removed. B and C both stay green under that mutation, which is how a whole
  # branch shipped with no covering case and a green judge.
  grep -qxF "$want_a" <<<"$FAILED_NAMES" || missed="$missed
    A did not go red: $want_a"
  grep -qxF "$want_b" <<<"$FAILED_NAMES" || missed="$missed
    B did not go red: $want_b"
  grep -qxF "$want_c" <<<"$FAILED_NAMES" || missed="$missed
    C did not go red: $want_c"
  if [[ -n "$missed" ]]; then
    printf '\n!! SELF-TEST FAILED — the skip logic was removed and the cases that exist to catch\n' >&2
    printf '   that stayed green:%s\n' "$missed" >&2
    return 3
  fi
  printf '\n--self-test → PASS  (B and C both went red under the mutation; %d check(s) total)\n' "$fails"
  return 0
}

main() {
  case "${1:-}" in
    --self-test) self_test; exit $? ;;
    "") ;;
    *) printf 'usage: test_eval_resolver.sh [--self-test]\n' >&2; exit 64 ;;
  esac
  [[ -f "$RESOLVER" ]] || {
    printf 'eval-target resolver judge\n\n  evals/resolve_cases.py → MISSING\n' >&2
    printf '\nThe RUN-vs-SKIP decision is not made by any code, so it cannot be judged: a deleted\n' >&2
    printf 'demo-app leaves /eval with nothing to run and nothing to say.\n' >&2
    exit 1
  }
  printf 'eval-target resolver judge — fixtures only; the repo tree is read, never written\n'
  run_cases "$RESOLVER"
  # The summary must not read "all checks PASS" when a group asserted nothing. An n/a is neither a
  # pass nor a failure, and folding it into the pass line is how a vacuous run looks complete.
  local summary
  if [[ $fails -ne 0 ]]; then summary="$fails check(s) FAILED"
  elif [[ ${NA_COUNT:-0} -gt 0 ]]; then summary="checks PASS, but ${NA_COUNT} asserted nothing (see n/a above)"
  else summary="all checks PASS"; fi
  printf '\n%s\n' "$summary"
  printf 'Run --self-test to prove cases B/C can fail (E-03).\n'
  [[ $fails -eq 0 ]] || exit 1
}

main "$@"
