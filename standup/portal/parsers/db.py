"""SQLite job store for the interactive Mission Control board (Slice 1).

The system of record for the THREE new job types (trigger-review / send-directive
/ assign-analysis-task). Stdlib `sqlite3` only — zero new dependencies. WAL mode so
the worker thread writes while HTTP handlers read without lock contention;
`busy_timeout` so a brief writer lock waits rather than erroring.

THE SINGLE-WRITER CONTRACT (from the pair-challenge)
----------------------------------------------------
The WORKER is the single writer of job-state TRANSITIONS. HTTP handlers only
ENQUEUE (`create_job`) or set a cancel-INTENT (`request_cancel`). Every state
change — claim, finish, cancel-honor — goes through ONE atomic primitive:

    transition(id, from_status, to_status)  ==  UPDATE ... WHERE id=? AND status=from

so any race resolves to exactly one winner (rowcount==1 wins; rowcount==0 lost).
`claim` is the same shape specialized for queued->running with attempts++.

CONNECTIONS: sqlite3 connection objects are NOT shareable across threads. We keep
ONE connection per thread in thread-local storage; every public function calls
`_conn()` which lazily opens + migrates that thread's connection. `STANDUP_JOBS_DB`
(via paths.jobs_db) overrides the path for tests.

LIFECYCLE (Slice 1):  queued -> running -> done | failed | cancelled
(awaiting_approval / committing are Slice 2 — the `status` column is free-form TEXT
so they can be added without a migration; the transition table just gains rows.)
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sqlite3
import threading
import uuid
from typing import Any, Dict, List, Optional

from . import paths

# Terminal states a job can never leave. 'rejected' (Slice 2) joins them: a code task
# whose diff the human declined — its worktree is discarded, nothing more happens.
TERMINAL = frozenset({"done", "failed", "cancelled", "rejected"})

# The status vocabulary (kept as data, not an enum, so it extends without a schema
# change). `status` is free-form TEXT in the table. Slice 2 adds 'awaiting_approval'
# (a code task whose diff is waiting on a human merge decision), 'committing' (the
# human APPROVED — the worker is performing the real commit on the job branch), and
# 'rejected' (the human declined — the worktree is discarded).
STATUSES = ("queued", "running", "awaiting_approval", "committing",
            "done", "failed", "cancelled", "rejected")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id               TEXT PRIMARY KEY,
  type             TEXT NOT NULL,
  target_kind      TEXT,
  target_id        TEXT,
  target_folder    TEXT,
  prompt           TEXT NOT NULL,
  review_kind      TEXT,
  status           TEXT NOT NULL DEFAULT 'queued',
  execution_path   TEXT NOT NULL DEFAULT 'read_only',
  idempotency_key  TEXT UNIQUE,
  run_id           TEXT,
  result_json      TEXT,
  error            TEXT,
  created_by       TEXT NOT NULL DEFAULT 'portal',
  created_at       TEXT NOT NULL,
  started_at       TEXT,
  finished_at      TEXT,
  updated_at       TEXT NOT NULL,
  attempts         INTEGER NOT NULL DEFAULT 0,
  cancel_requested INTEGER NOT NULL DEFAULT 0,
  -- Slice 2 (code_task): the isolated git worktree + review branch the agent's
  -- edits live in, the captured diff (the awaiting-approval artifact — surfaced
  -- by GET /api/jobs/{id} so approval never depends on the worktree still
  -- existing), and the human-approval audit (who/when).
  worktree_path    TEXT,
  branch           TEXT,
  base_sha         TEXT,
  diff_text        TEXT,
  approved_at      TEXT,
  approved_by      TEXT
);
CREATE INDEX IF NOT EXISTS ix_jobs_status  ON jobs(status);
CREATE INDEX IF NOT EXISTS ix_jobs_created ON jobs(created_at DESC);

CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
"""

# v2 adds the Slice-2 code-task columns to an existing jobs table (idempotent ALTERs).
_SCHEMA_VERSION = 2

# Slice-2 columns added by the v1->v2 migration (name -> SQL type). Applied via
# ALTER TABLE ADD COLUMN, skipping any that already exist (a fresh table created from
# _SCHEMA already has them).
_V2_COLUMNS = (
    ("worktree_path", "TEXT"),
    ("branch", "TEXT"),
    ("base_sha", "TEXT"),
    ("diff_text", "TEXT"),
    ("approved_at", "TEXT"),
    ("approved_by", "TEXT"),
)

# Columns surfaced in a job "view" dict, in a stable order. transition(fields=...)
# validates against this set, so every writable column MUST be listed.
_COLUMNS = (
    "id", "type", "target_kind", "target_id", "target_folder", "prompt",
    "review_kind", "status", "execution_path", "idempotency_key", "run_id",
    "result_json", "error", "created_by", "created_at", "started_at",
    "finished_at", "updated_at", "attempts", "cancel_requested",
    "worktree_path", "branch", "base_sha", "diff_text", "approved_at", "approved_by",
)

