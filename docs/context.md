# JEVE-BE working context

Read this before any task; it replaces exploring the repo. Open only the files the task
touches. In the same change, update the Map, Decisions and Log below so the next session
starts from here.

## Service

- JEVE (Journal Entry Validation & Evidence) backend: FastAPI, Python 3.13, uv-managed
  `.venv`, pinned deps in `app/requirements/*.txt`. No database or auth.
- Endpoints: only `GET /api/jeve/health/`. LLM providers exist but no endpoint uses them.
- Code under `app/` imports top-level: `from core.settings import config`.
- Commands: `make init | runserver | start | stop | runtests`. Before finishing, run
  `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`, `uv run pytest`.

## Map

```text
app/
  app.py                FastAPI app; router mounted at config.API_PREFIX (/api/jeve)
  applifespan.py        startup: dictConfig(config.LOGGING_CONFIG); shutdown log
  main.py               typer CLI: python app/main.py runserver
  core/
    apiview.py          APIView: one classmethod per HTTP verb, @APIView.configure(...),
                        View.register_route(router, path)
    consts.py           HttpMethod, runserver host/port
    settings/
      __init__.py       get_settings(): FLAVOUR env (dev|stag|prod, unknown -> error) picks
                        the class; `config` singleton; AppConfig = Dev | Stag | Prod
      base.py           BaseConfig (APP_ env prefix, frozen): DEBUG, APP_NAME, API_PREFIX,
                        LOG_LEVEL, DOCS/REDOC/OPENAPI_URL, LOGGING_CONFIG. Service-agnostic
      development.py    debug, docs, reads ./.env, OPENAI_SETTINGS
      staging.py        docs on, OPENAI_SETTINGS
      production.py     DEBUG locked False, OPENAI_SETTINGS
      consts.py         Flavour, LogLevel, OpenAISettings TypedDict (BASE_URL,
                        API_KEY: SecretStr | None, TIMEOUT_SECONDS), env constants
  api/
    consts.py           ApiPath, ApiTag, HealthStatus, ErrorCode, LLMProviderName,
                        OpenAIModel (GPT_5_5="gpt-5.5", GPT_4O="gpt-4o"; value = Azure
                        deployment name), ReasoningEffort, TOKENS_PER_MILLION
    views/__init__.py   router; registers every view (HealthView -> ApiPath.HEALTH)
    views/health.py     HealthView.get -> HealthResponse
    pydanticmodels/
      health_models.py  HealthResponse
      llm_models.py     LLMPricing (USD per 1M tokens), LLMRequest (input, instructions,
                        reasoning_effort, max_output_tokens), TokenUsage, LLMCost,
                        LLMCompletion (text, usage, response_id, incomplete_reason),
                        LLMResponse (completion + provider, model, cost)
    exceptions/
      base.py           BaseApplicationError(message, error_code: ErrorCode, details)
      llm_exceptions.py LLMConfigurationError, LLMProviderError
    helpers/llmproviders/
      base_llm_provider.py    BaseLLMProvider(model, pricing).generate(request): calls
                              abstract _complete(), prices it, logs one "llm call ..." INFO
                              line; logs and re-raises LLMProviderError
      openai_llm_provider.py  OpenAILLMProvider(client: AsyncOpenAI, model: OpenAIModel);
                              create_client() from config.OPENAI_SETTINGS; Responses API;
                              wraps openai.APIError in LLMProviderError
      llm_pricing.py          OPENAI_PRICING: OpenAIModel -> LLMPricing
    utils/
      metering_utils.py TokenMeteringUtils (uncached_input_tokens, sum_usage),
                        CostMeteringUtils (calculate_cost, sum_costs); exported from
                        api/utils/__init__.py
  tests/
    conftest.py         autouse isolated_environment (drops FLAVOUR/APP_* env, chdir to
                        tmp); `client` TestClient fixture
    unit/               mirrors app/ (api/helpers/llmproviders, api/utils, core/settings)
    integration/health/ endpoint tests through TestClient
docs/                   coding-guidelines.md (full rules), architecture.md, context.md
docker-compose.yml      dev stack, app/ bind-mounted; build/Dockerfile* are git-ignored
Makefile                init, start, stop, runserver, runtests only
.github/workflows/      empty files until a pipeline is requested
```

## Rules in brief

Full text in `docs/coding-guidelines.md`; open it only when a detail matters.

- Layers: view (HTTP only) -> controller (coordinates) -> handler (business rules) ->
  helper (one domain or external service; stateful class) -> utils (generic, stateless
  `*Utils` with staticmethods). Data between layers is Pydantic models in
  `api/pydanticmodels/<area>_models.py`.
