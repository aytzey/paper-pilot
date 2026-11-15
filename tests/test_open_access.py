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
from paper_pilot.models import PaperRecord, combine_papers
from paper_pilot.services.academic import AcademicSearchService
from paper_pilot.services.open_access import PMC_OPEN_DATA, OpenAccessService

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

    with pytest.raises(ValueError, match="Download exceeded 100 bytes"):
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


def test_unpaywall_alternate_pdf_is_downloaded_with_provenance(tmp_path: Path, monkeypatch) -> None:
    data = _pdf_bytes()
    base = "https://93.184.216.34"
    best = {"url_for_pdf": f"{base}/broken.pdf", "host_type": "publisher"}
    alternate = {"url_for_pdf": f"{base}/repository.pdf", "host_type": "repository", "license": "cc-by", "version": "acceptedVersion"}
    paper = PaperRecord(source="test", source_id="1", title="Paper", pdf_url=best["url_for_pdf"], raw={
        "unpaywall": {"status": "ok", "best_oa_location": best, "oa_locations": [best, alternate]},
    })
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, content=b"%PDF broken" if request.url.path == "/broken.pdf" else data)

    async def no_delay(seconds):
        pass

    monkeypatch.setattr(asyncio, "sleep", no_delay)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await OpenAccessService(_settings(tmp_path))._download_pdf(client, "topic", paper)

    path = asyncio.run(run())
    assert path.read_bytes() == data
    # A corrupt file is not retried: the next location gets its turn immediately.
    assert seen == [best["url_for_pdf"], alternate["url_for_pdf"]]
    assert paper.pdf_url == alternate["url_for_pdf"]
    assert paper.raw["pdf_download"] == {"url": alternate["url_for_pdf"], "resolver": "unpaywall", "location": alternate}


