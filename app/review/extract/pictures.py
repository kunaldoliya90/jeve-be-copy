"""Step 4, the other half: the screenshots pasted into the workbook.

For a vacation accrual the evidence is often a picture — an SAP posting screen, a signed
approval, a payroll extract — pasted onto a tab. A review that cannot see those cannot say
whether the entry has its evidence, only whether the arithmetic holds.

They are read from the package rather than through openpyxl, for two reasons. A read-only
load does not expose drawings at all, and a full load of a twelve-megabyte workbook costs
more than the rest of step 4 put together. The parts are plain XML inside the zip, so this
walks them: sheet -> its drawing -> the anchors in it -> the image part each anchor names.

A workbook whose drawing parts cannot be parsed yields no pictures rather than failing the
review. An absent screenshot is reported as missing evidence, which is an honest outcome.

Every part here comes out of a file a stranger uploaded, so ``_parse`` guards each one before
``ElementTree`` sees it: a size cap, and a refusal of any part carrying a document type or
entity declaration. Those declarations are what make the billion-laughs and quadratic-blowup
expansions possible, and no legitimate OOXML part contains one — so refusing them closes the
hole without reaching for another dependency.
"""

import hashlib
import logging
import re
import zipfile
from io import BytesIO
from typing import Final
from xml.etree import ElementTree

from review.models.workbook_data import PictureData
from review.utils.sheet_utils import column_letter

logger = logging.getLogger(__name__)

MAX_PICTURES: Final = 40
MAX_PICTURE_BYTES: Final = 12 * 1024 * 1024
"""Per picture. A pasted screenshot is tens of kilobytes; anything this size is not one."""

IMAGE_SUFFIXES: Final = frozenset({".png", ".jpeg", ".jpg", ".gif", ".bmp", ".emf", ".wmf"})

_NS: Final = {
    "xdr": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
}
_EMBED: Final = f"{{{_NS['r']}}}embed"
_SHEET_PART: Final = re.compile(r"^xl/worksheets/(sheet\d+\.xml)$")

MAX_XML_BYTES: Final = 8 * 1024 * 1024
"""Per part. A drawing or relationship part is kilobytes; this is three orders of magnitude
of headroom and still refuses a part built to exhaust memory."""

_DECLARATIONS: Final = (b"<!doctype", b"<!entity")
"""Refused outright. Only an attacker puts either of these in a spreadsheet part."""


class _UnsafeXMLError(ValueError):
    """A part this reader will not parse. Caught with the other parse failures."""


def _parse(payload: bytes) -> ElementTree.Element:
    """Parse one package part, having first refused the shapes that make parsing unsafe."""
    if len(payload) > MAX_XML_BYTES:
        raise _UnsafeXMLError(f"a {len(payload)} byte XML part is not a drawing")

    opening = payload[:4096].lower()
    for declaration in _DECLARATIONS:
        if declaration in opening:
            raise _UnsafeXMLError(f"the part declares {declaration.decode()}, which is refused")

    # Safe at this point: external entity resolution is off by default in CPython's parser,
    # and the entity expansions that remain all require a declaration refused above.
    return ElementTree.fromstring(payload)  # noqa: S314


def extract_pictures(content: bytes) -> tuple[PictureData, ...]:
    """Every picture in the package, in sheet order then reading order."""
    try:
        with zipfile.ZipFile(BytesIO(content)) as package:
            names = set(package.namelist())
            sheet_titles = _sheet_titles(package, names)
            found: list[PictureData] = []

            for part, title in sheet_titles.items():
                for drawing in _drawings_for(package, names, part):
                    found.extend(_pictures_in(package, names, drawing, title))

            return tuple(
                PictureData(
                    picture_id=f"P{index}",
                    sheet=picture.sheet,
                    anchor_cell=picture.anchor_cell,
                    sha256=picture.sha256,
                    content_type=picture.content_type,
                    data=picture.data,
                )
                for index, picture in enumerate(found[:MAX_PICTURES], start=1)
            )
    except (zipfile.BadZipFile, ElementTree.ParseError, KeyError, ValueError) as exc:
        # Never fatal: a review without its screenshots still checks the arithmetic, and
        # reports the evidence as missing rather than as present.
        logger.info("no pictures could be read from the workbook: %s", exc)
        return ()


