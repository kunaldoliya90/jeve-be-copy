# JEVE-BE architecture

How the service is put together. It follows the WorkIndia FastAPI guidelines; how to
write code in it is in [coding-guidelines.md](coding-guidelines.md).

## Application

- `app/app.py` creates the FastAPI instance (`uvicorn app:app`, run from `app/`) and
  mounts `api.views.router` under `API_PREFIX` (`/api/jeve`). API docs are served under the
  same prefix when `DOCS_URL` / `REDOC_URL` / `OPENAPI_URL` are set.
- `app/applifespan.py` runs on startup and shutdown: it applies `config.LOGGING_CONFIG`.
- `app/main.py` is the CLI (`python app/main.py runserver`), the place for management
  commands later.
- Requests flow view → controller → handler → helper → utils. Views subclass
  `core.apiview.APIView` (a local stand-in for `wifastapiutils.views.APIView`) and are
  registered with `register_route` in `api/views/__init__.py`.
- Only `GET /api/jeve/health/` exists today.

## Settings

```text
app/core/settings/
  __init__.py     get_settings() reads FLAVOUR → config
  base.py         BaseConfig: shared fields, safe defaults, LOGGING_CONFIG
  development.py  FLAVOUR=dev   debug, docs, DEBUG logs, reads .env
  staging.py      FLAVOUR=stag  docs on
  production.py   FLAVOUR=prod  docs off, DEBUG cannot be enabled
  consts.py       Flavour, LogLevel and other constants
```

- `FLAVOUR` comes from the process environment (default `dev`); an unknown value stops
  startup. The stag/prod images set it, and the deployment can override it.
- Fields are UPPER_CASE and overridable with `APP_<FIELD>` variables; settings are frozen.

## Images

`build/DockerfileStag` and `build/DockerfileProd` install their requirements file, copy
`app/` (without tests), set `FLAVOUR` and run Uvicorn as `nobody`. `build/DockerfileLocal`
has no `CMD`; `docker-compose.yml` runs `main.py runserver` with `app/` bind-mounted for
hot reload.

## Decisions

- WorkIndia internal packages are not on public PyPI, so views use the local `APIView`,
  logging uses stdlib `logging` with `LOGGING_CONFIG`, and there is no wi-errors envelope.
- Unknown `FLAVOUR` values fail instead of falling back to development, so a misconfigured
  deployment can never run with debug settings.
- Starlette 1.8's `TestClient` warns that `httpx` is deprecated in favour of `httpx2`;
  `httpx` still works, so pytest ignores that one warning.
- Deferred: database, authentication, business endpoints, CI/CD pipelines.
