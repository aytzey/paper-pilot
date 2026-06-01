from pathlib import Path

from paper_pilot.config import Settings
from paper_pilot.models import DeepReadArtifact, DownloadedDocument, PaperRecord, TextChunk
from paper_pilot.services.reporting import ReportService


def test_render_markdown_mentions_downloads(tmp_path: Path) -> None:
    settings = Settings(
        openalex_email=None,
        semantic_scholar_api_key=None,
        zotero_library_id=None,
        zotero_library_type="user",
        zotero_api_key=None,
        data_dir=tmp_path,
        libgen_mirrors=("https://libgen.is",),
        libgen_timeout_sec=10.0,
    )
    service = ReportService(settings)
    paper = PaperRecord(
        source="arxiv",
        source_id="1234.5678",
        title="Transformer Methods",
        authors=["Jane Doe"],
        year=2025,
        url="https://arxiv.org/abs/1234.5678",
        pdf_url="https://arxiv.org/pdf/1234.5678.pdf",
        is_open_access=True,
    )
    document = DownloadedDocument(
        paper=paper,
        path=tmp_path / "paper.pdf",
        page_count=12,
        extracted_preview="Sample preview",
    )

    markdown = service.render_markdown(
        topic="transformer methods",
        papers=[paper],
        related=[],
        downloads=[document],
        warnings=[],
    )

    assert "Transformer Methods" in markdown
    assert "Saved PDF" in markdown
    assert "Sample preview" in markdown


def test_deep_read_markdown_has_comparison_table_and_no_debug_scores(tmp_path: Path) -> None:
    settings = Settings(
        openalex_email=None,
        semantic_scholar_api_key=None,
        zotero_library_id=None,
        zotero_library_type="user",
        zotero_api_key=None,
        data_dir=tmp_path,
        libgen_mirrors=("https://libgen.is",),
        libgen_timeout_sec=10.0,
    )
    service = ReportService(settings)
    paper = PaperRecord(
        source="arxiv",
        source_id="1",
        title="RAG Survey",
        year=2023,
        venue="arXiv",
        citation_count=643,
    )
    artifact = DeepReadArtifact(
        paper=paper,
        pdf_path=tmp_path / "a.pdf",
        text_path=tmp_path / "a.txt",
        chunk_manifest_path=tmp_path / "a.json",
        page_count=21,
        full_text_char_count=5000,
        extracted_preview="",
        chunks=[
            TextChunk(chunk_index=0, start_page=1, end_page=1, text="retrieval augmented generation overview", score=12.5, keyword_hits=["retrieval", "generation"]),
            TextChunk(chunk_index=1, start_page=6, end_page=6, text="modular rag paradigm", score=8.0, keyword_hits=["rag"]),
        ],
    )

    markdown = service.render_deep_read_markdown(
        topic="retrieval augmented generation",
        research_question="retrieval augmented generation",
        papers=[paper],
        deep_reads=[artifact],
        warnings=[],
    )

    assert "## Comparison" in markdown
    assert "| # | Paper | Year | Venue | Citations | Pages | Top evidence |" in markdown
    assert "RAG Survey" in markdown
    assert "643" in markdown
    assert "p.1" in markdown  # top-evidence page derived from highest-score chunk
    # debug score=/hits= lines must NOT appear in the human-facing report
    assert "score=" not in markdown
    assert "hits=" not in markdown
    # but a clean "matched:" annotation should
    assert "matched:" in markdown
