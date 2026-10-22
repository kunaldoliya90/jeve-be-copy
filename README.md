# JEVE-BE

Backend for **JEVE (Journal Entry Validation & Evidence)**. Upload a journal entry
workbook; it is read, re-performed, judged, scored and stored. Numbers come only from
Python and nothing unproven is reported as a pass.

Needs [uv](https://docs.astral.sh/uv/), Docker and `make`. Write code by
[docs/coding-guidelines.md](docs/coding-guidelines.md); agents follow [AGENTS.md](AGENTS.md)
and start from [docs/context.md](docs/context.md).

## Run locally

```bash
make init        # create .venv and install app/requirements/development.txt
make runserver   # on the host: http://127.0.0.1:8000 (auto-reload)
make start       # or in Docker on http://localhost:8000; make stop to stop it
make runtests    # run the tests
```

- Health `/api/jeve/health/` · docs `/api/jeve/docs` (dev and stag) · review
  `POST /api/jeve/reviews/` then `GET /api/jeve/reviews/{id}/events/`
- Port 8000 taken by Docker? Set `API_HOST_PORT=8001` in `.env` (see `.env.example`).

## Settings and flavours

`FLAVOUR` selects the settings class in `app/core/settings/` (default `dev`):

| FLAVOUR | Settings | Requirements | Dockerfile |
| --- | --- | --- | --- |
| `dev` | `development.py` | `development.txt` | `build/DockerfileLocal` |
| `stag` | `staging.py` | `staging.txt` | `build/DockerfileStag` |
| `prod` | `production.py` | `production.txt` | `build/DockerfileProd` |

Override any setting with `APP_<FIELD>`; in code, `from core.settings import config`.

## Checks

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

## Layout

`app/` code (`api/`, `core/`, `review/` the harness, `requirements/`, `tests/`) · `build/`
Dockerfiles · `docs/` architecture and guidelines · `.github/workflows/` (empty for now).
