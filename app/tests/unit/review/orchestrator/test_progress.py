"""The progress weighting and the challenge counter behind the SSE stream.

Both exist only to be read by a person watching a review run, which is exactly why they
need pinning: nothing downstream breaks when a progress bar lies, so without a test the
lie survives.
"""

from review.models.verdicts import Challenge, Status, Verdict
from review.orchestrator.orchestrator import _STEP_ORDER, _STEP_WEIGHT, _tightened


def test_step_weights_never_go_backwards() -> None:
    """A bar that slides backwards reads as a failure, not as progress.

    Steps 5 and 6 once sat at 62 and 70, above step 7a at 60, so every review visibly lost
    ten points on recognising its own layout.
    """
    weights = [_STEP_WEIGHT[step] for step in _STEP_ORDER]

    assert weights == sorted(weights), dict(zip(_STEP_ORDER, weights, strict=True))


def test_every_ordered_step_has_a_weight() -> None:
    """A step with no weight leaves the bar frozen while it runs."""
    assert set(_STEP_ORDER) == set(_STEP_WEIGHT)


def test_the_last_step_completes_the_bar() -> None:
    assert _STEP_WEIGHT[_STEP_ORDER[-1]] == 100


def _verdict(check_id: str, status: Status) -> Verdict:
    return Verdict(check_id=check_id, status=status)


def _challenge(check_id: str, should_be: Status | None) -> Challenge:
    return Challenge(check_id=check_id, agrees=should_be is None, status_should_be=should_be)


def test_tightened_counts_only_stricter_conclusions() -> None:
    verdicts = {"a": _verdict("a", Status.PASS), "b": _verdict("b", Status.PARTIAL)}
    challenges = {
        "a": _challenge("a", Status.FAIL),  # stricter: counted
        "b": _challenge("b", Status.PARTIAL),  # unchanged: not counted
    }

    assert _tightened(verdicts, challenges) == 1


def test_tightened_ignores_a_challenge_that_would_loosen() -> None:
    """A second opinion may only ever tighten. A looser one is discarded upstream, and
    must not be reported to the reader as a change either."""
    verdicts = {"a": _verdict("a", Status.FAIL)}
    challenges = {"a": _challenge("a", Status.PASS)}

    assert _tightened(verdicts, challenges) == 0


def test_tightened_ignores_a_challenge_with_no_matching_verdict() -> None:
    assert _tightened({}, {"a": _challenge("a", Status.FAIL)}) == 0


def test_tightened_is_zero_when_the_challenge_agreed() -> None:
    verdicts = {"a": _verdict("a", Status.PASS)}

    assert _tightened(verdicts, {"a": _challenge("a", None)}) == 0
