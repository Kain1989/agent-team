# DESIGN RULEBOOK — citable rules, not prose

> **Why this file exists.** A team can run a full gated SDLC and still ship an ugly,
> misleading screen. Every review lens in a normal pipeline — correctness, conventions,
> tests — is an *engineering-correctness* lens, so **nobody in the ring is responsible for
> whether the screen is any good**. The design critique that does exist usually runs *after*
> the commit, where it is physically incapable of blocking anything, and its output lands in
> a file the one developer who could act on it never reads.
>
> The result is what Anthropic calls *quiet divergence*: the same defects get found tick
> after tick and nothing ever lands, because a finding written as prose can't become a queue
> item. A **rubric** is a lens. A **rulebook** is a language. This file is the language.
>
> Grounding — Anthropic, *How Anthropic runs large-scale code migrations with Claude Code*
> (2026-07): *"you don't fix the code, you fix the process (loop) that produced the code"*;
> *"reviewers cite the rule behind every finding, so a violation becomes a queue item instead
> of a quiet divergence"*; *"a judge that doesn't catch breakage isn't a judge."*

## How to use it

- **Every finding must cite a rule id** (see `E-01`). A finding that cannot cite one does not
  enter the queue — either add a rule, or it is not a defect.
- Rules come in two kinds:
  - **`[MACHINE]`** — decided by `standup/control/verify_design_quality.js`, which returns an
    exit code (0 no violations · 1 violations · 2 the page could not be loaded · **4 the judge
    itself could not run** — Playwright/Chromium missing, i.e. the gate is broken, not the page).
    That is a referee, not an opinion.
  - **`[JUDGMENT]`** — decided by the `design-quality` review lens (the design lead's rubric).
- **Rules grow.** When one rule is cited repeatedly, you fix the rule or the shared component,
  not the individual file (see `E-02`).
- Adopting this in your own project: keep the rule ids and the thresholds, replace the
  examples. Ids are the vocabulary your reviewers, your board, and the judge all share — if
  you renumber them, renumber them everywhere at once.

## Who owns this file

| Part | Owner | Why |
|---|---|---|
| **A–D** (the concrete design rules — *what good design is*) | the **design lead** (`design_lead`); the PM holds veto on product-level calls | deciding what good design is is a UX job, not the supervisor's |
| **E** (the meta-rules) | the **supervisor** | the supervisor defines that a design judgment must be *executable, verifiable, and assigned to someone* — it does **not** define what good design is |
| **F** (transcript and command-line surfaces) | the **design lead**, same as A–D | `F` is a *surface* family, not a priority family: it exists because A–D all presume a DOM, and this product's most-read surface has no DOM at all |

This split exists to break a loop. A rulebook authored entirely by the supervisor, combined with
`E-01` ("a finding without a rule id does not enter the queue"), is closed: the supervisor writes the
rules, the rules admit only findings that cite those rules, so an agent can only ever surface defects
the supervisor already named — the persona is structurally excluded. An outsider's one-glance
judgment ("these two pages do not look like one product") matches **no** rule, because every rule
here is **single-page in scope** (one view / one screen / side-by-side panels): there are zero
cross-page rules, and nobody's job was "the whole." The `judgments[]` output channel (in the design
and PM schemas) is the fix — an independent-judgment channel that does **not** require a rule id, for
conclusions the rules cannot express, including the judgment that a rule itself is wrong. Its one
condition is that it states the shape things *should* take; criticism that hands over no shape is
discarded. Finding a defect class the rulebook does not yet name, and writing it up as a new A–D
rule, is the **design lead's** job (via the `E-01` propose path), not the supervisor's.

---

## A — Accessibility and operability (highest priority)

| Rule | Decided by | Content |
|---|---|---|
| **A-01** | `[MACHINE]` | **Focus must be visible.** No interactive element may use `outline:none` / `focus:outline-none` without supplying a replacement visible focus indicator. WCAG 2.4.7. |
| **A-02** | `[MACHINE]` | **Touch targets ≥ 44×44 CSS px** — buttons, date inputs, selects, every clickable control. (Inline text links inside a paragraph are exempt; they live in the text flow.) |
| **A-03** | `[MACHINE]` | **Text contrast** ≥ 4.5:1 for body text; ≥ 3:1 for large text (≥18.66px, or ≥14px bold). WCAG 1.4.3. |
| **A-04** | `[MACHINE]` | **Error boundaries.** Every route-level view must be wrapped in an error boundary; a raw framework stack trace must never render to a user. |

## B — Data-visualization integrity

