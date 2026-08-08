#!/usr/bin/env bash
# Clock-independence judge — "does this suite pass at every hour, or only at convenient ones?"
#
#     bash standup/control/tests/test_clock_independence.sh
#     bash standup/control/tests/test_clock_independence.sh --self-test
#
# WHAT IT GUARDS. `portal/parsers/actions.py` rule (4) refuses a launch when the next SCHEDULED
# tick is less than IMMINENT_TICK_S away, and it reads the real local WALL CLOCK through
# `liveness.next_tick()` over a hardcoded tick table. `portal/tests/test_actions.py` exercises that
# code path on every test that POSTs an action — so for four ten-minute windows a day, 18 of its
# tests returned `409 tick_imminent` where they expected 202, and the README tells people to run
# exactly that suite. The gate was not flaky: every failing test asserted a launch is ALLOWED and
# every surviving one asserted it is BLOCKED. It was stuck closed.
#
# A suite that is red 40 minutes a day is worse than one that is red always. Always-red gets fixed
# on the first run; sometimes-red teaches a new user that the failures are normal, which is the
# habit that lets a real regression through.
#
# HOW IT TESTS THAT. It replays the whole portal action suite under pure POSIX TZ strings — no
# libfaketime, no source edits, nothing installed — at a probe INSIDE each tick window plus two
# ordinary times, and fails if any run is red. The times are DERIVED from `liveness.TICKS` and
# `actions.IMMINENT_TICK_S` at run time, never transcribed, so editing the tick table moves the
# probes with it instead of quietly making them meaningless.
#
# EVERY CASE PROVES THE SHIFT LANDED FIRST, and it proves it in PYTHON rather than with `date`.
# The code under test reads `datetime.datetime.now()`; a platform where the TZ string reached the
# shell but not the interpreter would otherwise produce a green run that tested nothing, which is
# the same shape of failure as the bug being judged.
#
# WHY THE 600-SECOND BOUNDARY IS NOT PROBED THROUGH THE WALL CLOCK. It cannot be, honestly: a
# T-601s probe drifts into T-599s within two seconds of the suite starting, so a green result would
# mean "the run was fast", not "the boundary is right". The boundary is pinned deterministically
# instead, by `test_imminent_window_is_exactly_bounded_against_the_real_tick_table`, which composes
# the real `next_tick()` with the real `guard()` at T-601/600/599/1s around EVERY entry in the tick
# table with an injected `now`. That test is inside the file this judge replays, so the exact
# boundary is re-checked at every clock below rather than at none of them.
#
# NOTHING HERE TOUCHES THE REAL TREE. Each run works on a copy of `standup/portal` +
# `standup/control` under `mktemp -d`, which is also what makes the self-test's mutation safe.
#
# --self-test IS THE PROOF OF TEETH (DESIGN_RULEBOOK E-03). It removes the fixture's tick pin from
# the COPY — reconstructing the pre-fix file rather than storing a transcription of it, which
# cannot drift out of date — and then requires the four window cases to go RED **and** the two
# ordinary-clock cases to stay GREEN. The green half is load-bearing: it separates "the judge
# caught the clock bug" from "the mutation broke the file for some unrelated reason". A mutation
# whose anchor no longer matches the source is a HARD ERROR (exit 3), never a skip — a mutation
# that silently no-ops reads as a pass.
#
# Exit codes: 0 pass · 1 failures · 3 the judge itself is broken · 64 usage
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
STANDUP_DIR="$REPO/standup"
PORTAL="$STANDUP_DIR/portal"
SUITE_REL="tests/test_actions.py"
PYBIN="${STANDUP_PY:-$STANDUP_DIR/.venv/bin/python}"

fails=0
FAILED_NAMES=""
check() { # name ok detail
  local name="$1" ok="$2" detail="${3:-}"
  if [[ "$ok" != "1" ]]; then fails=$((fails + 1)); FAILED_NAMES="$FAILED_NAMES
$name"; fi
  printf '  %s → %s%s\n' "$name" "$([[ "$ok" == 1 ]] && echo PASS || echo FAIL)" "${detail:+  $detail}"
}
section() { printf '\n%s\n' "$1"; }
die_judge() { printf '\n!! JUDGE BROKEN — %s\n' "$1" >&2; exit 3; }

# ---------------------------------------------------------------------------
# Preconditions. Each of these, left unchecked, is a way for this judge to
# report green while testing nothing.
# ---------------------------------------------------------------------------
[[ -f "$PORTAL/$SUITE_REL" ]] || die_judge "no suite at $PORTAL/$SUITE_REL"
[[ -x "$PYBIN" ]] || PYBIN="$(command -v python3 || true)"
[[ -n "$PYBIN" && -x "$PYBIN" ]] || die_judge "no usable python (set STANDUP_PY)"
"$PYBIN" -c 'import pytest, fastapi' 2>/dev/null \
  || die_judge "$PYBIN cannot import pytest+fastapi — run ./setup.sh, or set STANDUP_PY to the venv python"

