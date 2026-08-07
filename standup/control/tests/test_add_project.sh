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
# judges is `standup/control/verify_project.py` — the checker the prompt runs at its final step and
# that CI runs. (That wiring was MISSING when this judge first shipped: the comment here asserted it
# as fact while no skill file mentioned the script. The checker was unreachable from the product
# path, so the headline gitlink invariant was still enforced only by a sentence in a prompt — which
# is the thing verify_project.py's own docstring says a prompt cannot do.) So this proves the invariants are CHECKABLE and that the checker has teeth on every
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
  # The REAL gate file, copied read-only: the deny list is DERIVED from its constants at runtime,
  # so a fixture with a stub would be testing a stub. If the real constants change, this judge
  # changes with them — which is the point of deriving rather than transcribing.
  mkdir -p "$d/proj/hooks" && cp "$REPO/hooks/supervisor_gate.py" "$d/proj/hooks/"
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
  # `nogit`: the directory exists but is not a repo — a copied folder, or a clone that half-failed.
  [[ "$skip" == "nogit" ]] && { mkdir -p "$root/$name"; rm -rf "$root/$name/.git"; }
  # `unborn`: a repo with no commit. Everything in it is untracked, so `git diff` shows nothing.
  [[ "$skip" == "unborn" ]] && { rm -rf "$root/$name"; mkdir -p "$root/$name";
                                 git -C "$root/$name" init -q -b main; echo x > "$root/$name/a.py"; }
  # `noorigin`: a proper repo with a baseline commit but no remote — what `new`/`adopt` produce
  # before step 1c. The portal's code-task flow cannot build a worktree without one.
  [[ "$skip" == "noorigin" ]] && { git -C "$root/$name" remote remove origin >/dev/null 2>&1 || true; }
  # `symlink`: the target is a link to a directory. `/name/` will not ignore it and `git add -A`
  # stages it as mode 120000.
  [[ "$skip" == "symlink" ]] && { rm -rf "$root/$name"; mkdir -p "$d/real"; echo y > "$d/real/b.py";
                                  ln -s "$d/real" "$root/$name"; }
  # `worktree`: a linked worktree — its `.git` is a FILE, which the old isdir() test called "not a
  # repo". git handles it perfectly; the toplevel test must accept it.
  [[ "$skip" == "worktree" ]] && { rm -rf "$root/$name";
                                   git -C "$d/upstream" worktree add -q "$root/$name" -b adopted 2>/dev/null; }
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
if skip == "badpair":
    devs[0]["pair"] = "somebody_else"          # resolves to nobody in this squad
