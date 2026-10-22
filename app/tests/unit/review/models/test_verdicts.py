"""Status strictness: AI-4 may only agree or go stricter, and the stricter side wins."""

import pytest

from review.models import Status, stricter, strictness

ORDER = [
    Status.PASS,
    Status.PARTIAL,
    Status.UNABLE_TO_VALIDATE,
    Status.MISSING_EVIDENCE,
    Status.FAIL,
]


def test_strictness_runs_pass_to_fail() -> None:
    ranks = [strictness(status) for status in ORDER]
    assert ranks == sorted(ranks)
    assert len(set(ranks)) == len(ORDER)


@pytest.mark.parametrize("status", ORDER)
def test_stricter_is_reflexive(status: Status) -> None:
    assert stricter(status, status) is status


def test_stricter_prefers_the_harsher_status() -> None:
    assert stricter(Status.PASS, Status.FAIL) is Status.FAIL
    assert stricter(Status.FAIL, Status.PASS) is Status.FAIL
    assert stricter(Status.PARTIAL, Status.UNABLE_TO_VALIDATE) is Status.UNABLE_TO_VALIDATE


def test_unable_to_validate_is_stricter_than_partial() -> None:
    """Half credit either way, but unproven must not read as partly proven."""
    assert strictness(Status.UNABLE_TO_VALIDATE) > strictness(Status.PARTIAL)


def test_a_challenge_may_not_loosen_a_verdict() -> None:
    """The rule step 16 applies: a looser status_should_be is discarded, not honoured."""
    judge, challenge = Status.FAIL, Status.PASS
    assert stricter(judge, challenge) is judge
