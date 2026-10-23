# JEVE-BE architecture

How the service is put together. It follows the WorkIndia FastAPI guidelines; how to
write code in it is in [coding-guidelines.md](coding-guidelines.md).

## Application

- `app/app.py` creates the FastAPI instance (`uvicorn app:app`, run from `app/`) and
  mounts `api.views.router` under `API_PREFIX` (`/api/jeve`). API docs are served under the
  same prefix when `DOCS_URL` / `REDOC_URL` / `OPENAPI_URL` are set.
- `app/applifespan.py` runs on startup and shutdown: it applies `config.LOGGING_CONFIG`,
  then validates every context YAML and prompt and builds the review store, orchestrator and
  LLM provider once. A malformed business rule stops the service starting rather than
  surfacing halfway through a client's review.
- `app/main.py` is the CLI (`python app/main.py runserver`), the place for management
  commands later.
- Requests flow view → controller → handler → helper → utils. Views subclass
  `core.apiview.APIView` (a local stand-in for `wifastapiutils.views.APIView`) and are
  registered with `register_route` in `api/views/__init__.py`.
- `GET /api/jeve/health/`, the review endpoints (`POST /reviews/`, `GET /reviews/`,
  `GET /reviews/{id}/`, `DELETE /reviews/{id}/`, `GET /reviews/{id}/events/` as SSE) and
  `GET /je-types/`.

## The review harness

`app/review/` runs one review as 20 numbered steps. The orchestrator owns ordering,
concurrency and progress and **never judges content**: every conclusion comes from a tool or
a routing rule.

- `intake/` → `extract/` → `context/` → `orchestrator/` → `tools/` → `agents/` →
  `guardrails/` → `storage/`. Models in `review/models/` carry data between them; the
  dependency direction is one way, `workbook.py` first and `review_context.py` last.
- Identification runs deterministically first: the layout cache (step 7), then shape
  discriminators over column families (7a), and only then AI-1 (8). AI-1 corroborates; it
  can never move a binding or a number. When the shape settles nothing, the checks run
  against the type the uploader **declared** — recorded as such, scored, and never reported
  as `completed`. When nothing was declared either, the review stores a report naming what
  was ruled out and what to do next rather than ending blank.
- Five model calls always run where a key is configured (AI-1, AI-2, AI-3, AI-4, AI-5) and
  three are conditional (AI-M methodology, AI-R screenshots, AI-X exception clustering).
  Each is single-shot, strictly schema-bound, and validated by a guardrail before its answer
  reaches a report. With no key the arithmetic runs in full and the result names what was
  not judged.
- Step 18 also assembles a **category split work paper**: the working re-added row by row and
  divided by the classes its own classification column holds, each column tied against the
  total the file states above its header, the formula behind each column rewritten in the
  preparer's column names, one row traced, and every check reported with the status step 16
  gave it. Arithmetic and projection only - no model contributes a figure, and the tests
  section is carried rather than recomputed. Nothing in it is named in code: row titles are
  the preparer's headers, categories are whatever the column holds, and figures are formatted
  from Excel's own format string, so a workbook with different classes on a differently
  shaped tab needs no edit. It is written with the record, because its arithmetic is the
  evidence.
- Business knowledge is versioned YAML in `review/context/`, SHA-hashed into every review's
  audit trail and reuse key, so no stored result is served across a rule change.
- Every model call is metered and recorded against the numbered step that made it, so
  `GET /reviews/{id}/` reports cost per step rather than one total. The step comes from a
  table at the call site, not from whatever step was current: AI-M and AI-R fire between
  steps 12 and 13 (as 13a) and would otherwise be billed to a step they are not part of.

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
- The harness reads `config` in one place, `applifespan.py`, which passes every review
  limit into the orchestrator as a required keyword. Nothing under `app/review/` imports
  `config`, and no limit has a default there to drift from the field it mirrors.

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
