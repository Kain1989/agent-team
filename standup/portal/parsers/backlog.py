"""Parse BACKLOG.md.

Extracts:
  * the ``Last updated:`` header line -> run-id, agent count, health string
    (``0red/Nyellow/Mreported``) and worked/green/committed/PRs counts.
  * the ``🔴 BLOCKERS FOR KAIN`` list -> [{title, severity, date, days_remaining}].
  * the KEYSTONE / 🔴 SECURITY / ⚠️ Pending sections (as raw blocks).

Tolerant: every extractor degrades to None / [] / raw text on a miss and records
a ``_parse_warnings`` entry; it never raises on content shape.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Dict, List, Optional

from . import paths

# --- regexes (anchored on stable markers, case-insensitive where it helps) ---

_RUN_ID_RE = re.compile(r"`?(wf_[0-9a-f]{3,}(?:-[0-9a-z]+)*)`?", re.IGNORECASE)
_AGENTS_RE = re.compile(r"(\d+)\s+agents?", re.IGNORECASE)
# health string like "0red/7yellow/10reported" OR "0 red / 7 yellow / 10 reported"
_HEALTH_RE = re.compile(
    r"(\d+)\s*red\s*/\s*(\d+)\s*yellow\s*/\s*(\d+)\s*reported", re.IGNORECASE
)
# org color word
_ORG_COLOR_RE = re.compile(r"\b(GREEN|YELLOW|RED)\b", re.IGNORECASE)
# "0 worked / 0 green / 0 committed / 0 PRs"  (also tolerates **bold** markers,
# which we strip before matching)
_WGCP_RE = re.compile(
    r"(\d+)\s*worked\s*/\s*(\d+)\s*green\s*/\s*(\d+)\s*committed\s*/\s*(\d+)\s*PRs?",
    re.IGNORECASE,
)
# A dated risk inside a blocker, e.g. "exp 2026-06-30" / "expires 2026-06-30"
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
# Short MM-DD form used in the tick summary, e.g. "exp 06-30".
_SHORT_DATE_RE = re.compile(r"\b(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b")
# An explicit days-remaining the text already states, e.g. "11d" or "11 days".
_DAYS_RE = re.compile(r"\b(\d{1,3})\s*(?:d\b|days?\b)", re.IGNORECASE)
# severity hints
_SEVERITY_RE = re.compile(r"\bP([0-3])\b")


def _strip_md(s: str) -> str:
    """Drop the noisy markdown emphasis chars so regexes match plain numbers."""
    return s.replace("**", "").replace("`", "")


def _today() -> _dt.date:
    return _dt.date.today()


def _days_remaining(date_str: str, today: Optional[_dt.date] = None) -> Optional[int]:
    try:
        d = _dt.date.fromisoformat(date_str)
    except ValueError:
        return None
    return (d - (today or _today())).days


# ---------------------------------------------------------------------------
def parse_header(text: str, today: Optional[_dt.date] = None) -> Dict[str, Any]:
    """Parse the ``Last updated:`` line.

    Returns {raw, run_id, agents, color, counts:{red,yellow,reported},
             worked, green, committed, prs, _ok}.
    """
    out: Dict[str, Any] = {
        "raw": None,
        "run_id": None,
        "agents": None,
        "color": None,
        "counts": {"red": None, "yellow": None, "reported": None},
        "worked": None,
        "green": None,
        "committed": None,
        "prs": None,
        "_ok": True,
    }

    header_line = None
    for line in text.splitlines():
        if line.lower().startswith("last updated:"):
            header_line = line
            break
    if header_line is None:
        out["_ok"] = False
        return out

    out["raw"] = header_line.strip()
    flat = _strip_md(header_line)

    m = _RUN_ID_RE.search(flat)
    if m:
        out["run_id"] = m.group(1)

    m = _AGENTS_RE.search(flat)
    if m:
        out["agents"] = int(m.group(1))

    m = _ORG_COLOR_RE.search(flat)
    if m:
        out["color"] = m.group(1).lower()

    m = _HEALTH_RE.search(flat)
    if m:
        out["counts"] = {
            "red": int(m.group(1)),
            "yellow": int(m.group(2)),
            "reported": int(m.group(3)),
        }

    m = _WGCP_RE.search(flat)
    if m:
        out["worked"] = int(m.group(1))
        out["green"] = int(m.group(2))
        out["committed"] = int(m.group(3))
        out["prs"] = int(m.group(4))

    return out


# ---------------------------------------------------------------------------
def _find_section(lines: List[str], header_pred) -> Optional[Dict[str, Any]]:
    """Return {start, end, header, body_lines} for the first section whose
    header matches ``header_pred``. A section runs until the next ``## ``/``# ``
    heading or EOF.
    """
    start = None
    header = None
    for i, line in enumerate(lines):
        if header_pred(line):
            start = i
            header = line.strip()
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        s = lines[j].lstrip()
        if s.startswith("## ") or (s.startswith("# ") and j != start):
            end = j
            break
    return {
        "start": start,
        "end": end,
        "header": header,
        "body_lines": lines[start + 1 : end],
    }


# Circled numerals ①..⑳ (U+2460..U+2473).
_CIRCLED = "".join(chr(0x2460 + n) for n in range(20))
_CIRCLED_RE = re.compile(f"[{_CIRCLED}]")
# A "1. " / "2) " numbered marker at a token boundary.
_NUM_MARK_RE = re.compile(r"(?:^|\s)(\d{1,2})[.)]\s+")


def _tokenize_blockers(blob: str) -> List[Dict[str, Any]]:
    """Split a blob of blocker prose into individual items.

    Prefers circled-numeral markers (①②③…) if present; otherwise falls back to
    "1." numbered markers. Each item's text runs up to the next marker. A
    trailing "**People/infra:** …" trailer (which sometimes shares the line) is
    dropped from the last item.
    """
    blob = _strip_md(blob)

    # Drop a People/infra trailer if present (it follows the last blocker).
    for sep in ("People/infra:", "People / infra", "People/infra", "**People"):
        cut = blob.find(sep)
        if cut != -1:
            blob = blob[:cut]
            break

    markers = list(_CIRCLED_RE.finditer(blob))
    if len(markers) >= 2:
        spans = [m.start() for m in markers] + [len(blob)]
        out = []
        for a, b in zip(spans, spans[1:]):
            seg = blob[a + 1 : b].strip().strip(";").strip()
            if seg:
                out.append({"_text": seg})
        return out

    # Fallback: numbered list markers.
    nmarks = list(_NUM_MARK_RE.finditer(blob))
    if len(nmarks) >= 2:
        spans = [m.start(1) for m in nmarks] + [len(blob)]
        out = []
        for a, b in zip(spans, spans[1:]):
            seg = blob[a:b]
            seg = _NUM_MARK_RE.sub(" ", seg, count=1).strip().strip(";").strip()
            if seg:
                out.append({"_text": seg})
        return out

    # Single item or unstructured: degrade to the whole blob as one raw blocker.
    blob = blob.strip()
    return [{"_text": blob}] if blob else []


def parse_blockers(text: str, today: Optional[_dt.date] = None) -> List[Dict[str, Any]]:
    """Parse the most-recent ``🔴 BLOCKERS FOR KAIN`` list.

    The newest tick's blocker list appears highest in the file (newest-first).
    We take the FIRST ``BLOCKERS FOR KAIN`` heading and read its numbered items.
    Items may be blockquoted (``> 1. ...``) in the per-tick summary or plain
    (``1. ...``). We tolerate both.
    """
    lines = text.splitlines()

    # Find the first line mentioning the blockers heading. Match the bare word
    # "BLOCKERS" (case-insensitive) so BOTH this MVP's own `## Blockers` heading and
    # a `🔴 BLOCKERS FOR KAIN` heading are detected — the private deployment used the
    # latter, the shipped MVP BACKLOG uses the former, and before this the literal
    # "BLOCKERS FOR KAIN" match meant the MVP's list parsed EMPTY. `_MARKER` is the
    # ONE token the detection here and the head-tail slice below share (lockstep).
    _MARKER = "BLOCKERS"
    idx = None
    for i, line in enumerate(lines):
        if _MARKER in line.upper():
            idx = i
            break
    if idx is None:
        return []

    # Two layouts occur in practice:
    #  (a) a proper numbered list ("1. ...", "2. ...") — as in the log file and
    #      the "### 🔴 BLOCKERS FOR KAIN" headed sections; and
    #  (b) an inline run using circled numerals (①②③…⑩) all on the same logical
    #      line, item-separated by "; " — as in the BACKLOG "Last updated" tick
    #      summary blockquotes.
    # We gather the text following the heading (rest of the heading line + the
    # blockquote continuation lines) up to the next section, then split it.
    blob_parts: List[str] = []
    # text on the heading line AFTER the marker counts (inline (b) case).
    # Slice from the case-insensitive match index so this stays in lockstep with
    # the detection match above — the heading may be mixed-case ("### 🔴 Blockers
    # …"), so a literal-uppercase split would find nothing and IndexError. `pos` is
    # guaranteed >= 0 because the loop above already proved `_MARKER` is present in
    # `.upper()` of this same line, so the slice can never go out of range.
    pos = lines[idx].upper().find(_MARKER)
    head_tail = lines[idx][pos + len(_MARKER):]
    blob_parts.append(head_tail)
    # If the heading itself is blockquoted (the per-tick "> ### 🔴 Blockers for
    # Kain …" spine), the numbered list lives entirely inside that ONE
    # blockquote. Stripping "> " from every line would otherwise let the gather
    # bleed past the blank line that closes the quote and swallow the NEXT
    # tick's circled-numeral items, mis-tokenizing the wrong section. So when the
    # heading is in a blockquote, stop as soon as we leave it.
    heading_quoted = lines[idx].lstrip().startswith(">")
    for j in range(idx + 1, len(lines)):
        if heading_quoted and not lines[j].lstrip().startswith(">"):
            break
        s = lines[j].lstrip("> ").rstrip()
        if s.startswith("### ") or s.startswith("## ") or s.startswith("---"):
            break
        # Stop the inline run when the prose turns to People/infra trailer.
        blob_parts.append(s)
    blob = " ".join(p for p in blob_parts if p is not None)

    items = _tokenize_blockers(blob)

    # Now structure each item: title (first sentence-ish), severity, date, days.
    blockers: List[Dict[str, Any]] = []
    for it in items:
        body = _strip_md(it["_text"]).strip()
        if not body:
            continue
        # Skip an empty-state placeholder ("- _(none)_", "(none)", "n/a", "tbd") so a
        # fresh-install BACKLOG (`## Blockers` / `- _(none)_`) yields an EMPTY list —
        # a clean demo, not a bogus "(none)" blocker card.
        if re.sub(r"[\s_*()\-./]", "", body).lower() in {"none", "noneyet", "na", "tbd"}:
            continue
        # Title = leading bold/phrase up to the first em-dash, colon, or " (".
        title = re.split(r"\s+[—–-]\s+| \(| — |: ", body, maxsplit=1)[0].strip()
        title = title.rstrip(":").strip() or body[:80]

        sev = None
        ms = _SEVERITY_RE.search(body)
        if ms:
            sev = "P" + ms.group(1)

        # Dated risk: prefer a full YYYY-MM-DD; else accept MM-DD and assume the
        # nearest upcoming occurrence (this year, or next year if already past).
        date_str = None
        m_full = _DATE_RE.search(body)
        if m_full:
            date_str = m_full.group(1)
        else:
            m_short = _SHORT_DATE_RE.search(body)
            if m_short:
                ref = today or _today()
                cand = _dt.date(ref.year, int(m_short.group(1)), int(m_short.group(2)))
                if cand < ref:
                    cand = _dt.date(ref.year + 1, cand.month, cand.day)
                date_str = cand.isoformat()

        days = _days_remaining(date_str, today) if date_str else None
        # If the text already states an explicit days count, trust the computed
        # one from the date; fall back to the stated count when no date present.
        if days is None:
            m_days = _DAYS_RE.search(body)
            if m_days:
                days = int(m_days.group(1))

        # leverage: a short human-readable "why this matters" hint. Stop at the
        # first clause boundary so we don't drag in the rest of the sentence.
        leverage = None
        m_lev = re.search(
            r"\b(?:unstarves?|unblocks?|gates?)\b[^.;,)]*", body, re.IGNORECASE
        )
        if m_lev:
            leverage = m_lev.group(0).strip().rstrip(")").strip()[:120] or None

        if sev is None:
            sev = "P0" if (date_str or "MERGE GATE" in body.upper()) else "P1"

        blockers.append(
            {
                "title": title[:140],
                "severity": sev,
                "date": date_str,
                "days_remaining": days,
                "leverage": leverage,
                "raw": body[:400],
            }
        )
    return blockers


def parse_sections(text: str) -> Dict[str, Any]:
    """Return raw blocks for KEYSTONE / SECURITY / Pending. On a miss each is
    None (the UI shows nothing rather than crashing)."""
    lines = text.splitlines()

    keystone = _find_section(lines, lambda L: L.lstrip().startswith("📌") and "KEYSTONE" in L.upper())
    security = _find_section(lines, lambda L: L.lstrip().startswith("## ") and "SECURITY" in L.upper())
    pending = _find_section(lines, lambda L: L.lstrip().startswith("## ") and "PENDING" in L.upper())

    def _block(sec):
        if not sec:
            return None
        return {
            "header": sec["header"],
            "raw": "\n".join(sec["body_lines"]).strip(),
        }

    return {
        "keystone": _block(keystone),
        "security": _block(security),
        "pending": _block(pending),
    }


# ---------------------------------------------------------------------------
def parse(path=None, today: Optional[_dt.date] = None) -> Dict[str, Any]:
    p = path or paths.backlog_md()
    out: Dict[str, Any] = {
        "header": {},
        "blockers": [],
        "sections": {"keystone": None, "security": None, "pending": None},
        "_parse_warnings": [],
        "_ok": True,
        "_path": str(p),
    }
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        out["_ok"] = False
        out["_parse_warnings"].append(f"BACKLOG.md unreadable: {exc}")
        return out

    out["header"] = parse_header(text, today=today)
    if not out["header"].get("_ok"):
        out["_parse_warnings"].append("no 'Last updated:' header found")

    out["blockers"] = parse_blockers(text, today=today)
    if not out["blockers"]:
        out["_parse_warnings"].append("no BLOCKERS FOR KAIN list parsed")

    out["sections"] = parse_sections(text)
    return out
