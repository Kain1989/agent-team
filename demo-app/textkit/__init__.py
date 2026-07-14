"""textkit — a tiny, self-contained text-utilities library.

This is the bundled DEMO project the demo_squad works on. It has a real pytest
suite that passes out of the box, plus a short BACKLOG of well-scoped, creds-free
tasks you can hand to the team as code-tasks from the portal.
"""
from .slugify import slugify
from .wordcount import char_count, word_count

__all__ = ["slugify", "word_count", "char_count"]
__version__ = "0.1.0"
