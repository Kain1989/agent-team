"""Turn arbitrary text into a URL-safe slug."""
from __future__ import annotations

import re

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """Lowercase ``text`` and collapse any run of non-alphanumeric characters into a
    single hyphen, trimming leading/trailing hyphens.

        >>> slugify("Hello, World!")
        'hello-world'
        >>> slugify("  multiple   spaces ")
        'multiple-spaces'

    Note: non-ASCII characters are currently dropped (e.g. "Café" -> "caf"). See
    BACKLOG.md for the transliteration / max_length follow-ups.
    """
    text = (text or "").strip().lower()
    text = _NON_ALNUM.sub("-", text)
    return text.strip("-")
