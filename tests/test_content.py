import base64
from dataclasses import replace
from pathlib import Path

import pytest

try:
    import fitz
except ModuleNotFoundError:  # pragma: no cover
    import pymupdf as fitz

from mcp.server.fastmcp.utilities.types import Image
from mcp.types import EmbeddedResource, ResourceLink

from paper_pilot.config import Settings
from paper_pilot.services import content
from paper_pilot.services.content import PdfTooLargeError


def _settings(data_dir: Path, **over) -> Settings:
    base = Settings(
        openalex_email=None,
        semantic_scholar_api_key=None,
        zotero_library_id=None,
        zotero_library_type="user",
        zotero_api_key=None,
        data_dir=data_dir,
        libgen_mirrors=("https://libgen.is",),
        libgen_timeout_sec=10.0,
    )
    return replace(base, **over) if over else base


def _make_pdf(path: Path, pages: int = 2) -> None:
    document = fitz.open()
    for i in range(pages):
        document.new_page().insert_text((72, 72), f"page {i + 1} content figure table")
    document.save(path)
    document.close()


def test_to_pdf_embedded_resource_decodes_to_pdf(tmp_path: Path) -> None:
    pdf = tmp_path / "a.pdf"
    _make_pdf(pdf)
    er = content.to_pdf_embedded_resource(pdf, doc_id=content.stable_doc_id(pdf))
    assert isinstance(er, EmbeddedResource)
    assert er.type == "resource"
    assert er.resource.mimeType == "application/pdf"
    assert base64.b64decode(er.resource.blob).startswith(b"%PDF")
    assert str(er.resource.uri).startswith("paperpilot://pdf/")


def test_pdf_embed_size_guard_raises(tmp_path: Path) -> None:
    pdf = tmp_path / "a.pdf"
    _make_pdf(pdf)
    with pytest.raises(PdfTooLargeError):
        content.to_pdf_embedded_resource(pdf, doc_id="x", max_mb=0.00001)


def test_pdf_embed_page_guard_raises(tmp_path: Path) -> None:
    pdf = tmp_path / "a.pdf"
    _make_pdf(pdf, pages=2)
    with pytest.raises(PdfTooLargeError):
        content.to_pdf_embedded_resource(pdf, doc_id="x", max_pages=1)


def test_images_from_paths_produce_image_content(tmp_path: Path) -> None:
    pdf = tmp_path / "a.pdf"
    _make_pdf(pdf)
    png = tmp_path / "p.png"
    fitz.open(pdf).load_page(0).get_pixmap().save(png)
    images = content.images_from_paths([png])
    assert all(isinstance(img, Image) for img in images)
    ic = images[0].to_image_content()
    assert ic.type == "image"
    assert ic.mimeType == "image/png"
    assert ic.data


def test_pdf_resource_link_shape() -> None:
    link = content.pdf_resource_link("abc123", name="a.pdf")
    assert isinstance(link, ResourceLink)
    assert link.type == "resource_link"
    assert str(link.uri).startswith("paperpilot://pdf/abc123")
    assert link.mimeType == "application/pdf"


def test_safe_pdf_path_rejects_non_pdf(tmp_path: Path) -> None:
    txt = tmp_path / "x.txt"
    txt.write_text("hi", encoding="utf-8")
    with pytest.raises(ValueError):
        content.safe_pdf_path(txt, _settings(tmp_path))


def test_safe_pdf_path_containment(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    outside = tmp_path / "outside.pdf"  # sibling of data_dir, not inside it
    _make_pdf(outside)

    allowed = _settings(data_dir)  # allow_external_pdf_paths defaults True
    assert content.safe_pdf_path(outside, allowed) == outside.resolve()

    strict = _settings(data_dir, allow_external_pdf_paths=False)
    with pytest.raises(ValueError):
        content.safe_pdf_path(outside, strict)

    inside = data_dir / "in.pdf"
    _make_pdf(inside)
    assert content.safe_pdf_path(inside, strict) == inside.resolve()
