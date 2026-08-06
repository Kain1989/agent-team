#!/usr/bin/env bash
# /add-project judge.
#
#     bash standup/control/tests/test_add_project.sh
#     bash standup/control/tests/test_add_project.sh --self-test
#
# WHAT IT GUARDS. `/add-project` must land FOUR things together — clone, roster entry with a paired
# squad, a declared review_surface with a runnable `inspect`, and a .gitignore line. Any one missing
# gives you a project that looks added and fails later, in a different place, with an error about
# something else. The .gitignore one is the nastiest: it breaks nothing until someone runs
# `git add -A`, at which point the clone is recorded as a GITLINK (mode 160000) and it reads as a
# git problem rather than an onboarding one.
#
# WHAT IT DOES NOT GUARD, said plainly. `/add-project` is a PROMPT; this judge cannot run it. What it
# judges is `standup/control/verify_project.py` — the checker that prompt calls at its final step and
# that CI runs. So this proves the invariants are CHECKABLE and that the checker has teeth on every
# branch; it does not prove a model follows the prompt. The terminal output of the command is judged
# by a human walkthrough, which is the right instrument for that half.
#
# Every case runs inside `mktemp -d`. Nothing here reads or writes the real repo except the checker
# script itself.
#
# Exit codes: 0 pass · 1 failures · 3 the judge itself is broken · 64 usage
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
VERIFY="${STANDUP_VERIFY_PROJECT:-$REPO/standup/control/verify_project.py}"

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

[[ -f "$VERIFY" ]] || die_judge "verify_project.py not found at $VERIFY"

GIT_ID=(-c user.name=t -c user.email=t@t -c commit.gpgsign=false)

# A team project root with one existing squad, plus an "upstream" repo to clone.
build_project() { # -> echoes the root
  local d; d="$(mktemp -d)"
  mkdir -p "$d/proj/standup"
  cat > "$d/proj/standup/team.json" <<'JSON'
{
  "teams": [
    {
      "id": "portal",
      "name": "Team Portal Squad",
      "review_surface": { "kind": "web", "label": "Mission Control", "inspect": "curl -sS -f http://127.0.0.1:8770/healthz" },
      "developers": [
        { "id": "portal_backend", "folder": "standup/portal", "active": true, "pair": "portal_frontend" },
        { "id": "portal_frontend", "folder": "standup/portal", "active": true, "pair": "portal_backend" }
      ]
    }
  ],
  "staff": []
}
JSON
  printf '# venv\nstandup/.venv/\n' > "$d/proj/.gitignore"
  git -C "$d/proj" init -q -b main
  git -C "$d/proj" add -A && git -C "$d/proj" "${GIT_ID[@]}" commit -q -m init
  # the repo the user asks us to clone
  mkdir -p "$d/upstream"
  git -C "$d/upstream" init -q -b main
  printf 'print("hi")\n' > "$d/upstream/app.py"
  git -C "$d/upstream" add -A && git -C "$d/upstream" "${GIT_ID[@]}" commit -q -m init
  echo "$d"
}

# Apply what the skill instructs, as a script, so the invariants have something to check.
# `skip` neutralises exactly one step — this is how each branch of the checker gets its own case.
apply_add() { # <sandbox> <name> <skip: none|clone|squad|surface|inspect|gitignore|pair|folder>
  local d="$1" name="$2" skip="${3:-none}" root="$d/proj"
  [[ "$skip" == "clone" ]] || git -C "$root" clone -q "$d/upstream" "$name"
  [[ "$skip" == "gitignore" ]] || printf '/%s/\n' "$name" >> "$root/.gitignore"
  [[ "$skip" == "squad" ]] && return 0
  SKIP="$skip" NAME="$name" python3 - "$root/standup/team.json" <<'PY'
import json, os, sys
p = sys.argv[1]; name = os.environ["NAME"]; skip = os.environ["SKIP"]
d = json.load(open(p))
surface = {"kind": "cli", "label": "%s test suite" % name,
           "inspect": "cd %s && python3 -m pytest -q" % name, "how": "from the project root"}
if skip == "surface":
    surface = None
elif skip == "inspect":
    surface["inspect"] = ""
devs = [
    {"id": name + "_a", "folder": name, "role": "Builder",  "active": True, "pair": name + "_b"},
    {"id": name + "_b", "folder": name, "role": "Reviewer", "active": True, "pair": name + "_a"},
]
if skip == "pair":
    devs = devs[:1]
if skip == "folder":
    for x in devs:
        x["folder"] = "somewhere-else"
squad = {"id": name, "name": name.title(), "developers": devs}
if surface is not None:
    squad["review_surface"] = surface
d["teams"].append(squad)
json.dump(d, open(p, "w"), indent=2)
PY
}

