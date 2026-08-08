#!/usr/bin/env bash
# Exemption-release judge — `team_run_flag.sh clear` must be REACHABLE in a shared tree.
#
#     bash standup/control/tests/test_run_flag_clear.sh
#     bash standup/control/tests/test_run_flag_clear.sh --self-test
#
# WHAT IT GUARDS. `standup/control/team_run_active` is the flag that switches the supervisor gate
# off so dispatched dev agents can write at all. `clear <run-id>` is the documented way to give it
# back. Its refusal to delete the file while another run's record is present is CORRECT and stays —
# switching the gate back on mid-write is worse than leaving it off, and the gate's own 6h TTL, not
# `clear`, is the real backstop.
#
# What was wrong is that the refusal was the ONLY behaviour. There was no per-record delete, so the
# caller's own record was never removed either: the file only ever grew, and in any tree that had
# seen two overlapping runs `clear` could never succeed again. The safe path was unreachable and
# `--force` — which unlinks the flag under whatever else is running — was the only exit left. A
# build agent hit exactly that and correctly declined to force it. The install this repo was
# distilled from had 30+ records stacked up that way.
#
# So the property under judgement is not "does it refuse". It is:
#
#     N runs arm, N runs clear, and the flag is GONE — without any of them touching another's record.
#
# Case J asserts that end to end, because it is the one thing the old implementation could never do
# no matter how many times it was called.
#
# The second defect is quieter and is the reason a naive per-record delete would not have been safe:
# records were matched with `grep -v -- "$RUN"`, a SUBSTRING match that also read the id as a REGEX.
# A run called `nightly` therefore matched `nightly-2`'s record, concluded it was alone, and deleted
# the flag out from under a live run — the exact accident the refusal exists to prevent, reached
# through the check that implements it. Cases D and E pin that.
#
# Exit codes: 0 pass · 1 failures · 3 the judge itself is broken · 64 usage
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
# The script UNDER TEST. Overridable so --self-test can point the same cases at a pinned pre-fix
# copy and require them to go red. Only the self-test sets it.
SCRIPT="${STANDUP_FLAG_SCRIPT:-$REPO/standup/control/team_run_flag.sh}"

fails=0
check() { # name ok detail
  local name="$1" ok="$2" detail="${3:-}"
  [[ "$ok" == "1" ]] || fails=$((fails + 1))
  printf '  %s → %s%s\n' "$name" "$([[ "$ok" == 1 ]] && echo PASS || echo FAIL)" "${detail:+  $detail}"
}
die_judge() { printf '\n!! JUDGE BROKEN — %s\n' "$1" >&2; exit 3; }

[[ -f "$SCRIPT" ]] || die_judge "no flag script at $SCRIPT"

# ---------- fixture ----------
# The script derives FLAG from its OWN directory, so a fixture is just a temp dir holding a copy of
# the script plus a team_run_active. Nothing in the real tree is read or written by any case.
fixture() { # <record>...  -> echoes the dir
  local d; d="$(mktemp -d)"
  cp "$SCRIPT" "$d/team_run_flag.sh"
  local r
  for r in "$@"; do printf '%s\n' "$r" >> "$d/team_run_active"; done
  printf '%s' "$d"
}
rec() { printf '2026-08-07T0%s:00:00Z | %s | note %s' "$1" "$2" "$1"; }
flag_of() { printf '%s/team_run_active' "$1"; }
# BSD and GNU stat disagree on the flag for mtime and CI runs the other one.
mtime_of() { stat -f %m "$1" 2>/dev/null || stat -c %Y "$1"; }
# NEVER call this inside `$( )`. It reports through the global CLEAR_RC, and a command substitution
# runs it in a SUBSHELL, so the assignment is discarded and the caller silently reads the PREVIOUS
# case's exit code. That is not hypothetical: case F was written that way and passed on case G's
# rc=1 while the script it was judging correctly exited 2. Redirect the output instead.
CLEAR_RC=""
clear_run() { # <dir> <args...>  -> prints stdout+stderr; sets CLEAR_RC
  local d="$1"; shift
  local out; out="$(bash "$d/team_run_flag.sh" clear "$@" 2>&1)"; CLEAR_RC=$?
  printf '%s' "$out"
}