if skip == "badkind":
    surface["kind"] = "webb"                   # the typo this check exists to stop
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
    "$(grep -q 'FAIL the project is not staged as a pointer' <<<"$LAST_OUT" && echo 1 || echo 0)"
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

  d="$(build_project)"; apply_add "$d" "myapp" nogit; verify "$d/proj" myapp
  check "${pfx}a directory that is not a git repo is caught" \
    "$([[ $LAST_RC -eq 1 ]] && grep -q 'FAIL the project is its own git repo' <<<"$LAST_OUT" && echo 1 || echo 0)" \
    "git -C would resolve to the ENCLOSING repo and move ITS head"
  rm -rf "$d"

  d="$(build_project)"; apply_add "$d" "myapp" badpair; verify "$d/proj" myapp
  check "${pfx}a pair that resolves to nobody is caught" \
    "$([[ $LAST_RC -eq 1 ]] && grep -q "FAIL each developer's \`pair\` resolves" <<<"$LAST_OUT" && echo 1 || echo 0)" \
    "the engine stops the run on an unresolvable pair"
  rm -rf "$d"

  d="$(build_project)"; apply_add "$d" "myapp" badkind; verify "$d/proj" myapp
  check "${pfx}an unknown review_surface.kind is caught (--kind webb)" \
    "$([[ $LAST_RC -eq 1 ]] && grep -q 'FAIL review_surface.kind is one the engine knows' <<<"$LAST_OUT" && echo 1 || echo 0)" \
    "one of the four advertised invariants — a typo here reaches the engine otherwise"
  rm -rf "$d"

  d="$(build_project)"; apply_add "$d" "myapp" unborn; verify "$d/proj" myapp
  check "${pfx}a repo with an unborn HEAD is caught" \
    "$([[ $LAST_RC -eq 1 ]] && grep -q 'FAIL the project has at least one commit' <<<"$LAST_OUT" && echo 1 || echo 0)" \
    "untracked files are invisible to git diff — the review ring would read an empty diff"
  rm -rf "$d"

  d="$(build_project)"; apply_add "$d" "myapp" noorigin; verify "$d/proj" myapp
  check "${pfx}a repo with no origin is caught" \
    "$([[ $LAST_RC -eq 1 ]] && grep -q 'FAIL the project has an origin remote' <<<"$LAST_OUT" && echo 1 || echo 0)" \
    "the portal's code-task worktree resolves origin's default branch and cannot run without it"
  rm -rf "$d"

  d="$(build_project)"; apply_add "$d" "myapp" symlink; verify "$d/proj" myapp
  check "${pfx}a symlinked project directory is caught" \
    "$([[ $LAST_RC -eq 1 ]] && grep -q 'FAIL the project directory is not a symlink' <<<"$LAST_OUT" && echo 1 || echo 0)"
  rm -rf "$d"

  d="$(build_project)"; apply_add "$d" "myapp" worktree; verify "$d/proj" myapp
  check "${pfx}a linked WORKTREE is accepted (its .git is a file, not a dir)" \
    "$(grep -q 'FAIL the project is its own git repo' <<<"$LAST_OUT" && echo 0 || echo 1)" \
    "the old isdir(.git) test called this 'not a repo'; git handles it fine"
  rm -rf "$d"

  d="$(build_project)"; apply_add "$d" "hooks" none 2>/dev/null; verify "$d/proj" hooks
  check "${pfx}a management-territory name is caught (derived, not transcribed)" \
    "$([[ $LAST_RC -eq 1 ]] && grep -q 'FAIL the name is not management territory' <<<"$LAST_OUT" && echo 1 || echo 0)" \
    "hooks/ and skills/ were both missing from a hand-written copy of this list"
  rm -rf "$d"

  d="$(build_project)"; apply_add "$d" "MyApp" none; verify "$d/proj" myapp
  check "${pfx}a case-only id collision is caught" \
    "$(grep -q 'FAIL the roster has a squad with this id' <<<"$LAST_OUT" && echo 0 || echo 1)" \
    "MyApp and myapp are one directory on a case-insensitive filesystem"
  rm -rf "$d"

  d="$(build_project)"; apply_add "$d" "myapp" clone; verify "$d/proj" myapp
  check "${pfx}a failed clone is caught" \
    "$([[ $LAST_RC -eq 1 ]] && grep -q 'project directory exists' <<<"$LAST_OUT" && echo 1 || echo 0)"
  rm -rf "$d"

  section "${pfx}B2. the REMOVED side — every branch of check_removed"
  # 3 of check_removed's 4 assertions had no covering case, on the command whose headline promise is
  # "never deletes your code". Each of these neutralises exactly one.
  d="$(build_project)"; apply_add "$d" "myapp" none
  LAST_OUT="$(python3 "$VERIFY" removed myapp --root "$d/proj" --code-before present 2>&1)"; LAST_RC=$?
  check "${pfx}a squad still in the roster is caught by the REMOVED check" \
    "$([[ $LAST_RC -eq 1 ]] && grep -q 'FAIL the squad is gone from the roster' <<<"$LAST_OUT" && echo 1 || echo 0)"
  rm -rf "$d"

  # dead ignore entry: directory gone, line left behind
  d="$(build_project)"; apply_add "$d" "myapp" none
  python3 - "$d/proj/standup/team.json" <<'PY'
import json,sys
p=sys.argv[1]; dd=json.load(open(p)); dd["teams"]=[x for x in dd["teams"] if x["id"]!="myapp"]
json.dump(dd,open(p,"w"),indent=2)
PY
  rm -rf "$d/proj/myapp"
  LAST_OUT="$(python3 "$VERIFY" removed myapp --root "$d/proj" --code-before present 2>&1)"; LAST_RC=$?
  check "${pfx}deleting the user's code is caught (the headline promise)" \
    "$([[ $LAST_RC -eq 1 ]] && grep -q "FAIL the user's code was left alone" <<<"$LAST_OUT" && echo 1 || echo 0)" \
    "both branches used to call _ok — it printed ok at the moment the promise broke"
  check "${pfx}a dead .gitignore entry is caught" \
    "$(grep -q 'FAIL the gitignore entry matches reality' <<<"$LAST_OUT" && echo 1 || echo 0)" \
    "directory gone, ignore line left behind"
  rm -rf "$d"

  # the inverse, and the one the walkthrough found: line removed while the clone remains
  d="$(build_project)"; apply_add "$d" "myapp" none
  python3 - "$d/proj/standup/team.json" <<'PY'
