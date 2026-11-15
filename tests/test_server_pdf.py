import asyncio
import base64
import json
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
from paper_pilot.services.academic import AcademicSearchService, SearchBundle
from paper_pilot.services.content import PdfTooLargeError
from paper_pilot.services.open_access import OpenAccessService


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


def test_get_pdf_page_text_returns_page_text(tmp_path: Path, monkeypatch) -> None:
    _patch_settings(tmp_path, monkeypatch)
    pdf = tmp_path / "x.pdf"
    _make_pdf(pdf, pages=2)
    out = server.get_pdf_page_text(str(pdf), [1, 2])
    assert isinstance(out, dict)
    assert out["pdf_path"] and out["doc_id"]
    assert [p["page"] for p in out["pages"]] == [1, 2]
    assert "transformer" in out["pages"][0]["text"].lower()


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
    for key in ("deep_reads", "report_path", "warnings", "agent_notes", "pdf_paths"):
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
    reading = out[0]["deep_reads"][0]["full_text"]
    assert reading["end_of_document"]
    assert [p["page"] for p in reading["pages"]] == [1, 2]


def test_full_text_mcp_batches_reconstruct_every_page(tmp_path: Path, monkeypatch) -> None:
    _patch_settings(tmp_path, monkeypatch)
    pdf = tmp_path / "complete.pdf"
    with fitz.open() as document:
        for number in range(1, 9):
            page = document.new_page()
            for line in range(32):
                page.insert_text((40, 50 + 20 * line), f"Page {number}, line {line}: methods and evidence beyond the abstract.")
        document[-1].insert_text((40, 720), "FINAL APPENDIX: late evidence must reach the model.")
        document.save(pdf)
    expected = server.get_pdf_page_text(str(pdf), list(range(1, 9)))["pages"]
    reconstructed = {p["page"]: "" for p in expected}
    cursor = {"start_page": 1, "start_char": 0}
    saw_split = False
    while cursor is not None:
        # Exercise FastMCP serialization, not just the Python service return value.
        result = asyncio.run(server.mcp._tool_manager.call_tool(
            "read_pdf_text", {"pdf_path": str(pdf), "max_chars": 701, **cursor},
            context=None, convert_result=True,
        ))
        blocks = result[0] if isinstance(result, tuple) else result
        batch = json.loads(next(b.text for b in blocks if b.type == "text"))
        assert batch["returned_chars"] <= 701
        assert not batch["warnings"]
        for page in batch["pages"]:
            assert page["start_char"] == len(reconstructed[page["page"]])
            reconstructed[page["page"]] += page["text"]
            saw_split |= not page["page_complete"]
        assert batch["next_cursor"] != cursor
        cursor = batch["next_cursor"]
    assert saw_split
    assert reconstructed == {p["page"]: p["text"] for p in expected}
    assert "FINAL APPENDIX" in reconstructed[8]


@pytest.mark.parametrize("tool,kwargs", [
    (server.read_pdf_text, {}),
    (server.extract_local_pdf_text, {}),
    (server.render_pdf_pages, {"page_numbers": [1]}),
])
def test_pdf_readers_enforce_data_directory(tmp_path: Path, monkeypatch, tool, kwargs) -> None:
    pdf = tmp_path / "outside.pdf"
    _make_pdf(pdf)
    settings = _settings(tmp_path / "data", allow_external_pdf_paths=False)
    monkeypatch.setattr(server, "get_settings", lambda: settings)
    with pytest.raises(ValueError, match="outside the data directory"):
        tool(str(pdf), **kwargs)


def test_deep_read_preserves_good_paper_when_another_is_corrupt(tmp_path: Path, monkeypatch) -> None:
    _patch_settings(tmp_path, monkeypatch)
    good = tmp_path / "good.pdf"
    bad = tmp_path / "broken.pdf"
    _make_pdf(good)
    bad.write_bytes(b"%PDF broken")
    paper = PaperRecord(source="test", source_id="1", title="Paper")

    async def fake_pipeline(**kwargs):
        return {
            "top_papers": [paper], "related_papers": [], "warnings": [],
            "downloads": [DownloadedDocument(paper, path, 2, "") for path in [bad, good]],
            "scihub_downloads": [],
        }

    monkeypatch.setattr(server, "_run_research_pipeline", fake_pipeline)
    result = asyncio.run(server.deep_read_topic("topic"))[0]
    assert [p["pdf_path"] for p in result["deep_reads"]] == [str(good)]
    assert result["extraction_failures"][0]["pdf_path"] == str(bad)
    assert result["warnings"]


