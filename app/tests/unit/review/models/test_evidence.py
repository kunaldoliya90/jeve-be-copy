"""Gate rule 1 as the models enforce it: row-level agreement, or it is not a pass."""

from decimal import Decimal

import pytest

from review.models import Figure, RowCoverage


def _figure(**overrides: object) -> Figure:
    defaults: dict[str, object] = {
        "figure_id": "F-07",
        "check_id": "entry_balances",
        "tool": "balance",
        "ok": True,
    }
    return Figure(**{**defaults, **overrides})


def test_row_coverage_all_rows_tied_is_complete() -> None:
    coverage = RowCoverage(rows_total=2280, rows_bound=2280, rows_tied=2280)
    assert coverage.complete is True
    assert coverage.tie_rate == 1.0


@pytest.mark.parametrize(
    ("rows_total", "rows_bound", "rows_tied"),
    [
        (2280, 2280, 2279),  # one row did not tie
        (2280, 2279, 2279),  # one row never bound
        (0, 0, 0),  # nothing to tie: an empty table proves nothing
    ],
)
def test_row_coverage_short_of_every_row_is_not_complete(
    rows_total: int, rows_bound: int, rows_tied: int
) -> None:
    coverage = RowCoverage(rows_total=rows_total, rows_bound=rows_bound, rows_tied=rows_tied)
    assert coverage.complete is False


def test_figure_without_coverage_supports_pass_when_ok() -> None:
    assert _figure(coverage=None).supports_pass is True


def test_figure_with_incomplete_coverage_does_not_support_pass() -> None:
    """The agreeing-total trap: ok is True and the rows still disagree."""
    figure = _figure(coverage=RowCoverage(rows_total=2280, rows_bound=2280, rows_tied=1874))
    assert figure.ok is True
    assert figure.supports_pass is False


def test_figure_that_could_not_compute_supports_nothing() -> None:
    assert _figure(ok=None).supports_pass is False
    assert _figure(ok=False).supports_pass is False


def test_figure_id_must_match_the_citation_pattern() -> None:
    """Verdicts cite figure ids, so a malformed id would break the trace at step 16."""
    with pytest.raises(ValueError, match="figure_id"):
        _figure(figure_id="07")


def test_figure_carries_financial_impact_for_ranking() -> None:
    figure = _figure(exception_count=312, financial_impact=Decimal("88780.65"))
    assert figure.financial_impact == Decimal("88780.65")
