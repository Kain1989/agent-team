# demo-app backlog

Well-scoped, creds-free tasks for the **Demo Dev Squad**. Hand any of these to the
team as a **code-task** from the portal (target `project:demo-app` or `dev:dev_a`).
Each is small, self-contained, and ships with tests — ideal for the gated
plan → challenge → implement → review → **approve** → commit loop.

- [ ] **`truncate(text, length, suffix="…")`** — add `textkit/truncate.py`: shorten
  text to `length` characters, append `suffix` when it cuts, never exceed `length`.
  Export from `textkit/__init__.py`; add `tests/test_truncate.py`.
- [ ] **`slugify(max_length=...)`** — optional arg that trims the slug to whole words
  within the limit (no trailing hyphen). Extend `tests/test_slugify.py`.
- [ ] **`top_words(text, n)`** — return the `n` most common words (case-insensitive,
  punctuation-stripped) as a list of `(word, count)`. Add tests.
- [ ] **`title_case(text)`** — capitalize each word EXCEPT small words (a, an, the,
  of, and, …) unless first/last. Add `textkit/titlecase.py` + tests.