verify() { # <root> <name> -> LAST_OUT / LAST_RC
  LAST_OUT="$(python3 "$VERIFY" added "$2" --root "$1" 2>&1)"; LAST_RC=$?
}

run_cases() { # <label-prefix>
  local pfx="${1:-}" d

  section "${pfx}A. all four steps applied — the project is usable"
  d="$(build_project)"; apply_add "$d" "myapp" none; verify "$d/proj" myapp
  check "${pfx}verifier accepts a correctly added project" \
    "$([[ $LAST_RC -eq 0 ]] && echo 1 || echo 0)" "exit=$LAST_RC"
  check "${pfx}the clone landed as a real git repo" \
    "$([[ -d "$d/proj/myapp/.git" ]] && echo 1 || echo 0)"
  check "${pfx}team.json still parses after the edit" \
    "$(python3 -c 'import json,sys;json.load(open(sys.argv[1]))' "$d/proj/standup/team.json" \
       >/dev/null 2>&1 && echo 1 || echo 0)"
  # The gitlink invariant, exercised for real rather than asserted.
  git -C "$d/proj" add -A >/dev/null 2>&1
  check "${pfx}git add -A does NOT stage the clone as a gitlink" \
    "$(git -C "$d/proj" ls-files -s 2>/dev/null | awk '$1=="160000"{print}' | grep -q . && echo 0 || echo 1)" \
    "mode 160000 = a pointer to a commit nobody else can fetch"
  check "${pfx}and the clone is not staged at all" \
    "$(git -C "$d/proj" diff --cached --name-only 2>/dev/null | grep -q '^myapp' && echo 0 || echo 1)"
  rm -rf "$d"

  # ---- one case per checker branch. Each neutralises exactly ONE step. ----
  # Written this way on purpose: three times in this project a fixture set has been shipped where a
  # branch had no independent covering case, so deleting that branch alone left every check green.
  section "${pfx}B. each missing step is caught INDEPENDENTLY"

  d="$(build_project)"; apply_add "$d" "myapp" gitignore; verify "$d/proj" myapp
  check "${pfx}missing .gitignore line is caught" \
    "$([[ $LAST_RC -eq 1 ]] && grep -q 'gitignore ignores the cloned repo' <<<"$LAST_OUT" && echo 1 || echo 0)" \
    "exit=$LAST_RC"
  git -C "$d/proj" add -A >/dev/null 2>&1
  check "${pfx}...and WITHOUT it git really does create a gitlink" \
    "$(git -C "$d/proj" ls-files -s 2>/dev/null | awk '$1=="160000"{print $4}' | grep -q '^myapp$' && echo 1 || echo 0)" \
    "this is the measured consequence, not a precaution"
  verify "$d/proj" myapp
  # Grep the FAIL line, not the phrase: the ok line reads "no embedded repo is staged as a
  # gitlink" and contains the same words, so a loose match passed whether the check fired or not.
  check "${pfx}...and the verifier reports the gitlink too" \
    "$(grep -q 'FAIL no embedded repo is staged as a gitlink' <<<"$LAST_OUT" && echo 1 || echo 0)"
  rm -rf "$d"

  d="$(build_project)"; apply_add "$d" "myapp" squad; verify "$d/proj" myapp
  check "${pfx}missing roster entry is caught" \
    "$([[ $LAST_RC -eq 1 ]] && grep -q 'roster has a squad with this id' <<<"$LAST_OUT" && echo 1 || echo 0)"
  check "${pfx}...and it lists the squads that DO exist" \
    "$(grep -q 'existing: portal' <<<"$LAST_OUT" && echo 1 || echo 0)"
  rm -rf "$d"

  d="$(build_project)"; apply_add "$d" "myapp" surface; verify "$d/proj" myapp
  check "${pfx}missing review_surface is caught" \
    "$([[ $LAST_RC -eq 1 ]] && grep -q 'declares a review_surface' <<<"$LAST_OUT" && echo 1 || echo 0)"
  rm -rf "$d"

  d="$(build_project)"; apply_add "$d" "myapp" inspect; verify "$d/proj" myapp
  check "${pfx}blank inspect is caught (kind alone is not enough)" \
    "$([[ $LAST_RC -eq 1 ]] && grep -q 'inspect is runnable' <<<"$LAST_OUT" && echo 1 || echo 0)"
  rm -rf "$d"

  d="$(build_project)"; apply_add "$d" "myapp" pair; verify "$d/proj" myapp
  check "${pfx}a LONE developer is caught (no fresh-context critic)" \
    "$([[ $LAST_RC -eq 1 ]] && grep -q 'pair of active developers' <<<"$LAST_OUT" && echo 1 || echo 0)"
  rm -rf "$d"

  d="$(build_project)"; apply_add "$d" "myapp" folder; verify "$d/proj" myapp
  check "${pfx}a wrong developer folder is caught" \
    "$([[ $LAST_RC -eq 1 ]] && grep -q "folder\` is the project directory" <<<"$LAST_OUT" && echo 1 || echo 0)"
  rm -rf "$d"

  d="$(build_project)"; apply_add "$d" "myapp" clone; verify "$d/proj" myapp
  check "${pfx}a failed clone is caught" \
    "$([[ $LAST_RC -eq 1 ]] && grep -q 'project directory exists' <<<"$LAST_OUT" && echo 1 || echo 0)"
  rm -rf "$d"

  section "${pfx}C. an unparseable roster is exit 2, not a quiet invariant failure"
  d="$(build_project)"; apply_add "$d" "myapp" none
  printf '{ broken' > "$d/proj/standup/team.json"
  verify "$d/proj" myapp
  check "${pfx}unparseable team.json exits 2" \
    "$([[ $LAST_RC -eq 2 ]] && echo 1 || echo 0)" "exit=$LAST_RC — not 1: nothing can be checked at all"
  rm -rf "$d"
}

self_test() {
  # The mutation is on the CHECKER, one branch at a time, and each must drive its OWN case red.
  # A single "break everything" mutation would prove only that something fires.
  local muts=(
    # NOTE the anchor is the WHOLE condition, not the `want` assignment. Mutating `want` alone
    # no-ops: the two fallback comparisons (`name`, `name + "/"`) still catch the missing line, so
    # the case stayed green and the fixture proved nothing. Fourth time in this project that a
    # mutation has silently no-opped — the self-test is what keeps catching it.
    "gitignore|if want not in lines and name not in lines and (\"%s/\" % name) not in lines:|if False:|missing .gitignore line is caught"
    "gitlink|elif any(p == name or p.startswith(name + \"/\") for p in links):|elif False:|...and the verifier reports the gitlink too"
    "folder|if wrong:|if False:|a wrong developer folder is caught"
    "clone|if not os.path.isdir(proj):|if False:|a failed clone is caught"
    "surface|if not isinstance(surface, dict):|if False:|missing review_surface is caught"
    "inspect|if kind != \"none\" and not str(surface.get(\"inspect\") or \"\").strip():|if False:|blank inspect is caught (kind alone is not enough)"
    "pair|if len(devs) < 2:|if False:|a LONE developer is caught (no fresh-context critic)"
  )
  printf '=== --self-test: neutralise ONE checker branch at a time ===\n'
  local d rc=0 m name from to want out
  for m in "${muts[@]}"; do
    IFS='|' read -r name from to want <<<"$m"
    d="$(mktemp -d)"
    grep -qF "$from" "$VERIFY" || die_judge "self-test anchor not found in verify_project.py: $from
      Re-anchor it or delete the fixture — a mutation that silently no-ops reads as a pass."
    python3 - "$VERIFY" "$d/mutated.py" "$from" "$to" <<'PY'
import sys
src = open(sys.argv[1], encoding="utf-8").read()
assert sys.argv[3] in src
open(sys.argv[2], "w", encoding="utf-8").write(src.replace(sys.argv[3], sys.argv[4], 1))
PY
    fails=0; FAILED_NAMES=""
    out="$(STANDUP_VERIFY_PROJECT="$d/mutated.py" bash "$0" 2>&1)"
    if grep -qF "  $want → FAIL" <<<"$out"; then
      printf '  %-46s → correctly went RED\n' "$name"
    else
      printf '  %-46s → ERROR  its own case stayed green\n' "$name" >&2
      printf '      want red: %s\n' "$want" >&2
      rc=3
    fi
    rm -rf "$d"
  done
  [[ $rc -eq 0 ]] && printf '\n--self-test → PASS  (every checker branch has an independent covering case)\n'
  return $rc
}

main() {
  case "${1:-}" in
    --self-test) self_test; exit $? ;;
    "") ;;
    *) printf 'usage: test_add_project.sh [--self-test]\n' >&2; exit 64 ;;
  esac
  printf '/add-project judge — fixtures only; judges verify_project.py, not the prompt\n'
  run_cases
  printf '\n%s\n' "$([[ $fails -eq 0 ]] && echo "all checks PASS" || echo "$fails check(s) FAILED")"
  printf 'Run --self-test to prove each checker branch can fail on its own (E-03).\n'
  [[ $fails -eq 0 ]] || exit 1
}

main "$@"