run_cases() {
  local d f

  printf '\nreleasing one run out of several\n'

  # A. the caller's records go; every other record survives BYTE-IDENTICAL.
  d="$(fixture "$(rec 1 run-alpha)" "$(rec 2 run-beta)" "$(rec 3 run-alpha)")"; f="$(flag_of "$d")"
  clear_run "$d" run-alpha >/dev/null
  if [[ -f "$f" ]]; then
    check "clear-releases-only-the-callers-record" \
      "$([[ "$(cat "$f")" == "$(rec 2 run-beta)" ]] && echo 1 || echo 0)" \
      "$(printf '%s' "$(cat "$f")" | tr '\n' ';')"
  else
    check "clear-releases-only-the-callers-record" 0 "the flag was DELETED under a live run"
  fi

  # B. ...and the file itself stays, non-zero, because someone else still holds the exemption.
  check "clear-keeps-the-file-while-others-hold-it" \
    "$([[ -f "$f" && $CLEAR_RC -ne 0 ]] && echo 1 || echo 0)" \
    "exists=$([[ -f "$f" ]] && echo yes || echo no) rc=$CLEAR_RC"
  rm -rf "$d"

  printf '\nthe property the old implementation could not reach\n'

  # C. the LAST record going takes the file with it.
  d="$(fixture "$(rec 1 run-a)" "$(rec 2 run-b)")"; f="$(flag_of "$d")"
  clear_run "$d" run-a >/dev/null
  clear_run "$d" run-b >/dev/null
  check "clear-unlinks-when-the-last-record-goes" \
    "$([[ ! -e "$f" && $CLEAR_RC -eq 0 ]] && echo 1 || echo 0)" \
    "exists=$([[ -e "$f" ]] && echo yes || echo no) rc=$CLEAR_RC"
  rm -rf "$d"

  # J. the same thing at the scale that actually broke: five overlapping runs, each clearing once,
  #    in an order none of them chose. The old code returns a file with all five records still in it.
  local n ids=()
  for n in 1 2 3 4 5; do ids+=("run-$n"); done
  d="$(fixture "$(rec 1 run-1)" "$(rec 2 run-2)" "$(rec 3 run-3)" "$(rec 4 run-4)" "$(rec 5 run-5)")"
  f="$(flag_of "$d")"
  for n in "${ids[@]}"; do clear_run "$d" "$n" >/dev/null; done
  check "clear-drains-the-file-run-by-run" \
    "$([[ ! -e "$f" ]] && echo 1 || echo 0)" \
    "$([[ -e "$f" ]] && printf 'still holding %s record(s)' "$(wc -l < "$f" | tr -d ' ')" || echo 'gone after all five released')"
  rm -rf "$d"

  printf '\nwhose record is it\n'

  # D. SUBSTRING. `nightly` must not touch `nightly-2`, and must not conclude it is alone.
  d="$(fixture "$(rec 1 nightly)" "$(rec 2 nightly-2)")"; f="$(flag_of "$d")"
  clear_run "$d" nightly >/dev/null
  if [[ -f "$f" ]]; then
    check "clear-matches-the-id-field-not-a-substring" \
      "$([[ "$(cat "$f")" == "$(rec 2 nightly-2)" ]] && echo 1 || echo 0)" "$(cat "$f" | tr '\n' ';')"
  else
    check "clear-matches-the-id-field-not-a-substring" 0 \
      "the flag was DELETED — 'nightly' swallowed 'nightly-2' and reported itself alone"
  fi
  rm -rf "$d"

  # E. REGEX. An id carrying metacharacters must be compared literally, not applied as a pattern.
  d="$(fixture "$(rec 1 'run.1')" "$(rec 2 'runX1')")"; f="$(flag_of "$d")"
  clear_run "$d" 'run.1' >/dev/null
  if [[ -f "$f" ]]; then
    check "clear-treats-the-id-as-a-literal-not-a-regex" \
      "$([[ "$(cat "$f")" == "$(rec 2 runX1)" ]] && echo 1 || echo 0)" "$(cat "$f" | tr '\n' ';')"
  else
    check "clear-treats-the-id-as-a-literal-not-a-regex" 0 \
      "the flag was DELETED — 'run.1' was applied as a pattern and matched 'runX1'"
  fi
  rm -rf "$d"

  # K. a line the parser cannot read counts as SOMEONE ELSE'S. Deleting a record you do not
  #    understand is the failure this whole command exists to avoid.
  d="$(fixture "$(rec 1 run-a)" "a hand-edited line with no delimiter")"; f="$(flag_of "$d")"
  clear_run "$d" run-a >/dev/null
  check "clear-keeps-a-record-it-cannot-parse" \
    "$([[ -f "$f" && "$(cat "$f")" == "a hand-edited line with no delimiter" ]] && echo 1 || echo 0)" \
    "$([[ -e "$f" ]] && cat "$f" | tr '\n' ';' || echo 'flag deleted')"
  rm -rf "$d"

  printf '\nthe surrounding contract, which must not drift\n'

  # G. a teardown must not push the gate's 6h TTL out. The TTL is measured from mtime, and the
  #    rewrite would otherwise refresh it every time a run finished.
  d="$(fixture "$(rec 1 run-a)" "$(rec 2 run-b)")"; f="$(flag_of "$d")"
  touch -t 202608070100 "$f"
  local before after; before="$(mtime_of "$f")"
  clear_run "$d" run-a >/dev/null
  after="$(mtime_of "$f")"
  check "clear-does-not-extend-the-6h-ttl" \
    "$([[ -f "$f" && "$before" == "$after" ]] && echo 1 || echo 0)" "mtime $before -> $after"
  rm -rf "$d"

  # F. no run-id is a REFUSAL, not a full wipe. It used to fall through to `rm -f`.
  d="$(fixture "$(rec 1 run-a)" "$(rec 2 run-b)")"; f="$(flag_of "$d")"
  clear_run "$d" >/dev/null
  check "clear-refuses-without-a-run-id" \
    "$([[ -f "$f" && $CLEAR_RC -eq 2 && "$(wc -l < "$f" | tr -d ' ')" == "2" ]] && echo 1 || echo 0)" \
    "rc=$CLEAR_RC $([[ -e "$f" ]] && printf '%s record(s) intact' "$(wc -l < "$f" | tr -d ' ')" || echo 'FILE WIPED')"
  rm -rf "$d"

  # H. --force is the deliberated escape and still takes everything. Unchanged on purpose: the fix
  #    is about making the SAFE path reachable, not about removing the unsafe one.
  d="$(fixture "$(rec 1 run-a)" "$(rec 2 run-b)")"; f="$(flag_of "$d")"
  clear_run "$d" run-a --force >/dev/null
  check "clear-force-still-unlinks-everything" \
    "$([[ ! -e "$f" && $CLEAR_RC -eq 0 ]] && echo 1 || echo 0)" \
    "exists=$([[ -e "$f" ]] && echo yes || echo no) rc=$CLEAR_RC"
  rm -rf "$d"

  # I. clearing an absent flag is a quiet success, so a teardown that runs twice is not an error.
  d="$(fixture)"; f="$(flag_of "$d")"; rm -f "$f"
  clear_run "$d" run-a >/dev/null
  check "clear-on-an-absent-flag-is-a-no-op" \
    "$([[ $CLEAR_RC -eq 0 && ! -e "$f" ]] && echo 1 || echo 0)" "rc=$CLEAR_RC"
  rm -rf "$d"
}

