"""Reading the screenshots out of a workbook package, and refusing the parts that are traps.

The extractor walks XML out of a file a stranger uploaded, so half of this file is about
what it refuses: an oversized part, and any part carrying a document type or entity
declaration. Those declarations are what make the expansion attacks possible, and no real
spreadsheet part has one.
"""

import hashlib
import zipfile
from io import BytesIO

from review.extract.pictures import MAX_XML_BYTES, extract_pictures

PNG = (
    b"\x89PNG\r\n\x1a\n"
    + b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    + b"\x00" * 5_000
)

WORKBOOK = """<?xml version="1.0"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
          xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="Methodology" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""

WORKBOOK_RELS = """<?xml version="1.0"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Target="worksheets/sheet1.xml"
   Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>
</Relationships>"""

SHEET_RELS = """<?xml version="1.0"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Target="../drawings/drawing1.xml"
   Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/drawing"/>
</Relationships>"""

DRAWING = """<?xml version="1.0"?>
<wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
      xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
      xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <xdr:twoCellAnchor>
    <xdr:from><xdr:col>1</xdr:col><xdr:row>16</xdr:row></xdr:from>
    <xdr:pic><xdr:blipFill><a:blip r:embed="rId1"/></xdr:blipFill></xdr:pic>
  </xdr:twoCellAnchor>
</wsDr>"""

DRAWING_RELS = """<?xml version="1.0"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Target="../media/image1.png"
   Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"/>
</Relationships>"""


def _package(**overrides: bytes | str) -> bytes:
    """A minimal .xlsx carrying one picture anchored to one sheet."""
    parts: dict[str, bytes | str] = {
        "xl/workbook.xml": WORKBOOK,
        "xl/_rels/workbook.xml.rels": WORKBOOK_RELS,
        "xl/worksheets/sheet1.xml": "<worksheet/>",
        "xl/worksheets/_rels/sheet1.xml.rels": SHEET_RELS,
        "xl/drawings/drawing1.xml": DRAWING,
        "xl/drawings/_rels/drawing1.xml.rels": DRAWING_RELS,
        "xl/media/image1.png": PNG,
    }
    parts.update(overrides)

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as package:
        for name, payload in parts.items():
            package.writestr(name, payload)
    return buffer.getvalue()


# --- the happy path -----------------------------------------------------------------------


def test_a_pasted_picture_is_found_with_its_sheet_and_anchor() -> None:
    pictures = extract_pictures(_package())

    assert len(pictures) == 1
    picture = pictures[0]
    assert picture.picture_id == "P1"
    assert picture.sheet == "Methodology"
    # The drawing format counts from zero, a cell reference from one: col 1, row 16 is B17.
    assert picture.anchor_cell == "B17"
    assert picture.content_type == "image/png"
    assert picture.sha256 == hashlib.sha256(PNG).hexdigest()
    assert picture.data == PNG


def test_the_digest_is_what_keys_the_cache_so_identical_bytes_match() -> None:
    first = extract_pictures(_package())[0]
    second = extract_pictures(_package())[0]

    assert first.sha256 == second.sha256


# --- degrading rather than failing --------------------------------------------------------


def test_a_workbook_with_no_drawings_yields_no_pictures() -> None:
    package = _package()
    kept = {
        name: payload
        for name, payload in _parts(package).items()
        if "drawing" not in name and "media" not in name
    }

    assert extract_pictures(_rezip(kept)) == ()


def test_a_file_that_is_not_a_package_yields_no_pictures_rather_than_raising() -> None:
    """An absent screenshot is missing evidence. A crash is a failed review."""
    assert extract_pictures(b"not a zip at all") == ()


def test_a_malformed_drawing_part_yields_no_pictures() -> None:
    assert extract_pictures(_package(**{"xl/drawings/drawing1.xml": "<not closed"})) == ()


def test_a_non_image_target_is_skipped() -> None:
    rels = DRAWING_RELS.replace("image1.png", "notes.txt")
    assert extract_pictures(_package(**{"xl/drawings/_rels/drawing1.xml.rels": rels})) == ()


def test_an_icon_sized_image_is_still_extracted_here() -> None:
    """Size is AI-R's filter, not the extractor's: the profile reports every picture."""
    pictures = extract_pictures(_package(**{"xl/media/image1.png": b"\x89PNG\r\n\x1a\n" + b"x"}))

    assert len(pictures) == 1


# --- refusing the dangerous parts ---------------------------------------------------------


def test_a_part_declaring_a_document_type_is_refused() -> None:
    """The billion-laughs expansion needs a declaration, so the declaration is refused."""
    poisoned = (
        '<?xml version="1.0"?><!DOCTYPE wsDr [<!ENTITY a "aaaaaaaaaa">]>'
        + DRAWING.split("?>", 1)[1]
    )

    assert extract_pictures(_package(**{"xl/drawings/drawing1.xml": poisoned})) == ()


def test_a_part_declaring_an_entity_is_refused() -> None:
    poisoned = '<?xml version="1.0"?><!ENTITY x "y">' + DRAWING.split("?>", 1)[1]

    assert extract_pictures(_package(**{"xl/drawings/drawing1.xml": poisoned})) == ()


def test_an_oversized_xml_part_is_refused_before_it_is_parsed() -> None:
    enormous = "<wsDr>" + "<!-- padding -->" * (MAX_XML_BYTES // 16 + 1) + "</wsDr>"

    assert extract_pictures(_package(**{"xl/drawings/drawing1.xml": enormous})) == ()


def test_the_declaration_check_does_not_reject_a_normal_part() -> None:
    """The guard must not be so broad that it refuses every real workbook."""
    assert len(extract_pictures(_package())) == 1


# --- helpers ------------------------------------------------------------------------------


def _parts(package: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(BytesIO(package)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _rezip(parts: dict[str, bytes]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as package:
        for name, payload in parts.items():
            package.writestr(name, payload)
    return buffer.getvalue()
