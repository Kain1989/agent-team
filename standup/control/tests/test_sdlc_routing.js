#!/usr/bin/env node
/**
 * SDLC routing + review-target judge.
 *
 *     node standup/control/tests/test_sdlc_routing.js
 *     node standup/control/tests/test_sdlc_routing.js --self-test
 *
 * WHAT IT GUARDS. One defect class: **the machine does not complain when it is aimed at the wrong
 * thing.** A team pointed at a mistyped assignee, at a developer with no pair, at a folder nobody
 * owns, or at a raw ask nobody turned into a contract, used to produce a clean-looking report about
 * work it never did. Each case below is one way that happened.
 *
 * WHY IT LOADS THE ENGINE INSTEAD OF GREPPING IT. Parse != loadable != correct, and source text is
 * not behaviour. The two throws it checks sit near a try/catch whose handler records a soft status
 * and continues; a grep-based check would go green on the text while the run stayed soft. So this
 * judge loads the REAL engine source, strips `export const meta =`, and executes it inside
 * `new Function` with the same eight parameters the Workflow host supplies — agent/parallel/
 * pipeline/phase/log/workflow all stubbed. No real agents, no writes, no network.
 *
 * --self-test IS THE PROOF OF TEETH (DESIGN_RULEBOOK E-03). A list of reversals someone performed
 * by hand once is true the day it is written and false at the next edit. Instead, seven NAMED
 * mutations of the engine source are applied in memory, and each must drive a named case RED. A
 * mutation whose anchor no longer matches the source is a HARD ERROR (exit 3), never a skip — a
 * mutation that silently no-ops reads as a pass, which is precisely the gate-that-never-fires this
 * whole judge exists to delete.
 *
 * Exit codes (same vocabulary as control/verify_design_quality.js):
 *   0  pass
 *   1  failures
 *   3  the judge itself is logically broken (a fixture no longer matches the source)
 *   64 usage error
 */
'use strict';
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '../..');            // standup/
const REPO = path.resolve(ROOT, '..');                    // repo root
const ENGINE = path.join(ROOT, 'standup.workflow.js');

const roster = JSON.parse(fs.readFileSync(path.join(ROOT, 'team.json'), 'utf8'));
const engineSrc = fs.readFileSync(ENGINE, 'utf8').replace(/^\s*export\s+const\s+meta\s*=/m, 'const meta =');

// ---------- verdict vocabulary (F-01: a verdict is a WORD, never a glyph alone; F-04: `→`
// introduces a verdict and nothing else) ----------
let fails = 0;
const cases = new Set();
const failed = new Set();
function check(name, ok, detail) {
  cases.add(name);
  if (!ok) { fails++; failed.add(name); }
  console.log(`  ${name} → ${ok ? 'PASS' : 'FAIL'}${detail ? `  ${detail}` : ''}`);
  return ok;
}
const section = (t) => console.log(`\n${t}`);

// ---------- the host simulator ----------
// The engine is run the way the Workflow harness runs it. `hooks` decides what each stub returns,
// which is how a supervisor rejection or an agent-call count gets forced.
function makeRunner(src, hooks) {
  return (args) => new Function(
    'args', 'agent', 'parallel', 'pipeline', 'phase', 'log', 'workflow', 'budget',
    '"use strict"; return (async () => {\n' + src + '\n})()'
  )(args, hooks.agent, hooks.parallel, hooks.pipeline, hooks.phase, hooks.log, hooks.workflow,
    { total: null, spent: () => 0, remaining: () => Infinity });
}

// A recording host. `reply` maps an agent label to its canned structured result; anything it does
// not answer throws a sentinel, so a run that reaches an unexpected agent is loud rather than silent.
function host(reply) {
  const rec = { logs: [], labels: [], phases: [], prompts: [] };
  const h = {
    agent: async (prompt, opts) => {
      const label = (opts && opts.label) || '(unlabelled)';
      rec.labels.push(label);
      rec.prompts.push({ label, prompt: String(prompt) });
      const r = reply(label, String(prompt), rec);
      if (r !== undefined) return r;
      // ARM/DISARM open and close every code-writing run (they arm the supervisor-gate exemption
      // so dispatched dev agents can write at all — see section I). For every OTHER case they are
      // noise, so they default to "armed successfully". A case that wants to exercise a FAILED
      // arm just answers the label itself, and this default steps aside.
      if (/^arm:/.test(label)) return { flag_present: true, set_by_me: true, detail: 'STUBBED' };
      if (/^disarm:/.test(label)) return 'STUBBED';
      throw new Error('SENTINEL-AGENT: ' + label);
    },
    parallel: async (fns) => {
      const out = [];
      for (const f of fns) out.push(await f());
      return out;
    },
    pipeline: async () => { throw new Error('SENTINEL-PIPELINE'); },
    phase: (t) => { rec.phases.push(t); },
    log: (m) => { rec.logs.push(String(m)); },
    workflow: async () => { throw new Error('SENTINEL-WORKFLOW'); },
  };
  return { rec, hooks: h };
}

const TASK = { task: 'add a helper to textkit', assignee: 'dev_a', priority: 'P1', effort: 'S' };
// Reaching INVESTIGATE proves INTAKE was passed, not skipped: it is the step immediately after.
const STOP_AT_INVESTIGATE = 'SENTINEL-REACHED-INVESTIGATE';
const okContract = { goal: 'g', acceptance: ['a'], verification: 'v', priority: 'P1', out_of_scope: [] };

// A board the Synthesize agent would plausibly return, so the DEFAULT (board) path can be exercised
// with the same fidelity as the single-task path.
const boardReply = (assignee, extra) => ({
  summary: 's', team_health: 'green', blockers: [],
  todays_board: [Object.assign({
    team: 'demo_squad', project: 'demo-app', task: 'add a helper to textkit',
    priority: 'P1', effort: 'S', assignee, autoworkable: true,
    acceptance: 'pytest passes', serves_goal: 'NONE — smoke',
  }, extra || {})],
});

// Replies for the roster-wide phases so the DEFAULT path can run to Work without real agents.
function standupPhaseReply(label, assignee, extra) {
  if (label.startsWith('standup:')) return { project: 'demo-app', health: 'green', done: [], next: [], blockers: [], observations: [] };
  if (label.startsWith('sync:')) return { team: 'demo_squad', health: 'green', summary: 's', board: [], dependencies: [], blockers: [] };
  if (label.startsWith('design:')) return { summary: 's', tasks: [] };
  if (label.startsWith('pulse:')) return { lens: 'l', engaged: false, observations: [] };
  if (label === 'em:synthesize') return boardReply(assignee, extra);
  return undefined;
}

