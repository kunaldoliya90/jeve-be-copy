"""Step 2. Refuse anything that is not a safe, readable Excel workbook, before parsing it.

Every check here runs on bytes already in hand and costs no parse. The order matters: size
first because it is free, then content, then the zip guards, and only then does anything
open the package.
"""

import logging
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath

from api.consts import ErrorCode
from api.exceptions.review_exceptions import UploadRejectedError
from review.consts import (
    ACCEPTED_FORMATS,
    MAX_COMPRESSION_RATIO,
    MAX_UNCOMPRESSED_BYTES,
    MAX_ZIP_MEMBERS,
    REQUIRED_ZIP_MEMBERS,
    ZIP_MAGIC,
    WorkbookFormat,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class UploadOk:
    """A file that may be parsed. ``workbook_format`` decides which reader runs at step 4."""

    file_name: str
    size_bytes: int
    workbook_format: WorkbookFormat


def validate_upload(file_name: str, content: bytes, *, max_bytes: int) -> UploadOk:
    """Accept the upload or raise ``UploadRejectedError`` with the code the API returns.

    ``max_bytes`` comes from ``config.MAX_UPLOAD_BYTES``; it is passed in so the gatekeeper
    stays a pure function and tests need no settings.
    """
    workbook_format = _format_of(file_name)
    _check_size(file_name, len(content), max_bytes)
    _check_is_zip(file_name, content)
    _check_zip_safety(file_name, content)
    if workbook_format is not WorkbookFormat.XLSB:
        _check_ooxml_members(file_name, content)

    logger.info(
        "upload accepted file=%s bytes=%d format=%s", file_name, len(content), workbook_format
    )
    return UploadOk(file_name=file_name, size_bytes=len(content), workbook_format=workbook_format)


def _format_of(file_name: str) -> WorkbookFormat:
    """The suffix decides *which reader* to use; it never decides that a file is a workbook."""
    suffix = PurePosixPath(file_name).suffix.lower()
    try:
        workbook_format = WorkbookFormat(suffix)
    except ValueError:
        raise UploadRejectedError(
            f"{suffix or 'A file with no extension'} is not a supported workbook format",
            ErrorCode.NOT_EXCEL,
            {"accepted": ", ".join(sorted(fmt.value for fmt in ACCEPTED_FORMATS))},
        ) from None
    return workbook_format


def _check_size(file_name: str, size: int, max_bytes: int) -> None:
    if size == 0:
        raise UploadRejectedError(f"{file_name} is empty", ErrorCode.NOT_EXCEL)
    if size > max_bytes:
        raise UploadRejectedError(
            f"{file_name} is larger than the {max_bytes // (1024 * 1024)} MB limit",
            ErrorCode.TOO_LARGE,
            {"size_bytes": str(size), "limit_bytes": str(max_bytes)},
        )


def _check_is_zip(file_name: str, content: bytes) -> None:
    """Both accepted formats are zip packages. The first bytes are the only honest signal."""
    if not content.startswith(ZIP_MAGIC):
        raise UploadRejectedError(
            f"{file_name} is not an Excel workbook: its content is not a zip package",
            ErrorCode.NOT_EXCEL,
        )


def _check_zip_safety(file_name: str, content: bytes) -> None:
    """Guard the archive before anything reads a member out of it."""
    try:
        with zipfile.ZipFile(BytesIO(content)) as package:
            infos = package.infolist()
    except zipfile.BadZipFile as exc:
        raise UploadRejectedError(
            f"{file_name} could not be opened as a zip package",
            ErrorCode.CORRUPT_WORKBOOK,
            {"detail": str(exc)},
        ) from exc

    if len(infos) > MAX_ZIP_MEMBERS:
        raise UploadRejectedError(
            f"{file_name} holds {len(infos)} parts, more than a workbook should",
            ErrorCode.CORRUPT_WORKBOOK,
            {"parts": str(len(infos)), "limit": str(MAX_ZIP_MEMBERS)},
        )

    uncompressed = sum(info.file_size for info in infos)
    if uncompressed > MAX_UNCOMPRESSED_BYTES:
        raise UploadRejectedError(
            f"{file_name} expands to more than the uncompressed limit",
            ErrorCode.CORRUPT_WORKBOOK,
            {"uncompressed_bytes": str(uncompressed)},
        )

    compressed = sum(info.compress_size for info in infos) or 1
    if uncompressed // compressed > MAX_COMPRESSION_RATIO:
        raise UploadRejectedError(
            f"{file_name} is compressed far beyond what a workbook is",
            ErrorCode.CORRUPT_WORKBOOK,
            {"ratio": str(uncompressed // compressed)},
        )

    for info in infos:
        if _escapes_archive(info.filename):
            raise UploadRejectedError(
                f"{file_name} holds a part with a path that escapes the archive",
                ErrorCode.CORRUPT_WORKBOOK,
                {"member": info.filename},
            )


def _escapes_archive(member: str) -> bool:
    """Absolute paths and ``..`` segments. Nothing is extracted, but nothing is trusted."""
    path = PurePosixPath(member.replace("\\", "/"))
    return path.is_absolute() or ".." in path.parts


def _check_ooxml_members(file_name: str, content: bytes) -> None:
    """A zip is not a workbook until it holds the parts every OOXML workbook has."""
    with zipfile.ZipFile(BytesIO(content)) as package:
        names = set(package.namelist())
    missing = [member for member in REQUIRED_ZIP_MEMBERS if member not in names]
    if missing:
        raise UploadRejectedError(
            f"{file_name} is a zip package but not an Excel workbook",
            ErrorCode.NOT_EXCEL,
            {"missing": ", ".join(missing)},
        )