# Codex truncates tool output at roughly 10k tokens and Claude Code cuts server instructions at
# about 2,000 characters. Text lost there never reaches the model, so the defaults must fit.
CLIENT_OUTPUT_CHARS = 30000


def _long_pdf(path: Path, pages: int = 12) -> None:
    with fitz.open() as document:
        for number in range(1, pages + 1):
            page = document.new_page()
            for line in range(36):
                page.insert_text((40, 50 + 19 * line), f"Page {number} line {line}: methods, results and limits beyond the abstract.")
        document.save(path)


def _served_text(tool: str, arguments: dict) -> str:
    """The text block a client receives, after FastMCP serialization."""
    result = asyncio.run(server.mcp._tool_manager.call_tool(tool, arguments, context=None, convert_result=True))
    blocks = result[0] if isinstance(result, tuple) else result
    return next(block.text for block in blocks if block.type == "text")


def test_server_instructions_survive_client_truncation() -> None:
    assert len(server.mcp.instructions) <= 2000
    assert "download_paper" in server.mcp.instructions and "next_cursor" in server.mcp.instructions


def test_default_deep_read_fits_the_client_and_every_paper_can_be_continued(tmp_path: Path, monkeypatch) -> None:
    _patch_settings(tmp_path, monkeypatch)
    abstract = "Background and conclusions. " * 80
    papers = [PaperRecord(source="test", source_id=str(i), title=f"Paper {i}", doi=f"10.1234/{i}", abstract=abstract,
                          authors=[f"Author {n}" for n in range(30)]) for i in range(12)]
    documents = []
    for paper in papers[:4]:
        path = tmp_path / f"{paper.source_id}.pdf"
        _long_pdf(path)
        documents.append(DownloadedDocument(paper=paper, path=path, page_count=12, extracted_preview=""))

    async def fake_pipeline(**kwargs):
        return {"top_papers": papers, "related_papers": papers[6:], "downloads": documents,
                "scihub_downloads": [], "warnings": []}

    monkeypatch.setattr(server, "_run_research_pipeline", fake_pipeline)
    served = _served_text("deep_read_topic", {"topic": "methods results limits"})
    assert len(served) <= CLIENT_OUTPUT_CHARS
    result = json.loads(served)
    assert len(result["deep_reads"]) == 4
    for read in result["deep_reads"]:
        opening = read["full_text"]
        assert opening["pages"][0]["text"].startswith("Page 1 line 0")
        cursor = opening["next_cursor"]
        served_rest = _served_text("read_pdf_text", {"pdf_path": read["pdf_path"], **cursor})
        assert len(served_rest) <= CLIENT_OUTPUT_CHARS
        first = json.loads(served_rest)["pages"][0]
        assert (first["page"], first["start_char"]) == (cursor["start_page"], cursor["start_char"])


def _realistic_paper(i: int) -> PaperRecord:
    return PaperRecord(
        source="openalex", source_id=f"https://openalex.org/W{4000000000 + i}", doi=f"10.48550/arxiv.2310.{6000 + i:05d}",
        title=f"A Fairly Long and Specific Paper Title About Transformers for Long-Horizon Forecasting, Part {i}",
        authors=[f"Firstname{n} Lastname{n}" for n in range(9)], year=2024, publication_date="2024-03-11",
        venue="Proceedings of the AAAI Conference on Artificial Intelligence", citation_count=1234, is_open_access=True,
        url=f"https://ojs.aaai.org/index.php/AAAI/article/view/{26000 + i}",
        pdf_url=f"https://ojs.aaai.org/index.php/AAAI/article/download/{26000 + i}/{26000 + i}",
        abstract="Long abstract sentence about methods and results. " * 60, raw={"unpaywall": {"oa_locations": ["x" * 5000]}},
    )


