export const meta = {
  name: 'standup-mvp',
  // The SDLC sequence below is ONE sequence with ONE canonical source: standup/team.json ->
  // manager.policy.sdlc_pipeline. It is restated here (and nowhere else that can drift silently)
  // because `meta` is what the Workflow tool shows the user. Note what is NOT written here: a lens
  // COUNT. Green is derived from the lenses actually planned for the task (see the Work phase) —
  // printing "2-lens review" is how a 3rd lens gets added and silently ignored, which is the same
  // false-promise defect as advertising a gate that never runs.
  description: 'Slim, shareable squad standup + gated SDLC work pipeline: per-dev standup (with persistent progress files) -> squad sync -> design pass -> EM board -> light staff pulse -> gated INTAKE -> INVESTIGATE -> PLAN -> PLAN CHALLENGE -> IMPLEMENT -> TEST GATE -> REVIEW (the lenses planned for the task) -> COMMIT ON GREEN. No external services. Run it directly (Workflow tool) over the MVP roster: the whole board (args.work) or ONE task (args.task) — both run the SAME loop.',
  phases: [
    { title: 'Comms',      detail: 'optional: a comms_triage staff agent reads a local messages/inbox/ -> action items (skipped unless an active comms_triage exists). Skipped entirely on the single-task path (args.task)' },
    { title: 'Standup',    detail: 'one read-only agent per active developer; reads <folder>/.standup/<dev>.md to resume context' },
    { title: 'Team Sync',  detail: 'per-squad merge: squad board + cross-project dependencies (an agent phase tag, not a phase() call)' },
    { title: 'Design',     detail: 'design_lead runs the deterministic judge (control/verify_design_quality.js) over the live UI, then judges the [JUDGMENT] rules of DESIGN_RULEBOOK.md; every finding cites a rule id. Runs BEFORE Synthesize so its tasks land on THIS tick\'s board instead of in a progress file nobody reads' },
    { title: 'Synthesize', detail: 'EM merges squad boards into one ranked board' },
    { title: 'Staff Pulse',detail: 'light-but-real lens from pm_agent (scope/say-no) + design_lead (delivery of the design queue) + product_qa (actually uses the product)' },
    { title: 'Arm',        detail: 'code-writing runs only: arms standup/control/team_run_active so dispatched dev agents can write their project folder. Subagents inherit the EM cwd (the Task tool has no cwd parameter — anthropics/claude-code#12748) and the supervisor gate identifies the EM by cwd, so without this the whole run finishes with an empty diff' },
    { title: 'Work',       detail: 'the gated SDLC per task, identical on both entry paths: INTAKE (pm_agent turns the raw ask into an outcome contract; the supervisor gates it — one revision, one recheck, then the run STOPS) -> INVESTIGATE -> PLAN -> PLAN CHALLENGE (fresh ctx) -> IMPLEMENT+tests -> TEST GATE -> REVIEW (pair + correctness + conventions+tests, PLUS a design-quality lens driven by the squad\'s declared review_surface) -> COMMIT ON GREEN (feature branch, no push)' },
  ],
}

// ---- inputs ----
// args = { date, since, roster, work:false, maxTasks:2 }  — pr/merge/deploy are intentionally absent (MVP).
// The harness may deliver args as a JSON-encoded STRING. Parsing it is fine; SWALLOWING a
// parse failure is not. `A = null` used to look like "a few missing parameters" — it is not.
// It silently CHANGES WHICH PIPELINE RUNS: args.task disappears, so a single-task dispatch
// falls into the whole-roster standup shape; DO_WORK goes false, so there is no Work phase at
// all and the run is structurally incapable of producing code; the roster falls back to the
// embedded copy, so every squad gets polled; DATE becomes 'UNKNOWN-DATE'.
// Observed for real (2026-08-03): one unescaped double-quote inside a task string made a
// one-task run spend 38 agents standing up nine unrelated squads, and nothing errored.
// This is the same disease as a review apparatus pointed the wrong way — it never fails,
// it just quietly stops doing the thing you asked for. So: unparseable args THROW.
// The real cure is on the calling side — hand the Workflow tool an OBJECT, not a JSON string;
// objects never pass through hand-written escaping, so the failure mode disappears at source.
// NOTE the second line of defence, added later: there is no longer any embedded roster to fall
// back to, so a nulled `args` now stops at the roster guard instead of quietly polling a hardcoded
// team. Both gates are load-bearing — the throw here is what keeps the DIAGNOSIS right. Soften it
// and the user is told "args.roster was not provided" when the real fault was a stray quote.
let A = args
if (typeof A === 'string') {
  try { A = JSON.parse(A) }
  catch (e) {
    const _pos = Number((String(e.message).match(/position (\d+)/) || [])[1] || 0)
    throw new Error('args was a JSON string and failed to parse: ' + e.message
      + ' — refusing to fall back to a whole-roster standup, which would silently turn one'
      + ' task into a full poll that cannot produce code. Fix: pass args as an OBJECT, not a'
      + ' JSON string (unescaped quotes and newlines inside task text are the usual cause).'
      + ' Near the error: '
      + JSON.stringify(String(args).slice(Math.max(0, _pos - 90), _pos + 90)))
  }
}
const DATE    = (A && A.date)    || 'UNKNOWN-DATE'
const SINCE   = (A && A.since)   || '6 hours ago'
const DO_WORK = !!(A && A.work)
const MAXTASK = (A && A.maxTasks) || 2
const DO_DESIGN = !!(A && A.design)   // deep design tick: sweep every surface instead of a rotation
// The running instance the design gate judges. A PARAMETER, never a baked-in default: point it at
// YOUR app (e.g. args.designUrl = "http://127.0.0.1:8770" for the bundled portal). A roster entry
// may override it per-dev/per-squad with a `url` field. Empty => the agent must derive the URL from
// the project's own run method and say so; it may NOT skip the gate.
const DESIGN_URL = (A && A.designUrl) || ''

// Mechanical evidence-gathering (standup reporters, comms, pulse) can use a cheaper tier;
// the EM/developer work (plan/challenge/implement/review/sync) inherits the top session model.
// Leave MECH_MODEL undefined to make every agent inherit the session model (simplest for a shared MVP).
const MECH_MODEL = undefined

// ---- EFFORT TIERS (Workflow opts.effort; omit => inherit the session's effort) ----
// Not every agent in a run is doing the same KIND of thinking, and until this existed they all ran
// at one depth. Tiering follows the current model guidance: `low` for sub-agents doing simple,
// mechanical work; `high` as the floor for intelligence-sensitive work; `xhigh` for coding and
// agentic loops (what a coding agent uses by default).
const E_MECH  = 'low'     // evidence-gathering, polling, mechanical git steps
const E_JUDGE = 'high'    // review, product judgment, design, synthesis
const E_BUILD = 'xhigh'   // implementation (coding / agentic loops)
// DELIBERATELY left inheriting — this is a judgment call, not an oversight:
//   `sync`       merging a handful of reports; middling depth, and it is on the critical path.
//   `comms`      triage decides whether a thread is CLOSED; downgrading closure judgment is how
//                already-answered items get re-raised as todos. Not cheapening it.
//   `testgate`   mostly running commands; the HONESTY check over it is what needs depth (E_JUDGE).
// `max` is used nowhere: it shows diminishing returns and can overthink. If you want to move any
// of these, measure it on your own eval first — do not tune effort by intuition.

// Persona text (second-person identity instructions). Authored once here and transcribed VERBATIM
// into standup/team.json so the embedded fallback and the source-of-truth roster stay consistent —
// same contract as the roster fields below (team.json is authoritative; this is the fallback).
const PERSONA_PM = `[THIS IS YOUR PERSONA, not background reading. It comes BEFORE every checklist you are handed.]

You are the product owner for this product, working the way Steve Jobs worked. That means concrete behavior, not adjectives:

- Your default answer is NO. For a proposal to survive it must say which user experience it makes better; if it cannot, cut it. You measure yourself by what you turned DOWN this period, not by what you shipped.
- You reason backward from the EXPERIENCE, never forward from the feature. First ask "what should a user feel, and what can they decide, after looking at this screen?" — then ask what that requires. Never accept an experience decomposed into a feature checklist ticked off one by one.
- "This should not exist at all" is a legitimate and common conclusion. You may reach it about a whole page, a whole tab, a whole product line, and you do NOT need to cite a rule id to say it. The rulebook catches known defects; it does not bound what you are allowed to think.
- A product is one thing, not a pile of pages. You own the judgment "do these screens look like the same product, made by the same team?" — a call an outsider makes in a glance, and you are not allowed to be unable to make it.
- Detail IS the product. A dot stretched into an ellipse is not a "small issue"; it means nobody cared. You speak to that standard.
- You do not flatter anyone's taste, including the person who runs the team. They engaged you for independent product judgment; when you think they are wrong, say so and show what right looks like.

You will be handed rules, rubrics, checklists. They are your FLOOR, not your verdict. A product that passes every rule can still be a bad product — saying that out loud is exactly why you exist.`

const PERSONA_DESIGN_LEAD = `[THIS IS YOUR PERSONA, not background reading. It comes BEFORE every checklist you are handed.]

You are the design lead for this product. You own BOTH whether a screen can be READ and whether the product feels like one crafted thing. You work to the Apple Human Interface Guidelines for clarity and to a high, crafted-quality bar for finish.

- You judge whether a reader can read the TRUTH off a screen, not whether it violates some rule. A screen that passes every rule but leads a reader to a wrong conclusion is more dangerous than an ugly one — that is your first target.
- You may say "this page's information architecture is wrong", or "this is not one product, it is several people each doing their own thing" — and you do NOT need a rule id to say it. But you must then give the shape it SHOULD take: the hierarchy, what disappears, what the single focus is. Criticism that hands over no shape is worthless.
- Clarity over beauty, beauty over novelty — in that order when they conflict.
- Consistency is part of clarity AND part of craft: the same concept must look the same and be named the same across pages; work out of one workshop must be recognizable as one workshop. Cross-page inconsistency makes a user think they are in two products.
- Defaults are a design decision. A library's default marker, default palette, or default legend placement shipping in the product means nobody made a decision here.

The rulebook is your FLOOR. You OWN the A-D rules (what good design is) — finding a defect class the rulebook does not yet name, and writing it up as a new rule, is YOUR job, not the supervisor's. The supervisor owns only the E meta-rules (a design finding must be citable, verifiable, and actually assigned to someone).`

const PERSONA_PRODUCT_QA = `[THIS IS YOUR PERSONA, not background reading. It comes BEFORE every checklist you are handed.]

You are the person opening this product for the first time to get a real thing done. You are not a test engineer, not a reviewer, you carry no rule table — you are a user.

- Your one criterion is: can I get the thing done. If you cannot — or you can, but the path made you irritated, hesitant, or forced to guess — that is a defect. "Technically not broken" is not a pass.
- You are entitled to say "I don't understand what this screen is telling me", and that sentence is a COMPLETE report on its own. You do not need to point at a rule that was violated — saying which step you were on, what you were trying to do, what you saw, and why you are lost is enough. This team already has roles who can cite rules; the only one who can honestly say they are lost is you.
- You must actually click. Reading the code and inferring "this should be fine" is a dereliction. If it will not open, fails to load, or takes forever, record it exactly — that is what a user hits.
- You span the whole product and belong to no lane. "Not my job" does not exist for you. The same concept named two different things on two pages, two pages whose numbers do not agree, a page that looks like a different company made it — those are all yours to report, and often only you will, because everyone else looks only at their own slice.
- Do not invent problems just to hand something in. If it worked, say it worked and say which path you got through. A QA who must find three problems every tick will start fabricating them — worse than no QA.

You are the only role on this team that USES the product. Before this role existed, an obvious visible problem could sit until someone happened to open a random tab.`

const QA_FOCUS = 'Use the product AS A USER and report — do not review code or diffs. Each tick, pick 1-2 real user tasks (e.g. "see what is awaiting my approval and approve one", "read today\'s board and find the single top task"), run them from scratch on the live running instance, and record: which step you got stuck on, what was unreadable, which number disagreed with another screen, where it looks like nobody cared. Cover every UI the product has, rotate, do not fixate on one. You do NOT judge whether the design is good (that is the design lead) or whether a surface should exist (that is the PM) — you judge "can I get the thing done with it". Output must be reproducible: URL + steps + what you saw vs what you expected.'
const QA_CHARTER = 'Your output is a WALKTHROUGH, not a restatement of a defect list. Every problem carries: which step you reached, what you were trying to do, what you saw, and why it stops you getting the thing done. You are allowed and encouraged to report "I can\'t say exactly what is wrong but something is off" — mark it a vibe and say what produced the feeling; that signal is exactly the kind every existing gate misses. Forbidden: running a machine check and copying its output (that is the design lead\'s job, and the machine already did it).'


// ---- ROSTER RESOLUTION — no embedded fallback ----
// There used to be a hardcoded EMBEDDED_ROSTER here, used whenever args.roster was absent, falsy,
// or an unparseable string. It made the worst failure in this engine silent: a run handed no
// roster did not stop, it quietly worked a DIFFERENT team than the one in standup/team.json, and
// reported green. Measured on five inputs — key absent, `undefined`, `''`, `{}` and a truncated
// JSON string — every one produced a full, clean-looking tick against the embedded copy.
//
// Note the asymmetry that made it obviously wrong once seen: three lines above, an unparseable
// `args` STRING throws. An unparseable `args.roster` string was caught and swallowed. Same
// failure, same file, opposite treatment.
//
// Resolution is now recorded, not acted on, because `stopTick` is a `const` declared further down
// and calling it here is a TDZ ReferenceError (the trap this file documents at the `verdict`
// helper). Everything below tolerates an empty roster; the single guard after `stopTick` reports
// it. One stop mechanism, one message vocabulary.
let ROSTER_ERROR = null
let RAW = (A && A.roster)
if (typeof RAW === 'string') {
  try { RAW = JSON.parse(RAW) }
  catch (e) {
    ROSTER_ERROR = `args.roster was a JSON string and failed to parse (${e.message})`
    RAW = null
  }
}
if (RAW && typeof RAW !== 'object') {
  ROSTER_ERROR = `args.roster was a ${typeof RAW}, not an object`
  RAW = null
}
// `{teams: "..."}` used to escape the stop vocabulary entirely: `.map` on a string threw a bare
// TypeError, which still halts the run but in the shape of a crash rather than the three-line block
// the reader is meant to get. Shape-check the two fields the engine actually walks.
if (RAW && RAW.teams !== undefined && !Array.isArray(RAW.teams)) {
  ROSTER_ERROR = `args.roster.teams was a ${typeof RAW.teams}, not an array`
  RAW = null
}
if (RAW && RAW.staff !== undefined && !Array.isArray(RAW.staff)) {
  ROSTER_ERROR = `args.roster.staff was a ${typeof RAW.staff}, not an array`
  RAW = null
}
if (!RAW) {
  if (!ROSTER_ERROR) ROSTER_ERROR = 'args.roster was not provided'
  RAW = {}
}
const TEAMS = (RAW.teams || [{ id: 'workspace', name: 'Workspace', mission: '', coordination: '', developers: RAW.developers || [] }])
  .map(t => ({ ...t, developers: (t.developers || []).filter(d => d.active) }))
  .filter(t => t.developers.length > 0)
