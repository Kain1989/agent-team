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
# WHERE IT LOOKS — THE INSTRUCTION SURFACE ONLY. Documents that TELL someone (a human or a model)
# to do something: skills/, .claude/commands/, CLAUDE.md, README.md. Not CHANGELOG.md, not
# standup/log/, not .standup/ progress files. Those are RECORDS: a changelog entry describing this
# very bug quotes the old unguarded wording on purpose, and a judge that reads it as an instruction
# demands the history be rewritten to stay green. The first cut walked all 43 markdown files and
# passed only because a changelog narrative happened to repeat the guard phrase two lines later —
# green by coincidence, and it would have blocked CI on the next entry that recounted an old bug.
#
# WHAT COUNTS AS A SITE. Two classes, both narrow on purpose:
#   1. INIT — a mention of `demo-app/.git` followed closely by a conditional ("is missing" / "does
#      not exist"). SECURITY.md mentions `demo-app/.git` to say it is gitignored; that is not a
#      precondition and is deliberately not policed.
#   2. TARGET — telling the reader to submit a code task against `project:demo-app`. Same defect,
#      different sentence: with the sample deleted, `/portal` still sent people at a directory that
#      is not there. Class 1 is blind to it by construction (no `demo-app/.git` in the text), which
#      is exactly why it needed naming rather than assuming one pattern covered the surface.
#
# WHAT IT DOES *NOT* COVER — stated because the alternative is letting the next reader assume it is
# total. It matches two literal patterns and four wordings. A paraphrase ("if demo-app is not yet a
# git repo"), an inversion ("when there is no demo-app/.git"), or an instruction living in a prompt
# template rather than a document will NOT be classified. The ADVISORY pass below exists for that:
# it scans wider, judges nothing, and prints anything that looks like a demo-app init instruction it
# did not classify. Advisory rather than failing because a classifier loose enough to catch every
# paraphrase also fires on prose, and a judge that cries wolf gets switched off — but silent
# under-discovery is the worse failure, so the gap is made visible instead of hidden.
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
# Seam so the judge can be aimed at a fixture tree. Without it the judge always scanned the
# real repo no matter the cwd, which meant any attempt to test IT had to mutate the real
# tree — the one thing every judge here is forbidden to do.
REPO="${STANDUP_PARITY_ROOT:-$(cd "$HERE/../../.." && pwd)}"

MENTION='demo-app/\.git'
CONDITIONAL='is missing|does not exist|do not exist|does not yet exist'
GUARD='(and a `demo-app/` exists)'
DELEGATE='see `/agent-team:standup` step 1'
TARGET_MENTION='project:demo-app'
TARGET_GUARD='if a `demo-app/` exists'          # or a pointer to the roster, see TARGET_ALT
# Must express an ALTERNATIVE, not merely name the roster file. `standup/team.json` alone let a
# wholly unconditional instruction pass because an unrelated sentence nearby happened to
# mention the roster — an escape hatch that grants itself.
TARGET_ALT="otherwise any developer's \`folder\` from \`standup/team.json\`"
WINDOW=4          # lines either side of the instruction line the guard may appear on

# The instruction surface. Everything else in the tree is a record, not an order. Paths are relative
# to the repo root; a directory entry covers everything beneath it.
# `hooks/` is here because supervisor_charter.py / route_reminder.py / supervisor_gate.py
# INJECT their text into the model's context at SessionStart and on every prompt. By this
# judge's own definition — a document that tells someone (human or model) to do something —
# the charter is the purest instruction surface in the repo, and it was the one surface no
# rule and no advisory could see.
INSTRUCTION_PATHS=('skills' '.claude/commands' 'hooks' 'CLAUDE.md' 'README.md')

fails=0
sites=0
# (No local check() here: verdicts are printed by the python discovery pass, which owns the tally.
# A second, unreachable copy of check() lived here for a while — dead code in a judge is one more
# thing a reader can mistake for coverage.)
die_judge() { printf '\n!! JUDGE BROKEN — %s\n' "$1" >&2; exit 3; }

