#!/usr/bin/env bash
# Arm-path judge — "did we arm OUR install, or the neighbour's?"
#
#     bash standup/control/tests/test_arm_path.sh
#     bash standup/control/tests/test_arm_path.sh --self-test
#
# WHAT IT GUARDS. The Arm step writes `standup/control/team_run_active`, the flag that lets every
# dispatched dev agent write at all. It used to locate the helper by RELATIVE path from an inherited
# cwd, with a `mkdir -p` fallback. Two ways that goes wrong, and neither errored:
#
#   * a second agent-team tree nearby -> the relative path resolves into the NEIGHBOUR, whose helper
#     dutifully reports `team_run_active PRESENT` about the wrong repo. Observed for real: a plugin
#     probe armed the host system's control plane and left its gate off for six hours.
#   * no such tree -> `mkdir -p` CREATES the missing directory, writes there, and the old check
#     (`ls` on the path `mkdir -p` had just guaranteed) confirms it. A check that cannot fail.
#
# In both, the gate the dev agents are actually judged by stays ON, every write is blocked, the run
# ends with an empty diff, and it is reported as `review-failed` — a code-quality verdict on a
# plumbing fault.
#
# THE FIX BEING JUDGED is not "search harder". The agent resolves an absolute root by walking up for
# an anchor, and the ENGINE then asserts the armed tree's team/dev ids against the roster it was
# handed in memory. That assertion is the load-bearing half: the neighbour's helper is not lying
# when it says PRESENT, so no check the writer performs on itself can catch this. It needs a fact
# the writer never had.
#
# WHY THE DECOY IS NAMED `standup` (lowercase). The bug first surfaced through macOS case-folding,
# but CI is `ubuntu-latest` and case-SENSITIVE. A decoy at `<tmp>/STANDUP/...` would simply never
# resolve there, "the decoy is untouched" would be vacuously true, and the E-03 mutation would stay
# green on the only machine that runs this automatically — the precise trap E-03 exists for. So the
# hijack is built from LAYOUT (a sibling directory named exactly what the relative path looks for),
# which behaves identically on both platforms. Case-folding gets its own macOS-only case.
#
# Exit codes: 0 pass · 1 failures · 3 the judge itself is broken · 64 usage
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
ENGINE="${STANDUP_ENGINE_JS:-$REPO/standup/standup.workflow.js}"

fails=0
FAILED_NAMES=""
check() { # name ok detail
  local name="$1" ok="$2" detail="${3:-}"
  if [[ "$ok" != "1" ]]; then
    fails=$((fails + 1)); FAILED_NAMES="$FAILED_NAMES
$name"
  fi
  printf '  %s → %s%s\n' "$name" "$([[ "$ok" == 1 ]] && echo PASS || echo FAIL)" "${detail:+  $detail}"
}
section() { printf '\n%s\n' "$1"; }
die_judge() { printf '\n!! JUDGE BROKEN — %s\n' "$1" >&2; exit 3; }

[[ -f "$ENGINE" ]] || die_judge "engine not found at $ENGINE"

# ---------- the fixture ----------
# <tmp>/            <- cwd for the simulated agent
#   standup/        <- THE DECOY. A relative `standup/control/...` lands here.
#     control/team_run_flag.sh   (records into decoy_flag)
#     team.json                  (a DIFFERENT roster: squad "neighbour")
#     standup.workflow.js        (so it is a plausible anchor match too)
#   ours/           <- the real install, further from cwd
#     standup/{team.json,standup.workflow.js,control/team_run_flag.sh}
build_fixture() { # -> echoes the tmp dir
  local d; d="$(mktemp -d)"
  local i
  for i in "standup" "ours/standup"; do
    mkdir -p "$d/$i/control"
    cat > "$d/$i/control/team_run_flag.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FLAG="$HERE/team_run_active"
case "${1:-status}" in
  set) shift; printf '%s | %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> "$FLAG";
       echo "team_run_active SET" ;;
  status) if [ -f "$FLAG" ]; then echo "team_run_active PRESENT (gate OFF)"; cat "$FLAG";
          else echo "team_run_active ABSENT"; fi ;;
esac
SH
    echo "// anchor" > "$d/$i/standup.workflow.js"
  done
  printf '{"teams":[{"id":"neighbour","developers":[{"id":"nb_a"},{"id":"nb_b"}]}]}\n' \
    > "$d/standup/team.json"
  printf '{"teams":[{"id":"portal","developers":[{"id":"portal_backend"},{"id":"portal_frontend"}]}]}\n' \
    > "$d/ours/standup/team.json"
  echo "$d"
}

