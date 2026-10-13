# JEVE-BE

Backend for **JEVE (Journal Entry Validation & Evidence)**. Currently a FastAPI skeleton
with a health check and per-flavour settings; no database or business features yet.

Needs [uv](https://docs.astral.sh/uv/), Docker and `make`. Write code by
[docs/coding-guidelines.md](docs/coding-guidelines.md); agents also follow [AGENTS.md](AGENTS.md).

## Run locally

```bash
make init        # create .venv and install app/requirements/development.txt
make runserver   # run on the host: http://127.0.0.1:8000 (auto-reload)
make start       # or run in Docker on http://localhost:8000 (hot reload)
make stop        # stop the Docker stack
make runtests    # run the tests
```

- Health: `/api/jeve/health/` · API docs: `/api/jeve/docs` (dev and stag only)
- Port 8000 taken by Docker? Set `API_HOST_PORT=8001` in `.env` (see `.env.example`).

## Settings and flavours

`FLAVOUR` selects the settings class in `app/core/settings/` (default `dev`):

| FLAVOUR | Settings | Requirements | Dockerfile |
| --- | --- | --- | --- |
| `dev` | `development.py` | `development.txt` | `build/DockerfileLocal` |
| `stag` | `staging.py` | `staging.txt` | `build/DockerfileStag` |
| `prod` | `production.py` | `production.txt` | `build/DockerfileProd` |

Override any setting with `APP_<FIELD>` (e.g. `APP_LOG_LEVEL=INFO`). In code:
`from core.settings import config`.

## Checks

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

## Layout

`app/` code (`api/`, `core/`, `requirements/`, `tests/`, `app.py`, `main.py`) ·
`build/` Dockerfiles · `docs/` architecture and coding guidelines · `.github/workflows/`
(empty for now).