# ---------- audit one tree ----------
# Prints one `<file>:<line> → PASS|FAIL` per discovered site, and a trailing `#SITES <n> <fails>`
# the shell reads back. Discovery is done in python3 (already a hard prerequisite of this repo)
# because the mention and its conditional routinely straddle a line break, which no line-oriented
# grep can see.
audit_tree() { # <root>
  local root="$1" out rc
  out="$(MENTION="$MENTION" CONDITIONAL="$CONDITIONAL" GUARD="$GUARD" DELEGATE="$DELEGATE" \
         TARGET_MENTION="$TARGET_MENTION" TARGET_GUARD="$TARGET_GUARD" TARGET_ALT="$TARGET_ALT" \
         INSTRUCTION_PATHS="${INSTRUCTION_PATHS[*]}" \
         WINDOW="$WINDOW" ROOT="$root" python3 - <<'PYEOF'
import os, re, sys

root = os.environ["ROOT"]
mention = re.compile(os.environ["MENTION"])
conditional = os.environ["CONDITIONAL"]
guard, delegate = os.environ["GUARD"], os.environ["DELEGATE"]
target_mention = os.environ["TARGET_MENTION"]
target_guard, target_alt = os.environ["TARGET_GUARD"], os.environ["TARGET_ALT"]
instruction_paths = os.environ["INSTRUCTION_PATHS"].split()
window = int(os.environ["WINDOW"])

# The conditional must follow the mention CLOSELY — within this many characters of flattened text.
# Wide enough for a line wrap ("...`demo-app/.git` is\n   missing:"), far too narrow to pair a
# mention in one paragraph with an unrelated "is missing" further down the page.
NEAR = 60

SKIP_DIRS = (".venv", ".git", "node_modules", "__pycache__")


def collect(base, exts):
    """Every file under `base` (a dir or a single file) with one of `exts`."""
    full = os.path.join(root, base)
    if os.path.isfile(full):
        return [full] if full.endswith(exts) else []
    found = []
    for dirpath, dirnames, filenames in os.walk(full):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        found += [os.path.join(dirpath, fn) for fn in filenames if fn.endswith(exts)]
    return found


docs = sorted({p for base in instruction_paths for p in collect(base, (".md", ".py"))})

sites = fails = 0
mentioning = 0
classified = set()          # (abspath, line) of everything a rule looked at — for the advisory pass
for path in docs:
    try:
        raw = open(path, encoding="utf-8").read()
    except (OSError, UnicodeDecodeError):
        continue
    if not (mention.search(raw) or target_mention in raw):
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
        if ok:
            classified.add((path, n))
        print("  [init]   %s:%d → %s  %s" % (rel, n, "PASS" if ok else "FAIL", how))

    # ---- class 2: TARGET — "submit a code task against project:demo-app" ----
    for m in re.finditer(re.escape(target_mention), flat):
        sites += 1
        n = line_map[m.start()]
        lo, hi = max(1, n - window), min(len(lines), n + window)
        ctx = re.sub(r"\s+", " ", "\n".join(lines[lo - 1: hi]))
        if target_guard in ctx:
            how, ok = "conditional on the sample existing", True
        elif target_alt in ctx:
            how, ok = "offers the roster as the alternative target", True
        else:
            how, ok = ("sends the reader at project:demo-app unconditionally — with the sample "
                       "deleted that target does not exist"), False
            fails += 1
        rel = os.path.relpath(path, root)
        if ok:
            classified.add((path, n))
        print("  [target] %s:%d → %s  %s" % (rel, n, "PASS" if ok else "FAIL", how))

# ---- ADVISORY (judges nothing; makes under-discovery visible) ----
# Wider net, wider file types: anything that looks like an instruction to git-init the sample. Every
# hit the two classifiers above did NOT look at gets printed. A classifier loose enough to catch
# paraphrases fires on prose too, so this reports rather than fails — but an unreported gap in a
# self-discovering judge is the failure its own header warns about.
ADVISORY_HINT = re.compile(r"git\s+-C\s+[\"']?\$?\{?[A-Za-z_]*demo-app|demo-app.{0,40}\bgit init\b"
                           r"|\bgit init\b.{0,40}demo-app", re.I)
# What must be suppressed is the COMMAND BODY of an already-judged instruction — the
# `git -C demo-app init ...` lines under a guarded paragraph. What must NOT be suppressed is a
# second, unguarded instruction that happens to sit nearby.
#
# A proximity radius cannot tell those apart, and tuning it just picks which one to get wrong: at
# 12 a paraphrase planted 3 lines under a passing site vanished; at 2 the four legitimate command
# bodies came back as noise. So the rule is STRUCTURAL instead — suppress only a line that is
# itself a bare shell command near a PASSING site. Prose is never suppressed, which is what a
# paraphrase always is.
#
# Suppression is limited to PASSING sites on purpose: near a FAILING one the surrounding evidence
# is exactly what a reader needs.
#
# RESIDUAL GAP, stated rather than papered over: a paraphrase written AS a bare `git -C demo-app`
# command directly under a passing site is still suppressed. The classifier does not read
# paraphrases at all (see the header) — this narrows the blind spot, it does not remove it.
ADVISORY_NEAR = 6
# NOTE \x60 (backtick) is written as an escape, never literally: this whole python block sits
# inside a `$( ... )` command substitution, and bash scans that for backticks as legacy
# command-substitution delimiters even though the heredoc is quoted. One literal backtick
# here = "unexpected EOF while looking for matching" and the judge will not parse. Same
# disease the workflow engine has with raw backticks in template literals.
ADVISORY_CMD = re.compile(r"^[\x60]{0,3}\s*(git|bash|sh)\s")
advisory = []
for base in instruction_paths + ["standup/standup.workflow.js", "setup.sh", "standup/team.json"]:
    for path in collect(base, (".md", ".py", ".js", ".json", ".sh")):
        try:
            lines2 = open(path, encoding="utf-8").read().splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for i, ln in enumerate(lines2, 1):
            if not ADVISORY_HINT.search(ln):
                continue
            near_pass = any(p == path and abs(q - i) <= ADVISORY_NEAR for (p, q) in classified)
            is_cmd_body = bool(ADVISORY_CMD.match(ln.strip()))
            if near_pass and (is_cmd_body or i in {q for (p, q) in classified if p == path}):
                continue
            if True:
                advisory.append("%s:%d  %s" % (os.path.relpath(path, root), i, ln.strip()[:100]))

print("#ADVISORY %d" % len(advisory))
for a in advisory:
    print("#ADV %s" % a)
print("#SITES %d %d %d" % (sites, fails, mentioning))
PYEOF
)"; rc=$?
  [[ $rc -eq 0 ]] || die_judge "the discovery pass itself failed (python3 exit $rc):