@pytest.mark.parametrize("count,abstracts", [(24, True), (60, False)])
def test_search_results_fit_one_budget_whatever_the_result_count(tmp_path: Path, monkeypatch, count, abstracts) -> None:
    service = AcademicSearchService(_settings(tmp_path))
    papers = [_realistic_paper(i) for i in range(count)]

    async def search(**kwargs):
        return SearchBundle(papers, [], [])

    monkeypatch.setattr(service, "search_literature", search)
    monkeypatch.setattr(server, "get_academic_service", lambda: service)
    monkeypatch.setattr(server, "get_settings", lambda: service.settings)
    served = _served_text("search_literature", {"topic": "anything", "limit_per_source": 10})
    assert len(served) <= CLIENT_OUTPUT_CHARS
    results = json.loads(served)["results"]
    assert len(results) == count and "raw" not in results[0] and results[0]["doi"] and results[0]["pdf_url"]
    # A default call (six sources, four each) carries abstracts to screen by; sixty results get a line each.
    assert ("abstract" in results[0]) is abstracts
    if abstracts:
        assert results[0]["authors"][-1] == "+6 more" and "url" not in results[0]
        assert results[0]["abstract"].endswith("[abstract truncated]") and len(results[0]["abstract"]) > 250


def test_download_paper_starts_the_read_and_flags_a_stub(tmp_path: Path, monkeypatch) -> None:
    _patch_settings(tmp_path, monkeypatch)
    full, stub = tmp_path / "full.pdf", tmp_path / "stub.pdf"
    _long_pdf(full)
    _make_pdf(stub, pages=1)

    class FakeAccess:
        async def download_paper(self, pdf_url, title, doi, arxiv_id, pmcid):
            path = stub if doi == "10.1234/stub" else full
            paper = PaperRecord(source="doi", source_id=doi, title=title or doi, doi=doi,
                                raw={"pdf_download": {"url": "https://example.org/a.pdf", "resolver": "unpaywall", "location": None}})
            return OpenAccessService(server.get_settings()).inspect_local_pdf(paper, path)

    monkeypatch.setattr(server, "get_open_access_service", lambda: FakeAccess())
    result = json.loads(_served_text("download_paper", {"doi": "10.1234/full"}))
    assert result["page_count"] == 12 and result["text_chars"] > 20000 and not result["warnings"]
    assert result["paper"]["downloaded_from"]["resolver"] == "unpaywall"
    assert result["full_text"]["pages"][0]["page"] == 1 and result["full_text"]["next_cursor"]
    assert "extracted_preview" not in result
    # The same text is on disk for agents that prefer their own file tools.
    assert "Page 12 line 35" in Path(result["text_path"]).read_text(encoding="utf-8")

    stub_result = asyncio.run(server.download_paper(doi="10.1234/stub"))
    assert any("may be an abstract" in warning for warning in stub_result["warnings"])


def test_remaining_batches_fetched_in_any_order_rebuild_the_whole_paper(tmp_path: Path, monkeypatch) -> None:
    _patch_settings(tmp_path, monkeypatch)
    pdf = tmp_path / "long.pdf"
    _long_pdf(pdf, pages=30)
    first = server._first_batch(pdf)
    plan = first["remaining_batches"]
    assert first["remaining_batch_count"] == len(plan) >= 2
    assert plan[0] == {**first["next_cursor"], "max_chars": 20000}
    # Fetch out of order, as parallel tool calls return, then put the batches back by position.
    batches = [first] + [server.read_pdf_text(str(pdf), **arguments) for arguments in reversed(plan)]
    segments = sorted((page["page"], page["start_char"], page["text"]) for batch in batches for page in batch["pages"])
    expected = "".join(page["text"] for page in server.get_pdf_page_text(str(pdf), list(range(1, 31)))["pages"])
    assert "".join(text for _, _, text in segments) == expected
    assert sum(batch["next_cursor"] is None for batch in batches) == 1
    assert "remaining_batches" not in server.read_pdf_text(str(pdf))  # only a first batch carries the plan
