#!/usr/bin/env bash
# /remove-project judge.
#
#     bash standup/control/tests/test_remove_project.sh
#     bash standup/control/tests/test_remove_project.sh --self-test
#
# WHAT IT GUARDS, and why the central assertion is BYTE equality.
#
# `standup/team.json` is hand-formatted and carries `_comment` fields that document the schema —
# they are the only place several invariants are written down. Both project commands therefore
# declare `allowedTools: Read, Bash, Edit` and NOT `Write`: the edit has to be surgical. Byte
# equality after add -> remove is what makes "surgical" a testable claim instead of a wish. Round-
# tripping through `json.load` / `json.dump` reformats the whole file and drops nothing visible,
# which is exactly why it would otherwise go unnoticed until someone reads a comment that is gone.
#
# ⚠️ DO NOT "FIX" A FAILURE HERE BY NORMALISING BEFORE THE COMPARE. Sorting keys, re-indenting, or
# diffing parsed objects instead of bytes would make this pass again while deleting the only thing
# it checks. If the byte compare fails, the removal was not surgical — that is the finding.
#
# WHAT IT DOES NOT GUARD. `/remove-project` is a PROMPT and this judge cannot run it. It exercises a
# REFERENCE removal performed the way the skill instructs (line-scoped edits), proving the invariant
# is achievable and that the naive alternative fails it. Whether a model follows the prompt is for
# the human walkthrough.
#
# Exit codes: 0 pass · 1 failures · 3 the judge itself is broken · 64 usage
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
VERIFY="${STANDUP_VERIFY_PROJECT:-$REPO/standup/control/verify_project.py}"

fails=0
check() { # name ok detail
  local name="$1" ok="$2" detail="${3:-}"
  [[ "$ok" == "1" ]] || fails=$((fails + 1))
  printf '  %s → %s%s\n' "$name" "$([[ "$ok" == 1 ]] && echo PASS || echo FAIL)" "${detail:+  $detail}"
}
section() { printf '\n%s\n' "$1"; }
die_judge() { printf '\n!! JUDGE BROKEN — %s\n' "$1" >&2; exit 3; }
[[ -f "$VERIFY" ]] || die_judge "verify_project.py not found at $VERIFY"

# A hand-formatted roster, deliberately including a _comment and irregular spacing — the things a
# reformatting edit destroys and a surgical one preserves.
build_root() { # -> echoes root
  local d; d="$(mktemp -d)"
  mkdir -p "$d/standup"
  cat > "$d/standup/team.json" <<'JSON'
{
  "_comment": "MVP roster. The comments in this file ARE the schema documentation — a rewrite loses them.",
  "teams": [
    {
      "id": "portal",
      "name": "Team Portal Squad",
      "review_surface": { "kind": "web",  "label": "Mission Control",  "inspect": "curl -sS -f http://127.0.0.1:8770/healthz" },
      "developers": [
        { "id": "portal_backend",  "folder": "standup/portal", "active": true, "pair": "portal_frontend" },
        { "id": "portal_frontend", "folder": "standup/portal", "active": true, "pair": "portal_backend"  }
      ]
    }
  ],
  "staff": [
    { "id": "pm_agent", "folder": "standup", "active": true }
  ]
}
JSON
  printf '# venv\nstandup/.venv/\n' > "$d/.gitignore"
  echo "$d"
}

# SURGICAL add: append a squad by splicing text, the way an Edit does. No parse, no dump.
add_surgical() { # <root> <name>
  local root="$1" name="$2"
  NAME="$name" python3 - "$root/standup/team.json" <<'PY'
import os, sys
p = sys.argv[1]; name = os.environ["NAME"]
src = open(p, encoding="utf-8").read()
block = (
'    {\n'
'      "id": "%s",\n'
'      "name": "%s",\n'
'      "review_surface": { "kind": "cli", "label": "%s tests", "inspect": "cd %s && pytest -q" },\n'
'      "developers": [\n'
'        { "id": "%s_a", "folder": "%s", "active": true, "pair": "%s_b" },\n'
'        { "id": "%s_b", "folder": "%s", "active": true, "pair": "%s_a" }\n'
'      ]\n'
'    }\n'
) % (name, name.title(), name, name, name, name, name, name, name, name)
anchor = '  ],\n  "staff": ['
assert anchor in src
src = src.replace('    }\n' + anchor, '    },\n' + block + anchor, 1)
open(p, "w", encoding="utf-8").write(src)
PY
  printf '/%s/\n' "$name" >> "$root/.gitignore"
  mkdir -p "$root/$name"; printf 'code\n' > "$root/$name/app.py"
}

