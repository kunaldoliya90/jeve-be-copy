"""The state machine that runs one review, and the only place step order is decided.

It owns ordering, concurrency, timeouts and progress. It **never judges content**: every
conclusion comes from a tool or a routing rule, and the orchestrator only decides what runs
next and what to do when something cannot run.

This build performs phases A and B deterministically and reports the rest as not yet run.
Phases that do not execute are *named* in the result rather than skipped silently — a reader
must never mistake an absent judgement for a passed one, which is the single easiest way for
a review tool to mislead.
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from functools import partial
from typing import Final
from uuid import uuid4

from api.consts import AICall, ErrorCode, JETypeSource, SSEEvent
from api.exceptions.review_exceptions import ReviewError, UploadRejectedError
from api.helpers.llmproviders.base_llm_provider import BaseLLMProvider
from api.pydanticmodels.llm_models import LLMResponse
from api.pydanticmodels.review_models import ProgressEvent, ReviewState
from review.agents.clustering import cluster, needs_clustering, reconciles
from review.agents.judging import challenge, judge
from review.agents.methodology import (
    digest,
    methodology_sheet,
    read_methodology,
    text_of,
)
from review.agents.picture import read_picture, worth_reading
from review.agents.summary import summarise
from review.agents.understand import Understanding, understand
from review.context.binder import BindResult, bind_sheet_map
from review.context.caches import LayoutCache, LayoutEntry, MethodologyCache, PictureCache
from review.context.identify import Identification
from review.context.loader import ContextError, ContextLoader
from review.context.prompts import PromptRegistry
from review.extract.excel_reader import read_workbook
from review.extract.profiler import profile
from review.extract.structure_context import build_structure_context
from review.guardrails.final_check import final_check
from review.guardrails.map_check import MapVerdict, map_check
from review.guardrails.plan_check import CheckedPlan, plan_check
from review.intake.gatekeeper import UploadOk, validate_upload
from review.intake.reuse import file_digest, reuse_key
from review.models.context import JETypeContext
from review.models.evidence import (
    EvidenceItem,
    EvidenceStatus,
    ExceptionCluster,
    Figure,
    MethodologyReading,
    PictureReading,
)
from review.models.plan import ExecutionPlan, PlanStep
from review.models.result import (
    AICallRecord,
    AuditTrail,
    EntryIdentity,
    ReportPayload,
    ReviewResult,
    Score,
)
from review.models.review_context import ReviewContext
from review.models.sheet_map import SheetMap
from review.models.split_summary import SplitSummary
from review.models.verdicts import Challenge, CheckOutcome, Verdict, strictness
from review.models.workbook import WorkbookProfile
from review.models.workbook_data import WorkbookData
from review.orchestrator.diagnosis import diagnose
from review.orchestrator.planner import merge, plan_from_recipes, propose_steps, unplanned
from review.orchestrator.rules import outcome_for
from review.orchestrator.runner import run_checks
from review.orchestrator.scoring import score_review
from review.orchestrator.split_summary import build_split_summary
from review.storage.reviews import ReviewStore, utc_now
from review.tools.base import ToolContext
from review.tools.entry import EvidenceRegisterTool, PeriodAgreementTool

logger = logging.getLogger(__name__)

QUEUE_SIZE: Final = 256
"""Progress events buffered per subscriber. A browser that stops reading is dropped rather
than allowed to hold the review open."""

MAX_LISTED_REJECTIONS: Final = 3
"""Shape candidates named in a report's caveat. The full list is on the audit trail."""

MAX_REMEMBERED: Final = 50
"""Finished runs held in memory. Every one of them keeps a workbook profile alive, and the
stored record is the durable copy, so the rest are read back from disk."""


class Phase(StrEnum):
    """The orchestrator's own states, as the LLD names them."""

    INTAKE = "A"
    UNDERSTAND = "B"
    PLAN = "C"
    EXECUTE = "D"
    JUDGE = "E"
    REPORT = "F"


NOT_JUDGED: Final = (
    "AI-3 Judge and AI-4 Challenge did not run, so every check settled by judgement "
    "rather than by arithmetic reports as unable to validate.",
)


def not_run_for(run: "ReviewRun") -> tuple[str, ...]:
    """What this particular review did not do, named rather than silently skipped.

    Every numbered step is built, so this is no longer a list of gaps in the software. It is
    a list of things that did not happen *to this file* — a missing key, a format with no
    formulas, a conditional step with nothing to work on. A reader must never mistake an
    absent judgement for a passed one, and the way to prevent that is to say which
    judgements were absent.
    """
    missing: list[str] = []

    if not run.judged:
        missing.append(
            "No model was available, so every check settled by judgement rather than by "
            "arithmetic reports as unable to validate, and nothing corroborated the entry "
            "type beyond the workbook's own shape."
        )
    if run.data is not None and not run.data.formulas_available:
        missing.append(
            "This format carries no formulas, so the checks that prove a line is linked "
            "rather than typed could not run."
        )
    if run.data is not None and not run.data.pictures:
        missing.append(
            "No screenshot is pasted into the workbook, so any evidence that would be one "
            "is reported as missing rather than as read."
        )
    if run.layout_hit:
        missing.append(
            "This layout had already been proved out, so the map and plan were reused "
            "rather than re-derived. The arithmetic ran in full either way."
        )
    return tuple(missing)


@dataclass
class ReviewRun:
    """One review in flight: its state, its progress and its subscribers."""

    review_id: str
    file_name: str
    period: str | None = None
    status: ReviewState = "queued"
    step: str = "1"
    progress_pct: int = 0
    started_at: float = field(default_factory=time.monotonic)
    created_at: datetime = field(default_factory=utc_now)
    error: tuple[ErrorCode, str] | None = None
    context: ReviewContext | None = None
    profile: WorkbookProfile | None = None
    data: WorkbookData | None = None
    upload: UploadOk | None = None
    score: Score | None = None
    report: ReportPayload | None = None
    map_verdict: MapVerdict | None = None
    split_summary: SplitSummary | None = None
    """The category split work paper, assembled at step 18. Held on the run so the live
    view serves the same object the stored record carries."""
    layout_key: str = ""
    layout_hit: bool = False
    je_type: JETypeContext | None = None
    je_type_source: JETypeSource = JETypeSource.UNKNOWN
    file_sha256: str = ""
    size_bytes: int = 0
    reuse_key: str = ""
    reuse_hit: bool = False
    reused_from: str | None = None
    """The review whose result this one reuses. Without it the detail view would answer
    from a run that deliberately never read the workbook, which reads as an empty result."""
    ai_calls: int = 0
    ai_tokens: int = 0
    ai_cost: Decimal = field(default_factory=lambda: Decimal(0))
    call_records: list[AICallRecord] = field(default_factory=list)
    subscribers: list[asyncio.Queue[ProgressEvent | None]] = field(default_factory=list)

    @property
    def duration_ms(self) -> int:
        return int((time.monotonic() - self.started_at) * 1000)

    @property
    def judged(self) -> bool:
        """Whether any model contributed to this review."""
        return run_judged(self)

    @property
    def finished(self) -> bool:
        return self.status in {"completed", "needs_review", "failed", "rejected"}


