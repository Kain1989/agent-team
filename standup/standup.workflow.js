export const meta = {
  name: 'standup-mvp',
  description: 'Slim, shareable squad standup + gated SDLC work pipeline: per-dev standup (with persistent progress files) -> squad sync -> EM board -> light staff pulse -> gated plan->challenge->implement+test->2-lens review->commit-on-green. No external services. Run it directly (Workflow tool) over the MVP roster.',
  phases: [
    { title: 'Comms',      detail: 'optional: a comms_triage staff agent reads a local messages/inbox/ -> action items (skipped unless an active comms_triage exists)' },
    { title: 'Standup',    detail: 'one read-only agent per active developer; reads <folder>/.standup/<dev>.md to resume context' },
    { title: 'Team Sync',  detail: 'per-squad merge: squad board + cross-project dependencies' },
    { title: 'Synthesize', detail: 'EM merges squad boards into one ranked board' },
    { title: 'Staff Pulse',detail: 'light-but-real lens from pm_agent (scope/say-no) + design_lead (portal UI)' },
    { title: 'Work',       detail: 'SDLC per autoworkable task: plan -> pair challenge (fresh ctx) -> implement+tests -> 2-lens review -> commit-on-green (feature branch, no push)' },
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
const DO_DESIGN = !!(A && A.design)   // morning: design_lead screenshots the portal UI + files design tasks

// Mechanical evidence-gathering (standup reporters, comms, pulse) can use a cheaper tier;
// the EM/developer work (plan/challenge/implement/review/sync) inherits the top session model.
// Leave MECH_MODEL undefined to make every agent inherit the session model (simplest for a shared MVP).
const MECH_MODEL = undefined

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
      focus: 'owns the board, says no, pins keystones, challenges plans for scope/direction' },
    { id: 'design_lead', folder: 'standup/portal', role: 'Design Lead — Clarity & Craft (Apple HIG)', git: true, active: true,
      rubric: 'Apple HIG: clarity, deference, depth; contrast >=4.5:1, focus order, the states hover/focus/loading/error/empty.',
      focus: 'owns the clarity + craft of the portal UI' },
  ],
}

let RAW = (A && A.roster) || EMBEDDED_ROSTER
if (typeof RAW === 'string') { try { RAW = JSON.parse(RAW) } catch (e) { RAW = EMBEDDED_ROSTER } }
const TEAMS = (RAW.teams || [{ id: 'workspace', name: 'Workspace', mission: '', coordination: '', developers: RAW.developers || [] }])
  .map(t => ({ ...t, developers: (t.developers || []).filter(d => d.active) }))
  .filter(t => t.developers.length > 0)