# SURGICAL remove: delete exactly the spliced lines. Mirrors what the skill instructs.
remove_surgical() { # <root> <name>
  local root="$1" name="$2"
  NAME="$name" python3 - "$root/standup/team.json" <<'PY'
import os, re, sys
p = sys.argv[1]; name = os.environ["NAME"]
src = open(p, encoding="utf-8").read()
pat = re.compile(r'    \{\n      "id": "%s",\n(?:.*?\n)*?    \}\n' % re.escape(name))
m = pat.search(src)
assert m, "squad block not found"
src = src[:m.start()] + src[m.end():]
src = src.replace('    },\n  ],\n  "staff": [', '    }\n  ],\n  "staff": [', 1)
open(p, "w", encoding="utf-8").write(src)
PY
  # NOTE the .gitignore line is deliberately NOT touched. /remove-project leaves the clone on disk,
  # and while it is there that line is the only thing stopping `git add -A` recording it as a
  # gitlink. Deleting it does not tidy up — it re-arms the hazard /add-project exists to prevent.
}

# The NAIVE removal this judge exists to reject: parse, mutate, dump.
remove_reformatting() { # <root> <name>
  local root="$1" name="$2"
  NAME="$name" python3 - "$root/standup/team.json" <<'PY'
import json, os, sys
p = sys.argv[1]; name = os.environ["NAME"]
d = json.load(open(p))
d["teams"] = [t for t in d["teams"] if t.get("id") != name]
json.dump(d, open(p, "w"), indent=2)
PY
  python3 - "$root/.gitignore" "$name" <<'PY'
import sys
p, name = sys.argv[1], sys.argv[2]
keep = [l for l in open(p, encoding="utf-8").read().splitlines(True) if l.strip() != "/%s/" % name]
open(p, "w", encoding="utf-8").writelines(keep)
PY
}

run_cases() { # <label-prefix>
  local pfx="${1:-}" root before after

  section "${pfx}A. add -> remove leaves the roster BYTE-identical"
  root="$(build_root)"; before="$(mktemp)"; cp "$root/standup/team.json" "$before"
  add_surgical "$root" myapp
  check "${pfx}the add actually changed the file" \
    "$(cmp -s "$before" "$root/standup/team.json" && echo 0 || echo 1)" \
    "if this fails the round-trip below proves nothing"
  check "${pfx}the roster still parses after the surgical add" \
    "$(python3 -c 'import json,sys;json.load(open(sys.argv[1]))' "$root/standup/team.json" >/dev/null 2>&1 && echo 1 || echo 0)"
  remove_surgical "$root" myapp
  after="$root/standup/team.json"
  # .gitignore is intentionally NOT round-tripped (the line stays with the directory); the surgical
  # claim is about team.json, which is the hand-formatted file carrying the schema comments.
  check "${pfx}team.json is byte-identical to before the add" \
    "$(cmp -s "$before" "$after" && echo 1 || echo 0)" \
    "$(cmp -s "$before" "$after" || diff <(cat "$before") <(cat "$after") | head -3 | tr '\n' ' ')"
  check "${pfx}the _comment survived (it IS the schema documentation)" \
    "$(grep -q '_comment' "$after" && echo 1 || echo 0)"
  check "${pfx}.gitignore line is KEPT while the clone is on disk" \
    "$(grep -q '^/myapp/$' "$root/.gitignore" && echo 1 || echo 0)" \
    "removing it would re-arm the gitlink; it goes when the directory goes"
  check "${pfx}the user's code was NOT deleted" \
    "$([[ -f "$root/myapp/app.py" ]] && echo 1 || echo 0)" \
    "removing a squad is reversible; deleting a working tree is not"
  python3 "$VERIFY" removed myapp --root "$root" --code-before present >/dev/null 2>&1
  check "${pfx}verify_project agrees the project is removed" \
    "$([[ $? -eq 0 ]] && echo 1 || echo 0)"
  rm -rf "$root" "$before"

  section "${pfx}B. the NAIVE parse-and-dump removal is REJECTED"
  # This is the case that gives the byte assertion its meaning. A reformatting removal produces a
  # roster that parses, contains the right data, and looks fine — and silently reflows every line.
  root="$(build_root)"; before="$(mktemp)"; cp "$root/standup/team.json" "$before"
  add_surgical "$root" myapp
  remove_reformatting "$root" myapp
  after="$root/standup/team.json"
  check "${pfx}it still parses (which is why it slips through review)" \
    "$(python3 -c 'import json,sys;json.load(open(sys.argv[1]))' "$after" >/dev/null 2>&1 && echo 1 || echo 0)"
  check "${pfx}it carries the same DATA as the original" \
    "$(python3 -c 'import json,sys;a=json.load(open(sys.argv[1]));b=json.load(open(sys.argv[2]));sys.exit(0 if a==b else 1)' "$before" "$after" && echo 1 || echo 0)"
  check "${pfx}but the byte compare CATCHES it" \
    "$(cmp -s "$before" "$after" && echo 0 || echo 1)" \
    "this is the whole point of comparing bytes rather than parsed objects"
  rm -rf "$root" "$before"

  section "${pfx}C. dangling references are reported, not silently created"
  root="$(build_root)"; add_surgical "$root" myapp
  # point an existing developer at a soon-to-be-removed one
  python3 - "$root/standup/team.json" <<'PY'
import sys
p = sys.argv[1]; s = open(p, encoding="utf-8").read()
open(p, "w", encoding="utf-8").write(s.replace('"pair": "portal_frontend"', '"pair": "myapp_a"', 1))
PY
  remove_surgical "$root" myapp
  local out; out="$(python3 "$VERIFY" removed myapp --root "$root" --code-before present 2>&1)"; local rc=$?
  check "${pfx}a dangling pair is caught" \
    "$([[ $rc -eq 1 ]] && grep -q 'dangling references' <<<"$out" && echo 1 || echo 0)" "exit=$rc"
  check "${pfx}and it names both ids" \
    "$(grep -q 'portal_backend.pair -> myapp_a' <<<"$out" && echo 1 || echo 0)"
  rm -rf "$root"
}