| Rule | Decided by | Content |
|---|---|---|
| **B-01** | `[MACHINE]` | **Axes must be complete.** Any chart with a dimension needs readable axis ticks and an axis label. A floating line of small text is not an axis and not a legend. The worst grade of this is a chart with *zero* text: the reader cannot recover a single value from it. |
| **B-02** | `[MACHINE]` | **Category/time axis labels** must cover at least first, last, and a discernible middle tick. Labelling one endpoint is a violation. |
| **B-03** | `[JUDGMENT]` | **Color encoding must be explainable.** Any color coding needs either a legend or thresholds stated in-view. Unexplained red/amber/green banding is a violation. |
| **B-04** | `[JUDGMENT]` | **No factory defaults.** Do not ship the plotting library's default marker, default palette, or default legend placement. |
| **B-05** | `[JUDGMENT]` | **No visually indistinguishable near-duplicate charts in one view** (e.g. two donuts whose slices differ by under a point — the reader cannot tell which is which or why both exist). |
| **B-06** | `[MACHINE]` | **Isotropic rendering — no geometric distortion.** A chart SVG must not be stretched non-uniformly:<br>① any `<circle>` whose **rendered box** deviates from 1:1 by more than 10% → violation (a circle drawn as an ellipse is proof of non-uniform scaling);<br>② `viewBox` aspect ratio deviating from the rendered aspect ratio by more than 10% (usual cause: `preserveAspectRatio="none"`) → violation.<br>Distortion warps marker shape **and line slope** together, so the chart misstates the data. This is a rendering bug, not an aesthetic complaint. |

## C — Layout and information hierarchy

| Rule | Decided by | Content |
|---|---|---|
| **C-01** | `[JUDGMENT]` | **One view, one primary focus.** N co-equal KPI tiles tiled across a screen is a violation — nothing is emphasized, so nothing is read. |
| **C-02** | `[JUDGMENT]` | **Empty must be de-emphasized.** Empty / missing / zero values must carry less visual weight than real ones. `"0"`, `"—"` and a blank at the same size and weight as `"1,240"` is a violation. |
| **C-03** | `[MACHINE]` | **Side-by-side panels must have comparable content fill.** Compare **content fill ratio** (content bottom ÷ panel height); a spread > 25% is a violation.<br>⚠️ **Do not compare panel box height.** CSS grid stretches siblings to equal height, so a "heights differ by >25%" test can never fire while one panel carries a large trailing void. **Equal boxes do not mean equal fullness — the grid guarantees the first.** |
| **C-04** | `[JUDGMENT]` | **An empty state is a designed state** (explanation + next action). A bare `No data.` is a violation. |

## D — Typography and craft

| Rule | Decided by | Content |
|---|---|---|
| **D-01** | `[MACHINE]` | **Use the type scale.** No scattered magic font sizes (a bare `10px`, `15px`, …) outside the project's scale. |
| **D-02** | `[JUDGMENT]` | **Numerals must not out-rank their own heading.** A large figure in a heavy weight that visually beats the title it belongs to is a violation. |
| **D-03** | `[MACHINE]` | **Emoji are not a heading hierarchy and not an icon system.** |
| **D-04** | `[JUDGMENT]` | **Separate title from state.** Do not concatenate an empty-data signal into a heading string (e.g. `"Conversion rate · no data in window"`). |

## F — Transcript and command-line surfaces

Every rule in A–D presumes a DOM: focus rings, 44px targets, CSS contrast, error boundaries, chart
axes, CSS-grid panels, a type scale. But the surface every user of this system reads on **every**
run is a **terminal transcript** — the engine's `log()` stream and the judges' stdout — and until
this family existed it was governed by nothing. The `Who owns this file` section already names this
failure one level down (*"there are zero cross-page rules, and nobody's job was 'the whole'"*); F
exists so the same sentence is not true of the product's own primary surface. A squad may also
declare a non-web `review_surface` (`cli`, `report`, `agent`, `api`) — these are the rules its
design lens judges against, with the captured output as the artifact instead of a screenshot.

One constraint shapes all of them: this stream carries **zero colour and zero ANSI**, deliberately,
so that it survives a log file, a CI capture, the portal, and a screen reader. That leaves casing,
indentation, separators and terminality as the *only* hierarchy devices available — which is why
inconsistency in them is not a nitpick here, it is the whole design.

