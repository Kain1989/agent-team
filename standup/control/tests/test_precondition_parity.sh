#!/usr/bin/env bash
# demo-app precondition parity judge.
#
#     bash standup/control/tests/test_precondition_parity.sh
#     bash standup/control/tests/test_precondition_parity.sh --self-test
#
# WHAT IT GUARDS. One precondition — "make demo-app a git repo before running" — is written out in
# five different documents. Four of them said "if `demo-app/.git` is missing, init it"; one said
# "...(and a `demo-app/` exists)". The guarded one is the correct one, and it was the one nobody
# copied: `/work`, the single most-used entry point, carried the version that tells the model to
# `git -C demo-app init` a directory that isn't there.
#
# WHY IT DISCOVERS THE SITES INSTEAD OF LISTING THEM. A judge holding a hardcoded list of five
# files is true the day it is written. The sixth document — written next month, by someone who
# never read this file — is invisible to it, and the judge stays green while the defect spreads.
# That is DESIGN_RULEBOOK E-05 wearing different clothes: a gate scoped to what was known at the
# time, aimed at a surface that keeps growing. So the site list is DERIVED on every run by grepping
# the tree, and any site it finds must satisfy the rule or the judge is red.
#
# WHAT COUNTS AS A SITE. Only an INSTRUCTION: a mention of `demo-app/.git` followed closely by a
# conditional ("is missing" / "does not exist"). SECURITY.md mentions `demo-app/.git` to say it is
# gitignored — that is not a precondition and is deliberately not policed. A judge that cries wolf
# on prose gets switched off, and then it guards nothing.
#
# The match is made over the file with LINE BREAKS FLATTENED, not line by line. The first cut of
# this judge matched per line and silently discovered 4 of the 5 real sites: `skills/work/SKILL.md`
# wraps "If `demo-app/.git` is" / "missing:" across two lines, so the mention and its conditional
# never shared a line. It reported "4 sites audited, 2 failed" — a confident number, with the
# most-used entry point missing from it. Under-discovery in a self-discovering judge is worse than
# a hardcoded list, because the list at least shows you what it covers.
#
# WHAT A SITE MUST DO — one of:
#   * GUARD      state the directory-existence condition inline, verbatim: (and a `demo-app/` exists)
#   * DELEGATE   point at the one guarded copy instead of restating it: see `/agent-team:standup` step 1
#
# Exit codes: 0 pass · 1 failures · 3 the judge itself is broken · 64 usage
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"

MENTION='demo-app/\.git'
CONDITIONAL='is missing|does not exist|do not exist|does not yet exist'
GUARD='(and a `demo-app/` exists)'
DELEGATE='see `/agent-team:standup` step 1'
WINDOW=4          # lines either side of the instruction line the guard may appear on

fails=0
sites=0
check() { # name ok detail
  local name="$1" ok="$2" detail="${3:-}"
  [[ "$ok" == "1" ]] || fails=$((fails + 1))
  printf '  %s → %s%s\n' "$name" "$([[ "$ok" == 1 ]] && echo PASS || echo FAIL)" \
    "${detail:+  $detail}"
}
die_judge() { printf '\n!! JUDGE BROKEN — %s\n' "$1" >&2; exit 3; }

# ---------- audit one tree ----------
# Prints one `<file>:<line> → PASS|FAIL` per discovered site, and a trailing `#SITES <n> <fails>`
# the shell reads back. Discovery is done in python3 (already a hard prerequisite of this repo)
# because the mention and its conditional routinely straddle a line break, which no line-oriented
# grep can see.
audit_tree() { # <root>
  local root="$1" out rc
  out="$(MENTION="$MENTION" CONDITIONAL="$CONDITIONAL" GUARD="$GUARD" DELEGATE="$DELEGATE" \
         WINDOW="$WINDOW" ROOT="$root" python3 - <<'PYEOF'
import os, re, sys

root = os.environ["ROOT"]
mention = re.compile(os.environ["MENTION"])
conditional = os.environ["CONDITIONAL"]
guard, delegate = os.environ["GUARD"], os.environ["DELEGATE"]
window = int(os.environ["WINDOW"])

# The conditional must follow the mention CLOSELY — within this many characters of flattened text.
# Wide enough for a line wrap ("...`demo-app/.git` is\n   missing:"), far too narrow to pair a
# mention in one paragraph with an unrelated "is missing" further down the page.
NEAR = 60

docs = []
for dirpath, dirnames, filenames in os.walk(root):
    dirnames[:] = [d for d in dirnames if d not in (".venv", ".git", "node_modules", "__pycache__")]
    for fn in filenames:
        if fn.endswith(".md"):
            docs.append(os.path.join(dirpath, fn))
docs.sort()

sites = fails = 0
mentioning = 0
for path in docs:
    try:
        raw = open(path, encoding="utf-8").read()
    except (OSError, UnicodeDecodeError):
        continue
    if not mention.search(raw):
        continue
    mentioning += 1
    lines = raw.splitlines()
    # Flatten line breaks and collapse whitespace runs in ONE pass, carrying a parallel map from
    # each surviving character back to its source line. Doing the collapse as a second pass would
    # shift every offset out from under the map, which is how a judge starts reporting the wrong
    # line number — and a wrong line number in a failure message is a failure nobody can act on.
    flat_chars, line_map, prev_space = [], [], False
    for i, ln in enumerate(lines, 1):
        for ch in ln + " ":
            is_space = ch in " \t"
            if is_space and prev_space:
                continue
            flat_chars.append(" " if is_space else ch)
            line_map.append(i)
            prev_space = is_space
    flat = "".join(flat_chars)

    for m in mention.finditer(flat):
        tail = flat[m.end(): m.end() + NEAR]
        if not re.search(conditional, tail):
            continue                              # a mention, not an instruction — not policed
        sites += 1
        n = line_map[m.start()]
        lo, hi = max(1, n - window), min(len(lines), n + window)
        # Flatten the context the SAME way the mention was found. Matching the guard phrase against
        # raw lines while discovering the mention across a wrap makes the judge demand a formatting
        # accident: a correctly guarded paragraph fails purely because the phrase happened to
        # straddle a newline. (It did — CLAUDE.md, immediately after being fixed.) One rule, one
        # normalization, both sides.
        ctx = re.sub(r"\s+", " ", "\n".join(lines[lo - 1: hi]))
        how = None
        if guard in ctx:
            how = "guarded inline"
        elif delegate in ctx:
            how = "delegates to the guarded copy"
        ok = how is not None
        if not ok:
            fails += 1
            how = "states the init precondition with NO directory-existence guard"
        rel = os.path.relpath(path, root)
        print("  %s:%d → %s  %s" % (rel, n, "PASS" if ok else "FAIL", how))

print("#SITES %d %d %d" % (sites, fails, mentioning))
PYEOF
)"; rc=$?
  [[ $rc -eq 0 ]] || die_judge "the discovery pass itself failed (python3 exit $rc):
