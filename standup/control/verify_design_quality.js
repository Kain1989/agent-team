#!/usr/bin/env node
// DESIGN QUALITY JUDGE — the executable half of DESIGN_RULEBOOK.md (the `[MACHINE]` rules).
//
// Why it exists: a gated SDLC whose review lenses are all engineering-correctness lenses has
//   nobody responsible for whether the screen is any good. A "visual gate" implemented as a
//   regex looking for banned words in an LLM's prose is not a gate — it can tell you the dev
//   did not pass off an HTTP 200 as visual proof, and nothing else.
//   Anthropic: "Make review adversarial and verification mechanical. Let scripts be the referee."
//   This script is that referee — the EXIT CODE is the verdict, not an opinion.
//
// ⚠️ Read DESIGN_RULEBOOK.md E-07 before you trust a green run: a non-zero exit ALWAYS fails,
//   but exit 0 PROVES NOTHING. This judge catches "looks wrong"; it is blind to "looks right,
//   is lying" (a page of per-card-normalized sparklines renders perfectly and inverts the
//   true ranking). Exit 0 is a floor, never a design verdict.
//
// Usage:
//   node standup/control/verify_design_quality.js <url> [--json out.json] [--rules A-01,B-01]
//   node standup/control/verify_design_quality.js --self-test    # E-03: prove the judge can FAIL
//   node standup/control/verify_design_quality.js --rule-ids     # the citable rule ids (E-01)
//
// Requires Playwright. If it is not importable the judge EXITS 2 (cannot run) — never 0.
//   npm i -D playwright && npx playwright install chromium
//
// Exit codes: 0 = no violations / 1 = violations / 2 = page or browser unavailable
//             3 = self-test failed (the judge is broken) / 64 = usage error

const path = require('path');
const fs = require('fs');

