export const meta = {
  name: 'standup-mvp',
  description: 'Slim, shareable squad standup + gated SDLC work pipeline: per-dev standup (with persistent progress files) -> squad sync -> EM board -> light staff pulse -> gated plan->challenge->implement+test->2-lens review->commit-on-green. No external services. Run it directly (Workflow tool) over the MVP roster.',
  phases: [
    { title: 'Comms',      detail: 'optional: a comms_triage staff agent reads a local messages/inbox/ -> action items (skipped unless an active comms_triage exists)' },
    { title: 'Standup',    detail: 'one read-only agent per active developer; reads <folder>/.standup/<dev>.md to resume context' },
    { title: 'Team Sync',  detail: 'per-squad merge: squad board + cross-project dependencies' },
    { title: 'Synthesize', detail: 'EM merges squad boards into one ranked board' },
    { title: 'Design',     detail: 'design_lead runs the deterministic judge (control/verify_design_quality.js) over the live UI, then judges the [JUDGMENT] rules of DESIGN_RULEBOOK.md; every finding cites a rule id. Runs BEFORE Synthesize so its tasks land on THIS tick\'s board instead of in a progress file nobody reads' },
    { title: 'Staff Pulse',detail: 'light-but-real lens from pm_agent (scope/say-no) + design_lead (delivery of the design queue)' },
    { title: 'Work',       detail: 'SDLC per autoworkable task: plan -> pair challenge (fresh ctx) -> implement+tests -> review (pair + correctness + conventions+tests, PLUS a design-quality lens whenever the change is observable) -> commit-on-green (feature branch, no push)' },
  ],
}

// ---- inputs ----
// args = { date, since, roster, work:false, maxTasks:2 }  — pr/merge/deploy are intentionally absent (MVP).
let A = args
if (typeof A === 'string') { try { A = JSON.parse(A) } catch (e) { A = null } }
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

// Embedded fallback roster (source of truth: standup/team.json — the launcher passes it as args.roster).
const EMBEDDED_ROSTER = {
  teams: [
    { id: 'demo_squad', name: 'Demo Dev Squad',
      mission: 'Builds + maintains the bundled demo-app (a small Python library) through the full gated SDLC.',
      coordination: 'Two paired developer-agents who challenge each other in fresh context; dev_a builds, dev_b reviews.',
      developers: [
        { id: 'dev_a', folder: 'demo-app', role: 'Developer — Builder',  git: true, active: true, pair: 'dev_b', focus: 'implement demo-app backlog items with tests', context: 'demo-app/README.md', tests: 'pytest (demo-app/tests)' },
        { id: 'dev_b', folder: 'demo-app', role: 'Developer — Reviewer & Tests', git: true, active: true, pair: 'dev_a', focus: 'fresh-context plan/diff review, test coverage, edge cases', context: 'demo-app/README.md', tests: 'pytest (demo-app/tests)' },
      ] },
    { id: 'portal', name: 'Team Portal Squad',
      mission: 'Builds + owns the local Mission Control portal (standup/portal) — the team status board + job approval inbox.',
      coordination: 'FastAPI backend + a no-build static page integrate via a fixed JSON contract; the pair challenge each other.',
      developers: [
        { id: 'portal_backend',  folder: 'standup/portal', role: 'Portal Dev — Backend & Jobs (FastAPI)', git: true, active: true, pair: 'portal_frontend', focus: 'parsers, the read+job API, the job lifecycle + guardrails', tests: 'pytest (portal/tests)' },
        { id: 'portal_frontend', folder: 'standup/portal', role: 'Portal Dev — Mission Control UI', git: true, active: true, pair: 'portal_backend', focus: 'the single-window page + the approve/reject affordances', tests: 'the python API contract tests' },
      ] },
  ],
  staff: [
    { id: 'pm_agent', folder: 'standup', role: 'Product Manager Agent (Steve Jobs-grounded)', git: false, active: true,
      focus: 'owns the board, says no, pins keystones, challenges plans for scope/direction', persona: PERSONA_PM },
    { id: 'design_lead', folder: 'standup/portal', role: 'Design Lead — Clarity & Craft (Apple HIG)', git: true, active: true,
      rubric: 'Apple HIG: clarity, deference, depth; contrast >=4.5:1, focus order, the states hover/focus/loading/error/empty.',
      focus: 'owns the clarity + craft of the portal UI', persona: PERSONA_DESIGN_LEAD },
    // product_qa — the one role whose whole job is to USE the product as a user (Playwright/curl) and
    // report. needs_bash (not git): it operates the product, it does not patch source.
    { id: 'product_qa', folder: 'standup/portal', scope_folders: ['standup/portal', 'demo-app'],
      role: 'Product QA — user-perspective acceptance (actually uses the product every tick)',
      git: false, needs_bash: true, active: true,
      focus: QA_FOCUS, charter: QA_CHARTER, persona: PERSONA_PRODUCT_QA },
  ],
}