# ------------------------------------------------------------------------------------------------
# --self-test (E-03).
#
# The first and most important case runs a PINNED PRE-FIX COPY of `clear` and requires the cases to
# REPRODUCE the original defect. A mutation invented for the occasion proves a check can fail at
# something; a pre-fix copy proves it would have caught the thing that actually shipped.
#
# The copy is a fixture, not the live file — the live file is fixed, and a judge that reconstructs
# its own subject by patching it goes stale the moment the subject is edited again. The `clear` body
# below is the pre-0.5.2 implementation verbatim.
# ------------------------------------------------------------------------------------------------
prefix_copy() { # -> path to a script with the OLD clear semantics
  local d; d="$(mktemp -d)"
  cat > "$d/team_run_flag.sh" <<'PREFIX'
#!/usr/bin/env bash
# PRE-FIX FIXTURE — the pre-0.5.2 `clear`, kept verbatim so the judge can be shown catching the
# defect that actually shipped. Not a copy of the live script; do not "fix" it.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FLAG="$HERE/team_run_active"
CMD="${1:-status}"
RUN="${2:-}"
case "$CMD" in
  set)
    shift 2 || true
    printf '%s | %s | %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$RUN" "${*:-team run}" >> "$FLAG"
    touch "$FLAG"
    ;;
  clear)
    [ -f "$FLAG" ] || { echo "team_run_active already absent"; exit 0; }
    if [ -n "$RUN" ] && [ "${3:-}" != "--force" ]; then
      others=$(grep -v -- "$RUN" "$FLAG" || true)
      if [ -n "$others" ]; then
        echo "REFUSING to clear — another run still holds the exemption:" >&2
        echo "$others" | sed 's/^/  /' >&2
        exit 1
      fi
    fi
    rm -f "$FLAG"
    echo "team_run_active CLEARED"
    ;;
  status) [ -f "$FLAG" ] && cat "$FLAG" || echo "team_run_active ABSENT" ;;