const DEVS = TEAMS.flatMap(t => t.developers.map(d => ({ ...d, _team: t.id })))
const STAFF = (RAW.staff || []).filter(s => s.active)
const PM_AGENT = STAFF.find(s => s.id === 'pm_agent') || null
const SUP_RUBRIC = (RAW.manager && RAW.manager.supervisor && RAW.manager.supervisor.rubric)
  || 'Question every requirement; delete the part/step before optimizing it; simplify what survives. Be the validation bottleneck that contains error propagation; prefer subtraction to addition.'

// ---- TRANSCRIPT VOCABULARY ----
// This product's UI is a terminal transcript. The log stream below is the one surface every user
// reads on every run, and it carries ZERO colour and ZERO ANSI — deliberately, so it survives a log
// file, a CI capture, a screen reader and the portal. That leaves casing, indentation, separators
// and terminality as the ONLY hierarchy devices available, which makes consistency in them the
// whole design rather than a nitpick. These three helpers are the single definition of that
// vocabulary; every stop, verdict and tally goes through them instead of hand-writing the strings.
// (Six hand-written variants of one message IS the E-02 defect, committed while fixing E-02
// defects.) NOTE the hard constraint on the shape: this engine is executed inside
// `new Function(args, agent, parallel, pipeline, phase, log, workflow, budget, ...)` with NO
// require/import, so a shared control/transcript.js module is not loadable here — a top-of-file
// helper block is the only form available.

// A verdict is typographically distinct from a step, on EVERY verdict: `→` introduces a verdict and
// nothing else. It already meant that on three lines of this file and was missing from three more.
const verdict = (subject, state, detail) => log(`  ${subject} → ${state}${detail ? `  ${detail}` : ''}`)

// A run that STOPPED must not end in the shape of a run that FINISHED.
// Loudness in a monochrome plaintext stream is position + shape + terminality, not capital letters:
// a "loud stop" rendered as the 16th log line, in the same weight as `BOARD 5 item(s)`, is not
// loud. So a stop prints a three-line block — what is wrong (with the offending value quoted) /
// the enumerated valid set / the single fix naming the file — then the closing line, then nothing.
// The enumeration is ALWAYS generated from the roster at runtime, never hardcoded: an error that
// withholds the options just moves the guessing to the human, and a hardcoded list is one more
// thing that drifts. The message is emitted through log() BEFORE the throw, because a thrown
// workflow may surface to the user as nothing but a stack trace — which would discard the
// naming-the-valid-options payoff that is the entire user-visible point of this.
const stopTick = (what, validNoun, valid, fix) => {
  log(`STOP — ${what}`)
  log(`  valid ${validNoun}: ${(valid && valid.length) ? valid.join(', ') : '(none declared in the roster)'}`)
  log(`  fix: ${fix}`)
  log(`TICK STOPPED ${DATE} — ${what}`)
  // Same rendering on both surfaces. The log said "(none declared in the roster)" while the thrown
  // Error carried an empty string — and the caller sees the Error, so the one audience that gets a
  // stack trace got the degraded version of the message.
  const validText = (valid && valid.length) ? valid.join(', ') : '(none declared in the roster)'
  const e = new Error(`STOP — ${what} | valid ${validNoun}: ${validText} | fix: ${fix}`)
  e.tickStopped = true
  throw e
}

// ---- THE ROSTER GUARD ----
// Placed HERE, immediately after `stopTick` exists and before any phase runs. Not earlier: the
// roster is resolved ~40 lines up, but `stopTick` is a `const` and reaching it from there is a TDZ
// ReferenceError. Not later: a run with nobody on it must not reach a phase at all.
//
// Why an empty roster has to STOP rather than produce an empty board. Measured on three shapes —
// `{}`, `{teams:[],staff:[]}`, and a roster whose every developer is `active:false` — the engine
// produced BYTE-IDENTICAL output for all three, because `.filter(t => t.developers.length > 0)`
// collapses "nobody active" and "no squads" into the same state. It ran Comms, Standup, Design,
// Synthesize, Staff Pulse and **Arm**, then printed `TICK DONE — 0 task(s)`, which reads like
// success. The Arm step is the expensive part of that: it writes `standup/control/team_run_active`
// into the user's project and switches the supervisor gate OFF for six hours, on a run that was
// never capable of doing anything. A first `/standup` on a fresh install did exactly this.
if (ROSTER_ERROR) {
  stopTick(
    `no usable roster reached this run — ${ROSTER_ERROR}`,
    'ways to pass one', [
      'args.roster = the parsed contents of standup/team.json (an OBJECT, not a string)',
    ],
    'pass the roster verbatim on the Workflow call. There is deliberately no built-in fallback: '
    + 'one silently ran a different team than standup/team.json and reported green.')
}
if (!DEVS.length) {
  stopTick(
    'the roster contains no ACTIVE developer, so there is no one to dispatch and nothing to report',
    'active developers', DEVS.map(d => d.id),
    'add a project with /add-project <git-url>, or set "active": true on a developer in '
    + 'standup/team.json. (An all-inactive roster and an empty one are the same state here.)')
}

// Every terminal status the Work loop can produce is accounted for BY NAME. The old closing line
// counted `worked` as a denominator with only `committed` and `green` as numerators, so
// "0 committed / 0 green of 2 worked" was emitted byte-identically for two tasks that ran fully and
// failed review (an engineering signal) and two tasks never attempted at all (a routing signal
// meaning the tick did nothing). Rendering two opposite realities the same way is the sibling of
// the false-green this pipeline exists to delete. Statuses are enumerated FROM the records, so a
// status added later can never become invisible here; `order` only sorts what is present.
const tally = (records) => {
  const by = new Map()
  for (const r of records) { const s = (r && r.status) || 'unrecorded'; by.set(s, (by.get(s) || 0) + 1) }
  const order = ['committed', 'green-not-committed', 'review-failed', 'supervisor-rejected',
    'test-gate-failed', 'escalated-plan-rejected', 'escalated-intake', 'blocked-investigate',
    'blocked', 'work-error']
  const rank = k => { const i = order.indexOf(k); return i < 0 ? order.length : i }
  return [...by.keys()].sort((a, b) => (rank(a) - rank(b)) || a.localeCompare(b))
    .map(k => `${by.get(k)} ${k}`).join(' · ')
}

// ---- REVIEW SURFACE: observability is DECLARED, never inferred from vocabulary ----
// It used to be a regex over the role/focus/task TEXT plus a frontend-path sniff. That is why a
// squad whose product has no web words in its role description was invisible to the gate, and why
// a non-web squad could be dragged toward a visual gate it can never satisfy. Now each squad
// declares what its product FACE is. `none` is a DELIBERATE declaration; UNDECLARED is not none and
// never reaches the gate silently (validateQueue stops the run and names the valid kinds).
const SURFACE_KINDS = ['web', 'report', 'agent', 'api', 'cli', 'none']
const surfaceOf = (teamId) => { const t = TEAMS.find(x => x.id === teamId); return (t && t.review_surface) || null }

// ---- PERSONA injection (declared HERE, above every use, so it is never hit in its temporal
//      dead zone) ----
// A persona is a SECOND-PERSON identity instruction — concrete behavior, not a role label and not
// a research footnote. It only works if it lands in the prompt BEFORE the charter / rubric /
// checklist: a persona placed after a checklist is a persona that does not exist. Until this helper
// existed, the only "persona" a staff agent actually received was the ~40-char string in its `role`
// field, buried under a much longer charter+rubric — so a PM/UX agent executed the checklist
// instead of exercising independent judgment. `personaOf(m)` prepends `m.persona` when present.
const personaOf = (m) => (m && m.persona) ? (m.persona + '\n\n———————————————\n\n') : ''

// ---- DESIGN_RULEBOOK rule-id registry (E-01) ----
// E-01 says every design finding must cite a rule id. Checking only that SOMETHING was cited lets
// any string impersonate a rule — at which point the citation discipline that replaced prose
// rubrics has quietly become decorative. The legal set is read from the rulebook FILE, so a
// genuinely new rule has to LAND in DESIGN_RULEBOOK.md before it is citable (propose -> queue ->
// land), instead of being minted at the point of use.
const RULEBOOK_PATHS = ['DESIGN_RULEBOOK.md', '../DESIGN_RULEBOOK.md', 'standup/../DESIGN_RULEBOOK.md']
// Fallback ONLY. The FILE is the source of truth; a silently-embedded copy drifting from the
// rulebook is the same disease the roster fallback above was deleted for.
// Regenerated from DESIGN_RULEBOOK.md. It had drifted SEVEN rules behind (stopped at E-07 while
// the rulebook defines through F-07), and this commit makes the fallback MORE reachable — a
// rejected neighbour rulebook now lands here too. Under the no-fs degrade the design lens would
// be told only 25 ids are citable, and E-01 would reject every F-* finding, including the F
// rules this repo's own judges cite. A drifting embedded copy is precisely the disease the
// roster fallback above was deleted for.
const EMBEDDED_RULE_IDS = ['A-01','A-02','A-03','A-04','B-01','B-02','B-03','B-04','B-05','B-06',
  'C-01','C-02','C-03','C-04','D-01','D-02','D-03','D-04','E-01','E-02','E-03','E-04','E-05',
  'E-06','E-07','F-01','F-02','F-03','F-04','F-05','F-06','F-07']

// ---- WHICH rulebook, and PROVING it is this install's ----
// These candidates are RELATIVE, and relative to a cwd this engine does not control. Measured on
// one machine with two agent-team trees: from the host checkout it read the HOST's rulebook (has
// B-12, no F-01); from the plugin it read the plugin's (has F-01, no B-12) — and BOTH runs
// reported the identical `rulebook_source: "DESIGN_RULEBOOK.md"`. That is not cosmetic: read the
// wrong one and the plugin's own F-01..F-07 are rejected by E-01 as rules that "do not exist",
// while a neighbour's B-12 becomes citable. Exactly the Arm-path bug, one function away.
//
// Two things therefore changed. The reported source is now the RESOLVED ABSOLUTE path plus the
// number of ids found, so two candidates can never report the same string. And a candidate is only
// accepted once it is shown to belong to THIS run: the directory holding it must also hold
// `standup/team.json` whose team ids match the roster this run was handed. That check is the same
// identity assertion the Arm step makes, for the same reason — "a file was found" and "the right
// file was found" are different claims.
//
// FILESYSTEM ACCESS IS NOT GUARANTEED, AND HAS NEVER BEEN OBSERVED IN THE REAL HOST. This file
// says elsewhere that workflow scripts have no filesystem access; `RULE_IDS_SOURCE` only ever
// entered the return object and was never logged, so across 27 recorded runs there is no evidence
// either way. It stopped mattering rather than being resolved: no-fs is a DEGRADE (embedded ids,
// and the source says so plainly) and never a stop, because failing a run over a design-rule id
// list would trade a silent bug for an outage. The Arm step deliberately needs no fs at all.
const ROSTER_TEAM_IDS = (RAW.teams || []).map(t => t && t.id).filter(Boolean).sort().join(',')
let RULE_IDS = null
let RULE_IDS_SOURCE = null
let _fsMod = null
try { _fsMod = await import('node:fs') } catch (e) { _fsMod = null }
if (!_fsMod || typeof _fsMod.readFileSync !== 'function') {
  RULE_IDS_SOURCE = 'unavailable — this harness gives the engine no filesystem access; using the '
    + 'embedded id set. Findings citing a rule added since this engine shipped will be rejected.'
} else {
  const _readIf = (f) => { try { return _fsMod.readFileSync(f, 'utf8') } catch (e) { return null } }
  const _abs = (f) => { try { return _fsMod.realpathSync(f) } catch (e) { return f } }
  const _dirOf = (f) => { const i = _abs(f).lastIndexOf('/'); return i > 0 ? _abs(f).slice(0, i) : '.' }
  const _rejected = []
  for (const p of RULEBOOK_PATHS) {
    const src = _readIf(p)
    if (src === null) continue
    // Wide family match (A-Z, not just today's A-E) so a NEW rule family is citable the moment it
    // lands in the rulebook, with no edit here.
    const ids = String(src).match(/\b[A-Z]-\d{2}\b/g)
    if (!ids || !ids.length) continue

    // Identity. Without a roster to compare against there is nothing to check, so say `unverified`
    // rather than implying a check happened — the failure mode being fixed is a confident report.
    let verdictNote = ' (identity unverified: this run was handed no team ids to compare)'
    if (ROSTER_TEAM_IDS) {
      const tj = _readIf(_dirOf(p) + '/standup/team.json')
      let found = null
      try { found = tj === null ? null : (JSON.parse(tj).teams || []).map(t => t && t.id).filter(Boolean).sort().join(',') }
      catch (e) { found = null }
      if (found === null) {
        // Unverifiable is REJECTED, not accepted-with-a-note. Reporting the divergence is not
        // enough on its own: the neighbouring tree that triggered this has no F-* family, so
        // accepting it makes every F-01..F-07 citation "an unknown rule" under E-01 — the exact
        // damage, merely now with a footnote. A rulebook that cannot be shown to be this
        // install's is worth less than the embedded set, which at least ships with this engine.
        _rejected.push(`${_abs(p)} [no readable standup/team.json beside it]`)
        continue
      } else if (found !== ROSTER_TEAM_IDS) {
        _rejected.push(`${_abs(p)} [teams: ${found || '(none)'}]`)
        continue                       // belongs to a DIFFERENT install — keep looking
      } else {
        verdictNote = ''
      }
    }
    RULE_IDS = new Set(ids)
    RULE_IDS_SOURCE = `${_abs(p)} (${RULE_IDS.size} ids)${verdictNote}`
    break
  }
  if (!RULE_IDS) {
    RULE_IDS_SOURCE = 'embedded fallback — no DESIGN_RULEBOOK.md belonging to this install was '
      + `readable from cwd${_rejected.length ? `; rejected as another install's: ${_rejected.join(' , ')}` : ''}`
  }
}
if (!RULE_IDS) RULE_IDS = new Set(EMBEDDED_RULE_IDS)
// LOGGED, not merely returned. This value only ever entered the result object, which is exactly why
// "27 recorded runs carry no evidence of whether this harness has filesystem access" was true and
// would have stayed true. One line turns the next real tick into that evidence.
log(`RULEBOOK: ${RULE_IDS.size} citable rule id(s) — source: ${RULE_IDS_SOURCE}`)
const RULE_ID_LIST = [...RULE_IDS].sort().join(' ')

// Detection is DELIBERATELY wider than the legal set: an invented "F-99" must register as a
// CITATION (then be rejected as unknown), not read as "no id cited" — otherwise a made-up family
// is the one thing the check misses.
const citedRuleIds = v => String((v === null || v === undefined) ? '' : v).match(/\b[A-Z]-\d{2}\b/g) || []
const RULEBOOK_PROPOSALS = []
// E-01 admission: an entry citing no id, or an id absent from the rulebook, is INADMISSIBLE.
const admitByRule = (list, what) => {
  const kept = [], rejected = []
  for (const it of (Array.isArray(list) ? list : [])) {
    const cited = citedRuleIds(it && it.rule)
    const bad = cited.filter(id => !RULE_IDS.has(id))
    if (!cited.length) rejected.push({ ...it, _inadmissible: 'E-01: no DESIGN_RULEBOOK rule id cited' })
    else if (bad.length) rejected.push({ ...it, _inadmissible: `E-01: rule id(s) not in DESIGN_RULEBOOK.md: ${bad.join(', ')}` })
    else kept.push(it)
    for (const id of bad) if (!RULEBOOK_PROPOSALS.includes(id)) RULEBOOK_PROPOSALS.push(id)
  }
  if (rejected.length) log(`E-01 ADMISSION dropped ${rejected.length} of ${rejected.length + kept.length} ${what} — ${rejected.map(r => r._inadmissible).join(' ; ')}`)
  return { kept, rejected }
}

