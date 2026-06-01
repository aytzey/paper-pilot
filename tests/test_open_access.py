import asyncio
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

try:
    import fitz
except ModuleNotFoundError:  # pragma: no cover
    import pymupdf as fitz

from paper_pilot.config import Settings
from paper_pilot.models import PaperRecord
from paper_pilot.services.net import DownloadTooLargeError
from paper_pilot.services.open_access import OpenAccessService

_PUBLIC_PDF = "https://93.184.216.34/paper.pdf"


def _settings(tmp_path: Path, **overrides) -> Settings:
    base = Settings(
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
    return replace(base, **overrides) if overrides else base


def _pdf_bytes() -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Open access full text. Methods and results follow.")
    data = document.tobytes()
    document.close()
    return data


def test_download_pdf_writes_valid_pdf(tmp_path: Path) -> None:
    pdf = _pdf_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=pdf)

    service = OpenAccessService(_settings(tmp_path))
    paper = PaperRecord(source="t", source_id="1", title="A Paper", pdf_url=_PUBLIC_PDF)

    async def run() -> Path:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await service._download_pdf(client, "topic", paper)

    path = asyncio.run(run())
    assert path.exists()
    assert path.read_bytes().startswith(b"%PDF")


def test_download_pdf_rejects_non_pdf(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not a pdf</html>")

    service = OpenAccessService(_settings(tmp_path))
    paper = PaperRecord(source="t", source_id="1", title="A Paper", pdf_url=_PUBLIC_PDF)

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await service._download_pdf(client, "topic", paper)

    with pytest.raises(ValueError):
        asyncio.run(run())


def test_download_pdf_rejects_internal_url(tmp_path: Path) -> None:
    service = OpenAccessService(_settings(tmp_path))
    paper = PaperRecord(source="t", source_id="1", title="A Paper", pdf_url="http://169.254.169.254/x.pdf")

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as client:
            await service._download_pdf(client, "topic", paper)

    with pytest.raises(ValueError):
        asyncio.run(run())


def test_download_pdf_enforces_size_cap(tmp_path: Path) -> None:
    pdf = b"%PDF" + b"x" * 5000

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=pdf)

    service = OpenAccessService(_settings(tmp_path, max_download_bytes=100))
    paper = PaperRecord(source="t", source_id="1", title="A Paper", pdf_url=_PUBLIC_PDF)

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await service._download_pdf(client, "topic", paper)

    # size cap is hit on every retry -> the final raised error is DownloadTooLargeError
    with pytest.raises(DownloadTooLargeError):
        asyncio.run(run())


def test_inspect_local_pdf_extracts_preview(tmp_path: Path) -> None:
    pdf_path = tmp_path / "x.pdf"
    pdf_path.write_bytes(_pdf_bytes())
    service = OpenAccessService(_settings(tmp_path))
    paper = PaperRecord(source="t", source_id="1", title="A Paper")

    document = service.inspect_local_pdf(paper, pdf_path)
    assert document.page_count == 1
    assert "Open access" in document.extracted_preview


def test_download_best_papers_skips_records_without_pdf_url(tmp_path: Path) -> None:
    service = OpenAccessService(_settings(tmp_path))
    papers = [PaperRecord(source="t", source_id="1", title="No PDF")]  # no pdf_url

    async def run():
        return await service.download_best_papers("topic", papers, max_papers=3)

    downloaded, warnings = asyncio.run(run())
    assert downloaded == []
    assert warnings == []
