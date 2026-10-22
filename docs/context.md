# JEVE-BE working context

Read this before any task; it replaces exploring the repo. Open only the files the task
touches. In the same change, update the Map, Decisions and Log below so the next session
starts from here.

## Service

- JEVE (Journal Entry Validation & Evidence) backend: FastAPI, Python 3.13, uv-managed
  `.venv`, pinned deps in `app/requirements/*.txt`. No database or auth.
- Endpoints: `GET /health/`, `POST /reviews/`, `GET /reviews/`, `GET /reviews/{id}/`,
  `DELETE /reviews/{id}/`, `GET /reviews/{id}/events/` (SSE), `GET /je-types/`. All under
  `config.API_PREFIX`
  (`/api/jeve`).
- All 20 numbered steps run, plus 7a, 13a and 13b. Everything is deterministic except
  eight model calls — five that always run where a key is configured (AI-1, AI-2, AI-3,
  AI-4, AI-5) and three conditional (AI-M methodology, AI-R screenshots, AI-X clustering).
  Each is optional: with no API key the arithmetic runs end to end and `not_run_for(run)`
  names what did not happen to that file.
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
                        LOG_LEVEL, DOCS/REDOC/OPENAPI_URL, LOGGING_CONFIG, and the six
                        review limits (MAX_UPLOAD_MB, MAX_ACTIVE_REVIEWS, TOOL_WORKERS,
                        REVIEW_DEADLINE_S, STRUCTURE_CONTEXT_MAX_TOKENS, FIGURE_SAMPLES),
                        every one of them read by applifespan. Service-agnostic
      development.py    debug, docs, reads ./.env, OPENAI_SETTINGS
      staging.py        docs on, OPENAI_SETTINGS
      production.py     DEBUG locked False, OPENAI_SETTINGS
      consts.py         Flavour, LogLevel, OpenAISettings TypedDict (BASE_URL,
                        API_KEY: SecretStr | None, TIMEOUT_SECONDS), env constants
  api/
    consts.py           ApiPath, ApiTag, HealthStatus, RiskRating (an upload input),
                        ErrorCode, LLMProviderName, OpenAIModel (GPT_5_5="gpt-5.5",
                        GPT_4O="gpt-4o"; value = Azure deployment name), ReasoningEffort,
                        TOKENS_PER_MILLION
    views/__init__.py   router; registers HealthView, ReviewsView, ReviewView,
                        ReviewEventsView
    views/reviews.py    the four review routes; one ErrorResponse shape for every 4xx/5xx.
                        A reuse hit is answered with the stored result it reused, under the
                        new upload's id, with reused_from naming the original
    views/health.py     HealthView.get -> HealthResponse
    pydanticmodels/
      health_models.py  HealthResponse
      review_models.py  the HTTP contract: ReviewAccepted, ProgressEvent, ReviewDetail
                        (incl. je_type_source: shape | declared | unknown),
                        ReviewSummary, WorkbookView, AreaView (+status), CheckView,
                        EvidenceView.of(), AICallCostView.of(), CostView (+calls),
                        ErrorResponse, area_status(), stored_check(). snake_case end to
                        end; separate from review/models/ on purpose
      llm_models.py     LLMPricing (USD per 1M tokens), LLMRequest (input, instructions,
                        reasoning_effort, max_output_tokens), TokenUsage, LLMCost,
                        LLMCompletion (text, usage, response_id, incomplete_reason),
                        LLMResponse (completion + provider, model, cost, latency_ms)
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
  review/               JE review harness, steps 2-20. Three rules hold throughout:
                        numbers come only from Python; a model never sees the workbook;
                        anything unproven is never a pass
    consts.py           WorkbookFormat (.xlsx/.xlsm/.xlsb), zip-safety limits, header
                        detection thresholds, sampling and trimming caps
    intake/
      gatekeeper.py     step 2: validate_upload(name, bytes, max_bytes) -> UploadOk.
                        Size, ZIP magic, zip-bomb/traversal guards, OOXML members. The
                        extension picks the reader; it never proves the file is a workbook
      reuse.py          step 3: file_digest(), reuse_key(file_sha, je_type, versions)
    extract/
      excel_reader.py   step 4: read_workbook(source, format). openpyxl two-pass (values +
                        formulas) for xlsx/xlsm; pyxlsb values-only for xlsb with
                        formulas_available=False. Decimal via repr(float), never
                        Decimal(float). Positions come from enumeration: a read-only
                        worksheet yields EmptyCell with no coordinates
      pictures.py       step 4: extract_pictures() walks the package (sheet -> drawing ->
                        anchor -> image part) because a read-only openpyxl load exposes no
                        drawings. _parse() refuses an oversized part or any part declaring a
                        doctype or entity: the file is an upload, and those declarations are
                        what make the expansion attacks work
      profiler.py       step 5: profile() -> WorkbookProfile. Tables (widest-header-first
                        claim over candidate rows at a population discontinuity), column
                        types/totals, R1C1 shapes, lineage, defects (typed-over formulas)
      structure_context.py  step 6: build_structure_context() + layout_fingerprint()
    context/            business knowledge, loaded and SHA-hashed once at startup
      loader.py         ContextLoader: validates every YAML, fails fast naming the field.
                        .rules/.je_type()/.catalogue (shallow+uniform, what AI-1 sees)/
                        .by_precedence(). ContextError
      prompts.py        PromptRegistry: prompts/<AICall>/v<n>.md, highest version wins,
                        STATIC_BLOCK prefixed to each (digest covers it), .versions
      aliases.py        ALIASES: meaning -> header texts seen to carry it; resolve_meaning(),
                        known_meanings(). Its own module because both identification tiers
                        read it and the binder would otherwise import the identifier back
      identify.py       step 7a, tier 2: identify(je_types, profile, data) -> Identification
                        (je_type, variant, confidence, matched, corroborating, unobservable,
                        rejected). Signals are column meanings or PREDICATES; an
                        unobservable one is always false, so it can neither satisfy a
                        requires_all nor reject a type. Sheet names are never a signal.
                        Corroboration found only on the posting tab earns no uplift
      binder.py         step 7a: ROLE_RULES (precedence + rejects) -> BindResult with the
                        SheetMap, what could not be bound, unresolved headers, and the
                        Identification
      caches.py         LayoutCache (written only when map_problems == 0),
                        MethodologyCache, PictureCache; idle-TTL, thread-safe, keys
                        length-prefixed
      business_rules.yaml   credits, needs_review_cap, bands, tolerance, trend, pii
      je_types/vacation_accrual_france.yaml   v2. 8 areas, 18 checks (14 by figures),
                        discriminators, methodology, accounts, population, evidence
      prompts/          AI-1,2,3,4,5,R,M,X
    agents/           every model call. None of them can change a number
      projections.py    the AI boundary, enforced in code: no workbook, no employee data
                        (_SAMPLE_KEYS is an allowlist so a new tool fails closed), no weights
      schemas.py        VERDICTS_SCHEMA, CHALLENGES_SCHEMA
      understand.py     step 8, AI-1: a blind second reading of what the workbook is.
                        Corroborates the type; never moves a binding
      judging.py        steps 14-15, AI-3/AI-4. call_model() is the one request path every
                        agent uses. _clamp() discards a challenge looser than the verdict
      summary.py        step 18, AI-5. headline_for() is always code's; template() writes
                        the whole report from the outcomes when no model answers
      clustering.py     step 13b, AI-X. Groups exceptions by cause above the business's
                        threshold; reconciles() reports groups that do not add up
      methodology.py    AI-M. Reads the methodology tab's prose into parameters, which
                        methodology_drift diffs against the approved basis. Cached on the
                        tab's own words, never on the layout: the words are the finding
      picture.py        AI-R. Reads a pasted screenshot; worth_reading() skips icons.
                        legible=false is a real answer, and the register reports "nobody
                        looked" and "looked and could not read it" differently
    guardrails/
      map_check.py      step 9: verifies AI-1 against the workbook. Cited cells must exist
                        and hold something; agreement cited only from the posting tab earns
                        nothing; a dispute drops the confidence and sends it to a person
      plan_check.py     step 12: a step is dropped unless the tool exists, every arg is one
                        it declares, its table is bound, and no other step claims the check
      answer_check.py   step 16: citations exist, numbers trace, a fail needs an exception,
                        a pass needs complete coverage, the figure wins on figures-decided
      final_check.py    step 19: every number in the report traces to the score, an area
                        score, a cited figure, or a count over the outcomes. Untraced
                        numbers are listed on the report, never removed
    orchestrator/
      orchestrator.py   ReviewOrchestrator: one asyncio task per review, CPU-bound parsing
                        in an executor, SSE fan-out, deadline, capacity, MAX_REMEMBERED
                        finished runs. Owns step order and nothing else. Every limit is a
                        required keyword, passed in from `config`. new_review_id().
                        _AI_STEPS charges each model call to its numbered step;
                        entry_identity(), _evidence_register(), _period_from() assemble
                        the header, the register and the period for a reader.
                        _STEP_WEIGHT is the progress bar and must not decrease along
                        _STEP_ORDER; _tightened() counts the conclusions AI-4 made
                        stricter, for the step 15 event. forget() is what DELETE calls so
                        a removed review stops being answered from memory
      diagnosis.py      diagnose(): the report a review gets when nothing could be checked
                        - which candidate the shape ruled out, which table could not be
                        bound and with which column spellings, and what to do next
      planner.py        step 11: plan_from_recipes() for every check that states one;
                        propose_steps() asks AI-2 only about checks with no recipe, and a
                        step for any other check is discarded
      runner.py         step 13: runs the validated plan on a thread pool sized by
                        config.TOOL_WORKERS; figure ids are assigned in plan order
      rules.py          step 16: outcome_for() turns figures + any verdict into a status
      scoring.py        step 17: score_review(), four methods from the JE type file
    storage/reviews.py  ReviewStore: write-once JSON records + listing and reuse indexes,
                        atomic rename. No update method exists, by design. delete()
                        withdraws a record -- indexes first, then the file -- and purges the
                        reuse entry by value; withdrawing is not amending
    tools/base.py       Tool contract: pure, never raises upward, never sets a status.
                        BoundTable resolves meanings via the verified map. run_tool() is
                        also where a figure's exemplars are capped at ctx.figure_samples.
                        assign_figure_ids(), worst_first()
    tools/registry.py   the 17 tools a plan may name, and catalogue() for AI-2
    tools/calculation.py    balance, control_totals, rollup_chain
    tools/reperformance.py  recalc_accrual, recalc_leave_balance, negative_scan,
                        classification_check, population_reconcile
    tools/salary.py     recalc_salary_per_day, salary_validate
    tools/entry.py      account_mapping, reversal_check, period_agreement,
                        evidence_register, formula_health, pii_scan
    tools/methodology.py    methodology_drift: the period stamp by code, the stated basis
                        by diffing AI-M's reading against the approved MethodologySpec
    utils/sheet_utils.py  SheetUtils: column_letter/index, normalize_header, estimate_tokens
    models/             frozen pydantic v2, re-exported from review/models/__init__.py.
                        Dependencies one way: workbook -> everything, review_context last
      workbook.py       CellRef, TableRef, ColumnType, DefectKind, SheetProfile,
                        TableProfile, ColumnProfile, FormulaShape, Defect, PictureRef,
                        WorkbookProfile (step 5), SheetSummary, StructureContext (step 6)
      sheet_map.py      SheetRole, ChainHop, CalculationChain, SheetMap (AI-1 output)
      plan.py           PlanSource, PlanStep, ExecutionPlan
      evidence.py       RowCoverage (gate rule 1), Figure (.supports_pass), EvidenceItem,
                        EvidenceStatus, PictureReading, PIIFinding, PIIKind
      verdicts.py       Status + strictness()/stricter(), ReviewFlag, Finding, Verdict,
                        Challenge, CheckOutcome
      result.py         ScoringMethod, AreaScore, Score, ReportPayload, EntryIdentity,
                        AICallRecord (+step, name, model), ToolRunRecord, AuditTrail,
                        ReviewResult (+entry)
      review_context.py ReviewContext: the mutable case file; figures_for(),
                        decisive_figure()
      workbook_data.py  Cell/Sheet/PictureData/WorkbookData: the parsed workbook, in
                        memory only. Deliberately apart from workbook.py, which may be
                        persisted - a screenshot's bytes never are
      context.py        JETypeContext, BusinessRules, CheckSpec, EvidenceSpec, VariantSpec,
                        DiscriminatorSpec, MethodologySpec, ContextVersions. extra="forbid"
                        so a YAML typo is a startup error, not a rule that does nothing
  tests/
    conftest.py         autouse isolated_environment (drops FLAVOUR/APP_* env, chdir to
                        tmp); autouse no_live_model (config is built at import, so clearing
                        the env does not unconfigure a developer's key); `client`,
                        `stub_model`, `stub_client`; store_of()/orchestrator_of()
    doubles.py          StubLLMProvider: answers each call from its schema_name and records
                        what crossed the boundary, so judging and reporting are assertable
    unit/               mirrors app/ (api, core, review/models, intake, extract, context,
                        agents, guardrails, orchestrator, tools, utils)
    integration/        health and the whole review flow through TestClient
    golden/             steps 2-6 against the real workbooks in skills/docs (@pytest.mark.
                        golden, ~50s). Known gaps are strict xfails, so fixing one fails the
                        suite until its marker goes
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
- A model call's cost is attributed to a numbered step by `_AI_STEPS` at the call site, not
  by reading `run.step`. AI-M and AI-R fire between steps 12 and 13 (announced as 13a), so
  the current step would bill them to a step they are not part of. Two calls on one step
  stay two records: merging them hides which of the pair is the expensive one.
- `LLMResponse` carries `latency_ms`, measured inside `generate()`. A caller timing its own
  await also times whatever else it is doing concurrently.
- The shared AsyncOpenAI client is not created in `applifespan.py` yet. The first endpoint
  that uses an LLM adds it there: call `create_client()` once and close it on shutdown.
  Until then the app starts without an API key.
- Keep changes small: no speculative state, layers or files. The user prunes them.
- Every setting on BaseConfig has a reader. `applifespan.py` is the only module that reads
  `config` for the harness; the six review limits are required keywords on
  ReviewOrchestrator, so mypy fails if one stops being passed and no default can drift from
  the field's. A setting nothing reads was deleted rather than left documented
  (MAX_PLAN_STEPS, FISCAL_FIRST_MONTH: no plan-length cap and no proration exist, and the
  `.env` comment on the fiscal month described a guard that was never built).

### The review harness

- Area weights in `vacation_accrual_france.yaml` are business-confirmed (2026-10-20):
  evidence 10, population 15, classification 10, salary 10, leave 10, re-performance 25,
  JE reconciliation 15, period 5. Methodology drift and manual updates are unscored
  observations.
- Identification runs in three tiers and a model is the last of them: the layout cache
  (step 7), the shape discriminators (7a), then AI-1 (8). AI-1 can never move a binding or
  a number — agreement raises the recorded confidence, disagreement sends the review to a
  person. Both outcomes leave the arithmetic untouched.
- A figures-decided check always runs the recipe its JE type states. AI-2 is asked only
  about checks with no recipe, and a step it proposes for any other check is discarded in
  code, so how an arithmetic check is performed is never chosen by the system that
  interprets the result.
- Step 19 **lists** an untraced number rather than removing it: deleting it would leave a
  sentence reading as though it had been verified.
- A review that cannot be identified, or whose two readings disagree, still runs every
  check and still scores — it just never reports as `completed`. The shape decides the type;
  failing that, the type the uploader **declared** is used, recorded as
  `je_type_source="declared"` with confidence 0, kept out of the layout cache, and carrying
  a caveat at the top of the report's gaps and next steps. Only when the shape settles
  nothing *and* nobody declared a type does the arithmetic not run.
- A reuse hit (step 3) serves the **stored result** it matched. The run behind it never
  reads the workbook, so answering the detail from that run reported a file that had been
  reviewed in full as having no type, no score and no report — the one bug behind the blank
  report people actually saw. `reused_from` on the detail names the review it came from.
- A review that could not be checked is still a work product: it is written to the store
  like any other, and its report names what was ruled out, which tables could not be bound
  and with which header spellings, and what the uploader can do next (`diagnosis.py`). A
  blank report at 100% is the one outcome a reviewer cannot act on.
- The layout cache is in memory and per worker. A miss costs two model calls and never a
  wrong answer, so it does not earn a database. It is keyed on the version of the type that
  was *identified*, not the one the uploader typed.
- AI-M and AI-R feed no arithmetic. They produce readings that a *tool* then compares
  against something already established — the approved methodology, and facts code read from
  the workbook. A misreading is a false finding a reviewer clears, never a wrong number.
- Every model schema must satisfy the provider's strict contract: every property in
  `required`, `additionalProperties: false` everywhere, and a field with no answer typed as
  nullable rather than omitted. `tests/unit/review/agents/test_schemas_are_strict.py` checks
  all eight at once, because a schema that breaks the contract fails only when its call
  first fires.
- Vacation accrual area weights, confirmed by the business 2026-10-20: re-performance 25,
  JE reconciliation 15, population 15, classification 10, salary 10, leave 10, evidence 10,
  period 5. PII and trend are deliberately *not* scored areas for this JE type - PII because
  employee data on the calculation tab is expected and scoring it would cap every review at
  90, trend because max_change_pct is unset. Both still run; PII as an observation that
  flags personal data reaching the posting tab.
- A cell holding a formula is never blank, even with no cached value. A workbook written by a
  tool rather than by Excel has formulas and no cached values, and treating those as empty
  hides every table on the sheet.
- Headers are found by *type contrast*, not by text share: a real posting row is mostly text
  (company code, account, currency) with one amount, so "the row below must be numeric" is
  wrong. A header differs from the row beneath it in at least one column's type.
- Label uniqueness is never a header signal. GPAY repeats 3 of 42 labels and JE Create reuses
  8 across 14, so any threshold tight enough to matter rejects one or the other.
- Tables are claimed **widest header first**, not first-match: Batch JE's six-column preamble
  of batch options qualifies as a header and sits above the real 26-column line header, so
  first-match claimed the sheet for metadata. Candidate rows are only those at a population
  discontinuity (pop[r] != pop[r-1]), which is what keeps a 133k-row sheet cheap to scan.
- Trimming the structure context never drops a sheet. AI-1 assigns a role to every sheet it
  sees, so one it never sees is a tab that silently has no role; detail goes instead.
- reuse_key length-prefixes each field before hashing: joining on a separator alone lets a
  JE type named "a|x=b" collide with type "a" plus version "x=b".
- The review harness follows the LLD/artifact vocabulary (view -> orchestrator ->
  tools/context/agents -> models) rather than the guidelines' controller/handler naming, and
  its models live in `app/review/models/`, not `api/pydanticmodels/`. Request and response
  models for the endpoints will still go in `api/pydanticmodels/review_models.py`.
- Money is `Decimal` everywhere in `review/`. Never float.
- Gate rule 1: a `Figure` may back a `pass` only when `coverage.complete` — that is
  `rows_tied == rows_bound == rows_total`. An agreeing total over partly-bound rows is the
  failure this prevents, because a neighbouring column can hit the same total while every row
  beneath it is wrong. `Figure.supports_pass` is the single place that decides this.
- `Status` carries an explicit strictness rank (`strictness()`, `stricter()`) because AI-4 may
  only move a status equal-or-stricter and the stricter side wins a disagreement. Enum
  declaration order is not relied on.
- Figures carry `financial_impact` so exceptions rank by money, not by count, and samples hold
  cell references and row numbers — never employee identifiers or salary values.
- The evidence register is built from the register *tool's* own figure, never re-derived in
  the orchestrator. The tool decided what satisfies a layer, and a second opinion upstream is
  how a report comes to disagree with the check it reports on. A layer no check examined stays
  missing and says so — it must never read as one nobody needed.
- `EvidenceRegisterTool` emits a sample per expected layer, gaps first. All of them, because a
  register listing only what is missing hides what the conclusion rests on; gaps first because
  the figure sample cap must never be the thing that hides one.
- The entry header is entirely the system's own. `document_number` is the name the workbook
  was uploaded under, less its suffix (`236264.xlsx` is document 236264); `risk_rating` is
  the `risk` on the band the score reached; the rest is recognised (type, variant,
  confidence, process, sub-process, period). Neither of the first two is an upload form
  field any more.
  The rating *was* an input, on the reasoning that deriving it from the band turns one
  number into two that always agree. That reasoning still holds and the consequence is now
  carried instead of avoided: the rating is the score restated, the entry header says so in
  as many words, and nothing may present the two as corroborating each other. What tipped
  it was that the field was blank on essentially every real review, and a permanently empty
  risk rating in an audit header is worse than a frank restatement of the score.
  `Score.risk_rating` is optional for one reason only - records are write-once, so a review
  stored before the field existed can never be given one, and a required field would make
  all of them unreadable rather than merely incomplete.
- The period on the header is taken from the `period_agreement` figure rather than re-read, so
  the header and that check can never disagree about which period the entry is in.
- `AreaView.status` is the strictest status among an area's checks, carried alongside the
  score rather than inferred from it. An exception-rate area with three bad rows in forty
  thousand scores in the nineties and still contains a failure.
- A re-read of a stored review is deliberately thinner than the live view. Figures are working
  memory and are not persisted, so each check comes back with its status and no conclusion;
  the questions are restored from the versioned JE type file, which is context rather than run
  state. Reconstructing a plausible conclusion nobody wrote would be a fiction in an audit
  work product.

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

- 2026-10-22 (3): em dashes removed from everything a reader sees. Four runtime strings
  carried one into the UI: the headline in `summary.py` and its stored twin in
  `orchestrator._headline_for` (now "32.5 out of 100, High risk"), `diagnosis.py`'s
  "Ruled out: <reason>", and the tie-out summary in `calculation.py`, which now joins with
  a comma like the branch beside it. The AI-1, AI-M and AI-X v2 prompts are clean too: each
  field in their output specs is introduced with a colon, which is punctuation only and
  changes no instruction. Editing a prompt changes its sha, so `version_string` moves with
  it and the layout and methodology caches invalidate themselves; the reuse index keys on
  je_type, rules and harness versions only, so identical uploads still reuse. Records
  written before this keep their own wording, which is what write-once means. 510 tests,
  ruff/mypy clean.

- 2026-10-22 (2): a review can be deleted. `ReviewStore.delete()` removes the record, its
  listing row and any reuse-index entry pointing at it -- indexes first, record second, since
  a listing row whose record is already gone points at a 404 while an orphaned record file is
  invisible. `DELETE /reviews/{id}/` is 204, 404 on an unknown id, and 409
  (`REVIEW_RUNNING`) on one still in flight: deleting mid-run would orphan the task writing it
  and step 20 would re-create the record. It also calls `orchestrator.forget()`, without which
  the review stays in memory and both `GET` and the listing keep answering from it. Deleting
  is not a loophole in the write-once record and must not become one -- a record can be
  withdrawn, never amended. 510 tests, ruff/mypy clean.

- 2026-10-22: the progress stream is now written for the person waiting on the review
  rather than for the log. Backend: `_STEP_WEIGHT` had steps 5 and 6 at 62 and 70, above
  step 7a at 60, so every review visibly lost ten points of progress on recognising its own
  layout -- reweighted to 52 and 56, with `_STEP_ORDER` and
  `test_step_weights_never_go_backwards` pinning the ordering. Steps 11, 14, 15 and 17 now
  emit `step_finished` with detail they never carried, so the stream can say how the plan
  was arrived at (`from_cache`, `proposed_by_model`), how many conclusions were reached, how
  many of them the second opinion tightened (`_tightened`, which ignores a challenge that
  would loosen), and the score as soon as it exists rather than only once the report is
  written. No new dependency, no schema change: SSE detail is additive and an older client
  ignores what it does not read. 497 tests, ruff/mypy clean.

- 2026-10-21 (5): a review that could not be checked no longer ends as a blank report, and
  a *second upload of the same file* no longer does either — the reuse hit at step 3 copied
  only the status and score onto a run that never read the workbook, and the detail view
  answered from that run, so the page showed "Not recognised", no score and 0.0s for a file
  already reviewed in full. The detail now comes from the stored result, with `reused_from`.
  An unidentified workbook no longer ends as a blank report either. Found from a
  live upload that finished at 100% with no type, no checks, no score and nothing to act on:
  the two bail-outs at steps 7a and 9 returned without a report and without storing anything,
  and the type the uploader declared was never used for anything but the reuse key. Now:
  `orchestrator/diagnosis.py` writes the "could not be checked" report (candidates ruled out,
  unbound tables with the header spellings that would bind them, unresolved headers, next
  steps) and the run is persisted like any other; `_spec_for()` falls back to the declared
  type so the checks run, with `JETypeSource` on the wire, confidence 0, status pinned to
  `needs_review`, no layout caching and a caveat leading the report; `summarise(steps=...)`
  carries next steps code insists on; map_check no longer claims a type "was recognised from
  the shape" when none was; AuditTrail keeps `je_type_source` and `duration_ms`, so a re-read
  stops reporting every stored review as 0.0s. 490 tests, ruff/mypy clean.

- 2026-10-21 (4): the result a reviewer actually reads. `ReviewDetail` gains the entry header
  (document_number, risk_rating, je_type_name, confidence, corroborated, process, sub_process,
  period), `observations` for the unscored checks, a populated `evidence` register and
  `cost.calls` — every model call metered against its numbered step. New: `EntryIdentity`,
  `AICallCostView`, `RiskRating`, `_AI_STEPS`, step 13a announced, `latency_ms` on
  `LLMResponse`, `expected` on every evidence layer in the business file. `from_result` now
  projects areas, observations, the register and the per-call cost, so a re-read after the run
  is evicted no longer returns an empty page. Found on a live run: `period_agreement` sampled
  a bare period number, so the header read "08" — it now carries the fiscal year where the
  entry has exactly one ("2026 P07"). Verified live against both client workbooks: 7 and 9
  calls across 6 steps, $0.236 and $0.279. 474 tests, ruff/mypy clean.

- 2026-10-21 (3): the review limits now reach the code. `config.TOOL_WORKERS` sizes step
  13's pool (was `MAX_WORKERS` in `runner.py`), `config.STRUCTURE_CONTEXT_MAX_TOKENS` caps
  what AI-1 sees (was `_STRUCTURE_MAX_TOKENS` in `orchestrator.py`) and
  `config.FIGURE_SAMPLES` caps a figure's exemplars, now applied in `run_tool` against
  `ToolContext.figure_samples` rather than in `BaseTool.figure`, which has no context.
  Every ReviewOrchestrator limit is a required keyword. Deleted: `MAX_PLAN_STEPS` and
  `FISCAL_FIRST_MONTH`, which nothing read, and their `.env` lines. 447 tests,
  ruff/mypy clean.

- 2026-10-21 (2): the conditional calls and the numbered flow closed out. New:
  `agents/clustering.py` (AI-X, threshold in business_rules.yaml v2),
  `agents/methodology.py` (AI-M) with `MethodologyCache` and a basis diff in
  `methodology_drift`, `agents/picture.py` (AI-R) with `extract/pictures.py`, image input on
  `LLMRequest`/`OpenAILLMProvider`, and `not_run_for(run)` replacing the stale
  `NOT_YET_BUILT` constant. Frontend: step names for every step, AI badges, and panels for
  gaps, next steps and untraced numbers. Found by live runs against both client workbooks:
  the cost view reported $0 for a review that had spent $0.23; `je_type_variant` was never
  projected; step 19's pattern captured the comma in "AH230,"; and three schemas broke the
  provider's strict contract (AI-R failed live, AI-X and AI-2 had never fired) — all four
  fixed with regression tests, plus a guard covering all eight schemas. Verified live:
  9 model calls, $0.232, 76s, 3 screenshots read, 888 exceptions grouped into 4 causes,
  zero untraced numbers. 444 tests, ruff/mypy clean.

- 2026-10-21: steps 7, 8, 9, 11, 12, 18, 19 and 20 completed, closing the numbered flow.
  New: `context/identify.py` (tier-2 shape discriminators, which the binder previously
  hardcoded), `context/aliases.py`, `agents/understand.py`, `agents/summary.py`,
  `guardrails/map_check.py`, `guardrails/plan_check.py`, `orchestrator/planner.py`.
  `runner.py` now runs a validated `ExecutionPlan` rather than a `JETypeContext`. The
  layout cache is wired. Found and fixed on the way: the plan check caught two recipes
  naming args their tool ignored (`classification_check`'s master, `period_agreement`'s
  facts — YAML corrected, version 2); a corroborating signal backed by a predicate was
  bypassing the posting-table rule; step 19's number pattern was swallowing a sentence's
  full stop. Tests no longer reach a live model: `tests/doubles.py` + an autouse fixture.
  358 tests, ruff/mypy clean.

- 2026-10-20: the API is live. `POST /reviews/` (multipart), `GET /reviews/`,
  `GET /reviews/{id}/`, `GET /reviews/{id}/events/` (SSE), backed by ReviewOrchestrator and
  a write-once ReviewStore; lifespan builds context, prompts, store and orchestrator and
  fails fast. Tools: base contract + balance, control_totals, rollup_chain. 11 integration
  tests run the real pipeline on a real workbook. Area weights confirmed by the business.

- 2026-10-20: context layer. `review/context/` (loader, prompt registry, layout + picture
  caches) and `review/models/context.py`. Business files: `business_rules.yaml` and
  `je_types/vacation_accrual_france.yaml` (8 areas totalling 100, 18 checks, discriminators
  with rejects, methodology, accounts, population), plus eight prompt files sharing one
  static block. Validation is strict and fails at startup naming the field.

- 2026-10-20: `.env` written (git-ignored; OPENAI key carried from the vacation-accrual POC,
  loads as SecretStr) and `.env.example` updated with every key. Profiler table detection
  rewritten to widest-header-first over discontinuity candidates: JE Create and Batch JE now
  bind their real line headers on all three workbooks and no header is found mid-block. All
  five golden xfails promoted to assertions; 195 tests pass, zero xfail.

- 2026-10-20: steps 2-6 validated against the real workbooks and pinned by golden tests.
  Fixed: zip guard rejected the 11.5 MB .xlsb (238 MB expanded, ratio 20) so pyxlsb had never
  run; header uniqueness rule dropped GPAY on 305800; header row picked the sub-heading above
  the real one; MAX_TABLES_PER_SHEET sliced *sheets*; reuse_key field-separator collision.
  Removed the redundant XML formula re-scan and its setting. Confirmed correct and left alone:
  typed-over and #VALUE! detection, which find real findings (GPAY "Earned leaves": 2,136
  formulas, 137 hardcoded zeros). Open gaps tracked as strict xfails: JE Create undetected,
  a mid-data header on the .xlsb, Batch JE binding the preamble.
- 2026-10-20: review harness steps 2-6 (gatekeeper, reuse key, reader, profiler, structure
  context + layout fingerprint), `review/consts.py`, `review/utils/sheet_utils.py`. Deps added
  with approval: openpyxl, pyxlsb, pyyaml, python-multipart, sse-starlette (+ type stubs, and
  a mypy override for pyxlsb which ships none). Review settings on BaseConfig (§17).
- 2026-10-20: review harness, first slice. `app/review/models/` (7 modules, frozen pydantic),
  `api/exceptions/review_exceptions.py`, and `api/consts.py` gains review routes, `SSEEvent`,
  `AICall` and the review `ErrorCode`s. No new dependency, no infrastructure, no endpoint yet.
  Design in `skills/project_geeta.md` §8.0 (the flow) and §8.8 (build order).
- 2026-10-18 `7c7517d`: OpenAI LLM providers (gpt-5.5, gpt-4o on Azure AI Foundry), token
  and cost metering, exceptions package, OPENAI_SETTINGS per flavour, `openai==3.24.0`.
- `47af5c0`: FastAPI skeleton, health endpoint, flavour settings (FLAVOUR dev/stag/prod).