_STEP_WEIGHT: Final[dict[str, int]] = {
    "2": 4,
    "3": 8,
    "4": 40,
    "5": 52,
    "6": 56,
    "7a": 60,
    "7": 62,
    "8": 68,
    "9": 72,
    "11": 74,
    "12": 76,
    "13a": 77,
    "13": 78,
    "13b": 82,
    "14": 88,
    "15": 93,
    "16": 94,
    "17": 95,
    "18": 97,
    "19": 98,
    "20": 100,
}
"""Rough progress per step. Reading the workbook dominates the wall clock, so the bar
reflects that rather than pretending the steps are evenly sized.

The values must not decrease along ``_STEP_ORDER``: the bar is read as reassurance that
something is happening, and one that slides backwards reads as a failure. Steps 5 and 6
previously sat at 62 and 70, above step 7a at 60, so every review visibly lost ten points
of progress on recognising its own layout. ``test_step_weights_never_go_backwards`` pins
it.
"""


_STEP_ORDER: Final[tuple[str, ...]] = (
    "2",
    "3",
    "4",
    "5",
    "6",
    "7a",
    "7",
    "8",
    "9",
    "11",
    "12",
    "13a",
    "13",
    "13b",
    "14",
    "15",
    "16",
    "17",
    "18",
    "19",
    "20",
)
"""The order ``_phases`` runs the steps in. Only the progress weighting depends on it."""


_AI_STEPS: Final[dict[AICall, tuple[str, str]]] = {
    AICall.UNDERSTAND: ("8", "Reading the workbook independently"),
    AICall.PLAN: ("11", "Planning the checks"),
    AICall.METHODOLOGY: ("13a", "Reading the methodology"),
    AICall.PICTURE_READER: ("13a", "Reading the screenshots"),
    AICall.CLUSTERING: ("13b", "Grouping the exceptions"),
    AICall.JUDGE: ("14", "Judging the results"),
    AICall.CHALLENGE: ("15", "Challenging the conclusions"),
    AICall.SUMMARY: ("18", "Writing the report"),
}
"""Which numbered step is charged for each model call.

Looked up here rather than read off ``run.step`` at the time of the call: AI-M and AI-R
fire between step 12 and step 13, so whichever step happened to be current would be billed
for them, and the cost per step is the thing this exists to get right.
"""