# Simulate the ARM AGENT exactly as the prompt instructs, from a given cwd, and emit the fields the
# engine will assert on. `mode=absolute` follows the shipped prompt; `mode=relative` is the
# pre-fix behaviour the mutation restores.
simulate_arm() { # <fixture> <cwd> <mode>  -> sets ARM_ROOT/ARM_TEAMS/ARM_DEVS/ARM_OK
  local d="$1" cwd="$2" mode="$3"
  ARM_ROOT=""; ARM_TEAMS=""; ARM_DEVS=""; ARM_OK=0
  if [[ "$mode" == "relative" ]]; then
    # PRE-FIX: first relative hit wins, no root resolution at all.
    if [[ -f "$cwd/standup/control/team_run_flag.sh" ]]; then
      bash "$cwd/standup/control/team_run_flag.sh" set "engine-TEST" "auto-armed" >/dev/null 2>&1
      # Capture, THEN grep. Piping into `grep -q` makes grep exit on first match, the writer takes
      # SIGPIPE, and `pipefail` turns the whole pipeline non-zero — so the check silently read
      # false on a correct arm. This judge had the exact bug it exists to catch.
      local st; st="$(bash "$cwd/standup/control/team_run_flag.sh" status 2>/dev/null || true)"
      grep -q 'team_run_active PRESENT' <<<"$st" && ARM_OK=1
      ARM_ROOT="$cwd"
    fi
    return 0
  fi
  # SHIPPED: walk up for the anchor pair, stop at the first match, use absolute paths.
  local root="" D="$cwd"
  while [[ "$D" != "/" ]]; do
    if [[ -f "$D/standup/team.json" && -f "$D/standup/standup.workflow.js" ]]; then root="$D"; break; fi
    D="$(dirname "$D")"
  done
  [[ -n "$root" ]] || return 0
  [[ -f "$root/standup/control/team_run_flag.sh" ]] || return 0
  bash "$root/standup/control/team_run_flag.sh" set "engine-TEST" "auto-armed" >/dev/null 2>&1
  local st2; st2="$(bash "$root/standup/control/team_run_flag.sh" status 2>/dev/null || true)"
  grep -q 'team_run_active PRESENT' <<<"$st2" && ARM_OK=1
  ARM_ROOT="$root"
  ARM_TEAMS="$(python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print(','.join(sorted(t['id'] for t in d.get('teams',[]) if t.get('id'))))" "$root/standup/team.json")"
  ARM_DEVS="$(python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print(','.join(sorted(x['id'] for t in d.get('teams',[]) for x in t.get('developers',[]) if x.get('id'))))" "$root/standup/team.json")"
}

# The engine has THREE verdicts, and collapsing them to accept/refuse is what hid the pre-fix
# behaviour: an arm that reports NO ids is not refused, it is recorded `unverified` and the run
# continues. That is exactly what the old relative lookup produced, so a two-valued model would
# score the pre-fix engine as "refused" and the mutation would never go red.
engine_verdict() { # <expect_teams> <expect_devs> <got_teams> <got_devs> -> accept|refuse|unverified
  if [[ -z "$1" && -z "$2" ]]; then echo unverified; return; fi
  if [[ -z "$3" && -z "$4" ]]; then echo unverified; return; fi
  if [[ "$1" == "$3" && "$2" == "$4" ]]; then echo accept; else echo refuse; fi
}