const DEVS = TEAMS.flatMap(t => t.developers.map(d => ({ ...d, _team: t.id })))
const STAFF = (RAW.staff || []).filter(s => s.active)

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
    todays_board: { type: 'array', items: { type: 'object', required: ['project', 'task', 'priority', 'assignee'], properties: {
      team: { type: 'string' }, project: { type: 'string' }, task: { type: 'string' },
      priority: { type: 'string', enum: ['P0', 'P1', 'P2'] }, effort: { type: 'string', enum: ['S', 'M', 'L'] },
      assignee: { type: 'string' }, autoworkable: { type: 'boolean' },
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
${lanemate ? `Your pair is "${lanemate.id}" (${lanemate.focus}) — you challenge each other's plans and diffs; scope this report to YOUR lane.` : ''}
STANDUP for ${DATE}. READ-ONLY — do not edit, commit, or run side effects.

RESUME CONTEXT FIRST (fixes session amnesia):
- Read your progress file ${progressFile(dev)} if it exists — it says what you did last session and what's next. Put its gist in resumed_from. If it does not exist yet, say "no progress file yet".
- Read the project's README / any notes${dev.context ? ` and ${dev.context}` : ''}, and ${dev.folder}/BACKLOG.md if present.

Evidence:
${dev.git
  ? `- git -C ${dev.folder} log --since="${SINCE}" --format='%ad %s' --date=short -- .   ('-- .' scopes to this folder)\n- git -C ${dev.folder} status --short -- .`
  : `- list files in ${dev.folder} changed recently`}
- standup/BACKLOG.md for carried tasks.

Report: DONE in window, IN PROGRESS, ranked NEXT in your lane (P0-P2, S/M/L, why), BLOCKERS, needs_from_team, health. Concrete, file-level, no filler.`,
      { label: `standup:${dev.id}`, phase: 'Standup', agentType: 'Explore', model: MECH_MODEL, schema: REPORT_SCHEMA }
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

// ---- Phase 3: SYNTHESIZE (EM board) ----
phase('Synthesize')
const board = await agent(
  `You are the Engineering Manager running standup for ${DATE} over ${squads.length} squads. Squad syncs:

${JSON.stringify(squads.map(s => ({ team: s.team, name: s.name, sync: s.sync })), null, 2)}
${comms ? `\nComms-triage routed action items (tag these source=comms on the board):\n${JSON.stringify(comms.items, null, 2)}` : ''}

Produce the EM standup: narrative across squads (call out cross-squad dependencies explicitly), overall health, today's RANKED board merged across squads (P0 first; keep team + assignee; source=standup|comms), consolidated blockers. autoworkable=true ONLY if pure code/analysis with no outward side effects.

PM DISCIPLINE (you also wear the Product Manager hat — demanding, Jobs-grade 'say no'):
- PIN keystone items: any task tagged KEYSTONE in standup/BACKLOG.md or blocking >=2 other tasks MUST rank above unblocked busywork.
- Every board item needs an outcome shape: what done means + how it is verified, one line.
- Flag dated risks at the top of blockers.`,
  { label: 'em:synthesize', phase: 'Synthesize', schema: BOARD_SCHEMA }
)

// ---- Phase 3b: STAFF PULSE (light, every tick) — pm + design lenses ----
phase('Staff Pulse')
const PULSE_CONTEXT = JSON.stringify({
  date: DATE,
  board: board && { summary: board.summary, health: board.team_health, items: (board.todays_board || []).slice(0, 20), blockers: board.blockers },
  squads: squads.map(s => ({ team: s.team, name: s.name, health: s.sync && s.sync.health, board: (s.sync && s.sync.board) || [] })),
}, null, 2)
const pulseStaff = STAFF.filter(s => s.id === 'pm_agent' || /design/i.test(s.role || ''))
const staffPulse = (await parallel(pulseStaff.map(member => () => {
  const isPM = member.id === 'pm_agent'
  const lensKick = isPM
    ? `PM lens (light, every-tick): scan THIS tick's board + squad state for scope creep, missing outcome-shapes, starved keystones. Challenge 1-3 board items where scope/direction is off; flag anything to kill/merge against standup/PM_GOALS.md if present.`
    : `Design lens (light, every-tick): give a quick design read on the live portal UI (standup/portal Mission Control page) against your rubric (${member.rubric ? member.rubric.split(':')[0] : 'Apple HIG'}); call out the single biggest clarity/craft risk. Reserve a full screenshot critique for the morning design tick.`
  return agent(
    `You are "${member.id}" — ${member.role}. EVERY-TICK STAFF PULSE for ${DATE} — a LIGHT but REAL pass.
YOUR CHARTER/RUBRIC: ${member.charter || member.rubric || member.focus || ''}
THIS TICK (board + squads):
${PULSE_CONTEXT}
${lensKick}
Keep it tight: a headline, a few concrete observations, and 0-3 light board nudges (note + owner + priority). engaged=false only if genuinely nothing in your lens is in scope.`,
    { label: `pulse:${member.id}`, phase: 'Staff Pulse', model: MECH_MODEL, schema: PULSE_SCHEMA }
  ).then(r => r ? { ...r, _staff: member.id } : null)
}))).filter(Boolean)

