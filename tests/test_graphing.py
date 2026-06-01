from pathlib import Path

from paper_pilot.config import Settings
from paper_pilot.models import PaperRecord
from paper_pilot.services.graphing import GraphService


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


def _papers() -> list[PaperRecord]:
    return [
        PaperRecord(source="s", source_id="1", title="RAG Survey", year=2023, citation_count=600, keywords=["rag", "llm"], doi="10.1/a"),
        PaperRecord(source="s", source_id="2", title="RAG Benchmark", year=2024, citation_count=300, keywords=["rag", "eval"], venue="AAAI", doi="10.1/b"),
    ]


def test_build_graph_nodes_and_keyword_edges(tmp_path: Path) -> None:
    service = GraphService(_settings(tmp_path))
    related = [PaperRecord(source="s", source_id="3", title="Active RAG", year=2023, keywords=["rag"], doi="10.1/c")]
    graph = service.build_graph(_papers(), related)

    assert len(graph["nodes"]) == 3  # deduped union of papers + related
    # the two seed papers share keyword "rag" -> at least one edge
    assert any(e["from"] != e["to"] for e in graph["edges"])
    assert graph["legend"]
    # node sizes are integers derived from citation counts
    assert all(isinstance(n["value"], int) for n in graph["nodes"])


def test_write_graph_emits_self_contained_html(tmp_path: Path) -> None:
    service = GraphService(_settings(tmp_path))
    path = service.write_graph("retrieval augmented generation", _papers(), [])
    assert path.exists()
    html = path.read_text(encoding="utf-8")
    assert "vis-network" in html
    assert "Paper" in html and "Pilot" in html
    assert "const data" in html
    assert "retrieval augmented generation" in html


def test_render_html_escapes_topic(tmp_path: Path) -> None:
    service = GraphService(_settings(tmp_path))
    graph = service.build_graph(_papers(), [])
    html = service.render_html(graph, "<script>alert(1)</script>")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
