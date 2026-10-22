"""Column letters, header normalising and the token estimate."""

import pytest

from review.utils.sheet_utils import SheetUtils


@pytest.mark.parametrize(
    ("index", "letter"),
    [(1, "A"), (26, "Z"), (27, "AA"), (52, "AZ"), (53, "BA"), (702, "ZZ"), (703, "AAA")],
)
def test_column_letter_round_trips(index: int, letter: str) -> None:
    assert SheetUtils.column_letter(index) == letter
    assert SheetUtils.column_index(letter) == index


@pytest.mark.parametrize("index", [0, -1])
def test_column_letter_rejects_non_positive(index: int) -> None:
    """Spreadsheets are 1-based; a 0 here is a caller that forgot, not an empty column."""
    with pytest.raises(ValueError, match="1 or more"):
        SheetUtils.column_letter(index)


@pytest.mark.parametrize("letter", ["", "A1", "-"])
def test_column_index_rejects_non_alphabetic(letter: str) -> None:
    with pytest.raises(ValueError, match="alphabetic"):
        SheetUtils.column_index(letter)


def test_column_index_is_case_insensitive() -> None:
    assert SheetUtils.column_index("ab") == SheetUtils.column_index("AB")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Co Code", "co code"),
        ("  Co  Code  ", "co code"),
        ("Co_Code", "co code"),
        ("Co-Code:", "co code"),
        ("Acquis\nCP N-1", "acquis cp n 1"),
        ("Débit (EUR)", "d bit eur"),
        ("", ""),
        (None, ""),
        (12, "12"),
    ],
)
def test_normalize_header_folds_to_a_comparable_form(raw: object, expected: str) -> None:
    """Headers differ between periods in case, punctuation and line breaks, not in meaning."""
    assert SheetUtils.normalize_header(raw) == expected


def test_normalize_header_separates_distinct_labels() -> None:
    assert SheetUtils.normalize_header("Debit") != SheetUtils.normalize_header("Credit")


def test_estimate_tokens_scales_with_length() -> None:
    assert SheetUtils.estimate_tokens("") == 0
    assert SheetUtils.estimate_tokens("abcd") == 1
    assert SheetUtils.estimate_tokens("a" * 400) == 100


def test_estimate_tokens_never_reports_zero_for_content() -> None:
    """A non-empty context that estimated zero would skip the cap entirely."""
    assert SheetUtils.estimate_tokens("a") >= 1
