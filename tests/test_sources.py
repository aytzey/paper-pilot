from pathlib import Path

from paper_pilot.config import Settings
from paper_pilot.models import PaperRecord, normalize_arxiv_id
from paper_pilot.services.academic import AcademicSearchService


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


def test_paper_from_doaj_parses_bibjson(tmp_path: Path) -> None:
    service = AcademicSearchService(_settings(tmp_path))
    item = {
        "id": "abc123",
        "bibjson": {
            "title": "An Open Access RAG Study",
            "year": "2024",
            "abstract": "We study retrieval augmented generation.",
            "author": [{"name": "Jane Doe"}, {"name": "John Roe"}],
            "journal": {"title": "Journal of OA"},
            "keywords": ["RAG", "LLM"],
            "identifier": [{"type": "doi", "id": "10.1234/oa.2024"}, {"type": "pissn", "id": "1234-5678"}],
            "link": [
                {"type": "fulltext", "content_type": "HTML", "url": "https://oa.example/article"},
                {"type": "fulltext", "content_type": "PDF", "url": "https://oa.example/article.pdf"},
            ],
        },
    }

    paper = service._paper_from_doaj(item)

    assert paper.source == "doaj"
    assert paper.title == "An Open Access RAG Study"
    assert paper.year == 2024
    assert paper.doi == "10.1234/oa.2024"
    assert paper.pdf_url == "https://oa.example/article.pdf"
    assert paper.url == "https://oa.example/article"
    assert paper.is_open_access is True
    assert paper.authors == ["Jane Doe", "John Roe"]
    assert "RAG" in paper.keywords


def test_normalize_arxiv_id_collapses_forms() -> None:
    assert normalize_arxiv_id("http://arxiv.org/abs/2301.12345v2") == "2301.12345"
    assert normalize_arxiv_id("https://arxiv.org/pdf/2301.12345") == "2301.12345"
    assert normalize_arxiv_id("arXiv:2301.12345v3") == "2301.12345"
    assert normalize_arxiv_id("hep-th/9901001v1") == "hep-th/9901001"
    assert normalize_arxiv_id("not an id") is None


def test_arxiv_dedupe_key_matches_across_abs_and_pdf() -> None:
    a = PaperRecord(source="arxiv", source_id="2301.12345v2", title="X", url="http://arxiv.org/abs/2301.12345v2")
    b = PaperRecord(source="openalex", source_id="W1", title="X", url="https://arxiv.org/pdf/2301.12345")
    assert a.dedupe_key() == b.dedupe_key() == "arxiv:2301.12345"