class ReviewOrchestrator:
    """Runs reviews and fans progress out to anyone watching.

    One asyncio task per review, bounded by ``max_active``. The CPU-bound parsing runs in a
    worker thread so the event loop keeps serving progress while a 12 MB workbook is read.

    Every limit is a required keyword: they are all settings, and the lifespan is the one
    place that reads ``config``, so none of them has a default here to drift from it.
    """

    def __init__(
        self,
        store: ReviewStore,
        context: ContextLoader,
        prompts: PromptRegistry,
        provider: BaseLLMProvider | None = None,
        layouts: LayoutCache | None = None,
        *,
        max_active: int,
        deadline_s: float,
        max_upload_bytes: int,
        tool_workers: int,
        structure_context_max_tokens: int,
        figure_samples: int,
    ) -> None:
        self._store = store
        self._context = context
        self._prompts = prompts
        # In memory and per worker on purpose. They hold nothing a review needs — a miss
        # costs a model call, never a wrong answer — so they do not earn a database.
        self._layouts = layouts or LayoutCache()
        self._methodologies = MethodologyCache()
        self._readings = PictureCache()
        # None when no key is configured. The arithmetic runs either way; the review then
        # says plainly that nothing was judged rather than inventing a conclusion.
        self._provider = provider
        self._max_active = max_active
        self._deadline_s = deadline_s
        self._max_upload_bytes = max_upload_bytes
        # Step 13's pool, what AI-1 may see of the workbook, and the exemplars a figure
        # carries, in that order.
        self._tool_workers = tool_workers
        self._structure_max_tokens = structure_context_max_tokens
        self._figure_samples = figure_samples
        self._runs: dict[str, ReviewRun] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    # ------------------------------------------------------------------ state

    def get(self, review_id: str) -> ReviewRun | None:
        return self._runs.get(review_id)

    def forget(self, review_id: str) -> None:
        """Drop one finished run from memory.

        Safe only because step 20 stored it: before the record was written, evicting a run
        lost the result. A run still in flight is kept — forgetting it would orphan the
        task and the progress stream watching it.
        """
        run = self._runs.get(review_id)
        if run is not None and run.finished:
            del self._runs[review_id]

    def _prune(self) -> None:
        """Keep memory bounded, newest first.

        A worker that never forgets a review holds every workbook profile it has ever read.
        The stored record is the durable copy, so what is dropped here is only the
        convenience of answering without touching the disk.
        """
        finished = [run for run in self._runs.values() if run.finished]
        if len(finished) <= MAX_REMEMBERED:
            return
        finished.sort(key=lambda run: run.started_at, reverse=True)
        for run in finished[MAX_REMEMBERED:]:
            if not run.subscribers:
                del self._runs[run.review_id]

    def live_runs(self) -> list[ReviewRun]:
        """Reviews this worker still holds, newest first.

        A listing built only from the store would omit an upload still being processed, and
        to the person who just made it that reads as the upload having vanished.
        """
        return sorted(self._runs.values(), key=lambda run: run.started_at, reverse=True)

    @property
    def active(self) -> int:
        return sum(1 for run in self._runs.values() if not run.finished)

    def at_capacity(self) -> bool:
        return self.active >= self._max_active

    # ------------------------------------------------------------------ running

    def start(
        self,
        review_id: str,
        file_name: str,
        content: bytes,
        je_type: str | None,
    ) -> ReviewRun:
        """Validate synchronously, then run the rest in the background.

        The gatekeeper runs here, before the 202: a file the system will refuse should be
        refused in the response to the upload, not reported as a failure thirty seconds
        later through a progress stream.
        """
        run = ReviewRun(review_id=review_id, file_name=file_name)
        self._runs[review_id] = run

        upload = validate_upload(file_name, content, max_bytes=self._max_upload_bytes)
        run.upload = upload
        run.status = "queued"

        task = asyncio.create_task(self._run(run, content, je_type))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return run

    async def _run(self, run: ReviewRun, content: bytes, je_type: str | None) -> None:
        try:
            await asyncio.wait_for(self._phases(run, content, je_type), timeout=self._deadline_s)
        except TimeoutError:
            # Nothing is cached from a timed-out review: a partial read may have bound
            # columns that a complete one would not.
            self._fail(run, ErrorCode.REVIEW_TIMEOUT, "The review exceeded its deadline")
        except UploadRejectedError as exc:
            run.status = "rejected"
            self._fail(run, exc.error_code, exc.message, status="rejected")
        except ReviewError as exc:
            self._fail(run, exc.error_code, exc.message)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            logger.exception("review %s failed unexpectedly", run.review_id)
            self._fail(run, ErrorCode.INTERNAL, f"{type(exc).__name__}: {exc}")
        finally:
            await self._close(run)
            self._prune()

    async def _phases(self, run: ReviewRun, content: bytes, je_type: str | None) -> None:
        assert run.upload is not None  # noqa: S101 - set by start() before the task is created
        run.status = "running"
        await self._emit(
            run,
            SSEEvent.REVIEW_STARTED,
            step="2",
            name="Gatekeeper",
            detail={"file_name": run.file_name, "size_bytes": run.upload.size_bytes},
        )

        # --- step 3: has this exact file been reviewed under these exact rules? ---
        await self._step(run, "3", "Reuse check")
        digest = file_digest(content)
        versions = {
            "je_type": self._context.je_type_version(je_type) if je_type else "",
            "rules": self._context.rules_version,
            "harness": _HARNESS_VERSION,
        }
        key = reuse_key(file_sha256=digest, submitted_je_type=je_type, versions=versions)
        run.file_sha256 = digest
        run.size_bytes = run.upload.size_bytes
        run.reuse_key = key
        if (stored := self._store.find_by_reuse_key(key)) is not None:
            await self._emit(
                run, SSEEvent.CACHE_HIT, step="3", name="Reuse", detail={"kind": "reuse"}
            )
            run.status = stored.status
            run.reuse_hit = True
            run.reused_from = stored.review_id
            run.score = stored.score
            run.progress_pct = 100
            run.step = "20"
            await self._emit(run, SSEEvent.REVIEW_COMPLETED, step="20", name="Reused")
            return

        # --- steps 4 to 6: read, measure, reduce. CPU-bound, so off the event loop. ---
        await self._step(run, "4", "Reading the workbook")
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(None, read_workbook, content, run.upload.workbook_format)
        run.data = data
        await self._emit(
            run,
            SSEEvent.STEP_FINISHED,
            step="4",
            name="Reading the workbook",
            detail={
                "sheets": len(data.sheets),
                "cells": sum(len(sheet.cells) for sheet in data.sheets),
                "formulas_available": data.formulas_available,
            },
        )

        await self._step(run, "5", "Profiling the structure")
        measured = await loop.run_in_executor(None, profile, data)
        run.profile = measured
        await self._emit(
            run,
            SSEEvent.STEP_FINISHED,
            step="5",
            name="Profiling the structure",
            detail={
                "tables": len(measured.tables),
                "formula_shapes": len(measured.formula_shapes),
                "defects": len(measured.defects),
            },
        )

        await self._step(run, "6", "Building the structure context")
        structure = build_structure_context(measured, data, max_tokens=self._structure_max_tokens)
        run.context = ReviewContext(
            review_id=run.review_id,
            reuse_key=key,
            profile=measured,
            structure=structure,
            je_type=je_type or "unknown",
            versions=versions,
        )
        await self._emit(
            run,
            SSEEvent.STEP_FINISHED,
            step="6",
            name="Building the structure context",
            detail={
                "layout_fingerprint": structure.layout_fingerprint[:12],
                "token_estimate": structure.token_estimate,
                "trimmed": structure.trimmed,
            },
        )

        # --- step 7a: recognise the type and bind the columns, with no model involved ---
        await self._step(run, "7a", "Recognising the layout")
        binding = bind_sheet_map(measured, data, self._context.by_precedence())
        recognised = binding.identification or Identification()
        run.context.sheet_map = binding.sheet_map
        run.context.je_type = binding.sheet_map.je_type
        run.context.je_type_variant = recognised.variant
        await self._emit(
            run,
            SSEEvent.STEP_FINISHED,
            step="7a",
            name="Recognising the layout",
            detail={
                "je_type": recognised.je_type,
                "variant": recognised.variant,
                "confidence": recognised.confidence,
                "tables_bound": len(binding.bound),
                "unbound": ", ".join(binding.unbound_tables) or "none",
                "unresolved_headers": len(binding.unresolved_headers),
            },
        )
        if not binding.is_usable:
            # Without the calculation table nothing can be re-performed. Stopping here is
            # the point: a plausible binding to the wrong columns would produce a confident
            # score from the wrong arithmetic, which is worse than no score at all. What is
            # not acceptable is stopping silently, so the run ends with a report naming
            # every table and header that could not be placed.
            await self._finish_unmapped(
                run,
                binding,
                measured,
                declared=je_type,
                name="Could not be mapped",
                detail={"provisional": True, "unbound": ", ".join(binding.unbound_tables)},
            )
            return

        # --- step 7: has this exact layout already been proved out? ---
        await self._step(run, "7", "Checking for a known layout")
        # Keyed on the version of the type that was *identified*, not the one submitted: a
        # layout's map and plan depend on the rules actually applied to it, and a reviewer
        # who typed nothing on the upload form must not get a stale map because of it.
        run.layout_key = self._layouts.key(
            structure.layout_fingerprint,
            je_type_version=(
                self._context.je_type_version(recognised.je_type)
                if recognised.recognised
                else recognised.je_type
            ),
            understand_prompt=self._prompts.get(AICall.UNDERSTAND).version_string,
            plan_prompt=self._prompts.get(AICall.PLAN).version_string,
            model=self._provider.model if self._provider else "none",
        )
        known = self._layouts.get(run.layout_key)
        run.layout_hit = known is not None
        if known is not None:
            await self._emit(
                run,
                SSEEvent.CACHE_HIT,
                step="7",
                name="Known layout",
                detail={"kind": "layout", "je_type": known.je_type},
            )

        # --- step 8: a second, independent reading of what this workbook is ---
        # Skipped on a layout hit: this fingerprint was read, corroborated and proved out
        # under these same prompts, and paying to re-derive the same answer buys nothing.
        understanding: Understanding | None = None
        if known is None and self._provider is not None and structure is not None:
            await self._step(run, "8", "Reading the workbook independently")
            understanding, read = await understand(
                self._provider, self._prompts, structure, self._context.catalogue
            )
            await self._record_call(run, AICall.UNDERSTAND, read)

        # --- step 9: does that reading survive checking against the workbook? ---
        await self._step(run, "9", "Verifying the reading")
        verdict = map_check(understanding, recognised, binding.sheet_map, data)
        run.map_verdict = verdict
        run.context.sheet_map = binding.sheet_map.model_copy(
            update={
                "je_type_confidence": verdict.confidence,
                "je_type_citations": verdict.citations,
            }
        )
        await self._emit(
            run,
            SSEEvent.STEP_FINISHED,
            step="9",
            name="Verifying the reading",
            detail={
                "agrees": verdict.agrees,
                "confidence": verdict.confidence,
                "citations_verified": len(verdict.citations),
            },
        )

        # --- step 10: whose checks run. The shape first; failing that, what was declared ---
        je_spec = self._spec_for(recognised, je_type)
        if je_spec is None:
            # Nothing matched the shape and nobody named a type, so there is no set of
            # checks to run: inventing one would score this workbook against a methodology
            # nobody established it follows. The report says what was ruled out and why.
            await self._finish_unmapped(
                run,
                binding,
                measured,
                declared=je_type,
                name="Type not recognised",
                detail={"provisional": True, "why": "; ".join(recognised.rejected[:3])},
            )
            return

        run.je_type_source = JETypeSource.SHAPE if recognised.recognised else JETypeSource.DECLARED
        run.context.je_type = je_spec.id
        if run.je_type_source is JETypeSource.DECLARED:
            # The checks still run — a review that cannot be identified is not a review that
            # cannot be performed — but nothing below may read as confirmed, so the status
            # is pinned at step 17 and the caveat is prepended to the report's gaps.
            logger.info(
                "review %s: shape matched nothing; running the declared type %s",
                run.review_id,
                je_spec.id,
            )
            await self._emit(
                run,
                SSEEvent.STEP_FINISHED,
                step="10",
                name="Using the declared entry type",
                detail={
                    "je_type": je_spec.id,
                    "why": "the workbook's shape matched no type in the catalogue",
                    "ruled_out": "; ".join(recognised.rejected[:3]) or "none",
                },
            )
        # Held on the run from here rather than from step 17: the header naming the entry
        # type, its process and its sub-process is what a reviewer reads while the checks
        # are still running, and it is already settled by this point.
        run.je_type = je_spec
        rules = self._context.rules
        run.context.facts = _read_facts(data)

        # --- step 11: the plan. Recipes for every check that states one; AI-2 for the rest ---
        await self._step(run, "11", "Planning the checks")
        plan = known.plan if known is not None else plan_from_recipes(je_spec)
        gaps = unplanned(je_spec, plan)
        proposed: tuple[PlanStep, ...] = ()
        if gaps and known is None:
            proposed, planned_by_ai = await propose_steps(
                self._provider, self._prompts, je_spec, binding.sheet_map, gaps
            )
            await self._record_call(run, AICall.PLAN, planned_by_ai)
            plan = merge(plan, proposed)
        # How the plan was arrived at, not just that it was. A reviewer reads a
        # plan drawn entirely from fixed recipes differently from one a model had a
        # hand in, and until now the stream gave them no way to tell.
        await self._emit(
            run,
            SSEEvent.STEP_FINISHED,
            step="11",
            name="Planning the checks",
            detail={
                "steps": len(plan.steps),
                "from_cache": known is not None,
                "proposed_by_model": len(proposed),
            },
        )

        # --- step 12: no tool runs on arguments this workbook cannot support ---
        await self._step(run, "12", "Checking the plan")
        checked = plan_check(plan, binding.sheet_map)
        run.context.plan = checked.plan
        await self._emit(
            run,
            SSEEvent.STEP_FINISHED,
            step="12",
            name="Checking the plan",
            detail={
                "steps": len(checked.plan.steps),
                "source": checked.plan.source.value,
                "dropped": ", ".join(checked.dropped_checks) or "none",
            },
        )

        # --- step 13, first: the evidence that is not a number. AI-M reads the methodology
        # tab's prose into parameters; AI-R reads the pasted screenshots. Neither feeds any
        # arithmetic: both exist to be compared against what the workbook already says. ---
        if self._provider is not None:
            await self._step(run, "13a", "Reading the evidence that is not a number")
        reading = await self._read_methodology(run, data, binding.sheet_map)
        readings = await self._read_pictures(run, data)

        # --- step 13: every step, concurrently, on a worker thread ---
        await self._step(run, "13", "Running the checks")
        tool_ctx = ToolContext(
            data=data,
            sheet_map=binding.sheet_map,
            rules=rules,
            facts=run.context.facts,
            defects=measured.defects,
            methodology=je_spec.methodology,
            methodology_read=reading,
            picture_readings=readings,
            figure_samples=self._figure_samples,
        )
        pack = await loop.run_in_executor(
            None,
            partial(run_checks, checked.plan, tool_ctx, max_workers=self._tool_workers),
        )
        run.context.evidence_pack = pack
        run.context.evidence_register = _evidence_register(pack, je_spec)
        run.period = _period_from(pack)
        await self._emit(
            run,
            SSEEvent.STEP_FINISHED,
            step="13",
            name="Running the checks",
            detail={
                "checks": len(pack),
                "figures": sum(len(figures) for figures in pack.values()),
            },
        )

        # --- step 13b: too many exceptions to read as a list, so group them by cause ---
        clusters: dict[str, tuple[ExceptionCluster, ...]] = {}
        if any(needs_clustering(figures, rules.clustering) for figures in pack.values()):
            await self._step(run, "13b", "Grouping the exceptions")
            clusters, grouped = await cluster(self._provider, self._prompts, pack, rules.clustering)
            await self._record_call(run, AICall.CLUSTERING, grouped)
            if clusters:
                await self._emit(
                    run,
                    SSEEvent.STEP_FINISHED,
                    step="13b",
                    name="Grouping the exceptions",
                    detail={
                        "checks": len(clusters),
                        "causes": sum(len(found) for found in clusters.values()),
                    },
                )

        # --- steps 14 and 15: conclude, then read the same evidence again ---
        verdicts: dict[str, Verdict] = {}
        challenges: dict[str, Challenge] = {}
        if self._provider is not None:
            await self._step(run, "14", "Judging the results")
            verdicts, judged = await judge(
                self._provider,
                self._prompts,
                je_spec,
                rules,
                list(je_spec.checks),
                pack,
                run.context.facts,
                clusters,
            )
            await self._record_call(run, AICall.JUDGE, judged)
            await self._emit(
                run,
                SSEEvent.STEP_FINISHED,
                step="14",
                name="Judging the results",
                detail={"judged": len(verdicts), "checks": len(je_spec.checks)},
            )

            if verdicts:
                await self._step(run, "15", "Challenging the conclusions")
                challenges, challenged = await challenge(
                    self._provider,
                    self._prompts,
                    je_spec,
                    rules,
                    list(je_spec.checks),
                    pack,
                    verdicts,
                )
                await self._record_call(run, AICall.CHALLENGE, challenged)
                # Counted here rather than left for the reader to infer from the
                # outcomes: that a second opinion ran is not the interesting part,
                # that it tightened something is.
                await self._emit(
                    run,
                    SSEEvent.STEP_FINISHED,
                    step="15",
                    name="Challenging the conclusions",
                    detail={
                        "challenged": len(challenges),
                        "made_stricter": _tightened(verdicts, challenges),
                    },
                )

        # --- step 16: what the figures mean, and what goes to a person ---
        await self._step(run, "16", "Applying the rules")
        outcomes = {
            check.id: outcome_for(
                check,
                pack.get(check.id, ()),
                verdicts.get(check.id),
                challenges.get(check.id),
            )
            for check in je_spec.checks
        }
        run.context.outcomes = outcomes
        for outcome in outcomes.values():
            await self._emit(
                run,
                SSEEvent.CHECK_FINISHED,
                step="16",
                name=outcome.check_id,
                detail={
                    "check_id": outcome.check_id,
                    "area": outcome.area,
                    "status": outcome.final_status.value,
                    "needs_review": outcome.needs_review,
                },
            )

        # --- step 17: the score, pure arithmetic over those statuses ---
        await self._step(run, "17", "Scoring")
        run.score = score_review(je_spec, rules, outcomes, pack)
        # The score reaches the stream before the report is written, which is the
        # one figure a reader waiting on a review actually wants early.
        await self._emit(
            run,
            SSEEvent.STEP_FINISHED,
            step="17",
            name="Scoring",
            detail={
                "score": run.score.total,
                "band": run.score.band,
                "provisional": run.score.provisional,
            },
        )

        # A type nobody confirmed is a review nobody can sign off, whatever the score says.
        unconfirmed = run.je_type_source is JETypeSource.DECLARED
        run.status = (
            "needs_review"
            if run.score.provisional or verdict.disputed or unconfirmed
            else "completed"
        )

        # --- step 18: the work paper, then the prose over scores that are already fixed ---
        await self._step(run, "18", "Writing the report")
        # Built here rather than at step 13 because it reports the statuses step 16 reached
        # alongside its own arithmetic, and it runs in the executor because it walks every
        # row of the working a second time - on a 133k-row sheet that is not event-loop work.
        run.split_summary = await loop.run_in_executor(
            None,
            partial(
                build_split_summary,
                data=data,
                profile=measured,
                sheet_map=binding.sheet_map,
                rules=rules,
                je_spec=je_spec,
                pack=pack,
                outcomes=outcomes,
            ),
        )
        if run.split_summary is not None:
            paper = run.split_summary
            await self._emit(
                run,
                SSEEvent.STEP_FINISHED,
                step="18",
                name="Re-adding the working",
                detail={
                    "sheet": paper.basis.sheet,
                    "rows_read": paper.basis.rows_read,
                    "categories": len(paper.categories),
                    "components": len(paper.components),
                },
            )
        report, written = await summarise(
            self._provider,
            self._prompts,
            je_spec,
            run.score,
            outcomes,
            pack,
            (
                *_identification_notes(run, recognised, je_spec),
                *verdict.notes,
                *_plan_notes(checked),
                *_cluster_notes(clusters, pack),
            ),
            clusters,
            steps=_identification_steps(run, je_spec),
        )
        await self._record_call(run, AICall.SUMMARY, written)

        # --- step 19: every number in that prose must trace to a figure or the score ---
        await self._step(run, "19", "Tracing the numbers")
        run.report = final_check(report, run.score, pack, outcomes)
        if run.report.untraced_numbers:
            await self._emit(
                run,
                SSEEvent.STEP_FINISHED,
                step="19",
                name="Tracing the numbers",
                detail={"untraced": ", ".join(run.report.untraced_numbers)},
            )

        run.progress_pct = 100
        run.step = "20"

        # --- step 7, the other half: remember a layout this review proved out ---
        # Not from a declared type: caching that map would spread one person's answer about
        # one upload across every future file with the same fingerprint.
        if run.je_type_source is JETypeSource.SHAPE:
            self._remember_layout(run, je_spec, checked.plan, verdict)

        # --- step 20: the record, written once and never updated ---
        await self._step(run, "20", "Storing the record")
        self._persist(run, outcomes)

        await self._emit(
            run,
            SSEEvent.REVIEW_COMPLETED,
            step="20",
            name="Report",
            detail={
                "score": run.score.total,
                "band": run.score.band,
                "provisional": run.score.provisional,
                "ai_calls": run.ai_calls,
                "cost_usd": str(run.ai_cost),
            },
        )

    async def _read_methodology(
        self, run: ReviewRun, data: WorkbookData, sheet_map: SheetMap
    ) -> MethodologyReading | None:
        """AI-M, where there is a methodology tab and a model to read it with.

        Cached on the tab's own words, so a methodology that has not changed is read once
        however many entries arrive under it — and one that *has* changed is read again,
        which is the point.
        """
        sheet = methodology_sheet(data, sheet_map)
        if sheet is None:
            return None

        lines = text_of(sheet)
        if not lines:
            return None

        key = self._methodologies.key(
            digest(lines),
            reader_prompt=self._prompts.get(AICall.METHODOLOGY).version_string,
        )
        if (known := self._methodologies.get(key)) is not None:
            return known

        reading, response = await read_methodology(self._provider, self._prompts, lines)
        await self._record_call(run, AICall.METHODOLOGY, response)
        if reading is not None:
            self._methodologies.put(key, reading)
        return reading

    async def _read_pictures(self, run: ReviewRun, data: WorkbookData) -> dict[str, PictureReading]:
        """AI-R on every pasted screenshot big enough to be one, cached on its own bytes.

        Sequential rather than concurrent: a workbook carries a handful of these, and the
        provider's rate limit is a worse thing to spend than a second of wall clock.
        """
        readings: dict[str, PictureReading] = {}
        if self._provider is None:
            return readings

        for picture in data.pictures:
            if not worth_reading(picture):
                continue
            key = self._readings.key(
                picture.sha256,
                reader_prompt=self._prompts.get(AICall.PICTURE_READER).version_string,
            )
            if (known := self._readings.get(key)) is not None:
                readings[picture.picture_id] = known
                continue

            read, response = await read_picture(self._provider, self._prompts, picture)
            await self._record_call(run, AICall.PICTURE_READER, response)
            if read is not None:
                self._readings.put(key, read)
                readings[picture.picture_id] = read

        if readings and run.context is not None:
            run.context.pictures_read = dict(readings)
        return readings

    def _remember_layout(
        self,
        run: ReviewRun,
        je_spec: JETypeContext,
        plan: ExecutionPlan,
        verdict: MapVerdict,
    ) -> None:
        """Cache the map and plan, but only from a review that had nothing wrong with them.

        A map that needed a note is a map nobody has confirmed. Caching it would spread one
        doubtful reading across every future file from the same template, which is the one
        way a cache here could do harm rather than merely fail to help.
        """
        if run.context is None or run.context.sheet_map is None or run.layout_hit:
            return
        self._layouts.put(
            run.layout_key,
            LayoutEntry(
                sheet_map=run.context.sheet_map,
                plan=plan,
                je_type=je_spec.id,
                variant=run.context.je_type_variant,
            ),
            map_problems=len(verdict.notes),
        )

    def _spec_for(self, recognised: Identification, declared: str | None) -> JETypeContext | None:
        """Whose checks to run: what the shape settled, else what the uploader named.

        The shape always wins. The fallback exists because the alternative is worse than an
        unconfirmed review: a workbook the discriminators have not been taught yet is a
        workbook nobody checks at all, and the person who uploaded it already told the
        system what they believe it is. Everything downstream records that the type was
        declared rather than observed — the confidence stays at zero, the status never
        reaches ``completed`` and the layout is never cached from it.
        """
        if recognised.recognised:
            return self._context.je_type(recognised.je_type)
        if declared is None:
            return None
        try:
            return self._context.je_type(declared)
        except ContextError:
            logger.info("review declared unknown je type %r; nothing to run", declared)
            return None

    async def _finish_unmapped(
        self,
        run: ReviewRun,
        binding: BindResult,
        profile: WorkbookProfile,
        *,
        declared: str | None,
        name: str,
        detail: dict[str, str | int | float | bool | None],
    ) -> None:
        """End a review that could not be checked, saying why in terms a person can act on.

        This path runs no check, so it produces no score — but it is still a work product:
        it is written to the store like any other, because a review that vanishes on a
        restart is indistinguishable from one that was never uploaded.
        """
        run.report = diagnose(
            binding,
            profile,
            declared=declared,
            catalogue=sorted(entry["id"] for entry in self._context.catalogue),
        )
        run.status = "needs_review"
        run.progress_pct = 100
        run.step = "20"
        self._persist(run, {})
        await self._emit(run, SSEEvent.REVIEW_COMPLETED, step="20", name=name, detail=detail)

    def _persist(self, run: ReviewRun, outcomes: dict[str, CheckOutcome]) -> None:
        """Store the finished review with the trail that lets it be re-read later.

        Never fatal. A review that produced a result and could not be written is still a
        result the person watching should see, so a storage failure is logged and the run
        completes — but it is logged loudly, because an audit work product that was not
        kept is a gap somebody has to know about.
        """
        if run.context is None:
            return

        structure = run.context.structure
        result = ReviewResult(
            review_id=run.review_id,
            status=run.status,
            je_type=run.context.je_type,
            entry=entry_identity(run),
            score=run.score,
            checks=tuple(outcomes.values()),
            evidence_register=run.context.evidence_register,
            split_summary=run.split_summary,
            report=run.report or ReportPayload(headline=_headline_for(run)),
            audit=AuditTrail(
                versions=dict(run.context.versions),
                file_sha256=run.file_sha256,
                file_name=run.file_name,
                size_bytes=run.size_bytes,
                layout_fingerprint=structure.layout_fingerprint if structure else "",
                reuse_hit=run.reuse_hit,
                layout_hit=run.layout_hit,
                ai_calls=tuple(run.call_records),
                flags=tuple(
                    sorted(
                        {
                            flag.value
                            for outcome in outcomes.values()
                            for flag in outcome.review_reasons
                        }
                    )
                ),
                total_cost_usd=run.ai_cost,
                total_tokens=run.ai_tokens,
                je_type_confidence=run.map_verdict.confidence if run.map_verdict else 0.0,
                je_type_corroborated=run.map_verdict.agrees if run.map_verdict else False,
                je_type_source=run.je_type_source.value,
                duration_ms=run.duration_ms,
            ),
            created_at=run.created_at,
        )

        try:
            self._store.write(result, file_name=run.file_name, reuse_key=run.reuse_key)
        except (OSError, FileExistsError, ValueError):
            logger.exception("review %s completed but could not be stored", run.review_id)

    # ------------------------------------------------------------------ progress

    async def _record_call(
        self, run: ReviewRun, call: AICall, response: LLMResponse | None
    ) -> None:
        """Meter one model call. Cost and tokens are reported, never estimated."""
        if response is None:
            return
        run.ai_calls += 1
        run.ai_tokens += response.usage.total_tokens
        run.ai_cost += response.cost.total_usd
        step, name = _AI_STEPS.get(call, (run.step, ""))
        run.call_records.append(
            AICallRecord(
                call=call.value,
                step=step,
                name=name,
                model=response.model,
                input_tokens=response.usage.input_tokens,
                cached_tokens=response.usage.cached_input_tokens,
                output_tokens=response.usage.output_tokens,
                reasoning_tokens=response.usage.reasoning_tokens,
                cost_usd=response.cost.total_usd,
                latency_ms=response.latency_ms,
                schema_valid=not response.incomplete_reason,
            )
        )
        await self._emit(
            run,
            SSEEvent.AI_CALL,
            step=step,
            name=call.value,
            detail={
                "call": call.value,
                "model": response.model,
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "total_tokens": response.usage.total_tokens,
                "latency_ms": response.latency_ms,
                "cost_usd": str(response.cost.total_usd),
            },
        )

    async def _step(self, run: ReviewRun, step: str, name: str) -> None:
        run.step = step
        run.progress_pct = _STEP_WEIGHT.get(step, run.progress_pct)
        await self._emit(run, SSEEvent.STEP_STARTED, step=step, name=name)

    async def _emit(
        self,
        run: ReviewRun,
        event: SSEEvent,
        *,
        step: str = "",
        name: str = "",
        detail: dict[str, str | int | float | bool | None] | None = None,
    ) -> None:
        message = ProgressEvent(
            event=event,
            review_id=run.review_id,
            step=step or run.step,
            name=name,
            ts=utc_now(),
            progress_pct=run.progress_pct,
            detail=detail or {},
        )
        for queue in list(run.subscribers):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                # A browser that stopped reading must not hold the review open.
                logger.info("dropping a slow subscriber on review %s", run.review_id)
                run.subscribers.remove(queue)

    def _fail(
        self, run: ReviewRun, code: ErrorCode, message: str, *, status: ReviewState = "failed"
    ) -> None:
        run.status = status
        run.error = (code, message)
        run.progress_pct = 100
        logger.warning("review %s %s: %s", run.review_id, status, message)

    async def _close(self, run: ReviewRun) -> None:
        if run.error is not None:
            await self._emit(
                run,
                SSEEvent.REVIEW_FAILED,
                detail={"error_code": run.error[0], "message": run.error[1]},
            )
        for queue in list(run.subscribers):
            queue.put_nowait(None)

    # ------------------------------------------------------------------ streaming

    async def subscribe(self, run: ReviewRun) -> AsyncIterator[ProgressEvent]:
        """Yield progress until the review ends, then stop so the connection closes."""
        queue: asyncio.Queue[ProgressEvent | None] = asyncio.Queue(maxsize=QUEUE_SIZE)
        run.subscribers.append(queue)
        try:
            if run.finished:
                return
            while (event := await queue.get()) is not None:
                yield event
        finally:
            if queue in run.subscribers:
                run.subscribers.remove(queue)