@pytest.mark.parametrize("initial_path,resolve", [("/paper.pdf", False), ("/broken.pdf", True), (None, True)])
def test_unpaywall_runs_only_when_a_pdf_is_needed(tmp_path: Path, monkeypatch, initial_path, resolve) -> None:
    pdf = _pdf_bytes()
    requested = []
    lookups = []

    def handler(request):
        requested.append(request.url.path)
        return httpx.Response(404 if request.url.path == "/broken.pdf" else 200, content=pdf)

    async def lookup(self, client, doi):
        lookups.append(doi)
        return {"doi": doi, "is_oa": True, "best_oa_location": {"url_for_pdf": _PUBLIC_PDF}}

    async def no_delay(seconds):
        pass

    client_class = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client_class(transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setattr(AcademicSearchService, "_lookup_unpaywall", lookup)
    monkeypatch.setattr(asyncio, "sleep", no_delay)
    settings = _settings(tmp_path, unpaywall_email="you@example.com" if resolve else None)
    url = f"https://93.184.216.34{initial_path}" if initial_path else None
    document = asyncio.run(OpenAccessService(settings).download_paper(url, doi="10.1234/1"))
    assert document.path.read_bytes() == pdf
    assert lookups == (["10.1234/1"] if resolve else [])
    assert requested[-1] == "/paper.pdf"
    if initial_path:
        assert requested[0] == initial_path
    if resolve:
        assert document.paper.raw["unpaywall"]["status"] == "ok"


def test_failed_pdf_uses_openalex_when_unpaywall_is_unavailable(tmp_path, monkeypatch):
    data = _pdf_bytes()
    requested = []

    def handler(request):
        requested.append(request.url.path)
        return httpx.Response(404 if request.url.path == "/broken.pdf" else 200, content=data)

    async def unavailable(self, client, doi):
        raise httpx.ConnectError("Unpaywall unavailable")

    async def openalex(self, client, doi):
        return {"id": "W1", "display_name": "Paper", "best_oa_location": {"pdf_url": _PUBLIC_PDF},
                "open_access": {"is_oa": True, "oa_status": "green"}}

    async def no_delay(seconds):
        pass

    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setattr(AcademicSearchService, "_lookup_unpaywall", unavailable)
    monkeypatch.setattr(AcademicSearchService, "_lookup_openalex_by_doi", openalex)
    monkeypatch.setattr(asyncio, "sleep", no_delay)
    failed_url = "https://93.184.216.34/broken.pdf"
    document = asyncio.run(OpenAccessService(_settings(tmp_path)).download_paper(failed_url, doi="10.1234/test"))
    assert document.path.read_bytes() == data
    assert requested == ["/broken.pdf", "/paper.pdf"]
    assert document.paper.raw["original_pdf_url"] == failed_url
    assert document.paper.raw["unpaywall"]["status"] == "error"
    assert document.paper.raw["pdf_download"]["resolver"] == "openalex"
    assert any("OpenAlex fallback" in warning for warning in document.paper.raw["access_warnings"])


def test_previous_unpaywall_error_can_recover_after_pdf_failure(tmp_path, monkeypatch):
    data = _pdf_bytes()
    calls = []

    def handler(request):
        return httpx.Response(404 if request.url.path == "/broken.pdf" else 200, content=data)

    async def lookup(self, client, doi):
        calls.append(doi)
        return {"is_oa": True, "best_oa_location": {"url_for_pdf": _PUBLIC_PDF}}

    async def no_delay(seconds):
        pass

    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setattr(AcademicSearchService, "_lookup_unpaywall", lookup)
    monkeypatch.setattr(asyncio, "sleep", no_delay)
    paper = PaperRecord(source="test", source_id="1", title="Paper", doi="10.1234/test",
                        pdf_url="https://93.184.216.34/broken.pdf", raw={"unpaywall": {"status": "error"}})
    documents, _ = asyncio.run(OpenAccessService(_settings(tmp_path)).download_best_papers("topic", [paper]))
    assert len(documents) == 1 and documents[0].path.read_bytes() == data
    assert calls == [paper.doi] and paper.raw["unpaywall"]["status"] == "ok"


@pytest.mark.parametrize("initial_path", [None, "/broken.pdf"])
@pytest.mark.parametrize("unpaywall_status", [200, 403])
def test_missing_email_uses_dummy_then_openalex_on_failure(tmp_path, monkeypatch, initial_path, unpaywall_status):
    data = _pdf_bytes()
    requested = []

    def handler(request):
        requested.append(request.url.host)
        if request.url.host == "api.unpaywall.org":
            assert request.url.params["email"] == "nomail@mail.com"
            return httpx.Response(unpaywall_status, json={"is_oa": True, "best_oa_location": {"url_for_pdf": _PUBLIC_PDF}})
        if request.url.host == "api.openalex.org":
            return httpx.Response(200, json={"id": "W1", "display_name": "Paper",
                                            "best_oa_location": {"pdf_url": _PUBLIC_PDF}})
        return httpx.Response(404 if request.url.path == "/broken.pdf" else 200, content=data)

    async def no_delay(seconds):
        pass

    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setattr(asyncio, "sleep", no_delay)
    settings = _settings(tmp_path, unpaywall_email=None, openalex_email=None)
    settings.cache_dir.mkdir()
    url = f"https://93.184.216.34{initial_path}" if initial_path else None
    document = asyncio.run(OpenAccessService(settings).download_paper(url, doi="10.1234/test"))
    assert document.path.read_bytes() == data
    assert "api.unpaywall.org" in requested
    if unpaywall_status == 200:
        assert "api.openalex.org" not in requested
        assert document.paper.raw["pdf_download"]["resolver"] == "unpaywall"
        assert document.paper.raw["unpaywall"]["status"] == "ok"
    else:
        assert requested.index("api.unpaywall.org") < requested.index("api.openalex.org")
        assert document.paper.raw["pdf_download"]["resolver"] == "openalex"
        assert "HTTPStatusError" in document.paper.raw["unpaywall"]["error"]
        assert any("OpenAlex fallback" in warning for warning in document.paper.raw["access_warnings"])


def _routed_client(monkeypatch, routes: dict[str, httpx.Response], seen: list[str]) -> None:
    """Serve canned responses by URL prefix; anything else is a 404."""
    def handler(request):
        url = str(request.url)
        seen.append(url)
        return next((response for prefix, response in routes.items() if url.startswith(prefix)), httpx.Response(404))

    async def no_delay(seconds):
        pass

    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setattr(asyncio, "sleep", no_delay)


def _pmc_listing(*keys: str) -> httpx.Response:
    return httpx.Response(200, text="<ListBucketResult>" + "".join(f"<Contents><Key>{key}</Key></Contents>" for key in keys) + "</ListBucketResult>")


def test_pmc_link_is_downloaded_from_open_data_not_the_bot_walled_page(tmp_path, monkeypatch):
    data, seen = _pdf_bytes(), []
    _routed_client(monkeypatch, {
        f"{PMC_OPEN_DATA}/?list-type=2&prefix=PMC123.": _pmc_listing(
            "PMC123.1/PMC123.1.pdf", "PMC123.1/PMC123.1.txt", "PMC123.1/mmc1.pdf", "PMC1234.1/PMC1234.1.pdf"),
        f"{PMC_OPEN_DATA}/PMC123.1/PMC123.1.pdf": httpx.Response(200, content=data),
    }, seen)
    document = asyncio.run(OpenAccessService(_settings(tmp_path)).download_paper(
        "https://pmc.ncbi.nlm.nih.gov/articles/PMC123/pdf/"))
    assert document.path.read_bytes() == data
    assert not any("ncbi.nlm.nih.gov" in url for url in seen)
    download = document.paper.raw["pdf_download"]
    assert (download["resolver"], download["format"]) == ("pmc_open_data", "pdf")
    assert download["supplements"] == [f"{PMC_OPEN_DATA}/PMC123.1/mmc1.pdf"]


def test_closed_doi_with_text_only_pmc_manuscript_becomes_a_readable_pdf(tmp_path, monkeypatch):
    seen = []
    manuscript = "Methods\n" + "We measured drift over ninety days. " * 300 + "\x9f\nFINAL SECTION reaches the reader."
    _routed_client(monkeypatch, {
        "https://api.unpaywall.org": httpx.Response(200, json={"is_oa": False, "oa_locations": []}),
        "https://pmc.ncbi.nlm.nih.gov/tools/idconv": httpx.Response(200, json={"records": [{"pmcid": "PMC77"}]}),
        f"{PMC_OPEN_DATA}/?list-type=2&prefix=PMC77.": _pmc_listing("PMC77.1/PMC77.1.txt", "PMC77.1/PMC77.1.xml"),
        f"{PMC_OPEN_DATA}/PMC77.1/PMC77.1.txt": httpx.Response(200, content=manuscript.encode()),
    }, seen)
    settings = _settings(tmp_path)
    settings.cache_dir.mkdir()
    document = asyncio.run(OpenAccessService(settings).download_paper(doi="10.1234/closed"))
    with fitz.open(document.path) as pdf:
        text = "".join(page.get_text("text") for page in pdf)
    assert "FINAL SECTION" in text and "\x9f" not in text
    assert document.text_chars > 9000 and document.page_count > 1
    assert document.paper.raw["pdf_download"]["format"] == "text_rendered_as_pdf"
    assert any("generated from PMC" in warning for warning in document.paper.raw["access_warnings"])


def test_arxiv_copy_is_tried_before_a_publisher_link(tmp_path, monkeypatch):
    data, seen = _pdf_bytes(), []
    _routed_client(monkeypatch, {"https://arxiv.org/pdf/1706.03762": httpx.Response(200, content=data)}, seen)
    service = OpenAccessService(_settings(tmp_path))
    document = asyncio.run(service.download_paper("https://93.184.216.34/paywalled.pdf", arxiv_id="arXiv:1706.03762"))
    asyncio.run(service.download_paper("https://arxiv.org/abs/1706.03762v7"))  # a requested version is kept
    assert seen == ["https://arxiv.org/pdf/1706.03762", "https://arxiv.org/pdf/1706.03762v7"]
    assert document.paper.raw["pdf_download"]["resolver"] == "arxiv"


@pytest.mark.parametrize("status,attempts", [(403, 1), (404, 1), (503, 3)])
def test_only_transient_failures_are_retried(tmp_path, monkeypatch, status, attempts):
    seen = []
    _routed_client(monkeypatch, {_PUBLIC_PDF: httpx.Response(status)}, seen)
    with pytest.raises(ValueError, match=str(status)):
        asyncio.run(OpenAccessService(_settings(tmp_path)).download_paper(_PUBLIC_PDF))
    assert seen == [_PUBLIC_PDF] * attempts


def test_merging_sources_keeps_every_pdf_link_as_a_download_candidate(tmp_path, monkeypatch):
    data, seen = _pdf_bytes(), []
    dead, alive = "https://93.184.216.34/dead.pdf", "https://93.184.216.34/alive.pdf"
    merged = combine_papers([
        PaperRecord(source="a", source_id="1", title="Paper", doi="10.1234/x", pdf_url=dead, citation_count=9),
        PaperRecord(source="b", source_id="2", title="Paper", doi="10.1234/x", pdf_url=alive),
    ])[0]
    merged.raw["unpaywall"] = {"status": "ok"}
    _routed_client(monkeypatch, {alive: httpx.Response(200, content=data)}, seen)
    documents, _ = asyncio.run(OpenAccessService(_settings(tmp_path)).download_best_papers("topic", [merged]))
    assert seen == [dead, alive] and documents[0].path.read_bytes() == data
