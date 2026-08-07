---
name: add-project
description: Point a squad at a project — clone a git repo, start a new empty one, or adopt a folder you already put here. The one command that turns a fresh install into a team working on YOUR code. Use when the user wants to add a project, add a repo, start a new project, adopt an existing folder, point the team at their own code, or onboard a codebase.
allowedTools: Read, Bash, Edit
---

Clone a repo into this team project and give it a squad. Do it now; don't just describe it.

INPUT: `$ARGUMENTS` — one of three modes:

```
/add-project clone <git-url> [name] [--kind K] [--inspect CMD]   # bring a repo in
/add-project new   <name>          [--kind K] [--inspect CMD]    # start from nothing
/add-project adopt <name>          [--kind K] [--inspect CMD]    # a folder already here
/add-project <git-url> [name] …                                  # shorthand for clone
```

- `name` is the directory under this project root, the squad id, **and** the prefix of the two
  developer ids (`-` and `.` become `_`, so `my-app` gives `my_app_a` / `my_app_b`). For `clone` it
  defaults to the repo's basename. It must be free in all three namespaces.
- `--kind` is the product surface: `web` `report` `agent` `api` `cli` `none`.
- `--inspect` is the ONE command a stranger runs to actually SEE that surface.

**Resolving the shorthand.** The bare form is `clone` **only** when the first token is a URL:
`scheme://…`, or an `scp`-like `user@host:path`. Everything else is a refusal, not a guess:

- a lone `clone` / `new` / `adopt` with nothing after it → print usage. (Otherwise
  `/add-project adopt` reads as a complete command, and a directory genuinely named `adopt`
  becomes unexpressible.)
- anything starting `./`, `../` or `/` is a path, never a URL.
- `^[A-Za-z]:` is excluded **before** the `scp`-like test — `C:\src\thing` is a Windows path, and
  the `host:path` rule would otherwise read `C` as a host. That rule matches ONLY a single-letter
  drive prefix; `file:///…` and `localhost:3000/x` are handled elsewhere — `file://` by the
  scheme test (it is a URL, and a URL naming a local path is still a clone source you must be
  explicit about), `localhost:3000/x` by requiring a `user@` before `host:path`.
- anything else with no mode word → **refuse** and print the three forms.

Guessing a mode is how you clone into a directory the user meant to adopt. This repo's rule is
refuse rather than guess; a lexical URL test is not a guess, an "it looks like a directory" test is.

**This replaces four manual edits** (clone, roster entry, `review_surface`, `.gitignore`) that
previously had to be done by hand and in the right order. Three of the four were documented; the
fourth was not, which is how people ended up with a team pointed at a directory that did not exist.

> **Do not run this while `/standup` or `/work` is running.** Those runs snapshot the roster at
> launch and re-read `standup/team.json` from disk later, so editing it mid-run makes the two
> disagree and the run stops with a misleading `ARM armed the WRONG install`. See the known gap in
> `CHANGELOG.md` (0.4.0).

## Step 0 — decide whether you can ask questions at all

If `--kind` is missing — or `--kind` is anything but `none` and `--inspect` is missing:

**`--kind none` needs no `--inspect`.** `none` declares "this has no inspectable face", so there is
no command to ask for; treating it as missing would refuse a complete invocation with a fix the
caller cannot satisfy. (`--inspect none` is not a magic value — it is stored as the literal string.)

Then, depending on where you are running:

- **Interactive session** → ask for it. Explain what it is (below) and wait.
- **Non-interactive** (`claude -p`, a scheduled run, a workflow agent — anything with no human at
  the keyboard) → **do NOT ask. Refuse, and print a command the user can paste to re-run.** A
  question asked into a headless run is not a question, it is a hang, and the run dies on a timeout
  with nothing to show for it.

```
/add-project <git-url> <name> --kind <web|report|agent|api|cli> --inspect "<command>"
/add-project <git-url> <name> --kind none                      # a surface with no face
```

Say plainly which flag was missing and that nothing was changed. Do not clone first and ask after —
a half-added project is worse than none, because the next command sees a directory with no squad.

## Step 1 — get the directory there, and make it a repo

Resolve `ROOT` = the directory holding `standup/team.json` (this project root).

**Every mode ends in the same state: `<ROOT>/<name>` is its OWN git repo, with at least one
commit, and with an `origin`.** That is not tidiness — see "Why the guarantee" below.

### 1a. Refuse before you touch anything

Common to all modes, checked in this order:

- `name` is a management path. Derive the list from `hooks/supervisor_gate.py`
  (`PLUGIN_DIRS` ∪ `STANDUP_ALLOW_DIRS` ∪ `STANDUP_ALLOW_FILES`) — **do not transcribe it**; a
  hand-written copy in an earlier draft already omitted `hooks/` and `skills/`.