# ---------------------------------------------------------------------------
# The probe times, DERIVED from the code under test. Emits one "HH:MM label"
# line per probe: one inside each tick's imminent window, plus two clocks that
# are far from every tick.
# ---------------------------------------------------------------------------
PROBES="$(PYTHONPATH="$PORTAL" "$PYBIN" - <<'PY'
import datetime, sys
from parsers import actions, liveness

W = actions.IMMINENT_TICK_S
ticks = list(liveness.TICKS)
if not ticks or W <= 120:
    sys.exit("tick table empty or window too narrow to probe safely")

base = datetime.datetime(2026, 6, 20)
out = []
# INSIDE each window: half a window before the tick, so the probe has ~W/2 of slack on
# both sides and a slow suite cannot drift out of the window it is meant to be in.
for name, hh, mm in ticks:
    t = base.replace(hour=hh, minute=mm) - datetime.timedelta(seconds=W // 2)
    out.append(f"{t:%H:%M} in-window:{name}")

# FAR from every tick: scan the day and take the two minutes with the largest distance
# to any tick boundary. Derived, so a changed tick table cannot leave a "safe" probe
# sitting inside a window.
def dist(minute):
    d = []
    for _n, hh, mm in ticks:
        tm = hh * 60 + mm
        d.append(min((minute - tm) % 1440, (tm - minute) % 1440))
    return min(d)

ranked = sorted(range(1440), key=lambda m: -dist(m))
picked = []
for m in ranked:
    if all(min((m - p) % 1440, (p - m) % 1440) > 60 for p in picked):
        picked.append(m)
    if len(picked) == 2:
        break
for m in picked:
    out.append(f"{m // 60:02d}:{m % 60:02d} ordinary")
print("\n".join(out))
PY
)" || die_judge "could not derive probe times from liveness.TICKS / actions.IMMINENT_TICK_S"
[[ -n "$PROBES" ]] || die_judge "derived an empty probe list"

# POSIX TZ string that puts local time at HH:MM right now. Computed immediately before
# each run so the drift between computing it and reading it is a second or two.
tz_for() { # HH MM
  "$PYBIN" - "$1" "$2" <<'PY'
import datetime, sys
hh, mm = int(sys.argv[1]), int(sys.argv[2])
u = datetime.datetime.utcnow()
d = int((u - u.replace(hour=hh, minute=mm, second=0, microsecond=0)).total_seconds())
s = "-" if d < 0 else "+"
d = abs(d)
print(f"XXX{s}{d // 3600}:{(d % 3600) // 60:02d}:{d % 60:02d}")
PY
}

# ---------------------------------------------------------------------------
# One throwaway copy of the tree per invocation. `parents[2]` inside the suite
# resolves to <copy>/standup, so portal/ and control/ must keep their layout.
# ---------------------------------------------------------------------------
make_copy() { # -> prints the portal dir inside a fresh temp tree
  local tmp; tmp="$(mktemp -d)" || return 1
  mkdir -p "$tmp/standup" || return 1
  # cp -R then prune caches; --exclude is not portable to BSD cp.
  cp -R "$PORTAL" "$tmp/standup/portal" || return 1
  cp -R "$STANDUP_DIR/control" "$tmp/standup/control" || return 1
  [[ -f "$STANDUP_DIR/team.json" ]] && cp "$STANDUP_DIR/team.json" "$tmp/standup/team.json"
  find "$tmp" -type d \( -name __pycache__ -o -name .pytest_cache \) -prune -exec rm -rf {} + 2>/dev/null
  printf '%s' "$tmp"
}

# Removes the fixture's tick pin, reconstructing the file as it stood before the fix.
unpin() { # <portal dir>
  "$PYBIN" - "$1/$SUITE_REL" <<'PY'
import pathlib, sys
p = pathlib.Path(sys.argv[1])
src = p.read_text(encoding="utf-8")
START = "    # --- PIN THE TICK SCHEDULE:"
END = "    client = TestClient(app_module.app)"
if src.count(START) != 1 or src.count(END) != 1:
    sys.exit(f"ANCHOR: START x{src.count(START)}, END x{src.count(END)} (want 1 and 1)")
i, j = src.index(START), src.index(END)
if not i < j:
    sys.exit("ANCHOR: the pin block does not precede the TestClient construction")
p.write_text(src[:i] + src[j:], encoding="utf-8")
PY
}

