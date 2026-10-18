# JEVE-BE agent rules

Rules for any coding agent working in this repository. If a task seems to need breaking
one, ask first.

1. Start from [docs/context.md](docs/context.md): the repo map, rules, the user's
   decisions and a change log. Do not explore the repo; open only the files the task
   touches. In the same change, update its Map, Decisions and Log.
2. Follow [docs/coding-guidelines.md](docs/coding-guidelines.md) for every change: layout,
   view → controller → handler → helper → utils layering, Pydantic models, no hardcoded
   values, settings and `FLAVOUR`, exceptions, logging, naming, tests.
3. Build only what the task needs. No speculative modules, folders, helpers, dependencies
   or config "for later".
4. Ask before adding infrastructure (database, queues, caches, auth, external services) or
   any new dependency. WorkIndia internal packages (`wi-*`) are not on public PyPI; do not
   add them unless told they are available.
5. Keep the Makefile to `init`, `start`, `stop`, `runserver`, `runtests`, and leave
   `.github/workflows/*.yml` empty until a pipeline is requested.
6. Keep `README.md` under 45 lines; update it and `docs/architecture.md` when behaviour
   or structure changes.
7. Before finishing, run and report the results of:
   `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run pytest`.