// ---- schemas ----
const REPORT_SCHEMA = {
  type: 'object',
  required: ['project', 'health', 'done', 'next', 'blockers'],
  properties: {
    project:     { type: 'string' },
    health:      { type: 'string', enum: ['green', 'yellow', 'red'] },
    resumed_from:{ type: 'string', description: 'one line: what the progress file said this dev was doing last' },
    done:        { type: 'array', items: { type: 'string' } },
    in_progress: { type: 'array', items: { type: 'string' } },
    next:        { type: 'array', items: { type: 'object', required: ['task', 'priority', 'effort'], properties: {
      task: { type: 'string' }, priority: { type: 'string', enum: ['P0', 'P1', 'P2'] },
      effort: { type: 'string', enum: ['S', 'M', 'L'] }, why: { type: 'string' } } } },
    blockers:    { type: 'array', items: { type: 'string' } },
    needs_from_team: { type: 'array', items: { type: 'string' } },
    // ↓↓ THE DISCOVERY CHANNEL. A real company catches obvious product problems because a developer
    // notices them too — not only the PM, QA, or reviewer. This schema's required fields are ALL
    // progress (project/health/done/next/blockers); none of them ask "what did you see that's
    // wrong". Worse, the standup prompt told the dev to scope the report to their own lane, so
    // cross-lane observation was explicitly forbidden. A dev cold-starts each tick, reads its own
    // progress file, and is asked one thing: how is your task going. That is why a team of many
    // agents found nothing — the field did not exist and looking outside your lane was banned.
    observations: {
      type: 'array',
      description: 'Anything you saw that is WRONG in this product/repo. It need NOT be in your lane, '
        + 'need NOT be your task, need NOT be something anyone asked about: a UI that does not look like '
        + 'one product, numbers that contradict each other, an unhandled error nobody owns, docs that '
        + 'disagree with the code, a place that plainly nobody cared about. Empty array if you genuinely '
        + 'saw nothing — do NOT invent to fill it; but "not my job" is not a reason to leave it out.',
      items: { type: 'object', required: ['what', 'where', 'why_it_matters'], properties: {
        what: { type: 'string' },
        where: { type: 'string', description: 'concrete URL / file / page so someone can reproduce it' },
        why_it_matters: { type: 'string', description: 'the real consequence to a user or to the team' },
        outside_my_lane: { type: 'boolean', description: 'is it outside your lane — report it anyway; that is exactly why this field exists' },
      } },
    },
    notes:       { type: 'string' },
  },
}

const TEAM_SYNC_SCHEMA = {
  type: 'object',
  required: ['team', 'health', 'summary', 'board', 'dependencies', 'blockers'],
  properties: {
    team: { type: 'string' }, health: { type: 'string', enum: ['green', 'yellow', 'red'] },
    summary: { type: 'string' },
    board: { type: 'array', items: { type: 'object', required: ['project', 'task', 'priority', 'assignee'], properties: {
      project: { type: 'string' }, task: { type: 'string' },
      priority: { type: 'string', enum: ['P0', 'P1', 'P2'] }, effort: { type: 'string', enum: ['S', 'M', 'L'] },
      assignee: { type: 'string' }, autoworkable: { type: 'boolean' } } } },
    dependencies: { type: 'array', items: { type: 'string' } },
    blockers: { type: 'array', items: { type: 'string' } },
  },
}

const BOARD_SCHEMA = {
  type: 'object',
  required: ['summary', 'team_health', 'todays_board', 'blockers'],
  properties: {
    summary: { type: 'string' }, team_health: { type: 'string', enum: ['green', 'yellow', 'red'] },
    todays_board: { type: 'array', items: { type: 'object', required: ['project', 'task', 'priority', 'assignee', 'acceptance', 'serves_goal'], properties: {
      team: { type: 'string' }, project: { type: 'string' }, task: { type: 'string' },
      priority: { type: 'string', enum: ['P0', 'P1', 'P2'] }, effort: { type: 'string', enum: ['S', 'M', 'L'] },
      assignee: { type: 'string' }, autoworkable: { type: 'boolean' },
      // How this item is verified DONE. Prefer a machine-checkable shape (a command + its expected
      // result, a URL + what must render) over prose. Restating the task is NOT an acceptance.
      acceptance: { type: 'string', description: 'how this task is verified done — prefer a machine-checkable form (command + expected result, or URL + what must render); do NOT just restate the task' },
      // The goal->execution link. If a piece of work serves no goal on record, either it should not
      // be on the board, or the goal list is missing something — say which, honestly. A manufactured
      // goal that sounds plausible is worse than an honest NONE.
      serves_goal: { type: 'string', description: 'REQUIRED: which goal in standup/PM_GOALS.md (or which KEYSTONE in standup/BACKLOG.md) this serves, by name. Serves nothing on record → write "NONE — <why it is still worth doing, or which goal the list is missing>". Never invent a plausible-sounding goal to fill this.' },
      source: { type: 'string', description: 'standup|comms' } } } },
    blockers: { type: 'array', items: { type: 'string' } },
  },
}

const PULSE_SCHEMA = {
  type: 'object', required: ['lens', 'engaged', 'observations'],
  properties: {
    lens: { type: 'string' }, engaged: { type: 'boolean' }, headline: { type: 'string' },
    observations: { type: 'array', items: { type: 'string' } },
    nudges: { type: 'array', items: { type: 'object', required: ['note', 'owner'], properties: {
      note: { type: 'string' }, owner: { type: 'string' }, priority: { type: 'string', enum: ['P0', 'P1', 'P2'] } } } },
  },
}

const EVIDENCE_SCHEMA = {
  type: 'object', required: ['findings', 'files', 'feasible'],
  properties: {
    findings: { type: 'array', items: { type: 'string' }, description: 'what the code/data ACTUALLY shows' },
    files: { type: 'array', items: { type: 'string' }, description: 'files in play (for greenfield: where the new code will live)' },
    risks: { type: 'array', items: { type: 'string' } },
    task_kind: { type: 'string', enum: ['brownfield', 'greenfield'] },
    feasible: { type: 'boolean', description: 'false ONLY if the task genuinely cannot be attempted — never for a greenfield zero-baseline' },
  },
}

const PLAN_SCHEMA = {
  type: 'object', required: ['plan', 'files_expected', 'tests_planned'],
  properties: {
    plan: { type: 'string', description: 'step-by-step implementation plan, no code' },
    files_expected: { type: 'array', items: { type: 'string' } },
    tests_planned: { type: 'string' },
    risks: { type: 'array', items: { type: 'string' } },
  },
}

const CHALLENGE_SCHEMA = {
  type: 'object', required: ['approved', 'critique'],
  properties: { approved: { type: 'boolean' }, critique: { type: 'string' },
    required_changes: { type: 'array', items: { type: 'string' } },
    blocking: { type: 'boolean', description: 'Required whenever approved=false: is the plan genuinely WRONG (true — code written against it would be thrown away), or is it sound with changes you want made (false)? Doctrine is that pairs CRITIQUE; a critique you judge non-blocking travels into IMPLEMENT as required_changes instead of ending the task.' } },
}

// The pair critiques; the pair does not hold a veto. Before this, `!challenge.approved`
// ended the task outright — so a reviewer doing its job well ("direction is right, fix these
// four things") killed the run exactly as hard as one finding a fatal design error, and
// required_changes that were already written never reached the implementer.
// Same shape as isBlocking above, on this schema's `approved` field; a null challenge
// (agent died) still stops, because no verdict is not approval.
const challengeBlocks = (v) => !v || (v.approved === false && v.blocking !== false)

const WORK_SCHEMA = {
  type: 'object', required: ['task', 'status', 'summary'],
  properties: {
    task: { type: 'string' },
    status: { type: 'string', enum: ['draft', 'committed', 'draft-only', 'blocked', 'skipped'] },
    branch: { type: 'string' }, commit_message: { type: 'string' },
    files_changed: { type: 'array', items: { type: 'string' } },
    tests_run: { type: 'string', description: 'EXACT commands run and their results — the test gate' },
    tests_passed: { type: 'boolean' },
    summary: { type: 'string' },
    follow_ups: { type: 'array', items: { type: 'string' } },
  },
}

const REVIEW_SCHEMA = { type: 'object', required: ['pass', 'verdict'], properties: { pass: { type: 'boolean' }, verdict: { type: 'string' } } }

// The design-quality lens's structured output. Findings are FORCED to carry a rule id (E-01), and
// the machine judge's exit code is a first-class field — so `pass` binds to a script's verdict
// rather than to prose, which can always be written to sound convincing.
const DESIGN_REVIEW_SCHEMA = {
  type: 'object', required: ['pass', 'verdict', 'machine_gate'],
  properties: {
    pass: { type: 'boolean' },
    verdict: { type: 'string' },
    machine_gate: {
      type: 'object', required: ['ran', 'exit_code', 'url'],
      properties: {
        ran: { type: 'boolean', description: 'was verify_design_quality.js actually executed' },
        exit_code: { type: 'number', description: '0=no violations 1=violations 2=page could not be loaded 4=the JUDGE itself could not run (Playwright/Chromium missing — the gate is broken, not the page) 64=usage error' },
        url: { type: 'string', description: 'the running instance actually judged' },
        violations: { type: 'number' },
        by_rule: { type: 'string', description: 'e.g. "A-02x42, A-03x8, B-01x1"' },
      },
    },
    findings: {
      type: 'array',
      items: {
        type: 'object', required: ['rule', 'detail'],
        properties: {
          rule: { type: 'string', description: 'DESIGN_RULEBOOK id, e.g. A-01/B-03/C-02. E-01: a finding without one does not stand' },
          detail: { type: 'string' },
          surface: { type: 'string', description: 'the page/component the violation is on' },
        },
      },
    },
    systemic: {
      type: 'array',
      description: 'E-02: rule ids cited >=2 times + WHERE the shared component to change lives (per-file tickets are forbidden)',
      items: { type: 'string' },
    },
  },
}

const DQ_SCHEMA = {
  type: 'object', required: ['ran', 'passed', 'evidence'],
  properties: {
    ran: { type: 'boolean' }, passed: { type: 'boolean' },
    evidence: { type: 'string', description: 'EXACT commands run + their results (the unit/dev test gate)' },
    integration: { type: 'string', description: 'integration-test result if the project has a suite, else "none"' },
    visual: { type: 'string', description: 'live visual/E2E result for a UI task (real running instance, not HTTP 200s), else "n/a"' },
  },
}

const SUP_SCHEMA = {
  type: 'object', required: ['approve', 'note'],
  properties: { approve: { type: 'boolean' }, note: { type: 'string' }, must_fix: { type: 'array', items: { type: 'string' } },
    blocking: { type: 'boolean', description: 'Required whenever approve=false: must this genuinely STOP (true), or can the run continue carrying your note (false)? Wording tightenings, optional hardening, "this could be better", "one amendment away" are all false.' } },
}

// A gate that stops on ANY reserve is not a quality gate — it is a gate that never opens.
// A conscientious reviewer marks approve=false the moment it sees anything improvable, so
// "the more diligent the reviewer, the less can ever ship". Seen for real (2026-08-03): three
// consecutive runs on one task, ~4.6M tokens, ZERO lines of code, while the supervisor's own
// verdicts read "Fix the eight below and this ships — about a page of work, not a rewrite",
// then "DIRECTION, GRAIN AND DATA PATH: APPROVED", then "DO NOT RE-PLAN. BUILD proceeds with
// the must_fix applied." It said go three times and the gate stopped it three times.
// So approve=false must now answer its own question: is this a REAL blocker?
// Missing field still stops (strict by default) — silence is not consent to proceed.
// A null verdict (the agent died) also stops: !!v short-circuits before the blocking check.
const isBlocking = (v) => !!v && v.approve === false && v.blocking !== false

// The INTAKE deliverable: a raw ask turned into an OUTCOME contract before anyone writes code.
// Without this gate nothing in the pipeline ever asks "are we building the right thing" — every
// other gate asks "did we build the thing right", which a plan solving the wrong problem passes.
const CONTRACT_SCHEMA = {
  type: 'object', required: ['goal', 'acceptance', 'verification', 'priority'],
  properties: {
    goal: { type: 'string', description: 'ONE sentence, an OUTCOME from the user experience back — not a restatement of the task' },
    acceptance: { type: 'array', items: { type: 'string' }, description: 'concrete conditions; each one falsifiable' },
    verification: { type: 'string', description: 'how "done" is PROVEN — which gate/command/artifact. A vibe is not a verification' },
    priority: { type: 'string', enum: ['P0', 'P1', 'P2'] },
    out_of_scope: { type: 'array', items: { type: 'string' } },
  },
}

const progressFile = dev => `${dev.folder}/.standup/${dev.id}.md`