# Runs the suite at one clock. Echoes: <observed HH:MM> <exit> <pytest tail>
run_at() { # <portal dir> <HH> <MM>
  local dir="$1" hh="$2" mm="$3" tz seen out rc
  tz="$(tz_for "$hh" "$mm")" || return 1
  # Prove the shift reached PYTHON — the interpreter the code under test runs in.
  seen="$(TZ="$tz" "$PYBIN" -c 'import datetime;print(datetime.datetime.now().strftime("%H:%M"))')" || return 1
  out="$(cd "$dir" && TZ="$tz" "$PYBIN" -m pytest "$SUITE_REL" -q --no-header -p no:cacheprovider 2>&1)"
  rc=$?
  printf '%s\t%s\t%s\n' "$seen" "$rc" "$(printf '%s' "$out" | grep -E '[0-9]+ (passed|failed)' | tail -1)"
}

drift_ok() { # <observed HH:MM> <target HH> <target MM>  -- within 2 minutes
  "$PYBIN" - "$1" "$2" "$3" <<'PY'
import sys
o = sys.argv[1].split(":")
obs = int(o[0]) * 60 + int(o[1])
want = int(sys.argv[2]) * 60 + int(sys.argv[3])
d = min((obs - want) % 1440, (want - obs) % 1440)
sys.exit(0 if d <= 2 else 1)
PY
}

run_cases() { # <mode: fixed|unpinned> <prefix>
  local mode="$1" prefix="${2:-}" dir
  dir="$(make_copy)" || die_judge "could not build a throwaway copy"
  if [[ "$mode" == "unpinned" ]]; then
    local err
    err="$(unpin "$dir/standup/portal" 2>&1)" || { rm -rf "$dir"; die_judge "mutation failed: $err"; }
  fi
  local portal="$dir/standup/portal"

  while read -r hhmm label; do
    [[ -n "$hhmm" ]] || continue
    local hh="${hhmm%%:*}" mm="${hhmm##*:}" res seen rc tail
    res="$(run_at "$portal" "$hh" "$mm")" || { rm -rf "$dir"; die_judge "could not run the suite at $hhmm"; }
    seen="$(cut -f1 <<<"$res")"; rc="$(cut -f2 <<<"$res")"; tail="$(cut -f3 <<<"$res")"
    if ! drift_ok "$seen" "$hh" "$mm"; then
      rm -rf "$dir"
      die_judge "TZ shift did not land in python: asked for $hhmm, interpreter reported $seen"
    fi
    check "${prefix}the suite is green at $hhmm ($label)" \
      "$([[ "$rc" == "0" ]] && echo 1 || echo 0)" "clock=$seen · $tail"
  done <<<"$PROBES"

  rm -rf "$dir"
}

self_test() {
  printf '=== --self-test: remove the fixture tick pin (reconstruct the pre-fix file) ===\n'
  printf 'The window cases must go red BY NAME, and the ordinary-clock cases must stay green —\n'
  printf 'a mutation that reds everything has not proven anything about the clock.\n'
  fails=0; FAILED_NAMES=""
  run_cases "unpinned" "[unpinned] "

  local missed="" spurious="" line name
  while read -r hhmm label; do
    [[ -n "$hhmm" ]] || continue
    name="[unpinned] the suite is green at $hhmm ($label)"
    if [[ "$label" == in-window:* ]]; then
      grep -qxF "$name" <<<"$FAILED_NAMES" || missed="$missed
    did not go red: $name"
    else
      grep -qxF "$name" <<<"$FAILED_NAMES" && spurious="$spurious
    went red without a tick nearby: $name"
    fi
  done <<<"$PROBES"

  if [[ -n "$missed" || -n "$spurious" ]]; then
    printf '\n!! SELF-TEST FAILED — the tick pin was removed and this judge did not respond to it\n' >&2
    printf '   the way the defect it exists for would:%s%s\n' "$missed" "$spurious" >&2
    return 3
  fi
  printf '\n--self-test → PASS  (%d window case(s) red, every ordinary clock still green)\n' "$fails"
  return 0
}

main() {
  case "${1:-}" in
    --self-test) self_test; exit $? ;;
    "") ;;
    *) printf 'usage: test_clock_independence.sh [--self-test]\n' >&2; exit 64 ;;
  esac
  printf 'Clock-independence judge — throwaway copies only; the repo tree is read, never written\n'
  printf 'python: %s\n' "$PYBIN"
  section "The portal action suite, replayed across the day"
  run_cases "fixed"
  printf '\n%s\n' "$([[ $fails -eq 0 ]] && echo "all checks PASS" || echo "$fails check(s) FAILED")"
  printf 'Run --self-test to prove these cases can fail (E-03).\n'
  [[ $fails -eq 0 ]] || exit 1
}

main "$@"
