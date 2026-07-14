# demo-app — `textkit`

A tiny, self-contained Python text-utilities library. This is the sample project the
**Demo Dev Squad** works on in the MVP — it needs no credentials, no network, and no
external services, so the gated SDLC + the portal's code-task approval loop work the
moment you clone.

```bash
pip install pytest
pytest            # the suite passes out of the box
```

`setup.sh` (one level up) turns this folder into a local git repo with a local bare
`origin` so the portal's worktree-based code-task flow runs fully offline.

See [BACKLOG.md](BACKLOG.md) for ready-to-assign tasks.