let RAW = (A && A.roster) || EMBEDDED_ROSTER
if (typeof RAW === 'string') { try { RAW = JSON.parse(RAW) } catch (e) { RAW = EMBEDDED_ROSTER } }
const TEAMS = (RAW.teams || [{ id: 'workspace', name: 'Workspace', mission: '', coordination: '', developers: RAW.developers || [] }])
  .map(t => ({ ...t, developers: (t.developers || []).filter(d => d.active) }))
  .filter(t => t.developers.length > 0)
const DEVS = TEAMS.flatMap(t => t.developers.map(d => ({ ...d, _team: t.id })))
const STAFF = (RAW.staff || []).filter(s => s.active)

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
// Fallback ONLY — same contract as EMBEDDED_ROSTER above: the FILE is the source of truth, and
// RULE_IDS_SOURCE reports which one was used (a silently-embedded copy drifting from the rulebook
// would be this same bug wearing the other mask).
const EMBEDDED_RULE_IDS = ['A-01','A-02','A-03','A-04','B-01','B-02','B-03','B-04','B-05','B-06',
  'C-01','C-02','C-03','C-04','D-01','D-02','D-03','D-04','E-01','E-02','E-03','E-04','E-05','E-06','E-07']
let RULE_IDS = null
let RULE_IDS_SOURCE = 'embedded fallback (DESIGN_RULEBOOK.md unreadable from this harness)'
try {
  const _fs = await import('node:fs')
  for (const p of RULEBOOK_PATHS) {
    let src = null
    try { src = _fs.readFileSync(p, 'utf8') } catch (e) { continue }
    // Wide family match (A-Z, not just today's A-E) so a NEW rule family is citable the moment it
    // lands in the rulebook, with no edit here.
    const ids = String(src).match(/\b[A-Z]-\d{2}\b/g)
    if (ids && ids.length) { RULE_IDS = new Set(ids); RULE_IDS_SOURCE = p; break }
  }
} catch (e) { /* no fs in this harness — fall through to the embedded set */ }
if (!RULE_IDS) RULE_IDS = new Set(EMBEDDED_RULE_IDS)
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
  if (rejected.length) log(`E-01 admission: dropped ${rejected.length}/${rejected.length + kept.length} ${what} — ${rejected.map(r => r._inadmissible).join(' ; ')}`)
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
    required_changes: { type: 'array', items: { type: 'string' } } },
}

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
  properties: { approve: { type: 'boolean' }, note: { type: 'string' }, must_fix: { type: 'array', items: { type: 'string' } } },
}

const progressFile = dev => `${dev.folder}/.standup/${dev.id}.md`

// ---- Phase 0: COMMS (optional staff triage over a local inbox) ----
phase('Comms')
let comms = null
const triage = STAFF.find(s => s.id === 'comms_triage')
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
phase('Standup')
log(`Standup ${DATE} — ${TEAMS.length} squad(s), ${DEVS.length} devs, window="${SINCE}"${comms ? `, comms items: ${(comms.items || []).length}` : ''}`)