def _sheet_titles(package: zipfile.ZipFile, names: set[str]) -> dict[str, str]:
    """Worksheet part -> the tab name a reviewer would recognise.

    The part names in the package are ``sheet1.xml``; the titles live in the workbook part
    and are joined to the parts through the workbook's own relationships.
    """
    if "xl/workbook.xml" not in names or "xl/_rels/workbook.xml.rels" not in names:
        return {}

    rels = {
        node.get("Id", ""): node.get("Target", "")
        for node in _parse(package.read("xl/_rels/workbook.xml.rels"))
    }
    titles: dict[str, str] = {}
    for sheet in _parse(package.read("xl/workbook.xml")).iterfind("main:sheets/main:sheet", _NS):
        target = rels.get(sheet.get(_relationship_id(sheet), ""), "")
        part = f"xl/{target.lstrip('/').removeprefix('xl/')}"
        if part in names:
            titles[part] = sheet.get("name", "")
    return titles


def _relationship_id(sheet: ElementTree.Element) -> str:
    """The r:id attribute, whichever namespace prefix this writer used for it."""
    return next((key for key in sheet.attrib if key.endswith("}id")), "r:id")


def _drawings_for(package: zipfile.ZipFile, names: set[str], sheet_part: str) -> list[str]:
    """The drawing parts one worksheet references."""
    match = _SHEET_PART.match(sheet_part)
    rels_part = f"xl/worksheets/_rels/{match.group(1)}.rels" if match else ""
    if not rels_part or rels_part not in names:
        return []

    drawings: list[str] = []
    for node in _parse(package.read(rels_part)):
        target = node.get("Target", "")
        if "drawing" not in target:
            continue
        part = _resolve(target, base="xl/worksheets")
        if part in names:
            drawings.append(part)
    return drawings


def _pictures_in(
    package: zipfile.ZipFile, names: set[str], drawing_part: str, sheet_title: str
) -> list[PictureData]:
    """Every anchored image in one drawing part."""
    rels_name = drawing_part.replace("xl/drawings/", "xl/drawings/_rels/") + ".rels"
    if rels_name not in names:
        return []

    targets = {
        node.get("Id", ""): _resolve(node.get("Target", ""), base="xl/drawings")
        for node in _parse(package.read(rels_name))
    }

    found: list[PictureData] = []
    root = _parse(package.read(drawing_part))
    for anchor in root:
        embed = next(
            (node.get(_EMBED, "") for node in anchor.iter() if node.get(_EMBED)),
            "",
        )
        part = targets.get(embed, "")
        if not part or part not in names:
            continue
        if not any(part.lower().endswith(suffix) for suffix in IMAGE_SUFFIXES):
            continue

        data = package.read(part)
        if not data or len(data) > MAX_PICTURE_BYTES:
            logger.info("skipping a picture of %d bytes on %s", len(data), sheet_title)
            continue

        found.append(
            PictureData(
                picture_id="",  # assigned once the whole workbook is walked
                sheet=sheet_title,
                anchor_cell=_anchor_of(anchor),
                sha256=hashlib.sha256(data).hexdigest(),
                content_type=_content_type(part),
                data=data,
            )
        )
    return found


def _anchor_of(anchor: ElementTree.Element) -> str:
    """The top-left cell the drawing is pinned to, as a reviewer would quote it."""
    origin = anchor.find("xdr:from", _NS)
    if origin is None:
        return ""
    column = origin.findtext("xdr:col", default="0", namespaces=_NS)
    row = origin.findtext("xdr:row", default="0", namespaces=_NS)
    try:
        # The drawing format counts from zero; a cell reference counts from one.
        return f"{column_letter(int(column) + 1)}{int(row) + 1}"
    except ValueError:  # pragma: no cover - malformed drawing XML
        return ""


def _resolve(target: str, *, base: str) -> str:
    """A relationship target as a package part name."""
    cleaned = target.replace("\\", "/")
    if cleaned.startswith("/"):
        return cleaned.lstrip("/")
    while cleaned.startswith("../"):
        cleaned = cleaned[3:]
        base = base.rsplit("/", 1)[0] if "/" in base else ""
    return f"{base}/{cleaned}" if base else cleaned


def _content_type(part: str) -> str:
    suffix = part.lower().rsplit(".", 1)[-1]
    return {"jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(suffix, f"image/{suffix}")


__all__ = ["IMAGE_SUFFIXES", "MAX_PICTURES", "extract_pictures"]
