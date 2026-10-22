# JEVE-BE coding guidelines

Rules for every change to this service. They follow the WorkIndia backend guidelines
(FastAPI, common and Django basics), adapted where WorkIndia's internal packages
(`wi-fastapiutils-py`, `wi-logkit-py`, `wi-requests-py`, `wi-errors-py`) are not available.

## 1. Where code goes

```text
app/                          # all application code; imports start here (from core.settings import config)
├── api/
│   ├── views/                # endpoints: <area>.py, registered in views/__init__.py
│   ├── controllers/          # <area>_controller.py   (create when first needed)
│   ├── handlers/             # <area>_handler.py      (create when first needed)
│   ├── helpers/              # <area>_helper.py       (create when first needed)
│   ├── utils/                # <topic>_utils.py       (create when first needed)
│   ├── exceptions/           # base.py, <domain>_exceptions.py (create when first needed)
│   ├── commands/             # management/cron commands (create when first needed)
│   ├── pydanticmodels/       # <area>_models.py
│   └── consts.py             # API enums: paths, tags, statuses
├── core/
│   ├── settings/             # base.py, development.py, staging.py, production.py, consts.py
│   ├── apiview.py            # APIView base class for views
│   └── consts.py             # core enums and constants
├── requirements/             # base.txt, development.txt, staging.txt, production.txt
├── tests/                    # unit/ (mirrors app/) and integration/<feature>/
├── app.py                    # FastAPI instance
├── applifespan.py            # startup / shutdown
└── main.py                   # CLI: python app/main.py runserver
build/DockerfileLocal | DockerfileStag | DockerfileProd
```

Create a folder only when the first file for it is needed; no empty placeholders.

## 2. Request flow and layers

```text
HTTP request → View → Controller → Handler → Helper → Utils → View → HTTP response
                 └──────────── Pydantic models carry data between layers ────────────┘
```

| Layer | Does | Never does |
| --- | --- | --- |
| View | Validates input with Pydantic, calls the controller, maps exceptions to HTTP errors, returns a response model, applies auth dependencies | Business logic, database or external calls, calculations |
| Controller | Light coordination: takes a request model, delegates to handlers, returns a response model | HTTP concerns, FastAPI imports, domain work itself |
| Handler | Business logic: rules, decisions, multi-step workflows, transactions | HTTP concerns, generic utilities |
| Helper | Domain-specific work for one domain; external service calls; data access. A class whose `__init__` holds state, with instance methods. Reusable from controllers, handlers and commands | HTTP concerns, orchestrating workflows, mixing domains |
| Utils | Generic, stateless operations (strings, dates, numbers). Classes named `*Utils` with `@staticmethod`s, pure, framework-agnostic | Business logic, I/O, domain knowledge |

Quick decision: HTTP in/out → view. Coordinating steps → controller. A business rule or
decision → handler. Work for one domain or an external system → helper. Generic and
reusable anywhere → utils.

### Views

Views subclass `core.apiview.APIView`: one `@classmethod` per HTTP method, configured with
`@APIView.configure(...)`, then registered in `api/views/__init__.py`.

```python
# api/views/users.py
class UserView(APIView):
    """Creates users."""

    @classmethod
    @APIView.configure(
        response_model=UserResponse,
        status_code=status.HTTP_201_CREATED,
        tags=[ApiTag.USERS],
        summary="Create a user",
    )
    async def post(cls, request: UserCreateRequest) -> UserResponse:
        try:
            return UserController(request).create_user()
        except UserAlreadyExistsError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, exc.message) from exc


# api/views/__init__.py
UserView.register_route(router, ApiPath.USERS, dependencies=[Depends(api_key_auth)])
```

- Paths come from `ApiPath`, are relative to `API_PREFIX` (`/api/jeve`), use kebab-case
  and end with `/`.
- A view that calls blocking code is a plain `def` (FastAPI runs it in a thread pool);
  `async def` views must only await non-blocking calls.

### Controllers, handlers, helpers, utils

```python
# api/controllers/user_controller.py
class UserController:
    def __init__(self, request: UserCreateRequest) -> None:
        self.request = request

    def create_user(self) -> UserResponse:
        return UserHandler(self.request).process()
```

Utils live in `api/utils/<topic>_utils.py` and are exported from `api/utils/__init__.py`.

## 3. Pydantic models

- All structured data is a Pydantic model: request bodies, query parameters, responses
  and data passed between layers. No raw dicts, dataclasses or TypedDicts.
- File: `api/pydanticmodels/<area>_models.py`. Separate request and response models:
  `<Thing>CreateRequest`, `<Thing>Response`, `<Thing>ListQueryParams`.
- Every field has a type and, for public APIs, `Field(..., description=...)` with
  constraints (`min_length`, `ge`...). Every model has a docstring.