// ---- Phase 4: WORK — gated SDLC per task (serial; folders are shared) ----
let worked = []
if (DO_WORK) {
  phase('Work')
  const queue = (board.todays_board || [])
    .filter(t => t.autoworkable)
    .sort((a, b) => (a.priority || 'P2').localeCompare(b.priority || 'P2'))
    .slice(0, MAXTASK)
  log(`SDLC-working ${queue.length} task(s) (cap=${MAXTASK})`)

  for (const t of queue) {
   try {
    const dev = DEVS.find(d => d.id === t.assignee)
    if (!dev) { worked.push({ task: t.task, assignee: t.assignee, status: 'skipped', reason: 'unknown assignee' }); continue }
    const team = TEAMS.find(x => x.id === dev._team)
    const lanemate = (team.developers.find(x => x.id === dev.pair)) || team.developers.find(x => x.id !== dev.id) || dev
    const folder = dev.folder
    const isGit = !!dev.git
    const record = { task: t.task, assignee: dev.id, project: t.project, team: dev._team, folder, isGit }

    // -- 0 INVESTIGATE (read-only: observe reality BEFORE planning — a plan from imagination is the #1 failure) --
    const evidence = await agent(
      `You are "${dev.id}" (${dev.role}) on squad ${dev._team}, folder ${folder}. INVESTIGATE — READ-ONLY, gather real evidence; make NO edits, write NO code.
TASK: ${t.task} (${t.priority}/${t.effort}).
Read your progress file ${progressFile(dev)} (if present), the project's README${dev.context ? `, ${dev.context}` : ''}, and the ACTUAL source/data the task touches${isGit ? `, git -C ${folder} log/status -- .` : ''}. Observe reality, not imagination — verify assumptions against the real code (e.g. which component/library actually renders a thing), because a plan built on a wrong assumption is the #1 failure.
Judge FEASIBILITY, not readiness. Set task_kind='greenfield' if the task builds something NEW (a PoC, a new integration, a from-scratch module) — a ZERO baseline / "it doesn't exist yet" / a dirty branch is the EXPECTED starting point, NOT a blocker; else 'brownfield'. Set feasible=false ONLY if the task genuinely cannot be attempted (the data/API/permission it needs does not exist and cannot be obtained, or the task contradicts what the code/data shows). Report findings, files in play (for greenfield: where the new code will live), and risks.`,
      { label: `investigate:${dev.id}`, phase: 'Work', agentType: 'Explore', schema: EVIDENCE_SCHEMA }
    )
    record.evidence = evidence
    if (!evidence || evidence.feasible === false) { record.status = 'blocked-investigate'; record.reason = (evidence && evidence.risks) || 'infeasible as written'; worked.push(record); continue }

    // -- 1 PLAN (no code) — grounded in the INVESTIGATE evidence, not imagination --
    let plan = await agent(
      `You are "${dev.id}" (${dev.role}) on squad ${dev._team}. PLAN ONLY — write NO code, make NO edits.
TASK: ${t.task} (${t.priority}/${t.effort}) in folder ${folder}.
EVIDENCE from your INVESTIGATE (ground the plan in THIS, do not re-imagine): ${JSON.stringify(evidence)}
Produce a step-by-step implementation plan: exact files to touch (for a greenfield task, the new files to create), the approach, which tests you will write or run (your lane's test gate: ${dev.tests || 'project test suite'}), and risks. Plans solving the wrong problem are the #1 failure — restate the task's intent in one sentence first.`,
      { label: `plan:${dev.id}`, phase: 'Work', schema: PLAN_SCHEMA }
    )
    record.plan = plan
    if (!plan) { record.status = 'blocked'; worked.push(record); continue }

    // -- 2 PLAN CHALLENGE by the pair (fresh context, structured critique — not debate) --
    let challenge = await agent(
      `You are "${lanemate.id}" (${lanemate.role}), the PAIR of "${dev.id}" on squad ${dev._team}. Fresh-context plan review — you have NOT seen their reasoning, only the plan below. Catch wrong direction, wrong scope, missed risks, missing tests. Structured critique with specific required changes; do NOT rubber-stamp, and do NOT invent objections that don't affect correctness/direction.
TASK: ${t.task}
PLAN: ${JSON.stringify(plan, null, 2)}
Check the actual code in ${folder} where the plan makes claims. approved=true only if direction AND test plan are sound.`,
      { label: `challenge:${lanemate.id}`, phase: 'Work', schema: CHALLENGE_SCHEMA }
    )
    record.challenge = challenge
    if (challenge && !challenge.approved) {
      plan = await agent(
        `You are "${dev.id}". Your pair rejected your plan. Revise it to address EVERY required change, or push back with evidence only where they are factually wrong.
TASK: ${t.task}\nORIGINAL PLAN: ${JSON.stringify(plan, null, 2)}\nCRITIQUE: ${JSON.stringify(challenge, null, 2)}`,
        { label: `replan:${dev.id}`, phase: 'Work', schema: PLAN_SCHEMA }
      )
      record.plan = plan
      challenge = plan ? await agent(
        `You are "${lanemate.id}". Re-review the REVISED plan (your earlier critique attached). approved=true only if your required changes are addressed.
TASK: ${t.task}\nREVISED PLAN: ${JSON.stringify(plan, null, 2)}\nYOUR EARLIER CRITIQUE: ${JSON.stringify(record.challenge, null, 2)}`,
        { label: `rechallenge:${lanemate.id}`, phase: 'Work', schema: CHALLENGE_SCHEMA }
      ) : null
      record.rechallenge = challenge
    }
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
      { label: `work:${dev.id}`, phase: 'Work', schema: WORK_SCHEMA }
    )
    record.impl = impl
    if (!impl) { record.status = 'blocked'; worked.push(record); continue }

    // -- 3.5 TEST GATE (deterministic: unit ALWAYS; integration if a suite exists; visual/E2E if UI; supervisor verifies HONESTY, not just the verdict) --
    const dq = await agent(
      `You are "${dev.id}" (${dev.role}). TEST GATE for the change you just made in ${folder}. RUN the checks and record the EXACT commands + results — never claim untested work passes (ran=false / passed=false if you could not run them).
- unit/dev tests (ALWAYS): ${dev.tests || 'the project test suite'} — put the commands + results in evidence.
- integration: run the project's integration suite IF it has one; else integration="none".
- visual/E2E: IF this task changed UI, verify it LIVE the way a human user would (drive the real running instance / Playwright / a headless screenshot you actually read — NOT an HTTP 200) and put that proof in visual; else visual="n/a".`,
      { label: `testgate:${dev.id}`, phase: 'Work', schema: DQ_SCHEMA }
    )
    const dqOk = await agent(
      `You are the autonomous SUPERVISOR. Verify the TEST GATE's HONESTY, not just its verdict. Report: ${JSON.stringify(dq)}. approve=false if the commands weren't actually runnable, the evidence doesn't support passed, integration/visual was claimed without real proof, or a UI change reports visual="n/a".`,
      { label: `sup:testgate:${dev.id}`, phase: 'Work', schema: SUP_SCHEMA }
    )
    record.testgate = { dq, supervisor: dqOk }
    if (!(dq && dq.ran && dq.passed && dqOk && dqOk.approve)) { record.status = 'test-gate-failed'; worked.push(record); continue }

    // -- 4 REVIEW: pair-review of the DIFF + two fresh-context lenses (writer never grades own work) --
    const reviews = (await parallel([
      // pair review of the actual DIFF — the lanemate who challenged the plan now reviews the real change
      () => agent(
        `You are "${lanemate.id}" (${lanemate.role}), the PAIR of "${dev.id}". You challenged the PLAN earlier; now review the ACTUAL DIFF — did the implementation do what the approved plan said, without regressions or scope creep?
FOLDER: ${folder}\nTASK: ${t.task}\nAPPROVED PLAN: ${JSON.stringify(plan)}\nTEST GATE: ${JSON.stringify(dq)}
Read the real working-tree diff (git -C ${folder} diff -- . ; plus untracked files in the report). pass=false if it diverges from the plan, regresses, or a blocking defect exists.`,
        { label: `pair-review:${lanemate.id}`, phase: 'Work', schema: REVIEW_SCHEMA }
      ),
      // two fresh-context lenses — only the diff + criteria, no prior context
      ...['correctness', 'conventions-and-tests'].map(lens => () =>
        agent(
          `Fresh-context adversarial review, ${lens} lens. You see only the diff and criteria — not the writer's reasoning. Find defects that AFFECT CORRECTNESS or violate conventions/test requirements; do not pad with non-blocking nits as blockers.
FOLDER: ${folder}\nTASK: ${t.task}\nAPPROVED PLAN: ${JSON.stringify(plan)}\nIMPLEMENTATION REPORT: ${JSON.stringify(impl)}\nTEST GATE: ${JSON.stringify(dq)}
Read the ACTUAL working-tree diff (git -C ${folder} diff -- . ; plus untracked files listed in the report). VERIFY the test gate ran + supports passed. pass=false if any blocking defect.`,
          { label: `review:${dev.id}:${lens}`, phase: 'Work', schema: REVIEW_SCHEMA }
        )),
    ])).filter(Boolean)
    record.reviews = reviews
    const green = reviews.length === 3 && reviews.every(r => r.pass)
    record.green = green

    // -- 5 COMMIT on green (feature branch, no push) --
    if (green && isGit && impl.files_changed && impl.files_changed.length) {
      record.committed = await agent(
        `The change in ${folder} PASSED plan-challenge, the test gate, and all three reviews. Commit it:
- branch: create ${impl.branch || 'auto/standup-<short-slug>'} from the current default branch. Commit this task's edits onto that fresh feature branch.
- stage ONLY: ${JSON.stringify(impl.files_changed)} (never git add -A; NEVER stage .standup/ progress files)
- commit message: ${impl.commit_message || '(write a clear conventional message)'}
Do NOT push/merge/deploy. Report commit hash + branch.`,
        { label: `commit:${dev.id}`, phase: 'Work', schema: WORK_SCHEMA }
      )
      // -- 6 SUPERVISOR final review (of the COMMITTED diff — the last gate before it's called done) --
      if (record.committed && record.committed.status === 'committed') {
        record.supervisor_final = await agent(
          `You are the autonomous SUPERVISOR. FINAL review of the COMMITTED change in ${folder} before it is called done — the last gate. Read the committed diff (git -C ${folder} show HEAD -- .).
TASK/GOAL: ${t.task}\nPlan it was meant to deliver: ${JSON.stringify(plan)}\nReview verdicts: ${JSON.stringify(reviews.map(r => r.verdict))}
approve=false (with must_fix) if it does not deliver the goal, a gate was rubber-stamped, the wrong files were staged, or the commit is not what the reviews approved.`,
          { label: `sup:final:${dev.id}`, phase: 'Work', schema: SUP_SCHEMA }
        )
      }
    }

    record.status = (record.supervisor_final && record.supervisor_final.approve === false) ? 'supervisor-rejected'
      : (record.committed && record.committed.status === 'committed') ? 'committed'
      : (green ? 'green-not-committed' : 'review-failed')
    worked.push(record)
   } catch (e) {
      // A single agent({schema}) throw must NOT abort the whole tick — record + continue.
      worked.push({ task: t.task, assignee: t.assignee, team: t.team, status: 'work-error', error: String((e && e.message) || e) })
   }
  }
}