self_test() {
  local muts=(
    "byte-compare-normalised|the surgical remove is replaced by a reformatting one|but the byte compare CATCHES it"
    "dangling|if d.get(\"pair\") and d.get(\"pair\") not in live_ids:|a dangling pair is caught"
  )
  printf '=== --self-test: one branch at a time ===\n'
  local rc=0 d out

  # 1. If the surgical removal is swapped for the reformatting one, case A must go red. This is the
  #    mutation that matters: it is the difference between an Edit and a Write.
  d="$(mktemp -d)"
  # `[$]` not `\$`: inside single quotes the backslash reaches sed literally and the pattern
  # matches nothing. The first draft did that, silently mutated NOTHING, and then "verified" the
  # swap with a grep that case B already satisfies — a no-op mutation certified by a check that
  # could not fail. Both halves are fixed: the pattern matches, and the assertion is that the
  # ORIGINAL call is gone.
  sed 's|^  remove_surgical "[$]root" myapp$|  remove_reformatting "$root" myapp|' "$0" > "$d/mutated.sh"
  if grep -q '^  remove_surgical "[$]root" myapp$' "$d/mutated.sh"; then
    die_judge "self-test did not swap remove_surgical for remove_reformatting — the sed pattern
      no longer matches. A mutation that silently no-ops reads as a pass."
  fi
  # The mutated copy lives in /tmp, so its own REPO resolution points nowhere — it would die_judge
  # before running a single case and the self-test would score that as "case A stayed green". Same
  # trap the eval judge hit in batch 1. Hand it the real checker through the seam.
  out="$(STANDUP_VERIFY_PROJECT="$VERIFY" bash "$d/mutated.sh" 2>&1)"
  if grep -qF '  team.json is byte-identical to before the add → FAIL' <<<"$out"; then
    printf '  %-46s → correctly went RED\n' "reformatting removal"
  else
    printf '  %-46s → ERROR  case A stayed green\n' "reformatting removal" >&2; rc=3
  fi
  rm -rf "$d"

  # 2. The dangling-reference branch of the checker, on its own.
  d="$(mktemp -d)"
  local anchor='if d.get("pair") and d.get("pair") not in live_ids:'
  grep -qF "$anchor" "$VERIFY" || die_judge "dangling anchor not found in verify_project.py"
  python3 - "$VERIFY" "$d/mutated.py" "$anchor" <<'PY'
import sys
src = open(sys.argv[1], encoding="utf-8").read()
open(sys.argv[2], "w", encoding="utf-8").write(src.replace(sys.argv[3], "if False:", 1))
PY
  out="$(STANDUP_VERIFY_PROJECT="$d/mutated.py" bash "$0" 2>&1)"
  if grep -qF '  a dangling pair is caught → FAIL' <<<"$out"; then
    printf '  %-46s → correctly went RED\n' "dangling-reference check"
  else
    printf '  %-46s → ERROR  its own case stayed green\n' "dangling-reference check" >&2; rc=3
  fi
  rm -rf "$d"

  [[ $rc -eq 0 ]] && printf '\n--self-test → PASS  (both branches have independent covering cases)\n'
  return $rc
}

main() {
  case "${1:-}" in
    --self-test) self_test; exit $? ;;
    "") ;;
    *) printf 'usage: test_remove_project.sh [--self-test]\n' >&2; exit 64 ;;
  esac
  printf '/remove-project judge — fixtures only; byte equality is the surgical-edit contract\n'
  run_cases
  printf '\n%s\n' "$([[ $fails -eq 0 ]] && echo "all checks PASS" || echo "$fails check(s) FAILED")"
  printf 'Run --self-test to prove the byte compare and the dangling check can fail (E-03).\n'
  [[ $fails -eq 0 ]] || exit 1
}

main "$@"
