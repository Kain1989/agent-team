#!/usr/bin/env node
/* ============================================================================
 * FRONTEND CONTRACT TEST — the front↔back severity seam (previously 0 coverage)
 *
 * WHY: the backend + README emit awaiting_kain[].severity = P0/P1/P2/P3. The UI
 * (static/app.js) once piped that through normHealth() (which only knows
 * red/yellow/green/grey), so P-labels collapsed to "grey": grey left-borders,
 * the literal word "grey" as the label, and a no-op sort (sevRank["P0"] was
 * undefined). This harness feeds a REALISTIC /api/status payload (P0-style
 * severities, a stale dev, comms "stalled") through the REAL render path in
 * app.js and asserts the severity color, label, and sort order.
 *
 *   - It FAILS against the pre-fix app.js (grey label/border + broken sort).
 *   - It PASSES against the fixed app.js (P-label shown, P0=red…, P0<P1<P2<P3).
 *
 * No jsdom / npm deps: a tiny DOM + fetch stub runs the IIFE in a vm context,
 * then we inspect the nodes app.js produced. Pure Node (>=18 for global fetch
 * shape is irrelevant — we stub fetch ourselves).
 *
 * RUN (from anywhere — every path below is resolved off __dirname):
 *   node standup/portal/tests/contract.frontend.test.js
 * Exit code 0 = pass, 1 = fail, 2 = the harness itself broke. Each assertion prints a line.
 *
 * DEPENDENCIES: none. Node stdlib only (fs/path/vm) — no npm install, no jsdom, no
 * package.json. That is deliberate: this is the ONLY test that executes app.js, and a
 * harness that needs a toolchain is a harness that gets skipped.
 *
 * WIRED IN: README's Tests block + `.github/workflows/ci.yml`. It was neither for its whole
 * life — referenced by nothing but its own header — while three source-text judges were
 * added around it in `tests/test_static_mock.py`. Three checks reading app.js as TEXT and
 * the one that RUNS it connected to nothing is this repo's recurring shape: the apparatus
 * exists, and nothing is aimed through it.
 *
 * SELF-TEST: there is no `--self-test` flag; section 8 is the equivalent, in-band. It feeds
 * the same payload through the PRE-FIX producer shape and requires the card to break. A
 * judge that has not been shown to fail is not a judge (`E-03`) — this one shows it on
 * every run rather than behind a flag.
 * ========================================================================== */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const APP_JS = path.join(__dirname, "..", "static", "app.js");

// ---------------------------------------------------------------------------
// Minimal DOM stub — only what static/app.js touches (see grep of DOM APIs).
// ---------------------------------------------------------------------------
function makeNode(tag) {
  const node = {
    tagName: String(tag || "").toUpperCase(),
    children: [],
    dataset: {},
    style: {},
    _text: "",
    className: "",
    tabIndex: 0,
    title: "",
    hidden: false,
    disabled: false,
    classList: { add() {}, remove() {}, toggle() {} },
    attributes: {},
    appendChild(c) { this.children.push(c); c.parentNode = this; return c; },
    addEventListener() {},
    setAttribute(k, v) { this.attributes[k] = String(v == null ? "" : v); },
    getAttribute(k) { return this.attributes[k]; },
    removeAttribute(k) { delete this.attributes[k]; },
    get textContent() { return this._text; },
    set textContent(v) { this._text = String(v == null ? "" : v); this.children = []; },
    set innerHTML(v) { if (v === "" || v == null) { this.children = []; this._text = ""; } },
    get innerHTML() { return ""; },
  };
  return node;
}

