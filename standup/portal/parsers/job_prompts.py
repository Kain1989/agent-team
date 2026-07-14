"""Per-job-type prompt templates + the JOB_TYPES registry (Slice 1).

A board job is ONE record differentiated by `type`. Slice 1 ships the three
READ-ONLY types; each maps to a prompt builder that takes the job + its resolved
target metadata (folder/role/label, pulled from team.json via the team parser).
The agent runs under the read-only gate (parsers/agent_run), so every prompt is
phrased as an inspection/record task and ends with an explicit READ-ONLY guard
(belt-and-suspenders with the hook that machine-enforces it).

  trigger-review        -> on-demand PM (or UX) product/design review of a project
  send-directive        -> record + acknowledge a one-way EM directive to a target
  assign-analysis-task  -> a read-only analysis of a task scoped to the target
  assign-task           -> a CODE task (Slice 2): the agent edits files in an isolated
                           git worktree under the worktree-scoped code gate; the trusted
                           worker commits the diff to a review branch and a human
                           approves the merge (the agent never runs shell/commits/merges)
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# --- the JOB_TYPES registry ---------------------------------------------------
# Slice 1 is read-only end-to-end. `execution_path` is persisted at create for
# clarity; the column is free-form so Slice 2 can add 'code_task' types.
JOB_TYPES: Dict[str, Dict[str, Any]] = {
    "trigger-review": {
        "label": "Trigger review",
        "execution_path": "read_only",
        "needs_target": True,
        # review_kind in {'pm','ux'}; default 'pm'.
        "review_kinds": ("pm", "ux"),
    },
    "send-directive": {
        "label": "Send directive",
        "execution_path": "read_only",
        "needs_target": True,
    },
    "assign-analysis-task": {
        "label": "Assign analysis task",
        "execution_path": "read_only",
        "needs_target": True,
    },
    "assign-task": {
        "label": "Assign code task",
        # Slice 2: the WRITE-capable, worktree-scoped, human-approved path.
        "execution_path": "code_task",
        "needs_target": True,
    },
}


def is_known_type(t: str) -> bool:
    return t in JOB_TYPES


def execution_path_for(t: str) -> str:
    return JOB_TYPES.get(t, {}).get("execution_path", "read_only")


# --- target resolution -------------------------------------------------------
def resolve_target(team_parsed: Dict[str, Any], target_kind: Optional[str],
                   target_id: Optional[str]) -> Dict[str, Any]:
    """Resolve a target into {kind, id, folder, role, label, ok, reason}.

    Targets:
      - dev/staff id      -> looked up in team.active_dev_index (folder, role)
      - project folder    -> any team.json `folder` value (target_kind='project')
      - squad id          -> a team in teams[] (folder=None; label=squad name)
      - broadcast         -> no folder (workspace-wide)
    `ok=False` + `reason` when the id can't be resolved (the API turns that into a
    422/409 so a typo never silently runs against the wrong place)."""
    from . import team as team_mod

    kind = (target_kind or "").strip() or None
    tid = (target_id or "").strip() or None
    idx = team_mod.active_dev_index(team_parsed)

    # Broadcast: workspace-wide, no folder.
    if kind == "broadcast":
        return {"kind": "broadcast", "id": None, "folder": None, "role": None,
                "label": "the whole team", "ok": True, "reason": None}

    # Squad: match a team id.
    if kind == "squad":
        for sq in team_parsed.get("squads", []):
            if sq.get("id") == tid:
                return {"kind": "squad", "id": tid, "folder": None,
                        "role": sq.get("mission"), "label": sq.get("name") or tid,
                        "ok": True, "reason": None}
        return {"kind": "squad", "id": tid, "folder": None, "role": None,
                "label": tid, "ok": False,
                "reason": f"no squad '{tid}' in team.json"}

    # Project: a known folder value across devs/staff.
    if kind == "project":
        folders = _all_folders(team_parsed)
        if tid in folders:
            return {"kind": "project", "id": tid, "folder": tid, "role": None,
                    "label": tid, "ok": True, "reason": None}
        return {"kind": "project", "id": tid, "folder": tid, "role": None,
                "label": tid, "ok": False,
                "reason": f"'{tid}' is not a known project folder in team.json"}

    # dev/staff id (or unspecified kind that matches a roster id).
    if tid and tid in idx:
        meta = idx[tid]
        return {"kind": kind or ("staff" if meta.get("squad_id") is None else "dev"),
                "id": tid, "folder": meta.get("folder"), "role": meta.get("role"),
                "label": tid, "ok": True, "reason": None}

    return {"kind": kind or "dev", "id": tid, "folder": None, "role": None,
            "label": tid or "(none)", "ok": False,
            "reason": f"target '{tid}' not found in team.json roster"}


def _all_folders(team_parsed: Dict[str, Any]) -> set:
    out = set()
    for sq in team_parsed.get("squads", []):
        for dev in sq.get("devs", []):
            if dev.get("folder"):
                out.add(dev["folder"])
    for s in team_parsed.get("staff", []):
        if s.get("folder"):
            out.add(s["folder"])
    return out


# --- prompt builders ---------------------------------------------------------
def _scope_line(target: Dict[str, Any]) -> str:
    folder = target.get("folder")
    if folder:
        return f"the project at `{folder}`"
    if target.get("kind") == "squad":
        return f"the {target.get('label')} squad"
    if target.get("kind") == "broadcast":
        return "the whole workspace"
    return f"`{target.get('label')}`"


def _pm_review_prompt(job: Dict[str, Any], target: Dict[str, Any]) -> str:
    scope = _scope_line(target)
    return (
        "You are pm_agent, the product manager for this team (Steve Jobs-grounded: "
        "obsess over the user, cut scope to the essential, demand demonstrable "
        f"quality). Do an on-demand PRODUCT REVIEW of {scope}.\n\n"
        "Operator's review focus: " + (job.get("prompt") or "(general health check)") + "\n\n"
        "Method: read the project's CONTEXT.md / PM_GOALS.md / README / board files "
        "if present; assess scope, direction, starved keystones, and any dated risks "
        "against the stated goals. Be concrete and cite the files you read.\n"
        "Output: a short ranked verdict — the single most important thing, then the "
        "next few, each with a one-line why and a suggested action. End with an "
        "overall health call (green/yellow/red) and one sentence of rationale.\n\n"
        "READ-ONLY: do NOT edit, write, commit, deploy, or run shell commands. "
        "Inspect with read tools only and report. (A gate will deny any mutation; "
        "work within it.)"
    )


def _ux_review_prompt(job: Dict[str, Any], target: Dict[str, Any]) -> str:
    scope = _scope_line(target)
    return (
        "You are the design lead (design_lead — Apple HIG-grounded clarity & craft). "
        f"Do an on-demand UX / DESIGN REVIEW of {scope}.\n\n"
        "Operator's review focus: " + (job.get("prompt") or "(overall design quality)") + "\n\n"
        "Method: read the relevant frontend/source and any design notes in the "
        "project; critique hierarchy, clarity, consistency, and craft. Cite the "
        "files/components you assessed.\n"
        "Output: ranked design findings — the highest-impact issue first, each with "
        "a one-line rationale and a concrete fix. End with an overall craft call "
        "(green/yellow/red).\n\n"
        "READ-ONLY: do NOT edit, write, commit, deploy, take screenshots that write "
        "files, or run shell commands. Inspect with read tools only and report. "
        "(A gate will deny any mutation; work within it.)"
    )


def _directive_prompt(job: Dict[str, Any], target: Dict[str, Any]) -> str:
    label = target.get("label") or "the team"
    return (
        f"You are relaying a one-way DIRECTIVE from the EM (Kain) to {label}.\n\n"
        "The directive (relay it faithfully, do not act on it):\n"
        f"«{job.get('prompt') or ''}»\n\n"
        "Your job is to ACKNOWLEDGE receipt and restate the directive clearly so it "
        "is on record: summarise what was asked, who it's for "
        f"({label}"
        + (f", working in `{target.get('folder')}`" if target.get("folder") else "")
        + "), and confirm it has been noted as an EM directive. This is ONE-WAY: do "
        "NOT implement, edit code, commit, or take any action on the directive's "
        "content — only deliver + acknowledge it.\n\n"
        "READ-ONLY: do NOT write files, edit, commit, deploy, or run shell commands. "
        "Reply with the acknowledgement text only. (A gate will deny any mutation.)"
    )


def _analysis_prompt(job: Dict[str, Any], target: Dict[str, Any]) -> str:
    scope = _scope_line(target)
    role = target.get("role")
    role_line = f" (target role: {role})" if role else ""
    return (
        f"You are an analyst assigned to investigate a task scoped to {scope}{role_line}.\n\n"
        "The task to analyse:\n"
        f"«{job.get('prompt') or ''}»\n\n"
        "Method: read the relevant code/config/docs in the target to understand the "
        "current state, then produce a READ-ONLY analysis — do NOT implement "
        "anything. Cover: (1) what exists today relevant to the task (cite files), "
        "(2) what the task would entail (the shape of the work, key files/areas to "
        "touch), (3) risks / unknowns / open questions, and (4) a recommended "
        "approach. Cite every file you read.\n"
        "Output: a structured analysis with those four sections, concise and "
        "concrete.\n\n"
        "READ-ONLY: do NOT edit, write, create files, commit, deploy, or run shell "
        "commands. Inspect with read tools only and report your analysis. (A gate "
        "will deny any mutation; this is analysis, not implementation.)"
    )


def _code_task_prompt(job: Dict[str, Any], target: Dict[str, Any]) -> str:
    scope = _scope_line(target)
    return (
        "You are an engineer on this team implementing a change in an ISOLATED git "
        f"worktree of {scope}. Your ENTIRE workspace is this worktree — you may read "
        "and edit files inside it, and nothing else.\n\n"
        "The task to implement:\n"
        f"«{job.get('prompt') or ''}»\n\n"
        "Method: read the relevant code to understand the current state, then make the "
        "change directly with Edit/Write/MultiEdit. Keep it focused and minimal, match "
        "the surrounding code's style and conventions, and cite the files you touch.\n\n"
        "HARD constraints (enforced by a gate — work within them):\n"
        "- You have NO shell (Bash) and CANNOT run tests, builds, git, or any command. "
        "Make the code change only; the trusted harness commits it and a human reviews "
        "and runs the tests before merging.\n"
        "- You can read and write ONLY inside this worktree. Do NOT try to read outside "
        "it (home dir, other repos, secrets) — those reads are denied.\n"
        "- Do NOT commit, push, or merge. The harness commits your edits to a review "
        "branch; merging is a separate, human-approved step.\n\n"
        "When done, end with a concise summary: WHAT you changed (per file + why) and "
        "WHAT the reviewer should test/verify before merging — that summary is what the "
        "human reads to decide whether to approve the merge."
    )


def build_prompt(job: Dict[str, Any], target: Dict[str, Any]) -> str:
    """Resolve the agent prompt for a job given its resolved target. The job dict
    carries `type`, `prompt` (operator text), and `review_kind` (for reviews)."""
    t = job.get("type")
    if t == "trigger-review":
        rk = (job.get("review_kind") or "pm").lower()
        if rk == "ux":
            return _ux_review_prompt(job, target)
        return _pm_review_prompt(job, target)
    if t == "send-directive":
        return _directive_prompt(job, target)
    if t == "assign-analysis-task":
        return _analysis_prompt(job, target)
    if t == "assign-task":
        return _code_task_prompt(job, target)
    # Unknown type should have been rejected at create; be safe + read-only.
    return (
        "Read-only inspection task. Operator instruction: "
        + (job.get("prompt") or "")
        + "\n\nREAD-ONLY: do not write/edit/commit/run shell commands."
    )
