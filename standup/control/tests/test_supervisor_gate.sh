#!/usr/bin/env bash
# Supervisor-gate judge.
#
#     bash standup/control/tests/test_supervisor_gate.sh
#     bash standup/control/tests/test_supervisor_gate.sh --self-test
#
# WHAT IT GUARDS. `hooks/supervisor_gate.py` is the only mechanical thing standing between "the EM
# supervises" and "the EM quietly writes the product itself". Its whole verdict comes out of one
# function, `allowed_target()`, and until now **nothing tested it at all** — not a unit test, not a
# judge, not CI. The classification it performs is also imported by other code: `verify_project.py`
# derives its management deny list from this file's three constants, so a change here silently
# changes what `/add-project` refuses.
#
# A gate with no covering case fails the same way a review apparatus pointed the wrong way does: it
# does not error, it just stops stopping things. That failure has been recorded in this project
# three times already, which is why this exists as executable cases rather than as a paragraph.
#
# It drives the REAL hook end to end — a PreToolUse payload on stdin, the exit code as the verdict
# (2 = block, 0 = allow) — rather than importing `allowed_target()` directly, so the flags, the TTLs
# and the fail-open paths are covered too, not just the string logic.
#
# Every case runs inside `mktemp -d`. Nothing here reads or writes the real tree except the hook
# itself, read-only.
#
# Exit codes: 0 pass · 1 failures · 3 the judge itself is broken · 64 usage
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
GATE="${STANDUP_SUPERVISOR_GATE:-$REPO/hooks/supervisor_gate.py}"

fails=0
check() { # name ok detail
  local name="$1" ok="$2" detail="${3:-}"
  [[ "$ok" == "1" ]] || fails=$((fails + 1))
  printf '  %s → %s%s\n' "$name" "$([[ "$ok" == 1 ]] && echo PASS || echo FAIL)" "${detail:+  $detail}"
}
section() { printf '\n%s\n' "$1"; }
die_judge() { printf '\n!! JUDGE BROKEN — %s\n' "$1" >&2; exit 3; }

[[ -f "$GATE" ]] || die_judge "supervisor_gate.py not found at $GATE"

# A team root: the hook finds it by walking up to a directory holding standup/team.json.
build_root() {
  local d; d="$(mktemp -d)"
  mkdir -p "$d/standup/control" "$d/standup/portal" "$d/standup/workflows" "$d/standup/log" \
           "$d/skills" "$d/hooks" "$d/.claude" "$d/.claude-plugin" "$d/.github" \
           "$d/evals" "$d/research" "$d/myproj"
  printf '{"teams":[],"staff":[]}\n' > "$d/standup/team.json"
  echo "$d"
}

# Feed the hook one PreToolUse payload and echo its exit code.
gate_rc() { # <root> <tool> <abs target>
  python3 -c '
import json, sys
print(json.dumps({"tool_name": sys.argv[1], "cwd": sys.argv[2],
                  "tool_input": {"file_path": sys.argv[3]}}))' "$2" "$1" "$3" \
    | python3 "$GATE" >/dev/null 2>&1
  echo $?
}

expect() { # <label> <want rc> <root> <tool> <target>
  local label="$1" want="$2" got
  got="$(gate_rc "$3" "$4" "$5")"
  check "$label" "$([[ "$got" == "$want" ]] && echo 1 || echo 0)" "rc=$got want=$want"
}