_HARNESS_VERSION: Final = "2.0.0-dev"


def _tightened(verdicts: dict[str, Verdict], challenges: dict[str, Challenge]) -> int:
    """How many conclusions the second opinion actually made stricter.

    A challenge may only ever tighten a status, never loosen one, so a looser
    ``status_should_be`` is discarded upstream and is not counted here either. What is
    counted is the number a reader would care about: how often reading the same figures a
    second time changed the answer, and in the only direction it is allowed to.
    """
    return sum(
        1
        for check_id, found in challenges.items()
        if found.status_should_be is not None
        and check_id in verdicts
        and strictness(found.status_should_be) > strictness(verdicts[check_id].status)
    )


def document_number_of(file_name: str) -> str | None:
    """The entry's document number, taken from the name the workbook was uploaded under.

    ``236264.xlsx`` is document 236264. The suffix is dropped because a document number
    with a file extension on the end is not a document number, and the full name with its
    format is reported beside this anyway.

    Cut at the last dot rather than handed to ``pathlib``, for two reasons. A browser on
    Windows can send a name with backslashes in it, and a path class would read those as
    directories and return only the last segment of a name the user can see in full on
    their own screen. And ``pathlib`` reads ``.xlsx`` as a hidden file with no suffix at
    all, so a workbook uploaded under that name would be given ".xlsx" as its document
    number; here it yields nothing, and the field reads as absent.
    """
    name = file_name.strip()
    stem = name.rsplit(".", 1)[0].strip() if "." in name else name
    return stem or None


