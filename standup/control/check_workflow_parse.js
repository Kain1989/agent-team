#!/usr/bin/env node
// Workflow parse check — catches the class of breakage that `node --check` does NOT.
//
// Why it exists. A workflow script is mostly one enormous set of template literals (the agent
// prompts). Write a `code span` inside one and forget to escape the backticks as \` and the
// backtick TERMINATES THE TEMPLATE EARLY — the prose after it is then parsed as JavaScript.
// `node --check` frequently still PASSES, because whatever remains after the truncation happens
// to parse as valid JS. So the file is "syntactically fine" and the Workflow engine refuses to
// load it, and the next scheduled run dies silently at startup. This has shipped more than once.
//
// How it works: simulate the Workflow harness — strip `export`, wrap the whole script in an
// async function body, and hand it to the REAL parser (`new Function`). Wrapping in a function
// body makes a top-level `return` legal (the harness does the same), so any remaining
// SyntaxError is a real one. Using the real parser instead of a heuristic means zero false
// positives. (A hand-rolled backtick-pairing checker was tried first and reported 29 errors on
// an already-fixed file — backticks inside JSON strings, inside comments, and inside nested
// ${cond ? `a` : `b`} are indistinguishable to naive pairing. Discarded.)
//
// Self-check after editing THIS file: point it at a known-broken and a known-good revision of a
// workflow (e.g. `git show <bad-sha>:path/to.workflow.js > /tmp/bad.js`) and confirm it
// separates them — FAIL on the first, PASS on the second.
//
// Usage:  node standup/control/check_workflow_parse.js <workflow.js> [...]
// Exit:   0 = all parsed / 1 = at least one failed / 64 = usage error

const fs = require('fs');

const files = process.argv.slice(2);
if (!files.length) {
  console.error('usage: check_workflow_parse.js <workflow.js> [...]');
  process.exit(64);
}

let failed = 0;
for (const f of files) {
  let src;
  try {
    src = fs.readFileSync(f, 'utf8');
  } catch (e) {
    console.log(`FAIL ${f} — unreadable: ${e.message}`);
    failed++;
    continue;
  }
  // harness-provided globals; `export const meta` becomes a plain declaration
  const body = src.replace(/^\s*export\s+const\s/m, 'const ');
  try {
    new Function('args', 'agent', 'parallel', 'pipeline', 'phase', 'log', 'workflow', 'budget',
                 `return (async () => {\n${body}\n})()`);
    console.log(`PASS ${f} — parses as a workflow`);
  } catch (e) {
    failed++;
    console.log(`FAIL ${f} — ${e.constructor.name}: ${e.message}`);
    if (/Unexpected token|Invalid or unexpected/.test(e.message)) {
      console.log('   Most likely cause: a bare backtick in a code span inside a prompt, which ended');
      console.log('   the template literal early. Backticks inside a template string must be written \\` .');
    }
  }
}

if (failed) {
  console.log(`\n${failed} file(s) failed to parse. Note that \`node --check\` usually still PASSES on this class —`);
  console.log('it only guarantees that what remains AFTER the truncation parses, not that the template');
  console.log('string ends where you meant it to.');
}
process.exit(failed ? 1 : 0);
