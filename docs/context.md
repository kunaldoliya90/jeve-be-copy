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
- All 20 numbered steps run, plus 7a, 13a, 13b and 13c. Everything is deterministic except
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
  app.py                FastAPI app; router mounted at config.API_PREFIX (/api/jeve);
                        CORSMiddleware added only when config.CORS_ORIGINS names an origin
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
                        LOG_LEVEL, CORS_ORIGINS (comma-separated in the env, read by app.py),
                        DOCS/REDOC/OPENAPI_URL, LOGGING_CONFIG, and the six
                        review limits (MAX_UPLOAD_MB, MAX_ACTIVE_REVIEWS, TOOL_WORKERS,
                        REVIEW_DEADLINE_S, STRUCTURE_CONTEXT_MAX_TOKENS, FIGURE_SAMPLES),
                        every one of them read by applifespan. Service-agnostic
      development.py    debug, docs, localhost CORS origins, reads ./.env, OPENAI_SETTINGS
      staging.py        docs on, deployed CORS origin, OPENAI_SETTINGS
      production.py     DEBUG locked False, deployed CORS origin, OPENAI_SETTINGS
      consts.py         Flavour, LogLevel, OpenAISettings TypedDict (BASE_URL,
                        API_KEY: SecretStr | None, TIMEOUT_SECONDS), CorsOrigins alias and
                        the CORS_* middleware constants, env constants
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
                        ReviewSummary, WorkbookView, AreaView (+status), CheckView
                        (+samples: FigureSampleView, read field-by-field from a figure's
                        sample dict so a tool whose sample means something else carries
                        nulls rather than failing), EvidenceView.of(), AICallCostView.of(),
                        CostView (+calls), ErrorResponse, area_status(), stored_check().
                        snake_case end to end; separate from review/models/ on purpose -
                        except split_summary and recalculation, which are carried as
                        harness models
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
  review/               JE review harness, steps 2-20 (plus 7a, 13a, 13b, 13c). Three rules
                        hold throughout:
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
      je_types/vacation_accrual_france.yaml   v11. 6 areas, 30 checks, discriminators,
                        methodology, reperformance (12 record-level identities: six over the
                        columns the entry is built from, six over the rest of the row),
                        recalculation (the approved formula, its inputs and the stages it is
                        built from), accounts, population, evidence. tables[].meanings is what
                        the checks actually read, and it is enforced against both the
                        identities and the recalculation now
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
    recalc/             step 13c: the entry rebuilt from the inputs the business approved
      formula.py        the approved formula, parsed (never eval'd) into closures and cached:
                        four arithmetic operators, six comparisons, max/min/abs/round/coalesce/
                        if, literals, parentheses. A missing input propagates as None, so a row
                        an input does not resolve on is incomplete rather than recomputed as a
                        zero - coalesce(value, default) is the one deliberate exception,
                        verified against real data for a blank that is itself the business's
                        answer, and a comparison with an absent side is None rather than false.
                        if(cond, then, otherwise) evaluates only the branch it takes, so an
                        input one branch reads cannot make the other branch's rows incomplete.
                        A chained comparison is a parse error, not a guess. substitute()
                        rewrites an expression in the names a reader sees: an input by the
                        header the preparer wrote, a derived term by the short label the
                        business gave the stage, since a stage has no column to be named by.
                        expand() goes further and writes every stage out down to the columns
                        themselves, parenthesising each as it goes in, because that is the
                        only form that tells a reader which columns the arithmetic reads
      formula_audit.py  the second half of step 13c: the formulas the *workbook* applies,
                        read and never evaluated. Per column the business declares arithmetic
                        for: not calculated at all, cells that disagree with each other, a
                        constant typed over one, or references resolving to different meanings
                        than the approved expression reads - the last being the only fault a
                        figure that ties cannot hide. Two guards keep it honest: only record
                        rows are read, because the one formula in one total column is the
                        preparer's own SUBTOTAL beneath the data, and a column that references
                        itself is a carry-forward rather than another derivation
      engine.py         recalculate(ctx, spec) -> EntryRecalculation. One pass over the
                        records: inputs read straight out of the sheet's sparse dict into one
                        reusable row mapping, derived terms in declaration order, the formula,
                        then the row against the amount the file states. The booked side is
                        the posting table's own debit lines, quantised like the recomputed
                        side. Citations are worst-first with one of each kind kept
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
      split_summary.py  step 18's work paper: build_split_summary() re-adds the working and
                        divides it by the classes its own classification column holds, ties
                        each component against the total the file states above its header,
                        reads the formula behind each column, traces the first row and the
                        exception rows, and carries the checks' own results. Names no row
                        title, column title or category anywhere. resolve_references()
                        rewrites a formula with each reference replaced by that column's
                        header: a substitution, never a translation
      planner.py        step 11: plan_from_recipes() for every check that states one;
                        propose_steps() asks AI-2 only about checks with no recipe, and a
                        step for any other check is discarded. split_out(plan, tool) lifts
                        the recalculation step out of the validated plan for step 13c,
                        keeping step 12's guarantees rather than planning it separately
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
    tools/recalculation.py  recalc_entry_total: the thin half of step 13c. run_recalculation()
                        runs the engine once and returns the work paper *and* the figures, so
                        the entry reaches a status through the ordinary routing rules - a
                        decisive entry-level figure, a row-level one carrying the coverage,
                        and one per corroborated leg. The basis arrives on ctx.je_type_args,
                        never as a plan argument
    tools/reperform.py  the one tool for every record-level identity the business declares.
                        Reads them off ctx.je_type_args, evaluates each over the records, keeps
                        the declared per-row tolerance and the min_tie_rate guard, and reports
                        a column this workbook lacks as unable to validate. It replaced
                        recalc_accrual, recalc_leave_balance and recalc_salary_per_day
    tools/reperformance.py  negative_scan, classification_check, population_reconcile,
                        rtt_balance_reperforms - the row-level work that is not a formula.
                        The last rebuilds the RTT balance used for valuation from days earned,
                        days taken and months to accrue, as a tool rather than a reperform
                        identity because the rule it reproduces needs two blanks folded to
                        zero rather than read as missing, verified against real data, not one
                        line of arithmetic
    tools/salary.py     salary_validate: whether the daily rate is usable where it is used
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
      recalculation.py  RecalcUnit, RoundingMode, DiscrepancyKind, BookedMethod,
                        FormulaFault, FormulaFinding, CitedValue,
                        RecalcCitation, RecalcInputReading, RecalcComponent,
                        RecalcCorroboration (+.agrees), BookedAmount, RecalcBasis,
                        EntryRecalculation (+.complete, .ties): step 13c's work paper,
                        written with the record and served with it
      split_summary.py  SplitBasis, CategoryRef, ComponentSplit (+.shares), SplitTieOut,
                        DerivedRule, WalkStep, RowWalkthrough, ExceptionTest, SplitSummary:
                        the work paper, written with the record and served with it
      result.py         ScoringMethod, AreaScore, Score, ReportPayload, EntryIdentity,
                        AICallRecord (+step, name, model), ToolRunRecord, AuditTrail,
                        ReviewResult (+entry)
      review_context.py ReviewContext: the mutable case file; figures_for(),
                        decisive_figure()
      workbook_data.py  Cell/Sheet/PictureData/WorkbookData: the parsed workbook, in
                        memory only. Deliberately apart from workbook.py, which may be
                        persisted - a screenshot's bytes never are
      context.py        JETypeContext, BusinessRules, CheckSpec, EvidenceSpec, VariantSpec,
                        DiscriminatorSpec, MethodologySpec, ContextVersions, and the
                        recalculation basis (RecalculationSpec, RecalcInputSpec,
                        DerivedTermSpec, BookedSpec, RoundingSpec, CurrencySpec) and the
                        record-level identities (ReperformanceSpec, RowIdentitySpec) whose
                        formulas are compiled in a validator, so a malformed one fails at
                        startup naming the field. Two validators also cross-check every
                        meaning the identities and the recalculation name against the table
                        that type declares - the recalculation's because a misspelled
                        agrees_with is otherwise silent: the engine finds no column and simply
                        records no corroboration. extra="forbid" so a YAML typo is a startup
                        error, not a rule that does nothing
  tests/
    conftest.py         autouse isolated_environment (drops FLAVOUR/APP_* env, chdir to
                        tmp); autouse no_live_model (config is built at import, so clearing
                        the env does not unconfigure a developer's key); `client`,
                        `stub_model`, `stub_client`; store_of()/orchestrator_of()
    doubles.py          StubLLMProvider: answers each call from its schema_name and records
                        what crossed the boundary, so judging and reporting are assertable
    unit/               mirrors app/ (api, core, review/models, intake, extract, context,
                        agents, guardrails, orchestrator, recalc, tools, utils)
    integration/        health, CORS headers, and the whole review flow through TestClient
                        (including step 13c: the work paper, the stream and a re-read)
    golden/             steps 2-6, the split work paper and step 13c against the real
                        workbooks in skills/docs (@pytest.mark.golden, ~60s). Known gaps are
                        strict xfails, so fixing one fails the suite until its marker goes
skills/docs/            the three real client workbooks the harness was designed against
skills/scripts/         developer scripts, run by hand. T201 (no print) is ignored here only
  make_discrepancy.py   writes a copy of a sample workbook that deliberately does not
                        reconcile, for seeing step 13c fail: --preset resolves the cells
                        through the sheet map (so it works on any layout) and makes three
                        edits, one per kind of discrepancy. It edits the sheet XML inside the
                        package rather than re-saving: openpyxl would drop the posting tab's
                        cached formula values and the booked side would read as absent,
                        which is inconclusive rather than a mismatch
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
- CORS is a setting, not a middleware decision: `CORS_ORIGINS` is empty on BaseConfig and
  named by each flavour, and `app.py` adds `CORSMiddleware` only when it holds something.
  An empty `allow_origins` list would still answer every preflight, with a 400. The
  environment overrides it as a comma-separated string, not JSON (`NoDecode` on the field
  plus a `mode="before"` validator), because it is typed by hand into a deployment
  dashboard; the alias carrying `NoDecode` lives in `settings/consts.py` so a flavour that
  overrides the field cannot silently put JSON decoding back. `allow_credentials` is on,
  which is why the origins are named rather than `*`.
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
  sentence reading as though it had been verified. It also reads only numbers *nobody named*:
  the digits inside a cell reference, a figure id or a period (`AH230`, `F-12`, `FY26`) are
  part of a name, and listing one tells a reviewer to distrust a figure the report never
  quoted. A guardrail whose list carries noise is a guardrail that gets ignored.
- The **approved formula is business knowledge, not code**. `recalculation:` in the JE type
  file states the formula as an expression over named inputs, each with its unit, its source
  and whether the formula needs it, plus the supporting conventions the design asks for -
  sign, currency, rounding, day count, tolerance and a worked example. `review/recalc/
  formula.py` parses it (never `eval`) into closures and the spec compiles it in a
  validator, so a malformed formula or a typo in an input name is a startup error naming the
  field rather than a term that silently evaluates to nothing on every row. A JE type that
  states no formula reports the recalculation as not run; nothing assumes one.
- The formula **reads source columns, not the stated figures they produce** (v11). Which
  level it reads at is the whole design question, and the answer is: as far back as the
  columns go. A formula over two stated columns asking whether they add up to a third is a
  tie-out - the inputs behind them were examined, but by other checks with their own
  statuses, so an edited source column left the headline figure reconciling to the cent. The
  formula now rebuilds its RTT leg from the entitlement, the months worked, the days taken
  and the daily rate, and every intermediate carries an `agrees_with` naming the column the
  workbook states its own answer for that quantity in. So an edited *stated* column is still
  caught - as a corroboration that no longer agrees, which drops the check to partial and
  sends it to a person - and an edited *source* column now moves the entry's own number. The
  one thing lost is that a stated column is no longer decisive; that is the trade the
  business asked for.
  This is also what gave `rtt_earned_days` (the workbook's own prorated earned-days column)
  its first reader. It had been aliased to the same meaning as the yearly entitlement since
  the alias table was written, so first-match-wins stood one meaning in for two different
  quantities - the third instance of that defect, after `cp_provision` and `rtt_balance`.
- The formula grammar has a **conditional**, and it is there because a real convention needed
  one: an employee who has taken no RTT accrues none of it. Nothing in four operators
  reproduces that. The arithmetic stand-in - scaling the leg by `min(days_taken, 1)` - was
  written, measured and rejected: it is correct only while nobody takes a half day, and three
  records across the two real workbooks do, which is a false exception on a clean file. A
  comparison with an absent side answers `None` rather than false, for the same reason every
  other operator propagates: reading a blank as a failed test turns one missing column into a
  confident finding about every record.
- **Every numeric column on a record is covered by something.** Six identities cover the
  columns the posted figure is built from; six more cover the rest of the row - the employer
  charge rate and amount, the two provision totals that include those charges, the RTT
  provision stated in money, and the prior period's leave balance. None of that second set
  reaches the entry, because it excludes employer social charges, and they exist anyway: an
  edit to a column nothing reads is still an edit to the working, and that is what an
  undetected manipulation looks like. One check each, never grouped, because a figures-decided
  status comes from the *decisive* figure and a reperform figure is not decisive - grouped,
  only the first identity in the list could ever report a failure rather than partial.
- `RowIdentitySpec.against_blank_is_zero` is **off by default and has to be earned**. A blank
  in a compared column is normally a figure the workbook does not state, and reading it as a
  nought invents an answer. But `reperform` skips a blank one, a skipped row is an uncovered
  row, and an identity that cannot cover the population can never support a pass - so a column
  the preparer legitimately leaves empty makes a check permanently partial on a workbook with
  nothing wrong with it. Turning it on is allowed only where the blank has been verified
  against real data to be the business's own way of writing nil, which for these six means
  every blank coincides with a computed nil on both French periods.
- The **record-level arithmetic is declared too** (`reperformance.identities`, version 4).
  One `reperform` tool evaluates any of them, and it replaced three tools whose arithmetic
  was Python. The reason is not that the code was wrong: a business owner could read their
  own arithmetic only by reading code, and a country whose leave convention differs needed a
  new tool rather than a new line of YAML. Every decision those tools carried is declared
  rather than hardcoded now - the per-row tolerance (a product rounded before it was stored
  needs a cent of room; a sum of two stored amounts does not) and the `min_tie_rate` below
  which an identity reports as *unable to validate* rather than as thousands of exceptions.
  Verified by reproducing the old figures exactly on all three workbooks, knife-edge
  included: 1,177 of 1,214 compared rows on one period's RTT movement, which is the figure
  the guardrail tests already pinned.
- Declaring the identities made a latent defect visible: `tables[].meanings` had **no reader
  at all**, and the leave columns the three tools read were never in it. A workbook missing
  `cp_earned` would have failed inside a tool instead of being reported at step 12. The
  validator that compiles each identity against the table's meanings is that field's first
  reader, and the list now says what the checks actually read.
- A fifth identity, `cp_rtt_composition` (v5): the stated total equals the CP provision plus
  a twelfth of the RTT accrual - the same convention `methodology.notes` already named in
  prose but nothing had ever evaluated. It closes a gap none of the other identities cover:
  `fte_split_sums` and `entry_total_recalculates` both take `fte_accrual`/`non_fte_accrual` as
  given and only check that they were summed correctly, so an edited `cp_provision` or
  `rtt_accrual` that never touches the split or the total tied every other check in the file.
  Verified to the cent on both French workbooks before being declared (2,271/2,271 and
  2,280/2,280 rows) - the same bar as every other identity here.
  Declaring it surfaced a second latent defect, in the alias table rather than in the YAML:
  `cp_provision` carried two alias phrases, `"total provision cp"` (column M, the combined
  N-1 + N provision the business means) and `"provision cp n"` (column L, the current
  period alone), and first-match-wins silently bound L. Every file in this JE type carries
  both headers, so the new identity disagreed on nearly every row of an otherwise clean
  workbook until the alias was narrowed to the one phrase that names the quantity the
  rest of this file is written over - and the recalculation's existing `cp_provision` input
  (`recalculation.inputs`, carried for the reader since v4) had been quietly reading the
  wrong column the whole time.
- Two more identities, v6, one level further back than `cp_rtt_composition`: that check takes
  `cp_provision` and `rtt_accrual` as given, so editing the columns *they* are built from still
  tied everywhere.
  `cp_provision_builds_up` rebuilds the CP provision from the prior- and current-period legs
  the payroll extract types separately (`cp_provision_prior`, `cp_provision_current` - new
  meanings, column H and L). Either leg is blank in a period it does not apply to - a new
  joiner has no N-1 leg - and the business's own total simply omits it, which
  `coalesce(value, 0)` reproduces. Verified on both workbooks: every one of the 1,999 and
  1,961 rows where neither leg is blank ties, and so does every one of the 274 and 319 rows
  where one leg is. `coalesce` is a new, fifth function in `recalc/formula.py`'s grammar for
  this - the one deliberate hole in "a missing input propagates," written explicitly in the
  business's own expression rather than assumed by the engine for every formula.
  `rtt_balance_reperforms` (new tool, `tools/reperformance.py`) rebuilds the RTT days actually
  valued from days earned, days taken and months to accrue (`months_to_accrue` - new meaning,
  already aliased but never declared). Not a `reperform` identity: on both workbooks, a blank
  "Pris RTT" does not carry the full earned-to-date balance forward - the file's own balance
  is zero on every one of 547 and 527 such rows - and a blank "Acquis RTT" is 137 and 132
  employees with real CP activity and simply no RTT entitlement that period, not a data gap,
  their balance zero too on all of them. Declaring either with `reperform`'s missing-propagates
  rule would read a sixth of the population as unbound rather than tied, which would leave the
  check permanently short of full coverage - never a pass - on a workbook where nothing is
  wrong; a genuinely unresolved row still reads as unbound (two employees on 305800.xlsx carry
  an unmapped `#N/A` staffing type with every CP and RTT column blank, and `cp_provision_rebuilds`
  correctly reports them so, rather than inventing a zero for a row actually missing data).
  Both new checks were verified live against both real workbooks before being wired in: edits
  to M, H, L, Q and Z on a row where the edit is not masked by the "nothing taken" rule each
  now surface as a cited difference; a tamper that only touches an input the posted figure
  already discounts - e.g. the earned days on a row with nothing taken - stays invisible, which
  is a property of the business's own convention and not a gap in these checks.
- The **workbook's own formulas are audited after the rebuild**, which closes the one gap the
  recalculation cannot see: a column built from the wrong columns still produces the right
  total on every record where the missing input happens to be zero, so the entry reconciles,
  every check passes, and the arithmetic is still not the approved one. Proved on a real
  workbook - 305800 given `=AF9` in place of `=AF9+AG9` on all 2,273 records rebuilds to
  27,905,812.64 against 27,905,812.64 booked with zero citations, and the audit reports that
  the column reads the wrong inputs.
  It compares **which columns are read**, never the shape of the expression: `=B2+C2` and
  `=SUM(B2:C2)` are the same arithmetic written two ways, and judging the shape would mean
  inventing an opinion about what the preparer meant. Nothing evaluates Excel anywhere.
  Two guards came out of the real files and each prevents a confident false finding: only
  *record* rows are read, because the only formula in one period's total column is the
  preparer's own `SUBTOTAL` on the row beneath the data, and a column that references itself
  is a carry-forward rather than an alternative derivation.
  `formula_defects_can_fail_check` in `business_rules.yaml` - until now a setting nothing
  read - decides whether a fault may fail the check. It stays false: a fault is reported
  beside the figure and moves no score, the treatment every other formula defect in the
  harness gets. Turning it on is a decision about how strictly these entries are reviewed,
  and not one for code to take.
  Only a **defect** may block it, which is `FormulaFault.is_defect`: different inputs,
  inconsistent cells, a constant typed over a formula. A column the file states rather than
  calculates, and a format carrying no formulas at all, are limits of the evidence and are
  reported without touching any status. The first cut of this let *any* finding block, which
  would have failed every review on all three sample workbooks the moment the business threw
  the switch - every one of them states columns that arrive from the payroll extract. A
  setting that cannot safely be turned on is a landmine rather than a setting, so the
  classification is pinned by its own tests.
- Step 13c is its **own numbered step**, not a tool inside step 13's fan-out, for two
  reasons: it produces a work paper as well as figures, and it is the one check whose work is
  visible on the clock, so the person waiting is owed a line of progress naming it (the
  `step_started` event carries the row count, which is why `_step()` takes a detail at all).
  The step is lifted out of the *validated* plan by `planner.split_out`, so step 12 has
  already proved the tool exists, its arguments are ones it declares and its table is bound.
- The entry recalculation reaches its status as **ordinary figures**: a decisive entry-level
  figure, a row-level figure carrying the coverage, and one per corroborated leg. No special
  case in step 16, in scoring or in the guardrails - and `Figure.supports_pass` then enforces
  gate rule 1 here as everywhere, so a total that agrees over an incomplete population is
  partial and never a pass. The engine runs **once**, and `run_recalculation` hands back the
  paper and the figures together: getting them from two runs would walk 37,613 rows twice.
- The basis travels on `ctx.je_type_args`, never as a plan argument. A tool argument is a
  column or a table name; a methodology arriving through the plan could be chosen by whatever
  wrote the plan, which is the thing the recipe rule exists to prevent.
- Both sides of the comparison are **quantised to the declared scale** before being
  subtracted. The posted line amounts carry the full precision of the floats they were read
  from, so summing 6,465 of them leaves a tail around 1e-8; reporting that as
  "0.0000000248 more than was posted" puts a difference a reviewer cannot act on where a real
  one belongs. Sub-cent materiality is what `row_tolerance` is for.
- The formula declared for vacation accrual rebuilds **the leg that can be rebuilt and reads
  the leg that cannot**, which is where the line sits and why it sits there. The RTT leg is
  rebuilt from first principles - yearly entitlement, months worked, days taken, daily rate -
  because that reproduces the workbook's own figure on every one of 2,273 and 2,280 records.
  The CP provision is read as a column, because it arrives already calculated from the payroll
  extract and is reproducible from the legs present on only 4% of rows: a formula that tried
  to rebuild it would report thousands of exceptions that are not there. That asymmetry was
  measured against the real workbooks, not assumed, and it is the reason `cp_provision` is an
  input while the RTT accrual is a stage. The CP provision is still rebuilt where it can be -
  by `cp_provision_rebuilds`, which reports the 4% rather than deciding the entry on it.
  `provisional_basis: true` until the business signs the formula and the +/-1% tolerance off,
  and the work paper says so on its face.
- `entry_total_recalculates` overlaps `fte_split_aggregates` deliberately. One asks whether
  each row's split sums to its own stated total; the other asks whether the entry can be
  produced again at all and whether what was produced is what was booked. The first can hold
  while the second fails, which is the case nothing else here would catch.
- The md spec's **PASS = 30 points** is not implemented as a points rule. Scoring is by the
  business-confirmed area weights, and the check lands in `accrual_reperformance` (weight 25,
  scored by its weakest check), so a failure there costs the area outright. Adding a parallel
  points scale would mean two numbers that can disagree about the same check.
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
- The category split work paper (`orchestrator/split_summary.py`, step 18) is the arithmetic
  a reviewer reads: what the entry's value is composed of, how it divides by the classes the
  workbook's own classification column holds, the totals the file states above its data
  against a re-add of the rows beneath them, the formula behind each column, one row traced,
  and what each test found. Four decisions hold it together.
  It is **not a tool**. A `Figure` is one measurement, an `ok` and an allowlist of quotable
  numbers; a 24-column matrix pushed through a figure's exemplars would be capped by the
  sample limit and read by every guardrail as evidence for a conclusion nobody drew. It sits
  beside `diagnosis.py` instead, as a report step 18 assembles.
  Its tests section is **carried**, never recomputed - the statuses are the ones step 16
  reached. Same rule as the evidence register, same reason: a second opinion assembled
  upstream is how a report comes to disagree with the check it reports on.
  Nothing in it is **named in code**. Row titles are the headers the preparer wrote, column
  letters come from the verified map, categories are whatever the classification column
  holds, and the number formatting is Excel's own format string. The 37,613-row `.xlsb` on a
  tab called DENT, with a quarter of the columns and different classes, produces a correct
  work paper with no edit - which is what the golden tests pin.
  A control total is identified by **being the same quantity** as the column above it: the
  nearest number above the header that ties, or is within `CONTROL_TOTAL_MAX_DRIFT` of, that
  column's re-add. The header band of a real working also holds rates and per-unit figures,
  and the earlier nearest-number rule reported two of those as failed tie-outs - a confident
  false finding, which is worse than not finding the total at all.
- A numeric column Excel left on `General` holding only whole numbers is a **code**, not a
  quantity, and is left out of the split. Every real amount and day count in these workbooks
  carries an explicit format because somebody meant it read as money or as days; on the
  sample files exactly one column fails both tests, and it is the SAP company code, whose
  sum is 2,633,130 and means nothing. Both conditions are required - a General column with a
  fraction in it is a quantity somebody forgot to format. A format of `None` is *unreadable*
  rather than General, because the binary reader exposes no cell formats at all, and there
  the signal is the preparer's own control total instead: somebody totals a quantity and
  nobody totals a code. That separates the `.xlsb`'s nine numeric columns exactly. Reading
  unknown as General dropped two whole-number quantity columns from it; reading it as a
  quantity put a cost-centre code summing to 394 *billion* on an audit work paper.
- The work paper's totals are over **classified rows only**, so the split always visibly adds
  up to the total beside it. A profiled block ends at a run of blank rows and so reaches past
  the last record into the preparer's own subtotal rows - on one sample file a trailing row
  holds the entire entry a second time. Those rows are neither summed nor reported as records
  that failed to classify: `rows_read` against `rows_total` is where the gap is stated.
- `RowWalkthrough` shows amounts against a **row number and nothing else**. This is the one
  place the harness shows per-row values rather than cell references, and it is deliberate:
  a trace without amounts traces nothing, and tracing one employee's arithmetic is the
  evidence a reviewer is being shown. Identifying columns are excluded twice - by the meaning
  the map bound them to, and by being distinct on nearly every row, so a spelling the alias
  table has never seen still fails closed. No name, no personnel reference, ever.
- `ReviewDetail.split_summary` carries the harness model rather than a parallel set of nine
  view classes. Every field on it is already a reader's field - headers, column letters,
  amounts, row numbers - and nine identical classes in `api/pydanticmodels/` would be the
  mapping layer that hides drift rather than preventing it. It is the one exception to the
  separation above, and the reason is on the field.
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

- 2026-11-04 (9): the recalculation card names the columns it reads. Reported from the UI:
  the headline read "Total Provision CP + Rebuilt RTT accrual / 12", which is the *staged*
  formula, and a stage has no column - so the card looked unchanged from the version that
  genuinely did lift the RTT accrual straight out of one, and told a reviewer nothing about
  the four columns now behind it. `RecalcBasis.formula_expanded` (new) writes every stage out
  in the headers this workbook uses, and the frontend leads with it, keeping the staged and
  the declared forms beneath. `recalc/formula.py` gained `expand()`, which inlines the stages
  in declaration order - one pass is enough, since a stage may only read stages declared
  before it - and parenthesises each as it goes in: inlining `a + b` into `x + s / 12`
  unparenthesised gives `x + a + b / 12`, a different sum, and a displayed formula that is
  not the evaluated one is worse than one nobody can read. It follows the file rather than
  the entry type, which is what the global `.xlsb` pins: it shows that model's own
  "Daily Salary" where a column was bound and the business's own id where none was.
  6 new unit tests, 2 new golden tests, 1 integration assertion, 2 frontend tests.

- 2026-11-04 (8): every numeric column on a record is now covered, so an edit anywhere in the
  row surfaces. Six new identities over the columns the posted figure is *not* built from -
  the employer charge rate and amount, the two provision totals that include those charges,
  the RTT provision stated in money, and the prior period's own leave balance - and six new
  checks in `calculation_accuracy`, one per identity. Before this, `Provision Totale` could be
  moved by 1.1 million euros with every check passing, which is exactly what the tampered
  sample copy had been carrying unnoticed all session.
  One check per identity rather than one carrying all six, and the reason is the status a
  tamper reaches: `outcome_for` takes a figures-decided status from the *decisive* figure, a
  reperform figure is not decisive, so `figures[0]` stands in - grouped, a discrepancy in the
  second or third identity would surface only as incomplete coverage, partial rather than
  failed, and a million euros moved in a provision total deserves the stronger word.
  New mechanism, and the only one this needed: `RowIdentitySpec.against_blank_is_zero`. These
  columns are left empty for an employee the provision does not apply to rather than written
  as a nought, and `reperform` skipped a blank compared column - a skipped row is an uncovered
  row, and an identity that cannot cover the population can never support a pass, so all six
  would have reported *partial on a clean workbook*: the same landmine `rtt_balance_reperforms`
  was built as a tool to avoid. Off by default, because a blank is normally a figure the
  workbook does not state. Verified before turning it on: every blank coincides with a
  computed nil (the CP provision is itself zero wherever its charge amount is blank; the prior
  period's earned and taken days are equal wherever its balance is), and with it on all six
  tie on every record of both periods - 2,273 and 2,280 each, full coverage.
  The inputs use `coalesce`, which the grammar already had. The two charge identities coalesce
  the *rate* as well, which is the one assumption here and it is bounded: it could hide a
  missing rate only on a row whose base provision is non-zero, and there is no such row on
  either workbook. Checked explicitly rather than assumed, because reading a blank statutory
  rate as 0% is the sort of confident nought this harness otherwise refuses.
  A fourth instance of the alias defect, found on the way and fixed: `employer_charges`
  carried both `"taux charges patronales"` (N, a rate) and `"montant charges patronales"`
  (O, an amount), so first-match-wins bound the rate and the amount was never bound at all -
  after `cp_provision`, `rtt_balance` and `rtt_earned`. Split, along with new meanings for
  `cp_provision_with_charges` (P), `rtt_provision_base` (T), `rtt_employer_charge_amount` (U)
  and the prior-period CP trio (E, F, G).
  Verified live: the 1.1M `P9` edit the sample copy carries now fails `cp_provision_total_sums`
  citing row 9, cell P9, expected 19,287.76 against 1,119,287.76. Each of N, O, T, U, V, E, F
  and G was then tampered individually on a clean copy and every one surfaced, most through
  two independent checks. 3 new unit tests on the flag, 12 new golden assertions.
  Two counts moved as a consequence, both checked rather than just re-pinned: the formula
  audit now examines 12 of the workbook's columns rather than 6, because an identity over a
  column is what puts that column in front of the audit, and all six new ones report the
  same `not_calculated` these files already reported for the other five; and a review now
  carries 30 checks rather than 24.

- 2026-11-04 (7): the entry formula rebuilds the RTT leg from its source columns (v11), on
  business instruction, so an edit to any column the entry is built from moves the entry's own
  number. `cp_provision + rtt_amount / 12` replaces `cp_provision + (rtt_accrual / 12)`, where
  `rtt_amount` is now three declared stages rather than a column read: the yearly entitlement
  prorated over months worked (`earned_rtt_days`), the days taken subtracted and floored
  (`unused_rtt_days`), then valued at the daily rate. Five source columns instead of two
  (`Acquis RTT`, `Months to Accrue`, `Pris RTT`, `Daily Salary (RTT Rate)`, `Total Provision
  CP`), and each stage carries an `agrees_with` naming the column the file states its own
  answer in - so a tampered *stated* column is still caught, as a corroboration that no longer
  agrees, which drops the check to partial through the ordinary coverage rule with no special
  case anywhere.
  `recalc/formula.py` gained `if(condition, then, otherwise)` and six comparisons, because the
  convention "an employee who has taken no RTT accrues none of it" is not expressible with the
  four operators. Written, measured and rejected first: scaling the leg by `min(days_taken, 1)`
  is correct only while nobody takes a half day, and three records across the two real
  workbooks do - it put 2,271 of 2,273 rows right and invented two false exceptions. The
  untaken branch is not evaluated, a comparison with an absent side is `None` rather than
  false, and a chained `a < b < c` is a parse error rather than a guess at which reading was
  meant.
  Found on the way, and the reason this formula could be written at all: `rtt_earned_days`
  (the workbook's own "Earned RTT Days") was aliased to the *same meaning* as the yearly
  "Acquis RTT", so one meaning stood in for two different quantities and first-match-wins bound
  the entitlement. The third instance of that exact defect, after `cp_provision` (2026-11-02)
  and `rtt_balance`. Split into its own meaning, which also gave that column its first reader -
  verified safe on the `.xlsb`, which carries no such column and so binds nothing differently.
  The practical effect is visible on a tampered copy of 305800.xlsx carrying `AA9 = 100`
  against a 5.67-day balance: previously invisible to every check in the harness, now
  `earned_rtt_days` reports 2,272 of 2,273 records agreeing.
  Also fixed: an incomplete row's citation named every blank declared input as the reason it
  could not be rebuilt, including the optional ones. With `Acquis RTT` and `Pris RTT` now
  declared and blank on 137 and 547 records, the two `#N/A` rows on 305800.xlsx read as
  "Total Provision CP, Acquis RTT and Pris RTT holds no number" - pointing a reviewer at two
  columns there is nothing wrong with. Only required inputs are named now, falling back to
  every blank where nothing required is missing.
  A second validator on `JETypeContext` cross-checks every meaning the recalculation names
  against the table that type declares - the sibling of the identity check, and needed here
  because a misspelled `agrees_with` is *silent*: the engine looks it up in the bound table,
  finds nothing and records no corroboration, so the strongest evidence on the work paper
  would quietly not be there.
  Verified against all four real workbooks before landing: 236264.xlsx now reconciles outright
  at 28,672,591.23 against 28,672,591.24 booked (a cent out where v10 was three, since a
  product recomputed in Decimal keeps precision the stated column had rounded away);
  305800.xlsx at 27,905,812.63 against 27,905,812.64, still partial for the two `#N/A`
  employees; all four corroborating legs tie on every one of 2,280 and 2,273 records; and
  935348.xlsb still reports `unable_to_validate`, unchanged since v9. `make_discrepancy.py
  --preset` is still stale (it edits `fte_accrual`/`total_accrual`, neither decisive since v9,
  with a `MOVE_BY` below the 1% tolerance) and was deliberately left alone - a separate change.
  Presentation came with it, because four stages instead of two broke the work paper's own
  table. `RecalcComponent.label` was `term.description or term.id`, so the "Stage" column
  held whole paragraphs; `DerivedTermSpec` now carries a short `label` beside the sentence -
  the same split `RowIdentitySpec` already had - and `RecalcComponent` carries both. One
  `display` map now writes every expression a reader sees, so a stage is named the way the
  rest of the paper names it rather than by its id: the headline reads "Total Provision CP +
  Rebuilt RTT accrual / 12" and not "... + rtt_amount / 12". Still a substitution and never
  a translation - a label is the business's own name, not prose invented for a formula.
  Frontend (JEVE-FE, same branch): the stages table already rendered generically, so it
  picked the new stages up with no change; `RecalcComponent.description` was added to the
  type and is rendered under each stage's name, and the fixture now carries two stages so the
  stage-to-stage substitution is covered. 1 new frontend test.
  11 new unit tests on the grammar, 4 on the new validator, 3 new golden tests (the two new
  legs on both periods, and each stage reading back in the preparer's own headers), 5 golden
  and 3 integration expectations updated.

- 2026-11-04 (6): `make runserver`'s auto-reload now watches YAML as well as Python.
  `uvicorn.run(..., reload=True)` with no `reload_includes` defaults, via
  `uvicorn.supervisors.watchfilesreload`, to globbing `*.py` only - so every business-rule
  edit this session made to `review/context/*.yaml` and `je_types/*.yaml` (the evidence
  completeness fix, the formula swap, the tolerance change) was invisible to the dev server's
  reload watcher. The server kept answering from whichever context it had loaded at its last
  Python-triggered restart, which reads exactly like a fix was not applied - the "0/100, No
  evidence" and "score still 0" reports chased across this session were this, each time.
  `app/main.py` now passes `reload_includes=["*.py", "*.yaml", "*.yml"]`; no new dependency,
  since `watchfiles` (the reloader that actually honours `reload_includes` - the plain
  stat-based fallback ignores it entirely and warns as much) is already pulled in by
  `uvicorn[standard]`, already pinned in `app/requirements/base.txt`.

- 2026-11-04 (5): the entry-level tolerance on `entry_total_recalculates` widened from 0.1%
  to 1% (v10), on business instruction, after checking whether the -0.03/-0.01 dust the v9
  formula carries (see the entry below) was actually a rounding bug first. It is not.
  Tried and empirically ruled out: rounding each leg to the cent before combining them
  (matching the worked example's own intermediate step) gives the identical aggregate total
  either way on both real files - the two rounding orders are not where the dust comes from.
  Also tried: rounding `rtt_accrual` itself to the cent before dividing, on the theory that a
  cell holding a formula result can carry float precision past the cent (`2447.8199999999997`
  for a value shown as `2,447.82`) - on 236264.xlsx three of 2,280 rows are exactly that, and
  normalising them changes nothing in aggregate; on 305800.xlsx it makes the entry total
  *worse* (+0.29 instead of -0.01), proving `rtt_accrual` genuinely carries more than two
  decimals of real precision in these files and is not a float artefact to clean up - reading
  it as 2-decimal currency would have been the bug a "fix" there introduced. The dust is
  correctly-computed arithmetic meeting data that is not perfectly reconstructible to the
  cent from these two columns alone, which is exactly what a tolerance is for. 0.1% of a
  multi-million-euro entry already cleared both real files' dust by orders of magnitude; the
  widening to 1% is headroom for a smaller entry of this type, not a response to these two.
  `tolerance_amount` is still computed from `tolerance_pct` dynamically, so nothing besides
  the one YAML field needed to change. 2 tests updated (a docstring and one hard assertion on
  `tolerance_pct`); the two synthetic specs in `test_recalculation_spec.py` and
  `test_engine.py` test the model's own default (still 0.1%) and are untouched on purpose.

- 2026-11-04 (4): the entry recalculation's approved formula (v9), swapped on explicit
  business instruction: `cp_provision + (rtt_accrual / 12)` replaces
  `fte_accrual + non_fte_accrual` as what `entry_total_recalculates` and the "Rebuilt to the
  cent" card decisively rebuild. The business wanted the headline figure itself, not just a
  nearby block, to move when the CP provision or RTT accrual is wrong - the same arithmetic
  `cp_rtt_composition`/`accrual_composition_reperforms` already re-performs independently,
  now promoted to decide rather than corroborate. `fte_split` (the old formula) moves to
  `recalculation.derive` as a corroborating leg instead - still verified every record, via
  `fte_split_aggregates` and this leg, just no longer decisive.
  Flagged and confirmed before landing: 935348.xlsb (the global MSVacation model, tab DENT)
  carries neither `cp_provision` nor `rtt_accrual` - that convention is French-only - so its
  entry recalculation now reports `unable_to_validate` (`recomputed_total=0`, formula shown
  unsubstituted) every time, where it previously reconciled to the cent on 37,613 records.
  `RecalculationSpec` has one formula for the whole JE type, no per-variant override; the
  business chose to accept this rather than build one. 236264.xlsx and 305800.xlsx still
  reconcile within tolerance (-0.03 and -0.01, both inside the 0.1% entry tolerance and never
  outside the 0.01 row tolerance per record - dividing by 12 and rounding per row leaves a
  little dust a formula with no division never had), and 305800.xlsx's two employees with an
  unmapped `#N/A` staffing type (a known, already-documented gap) are now genuinely
  incomplete under this formula rather than silently covered by FTE/non-FTE always being
  present.
  That incompleteness surfaced a real, separate bug: `_entry_figure`'s `ok` was
  `paper.ties` (within tolerance *and* complete), so an amount that reconciled over an
  incomplete population read as outright FAIL - `outcome_for`'s partial-coverage branch is
  only reached when `ok` is already true, and a false `ok` returns FAIL before that branch is
  ever considered. Fixed in `review/tools/recalculation.py`: `ok` is now `within_tolerance`
  alone; coverage is the row-level figure's own job, exactly as it already was for the
  non-decisive figure, and the two now agree with the paper's own three-way `status` instead
  of flattening partial into fail. New regression test in `test_recalculation.py` pins it.
  `accrual_composition_reperforms` is now literally the same comparison as the entry-level
  formula (same expression, same tolerance) and was deliberately left in place rather than
  removed - a visible, independent cross-check of the same arithmetic, not dead weight, and
  the business did not ask for it to go.
  8 golden tests rewritten to match (`BOOKED` vs `RECOMPUTED`, a dedicated test for the global
  workbook's inconclusive state, `fte_split` added beside `rtt_amount` as a pinned
  corroboration), 2 integration tests updated with the new totals/formula text, 1 new unit
  test for the `ok` fix. Full suite green: 90 golden, 83 integration, unit suite clean.

- 2026-11-04 (3): evidence completeness stopped reading as a hard 0/100 the moment one
  required layer is missing. Reported live: 3 of 4 required layers present (only `approval`
  missing) scored the area 0/100, `Fail` - because `evidence_baseline` was `decided_by:
  judgement`, so AI-3 answered the binary question its own prompt asks ("are the four
  required items present") literally, and `baseline_share` over one check's fail/pass credit
  has no room for a result in between. `evidence_register`'s tool now carries a `RowCoverage`
  over the *required* layers only (never the optional two, which must never be what turns a
  pass into a partial) and the check moved to `decided_by: figures` (v8) - deterministic,
  the same as nearly every other check in this file, for a question ("is this sheet present")
  code can already answer outright. Zero required layers present still fails; some but not
  all is `partial` through the ordinary figures routing, no special case in `rules.py` or
  `scoring.py`; all required present (regardless of the two optional ones) passes. The
  required/optional split itself was already declared once, in `je_spec.evidence`
  (`EvidenceSpec.required`) for the register endpoint - the tool now reads the same list via
  `ctx.je_type_args["evidence"]` rather than a second copy. New `test_entry.py` (5 tests,
  the first dedicated test file for `tools/entry.py`); verified live on 305800.xlsx: the area
  moved from 0/100 Fail to 50/100 Partial, `needs_review` still true since `approval` is
  still genuinely missing. Full backend suite green.

- 2026-11-04 (2): "the records rebuilt" now shows every calculation-accuracy finding on the
  same records, not just the entry-level one. Reported live: editing `GPAY!M9`/`M10` (Total
  Provision CP) correctly failed `cp_provision_rebuilds` and `accrual_composition_reperforms`,
  but Section 5 - the entry recalculation's own card, citations and row browser - genuinely
  could not see it (that formula reads `fte_accrual`/`non_fte_accrual`, never `cp_provision`),
  which read as a UI bug on a file with a real, correctly-caught discrepancy.
  `EntryRecalculation.related_checks` (new, `RelatedRecalcCheck`) now carries every other
  check that reads the recalculation's own table through the generic `reperform` tool and
  found something - found by recipe (`tool == reperform`, same `table`), never by naming a
  check, so a new identity on this table is picked up the same way with no further wiring.
  Each is rendered as its own block in `EntryRecalculation.tsx`, reusing `RecalcCitation`'s
  Row/Cell/Recomputed/Stated/Difference shape rather than inventing a second table format.
  `RecalcRow` gains `flags: RowFlag[]`, merged in by row number from those same (already
  worst-first, capped) citations via `rows_query.merge_related_flags`, so "Browse every
  record" marks a row any related check flagged, and `filter_rows`'s `changed`/`agrees` and
  `group_rows`'s `flagged` count now see it too. Nothing is merged into
  `recomputed_total`/`difference` themselves - a different formula stays a different
  question. Block lettering in `EntryRecalculation.tsx` is now computed from which optional
  blocks actually render (`blockLetters`) rather than a hardcoded `'E'`/`'F'` ternary, which
  was already one new block away from being wrong. Verified live on `305800 - Copy (6).xlsx`:
  `related_checks` surfaced `cp_provision_rebuilds`, `accrual_composition_reperforms` and
  (previously invisible here) `leave_balance_recalculates`'s own RTT-leg mismatches, and rows
  9 and 10 now carry both checks' flags in the row browser. 7 new backend unit tests
  (`test_rows_query.py`), 2 new frontend tests; 710 backend tests and 259 frontend tests pass.

- 2026-11-04: AI-E (evidence appropriateness, step 12a) disabled for now, on a live finding:
  305800.xlsx genuinely has mismatched cost centres between the calculation/employee_master
  evidence and the posting tab, and the hard gate it tripped stopped the whole review before
  anything else ran - working as designed (`test_evidence_appropriateness_failing_stops_everything_else`,
  now removed), but not a call the business has signed off trusting yet. `_gate_on_evidence`
  no longer calls a model at all: `evidence_matches_je_type` always reports `missing_evidence`
  (zero credit, `ReviewFlag.CHECK_DISABLED`, needs a person) and the rest of the plan always
  runs regardless of what AI-E would have said - the gate itself is gone, not just loosened.
  `not_run_for` names it generically, off `ReviewFlag.CHECK_DISABLED` in the stored outcomes
  rather than a hardcoded check id, so any other JE type that wires this check in gets the
  same treatment. `ReviewRun.evidence_gate` and `_finish_evidence_inappropriate` removed as
  dead code; `review/agents/evidence_appropriateness.py` (the model call itself) is untouched
  and ready to be wired back in behind the same `_gate_on_evidence` call site. 2 integration
  tests replace the old pass/fail/inconclusive trio.

- 2026-11-03: coverage pushed one level further back than the CP provision and the RTT
  accrual - to the columns those are themselves built from. `cp_provision_builds_up` (v6, new
  check `cp_provision_rebuilds`) rebuilds the CP provision from the prior- and current-period
  legs the payroll extract types separately (new meanings `cp_provision_prior`,
  `cp_provision_current`), using a new `coalesce(value, 0)` function in `recalc/formula.py`'s
  grammar because either leg is blank in a period it does not apply to and the business's own
  total simply omits it - verified exact on every row of both workbooks where that is checkable.
  `rtt_balance_reperforms` (new tool in `tools/reperformance.py`, new check
  `rtt_balance_rebuilds` in `leave_validation`) rebuilds the RTT days valued from days earned,
  days taken and months to accrue (new meaning `months_to_accrue`). Built as a tool rather than
  a declared identity on purpose: a blank "Pris RTT" does not carry the full earned-to-date
  balance forward on either workbook (the posted balance is zero on all 547 and 527 such rows),
  and a blank "Acquis RTT" is 137 and 132 ordinary employees with no RTT entitlement that
  period, not a data gap - declaring either with `reperform`'s missing-propagates rule would
  have left the check permanently short of full coverage on a sixth of the population, on a
  clean file. A genuinely unresolved row (two employees on 305800.xlsx with an unmapped `#N/A`
  staffing type and every CP/RTT column blank) still correctly reads as unbound via the sibling
  CP check. `CheckView.samples` (added yesterday) is what made both checks' citations visible
  without further frontend work. Verified live: edits to M, H, L, Q and Z each now surface as a
  cited difference on both real workbooks, except where the edited input is one the posted
  figure already discounts under the business's own convention (the earned days on a row with
  nothing yet taken) - that is a property of the convention, not a gap in these checks. 2 new
  integration tests tampering H9 and Q8 on a real workbook, 7 new unit tests for the tool
  (`test_reperformance.py`), `coalesce` pinned in `test_formula.py` as the one deliberate
  exception to "a missing input propagates."

- 2026-11-02: an edited CP provision now surfaces as a discrepancy, and a reviewer can see
  what it is. Reported live: editing `GPAY!M9` (the "Total Provision CP" the payroll extract
  states) on a real workbook left every check agreeing with itself - `fte_split_sums` and
  `entry_total_recalculates` both take `fte_accrual`/`non_fte_accrual` as given and only check
  that they were summed correctly, so nothing re-read the CP provision at all.
  New identity `cp_rtt_composition` (v5, `accrual_composition_reperforms` in
  `accrual_reperformance`): the stated total equals the CP provision plus a twelfth of the RTT
  accrual, the convention `methodology.notes` already described in prose but nothing had ever
  evaluated. Verified to the cent on both French workbooks (2,271/2,271 and 2,280/2,280 rows)
  before being declared.
  Declaring it surfaced a second, pre-existing defect: the alias table bound `cp_provision` to
  whichever of two columns matched first - column M (`"total provision cp"`, the combined
  N-1 + N provision) or column L (`"provision cp n"`, the current period alone) - and
  first-match-wins had silently picked L on both real files. The recalculation's `cp_provision`
  input (carried for the reader since v4) had been reading the wrong column since it was
  declared. Fixed by narrowing the alias to the one phrase that names the quantity the rest of
  the file is written over; `"provision cp n"` is removed rather than kept as a second meaning,
  since nothing in this JE type currently reads the current-period figure on its own.
  The API contract gained a field this fix needed and every check benefits from: `CheckView`
  carries `samples: FigureSampleView[]` (row, cell, expected, actual, difference - each `None`
  where a tool's sample does not carry it, read defensively field-by-field so a tool whose
  `"expected"` key means something else entirely, like the formula audit's rewritten
  expression, is carried as `None` rather than failing the response). Additive: `cells` is
  unchanged and still built from the same samples. Frontend: `CheckFacts` renders a
  Row/Cell/Expected/Actual/Difference table in place of the bare cell list whenever a check's
  samples carry an amount, not behind a disclosure, same as the rest of this report. New
  integration test tampers `GPAY!M9` on a copy of 236264.xlsx (editing the worksheet XML in
  place, same technique as `make_discrepancy.py`) and asserts `accrual_composition_reperforms`
  fails with one cited row carrying expected, actual and a difference that reconciles.

- 2026-10-23 (2): the deployed frontend can reach the API. The app had no CORS middleware
  at all, so every call from the Vercel origin failed the preflight ("No
  'Access-Control-Allow-Origin' header is present"), including the upload, the listing, the
  delete and the SSE stream. New `CORS_ORIGINS` setting (empty on BaseConfig, localhost in
  dev, the deployed frontend in stag and prod, overridable as `APP_CORS_ORIGINS=a,b`) and
  `CORSMiddleware` in `app.py`, added only when an origin is configured. The frontend's
  `POST /api/auth/refresh` 404 is not CORS and is not this service: no auth endpoint exists
  here, by design. 4 integration tests on the preflight and the allow-origin header, 3 unit
  tests on the setting.

- 2026-10-29 (2): the formula switch made safe to throw. `formula_defects_can_fail_check`
  blocked a pass on *any* audit finding, and every sample workbook carries columns that
  arrive already calculated from the payroll extract - so the first business owner to turn
  that setting on would have failed every review, including the clean ones. Faults are now
  classified by `FormulaFault.is_defect`: different inputs, inconsistent cells and a typed-over
  constant may fail a check; a column the file states rather than calculates, and a format
  with no formulas, may not. The audit figure reports on defects alone for the same reason,
  and stays None where nothing could be read. The YAML now also says why the identities are
  written over column meanings while the entry formula is written over declared inputs with
  units - the one asymmetry a reader of that file would otherwise trip on.

- 2026-10-29: the business owns the record-level arithmetic too, and the workbook's own
  formulas are examined now. `reperformance.identities` in the JE type file (version 4)
  declares each identity as one line over the columns the working carries, with its per-row
  tolerance and its `min_tie_rate`; the new `reperform` tool evaluates any of them and
  replaced `recalc_accrual`, `recalc_leave_balance` and `recalc_salary_per_day`. The figures
  are unchanged on all three workbooks, knife-edge included. Declaring them surfaced a latent
  defect: `tables[].meanings` had no reader and omitted every leave column those tools read,
  so step 12 could not have reported one missing.
  Then `recalc/formula_audit.py`, which closes the gap the rebuild cannot see: for every
  column the business declares arithmetic for it reports a column that is not calculated,
  cells that disagree with each other, a constant typed over one, and - the one that matters
  - references resolving to different columns than the approved expression reads. Such a
  column ties on every record where the missing input is zero; demonstrated on 305800 with
  `=AF9` injected in place of `=AF9+AG9`, which still rebuilds to the cent with zero
  citations while the audit names the column, the cell, what the file does and what the
  business states instead. Two false findings were found and fixed on the way, both from the
  real files: the preparer's own `SUBTOTAL` under the data read as the column's derivation,
  and a typed-over constant reported from the header band above it. Frontend: a new lettered
  block in the recalculation section carrying the faults, the formula in the preparer's own
  headers beside the approved one, and distinct treatments for nothing-wrong and
  nothing-readable. 748 backend tests (90 golden assertions on the three workbooks), 252
  frontend tests, ruff/mypy/eslint/tsc clean.

- 2026-10-28 (2): `skills/scripts/make_discrepancy.py`, for seeing step 13c fail. The three
  sample workbooks all reconcile to the cent, so the citations, the failed check and the
  tolerance breach could only be seen by hand-editing a copy. `--preset` resolves the cells
  through the harness's own sheet map rather than naming column letters - it picks AF/AG/AH
  on the French periods and tracks the row offset between them without being told - and
  makes three edits, one per kind of discrepancy. It rewrites one worksheet part inside the
  zip rather than re-saving the workbook, because the posting tab's line amounts are formulas
  (`='JE Create'!O6`) and openpyxl writes formulas back without their cached values: the
  booked side then reads as absent and the review comes back inconclusive, which looks like
  the check failing to notice. Verified: the generated copy fails at 135,665.40 with three
  citations, the same figures as the live run. `T201` is ignored for `skills/scripts/**` -
  a script run by hand prints, a service logs.

- 2026-10-28: step 19 stopped reporting cell references as untraced numbers. `_NUMBER` had
  no left boundary, so the digits inside `AH230` matched as 230 and a next step reading
  "example cell AH230 with -1074.35" listed 230 as a figure that traces to nothing - on a
  live run of 305800.xlsx, where the cell was the one the negative-accrual check cites. The
  lookbehind excludes digits, dots, commas and hyphens as well as letters, because the
  scanner restarts one character later on a blocked position: letters alone would still have
  matched the `30` of `AH230` and the `12` of `F-12`. Fiscal periods (`FY26`, `P07`) and
  figure ids stop being reported for the same reason, and a real amount beside a reference
  ("the -1,074.35 at cell AH230") is still checked. The test that had pinned the old
  behaviour is now the one that pins this. 706 backend tests, ruff/mypy clean.

- 2026-10-27: the posted amount, rebuilt from its inputs. New step **13c**: the approved
  formula is now business knowledge in the JE type file (`recalculation:` - formula, inputs
  with units and sources, sign, currency, rounding, day count, tolerance, worked example),
  parsed at startup by `review/recalc/formula.py` into closures and evaluated per record by
  `review/recalc/engine.py` in one pass over the working. The recomputed total is set against
  the booked amount read from the posting tab's own debit lines, and every record that does
  not reconcile is a citation: the row, the cell the file states its figure in, the cells each
  input came from, and a sentence written by code. A missing input is reported, never read as
  a zero. `recalc_entry_total` turns the same run into ordinary figures, so the new check
  (`entry_total_recalculates`, in `accrual_reperformance`) reaches its status through step 16
  with no special case; the work paper is persisted on `ReviewResult` and served as
  `ReviewDetail.recalculation`. Frontend: a new numbered section, `EntryRecalculation.tsx`,
  whose head has three distinct states - rebuilt to the cent (the figure stated once, not a
  table of zeroes), a difference leading with the amount, the percentage and the tolerance it
  broke, and nothing-to-compare rendered neutral rather than red - plus the formula in the
  preparer's own headers, the inputs table, the citations, the corroborated legs and the
  basis. `Block` moved into `Section.tsx` so both work papers share it, and `formatAmount`
  joins `excelFormat` because `formatExcel` writes a whole number without cents, which is
  right for a count column and wrong for an entry total. Step 13c is on the progress stream,
  the `step_started` event carrying the row count so the line reads "37,613 records to re-add"
  rather than spinning. Verified against all three real workbooks: 28,672,591.24 /
  27,905,812.64 / 8,435,498,474.10 rebuilt from 2,280, 2,273 and 37,613 records, each equal
  to its posting tab's debit total to the cent, in 23 ms, 24 ms and 202 ms; the RTT leg
  re-performs from days times rate on every row of both French periods. Found on the way: the
  figure quoted the posted line count in its own summary without offering it as traceable, so
  step 19 listed "426" as an untraced number on every review - every count the explanation
  states is now on the figure. Verified live against 305800.xlsx end to end (9 model calls,
  $0.28, 59 events): the entry rebuilt to 27,905,812.64 against the same amount booked, in
  15 ms, and the work paper came back from the stored record on a second upload rather than
  as a blank. Verified against a copy of the same workbook edited not to reconcile - one FTE
  accrual raised by 150,000, one stated total cut by 2,500, one FTE accrual emptied: fail,
  135,665.40 out (the raise *less* the record that could no longer be rebuilt, which is the
  proof the gap is not read as a zero), three citations, two kinds, worst first, 0.4862% and
  the tolerance it broke, and the RTT leg still re-performing on all 2,273 records. 702
  backend tests (15 golden assertions on the three workbooks), 245 frontend tests,
  ruff/mypy/eslint/tsc clean.

- 2026-10-23: the arithmetic a reviewer reads. New `orchestrator/split_summary.py` and
  `models/split_summary.py`: at step 18 the working is re-added row by row and divided by
  the classes its own classification column holds, each component is tied against the total
  the file states above its header, the formula behind each column is read and rewritten in
  the preparer's column names, one row is traced step by step, and every check that ran is
  reported with the status step 16 gave it. Persisted on `ReviewResult` and served as
  `ReviewDetail.split_summary`; a `step_finished` event announces it. Frontend: a new
  numbered section, `Recalculation.tsx`, with `lib/excelFormat.ts` rendering each figure from
  Excel's own format string - so an accounting negative keeps its brackets and an accounting
  zero stays a dash - and `ReviewResult` now hands out section numbers in reading order,
  which also fixes a spine that skipped 4 whenever the evidence register was empty. Nothing
  is labelled in code: the 37,613-row `.xlsb` on a tab called DENT produces a correct work
  paper with no edit. Found and fixed against the three real workbooks and one live upload:
  the SAP company code was being summed to 2,633,130 (now excluded as a code by its General
  format), a trailing subtotal row was doubling the TOTAL ACCRUAL column, two rates in the
  header band were reported as failed control totals, the matrix headers computed their
  accessible name as "FTE2,193 rows", and all five traced exception rows came from the one
  check that had 1,096 of them (now at most one row per check, so the traced rows span the
  causes), and on the `.xlsb` - which exposes no cell formats - the General test dropped two
  whole-number quantity columns while its naive inverse printed a 394-billion cost-centre
  code, so an unreadable format now falls back to whether the preparer totalled the column.
  Two pre-existing defects fell out of the renumbering: the spine printed 1, 2, 4
  whenever a review had nothing scored, and `ReviewResult`'s own spine test read `aria-label`
  on sections named by `aria-labelledby`, so it had been comparing an empty list to an empty
  list. Verified live against 305800.xlsx end to end: 9 model calls, $0.26, 2,273 rows
  re-added into 3 classes and 32 components, 15 of 15 control totals agreeing to the cent,
  18 tests carried with the statuses step 16 gave them, 3 rows traced. 578 backend tests,
  231 frontend tests, 15 golden assertions on all three workbooks, ruff/mypy/eslint clean.

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
