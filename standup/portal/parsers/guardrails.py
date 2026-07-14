"""Programmable content guardrails (v0.2 — the P5 gap).

Beyond the allowlist + deny-hook ACCESS control (what tools may run / where), these
validate the CONTENT of a job's input (the prompt) and output (the produced diff):

  - check_input(prompt)  -> reject oversized / configured-denied input at create time.
  - check_output(diff)   -> the high-value one: the worker runs this on the STAGED diff
                            BEFORE parking it for human approval, so a diff that leaked a
                            secret is FAILED, never quietly queued for a click-through.

Configurable via control/guardrails.json:
  { "deny_input_patterns": ["regex", ...], "deny_output_patterns": ["regex", ...] }
Sensible secret-detection defaults always apply on output.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List

from . import paths

# High-signal, low-false-positive secret patterns scanned on every produced diff.
_SECRET_PATTERNS = [
    ("aws_access_key_id", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private_key_block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    ("aws_secret_access_key", re.compile(r"(?i)aws_secret_access_key\s*[=:]\s*['\"]?[A-Za-z0-9/+=]{40}")),
    ("slack_token", re.compile(r"xox[baprs]-[0-9A-Za-z-]{10,}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}")),
    ("generic_secret_assignment", re.compile(
        r"(?i)(api[_-]?key|secret|password|access[_-]?token|auth[_-]?token)\s*[=:]\s*['\"][A-Za-z0-9_\-/+=]{20,}['\"]")),
]

MAX_PROMPT_CHARS = int(os.environ.get("STANDUP_MAX_PROMPT_CHARS", "20000"))


def _config() -> Dict[str, Any]:
    try:
        raw = json.loads((paths.control_dir() / "guardrails.json").read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def check_input(prompt: str) -> Dict[str, Any]:
    """Validate job input. Returns {ok, reason}. Oversized or pattern-denied -> ok:False."""
    p = prompt or ""
    if len(p) > MAX_PROMPT_CHARS:
        return {"ok": False, "code": "input_too_long",
                "reason": f"prompt is {len(p)} chars (> {MAX_PROMPT_CHARS} limit)"}
    for pat in _config().get("deny_input_patterns", []):
        try:
            if re.search(pat, p):
                return {"ok": False, "code": "input_denied",
                        "reason": "input matched a configured deny pattern"}
        except re.error:
            continue
    return {"ok": True, "reason": ""}


def scan_secrets(text: str) -> List[str]:
    """Names of secret/deny patterns present in `text` (empty = clean)."""
    t = text or ""
    found: List[str] = []
    for name, rx in _SECRET_PATTERNS:
        if rx.search(t):
            found.append(name)
    for pat in _config().get("deny_output_patterns", []):
        try:
            if re.search(pat, t):
                found.append(f"custom:{pat}")
        except re.error:
            continue
    return found


def check_output(diff_text: str) -> Dict[str, Any]:
    """Validate produced output (a code-task diff). Returns {ok, findings, reason};
    ok:False when the diff appears to contain a secret (a HARD stop — do not park it)."""
    findings = scan_secrets(diff_text or "")
    return {"ok": not findings, "findings": findings,
            "reason": (f"diff appears to contain secrets: {', '.join(findings)}"
                       if findings else "")}