def entry_identity(run: ReviewRun) -> EntryIdentity:
    """Which entry this review is of. Everything here the system worked out for itself.

    The document number is the name the workbook was uploaded under, less its suffix:
    these files are named for the entry they hold, and asking a person to retype that on
    the upload form produced a blank field on nearly every review.

    The risk rating is the band the score reached, so it does not exist until step 17 has
    run. It is the score restated, not a second opinion on it.
    """
    return EntryIdentity(
        document_number=document_number_of(run.file_name),
        risk_rating=run.score.risk_rating if run.score else None,
        je_type=run.context.je_type if run.context else "unknown",
        je_type_name=run.je_type.name if run.je_type else "",
        je_type_variant=run.context.je_type_variant if run.context else None,
        process=run.je_type.process if run.je_type else "",
        sub_process=run.je_type.sub_process if run.je_type else "",
        period=run.period,
    )


def _evidence_register(
    pack: dict[str, tuple[Figure, ...]], je_spec: JETypeContext
) -> tuple[EvidenceItem, ...]:
    """The expected evidence layers, each with what the workbook actually offered.

    Built from the register tool's own figure rather than re-derived here: the tool decided
    what satisfies a layer, and a second opinion in the orchestrator is how a report comes
    to disagree with the check it is reporting on. A layer the tool never looked at stays
    missing, which is the safe way for the two to fall out of step.
    """
    looked_at = {
        str(sample["item"]): sample
        for figures in pack.values()
        for figure in figures
        if figure.tool == EvidenceRegisterTool.name
        for sample in figure.samples
        if sample.get("item")
    }

    register: list[EvidenceItem] = []
    for spec in je_spec.evidence:
        sample = looked_at.get(spec.id)
        present = bool(sample and sample.get("present"))
        issue = str(sample["issue"]) if sample and sample.get("issue") else ""
        location = str(sample["location"]) if sample and sample.get("location") else None
        register.append(
            EvidenceItem(
                item_id=spec.id,
                expected=spec.expected,
                required=spec.required,
                status=EvidenceStatus.AVAILABLE if present else EvidenceStatus.MISSING,
                location=location,
                remediation=spec.remediation or None,
                note=_evidence_note(spec.id, present, issue, location, sample is not None),
            )
        )
    return tuple(register)