# Thread-local connection store: one sqlite3 connection per thread, keyed by the
# resolved DB path so a test that swaps STANDUP_JOBS_DB mid-process gets a fresh
# connection rather than a stale handle to the old file.
_local = threading.local()


def _now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    # Forward-migrate an EXISTING jobs table that predates the Slice-2 columns:
    # ADD COLUMN any of _V2_COLUMNS not already present (a fresh table from _SCHEMA
    # already has them, so this is a no-op there). sqlite has no "ADD COLUMN IF NOT
    # EXISTS", so we read the current columns first.
    have = {r["name"] for r in conn.execute("PRAGMA table_info(jobs);").fetchall()}
    for name, sqltype in _V2_COLUMNS:
        if name not in have:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {sqltype};")
    row = conn.execute("SELECT version FROM schema_version LIMIT 1;").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version(version) VALUES (?);", (_SCHEMA_VERSION,))
    elif row["version"] != _SCHEMA_VERSION:
        conn.execute("UPDATE schema_version SET version=?;", (_SCHEMA_VERSION,))
    conn.commit()


def _conn() -> sqlite3.Connection:
    """This thread's migrated connection to the current jobs.db path. Opens lazily;
    re-opens if the resolved path changed (test isolation)."""
    p = paths.jobs_db()
    # Ensure the parent dir exists (control/ may not yet on a fresh box / tmp root).
    p.parent.mkdir(parents=True, exist_ok=True)
    db_path = str(p)
    existing = getattr(_local, "conn", None)
    if existing is not None and getattr(_local, "path", None) == db_path:
        return existing
    if existing is not None:
        try:
            existing.close()
        except sqlite3.Error:
            pass
    conn = _connect(db_path)
    _migrate(conn)
    _local.conn = conn
    _local.path = db_path
    return conn


def init() -> None:
    """Open + migrate this thread's connection eagerly (idempotent). Safe to call
    at import / worker startup."""
    _conn()


def _row_to_view(row: sqlite3.Row) -> Dict[str, Any]:
    d = {k: row[k] for k in row.keys()}
    # Decode result_json into a `result` object for callers (kept the raw too).
    rj = d.get("result_json")
    if rj:
        try:
            d["result"] = json.loads(rj)
        except (ValueError, TypeError):
            d["result"] = None
    else:
        d["result"] = None
    d["cancel_requested"] = bool(d.get("cancel_requested"))
    return d


def new_job_id(now: Optional[_dt.datetime] = None) -> str:
    """`job_<YYYYMMDDTHHMMSS>-<uuid4hex8>` — lexically sortable by time + unique."""
    now = now or _dt.datetime.now()
    return "job_" + now.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]