import json,sys
p=sys.argv[1]; dd=json.load(open(p)); dd["teams"]=[x for x in dd["teams"] if x["id"]!="myapp"]
json.dump(dd,open(p,"w"),indent=2)
PY
  grep -v '^/myapp/$' "$d/proj/.gitignore" > "$d/proj/.gi" && mv "$d/proj/.gi" "$d/proj/.gitignore"
  LAST_OUT="$(python3 "$VERIFY" removed myapp --root "$d/proj" --code-before present 2>&1)"; LAST_RC=$?
  check "${pfx}removing the ignore line while the clone remains is caught" \
    "$([[ $LAST_RC -eq 1 ]] && grep -q 'FAIL the gitignore entry matches reality' <<<"$LAST_OUT" && echo 1 || echo 0)" \
    "that re-arms the gitlink /add-project exists to prevent"
  rm -rf "$d"

  # staff scope_folders dangling
  d="$(build_project)"; apply_add "$d" "myapp" none
  python3 - "$d/proj/standup/team.json" <<'PY'
import json,sys
p=sys.argv[1]; dd=json.load(open(p))
dd["teams"]=[x for x in dd["teams"] if x["id"]!="myapp"]
dd["staff"]=[{"id":"pm_agent","folder":"standup","scope_folders":["myapp"],"active":True}]
json.dump(dd,open(p,"w"),indent=2)
PY
  LAST_OUT="$(python3 "$VERIFY" removed myapp --root "$d/proj" --code-before present 2>&1)"
  check "${pfx}a staff scope_folders reference to the removed squad is caught" \
    "$(grep -q 'staff pm_agent.scope_folders -> myapp' <<<"$LAST_OUT" && echo 1 || echo 0)"
  rm -rf "$d"

  section "${pfx}D. WHY the guarantee — demonstrated, not asserted"
  # These two do not check verify_project at all. They exercise git itself, because the reasons
  # behind the two hardest-to-argue rules are the thing a future reader needs in order to judge
  # whether the rules can be relaxed. "It would review-fail" sounds relaxable. "It commits into
  # your installation" does not.
  d="$(build_project)"
  git -C "$d/proj" add -A >/dev/null 2>&1
  git -C "$d/proj" "${GIT_ID[@]}" commit -q -m base >/dev/null 2>&1
  mkdir -p "$d/proj/notarepo"; echo wip > "$d/proj/unrelated-wip.txt"
  local head_before head_after
  head_before="$(git -C "$d/proj" rev-parse --abbrev-ref HEAD)"
  git -C "$d/proj/notarepo" checkout -b feature/task-1 >/dev/null 2>&1
  head_after="$(git -C "$d/proj" rev-parse --abbrev-ref HEAD)"
  check "${pfx}a non-repo project MOVES THE ENCLOSING repo's HEAD" \
    "$([[ "$head_before" != "$head_after" ]] && echo 1 || echo 0)" \
    "$head_before -> $head_after — that is the user's agent-team installation, not the project"
  git -C "$d/proj/notarepo" add -A >/dev/null 2>&1
  check "${pfx}...and stages unrelated in-flight work from elsewhere in the tree" \
    "$(git -C "$d/proj" diff --cached --name-only 2>/dev/null | grep -q 'unrelated-wip.txt' && echo 1 || echo 0)" \
    "several sessions share this working tree; this is why the push job never auto-commits"
  rm -rf "$d"

  d="$(mktemp -d)"; mkdir -p "$d/adopted"
  printf 'def f():\n    return 1\n' > "$d/adopted/app.py"
  git -C "$d/adopted" init -q -b main
  git -C "$d/adopted" "${GIT_ID[@]}" commit -q --allow-empty -m "empty baseline"
  printf 'def f():\n    return 2\n' > "$d/adopted/app.py"
  check "${pfx}an --allow-empty baseline yields an EMPTY diff after a real edit" \
    "$([[ -z "$(git -C "$d/adopted" diff -- . 2>/dev/null)" ]] && echo 1 || echo 0)" \
    "pre-existing files stay untracked and git diff cannot see them — the review ring reads nothing"
  git -C "$d/adopted" add -A >/dev/null 2>&1
  git -C "$d/adopted" "${GIT_ID[@]}" commit -q -m tracked
  printf 'def f():\n    return 3\n' > "$d/adopted/app.py"
  check "${pfx}...while a TRACKED baseline yields a real diff for the same edit" \
    "$([[ -n "$(git -C "$d/adopted" diff -- . 2>/dev/null)" ]] && echo 1 || echo 0)" \
    "this is why adopt commits content, with a secret scan in front of it"
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
    "gitlink|if hit:|if False:|...and the verifier reports the gitlink too"
    "folder|if wrong:|if False:|a wrong developer folder is caught"
    "clone|if not os.path.isdir(proj):|if False:|a failed clone is caught"
    "toplevel|if not own_root:|if False:|a directory that is not a git repo is caught"
    "unborn|elif not has_commit:|elif False:|a repo with an unborn HEAD is caught"
    "noorigin|if not has_origin(proj):|if False:|a repo with no origin is caught"
    "symlink|elif os.path.islink(proj):|elif False:|a symlinked project directory is caught"
    "denylist|elif name.lower() in {o.lower() for o in owned}:|elif False:|a management-territory name is caught (derived, not transcribed)"
    "badpair|if unpaired:|if False:|a pair that resolves to nobody is caught"
    "badkind|if kind not in SURFACE_KINDS:|if False:|an unknown review_surface.kind is caught (--kind webb)"
    "squad-gone|if any(t.get(\"id\") == name for t in roster.get(\"teams\", [])):|if False:|a squad still in the roster is caught by the REMOVED check"
    "code-deleted|if code_before == \"present\" and not here:|if False:|deleting the user's code is caught (the headline promise)"
    "ignore-mismatch|if here and not present:|if False:|removing the ignore line while the clone remains is caught"
    "staff-scope|if f == name:|if False:|a staff scope_folders reference to the removed squad is caught"
    "surface|if not isinstance(surface, dict):|if False:|missing review_surface is caught"
    "inspect|if kind != \"none\" and not str(surface.get(\"inspect\") or \"\").strip():|if False:|blank inspect is caught (kind alone is not enough)"
    "pair|if len(devs) < 2:|if False:|a LONE developer is caught (no fresh-context critic)"
  )
  local rc=0
  printf '=== --self-test: neutralise ONE checker branch at a time ===\n'
  # Run the mutations CONCURRENTLY. Each one re-runs the whole case suite (~7s) against its own
  # mutated copy of the checker, in its own mktemp dir, touching nothing shared — so they are
  # independent by construction. Serially this is ~2 minutes and growing with every branch added,
  # which is how a judge stops being run. Coverage is unchanged: every mutation still executes the
  # full suite and is still required to redden its OWN named case.
  local jobdir; jobdir="$(mktemp -d)"
  local m name from to want i=0
  for m in "${muts[@]}"; do
    IFS='|' read -r name from to want <<<"$m"
    grep -qF "$from" "$VERIFY" || die_judge "self-test anchor not found in verify_project.py: $from
      Re-anchor it or delete the fixture — a mutation that silently no-ops reads as a pass."
    (
      d="$(mktemp -d)"
      python3 - "$VERIFY" "$d/mutated.py" "$from" "$to" <<'PY'
import sys
src = open(sys.argv[1], encoding="utf-8").read()
assert sys.argv[3] in src
open(sys.argv[2], "w", encoding="utf-8").write(src.replace(sys.argv[3], sys.argv[4], 1))
PY
      out="$(STANDUP_VERIFY_PROJECT="$d/mutated.py" bash "$0" 2>&1)"
      if grep -qF "  $want → FAIL" <<<"$out"; then
        printf 'RED %s\n' "$name" > "$jobdir/$i"
      else
        { printf 'GREEN %s\n' "$name"; printf '   want red: %s\n' "$want"; } > "$jobdir/$i"
      fi
      rm -rf "$d"
    ) &
    i=$((i + 1))
  done
  wait

  local f verdict
  for f in $(ls "$jobdir" | sort -n); do
    verdict="$(head -1 "$jobdir/$f")"
    if [[ "$verdict" == RED\ * ]]; then
      printf '  %-46s → correctly went RED\n' "${verdict#RED }"
    else
      printf '  %-46s → ERROR  its own case stayed green\n' "${verdict#GREEN }" >&2
      tail -n +2 "$jobdir/$f" >&2
      rc=3
    fi
  done
  rm -rf "$jobdir"

  [[ $rc -eq 0 ]] && printf '\n--self-test → PASS  (%d checker branch(es) neutralised; each drove its OWN named case red)\n' "${#muts[@]}"
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