function makeDom() {
  const byId = {};
  // ids referenced by app.js render() path (redesigned IA:
  // verdict strip → dynamic hero → decisions board → squads → staff → footer)
  const ids = [
    "conn-banner", "conn-foot", "degraded-banner", "freshness-age",
    "verdict", "verdict-headline", "verdict-move", "verdict-counts", "verdict-chips",
    "hero",
    "board", "board-lanes",
    "squads-grid", "staff-list",
    "bench", "bench-label", "bench-list", "bench-toggle",
    "lasttick-line",
    "landing-label", "landing-list",
    "actions-lock", "dual-runner-banner", "actions-context",
    "act-run-standup", "act-pm-review",
    "action-live", "action-live-dot", "action-live-title", "action-live-detail",
    "action-live-result", "action-live-log", "action-live-retry", "action-live-dismiss",
    "action-dialog-backdrop", "action-dialog", "dialog-kicker", "dialog-title",
    "dialog-body", "dialog-cancel", "dialog-confirm",
    "refresh-btn",
  ];
  ids.forEach((id) => { byId[id] = makeNode("div"); byId[id].id = id; });

  const body = makeNode("body");
  const documentStub = {
    body,
    readyState: "complete",
    getElementById: (id) => byId[id] || (byId[id] = makeNode("div")),
    createElement: (tag) => makeNode(tag),
    createTextNode: (t) => ({ nodeType: 3, _text: String(t == null ? "" : t), get textContent() { return this._text; } }),
    addEventListener() {},
  };
  return { documentStub, byId };
}

// text of a node, recursively (covers createTextNode children, e.g. health pill)
function nodeText(n) {
  if (!n) return "";
  if (n.nodeType === 3) return n._text || "";
  let t = n._text || "";
  for (const c of n.children || []) t += nodeText(c);
  return t;
}

// ---------------------------------------------------------------------------
// Realistic /api/status payload — exercises the contract that broke.
//   - severities are P0/P1/P2/P3 (the canonical backend vocab), NOT r/y/g
//   - mixes dated + undated to exercise the dated-first + severity sort
//   - one dev with stale:true (backend "can't read this dev")
//   - comms.state "stalled" (the worst, ≥48h)
// ---------------------------------------------------------------------------
function realisticStatus() {
  return {
    org: { health: "yellow", counts: { red: 1, yellow: 3, reported: 12 } },
    runner: { state: "alive", last_tick: { id: "wf_x", name: "EVENING", at: null },
              next_tick: { name: "NIGHT", at: null, in_seconds: 3000 }, heartbeat_age_s: 3 },
    awaiting_kain: [
      // intentionally OUT of priority order so the sort has work to do:
      { title: "low prio undated", severity: "P3", days_remaining: null, leverage: null },
      { title: "P0 undated critical", severity: "P0", days_remaining: null, leverage: "unblocks lanes" },
      { title: "P1 undated", severity: "P1", days_remaining: null, leverage: null },
      { title: "dated 11d (PAT)", severity: "P0", days_remaining: 11, leverage: null },
      { title: "dated 3d soon", severity: "P1", days_remaining: 3, leverage: null },
      { title: "P2 undated", severity: "P2", days_remaining: null, leverage: null },
    ],
    // Neutral placeholder squad. This used to name `demo_squad`/`dev_a`/`dev_b` — the
    // bundled sample deleted in 0.5.0 — so the one harness that runs app.js described a
    // roster that no longer ships. Cosmetic (the render paths under test never read these
    // ids), but the same rename the embedded mock already took in db5ba38; the names here
    // now match it so both fixtures speak one vocabulary.
    squads: [
      { id: "your_squad", name: "Your Dev Squad", health: "yellow", devs: [
        { id: "dev_1", role: "Builder", health: "green", current_task: "slugify helper" },
        { id: "dev_2", role: "Reviewer & Tests", health: "yellow", current_task: "unpushed branch", stale: true },
      ] },
    ],
    // comms_triage MUST be present: renderCommsStreams() only runs inside the
    // comms_triage staff card (app.js), so without it the streams never render.
    staff: [
      { id: "comms_triage", role: "Comms Triage — Local Intake & Routing (message/email/meeting)" },
    ],
    last_tick: { id: "wf_x", name: "EVENING", at: null },
    // CANONICAL backend comms shape: each stream keyed on `kind` (parsers/comms.py
    // emits "kind", NEVER "id"). Counts 43/10/8 mirror tests/test_api.py. Real
    // (non-"missing") states on message/email so the dataset.state assertion below
    // proves the exact state token survives the index fix.
    comms: {
      last_pull_at: null, stale_hours: 51, state: "stalled",
      streams: [
        { kind: "message", label: "Messages", source: "teams_activity.json", count: 43, state: "stale",   stale_hours: 51, signed_in: true,  last_pull_at: null },
        { kind: "email",   label: "Email",    source: "outlook.json",        count: 10, state: "stalled", stale_hours: 51, signed_in: true,  last_pull_at: null },
        { kind: "meeting", label: "Meetings", source: "outlook.json",        count: 8,  state: "missing", stale_hours: null, signed_in: false, last_pull_at: null },
      ],
    },
    landing_queue: [],
    updated_at: new Date().toISOString(),
    sources: {},
  };
}

