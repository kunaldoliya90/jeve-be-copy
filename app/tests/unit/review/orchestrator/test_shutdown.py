"""Step 20 must never fire after the app that would receive it is gone.

A review's background task is not tied to the request that started it, and nothing
previously cancelled it at shutdown — so a task still running when the process exits kept
running on the event loop regardless, with nowhere left to report to. In a test that showed
up as a write landing after the test's own temp directory was already torn down; in
production it is a review that finishes into a worker nobody is serving from any more.
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from review.orchestrator.orchestrator import ReviewOrchestrator

pytestmark = pytest.mark.anyio


def _orchestrator() -> ReviewOrchestrator:
    return ReviewOrchestrator(
        store=MagicMock(),
        context=MagicMock(),
        prompts=MagicMock(),
        provider=None,
        max_active=10,
        deadline_s=60,
        max_upload_bytes=10_000_000,
        tool_workers=1,
        structure_context_max_tokens=1_000,
        figure_samples=3,
    )


async def test_shutdown_cancels_a_task_still_running() -> None:
    orchestrator = _orchestrator()
    ran_to_completion = False

    async def _never_finishes() -> None:
        nonlocal ran_to_completion
        await asyncio.sleep(10)
        ran_to_completion = True  # pragma: no cover - only reached if cancellation failed

    task = asyncio.create_task(_never_finishes())
    orchestrator._tasks.add(task)

    await orchestrator.shutdown()

    assert task.cancelled()
    assert ran_to_completion is False


async def test_shutdown_cancels_every_task_still_running() -> None:
    orchestrator = _orchestrator()
    tasks = [asyncio.create_task(asyncio.sleep(10)) for _ in range(3)]
    orchestrator._tasks.update(tasks)

    await orchestrator.shutdown()

    assert all(task.cancelled() for task in tasks)


async def test_shutdown_is_safe_with_nothing_running() -> None:
    await _orchestrator().shutdown()


async def test_shutdown_waits_for_a_task_that_cleans_up_on_cancellation() -> None:
    """A task that catches the cancellation to clean up is awaited, not abandoned mid-way."""
    orchestrator = _orchestrator()
    cleaned_up = False

    async def _cleans_up_on_cancel() -> None:
        nonlocal cleaned_up
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cleaned_up = True
            raise

    task = asyncio.create_task(_cleans_up_on_cancel())
    orchestrator._tasks.add(task)
    await asyncio.sleep(0)  # let the task actually reach its `await` before cancelling it

    await orchestrator.shutdown()

    assert cleaned_up is True


async def test_shutdown_does_not_leave_a_task_running_past_return() -> None:
    """The guarantee that matters: by the time `shutdown` returns, nothing is still writing."""
    orchestrator = _orchestrator()
    task = asyncio.create_task(asyncio.sleep(10))
    orchestrator._tasks.add(task)

    await orchestrator.shutdown()

    assert task.done()