// ---------- Playwright resolution ----------
// A shareable plugin cannot assume a local `npm install`. Try the normal require, then any
// NODE_PATH entry, then the npx cache, then the global root. If every path fails we exit 2
// with instructions — a judge that cannot run must not report "no violations".
function loadPlaywright() {
  const tried = [];
  const attempt = mod => { try { return require(mod); } catch (e) { tried.push(mod); return null; } };
  let pw = attempt('playwright');
  if (pw) return { pw };
  const roots = [];
  for (const p of String(process.env.NODE_PATH || '').split(path.delimiter)) if (p) roots.push(p);
  try {
    const npx = path.join(require('os').homedir(), '.npm', '_npx');
    for (const d of fs.readdirSync(npx)) roots.push(path.join(npx, d, 'node_modules'));
  } catch (e) { /* no npx cache */ }
  try {
    roots.push(require('child_process').execSync('npm root -g', { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim());
  } catch (e) { /* npm not on PATH */ }
  for (const r of roots) {
    if (!r) continue;
    pw = attempt(path.join(r, 'playwright'));
    if (pw) return { pw };
  }
  return { pw: null, tried: roots };
}

const TYPE_SCALE = [11, 12, 13, 14, 16, 18, 20, 24, 28, 32, 40, 48, 56, 64];
const EMOJI_RE = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{FE0F}]/u;

// ---------- in-page probe ----------
// Injected wholesale and evaluated in the page: returns a structured violation list.
// Rule ids map 1:1 onto DESIGN_RULEBOOK.md.
function probe(cfg) {
  const V = [];
  const add = (rule, detail, sample) => V.push({ rule, detail, sample: (sample || '').slice(0, 80) });
  const vis = el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0';
  };
  const label = el => (el.getAttribute('aria-label') || el.textContent || el.tagName).trim().replace(/\s+/g, ' ');

  const INTERACTIVE = 'a[href],button,input,select,textarea,[role="button"],[role="tab"],[tabindex]:not([tabindex="-1"])';
  const interactives = [...document.querySelectorAll(INTERACTIVE)].filter(vis);

  // ---- A-02 touch targets >= 44x44 ----
  if (cfg.rules.includes('A-02')) {
    for (const el of interactives) {
      const r = el.getBoundingClientRect();
      // an inline text link lives in the paragraph flow — the touch-target rule does not apply
      const inFlow = el.tagName === 'A' && el.closest('p,li,span') && r.height < 30 && getComputedStyle(el).display === 'inline';
      if (inFlow) continue;
      if (r.width < 44 || r.height < 44) {
        add('A-02', `${Math.round(r.width)}x${Math.round(r.height)}px < 44x44`, label(el));
      }
    }
  }

  // ---- A-03 contrast ----
  if (cfg.rules.includes('A-03')) {
    const lum = (r, g, b) => {
      const f = c => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
      return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
    };
    const parse = c => { const m = (c || '').match(/[\d.]+/g); return m ? m.map(Number) : null; };
    const effBg = el => {           // walk up to the first opaque background
      let n = el;
      while (n && n !== document.documentElement) {
        const c = parse(getComputedStyle(n).backgroundColor);
        if (c && (c[3] === undefined || c[3] > 0.5)) return c;
        n = n.parentElement;
      }
      return [255, 255, 255];
    };
    const texts = [...document.querySelectorAll('p,span,div,td,th,li,h1,h2,h3,h4,h5,h6,label,button,a')]
      .filter(el => vis(el) && [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim().length > 1));
    for (const el of texts.slice(0, 400)) {
      const s = getComputedStyle(el);
      const fg = parse(s.color); if (!fg) continue;
      const bg = effBg(el);
      const L1 = lum(fg[0], fg[1], fg[2]), L2 = lum(bg[0], bg[1], bg[2]);
      const ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
      const size = parseFloat(s.fontSize), weight = parseInt(s.fontWeight) || 400;
      const large = size >= 18.66 || (size >= 14 && weight >= 700);
      const need = large ? 3 : 4.5;
      if (ratio < need) add('A-03', `contrast ${ratio.toFixed(2)}:1 < ${need}:1 (${Math.round(size)}px)`, label(el));
    }
  }

  // ---- A-04 a raw error stack rendered to the user ----
  if (cfg.rules.includes('A-04')) {
    const body = document.body.innerText || '';
    const sigs = [/at\s+\w+\s+\(.*\.jsx?:\d+:\d+\)/, /Unexpected Application Error/i, /TypeError:.*\n\s+at\s/,
                  /Cannot read propert(y|ies) of (undefined|null)/];
    for (const re of sigs) {
      const m = body.match(re);
      if (m) { add('A-04', 'the page rendered a raw stack trace / uncaught exception to the user', m[0]); break; }
    }
  }

  // ---- B-01 / B-02 chart axes + B-06 isotropic rendering ----
  // ⚠ This outer `if` MUST list every rule implemented inside the block. When B-06 was first
  //   added it was omitted here, so `--rules B-06` on its own skipped the whole block and
  //   reported 0 violations on a page that plainly violated it — while the full-set self-test
  //   masked the bug completely. Add a rule inside this block => add it to this condition.
  if (['B-01', 'B-02', 'B-06'].some(r => cfg.rules.includes(r))) {
    // Size floor 120x48 only. Icons are filtered by the marks>=5 test below, NOT by size:
    // a 274x80 small multiple is exactly the chart class most likely to be normalized per card,
    // axis-free and distorted — exempting it "for being small" is how a judge goes quiet.
    const charts = [...document.querySelectorAll('svg')].filter(s => {
      const r = s.getBoundingClientRect();
      return vis(s) && r.width >= 120 && r.height >= 48;
    });
    for (const svg of charts) {
      const ticks = [...svg.querySelectorAll('text')].map(t => (t.textContent || '').trim()).filter(Boolean);
      const numeric = ticks.filter(t => /^[$%]?[\d,.]+[%kKmM]?$/.test(t));
      const r = svg.getBoundingClientRect();
      const near = (t, edge) => {                    // which edge is this tick pinned to
        const b = t.getBoundingClientRect();
        return edge === 'left' ? (b.left - r.left) < r.width * 0.18 : (r.bottom - b.bottom) < r.height * 0.18;
      };
      const texts = [...svg.querySelectorAll('text')].filter(t => (t.textContent || '').trim());
      const yTicks = texts.filter(t => near(t, 'left') && /[\d.]/.test(t.textContent));
      const xTicks = texts.filter(t => near(t, 'bottom'));
      const ctx = (svg.closest('[class*=card],[class*=panel],section,article') || svg.parentElement);
      const name = ((ctx && ctx.querySelector('h1,h2,h3,h4,h5,h6')) || {}).textContent || 'chart';
      // Only judge axes on SVGs that are actually plotting data: >=5 marks. Excludes icons/decoration.
      const marks = svg.querySelectorAll('path,circle,rect,polyline,line,ellipse').length;
      if (marks < 5) continue;

      if (cfg.rules.includes('B-01')) {
        // Worst grade: zero text anywhere in the chart — not one tick, not one label, so the
        // reader cannot recover a single value. (An early version only tested "has numbers but
        // no left-edge ticks", which missed this more extreme case entirely.)
        if (ticks.length === 0) {
          add('B-01', `chart has no axis ticks or labels at all (${marks} data marks, 0 text nodes) — no value on it can be read`, name.trim());
        } else if (numeric.length > 0 && yTicks.length === 0) {
          add('B-01', `chart plots a dimension but has no Y axis ticks (${ticks.length} text nodes, 0 pinned left)`, name.trim());
        }
      }
      if (cfg.rules.includes('B-02') && ticks.length > 0 && xTicks.length < 3) {
        add('B-02', `X axis has only ${xTicks.length} tick label(s) (need >=3: first/middle/last)`,
            `${name.trim()}${xTicks.length ? ` — ${xTicks.map(t => t.textContent.trim()).join(',')}` : ''}`);
      }

      // ---- B-06 isotropy: a circle must be a circle; viewBox must not be stretched ----
      if (cfg.rules.includes('B-06')) {
        // (1) a circle's rendered box must be ~square. An ellipse is proof of non-uniform scaling.
        const circles = [...svg.querySelectorAll('circle')];
        let worst = null;
        for (const c of circles) {
          const cb = c.getBoundingClientRect();
          if (cb.width < 1 || cb.height < 1) continue;
          const ar = cb.width / cb.height;
          const skew = Math.max(ar, 1 / ar);
          if (skew > 1.1 && (!worst || skew > worst.skew)) {
            worst = { skew, w: cb.width, h: cb.height, n: circles.length };
          }
        }
        if (worst) {
          add('B-06', `<circle> rendered as an ellipse: ${worst.w.toFixed(1)}x${worst.h.toFixed(1)}px (aspect ${worst.skew.toFixed(2)}:1, must be 1:1) — all ${worst.n} markers are distorted, and every line slope with them`, name.trim());
        }
        // (2) viewBox aspect ratio vs rendered aspect ratio. Usual cause: preserveAspectRatio="none".
        const vb = (svg.getAttribute('viewBox') || '').split(/[\s,]+/).map(Number).filter(n => !isNaN(n));
        if (vb.length === 4 && vb[2] > 0 && vb[3] > 0 && r.height > 0) {
          const vbAR = vb[2] / vb[3], realAR = r.width / r.height;
          const dev = Math.max(vbAR / realAR, realAR / vbAR);
          if (dev > 1.1) {
            const par = svg.getAttribute('preserveAspectRatio') || '(unset)';
            add('B-06', `viewBox aspect ${vbAR.toFixed(2)} vs rendered ${realAR.toFixed(2)} — off by ${dev.toFixed(1)}x, preserveAspectRatio="${par}"; the chart is non-uniformly stretched`, name.trim());
          }
        }
      }
    }
  }

  // ---- C-03 content fill ratio of side-by-side panels ----
  // ⚠ Measure CONTENT FILL, not box height. CSS grid stretches siblings to equal height, so a
  //   "heights differ by >25%" test is silent by construction while one panel carries a large
  //   trailing void. Equal boxes do not mean equal fullness — the grid guarantees the first.
  if (cfg.rules.includes('C-03')) {
    const cards = [...document.querySelectorAll('[class*=card],[class*=panel]')].filter(vis);
    const rows = new Map();
    const fillRatio = (el) => {
      const pr = el.getBoundingClientRect();
      if (pr.height <= 0) return null;
      let bottom = pr.top;                                   // lowest visible content edge
      for (const kid of el.querySelectorAll('*')) {
        if (!vis(kid)) continue;
        const kr = kid.getBoundingClientRect();
        if (kr.height <= 0 || kr.width <= 0) continue;
        if (kr.bottom > bottom && kr.bottom <= pr.bottom + 1) bottom = kr.bottom;
      }
      return Math.min(1, Math.max(0, (bottom - pr.top) / pr.height));
    };
    for (const c of cards) {
      const r = c.getBoundingClientRect();
      if (r.width < 200 || r.height < 100) continue;
      if (c.parentElement && c.parentElement.closest('[class*=card],[class*=panel]')) continue;  // outermost panels only
      const key = Math.round((r.top + window.scrollY) / 24) * 24;      // bin into rows
      if (!rows.has(key)) rows.set(key, []);
      rows.get(key).push({ el: c, fill: fillRatio(c), h: r.height });
    }
    for (const [, group] of rows) {
      const g = group.filter(x => x.fill !== null);
      if (g.length < 2) continue;
      const fills = g.map(x => x.fill);
      const max = Math.max(...fills), min = Math.min(...fills);
      if (max - min > 0.25) {
        const empty = g.find(x => x.fill === min);
        add('C-03', `side-by-side panels differ in content fill by ${Math.round((max - min) * 100)}% > 25% (fullest ${Math.round(max * 100)}% vs emptiest ${Math.round(min * 100)}%, ~${Math.round(empty.h * (1 - min))}px trailing void)`,
            ((empty.el.querySelector('h1,h2,h3,h4,h5,h6') || {}).textContent || '').trim());
      }
    }
  }

  // ---- D-01 magic font sizes ----
  if (cfg.rules.includes('D-01')) {
    const seen = new Map();
    for (const el of [...document.querySelectorAll('*')].slice(0, 3000)) {
      if (!vis(el)) continue;
      if (![...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())) continue;
      const px = Math.round(parseFloat(getComputedStyle(el).fontSize));
      if (!cfg.scale.includes(px)) seen.set(px, (seen.get(px) || 0) + 1);
    }
    for (const [px, n] of seen) {
      if (n >= 2) add('D-01', `font-size ${px}px is not in the type scale (${n} occurrences)`, `${px}px`);
    }
  }

  // ---- D-03 emoji used as heading hierarchy / icon system ----
  if (cfg.rules.includes('D-03')) {
    const re = new RegExp(cfg.emoji, 'u');
    for (const h of [...document.querySelectorAll('h1,h2,h3,h4,h5,h6,[role="heading"]')].filter(vis)) {
      const t = (h.textContent || '').trim();
      if (re.test(t)) add('D-03', 'emoji in a heading, used as hierarchy / an icon system', t);
    }
  }

  return V;
}