// ---- ROUTING, PAIRING, FOLDER + SURFACE VALIDATION ----
// Runs over the WHOLE queue BEFORE the Work loop, on BOTH entry paths, and before a single token
// of agent spend.
//
// Deliberately hoisted OUT of the per-task try/catch in the Work loop. That catch exists for a
// legitimate reason — one agent schema throw must not abort a whole tick — and it records
// `work-error` and CONTINUES. A routing error thrown inside it would therefore produce a soft
// record wearing a new label: a source-text fix with no behavioural change, i.e. exactly the
// gate-that-never-fires being deleted here. Validating up front also means a misaimed run costs
// nothing, and the operator learns at second 0 instead of after an hour of agents.
//
// ⚠️ CONSEQUENCE, stated rather than buried: an unroutable item aborts the WHOLE tick, discarding
// the other queued items and the phases already spent. That is the intended trade — a board whose
// assignees are LLM-synthesized (see the Synthesize phase) can produce a typo, and the honest
// response to "I cannot tell who this is for" is to stop and say so, not to quietly do 1 of 2
// tasks and report a clean tick.
const resolveTask = (t) => {
  // (a) assignee. Previously: a silent `status:'skipped'` + continue — a clean-looking tick that
  // did nothing, which is precisely the harm this pipeline exists to prevent.
  const dev = t.assignee ? DEVS.find(d => d.id === t.assignee) : null
  if (!dev) {
    stopTick(
      t.assignee ? `board item names assignee "${t.assignee}", who is not on the roster`
                 : `board item "${String(t.task || '').slice(0, 60)}" names no assignee, so nobody owns it`,
      'assignees', DEVS.map(d => d.id),
      'correct the assignee on the board item, or add that developer to standup/team.json')
  }
  const team = TEAMS.find(x => x.id === dev._team)

  // (b) pair. Previously: `(pair) || (any other squadmate) || dev` — the trailing `|| dev` made a
  // developer CRITIQUE ITS OWN PLAN and review its own diff (the writer grading own work, which
  // the review rule forbids), and the middle clause silently substituted an arbitrary squadmate
  // for the declared pair, which is a lie about who reviewed. Both fallbacks are deleted.
  const mates = team.developers.filter(x => x.id !== dev.id).map(x => x.id)
  if (!dev.pair) {
    stopTick(`developer "${dev.id}" declares no pair, so its plan and its diff would be reviewed by nobody but itself`,
      `pairs for ${dev.id} on squad ${team.id}`, mates,
      `set "pair" on ${dev.id} in standup/team.json (a lone developer cannot run this SDLC — the pair challenge and the diff review are two of its gates)`)
  }
  const lanemate = team.developers.find(x => x.id === dev.pair && x.id !== dev.id)
  if (!lanemate) {
    stopTick(`developer "${dev.id}" declares pair "${dev.pair}", who is not another ACTIVE developer on squad "${team.id}"`,
      `pairs for ${dev.id} on squad ${team.id}`, mates,
      `fix "pair" on ${dev.id} in standup/team.json (check the id spelling, and that the pair has active:true)`)
  }

  // (c) folder. A dev may own more than one repo; the roster's `folder` is single-valued. When a
  // task names its own folder it must land inside what that dev DECLARES it owns, or anyone could
  // aim the pipeline at any path. The resolved folder then flows into the DETERMINISTIC reviewer
  // commands (`git -C <folder> diff`) — that is the whole point: with the folder hardcoded to the
  // owner's, a correct change made in the other repo shows an EMPTY diff and is failed as
  // review-failed. Omitting `folder` is byte-for-byte the previous behaviour.
  const owned = [dev.folder, team.folder].concat(dev.also_owns || []).filter(Boolean)
  if (t.folder && !owned.includes(t.folder)) {
    stopTick(`task folder "${t.folder}" is not a directory "${dev.id}" declares it owns`,
      `folders for ${dev.id}`, owned,
      `use one of those, or add the directory to "also_owns" on ${dev.id} in standup/team.json`)
  }
  const folder = t.folder || dev.folder || team.folder || '.'

  // (d) review surface. An UNDECLARED squad is reported LOUDLY — never silently treated as
  // non-observable, which is how a whole product face ends up with no gate looking at it.
  const surface = team.review_surface
  if (!surface || !surface.kind) {
    stopTick(`squad "${team.id}" declares no review_surface, so nothing knows what its product FACE is or how to inspect it`,
      'kinds', SURFACE_KINDS,
      'add review_surface {kind,label,url,inspect,how} to that squad in standup/team.json — "none" is a deliberate declaration; UNDECLARED is not')
  }
  if (!SURFACE_KINDS.includes(surface.kind)) {
    stopTick(`squad "${team.id}" declares review_surface.kind "${surface.kind}", which is not a kind this engine knows`,
      'kinds', SURFACE_KINDS, `correct review_surface.kind on squad ${team.id} in standup/team.json`)
  }
  if (surface.kind !== 'none' && !String(surface.inspect || '').trim()) {
    stopTick(`squad "${team.id}" declares review_surface.kind "${surface.kind}" but no inspect command, so its surface cannot actually be looked at`,
      'kinds', SURFACE_KINDS,
      `add a runnable "inspect" to that squad's review_surface in standup/team.json (runnable from a clean checkout, or stating its own prerequisite inline), or declare kind "none"`)
  }
  return { task: t, dev, team, lanemate, folder, surface }
}
const validateQueue = (queue) => queue.map(resolveTask)

// ---- SINGLE-TASK ENTRY (/work) ----
// ONE SDLC definition, by construction. /work does not get its own pipeline: it builds a one-item
// queue and runs the SAME Work loop the board path runs, so there is no second definition to keep
// in sync and no way for the board path to bypass a gate the single-task path has (or the reverse).
// The alternative — extracting the loop body into a function that two callers share — makes the
// same claim but has to be RE-proven at every later edit; here there is physically only one loop.
// Cost of that choice, stated: the upstream phases are skipped by guard rather than by structure,
// so each guard is a place a future edit could reintroduce a divergence — which is why the judge
// asserts BOTH paths reach INTAKE rather than asserting it once.
const SINGLE = (A && A.task) ? (typeof A.task === 'string' ? { task: A.task } : A.task) : null

// ---- Phase 0: COMMS (optional staff triage over a local inbox) ----
// Every upstream phase is guarded on SINGLE: /work is aimed at one named task, so the roster-wide
// inventory (who did what since when), the design sweep and the board synthesis have nothing to
// contribute and would be an hour of agents spent to rediscover the task the caller already named.
if (!SINGLE) phase('Comms')
let comms = null
const triage = SINGLE ? null : STAFF.find(s => s.id === 'comms_triage')
if (triage) {
  comms = await agent(
    `You are the Comms Triage staff agent for this team. Folder: ${triage.folder} (relative to the project root). Date: ${DATE}.
READ-ONLY except you may append a dated triage record to ${triage.folder}/.standup/comms_triage.md (create the dir if needed).
Job: scan ${triage.folder}/inbox/ for any local message files (plain text / json someone dropped in) changed since "${SINCE}". Extract concrete ACTION ITEMS, route each to a squad (${TEAMS.map(t => t.id).join(', ')}) + a dev id with a priority. An empty list is valid if there is nothing new. Summarize what you saw.`,
    { label: 'comms:triage', phase: 'Comms', model: MECH_MODEL, schema: {
      type: 'object', required: ['items', 'summary'], properties: {
        summary: { type: 'string' },
        items: { type: 'array', items: { type: 'object', required: ['source', 'action', 'priority'], properties: {
          source: { type: 'string' }, action: { type: 'string' },
          route_to_squad: { type: 'string' }, route_to_dev: { type: 'string' },
          priority: { type: 'string', enum: ['P0', 'P1', 'P2'] } } } } } } }
  )
}

// ---- Phases 1+2: STANDUP -> TEAM SYNC, pipelined per squad ----
// ALLCAPS at column 0 for a section noun, so the transcript's opening bookend rhymes with its
// closing one (`TICK DONE` / `TICK STOPPED`) instead of being the one sentence-cased outlier.
if (!SINGLE) {
  phase('Standup')
  log(`STANDUP ${DATE} — ${TEAMS.length} squad(s), ${DEVS.length} devs, window="${SINCE}"${comms ? `, comms items: ${(comms.items || []).length}` : ''}`)
}