run_cases() {
  local pfx="${1:-}" d
  d="$(build_root)"

  # PROJECT territory — the EM produces none of it. Five paths, five different reasons to be one.
  section "${pfx}A. project content is BLOCKED (rc=2)"
  expect "${pfx}standup/portal/ — the portal is a product, not the control plane" 2 "$d" Edit "$d/standup/portal/app.py"
  expect "${pfx}standup/portal/parsers/ — depth does not launder it"              2 "$d" Edit "$d/standup/portal/parsers/team.py"
  expect "${pfx}evals/ — the eval suite belongs to its squad"                     2 "$d" Write "$d/evals/cases.json"
  expect "${pfx}research/ — reports are produced by the team"                     2 "$d" Write "$d/research/2026-08-07/notes.md"
  expect "${pfx}a project directory added by /add-project"                        2 "$d" Edit "$d/myproj/src/a.py"

  # MANAGEMENT / GOVERNANCE territory — this is what the EM is FOR. Six, one per allow branch.
  section "${pfx}B. management + governance content is ALLOWED (rc=0)"
  expect "${pfx}standup/team.json — the roster"                     0 "$d" Edit "$d/standup/team.json"
  expect "${pfx}standup/control/** — gates, budget, kill switch"    0 "$d" Write "$d/standup/control/budget.json"
  expect "${pfx}standup/workflows/** — the orchestration engine"    0 "$d" Edit "$d/standup/workflows/x.workflow.js"
  expect "${pfx}skills/** — the plugin's own commands"              0 "$d" Edit "$d/skills/add-team/SKILL.md"
  expect "${pfx}hooks/** — the governance framework itself"         0 "$d" Edit "$d/hooks/supervisor_gate.py"
  expect "${pfx}a top-level file in the team root"                  0 "$d" Edit "$d/README.md"

  # The two boundaries that are easy to get backwards.
  section "${pfx}C. the boundaries"
  expect "${pfx}outside the team root — not ours to police"     0 "$d" Edit "/tmp/somewhere-else/x.py"
  expect "${pfx}a non-writing tool is never gated"              0 "$d" Read "$d/standup/portal/app.py"
  # standup/ is NOT wholesale-allowed: only the four names and the three dirs are.
  expect "${pfx}an unlisted path under standup/ is blocked"     2 "$d" Edit "$d/standup/somethingelse/x.py"
  expect "${pfx}an unlisted FILE directly under standup/ is blocked" 2 "$d" Edit "$d/standup/NOTES.md"
  rm -rf "$d"

  section "${pfx}D. the two release valves — and their expiry"
  # TEAM-RUN EXEMPTION. This is not a nicety: the Task tool has no `cwd`, so every dispatched dev
  # agent inherits the EM's cwd and is classified as the EM. Without the flag a whole run writes
  # nothing and reports `review-failed` on an empty diff.
  d="$(build_root)"
  expect "${pfx}without the flag, a dispatched agent's project write is blocked" 2 "$d" Edit "$d/standup/portal/app.py"
  : > "$d/standup/control/team_run_active"
  expect "${pfx}a FRESH team_run_active lets it through"                        0 "$d" Edit "$d/standup/portal/app.py"
  # 6h TTL. `clear` is not the safety mechanism — a crashed run never reaches it — the TTL is, so
  # a stale flag must stop exempting.
  touch -t "$(python3 -c 'import time;print(time.strftime("%Y%m%d%H%M", time.localtime(time.time()-7*3600)))')" \
        "$d/standup/control/team_run_active"
  expect "${pfx}a STALE team_run_active (>6h) blocks again"                     2 "$d" Edit "$d/standup/portal/app.py"
  rm -rf "$d"

  d="$(build_root)"
  printf 'one-line hotfix: typo in a label\n' > "$d/standup/control/supervisor_override"
  expect "${pfx}a FRESH supervisor_override lets one action through"            0 "$d" Edit "$d/standup/portal/app.py"
  check "${pfx}...and it is written to the audit log" \
    "$(grep -q 'typo in a label' "$d/standup/control/hotfix_audit.log" 2>/dev/null && echo 1 || echo 0)" \
    "an unaudited hatch is indistinguishable from no gate"
  touch -t "$(python3 -c 'import time;print(time.strftime("%Y%m%d%H%M", time.localtime(time.time()-2*3600)))')" \
        "$d/standup/control/supervisor_override"
  expect "${pfx}a STALE supervisor_override (>1h) blocks again"                 2 "$d" Edit "$d/standup/portal/app.py"
  rm -rf "$d"

  section "${pfx}E. it fails OPEN, never closed"
  # A hook that dies closed would brick every session in the folder. Malformed stdin must allow.
  d="$(build_root)"
  check "${pfx}malformed stdin is allowed, not blocked" \
    "$(printf 'not json' | python3 "$GATE" >/dev/null 2>&1; [[ $? -eq 0 ]] && echo 1 || echo 0)"
  check "${pfx}a session outside any team root is allowed" \
    "$(python3 -c '
import json,sys
print(json.dumps({"tool_name":"Edit","cwd":sys.argv[1],
                  "tool_input":{"file_path":sys.argv[1]+"/x.py"}}))' "$(mktemp -d)" \
      | python3 "$GATE" >/dev/null 2>&1; [[ $? -eq 0 ]] && echo 1 || echo 0)"
  rm -rf "$d"
}