// ---------- A-01 focus visibility (needs real focus — driven from Node) ----------
async function checkFocusVisible(page) {
  const out = [];
  const handles = await page.$$('a[href],button,input,select,textarea,[role="button"],[role="tab"]');
  for (const h of handles.slice(0, 60)) {                 // sample: focusing every node on a big page is slow
    try {
      if (!(await h.isVisible())) continue;
      const before = await h.evaluate(el => {
        const s = getComputedStyle(el);
        return `${s.outlineWidth}|${s.outlineStyle}|${s.boxShadow}|${s.borderColor}|${s.backgroundColor}`;
      });
      await h.focus();
      const after = await h.evaluate(el => {
        const s = getComputedStyle(el);
        return `${s.outlineWidth}|${s.outlineStyle}|${s.boxShadow}|${s.borderColor}|${s.backgroundColor}`;
      });
      if (before === after) {
        const label = await h.evaluate(el => (el.getAttribute('aria-label') || el.textContent || el.tagName).trim().replace(/\s+/g, ' '));
        out.push({ rule: 'A-01', detail: 'no visible style change on focus (outline/box-shadow/border/background all identical)', sample: label.slice(0, 80) });
      }
    } catch (e) { /* element left the DOM mid-check — skip */ }
  }
  return out;
}

