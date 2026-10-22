"""Step 2: what the gatekeeper lets through, and the code it refuses each file with."""

import zipfile
from io import BytesIO

import pytest

from api.consts import ErrorCode
from api.exceptions.review_exceptions import UploadRejectedError
from review.consts import WorkbookFormat
from review.intake.gatekeeper import validate_upload

MAX_BYTES = 25 * 1024 * 1024


def _zip(members: dict[str, bytes]) -> bytes:
    """Deflated, like a real workbook: stored entries would give every archive a 1:1 ratio."""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for name, payload in members.items():
            package.writestr(name, payload)
    return buffer.getvalue()


def _workbook_zip() -> bytes:
    return _zip({"[Content_Types].xml": b"<Types/>", "xl/workbook.xml": b"<workbook/>"})


def test_accepts_a_real_workbook(workbook_bytes: bytes) -> None:
    result = validate_upload("accrual.xlsx", workbook_bytes, max_bytes=MAX_BYTES)
    assert result.workbook_format is WorkbookFormat.XLSX
    assert result.size_bytes == len(workbook_bytes)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("accrual.xlsx", WorkbookFormat.XLSX),
        ("accrual.xlsm", WorkbookFormat.XLSM),
        ("ACCRUAL.XLSX", WorkbookFormat.XLSX),
    ],
)
def test_accepted_suffixes(name: str, expected: WorkbookFormat) -> None:
    assert validate_upload(name, _workbook_zip(), max_bytes=MAX_BYTES).workbook_format is expected


def test_xlsb_is_accepted_without_the_ooxml_parts() -> None:
    """A .xlsb is a zip but not an OOXML package, so the member check must not run on it."""
    binary = _zip({"xl/workbook.bin": b"\x00"})
    assert validate_upload("accrual.xlsb", binary, max_bytes=MAX_BYTES).workbook_format is (
        WorkbookFormat.XLSB
    )


@pytest.mark.parametrize("name", ["accrual.xls", "accrual.csv", "accrual", "accrual.xlsx.exe"])
def test_unsupported_suffixes_are_not_excel(name: str) -> None:
    with pytest.raises(UploadRejectedError) as excinfo:
        validate_upload(name, _workbook_zip(), max_bytes=MAX_BYTES)
    assert excinfo.value.error_code is ErrorCode.NOT_EXCEL


def test_a_renamed_file_is_rejected_on_its_content() -> None:
    """The extension is never trusted: this is a PDF wearing an .xlsx name."""
    with pytest.raises(UploadRejectedError) as excinfo:
        validate_upload("accrual.xlsx", b"%PDF-1.7 not a workbook", max_bytes=MAX_BYTES)
    assert excinfo.value.error_code is ErrorCode.NOT_EXCEL


def test_a_zip_that_is_not_a_workbook_is_rejected() -> None:
    with pytest.raises(UploadRejectedError) as excinfo:
        validate_upload("accrual.xlsx", _zip({"notes.txt": b"hello"}), max_bytes=MAX_BYTES)
    assert excinfo.value.error_code is ErrorCode.NOT_EXCEL


def test_empty_file_is_rejected() -> None:
    with pytest.raises(UploadRejectedError) as excinfo:
        validate_upload("accrual.xlsx", b"", max_bytes=MAX_BYTES)
    assert excinfo.value.error_code is ErrorCode.NOT_EXCEL


def test_oversize_is_rejected_before_anything_is_parsed() -> None:
    with pytest.raises(UploadRejectedError) as excinfo:
        validate_upload("accrual.xlsx", _workbook_zip(), max_bytes=10)
    assert excinfo.value.error_code is ErrorCode.TOO_LARGE


def test_too_many_parts_is_corrupt() -> None:
    many = _zip({f"xl/part{index}.xml": b"<x/>" for index in range(2_001)})
    with pytest.raises(UploadRejectedError) as excinfo:
        validate_upload("accrual.xlsx", many, max_bytes=MAX_BYTES)
    assert excinfo.value.error_code is ErrorCode.CORRUPT_WORKBOOK


def test_a_zip_bomb_is_refused_on_its_ratio() -> None:
    bomb = _zip({"[Content_Types].xml": b"<Types/>", "xl/workbook.xml": b"0" * 20_000_000})
    with pytest.raises(UploadRejectedError) as excinfo:
        validate_upload("accrual.xlsx", bomb, max_bytes=MAX_BYTES)
    assert excinfo.value.error_code is ErrorCode.CORRUPT_WORKBOOK


def test_path_traversal_in_a_member_is_refused() -> None:
    escaping = _zip(
        {
            "[Content_Types].xml": b"<Types/>",
            "xl/workbook.xml": b"<workbook/>",
            "../../etc/passwd": b"root",
        }
    )
    with pytest.raises(UploadRejectedError) as excinfo:
        validate_upload("accrual.xlsx", escaping, max_bytes=MAX_BYTES)
    assert excinfo.value.error_code is ErrorCode.CORRUPT_WORKBOOK