// ---------------------------------------------------------------------------
// Load app.js into a vm sandbox with our stubs, then drive its real poll/render.
// ---------------------------------------------------------------------------
function loadApp(status) {
  const { documentStub, byId } = makeDom();

  const sandbox = {
    document: documentStub,
    window: {},
    console,
    setInterval: () => 0,        // don't actually run timers in the test
    clearTimeout: () => {},
    setTimeout: () => 0,         // app.js uses it for the abort guard + boot fallback
    Date,
    Math,
    JSON,
    AbortController: function () { this.signal = {}; this.abort = () => {}; },
    // fetch stub: serve our realistic payload for /api/status, a heartbeat for the rest
    fetch: async (url) => ({
      ok: true,
      status: 200,
      json: async () => (String(url).includes("/api/status")
        ? status
        : { state: "alive", next_tick: status.runner.next_tick }),
    }),
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);

  const code = fs.readFileSync(APP_JS, "utf8");
  vm.runInContext(code, sandbox, { filename: "app.js" });
  return { sandbox, byId };
}

// ---------------------------------------------------------------------------
// Assertions
// ---------------------------------------------------------------------------
let failures = 0;
function check(name, cond, detail) {
  const ok = !!cond;
  if (!ok) failures++;
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${ok ? "" : "  ->  " + (detail || "")}`);
}

// Map a DECISION row (.decision, under #board-lanes) to a small descriptor.
// REDESIGN: the queue became the Decisions Board — rows are .decision with a
// .decision__title and a .decision__chip (carrying the P-label + overdue copy).
// data-sev / data-sevcolor are still emitted (the severity→color seam under test).
function describeQueueItem(li) {
  const chipSpan = li.children.find((c) => c.className === "decision__chip");
  const titleNode = li.children.find((c) => c.className === "decision__title");
  // the chip text leads with the P-label (e.g. "P0", "P0 · due today", "P2 · 4d overdue")
  const chipText = chipSpan ? nodeText(chipSpan) : "";
  const pLabel = (/(P[0-3])/.exec(chipText) || [])[1] || "";
  return {
    sev: li.dataset.sev,                       // canonical P-label (data attr)
    sevcolor: li.dataset.sevcolor,             // sevColor() output CSS keys the edge off
    label: pLabel,                             // the visible P-label in the chip
    title: titleNode ? nodeText(titleNode) : "",
  };
}

// collect every .decision row across all lanes, in DOM order (= sorted render order,
// ACT NOW lane first then WHEN YOU CAN then FYI — which is exactly the global sort).
function collectDecisions(boardLanes) {
  const rows = [];
  (function walk(n) {
    for (const c of n.children || []) {
      if (c.className === "decision") rows.push(c);
      walk(c);
    }
  })(boardLanes);
  return rows;
}

(async () => {
  const status = realisticStatus();
  const { sandbox, byId } = loadApp(status);

  // app.js init() kicks off pollStatus() (async). Let microtasks drain so the
  // fetch().then(render) chain completes before we inspect the DOM.
  await new Promise((r) => setTimeout(r, 0));
  await Promise.resolve();
  await new Promise((r) => setTimeout(r, 0));

  const boardLanes = byId["board-lanes"];
  const items = collectDecisions(boardLanes).map(describeQueueItem);

  console.log("\n--- rendered decisions order (board, all lanes in DOM order) ---");
  items.forEach((it, i) => console.log(`  [${i}] sev=${it.sev} label="${it.label}" title="${it.title}"`));
  console.log("");

  check("board rendered 6 decisions", items.length === 6, `got ${items.length}`);

  // 1) LABEL: the visible severity text must be the real P-label, never "grey".
  const anyGreyLabel = items.some((it) => /grey/i.test(it.label));
  check("no item labeled 'grey' (P-label shown, not health word)", !anyGreyLabel,
    "labels=" + JSON.stringify(items.map((i) => i.label)));
  const allPLabels = items.every((it) => /^P[0-3]$/.test(it.label));
  check("every label is a P0..P3 token", allPLabels,
    "labels=" + JSON.stringify(items.map((i) => i.label)));

  // 2) data-sev (the color key) must be the P-label, not a collapsed health color.
  const sevSet = new Set(items.map((it) => it.sev));
  check("data-sev carries P-labels (P0/P1/P2/P3), not red/yellow/green/grey",
    [...sevSet].every((s) => /^P[0-3]$/.test(s)),
    "data-sev=" + JSON.stringify([...sevSet]));
  // and it must actually DISTINGUISH severities (the bug made them all "grey")
  check("data-sev distinguishes P0 from P3 (sort/color not collapsed)",
    sevSet.has("P0") && sevSet.has("P3"), "data-sev=" + JSON.stringify([...sevSet]));

  // 3) SORT (REDESIGN — O1 comparator): SEVERITY-dominant (×100), leverage as the
  //   tie-break within a severity (×40), date as a BOUNDED nudge (< the leverage step,
  //   shaped not magnitude-scaled). This replaces the OLD buggy dated-ascending sort
  //   (which let a stale most-negative item lead). Expected order on these 6 items:
  //     P0 undated+lev (340) -> P0 dated-11 no-lev (300) -> P1 dated-3 (212, dueSoon
  //     nudge) -> P1 undated (200) -> P2 undated (100) -> P3 undated (0)
  //   The key property this locks: a P0 always precedes any P1 (severity dominates),
  //   and a dated P1 never jumps ahead of an undated P0.
  const gotTitles = items.map((it) => it.title);
  const wantTitles = [
    "P0 undated critical", "dated 11d (PAT)",
    "dated 3d soon", "P1 undated", "P2 undated", "low prio undated",
  ];
  check("sort: severity-dominant (P0 before any P1), leverage tie-break, bounded date nudge",
    JSON.stringify(gotTitles) === JSON.stringify(wantTitles),
    "got=" + JSON.stringify(gotTitles));
  // explicit severity-dominance guard: the index of the first P1 must be AFTER every P0.
  const firstP1 = items.findIndex((it) => it.label === "P1");
  const anyP0AfterP1 = firstP1 >= 0 && items.slice(firstP1).some((it) => it.label === "P0");
  check("severity dominates: no P0 ever sorts below a P1", !anyP0AfterP1,
    "order=" + JSON.stringify(items.map((i) => i.label)));

  // 4) COLOR MAPPER (sevColor): the dedicated severity->color map must produce
  //   P0->red, P1->yellow, P2->green, P3->green on data-sevcolor (what CSS uses
  //   for the left edge + label tint). This is the seam the FAIL collapsed to grey.
  const colorByTitle = Object.fromEntries(items.map((it) => [it.title, it.sevcolor]));
  check("sevColor: P0 -> red (CSS -> oxblood border)", colorByTitle["P0 undated critical"] === "red", colorByTitle["P0 undated critical"]);
  check("sevColor: P1 -> yellow (CSS -> yellow border)", colorByTitle["P1 undated"] === "yellow", colorByTitle["P1 undated"]);
  check("sevColor: P2 -> green (CSS -> green border)", colorByTitle["P2 undated"] === "green", colorByTitle["P2 undated"]);
  check("sevColor: P3 -> green (CSS -> green border)", colorByTitle["low prio undated"] === "green", colorByTitle["low prio undated"]);
  // never grey: the mapper must never collapse a real severity to a non-color
  const anyGreyColor = items.some((it) => !["red", "yellow", "green"].includes(it.sevcolor));
  check("sevColor never collapses severity to grey/undefined", !anyGreyColor,
    "sevcolors=" + JSON.stringify(items.map((i) => i.sevcolor)));

  // 5) STALE DEV: the stale dev must get the "blind" treatment, not healthy-grey.
  const grid = byId["squads-grid"];
  const devLis = [];
  (function collectDevs(n) {
    for (const c of n.children || []) {
      if (c.className === "dev") devLis.push(c);
      collectDevs(c);
    }
  })(grid);
  const staleDev = devLis.find((li) => li.dataset.vis === "blind");
  check("stale dev rendered with data-vis='blind' (not healthy-grey)", !!staleDev,
    "dev vis attrs=" + JSON.stringify(devLis.map((l) => l.dataset.vis)));
  if (staleDev) {
    check("stale dev shows a 'no signal' tag (not a trusted health word)",
      /no signal/i.test(nodeText(staleDev)), nodeText(staleDev));
  } else { check("stale dev shows a 'no signal' tag", false, "no blind dev"); }

  // 6) COMMS STALLED (REDESIGN): the standalone comms pill was demoted to a verdict
  //    CHIP. The chip must carry data-state='oxblood' (the escalated alarm color) and
  //    its label must read "intake stalled" — never fall through to an unstyled chip.
  function collectChips(root) {
    const chips = [];
    (function walk(n) { for (const c of n.children || []) { if (c.className === "chip") chips.push(c); walk(c); } })(root);
    return chips;
  }
  const verdictChips = collectChips(byId["verdict-chips"]);
  const commsChip = verdictChips.find((c) => /intake/i.test(nodeText(c)));
  check("comms 'stalled' surfaced as a verdict chip", !!commsChip,
    "chips=" + JSON.stringify(verdictChips.map((c) => nodeText(c))));
  check("comms chip escalates to oxblood (data-state) when stalled",
    !!commsChip && commsChip.dataset.state === "oxblood",
    "data-state=" + JSON.stringify(commsChip && commsChip.dataset.state));
  check("comms chip label reads 'intake stalled'",
    !!commsChip && /intake stalled/i.test(nodeText(commsChip)),
    "label=" + JSON.stringify(commsChip && nodeText(commsChip)));

  // ---------------------------------------------------------------------------
  // 7) COMMS STREAMS kind/id ALIGNMENT (the bug under fix).
  //    The backend emits each comms stream keyed on `kind` (message/email/meeting).
  //    The frontend must index on that same key; if it ever keys on `s.id` again,
  //    byId is empty, every default lookup misses, and count -> "—" / state ->
  //    "unknown". We inspect the comms_triage card's three .stream rows and assert
  //    the REAL counts (43/10/8) and the EXACT state tokens render.
  // ---------------------------------------------------------------------------
  function collectStreamRows(root) {
    const rows = [];
    (function walk(n) {
      for (const c of n.children || []) {
        if (c.className === "stream" && c.dataset && c.dataset.stream) rows.push(c);
        walk(c);
      }
    })(root);
    return rows;
  }
  function streamCell(li, cls) {
    return (li.children || []).find((c) => c.className === cls);
  }
  const staffList = byId["staff-list"];
  const streamRows = collectStreamRows(staffList);
  const byStream = {};
  streamRows.forEach((li) => { byStream[li.dataset.stream] = li; });

  console.log("\n--- rendered comms streams ---");
  ["message", "email", "meeting"].forEach((k) => {
    const li = byStream[k];
    const cnt = li ? (streamCell(li, "stream__count mono") || {}).textContent : "(missing row)";
    const fr = li ? (streamCell(li, "stream__fresh") || {}) : {};
    console.log(`  ${k}: count="${cnt}" state="${fr._text || ""}" data-state="${(fr.dataset || {}).state || ""}"`);
  });
  console.log("");

  check("comms streams: all 3 rows rendered (message/email/meeting)",
    !!(byStream.message && byStream.email && byStream.meeting),
    "got streams=" + JSON.stringify(Object.keys(byStream)));

  // COUNTS — the real per-stream counts, NOT "—". This is the assertion that
  // FAILS on the pre-fix s.id index (every count collapses to "—").
  const wantCounts = { message: "43", email: "10", meeting: "8" };
  Object.entries(wantCounts).forEach(([k, want]) => {
    const li = byStream[k];
    const got = li ? (streamCell(li, "stream__count mono") || {}).textContent : null;
    check(`comms stream ${k}: count == "${want}" (NOT "—")`, got === want,
      `got=${JSON.stringify(got)}`);
  });

  // STATES — assert the EXACT real state token survives the index fix (streamRow
  // only sets dataset.state for KNOWN states, so a weak non-empty check would pass
  // on a partial regression — assert the precise token).
  const wantStates = { message: "stale", email: "stalled" };
  Object.entries(wantStates).forEach(([k, want]) => {
    const li = byStream[k];
    const fresh = li ? streamCell(li, "stream__fresh") : null;
    check(`comms stream ${k}: data-state == "${want}" (NOT "unknown"/empty)`,
      !!fresh && fresh.dataset.state === want,
      `data-state=${JSON.stringify(fresh && fresh.dataset.state)}`);
    // and the visible freshness text must be the real token, never "unknown"
    check(`comms stream ${k}: freshness text not "unknown"`,
      !!fresh && fresh._text === want,
      `text=${JSON.stringify(fresh && fresh._text)}`);
  });

  // SIGNED-IN sub-row must render for message (signed_in:true).
  const msgLi = byStream.message;
  const msgSignin = msgLi && (msgLi.children || []).find((c) => c.className === "stream__signin");
  check("comms stream message: signed_in sub-row renders (signed_in:true)",
    !!(msgSignin && msgSignin.dataset.in === "yes"),
    "signin=" + JSON.stringify(msgSignin && msgSignin.dataset.in));

  // ---------------------------------------------------------------------------
  // 8) NEGATIVE / REGRESSION ANCHOR — prove the SAME canonical payload collapses
  //    to "—"/"unknown" when the streams are keyed on `id` instead of `kind`.
  //    This demonstrates the test actually LOCKS the kind/id alignment: feed the
  //    identical streams but rewrite each {kind} -> {id}, render through the REAL
  //    app.js, and assert the card breaks exactly as the bug did. If app.js ever
  //    (wrongly) accepts `id`, this anchor fails — flagging the re-masking risk.
  // ---------------------------------------------------------------------------
  const idStatus = realisticStatus();
  idStatus.comms.streams = idStatus.comms.streams.map((s) => {
    const { kind, ...rest } = s;
    return { id: kind, ...rest };   // id-keyed (the pre-fix producer shape)
  });
  const { byId: byId2 } = loadApp(idStatus);
  await new Promise((r) => setTimeout(r, 0));
  await Promise.resolve();
  await new Promise((r) => setTimeout(r, 0));

  const idRows = collectStreamRows(byId2["staff-list"]);
  const idByStream = {};
  idRows.forEach((li) => { idByStream[li.dataset.stream] = li; });

  // With id-keyed streams, app.js (keying on kind) finds nothing -> defaults ->
  // count "—" and state "unknown". Assert that breakage to lock alignment.
  ["message", "email", "meeting"].forEach((k) => {
    const li = idByStream[k];
    const cnt = li ? (streamCell(li, "stream__count mono") || {}).textContent : null;
    check(`ANCHOR: id-keyed stream ${k} collapses count to "—" (proves kind is required)`,
      cnt === "—", `got=${JSON.stringify(cnt)}`);
    const fresh = li ? streamCell(li, "stream__fresh") : null;
    check(`ANCHOR: id-keyed stream ${k} collapses freshness to "unknown"`,
      !!fresh && fresh._text === "unknown", `text=${JSON.stringify(fresh && fresh._text)}`);
  });

  // ---------------------------------------------------------------------------
  // 9) DEGRADED BANNER — freshness spine (previously 0 assertions on this path).
  //    The backend emits top-level shown_log_date (ISO date-only) + fell_back on a
  //    degraded payload. The banner must LEAD with that date so a viewer knows WHICH
  //    day's snapshot they see. Three scenarios, each its own loadApp() (mirrors the
  //    idStatus anchor block) so they never share/mutate realisticStatus().
  // ---------------------------------------------------------------------------
  function degradedStatus(overrides) {
    const s = realisticStatus();
    s.degraded = true;
    s.fell_back = true;
    s.shown_log_date = "2026-06-20";
    s.warnings = ["requested log 2026-06-22 missing; fell back to 2026-06-20.md"];
    return Object.assign(s, overrides || {});
  }

  // 9a) date present -> banner shown, leads with the date, not date-less copy.
  {
    const { byId: bD } = loadApp(degradedStatus());
    await new Promise((r) => setTimeout(r, 0));
    await Promise.resolve();
    await new Promise((r) => setTimeout(r, 0));
    const banner = bD["degraded-banner"];
    const txt = banner ? banner.textContent : "";
    console.log(`\n--- degraded banner (date present) ---\n  hidden=${banner && banner.hidden}\n  text="${txt}"\n`);
    check("degraded banner: NOT hidden when degraded:true", !!banner && banner.hidden === false,
      `hidden=${banner && banner.hidden}`);
    // friendly date form is "Jun 20"; assert the date is surfaced.
    check("degraded banner: contains the stale date (Jun 20)", /Jun 20/.test(txt), txt);
    check("degraded banner: leads with the date (not date-less 'Showing last-known —')",
      /^Showing data from /.test(txt) && !/^Showing last-known —/.test(txt), txt);
    check("degraded banner: still mentions 'last-known'", /last-known/.test(txt), txt);
    check("degraded banner: carries the warning detail", /fell back to 2026-06-20/.test(txt), txt);
  }

  // 9b) NULL shown_log_date -> graceful fallback to the date-less copy, no junk.
  {
    const { byId: bN } = loadApp(degradedStatus({ shown_log_date: null }));
    await new Promise((r) => setTimeout(r, 0));
    await Promise.resolve();
    await new Promise((r) => setTimeout(r, 0));
    const banner = bN["degraded-banner"];
    const txt = banner ? banner.textContent : "";
    console.log(`--- degraded banner (shown_log_date:null) ---\n  text="${txt}"\n`);
    check("degraded banner (null date): falls back to 'Showing last-known —'",
      /^Showing last-known —/.test(txt), txt);
    check("degraded banner (null date): no 'null'/'undefined'/'Invalid Date' leak",
      !/null|undefined|Invalid Date/i.test(txt), txt);
  }

  // 9c) not degraded -> banner hidden.
  {
    const ns = realisticStatus();           // realisticStatus has no `degraded` -> falsy
    const { byId: bH } = loadApp(ns);
    await new Promise((r) => setTimeout(r, 0));
    await Promise.resolve();
    await new Promise((r) => setTimeout(r, 0));
    const banner = bH["degraded-banner"];
    check("degraded banner: hidden when not degraded", !!banner && banner.hidden === true,
      `hidden=${banner && banner.hidden}`);
  }

  console.log(`\n${failures === 0 ? "ALL PASS" : failures + " FAILURE(S)"} — frontend contract test`);
  process.exit(failures === 0 ? 0 : 1);
})().catch((e) => { console.error("harness error:", e); process.exit(2); });