const ALL_RULES = ['A-01', 'A-02', 'A-03', 'A-04', 'B-01', 'B-02', 'B-06', 'C-03', 'D-01', 'D-03'];

// ---------- DESIGN_RULEBOOK rule-id registry (E-01) ----------
// E-01 used to be checked for PRESENCE only — so any string could impersonate a rule id and
// nothing caught a board citing ids that exist nowhere. The legal set is read from the rulebook
// FILE and used for:
//   (a) --rules input validation: a typo used to SILENTLY SKIP that rule -> 0 violations -> false
//       green (the same accident class as the B-06 block-condition bug above). Now it exits 64.
//   (b) --self-test drift check: every rule this judge implements must exist in the rulebook,
//       else the exit code is adjudicating a rule nobody has agreed to.
//   (c) --rule-ids: a deterministic source of legal ids for the workflow/agents, so nothing guesses.
const RULEBOOK_CANDIDATES = [
  path.join(__dirname, '..', '..', 'DESIGN_RULEBOOK.md'),   // plugin root (standup/control/ -> ../../)
  path.join(__dirname, '..', 'DESIGN_RULEBOOK.md'),
  path.join(process.cwd(), 'DESIGN_RULEBOOK.md'),
];
let RULEBOOK_PATH = RULEBOOK_CANDIDATES.find(p => { try { return fs.statSync(p).isFile(); } catch (e) { return false; } })
  || RULEBOOK_CANDIDATES[0];