def _evidence_note(
    item_id: str, present: bool, issue: str, location: str | None, examined: bool
) -> str:
    """One line saying why a layer reads as it does.

    "Missing" on its own sends a reviewer looking for a file that may already be in the
    workbook, so the four cases a register can be in are worded differently: satisfied,
    satisfied-but-flawed, looked for and not found, and never looked for at all.
    """
    if not examined:
        return f"No check examined {item_id.replace('_', ' ')}, so nothing was established."
    if present:
        return f"Satisfied by {location}." if location else "Satisfied by the workbook's own tabs."
    if issue:
        return f"Not available: {issue}."
    return f"No {item_id.replace('_', ' ')} was found in the workbook."


def _period_from(pack: dict[str, tuple[Figure, ...]]) -> str | None:
    """The fiscal period on the entry, taken from the figure that already read it.

    Code's own reading, never a model's, and never re-parsed here: the period agreement
    check counts the labels on the posting table, and taking its most common one means the
    header and that check can never disagree about what period this entry is in.

    The year is prefixed where the entry carries exactly one, because a bare "08" is not a
    period anybody can act on. Both halves are printed as the workbook spells them; no
    house notation is invented on top of what the preparer wrote.
    """
    samples = [
        sample
        for figures in pack.values()
        for figure in figures
        if figure.tool == PeriodAgreementTool.name
        for sample in figure.samples
        if sample.get("period")
    ]
    if not samples:
        return None
    period = str(samples[0]["period"])
    year = str(samples[0].get("fiscal_year") or "")
    return f"{year} P{period}" if year else period


