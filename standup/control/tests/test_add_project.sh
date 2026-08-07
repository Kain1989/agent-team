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

  # --- the two refusals that lived ONLY in the skill's prose.
  # Step 1a of skills/add-project/SKILL.md refuses a `name` containing `/`, and refuses a name that
  # is management territory. Neither was in the checker. Measured on a fixture: a squad
  # `standup/portal`, hand-adopted exactly the way the README told people to, scored
  # "12 check(s), 0 failed" and exit 0 — the checker signed off on a dev squad pointed at the
  # engine's own control plane, with dev ids that crash /sync-roster.
  d="$(build_project)"; apply_add "$d" "sub/proj" none 2>/dev/null; verify "$d/proj" "sub/proj"
  check "${pfx}a name containing a path separator is caught" \
    "$([[ $LAST_RC -eq 1 ]] && grep -q 'FAIL the name is usable as a directory, a squad id and a dev-id prefix' <<<"$LAST_OUT" && echo 1 || echo 0)" \
    "name is all three at once; a / makes the dev id \`sub/proj_a\`, and /sync-roster then writes .claude/agents/sub/proj_a.md into a directory that does not exist"
  rm -rf "$d"

  d="$(build_project)"; apply_add "$d" "standup/portal" none 2>/dev/null; verify "$d/proj" "standup/portal"
  check "${pfx}a management SUB-PATH name is caught" \
    "$([[ $LAST_RC -eq 1 ]] && grep -q 'FAIL the name is not management territory' <<<"$LAST_OUT" && echo 1 || echo 0)" \
    "the deny list compared the WHOLE string, so \`hooks\` was refused and \`standup/portal\` sailed through"
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

  # --- S3: branches that shipped with no covering case, each now exercised ---
  # `gitlinked_paths()` is tested DIRECTLY, because 120000 is not reachable through check_added:
  # if the project dir is a symlink, `islink(proj)` refuses first, and a symlink INSIDE a project
  # never reaches the outer index at all — a nested repo stages as ONE 160000 entry (measured).
  # So the mode support is defence in depth: the refusal closes the entrance, and this keeps the
  # checker from reporting green about a mode it cannot see if one ever arrives another way.
  #
  # The first version of this case pointed check_added at the symlink and grepped for the literal
  # "120000" — which the symlink-REFUSAL message also contains, so it passed either way, and then
  # failed unmutated while --self-test still scored it "correctly went RED". Both bugs are fixed:
  # the assertion is specific, and the baseline-green gate below catches an always-red case.
  d="$(mktemp -d)"; git -C "$d" init -q -b main
  mkdir -p "$d/elsewhere"; echo z > "$d/elsewhere/z.txt"; ln -s "$d/elsewhere" "$d/linkdir"
  git -C "$d" add -A >/dev/null 2>&1
  LAST_OUT="$(STANDUP_VERIFY_PROJECT="$VERIFY" python3 -c '