function rulebookIds() {
  try {
    // Wide family match (A-Z, not just today's A-E) so a NEW rule family is citable the moment
    // it lands in the rulebook, with no edit here.
    const ids = fs.readFileSync(RULEBOOK_PATH, 'utf8').match(/\b[A-Z]-\d{2}\b/g);
    return ids && ids.length ? new Set(ids) : null;
  } catch (e) {
    return null;   // unreadable => do not gate on it; a judge must not refuse to sit because it lost its handbook
  }
}

async function audit(url, rules) {
  const { pw, tried } = loadPlaywright();
  if (!pw) {
    return { error: `Playwright is not installed or not importable (looked in: ${(tried || []).join(', ') || 'require paths'}).\n` +
      '  Install it:  npm i -D playwright && npx playwright install chromium\n' +
      '  Or point NODE_PATH at an existing install.\n' +
      '  The judge exits 2 (cannot run) rather than 0 — an unrunnable gate must never report "no violations".' };
  }
  const browser = await pw.chromium.launch().catch(() => pw.chromium.launch({ channel: 'chrome' }).catch(() => null));
  if (!browser) return { error: 'could not launch Chromium (run: npx playwright install chromium)' };
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  let violations = [];
  try {
    const resp = await page.goto(url, { waitUntil: 'networkidle', timeout: 45000 }).catch(() => null);
    if (!resp && !url.startsWith('file://')) { await browser.close(); return { error: `could not load ${url}` }; }
    await page.waitForTimeout(2500);                       // let async charts render
    violations = await page.evaluate(probe, { rules, scale: TYPE_SCALE, emoji: EMOJI_RE.source });
    if (rules.includes('A-01')) violations.push(...await checkFocusVisible(page));
  } finally {
    await browser.close();
  }
  return { url, violations };
}

// ---------- E-03 self-test: a judge that cannot catch breakage is not a judge ----------
async function selfTest() {
  const fixture = path.join(__dirname, 'fixtures', 'design_violations.html');
  if (!fs.existsSync(fixture)) {
    console.error(`SELF-TEST FAIL: missing the deliberately-broken fixture ${fixture} (required by DESIGN_RULEBOOK E-03)`);
    return 3;
  }
  // Drift check: this judge may not adjudicate a rule that does not exist in DESIGN_RULEBOOK.md.
  const known = rulebookIds();
  if (known) {
    const orphan = ALL_RULES.filter(r => !known.has(r));
    if (orphan.length) {
      console.error(`SELF-TEST FAIL: rules implemented here but absent from ${RULEBOOK_PATH} -> ${orphan.join(', ')}`);
      console.error('Either land the rule in the rulebook or delete the implementation — an exit code adjudicating an unwritten rule is baseless.');
      return 3;
    }
  } else {
    console.log(`SELF-TEST note: could not read ${RULEBOOK_PATH} — skipping the rule-id drift check.`);
  }
  const { violations, error } = await audit('file://' + fixture, ALL_RULES);
  if (error) { console.error(`SELF-TEST FAIL: ${error}`); return 3; }
  const caught = new Set((violations || []).map(v => v.rule));
  const missed = ALL_RULES.filter(r => !caught.has(r));
  console.log(`SELF-TEST — the fixture triggered ${violations.length} violation(s), covering: ${[...caught].sort().join(', ')}`);
  if (missed.length) {
    console.error(`SELF-TEST FAIL: these rules did NOT fire on a deliberately broken fixture, i.e. they are dead letters -> ${missed.join(', ')}`);
    console.error('DESIGN_RULEBOOK E-03: "a judge that doesn\'t catch breakage isn\'t a judge."');
    return 3;
  }
  console.log(`SELF-TEST PASS — all ${ALL_RULES.length} [MACHINE] rules caught their planted violation. The judge can fail, so its verdicts mean something.`);
  console.log('Reminder (E-07): a non-zero exit always fails, but exit 0 proves nothing — UX/PM judgment still has to pass independently.');
  return 0;
}