const squads = (await parallel(TEAMS.map(team => async () => {
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
log(`SQUADS ${squads.length} synced / ${reports.length} dev report(s): ` +
  squads.map(s => `${s.team}=${(s.sync && s.sync.health) || '?'}`).join(' '))
{
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
phase('Design')
const DESIGN_LEADS = STAFF.filter(s => s.role && /design/i.test(s.role))
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
phase('Synthesize')
const board = await agent(
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
{
  const _items = (board && board.todays_board) || []
  log(`BOARD ${_items.length} item(s), ${_items.filter(t => t.priority === 'P0').length} P0, ` +
    `${_items.filter(t => t.autoworkable).length} autoworkable · team health ${(board && board.team_health) || '?'}` +
    (_items.length ? ` · top: ${String(_items[0].task || '').slice(0, 80)}` : ''))
  if (RULEBOOK_PROPOSALS.length) {
    log(`RULEBOOK PROPOSALS this tick — ids cited but not defined: ${RULEBOOK_PROPOSALS.join(', ')}. Land them in DESIGN_RULEBOOK.md (with a real recorded violation, per E-03) or stop citing them.`)
  }
}

// ---- Phase 3b: STAFF PULSE (light, every tick) — pm + design lenses ----
phase('Staff Pulse')
const PULSE_CONTEXT = JSON.stringify({
  date: DATE,
  board: board && { summary: board.summary, health: board.team_health, items: (board.todays_board || []).slice(0, 20), blockers: board.blockers },
  squads: squads.map(s => ({ team: s.team, name: s.name, health: s.sync && s.sync.health, board: (s.sync && s.sync.board) || [] })),
}, null, 2)
const pulseStaff = STAFF.filter(s => s.id === 'pm_agent' || s.id === 'product_qa' || /design/i.test(s.role || ''))
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
log(`STAFF PULSE ${staffPulse.filter(p => p.engaged).length}/${staffPulse.length} engaged: ` +
  staffPulse.map(p => `${p._staff}${p.engaged ? '' : '(skip)'}`).join(' '))

// ---- Phase 4: WORK — gated SDLC per task (serial; folders are shared) ----
let worked = []
if (DO_WORK) {
  phase('Work')
  const _autoworkable = (board.todays_board || []).filter(t => t.autoworkable)
  const queue = _autoworkable
    .sort((a, b) => (a.priority || 'P2').localeCompare(b.priority || 'P2'))
    .slice(0, MAXTASK)
  log(`WORK QUEUE ${queue.length} of ${_autoworkable.length} autoworkable board item(s) (cap=${MAXTASK})` +
    (queue.length ? `: ${queue.map(t => `${t.assignee}/${String(t.task || '').slice(0, 50)}`).join(' | ')}` : ' — nothing autoworkable this tick'))

  for (const t of queue) {
   try {
    const dev = DEVS.find(d => d.id === t.assignee)
    if (!dev) { worked.push({ task: t.task, assignee: t.assignee, status: 'skipped', reason: 'unknown assignee' }); continue }
    const team = TEAMS.find(x => x.id === dev._team)
    const lanemate = (team.developers.find(x => x.id === dev.pair)) || team.developers.find(x => x.id !== dev.id) || dev
    const folder = dev.folder
    const isGit = !!dev.git
    const record = { task: t.task, assignee: dev.id, project: t.project, team: dev._team, folder, isGit }
    log(`TASK ${dev.id} · ${folder} · ${String(t.task).slice(0, 90)}`)

    // -- 0 INVESTIGATE (read-only: observe reality BEFORE planning — a plan from imagination is the #1 failure) --
    const evidence = await agent(
      `You are "${dev.id}" (${dev.role}) on squad ${dev._team}, folder ${folder}. INVESTIGATE — READ-ONLY, gather real evidence; make NO edits, write NO code.
TASK: ${t.task} (${t.priority}/${t.effort}).
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
Check the actual code in ${folder} where the plan makes claims. approved=true only if direction AND test plan are sound.`,
      { label: `challenge:${lanemate.id}`, phase: 'Work', effort: E_JUDGE, schema: CHALLENGE_SCHEMA }
    )
    record.challenge = challenge
    if (challenge && !challenge.approved) {
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
    log(`  plan ${challenge && challenge.approved ? 'APPROVED by the pair' : 'REJECTED by the pair'}` +
      (challenge && challenge.required_changes && challenge.required_changes.length ? ` (${challenge.required_changes.length} required change(s))` : ''))
    if (!challenge || !challenge.approved) { record.status = 'escalated-plan-rejected'; worked.push(record); continue }

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

    // OBSERVABLE = the change produces something a user can SEE (a chart, page, panel, flow,
    // endpoint output). OWNER-AGNOSTIC on purpose: a BACKEND change that alters what renders is
    // still observable. Detected from the role/lane/task text AND re-checked against the files
    // actually changed, so a backend lane cannot quietly opt out of the visual gate.
    // DELIBERATELY OVER-INCLUSIVE: because the role text counts, a UI-lane developer's change is
    // treated as observable even when the diff looks like plumbing — a job-store refactor behind a
    // board IS a rendering change. The cost of a false positive is one wasted verification; the
    // cost of a false negative is shipping a broken screen. If your project has a lane that
    // genuinely never renders anything, narrow the pattern here rather than teaching devs to write
    // task titles that dodge it.
    const _touchedFrontend = Array.isArray(impl.files_changed) && impl.files_changed.some(p =>
      /(?:^|\/)(?:frontend|web|ui|client|static|templates)\//i.test(String(p)) || /\.(?:jsx|tsx|vue|svelte)$/i.test(String(p)) || /\.html?$/i.test(String(p)) || /\.s?css$/i.test(String(p)))
    const OBSERVABLE_DQ = /chart|dashboard|render|panel|button|click|screen|\bpage\b|\btab\b|modal|dialog|widget|visual|user (?:sees|clicks|views)|\bUI\b|frontend|endpoint/i.test(`${dev.role || ''} ${dev.focus || ''} ${t.task || ''}`) || _touchedFrontend

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
    log(`  test gate ${dq && dq.ran && dq.passed ? 'PASS' : 'FAIL'} · supervisor ${dqOk && dqOk.approve ? 'approve' : 'reject'}` +
      (OBSERVABLE_DQ ? ` · observable change → live visual proof ${_visualSatisfied ? 'present' : 'MISSING (unit tests do not count)'}` : ''))
    if (!(dq && dq.ran && dq.passed && dqOk && dqOk.approve && _visualSatisfied)) { record.status = 'test-gate-failed'; worked.push(record); continue }

    // -- 4 REVIEW: pair-review of the DIFF + fresh-context lenses (writer never grades own work) --
    // THE DESIGN-QUALITY LENS. Without it, every lens in this ring is an ENGINEERING-CORRECTNESS
    // lens (pair / correctness / conventions+tests) — so no layer of the review ring was ever
    // responsible for whether the screen was any good, and UI quality was never a condition of
    // green. Now: an OBSERVABLE change gets a 4th lens that runs the deterministic judge first and
    // then applies the [JUDGMENT] rules of DESIGN_RULEBOOK.md. It does not pass, it does not commit.
    const reviewPlan = [
      ...(OBSERVABLE_DQ ? [{ kind: 'design-quality', run: () => agent(
        `Fresh-context DESIGN-QUALITY review. You look ONLY at the UI this change affects and at the rulebook — never at the author's reasoning.
FOLDER: ${folder}\nTASK: ${t.task}\nIMPLEMENTATION REPORT: ${JSON.stringify(impl)}

THE RULEBOOK IS THE ONLY CRITERION (do not invent your own): read DESIGN_RULEBOOK.md at the repo
root. Every finding MUST cite a rule id (E-01) — a finding that cannot cite one is not a defect;
either propose a rule or let it pass.
⚠️ Rule ids may ONLY come from these ${RULE_IDS.size} (validated in code — anything else is dropped):
${RULE_ID_LIST}
Need a new rule? Write "propose a new rule: <text>" and cite E-01. Do NOT mint an id here; a new
rule must land in DESIGN_RULEBOOK.md before it can be cited.

STEP 1 — run the deterministic judge (the referee, not an opinion; do NOT skip it):
    node standup/control/verify_design_quality.js <url of the page this change affects> --json /tmp/dq-${dev.id}.json
  ${DESIGN_URL ? `URL to judge: ${DESIGN_URL} (navigate to the affected route).` : 'Derive the running instance URL from the project\'s own run method and START it if needed.'}
  Record the exit code + per-rule counts in machine_gate.
  Exit 0 = no violations · 1 = violations · 2 = the page could not be loaded (bad URL / server down) ·
  **4 = the JUDGE itself could not run** (Playwright/Chromium unavailable). 2 and 4 both mean the gate
  produced no verdict → pass=false with the reason, never a wave-through. For 4, say explicitly that
  it is the GATE that is broken, not the page — reporting it as a design violation points attention at
  the wrong thing — and run the remediation command the script printed, then re-run; do NOT route around it.

STEP 2 — judge the [JUDGMENT] rules the script cannot decide (B-03 color semantics, B-04 factory
  defaults, B-05 indistinguishable near-duplicates, C-01 single focus, C-02 empty de-emphasis,
  C-04 designed empty states, D-02 numeral typography, D-04 title/state separation) from a REAL
  screenshot. Cite a rule id on every conclusion.

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
          log(`  design gate OVERRIDE: lens said pass but the judge exited ${_why} — forcing pass=false (E-07: any non-zero exit always fails)`)
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
    log(`  review ${reviews.filter(r => r.pass).length}/${reviewPlan.length} pass (${reviewPlan.map(l => l.kind).join(', ')})` +
      (green ? ' → GREEN' : ` → blocked by: ${reviews.filter(r => !r.pass).map(r => String(r.verdict || '').slice(0, 60)).join(' | ').slice(0, 160)}`))

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
    log(`  → ${record.status}${record.committed && record.committed.commit ? ` ${record.committed.commit}` : ''}${record.committed && record.committed.branch ? ` @${record.committed.branch}` : ''}`)
    worked.push(record)
   } catch (e) {
      // A single agent({schema}) throw must NOT abort the whole tick — record + continue.
      log(`  → work-error: ${String((e && e.message) || e).slice(0, 140)}`)
      worked.push({ task: t.task, assignee: t.assignee, team: t.team, status: 'work-error', error: String((e && e.message) || e) })
   }
  }
}

// NOTE: the DESIGN phase used to live HERE, after Work — see Phase 2b above for why it moved.
// A design critique that runs after the commit cannot block anything, which is the entire reason
// the same defects survived tick after tick.

log(`TICK DONE ${DATE} — ${worked.filter(Boolean).filter(w => w.status === 'committed').length} committed / ` +
  `${worked.filter(Boolean).filter(w => w.green).length} green of ${worked.filter(Boolean).length} worked · ` +
  `board ${((board && board.todays_board) || []).length} item(s) · design ${design ? `${DESIGN_TASKS.length} task(s) boarded` : 'no design lead active'}`)

return {
  date: DATE,
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
    design_tasks_boarded: DESIGN_TASKS.length,
    design_gated: worked.filter(Boolean).filter(w => w.design_gate).length,
  },
  // Rule ids cited anywhere this tick that are NOT defined in DESIGN_RULEBOOK.md. Empty means the
  // citation discipline held. Non-empty means: land the rule (with a real recorded violation, per
  // E-03) or stop citing it — an id nobody has agreed to is not a rule.
  rulebook_proposals: RULEBOOK_PROPOSALS,
  rulebook_source: RULE_IDS_SOURCE,
}