run_cases() { # <mode> <label-prefix>
  local mode="$1" pfx="${2:-}" d v
  local OURS_TEAMS="portal" OURS_DEVS="portal_backend,portal_frontend"

  section "${pfx}A. cwd sits ABOVE the install and a decoy occupies the name — the engine must REFUSE"
  # WHAT THE TWO HALVES ACTUALLY COVER — and they are DISJOINT SETS, not a layered defence. An
  # earlier draft of this comment said "resolution narrows the surface; identity closes it", which
  # is backwards for the case that was actually observed:
  #
  #   * RESOLUTION (walk up for two anchor files) is what kills the CASE-FOLDING hijack — the real
  #     one, seen on this machine, where `standup/` resolved into a differently-cased sibling.
  #   * IDENTITY (compare the armed tree's ids to the roster in memory) is what catches a
  #     CROSS-PROJECT decoy — a neighbour whose roster differs.
  #   * NEITHER catches a TWIN: two checkouts of the SAME repo. Identical rosters, so identity
  #     agrees; a valid anchor, so resolution accepts. The run arms the wrong tree and logs
  #     `verified`. That is not exotic — it is the most likely two-tree layout for a published
  #     plugin (a marketplace install beside a git clone of the same project).
  #
  # KNOWN GAP, not covered here: closing the twin case needs a RUN-SCOPED fact — a nonce written by
  # the engine and read back, or the realpath+size of the running standup.workflow.js. Deliberately
  # not built in this batch; recorded so nobody reads the checks below as covering it.
  #
  # This case therefore asserts DETECTION of the cross-project decoy, not prevention: the flag does
  # land in the neighbour before the run stops.
  d="$(build_fixture)"
  simulate_arm "$d" "$d" "$mode"
  v="$(engine_verdict "$OURS_TEAMS" "$OURS_DEVS" "$ARM_TEAMS" "$ARM_DEVS")"
  check "${pfx}the arm step reports WHICH tree it touched" \
    "$([[ -n "$ARM_TEAMS" || -n "$ARM_DEVS" ]] && echo 1 || echo 0)" \
    "reported teams=[${ARM_TEAMS}] devs=[${ARM_DEVS}] — silence here is what let this run green"
  check "${pfx}the engine REFUSES the mis-armed run" \
    "$([[ "$v" == "refuse" ]] && echo 1 || echo 0)" \
    "verdict=$v (pre-fix: 'unverified', and the run continued)"
  rm -rf "$d"

  section "${pfx}A2. cwd is INSIDE the install, decoy is a sibling — nearest anchor wins"
  d="$(build_fixture)"
  mkdir -p "$d/ours/sub/deeper"
  simulate_arm "$d" "$d/ours/sub/deeper" "$mode"
  check "${pfx}resolves to the nearest enclosing install" \
    "$([[ "$ARM_ROOT" == "$d/ours" ]] && echo 1 || echo 0)" \
    "root=${ARM_ROOT:-(unresolved)}"
  check "${pfx}the flag lands in OUR install" \
    "$([[ -f "$d/ours/standup/control/team_run_active" ]] && echo 1 || echo 0)"
  check "${pfx}the neighbour is untouched" \
    "$([[ -e "$d/standup/control/team_run_active" ]] && echo 0 || echo 1)"
  check "${pfx}the engine ACCEPTS" \
    "$([[ "$(engine_verdict "$OURS_TEAMS" "$OURS_DEVS" "$ARM_TEAMS" "$ARM_DEVS")" == "accept" ]] && echo 1 || echo 0)"
  rm -rf "$d"

  section "${pfx}B. cwd IS the install root — the ordinary case still works"
  d="$(build_fixture)"
  simulate_arm "$d" "$d/ours" "$mode"
  check "${pfx}armed, and status reported PRESENT" "$([[ $ARM_OK -eq 1 ]] && echo 1 || echo 0)"
  check "${pfx}flag in OUR install" \
    "$([[ -f "$d/ours/standup/control/team_run_active" ]] && echo 1 || echo 0)"
  check "${pfx}the engine ACCEPTS" \
    "$([[ "$(engine_verdict "$OURS_TEAMS" "$OURS_DEVS" "$ARM_TEAMS" "$ARM_DEVS")" == "accept" ]] && echo 1 || echo 0)" \
    "got teams=[${ARM_TEAMS}]"
  rm -rf "$d"

  section "${pfx}C. no install anywhere above cwd — refuse, never fabricate one"
  d="$(mktemp -d)"; mkdir -p "$d/empty"
  simulate_arm "$d" "$d/empty" "$mode"
  check "${pfx}no root resolved, nothing armed" \
    "$([[ -z "$ARM_ROOT" && $ARM_OK -eq 0 ]] && echo 1 || echo 0)" \
    "root=${ARM_ROOT:-(none)} armed=$ARM_OK"
  check "${pfx}no standup/ directory was fabricated" \
    "$([[ -e "$d/empty/standup" ]] && echo 0 || echo 1)" \
    "the old mkdir -p created exactly this, then confirmed it existed"
  rm -rf "$d"
}