$out"

  local tally; tally="$(grep '^#SITES ' <<<"$out")"
  [[ -n "$tally" ]] || die_judge "the discovery pass produced no tally line"
  grep -v '^#SITES \|^#ADV' <<<"$out"

  # Advisory: printed, never scored. See the header for why this is not a failure.
  local adv_n; adv_n="$(sed -n 's/^#ADVISORY //p' <<<"$out")"
  if [[ "${adv_n:-0}" != "0" ]]; then
    printf '\n  advisory — %s instruction-shaped line(s) NO rule classified (not scored):\n' "$adv_n"
    sed -n 's/^#ADV /    /p' <<<"$out"
    printf '  If one of those is a real precondition, it needs a rule — the classifier is narrow\n'
    printf '  on purpose and does not read paraphrases.\n'
  fi

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
  # Copy the whole instruction surface, so the mutated tree is the real one plus the planted files.
  local p
  for p in "${INSTRUCTION_PATHS[@]}"; do
    if [[ -d "$REPO/$p" ]]; then mkdir -p "$dir/$p" && cp -R "$REPO/$p/." "$dir/$p/"
    elif [[ -f "$REPO/$p" ]]; then mkdir -p "$dir/$(dirname "$p")" && cp "$REPO/$p" "$dir/$p"; fi
  done

  printf '=== --self-test: NEW documents restate BOTH preconditions without a guard ===\n'
  printf 'Written to a throwaway copy (%s), never to the repo.\n' "$dir"
  # One planted file per site class. Planting only the init class would leave the target class with
  # no proof it can fail — the same "covered on paper" gap this judge was revised to close.
  mkdir -p "$dir/skills/newcomer"
  cat > "$dir/skills/newcomer/SKILL.md" <<'EOF'
# Some future document

1. **Ensure the work repo is ready.** If `demo-app/.git` does not exist, run:
   `git -C demo-app init -b main`
2. Then open the portal and submit a code task (target `project:demo-app`).
EOF
  fails=0; sites=0
  local report; report="$(audit_tree "$dir")"
  printf '%s\n' "$report"

  # The assertion is about THE NEW FILE specifically, and about BOTH classes by name — not about
  # `fails > 0`. "Some check went red" would pass on somebody else's failure while the planted site
  # was never discovered, and it would pass with one whole site class unproven.
  local missed=""
  grep -q '^  \[init\]   skills/newcomer/SKILL\.md:[0-9]* → FAIL' <<<"$report" \
    || missed="$missed
    the planted [init] site was not discovered and failed"
  grep -q '^  \[target\] skills/newcomer/SKILL\.md:[0-9]* → FAIL' <<<"$report" \
    || missed="$missed
    the planted [target] site was not discovered and failed"
  if [[ -n "$missed" ]]; then
    printf '\n!! SELF-TEST FAILED — a planted unguarded site was missed:%s\n' "$missed" >&2
    printf '   Self-discovery is the entire point; without it this is a hardcoded list.\n' >&2
    printf '%s\n' "$report" >&2
    return 3
  fi
  # `audit_tree` ran in a command substitution, so its globals did not survive; count from the
  # report itself rather than printing a stale 0.
  local n; n="$(grep -c ' → \(PASS\|FAIL\)' <<<"$report")"
  printf '\n--self-test → PASS  (both planted site classes discovered and failed; %s site(s) audited)\n' "$n"
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