- `name` collides with an existing squad id **case-insensitively**. macOS and Windows default to
  case-insensitive filesystems, so `MyApp` and `myapp` are one directory; an exact comparison lets
  the second one through and you get two squads sharing a folder.
- `name` contains a space, `/`, or a leading `-`.
- The target is a **symlink** → refuse. `/name/` in `.gitignore` will not match it (trailing-slash
  patterns are directory-only, and git does not treat a symlink-to-directory as a directory), so
  `git add -A` stages it as mode `120000` — a blob holding a path out of the tree. Ask the user to
  move or copy the real directory in.
- The target's realpath is outside `ROOT`, or is `ROOT` itself (`adopt .`).
- The target is already covered by a parent `.gitignore` rule — the append in step 4 would look
  like it worked while the directory stayed invisible to git.

### 1b. Per mode

**clone** — `[ -e "$ROOT/<name>" ]` → refuse (see below); then
`git -C "$ROOT" clone <git-url> <name>`. Git is not a backstop here: cloning into an existing
**empty** directory succeeds. If the clone fails, report git's error verbatim and change nothing.

**new** — create `<ROOT>/<name>`, write a one-line `README.md` (nothing else — every file you seed
is a file the user has to delete, and a `.gitignore` invented for a stack you have not seen will be
wrong), `git init`, `git add README.md`, commit.

**adopt** — the directory is already there. Do these in order and **stop at the first refusal**:

1. If it is already its own repo with commits, leave its contents and its `origin` completely
   alone. Skip to 1c.
2. **Ensure the dangerous paths are ignored — do not make this conditional on a `.gitignore`
   existing.** For each of `.env`, `.env.*`, `*.pem`, `id_rsa`, `node_modules/`, `.venv/`,
   `__pycache__/` that is actually present, make sure a matching line is in the directory's
   `.gitignore`, appending to whatever is already there. Only lines for things you can see — do
   not invent entries for a stack you are guessing at.

   The earlier "if the directory has no `.gitignore`, write one" was the wrong condition: a stale
   `*.pyc` from years ago skipped the whole step, and the baseline commit then contained `.env`.
   Measured end to end.
3. **Refuse on anything secret-shaped, by NAME first.** A filename test is the reliable signal
   here: if `.env`, `.env.*`, `*.pem`, `id_rsa`, `id_dsa`, `*.p12`, `*.keystore` or
   `credentials.json` would be committed, **refuse** and list them.

   Then also run the plugin's scanner over the file contents
   (`PYTHONPATH="$ROOT/standup/portal" python3 -c "from parsers import guardrails; …"`, walking the
   tree, skipping `.git`, `node_modules`, `.venv`, `__pycache__`) and refuse on a hit.

   **Content matching alone is not enough and must not be relied on.** Measured against the
   scanner: `DB_PASSWORD=hunter2`, `API_KEY=abcdef123456`, `DATABASE_URL=postgres://u:s3cr3t@…`
   and even `PASSWORD="hunter2"` are all MISSED; only prefixed tokens (`AKIA…`, `ghp_…`) are
   caught. Unquoted `KEY=value` is the normal shape of a real dotenv file, which is exactly the
   file an adopted folder is most likely to carry. The name test is what actually stops it.
   (Speed is not the constraint: ~20,000 files/s, so a 3,600-file tree costs about 0.17s.)
4. `git init` if needed, `git add -A`, commit.

**Why a real baseline commit and not `git commit --allow-empty`.** An empty baseline leaves every
pre-existing file untracked, and `git diff` cannot see untracked files — so when a developer edits
the user's existing source, the review ring reads an **empty diff** and fails the work for a reason
that has nothing to do with it. Measured: with an empty baseline a real edit produces `[]`; with a
tracked baseline the same edit produces a normal diff. The empty-baseline shortcut trades the
secret problem for the exact defect this guarantee exists to prevent.

**Author identity.** Check `git config user.email` **explicitly** before committing; if it is
empty, **refuse** and print the two `git config` lines. Do not invent one, and do not rely on
git's own error: git only says `Author identity unknown` when it cannot GUESS one, and on a machine
with a resolvable FQDN it silently invents `user@host` and commits. The refusal would never fire
where it matters most. The bundled sample is initialised with a
`demo`/`demo@local` identity because it is a throwaway; attributing a commit in the user's own
source to a fabricated author is a different decision, and not ours.

### 1c. Origin

`git -C "$ROOT/<name>" remote` — if there is no `origin`, create a local bare one and push:

```
git init --bare "$ROOT/.<name>-origin.git"
git -C "$ROOT/<name>" remote add origin "$ROOT/.<name>-origin.git"
git -C "$ROOT/<name>" push -u origin HEAD
```

