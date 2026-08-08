#!/usr/bin/env bash
# Version-consistency judge — the release must name itself the same way everywhere.
#
#     bash standup/control/tests/test_version_consistency.sh
#     bash standup/control/tests/test_version_consistency.sh --self-test
#
# WHY THIS EXISTS. Three files declare which release this is, and until 0.5.2 NOTHING asserted that
# any two of them agreed:
#
#   .claude-plugin/plugin.json       "version"   — README calls this the one authoritative place
#   .claude-plugin/marketplace.json  "version"   — its own separate field for the same plugin
#   CHANGELOG.md                     "## [x.y.z]" — the newest entry
#
# During 0.5.2 a wrong version reached a commit message and was caught BY HAND before push. That is
# luck, not a gate, and it is the shape this repo keeps recording: a fact that lives in more than
# one place with no mechanism holding the copies together. The manifests are also the worst place
# for it to happen, because a published manifest is not recoverable by a later edit — an install
# already served the wrong number.
#
# The failure mode is quiet in both directions and neither is theoretical:
#   * bump plugin.json, forget marketplace.json  -> the marketplace serves a version that does not
#     exist, and `/plugin list` disagrees with the file the README tells you to read;
#   * bump both manifests, forget the CHANGELOG  -> a release ships whose notes describe the
#     PREVIOUS one, which is precisely the state 0.5.2 opened in (six merges, no entry).
#
# WHAT "NEWEST" MEANS. The FIRST `## [...]` heading in CHANGELOG.md. Keep a Changelog is
# reverse-chronological, so first is newest — and if a new entry is appended at the BOTTOM instead,
# this judge goes red, which is the correct answer rather than a limitation. A leading
# `## [Unreleased]` is skipped: Keep a Changelog explicitly sanctions that section, so convicting
# it would be crying wolf, and a checker that cries wolf gets switched off.
#
# WHY THE WORKING TREE, NOT THE COMMITTED BLOB. test_release_invariants.sh reads
# `git show HEAD:...` on purpose, because its subjects (the roster, the teammate definitions) are
# things a USER customises, and customising your own install must never redden the shipped suite.
# That reasoning does not transfer here: nobody edits plugin.json, marketplace.json or CHANGELOG.md
# on their own install, and the mistake this judge exists to catch happens in a working tree that
# is ABOUT to be committed. Reading HEAD would make it blind at the one moment it is needed. In CI
# the two are the same bytes anyway, since the runner judges a fresh checkout.
#
# A CASE THIS JUDGE CANNOT ANSWER IS A FAILURE, NOT A PASS. A missing file, JSON that does not
# parse, no marketplace entry for this plugin, no version heading at all — every one of them is a
# defect in the release, so it reddens its case and names what it found. Exit 3 is reserved for the
# judge itself being unable to run. e49f8a1 shipped a judge that printed "all checks PASS" over a
# question it never asked; this one cannot.
#
# Read-only against the tree. `--self-test` mutates a staged COPY (STANDUP_VERSION_ROOT), never the
# real repo.
#
# Exit codes: 0 pass · 1 failures · 3 the judge itself is broken · 64 usage
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# The tree being JUDGED. The judging TOOL is always this script; only the DATA is staged, which is
# what makes the mutations below evidence about the cases rather than about a doctored checker.
ROOT="${STANDUP_VERSION_ROOT:-$(cd "$HERE/../../.." && pwd)}"

PLUGIN_REL=".claude-plugin/plugin.json"
MARKET_REL=".claude-plugin/marketplace.json"
CHANGELOG_REL="CHANGELOG.md"

fails=0
check() { # name ok detail
  local name="$1" ok="$2" detail="${3:-}"
  [[ "$ok" == "1" ]] || fails=$((fails + 1))
  printf '  %s → %s%s\n' "$name" "$([[ "$ok" == 1 ]] && echo PASS || echo FAIL)" "${detail:+  $detail}"
}
die_judge() { printf '\n!! JUDGE BROKEN — %s\n' "$1" >&2; exit 3; }

command -v python3 >/dev/null 2>&1 || die_judge "python3 is not on PATH, so no manifest can be read"

