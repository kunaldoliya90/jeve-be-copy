"""Paging, filtering, sorting and grouping over a plain list of rows - no engine, no file."""

from decimal import Decimal

from review.models.recalculation import DiscrepancyKind, RecalcRow
from review.recalc.rows_query import (
    RelatedCheck,
    filter_rows,
    group_rows,
    merge_related_flags,
    paginate,
    sort_rows,
)


def row(
    number: int,
    *,
    kind: DiscrepancyKind | None = None,
    recomputed: str | None = "100.00",
    stated: str | None = "100.00",
    difference: str | None = "0.00",
    cost_center: str | None = None,
) -> RecalcRow:
    return RecalcRow(
        row=number,
        group={"cost_center": cost_center} if cost_center else {},
        recomputed=Decimal(recomputed) if recomputed else None,
        stated=Decimal(stated) if stated else None,
        difference=Decimal(difference) if difference else None,
        kind=kind,
    )


# --------------------------------------------------------------------------------- filter


def test_all_returns_every_row() -> None:
    rows = [row(1), row(2, kind=DiscrepancyKind.STATED_DIFFERS)]
    assert filter_rows(rows, status="all") == rows
    assert filter_rows(rows, status=None) == rows


def test_changed_is_every_row_carrying_a_finding() -> None:
    rows = [
        row(1),
        row(2, kind=DiscrepancyKind.STATED_DIFFERS),
        row(3, kind=DiscrepancyKind.INPUT_MISSING),
    ]
    assert [r.row for r in filter_rows(rows, status="changed")] == [2, 3]


def test_agrees_is_every_row_with_no_finding() -> None:
    rows = [row(1), row(2, kind=DiscrepancyKind.STATED_DIFFERS)]
    assert [r.row for r in filter_rows(rows, status="agrees")] == [1]


def test_a_specific_kind_narrows_further() -> None:
    rows = [row(1, kind=DiscrepancyKind.STATED_DIFFERS), row(2, kind=DiscrepancyKind.INPUT_MISSING)]
    assert [r.row for r in filter_rows(rows, status="input_missing")] == [2]


def test_an_unknown_status_matches_nothing_rather_than_raising() -> None:
    assert filter_rows([row(1)], status="not_a_real_status") == []


# ----------------------------------------------------------------------------------- sort


def test_sort_by_difference_descending_is_worst_first() -> None:
    rows = [row(1, difference="1.00"), row(2, difference="50.00"), row(3, difference="-10.00")]
    assert [r.row for r in sort_rows(rows, sort="-difference")] == [2, 3, 1]


def test_sort_ascending_drops_the_leading_minus() -> None:
    rows = [row(1, difference="50.00"), row(2, difference="1.00")]
    assert [r.row for r in sort_rows(rows, sort="difference")] == [2, 1]


def test_rows_with_nothing_to_rank_by_sort_last_either_direction() -> None:
    rows = [row(1, difference="5.00"), row(2, difference=None)]
    assert sort_rows(rows, sort="-difference")[-1].row == 2
    assert sort_rows(rows, sort="difference")[-1].row == 2


def test_no_sort_requested_leaves_the_order_alone() -> None:
    rows = [row(2), row(1)]
    assert sort_rows(rows, sort=None) == rows


def test_an_unknown_sort_field_is_ignored_rather_than_raising() -> None:
    rows = [row(2), row(1)]
    assert sort_rows(rows, sort="-not_a_field") == rows


# ------------------------------------------------------------------------------- paginate


def test_paginate_returns_a_cursor_for_the_next_page() -> None:
    rows = [row(index) for index in range(1, 11)]
    page = paginate(rows, limit=4, cursor=None)
    assert [r.row for r in page.rows] == [1, 2, 3, 4]
    assert page.total == 10
    assert page.next_cursor == "4"


def test_paginate_the_cursor_resumes_where_the_last_page_stopped() -> None:
    rows = [row(index) for index in range(1, 11)]
    page = paginate(rows, limit=4, cursor="4")
    assert [r.row for r in page.rows] == [5, 6, 7, 8]


def test_the_last_page_carries_no_cursor() -> None:
    rows = [row(index) for index in range(1, 5)]
    page = paginate(rows, limit=4, cursor=None)
    assert page.next_cursor is None