> **Which judge decides F.** `[MACHINE]` means *an exit code decides it*, and naming a judge is part
> of the label being honest. F's machine rules are **not** adjudicated by
> `control/verify_design_quality.js` — that judge probes a DOM and is structurally blind to a
> transcript. They are adjudicated by **`control/tests/test_sdlc_routing.js`** (section J), which
> ships the same `--self-test` proof of teeth. Marking a rule `[MACHINE]` with no machine behind it
> would be an advertised-but-unimplemented gate *inside the rulebook that governs them* — the exact
> defect `E-03` exists to prevent. `F-02` and `F-03` are decided **behaviourally** there, by running
> the engine and reading the line it actually prints: an earlier draft checked for the presence of a
> helper's NAME in the source, and `--self-test` immediately proved that vacuous by gutting the
> helper's body while keeping its name.

| Rule | Decided by | Content |
|---|---|---|
| **F-01** | `[MACHINE]` | **Status must survive glyph loss.** A verdict is readable as a WORD. Never carry pass/fail by an emoji, a colour, or a symbol alone — in a terminal that drops emoji, a CI capture, a plaintext log, or a screen reader, `✅` and `❌` differ by one character that may not arrive. This is `A-03`'s principle on a text surface, so per `E-04` it inherits A's priority. |
| **F-02** | `[MACHINE]` | **A summary line accounts for every record it counts.** If a denominator includes states its numerators cannot express, that is a violation; a named residual bucket is the minimum. Enumerate statuses *from the records*, so a status added later cannot become invisible. |
| **F-03** | `[JUDGMENT]` | **One separator, one meaning, per line.** `/` is a fraction only — never "and"; `·` separates independent facts; `—` introduces the subject's elaboration, once. A line that uses one glyph for two relations forces the reader to re-parse it. |
| **F-04** | `[JUDGMENT]` | **A verdict is typographically distinct from a step, on every verdict.** Pick the device (here: `→` introduces a verdict and nothing else) and apply it to *all* of them. A device applied to half its cases is not a device — it reads as several people writing to no agreement. |
| **F-05** | `[JUDGMENT]` | **A run that stopped must not end in the shape of a run that finished.** A stop needs its own closing form, emitted *instead of* the normal one, with nothing printed after it. Loudness in a monochrome stream comes from position, shape and terminality — not from capital letters. A "loud stop" rendered as the 16th log line in the same weight as `BOARD 5 item(s)` is not loud. |
| **F-06** | `[MACHINE]` | **One name per concept across every printed surface.** One canonical list in one file; every other surface references it or is deleted. Corollary: never print a COUNT the code deliberately refuses to hardcode (e.g. a lens count) — that is a claim the product makes about itself that its own code contradicts. |
| **F-07** | `[JUDGMENT]` | **An error names the valid set.** A message that says what was wrong without enumerating what would be right moves the guessing to the human. The enumeration is generated at runtime from the source of truth, never hardcoded, or it becomes one more thing that drifts. |

---

## E — Meta-rules (these govern the loop itself)