def run_judged(run: ReviewRun) -> bool:
    """A review was judged if a judging call actually returned something."""
    return any(record.call in {"AI-3", "AI-4"} for record in run.call_records)


def new_review_id() -> str:
    """Sortable and readable: a reviewer reading a log can tell when a review ran."""
    stamp = utc_now().strftime("%Y%m%dT%H%M%S")
    return f"rev_{stamp}_{uuid4().hex[:8]}"


_FACT_LABELS: Final[dict[str, str]] = {
    "hash total": "hash_total",
    "je total": "je_total",
}


def _read_facts(data: WorkbookData) -> dict[str, object]:
    """Facts the checks cite, read from the workbook by code.

    Deliberately code rather than a model: a stated hash total that a model transcribed is a
    number nobody checked, and the entire point of citing it is that somebody did. The value
    sits a cell or two to the right of its label, which is how these workbooks are laid out.
    """
    facts: dict[str, object] = {}
    for sheet in data.sheets:
        for (row, col), cell in sheet.cells.items():
            if not isinstance(cell.value, str):
                continue
            fact = _FACT_LABELS.get(cell.value.strip().rstrip(":").casefold())
            if fact is None or fact in facts:
                continue
            for offset in (1, 2, 3):
                candidate = sheet.cell(row, col + offset).value
                if isinstance(candidate, Decimal):
                    facts[fact] = candidate
                    break
    return facts