Then **ignore it** — append `/.<name>-origin.git/` to the ROOT `.gitignore` in step 4, alongside
`/<name>/`. A bare origin is neither covered by `/<name>/` nor a pointer entry; it is ordinary
files, so an unignored one lets `git add -A` in the installation absorb the project's entire git
object store. Measured: every object under it staged, including a loose one that decompressed to
`DB_PASSWORD=hunter2`. That is worse than the gitlink this command was built to prevent — a gitlink
is a dangling pointer, this is the content itself, and it travels when the user pushes their own
agent-team repo.

Offline, no network, no account. **This is not optional and it is not cosmetic:** the portal's
code-task flow — the approve-then-commit loop that is this plugin's headline feature — resolves
origin's default branch to build its worktree, and returns a hard error without one. Shipping
`new` and `adopt` without this would leave two of the three modes silently unable to use it.

**If the repo already has an `origin`, do not touch it.** A dead origin still resolves locally and
fails honestly later; re-pointing someone's remote is not this command's business.

### The collision refusal, in full

`clone` and `new` both refuse an existing `<ROOT>/<name>`; `adopt` requires one. Say it like this —
git's own `fatal: destination path 'x' already exists` names neither `name`'s triple role nor the
way out:

```
refusing: <ROOT>/<name> already exists — nothing was changed.
  field  the directory name, the squad id and the developer-id prefix are the SAME string
  fix    adopt it instead:  /add-project adopt <name> --kind <K> --inspect "<CMD>"
         or pick another:   /add-project clone <git-url> <name>2 --kind <K> --inspect "<CMD>"
         or, if a previous attempt left it:  /remove-project <name>
                                             then  rm -rf "<ROOT>/<name>"
```

### Why the guarantee — repo + baseline commit + origin

Not a preference. Measured, in the shape that actually occurs:

- **A project directory that is not its own repo is worse than useless — it is destructive.** In a
  cloned agent-team install the plugin root *is* a git repo, so `git -C <project> …` resolves to
  **the enclosing repo**. `git -C <non-repo project> checkout -b feature/task-1` moves **your
  installation's HEAD**, and `git add -A` stages unrelated in-flight work from elsewhere in the
  tree. Several sessions share this working tree; that is why the push job never auto-commits.
  A run against a non-repo project does not quietly produce nothing — it commits into your setup.
- **`git -C` does not save you.** Outside any repo it is loud — `diff` exits **129** (it falls
  through to `--no-index` usage), and `status`/`rev-parse`/`add`/`checkout` exit **128**. Inside a
  parent repo the same `diff` exits **0 with empty output** — the silent shape. Measured on git
  2.55.0; the number differs by subcommand, so do not restate one code for all of them.
- **A repo with no commit is the same failure by another route.** Untracked files are invisible to
  `git diff`, so the review ring reads an empty diff no matter what changed.
- **No origin disables the portal's code-task loop** (`worktree.py` resolves origin's default
  branch and hard-errors without it).

## Step 2 — the review surface, and why it is not optional

The engine **refuses to run** a squad that declares no `review_surface`, and refuses one whose
`kind` is anything but the six above, and — **for every kind except `none`** — refuses one with a
blank `inspect`. That exception is in the engine, not a courtesy: `surface.kind !== 'none' &&
!inspect.trim()`. `none` means there is no face to inspect, so there is nothing to demand. That is
deliberate: a squad with no declared surface is a team nobody can check the output of.

- `kind` is a label. **`inspect` is the load-bearing field** — the deterministic way to actually see
  what a user sees. A URL for `web`; the command that prints the report for `report`; the question
  you ask an `agent` and what a good answer looks like; a `curl` for `api`; the command plus what
  its output should say for `cli`.
- `none` is a legitimate answer for something genuinely faceless, and it is honest. **An invented
  `inspect` that does not run is worse than `none`** — it becomes a promise the review gate keeps
  trying to cash.

Do not guess `inspect` from the repo's README. If you cannot get a real one, use `kind: none` and
say so in the summary.

**For `new`, `kind: none` is the honest default and you must omit `inspect` entirely** — not
`"inspect": ""`, not a placeholder. A brand-new empty project has no surface, and nothing validates
`inspect` when kind is `none`, so an invented value would pass every check and stay wrong forever.

Write the reason into `how`, and write the TRUE one rather than "revisit this later": the engine
escalates on its own. `standup.workflow.js`'s `_touchedFrontend` turns on **both** `DESIGN_LENS`
and `VISUAL_DQ` the moment a diff touches `frontend|web|ui|client|static|templates` or
`.jsx/.tsx/.vue/.svelte/.html/.css` — regardless of what the squad declared. A `new` project is
gated the instant it grows a UI, whether or not anyone came back to update this field.

## Step 3 — the roster entry

Edit `standup/team.json` **surgically** — this is why this skill has `Edit` and not `Write`. The
file is hand-formatted and carries `_comment` fields that explain the schema; a rewrite loses them.