| Rule | Content |
|---|---|
| **E-01** | **Every finding must cite a rule id, and the id must EXIST in this file.** No id → either add a rule or it is not a defect. An id that appears nowhere in this rulebook is *inadmissible*: presence-only checking lets any string impersonate a rule, at which point the citation discipline that replaced prose rubrics has quietly become decorative. A genuinely new rule is **proposed → queued → landed here**, then cited — never minted at the point of use.<br>**Judgment-level conclusions do NOT need a rule id (added 0.3.6).** "This surface should not exist", "these pages do not look like one product", "this page's information architecture is wrong" are independent PM/UX judgments — they travel the `judgments[]` channel and are **not** bound by this rule. Their one condition is that they state what *should* be (a judgment with no proposed shape is discarded). Before this, E-01 covered *every* finding, and so silently excluded any judgment that could not be mapped to an existing rule — while the rulebook was authored entirely by the supervisor, which made the supervisor's blind spots the whole team's blind spots. This is the branch that lets the persona reach a conclusion the rules cannot name. |
| **E-02** | **Repeated citation ⇒ fix the rule, not the instance.** If one rule id is cited ≥2 times within a surface, or across ≥2 surfaces, per-file tickets are **forbidden**; it must be escalated to "change the shared component / change the rule, then regenerate the affected batch."<br>*Anthropic: "the fix isn't per-file. You add one sentence to the rulebook and regenerate the affected batch. The rulebook keeps growing; the code never gets hand-patched against it."* |
| **E-03** | **The judge must itself be verified.** Every new `[MACHINE]` rule ships with a **deliberately broken fixture** proving the rule FAILS. A judge that cannot catch breakage is not a judge — that is exactly what `verify_design_quality.js --self-test` runs. Every design verdict is unreliable until the judge can fail. |
| **E-04** | **Priority on conflict: A > B > C > D.** Accessibility always beats aesthetics.<br>`F` is a **surface** family, not a priority family: where an F rule restates an A concern (`F-01` is accessibility) it inherits A's priority; otherwise F follows D. |
| **E-05** | **The judge scans the whole surface, not the diff.** The quality gate must hold for **every** page currently deployed, independent of "what changed this time" or "who wrote it." Users see the product, not the changelog. |
| **E-06** | **When a human finds a defect the gate missed: attribute to a role BEFORE adding a rule.** A gate can only stop *known* defect classes. Finding *unknown* ones — and designing what should exist instead — is the PM/UX job, not the gate's, and emphatically not the product owner's.<br>So the first question after an owner spots something is **not** "which rule do we add?" but **"why didn't PM/UX find it first?"** — never invoked? invoked but only critiquing instead of designing? rubric doesn't cover the class at all? Adding a `[MACHINE]` rule is the **second** step: it stops that one defect recurring, it does not give the team the ability to find the next unknown one.<br>⚠️ **Anti-pattern this rule exists to kill:** encoding whatever the owner happened to notice straight into a machine rule. That makes the product owner the source of defect discovery and demotes PM/UX to gatekeepers — precisely what a product-led operating model forbids: *a PM/UX who only reviews or vetoes at a checkpoint is not shaping the product and adds nothing.* **The rulebook must not grow by borrowing the owner's eyes.**<br>*(One technical observation that still holds: an LLM checking a screen against a rubric line by line systematically misses defects that are visible but hard to name — distortion, misalignment, broken proportion. A human eye catches them in a second. So `[JUDGMENT]` reads must take a **real screenshot** as input, and can never replace the `[MACHINE]` pass.)* |
| **E-07** | **The machine check is a FLOOR, not a verdict. A machine PASS is never a reason to call a design acceptable.** Non-zero exit → always fail. Exit 0 → **proves nothing**; a UX/PM judgment must still pass independently.<br>⚠️ **Evidence.** In a blind audit, a page passed **every** `[MACHINE]` rule — viewBox aspect ratio exact, `r=4` circles rendering 8.0×8.0, geometry perfectly clean — and the design lead scored it **2/10**, *worse* than a page with gross geometric distortion at 4/10. Cause: its ten small-multiple charts were each min-max normalized **per card**, so vertical position carried no meaning — a value of **9** and a value of **63** were drawn at the same height, and two adjacent cards drew **58** at the bottom of its rail and **51** at the top. **The visual encoding inverted the true ranking.** The machine check was silent from top to bottom.<br>**The gate catches "looks wrong." It is blind to "looks right, is lying."** Only actually reading what the chart claims will find the second kind — that is UX/PM judgment, not a script.<br>Therefore the `design-quality` lens's `pass` **must not** be defined as "exit code 0". It is "exit code 0 **AND** an independent UX judgment passed." |

---

## Change log

- **v1** — Initial. Every rule was derived from a **real recorded violation** observed on a live
  deployed UI across six consecutive review ticks, not drafted from theory. `B-06` and `E-07`
  were added after a blind audit in which a human found, in seconds, defects the whole review
  ring had missed — see `E-06` for why that sequence is itself a finding.
- **F-01..F-07** — Added when the SDLC gates (INTAKE + routing) landed, and derived the same way:
  every one is anchored to a violation **recorded in this repo's own source**, not drafted from
  theory (`E-06`, and the v1 provenance discipline). The recorded violations, in order: `F-01` — a
  judge closing with `✅` / `❌` as its entire status system, in a repo whose `D-03` forbids emoji as
  an icon system; `F-02` — a closing line reading `N committed / M green of K worked`, whose
  denominator counted `skipped`, `blocked`, `review-failed` and `work-error` records that its two
  numerators could not express, so "the tick did nothing" and "the tick tried and failed" rendered
  byte-identically; `F-03` — that same line using `/` as *and* and `of` as the fraction four words
  apart; `F-04` — `→` marking three of six verdicts; `F-05` — `TICK DONE` being the engine's only
  closing form, so an aborted run ended in the shape of a completed one; `F-06` — the SDLC sequence
  written out nine times, in three arrow glyphs, with five step counts, three of them printing a
  "2-lens review" that the engine's own code comment refuses to hardcode; `F-07` — the two silent
  degradations this work replaced, which knew the valid set and did not print it.<br>
  Restraint note, because seven rules at once is a fair challenge: it is justified only because this
  surface family previously had **zero** coverage, and `F-06`/`F-07` carry the weight — `F-01`..`F-05`
  are the floor.