import sys, importlib.util
spec = importlib.util.spec_from_file_location("vp", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
print(m.gitlinked_paths(sys.argv[2]))' "$VERIFY" "$d" 2>&1)"
  check "${pfx}a staged symlink blob (120000) is caught" \
    "$(grep -q "'linkdir', '120000'" <<<"$LAST_OUT" && echo 1 || echo 0)" \
    "gitlinked_paths saw: $LAST_OUT"
  rm -rf "$d"

  d="$(build_project)"
  python3 - "$d/proj/standup/team.json" <<'PY'
import json,sys
p=sys.argv[1]; dd=json.load(open(p))
dd["teams"].append({"id":"fresh","name":"Fresh",
  "review_surface":{"kind":"none","label":"no face yet","how":"new project"},
  "developers":[{"id":"fresh_a","folder":"fresh","active":True,"pair":"fresh_b"},
                {"id":"fresh_b","folder":"fresh","active":True,"pair":"fresh_a"}]})
json.dump(dd,open(p,"w"),indent=2)
PY
  git -C "$d/proj" clone -q "$d/upstream" fresh; printf '/fresh/\n' >> "$d/proj/.gitignore"
  LAST_OUT="$(python3 "$VERIFY" added fresh --root "$d/proj" 2>&1)"; LAST_RC=$?
  check "${pfx}kind none with no inspect is ACCEPTED" \
    "$([[ $LAST_RC -eq 0 ]] && echo 1 || echo 0)" \
    "this is exactly what \`new\` writes — tightening the guard breaks every new project"
  rm -rf "$d"

  d="$(build_project)"; apply_add "$d" "myapp" none
  git -C "$d/proj" init -q --bare "$d/proj/.myapp-origin.git"
  LAST_OUT="$(python3 "$VERIFY" added myapp --root "$d/proj" 2>&1)"; LAST_RC=$?
  check "${pfx}an unignored local bare origin is caught" \
    "$([[ $LAST_RC -eq 1 ]] && grep -q 'FAIL the local bare origin is ignored' <<<"$LAST_OUT" && echo 1 || echo 0)" \
    "unignored, git add -A absorbs the project's objects — secrets included — into the install"
  printf '/.myapp-origin.git/\n' >> "$d/proj/.gitignore"
  LAST_OUT="$(python3 "$VERIFY" added myapp --root "$d/proj" 2>&1)"; LAST_RC=$?
  check "${pfx}...and accepted once ignored" "$([[ $LAST_RC -eq 0 ]] && echo 1 || echo 0)"
  rm -rf "$d"

  d="$(build_project)"; apply_add "$d" "MyApp" none 2>/dev/null
  LAST_OUT="$(python3 "$VERIFY" added Hooks --root "$d/proj" 2>&1)"
  check "${pfx}a management-territory name is caught case-insensitively" \
    "$(grep -q 'FAIL the name is not management territory' <<<"$LAST_OUT" && echo 1 || echo 0)" \
    "Hooks vs hooks — one directory on a case-insensitive filesystem"
  rm -rf "$d"

  # --- the missing-gate branch. It had NO case and NO mutation, because build_project always
  # copies the real gate (line ~74) — so the one shape where the deny list is inert was the one
  # shape never exercised. `skills/init` produces exactly that shape: it scaffolds standup/,
  # setup.sh and .env.example, and never hooks/.
  d="$(build_project)"; apply_add "$d" "myapp" none; rm -rf "$d/proj/hooks"
  LAST_OUT="$(python3 "$VERIFY" added myapp --root "$d/proj" 2>&1)"; LAST_RC=$?
  check "${pfx}an unreadable supervisor_gate.py FAILS, it does not report ok" \
    "$([[ $LAST_RC -eq 1 ]] && grep -q 'FAIL the name is not management territory' <<<"$LAST_OUT" && echo 1 || echo 0)" \
    "the line used to read 'NOT CHECKED, not a pass' while being a pass"
  rm -rf "$d"

  # --- a committed secret file. The B2 refusal lived only in skill prose until now.
  d="$(build_project)"
  git -C "$d/upstream" checkout -q -b withsecret 2>/dev/null || true
  printf 'DB_PASSWORD=hunter2\n' > "$d/upstream/.env"
  git -C "$d/upstream" add -A >/dev/null && git -C "$d/upstream" "${GIT_ID[@]}" commit -q -m "oops"
  apply_add "$d" "myapp" none; verify "$d/proj" myapp
  check "${pfx}a committed secret-shaped file is caught by NAME" \
    "$([[ $LAST_RC -eq 1 ]] && grep -q 'FAIL no secret-shaped file is committed' <<<"$LAST_OUT" && echo 1 || echo 0)" \
    "the scanner misses unquoted dotenv lines entirely; the filename is the reliable signal"
  rm -rf "$d"

  # --- S2: a non-git install root must not FAIL the bare-origin check
  # The roster MUST carry the squad: check_added returns early on an unknown id, so a fixture
  # without it never reaches the bare-origin block and the case passes vacuously both ways.
  d="$(mktemp -d)"; mkdir -p "$d/proj/standup" "$d/proj/hooks" "$d/proj/.myapp-origin.git" "$d/proj/myapp"
  cp "$REPO/hooks/supervisor_gate.py" "$d/proj/hooks/"
  git -C "$d/proj/myapp" init -q -b main && echo x > "$d/proj/myapp/a.py"
  git -C "$d/proj/myapp" add -A && git -C "$d/proj/myapp" "${GIT_ID[@]}" commit -q -m base
  git -C "$d/proj/myapp" remote add origin "$d/proj/.myapp-origin.git" 2>/dev/null || true
  cat > "$d/proj/standup/team.json" <<'JSON'
{ "teams": [ { "id": "myapp", "name": "M",
    "review_surface": { "kind": "cli", "label": "t", "inspect": "pytest -q" },
    "developers": [ { "id": "myapp_a", "folder": "myapp", "active": true, "pair": "myapp_b" },
                    { "id": "myapp_b", "folder": "myapp", "active": true, "pair": "myapp_a" } ] } ],
  "staff": [] }
JSON
  printf '/myapp/\n' > "$d/proj/.gitignore"
  LAST_OUT="$(python3 "$VERIFY" added myapp --root "$d/proj" 2>&1)"
  check "${pfx}a non-git install root does not FAIL the bare-origin check" \
    "$(grep -q 'FAIL the local bare origin is ignored' <<<"$LAST_OUT" && echo 0 || echo 1)" \
    "check-ignore exits 128 outside a repo — that is 'no index to absorb it', not 'unignored'"
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
    "denylist|elif head.lower() in {o.lower() for o in owned}:|elif False:|a management-territory name is caught (derived, not transcribed)"
    # Anchored on the CALL SITE, not on management_head()'s body: the body is
    # `.replace(\"\\\\\", \"/\")` and threading that through a `|`-delimited bash array is three
    # layers of escaping deep, which is its own way for a mutation to silently no-op.
    # `head = name` restores the old whole-string comparison exactly, so `hooks` stays caught and
    # only the sub-path case reddens — which is what makes it an INDEPENDENT mutation.
    "denylist-subpath|head = management_head(name)|head = name|a management SUB-PATH name is caught"
    "legalname|if bad_name:|if False:|a name containing a path separator is caught"
    "mode120000|DANGEROUS_MODES = {\"160000\": \"gitlink (embedded repo)\", \"120000\": \"symlink blob\"}|DANGEROUS_MODES = {\"160000\": \"gitlink (embedded repo)\"}|a staged symlink blob (120000) is caught"
    "kindnone|if kind != \"none\" and not str(surface.get(\"inspect\") or \"\").strip():|if not str(surface.get(\"inspect\") or \"\").strip():|kind none with no inspect is ACCEPTED"
    "denylist-exact|elif head.lower() in {o.lower() for o in owned}:|elif head in owned:|a management-territory name is caught case-insensitively"
    "bareorigin|elif _git(root, \"check-ignore\", \"-q\", origin_dir)[0] != 0:|elif False:|an unignored local bare origin is caught"
    "gate-unreadable|if owned is None:|if False:|an unreadable supervisor_gate.py FAILS, it does not report ok"
    "secretname|if secrets:|if False:|a committed secret-shaped file is caught by NAME"
    "bareorigin-nonrepo|if rc_isrepo != 0:|if False:|a non-git install root does not FAIL the bare-origin check"
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
  # BASELINE GATE. Every mutation verdict below is "did this case go RED" — which is meaningless
  # for a case that was already red. Measured: a broken new case failed unmutated AND was scored
  # "correctly went RED". Establish green first, or the whole self-test is unfalsifiable.
  if ! bash "$0" >/dev/null 2>&1; then
    printf '!! SELF-TEST ABORTED — the unmutated suite is not green.\n' >&2
    printf '   Every check below asks "did this case go red"; a case that is already red answers\n' >&2
    printf '   yes for the wrong reason. Fix the baseline first, then re-run.\n' >&2
    return 3
  fi
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

  # EVERY job must have left a verdict. The serial version computed each verdict inline, so a
  # mutation could not go missing; this one iterates over the files that EXIST, which is intent
  # rather than fact. Measured: kill one subshell before it writes and the run printed one fewer
  # verdict than it claimed, and exited 0 — on a 2-core CI runner running every mutation
  # concurrently as a git-heavy job, a job dying is both the likeliest failure and the least
  # visible one. (The counts are read from `muts` at run time and printed below; they are
  # deliberately not restated here, because a number written into a comment rots the moment a
  # mutation is added — this one already said "17 of 18" while the suite ran 25.)
  local collected; collected="$(ls "$jobdir" 2>/dev/null | wc -l | tr -d ' ')"
  if [[ "$collected" -ne "${#muts[@]}" ]]; then
    printf '\n!! SELF-TEST FAILED — %s of %s mutation job(s) reported back.\n' \
      "$collected" "${#muts[@]}" >&2
    printf '   A job that dies before writing its verdict is invisible to the collector, so the\n' >&2
    printf '   missing branch would read as covered. Re-run; if it persists, run serially.\n' >&2
    rm -rf "$jobdir"
    return 3
  fi

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

  # Prints what was COLLECTED, not what was intended — the two differing is the bug above.
  [[ $rc -eq 0 ]] && printf '\n--self-test → PASS  (%s checker branch(es) neutralised; each drove its OWN named case red)\n' "$collected"
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