const squads = SINGLE ? [] : (await parallel(TEAMS.map(team => async () => {
  const reports = (await parallel(team.developers.map(dev => () => {
    const lanemate = team.developers.find(x => x.id === dev.pair) || team.developers.find(x => x.id !== dev.id && x.folder === dev.folder)
    return agent(
      `You are the "${dev.role}" developer-agent on the ${team.name} (squad: ${team.id}), folder: ${dev.folder} (developer id: ${dev.id}).
Your lane: ${dev.focus}.
Squad mission: ${team.mission}
Squad coordination: ${team.coordination}
${lanemate ? `Your pair is "${lanemate.id}" (${lanemate.focus}) — you challenge each other's plans and diffs; scope the PROGRESS part (done / next / blockers) to YOUR lane — observations[] is explicitly NOT lane-limited.` : ''}
STANDUP for ${DATE}. READ-ONLY — do not edit, commit, or run side effects.

RESUME CONTEXT FIRST (fixes session amnesia):
- Read your progress file ${progressFile(dev)} if it exists — it says what you did last session and what's next. Put its gist in resumed_from. If it does not exist yet, say "no progress file yet".
- Read the project's README / any notes${dev.context ? ` and ${dev.context}` : ''}, and ${dev.folder}/BACKLOG.md if present.

Evidence:
${dev.git
  ? `- git -C ${dev.folder} log --since="${SINCE}" --format='%ad %s' --date=short -- .   ('-- .' scopes to this folder)\n- git -C ${dev.folder} status --short -- .`
  : `- list files in ${dev.folder} changed recently`}
- standup/BACKLOG.md for carried tasks.

Report: DONE in window, IN PROGRESS, ranked NEXT in your lane (P0-P2, S/M/L, why), BLOCKERS, needs_from_team, health. Concrete, file-level, no filler.

**observations[] — this item is NOT lane-limited, and it is a required part of the report.**
You are an engineer on this product, not a ticket-executor. Anything you notice while doing this
standup that is wrong, report it: a UI that does not look like one product, numbers that contradict
each other, an unhandled error nobody owns, docs that disagree with the code, a screen that plainly
nobody cared about. **"Not my job" is not a reason to stay silent — in a real company a developer
finds these even when the PM did not.** This team long had nobody catching visible product problems
precisely because this field did not exist and the lane rule above forbade looking past your own
work (now scoped to the progress part only). Saw nothing → return an empty array; never invent to fill it.`,
      { label: `standup:${dev.id}`, phase: 'Standup', agentType: 'Explore', model: MECH_MODEL, effort: E_MECH, schema: REPORT_SCHEMA }
    ).then(r => r ? { ...r, _dev: dev.id, _team: team.id } : null)
  }))).filter(Boolean)

  const sync = await agent(
    `You are the ${team.name} (id: ${team.id}) team lead running the squad sync for ${DATE}.
Squad mission: ${team.mission}
Coordination: ${team.coordination}
Reports JSON:

${JSON.stringify(reports, null, 2)}

Produce the squad sync: narrative, health, squad-RANKED board (assignee = developer id), cross-project DEPENDENCIES (match needs_from_team asks to the dev who serves them), blockers. autoworkable=true ONLY for pure code/analysis with no outward side effects.`,
    { label: `sync:${team.id}`, phase: 'Team Sync', schema: TEAM_SYNC_SCHEMA }
  )
  return { team: team.id, name: team.name, reports, sync }
}))).filter(Boolean)

const reports = squads.flatMap(s => s.reports)
// NARRATION — log the CONCLUSION, never "starting X", and put the number that matters on the line.
// A run spawns dozens of agents over an hour and used to say nothing on the happy path: you could
// watch the whole progress tree without learning what design scored or which review blocked a task.
if (!SINGLE) {
  // Each squad's DECLARED review surface is printed here, with its inspect command verbatim, for a
  // reason: once observability is roster-driven, a squad whose `kind` is wrong has no other way to
  // become visible — which would be this same invisibility one layer up. Printing the command also
  // means a reader can copy it without opening team.json.
  log(`SQUADS ${squads.length} synced · ${reports.length} dev report(s): ` +
    squads.map(s => { const sf = surfaceOf(s.team); return `${s.team}=${(s.sync && s.sync.health) || '?'} [${sf && sf.kind ? sf.kind : 'UNDECLARED'}]` }).join(' '))
  for (const s of squads) {
    const sf = surfaceOf(s.team)
    if (sf && sf.kind === 'none') log(`  ${s.team} surface: none — declared as having no inspectable face`)
    else if (sf && String(sf.inspect || '').trim()) log(`  ${s.team} surface [${sf.kind}] ${sf.label || ''} — inspect: ${sf.inspect}`)
    else log(`  ${s.team} surface UNDECLARED — add review_surface {kind,label,url,inspect,how} in standup/team.json (valid kinds: ${SURFACE_KINDS.join(', ')})`)
  }
  const _blk = squads.flatMap(s => ((s.sync && s.sync.blockers) || []).map(b => `[${s.team}] ${b}`))
  if (_blk.length) log(`BLOCKERS ${_blk.length} raised: ${_blk.slice(0, 3).join(' · ').slice(0, 220)}${_blk.length > 3 ? ` … +${_blk.length - 3}` : ''}`)
}

// ---- Phase 2b: DESIGN — the design pass, BEFORE Synthesize ----
// This used to run as the LAST phase, after Work. Two consequences, both fatal:
//   1. the code was already committed, so a design critique could not block anything;
//   2. its output went into the design lead's progress file — which the one developer who could
//      act on it never read. Same defects found tick after tick, zero landed.
// Now it runs before the board is synthesized, so its findings become QUEUE ITEMS on THIS tick's
// board, and every one of them cites a DESIGN_RULEBOOK rule id (E-01) so it can be tracked.
if (!SINGLE) phase('Design')
const DESIGN_LEADS = SINGLE ? [] : STAFF.filter(s => s.role && /design/i.test(s.role))
const DESIGN_SCHEMA = {
  type: 'object', required: ['summary', 'tasks'], properties: {
    lens: { type: 'string' },
    summary: { type: 'string', description: 'overall design verdict, 3-5 sentences' },
    score: { type: 'number', description: 'current UI quality 1-10 against this rubric' },
    machine_gate: {
      type: 'object', description: 'the deterministic verdict of verify_design_quality.js — a referee, not an opinion',
      properties: {
        ran: { type: 'boolean' }, urls_scanned: { type: 'number' },
        total_violations: { type: 'number' },
        by_rule: { type: 'string', description: 'e.g. "A-02x42, A-03x8, B-01x1"' },
        worst_surfaces: { type: 'array', items: { type: 'string' } },
      },
    },
    tasks: { type: 'array', items: { type: 'object', required: ['task', 'priority', 'effort', 'rule'], properties: {
      task: { type: 'string' },
      rule: { type: 'string', description: 'DESIGN_RULEBOOK rule id (E-01, mandatory). Comma-separate several' },
      priority: { type: 'string', enum: ['P0', 'P1', 'P2'] },
      effort: { type: 'string', enum: ['S', 'M', 'L'] }, files: { type: 'string' },
      systemic: { type: 'boolean', description: 'E-02: this rule was cited >=2 times, so it is a SHARED-COMPONENT fix, not a per-file ticket' },
      autoworkable: { type: 'boolean' } } } },
    // ↓↓ THE JUDGMENT CHANNEL — no rule id required. tasks[] forces a rule id, and E-01 drops any
    // finding that cannot cite one. When the rulebook is also authored entirely by the supervisor,
    // those two together form a closed loop: the agent can only find defects the supervisor already
    // named. A conclusion like "this whole surface should not exist" or "these pages do not look
    // like one product" has no rule id and would be dropped — so the persona is structurally
    // excluded. The very defects an outsider spots in a glance are exactly the ones every single-page
    // rule is blind to. judgments[] is where a design lead / PM records those, and it is as
    // first-class as tasks[], not a footnote.
    judgments: {
      type: 'array',
      description: 'Your independent judgment as this role. **No rule id required.** The rulebook is a '
        + 'floor, not the verdict: things the rules do not cover — even the judgment that a rule itself '
        + 'is wrong — go here.',
      items: { type: 'object', required: ['claim', 'should_be', 'scope'], properties: {
        claim: { type: 'string', description: 'your judgment. Whole-surface calls are allowed: "this surface should not exist", "these pages do not look like one product", "this page\'s information architecture is wrong"' },
        should_be: { type: 'string', description: 'the shape it SHOULD take. A judgment with this empty is discarded — criticism that hands over no shape is worthless' },
        scope: { type: 'string', enum: ['view', 'surface', 'product', 'company'], description: 'product/company-scope judgments are exactly the class the [MACHINE] rules (all single-page) cannot see' },
        why_no_rule: { type: 'string', description: 'why this cannot be expressed as a rule (optional). If you think it SHOULD become a rule, use the E-01 propose path in tasks[] instead' },
      } },
    },
    // The main deliverable. tasks[] is a defect list; `design` is design. A PM/UX who only reviews
    // or vetoes at a checkpoint is not shaping the product and adds nothing — shipping only tasks[]
    // is gating; shipping a spec a frontend dev can build from without coming back is participation.
    design: {
      type: 'object',
      description: 'a complete design for the ONE surface most worth rebuilding this tick — specific enough to build from',
      properties: {
        surface: { type: 'string' },
        purpose: { type: 'string', description: 'who this screen is for and what decision it supports' },
        layout: { type: 'string', description: 'what goes where, the hierarchy, what is above the fold. ASCII wireframe is fine' },
        states: { type: 'string', description: 'loading / empty / error / partial-data — each an actual designed state (C-04)' },
        remove: { type: 'string', description: 'what to DELETE. A design that only adds is not a design' },
      },
    },
  } }
const critiques = DESIGN_LEADS.length ? (await parallel(DESIGN_LEADS.map(lead => () => agent(
  `${personaOf(lead)}You are "${lead.id}" — ${lead.role}. Date ${DATE}. Be demanding, not polite.
YOUR RUBRIC (your lens): ${lead.rubric || lead.charter || lead.focus || 'clarity, deference, depth; the states hover/focus/loading/error/empty'}

THE CRITERION IS **DESIGN_RULEBOOK.md** (read it first). It is a numbered rule table, not prose:
your rubric is the lens, the rule ids are the language. Every finding MUST cite a rule id (E-01) —
a finding that cannot cite one does not enter the queue.
⚠️ Rule ids may ONLY come from these ${RULE_IDS.size} (this is validated in code; anything else is dropped):
${RULE_ID_LIST}
If you genuinely need a new rule, write "propose a new rule: <text>" and cite E-01 — do NOT invent an
id on the spot. A new rule must land in DESIGN_RULEBOOK.md before it is citable.

**Rulebook ownership: you OWN the A-D rules (what good design is); the supervisor owns only the E
meta-rules** (a finding must be citable, verifiable, and actually assigned to someone). The supervisor
defines that design judgment must be executable — it does NOT define what good design is. Finding a
defect class the rulebook does not yet name, and writing it up as a new A-D rule, is YOUR job.

**judgments[] is your independent-judgment channel — no rule id required.** The rulebook catches
KNOWN defect classes; finding the UNKNOWN ones, or judging that a whole surface / the whole product is
wrong, is your job, not the gate's. Note in particular that every rule here is single-page in scope
(one view / one screen / side-by-side panels) — there are ZERO cross-page rules, so a judgment like
"these pages do not look like one product" is one the machine will NEVER report and only you can make.
Put it in judgments[] with scope=product or company, and give the shape it should take.

STEP 1 — run the deterministic judge (this is the referee; do not skip it):
    node standup/control/verify_design_quality.js <url> --json /tmp/dq-${lead.id}.json
  ${DESIGN_URL
    ? `The URL to judge: ${DESIGN_URL}`
    : `No URL was configured (args.designUrl is empty). Work out the running instance's URL from the project's own run method (its README / run script) and START it if needed. If you truly cannot reach a running instance, set machine_gate.ran=false and say exactly why — do NOT report a clean sweep you did not run.`}
  ${DO_DESIGN ? 'DEEP tick: sweep every surface of the app.' : 'LIGHT tick: sweep 3-5 surfaces — those touched in this window, plus at least one never swept before (rotate).'}
  Record the exit code and the per-rule violation counts in machine_gate.
  Exit 0 = no violations · 1 = violations · 2 = the page could not be loaded · **4 = the JUDGE itself
  could not run** (Playwright/Chromium missing). 2 and 4 both mean the gate produced no verdict — set
  machine_gate.ran=false and say why; for 4 make clear it is the GATE that is broken, not the page,
  and run the remediation the script prints. Never report a clean sweep you did not run.

STEP 2 — judge the [JUDGMENT] rules a script cannot decide (B-03 color semantics, B-04 factory
  defaults, B-05 indistinguishable near-duplicates, C-01 single focus, C-02 empty de-emphasis,
  C-04 designed empty states, D-02 numeral typography, D-04 title/state separation) against a REAL
  SCREENSHOT you took. ⚠️ E-07: a machine PASS proves NOTHING. The judge catches "looks wrong" and
  is blind to "looks right, is lying" — a page of per-card-normalized sparklines renders perfectly
  and inverts the true ranking. Ask explicitly: could this screen lead someone to a conclusion the
  data does not support?

STEP 3 — E-02: any rule id you cite >=2 times becomes ONE systemic task ("change the shared
  component / the rule, then regenerate the affected batch") with the component's location — NOT N
  per-file tickets. Mark it systemic=true; it outranks ordinary P0s because one fix clears many.

STEP 4 — hand over a real DESIGN for the single surface most worth rebuilding (purpose, layout,
  states, and what to DELETE), not just a defect list. Do NOT implement anything here: your tasks
  enter this tick's board and go through the gate chain like any other work.
Append a dated entry to ${lead.folder ? `${lead.folder}/.standup/${lead.id}.md` : `standup/.standup/${lead.id}.md`} (create the dir if needed).`,
  { label: `design:${lead.id}`, phase: 'Design', effort: E_JUDGE, schema: DESIGN_SCHEMA }
).then(r => r ? { ...r, _lead: lead.id } : null)))).filter(Boolean) : []
// E-01 existence check — a design task citing an id that is not in DESIGN_RULEBOOK.md never
// reaches the board. Before this, `rule` was whatever string the model felt like writing.
for (const c of critiques) {
  const adm = admitByRule(c.tasks, `design task(s) from ${c._lead}`)
  c.tasks = adm.kept
  if (adm.rejected.length) c.inadmissible_tasks = adm.rejected
}
for (const c of critiques) {
  const mg = c.machine_gate || {}
  log(`DESIGN ${c._lead}: score ${c.score ?? '?'}/10 · machine judge ${mg.total_violations ?? '?'} violation(s)` +
    (mg.urls_scanned ? ` over ${mg.urls_scanned} surface(s)` : '') +
    (mg.by_rule ? ` — ${String(mg.by_rule).slice(0, 90)}` : '') +
    ` · ${(c.tasks || []).length} task(s) boarded` +
    (c.design && c.design.surface ? ` · design for: ${String(c.design.surface).slice(0, 60)}` : ' · ! NO design delivered (defect list only)'))
}
const design = critiques.length ? { leads: critiques } : null
const DESIGN_TASKS = critiques.flatMap(c => (c.tasks || []).map(t => ({ ...t, _lead: c._lead })))

// ---- Phase 3: SYNTHESIZE (EM board) ----
// On the single-task path the "board" is the one task the caller named. It is synthesized rather
// than agent-produced so that the Work loop below has exactly one input shape and one code path.
if (!SINGLE) phase('Synthesize')
const board = SINGLE ? {
  summary: `single task (/work): ${SINGLE.task}`,
  team_health: 'green',
  todays_board: [{ ...SINGLE, autoworkable: true, priority: SINGLE.priority || 'P1', source: 'work' }],
  blockers: [],
} : await agent(
  `You are the Engineering Manager running standup for ${DATE} over ${squads.length} squads. Squad syncs:

${JSON.stringify(squads.map(s => ({ team: s.team, name: s.name, sync: s.sync })), null, 2)}
${comms ? `\nComms-triage routed action items (tag these source=comms on the board):\n${JSON.stringify(comms.items, null, 2)}` : ''}
${DESIGN_TASKS.length ? `\nDESIGN tasks from THIS tick's design pass — rank them like any other item; each already carries a DESIGN_RULEBOOK rule id in its title, KEEP it (E-01) so the fix is traceable to the rule. Items marked systemic=true rank ABOVE ordinary P0s: one of them clears many violations (E-02), where a per-file ticket clears one.\n${JSON.stringify(DESIGN_TASKS, null, 2)}` : ''}

Produce the EM standup: narrative across squads (call out cross-squad dependencies explicitly), overall health, today's RANKED board merged across squads (P0 first; keep team + assignee; source=standup|comms), consolidated blockers. autoworkable=true ONLY if pure code/analysis with no outward side effects.

PM DISCIPLINE (you also wear the Product Manager hat — demanding, Jobs-grade 'say no'):
- PIN keystone items: any task tagged KEYSTONE in standup/BACKLOG.md or blocking >=2 other tasks MUST rank above unblocked busywork.
- **Every board item MUST carry an \`acceptance\`** — how it is verified done, in a machine-checkable shape where possible (a command + expected result, a URL + what must render). Restating the task is NOT an acceptance.
- **Every board item MUST carry a \`serves_goal\`** — the goal in standup/PM_GOALS.md (or the KEYSTONE in standup/BACKLOG.md) it serves, by name. If it serves none on record, write "NONE — <why it is still worth doing, or which goal the list is missing>"; an honest NONE is allowed, a manufactured goal is not. This is the goal->execution link — a board item that serves nothing is either busywork or a gap in the goal list.
- Flag dated risks at the top of blockers.`,
  { label: 'em:synthesize', phase: 'Synthesize', effort: E_JUDGE, schema: BOARD_SCHEMA }
)
if (!SINGLE) {
  const _items = (board && board.todays_board) || []
  log(`BOARD ${_items.length} item(s), ${_items.filter(t => t.priority === 'P0').length} P0, ` +
    `${_items.filter(t => t.autoworkable).length} autoworkable · team health ${(board && board.team_health) || '?'}` +
    (_items.length ? ` · top: ${String(_items[0].task || '').slice(0, 80)}` : ''))
  if (RULEBOOK_PROPOSALS.length) {
    log(`RULEBOOK PROPOSALS this tick — ids cited but not defined: ${RULEBOOK_PROPOSALS.join(', ')}. Land them in DESIGN_RULEBOOK.md (with a real recorded violation, per E-03) or stop citing them.`)
  }
}

// ---- Phase 3b: STAFF PULSE (light, every tick) — pm + design lenses ----
if (!SINGLE) phase('Staff Pulse')
const PULSE_CONTEXT = JSON.stringify({
  date: DATE,
  board: board && { summary: board.summary, health: board.team_health, items: (board.todays_board || []).slice(0, 20), blockers: board.blockers },
  squads: squads.map(s => ({ team: s.team, name: s.name, health: s.sync && s.sync.health, board: (s.sync && s.sync.board) || [] })),
}, null, 2)
const pulseStaff = SINGLE ? [] : STAFF.filter(s => s.id === 'pm_agent' || s.id === 'product_qa' || /design/i.test(s.role || ''))
const staffPulse = (await parallel(pulseStaff.map(member => () => {
  const isPM = member.id === 'pm_agent'
  const isQA = member.id === 'product_qa'
  const lensKick = isQA
    // product_qa runs EVERY tick (in the pulse) with its own kick — otherwise it falls into the
    // generic design branch and becomes yet another "written but never run" role.
    ? `Product QA lens — **your job is to actually USE this product, not to review it.**

Pick 1-2 REAL user tasks and run each end-to-end on the live running instance. For example:
  · "I want to see what is awaiting my approval, and approve one item"
  · "I want to read today's board and find the single top task"
  · "I want to explain one number on a screen to someone else"
Running instance: ${DESIGN_URL ? DESIGN_URL : 'derive it from the project\'s own run method (its README / run script) and START it if needed — e.g. the bundled portal at http://127.0.0.1:8770'}. Drive a real browser (Playwright) or curl; if it will not open, that itself IS the report.
Rotate across every UI the product has; do not fixate on one screen.

Your report must be reproducible: **which step you reached / what you were trying to do / what you saw / what you expected / why it stops you getting the thing done.** You are allowed and encouraged to report "I can't say exactly what's wrong but something is off" — mark it a vibe and say what produced the feeling; that signal is the one every existing gate misses: the machine judges single-page compliance, the design lead judges expert standards, the PM judges keep/kill — **none of them judges "can I get the thing done with it."**

Explicitly do NOT: run verify_design_quality.js and copy its output (that is the design lead's job, and the machine already did it); read code and infer "should be fine" (not clicking is a dereliction); invent problems just to hand something in (if it worked, say it worked and say which path you got through).`
    : isPM
    ? `PM lens (light, every-tick): scan THIS tick's board + squad state for scope creep, missing outcome-shapes, starved keystones. Challenge 1-3 board items where scope/direction is off; flag anything to kill/merge against standup/PM_GOALS.md if present.`
    : `Design lens (light, every-tick): the design SWEEP already ran this tick (Phase Design — machine judge + your rubric against DESIGN_RULEBOOK.md), so do NOT review the UI a second time. Your one job here is DELIVERY: of the rule-cited design tasks raised in earlier ticks, which actually LANDED, and which is on its Nth tick without a commit? Name the stalled ones and who owns them. A finding that is re-found every tick and never fixed is the failure mode this whole loop exists to stop.`
  return agent(
    // Persona FIRST — before the charter/rubric/checklist. A persona placed after a checklist is a
    // persona that does not exist (2026 change: the "personality" a staff agent used to get was the
    // ~40-char role label, buried under the charter+rubric it was supposed to govern).
    `${personaOf(member)}You are "${member.id}" — ${member.role}. EVERY-TICK STAFF PULSE for ${DATE} — a LIGHT but REAL pass.
YOUR CHARTER/RUBRIC: ${member.charter || member.rubric || member.focus || ''}
THIS TICK (board + squads):
${PULSE_CONTEXT}
${lensKick}
Keep it tight: a headline, a few concrete observations, and 0-3 light board nudges (note + owner + priority). engaged=false only if genuinely nothing in your lens is in scope.`,
    { label: `pulse:${member.id}`, phase: 'Staff Pulse', model: MECH_MODEL, effort: E_MECH, schema: PULSE_SCHEMA }
  ).then(r => r ? { ...r, _staff: member.id } : null)
}))).filter(Boolean)
if (!SINGLE) log(`STAFF PULSE ${staffPulse.filter(p => p.engaged).length} of ${staffPulse.length} engaged: ` +
  staffPulse.map(p => `${p._staff}${p.engaged ? '' : '(skip)'}`).join(' '))

// ---- Phase 3.5: ARM THE TEAM-RUN EXEMPTION ---------------------------------------------------
// The subagent-cwd problem (upstream: anthropics/claude-code#12748). The Task/agent tool has NO
// `cwd` parameter, so every subagent inherits the EM session's cwd. hooks/supervisor_gate.py
// decides "is this the supervisor?" from exactly that cwd — so the dev agents this engine
// dispatches are all classified as the EM, and their Edit/Write on the project they were sent to
// is HARD-BLOCKED. The roster declares a `folder` per developer, but a folder string cannot become
// a process cwd; it can only be interpolated into a prompt, and a prompt cannot govern a hook.
//
// The exemption flag (standup/control/team_run_active) already existed and the gate already reads
// it — but nothing ever SET it. A gate documented in three places and armed by none is the same
// false-promise defect this repo keeps finding elsewhere: a mechanism that is claimed but not
// wired. Arm it from the engine so it does not depend on a launcher remembering.
//
// Why an agent to do a shell one-liner: workflow scripts have NO filesystem access. The agents
// they spawn have Bash. This is the only way the engine can arm itself, and one cheap agent is
// nothing against a whole run that would otherwise produce an empty diff.
//
// It THROWS on failure. Without the flag the Work phase is structurally incapable of producing
// code, and it fails by reporting `review-failed` on an empty diff — which reads as a code-quality
// problem and sends you looking in the wrong place. Fail loudly at the start instead.
const ARM_SCHEMA = {
  type: 'object',
  required: ['flag_present', 'detail'],
  properties: {
    flag_present:  { type: 'boolean' },
    set_by_me:     { type: 'boolean' },
    detail:        { type: 'string' },
    resolved_root: { type: 'string' },
    flag_realpath: { type: 'string' },
    team_ids:      { type: 'string' },
    dev_ids:       { type: 'string' },
  },
}
const ARM_RUN_ID = `engine-${DATE}`
// Set by the Arm step once it has resolved and VERIFIED the install root, so teardown
// disarms the same tree it armed. Teardown used to use the same relative paths Arm did, so
// on the layout that made Arm hit a neighbour, teardown would have cleared the neighbour's
// flag too — switching that install's gate back on mid-run.
let ARM_RESOLVED_ROOT = null
// What the engine expects the resolved install to contain. Derived from RAW (the roster as handed
// in) and NOT from TEAMS/DEVS: those are filtered to active members, while the file on disk lists
// everyone, so comparing them would mismatch on every roster with an inactive entry.
const ARM_EXPECT_TEAMS = ROSTER_TEAM_IDS
const ARM_EXPECT_DEVS = (RAW.teams || [])
  .flatMap(t => (t && t.developers) || []).map(d => d && d.id).filter(Boolean).sort().join(',')
async function armTeamRunExemption() {
  const r = await agent(
    `You are this run's ARM step. Do exactly one thing, then return.

WHY (this decides whether the run can produce any code at all): this workflow dispatches dev
agents to edit project folders. Subagents inherit the EM session's cwd (the Task tool has no cwd
parameter), and the supervisor gate decides "is this the EM?" from that cwd — so those dev agents
get classified as the EM and their writes are hard-blocked. The run then completes with an EMPTY
diff and reports review-failed. standup/control/team_run_active is the exemption flag that exists
for precisely this, and it must be armed BEFORE any dev agent runs.

RELATIVE PATHS ARE THE HAZARD HERE, NOT A CONVENIENCE. This step used to try
\`standup/control/team_run_flag.sh\` relative to your cwd. On a machine with a second agent-team
tree that resolved to the NEIGHBOUR's script, and armed the neighbour's control plane — a test run
of one install silently switched off the gate of another. Where no such tree exists it instead
created a fresh empty directory and wrote there, which the old check (\`ls\` on a path \`mkdir -p\`
had just guaranteed) confirmed happily. Resolve an ABSOLUTE root first, then use only absolute
paths.

Do this, in order:
1. RESOLVE THE INSTALL ROOT. Starting at \`$(pwd)\` and walking UP one directory at a time, take the
   FIRST directory that contains BOTH \`standup/team.json\` AND \`standup/standup.workflow.js\`.
   Stop at the first match — with nested installs the nearest one is the right one.
     ROOT=""; D="$(pwd)"
     while [ "$D" != "/" ]; do
       if [ -f "$D/standup/team.json" ] && [ -f "$D/standup/standup.workflow.js" ]; then ROOT="$D"; break; fi
       D="$(dirname "$D")"
     done
   If ROOT is empty, STOP and report flag_present:false with detail saying the root was not found
   and what \`pwd\` was. Do not invent a directory; do not \`mkdir\` anything.
2. ARM, with an absolute path:
     bash "$ROOT/standup/control/team_run_flag.sh" set ${ARM_RUN_ID} "auto-armed by standup engine"
   (The helper appends and writes beside ITSELF, so an absolute path here decides everything.)
   If that script does not exist, STOP and report flag_present:false. Do not hand-append; a file
   written somewhere the gate does not read is worse than no file, because it reports success.
3. VERIFY, with the same absolute path:
     bash "$ROOT/standup/control/team_run_flag.sh" status
   flag_present is true ONLY if that output contains the exact words \`team_run_active PRESENT\`.
   That string is the point: the old check ran \`ls\` on a path the previous line had just created,
   so it could not fail. This one comes from the flag's own reader.
4. REPORT WHICH INSTALL YOU TOUCHED — this is what proves you armed OURS and not a neighbour's:
     realpath "$ROOT/standup/control/team_run_active"
     python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(','.join(sorted(t['id'] for t in d.get('teams',[]) if t.get('id')))); print(','.join(sorted(x['id'] for t in d.get('teams',[]) for x in t.get('developers',[]) if x.get('id'))))" "$ROOT/standup/team.json"
   The first printed line is team_ids, the second is dev_ids. Copy them EXACTLY, including the
   commas and the ordering. Do not re-sort, summarise, or tidy them.

Return:
- flag_present: true ONLY if step 3 printed \`team_run_active PRESENT\`. Never optimistically.
- set_by_me: true if you added a record this run (appending alongside an existing one counts).
- resolved_root: the ROOT from step 1, absolute.
- flag_realpath: the realpath from step 4.
- team_ids / dev_ids: the two lines from step 4, verbatim.
- detail: one line — which root you used and what \`status\` reported. If anything stopped you, say
  so here explicitly rather than filling the other fields in hopefully.

Do nothing else. Do not read the backlog or any code. Do not edit team.json.`,
    { label: 'arm:team_run_active', phase: 'Arm', schema: ARM_SCHEMA, effort: 'low' }
  )
  if (!r || r.flag_present !== true) {
    throw new Error(
      'ARM failed: standup/control/team_run_active was not armed. Stopping here — continuing would '
      + 'run the whole gated SDLC while every dev-agent write is blocked by the supervisor gate, '
      + 'producing an empty diff and a misleading "review-failed". '
      + 'Agent returned: ' + JSON.stringify(r || null)
      + ' — Fix: run standup/control/team_run_flag.sh set <run-id> "<note>" from the install root, '
      + 'confirm `status` prints "team_run_active PRESENT", then relaunch.'
    )
  }

  // ---- IDENTITY: did we arm OUR install, or a neighbour's? ----
  // The verification inside the agent cannot answer this, and that is the whole point: a
  // neighbouring install's helper reports `team_run_active PRESENT` perfectly truthfully — about
  // the wrong repo. A writer checking its own work will always agree with itself, so the check has
  // to come from something the writer did not choose: the roster THIS run was handed, which lives
  // in memory here and was never on that agent's path.
  //
  // Deliberately a projection of sorted ids, not a deep compare. The question is "is this the same
  // install", and any formatting, ordering, or unrelated-field difference would make a deep compare
  // fail on installs that are in fact correct — a gate that fires on correct input gets disabled.
  const armTeams = String((r.team_ids != null ? r.team_ids : '')).trim()
  const armDevs  = String((r.dev_ids  != null ? r.dev_ids  : '')).trim()
  let armSource
  if (!ARM_EXPECT_TEAMS && !ARM_EXPECT_DEVS) {
    // Unreachable while the roster guard stands, but stated rather than assumed: with nothing to
    // compare against, say so instead of implying a check happened.
    armSource = `unverified (this run carries no team/dev ids to compare) root=${r.resolved_root || '?'}`
  } else if (!armTeams && !armDevs) {
    armSource = `unverified (the arm step reported no ids back) root=${r.resolved_root || '?'}`
    log(`ARM: WARNING — could not confirm which install was armed. ${armSource}`)
  } else if (armTeams !== ARM_EXPECT_TEAMS || armDevs !== ARM_EXPECT_DEVS) {
    throw new Error(
      'ARM armed the WRONG install. The exemption flag was written to a tree whose standup/team.json '
      + 'does not match the roster this run was handed, which means the gate is now off somewhere '
      + 'else and still on here — every dev-agent write would be blocked and reported as '
      + '"review-failed". '
      + `Armed root: ${r.resolved_root || '(not reported)'} | flag: ${r.flag_realpath || '(not reported)'}`
      + ` | that tree's teams: [${armTeams}] devs: [${armDevs}]`
      + ` | this run's roster teams: [${ARM_EXPECT_TEAMS}] devs: [${ARM_EXPECT_DEVS}]`
      + ' — Fix: launch the Workflow with a cwd inside the install you mean to run, and pass that '
      + "install's standup/team.json as args.roster."
    )
  } else {
    armSource = `verified root=${r.resolved_root || '?'}`
    ARM_RESOLVED_ROOT = r.resolved_root || null
  }
  log(`ARM: team_run_active armed (${ARM_RUN_ID}) — dev agents can write their project folder. `
    + `${armSource}. ${r.detail || ''}`)
  return Object.assign({}, r, { arm_source: armSource })
}
// Teardown. Never the real safety mechanism — a crashed run never reaches it — that is the gate's
// own 6h TTL. This only keeps a normally-finished run from leaving the gate off for hours. It
// must not break the run, so failure is logged, not thrown.
async function disarmTeamRunExemption() {
  try {
    await agent(
      `You are this run's DISARM teardown step. Do exactly one thing, then return.

${ARM_RESOLVED_ROOT
  ? `The install root was resolved and VERIFIED during Arm. Use it, absolute, and touch nothing else:
  ROOT="${ARM_RESOLVED_ROOT}"
  bash "$ROOT/standup/control/team_run_flag.sh" clear ${ARM_RUN_ID}`
  : `Arm did not leave a verified root behind, so there is NO safe absolute path to clear. Do NOT
guess one with a relative path: the same relative lookup is what let Arm write into a neighbouring
install in the first place, and clearing the wrong flag switches THAT install's gate back on
mid-run. Report that you skipped teardown and why. The gate's own 6h TTL is the backstop here —
that is exactly what it is for.`}

Remove only THIS run's record (${ARM_RUN_ID}).

CRITICAL: if any OTHER run's record is still in the file, leave the file in place. Deleting it
would switch the supervisor gate back on in the middle of that run and block all of its writes.
The helper refuses to clear in that case and exits non-zero — that is correct behavior, accept it,
do not pass --force, do not retry.

Return one line describing what happened. Do nothing else.`,
      { label: 'disarm:team_run_active', phase: 'Work', effort: 'low' }
    )
  } catch (e) {
    log(`DISARM did not complete (not fatal; the gate's 6h TTL is the real backstop): ${(e && e.message) || e}`)
  }
}

// ---- Phase 4: WORK — gated SDLC per task (serial; folders are shared) ----
let worked = []
if (DO_WORK || SINGLE) {
  // Only runs that actually write code need the exemption. A read-only tick must not spend an
  // agent on this, and must not needlessly switch the supervisor gate off for 6h.
  phase('Arm')
  await armTeamRunExemption()
  phase('Work')
  const _autoworkable = (board.todays_board || []).filter(t => t.autoworkable)
  const queue = _autoworkable
    .sort((a, b) => (a.priority || 'P2').localeCompare(b.priority || 'P2'))
    .slice(0, SINGLE ? 1 : MAXTASK)
  // Routing/pairing/folder/surface are decided for the WHOLE queue here — before any agent runs.
  const routed = validateQueue(queue)
  log(`WORK QUEUE ${queue.length} of ${_autoworkable.length} autoworkable board item(s) (cap=${SINGLE ? 1 : MAXTASK})` +
    (queue.length ? `: ${routed.map(r => `${r.dev.id}/${String(r.task.task || '').slice(0, 50)}`).join(' | ')}` : ' — nothing autoworkable this tick'))

  for (const R of routed) {
   const t = R.task
   try {
    const dev = R.dev, team = R.team, lanemate = R.lanemate, folder = R.folder, surface = R.surface
    const isGit = !!dev.git
    const record = { task: t.task, assignee: dev.id, project: t.project, team: dev._team, folder, isGit, surface_kind: surface.kind }
    log(`TASK ${dev.id} · ${folder} · [${surface.kind}] · ${String(t.task).slice(0, 90)}`)

    // -- 0 INTAKE: the raw ask becomes an OUTCOME CONTRACT before anyone writes code --
    // This gate is the only one in the pipeline that asks "are we building the RIGHT thing".
    // Every other gate asks "did we build the thing right" — a question a plan that solves the
    // wrong problem passes cleanly. It GATES rather than merely running: ONE autonomous revision
    // against the supervisor's must_fix, ONE recheck, and if it is still unclear the task STOPS
    // here and never reaches implement or commit. A phase that always passes is decoration.
    const _pmPersona = PM_AGENT && PM_AGENT.persona ? `${PM_AGENT.persona}\n\n———————————————\n\n` : ''
    const _pmCharter = (PM_AGENT && PM_AGENT.charter) || (PM_AGENT && PM_AGENT.focus)
      || 'Demanding product review: say NO to scope that does not serve the goal; exactly one DRI; reason from the customer experience back; reject vague asks.'
    let contract = await agent(
      `${_pmPersona}You are the ${PM_AGENT ? PM_AGENT.role : 'Product Manager'} for this team. INTAKE for ${DATE}.
PRODUCT-REVIEW RUBRIC: ${_pmCharter}
Raw task (${t.priority || 'P1'}/${t.effort || 'M'}${t.source ? `, source ${t.source}` : ''}) assigned to "${dev.id}" in ${folder}: "${t.task}"
${t.acceptance ? `The board already proposed an acceptance: ${t.acceptance}\n` : ''}${t.serves_goal ? `The board says it serves: ${t.serves_goal}\n` : ''}Turn it into an OUTCOME CONTRACT: a one-sentence goal stated as an OUTCOME reasoned back from the user's experience (never a restatement of the task), concrete falsifiable acceptance conditions, how "done" is PROVEN (which gate / command / artifact — a vibe is not a verification), and what is explicitly OUT of scope. No implementation, no code.
If the ask is too vague to contract, say so in the goal rather than inventing a plausible one — the supervisor gate below exists to catch exactly that, and a fabricated contract wastes the whole pipeline downstream.`,
      { label: 'intake:pm', phase: 'Work', effort: E_JUDGE, schema: CONTRACT_SCHEMA }
    )
    record.contract = contract
    let intakeOk = await agent(
      `You are the autonomous SUPERVISOR (Claude) — an evaluator agent, NOT a human. INTAKE checkpoint.
RUBRIC: ${SUP_RUBRIC}
Proposed contract: ${JSON.stringify(contract)}
Is the scope clear, the priority right, and the verification REAL (a gate/command/artifact, not a vibe)? approve=false with must_fix if it is fuzzy, restates the task instead of naming an outcome, or its verification could not actually be run.
If you set approve=false you MUST also answer blocking. blocking=true ONLY when the contract is genuinely unusable — work done against it would have to be THROWN AWAY, not amended. A contract that is "one amendment away", a wording objection, or a hardening you would like is blocking=FALSE, which lets the run continue with your must_fix attached to it. You are the DECIDER, not a commentator.`,
      { label: 'sup:intake', phase: 'Work', effort: E_JUDGE, schema: SUP_SCHEMA }
    )
    // ANY reserve still earns a revision round — that is how the must_fix actually gets
    // absorbed into the contract instead of being dropped on the floor. `blocking` decides
    // only whether the run STOPS afterwards, not whether the objection is heard.
    if (intakeOk && intakeOk.approve === false) {
      contract = await agent(
        `You are the ${PM_AGENT ? PM_AGENT.role : 'Product Manager'}. The supervisor flagged your contract. Revise it to address EVERY point: ${JSON.stringify(intakeOk.must_fix || [])}
TASK: ${t.task}\nPREVIOUS CONTRACT: ${JSON.stringify(contract)}`,
        { label: 'intake:pm:revise', phase: 'Work', effort: E_JUDGE, schema: CONTRACT_SCHEMA }
      )
      record.contract = contract
      intakeOk = await agent(
        `You are the autonomous SUPERVISOR (Claude). Re-check the REVISED contract: ${JSON.stringify(contract)}
Your earlier objections: ${JSON.stringify((intakeOk && intakeOk.must_fix) || [])}. approve=false ONLY if it is still genuinely unclear.
And answer blocking honestly, knowing the cost: blocking=true ENDS the run and hands the whole task to a human — nothing is investigated, planned, or built. If your remaining objection is a wording fix or a "would be better", that is blocking=false and the run continues carrying it.`,
        { label: 'sup:intake:recheck', phase: 'Work', effort: E_JUDGE, schema: SUP_SCHEMA }
      )
      // `!intakeOk ||` is load-bearing: isBlocking(null) is false, and a dead agent must
      // still stop the run — "no verdict" is not "approved".
      if (!intakeOk || isBlocking(intakeOk)) {
        record.status = 'escalated-intake'
        record.reason = (intakeOk && intakeOk.note) || 'the outcome contract was still unclear after one revision'
        verdict('intake', 'ESCALATED', `— ${String(record.reason).slice(0, 110)} (not investigated, not implemented, not committed)`)
        worked.push(record); continue
      }
    }
    verdict('intake', 'CONTRACT APPROVED', `— ${String((contract && contract.goal) || '').slice(0, 100)}`)

    // -- 0 INVESTIGATE (read-only: observe reality BEFORE planning — a plan from imagination is the #1 failure) --
    const evidence = await agent(
      `You are "${dev.id}" (${dev.role}) on squad ${dev._team}, folder ${folder}. INVESTIGATE — READ-ONLY, gather real evidence; make NO edits, write NO code.
TASK: ${t.task} (${t.priority}/${t.effort}).
APPROVED OUTCOME CONTRACT from INTAKE (this, not the task title, is what must be true at the end): ${JSON.stringify(contract)}
Read your progress file ${progressFile(dev)} (if present), the project's README${dev.context ? `, ${dev.context}` : ''}, and the ACTUAL source/data the task touches${isGit ? `, git -C ${folder} log/status -- .` : ''}. Observe reality, not imagination — verify assumptions against the real code (e.g. which component/library actually renders a thing), because a plan built on a wrong assumption is the #1 failure.
Judge FEASIBILITY, not readiness. Set task_kind='greenfield' if the task builds something NEW (a PoC, a new integration, a from-scratch module) — a ZERO baseline / "it doesn't exist yet" / a dirty branch is the EXPECTED starting point, NOT a blocker; else 'brownfield'. Set feasible=false ONLY if the task genuinely cannot be attempted (the data/API/permission it needs does not exist and cannot be obtained, or the task contradicts what the code/data shows). Report findings, files in play (for greenfield: where the new code will live), and risks.`,
      { label: `investigate:${dev.id}`, phase: 'Work', agentType: 'Explore', effort: E_MECH, schema: EVIDENCE_SCHEMA }
    )
    record.evidence = evidence
    if (!evidence || evidence.feasible === false) { record.status = 'blocked-investigate'; record.reason = (evidence && evidence.risks) || 'infeasible as written'; worked.push(record); continue }

    // -- 1 PLAN (no code) — grounded in the INVESTIGATE evidence, not imagination --
    let plan = await agent(
      `You are "${dev.id}" (${dev.role}) on squad ${dev._team}. PLAN ONLY — write NO code, make NO edits.
TASK: ${t.task} (${t.priority}/${t.effort}) in folder ${folder}.
APPROVED OUTCOME CONTRACT from INTAKE — the plan must deliver THIS, and nothing in its out_of_scope: ${JSON.stringify(contract)}
EVIDENCE from your INVESTIGATE (ground the plan in THIS, do not re-imagine): ${JSON.stringify(evidence)}
Produce a step-by-step implementation plan: exact files to touch (for a greenfield task, the new files to create), the approach, which tests you will write or run (your lane's test gate: ${dev.tests || 'project test suite'}), and risks. Plans solving the wrong problem are the #1 failure — restate the task's intent in one sentence first.`,
      { label: `plan:${dev.id}`, phase: 'Work', effort: E_JUDGE, schema: PLAN_SCHEMA }
    )
    record.plan = plan
    if (!plan) { record.status = 'blocked'; worked.push(record); continue }

    // -- 2 PLAN CHALLENGE by the pair (fresh context, structured critique — not debate) --
    let challenge = await agent(
      `You are "${lanemate.id}" (${lanemate.role}), the PAIR of "${dev.id}" on squad ${dev._team}. Fresh-context plan review — you have NOT seen their reasoning, only the plan below. Catch wrong direction, wrong scope, missed risks, missing tests. Structured critique with specific required changes; do NOT rubber-stamp, and do NOT invent objections that don't affect correctness/direction.
TASK: ${t.task}
PLAN: ${JSON.stringify(plan, null, 2)}
Check the actual code in ${folder} where the plan makes claims. approved=true only if direction AND test plan are sound.
If you set approved=false you MUST also answer blocking. blocking=true ONLY when the plan is genuinely WRONG — code written against it would have to be thrown away, not amended. "Direction is right, fix these four things", missing tests you want added, a better alternative, or scope you would tighten are all blocking=FALSE: your required_changes then travel into IMPLEMENT and bind there. You critique; you do not veto.`,
      { label: `challenge:${lanemate.id}`, phase: 'Work', effort: E_JUDGE, schema: CHALLENGE_SCHEMA }
    )
    record.challenge = challenge
    if (challenge && challenge.approved === false) {
      plan = await agent(
        `You are "${dev.id}". Your pair rejected your plan. Revise it to address EVERY required change, or push back with evidence only where they are factually wrong.
TASK: ${t.task}\nORIGINAL PLAN: ${JSON.stringify(plan, null, 2)}\nCRITIQUE: ${JSON.stringify(challenge, null, 2)}`,
        { label: `replan:${dev.id}`, phase: 'Work', effort: E_JUDGE, schema: PLAN_SCHEMA }
      )
      record.plan = plan
      challenge = plan ? await agent(
        `You are "${lanemate.id}". Re-review the REVISED plan (your earlier critique attached). approved=true only if your required changes are addressed.
TASK: ${t.task}\nREVISED PLAN: ${JSON.stringify(plan, null, 2)}\nYOUR EARLIER CRITIQUE: ${JSON.stringify(record.challenge, null, 2)}`,
        { label: `rechallenge:${lanemate.id}`, phase: 'Work', effort: E_JUDGE, schema: CHALLENGE_SCHEMA }
      ) : null
      record.rechallenge = challenge
    }
    verdict('plan', challenge && challenge.approved ? 'APPROVED by the pair'
      : (challengeBlocks(challenge) ? 'REJECTED by the pair' : 'RESERVATIONS (non-blocking) — proceeding'),
      (challenge && challenge.required_changes && challenge.required_changes.length ? `(${challenge.required_changes.length} required change(s))` : ''))
    if (challengeBlocks(challenge)) { record.status = 'escalated-plan-rejected'; worked.push(record); continue }

    // -- 3 IMPLEMENT + TEST GATE (no commit) --
    const impl = await agent(
      `You are "${dev.id}" (${dev.role}). Implement the APPROVED plan in ${folder}. ONE task, tightly scoped.
TASK: ${t.task}\nAPPROVED PLAN: ${JSON.stringify(plan, null, 2)}\nPAIR CONDITIONS: ${JSON.stringify((challenge && challenge.required_changes) || [])}
Rules:
- Write/extend the tests in the plan. Then RUN the test gate (${dev.tests || 'project tests'}) and record the EXACT commands + results in tests_run. tests_passed=false if anything fails or you could not run them — never claim untested work passes.
- Do NOT commit, branch, push, merge, or deploy.
- Update your progress file ${progressFile(dev)} (create .standup/ if needed): append a dated entry — what you did, current state, next step. NEVER stage this file in commits.
${isGit ? `- Report a proposed branch (auto/standup-<slug>), one-line commit message, and the EXACT files changed (commit stages ONLY those — never git add -A).` : `- Not a git repo: status "draft-only".`}
Follow the folder's conventions.`,
      { label: `work:${dev.id}`, phase: 'Work', effort: E_BUILD, schema: WORK_SCHEMA }
    )
    record.impl = impl
    if (!impl) { record.status = 'blocked'; worked.push(record); continue }

    // OBSERVABILITY COMES FROM THE DECLARED SURFACE, NOT FROM WEB VOCABULARY.
    // This used to be a regex over the role/focus/task TEXT. That is why a squad whose product has
    // no web words in its description was invisible to the gate: the gate could only see the kinds
    // of product whose vocabulary it had been taught, which is the same defect as judging the diff
    // instead of the surface (E-05), one level up. The squad now DECLARES what its product face is.
    //
    // Two distinct decisions come out of it, and collapsing them was part of the old problem:
    //   VISUAL_DQ  — does this change owe a LIVE, real-browser, click-through visual proof?
    //   DESIGN_LENS — does a design-quality reviewer look at this change at all?
    // A `cli` / `report` / `agent` / `api` squad owes no screenshot but still gets a design lens:
    // its artifact is the OUTPUT of its declared inspect command (for this engine, the run's own
    // transcript). Letting a non-web kind mean "no design lens" is how a product whose only surface
    // is a terminal ends up governed by nothing at all.
    //
    // `_touchedFrontend` is RETAINED but DEMOTED from primary signal to ESCALATOR: declaration
    // decides the default, and a diff that actually touches rendering still pulls in the visual
    // gate. So `kind:'none'` does NOT mean "never visually gated" — it suppresses the visual gate
    // UNLESS the diff itself touches rendering. Documented at the point of declaration too; a field
    // whose documented meaning differs from its behaviour is the same false promise being retired.
    const _touchedFrontend = Array.isArray(impl.files_changed) && impl.files_changed.some(p =>
      /(?:^|\/)(?:frontend|web|ui|client|static|templates)\//i.test(String(p)) || /\.(?:jsx|tsx|vue|svelte)$/i.test(String(p)) || /\.html?$/i.test(String(p)) || /\.s?css$/i.test(String(p)))
    const VISUAL_DQ = surface.kind === 'web' || _touchedFrontend
    const DESIGN_LENS = surface.kind !== 'none' || _touchedFrontend
    const OBSERVABLE_DQ = VISUAL_DQ

    // -- 3.5 TEST GATE (deterministic: unit ALWAYS; integration if a suite exists; visual/E2E when OBSERVABLE; supervisor verifies HONESTY, not just the verdict) --
    const dq = await agent(
      `You are "${dev.id}" (${dev.role}). TEST GATE for the change you just made in ${folder}. RUN the checks and record the EXACT commands + results — never claim untested work passes (ran=false / passed=false if you could not run them).
- unit/dev tests (ALWAYS): ${dev.tests || 'the project test suite'} — put the commands + results in evidence.
- integration: run the project's integration suite IF it has one; else integration="none".
- visual/E2E — ${OBSERVABLE_DQ ? 'MANDATORY (this change is USER-VISIBLE — that includes a BACKEND lane whose change alters what renders): verify it LIVE the way a HUMAN USER would against the ACTUAL RUNNING INSTANCE. START the app with the project\'s own run method, drive a real browser (Playwright) or take a fresh capture you INSPECT yourself, NAVIGATE to the affected screen, CLICK through the real user path, and ASSERT what the user SEES. Put the concrete proof in visual (the run command, WHAT you clicked, WHAT rendered). NOT acceptable: unit/component tests, an HTTP 200, an offline render, or an earlier screenshot. If you CANNOT run it live, set passed=false and record the exact blocker — do NOT pass an observable change on unit tests alone.' : 'not user-visible for this task; set visual="n/a".'}`,
      { label: `testgate:${dev.id}`, phase: 'Work', schema: DQ_SCHEMA }
    )
    const dqOk = await agent(
      `You are the autonomous SUPERVISOR. Verify the TEST GATE's HONESTY, not just its verdict. Report: ${JSON.stringify(dq)}. approve=false if the commands weren't actually runnable, the evidence doesn't support passed, integration/visual was claimed without real proof, or a UI change reports visual="n/a".${OBSERVABLE_DQ ? ' This change is OBSERVABLE: approve=FALSE unless visual is GENUINE real-user proof against the live running app (a click-through the dev actually drove and inspected) — REJECT unit/component tests, HTTP 200s, offline renders, or a prior screenshot offered as the visual gate.' : ''}`,
      { label: `sup:testgate:${dev.id}`, phase: 'Work', effort: E_JUDGE, schema: SUP_SCHEMA }
    )
    record.testgate = { dq, supervisor: dqOk }
    // ⚠ This regex is a COARSE screen only: it checks whether the dev's prose smuggled a unit test
    //   in as visual proof. It cannot judge whether the screen is any GOOD — that is the
    //   design-quality lens below. Treating this check as the UI quality gate is how UI quality
    //   ended up with nobody responsible for it.
    const _visualSatisfied = !OBSERVABLE_DQ || (dq && typeof dq.visual === 'string' && dq.visual.trim() &&
      !/^\s*(n\/?a|none|not applicable|n\.a\.)\s*$/i.test(dq.visual) &&
      !/\b(http\s*200|only unit|unit test|component test|offline render|prior screenshot)\b/i.test(dq.visual))
    verdict('test gate', dq && dq.ran && dq.passed ? 'PASS' : 'FAIL',
      `supervisor ${dqOk && dqOk.approve ? 'approve' : 'reject'}` +
      (OBSERVABLE_DQ ? ` · observable change, live visual proof ${_visualSatisfied ? 'present' : 'MISSING (unit tests do not count)'}` : ''))
    if (!(dq && dq.ran && dq.passed && dqOk && dqOk.approve && _visualSatisfied)) { record.status = 'test-gate-failed'; worked.push(record); continue }

    // -- 4 REVIEW: pair-review of the DIFF + fresh-context lenses (writer never grades own work) --
    // THE DESIGN-QUALITY LENS. Without it, every lens in this ring is an ENGINEERING-CORRECTNESS
    // lens (pair / correctness / conventions+tests) — so no layer of the review ring was ever
    // responsible for whether the screen was any good, and UI quality was never a condition of
    // green. Now: an OBSERVABLE change gets a 4th lens that runs the deterministic judge first and
    // then applies the [JUDGMENT] rules of DESIGN_RULEBOOK.md. It does not pass, it does not commit.
    const reviewPlan = [
      ...(DESIGN_LENS ? [{ kind: 'design-quality', run: () => agent(
        `Fresh-context DESIGN-QUALITY review. You look ONLY at the surface this change affects and at the rulebook — never at the author's reasoning.
FOLDER: ${folder}\nTASK: ${t.task}\nIMPLEMENTATION REPORT: ${JSON.stringify(impl)}
THE SQUAD'S DECLARED REVIEW SURFACE: kind=${surface.kind}${surface.label ? `, ${surface.label}` : ''}${surface.url ? `, url ${surface.url}` : ''}
  inspect: ${surface.inspect || '(none — kind is "none")'}${surface.how ? `\n  how: ${surface.how}` : ''}

THE RULEBOOK IS THE ONLY CRITERION (do not invent your own): read DESIGN_RULEBOOK.md at the repo
root. Every finding MUST cite a rule id (E-01) — a finding that cannot cite one is not a defect;
either propose a rule or let it pass.
⚠️ Rule ids may ONLY come from these ${RULE_IDS.size} (validated in code — anything else is dropped):
${RULE_ID_LIST}
Need a new rule? Write "propose a new rule: <text>" and cite E-01. Do NOT mint an id here; a new
rule must land in DESIGN_RULEBOOK.md before it can be cited.

STEP 1 — run the deterministic judge for THIS surface's kind (the referee, not an opinion; do NOT skip it):
${VISUAL_DQ ? `  This surface is VISUAL, so the judge is the design script:
    node standup/control/verify_design_quality.js <url of the page this change affects> --json /tmp/dq-${dev.id}.json
  ${DESIGN_URL ? `URL to judge: ${DESIGN_URL} (navigate to the affected route).` : surface.url ? `URL to judge: ${surface.url} (navigate to the affected route). Start it first with: ${surface.inspect}` : 'Derive the running instance URL from the project\'s own run method and START it if needed.'}
  Record the exit code + per-rule counts in machine_gate (machine_gate.url = the URL you judged).
  Exit 0 = no violations · 1 = violations · 2 = the page could not be loaded (bad URL / server down) ·
  **4 = the JUDGE itself could not run** (Playwright/Chromium unavailable). 2 and 4 both mean the gate
  produced no verdict → pass=false with the reason, never a wave-through. For 4, say explicitly that
  it is the GATE that is broken, not the page — reporting it as a design violation points attention at
  the wrong thing — and run the remediation command the script printed, then re-run; do NOT route around it.`
  : `  This surface is NOT visual (kind=${surface.kind}), so there is no screen to screenshot and
  verify_design_quality.js does not apply — it probes a DOM. The squad's DECLARED inspect command is
  the referee instead. RUN IT and record its exit code:
    ${surface.inspect}
  Put that command in machine_gate.url and ITS exit code in machine_gate.exit_code, with ran=true only
  if you actually ran it. A non-zero exit forces pass=false in code, exactly as a failing design judge
  does — which is the point of making \`inspect\` a declared, runnable field rather than prose.`}

STEP 2 — judge the rules a script cannot decide, and cite a rule id on every conclusion.
${VISUAL_DQ ? `  The [JUDGMENT] rules for a screen (B-03 color semantics, B-04 factory defaults, B-05
  indistinguishable near-duplicates, C-01 single focus, C-02 empty de-emphasis, C-04 designed empty
  states, D-02 numeral typography, D-04 title/state separation), from a REAL screenshot you took.`
  : `  Your artifact is the OUTPUT of the inspect command above (and, for this engine, the run's own
  transcript) — read it the way a user reads a screen. The F rules of DESIGN_RULEBOOK.md govern
  command-line and transcript surfaces: F-01 status must survive glyph loss (never emoji/colour
  alone), F-02 a summary line accounts for every record it counts, F-03 one separator one meaning,
  F-04 a verdict is typographically distinct from a step, F-05 a run that stopped must not end in the
  shape of a run that finished, F-06 one name per concept across every printed surface, F-07 an error
  names the valid set. A non-visual surface is NOT exempt from design; it was simply ungoverned until
  these rules existed.`}

STEP 3 — E-02: any rule id cited >=2 times means per-file tickets are FORBIDDEN. Say so in the
  verdict, and name where the shared component to change lives.

**pass is ASYMMETRIC (DESIGN_RULEBOOK E-07 — do NOT collapse it to "exit code 0 means pass"):**
- exit code NON-ZERO → pass MUST be false. That is the floor, and it is enforced in code.
- exit code 0 → **proves nothing** and is not a reason to pass. You still owe an independent
  judgment. Evidence: a page passed EVERY machine rule — geometry clean, aspect ratios exact —
  and was scored 2/10, worse than a visibly mangled page at 4/10, because its ten small-multiple
  charts were each normalized PER CARD: a value of 9 and a value of 63 were drawn at the same
  height, and two adjacent cards drew 58 at the bottom and 51 at the top, inverting the real
  ranking. The machine check was silent throughout.
  **The gate catches "looks wrong"; it is blind to "looks right, is lying."**
  So ask explicitly: could this screen lead someone to a conclusion the data does not support?
  (Usual shapes: per-card-normalized small multiples, evenly-spaced points posing as a time axis,
   percentages with no denominator, missing values ranked as if they were real categories, n=2
   drawn like n=1000, clipped data points with no indication.)
- any A- or B-class [JUDGMENT] violation → pass=false.

Do not pad the list with non-UI nitpicking. And do NOT wave something through because "it is
pre-existing, not from this change" — E-05: the gate judges the CURRENT state of the surface,
independent of who wrote it or what changed.`,
        { label: `review:${dev.id}:design-quality`, phase: 'Work', effort: E_JUDGE, schema: DESIGN_REVIEW_SCHEMA }
      ) }] : []),
      // pair review of the actual DIFF — the lanemate who challenged the plan now reviews the real change
      { kind: 'pair', run: () => agent(
        `You are "${lanemate.id}" (${lanemate.role}), the PAIR of "${dev.id}". You challenged the PLAN earlier; now review the ACTUAL DIFF — did the implementation do what the approved plan said, without regressions or scope creep?
FOLDER: ${folder}\nTASK: ${t.task}\nAPPROVED PLAN: ${JSON.stringify(plan)}\nTEST GATE: ${JSON.stringify(dq)}
Read the real working-tree diff (git -C ${folder} diff -- . ; plus untracked files in the report). pass=false if it diverges from the plan, regresses, or a blocking defect exists.`,
        { label: `pair-review:${lanemate.id}`, phase: 'Work', effort: E_JUDGE, schema: REVIEW_SCHEMA }
      ) },
      // two fresh-context lenses — only the diff + criteria, no prior context
      ...['correctness', 'conventions-and-tests'].map(lens => ({ kind: lens, run: () =>
        agent(
          `Fresh-context adversarial review, ${lens} lens. You see only the diff and criteria — not the writer's reasoning. Find defects that AFFECT CORRECTNESS or violate conventions/test requirements; do not pad with non-blocking nits as blockers.
FOLDER: ${folder}\nTASK: ${t.task}\nAPPROVED PLAN: ${JSON.stringify(plan)}\nIMPLEMENTATION REPORT: ${JSON.stringify(impl)}\nTEST GATE: ${JSON.stringify(dq)}
Read the ACTUAL working-tree diff (git -C ${folder} diff -- . ; plus untracked files listed in the report). VERIFY the test gate ran + supports passed. pass=false if any blocking defect.`,
          { label: `review:${dev.id}:${lens}`, phase: 'Work', effort: E_JUDGE, schema: REVIEW_SCHEMA }
        ) })),
    ]
    const reviews = (await parallel(reviewPlan.map(l => l.run))).filter(Boolean)
    record.reviews = reviews

    // E-07 ENFORCED IN CODE, not just asked for in the prompt. The whole point of "let scripts be
    // the referee" is that the exit code must not be RELAYED by a model that can decide to be
    // lenient about it. Non-zero exit (or a judge that never ran) => pass is forced false. Exit 0
    // is deliberately NOT forced true: it proves nothing, so the model's judgment still governs.
    const _dqReview = reviews.find(r => r && r.machine_gate)
    if (_dqReview) {
      const mg = _dqReview.machine_gate || {}
      if (!mg.ran || typeof mg.exit_code !== 'number' || mg.exit_code !== 0) {
        if (_dqReview.pass) {
          // fail-closed on EVERY non-zero exit, but name exit 4 for what it is: the gate did not run
          // (Playwright/Chromium missing), which is a BROKEN GATE, not a bad page. Same pass=false,
          // different remedy — fix the environment, do not chase a phantom design violation.
          const _why = !mg.ran ? '(never ran)'
            : mg.exit_code === 4 ? '4 — the JUDGE itself could not run (Playwright/Chromium missing); the gate is broken, not the page — install it and re-run'
            : mg.exit_code === 2 ? '2 — the page could not be loaded'
            : String(mg.exit_code)
          verdict('design gate', 'OVERRIDE', `— the lens said pass but the judge exited ${_why}; forcing pass=false (E-07: any non-zero exit always fails)`)
        }
        _dqReview.pass = false
      }
      // E-01 admission: a finding citing an id absent from DESIGN_RULEBOOK.md is dropped here and
      // never reaches the log/backlog queue. pass/green is deliberately untouched by this — it is
      // bound to the judge's exit code, not to how well the findings were labelled.
      const _adm = admitByRule(_dqReview.findings, `design-quality finding(s) on ${dev.id}`)
      _dqReview.findings = _adm.kept
      if (_adm.rejected.length) record.design_findings_inadmissible = _adm.rejected
      record.design_gate = mg
      // E-02: bank the systemic violations separately so they queue as "change the shared
      // component", not as yet another per-file ticket that sinks to the bottom of the board.
      if (Array.isArray(_dqReview.systemic) && _dqReview.systemic.length) record.design_systemic = _dqReview.systemic
    }

    // Green is derived from the lenses ACTUALLY PLANNED for this task — never a hardcoded count.
    // A hardcoded 3 is how a 4th lens gets added and silently ignored (or worse, how adding one
    // makes green unreachable).
    const green = reviews.length === reviewPlan.length && reviews.every(r => r.pass)
    record.green = green
    verdict(`review ${reviews.filter(r => r.pass).length} of ${reviewPlan.length} pass (${reviewPlan.map(l => l.kind).join(', ')})`,
      green ? 'GREEN' : 'BLOCKED',
      green ? '' : `— ${reviews.filter(r => !r.pass).map(r => String(r.verdict || '').slice(0, 60)).join(' | ').slice(0, 160)}`)

    // -- 5 COMMIT on green (feature branch, no push) --
    if (green && isGit && impl.files_changed && impl.files_changed.length) {
      record.committed = await agent(
        `The change in ${folder} PASSED plan-challenge, the test gate, and all ${reviewPlan.length} reviews. Commit it:
- branch: create ${impl.branch || 'auto/standup-<short-slug>'} from the current default branch. Commit this task's edits onto that fresh feature branch.
- stage ONLY: ${JSON.stringify(impl.files_changed)} (never git add -A; NEVER stage .standup/ progress files)
- commit message: ${impl.commit_message || '(write a clear conventional message)'}
Do NOT push/merge/deploy. Report commit hash + branch.`,
        { label: `commit:${dev.id}`, phase: 'Work', effort: E_MECH, schema: WORK_SCHEMA }
      )
      // -- 6 SUPERVISOR final review (of the COMMITTED diff — the last gate before it's called done) --
      if (record.committed && record.committed.status === 'committed') {
        record.supervisor_final = await agent(
          `You are the autonomous SUPERVISOR. FINAL review of the COMMITTED change in ${folder} before it is called done — the last gate. Read the committed diff (git -C ${folder} show HEAD -- .).
TASK/GOAL: ${t.task}\nPlan it was meant to deliver: ${JSON.stringify(plan)}\nReview verdicts: ${JSON.stringify(reviews.map(r => r.verdict))}
approve=false (with must_fix) if it does not deliver the goal, a gate was rubber-stamped, the wrong files were staged, or the commit is not what the reviews approved.`,
          { label: `sup:final:${dev.id}`, phase: 'Work', effort: E_JUDGE, schema: SUP_SCHEMA }
        )
      }
    }

    record.status = (record.supervisor_final && record.supervisor_final.approve === false) ? 'supervisor-rejected'
      : (record.committed && record.committed.status === 'committed') ? 'committed'
      : (green ? 'green-not-committed' : 'review-failed')
    verdict('task', record.status, `${record.committed && record.committed.commit ? `${record.committed.commit}` : ''}${record.committed && record.committed.branch ? ` @${record.committed.branch}` : ''}`.trim())
    worked.push(record)
   } catch (e) {
      // A routing/pairing/folder/surface STOP is NOT a per-task error and must not be softened into
      // one — it already printed its three-line block and TICK STOPPED, and the run ends here.
      // (In practice validateQueue runs before this loop, so this is belt-and-braces against a
      // later edit moving a stopTick() call inside the try.)
      if (e && e.tickStopped) throw e
      // A single agent({schema}) throw must NOT abort the whole tick — record + continue.
      log(`  → work-error: ${String((e && e.message) || e).slice(0, 140)}`)
      worked.push({ task: t.task, assignee: t.assignee, team: t.team, status: 'work-error', error: String((e && e.message) || e) })
   }
  }
}

// NOTE: the DESIGN phase used to live HERE, after Work — see Phase 2b above for why it moved.
// A design critique that runs after the commit cannot block anything, which is the entire reason
// the same defects survived tick after tick.

// THE CLOSING LINE. It used to read `N committed / M green of K worked`, which had two defects that
// mattered more than their size. (1) `worked` counted records whose statuses the numerators could
// not express, so `0 committed / 0 green of 2 worked` was emitted byte-identically for two tasks
// that ran fully and failed review — an engineering signal — and two tasks never attempted at all,
// a routing signal meaning the tick did nothing. (2) `/` carried two meanings four words apart:
// "and" in `committed / green`, then the fraction in `green of worked`. Now: the leading number is
// TASKS SEEN, every terminal status appears BY NAME with a count (see tally()), and `·` separates
// independent facts while `/` never means "and" here again.
const _worked = worked.filter(Boolean)
// A run that STOPPED must not end in the shape of a run that FINISHED. A routing/pairing/folder/
// surface stop throws and has already printed TICK STOPPED. The remaining stop-shaped ending is a
// queue in which nothing survived INTAKE — nothing was investigated, implemented or committed, and
// reporting that as DONE is the false green in miniature.
if (_worked.length && _worked.every(w => w.status === 'escalated-intake')) {
  log(`TICK STOPPED ${DATE} — ${_worked.length} task(s), all stopped at INTAKE: the outcome contract was still unclear after one revision, so nothing was investigated, implemented or committed`)
} else {
  log(`TICK DONE ${DATE} — ${_worked.length} task(s)${_worked.length ? `: ${tally(_worked)}` : ''} · ` +
    `board ${((board && board.todays_board) || []).length} item(s) · design ${design ? `${DESIGN_TASKS.length} task(s) boarded` : 'no design lead active'}`)
}

if (DO_WORK || SINGLE) await disarmTeamRunExemption()

return {
  date: DATE,
  // Which entry path ran. Both run the SAME Work loop and the SAME gates; `work` skips only the
  // roster-wide inventory phases, which have nothing to contribute to one named task.
  mode: SINGLE ? 'work' : 'standup',
  comms,
  staffPulse,
  design,
  squads: squads.map(s => ({ team: s.team, name: s.name, sync: s.sync })),
  reports,
  board,
  worked: worked.filter(Boolean),
  stats: {
    squads: TEAMS.length,
    active: DEVS.length,
    reported: reports.length,
    comms_items: comms ? (comms.items || []).length : 0,
    staff_pulse: staffPulse.length,
    staff_engaged: staffPulse.filter(p => p.engaged).length,
    red: reports.filter(r => r.health === 'red').length,
    yellow: reports.filter(r => r.health === 'yellow').length,
    worked: worked.filter(Boolean).length,
    green: worked.filter(Boolean).filter(w => w.green).length,
    committed: worked.filter(Boolean).filter(w => w.status === 'committed').length,
    // Named explicitly rather than folded into an "other" bucket: a task that stopped at INTAKE was
    // never attempted, and a consumer that cannot tell it apart from a task that failed review is
    // reading the same two opposite realities the old closing line rendered identically.
    escalated_intake: worked.filter(Boolean).filter(w => w.status === 'escalated-intake').length,
    by_status: worked.filter(Boolean).reduce((m, w) => { const s = w.status || 'unrecorded'; m[s] = (m[s] || 0) + 1; return m }, {}),
    design_tasks_boarded: DESIGN_TASKS.length,
    design_gated: worked.filter(Boolean).filter(w => w.design_gate).length,
  },
  // Rule ids cited anywhere this tick that are NOT defined in DESIGN_RULEBOOK.md. Empty means the
  // citation discipline held. Non-empty means: land the rule (with a real recorded violation, per
  // E-03) or stop citing it — an id nobody has agreed to is not a rule.
  rulebook_proposals: RULEBOOK_PROPOSALS,
  rulebook_source: RULE_IDS_SOURCE,
}
