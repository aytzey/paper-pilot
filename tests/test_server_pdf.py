import asyncio
import base64
from dataclasses import replace
from pathlib import Path

import pytest

try:
    import fitz
except ModuleNotFoundError:  # pragma: no cover
    import pymupdf as fitz

from mcp.server.fastmcp.utilities.types import Image
from mcp.types import EmbeddedResource, ImageContent, ResourceLink

from paper_pilot import server
from paper_pilot.config import Settings
from paper_pilot.models import DownloadedDocument, PaperRecord
from paper_pilot.services.content import PdfTooLargeError


def _settings(data_dir: Path, **over) -> Settings:
    base = Settings(
        openalex_email="you@example.com",
        semantic_scholar_api_key=None,
        zotero_library_id=None,
        zotero_library_type="user",
        zotero_api_key=None,
        data_dir=data_dir,
        libgen_mirrors=("https://libgen.is",),
        libgen_timeout_sec=10.0,
        unpaywall_email="you@example.com",
    )
    return replace(base, **over) if over else base


def _make_pdf(path: Path, pages: int = 2) -> None:
    document = fitz.open()
    for i in range(pages):
        document.new_page().insert_text((72, 72), f"page {i + 1}: transformer benchmark figure table results")
    document.save(path)
    document.close()


def _patch_settings(tmp_path: Path, monkeypatch) -> Settings:
    s = _settings(tmp_path)
    monkeypatch.setattr(server, "get_settings", lambda: s)
    return s


def test_render_pdf_pages_returns_meta_plus_images(tmp_path: Path, monkeypatch) -> None:
    _patch_settings(tmp_path, monkeypatch)
    pdf = tmp_path / "x.pdf"
    _make_pdf(pdf)
    out = server.render_pdf_pages(str(pdf), [1, 2])
    assert isinstance(out, list)
    assert isinstance(out[0], dict)
    assert out[0]["images"] and "doc_id" in out[0]
    assert all(isinstance(block, Image) for block in out[1:])
    assert len(out) == 3  # meta + 2 page images


def test_render_pdf_pages_convert_result_roundtrip(tmp_path: Path, monkeypatch) -> None:
    # Guards the "-> dict vs -> list" pitfall: the real MCP call path must emit content blocks.
    _patch_settings(tmp_path, monkeypatch)
    pdf = tmp_path / "x.pdf"
    _make_pdf(pdf)
    out = asyncio.run(
        server.mcp._tool_manager.call_tool(
            "render_pdf_pages", {"pdf_path": str(pdf), "page_numbers": [1]}, context=None, convert_result=True
        )
    )
    blocks = out[0] if isinstance(out, tuple) else out
    assert any(isinstance(b, ImageContent) for b in blocks)


def test_read_pdf_document_embeds_pdf(tmp_path: Path, monkeypatch) -> None:
    _patch_settings(tmp_path, monkeypatch)
    pdf = tmp_path / "x.pdf"
    _make_pdf(pdf)
    out = server.read_pdf_document(str(pdf), embed_base64=True)
    assert out[0]["pdf_path"] and out[0]["page_count"] == 2
    assert isinstance(out[1], EmbeddedResource)
    assert base64.b64decode(out[1].resource.blob).startswith(b"%PDF")


def test_read_pdf_document_defaults_to_path_and_link(tmp_path: Path, monkeypatch) -> None:
    _patch_settings(tmp_path, monkeypatch)
    pdf = tmp_path / "x.pdf"
    _make_pdf(pdf)
    out = server.read_pdf_document(str(pdf))  # default: no base64
    assert out[0]["pdf_path"]
    assert isinstance(out[1], ResourceLink)
    assert not any(isinstance(block, EmbeddedResource) for block in out)


def test_read_pdf_document_size_guard(tmp_path: Path, monkeypatch) -> None:
    _patch_settings(tmp_path, monkeypatch)
    pdf = tmp_path / "x.pdf"
    _make_pdf(pdf)
    with pytest.raises(PdfTooLargeError):
        server.read_pdf_document(str(pdf), embed_base64=True, max_mb=0.00001)


def test_deep_read_topic_attaches_images_and_pdf(tmp_path: Path, monkeypatch) -> None:
    _patch_settings(tmp_path, monkeypatch)
    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir(parents=True, exist_ok=True)
    pdf = downloads_dir / "paper.pdf"
    _make_pdf(pdf, pages=3)
    paper = PaperRecord(source="test", source_id="1", title="A Transformer Paper")
    doc = DownloadedDocument(paper=paper, path=pdf, page_count=3, extracted_preview="preview")

    async def fake_pipeline(**kwargs):
        return {
            "top_papers": [paper],
            "related_papers": [],
            "downloads": [doc],
            "scihub_downloads": [],
            "libgen_results": [],
            "libgen_downloads": [],
            "warnings": [],
        }

    monkeypatch.setattr(server, "_run_research_pipeline", fake_pipeline)

    out = asyncio.run(
        server.deep_read_topic("transformer benchmark", download_top_n=1, render_top_pages=True, attach_top_pdf=True)
    )
    assert isinstance(out, list)
    result = out[0]
    assert isinstance(result, dict)
    # backward-compat keys preserved + all PDF paths surfaced
    for key in ("deep_reads", "report_path", "downloads", "warnings", "agent_notes", "pdf_paths"):
        assert key in result
    assert result["pdf_paths"]
    # opt-in escalation: page images + embedded PDF in the trailing content
    assert any(isinstance(block, Image) for block in out[1:])
    assert any(isinstance(block, EmbeddedResource) for block in out[1:])
    assert "rendered_pages" in result and "attached_pdf" in result


def test_deep_read_topic_escalation_off(tmp_path: Path, monkeypatch) -> None:
    _patch_settings(tmp_path, monkeypatch)
    downloads_dir = tmp_path / "downloads"
    downloads_dir.mkdir(parents=True, exist_ok=True)
    pdf = downloads_dir / "paper.pdf"
    _make_pdf(pdf, pages=2)
    paper = PaperRecord(source="test", source_id="1", title="Paper")
    doc = DownloadedDocument(paper=paper, path=pdf, page_count=2, extracted_preview="preview")

    async def fake_pipeline(**kwargs):
        return {
            "top_papers": [paper],
            "related_papers": [],
            "downloads": [doc],
            "scihub_downloads": [],
            "libgen_results": [],
            "libgen_downloads": [],
            "warnings": [],
        }

    monkeypatch.setattr(server, "_run_research_pipeline", fake_pipeline)

    out = asyncio.run(server.deep_read_topic("topic", download_top_n=1))  # defaults: paths only, no base64
    assert isinstance(out, list)
    assert len(out) == 1  # only the result dict, no inlined content blocks
    assert out[0]["pdf_paths"]  # file paths still surfaced
