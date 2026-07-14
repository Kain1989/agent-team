/* ============================================================
   MISSION CONTROL — Phase 1 controller (read-only)
   - polls GET /api/status  (~12s)  -> full render
   - polls GET /api/heartbeat (~5s) -> runner dot freshness
   - countdown to next tick ticks client-side between polls
   - renders gracefully on stale / empty / error: keeps last-known,
     shows "stale since …", NEVER a blank hang.
   Only GET requests are issued. No mutation. Buttons are inert.
   ============================================================ */
(() => {
  "use strict";

  const STATUS_MS = 12000;
  const HEARTBEAT_MS = 5000;
  const TICK_MS = 1000;           // countdown + freshness repaint
  // After this long with no successful poll, treat the portal as BLIND (can't see system).
  const BLIND_AFTER_MS = 30000;
  // MVP: this build runs ON-DEMAND (the tick scheduler is off by default; the job worker
  // handles work as you submit it). So "scheduler off" is the NORMAL state, not a "runner
  // down" alarm — when true, the hero/verdict present scheduler-off calmly instead of red.
  const ON_DEMAND_MODE = true;

  // ---- embedded mock so the page renders standalone (file://) for dev/testing.
  // The live page still fetches the real endpoints first; mock is the fallback
  // ONLY when no fetch has ever succeeded (e.g. opened directly off disk).
  // HARD LESSON: the staff section once broke on REAL data because this mock used
  // SHORT role strings while the live API emits the LONGER roster strings from
  // team.json. The mock now carries descriptive multi-line roles (pm_agent /
  // design_lead / comms_triage), the comms.streams[], and per-dev id/pair/next_step
  // — so opening the page off-disk exercises the same wrapping, 3-stream comms card,
  // and full-roster paths the live data hits. A short-string mock can never again
  // hide a long-string layout break.
  const MOCK_STATUS = {
    org: { health: "yellow", counts: { red: 1, yellow: 3, reported: 12, worked: 6, green: 5, committed: 4, prs: 2 } },
    runner: {
      state: "alive",
      last_tick:  { id: "wf_c4bdaca7", name: "EVENING", at: isoMinutesAgo(126) },
      next_tick:  { name: "NIGHT", at: isoMinutesFromNow(54), in_seconds: 54 * 60 },
      scheduler: { enabled: true, running: true },
      heartbeat_age_s: 3, busy: false, dual_runner: false, in_flight: null
    },
    // Mock mirrors the LIVE contract exactly: severity is P0/P1/P2/P3 (NOT
    // red/yellow/green). Keeping it on the real vocab means the mock can never
    // again mask a front↔back severity contract break. It now also carries an
    // EXACT-duplicate item, an OVERDUE (negative-day) item, a null-leverage item,
    // and a 360-day noise item — so opening the page off-disk exercises the dedup,
    // overdue copy, lane grouping, and noise-penalty paths the live data hits.
    awaiting_kain: [
      { title: "Approve dev_b's truncate() commit", severity: "P0", days_remaining: 0, leverage: "lands the first demo-app helper" },
      { title: "Decide slugify separator: dash vs underscore", severity: "P0", days_remaining: null, leverage: "unblocks dev_a's slugify task" },
      { title: "Cut the demo-app v0.1 tag", severity: "P1", days_remaining: 11, leverage: null },
      { title: "Review the portal severity-contract test", severity: "P1", days_remaining: null, leverage: "closes the front↔back seam" },
      { title: "Name the top-words helper API", severity: "P1", days_remaining: null, leverage: null },
      { title: "Update the demo-app README quickstart", severity: "P2", days_remaining: -4, leverage: "unblocks onboarding" },
      { title: "Backfill title-case helper docs", severity: "P2", days_remaining: 360, leverage: "housekeeping" },
      // exact dup of item[0] (same title + severity) -> must dedup to one row
      { title: "Approve dev_b's truncate() commit", severity: "P0", days_remaining: 0, leverage: "lands the first demo-app helper" }
    ],
    squads: [
      { id: "demo_squad", name: "Demo Dev Squad", health: "green", devs: [
        { id: "dev_a", role: "Developer — Builder",           pair: "dev_b", branch: null, health: null, current_task: "implement slugify() with a max_length cap; tests green", next_step: "start the top_words helper next", last_entry_date: isoDateToday(), last_entry: { date: isoDateToday(), title: "slugify max_length" } },
        { id: "dev_b", role: "Developer — Reviewer & Tests",  pair: "dev_a", branch: "auto/standup-truncate-helper", health: null, current_task: "truncate() helper — committed to auto/standup-truncate-helper, awaiting approval", next_step: "on approve it lands on the branch; main stays untouched", last_entry_date: "2026-06-20", last_entry: { date: "2026-06-20", title: "truncate committed" } }
      ]},
      { id: "portal", name: "Team Portal Squad", health: "yellow", devs: [
        { id: "portal_backend",  role: "Portal Dev — Backend & Jobs (FastAPI)", pair: "portal_frontend", branch: null, health: null, current_task: "regression test: P0/P1/P2 severity contract on the decisions board", next_step: "cover the blockquoted-heading parse path too", last_entry_date: isoDateToday(), last_entry: { date: isoDateToday(), title: "severity contract test" } },
        { id: "portal_frontend", role: "Portal Dev — Mission Control UI",       pair: "portal_backend",  branch: null, health: null, current_task: "resolve rebase conflict in app.js [draft, uncommitted]", next_step: "branch: auto/standup-resolve-appjs-conflict", last_entry_date: "2026-06-22", last_entry: { date: "2026-06-22", title: "rebase conflict" } }
      ]}
    ],
    // Staff: the MVP's lean staff — a Steve-Jobs-grounded pm_agent, one Apple-HIG
    // design_lead, and the (off-by-default) comms_triage. comms_triage MUST be
    // present and carry the comms.streams shape (renderCommsStreams only runs inside
    // its card). Roles stay descriptive so the off-disk mock still exercises the
    // multi-line role wrapping the live roster strings hit.
    staff: [
      { id: "comms_triage", role: "Comms Triage — Local Intake & Routing (optional, off by default in the MVP)",  note: "reads a local messages/inbox/ folder; routed items appear on the EM board tagged source=comms" },
      { id: "pm_agent",     role: "Product Manager Agent (Steve Jobs-grounded scope + say-no + board)",            note: "joins INTAKE + the DESIGN challenge; the board reflects its keep/kill calls" },
      { id: "design_lead",  role: "Design Lead — Clarity & Craft (Apple HIG-grounded)",                            note: "pairs with portal_frontend; a light design read every tick + a full critique on the morning design tick" }
    ],
    // comms = 1 agent (comms_triage), 3 streams: MESSAGE / EMAIL / MEETING, each
    // read from local sample files under messages/inbox/. Each carries a count +
    // per-stream freshness state + signed_in. last_pull_at/stale_hours/state stay as
    // the aggregate fallback for when streams[] is absent.
    comms: {
      last_pull_at: isoMinutesAgo(50 * 60), stale_hours: 50, state: "stalled", signed_in: true,
      streams: [
        { kind: "message", label: "Message", source: "inbox/message.json", count: 7,  state: "stale",   stale_hours: 50, signed_in: true,  last_pull_at: isoMinutesAgo(50 * 60) },
        { kind: "email",   label: "Email",   source: "inbox/email.json",   count: 23, state: "stalled", stale_hours: 50, signed_in: true,  last_pull_at: isoMinutesAgo(50 * 60) },
        { kind: "meeting", label: "Meeting", source: "inbox/meeting.json", count: 2,  state: "missing", stale_hours: null, signed_in: false, last_pull_at: null }
      ]
    },
    last_tick: { id: "wf_c4bdaca7", name: "EVENING", at: isoMinutesAgo(126), agents: 18, worked: 0, green: 6, committed: 0, prs: 0, duration_min: 41 },
    // landing_queue carries duplicate commits (f172cf7 ×3, 00a72e7 ×2) so the
    // off-disk mock exercises the client-side commit dedup + ×N multiplier path.
    landing_queue: [
      { branch: null, commit: "a1b2c3d", status: "committed-unpushed" },
      { branch: null, commit: "f172cf7", status: "committed-unpushed" },
      { branch: null, commit: "00a72e7", status: "committed-unpushed" },
      { branch: null, commit: "f172cf7", status: "committed-unpushed" },
      { branch: null, commit: "843f0ce", status: "committed-unpushed" },
      { branch: null, commit: "00a72e7", status: "committed-unpushed" },
      { branch: null, commit: "f172cf7", status: "committed-unpushed" }
    ],
    updated_at: new Date().toISOString()
  };

  // Embedded roster fallback (the /api/team payload shape). The MVP roster has an
  // EMPTY bench (team.json bench: []), so this carries no bench cast; pairs come from
  // the per-dev `pair` fields in MOCK_STATUS. It still serves as the last-known
  // roster if /api/team is unreachable on the live page.
  const MOCK_TEAM = {
    bench: []
  };

  // ---- in-memory state
  const S = {
    data: null,            // last-known good /api/status payload
    team: null,            // last-known /api/team payload (pairs + bench)
    pairOf: {},            // dev_id -> pair id, merged from /api/team
    lastStatusOk: 0,       // epoch ms of last successful status fetch
    lastBeatOk: 0,         // epoch ms of last successful heartbeat
    runnerState: null,     // live state, can be overridden by heartbeat staleness
    nextTickAt: null,      // Date for countdown
    usingMock: false,
    statusFails: 0         // consecutive /api/status failures (drives poll backoff)
  };

  // ───────────────────────── helpers ─────────────────────────
  // NAIVE-LOCAL TIMESTAMP ASSUMPTION (load-bearing):
  // The backend emits ISO-8601 WITHOUT a timezone offset (Python
  // datetime.isoformat() on a naive local datetime — see app.py). `new Date(iso)`
  // parses such an offset-less string as the VIEWER's local time. So every
  // derived value below — the countdown to next_tick, "Xs ago" freshness,
  // last_tick age, comms last-pull age — is correct ONLY because the viewer and
  // the server share the same wall clock + timezone. That holds today because the
  // portal is bound to 127.0.0.1 (loopback): browser and uvicorn are the same
  // host. If this is ever served cross-host/cross-tz, the backend must emit ISO
  // with an explicit offset/Z (already requested in .standup/portal_frontend.md),
  // OR the times here will silently skew by the tz delta. Do not assume UTC.
  function isoMinutesAgo(m)      { return new Date(Date.now() - m * 60000).toISOString(); }
  function isoMinutesFromNow(m)  { return new Date(Date.now() + m * 60000).toISOString(); }
  // local YYYY-MM-DD for "today" (mock last_entry_date so derived status reads "fresh")
  function isoDateToday() {
    const d = new Date();
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  }
  function $(id) { return document.getElementById(id); }
  function el(tag, cls, txt) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (txt != null) n.textContent = txt;
    return n;
  }
  function esc(s) { return String(s == null ? "" : s); }

  function fmtClock(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (isNaN(d)) return "—";
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  }
  // Format the backend's shown_log_date (an ISO DATE-ONLY string, e.g. "2026-06-20",
  // produced from date.today().isoformat() or a log filename stem). It carries NO
  // time/tz, so we MUST NOT feed it to `new Date(...)` — `new Date("2026-06-20")`
  // parses as UTC midnight and renders the PREVIOUS day in negative-UTC zones (the
  // standing tz trap in this project). So we split the YYYY-MM-DD shape manually and
  // build a friendly "Mon DD" label; anything that doesn't match (defensive — a
  // filename-stem variant) is echoed verbatim. Null/blank -> "" so the caller can
  // gracefully fall back to the date-less copy (never "undefined"/"Invalid Date").
  function fmtLogDate(s) {
    if (s == null) return "";
    const str = String(s).trim();
    if (!str) return "";
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(str);
    if (!m) return str; // unknown shape -> echo as-is, never mangle
    const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    const mi = parseInt(m[2], 10) - 1;
    const day = parseInt(m[3], 10);
    if (mi < 0 || mi > 11 || day < 1 || day > 31) return str;
    return `${months[mi]} ${day}`;
  }
  function fmtAge(ms) {
    if (ms == null || isNaN(ms)) return "—";
    const s = Math.max(0, Math.round(ms / 1000));
    if (s < 60) return s + "s ago";
    const m = Math.floor(s / 60);
    if (m < 60) return m + "m ago";
    const h = Math.floor(m / 60);
    if (h < 24) return h + "h " + (m % 60) + "m ago";
    return Math.floor(h / 24) + "d ago";
  }
  function fmtDur(min) {
    if (min == null) return "—";
    const h = Math.floor(min / 60), m = min % 60;
    return h ? `${h}h ${m}m` : `${m}m`;
  }
  function normHealth(h) {
    h = String(h || "").toLowerCase();
    return ["green", "yellow", "red", "grey", "gray"].includes(h) ? (h === "gray" ? "grey" : h) : "grey";
  }
  // A dev's progress is "stale" if its last dated entry is >1 day old. Used for
  // the "as of <date>" treatment so an aging progress file reads as a soft warn.
  function isStaleDate(dateStr) {
    if (!dateStr) return false;
    const d = new Date(dateStr + "T00:00:00");
    if (isNaN(d)) return false;
    const days = (Date.now() - d.getTime()) / 86400000;
    return days > 1.5;
  }

  // Severity is a SEPARATE contract from health. The backend (and README) emit
  // awaiting_kain[].severity as P0/P1/P2/P3 — NOT red/yellow/green. Health's
  // normHealth() must never touch it (that was the bug: P-labels fell through to
  // "grey", giving grey borders + the literal word "grey" + a broken sort).
  //   normSev:  any input -> canonical "P0".."P3" (default "P3" = least urgent).
  //   sevColor: P-label   -> the health COLOR token used for the left edge / tint.
  //   sevRank:  P0 < P1 < P2 < P3 for sorting (lower = more urgent, sorts first).
  function normSev(s) {
    const m = /\bP([0-3])\b/i.exec(String(s == null ? "" : s));
    return m ? "P" + m[1] : "P3";
  }
  function sevColor(s) {
    switch (normSev(s)) {
      case "P0": return "red";
      case "P1": return "yellow";
      default:   return "green";   // P2 / P3
    }
  }
  function sevRank(s) {
    const r = { P0: 0, P1: 1, P2: 2, P3: 3 };
    const k = normSev(s);
    return k in r ? r[k] : 3;
  }

  // ─────────────── humanize a seconds duration ("~59h", "~3m", "12h 4m") ───────────────
  function humanizeSecs(s) {
    if (s == null || isNaN(s)) return "unknown";
    s = Math.max(0, Math.round(s));
    if (s < 90) return `~${s}s`;
    const m = Math.round(s / 60);
    if (m < 90) return `~${m}m`;
    let h = Math.floor(s / 3600);
    let rm = Math.round((s % 3600) / 60);
    // CARRY: rounding the remainder minutes can yield 60 (e.g. 215990s -> 59h "60m").
    // Roll a full 60 into the next hour so we never render "Xh 60m"; the rm? ternary
    // below then drops the now-zero minutes, giving a clean "~60h".
    if (rm === 60) { h += 1; rm = 0; }
    // keep HOURS up to 72h — a runner-down alarm reads more precisely as "~59h"
    // than "~2d" (the buried-lede the verdict + hero must surface sharply).
    if (h < 72) return rm ? `~${h}h ${rm}m` : `~${h}h`;
    return `~${Math.round(h / 24)}d`;
  }

  // ═══════════════ DECISIONS: dedup + score + lane (client-side derivations) ═══════════════
  // There is NO stable `id` in the awaiting_kain contract (app.py strips it) — title
  // is the only identity. Dedup key = normalized title + normalized severity.
  function normDecisionTitle(t) { return String(t == null ? "" : t).toLowerCase().replace(/\s+/g, " ").trim(); }

  // Keep FIRST occurrence; on an exact-key dup keep the MORE urgent days_remaining
  // (smaller non-null; null = +∞) and a non-null leverage if the dup supplies one.
  function dedupAwaiting(items) {
    const map = new Map();
    const order = [];
    (items || []).forEach((it) => {
      if (!it) return;
      const key = normDecisionTitle(it.title) + "|" + normSev(it.severity);
      if (!map.has(key)) { const copy = Object.assign({}, it); map.set(key, copy); order.push(key); }
      else {
        const cur = map.get(key);
        const a = cur.days_remaining, b = it.days_remaining;
        if (a == null) cur.days_remaining = b;
        else if (b != null) cur.days_remaining = Math.min(a, b);
        if (cur.leverage == null && it.leverage != null) cur.leverage = it.leverage;
      }
    });
    return order.map((k) => map.get(k));
  }

  // O1 COMPARATOR (overrides the spec's score). Ordering keys, by weight:
  //   severity ×100  (DOMINANT — a P0 always outranks a P1)
  //   leverage ×40   (tie-break WITHIN a severity — a full step, gating beats non-gating)
  //   date nudge     (SHAPED, bounded to ±15 — strictly < the 40 leverage step, so the
  //                   overdue contribution can NEVER exceed a leverage step; and it is
  //                   shaped, NOT magnitude-scaled, so "most-overdue" can't lead):
  //       due today (0)            -> +15
  //       due soon  (1..7)         -> +14..+8   (8 + (7-dr); sooner ranks higher)
  //       undated   (null)         ->   0
  //       fresh overdue (-1..-10)  -> +6  FLAT  (recent miss = "look", fixed nudge)
  //       STALE overdue (<= -11)   -> -10       (deep-negative = parser-garbage / already
  //                                              -resolved leftovers; PENALIZED so they sink
  //                                              below live items in their severity tier)
  //       far/noise (>= 180)       -> -500      (360-day MM-DD rollover garbage)
  // This kills the original bug: a stale "-32d already-resolved leftover" P0 no
  // longer LEADS by being most-negative — it sinks behind every live-dated + undated P0.
  const SEV_W = 100, LEV_W = 40, NOISE_PEN = 500;
  function decisionDateNudge(dr) {
    if (dr == null) return 0;
    if (dr >= 180) return -NOISE_PEN;
    if (dr === 0) return 15;
    if (dr > 0 && dr <= 7) return 8 + (7 - dr);
    if (dr < 0 && dr >= -10) return 6;
    if (dr <= -11) return -10;
    return 0;
  }
  function decisionScore(it) {
    const sev = { P0: 3, P1: 2, P2: 1, P3: 0 }[normSev(it.severity)];
    const lev = it.leverage ? 1 : 0;
    return sev * SEV_W + lev * LEV_W + decisionDateNudge(it.days_remaining);
  }
  function decisionCmp(a, b) {
    const sa = decisionScore(a), sb = decisionScore(b);
    if (sb !== sa) return sb - sa;
    // tie-break: a real near date first (soonest), then deep-overdue, then noise, then title
    const key = (it) => {
      const dr = it.days_remaining;
      if (dr == null) return 1e6;
      if (dr >= 180) return 1e7;
      if (dr < 0) return 1000 + (-dr);
      return dr;
    };
    const ka = key(a), kb = key(b);
    if (ka !== kb) return ka - kb;
    return normDecisionTitle(a.title) < normDecisionTitle(b.title) ? -1 : 1;
  }
  // Lane predicate (committed names). ACT NOW: P0 OR overdue OR due-within-a-week
  // (the ≥180 noise is excluded by the range check). WHEN YOU CAN: has leverage or is P1.
  function decisionLane(it) {
    const sv = normSev(it.severity), dr = it.days_remaining, lev = it.leverage != null;
    const actNow = sv === "P0" || (dr != null && dr < 0) || (dr != null && dr >= 0 && dr <= 7);
    if (actNow) return "act";
    if (lev || sv === "P1") return "when";
    return "fyi";
  }

  // ═══════════════ DEV STATUS: derived (health is null for every dev) ═══════════════
  // Ordered predicates; first match wins. Matching is WORD-BOUNDARY regex on the
  // combined current_task + next_step (so "uncommitted" ≠ "committed", and "fix"
  // doesn't false-match inside another token). BLOCKED is first (the safest
  // over-trigger — flag "look here" rather than hide a stuck dev), but with a
  // COMPLETED-FIX guard so a resolved fix ("FALSE-GREEN fix … build was failing" →
  // GREEN) reads PROGRESS, not BLOCKED. Two traps this navigates:
  //   (a) a dev mid-keystone-handoff with a branch + recent activity must NOT read
  //       "Idle" — the branch + task keep it awaiting/in-progress.
  //   (b) do NOT label "Blocked" when the blocker marker co-occurs with a
  //       fix/resolved/GREEN signal (a COMPLETED fix is not a block).
  // Markers are anchored so "[draft, UNcommitted]" is NOT read as "committed".
  const BLOCK_RE = /\b(blocked|refused|hold|breach|conflict|false-green|build was failing|build break|500|hazard|lost|stalled)\b/;
  // a COMPLETED-fix / resolved signal — its presence means a blocker word is the
  // description of work just DONE, not a live stuck state. "uncommitted" excluded by \b.
  const RESOLVED_RE = /\b(fix|fixed|fixes|resolved|green|passes|passing|pass at 100|committed)\b/;
  // "uncommitted" / "not committed" must NOT count as a commit-to-branch signal.
  const UNCOMMITTED_RE = /\b(uncommitted|not committed|un-committed)\b/;
  const COMMITTED_RE = /\bcommitted\b/;
  function deriveDevStatus(dev) {
    if (dev && dev.stale === true) return "blind";          // can't read the file → no signal
    const ct = (dev && dev.current_task ? String(dev.current_task) : "").trim();
    const ns = (dev && dev.next_step ? String(dev.next_step) : "").trim();
    const t = (ct + " " + ns).toLowerCase();
    const branch = dev && dev.branch ? String(dev.branch).trim() : "";

    const hasBlockMarker = BLOCK_RE.test(t);
    // "committed" is a real landed-commit signal ONLY when it isn't "uncommitted"/
    // "not committed" (those negate it). A bare "uncommitted" must read as NOT-landed.
    const reallyCommitted = COMMITTED_RE.test(t) && !UNCOMMITTED_RE.test(t);
    // resolved/fix context: a genuine fix/resolved/green/passing signal, OR a real
    // landed commit. "[draft, uncommitted]" alone is NONE of these → no resolve context.
    const FIX_GREEN_RE = /\b(fix|fixed|fixes|resolved|green|passes|passing|pass at 100)\b/;
    const hasResolved = FIX_GREEN_RE.test(t) || reallyCommitted;
    // A live blocker word with NO fix/resolved signal → BLOCKED. A draft/uncommitted
    // conflict ("resolve … conflict … [draft, uncommitted]") has no fix/green → BLOCKED.
    if (hasBlockMarker && !hasResolved) return "blocked";

    // AWAITING-MERGE: work is COMMITTED to a branch and needs landing.
    if (!hasBlockMarker && reallyCommitted) return "awaiting";
    // committed-to-a-branch even when the only commit signal is "→ COMMITTED to auto/…"
    if (!hasBlockMarker && /\bauto\/standup/.test(t) && branch && !UNCOMMITTED_RE.test(t)) return "awaiting";

    // IN-PROGRESS: has a current task AND the last entry is fresh (≤1.5d).
    const fresh = !isStaleDate(dev && (dev.last_entry_date || (dev.last_entry && dev.last_entry.date)));
    if (ct && fresh) return "progress";

    // a dev mid-handoff with a branch + a real task but an older entry still reads as
    // awaiting-merge (a keystone on a feature branch), never Idle.
    if (ct && branch) return "awaiting";

    // IDLE: no task, or an old entry with nothing actionable.
    if (!ct) return "idle";
    return "progress";   // has a task but the entry is a touch old — still in-progress, not idle
  }
  const STATUS_META = {
    blocked:  { label: "BLOCKED",       glyph: "■" },
    awaiting: { label: "AWAITING MERGE", glyph: "◆" },
    progress: { label: "IN PROGRESS",   glyph: "▸" },
    idle:     { label: "IDLE",          glyph: "·" },
    blind:    { label: "NO SIGNAL",     glyph: "▨" }
  };

  // ═══════════════ RUNNER STATE (folds heartbeat-staleness over the payload) ═══════════════
  // The portal can only PROVE liveness from its own heartbeats. If the payload claims
  // "alive" but beats stopped past 3 cycles, downgrade to "stale" (we can't confirm).
  // scheduler.enabled===false is treated as the daemon being OFF → effectively dead
  // (no tick will fire on its own).
  function computeRunnerState() {
    const r = (S.data && S.data.runner) || {};
    let state = String(S.runnerState || r.state || "stale").toLowerCase();
    const beatAge = S.lastBeatOk ? Date.now() - S.lastBeatOk : Infinity;
    if (!S.usingMock && state === "alive" && beatAge > HEARTBEAT_MS * 3) state = "stale";
    if (!["alive", "stale", "dead"].includes(state)) state = "stale";
    return state;
  }

  // ───────────────────────── fetch ─────────────────────────
  async function getJSON(url) {
    // 4s timeout so a hung backend never freezes the UI
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 4000);
    try {
      const r = await fetch(url, { method: "GET", cache: "no-store", signal: ctrl.signal });
      if (!r.ok) throw new Error("HTTP " + r.status);
      return await r.json();
    } finally {
      clearTimeout(t);
    }
  }

  async function pollStatus() {
    try {
      const data = await getJSON("/api/status");
      S.data = data;
      S.lastStatusOk = Date.now();
      S.usingMock = false;
      S.statusFails = 0;     // recovered — reset poll backoff
      if (data.runner) {
        S.runnerState = data.runner.state;
        if (data.runner.next_tick && data.runner.next_tick.at) {
          S.nextTickAt = new Date(data.runner.next_tick.at);
        } else if (data.runner.next_tick && data.runner.next_tick.in_seconds != null) {
          S.nextTickAt = new Date(Date.now() + data.runner.next_tick.in_seconds * 1000);
        }
      }
      render(data);
      clearConnBanner();
    } catch (e) {
      handleStatusFailure(e);
    }
  }

  async function pollHeartbeat() {
    try {
      const hb = await getJSON("/api/heartbeat");
      S.lastBeatOk = Date.now();
      // heartbeat may carry an authoritative runner state / age; honor it
      if (hb && hb.runner && hb.runner.state) S.runnerState = hb.runner.state;
      else if (hb && hb.state) S.runnerState = hb.state;
      // heartbeat carries the freshest single-flight signals (busy / dual_runner /
      // in_flight) — feed them to the action guard so the lockout reacts in ~5s.
      if (hb) Actions.onLive(hb);
      // a fresh heartbeat can change the derived runner state (alive↔stale↔dead),
      // which drives the verdict + hero — re-render those cheaply when we have data.
      if (S.data) { renderVerdict(S.data); renderHero(S.data); }
    } catch (e) {
      // a missed heartbeat alone isn't fatal; staleness logic in tick() handles it
    }
  }

  // /api/team carries the FULL roster: per-dev `pair` (not in /api/status) and the
  // inactive `bench[]`. We fetch it once (and refresh it occasionally) so the page
  // can show every lanemate + the bench strip. /api/status stays the live-state
  // source; this is roster completeness. Failure is non-fatal — we keep last-known
  // (or the embedded MOCK_TEAM) so a roster glitch never blanks pairs/bench.
  function adoptTeam(team) {
    if (!team) return;
    S.team = team;
    const idx = {};
    (team.squads || []).forEach(sq => (sq.devs || []).forEach(d => {
      if (d && d.id && d.pair) idx[d.id] = d.pair;
    }));
    S.pairOf = idx;
    // keep the Operate target picker in sync with the freshly-adopted roster.
    if (typeof Jobs !== "undefined" && Jobs.onTeam) Jobs.onTeam(team);
  }
  async function pollTeam() {
    try {
      const team = await getJSON("/api/team");
      adoptTeam(team);
      // re-render with the freshly merged pairs/bench if we already have status
      if (S.data) render(S.data);
    } catch (e) {
      // keep last-known roster; if we have none at all, seed from the mock so
      // pairs + bench still render rather than vanish.
      if (!S.team) adoptTeam(MOCK_TEAM);
    }
  }

  // First-paint bootstrap: if no real endpoint ever answers, fall back to mock
  // so the page is never blank when opened straight off disk.
  function bootstrapMockIfNeeded() {
    if (S.lastStatusOk) return;
    S.data = MOCK_STATUS;
    S.usingMock = true;
    S.runnerState = MOCK_STATUS.runner.state;
    S.nextTickAt = new Date(MOCK_STATUS.runner.next_tick.at);
    if (!S.team) adoptTeam(MOCK_TEAM);
    render(MOCK_STATUS);
    setConnBanner("warn", "Showing embedded sample — live API not reachable yet.");
  }

  // ───────────────────────── connection banner ─────────────────────────
  // Distinguish "team unhealthy" (data-driven, red health words) from
  // "portal can't see the system" (blind/stale — grey, different copy).
  function setConnBanner(kind, msg) {
    const b = $("conn-banner");
    b.hidden = false;
    b.dataset.kind = kind;
    b.textContent = msg;
    document.body.dataset.conn = kind === "blind" ? "stale" : (kind === "error" ? "error" : "ok");
    $("conn-foot").textContent = kind === "blind" ? "PORTAL BLIND" : kind.toUpperCase();
  }
  function clearConnBanner() {
    const b = $("conn-banner");
    if (S.usingMock) return; // keep the mock notice
    b.hidden = true;
    document.body.dataset.conn = "ok";
    $("conn-foot").textContent = "LIVE";
  }
  function handleStatusFailure(e) {
    S.statusFails = Math.min(S.statusFails + 1, 6);   // cap for backoff
    const age = S.lastStatusOk ? Date.now() - S.lastStatusOk : Infinity;
    if (S.data) {
      // we have last-known data — keep it, mark stale, never blank
      const since = S.lastStatusOk ? fmtAge(age) : "start";
      if (age > BLIND_AFTER_MS) {
        setConnBanner("blind", `Portal can't reach the system — showing last-known from ${since}. This is NOT a team-health red; the portal lost sight of the runner.`);
      } else {
        setConnBanner("warn", `Refresh failed — retrying. Last good ${since}.`);
      }
    } else {
      // never even got one payload AND we are on the live server
      setConnBanner("blind", "Portal can't reach /api/status. Showing nothing live yet — is the backend up?");
      bootstrapMockIfNeeded();
    }
  }

  // ───────────────────────── render: full status ─────────────────────────
  function render(d) {
    if (!d) return;
    renderVerdict(d);
    renderHero(d);
    renderDecisions(d.awaiting_kain);
    renderSquads(d.squads);
    renderStaff(d.staff, d.comms);
    renderBench();
    renderSysMeta(d.last_tick, d.landing_queue);
    renderDegradedBanner(d);
    Actions.onStatus(d);
  }

  // ─────────────── VERDICT (worst-signal-wins synthesis) ───────────────
  // Returns { level: "critical"|"warn"|"steady", headline, moveText, moveKey }.
  // moveKey set when the first move IS the promoted Run-standup button (so the line
  // can render it as a key affordance). O3: when org.health is unknown (not
  // green/yellow/red), we NEVER fall through to "All clear" — we emit a WARN.
  function verdictOf(d) {
    const r = (d && d.runner) || {};
    const org = (d && d.org) || {};
    const comms = (d && d.comms) || {};
    const state = computeRunnerState();
    const schedOff = r.scheduler && r.scheduler.enabled === false;
    const hbAge = r.heartbeat_age_s;
    const hbStr = humanizeSecs(hbAge);
    const deduped = dedupAwaiting(d && d.awaiting_kain);
    const awaitingCount = deduped.length;
    const nActNow = deduped.filter((it) => decisionLane(it) === "act").length;
    const datedP0 = deduped.filter((it) =>
      normSev(it.severity) === "P0" && it.days_remaining != null &&
      ((it.days_remaining >= 0 && it.days_remaining < 180) || it.days_remaining < 0)).length;
    const health = String(org.health || "").toLowerCase();
    const knownHealth = ["green", "yellow", "red"].includes(health);

    // 1. split-brain
    if (r.dual_runner === true) {
      return { level: "critical", headline: "SPLIT-BRAIN: two runners live. Kill one before anything else.",
        moveText: "Resolve the dual runner — kill one process.", moveKey: null };
    }
    // 2. ON-DEMAND mode (MVP): the tick scheduler is intentionally off; the job worker
    //    runs work as you submit it. This is the NORMAL posture, not a failure — present
    //    it calmly and point at the queue rather than crying "runner down".
    if (ON_DEMAND_MODE && schedOff && state !== "dead" && r.dual_runner !== true) {
      return { level: awaitingCount > 0 ? "warn" : "steady",
        headline: awaitingCount > 0
          ? `On-demand mode · ${awaitingCount} item(s) awaiting your approval. Review and approve them below.`
          : `On-demand mode — submit a job and the worker runs it. The queue is clear.`,
        moveText: awaitingCount > 0 ? `Review the ${awaitingCount} awaiting item(s) below.` : "Submit a code task from the Operate board below.",
        moveKey: null };
    }
    // 2b. runner dead (state dead OR scheduler off OR heartbeat ≥ 3h)
    if (state === "dead" || schedOff || (hbAge != null && hbAge >= 10800)) {
      return { level: "critical",
        headline: `Runner is DOWN — the scheduler is off and no tick has fired in ${hbStr}. Nothing is moving until you restart it.`,
        moveText: "Restart the runner", moveKey: "run-standup" };
    }
    // 3. runner stale
    if (state === "stale" || (hbAge != null && hbAge >= 90 && hbAge < 10800)) {
      return { level: "warn",
        headline: `Runner unconfirmed — no heartbeat for ${hbStr}. The board may be last-known.`,
        moveText: "Confirm the runner is alive (or restart it).", moveKey: "run-standup" };
    }
    // O3 guard: unknown org health must NOT read as steady.
    if (!knownHealth) {
      return { level: "warn",
        headline: `Org health unknown — board may be degraded.${awaitingCount ? ` ${awaitingCount} call(s) still waiting.` : ""}`,
        moveText: awaitingCount ? `Clear the ACT NOW lane (${nActNow} item(s)).` : "Verify the data source.", moveKey: null };
    }
    // 4. team red
    if (health === "red") {
      const red = (org.counts && org.counts.red) || 0;
      return { level: "critical", headline: `Team RED — ${red} squad(s) blocked red. Triage the red lane first.`,
        moveText: `Triage the ${red} red squad(s).`, moveKey: null };
    }
    // 5. dated P0(s)
    if (datedP0 > 0) {
      return { level: "warn", headline: `${datedP0} dated P0 decision(s) overdue or due — they gate the rest.`,
        moveText: `Clear the ACT NOW lane (${nActNow} item(s)).`, moveKey: null };
    }
    // 6. steady-yellow with a queue
    if (health === "yellow" && awaitingCount > 0) {
      return { level: "warn",
        headline: `Team steady-yellow · ${awaitingCount} call(s) waiting on you. No fire, but the queue is yours to clear.`,
        moveText: `Clear the ACT NOW lane (${nActNow} item(s)).`, moveKey: null };
    }
    // 7. comms stalled
    if (String(comms.state || "").toLowerCase() === "stalled") {
      const sh = comms.stale_hours != null ? Math.round(comms.stale_hours) : "?";
      return { level: "warn", headline: `Comms intake stalled ${sh}h — you may be flying blind on inbound.`,
        moveText: "Re-pull comms / check the intake puller.", moveKey: null };
    }
    // 8. all clear
    return { level: "steady", headline: "All clear — runner alive, nothing red, queue manageable.",
      moveText: awaitingCount > 0 ? `Clear the ACT NOW lane (${nActNow} item(s)).` : "Nothing needs you right now.", moveKey: null };
  }

  function renderVerdict(d) {
    const sec = $("verdict");
    const v = verdictOf(d);
    sec.dataset.level = v.level;
    $("verdict-headline").textContent = v.headline;

    // first-move line — render the Run-standup affordance inline when it IS the move
    const move = $("verdict-move");
    move.innerHTML = "";
    if (v.moveKey === "run-standup") {
      move.appendChild(document.createTextNode("First move: "));
      const key = el("span", "verdict__movekey", "▶ Restart the runner");
      move.appendChild(key);
      move.appendChild(document.createTextNode(" — use “Run standup now” in the focus card below."));
    } else {
      move.appendChild(document.createTextNode("First move: "));
      move.appendChild(el("span", "verdict__movekey", v.moveText));
    }

    // compact mono counts row (the full org counts have a real home here)
    const org = (d && d.org) || {};
    const c = org.counts || {};
    const counts = $("verdict-counts");
    counts.innerHTML = "";
    const seg = [
      ["reported", c.reported], ["worked", c.worked], ["green", c.green, "c-green"],
      ["committed", c.committed], ["PRs", c.prs], ["red", c.red, "c-red"], ["yellow", c.yellow, "c-yellow"]
    ];
    const parts = seg.filter(([, val]) => val != null);
    parts.forEach(([lbl, val, cls], i) => {
      const b = el("b", cls || null, String(val));
      counts.appendChild(b);
      counts.appendChild(document.createTextNode(" " + lbl + (i < parts.length - 1 ? "   ·   " : "")));
    });
    if (!parts.length) counts.textContent = "no org counts in payload";

    renderVerdictChips(d, v);
  }

  function renderVerdictChips(d, v) {
    const ul = $("verdict-chips");
    ul.innerHTML = "";
    const r = (d && d.runner) || {};
    const comms = (d && d.comms) || {};
    const state = computeRunnerState();
    const schedOff = r.scheduler && r.scheduler.enabled === false;

    // 1 — Runner
    const onDemand = ON_DEMAND_MODE && schedOff && state !== "dead" && r.dual_runner !== true;
    const runnerDead = !onDemand && (state === "dead" || schedOff);
    ul.appendChild(makeChip({
      glyph: null, dot: true,
      label: "Runner", value: onDemand ? "on-demand" : (runnerDead ? "dead" : state),
      stateColor: onDemand ? "green" : (runnerDead ? "oxblood" : (state === "stale" ? "grey" : "green")),
      title: onDemand ? "on-demand mode — tick scheduler off, the job worker runs work you submit" : (schedOff ? "scheduler disabled — daemon off" : `runner ${state}`)
    }));

    // 2 — Comms (replaces the standalone comms card)
    const cs = String(comms.state || "unknown").toLowerCase();
    const sh = comms.stale_hours != null ? Math.round(comms.stale_hours) + "h" : "?";
    ul.appendChild(makeChip({
      glyph: "✉", dot: false,
      label: "intake " + cs, value: sh,
      stateColor: cs === "stalled" ? "oxblood" : (cs === "stale" ? "yellow" : (cs === "fresh" ? "green" : "grey")),
      title: "comms intake " + cs
    }));

    // 3 — Landing (deduped distinct commits)
    const distinct = dedupLanding(d && d.landing_queue).length;
    ul.appendChild(makeChip({
      glyph: "⇪", dot: false, label: "unpushed", value: String(distinct),
      stateColor: "cobalt", title: distinct + " distinct unpushed commit(s)",
      clickTarget: "landing-chip"
    }));

    // 4 — Awaiting (deduped) + P0 count
    const deduped = dedupAwaiting(d && d.awaiting_kain);
    const nP0 = deduped.filter((it) => normSev(it.severity) === "P0").length;
    ul.appendChild(makeChip({
      glyph: "◷", dot: false, label: "awaiting", value: `${deduped.length} · ${nP0} P0`,
      stateColor: nP0 > 0 ? "yellow" : "grey", title: `${deduped.length} awaiting · ${nP0} P0`,
      clickTarget: "board"
    }));
  }

  function makeChip({ glyph, dot, label, value, stateColor, title, clickTarget }) {
    const tag = clickTarget ? "button" : "li";
    const chip = el(tag, "chip");
    if (clickTarget) {
      chip.dataset.clickable = "1";
      chip.type = "button";
      chip.addEventListener("click", () => {
        const t = $(clickTarget);
        if (t && t.scrollIntoView) t.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    }
    chip.dataset.state = stateColor || "";
    if (title) chip.setAttribute("title", title);
    if (dot) chip.appendChild(el("span", "chip__dot"));
    else if (glyph) chip.appendChild(el("span", "chip__glyph", glyph));
    chip.appendChild(el("span", "chip__label", label));
    if (value != null) chip.appendChild(el("span", "chip__val mono", value));
    // when chip is a <button> (li expected by aria-list) wrap in li for valid markup
    if (clickTarget) { const liw = el("li"); liw.appendChild(chip); return liw; }
    return chip;
  }

  // ─────────────── HERO (exactly ONE element at display size) ───────────────
  function renderHero(d) {
    const hero = $("hero");
    hero.innerHTML = "";
    const r = (d && d.runner) || {};
    const state = computeRunnerState();
    const schedOff = r.scheduler && r.scheduler.enabled === false;
    // ON-DEMAND mode (MVP): scheduler-off is intentional — render the calm DECISION hero
    // (which surfaces the awaiting-approval queue, the MVP's real primary loop), not the
    // red runner alarm. A genuine dead/stale-heartbeat or dual-runner still alarms.
    const onDemand = ON_DEMAND_MODE && schedOff && state !== "dead" && r.dual_runner !== true;
    const isAlarm = !onDemand && (state === "dead" || state === "stale" || schedOff || r.dual_runner === true);

    if (isAlarm) {
      hero.dataset.mode = "runner";
      hero.appendChild(buildHeroAlarm(d, state, schedOff));
    } else {
      hero.dataset.mode = "decision";
      hero.appendChild(buildHeroDecision(d));
    }
    // O5: (re)bind the promoted Run-standup button to the SAME Actions controller +
    // single-flight guard as the footer button (it is recreated on each render).
    Actions.bindHero();
  }

  function buildHeroAlarm(d, state, schedOff) {
    const r = (d && d.runner) || {};
    const dead = state === "dead" || schedOff;
    const card = el("div", "hero-alarm");
    card.dataset.state = dead ? "dead" : "stale";

    const main = el("div", "hero-alarm__main");
    main.appendChild(el("p", "hero-alarm__eyebrow", dead ? "Runner alarm" : "Runner unconfirmed"));

    const word = el("p", "hero-alarm__word");
    word.appendChild(el("span", "hero-alarm__dot"));
    word.appendChild(document.createTextNode(dead ? "RUNNER DOWN" : "RUNNER UNCONFIRMED"));
    main.appendChild(word);

    // mono facts sub-line
    const lt = r.last_tick || {};
    const hbStr = humanizeSecs(r.heartbeat_age_s);
    const sub = el("p", "hero-alarm__sub");
    sub.appendChild(document.createTextNode(schedOff ? "scheduler off · " : ""));
    sub.appendChild(document.createTextNode("no heartbeat for "));
    sub.appendChild(el("span", "mono", hbStr));
    if (lt.name || lt.at) {
      sub.appendChild(document.createTextNode(" · last tick "));
      sub.appendChild(el("span", "mono", `${lt.name || "?"} ${fmtClock(lt.at)}`));
    }
    main.appendChild(sub);

    // next scheduled tick — but nothing will fire it (the daemon-off truth)
    const nt = r.next_tick || {};
    if (nt.name || nt.at) {
      const next = el("p", "hero-alarm__next");
      next.appendChild(document.createTextNode("next: "));
      next.appendChild(el("span", "mono", `${nt.name || "?"} ${fmtClock(nt.at)}`));
      if (schedOff) {
        next.appendChild(document.createTextNode(" — "));
        next.appendChild(el("em", null, "but nothing will fire it"));
      }
      main.appendChild(next);
    }
    card.appendChild(main);

    // adjacent PRIMARY action — promoted Run-standup. Same data-action + controller.
    const actionCol = el("div", "hero-alarm__action");
    const btn = el("button", "btn--hero-action");
    btn.type = "button";
    btn.id = "hero-run-standup";
    btn.dataset.action = "run-standup";
    btn.setAttribute("aria-haspopup", "dialog");
    btn.appendChild(el("span", "btn__glyph", "▶"));
    btn.appendChild(el("span", "btn__label", "Run standup now"));
    actionCol.appendChild(btn);
    actionCol.appendChild(el("p", "hero-alarm__actionnote", "drops a request the runner drains · single-flight guarded"));
    card.appendChild(actionCol);
    return card;
  }

  function buildHeroDecision(d) {
    const deduped = dedupAwaiting(d && d.awaiting_kain);
    if (!deduped.length) {
      const calm = el("div", "hero-clear");
      calm.appendChild(el("p", "hero-clear__word", "Nothing waiting on you — queue clear"));
      calm.appendChild(el("p", "hero-clear__sub", "The runner is alive and no decision is pending. Off you go."));
      return calm;
    }
    const top = deduped.slice().sort(decisionCmp)[0];
    const card = el("div", "hero-decision");
    card.dataset.sevcolor = sevColor(top.severity);
    card.appendChild(el("p", "hero-decision__eyebrow", "Your first decision"));
    card.appendChild(el("p", "hero-decision__title", esc(top.title)));
    if (top.leverage) card.appendChild(el("p", "hero-decision__lev", esc(top.leverage)));

    const foot = el("div", "hero-decision__foot");
    const chip = el("span", "hero-decision__sevchip", decisionChipText(top));
    foot.appendChild(chip);
    const scroll = el("button", "hero-decision__scroll", "Decide on the board ↓");
    scroll.type = "button";
    scroll.addEventListener("click", () => { const b = $("board"); if (b) b.scrollIntoView({ behavior: "smooth", block: "start" }); });
    foot.appendChild(scroll);
    card.appendChild(foot);
    return card;
  }

  // mono severity/overdue chip text shared by hero + board rows.
  // FIX: negatives render "Nd overdue", never the old nonsense "-32d left".
  function decisionChipText(it) {
    const p = normSev(it.severity);
    const dr = it.days_remaining;
    if (dr == null) return p;
    if (dr >= 180) return p;                      // noise: no date label
    if (dr < 0) return `${p} · ${-dr}d overdue`;
    if (dr === 0) return `${p} · due today`;
    if (dr <= 7) return `${p} · ${dr}d left`;
    return p;
  }

  // ─────────────── DECISIONS BOARD (dedup → sort → lane → top-N + collapse) ───────────────
  function renderDecisions(rawItems) {
    const wrap = $("board-lanes");
    wrap.innerHTML = "";
    const deduped = dedupAwaiting(rawItems);

    if (!deduped.length) {
      wrap.appendChild(el("div", "empty empty--good", "Nothing waiting on you — the queue is clear."));
      return;
    }

    const sorted = deduped.slice().sort(decisionCmp);
    const lanes = { act: [], when: [], fyi: [] };
    sorted.forEach((it) => lanes[decisionLane(it)].push(it));

    const LANE_META = [
      ["act", "ACT NOW", 7],     // O6: above-fold lane shows top-7
      ["when", "WHEN YOU CAN", 5],
      ["fyi", "FYI", 5]
    ];
    LANE_META.forEach(([key, name, topN]) => {
      const items = lanes[key];
      if (!items.length) return;
      wrap.appendChild(buildLane(key, name, items, topN));
    });
  }

  function buildLane(key, name, items, topN) {
    const lane = el("div", "lane");
    lane.dataset.lane = key;
    lane.dataset.open = "0";

    const head = el("p", "lane__head");
    head.appendChild(el("span", "lane__name", name));
    head.appendChild(el("span", "lane__count", "· " + items.length));
    lane.appendChild(head);

    const list = el("ul", "lane__list");
    const head_items = items.slice(0, topN);
    const tail_items = items.slice(topN);
    head_items.forEach((it) => list.appendChild(buildDecisionRow(it)));
    lane.appendChild(list);

    if (tail_items.length) {
      const moreBtn = el("button", "lane__more");
      moreBtn.type = "button";
      moreBtn.setAttribute("aria-expanded", "false");
      const tailWrap = el("div", "lane__tail");
      const tailList = el("ul", "lane__list");
      tail_items.forEach((it) => tailList.appendChild(buildDecisionRow(it)));
      tailWrap.appendChild(tailList);
      const tailId = `lane-tail-${key}`;
      tailWrap.id = tailId;
      moreBtn.setAttribute("aria-controls", tailId);
      moreBtn.appendChild(el("span", "lane__more-caret", "▶"));
      const moreLabel = el("span", null, `+${tail_items.length} more in ${name}`);
      moreBtn.appendChild(moreLabel);
      moreBtn.addEventListener("click", () => {
        const open = lane.dataset.open === "1";
        lane.dataset.open = open ? "0" : "1";
        moreBtn.setAttribute("aria-expanded", open ? "false" : "true");
        moreLabel.textContent = open ? `+${tail_items.length} more in ${name}` : `show fewer in ${name}`;
      });
      lane.appendChild(moreBtn);
      lane.appendChild(tailWrap);
    }
    return lane;
  }

  function buildDecisionRow(it) {
    const li = el("li", "decision");
    li.dataset.sev = normSev(it.severity);
    li.dataset.sevcolor = sevColor(it.severity);
    const dr = it.days_remaining;
    const overdue = dr != null && dr < 0 && dr > -180;
    if (overdue) li.dataset.overdue = "1";
    li.tabIndex = 0;
    li.setAttribute("title", esc(it.title) + (it.leverage ? "  —  unblocks: " + esc(it.leverage) : ""));

    li.appendChild(el("span", "decision__edge"));   // grid spacer (visual edge = border-left)

    // state glyph: ▲ overdue · ● dated-soon · · undated (redundant with the edge color)
    let glyph = "·";
    if (overdue) glyph = "▲";
    else if (dr != null && dr >= 0 && dr <= 7) glyph = "●";
    li.appendChild(el("span", "decision__glyph", glyph));

    li.appendChild(el("span", "decision__title", esc(it.title)));

    // unblocks-metric (right-aligned, promoted). null leverage → empty.
    const metric = el("span", "decision__metric");
    if (it.leverage) {
      metric.appendChild(el("span", "lev-key", "unblocks · "));
      metric.appendChild(document.createTextNode(esc(it.leverage)));
    }
    li.appendChild(metric);

    // severity/overdue chip (far right)
    const chip = el("span", "decision__chip");
    const p = normSev(it.severity);
    if (dr != null && dr < 0 && dr > -180) {
      chip.appendChild(document.createTextNode(p + " · "));
      chip.appendChild(el("span", "od", `${-dr}d overdue`));
    } else {
      chip.textContent = decisionChipText(it);
    }
    chip.title = "severity " + p;
    li.appendChild(chip);
    return li;
  }

  function renderSquads(squads) {
    squads = squads || [];
    const grid = $("squads-grid");
    grid.innerHTML = "";

    if (squads.length === 0) {
      grid.appendChild(el("div", "empty", "No squad data in this payload."));
      return;
    }

    squads.forEach(sq => {
      const card = el("article", "squad");
      const head = el("div", "squad__head");
      head.appendChild(el("h3", "squad__name", esc(sq.name || sq.id)));
      head.appendChild(healthPill(sq.health));   // squad-level health IS real — keep it
      card.appendChild(head);

      const ul = el("ul", "devs");
      (sq.devs || []).forEach(dev => {
        // dev.stale = backend couldn't READ this dev's progress file ("blind").
        const blind = dev.stale === true;
        const status = deriveDevStatus(dev);       // DERIVED (health is null for all devs)
        const meta = STATUS_META[status] || STATUS_META.idle;
        const li = el("li", "dev");
        if (blind) li.dataset.vis = "blind";

        const top = el("div", "dev__top");
        // neutral health glyph: hollow ring ◌ = "no health reported" (health===null).
        // (When a real health color ever arrives, this would carry it; today it's null.)
        const hg = el("span", "dev__glyph", normHealth(dev.health) === "grey" ? "◌" : "●");
        if (normHealth(dev.health) !== "grey") hg.style.color = `var(--${normHealth(dev.health)})`;
        top.appendChild(hg);
        if (dev.id) top.appendChild(el("span", "dev__id mono", esc(dev.id)));
        // branch chip (mono) when non-null
        if (dev.branch) {
          const br = el("span", "dev__branch mono", esc(dev.branch));
          br.title = "branch " + esc(dev.branch);
          top.appendChild(br);
        }
        top.appendChild(el("span", "dev__role", esc(dev.role || dev.id)));
        // DERIVED status chip (primary right-aligned label; words + glyph + color)
        const st = el("span", "dev__status");
        st.dataset.status = status;
        st.appendChild(el("span", "dev__status-glyph", meta.glyph));
        st.appendChild(document.createTextNode(meta.label));
        top.appendChild(st);
        li.appendChild(top);

        // pair (⇄ lanemate) — from /api/status if present, else merged /api/team.
        const pair = dev.pair || S.pairOf[dev.id];
        if (pair) {
          const pr = el("div", "dev__pair");
          pr.appendChild(document.createTextNode("⇄ pair "));
          pr.appendChild(el("span", "mono", esc(pair)));
          li.appendChild(pr);
        }

        // literal current_task ALWAYS visible (so a wrong derived label is never the
        // only signal — Kain can always read the truth under the chip).
        const task = dev.current_task && String(dev.current_task).trim();
        li.appendChild(el("div", "dev__task" + (task ? "" : " dev__task--idle"),
          blind ? "portal can't read this dev's progress file (last-known)"
                : (task ? task : "idle — no current task")));

        // next_step behind a per-dev disclosure ("next ▸"); full text in --ink-3.
        const nxt = dev.next_step && String(dev.next_step).trim();
        if (nxt && !blind) {
          const btn = el("button", "dev__disclosure");
          btn.type = "button";
          btn.setAttribute("aria-expanded", "false");
          btn.appendChild(el("span", "dev__disclosure-caret", "▸"));
          btn.appendChild(document.createTextNode("next step"));
          const panel = el("div", "dev__next", nxt);
          panel.hidden = true;
          const pid = `dev-next-${dev.id || Math.random().toString(36).slice(2, 7)}`;
          panel.id = pid;
          btn.setAttribute("aria-controls", pid);
          btn.addEventListener("click", () => {
            const open = btn.getAttribute("aria-expanded") === "true";
            btn.setAttribute("aria-expanded", open ? "false" : "true");
            panel.hidden = open;
          });
          li.appendChild(btn);
          li.appendChild(panel);
        }

        // "as of <date>" / stale treatment from the last progress-file entry.
        const asOf = dev.last_entry_date || (dev.last_entry && dev.last_entry.date);
        if (asOf || blind) {
          const stale = blind || isStaleDate(asOf);
          const a = el("div", "dev__asof", blind ? "last-known — no fresh signal" : `as of ${asOf}`);
          a.dataset.stale = stale ? "1" : "0";
          li.appendChild(a);
        }

        ul.appendChild(li);
      });
      card.appendChild(ul);

      // ── per-squad quick reviews: "review THIS project on demand" in one click.
      // Target the squad's single project folder if it has exactly one (project:…),
      // else the squad itself (squad:<id> — the API resolves it, prompt scopes to
      // "the <name> squad"). Fires a trigger-review job via the Jobs controller.
      const folders = Array.from(new Set((sq.devs || [])
        .map(dv => dv && dv.folder).filter(Boolean)));
      const reviewTarget = folders.length === 1 ? `project:${folders[0]}` : `squad:${sq.id}`;
      const reviews = el("div", "squad__reviews");
      const mk = (kind, label) => {
        const b = el("button", "squad__review-btn");
        b.type = "button";
        b.dataset.kind = kind;
        b.setAttribute("aria-label", `Trigger ${label} of ${reviewTarget}`);
        b.title = `Trigger a ${label} job for ${reviewTarget}`;
        b.appendChild(el("span", "squad__review-glyph", "▸"));
        b.appendChild(document.createTextNode(label));
        b.addEventListener("click", () => {
          if (typeof Jobs !== "undefined" && Jobs.quickReview) Jobs.quickReview(reviewTarget, kind, b);
        });
        return b;
      };
      reviews.appendChild(mk("pm", "PM review"));
      reviews.appendChild(mk("ux", "UX review"));
      card.appendChild(reviews);

      grid.appendChild(card);
    });
  }

  function healthPill(health) {
    const h = normHealth(health);
    const pill = el("span", "pill");
    pill.dataset.health = h;
    pill.appendChild(el("span", "pill__dot"));
    pill.appendChild(document.createTextNode(h));
    return pill;
  }

  // Staff = PROPER cards (2-up, like the squad cards), NOT a cramped text grid.
  // The long real roles WRAP (CSS: overflow-wrap:anywhere + min-width:0), which is
  // what fixes the P0 break + the mobile horizontal scroll. comms_triage renders
  // as a dedicated comms-triage card with 3 labelled streams.
  function renderStaff(staff, comms) {
    staff = staff || [];
    const ul = $("staff-list");
    ul.innerHTML = "";
    if (staff.length === 0) {
      ul.appendChild(el("li", "staff__item", "—"));
      return;
    }
    staff.forEach(s => {
      const li = el("li", "staff__item");

      const head = el("div", "staff__head");
      if (s.id) head.appendChild(el("span", "staff__id", esc(s.id)));
      head.appendChild(el("span", "staff__role", esc(s.role || s.id)));
      li.appendChild(head);

      // comms_triage is ONE agent with THREE streams (message/email/meeting).
      if (s.id === "comms_triage") {
        li.appendChild(renderCommsStreams(comms));
      }

      if (s.note) li.appendChild(el("p", "staff__note", esc(s.note)));
      ul.appendChild(li);
    });
  }

  // 3 labelled sub-rows — MESSAGE (Teams) / EMAIL (Outlook mail) / MEETING
  // (calendar) — each: count + per-stream freshness pill + signed_in. Consumes
  // comms.streams[] when the backend emits it; degrades to a single aggregate row
  // built from comms.{state,stale_hours} when streams[] is absent.
  const STREAM_DEFAULTS = [
    { id: "message", label: "Message", source: "Teams" },
    { id: "email",   label: "Email",   source: "Outlook mail" },
    { id: "meeting", label: "Meeting", source: "Calendar" }
  ];
  function renderCommsStreams(comms) {
    comms = comms || {};
    const ul = el("ul", "staff__streams");
    const streams = Array.isArray(comms.streams) && comms.streams.length
      ? comms.streams
      : null;

    if (!streams) {
      // backend hasn't emitted streams[] yet — show the aggregate as one row so the
      // card still reads as a comms surface (no blank), labelled "intake".
      const row = streamRow({
        id: "intake", label: "Intake", source: comms.newest_file || "Teams + Outlook",
        count: null, state: comms.state, stale_hours: comms.stale_hours,
        signed_in: null, last_pull_at: comms.last_pull_at
      });
      ul.appendChild(row);
      const note = el("li", "stream__signin");
      note.dataset.in = "yes";
      note.textContent = "per-stream split coming from backend (comms.streams[])";
      ul.appendChild(note);
      return ul;
    }

    // index incoming by KIND (the canonical backend key — parsers/comms.py emits
    // {"kind": "message"|"email"|"meeting"}) so defaults fill any missing stream.
    // STREAM_DEFAULTS[].id equals those kind values, so the byId[def.id] lookup hits.
    const byId = {};
    streams.forEach(s => { if (s && s.kind) byId[String(s.kind).toLowerCase()] = s; });
    STREAM_DEFAULTS.forEach(def => {
      const s = byId[def.id] || {};
      ul.appendChild(streamRow({
        id: def.id,
        label: s.label || def.label,
        source: s.source || def.source,
        count: s.count,
        state: s.state,
        stale_hours: s.stale_hours,
        signed_in: s.signed_in,
        last_pull_at: s.last_pull_at
      }));
    });
    return ul;
  }

  const STREAM_ICON = { message: "💬", email: "✉", meeting: "📅", intake: "📥" };
  function streamRow(s) {
    const li = el("li", "stream");
    li.dataset.stream = s.id;

    li.appendChild(el("span", "stream__icon", STREAM_ICON[s.id] || "•"));

    const chan = el("div", "stream__chan");
    chan.appendChild(el("span", "stream__label", esc(s.label)));
    chan.appendChild(el("span", "stream__src", esc(s.source || "")));
    li.appendChild(chan);

    li.appendChild(el("span", "stream__count mono", s.count == null ? "—" : String(s.count)));

    const st = String(s.state || "unknown").toLowerCase();
    const fresh = el("span", "stream__fresh", st);
    fresh.dataset.state = ["fresh", "stale", "stalled", "signedout", "missing"].includes(st) ? st : "";
    fresh.title = s.stale_hours != null ? `stale ${s.stale_hours}h` : "freshness unknown";
    li.appendChild(fresh);

    // signed_in line (only when we actually know) — full-width sub-row
    if (s.signed_in === true || s.signed_in === false) {
      const sign = el("span", "stream__signin", s.signed_in ? "signed in" : "signed out — re-auth needed");
      sign.dataset.in = s.signed_in ? "yes" : "no";
      li.appendChild(sign);
    }
    return li;
  }

  // Bench = collapsed "N inactive" strip (id/role/folder, dimmed). Source: the
  // /api/team bench[] (merged) or the embedded MOCK_TEAM. This is what makes EVERY
  // id in team.json appear on the page (squad devs + staff + bench).
  function renderBench() {
    const wrap = $("bench");
    if (!wrap) return;
    const bench = (S.team && S.team.bench) || (S.usingMock ? MOCK_TEAM.bench : []) || [];
    const list = $("bench-list");
    const label = $("bench-label");
    list.innerHTML = "";
    if (!bench.length) {
      wrap.hidden = true;
      return;
    }
    wrap.hidden = false;
    label.textContent = `Bench (${bench.length} inactive)`;
    bench.forEach(b => {
      const li = el("li", "bench__item");
      li.appendChild(el("span", "bench__id mono", esc(b.id)));
      if (b.role) li.appendChild(el("span", "bench__role", esc(b.role)));
      if (b.folder) li.appendChild(el("span", "bench__folder", esc(b.folder)));
      list.appendChild(li);
    });
  }

  // Dedup landing_queue by commit (the only stable field; branch is null). Keep first
  // occurrence, count repeats → ×N multiplier. Returns [{commit, status, count}].
  function dedupLanding(items) {
    const map = new Map();
    const order = [];
    (items || []).forEach((it) => {
      if (!it) return;
      const c = String(it.commit || "").trim();
      if (!c) return;
      if (!map.has(c)) { map.set(c, { commit: c, status: it.status || "committed-unpushed", count: 1 }); order.push(c); }
      else map.get(c).count += 1;
    });
    return order.map((c) => map.get(c));
  }

  // Footer system meta: last-tick one-liner + landing-queue chip (deduped, ×N).
  function renderSysMeta(t, landing) {
    t = t || {};
    const line = $("lasttick-line");
    line.innerHTML = "";
    if (!t.id && !t.name && !t.at) {
      line.textContent = "Last tick — no recorded run.";
    } else {
      line.appendChild(document.createTextNode("Last tick · "));
      line.appendChild(el("span", "lt-name", t.name || "?"));
      line.appendChild(document.createTextNode(" · "));
      line.appendChild(el("span", null, t.id || "—"));
      if (t.at) {
        line.appendChild(document.createTextNode(" · "));
        line.appendChild(el("span", "lt-dim", `${fmtClock(t.at)} (${fmtAge(Date.now() - new Date(t.at))})`));
      }
      // worked/green/committed/prs are null in this contract → omit nulls (as before).
      const extra = [];
      if (t.agents != null) extra.push(`${t.agents} agents`);
      if (t.worked != null) extra.push(`${t.worked} worked`);
      if (t.green != null) extra.push(`${t.green} green`);
      if (t.committed != null) extra.push(`${t.committed} committed`);
      if (t.prs != null) extra.push(`${t.prs} PRs`);
      if (t.duration_min != null) extra.push(fmtDur(t.duration_min));
      if (extra.length) {
        line.appendChild(document.createTextNode(" · "));
        line.appendChild(el("span", "lt-dim", extra.join(" · ")));
      }
    }

    // landing chip + disclosure
    const deduped = dedupLanding(landing);
    const distinct = deduped.length;
    const label = $("landing-label");
    label.textContent = distinct
      ? `${distinct} unpushed commit${distinct === 1 ? "" : "s"}`
      : "0 unpushed";
    const ul = $("landing-list");
    ul.innerHTML = "";
    if (!distinct) {
      ul.appendChild(el("li", "empty", "Nothing waiting to land."));
      return;
    }
    deduped.forEach((it) => {
      const li = el("li", "landing__item");
      const left = el("span", null);
      left.style.display = "inline-flex"; left.style.alignItems = "baseline"; left.style.gap = "7px"; left.style.minWidth = "0";
      left.appendChild(el("span", "landing__commit mono", esc(it.commit)));
      if (it.count > 1) left.appendChild(el("span", "landing__mult mono", "×" + it.count));
      li.appendChild(left);
      li.appendChild(el("span", "landing__status", esc(it.status)));
      ul.appendChild(li);
    });
  }

  // Surface backend `degraded` (a parse fell back to last-known data, e.g. today's
  // log missing -> used yesterday's). This is a DATA-freshness warning, distinct
  // from the connection banner (which is about reaching the API at all). We only
  // raise it when the connection is otherwise fine, so the two never fight.
  function renderDegradedBanner(d) {
    const b = $("degraded-banner");
    if (!b) return;
    const degraded = d && d.degraded === true && !S.usingMock;
    if (!degraded) { b.hidden = true; return; }
    const warns = (d.warnings || []).filter(Boolean);
    const detail = warns.length ? warns[0] : "some data fell back to last-known";
    b.hidden = false;
    // O4 CRY-WOLF FIX: the kind was never set (and index.html hardcoded
    // data-kind="warn"), so this banner was PERMANENTLY yellow. Derive the kind from
    // the actual warning severity so it reads as the real condition, not a constant
    // amber. Heuristic over the warning copy: error words → error(oxblood),
    // can't-read/blind words → blind(grey), otherwise the default fell-back → warn.
    const blob = (warns.join(" ") + " " + esc(d.shown_log_date)).toLowerCase();
    let kind = "warn";
    if (/\b(error|exception|traceback|crash|failed|unreadable|corrupt)\b/.test(blob)) kind = "error";
    else if (/\b(can't read|cannot read|blind|missing|no log|not found|unparse)\b/.test(blob)) kind = "blind";
    b.dataset.kind = kind;
    // Lead with the actual DATE of the stale snapshot we're showing (from the
    // backend's already-emitted shown_log_date). Null-safe fallback to date-less copy.
    const shownDate = fmtLogDate(d.shown_log_date);
    b.textContent = shownDate
      ? `Showing data from ${shownDate} (last-known) — ${detail}`
      : `Showing last-known — ${detail}`;
  }

  // ───────────────────────── 1Hz tick: freshness + liveness re-eval ─────────────────────────
  let _lastRunnerState = null;
  function tick() {
    // freshness indicator (updated Xs ago)
    if (S.lastStatusOk) {
      $("freshness-age").textContent = fmtAge(Date.now() - S.lastStatusOk);
    } else if (S.usingMock) {
      $("freshness-age").textContent = "sample";
    }

    // Re-evaluate runner liveness vs heartbeat staleness every second. The runner
    // facts (heartbeat age, next-tick) live in the hero ALARM card now, so when the
    // hero is in alarm mode we re-render it so those mono facts stay fresh; and if the
    // derived state flips (alive↔stale↔dead) we re-render the verdict too.
    if (S.data) {
      const state = computeRunnerState();
      const hero = $("hero");
      if (hero && hero.dataset.mode === "runner") renderHero(S.data);
      if (state !== _lastRunnerState) { renderVerdict(S.data); renderHero(S.data); _lastRunnerState = state; }
    }

    // if status polling has gone silent, escalate to a blind banner
    if (S.lastStatusOk && (Date.now() - S.lastStatusOk) > BLIND_AFTER_MS && document.body.dataset.conn !== "stale") {
      setConnBanner("blind", `Portal can't reach the system — showing last-known from ${fmtAge(Date.now() - S.lastStatusOk)}. Not a team red; the portal lost sight of the runner.`);
    }
  }

  // ════════════════════════════════════════════════════════════════════════
  // PHASE 2 — ACTIONS controller
  // State machine per action button:
  //   idle (last ran HH:MM · next tick in Xh)
  //     → confirm   (consequence-stating dialog, focus-trapped, Esc-closable)
  //     → guard-check (live single-flight re-check at confirm time)
  //        → BLOCKED  (honest double-fire dialog; no launch)
  //        → LAUNCHING → RUNNING (honest: "running since HH:MM", phase, ≈50 min)
  //            → DONE  (result summary + log link, then back to idle)
  //            → ERROR (real reason + Retry)
  // Single-flight is the critical invariant: BEFORE the confirm we check live
  // state; while busy BOTH buttons are locked with the reason shown; a dual-runner
  // flag shows a full-width red override and disables all launches.
  // ════════════════════════════════════════════════════════════════════════
  const Actions = (() => {
    const IMMINENT_S = 10 * 60;          // a tick < 10 min away blocks a launch
    const POLL_MS = 1500;                // lifecycle poll cadence after a POST
    const ETA_MIN = 50;                  // honest typical duration

    const LABEL = { "run-standup": "Run standup", "pm-review": "PM review" };

    // last-known live single-flight signals (merged from /api/status + /api/heartbeat)
    let live = { busy: null, dual_runner: false, in_flight: null, next_tick: null,
                 last_tick: null, scheduler: null };
    let activeId = null;                 // id of the action this portal launched
    let pollTimer = null;
    let lastFocus = null;                // element to restore focus to on dialog close
    let dialogKeydown = null;            // bound trap handler (for removal)

    // ---- elements (resolved at init) ----
    let E = {};
    function grab() {
      E = {
        cardLock:   $("actions-lock"),
        dualBanner: $("dual-runner-banner"),
        context:    $("actions-context"),
        btnRun:     $("act-run-standup"),
        btnPm:      $("act-pm-review"),
        live:       $("action-live"),
        liveDot:    $("action-live-dot"),
        liveTitle:  $("action-live-title"),
        liveDetail: $("action-live-detail"),
        liveResult: $("action-live-result"),
        liveLog:    $("action-live-log"),
        liveRetry:  $("action-live-retry"),
        liveDismiss:$("action-live-dismiss"),
        backdrop:   $("action-dialog-backdrop"),
        dialog:     $("action-dialog"),
        dlgKicker:  $("dialog-kicker"),
        dlgTitle:   $("dialog-title"),
        dlgBody:    $("dialog-body"),
        dlgCancel:  $("dialog-cancel"),
        dlgConfirm: $("dialog-confirm"),
      };
    }

    // ───── live-state ingestion ─────
    function onStatus(d) {
      if (!d || !d.runner) return;
      onLive(d.runner);
    }
    function onLive(r) {
      if (!r) return;
      // r may be a runner block (/api/status) or a heartbeat (/api/heartbeat).
      if ("busy" in r) live.busy = r.busy;
      if ("dual_runner" in r) live.dual_runner = !!r.dual_runner;
      if ("in_flight" in r) live.in_flight = r.in_flight || null;
      if (r.next_tick) live.next_tick = r.next_tick;
      if (r.last_tick) live.last_tick = r.last_tick;
      if (r.scheduler) live.scheduler = r.scheduler;
      paintLock();
      refreshContext();
    }

    // ───── guard: is a launch safe RIGHT NOW? ─────
    // Mirrors the backend guard so the UI can BLOCK before confirm (and lock the
    // buttons continuously). Returns {ok, code, reason, kind:"hard"|"soft"}.
    function guard() {
      if (live.dual_runner === true) {
        return { ok: false, code: "dual_runner", kind: "hard",
          reason: "Dual runner detected — two runners are live. All launches are disabled until one is killed." };
      }
      const mine = live.in_flight;
      if (mine && (mine.state === "pending" || mine.state === "running")) {
        const wf = mine.run_id || mine.id || "wf_…";
        const ago = startedAgoMin(mine.started_at || mine.created_at);
        return { ok: false, code: "in_flight", kind: "hard",
          reason: `Can't run — a ${LABEL[mine.kind] || "run"} is already in flight (${wf}${ago != null ? `, started ${ago}m ago` : ""}); a 2nd would double-fire: duplicate commits, racing deploys, 2× spend.` };
      }
      if (live.busy === true) {
        const wf = (live.last_tick && live.last_tick.id) || "wf_…";
        return { ok: false, code: "busy", kind: "hard",
          reason: `Can't run — a tick is already running (${wf}); a 2nd would double-fire: duplicate commits, racing deploys, 2× spend.` };
      }
      const inS = nextTickInSeconds();
      if (inS != null && inS >= 0 && inS < IMMINENT_S) {
        const m = Math.max(0, Math.floor(inS / 60));
        const nm = (live.next_tick && live.next_tick.name) || "next";
        return { ok: false, code: "tick_imminent", kind: "soft",
          reason: `Can't run — the scheduled ${nm} tick fires in ~${m}m (${fmtClock(live.next_tick && live.next_tick.at)}). Launching now races it and double-fires. Wait for the tick.` };
      }
      return { ok: true };
    }

    function nextTickInSeconds() {
      const nt = live.next_tick;
      if (!nt) return null;
      if (nt.at) {
        const ms = new Date(nt.at) - Date.now();
        if (!isNaN(ms)) return Math.round(ms / 1000);
      }
      if (nt.in_seconds != null) return nt.in_seconds;
      return null;
    }
    function startedAgoMin(iso) {
      if (!iso) return null;
      const ms = Date.now() - new Date(iso);
      if (isNaN(ms)) return null;
      return Math.max(0, Math.floor(ms / 60000));
    }
    function nextTickPhrase() {
      const inS = nextTickInSeconds();
      const nm = (live.next_tick && live.next_tick.name) || "next";
      const schedOff = live.scheduler && live.scheduler.enabled === false;
      const when = fmtClock(live.next_tick && live.next_tick.at);
      // When the scheduler is OFF (daemon dead), the "next tick" time still exists on
      // paper but NOTHING will fire it — so a "(in -Nm)" / "(in Nm)" countdown is a lie
      // that contradicts the dead-runner verdict. Echo the time with the hero's honest
      // phrasing and NO countdown. Same when the tick is already in the past (negative).
      if (schedOff) {
        return when !== "—"
          ? `scheduled ${nm} tick was ${when} — but nothing will fire it`
          : `scheduler off — nothing will fire the ${nm} tick`;
      }
      if (inS == null) return `next ${nm} tick time unknown`;
      if (inS < 0) return `next scheduled tick is ${when}`;   // never render a negative "in -Nm"
      const h = Math.floor(inS / 3600), m = Math.floor((inS % 3600) / 60);
      const rel = h > 0 ? `in ${h}h${m ? " " + m + "m" : ""}` : `in ${m}m`;
      return `next scheduled tick is ${when} (${rel})`;
    }
    function lastRanPhrase() {
      const lt = live.last_tick;
      if (!lt || !lt.at) return "no recorded last run";
      return `last ran ${fmtClock(lt.at)}${lt.name ? " (" + lt.name + ")" : ""}`;
    }

    // ───── continuous button lockout (single-flight) ─────
    function paintLock() {
      const g = guard();
      const busyMine = activeId != null;     // an action WE launched is live
      const locked = !g.ok || busyMine;

      // dual-runner override banner (full width)
      if (live.dual_runner === true) {
        E.dualBanner.hidden = false;
        E.dualBanner.textContent = "⚠ DUAL RUNNER DETECTED — two runner processes are live at once. All launches are disabled to avoid compounding the split-brain. Kill one runner.";
      } else {
        E.dualBanner.hidden = true;
      }

      // O5: the hero-promoted Run-standup button shares this SAME single-flight
      // guard — it is disabled by the same `locked` condition as the footer buttons,
      // so both disable together while a run is in-flight. It is resolved fresh each
      // call (the hero re-renders on each status/heartbeat/tick).
      const heroBtn = $("hero-run-standup");
      [E.btnRun, E.btnPm, heroBtn].forEach(btn => {
        if (!btn) return;
        btn.disabled = locked;
        btn.setAttribute("aria-disabled", locked ? "true" : "false");
      });

      // the WHY (disabled buttons must say why)
      let reason = null, kind = "soft";
      if (busyMine && g.ok) {
        const a = currentAction();
        reason = a ? `${LABEL[a.kind] || "Action"} in progress — buttons locked until it finishes (single-flight).` : "An action is in progress — buttons locked (single-flight).";
        kind = "hard";
      } else if (!g.ok) {
        reason = g.reason; kind = g.kind || "soft";
      }
      if (reason) {
        E.cardLock.hidden = false;
        E.cardLock.textContent = reason;
        E.cardLock.dataset.kind = kind;
        E.btnRun && E.btnRun.setAttribute("title", reason);
        E.btnPm && E.btnPm.setAttribute("title", reason);
        heroBtn && heroBtn.setAttribute("title", reason);
      } else {
        E.cardLock.hidden = true;
        E.btnRun && E.btnRun.removeAttribute("title");
        E.btnPm && E.btnPm.removeAttribute("title");
        heroBtn && heroBtn.removeAttribute("title");
      }
    }

    // O5: (re)bind the hero-promoted Run-standup button to the SAME controller path
    // (onButtonClick → confirm/blocked/single-flight). Called after each renderHero,
    // since the button node is recreated. A dataset flag prevents double-binding.
    function bindHero() {
      const heroBtn = $("hero-run-standup");
      if (heroBtn && !heroBtn.dataset.bound) {
        heroBtn.dataset.bound = "1";
        heroBtn.addEventListener("click", () => onButtonClick("run-standup"));
      }
      paintLock();   // apply the current lock state to the freshly-rendered button
    }

    function refreshContext() {
      if (activeId != null) return;        // live panel owns the copy while running
      const schedOff = live.scheduler && live.scheduler.enabled === false;
      // The "scheduled tick still runs" reassurance is only true when the scheduler is
      // ON. When it's OFF, nextTickPhrase() already says nothing will fire it — don't
      // append a clause that contradicts the dead-runner verdict.
      const tail = schedOff ? "" : " Off-cadence — the scheduled tick still runs.";
      E.context.textContent = `${lastRanPhrase()} · ${nextTickPhrase()}.${tail}`;
    }

    // ───── dialog: focus-trap + Esc + keyboard ─────
    function focusables() {
      return [E.dlgCancel, E.dlgConfirm].filter(b => b && !b.disabled && !b.hidden);
    }
    function openDialog({ variant, kicker, title, bodyNodes, confirmText, onConfirm, confirmDanger }) {
      lastFocus = document.activeElement;
      E.dialog.dataset.variant = variant;
      E.dlgKicker.textContent = kicker;
      E.dlgTitle.textContent = title;
      E.dlgBody.innerHTML = "";
      bodyNodes.forEach(n => E.dlgBody.appendChild(n));

      if (onConfirm) {
        E.dlgConfirm.hidden = false;
        E.dlgConfirm.textContent = confirmText || "Confirm";
        E.dlgConfirm.classList.toggle("btn--danger", confirmDanger !== false);
        E.dlgConfirm.onclick = () => { closeDialog(); onConfirm(); };
        E.dlgCancel.textContent = "Cancel";
      } else {
        // BLOCKED variant: no launch — single dismiss button.
        E.dlgConfirm.hidden = true;
        E.dlgCancel.textContent = "OK — don't run";
      }
      E.dlgCancel.onclick = () => closeDialog();

      E.backdrop.hidden = false;
      // focus the safest control: Cancel (so Enter doesn't fire a dangerous launch)
      (E.dlgCancel || E.dialog).focus();

      dialogKeydown = (ev) => {
        if (ev.key === "Escape") { ev.preventDefault(); closeDialog(); return; }
        if (ev.key === "Tab") {
          const f = focusables();
          if (!f.length) { ev.preventDefault(); return; }
          const first = f[0], last = f[f.length - 1];
          const active = document.activeElement;
          if (ev.shiftKey && (active === first || !f.includes(active))) {
            ev.preventDefault(); last.focus();
          } else if (!ev.shiftKey && (active === last || !f.includes(active))) {
            ev.preventDefault(); first.focus();
          }
        }
      };
      document.addEventListener("keydown", dialogKeydown, true);
      // click on backdrop (not the dialog) closes
      E.backdrop.onmousedown = (ev) => { if (ev.target === E.backdrop) closeDialog(); };
    }
    function closeDialog() {
      E.backdrop.hidden = true;
      if (dialogKeydown) document.removeEventListener("keydown", dialogKeydown, true);
      dialogKeydown = null;
      if (lastFocus && lastFocus.focus) { try { lastFocus.focus(); } catch (_) {} }
    }

    // ───── click → live guard pre-check → (BLOCKED | CONFIRM) ─────
    // The spec: BEFORE the confirm, check LIVE state. We first apply the cached
    // guard (instant), then confirm it against the authoritative server guard so
    // the confirm dialog can never open over a state that already double-fires.
    async function onButtonClick(kind) {
      const cached = guard();
      if (!cached.ok) { openBlocked(cached); return; }
      // authoritative live re-check (cheap GET) — closes the race window
      const live2 = await fetchGuard();
      if (live2 && live2.ok === false) {
        openBlocked({ ok: false, code: live2.code,
          kind: live2.code === "tick_imminent" ? "soft" : "hard",
          reason: live2.reason });
        return;
      }
      openConfirm(kind);
    }

    async function fetchGuard() {
      try { return await getJSON("/api/actions/guard"); }
      catch (e) { return null; }   // network miss → fall back to the cached guard
    }

    function openBlocked(g) {
      const block = el("div", "dialog__block");
      block.appendChild(document.createTextNode(g.reason));
      const sub = el("p", "dialog__detail");
      sub.style.margin = "0";
      sub.style.fontSize = "12.5px";
      sub.style.color = "var(--ink-3)";
      sub.textContent = g.code === "dual_runner"
        ? "Resolve the split-brain first — only one runner may be live."
        : "The portal blocked this to protect the single-flight invariant — a 2nd run while one is live is the system's worst failure.";
      openDialog({
        variant: "blocked",
        kicker: "BLOCKED",
        title: g.code === "tick_imminent" ? "A scheduled tick is imminent" : "A run is already in flight",
        bodyNodes: [block, sub],
        onConfirm: null,
      });
    }

    function openConfirm(kind) {
      const label = LABEL[kind] || "Run";
      // CONFIRM states the REAL cost (consequence-stating, not "are you sure?").
      const lead = el("p");
      lead.style.margin = "0";
      lead.appendChild(document.createTextNode(`This launches a full ${label.toLowerCase()} pass off-cadence:`));

      const cost = el("p", "dialog__cost");
      cost.innerHTML =
        "<b>~30–50 min</b> · <b>~15 agents</b> · may <b>commit to branches</b> + " +
        "<b>post to the team channel</b>.";

      const sched = el("p");
      sched.style.margin = "0";
      sched.style.fontSize = "13px";
      sched.style.color = "var(--ink-2)";
      sched.appendChild(document.createTextNode(
        `The ${nextTickPhrase()} — it will run anyway. This is in addition to it, not instead of it.`
      ));

      openDialog({
        variant: "confirm",
        kicker: "CONFIRM",
        title: `${label} now?`,
        bodyNodes: [lead, cost, sched],
        confirmText: `Yes — ${label.toLowerCase()} now`,
        confirmDanger: true,
        onConfirm: () => launch(kind),
      });
    }

    // ───── launch → poll lifecycle ─────
    async function launch(kind) {
      // re-check the guard at the moment of launch (state may have changed while
      // the dialog was open) — single-flight must hold at POST time.
      const g = guard();
      if (!g.ok) { openBlocked(g); return; }

      setLive("launching", { kind, title: `Launching ${LABEL[kind] || "run"}…`,
        detail: "Dropping the request for the runner to drain." });
      paintLock();

      // a per-attempt idempotency key: if this POST somehow fires twice (a fast
      // double-click that beat the disabling), the backend treats the 2nd as a
      // no-op rather than queueing a 2nd request — belt-and-braces single-flight.
      const idemKey = `portal-${kind}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      try {
        const r = await fetch(`/api/actions/${kind}`, {
          method: "POST", cache: "no-store",
          // X-Requested-By is a custom header a cross-site simple <form> CANNOT
          // set — it is the backend's CSRF same-origin assertion (the backend also
          // accepts a 127.0.0.1/localhost Origin, so same-origin works either way).
          headers: { "Content-Type": "application/json", "X-Idempotency-Key": idemKey,
                     "X-Requested-By": "portal" },
        });
        const body = await r.json().catch(() => ({}));
        if (r.status === 409) {
          // backend's authoritative single-flight block — show it honestly.
          activeId = null;
          hideLive();
          openBlocked({ ok: false, code: body.code, kind: "hard",
            reason: body.reason || "Blocked — a run is already in flight (double-fire prevented)." });
          paintLock();
          return;
        }
        if (!r.ok || !body.id) throw new Error(body.reason || ("HTTP " + r.status));
        activeId = body.id;
        applyAction(body.action || { id: body.id, kind, state: "pending" });
        startPolling();
      } catch (e) {
        activeId = null;
        setLive("error", { kind, title: `${LABEL[kind] || "Run"} failed to launch`,
          detail: String(e && e.message || e), retry: kind });
      }
      paintLock();
    }

    function startPolling() {
      stopPolling();
      pollTimer = setInterval(pollOnce, POLL_MS);
      pollOnce();
    }
    function stopPolling() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }

    async function pollOnce() {
      if (!activeId) { stopPolling(); return; }
      try {
        const a = await getJSON(`/api/actions/${activeId}`);
        applyAction(a);
      } catch (e) {
        // a transient poll miss isn't fatal; keep the last-known running copy.
      }
    }

    let currentActionObj = null;
    function currentAction() { return currentActionObj; }

    function applyAction(a) {
      if (!a) return;
      currentActionObj = a;
      const kind = a.kind;
      if (a.state === "pending") {
        setLive("launching", { kind, title: `${LABEL[kind] || "Run"} queued`,
          detail: a.phase || "Waiting for the runner to drain the request." });
      } else if (a.state === "running") {
        const since = a.started_at ? fmtClock(a.started_at) : "now";
        const phase = a.phase ? ` · ${a.phase}` : "";
        const agents = a.agents_expected ? ` · ~${a.agents_expected} agents` : "";
        setLive("running", { kind,
          title: `${LABEL[kind] || "Run"} running since ${since}`,
          detail: `≈${a.eta_min || ETA_MIN} min typical${agents}${phase}. This is the real run — not a placeholder spinner.` });
      } else if (a.state === "done") {
        stopPolling();
        activeId = null;
        const res = a.result || {};
        // log_ref: prefer an explicit one, else derive from run_id + the day the
        // run finished so DONE always links to the new log entry (GET /api/log).
        const logRef = a.log_ref || (a.run_id ? {
          run_id: a.run_id,
          date: (a.finished_at && String(a.finished_at).slice(0, 10)) || null
        } : null);
        setLive("done", { kind, title: `${LABEL[kind] || "Run"} complete`,
          detail: a.finished_at ? `finished ${fmtClock(a.finished_at)}.` : "finished.",
          result: res.summary ||
            `worked ${res.worked ?? "—"} · green ${res.green ?? "—"} · committed ${res.committed ?? "—"} · PRs ${res.prs ?? "—"}${res.posted_botchannel ? " · posted to the team channel" : ""}`,
          log: logRef });
        // pull a fresh status so "last ran" updates, then return to idle shortly.
        if (typeof pollStatus === "function") pollStatus();
      } else if (a.state === "failed") {
        stopPolling();
        activeId = null;
        setLive("error", { kind, title: `${LABEL[kind] || "Run"} failed`,
          detail: a.error || a.note || "The runner exited non-zero. Nothing was committed or posted.",
          retry: kind });
      } else if (a.state === "expired") {
        // portal TTL safety sweep fired — the request never ran (honest copy).
        stopPolling();
        activeId = null;
        setLive("error", { kind, title: `${LABEL[kind] || "Run"} expired — never ran`,
          detail: a.note || "The request sat pending past its TTL with no live runner and was swept so it can't fire stale. Nothing ran.",
          retry: kind });
      }
      paintLock();
    }

    // ───── live panel painter ─────
    function setLive(state, { kind, title, detail, result, log, retry } = {}) {
      E.live.hidden = false;
      E.live.dataset.state = state;
      E.liveDot && (E.liveDot.dataset.state = state);
      E.liveTitle.textContent = title || "";
      E.liveDetail.textContent = detail || "";

      if (result) { E.liveResult.hidden = false; E.liveResult.textContent = result; }
      else { E.liveResult.hidden = true; E.liveResult.textContent = ""; }

      if (log && (log.date || log.run_id)) {
        E.liveLog.hidden = false;
        const q = log.date ? `?date=${encodeURIComponent(log.date)}` : "";
        E.liveLog.setAttribute("href", `/api/log${q}`);
        E.liveLog.textContent = `view log entry${log.run_id ? " · " + log.run_id : ""} →`;
      } else { E.liveLog.hidden = true; }

      if (retry) {
        E.liveRetry.hidden = false;
        E.liveRetry.onclick = () => { hideLive(); openConfirm(retry); };
      } else { E.liveRetry.hidden = true; }

      // DONE / ERROR are terminal — offer a dismiss back to idle.
      const terminal = state === "done" || state === "error";
      E.liveDismiss.hidden = !terminal;
      E.liveDismiss.onclick = () => hideLive();
    }
    function hideLive() {
      E.live.hidden = true;
      E.live.removeAttribute("data-state");
      currentActionObj = null;
      refreshContext();
      paintLock();
    }

    // ───── init ─────
    function init() {
      grab();
      if (!E.btnRun || !E.btnPm) return;   // markup missing — no-op rather than throw
      E.btnRun.addEventListener("click", () => onButtonClick("run-standup"));
      E.btnPm.addEventListener("click",  () => onButtonClick("pm-review"));
      refreshContext();
      paintLock();
    }

    return { init, onStatus, onLive, bindHero };
  })();

  // ════════════════════════════════════════════════════════════════════════
  // SLICE 1 — OPERATE + JOBS controller (interactive board)
  // The human-facing controls (ASSIGN / TRIGGER / DIRECT) + a live job feed, on
  // top of the read-only job API. Reuses the redesigned board's machinery:
  // getJSON, $, el, esc, fmtClock, fmtAge, and the SAME CSRF header + per-attempt
  // idempotency-key the Actions controller sends. A board job is READ-ONLY
  // (review / directive / analysis) so it submits inline with clear feedback
  // rather than the heavy run-standup confirm dialog — but it still states what it
  // does and reports honestly (queued → running → done) in the feed.
  //
  // The four job ACTIONS map to the API's {type, review_kind}:
  //   trigger-review:pm → trigger-review + review_kind=pm
  //   trigger-review:ux → trigger-review + review_kind=ux
  //   send-directive    → send-directive
  //   assign-analysis-task → assign-analysis-task
  // TARGET is the flat string the API accepts:
  //   broadcast | squad:<id> | dev:<id> | staff:<id> | project:<folder>
  // ════════════════════════════════════════════════════════════════════════
  const Jobs = (() => {
    const POLL_MS = 4000;                 // feed refresh cadence (3–5s band)

    // human label + placeholder per ACTION (the select's value, not the raw type)
    const ACTIONS = {
      "assign-task": {
        type: "assign-task", review_kind: null,
        label: "Code task", verb: "Assign code task",
        promptLabel: "Code task",
        placeholder: "Describe the change — the agent edits files in an ISOLATED worktree and stops at a diff for your approval. Nothing commits without you.",
        cost: "Gated code task — the agent plans + edits in an isolated git worktree (no shell, no commit), then STOPS at a diff. You review it below and Approve to commit (to a job branch, never pushed) or Reject to discard.",
        code_task: true,
        confirm: true,
      },
      "trigger-review:pm": {
        type: "trigger-review", review_kind: "pm",
        label: "PM review", verb: "Trigger PM review",
        promptLabel: "Review focus",
        placeholder: "What should the PM zoom in on? (blank → general health check)",
        cost: "Read-only product review — the PM reads the project's docs/board and reports a ranked verdict. No edits.",
      },
      "trigger-review:ux": {
        type: "trigger-review", review_kind: "ux",
        label: "UX review", verb: "Trigger UX / design review",
        promptLabel: "Review focus",
        placeholder: "What design surface or concern? (blank → overall craft & clarity)",
        cost: "Read-only design review — the design lead critiques hierarchy, clarity & craft and reports. No edits.",
      },
      "send-directive": {
        type: "send-directive", review_kind: null,
        label: "Directive", verb: "Send directive",
        promptLabel: "Directive",
        placeholder: "The one-way instruction to record + acknowledge for this target…",
        cost: "One-way directive — recorded + acknowledged on the target's record. The agent does NOT act on it.",
      },
      "assign-analysis-task": {
        type: "assign-analysis-task", review_kind: null,
        label: "Analysis", verb: "Assign analysis task",
        promptLabel: "Task to analyse",
        placeholder: "Describe the task — the agent produces a read-only analysis (state · shape · risks · approach)…",
        cost: "Read-only analysis — the agent investigates the task and reports state / shape / risks / approach. No implementation.",
      },
    };

    // status badge: SHAPE (glyph) + WORD, never color-alone.
    const STATUS = {
      queued:            { glyph: "◷", word: "Queued" },
      running:           { glyph: "◐", word: "Running" },
      awaiting_approval: { glyph: "⏸", word: "Awaiting you" },
      committing:        { glyph: "◑", word: "Committing" },
      done:              { glyph: "●", word: "Done" },
      failed:            { glyph: "✕", word: "Failed" },
      cancelled:         { glyph: "⊘", word: "Cancelled" },
      rejected:          { glyph: "⊘", word: "Rejected" },
    };
    const TERMINAL = new Set(["done", "failed", "cancelled", "rejected"]);
    // code tasks paused for the human (the approval inbox); committing = approved, mid-commit.
    const AWAITING = new Set(["awaiting_approval", "committing"]);

    let E = {};
    let pollTimer = null;
    let lastById = {};               // id -> last-seen status (to announce transitions)
    const expanded = new Set();      // ids whose result is expanded (persist across re-render)
    let announcedTerminal = new Set(); // ids already announced as done/failed
    let firstLoad = true;
    let inFlightSubmit = false;

    function grab() {
      E = {
        form:       $("operate-form"),
        target:     $("op-target"),
        type:       $("op-type"),
        prompt:     $("op-prompt"),
        promptLbl:  $("op-prompt-label"),
        cost:       $("op-cost"),
        submit:     $("op-submit"),
        submitLbl:  $("op-submit-label"),
        ack:        $("op-ack"),
        list:       $("jobs-list"),
        live:       $("jobs-live"),
        workerHint: $("jobs-worker-hint"),
        hint:       $("jobs-hint"),
        // the AWAITING YOUR APPROVAL inbox (code-task HITL)
        approvals:    $("approvals"),
        approvalsList:$("approvals-list"),
        approvalsLive:$("approvals-live"),
      };
    }

    // ───── CSRF header + per-attempt idempotency key (same as Actions.launch) ─────
    function postHeaders(idemKey) {
      return {
        "Content-Type": "application/json",
        "X-Idempotency-Key": idemKey,
        // X-Requested-By is the custom header a cross-site simple <form> cannot
        // set — the backend's CSRF same-origin assertion (identical to Actions).
        "X-Requested-By": "portal",
      };
    }
    function newIdemKey(tag) {
      return `portal-job-${tag}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    }

    // ───── target picker (populated from /api/team) ─────
    // Builds optgroups: Broadcast · Squads · Active devs · Staff · Projects. Values
    // are the flat target strings the API resolves. Folder targets come from the
    // distinct dev/staff folders. Re-runnable: preserves the current selection.
    function populateTargets(team) {
      if (!E.target) return;
      const prev = E.target.value;
      E.target.innerHTML = "";

      const opt = (value, text) => { const o = document.createElement("option"); o.value = value; o.textContent = text; return o; };
      const group = (label) => { const g = document.createElement("optgroup"); g.label = label; return g; };

      // Broadcast (always first)
      E.target.appendChild(opt("broadcast", "Broadcast — the whole team"));

      const t = team || S.team || {};
      const squads = t.squads || [];
      const staff = t.staff || [];

      // Squads
      if (squads.length) {
        const g = group("Squads");
        squads.forEach(sq => {
          if (sq && sq.id) g.appendChild(opt(`squad:${sq.id}`, `Squad · ${sq.name || sq.id}`));
        });
        E.target.appendChild(g);
      }

      // Active devs (across squads)
      const devGroup = group("Devs");
      const folders = new Set();
      squads.forEach(sq => (sq.devs || []).forEach(d => {
        if (!d || !d.id) return;
        if (d.folder) folders.add(d.folder);
        if (d.active !== false) devGroup.appendChild(opt(`dev:${d.id}`, `Dev · ${d.id}`));
      }));
      if (devGroup.childElementCount) E.target.appendChild(devGroup);

      // Staff
      if (staff.length) {
        const g = group("Staff");
        staff.forEach(s => {
          if (!s || !s.id) return;
          if (s.folder) folders.add(s.folder);
          if (s.active !== false) g.appendChild(opt(`staff:${s.id}`, `Staff · ${s.id}`));
        });
        if (g.childElementCount) E.target.appendChild(g);
      }

      // Projects (distinct folders devs/staff work in)
      if (folders.size) {
        const g = group("Projects");
        Array.from(folders).sort().forEach(f => g.appendChild(opt(`project:${f}`, `Project · ${f}`)));
        E.target.appendChild(g);
      }

      // restore prior selection if it still exists
      if (prev) {
        const has = Array.from(E.target.options).some(o => o.value === prev);
        if (has) E.target.value = prev;
      }
    }

    // ───── action-type → prompt label / placeholder / cost copy ─────
    function syncType() {
      const a = ACTIONS[E.type.value] || ACTIONS["send-directive"];
      E.promptLbl.textContent = a.promptLabel;
      E.prompt.setAttribute("placeholder", a.placeholder);
      E.cost.textContent = a.cost;
      E.submitLbl.textContent = a.verb;
    }

    // ───── submit → POST /api/jobs → inline ack ─────
    async function onSubmit(ev) {
      if (ev) ev.preventDefault();
      if (inFlightSubmit) return;
      const a = ACTIONS[E.type.value] || ACTIONS["send-directive"];
      const target = E.target.value || "broadcast";
      const prompt = (E.prompt.value || "").trim();

      // client-side guard: directives/analysis/reviews all need SOME instruction
      // (the API rejects an empty prompt with 409 empty_prompt — fail fast here).
      if (!prompt) {
        showAck("err", "Add a message — the agent needs an instruction (a directive, a review focus, or the task).");
        E.prompt.focus();
        return;
      }

      const body = { type: a.type, target, prompt };
      if (a.review_kind) body.review_kind = a.review_kind;

      // A code task is the consequential one — confirm the isolation+gate contract
      // before queuing (reviews/directives/analysis are read-only, submit inline).
      if (a.confirm) {
        const tlabel = target.replace(/^project:|^dev:|^staff:|^squad:/, "");
        jobConfirm({
          kicker: "ASSIGN CODE TASK",
          title: `Assign a code task to ${tlabel}?`,
          lines: [
            "An agent will plan + edit files in an ISOLATED git worktree (no shell, no commit) and STOP at a diff.",
            "It lands in “Awaiting your approval” below — nothing commits until you click Approve. Nothing commits from a button.",
          ],
          confirmText: "Assign code task",
          onConfirm: () => doSubmit(a, target, body),
        });
        return;
      }
      doSubmit(a, target, body);
    }

    async function doSubmit(a, target, body) {
      inFlightSubmit = true;
      E.submit.disabled = true;
      showAck("info", "Submitting…");

      const idemKey = newIdemKey(a.type);
      try {
        const r = await fetch("/api/jobs", {
          method: "POST", cache: "no-store",
          headers: postHeaders(idemKey),
          body: JSON.stringify(body),
        });
        const data = await r.json().catch(() => ({}));
        if (r.status === 202 && data.id) {
          const label = ACTIONS[E.type.value] ? ACTIONS[E.type.value].label : a.type;
          showAck("ok", `${data.idempotent ? "Already queued" : "Queued"} — ${esc(label)} job ${esc(data.id)} landed in the feed below.`);
          E.prompt.value = "";
          refresh();   // pull the feed immediately so the new job appears at once
        } else {
          // validation/CSRF error carries a `code` + `reason`
          const reason = data.reason || `Rejected (HTTP ${r.status}).`;
          showAck("err", `Couldn't queue${data.code ? ` [${esc(data.code)}]` : ""} — ${esc(reason)}`);
        }
      } catch (e) {
        showAck("err", `Couldn't reach the job API — ${esc(String(e && e.message || e))}.`);
      } finally {
        inFlightSubmit = false;
        E.submit.disabled = false;
      }
    }
    function showAck(kind, msg) {
      E.ack.hidden = false;
      E.ack.dataset.kind = kind;
      E.ack.textContent = msg;
    }

    // ───── per-squad quick review (called from renderSquads) ─────
    // Fires a trigger-review job scoped to a squad's project folder (or the squad
    // itself if it has no single folder) — "review THIS project on demand" in one
    // click. Returns a small result note for inline feedback.
    async function quickReview(targetStr, reviewKind, btn) {
      const idemKey = newIdemKey(`qr-${reviewKind}`);
      const body = { type: "trigger-review", review_kind: reviewKind, target: targetStr,
        prompt: `On-demand ${reviewKind.toUpperCase()} review of ${targetStr}, triggered from its squad card.` };
      const orig = btn ? btn.textContent : "";
      if (btn) { btn.disabled = true; btn.dataset.busy = "1"; }
      try {
        const r = await fetch("/api/jobs", {
          method: "POST", cache: "no-store",
          headers: postHeaders(idemKey),
          body: JSON.stringify(body),
        });
        const data = await r.json().catch(() => ({}));
        if (r.status === 202 && data.id) {
          flashSquadAck(btn, "ok", `${reviewKind.toUpperCase()} review queued →`);
          refresh();
        } else {
          flashSquadAck(btn, "err", data.reason ? `Blocked: ${data.code || "error"}` : `HTTP ${r.status}`);
        }
      } catch (e) {
        flashSquadAck(btn, "err", "API unreachable");
      } finally {
        if (btn) { btn.disabled = false; delete btn.dataset.busy; btn.textContent = orig; }
      }
    }
    function flashSquadAck(btn, kind, msg) {
      if (!btn) return;
      const row = btn.closest(".squad__reviews");
      if (!row) return;
      let note = row.querySelector(".squad__review-ack");
      if (!note) { note = el("span", "squad__review-ack"); row.appendChild(note); }
      note.dataset.kind = kind;
      note.textContent = msg;
      // announce to AT via the jobs live region too
      announce(msg);
      clearTimeout(note._t);
      note._t = setTimeout(() => { if (note) note.textContent = ""; }, 6000);
    }

    // ───── live feed: GET /api/jobs (newest-first) → cards ─────
    async function refresh() {
      try {
        const data = await getJSON("/api/jobs");
        render(data.jobs || [], data.counts || {});
      } catch (e) {
        // a transient miss keeps the last-rendered feed (never blank on a blip)
        if (firstLoad) renderEmpty("Can't reach the job feed yet — is the portal up?");
      }
    }

    function announce(msg) {
      if (E.live) { E.live.textContent = ""; E.live.textContent = msg; }
    }

    function render(jobs, counts) {
      firstLoad = false;
      if (!E.list) return;

      // announce terminal transitions (a job we saw running/queued reaching done/failed)
      jobs.forEach(j => {
        const prev = lastById[j.id];
        if (prev && prev !== j.status && (j.status === "done" || j.status === "failed")
            && !announcedTerminal.has(j.id)) {
          announcedTerminal.add(j.id);
          announce(`Job ${j.id} ${j.status === "done" ? "finished" : "failed"}: ${jobLabel(j)} for ${targetLabel(j)}.`);
        }
        lastById[j.id] = j.status;
      });

      // worker-disabled hint: jobs exist, none ever started, oldest is aging.
      maybeWorkerHint(jobs, counts);

      // the AWAITING YOUR APPROVAL inbox (code-task HITL) — rendered above the feed.
      renderApprovals(jobs);

      if (!jobs.length) { renderEmpty(); return; }

      E.list.innerHTML = "";
      jobs.forEach(j => E.list.appendChild(jobCard(j)));
    }

    function renderEmpty(msg) {
      if (!E.list) return;
      E.list.innerHTML = "";
      const li = el("li");
      li.appendChild(el("div", "jobs__empty",
        msg || "No jobs yet — assign one above (trigger a review, send a directive, or assign an analysis task)."));
      E.list.appendChild(li);
    }

    // a job whose worker never runs: queued jobs exist, nothing is/ran running or
    // terminal, and the oldest queued job is older than a few poll cycles.
    function maybeWorkerHint(jobs, counts) {
      if (!E.workerHint) return;
      const c = counts || {};
      const queued = jobs.filter(j => j.status === "queued");
      const anyMoving = jobs.some(j => j.status === "running" || TERMINAL.has(j.status));
      let stuck = false;
      if (queued.length && !anyMoving) {
        // age the oldest queued (created_at) — > ~20s with nothing moving = worker likely off
        const oldest = queued.reduce((a, b) => (new Date(a.created_at) < new Date(b.created_at) ? a : b));
        const ageMs = Date.now() - new Date(oldest.created_at);
        if (!isNaN(ageMs) && ageMs > 20000) stuck = true;
      }
      if (stuck) {
        E.workerHint.hidden = false;
        E.workerHint.textContent = `${queued.length} job${queued.length > 1 ? "s" : ""} queued but none have started — the job worker may be disabled (set STANDUP_JOBWORKER=1). They'll run once it's on.`;
      } else {
        E.workerHint.hidden = true;
      }
    }

    function jobLabel(j) {
      if (j.type === "trigger-review") return (j.review_kind === "ux" ? "UX review" : "PM review");
      if (j.type === "send-directive") return "Directive";
      if (j.type === "assign-analysis-task") return "Analysis task";
      return j.type || "Job";
    }
    // target label without needing the full record: prefer folder, else kind:id.
    function targetLabel(j) {
      if (j.target_kind === "broadcast" || (!j.target_id && !j.target_folder)) return "broadcast";
      if (j.target_folder) return j.target_folder;
      return j.target_id || "—";
    }
    function targetKindWord(j) {
      const k = j.target_kind;
      if (k === "broadcast") return "to";
      if (k === "squad") return "squad";
      if (k === "project") return "project";
      if (k === "staff") return "staff";
      if (k === "dev") return "dev";
      return "target";
    }

    function badge(status) {
      const meta = STATUS[status] || { glyph: "·", word: status || "—" };
      const b = el("span", "badge");
      b.dataset.status = status;
      b.appendChild(el("span", "badge__glyph", meta.glyph));
      b.appendChild(document.createTextNode(meta.word));
      // a non-color cue for AT: the word is already there; add an explicit label.
      b.setAttribute("aria-label", `status: ${meta.word}`);
      return b;
    }

    function ageText(j) {
      // running/queued: age since created; terminal: when it finished.
      const base = TERMINAL.has(j.status) ? (j.finished_at || j.updated_at) : j.created_at;
      const ms = base ? Date.now() - new Date(base) : NaN;
      if (isNaN(ms)) return "—";
      const rel = fmtAge(ms);
      if (j.status === "queued") return `queued ${rel}`;
      if (j.status === "running") return `started ${rel}`;
      if (j.status === "done") return `done ${rel}`;
      if (j.status === "failed") return `failed ${rel}`;
      if (j.status === "cancelled") return `cancelled ${rel}`;
      return rel;
    }

    function jobCard(j) {
      const li = el("li", "job");
      li.dataset.status = j.status;
      li.dataset.id = j.id;

      // ── top: type + target | badge + age ──
      const top = el("div", "job__top");
      const head = el("div", "job__head");
      head.appendChild(el("span", "job__type", jobLabel(j)));
      const tgt = el("span", "job__target");
      tgt.appendChild(el("span", "job__target-kind", targetKindWord(j) + " "));
      tgt.appendChild(el("span", "job__target-label mono", targetLabel(j)));
      head.appendChild(tgt);
      top.appendChild(head);

      const right = el("div", "job__right");
      right.appendChild(badge(j.status));
      right.appendChild(el("span", "job__age mono", ageText(j)));
      top.appendChild(right);
      li.appendChild(top);

      // ── foot: cancel (queued/running) · result disclosure (done) · error (failed) ──
      const foot = el("div", "job__foot");

      if (j.status === "queued" || j.status === "running") {
        const cancel = el("button", "job__cancel", "Cancel");
        cancel.type = "button";
        cancel.setAttribute("aria-label", `Cancel ${jobLabel(j)} job ${j.id}`);
        cancel.addEventListener("click", () => onCancel(j.id, cancel));
        foot.appendChild(cancel);
      }

      if (j.status === "done" && j.summary) {
        const isOpen = expanded.has(j.id);
        const btn = el("button", "job__disclosure");
        btn.type = "button";
        btn.setAttribute("aria-expanded", isOpen ? "true" : "false");
        btn.appendChild(el("span", "job__disclosure-caret", "▸"));
        btn.appendChild(document.createTextNode("result summary"));
        const panel = el("div", "job__result");
        panel.textContent = j.summary;
        panel.hidden = !isOpen;
        const pid = `job-result-${j.id}`;
        panel.id = pid;
        btn.setAttribute("aria-controls", pid);
        if (Array.isArray(j.denied_tools) && j.denied_tools.length) {
          const dn = el("div", "job__denied", `read-only gate denied: ${j.denied_tools.join(", ")}`);
          panel.appendChild(dn);
        }
        btn.addEventListener("click", () => {
          const open = btn.getAttribute("aria-expanded") === "true";
          btn.setAttribute("aria-expanded", open ? "false" : "true");
          panel.hidden = open;
          if (open) expanded.delete(j.id); else expanded.add(j.id);
        });
        foot.appendChild(btn);
        li.appendChild(foot);
        li.appendChild(panel);
        return li;
      }

      if (j.status === "failed" && j.error) {
        foot.appendChild(el("p", "job__error", esc(j.error)));
      }

      if (foot.childElementCount) li.appendChild(foot);
      return li;
    }

    async function onCancel(id, btn) {
      if (btn) { btn.disabled = true; btn.textContent = "Cancelling…"; }
      try {
        const r = await fetch(`/api/jobs/${encodeURIComponent(id)}/cancel`, {
          method: "POST", cache: "no-store",
          headers: { "X-Requested-By": "portal" },
        });
        await r.json().catch(() => ({}));
        announce(`Cancel requested for job ${id}.`);
      } catch (e) {
        if (btn) { btn.disabled = false; btn.textContent = "Cancel"; }
      } finally {
        refresh();   // re-pull so the card reflects the new state
      }
    }

    // ════════════════════════════════════════════════════════════════════════
    // AWAITING YOUR APPROVAL inbox (code-task HITL) — the live demo of
    // "Code waits for you — nothing commits from a button".
    // ════════════════════════════════════════════════════════════════════════
    const diffCache = {};            // id -> diff_text (fetched once on first expand)
    const diffOpen = new Set();      // ids whose diff panel is expanded

    function approveDiffText(id) {
      // fetch the full job (with diff_text) once; cache it.
      if (diffCache[id] != null) return Promise.resolve(diffCache[id]);
      return getJSON(`/api/jobs/${encodeURIComponent(id)}`)
        .then(j => { diffCache[id] = (j && j.diff_text) || ""; return diffCache[id]; })
        .catch(() => "");
    }

    function renderApprovals(jobs) {
      if (!E.approvals || !E.approvalsList) return;
      const items = (jobs || []).filter(j => AWAITING.has(j.status));
      if (!items.length) {
        E.approvals.hidden = true;
        E.approvalsList.innerHTML = "";
        return;
      }
      E.approvals.hidden = false;
      E.approvalsList.innerHTML = "";
      items.forEach(j => E.approvalsList.appendChild(approvalCard(j)));
    }

    function approvalCard(j) {
      const li = el("li", "approval");
      li.dataset.status = j.status;
      li.dataset.id = j.id;

      // ── head: target + AWAITING badge ──
      const head = el("div", "approval__head");
      const title = el("div", "approval__title");
      title.appendChild(el("span", "approval__kind", "Code task"));
      const tgt = el("span", "approval__target");
      tgt.appendChild(el("span", "approval__target-kind", targetKindWord(j) + " "));
      tgt.appendChild(el("span", "approval__target-label mono", targetLabel(j)));
      title.appendChild(tgt);
      head.appendChild(title);
      head.appendChild(badge(j.status));
      li.appendChild(head);

      // ── the task prompt ──
      if (j.prompt) li.appendChild(el("p", "approval__prompt", j.prompt));

      // ── branch line + the agent's change summary ──
      const meta = el("p", "approval__meta mono");
      const bits = [];
      if (j.branch) bits.push(`branch ${j.branch}`);
      if (j.commit_sha) bits.push(`@ ${String(j.commit_sha).slice(0, 10)}`);
      meta.textContent = bits.join("  ") || "isolated worktree · not committed";
      li.appendChild(meta);
      if (j.summary) li.appendChild(el("p", "approval__summary", j.summary));

      // ── View diff disclosure ──
      const diffWrap = el("div", "approval__diffwrap");
      const isOpen = diffOpen.has(j.id);
      const dbtn = el("button", "approval__diff-toggle");
      dbtn.type = "button";
      dbtn.setAttribute("aria-expanded", isOpen ? "true" : "false");
      dbtn.appendChild(el("span", "approval__diff-caret", isOpen ? "▾" : "▸"));
      dbtn.appendChild(document.createTextNode(" View diff"));
      const pre = el("pre", "approval__diff mono");
      const pid = `approval-diff-${j.id}`;
      pre.id = pid;
      pre.hidden = !isOpen;
      dbtn.setAttribute("aria-controls", pid);
      if (isOpen) fillDiff(pre, j.id);
      dbtn.addEventListener("click", () => {
        const open = dbtn.getAttribute("aria-expanded") === "true";
        dbtn.setAttribute("aria-expanded", open ? "false" : "true");
        dbtn.querySelector(".approval__diff-caret").textContent = open ? "▸" : "▾";
        pre.hidden = open;
        if (open) { diffOpen.delete(j.id); }
        else { diffOpen.add(j.id); fillDiff(pre, j.id); }
      });
      diffWrap.appendChild(dbtn);
      diffWrap.appendChild(pre);
      li.appendChild(diffWrap);

      // ── Approve / Reject (hidden once committing — the worker is mid-commit) ──
      const foot = el("div", "approval__foot");
      if (j.status === "committing") {
        foot.appendChild(el("p", "approval__committing", "Approved — committing to the branch…"));
      } else {
        const approve = el("button", "btn approval__approve", "Approve & commit");
        approve.type = "button";
        approve.setAttribute("aria-label", `Approve and commit code task for ${targetLabel(j)}`);
        approve.addEventListener("click", () => onApprove(j, approve));
        const reject = el("button", "btn btn--ghost approval__reject", "Reject");
        reject.type = "button";
        reject.setAttribute("aria-label", `Reject and discard code task for ${targetLabel(j)}`);
        reject.addEventListener("click", () => onReject(j, reject));
        foot.appendChild(approve);
        foot.appendChild(reject);
      }
      li.appendChild(foot);
      return li;
    }

    function fillDiff(pre, id) {
      if (diffCache[id] != null) { pre.textContent = diffCache[id] || "(empty diff)"; return; }
      pre.textContent = "loading diff…";
      approveDiffText(id).then(txt => { pre.textContent = txt || "(empty diff)"; });
    }

    function announceApproval(msg) {
      if (E.approvalsLive) { E.approvalsLive.textContent = ""; E.approvalsLive.textContent = msg; }
    }

    async function onApprove(j, btn) {
      // Confirm dialog stating the consequence (reuse the shared dialog DOM).
      jobConfirm({
        kicker: "APPROVE",
        title: "Commit this code task?",
        lines: [
          `This commits the reviewed diff to branch ${j.branch || "the job branch"} in ${targetLabel(j)}.`,
          "It commits to the JOB BRANCH only — it is NOT pushed and NOT merged to your mainline. You merge it yourself when ready.",
        ],
        confirmText: "Approve & commit",
        onConfirm: async () => {
          if (btn) { btn.disabled = true; btn.textContent = "Approving…"; }
          try {
            const r = await fetch(`/api/jobs/${encodeURIComponent(j.id)}/approve`, {
              method: "POST", cache: "no-store",
              headers: { "X-Requested-By": "portal", "Content-Type": "application/json" },
              body: JSON.stringify({ approved_by: "portal" }),
            });
            const data = await r.json().catch(() => ({}));
            if (r.status === 202) {
              announceApproval(`Approved — committing code task for ${targetLabel(j)}.`);
            } else {
              announceApproval(`Couldn't approve: ${(data && data.reason) || ("HTTP " + r.status)}.`);
              if (btn) { btn.disabled = false; btn.textContent = "Approve & commit"; }
            }
          } catch (e) {
            announceApproval("Couldn't reach the approve endpoint.");
            if (btn) { btn.disabled = false; btn.textContent = "Approve & commit"; }
          } finally {
            refresh();
          }
        },
      });
    }

    async function onReject(j, btn) {
      jobConfirm({
        kicker: "REJECT",
        title: "Discard this code task?",
        lines: [
          `This discards the diff and tears down the isolated worktree + branch for ${targetLabel(j)}.`,
          "Nothing is committed. The task is marked rejected.",
        ],
        confirmText: "Reject & discard",
        confirmDanger: true,
        onConfirm: async () => {
          if (btn) { btn.disabled = true; btn.textContent = "Rejecting…"; }
          try {
            const r = await fetch(`/api/jobs/${encodeURIComponent(j.id)}/reject`, {
              method: "POST", cache: "no-store",
              headers: { "X-Requested-By": "portal" },
            });
            const data = await r.json().catch(() => ({}));
            if (r.status === 202) announceApproval(`Rejected code task for ${targetLabel(j)} — worktree discarded.`);
            else announceApproval(`Couldn't reject: ${(data && data.reason) || ("HTTP " + r.status)}.`);
          } catch (e) {
            announceApproval("Couldn't reach the reject endpoint.");
          } finally {
            if (btn) { btn.disabled = false; btn.textContent = "Reject"; }
            refresh();
          }
        },
      });
    }

    // ───── a small self-contained confirm dialog reusing the shared #action-dialog
    // DOM (markup is global; only the Actions JS handlers are private, so Jobs drives
    // the same element independently). Focus-trap + Esc + backdrop-close, like openDialog.
    let dlgKeydown = null, dlgLastFocus = null;
    function jobConfirm({ kicker, title, lines, confirmText, confirmDanger, onConfirm }) {
      const backdrop = $("action-dialog-backdrop");
      const dialog = $("action-dialog");
      const kickerEl = $("dialog-kicker"), titleEl = $("dialog-title"), bodyEl = $("dialog-body");
      const confirmBtn = $("dialog-confirm"), cancelBtn = $("dialog-cancel");
      if (!backdrop || !dialog) { if (onConfirm) onConfirm(); return; }  // fallback: just run
      dlgLastFocus = document.activeElement;
      dialog.dataset.variant = "confirm";
      kickerEl.textContent = kicker || "CONFIRM";
      titleEl.textContent = title || "";
      bodyEl.innerHTML = "";
      (lines || []).forEach(t => bodyEl.appendChild(el("p", "dialog__line", t)));
      confirmBtn.hidden = false;
      confirmBtn.textContent = confirmText || "Confirm";
      confirmBtn.classList.toggle("btn--danger", confirmDanger === true);
      const close = () => {
        backdrop.hidden = true;
        if (dlgKeydown) document.removeEventListener("keydown", dlgKeydown, true);
        dlgKeydown = null;
        if (dlgLastFocus && dlgLastFocus.focus) { try { dlgLastFocus.focus(); } catch (_) {} }
      };
      confirmBtn.onclick = () => { close(); if (onConfirm) onConfirm(); };
      cancelBtn.textContent = "Cancel";
      cancelBtn.onclick = () => close();
      backdrop.hidden = false;
      (cancelBtn || dialog).focus();
      dlgKeydown = (ev) => {
        if (ev.key === "Escape") { ev.preventDefault(); close(); return; }
        if (ev.key === "Tab") {
          const f = Array.from(dialog.querySelectorAll("button:not([hidden])"));
          if (!f.length) { ev.preventDefault(); return; }
          const first = f[0], last = f[f.length - 1], a = document.activeElement;
          if (ev.shiftKey && (a === first || !f.includes(a))) { ev.preventDefault(); last.focus(); }
          else if (!ev.shiftKey && (a === last || !f.includes(a))) { ev.preventDefault(); first.focus(); }
        }
      };
      document.addEventListener("keydown", dlgKeydown, true);
      backdrop.onmousedown = (ev) => { if (ev.target === backdrop) close(); };
    }

    function startPolling() {
      stopPolling();
      pollTimer = setInterval(refresh, POLL_MS);
    }
    function stopPolling() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }

    // ───── init ─────
    function init() {
      grab();
      if (!E.form || !E.list) return;   // markup missing — no-op rather than throw
      populateTargets(S.team);
      syncType();
      E.type.addEventListener("change", syncType);
      E.form.addEventListener("submit", onSubmit);
      refresh();          // first feed paint
      startPolling();     // then poll
    }

    // called from pollTeam's adoptTeam so the picker stays in sync with the roster
    function onTeam(team) { populateTargets(team); }

    return { init, onTeam, quickReview };
  })();

  // ───────────────────────── manual refresh ─────────────────────────
  function wireRefresh() {
    const btn = $("refresh-btn");
    btn.addEventListener("click", async () => {
      btn.classList.add("is-spinning");
      btn.disabled = true;
      await Promise.allSettled([pollStatus(), pollHeartbeat()]);
      btn.classList.remove("is-spinning");
      btn.disabled = false;
    });
  }

  // Self-rescheduling status poll with BACKOFF. On a healthy connection it polls
  // every STATUS_MS; after consecutive failures it backs off (STATUS_MS * 2^fails,
  // capped) so a downed backend doesn't spam the console with a fetch error every
  // 12s while the portal is blind. It snaps back to STATUS_MS on the first success.
  function scheduleStatus() {
    const base = STATUS_MS;
    const delay = S.statusFails > 0
      ? Math.min(base * Math.pow(2, S.statusFails), 120000)  // up to ~2min when blind
      : base;
    setTimeout(async () => {
      await pollStatus();
      scheduleStatus();
    }, delay);
  }

  // ───────────────────────── boot ─────────────────────────
  function init() {
    wireRefresh();
    wireBench();
    Actions.init();
    Jobs.init();
    // immediate first paint: try live, fall back to mock if it never answers
    pollStatus().then(() => { if (!S.lastStatusOk) bootstrapMockIfNeeded(); });
    pollHeartbeat();
    pollTeam();
    // if the very first status call is slow/dead, don't leave a blank screen
    setTimeout(() => { if (!S.lastStatusOk && !S.data) bootstrapMockIfNeeded(); }, 1200);

    scheduleStatus();                              // backoff-aware status loop
    setInterval(pollHeartbeat, HEARTBEAT_MS);
    setInterval(pollTeam, STATUS_MS * 10);         // roster changes rarely
    setInterval(tick, TICK_MS);
    tick();
  }

  // Bench strip expand/collapse (collapsed by default).
  function wireBench() {
    const wrap = $("bench");
    const toggle = $("bench-toggle");
    if (!wrap || !toggle) return;
    toggle.addEventListener("click", () => {
      const open = wrap.dataset.open === "1";
      wrap.dataset.open = open ? "0" : "1";
      toggle.setAttribute("aria-expanded", open ? "false" : "true");
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
