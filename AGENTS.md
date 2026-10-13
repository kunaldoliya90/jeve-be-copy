# JEVE-BE agent rules

Rules for any coding agent working in this repository. If a task seems to need breaking
one, ask first.

1. Follow [docs/coding-guidelines.md](docs/coding-guidelines.md) for every change: layout,
   view → controller → handler → helper → utils layering, Pydantic models, no hardcoded
   values, settings and `FLAVOUR`, exceptions, logging, naming, tests.
2. Build only what the task needs. No speculative modules, folders, helpers, dependencies
   or config "for later".
3. Ask before adding infrastructure (database, queues, caches, auth, external services) or
   any new dependency. WorkIndia internal packages (`wi-*`) are not on public PyPI; do not
   add them unless told they are available.
4. Keep the Makefile to `init`, `start`, `stop`, `runserver`, `runtests`, and leave
   `.github/workflows/*.yml` empty until a pipeline is requested.
5. Keep `README.md` under 45 lines; update it and `docs/architecture.md` when behaviour
   or structure changes.
6. Before finishing, run and report the results of:
   `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run pytest`.