// ---------- the cases ----------
async function runCases(src) {
  fails = 0;
  cases.clear();
  failed.clear();

  // ═══ A. INTAKE is LIVE on the single-task path (/work) ═══
  section('A. INTAKE on the single-task path (/work)');
  {
    const { rec, hooks } = host((label) => {
      if (label === 'intake:pm') return okContract;
      if (label === 'sup:intake') return { approve: true, note: 'ok' };
      if (label.startsWith('investigate:')) throw new Error(STOP_AT_INVESTIGATE);
      return undefined;
    });
    // NOTE the shape of this assertion. The sentinel thrown at INVESTIGATE does NOT escape the run:
    // the Work loop's per-task try/catch legitimately records `work-error` and continues, which is
    // exactly why the routing guards had to be hoisted out of that try in the first place. So
    // "did it get past INTAKE" is proven by the recorded outcome, never by a propagating throw.
    const out = await makeRunner(src, hooks)({ date: 'D', roster, task: TASK });
    const w = (out && out.worked && out.worked[0]) || {};
    check('single-task path reaches INTAKE', rec.labels.includes('intake:pm'),
      'labels=' + rec.labels.join(','));
    check('INTAKE is supervisor-gated, not self-certified', rec.labels.includes('sup:intake'));
    check('INTAKE runs BEFORE investigate',
      rec.labels.indexOf('intake:pm') >= 0 && rec.labels.indexOf('intake:pm') < rec.labels.indexOf('investigate:dev_a'));
    check('the approved contract is handed to INVESTIGATE (not merely produced)',
      (rec.prompts.find(p => p.label.startsWith('investigate:')) || { prompt: '' }).prompt.includes('OUTCOME CONTRACT'));
    check('/work skips the roster-wide phases', !rec.phases.some(p => /Comms|Standup|Synthesize|Staff Pulse/.test(p)),
      'phases=' + rec.phases.join(','));
    check('/work still enters Work', rec.phases.includes('Work'));
    check('an approved contract lets the task PROCEED past INTAKE',
      rec.labels.includes('investigate:dev_a') && w.status !== 'escalated-intake',
      'status=' + w.status);
    check('mode reports the entry path taken', out && out.mode === 'work', 'mode=' + (out && out.mode));
  }

  // ═══ B. INTAKE is LIVE on the DEFAULT path (the board loop) ═══
  // Acceptance is about the path a user gets BY DEFAULT. A judge that only exercises the new path
  // cannot catch the one outcome that is forbidden: a new INTAKE-bearing path while the default
  // path still skips INTAKE.
  section('B. INTAKE on the DEFAULT path (the board loop)');
  {
    const { rec, hooks } = host((label) => {
      const s = standupPhaseReply(label, 'dev_a');
      if (s !== undefined) return s;
      if (label === 'intake:pm') return okContract;
      if (label === 'sup:intake') return { approve: true, note: 'ok' };
      if (label.startsWith('investigate:')) throw new Error(STOP_AT_INVESTIGATE);
      return undefined;
    });
    const out = await makeRunner(src, hooks)({ date: 'D', roster, work: true, maxTasks: 1 });
    const w = (out && out.worked && out.worked[0]) || {};
    check('default path runs the roster-wide phases', rec.phases.includes('Standup') && rec.phases.includes('Synthesize'));
    check('default path reaches INTAKE too', rec.labels.includes('intake:pm'), 'labels=' + rec.labels.slice(-6).join(','));
    check('default path INTAKE precedes investigate',
      rec.labels.indexOf('intake:pm') < rec.labels.indexOf('investigate:dev_a'));
    check('default path proceeds past INTAKE on an approved contract',
      rec.labels.includes('investigate:dev_a') && w.status !== 'escalated-intake', 'status=' + w.status);
    check('mode reports the entry path taken', out && out.mode === 'standup', 'mode=' + (out && out.mode));
  }

  // ═══ C. INTAKE GATES — it does not merely run ═══
  // The supervisor is forced to reject through BOTH the revision and the recheck. A stub that
  // always returns a well-formed contract proves the phase is CALLED, never that it GATES.
  section('C. INTAKE gates (supervisor rejects twice)');
  {
    const { rec, hooks } = host((label) => {
      if (label.startsWith('intake:pm')) return okContract;
      if (label.startsWith('sup:intake')) return { approve: false, note: 'still fuzzy', must_fix: ['name an outcome'] };
      if (label.startsWith('investigate:')) throw new Error('SENTINEL-SHOULD-NOT-INVESTIGATE');
      return undefined;
    });
    const out = await makeRunner(src, hooks)({ date: 'D', roster, task: TASK });
    const w = (out && out.worked && out.worked[0]) || {};
    check('a rejected contract gets exactly one revision', rec.labels.filter(l => l === 'intake:pm:revise').length === 1);
    check('and exactly one recheck', rec.labels.filter(l => l === 'sup:intake:recheck').length === 1);
    check('it does not loop', rec.labels.filter(l => l.startsWith('intake:pm')).length === 2);
    check('the task stops at escalated-intake', w.status === 'escalated-intake', 'status=' + w.status);
    check('it never reaches investigate/implement/commit',
      !rec.labels.some(l => /^(investigate|plan|work|commit):/.test(l)), 'labels=' + rec.labels.join(','));
    check('the run ends in the shape of a run that STOPPED, not one that finished',
      rec.logs.some(l => l.startsWith('TICK STOPPED')) && !rec.logs.some(l => l.startsWith('TICK DONE')),
      rec.logs.filter(l => /^TICK/.test(l)).join(' | '));
  }

  // ═══ C2. A gate that stops on ANY reserve is a gate that never opens ═══
  // Ported from the live system 2026-08-03, where this cost three consecutive runs and ~4.6M
  // tokens for ZERO lines of code while the supervisor's own verdicts read "about a page of
  // work, not a rewrite" and finally "DO NOT RE-PLAN. BUILD proceeds with the must_fix applied."
  // A conscientious reviewer sets approve=false the moment it sees anything improvable, so the
  // more diligent the reviewer the less can ever ship. approve=false must now answer whether it
  // is a REAL blocker; and the pair CRITIQUES rather than holding a veto.
  section('C2. approve=false answers "is this a real blocker?"');
  {
    // (a) reserve, but explicitly non-blocking -> the run continues, carrying the note
    const { rec, hooks } = host((label) => {
      if (label.startsWith('intake:pm')) return okContract;
      if (label.startsWith('sup:intake')) return { approve: false, blocking: false, note: 'tighten the wording', must_fix: ['name the metric'] };
      if (label.startsWith('investigate:')) throw new Error(STOP_AT_INVESTIGATE);
      return undefined;
    });
    // Assert on the RECORDED labels, not on a propagated throw: the engine records a failed
    // agent against the task rather than letting it escape, so a sentinel never reaches here.
    try { await makeRunner(src, hooks)({ date: 'D', roster, task: TASK }); } catch (e) { /* recorded */ }
    const reached = rec.labels.some(l => l.startsWith('investigate:'));
    check('a NON-blocking intake reserve lets the run continue', reached,
      reached ? '' : 'labels=' + rec.labels.join(','));
  }
  {
    // (b) a real blocker still stops
    const { hooks } = host((label) => {
      if (label.startsWith('intake:pm')) return okContract;
      if (label.startsWith('sup:intake')) return { approve: false, blocking: true, note: 'the ask contradicts itself' };
      if (label.startsWith('investigate:')) throw new Error('SENTINEL-SHOULD-NOT-INVESTIGATE');
      return undefined;
    });
    const out = await makeRunner(src, hooks)({ date: 'D', roster, task: TASK });
    const w = (out && out.worked && out.worked[0]) || {};
    check('a BLOCKING intake reserve still stops the task', w.status === 'escalated-intake', 'status=' + w.status);
  }
  {
    // (c) the field omitted -> strict. Silence is not consent to proceed.
    const { hooks } = host((label) => {
      if (label.startsWith('intake:pm')) return okContract;
      if (label.startsWith('sup:intake')) return { approve: false, note: 'did not say whether it blocks' };
      if (label.startsWith('investigate:')) throw new Error('SENTINEL-SHOULD-NOT-INVESTIGATE');
      return undefined;
    });
    const out = await makeRunner(src, hooks)({ date: 'D', roster, task: TASK });
    const w = (out && out.worked && out.worked[0]) || {};
    check('a MISSING blocking field still stops (strict by default)', w.status === 'escalated-intake', 'status=' + w.status);
  }
  {
    // (d) the pair critiques; it does not veto. Non-blocking required_changes reach IMPLEMENT.
    const { rec, hooks } = host((label) => {
      if (label.startsWith('intake:pm')) return okContract;
      if (label.startsWith('sup:intake')) return { approve: true, note: 'ok' };
      if (label.startsWith('investigate:')) return { findings: 'f', feasible: true, task_kind: 'brownfield' };
      if (label.startsWith('plan:') || label.startsWith('replan:')) return { steps: ['s'], tests: ['t'], risks: [] };
      if (label.startsWith('challenge:') || label.startsWith('rechallenge:')) {
        return { approved: false, blocking: false, critique: 'direction is right, tighten four things',
          required_changes: ['add a test for the empty case'] };
      }
      if (label.startsWith('work:')) throw new Error('SENTINEL-REACHED-IMPLEMENT');
      return undefined;
    });
    try { await makeRunner(src, hooks)({ date: 'D', roster, task: TASK }); } catch (e) { /* recorded */ }
    const reachedImpl = rec.labels.some(l => l.startsWith('work:'));
    check('a NON-blocking pair critique reaches IMPLEMENT instead of ending the task', reachedImpl,
      reachedImpl ? '' : 'labels=' + rec.labels.join(','));
  }
  {
    // (e) a plan that is genuinely wrong still ends the task
    const { hooks } = host((label) => {
      if (label.startsWith('intake:pm')) return okContract;
      if (label.startsWith('sup:intake')) return { approve: true, note: 'ok' };
      if (label.startsWith('investigate:')) return { findings: 'f', feasible: true, task_kind: 'brownfield' };
      if (label.startsWith('plan:') || label.startsWith('replan:')) return { steps: ['s'], tests: ['t'], risks: [] };
      if (label.startsWith('challenge:') || label.startsWith('rechallenge:')) {
        return { approved: false, blocking: true, critique: 'wrong layer entirely', required_changes: ['start over'] };
      }
      if (label.startsWith('work:')) throw new Error('SENTINEL-SHOULD-NOT-IMPLEMENT');
      return undefined;
    });
    const out = await makeRunner(src, hooks)({ date: 'D', roster, task: TASK });
    const w = (out && out.worked && out.worked[0]) || {};
    check('a BLOCKING pair critique still ends the task', w.status === 'escalated-plan-rejected', 'status=' + w.status);
  }

  // ═══ C3. Unparseable args THROW — they never degrade into a whole-roster standup ═══
  // The failure this prevents is invisible by construction: args.task vanishes, the single-task
  // dispatch silently becomes a roster-wide poll, DO_WORK goes false so no code can be produced,
  // and nothing errors. One unescaped quote inside a task string is enough to trigger it.
  section('C3. Bad args are loud, not quietly downgraded');
  {
    const { hooks } = host(() => undefined);
    const good = JSON.stringify({ date: 'D', roster, task: TASK });
    // A well-formed JSON string must still work — the harness really does deliver args this way.
    let goodErr = '';
    try { await makeRunner(src, hooks)(good); } catch (e) { goodErr = String(e.message); }
    check('a VALID JSON string still runs (backwards compatible)', !/failed to parse/.test(goodErr), goodErr.slice(0, 60));

    // The real shape of the incident: a raw double-quote inside a string value.
    const bad = '{"date":"D","task":{"task":"the owner accepted "this cost" explicitly","assignee":"dev_a"}}';
    let msg = '';
    try { await makeRunner(src, hooks)(bad); }
    catch (e) { msg = String(e.message); }
    check('an UNPARSEABLE args string throws', /failed to parse/.test(msg), msg.slice(0, 60));
    check('it never falls through into the standup shape', !/SENTINEL/.test(msg), msg.slice(0, 60));
    check('the error carries the offending text so it is fixable', /Near the error/.test(msg));
  }

  // ═══ C4. A roster that cannot dispatch anyone STOPS — it never produces an empty board ═══
  // There used to be a hardcoded EMBEDDED_ROSTER here, used whenever args.roster was absent or
  // unparseable, so a run handed no roster quietly worked a DIFFERENT team and reported green.
  // Deleting it exposed a second shape: `{}`, `{teams:[],staff:[]}` and an all-`active:false`
  // roster produced BYTE-IDENTICAL output, because the squad filter collapses "nobody active" and
  // "no squads" into one state. All three ran every phase including Arm — which writes the
  // supervisor-gate exemption into the user's project and switches the gate off for six hours —
  // and then printed `TICK DONE — 0 task(s)`, which reads like success.
  section('C4. An undispatchable roster stops before any phase');
  {
    const { hooks } = host(() => undefined);
    // `rec` is captured, not just the message. The first version of this group asserted that the
    // THROWN TEXT did not contain "team_run_active" — which is true whether Arm ran or not, and was
    // measured PASSing with both guards neutralised while `em:synthesize` actually executed. Arm
    // writing the exemption and switching the user's supervisor gate off for six hours is the most
    // consequential thing A2 exists to prevent, and it was the one check here with no teeth.
    // The host must ANSWER the roster-wide phases. With `host(() => undefined)` every agent throws
    // the sentinel, the run dies at `em:synthesize`, and Arm is unreachable whatever the guards do —
    // so the Arm assertion below passed identically with the guards on and off. Measured: standalone
    // (answering stub) both-guards-off DOES reach `arm:team_run_active`; inside the sentinel host it
    // never gets past Synthesize. A check whose subject the harness cannot reach is not a check.
    const runOf = async (args) => {
      const { rec: r, hooks: h } = host((label, assignee, extra) =>
        standupPhaseReply(label, assignee, extra));
      let msg = '';
      try { await makeRunner(src, h)(args); } catch (e) { msg = String(e.message); }
      return { msg, labels: r.labels, phases: r.phases };
    };
    const stopOf = async (args) => (await runOf(args)).msg;
    const noRoster   = await stopOf({ date: 'D', work: true, maxTasks: 1 });
    const emptyObj   = await stopOf({ date: 'D', roster: {}, work: true, maxTasks: 1 });
    const emptyTeams = await stopOf({ date: 'D', roster: { teams: [], staff: [] }, work: true, maxTasks: 1 });
    const allOff = JSON.parse(JSON.stringify(roster));
    allOff.teams.forEach(tm => (tm.developers || []).forEach(d => { d.active = false; }));
    const inactive   = await stopOf({ date: 'D', roster: allOff, work: true, maxTasks: 1 });

    check('a missing roster stops the run', /STOP — no usable roster/.test(noRoster), noRoster.slice(0, 70));
    check('and it never reaches an agent', !/SENTINEL/.test(noRoster));
    check('an empty roster object stops the run', /no ACTIVE developer/.test(emptyObj), emptyObj.slice(0, 70));
    check('an explicitly empty teams list stops the run', /no ACTIVE developer/.test(emptyTeams));
    check('an ALL-INACTIVE roster stops too (same state, not a different one)',
      /no ACTIVE developer/.test(inactive), inactive.slice(0, 70));
    check('the stop names the command that fixes it', /add-project/.test(emptyObj), emptyObj.slice(0, 90));
    const emptyRun = await runOf({ date: 'D', roster: {}, work: true, maxTasks: 1 });
    check('Arm never runs on a roster that cannot dispatch',
      !emptyRun.labels.includes('arm:team_run_active') && !emptyRun.phases.includes('Arm'),
      `labels=[${emptyRun.labels.join(',')}] phases=[${emptyRun.phases.join(',')}]`);
    check('and no agent runs at all on such a roster', emptyRun.labels.length === 0,
      `labels=[${emptyRun.labels.join(',')}]`);
  }

  // ═══ D. Routing is a gate, not a guess ═══
  // Asserting "zero agent calls" would only be satisfiable on the single-task path — the default
  // path has already spent agents on Standup/Design/Synthesize before the queue exists. The
  // invariant that holds on BOTH is: no Work-phase agent label is ever emitted.
  section('D. Routing stops loudly, on both entry paths');
  const workLabel = /^(intake|sup:intake|investigate|plan|challenge|work|testgate|review|pair-review|commit):/;
  // Each case carries the board item it would produce EXPLICITLY. (An earlier version derived the
  // board assignee from the patch and silently turned the folder case into the missing-assignee
  // case — the test harness committing, in miniature, the exact defect under test.)
  const routingCases = [
    ['unknown assignee', { assignee: 'dev_typo' }, { assignee: 'dev_typo' }, /is not on the roster/, /valid assignees:/],
    ['missing assignee', { _dropAssignee: true }, { assignee: undefined }, /names no assignee/, /valid assignees:/],
    ['folder the dev does not own', { folder: 'some/other/repo' }, { assignee: 'dev_a', folder: 'some/other/repo' }, /is not a directory/, /valid folders for dev_a:/],
  ];
  for (const [name, patch, boardItem, msgPat, logPat] of routingCases) {
    for (const path_ of ['work', 'standup']) {
      const { rec, hooks } = host((label) => {
        const s = standupPhaseReply(label, boardItem.assignee, boardItem.folder ? { folder: boardItem.folder } : null);
        if (s !== undefined) return s;
        return undefined;
      });
      const task = Object.assign({}, TASK, patch);
      delete task._dropAssignee;
      if (patch._dropAssignee) delete task.assignee;
      let err = null;
      try {
        await makeRunner(src, hooks)(path_ === 'work'
          ? { date: 'D', roster, task }
          : { date: 'D', roster, work: true, maxTasks: 1 });
      } catch (e) { err = e; }
      const label = `${name} [${path_}]`;
      if (!check(`${label} rejects`, !!err && msgPat.test(String(err.message)), err ? String(err.message).slice(0, 70) : '(no throw — the silent degradation is back)')) continue;
      check(`${label} spends no Work-phase agent`, !rec.labels.some(l => workLabel.test(l)), 'labels=' + rec.labels.join(','));
      check(`${label} PRINTS the enumeration before throwing`, rec.logs.some(l => logPat.test(l)),
        rec.logs.filter(l => /^(STOP|  valid|  fix)/.test(l)).join(' / ').slice(0, 90));
      check(`${label} ends in TICK STOPPED`, rec.logs.some(l => l.startsWith('TICK STOPPED')));
    }
  }

  // ═══ E. Pairing is a gate — a dev may not review its own plan ═══
  section('E. Pairing (no self-review, no silent substitute)');
  {
    const noPair = JSON.parse(JSON.stringify(roster));
    delete noPair.teams[0].developers[0].pair;
    for (const path_ of ['work', 'standup']) {
      const { rec, hooks } = host((label) => standupPhaseReply(label, 'dev_a'));
      let err = null;
      try {
        await makeRunner(src, hooks)(path_ === 'work'
          ? { date: 'D', roster: noPair, task: TASK }
          : { date: 'D', roster: noPair, work: true, maxTasks: 1 });
      } catch (e) { err = e; }
      check(`unpaired dev rejects [${path_}]`, !!err && /declares no pair/.test(String(err.message)),
        err ? String(err.message).slice(0, 70) : '(no throw — the `|| dev` self-review fallback is back)');
      check(`unpaired dev spends no Work-phase agent [${path_}]`, !rec.labels.some(l => workLabel.test(l)));
      check(`unpaired dev enumerates the squadmates [${path_}]`, rec.logs.some(l => /valid pairs for dev_a/.test(l)));
    }
    const badPair = JSON.parse(JSON.stringify(roster));
    badPair.teams[0].developers[0].pair = 'nobody_xyz';
    let err2 = null;
    try { await makeRunner(src, host(() => undefined).hooks)({ date: 'D', roster: badPair, task: TASK }); } catch (e) { err2 = e; }
    check('a pair id that resolves to nobody rejects', !!err2 && /is not another ACTIVE developer/.test(String(err2.message)),
      err2 ? String(err2.message).slice(0, 70) : '(no throw — an arbitrary squadmate was substituted)');
  }

  // ═══ F. task.folder actually moves the reviewer's deterministic diff target ═══
  // This is the trap that ran `git -C <wrong repo> diff`, saw nothing, and failed a correct change.
  // The allow-half needs a dev that owns TWO directories; the shipped roster deliberately declares
  // no `also_owns` (nothing to validate against would be manufactured), so it is injected here —
  // and said so out loud, or a passing test would read as proof of a field that does not ship.
  section('F. task.folder — ownership-validated, and it moves `git -C`');
  {
    const multi = JSON.parse(JSON.stringify(roster));
    multi.teams[0].developers[0].also_owns = ['other-lib'];
    const grabPrompts = (extra) => {
      const { rec, hooks } = host((label) => {
        if (label === 'intake:pm') return okContract;
        if (label === 'sup:intake') return { approve: true, note: 'ok' };
        if (label.startsWith('investigate:')) throw new Error(STOP_AT_INVESTIGATE);
        return undefined;
      });
      return makeRunner(src, hooks)({ date: 'D', roster: multi, task: Object.assign({}, TASK, extra) })
        .catch(() => null).then(() => rec);
    };
    const owned = await grabPrompts({ folder: 'other-lib' });
    const inv = (owned.prompts.find(p => p.label.startsWith('investigate:')) || { prompt: '' }).prompt;
    check('a declared also_owns folder is allowed', /other-lib/.test(inv), 'investigate folder mentioned: ' + /other-lib/.test(inv));
    check('the resolved folder appears in the deterministic git command',
      /git -C other-lib/.test(inv) || /folder other-lib/.test(inv), inv.slice(0, 0));
    const plain = await grabPrompts({});
    const invPlain = (plain.prompts.find(p => p.label.startsWith('investigate:')) || { prompt: '' }).prompt;
    check('omitting folder falls back to the owner folder unchanged', /demo-app/.test(invPlain) && !/other-lib/.test(invPlain));
  }

  // ═══ G. Observability comes from the DECLARED surface, not from web vocabulary ═══
  section('G. review_surface drives observability');
  {
    for (const t of roster.teams) {
      const s = t.review_surface;
      check(`squad ${t.id} declares a review_surface`, !!(s && s.kind), s ? 'kind=' + s.kind : '(absent)');
      check(`squad ${t.id} kind is one this engine knows`, !!s && /^(web|report|agent|api|cli|none)$/.test(s.kind || ''));
      if (s && s.kind !== 'none') {
        check(`squad ${t.id} inspect is non-blank`, !!String(s.inspect || '').trim(), (s.inspect || '').slice(0, 48));
      }
      check(`squad ${t.id} does not ship needs_declaration`, !('needs_declaration' in t));
    }
    check('the engine reads the surface, not a web-word regex',
      /surface\.kind === 'web'/.test(src) && !/OBSERVABLE_DQ = \/chart\|dashboard/.test(src));
    check('a non-web squad still draws a design lens',
      /DESIGN_LENS = surface\.kind !== 'none'/.test(src) && /DESIGN_LENS \? \[\{ kind: 'design-quality'/.test(src));
    // Undeclared must be LOUD, never silently treated as `none`.
    const undeclared = JSON.parse(JSON.stringify(roster));
    delete undeclared.teams[0].review_surface;
    const { rec, hooks } = host(() => undefined);
    let err = null;
    try { await makeRunner(src, hooks)({ date: 'D', roster: undeclared, task: TASK }); } catch (e) { err = e; }
    check('an UNDECLARED surface stops the run', !!err && /declares no review_surface/.test(String(err.message)),
      err ? String(err.message).slice(0, 70) : '(no throw — undeclared was treated as none)');
    check('and it names the valid kinds', rec.logs.some(l => /valid kinds:.*web.*none/.test(l)));
  }

  // ═══ H. The closing line can tell "did nothing" from "did work that failed" ═══
  // BEHAVIOURAL, not source-text. An earlier version of this section asserted that the string
  // `tally(_worked)` appeared in the source — and --self-test immediately proved that check
  // vacuous: a mutation that keeps the name and guts the body sailed through it. A check that
  // cannot fail is not a check, which is the same defect this whole judge is about.
  section('H. The closing line accounts for every record it counts');
  {
    // Two tasks, deliberately ending in DIFFERENT terminal states: one stopped at INTAKE (never
    // attempted) and one that ran and errored. The old line rendered both as "0 committed / 0 green
    // of 2 worked" — two opposite realities, byte-identical.
    const twoItemBoard = {
      summary: 's', team_health: 'green', blockers: [],
      todays_board: [
        { team: 'demo_squad', project: 'demo-app', task: 'first', priority: 'P0', effort: 'S', assignee: 'dev_a', autoworkable: true, acceptance: 'a', serves_goal: 'g' },
        { team: 'demo_squad', project: 'demo-app', task: 'second', priority: 'P1', effort: 'S', assignee: 'dev_b', autoworkable: true, acceptance: 'a', serves_goal: 'g' },
      ],
    };
    // Which task is in flight is tracked from the INTAKE prompt (the supervisor prompt carries only
    // the contract, so it cannot be discriminated on directly). Task "first" is rejected twice
    // (-> escalated-intake); task "second" is approved and its INVESTIGATE then throws
    // (-> work-error).
    let current = '';
    const { rec, hooks } = host((label, prompt) => {
      const s = standupPhaseReply(label, 'dev_a');
      if (label === 'em:synthesize') return twoItemBoard;
      if (s !== undefined) return s;
      if (label.startsWith('intake:pm')) {
        if (/"first"|TASK: first/.test(prompt)) current = 'first';
        else if (/"second"|TASK: second/.test(prompt)) current = 'second';
        return okContract;
      }
      if (label.startsWith('sup:intake')) return { approve: current !== 'first', note: 'n', must_fix: ['x'] };
      if (label.startsWith('investigate:')) throw new Error('boom');
      return undefined;
    });
    await makeRunner(src, hooks)({ date: 'D', roster, work: true, maxTasks: 2 });
    const closing = rec.logs.filter(l => /^TICK /.test(l)).join(' ');
    check('two DIFFERENT terminal states are not rendered identically',
      /escalated-intake/.test(closing) && /work-error/.test(closing), closing.slice(0, 120));
    check('the leading number is TASKS SEEN, so "did nothing" is distinguishable', /2 task\(s\)/.test(closing));
    check('`/` is no longer used to mean "and" on the closing line', !/committed \/ `/.test(src) && !/\d+ committed \/ \d+ green/.test(closing));
    check('a stopped run has its own closing form', /TICK STOPPED \$\{DATE\}/.test(src));
  }

  // ═══ J. The F rules that are marked [MACHINE] — decided HERE, by this script ═══
  // DESIGN_RULEBOOK's [MACHINE] label is a promise that an exit code decides the rule. The DOM
  // judge (control/verify_design_quality.js) cannot decide F: it probes a page, and F governs a
  // transcript. Shipping F-01/F-02/F-06 as [MACHINE] with no machine behind them would be the
  // advertised-but-unimplemented gate this whole judge exists to retire, committed inside the
  // rulebook that governs it. So they are adjudicated here.
  section('J. DESIGN_RULEBOOK F rules ([MACHINE] ones are decided by this script)');
  {
    const EMOJI = /[\u{1F300}-\u{1FAFF}\u{2700}-\u{27BF}\u{2B00}-\u{2BFF}\u{FE0F}\u{2705}\u{274C}]/u;
    const judgeSrc = fs.readFileSync(__filename, 'utf8');
    // F-01 — status must survive glyph loss. Applied to this judge's OWN output first: the named
    // port source closed with ✅/❌, which is a status system one dropped glyph wide.
    const emittedStrings = (judgeSrc.match(/console\.(?:log|error)\(([^\n]*)/g) || []).join('\n');
    check('F-01 the judge carries no emoji in anything it prints', !EMOJI.test(emittedStrings),
      (emittedStrings.match(EMOJI) || []).join(''));
    check('F-01 the engine carries no emoji in anything it logs',
      !EMOJI.test((src.match(/log\(`[^`]*`/g) || []).join('\n')));
    check('F-01 every verdict is carried by a WORD', /PASS|FAIL/.test('PASS') && /→ \$\{ok \? 'PASS' : 'FAIL'\}/.test(judgeSrc));
    // F-02 / F-03 are adjudicated BEHAVIOURALLY in section H (running the engine and reading the
    // line it actually prints), not by source text — see the note there for why.
    // F-06 — one name per concept; never print a count the code refuses to hardcode.
    const printed = ['skills/standup/SKILL.md', 'skills/work/SKILL.md', 'skills/team/SKILL.md',
      'skills/eval/SKILL.md', '.claude/commands/standup.md', 'README.md'];
    for (const rel of printed) {
      const p = path.join(REPO, rel);
      if (!fs.existsSync(p)) { check(`F-06 ${rel} exists to be checked`, false, '(missing)'); continue; }
      const txt = fs.readFileSync(p, 'utf8');
      // A prose mention explaining that the claim USED to be made is allowed; a live claim is not.
      const live = txt.split('\n').filter(l => /\d-lens/.test(l) && !/used to|no longer|it said/.test(l));
      check(`F-06 ${rel} prints no hardcoded lens count`, live.length === 0, live.join(' ').slice(0, 70));
    }
    check('F-06 the engine meta prints no hardcoded lens count',
      !/\d-lens/.test((src.match(/description: '[^']*'/) || [''])[0]));
    check('F-06 the canonical SDLC list exists in the roster and starts at INTAKE',
      Array.isArray(roster.manager.policy.sdlc_pipeline) && /^0 INTAKE/.test(roster.manager.policy.sdlc_pipeline[0]),
      String(roster.manager.policy.sdlc_pipeline[0]).slice(0, 40));
    // F-07 — an error names the valid set. Every stopTick call site must pass an enumeration that
    // is DERIVED, not literal.
    const stopCalls = src.match(/stopTick\(/g) || [];
    check('F-07 every stop is emitted through the one helper', stopCalls.length >= 5, 'stopTick call sites=' + stopCalls.length);
    check('F-07 the helper prints the valid set before it throws',
      /log\(`  valid \$\{validNoun\}: /.test(src) && src.indexOf('log(`  valid ${validNoun}') < src.indexOf('throw e'));
  }

  // ═══ I. Necessary but insufficient: the file the host must be able to LOAD ═══
  // ═══ H2. The supervisor-gate exemption is armed BY THE ENGINE ═══
  // The subagent-cwd problem (anthropics/claude-code#12748): the Task/agent tool has no `cwd`
  // parameter, so every dispatched dev agent inherits the EM's cwd — and supervisor_gate.py
  // identifies the EM by cwd. Result: the dev agents are classified as the EM and their writes are
  // hard-blocked. The run then completes with an EMPTY diff and reports review-failed, which reads
  // as a code-quality problem and sends you looking in the wrong place.
  //
  // The exemption flag and the gate's reading of it both already existed; nothing ever SET it. A
  // gate documented in three places and armed by none is the false-promise defect this repo keeps
  // finding elsewhere. These cases hold the wiring: it must be armed, armed FIRST, and a failed
  // arm must STOP the run rather than let it burn a full pipeline on a guaranteed empty diff.
  section('H2. The team-run exemption is armed by the engine, before any dev agent');
  {
    const { rec, hooks } = host((label) => {
      if (label === 'intake:pm') return okContract;
      if (label === 'sup:intake') return { approve: true, note: 'ok' };
      if (label.startsWith('investigate:')) throw new Error(STOP_AT_INVESTIGATE);
      return undefined;
    });
    await makeRunner(src, hooks)({ date: 'D', roster, task: TASK });
    const iArm = rec.labels.indexOf('arm:team_run_active');
    const iDev = rec.labels.findIndex(l => /^(investigate|implement|plan):/.test(l));
    check('a code-writing run arms the exemption itself (not "the launcher remembers")', iArm >= 0,
      'labels=' + rec.labels.slice(0, 4).join(','));
    check('it is armed BEFORE the first dev agent (arming after is not arming)',
      iArm >= 0 && iDev >= 0 && iArm < iDev, `arm@${iArm} dev@${iDev}`);
    check('the run tears the exemption down at the end', rec.labels.includes('disarm:team_run_active'),
      'labels=' + rec.labels.join(','));
  }
  {
    // A failed arm must be fatal. Anything softer reproduces the exact failure this guards against,
    // and disguises it as a review verdict.
    for (const [name, verdict] of [
      ['a failed arm STOPS the run (flag_present:false)', { flag_present: false, detail: 'could not create it' }],
      ['a null arm result STOPS the run', null],
    ]) {
      const { rec, hooks } = host((label) => {
        if (label === 'arm:team_run_active') return verdict;
        if (label === 'intake:pm') return okContract;
        if (label === 'sup:intake') return { approve: true, note: 'ok' };
        return undefined;
      });
      let err = null;
      try { await makeRunner(src, hooks)({ date: 'D', roster, task: TASK }); } catch (e) { err = e; }
      const reachedDev = rec.labels.some(l => /^(investigate|implement|plan):/.test(l));
      check(name, !!err && /ARM failed/.test(String(err.message)) && !reachedDev,
        err ? (reachedDev ? 'threw, but a dev agent already ran' : String(err.message).slice(0, 44))
            : 'did not throw — the run would burn a full pipeline on a guaranteed empty diff');
    }
  }

  // ═══ C5. Arm identity — the half no self-check can cover ═══
  // The Arm agent verifies its own work by asking the flag helper it just used, and a NEIGHBOURING
  // install's helper answers `team_run_active PRESENT` perfectly truthfully — about the wrong repo.
  // So the only thing that can catch a mis-armed run is a fact the writer never had: the roster
  // THIS run was handed. Until this group existed, every case in this file stubbed Arm without ids,
  // so the `verified` and the throw branches were never executed anywhere in the repo — the
  // load-bearing half of the fix had zero executable coverage while five commands reported green.
  section('C5. Arm asserts WHICH install it armed');
  {
    const expectTeams = (roster.teams || []).map(x => x.id).filter(Boolean).sort().join(',');
    const expectDevs = (roster.teams || [])
      .flatMap(x => x.developers || []).map(d => d.id).filter(Boolean).sort().join(',');

    const armWith = async (extra) => {
      const { rec, hooks } = host((label) => {
        if (label === 'arm:team_run_active') {
          return Object.assign({ flag_present: true, set_by_me: true, detail: 'STUBBED' }, extra);
        }
        if (label === 'intake:pm') return okContract;
        if (label === 'sup:intake') return { approve: true, note: 'ok' };
        if (label.startsWith('investigate:')) throw new Error(STOP_AT_INVESTIGATE);
        return undefined;
      });
      let err = null;
      try { await makeRunner(src, hooks)({ date: 'D', roster, task: TASK }); } catch (e) { err = e; }
      return { err, reachedDev: rec.labels.some(l => /^(investigate|implement|plan):/.test(l)), rec };
    };

    // The neighbour: a real install, correctly armed, wrong tree.
    const wrong = await armWith({ resolved_root: '/somewhere/else', flag_realpath: '/somewhere/else/standup/control/team_run_active',
                                  team_ids: 'neighbour', dev_ids: 'nb_a,nb_b' });
    check('an arm on a DIFFERENT install throws',
      !!wrong.err && /ARM armed the WRONG install/.test(String(wrong.err.message)),
      wrong.err ? String(wrong.err.message).slice(0, 52) : 'did not throw');
    check('and no dev agent runs after a mis-armed exemption', !wrong.reachedDev,
      'the gate would be off THERE and on HERE — every write blocked, reported as review-failed');
    check('the throw names both rosters so it is diagnosable',
      !!wrong.err && /neighbour/.test(String(wrong.err.message)) && new RegExp(expectTeams.split(',')[0]).test(String(wrong.err.message)));

    // The matching case — this is what executes the `verified` branch.
    const right = await armWith({ resolved_root: '/here', team_ids: expectTeams, dev_ids: expectDevs });
    check('a MATCHING roster passes identity and the run proceeds',
      !right.err || !/ARM armed the WRONG install/.test(String(right.err.message)),
      right.err ? String(right.err.message).slice(0, 40) : 'no throw');
    check('and it reaches a dev agent', right.reachedDev);

    // No ids reported: `unverified`, and deliberately NOT fatal — an older arm step that cannot
    // report ids must not brick every run.
    const silent = await armWith({});
    check('an arm that reports no ids is unverified, not fatal',
      !silent.err || !/ARM armed the WRONG install/.test(String(silent.err.message)));
    check('and the run still proceeds on it', silent.reachedDev);
  }

  section('I. The engine still loads in the host');
  {
    let loadErr = null;
    try { makeRunner(src, host(() => undefined).hooks); } catch (e) { loadErr = e; }
    check('engine source is loadable by `new Function` (parse != loadable)', !loadErr,
      loadErr ? String(loadErr.message).slice(0, 70) : '');
  }
  return { fails, cases: new Set(cases), failed: new Set(failed) };
}

// ---------- E-03: the negative fixtures ----------
// Each mutation is a NAMED reversal of one thing this port added, applied to the engine source in
// memory. It must drive at least one named case RED. Anchors are stable substrings, never line
// numbers — most of these target lines this port rewrote.
const FIXTURES = [
  { name: 'arm-failure-non-fatal',
    why: 'the exemption arm still runs but a failure no longer stops the run — which is exactly the original defect: a full gated pipeline burned on a guaranteed empty diff, reported as review-failed',
    must_red: ['a failed arm STOPS the run (flag_present:false)', 'a null arm result STOPS the run'],
    from: `  if (!r || r.flag_present !== true) {`,
    to:   `  if (false) {` },
  { name: 'arm-step-removed',
    why: 'the engine no longer arms the supervisor-gate exemption, so it is back to depending on a launcher remembering — and every dispatched dev agent write is hard-blocked',
    must_red: ['a code-writing run arms the exemption itself (not "the launcher remembers")',
               'it is armed BEFORE the first dev agent (arming after is not arming)'],
    from: `  phase('Arm')\n  await armTeamRunExemption()`,
    to:   `  const _unusedArm = async () => armTeamRunExemption()` },
  { name: 'intake-step-removed',
    why: 'INTAKE deleted from the loop — the phase that asks whether we are building the right thing',
    // must_red names the cases this fixture is REQUIRED to break. Counting reds is not enough: a
    // fixture that reddens four incidental cases while leaving the one it exists for green is a
    // tooth pointing the wrong way. In particular INTAKE must break on BOTH entry paths — the
    // forbidden outcome for this work is a new INTAKE-bearing path while the default path skips it.
    must_red: ['single-task path reaches INTAKE', 'default path reaches INTAKE too'],
    from: `    let contract = await agent(`,
    to:   `    let contract = { goal: 'skipped', acceptance: [], verification: 'none', priority: 'P1' }; const _unused = async () => agent(` },
  { name: 'supervisor-rejection-non-blocking',
    why: 'INTAKE still runs but its rejection no longer stops the task — a phase that always passes is decoration',
    must_red: ['the task stops at escalated-intake', 'it never reaches investigate/implement/commit'],
    from: `      if (!intakeOk || isBlocking(intakeOk)) {\n        record.status = 'escalated-intake'`,
    to:   `      if (false) {\n        record.status = 'escalated-intake'` },
  { name: 'intake-escape-valve-removed',
    why: 'INTAKE goes back to stopping on ANY reserve, so a reviewer doing its job well ("one amendment away") kills the run as hard as a fatal objection',
    must_red: ['a NON-blocking intake reserve lets the run continue'],
    from: `      if (!intakeOk || isBlocking(intakeOk)) {`,
    to:   `      if (!intakeOk || intakeOk.approve !== true) {` },
  { name: 'pair-challenge-veto-restored',
    why: 'the pair holds a veto again — a critique that says "direction is right, fix four things" ends the task, and its required_changes never reach the implementer',
    must_red: ['a NON-blocking pair critique reaches IMPLEMENT instead of ending the task'],
    from: `    if (challengeBlocks(challenge)) { record.status = 'escalated-plan-rejected'`,
    to:   `    if (!challenge || !challenge.approved) { record.status = 'escalated-plan-rejected'` },
  { name: 'args-parse-failure-silently-nulled',
    why: 'unparseable args go back to A = null — one task silently becomes a whole-roster poll that cannot produce code, and nothing errors',
    // 'it never falls through into the standup shape' used to belong here and no longer can: with
    // the embedded-roster fallback deleted, a nulled `args` reaches the roster guard and stops
    // there instead. The invariant still holds — it is now defended twice — so asserting it as a
    // tooth of THIS mutation would be asserting something the other gate guarantees. Group C4
    // proves the second gate directly.
    must_red: ['an UNPARSEABLE args string throws'],
    from: `  try { A = JSON.parse(A) }\n  catch (e) {`,
    to:   `  try { A = JSON.parse(A) }\n  catch (e) { A = null } if (false) {` },
  // TWO fixtures, one per branch, deliberately. A single mutation of `if (ROSTER_ERROR)` reddened
  // only the missing-roster case: the empty and all-inactive shapes are caught by the OTHER branch,
  // which was still live. That is the same "a branch with no independent covering case" defect the
  // eval judge shipped once, so each guard gets its own tooth.
  // The reviewer's Mutation A. It survived FIVE green commands before this fixture existed: the
  // string 'ARM armed the WRONG install' stayed in the file, so a grep-based source check passed
  // while the branch became unreachable — exactly what this judge's own header forbids.
  { name: 'arm-identity-assertion-neutralized',
    why: 'the engine stops comparing the armed tree against the roster it was handed, so a run that armed a NEIGHBOURING install reports success and every dev write is blocked here',
    must_red: ['an arm on a DIFFERENT install throws',
               'and no dev agent runs after a mis-armed exemption'],
    from: `} else if (armTeams !== ARM_EXPECT_TEAMS || armDevs !== ARM_EXPECT_DEVS) {`,
    to:   `} else if (false) {` },
  // BLOCK-3's fixture: the Arm check in C4 used to assert on thrown TEXT and passed with BOTH
  // guards off while em:synthesize actually ran. Now it reads the recorded labels/phases.
  { name: 'both-roster-guards-neutralized',
    why: 'an undispatchable roster runs phases again — including Arm, which writes the exemption into the user project and switches their supervisor gate off for six hours — then prints TICK DONE with 0 tasks',
    must_red: ['Arm never runs on a roster that cannot dispatch',
               'and no agent runs at all on such a roster',
               'a missing roster stops the run'],
    from: `if (ROSTER_ERROR) {`,
    to:   `if (false) {`,
    from2: `if (!DEVS.length) {`,
    to2:   `if (false) {` },
  { name: 'roster-missing-guard-neutralized',
    why: 'a run handed no roster proceeds again — it used to silently work a hardcoded team instead of standup/team.json and report green',
    must_red: ['a missing roster stops the run'],
    from: `if (ROSTER_ERROR) {`,
    to:   `if (false) {` },
  { name: 'empty-roster-guard-neutralized',
    why: 'an undispatchable roster runs every phase again — including Arm, which writes the exemption flag into the user project and switches the supervisor gate off for six hours — then prints TICK DONE with 0 tasks, which reads like success',
    must_red: ['an empty roster object stops the run',
               'an ALL-INACTIVE roster stops too (same state, not a different one)'],
    from: `if (!DEVS.length) {`,
    to:   `if (false) {` },
  { name: 'squad-inspect-blanked',
    why: 'a shipped squad declares a surface with no runnable inspect — the false promise one layer down',
    roster: (r) => { r.teams[0].review_surface.inspect = ''; return r; } },
  { name: 'observability-back-to-web-word-regex',
    why: 'observability inferred from role/task vocabulary again, so a non-web squad is invisible',
    must_red: ['the engine reads the surface, not a web-word regex', 'a non-web squad still draws a design lens'],
    from: `    const VISUAL_DQ = surface.kind === 'web' || _touchedFrontend\n    const DESIGN_LENS = surface.kind !== 'none' || _touchedFrontend`,
    to:   `    const VISUAL_DQ = /chart|dashboard|render|panel|\\bUI\\b/i.test(\`\${dev.role || ''} \${t.task || ''}\`) || _touchedFrontend\n    const DESIGN_LENS = VISUAL_DQ` },
  { name: 'unknown-assignee-silently-skipped',
    why: 'the mistyped assignee returns to `status:"skipped"` + continue — a clean tick that did nothing',
    // Both entry paths again: :683 lived in the standup loop, which is the path the acceptance names.
    must_red: ['unknown assignee [work] rejects', 'unknown assignee [standup] rejects'],
    from: `  const dev = t.assignee ? DEVS.find(d => d.id === t.assignee) : null\n  if (!dev) {`,
    to:   `  const dev = t.assignee ? DEVS.find(d => d.id === t.assignee) : DEVS[0]\n  if (false) {` },
  { name: 'self-review-fallback-restored',
    why: 'the `|| dev` fallback returns, so a developer critiques its own plan and reviews its own diff',
    must_red: ['unpaired dev rejects [work]', 'unpaired dev rejects [standup]'],
    from: `  if (!dev.pair) {\n    stopTick(`,
    to:   `  if (false) {\n    stopTick(` },
  { name: 'reviewer-folder-hardcoded-to-owner',
    why: 'task.folder stops moving the deterministic diff target — the empty-diff review-failed trap',
    must_red: ['a declared also_owns folder is allowed'],
    from: `  const folder = t.folder || dev.folder || team.folder || '.'`,
    to:   `  const folder = dev.folder || team.folder || '.'` },
  // The last two guard the design rules this port added. They matter because the two throws above
  // are only half the fix: an error that stops the run but reports it in the shape of a normal
  // finish, or a closing line that cannot express the stop, leaves the user reading a green tick.
  { name: 'closing-line-collapses-outcomes',
    why: 'the closing line goes back to a denominator its numerators cannot express, so "did nothing" and "did work that failed" render identically (F-02)',
    must_red: ['two DIFFERENT terminal states are not rendered identically'],
    from: `const tally = (records) => {`,
    to:   `const tally = (records) => { return String(records.length) } // eslint-disable-line\nconst _tally_disabled = (records) => {` },
  { name: 'stop-has-no-closing-form',
    why: 'a stopped run ends in the shape of a run that finished — the stop scrolls past as one more log line (F-05)',
    must_red: ['unknown assignee [work] ends in TICK STOPPED', 'unknown assignee [standup] ends in TICK STOPPED'],
    from: `  log(\`TICK STOPPED \${DATE} — \${what}\`)`,
    to:   `  log(\`  note: ${'${what}'}\`)` },
];

async function selfTest() {
  console.log('SELF-TEST — each fixture is a named reversal of one thing this judge is supposed to catch.\n');
  const base = await runCases(engineSrc);
  if (base.fails) {
    console.error(`\nSELF-TEST CANNOT RUN (exit 3) — the UNMUTATED engine already fails ${base.fails} case(s).`);
    console.error('A judge cannot prove it catches breakage while the baseline is broken. Fix the engine first.');
    return 3;
  }
  console.log(`\nbaseline → PASS (${base.cases.size} case(s) green on the unmutated engine)\n`);

  let broken = 0;
  for (const f of FIXTURES) {
    let src = engineSrc;
    let rosterPatch = null;
    if (f.from) {
      if (!src.includes(f.from)) {
        console.error(`  ${f.name} → ERROR  mutation no longer matches source; anchor not found:`);
        console.error(`      ${JSON.stringify(f.from.slice(0, 90))}`);
        console.error('      A mutation that silently no-ops reads as a pass. Re-anchor it or delete the fixture.');
        broken++;
        continue;
      }
      src = src.replace(f.from, f.to);
    }
    // A SECOND anchor, for defences that come in pairs. Neutralising one branch of a two-branch
    // guard leaves the other catching everything, the fixture reddens nothing it claims, and the
    // pair reads as covered — this project has shipped that mistake four times. `from2` makes
    // "turn the whole defence off" expressible instead of approximated.
    if (f.from2) {
      if (!src.includes(f.from2)) {
        console.error(`  ${f.name} → ERROR  second mutation anchor not found:`);
        console.error(`      ${JSON.stringify(f.from2.slice(0, 90))}`);
        broken++;
        continue;
      }
      src = src.replace(f.from2, f.to2);
    }
    if (f.roster) {
      // A roster-shaped fixture mutates the file on disk's parsed copy for the duration of one run.
      rosterPatch = f.roster(JSON.parse(JSON.stringify(roster)));
    }
    const before = console.log;
    console.log = () => {};                 // silence the case-by-case output of the mutated run
    let res;
    try {
      if (rosterPatch) {
        // Exercise the surface path directly: a blank inspect must stop the run.
        const { hooks } = host(() => undefined);
        let err = null;
        try { await makeRunner(src, hooks)({ date: 'D', roster: rosterPatch, task: TASK }); } catch (e) { err = e; }
        const caught = !!(err && /no inspect command/.test(String(err.message)));
        res = { fails: caught ? 1 : 0, failed: new Set(caught ? ['a blanked inspect stops the run'] : []) };
      } else {
        res = await runCases(src);
      }
    } finally { console.log = before; }
    const missed = (f.must_red || []).filter(n => !(res.failed || new Set()).has(n));
    if (res.fails > 0 && !missed.length) {
      const names = [...(res.failed || [])].slice(0, 3).join('; ');
      console.log(`  ${f.name} → correctly went RED (${res.fails} case(s): ${names}${res.fails > 3 ? ' …' : ''})  — ${f.why}`);
    } else if (res.fails > 0 && missed.length) {
      console.error(`  ${f.name} → ERROR  it reddened ${res.fails} case(s) but NOT the one(s) it exists for -> ${missed.join('; ')}`);
      console.error('      A tooth pointing the wrong way still bites something; that is not the same as guarding what it claims to.');
      broken++;
    } else {
      console.error(`  ${f.name} → ERROR  the mutation applied but NOTHING went red  — ${f.why}`);
      console.error('      A tooth that does not bite is not a tooth; the case for this fixture is missing or vacuous.');
      broken++;
    }
  }

  console.log('');
  if (broken) {
    console.error(`SELF-TEST FAIL: ${broken} of ${FIXTURES.length} fixture(s) did not prove a tooth -> see the ERROR lines above`);
    console.error('DESIGN_RULEBOOK E-03: a judge that cannot catch breakage is not a judge. Its verdicts mean nothing until this is green.');
    return 3;
  }
  console.log(`SELF-TEST PASS — all ${FIXTURES.length} fixtures drove a named case RED. The judge can fail, so its verdicts mean something.`);
  console.log('Reminder: this proves the judge has teeth, not that the pipeline is correct — a green run still needs a human reading it.');
  return 0;
}

(async () => {
  const argv = process.argv.slice(2);
  const unknown = argv.filter(a => a !== '--self-test');
  if (unknown.length) {
    console.error('usage: test_sdlc_routing.js [--self-test]');
    console.error('  (no arguments)  run the cases against standup/standup.workflow.js');
    console.error('  --self-test     prove the cases can FAIL, by mutating the engine source in memory');
    console.error('  There is no target parameter on purpose: this judge adjudicates THIS repo\'s engine,');
    console.error('  and a configurable target is a target that can be pointed somewhere harmless.');
    process.exit(64);
  }
  if (argv.includes('--self-test')) process.exit(await selfTest());

  console.log(`SDLC ROUTING JUDGE — ${path.relative(REPO, ENGINE)}`);
  const res = await runCases(engineSrc);
  console.log('');
  if (res.fails) {
    console.error(`SDLC-ROUTING FAIL: ${res.fails} of ${res.cases.size} case(s) -> see the FAIL lines above`);
    console.error('Each case is one way a team gets aimed at the wrong thing and reports success anyway. Fix the engine, not the case.');
    process.exit(1);
  }
  console.log(`SDLC-ROUTING PASS — all ${res.cases.size} cases green: the pipeline gates INTAKE on both entry paths, and refuses to run on an assignee, pair, folder or surface nobody declared.`);
  console.log('Run --self-test to prove these cases can fail; a case that cannot fail is not a check (E-03).');
  process.exit(0);
})();