esac
PREFIX
  printf '%s/team_run_flag.sh' "$d"
}

# Every case the pre-fix copy MUST fail. Anything outside this list it is allowed to pass — the old
# implementation was not wrong about everything, and demanding a total collapse would let a broken
# judge look convincing.
#
# This list is OBSERVED, not reasoned: the cases were first run against the real pre-fix blob
# (`git show <the commit before this fix>:standup/control/team_run_flag.sh`) and this is the verdict
# set that came back. Writing it from intuition got it wrong on the first attempt —
# `clear-does-not-extend-the-6h-ttl` was listed here, and the old code passes it, because refusing
# outright means it never rewrote the file and so never had a mtime to spoil. A bug it did not have
# is not evidence about a case, and demanding it would have made this proof unfalsifiable noise.
PREFIX_MUST_FAIL="clear-releases-only-the-callers-record
clear-unlinks-when-the-last-record-goes
clear-drains-the-file-run-by-run
clear-matches-the-id-field-not-a-substring
clear-treats-the-id-as-a-literal-not-a-regex
clear-keeps-a-record-it-cannot-parse
clear-refuses-without-a-run-id"

expect_red() { # <label> <script> <case-name>...
  local label="$1" script="$2"; shift 2
  local out name rc=0
  out="$(STANDUP_FLAG_SCRIPT="$script" bash "$0" 2>&1)"
  for name in "$@"; do
    if ! grep -qF -- "$name → FAIL" <<<"$out"; then
      printf '  %-44s → ERROR  %s stayed green\n' "$label" "$name" >&2; rc=3
    fi
  done
  [[ $rc -eq 0 ]] && printf '  %-44s → its own case(s) went RED\n' "$label"
  return $rc
}