- Query-parameter models use `model_config = ConfigDict(extra="forbid")`. Use
  `ConfigDict(from_attributes=True)` to build models from ORM objects.
- Validators only validate; business logic stays in handlers. Never return ORM objects.

## 4. Constants and configuration

- **No magic strings or numbers in code.** Use a `StrEnum`/`IntEnum` for a related set and
  a `Final` constant for a single value, in the `consts.py` of the layer that owns it:
  `api/consts.py`, `core/consts.py`, `core/settings/consts.py`.
- Fine inline: log messages, docstrings, keys required by a library's API, and literal
  expected values in tests.
- **Anything that can differ per environment is a setting**, not a constant: a typed,
  UPPER_CASE field on `BaseConfig` (`core/settings/base.py`) with a safe default,
  overridden in `development.py` / `staging.py` / `production.py` only when it differs.
- Read settings only through `from core.settings import config`; never `os.environ` in
  application code.
- `FLAVOUR` (`dev` | `stag` | `prod`) selects the class; fields are overridden with
  `APP_<FIELD>` environment variables. Secrets have no default and come only from the
  environment; never hardcode or log them.
- Prefer a settings flag over `if config.FLAVOUR == Flavour.PROD` branches.

## 5. Exceptions

- Define domain exceptions in `api/exceptions/`: `base.py` with
  `BaseApplicationError(message, error_code, details)`, one `<domain>_exceptions.py` per
  domain (`UserNotFoundError`, `PaymentFailedError`...), each with a docstring.
- Controllers, handlers and helpers raise specific exceptions; views catch specific ones
  and raise `HTTPException` with the matching status code.
- Never `except Exception:` or bare `except:`, never `raise Exception(...)`, never put
  secrets or stack traces in responses.

## 6. Logging

- `logger = logging.getLogger(__name__)` at module level; never `print`.
- Configuration comes from `config.LOGGING_CONFIG`, applied in `applifespan.py`. The level is
  `APP_LOG_LEVEL`.
- Use `%s` arguments, not f-strings: `logger.info("user created id=%s", user_id)`. Log at the
  right level; never log passwords, tokens or personal data.

## 7. External services (when introduced)

- Calls to other services live in helpers. Create one pooled client per downstream in
  `applifespan.py`, close it on shutdown, and always pass an explicit timeout.
- Base URLs, timeouts and credentials are settings.

## 8. Naming and versioning

- Python files and folders: lowercase / snake_case. Suffixes: `_controller`, `_handler`,
  `_helper`, `_utils`, `_models`, `_exceptions`. Classes: `UserView`, `UserController`,
  `UserHandler`, `UserHelper`, `DateUtils`, `UserResponse`.
- Name by behaviour, never by version: `PricingControllerWithGeoDiscount`, not
  `PricingControllerV2`. Prefer composition (inject a strategy or helper) over subclassing.
- URLs are unversioned (`/api/jeve/users/`). Only a breaking contract change gets a new
  version (`/api/jeve/v2/users/`), with the whole stack (view, controller, models)
  versioned by descriptive names.

## 9. Code style

- Python 3.13, full type hints (mypy strict), docstrings on modules, classes and public
  methods.
- Absolute imports from `app/`, grouped stdlib → third-party → local (ruff sorts them).
- Small, single-purpose functions; inject dependencies through constructors or FastAPI
  `Depends`, so tests can replace them.

## 10. Tests

- Unit tests mirror the code: `app/api/helpers/user_helper.py` →
  `app/tests/unit/api/helpers/test_user_helper.py`. Integration tests go in
  `app/tests/integration/<feature>/` and call endpoints through `TestClient`.
- Every test folder has an `__init__.py`; shared fixtures live in `app/tests/conftest.py`.
- Plain pytest, Arrange-Act-Assert, one behaviour per test, named
  `test_<unit>_<behaviour>_<expected>`. Use `pytest.mark.parametrize` for variations.
- No real network or database: use fakes, `unittest.mock` and `app.dependency_overrides`.
- New module → unit tests. New endpoint → integration tests.

## 11. Dependencies, Docker and commands

- Dependencies: `app/requirements/base.txt` (shared runtime); `development.txt` (plus dev
  tools), `staging.txt` and `production.txt` each start with `-r base.txt`. Pin with `==`.
- Dockerfiles in `build/` stay about 10 lines: base image → copy requirements →
  `pip install` → copy `app/` → `FLAVOUR` → `CMD`.
- Commands: `make init`, `make start` / `make stop` (Docker), `make runserver` (host,
  auto-reload), `make runtests`.

## 12. Before you push

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

All four must pass. Update `README.md` (max 45 lines) and `docs/architecture.md` when
behaviour or structure changes.
