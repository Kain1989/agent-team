"""Counting helpers."""
from __future__ import annotations


def word_count(text: str) -> int:
    """Number of whitespace-separated words in ``text``."""
    return len((text or "").split())


def char_count(text: str, include_spaces: bool = True) -> int:
    """Number of characters in ``text``.

    Set ``include_spaces=False`` to ignore all whitespace.
    """
    text = text or ""
    if include_spaces:
        return len(text)
    return len("".join(text.split()))