# ------------------------------------------------------------------------------------------------
# Extraction. One python pass prints exactly three lines:
#
#     PLUGIN     <version>  |  !!<why it could not be read>
#     MARKET     <version>  |  !!<why it could not be read>
#     CHANGELOG  <version>  |  !!<why it could not be read>
#
# A `!!` value is carried through to the case as a FAILURE detail rather than being swallowed, so
# an unreadable file can never be mistaken for agreement.
# ------------------------------------------------------------------------------------------------
extract() {
  ROOT="$ROOT" PLUGIN_REL="$PLUGIN_REL" MARKET_REL="$MARKET_REL" CHANGELOG_REL="$CHANGELOG_REL" \
  python3 <<'PY'
import json, os, re

root = os.environ["ROOT"]
plugin_rel = os.environ["PLUGIN_REL"]
market_rel = os.environ["MARKET_REL"]
changelog_rel = os.environ["CHANGELOG_REL"]

# The heading shape Keep a Changelog uses, and the one every entry in this file already has.
HEADING = re.compile(r"^##\s+\[([^\]]+)\]")


def read_json(rel):
    path = os.path.join(root, rel)
    if not os.path.isfile(path):
        return None, "!!%s is missing" % rel
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh), None
    except (ValueError, UnicodeDecodeError) as exc:
        return None, "!!%s does not parse as JSON (%s)" % (rel, exc)


def emit(key, value):
    print("%s %s" % (key, value))


# --- plugin.json -------------------------------------------------------------------------------
plugin, err = read_json(plugin_rel)
plugin_name = None
if err:
    emit("PLUGIN", err)
else:
    plugin_name = plugin.get("name")
    version = plugin.get("version")
    if isinstance(version, str) and version.strip():
        emit("PLUGIN", version)
    else:
        emit("PLUGIN", "!!%s has no non-empty string `version`" % plugin_rel)

# --- marketplace.json --------------------------------------------------------------------------
# The entry is matched by plugin.json's OWN `name`, so the two files are compared for the same
# plugin rather than for a name written twice here. The literal fallback only applies when
# plugin.json could not be read at all — that case is already failing, and it lets MARKET still
# report something useful instead of a second copy of the same error.
wanted = plugin_name if isinstance(plugin_name, str) and plugin_name else "agent-team"
market, err = read_json(market_rel)
if err:
    emit("MARKET", err)
else:
    plugins = market.get("plugins")
    if not isinstance(plugins, list):
        emit("MARKET", "!!%s has no `plugins` list" % market_rel)
    else:
        hits = [p for p in plugins if isinstance(p, dict) and p.get("name") == wanted]
        if not hits:
            emit("MARKET", "!!%s has no entry named %r" % (market_rel, wanted))
        elif len(hits) > 1:
            emit("MARKET", "!!%s has %d entries named %r" % (market_rel, len(hits), wanted))
        else:
            version = hits[0].get("version")
            if isinstance(version, str) and version.strip():
                emit("MARKET", version)
            else:
                emit("MARKET", "!!%s entry %r has no non-empty string `version`" % (market_rel, wanted))

# --- CHANGELOG.md ------------------------------------------------------------------------------
path = os.path.join(root, changelog_rel)
if not os.path.isfile(path):
    emit("CHANGELOG", "!!%s is missing" % changelog_rel)
else:
    newest = None
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            m = HEADING.match(line)
            if not m:
                continue
            label = m.group(1).strip()
            if label.lower() == "unreleased":   # sanctioned by Keep a Changelog; not a release
                continue
            newest = label
            break
    if newest:
        emit("CHANGELOG", newest)
    else:
        emit("CHANGELOG", "!!%s has no `## [x.y.z]` version heading" % changelog_rel)
PY
}