Read it first, refuse a duplicate squad id, then insert into `teams[]`:

```json
{ "id": "<name>", "name": "<Title Case>", "mission": "<one line — ask if not obvious>",
  "coordination": "Two paired developer-agents who challenge each other's plans and diffs in a FRESH context (pairs critique, they do not debate).",
  "review_surface": { "kind": "<K>", "label": "<what a stranger would call it>", "url": "<if web>",
                      "inspect": "<CMD>", "how": "<any prerequisite, stated inline>" },
  // for kind "none": drop `inspect` and `url`. Do NOT invent a placeholder — nothing validates
  // `inspect` when kind is "none", so a made-up value passes every check and lies forever after.
  "developers": [
    { "id": "<name>_a", "folder": "<name>", "role": "Developer — Builder", "stack": "<infer from the repo>",
      "git": true, "active": true, "pair": "<name>_b", "focus": "implement backlog items with tests",
      "tests": "<the repo's test command>" },
    { "id": "<name>_b", "folder": "<name>", "role": "Developer — Reviewer & Tests", "stack": "<infer>",
      "git": true, "active": true, "pair": "<name>_a", "focus": "fresh-context plan/diff review, coverage, edge cases",
      "tests": "<the repo's test command>" }
  ] }
```

Two developers, not one: the pair critique in a **fresh context** is the gate, and a lone developer
would review its own plan and its own diff. Sanitise `<name>` for the ids if it contains `-` or `.`.

**Re-parse `standup/team.json` after writing.** If it no longer parses, restore it and stop —
say so loudly. A roster that does not parse stops every command in this plugin.

## Step 4 — `.gitignore`

Append `/<name>/` to this project's `.gitignore` — and `/.<name>-origin.git/` too if step 1c
created a local bare origin.

Not cosmetic: the cloned repo is a git repo **inside** this one, and without the ignore a
`git add -A` here records it as a gitlink (mode `160000`) — a pointer to a commit nobody else can
fetch. Verified behaviour, not a precaution.

## Step 5 — tell them what is now true

**Put the required next step FIRST**, then the summary. A four-line success block followed by a
paragraph of caveats reads as "done" and the last line gets skipped — which is exactly how
`/sync-roster` gets missed.

**Substitute every `<…>`.** Print the resolved absolute path and the real origin URL; a literal
`<ROOT>/<name>` on screen means the one line whose whole job is telling the user where their code
went has not done it.

Use the SAME command prefix the user typed: `/sync-roster` in a checkout, `/agent-team:sync-roster`
when the plugin is installed. Alternating between the two guarantees one of them is dead in
whichever install they have.

```
NEXT — required:  /sync-roster
       The two new developers do not exist as agent types until you run it, so /team
       cannot spawn them.

Added my-app
  repo     /Users/you/agent-team/my-app  (origin: git@github.com:you/my-app.git)
  squad    my-app — developers my_app_a, my_app_b
  surface  cli — inspect: cd my-app && pytest -q
  ignored  /my-app/ and /.my-app-origin.git/ added to .gitignore
  wrote    my-app/.gitignore  (+.env, +node_modules/) — we found those in your project

Then:  /work "<task>"   one task through the gated SDLC
       /standup         the whole roster
```

**If you wrote or extended a `.gitignore` inside the user's project, say so on its own line and
name the entries you added and why.** We put a file in their repository; they should learn that
here, not from `git log`. That line is part of the summary, not an extra.

Keep the summary to those lines. The rules above (why `inspect` must terminate, why an invented one
is worse than `none`) belong in this document, not repeated in every run's output — a reader who
has to skim to find the actionable line will skim past it.

## Step 6 — verify, with the checker, not by eye

```
python3 standup/control/verify_project.py added "<name>" --root "$ROOT"
```

**Run it. Do not skip it and do not summarise it.** If it exits non-zero, print its FAIL lines
**verbatim** and say plainly what was and was not changed — the clone landed, the roster entry may
have, and the user needs to know which.

This step is the whole reason the four invariants are enforceable at all. The `.gitignore` one in
particular cannot be checked by reading: it is `git ls-files -s` looking for mode `160000`, and a
checklist in a prompt cannot run that. A gate promised in a document and backed by nothing is the
failure this project has already recorded once, when `/work` was referenced by three governance
files and did not exist.

## Failure contract

Every refusal names **the field in `standup/team.json`** it is about and **one command that fixes
it** — the same contract the engine's own `STOP —` blocks follow. "Something went wrong" is not a
report. If you changed nothing, say that too: the reader's next question is always whether they
need to clean something up.

**Keep refusals to those three things** — what is wrong, the field, the fix. They are already the
strongest output this command produces; padding them with the reasoning behind the rule is what
turns a three-line answer into twenty-five lines of prose that gets skimmed.