// ---- Phase 5: DESIGN (morning only, args.design) — design_lead on the portal UI ----
let design = null
if (DO_DESIGN) {
  phase('Staff Pulse')
  const lead = STAFF.find(s => /design/i.test(s.role || ''))
  if (lead) {
    design = await agent(
      `You are "${lead.id}" — ${lead.role} for the Mission Control portal (pairs with portal_frontend). Date ${DATE}. Be demanding, not polite.
YOUR RUBRIC: ${lead.rubric || 'Apple HIG: clarity, deference, depth + the states hover/focus/loading/error/empty.'}
Loop:
1. Read standup/portal/.standup/${lead.id}.md (your progress file) if present.
2. Get the CURRENT portal UI: read standup/portal/static/index.html + app.css + app.js (and screenshot the live page at http://127.0.0.1:8770 if the portal is running).
3. Critique strictly against YOUR rubric; score 1-10.
4. Produce a RANKED design-task list (P0-P2, S/M/L) with concrete file-level fixes; autoworkable=true ONLY for pure CSS/layout with no behavior change.
5. Append a dated entry to your progress file. Do NOT implement here — tasks enter the gate chain next tick.`,
      { label: `design:${lead.id}`, phase: 'Staff Pulse', schema: {
        type: 'object', required: ['summary', 'tasks'], properties: {
          summary: { type: 'string' }, score: { type: 'number' },
          tasks: { type: 'array', items: { type: 'object', required: ['task', 'priority', 'effort'], properties: {
            task: { type: 'string' }, priority: { type: 'string', enum: ['P0', 'P1', 'P2'] },
            effort: { type: 'string', enum: ['S', 'M', 'L'] }, files: { type: 'string' },
            autoworkable: { type: 'boolean' } } } } } } }
    )
  }
}

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
  },
}