# --- CREATE (enqueue) --------------------------------------------------------
def create_job(
    *,
    type: str,
    target_kind: Optional[str],
    target_id: Optional[str],
    target_folder: Optional[str],
    prompt: str,
    review_kind: Optional[str] = None,
    execution_path: str = "read_only",
    idempotency_key: Optional[str] = None,
    created_by: str = "portal",
    now: Optional[_dt.datetime] = None,
) -> Dict[str, Any]:
    """Insert a queued job. Idempotent on `idempotency_key`: a duplicate key returns
    the EXISTING job with `idempotent=True` (never a second row). The HTTP handler
    is the only caller; it never writes any other column.

    Returns the job view dict, augmented with `idempotent` (bool)."""
    now = now or _dt.datetime.now()
    conn = _conn()

    # Idempotency fast-path: a repeat key returns the existing row unchanged.
    if idempotency_key:
        row = conn.execute(
            "SELECT * FROM jobs WHERE idempotency_key=?;", (idempotency_key,)
        ).fetchone()
        if row is not None:
            v = _row_to_view(row)
            v["idempotent"] = True
            return v

    jid = new_job_id(now)
    ts = now.astimezone().isoformat(timespec="seconds")
    try:
        conn.execute(
            """INSERT INTO jobs
               (id, type, target_kind, target_id, target_folder, prompt,
                review_kind, status, execution_path, idempotency_key,
                created_by, created_at, updated_at, attempts, cancel_requested)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,0);""",
            (jid, type, target_kind, target_id, target_folder, prompt,
             review_kind, "queued", execution_path, idempotency_key,
             created_by, ts, ts),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        # A concurrent insert won the UNIQUE(idempotency_key) race — return theirs.
        conn.rollback()
        if idempotency_key:
            row = conn.execute(
                "SELECT * FROM jobs WHERE idempotency_key=?;", (idempotency_key,)
            ).fetchone()
            if row is not None:
                v = _row_to_view(row)
                v["idempotent"] = True
                return v
        raise

    v = get(jid)
    assert v is not None
    v["idempotent"] = False
    return v


# --- THE atomic state primitive ---------------------------------------------
def transition(job_id: str, from_status: str, to_status: str,
               *, now: Optional[_dt.datetime] = None,
               fields: Optional[Dict[str, Any]] = None) -> bool:
    """Atomically move a job from `from_status` to `to_status`.

    Implemented as `UPDATE jobs SET status=to,... WHERE id=? AND status=from`. The
    WHERE-status clause is what makes it a race winner: rowcount==1 means THIS
    caller owned the transition; rowcount==0 means it lost (the row was already in
    a different state — e.g. another worker claimed it, or a cancel landed). USE
    THIS EVERYWHERE (claim, finish, cancel) so every race resolves to one winner.

    `fields` sets extra columns in the same UPDATE (e.g. run_id, error,
    result_json, started_at, finished_at). `updated_at` is always bumped. Returns
    True iff the transition was applied."""
    now = now or _dt.datetime.now()
    ts = now.astimezone().isoformat(timespec="seconds")
    conn = _conn()
    sets = ["status=?", "updated_at=?"]
    vals: List[Any] = [to_status, ts]
    if fields:
        for k, val in fields.items():
            if k not in _COLUMNS:
                raise ValueError(f"transition: unknown column {k!r}")
            sets.append(f"{k}=?")
            vals.append(val)
    vals.extend([job_id, from_status])
    cur = conn.execute(
        f"UPDATE jobs SET {', '.join(sets)} WHERE id=? AND status=?;", vals
    )
    conn.commit()
    return cur.rowcount == 1


def claim(job_id: str, *, now: Optional[_dt.datetime] = None) -> bool:
    """Atomic claim of one queued job for THIS worker:
        UPDATE ... SET status='running', started_at=now, attempts=attempts+1
        WHERE id=? AND status='queued'
    rowcount==1 means this worker owns it. (transition() can't express the
    attempts++ increment, so claim is its own statement — same WHERE-status race
    guard.)"""
    now = now or _dt.datetime.now()
    ts = now.astimezone().isoformat(timespec="seconds")
    conn = _conn()
    cur = conn.execute(
        """UPDATE jobs
           SET status='running', started_at=?, updated_at=?, attempts=attempts+1
           WHERE id=? AND status='queued';""",
        (ts, ts, job_id),
    )
    conn.commit()
    return cur.rowcount == 1


def claim_next(*, now: Optional[_dt.datetime] = None) -> Optional[Dict[str, Any]]:
    """Pick the oldest queued job and atomically claim it. Returns the claimed job
    view (now status='running'), or None if nothing was claimable.

    SELECT the candidate, then `claim(id)` — the claim's WHERE status='queued'
    makes the pickup atomic, so two workers selecting the same row resolve to one
    winner (the loser's claim returns False and it tries the next poll)."""
    now = now or _dt.datetime.now()
    conn = _conn()
    # Try candidates oldest-first; the first we win, we return. (Normally one
    # worker, so the first candidate is claimed; the loop is the race-safe form.)
    rows = conn.execute(
        "SELECT id FROM jobs WHERE status='queued' "
        "ORDER BY created_at ASC, rowid ASC;"  # FIFO; rowid breaks same-second ties
    ).fetchall()
    for r in rows:
        if claim(r["id"], now=now):
            return get(r["id"])
    return None


def request_cancel(job_id: str, *, now: Optional[_dt.datetime] = None) -> Optional[Dict[str, Any]]:
    """Set the cooperative cancel INTENT (cancel_requested=1) on a non-terminal
    job. HTTP handlers call this; they NEVER transition state themselves. The
    worker honors the intent (a queued job is cancelled immediately by the caller
    via transition; a running job aborts cooperatively). Returns the updated view,
    or None if the job is unknown. A terminal job is left unchanged (no-op)."""
    now = now or _dt.datetime.now()
    ts = now.astimezone().isoformat(timespec="seconds")
    conn = _conn()
    # Only an actively-executing job (queued/running) is cancellable. A code task in
    # 'awaiting_approval' is NOT cancelled this way — it is discarded via /reject (which
    # also tears down its worktree); terminal jobs no-op.
    conn.execute(
        "UPDATE jobs SET cancel_requested=1, updated_at=? "
        "WHERE id=? AND status IN ('queued','running');",
        (ts, job_id),
    )
    conn.commit()
    return get(job_id)


def request_approve(job_id: str, *, approved_by: str = "portal",
                    now: Optional[_dt.datetime] = None) -> bool:
    """Slice-2 HITL APPROVE intent: atomically flip awaiting_approval -> committing
    and stamp who/when approved. This is the ONLY state change the HTTP /approve
    handler makes — it sets the COMMITTING INTENT; the WORKER (the single writer of
    the actual git commit, R6) picks up 'committing' rows and does the real commit on
    the job branch. Returns True iff the job was awaiting_approval (the transition
    applied); False if it was anything else (already committing/terminal — 409).

    Uses the atomic transition primitive so a double-approve is a clean no-op (the
    second loses the WHERE status='awaiting_approval' race)."""
    now = now or _dt.datetime.now()
    ts = now.astimezone().isoformat(timespec="seconds")
    return transition(job_id, "awaiting_approval", "committing", now=now,
                      fields={"approved_at": ts, "approved_by": approved_by})


def list_committing() -> List[Dict[str, Any]]:
    """All jobs the human has APPROVED that are awaiting the worker's actual commit
    (status='committing'). The worker polls this beside claim_next so a board approve
    is honored within a poll. (Distinct from claim_next, which only takes 'queued'.)"""
    rows = _conn().execute(
        "SELECT * FROM jobs WHERE status='committing' "
        "ORDER BY approved_at ASC, rowid ASC;"
    ).fetchall()
    return [_row_to_view(r) for r in rows]


def request_reject(job_id: str, *, now: Optional[_dt.datetime] = None) -> bool:
    """Slice-2 HITL REJECT: the human declined the diff. Atomically flip
    awaiting_approval -> rejected (terminal). The HTTP handler then tears down the
    worktree (a safe, idempotent git op — no job-state write). Returns True iff the
    job was awaiting_approval. A code task in awaiting_approval is discarded this way,
    NOT via /cancel (which targets queued/running)."""
    now = now or _dt.datetime.now()
    ts = now.astimezone().isoformat(timespec="seconds")
    return transition(job_id, "awaiting_approval", "rejected", now=now,
                      fields={"finished_at": ts})


def set_result(job_id: str, result: Dict[str, Any], *,
               now: Optional[_dt.datetime] = None) -> None:
    """Persist the agent result JSON without changing status (the worker calls this
    just before the terminal transition, or transition(fields=...) can carry it).
    Kept as a helper for clarity + tests."""
    now = now or _dt.datetime.now()
    ts = now.astimezone().isoformat(timespec="seconds")
    conn = _conn()
    conn.execute(
        "UPDATE jobs SET result_json=?, updated_at=? WHERE id=?;",
        (json.dumps(result, ensure_ascii=False), ts, job_id),
    )
    conn.commit()


# --- READ --------------------------------------------------------------------
def get(job_id: str) -> Optional[Dict[str, Any]]:
    row = _conn().execute("SELECT * FROM jobs WHERE id=?;", (job_id,)).fetchone()
    return _row_to_view(row) if row is not None else None


def list_jobs(*, status: Optional[str] = None, type: Optional[str] = None,
              limit: int = 100) -> List[Dict[str, Any]]:
    """Jobs newest-first, optionally filtered by status and/or type. `status` may be
    a comma-separated set (e.g. 'queued,running')."""
    conn = _conn()
    where: List[str] = []
    vals: List[Any] = []
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        if statuses:
            where.append("status IN (%s)" % ",".join("?" * len(statuses)))
            vals.extend(statuses)
    if type:
        where.append("type=?")
        vals.append(type)
    sql = "SELECT * FROM jobs"
    if where:
        sql += " WHERE " + " AND ".join(where)
    # Newest-first. `created_at` is second-resolution, so jobs created in the same
    # second tie on it; the implicit monotonic rowid (insertion order) is the
    # tiebreaker, giving a stable, truly-newest-first order regardless of clock
    # resolution (the random uuid suffix in `id` would not).
    sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?;"
    vals.append(int(limit))
    rows = conn.execute(sql, vals).fetchall()
    return [_row_to_view(r) for r in rows]


def counts() -> Dict[str, int]:
    """status -> count, for the list endpoint's summary block."""
    rows = _conn().execute(
        "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status;"
    ).fetchall()
    return {r["status"]: r["n"] for r in rows}


def list_running(*, now: Optional[_dt.datetime] = None) -> List[Dict[str, Any]]:
    """All jobs currently in 'running' (input to the orphan reconciler)."""
    rows = _conn().execute(
        "SELECT * FROM jobs WHERE status='running' ORDER BY started_at ASC;"
    ).fetchall()
    return [_row_to_view(r) for r in rows]


# --- test/verification hooks -------------------------------------------------
def _close_thread_conn() -> None:
    """Close + drop this thread's connection (so the next call re-opens against the
    current STANDUP_JOBS_DB). Tests that swap the env between cases call this."""
    existing = getattr(_local, "conn", None)
    if existing is not None:
        try:
            existing.close()
        except sqlite3.Error:
            pass
    _local.conn = None
    _local.path = None
