"""Session-scoped parses of the real sample workbooks.

Parsing these costs seconds, not milliseconds, so each one is read once for the whole
session. They live in ``skills/docs/`` rather than being copied into the test tree: they are
the actual files the harness was designed against, and a second copy would drift.
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from review.consts import WorkbookFormat
from review.extract.excel_reader import read_workbook
from review.extract.profiler import profile
from review.models.workbook import WorkbookProfile
from review.models.workbook_data import WorkbookData

WORKBOOK_DIR = Path(__file__).resolve().parents[3] / "skills" / "docs"

SAMPLES: dict[str, WorkbookFormat] = {
    "236264.xlsx": WorkbookFormat.XLSX,
    "305800.xlsx": WorkbookFormat.XLSX,
    "935348.xlsb": WorkbookFormat.XLSB,
}


def _require(name: str) -> Path:
    path = WORKBOOK_DIR / name
    if not path.is_file():
        pytest.skip(f"Sample workbook {name} is not present at {WORKBOOK_DIR}")
    return path


@pytest.fixture(scope="session")
def sample_bytes() -> Callable[[str], bytes]:
    cache: dict[str, bytes] = {}

    def load(name: str) -> bytes:
        if name not in cache:
            cache[name] = _require(name).read_bytes()
        return cache[name]

    return load


@pytest.fixture(scope="session")
def sample_data(sample_bytes: Callable[[str], bytes]) -> Callable[[str], WorkbookData]:
    cache: dict[str, WorkbookData] = {}

    def load(name: str) -> WorkbookData:
        if name not in cache:
            cache[name] = read_workbook(sample_bytes(name), SAMPLES[name])
        return cache[name]

    return load


@pytest.fixture(scope="session")
def sample_profile(sample_data: Callable[[str], WorkbookData]) -> Callable[[str], WorkbookProfile]:
    cache: dict[str, WorkbookProfile] = {}

    def load(name: str) -> WorkbookProfile:
        if name not in cache:
            cache[name] = profile(sample_data(name))
        return cache[name]

    return load
