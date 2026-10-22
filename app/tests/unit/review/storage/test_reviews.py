"""The review store's delete, and what it has to take with it.

A record is written once and never updated, and ``delete`` is not a loophole in that:
removing a review is a different act from rewriting one. What these tests pin is that it
removes *everything* — a review left in any one of the three places it lives comes back.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from review.models.result import AuditTrail, ReviewResult, Score
from review.storage.reviews import ReviewStore

DIGEST = "a" * 64


@pytest.fixture
def store(tmp_path: Path) -> ReviewStore:
    created = ReviewStore(tmp_path)
    created.initialise()
    return created


def _result(review_id: str) -> ReviewResult:
    return ReviewResult(
        review_id=review_id,
        status="needs_review",
        je_type="vacation-accrual-france",
        audit=AuditTrail(file_sha256=DIGEST, file_name="je.xlsx"),
        created_at=datetime.now(UTC),
    )


def test_delete_removes_the_record(store: ReviewStore) -> None:
    store.write(_result("rev_1"), file_name="je.xlsx", reuse_key=None)

    assert store.delete("rev_1") is True
    assert store.read("rev_1") is None


def test_delete_removes_the_listing_row(store: ReviewStore) -> None:
    """A row whose record is gone points at a 404, which is the one state a reviewer
    cannot act on."""
    store.write(_result("rev_1"), file_name="je.xlsx", reuse_key=None)
    store.write(_result("rev_2"), file_name="other.xlsx", reuse_key=None)

    store.delete("rev_1")
    rows, _ = store.list()

    assert [row["review_id"] for row in rows] == ["rev_2"]


def test_delete_purges_the_reuse_entry(store: ReviewStore) -> None:
    """Otherwise the next identical upload is answered from a review that no longer
    exists."""
    store.write(_result("rev_1"), file_name="je.xlsx", reuse_key="key-1")
    assert store.find_by_reuse_key("key-1") is not None

    store.delete("rev_1")

    assert store.find_by_reuse_key("key-1") is None


def test_delete_leaves_other_reviews_alone(store: ReviewStore) -> None:
    store.write(_result("rev_1"), file_name="je.xlsx", reuse_key="key-1")
    store.write(_result("rev_2"), file_name="other.xlsx", reuse_key="key-2")

    store.delete("rev_1")

    assert store.read("rev_2") is not None
    assert store.find_by_reuse_key("key-2") is not None


def test_delete_reports_nothing_removed_for_an_unknown_id(store: ReviewStore) -> None:
    assert store.delete("rev_nope") is False


def test_delete_is_safe_to_repeat(store: ReviewStore) -> None:
    store.write(_result("rev_1"), file_name="je.xlsx", reuse_key="key-1")

    assert store.delete("rev_1") is True
    assert store.delete("rev_1") is False


def test_a_deleted_id_can_be_written_again(store: ReviewStore) -> None:
    """``write`` refuses to overwrite, and the delete has to clear that refusal too —
    otherwise a deleted review's id is poisoned for good."""
    store.write(_result("rev_1"), file_name="je.xlsx", reuse_key=None)
    store.delete("rev_1")

    store.write(_result("rev_1"), file_name="again.xlsx", reuse_key=None)

    assert store.read("rev_1") is not None


def test_write_still_refuses_to_overwrite(store: ReviewStore) -> None:
    """The immutability delete sits next to, unchanged."""
    store.write(_result("rev_1"), file_name="je.xlsx", reuse_key=None)

    with pytest.raises(FileExistsError):
        store.write(_result("rev_1"), file_name="je.xlsx", reuse_key=None)


def test_a_record_stored_before_the_risk_rating_existed_still_reads() -> None:
    """Records are write-once, so an older one can never be given the new field.

    A required `risk_rating` would therefore not make old reviews incomplete, it would
    make them unreadable - every stored review from before the change would 404 or 500
    rather than report what it found. The field is optional for this reason alone.
    """
    stored = {"total": 62.5, "band": "Medium risk", "areas": [], "provisional": True}

    score = Score.model_validate(stored)

    assert score.total == 62.5
    assert score.risk_rating is None