def _headline_for(run: ReviewRun) -> str:
    """The stored headline. Kept with the record so a re-read says what the run said."""
    if run.score is None:
        return "No score: no check was run."
    return (
        f"{run.score.total} out of 100, {run.score.band}"
        f"{' (provisional)' if run.score.provisional else ''}"
    )


def _identification_notes(
    run: ReviewRun, recognised: Identification, je_spec: JETypeContext
) -> tuple[str, ...]:
    """What a reader must be told about how this entry type was settled.

    Only the declared case produces a note. A type the shape established needs no caveat,
    and a note on every review is a note nobody reads.
    """
    if run.je_type_source is not JETypeSource.DECLARED:
        return ()
    ruled_out = "; ".join(recognised.rejected[:MAX_LISTED_REJECTIONS])
    return (
        f"The workbook's shape matched no entry type, so these checks were run against "
        f"{je_spec.name} because that is what the upload declared. Nothing here confirms "
        f"the entry is of that type, and the review stays with a person for that reason."
        + (f" Ruled out by shape: {ruled_out}." if ruled_out else ""),
    )


def _identification_steps(run: ReviewRun, je_spec: JETypeContext) -> tuple[str, ...]:
    """The instruction that follows from how the type was settled, not from what was found.

    A model given only the outcomes cannot know the entry type was taken on trust, so this
    is code's to insist on rather than AI-5's to remember.
    """
    if run.je_type_source is not JETypeSource.DECLARED:
        return ()
    return (
        f"Confirm this entry really is {je_spec.name} before relying on anything below: "
        f"the workbook's shape did not establish it. If it is a different entry type, "
        f"upload it again naming that one.",
    )


def _plan_notes(checked: "CheckedPlan") -> tuple[str, ...]:
    """What the plan check removed, written for the person reading the report.

    A dropped step means its check has no evidence at all, which the routing rules read as
    unproven. Saying which step was dropped and why turns that from a blank into something
    a reviewer can act on.
    """
    if not checked.dropped:
        return ()
    return tuple(f"{check_id} could not be checked: {why}." for check_id, why in checked.dropped)


def _cluster_notes(
    clusters: dict[str, tuple[ExceptionCluster, ...]],
    pack: dict[str, tuple[Figure, ...]],
) -> tuple[str, ...]:
    """Say so when the causes do not account for every exception.

    Groups that do not add up are still worth reading. A reader just has to know they are
    not the whole picture, and finding that out for themselves by adding up the counts is
    not a reasonable thing to ask.
    """
    return tuple(
        f"The causes listed for {check_id} account for "
        f"{sum(item.affected for item in found):,} of "
        f"{sum(figure.exception_count for figure in pack.get(check_id, ())):,} exceptions."
        for check_id, found in sorted(clusters.items())
        if not reconciles(found, pack.get(check_id, ()))
    )