- No magic strings or numbers: StrEnum or `Final` in the owning `consts.py`. Anything
  that differs per environment is a setting, read via `config`, never `os.environ`.
- Errors: `<Domain>Error(BaseApplicationError)` in `api/exceptions/<domain>_exceptions.py`;
  views map them to HTTPException. Never `except Exception`.
- Logging: `logger = logging.getLogger(__name__)`, `%s` arguments, never log secrets.
- Tests: unit tests mirror `app/`, every test folder has `__init__.py`, names
  `test_<unit>_<behaviour>_<expected>`, no network (mock the client). Async tests use
  `pytestmark = pytest.mark.anyio`.
- mypy strict, ruff (line length 100). Ask before adding dependencies or infrastructure.
  Build only what the task needs. README stays at 45 lines or fewer.

## Decisions

These are the user's choices. They override the guidelines; do not flag them in reviews.

- External-service settings are a dict in each flavour file (`OPENAI_SETTINGS = {...}` in
  development, staging and production), not fields on BaseConfig. Their keys are typed by
  a TypedDict in `core/settings/consts.py`. `config` is typed as the AppConfig union, so
  mypy fails if a flavour lacks a setting. RUF012 is ignored for the flavour files.
- The environment replaces the whole dict as JSON: `APP_OPENAI_SETTINGS='{"BASE_URL": ...,
  "API_KEY": ..., "TIMEOUT_SECONDS": 300}'`. There is no per-key merge (rejected as extra
  complexity). API_KEY is `None` in code.
- LLM: Azure AI Foundry's OpenAI v1 endpoint, Responses API, async `AsyncOpenAI`. Use
  gpt-5.5 for reasoning (set `reasoning_effort`) and gpt-4o for cheap work that needs no
  reasoning. The answer is `response.output_text`: `output[0]` is the reasoning item.
- Providers live in `api/helpers/llmproviders/`, one `<vendor>_llm_provider.py` per vendor
  subclassing BaseLLMProvider, with classes named `*LLMProvider`.
- Prices live in `llm_pricing.py`, a Python module rather than a setting. They are Azure
  Global Standard list prices from 2026-10, USD per 1M tokens (input / cached / output):
  gpt-5.5 $5 / $0.50 / $30, gpt-4o $2.50 / $1.25 / $10.
- Token and cost metering is general code in `api/utils/metering_utils.py`. Providers
  keep no running totals; callers sum their responses' usage and cost.
- The shared AsyncOpenAI client is not created in `applifespan.py` yet. The first endpoint
  that uses an LLM adds it there: call `create_client()` once and close it on shutdown.
  Until then the app starts without an API key.
- Keep changes small: no speculative state, layers or files. The user prunes them.

## How to

- Add an endpoint: path and tag in `api/consts.py` -> models in `pydanticmodels/` -> view
  in `api/views/<area>.py` (APIView subclass) -> register it in `views/__init__.py` ->
  controller and handler as needed -> integration test in `tests/integration/<feature>/`.
- Call an LLM from an agent. The agent takes a `BaseLLMProvider`, so the caller picks
  the model:
  ```python
  client = OpenAILLMProvider.create_client()  # once; shared connection pool
  llm = OpenAILLMProvider(client, OpenAIModel.GPT_5_5)  # or GPT_4O
  r = await llm.generate(
      LLMRequest(instructions="...", input="...", reasoning_effort=ReasoningEffort.HIGH)
  )
  r.text, r.usage.total_tokens, r.cost.total_usd
  ```
- Add an LLM vendor:
  1. Write `llmproviders/<vendor>_llm_provider.py`, implementing `_complete`: map usage
     OpenAI-style (input includes cached, output includes reasoning) and wrap vendor
     errors in LLMProviderError.
  2. Add a model enum to `api/consts.py` and its prices to `llm_pricing.py`.
  3. Add a `<VENDOR>_SETTINGS` dict to all three flavour files, with its TypedDict.
  4. Add tests in `tests/unit/api/helpers/llmproviders/`.
- Add a setting: a service-wide one is a field on BaseConfig; an external service's is a
  dict in each flavour file.

## Log

Newest first, one line per change. Add a line with every change.

- 2026-10-18 `7c7517d`: OpenAI LLM providers (gpt-5.5, gpt-4o on Azure AI Foundry), token
  and cost metering, exceptions package, OPENAI_SETTINGS per flavour, `openai==3.24.0`.
- `47af5c0`: FastAPI skeleton, health endpoint, flavour settings (FLAVOUR dev/stag/prod).