$out"

  local tally; tally="$(grep '^#SITES ' <<<"$out")"
  [[ -n "$tally" ]] || die_judge "the discovery pass produced no tally line"
  grep -v '^#SITES ' <<<"$out"

  local s f mentioning
  read -r _ s f mentioning <<<"$tally"
  sites=$((sites + s)); fails=$((fails + f))

  if [[ "$mentioning" -eq 0 ]]; then
    die_judge "no document under $root mentions demo-app/.git at all.
      Either the precondition was removed everywhere (then delete this judge deliberately) or the
      pattern no longer matches. A judge that finds zero sites reports success while checking nothing."
  fi
  if [[ "$s" -eq 0 ]]; then
    die_judge "$mentioning document(s) mention demo-app/.git but NONE was classified an instruction.
      The conditional pattern stopped matching. Under-discovery in a self-discovering judge reads
      as a pass — re-anchor CONDITIONAL or delete this judge deliberately."
  fi
}

# ---------- --self-test: prove a NEW unguarded site is caught (E-03) ----------
# The point of self-discovery is the site nobody told the judge about. So the mutation is not an
# edit to a known file — it is a brand-new document, in a throwaway copy of the docs. The real tree
# is never written to.
self_test() {
  local dir; dir="$(mktemp -d)"
  # shellcheck disable=SC2064
  trap "rm -rf '$dir'" EXIT
  (cd "$REPO" && grep -rlE "$MENTION" --include='*.md' . 2>/dev/null | grep -v '/\.venv/' \
     | while IFS= read -r f; do mkdir -p "$dir/$(dirname "$f")"; cp "$f" "$dir/$f"; done)

  printf '=== --self-test: a NEW document restates the precondition without a guard ===\n'
  printf 'Written to a throwaway copy (%s), never to the repo.\n' "$dir"
  cat > "$dir/NEWCOMER.md" <<'EOF'
# Some future document

1. **Ensure the work repo is ready.** If `demo-app/.git` does not exist, run:
   `git -C demo-app init -b main`
EOF
  fails=0; sites=0
  local report; report="$(audit_tree "$dir")"
  printf '%s\n' "$report"

  # The assertion is about THE NEW FILE specifically, not about `fails > 0`. While the repo still
  # has unguarded sites of its own, "some check went red" would pass even if NEWCOMER.md were never
  # discovered — a self-test that green-lights on somebody else's failure proves nothing.
  if ! grep -q '^  NEWCOMER\.md:[0-9]* → FAIL' <<<"$report"; then
    printf '\n!! SELF-TEST FAILED — the new unguarded document was not discovered and failed.\n' >&2
    printf '   Self-discovery is the entire point; without it this is a hardcoded list.\n' >&2
    return 3
  fi
  # `audit_tree` ran in a command substitution, so its globals did not survive; count from the
  # report itself rather than printing a stale 0.
  local n; n="$(grep -c ' → \(PASS\|FAIL\)' <<<"$report")"
  printf '\n--self-test → PASS  (the unplanned site NEWCOMER.md was discovered and failed; %s site(s) audited)\n' "$n"
  return 0
}

main() {
  case "${1:-}" in
    --self-test) self_test; exit $? ;;
    "") ;;
    *) printf 'usage: test_precondition_parity.sh [--self-test]\n' >&2; exit 64 ;;
  esac

  printf 'demo-app precondition parity — sites DISCOVERED by grep, never listed\n'
  printf 'A site must either carry the guard %s or delegate: %s\n\n' "$GUARD" "$DELEGATE"
  audit_tree "$REPO"

  printf '\n%d site(s) audited. %s\n' "$sites" \
    "$([[ $fails -eq 0 ]] && echo "all PASS" || echo "$fails FAILED")"
  printf 'Run --self-test to prove an unplanned sixth site would be caught (E-03).\n'
  [[ $fails -eq 0 ]] || exit 1
}

main "$@"
