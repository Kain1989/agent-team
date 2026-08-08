# Team Backlog

_The carried-state board. The standup workflow updates this; the portal reads it._

Last updated: (fresh install — run a tick or submit a job)

## Keystones
- _(none yet)_

## portal — Mission Control
- [ ] Try a read-only **review** job against `project:standup/portal` to see the
  read-only gate in action (no edits, just a written review).
- [ ] **Design debt — one shared-component fix, not fifteen tickets.** The design gate reports
  **15 violations** against the portal UI: **A-02 (x11)** interactive controls under 44x44, and
  **D-01 (x4)** font sizes off the type scale (22px, 15px, 10px, 9px). This is **pre-existing
  `standup/portal/` UI debt** — the SDLC-routing work that recorded it changed **zero lines** of
  portal UI. Per `E-02`, a rule cited eleven times is one defect in a shared component, so this is
  a single "fix the control primitive and the type scale, then regenerate" item. Opening a ticket
  per file is the move that guarantees none of them get fixed.

  Reproduce:

  ```
  ( cd standup/portal && ./run_local.sh ) &        # binds PORT from .env
  node standup/control/verify_design_quality.js http://127.0.0.1:<port>
  ```

  **Capture state.** Observed at `http://127.0.0.1:8780` with the runner reporting
  `state: stale` / `source: fallback`, the scheduler disabled, no tick in flight, and the hero
  and action controls **enabled**. Two consecutive runs returned the identical 15.

  **Before calling a different number a regression, check the state it was captured in.**
  Several of this judge's rules sample what is actually rendered, so the total moves with portal
  state as well as with UI changes. An earlier run recorded 15 as A-02 x11 / A-03 x1 / D-01 x3;
  a parallel run taken under a runner-down alarm hero recorded 17 as A-01 x3 / A-02 x11 / D-01 x3.
  `A-01` flags any control whose computed style does not change on focus, which a **disabled**
  button cannot do — so an `A-01` finding on a disabled control is a **false positive**
  (WCAG 2.4.7 does not apply to elements that cannot take focus) and must not be recorded as UI
  debt. Record the capture state next to any future count.

## Blockers
- _(none)_