self_test() {
  # E-03. One mutation per allow/block branch, each required to redden its OWN named case — a
  # single "break everything" mutation proves only that something fires.
  local muts=(
    "plugin-dirs|if parts[0] in PLUGIN_DIRS:|if False:|skills/** — the plugin's own commands"
    "standup-default-deny|        return False                         # standup/portal/ and any other standup/ path|        return True|standup/portal/ — the portal is a product, not the control plane"
    "toplevel-file|    if len(parts) == 1:|    if False:|a top-level file in the team root"
    "outside-root|    if rel.startswith(\"..\"):|    if False:|outside the team root — not ours to police"
    "standup-allow-dirs|        if sub in STANDUP_ALLOW_DIRS:|        if False:|standup/control/** — gates, budget, kill switch"
    "standup-allow-files|        if len(parts) == 2 and sub in STANDUP_ALLOW_FILES:|        if False:|standup/team.json — the roster"
    "teamrun-flag|    if _mtime_fresh(os.path.join(control_dir, \"team_run_active\"), TEAM_RUN_TTL):|    if False:|a FRESH team_run_active lets it through"
    "teamrun-ttl|TEAM_RUN_TTL = 6 * 3600|TEAM_RUN_TTL = 6 * 3600 * 1000|a STALE team_run_active (>6h) blocks again"
    "override-ttl|OVERRIDE_TTL = 3600|OVERRIDE_TTL = 3600 * 1000|a STALE supervisor_override (>1h) blocks again"
    "override-audit|        audit(control_dir, tool, target, reason)|        pass|...and it is written to the audit log"
    "gated-tools|    if tool not in (\"Edit\", \"Write\", \"NotebookEdit\"):|    if tool not in (\"Edit\", \"Write\", \"NotebookEdit\", \"Read\"):|a non-writing tool is never gated"
  )
  if ! bash "$0" >/dev/null 2>&1; then
    printf '!! SELF-TEST ABORTED — the unmutated suite is not green.\n' >&2
    printf '   Every check below asks "did this case go red"; a case already red answers yes for\n' >&2
    printf '   the wrong reason. Fix the baseline first.\n' >&2
    return 3
  fi
  printf '=== --self-test: neutralise ONE gate branch at a time ===\n'
  local rc=0 m name from to want d out
  for m in "${muts[@]}"; do
    IFS='|' read -r name from to want <<<"$m"
    grep -qF "$from" "$GATE" || die_judge "self-test anchor not found in supervisor_gate.py: $from
      Re-anchor it or delete the fixture — a mutation that silently no-ops reads as a pass."
    d="$(mktemp -d)"
    python3 - "$GATE" "$d/mutated.py" "$from" "$to" <<'PY'
import sys
src = open(sys.argv[1], encoding="utf-8").read()
assert sys.argv[3] in src
open(sys.argv[2], "w", encoding="utf-8").write(src.replace(sys.argv[3], sys.argv[4], 1))
PY
    out="$(STANDUP_SUPERVISOR_GATE="$d/mutated.py" bash "$0" 2>&1)"
    if grep -qF "  $want → FAIL" <<<"$out"; then
      printf '  %-46s → correctly went RED\n' "$name"
    else
      printf '  %-46s → ERROR  its own case stayed green\n' "$name" >&2
      printf '     want red: %s\n' "$want" >&2
      rc=3
    fi
    rm -rf "$d"
  done
  [[ $rc -eq 0 ]] && printf '\n--self-test → PASS  (%s gate branch(es) neutralised; each drove its OWN named case red)\n' "${#muts[@]}"
  return $rc
}

main() {
  case "${1:-}" in
    --self-test) self_test; exit $? ;;
    "") ;;
    *) printf 'usage: test_supervisor_gate.sh [--self-test]\n' >&2; exit 64 ;;
  esac
  printf 'supervisor-gate judge — fixtures only; drives the real hook end to end\n'
  run_cases
  printf '\n%s\n' "$([[ $fails -eq 0 ]] && echo "all checks PASS" || echo "$fails check(s) FAILED")"
  printf 'Run --self-test to prove each gate branch can fail on its own (E-03).\n'
  [[ $fails -eq 0 ]] || exit 1
}

main "$@"