# ---------- the shipped engine must actually contain the fix ----------
source_checks() {
  local src; src="$(cat "$ENGINE")"
  section "D. the shipped engine, not just this simulation"
  check "the mkdir -p fallback is gone" \
    "$(grep -q 'mkdir -p standup/control' <<<"$src" && echo 0 || echo 1)" \
    "it fabricated the very directory whose absence was the only evidence of a problem"
  check "the arm step asserts roster identity" \
    "$(grep -q 'ARM armed the WRONG install' <<<"$src" && echo 1 || echo 0)"
  check "verification keys off the flag reader, not ls" \
    "$(grep -q 'team_run_active PRESENT' <<<"$src" && echo 1 || echo 0)"
  check "teardown uses the verified absolute root" \
    "$(grep -q 'ARM_RESOLVED_ROOT' <<<"$src" && echo 1 || echo 0)"
  check "the engine never needs fs for arming" \
    "$(sed -n '/async function armTeamRunExemption/,/^}/p' <<<"$src" | grep -q "node:fs" && echo 0 || echo 1)" \
    "filesystem access has never been observed in the real host"

  # ---- the PROMPT's own resolution rule ----
  # The reviewer's Mutation B gutted the walk in the shipped prompt down to a bare
  # `if [ -d "$D/standup" ]`, re-enabling the case-folding hijack on this machine, and all five
  # commands stayed green — the simulation above tests a bash REIMPLEMENTATION of the walk, not the
  # text the agent is actually handed. These greps read the shipped prompt.
  local armprompt; armprompt="$(sed -n '/RELATIVE PATHS ARE THE HAZARD HERE/,/Do nothing else/p' <<<"$src")"
  [[ -n "$armprompt" ]] || die_judge "could not slice the Arm prompt out of the engine — re-anchor this check"
  # Grep the RESOLUTION TEST itself, not the prompt at large: both filenames also appear in the
  # reporting step, so a loose grep passed even with the walk gutted to `if [ -d "$D/standup" ]`.
  check "the prompt requires BOTH anchor files, not just a standup/ directory" \
    "$(grep -qF '[ -f "$D/standup/team.json" ] && [ -f "$D/standup/standup.workflow.js" ]' <<<"$armprompt" && echo 1 || echo 0)" \
    "a bare -d standup/ test matches any neighbour that merely has the directory"
  check "the prompt walks UP rather than trusting cwd" \
    "$(grep -q 'dirname' <<<"$armprompt" && echo 1 || echo 0)"
  # Grep the LOOP CONTROL, not the word "first". `grep -qi 'first'` matched the prose ("take the
  # FIRST directory") and also "The first printed line is team_ids", so deleting the `break` from
  # the shipped walk — which makes the OUTERMOST install win, the nested-install hijack resolution
  # exists to kill — left this PASSing at exit 0. The sibling anchor-pair check was hardened for
  # exactly this reason two lines up and this one was left loose.
  check "the prompt stops at the FIRST match (nested installs: nearest wins)" \
    "$(grep -qF 'ROOT="$D"; break' <<<"$armprompt" && echo 1 || echo 0)" \
    "without the break the OUTERMOST install wins"
  check "the prompt forbids inventing a directory when no root resolves" \
    "$(grep -qi 'do not .*mkdir\|Do not invent' <<<"$armprompt" && echo 1 || echo 0)"
  check "the prompt verifies via the flag reader, not ls" \
    "$(grep -q 'team_run_active PRESENT' <<<"$armprompt" && echo 1 || echo 0)"
  check "the prompt asks for the ids the engine will assert on" \
    "$(grep -q 'team_ids' <<<"$armprompt" && grep -q 'dev_ids' <<<"$armprompt" && echo 1 || echo 0)"
}

self_test() {
  printf '=== --self-test: restore the pre-fix RELATIVE lookup ===\n'
  printf 'Case A must go red BY NAME. Platform-independent: the decoy is a real lowercase\n'
  printf '`standup/` directory, so the hijack does not depend on case-folding.\n'
  fails=0; FAILED_NAMES=""
  run_cases "relative" "[mutated] "
  local missed="" want
  for want in "[mutated] the arm step reports WHICH tree it touched" \
              "[mutated] the engine REFUSES the mis-armed run" \
              "[mutated] resolves to the nearest enclosing install"; do
    grep -qxF "$want" <<<"$FAILED_NAMES" || missed="$missed
    did not go red: $want"
  done
  if [[ -n "$missed" ]]; then
    printf '\n!! SELF-TEST FAILED — the relative lookup was restored and the checks that exist to\n' >&2
    printf '   catch it stayed green:%s\n' "$missed" >&2
    return 3
  fi
  printf '\n--self-test → PASS  (the hijack was caught by name; %d check(s) red)\n' "$fails"
  return 0
}

main() {
  case "${1:-}" in
    --self-test) self_test; exit $? ;;
    "") ;;
    *) printf 'usage: test_arm_path.sh [--self-test]\n' >&2; exit 64 ;;
  esac
  printf 'Arm-path judge — fixtures only; the repo tree is read, never written\n'
  run_cases "absolute"
  source_checks
  printf '\n%s\n' "$([[ $fails -eq 0 ]] && echo "all checks PASS" || echo "$fails check(s) FAILED")"
  printf 'Run --self-test to prove case A can fail (E-03).\n'
  [[ $fails -eq 0 ]] || exit 1
}

main "$@"