(async () => {
  const argv = process.argv.slice(2);
  // The legal rule ids — a deterministic source for the workflow/agents (E-01 existence check)
  if (argv.includes('--rule-ids')) {
    const ids = rulebookIds();
    if (!ids) { console.error(`cannot read the rulebook at ${RULEBOOK_PATH}`); process.exit(2); }
    console.log([...ids].sort().join('\n'));
    process.exit(0);
  }
  if (argv.includes('--self-test')) process.exit(await selfTest());

  const url = argv.find(a => !a.startsWith('--'));
  if (!url) {
    console.error('usage: verify_design_quality.js <url> [--json out.json] [--rules A-01,B-01] | --self-test | --rule-ids');
    console.error('  <url> is a REQUIRED parameter — this judge has no default target. Point it at your own running instance.');
    process.exit(64);
  }
  const ri = argv.indexOf('--rules');
  const rules = ri >= 0 && argv[ri + 1] ? argv[ri + 1].split(',').map(s => s.trim()) : ALL_RULES;
  // A mistyped/invented rule id used to be silently skipped -> 0 violations -> false green. Now hard-fails.
  const _known = rulebookIds();
  if (_known) {
    const _unknown = rules.filter(r => !_known.has(r));
    if (_unknown.length) {
      console.error(`--rules contains id(s) absent from ${RULEBOOK_PATH}: ${_unknown.join(', ')}`);
      console.error(`legal ids: ${[...(_known)].sort().join(', ')}`);
      console.error('(E-01: a rule id must land in the rulebook before it can be cited; silently skipping a typo = a false green.)');
      process.exit(64);
    }
    const _unimpl = rules.filter(r => !ALL_RULES.includes(r));
    if (_unimpl.length) {
      console.error(`--rules contains id(s) this judge does not implement (likely [JUDGMENT] rules, which a script cannot decide): ${_unimpl.join(', ')}`);
      console.error(`[MACHINE] rules implemented here: ${ALL_RULES.join(', ')}`);
      process.exit(64);
    }
  }

  const res = await audit(url, rules);
  if (res.error) { console.error(`CANNOT RUN: ${res.error}`); process.exit(2); }

  const byRule = {};
  for (const v of res.violations) (byRule[v.rule] = byRule[v.rule] || []).push(v);

  console.log(`\nDESIGN GATE — ${url}`);
  console.log(`rule set: ${rules.join(', ')}`);
  console.log(`violations: ${res.violations.length}\n`);
  for (const rule of Object.keys(byRule).sort()) {
    const list = byRule[rule];
    console.log(`  ${rule}  x ${list.length}`);
    for (const v of list.slice(0, 5)) console.log(`      - ${v.detail}${v.sample ? `  [${v.sample}]` : ''}`);
    if (list.length > 5) console.log(`      … and ${list.length - 5} more`);
  }

  const ji = argv.indexOf('--json');
  if (ji >= 0 && argv[ji + 1]) {
    fs.writeFileSync(argv[ji + 1], JSON.stringify({ url, rules, violations: res.violations, by_rule: byRule }, null, 2));
    console.log(`\nJSON report -> ${argv[ji + 1]}`);
  }

  // E-02: a rule cited >=2 times is a SHARED-COMPONENT fix, never N per-file tickets
  const systemic = Object.entries(byRule).filter(([, l]) => l.length >= 2).map(([r, l]) => `${r}(x${l.length})`);
  if (systemic.length) {
    console.log(`\n! E-02 systemic violations (do NOT open per-file tickets — change the shared component/rule and regenerate the affected batch): ${systemic.join(', ')}`);
  }
  if (res.violations.length === 0) {
    console.log('\nGATE PASS — floor cleared. E-07: this proves NOTHING about design quality; UX/PM judgment still has to pass independently.');
  } else {
    console.log('\nGATE FAIL');
  }
  process.exit(res.violations.length === 0 ? 0 : 1);
})();