def test_a_malformed_cursor_is_read_as_the_start() -> None:
    rows = [row(index) for index in range(1, 5)]
    page = paginate(rows, limit=2, cursor="not-a-number")
    assert [r.row for r in page.rows] == [1, 2]


# ----------------------------------------------------------------------------------- group


def test_group_rolls_up_by_the_dimension_requested() -> None:
    rows = [
        row(1, cost_center="CC100", recomputed="100.00", stated="90.00"),
        row(2, cost_center="CC100", recomputed="50.00", stated="50.00"),
        row(
            3,
            cost_center="CC200",
            recomputed="10.00",
            stated="10.00",
            kind=DiscrepancyKind.STATED_DIFFERS,
        ),
    ]

    summaries = {summary.value: summary for summary in group_rows(rows, dimension="cost_center")}

    assert summaries["CC100"].rows == 2
    assert summaries["CC100"].recomputed_total == Decimal("150.00")
    assert summaries["CC100"].stated_total == Decimal("140.00")
    assert summaries["CC100"].difference == Decimal("10.00")
    assert summaries["CC100"].flagged == 0
    assert summaries["CC200"].flagged == 1


def test_a_row_with_no_value_for_the_dimension_groups_under_the_empty_string() -> None:
    rows = [row(1, cost_center="CC100"), row(2)]
    summaries = {summary.value: summary for summary in group_rows(rows, dimension="cost_center")}
    assert summaries[""].rows == 1


# ---------------------------------------------------------------------- merge_related_flags


def test_merge_related_flags_annotates_the_matching_row_by_number() -> None:
    rows = [row(1), row(2)]
    related = [
        RelatedCheck(
            check_id="cp_provision_rebuilds",
            label="Does the stated CP provision equal the prior plus current period?",
            samples=(
                {
                    "row": 2,
                    "cell": "M2",
                    "expected": Decimal("10"),
                    "actual": Decimal("20"),
                    "difference": Decimal("-10"),
                },
            ),
        )
    ]

    merged = merge_related_flags(rows, related)

    assert merged[0].flags == ()
    assert len(merged[1].flags) == 1
    flag = merged[1].flags[0]
    assert flag.check_id == "cp_provision_rebuilds"
    assert flag.cell == "M2"
    assert flag.expected == Decimal("10")
    assert flag.actual == Decimal("20")
    assert flag.difference == Decimal("-10")


def test_merge_related_flags_with_nothing_related_leaves_rows_untouched() -> None:
    rows = [row(1), row(2)]
    assert merge_related_flags(rows, []) == rows


def test_merge_related_flags_combines_more_than_one_check_on_the_same_row() -> None:
    rows = [row(1)]
    related = [
        RelatedCheck("check_a", "Check A", ({"row": 1, "cell": "A1"},)),
        RelatedCheck("check_b", "Check B", ({"row": 1, "cell": "B1"},)),
    ]

    merged = merge_related_flags(rows, related)

    assert {flag.check_id for flag in merged[0].flags} == {"check_a", "check_b"}


def test_merge_related_flags_ignores_a_sample_with_no_row_number() -> None:
    rows = [row(1)]
    related = [RelatedCheck("check_a", "Check A", ({"cell": "A1"},))]

    assert merge_related_flags(rows, related)[0].flags == ()


def test_a_row_flagged_by_another_check_counts_as_changed() -> None:
    rows = [row(1), row(2)]
    related = [RelatedCheck("check_a", "Check A", ({"row": 2, "cell": "A2"},))]
    merged = merge_related_flags(rows, related)

    assert [r.row for r in filter_rows(merged, status="changed")] == [2]
    assert [r.row for r in filter_rows(merged, status="agrees")] == [1]


def test_a_row_flagged_by_another_check_counts_toward_a_group_s_flagged_total() -> None:
    rows = [row(1, cost_center="CC100"), row(2, cost_center="CC100")]
    related = [RelatedCheck("check_a", "Check A", ({"row": 2, "cell": "A2"},))]
    merged = merge_related_flags(rows, related)

    summaries = {s.value: s for s in group_rows(merged, dimension="cost_center")}
    assert summaries["CC100"].flagged == 1