run_cases() {
  local raw plugin market changelog
  raw="$(extract)" || die_judge "the extractor crashed reading $ROOT"
  plugin="$(sed -n 's/^PLUGIN //p'    <<<"$raw")"
  market="$(sed -n 's/^MARKET //p'    <<<"$raw")"
  changelog="$(sed -n 's/^CHANGELOG //p' <<<"$raw")"
  # If any line is absent the extractor did not do its job — that is the judge, not the release.
  [[ -n "$plugin" && -n "$market" && -n "$changelog" ]] \
    || die_judge "the extractor did not report all three versions"

  printf '\nA. the two manifests agree with each other\n'
  if [[ "$plugin" == '!!'* || "$market" == '!!'* ]]; then
    check "manifests-agree" 0 "$(printf '%s %s' "${plugin#!!}" "${market#!!}" | sed 's/^ *//')"
  elif [[ "$plugin" == "$market" ]]; then
    check "manifests-agree" 1 "both say $plugin"
  else
    check "manifests-agree" 0 "$PLUGIN_REL says $plugin, $MARKET_REL says $market"
  fi

  printf '\nB. the release the manifests declare is the one the newest CHANGELOG entry describes\n'
  # Keyed on plugin.json because README names it the single authoritative declaration; case A
  # already binds marketplace.json to it, so the CHANGELOG is transitively bound to both.
  if [[ "$plugin" == '!!'* || "$changelog" == '!!'* ]]; then
    check "changelog-matches-manifests" 0 "$(printf '%s %s' "${plugin#!!}" "${changelog#!!}" | sed 's/^ *//')"
  elif [[ "$plugin" == "$changelog" ]]; then
    check "changelog-matches-manifests" 1 "both say $plugin"
  else
    check "changelog-matches-manifests" 0 \
      "$PLUGIN_REL says $plugin, newest $CHANGELOG_REL heading says [$changelog] — bump the manifests or write the entry"
  fi
}

# ------------------------------------------------------------------------------------------------
# --self-test (E-03). A judge that has not been shown to fail is not a judge.
#
# Every mutation is planted on a staged COPY and must redden ITS OWN named case — off a baseline
# proven green first, because "did it go red" answers yes for the wrong reason on a case that was
# already red. Two CONTROLS plant well-formed input and require the cases to stay green: a mutation
# set proves a check fires on bad input, only a control proves it holds its tongue on good input.
#
# Every case asserts the EXIT STATUS as well as the text, and `verdict-text-and-exit-code-agree`
# pins the two together in both polarities. That is not belt-and-braces: an earlier version of this
# self-test read stdout only, and a judge with `fails` neutralised in check() printed both cases as
# FAIL, printed "all checks PASS", exited 0 on a desynchronised release — and this self-test
# reported PASS over the top of it. CI reads the exit status and nothing else, so the one channel
# that gates the release was the one channel nothing checked.
# ------------------------------------------------------------------------------------------------
stage_root() { # -> a temp dir holding just the three files this judge reads
  local d; d="$(mktemp -d)"
  mkdir -p "$d/.claude-plugin"
  cp "$ROOT/$PLUGIN_REL" "$d/$PLUGIN_REL"
  cp "$ROOT/$MARKET_REL" "$d/$MARKET_REL"
  cp "$ROOT/$CHANGELOG_REL" "$d/$CHANGELOG_REL"
  printf '%s' "$d"
}

# Rewrite one JSON version in place. Deliberately json.load/json.dump rather than sed, so a
# mutation can never fail by producing a file that is merely UNPARSEABLE — that would redden the
# case for the wrong reason and prove nothing about the comparison.
set_version() { # dir  plugin|market  value
  D="$1" WHICH="$2" VALUE="$3" PLUGIN_REL="$PLUGIN_REL" MARKET_REL="$MARKET_REL" python3 <<'PY'
import json, os
d, which, value = os.environ["D"], os.environ["WHICH"], os.environ["VALUE"]
rel = os.environ["PLUGIN_REL"] if which == "plugin" else os.environ["MARKET_REL"]
path = os.path.join(d, rel)
with open(path, encoding="utf-8") as fh:
    doc = json.load(fh)
if which == "plugin":
    doc["version"] = value
else:
    for entry in doc["plugins"]:
        if entry.get("name") == "agent-team":
            entry["version"] = value
with open(path, "w", encoding="utf-8") as fh:
    json.dump(doc, fh, indent=2)
PY
}

# Assert the judged run's TEXT and its EXIT STATUS together.
#
# Text alone is not enough, and the gap was real: this helper used to grep stdout and throw the
# status away, so a judge that printed every case as FAIL and then exited 0 passed its own
# self-test. CI reads the exit code and nothing else — asserting only the channel CI ignores is
# how a judge ends up unable to catch its own breakage, which is the one thing this repo does not
# get to ship twice.
expect() { # label  dir  expected-rc  grep-pattern...
  local label="$1" d="$2" want_rc="$3"; shift 3
  local out rc pat
  out="$(STANDUP_VERSION_ROOT="$d" bash "$0" 2>&1)"; rc=$?
  for pat in "$@"; do
    if ! grep -qF -- "$pat" <<<"$out"; then
      printf '  %-42s → ERROR  expected %s\n' "$label" "$pat" >&2
      sed 's/^/       /' <<<"$out" >&2
      return 1
    fi
  done
  if [[ "$rc" != "$want_rc" ]]; then
    printf '  %-42s → ERROR  the text was right but the judge exited %s, wanted %s.\n' \
      "$label" "$rc" "$want_rc" >&2
    printf '     CI reads ONLY this number, so a green here would gate nothing.\n' >&2
    sed 's/^/       /' <<<"$out" >&2
    return 1
  fi
  printf '  %-42s → as required (exit %s)\n' "$label" "$rc"
  return 0
}

# Whatever the judge decides, it says so THREE times — the per-case rows, the summary line, and the
# exit status — and CI only ever reads the last. Those three must not be able to disagree. A judge
# whose rows read FAIL while its status reads 0 is worse than a broken judge: it is a broken judge
# reporting healthy, on the one channel that gates the release.
verdict_agrees() { # dir  -> 0 when rows, summary and exit status tell the same story
  local out rc rows summary
  out="$(STANDUP_VERSION_ROOT="$1" bash "$0" 2>&1)"; rc=$?
  grep -q '→ FAIL' <<<"$out" && rows=failed || rows=passed
  grep -qE '^[0-9]+ check\(s\) FAILED$' <<<"$out" && summary=failed || summary=passed
  VERDICT_DETAIL="rows=$rows summary=$summary exit=$rc"
  [[ "$rows" == "$summary" ]] || return 1
  if [[ "$rows" == failed ]]; then [[ $rc -ne 0 ]]; else [[ $rc -eq 0 ]]; fi
}

self_test() {
  if ! bash "$0" >/dev/null 2>&1; then
    printf '!! SELF-TEST ABORTED — the unmutated judge is not green.\n' >&2
    printf '   Every case below asks "did this go red"; a case already red answers yes for the\n' >&2
    printf '   wrong reason, so the baseline has to be green first. Fix the tree, then re-run.\n' >&2
    return 3
  fi
  printf '=== --self-test: desynchronise ONE declaration at a time ===\n'
  local rc=0 d

  # CONTROL 1 — an untouched copy must stay green. Without this, every mutation below could be
  # passing because the judge fails on ANY staged tree.
  d="$(stage_root)"
  expect "control-untouched-copy-stays-green" "$d" 0 \
    "manifests-agree → PASS" "changelog-matches-manifests → PASS" || rc=3
  rm -rf "$d"

  # THE VERDICT MUST SPEAK WITH ONE VOICE, in both polarities. Checked before the desync cases
  # because they are the ones that assert on the exit status, and this is what makes that status
  # mean anything. Neutralising `fails` in check() produces a judge that prints every row as FAIL,
  # then prints "all checks PASS" and exits 0 on a genuinely broken release — green in CI, which
  # reads nothing else. A self-test that greps only stdout cannot see that, and this one used to.
  d="$(stage_root)"
  set_version "$d" market "9.9.9"
  if verdict_agrees "$d"; then
    printf '  %-42s → agree on a BROKEN release (%s)\n' "verdict-text-and-exit-code-agree" "$VERDICT_DETAIL"
  else
    printf '  %-42s → ERROR  the judge contradicted itself: %s\n' \
      "verdict-text-and-exit-code-agree" "$VERDICT_DETAIL" >&2
    printf '     CI reads only the exit status, so this combination ships as green.\n' >&2
    rc=3
  fi
  rm -rf "$d"

  d="$(stage_root)"
  if verdict_agrees "$d"; then
    printf '  %-42s → agree on a CLEAN release (%s)\n' "verdict-text-and-exit-code-agree" "$VERDICT_DETAIL"
  else
    printf '  %-42s → ERROR  the judge contradicted itself: %s\n' \
      "verdict-text-and-exit-code-agree" "$VERDICT_DETAIL" >&2
    rc=3
  fi
  rm -rf "$d"

  # A — marketplace.json alone. Chosen over plugin.json for the clean diagonal: case A must go red
  # while case B, which does not read this file, must stay GREEN. That second half is what pins B
  # to plugin.json rather than to "any manifest".
  d="$(stage_root)"
  set_version "$d" market "9.9.9"
  expect "manifests-disagree-with-each-other" "$d" 1 \
    "manifests-agree → FAIL" "changelog-matches-manifests → PASS" || rc=3
  rm -rf "$d"

  # B — the newest CHANGELOG heading alone. The mirror: case B red, case A green, which pins A to
  # the two manifests rather than to the changelog.
  d="$(stage_root)"
  python3 - "$d/$CHANGELOG_REL" <<'PY'
import re, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    lines = fh.readlines()
for i, line in enumerate(lines):
    if re.match(r"^##\s+\[", line):
        lines[i] = re.sub(r"\[[^\]]+\]", "[9.9.9]", line, count=1)
        break
with open(path, "w", encoding="utf-8") as fh:
    fh.writelines(lines)
PY
  expect "changelog-behind-the-manifests" "$d" 1 \
    "changelog-matches-manifests → FAIL" "manifests-agree → PASS" || rc=3
  rm -rf "$d"

  # C — plugin.json alone. BOTH cases must go red, and that is the assertion, not a side effect:
  # it is the only thing proving each case actually reads plugin.json rather than comparing the
  # other two files, or a constant, to each other. A and B above would both pass a judge that had
  # this release's number hardcoded in it.
  d="$(stage_root)"
  set_version "$d" plugin "9.9.9"
  expect "plugin-manifest-is-the-one-both-read" "$d" 1 \
    "manifests-agree → FAIL" "changelog-matches-manifests → FAIL" || rc=3
  rm -rf "$d"

  # D — the marketplace entry for this plugin is GONE. A question the judge cannot answer must
  # fail loudly; the shape this repo has already shipped once is a judge printing "all checks PASS"
  # over a case it silently skipped.
  d="$(stage_root)"
  python3 - "$d/$MARKET_REL" <<'PY'
import json, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    doc = json.load(fh)
doc["plugins"] = [p for p in doc["plugins"] if p.get("name") != "agent-team"]
with open(path, "w", encoding="utf-8") as fh:
    json.dump(doc, fh, indent=2)
PY
  expect "marketplace-entry-missing-is-a-failure" "$d" 1 \
    "manifests-agree → FAIL" "has no entry" || rc=3
  rm -rf "$d"

  # E — a CHANGELOG with no version heading at all. Same reasoning as D from the other file.
  d="$(stage_root)"
  python3 - "$d/$CHANGELOG_REL" <<'PY'
import re, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    text = fh.read()
with open(path, "w", encoding="utf-8") as fh:
    fh.write(re.sub(r"(?m)^##\s+\[", "## ", text))
PY
  expect "changelog-with-no-version-heading-fails" "$d" 1 \
    "changelog-matches-manifests → FAIL" "no \`## [x.y.z]\` version heading" || rc=3
  rm -rf "$d"

  # CONTROL 2 — a Keep a Changelog `## [Unreleased]` section above the newest release must NOT
  # redden anything. The rule is the same one this repo applies to every lint it owns: firing on
  # correct input is how a check gets switched off, and Unreleased is correct input.
  d="$(stage_root)"
  python3 - "$d/$CHANGELOG_REL" <<'PY'
import re, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    lines = fh.readlines()
for i, line in enumerate(lines):
    if re.match(r"^##\s+\[", line):
        lines.insert(i, "## [Unreleased]\n\n")
        break
with open(path, "w", encoding="utf-8") as fh:
    fh.writelines(lines)
PY
  expect "control-unreleased-section-stays-green" "$d" 0 \
    "manifests-agree → PASS" "changelog-matches-manifests → PASS" || rc=3
  rm -rf "$d"

  if [[ $rc -eq 0 ]]; then
    printf '\n--self-test → PASS  (each desync reddened its OWN case; both well-formed controls stayed green)\n'
  fi
  return $rc
}

main() {
  case "${1:-}" in
    --self-test) self_test; exit $? ;;
    "") ;;
    *) printf 'usage: test_version_consistency.sh [--self-test]\n' >&2; exit 64 ;;
  esac
  printf 'version-consistency judge — read-only; judging %s\n' "$ROOT"
  run_cases
  printf '\n%s\n' "$([[ $fails -eq 0 ]] && echo "all checks PASS" || echo "$fails check(s) FAILED")"
  printf 'Run --self-test to prove each declaration can fail on its own (E-03).\n'
  [[ $fails -eq 0 ]] || exit 1
}

main "$@"