self_test() {
  if ! bash "$0" >/dev/null 2>&1; then
    printf '!! SELF-TEST ABORTED — the unmutated judge is not green against the real script.\n' >&2
    printf '   "did it go red" proves nothing about a case that was already red. Fix that first.\n' >&2
    return 3
  fi
  printf '=== --self-test: the cases must catch the defect that shipped ===\n'
  local rc=0 p m d

  # THE pre-fix proof. Every case in PREFIX_MUST_FAIL has to reproduce red on the old code.
  p="$(prefix_copy)"
  # shellcheck disable=SC2086
  expect_red "pre-fix clear reproduces the defect" "$p" $PREFIX_MUST_FAIL || rc=3
  rm -rf "$(dirname "$p")"

  # ...and the counter-half: the old code was RIGHT about the refusal and about --force, so those
  # cases must stay GREEN on it. Without this the pre-fix run above could be passing because the
  # fixture is simply broken, which would make the whole proof worthless.
  local out
  out="$(STANDUP_FLAG_SCRIPT="$(prefix_copy)" bash "$0" 2>&1)"
  if grep -qF -- "clear-keeps-the-file-while-others-hold-it → PASS" <<<"$out" \
     && grep -qF -- "clear-force-still-unlinks-everything → PASS" <<<"$out"; then
    printf '  %-44s → still green on the pre-fix copy\n' "the refusal it got RIGHT"
  else
    printf '  %-44s → ERROR  the pre-fix fixture fails even the cases it should pass,\n' "the refusal it got RIGHT" >&2
    printf '     so "it went red" above is not evidence about these cases.\n' >&2
    rc=3
  fi

  # Targeted mutations of the FIXED script, one property each, for the cases the pre-fix copy
  # cannot speak to.
  m="$(mktemp -d)/team_run_flag.sh"; mkdir -p "$(dirname "$m")"
  sed 's/touch -r "\$FLAG" "\$tmp".*$/:/' "$SCRIPT" > "$m"
  if cmp -s "$m" "$SCRIPT"; then
    printf '  %-44s → ERROR  the mutation changed nothing\n' "dropping touch -r reopens the TTL" >&2; rc=3
  else
    expect_red "dropping touch -r reopens the TTL" "$m" "clear-does-not-extend-the-6h-ttl" || rc=3
  fi
  rm -rf "$(dirname "$m")"

  m="$(mktemp -d)/team_run_flag.sh"; mkdir -p "$(dirname "$m")"
  sed 's/f = \$2$/f = $0/' "$SCRIPT" > "$m"
  if cmp -s "$m" "$SCRIPT"; then
    printf '  %-44s → ERROR  the mutation changed nothing\n' "matching the LINE not the id field" >&2; rc=3
  else
    expect_red "matching the LINE not the id field" "$m" \
      "clear-releases-only-the-callers-record" || rc=3
  fi
  rm -rf "$(dirname "$m")"

  # CONTROL — an untouched copy of the real script must pass every case. A mutation set proves the
  # cases fire on bad input; only this proves they hold their tongue on good input.
  d="$(mktemp -d)"; cp "$SCRIPT" "$d/team_run_flag.sh"
  if STANDUP_FLAG_SCRIPT="$d/team_run_flag.sh" bash "$0" >/dev/null 2>&1; then
    printf '  %-44s → stayed GREEN\n' "control: the real script, copied"
  else
    printf '  %-44s → ERROR  the real script failed its own cases\n' "control: the real script, copied" >&2
    rc=3
  fi
  rm -rf "$d"

  [[ $rc -eq 0 ]] && printf '\n--self-test → PASS  (the pre-fix copy reproduced the shipped defect; every control stayed green)\n'
  return $rc
}

main() {
  case "${1:-}" in
    --self-test) self_test; exit $? ;;
    "") ;;
    *) printf 'usage: test_run_flag_clear.sh [--self-test]\n' >&2; exit 64 ;;
  esac
  printf 'exemption-release judge — sandboxed fixtures only; judging %s\n' "$SCRIPT"
  run_cases
  printf '\n%s\n' "$([[ $fails -eq 0 ]] && echo "all checks PASS" || echo "$fails check(s) FAILED")"
  printf 'Run --self-test to prove these cases catch the pre-0.5.2 clear (E-03).\n'
  [[ $fails -eq 0 ]] || exit 1
}

main "$@"
