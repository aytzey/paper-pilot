import json
from pathlib import Path

import pytest

try:
    import fitz
except ModuleNotFoundError:  # pragma: no cover - compatibility fallback
    import pymupdf as fitz

from paper_pilot.config import Settings
from paper_pilot.models import DownloadedDocument, PaperRecord
from paper_pilot.services.deep_read import DeepReadingService


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        openalex_email="you@example.com",
        semantic_scholar_api_key=None,
        zotero_library_id=None,
        zotero_library_type="user",
        zotero_api_key=None,
        data_dir=tmp_path,
        libgen_mirrors=("https://libgen.is",),
        libgen_timeout_sec=10.0,
        unpaywall_email="you@example.com",
    )


def _make_pdf(path: Path) -> None:
    document = fitz.open()
    first_page = document.new_page()
    first_page.insert_text(
        (72, 72),
        "Transformer models improve machine translation results.\n"
        "The benchmark compares BLEU scores across datasets.\n"
        "Figure 1 shows the architecture.",
    )
    second_page = document.new_page()
    second_page.insert_text(
        (72, 72),
        "Results section discusses evaluation metrics and dataset splits.\n"
        "Table 2 reports benchmark numbers for transformer baselines.",
    )
    document.save(path)
    document.close()


def test_extract_document_writes_text_and_chunk_manifest(tmp_path: Path) -> None:
    pdf_path = tmp_path / "paper.pdf"
    _make_pdf(pdf_path)
    service = DeepReadingService(_settings(tmp_path))
    document = DownloadedDocument(
        paper=PaperRecord(source="test", source_id="1", title="Transformer Paper"),
        path=pdf_path,
        page_count=2,
        extracted_preview="",
    )

    artifact = service.extract_document(document, research_question="transformer benchmark results")

    assert artifact.text_path.exists()
    assert artifact.chunk_manifest_path.exists()
    assert artifact.page_count == 2
    assert artifact.full_text_char_count > 50
    assert artifact.chunks
    assert any("transformer" in chunk.keyword_hits for chunk in artifact.chunks)


def test_page_count(tmp_path: Path) -> None:
    pdf_path = tmp_path / "p.pdf"
    _make_pdf(pdf_path)
    service = DeepReadingService(_settings(tmp_path))
    assert service.page_count(pdf_path) == 2


def test_select_evidence_pages_bounded(tmp_path: Path) -> None:
    pdf_path = tmp_path / "p.pdf"
    _make_pdf(pdf_path)
    service = DeepReadingService(_settings(tmp_path))
    document = DownloadedDocument(
        paper=PaperRecord(source="test", source_id="1", title="Transformer Paper"),
        path=pdf_path,
        page_count=2,
        extracted_preview="",
    )
    artifact = service.extract_document(document, research_question="transformer benchmark results")
    pages = service.select_evidence_pages(artifact, max_pages=1)
    assert len(pages) <= 1
    assert all(1 <= page <= 2 for page in pages)


def test_page_text_returns_full_text_and_validates(tmp_path: Path) -> None:
    pdf_path = tmp_path / "p.pdf"
    _make_pdf(pdf_path)
    service = DeepReadingService(_settings(tmp_path))
    pages = service.page_text(pdf_path, [1, 2])
    assert [p["page"] for p in pages] == [1, 2]
    assert "Transformer" in pages[0]["text"]
    with pytest.raises(ValueError):
        service.page_text(pdf_path, [99])


def test_manifest_stores_full_untruncated_chunk_text(tmp_path: Path) -> None:
    pdf_path = tmp_path / "long.pdf"
    document_pdf = fitz.open()
    page = document_pdf.new_page()
    y = 50
    for i in range(40):
        page.insert_text((40, y), f"line {i}: retrieval augmented generation evidence chunk content here")
        y += 18
    document_pdf.save(pdf_path)
    document_pdf.close()

    service = DeepReadingService(_settings(tmp_path))
    document = DownloadedDocument(
        paper=PaperRecord(source="test", source_id="1", title="Long"),
        path=pdf_path,
        page_count=1,
        extracted_preview="",
    )
    artifact = service.extract_document(document, research_question="retrieval")
    manifest = json.loads(artifact.chunk_manifest_path.read_text(encoding="utf-8"))
    # the on-disk manifest must keep full chunk text (no 1200-char truncation marker)
    assert any(len(chunk["text"]) > 1200 for chunk in manifest["chunks"])
    assert all(not chunk["text"].endswith("...") for chunk in manifest["chunks"])


def test_render_pages_exports_pngs(tmp_path: Path) -> None:
    pdf_path = tmp_path / "figures.pdf"
    _make_pdf(pdf_path)
    service = DeepReadingService(_settings(tmp_path))

    images = service.render_pages(str(pdf_path), [1, 2], scale=1.5)

    assert len(images) == 2
    assert all(image.exists() for image in images)
    assert all(image.suffix == ".png" for image in images)
