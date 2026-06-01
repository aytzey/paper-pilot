from __future__ import annotations

import logging
from functools import lru_cache

from mcp.server.fastmcp import FastMCP

from paper_pilot.config import Settings, load_settings
from paper_pilot.models import PaperRecord
from paper_pilot.services.academic import AcademicSearchService
from paper_pilot.services.deep_read import DeepReadingService
from paper_pilot.services.graphing import GraphService
from paper_pilot.services.libgen import LibgenService
from paper_pilot.services.open_access import OpenAccessService
from paper_pilot.services.reporting import ReportService
from paper_pilot.services.scihub import ScihubService
from paper_pilot.services.zotero import ZoteroService

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

mcp = FastMCP(
    "Paper Pilot",
    instructions=(
        "Your AI's research copilot. Searches 6 academic databases, downloads real PDFs, "
        "extracts full text with evidence chunking, renders figures, and syncs everything to Zotero. "
        "When using shadow-library sources, surface provenance and keep them supplemental."
    ),
)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()


def get_academic_service() -> AcademicSearchService:
    return AcademicSearchService(get_settings())


def get_open_access_service() -> OpenAccessService:
    return OpenAccessService(get_settings())


def get_libgen_service() -> LibgenService:
    return LibgenService(get_settings())


def get_report_service() -> ReportService:
    return ReportService(get_settings())


def get_deep_read_service() -> DeepReadingService:
    return DeepReadingService(get_settings())


def get_graph_service() -> GraphService:
    return GraphService(get_settings())


def get_scihub_service() -> ScihubService:
    return ScihubService(get_settings())


def _require_scihub_enabled() -> None:
    if not get_settings().scihub_enabled:
        raise RuntimeError(
            "Sci-Hub is disabled. Set SCIHUB_ENABLED=true to use search_scihub / "
            "download_scihub_paper or the include_scihub fallback. Note: Sci-Hub access "
            "may be unlawful in your jurisdiction; you are responsible for compliance."
        )


def get_zotero_service() -> ZoteroService:
    return ZoteroService(get_settings())


async def _run_research_pipeline(
    topic: str,
    limit_per_source: int,
    related_limit: int,
    download_top_n: int,
    include_libgen: bool,
    libgen_limit: int,
    libgen_download_top_n: int,
    from_year: int | None,
    to_year: int | None,
    open_access_only: bool,
    include_scihub: bool = False,
    scihub_fallback_limit: int = 2,
) -> dict:
    academic = get_academic_service()
    oa_service = get_open_access_service()
    libgen_service = get_libgen_service()

    search_bundle = await academic.search_literature(
        topic=topic,
        limit_per_source=limit_per_source,
        from_year=from_year,
        to_year=to_year,
        open_access_only=open_access_only,
    )
    top_papers = search_bundle.results[: max(limit_per_source * 3, 1)]

    related_bundle = (
        await academic.recommend_similar(
            seed_title=top_papers[0].title,
            seed_doi=top_papers[0].doi,
            limit=related_limit,
            open_access_only=open_access_only,
        )
        if top_papers
        else None
    )
    related_papers = related_bundle.results[:related_limit] if related_bundle else []
    related_warnings = related_bundle.warnings if related_bundle else []

    downloads, download_warnings = await oa_service.download_best_papers(
        topic=topic,
        papers=top_papers,
        max_papers=download_top_n,
    )

    # Sci-Hub fallback for papers without OA PDFs
    scihub_downloads = []
    scihub_warnings: list[str] = []
    if include_scihub and get_settings().scihub_enabled:
        scihub_service = get_scihub_service()
        papers_without_pdf = [p for p in top_papers if p.doi and not p.pdf_url]
        for paper in papers_without_pdf[:scihub_fallback_limit]:
            try:
                doc = await scihub_service.download_paper(doi=paper.doi, topic_hint=topic)
                scihub_downloads.append(doc)
            except Exception as exc:
                scihub_warnings.append(f"Sci-Hub fallback failed ({paper.title}): {exc}")

    libgen_results: list[PaperRecord] = []
    libgen_downloads = []
    libgen_warnings: list[str] = []
    if include_libgen:
        libgen_bundle = await libgen_service.search(
            query=topic,
            search_type="title",
            limit=libgen_limit,
            allowed_extensions=("pdf", "epub"),
        )
        libgen_results = libgen_bundle.results
        libgen_warnings = libgen_bundle.warnings
        for paper in libgen_results[:libgen_download_top_n]:
            try:
                libgen_downloads.append(await libgen_service.download_preview(paper.raw, topic_hint=topic))
            except Exception as exc:
                libgen_warnings.append(f"LibGen preview unavailable ({paper.title}): {exc}")

    all_warnings = search_bundle.warnings + related_warnings + download_warnings + scihub_warnings + libgen_warnings
    return {
        "top_papers": top_papers,
        "related_papers": related_papers,
        "downloads": downloads,
        "scihub_downloads": scihub_downloads,
        "libgen_results": libgen_results,
        "libgen_downloads": libgen_downloads,
        "warnings": all_warnings,
    }


def _collect_remediation(zotero_status: dict) -> list[str]:
    """Gather actionable remediation hints from the Zotero health status."""
    hints: list[str] = []
    if zotero_status.get("local_api_remediation"):
        hints.append(zotero_status["local_api_remediation"])
    if zotero_status.get("bridge_remediation"):
        hints.append(zotero_status["bridge_remediation"])
    return hints


@mcp.tool()
def healthcheck() -> dict:
    """Return current configuration summary and enabled integrations."""
    settings = get_settings()
    zotero_status = get_zotero_service().status()
    return {
        "status": "ok",
        "data_dir": str(settings.data_dir),
        "cache_dir": str(settings.cache_dir),
        "deep_reads_dir": str(settings.deep_reads_dir),
        "render_dir": str(settings.render_dir),
        "openalex_email_configured": bool(settings.openalex_email),
        "unpaywall_email_configured": bool(settings.unpaywall_email),
        "semantic_scholar_api_key_configured": bool(settings.semantic_scholar_api_key),
        "zotero": zotero_status,
        "proxy_configured": settings.proxy_configured,
        "ssl_cert_file_configured": bool(settings.ssl_cert_file),
        "libgen_mirrors": list(settings.libgen_mirrors),
        "scihub_enabled": settings.scihub_enabled,
        "scihub_mirrors": list(settings.scihub_mirrors),
        "note": (
            "Legal OA sources honor trust_env proxies and optional SSL_CERT_FILE; "
            "LibGen and Sci-Hub remain best-effort only. Local Zotero mode uses localhost:23119 "
            "and optionally a zoty-bridge compatible /execute plugin for full writes."
        ),
        "remediation": _collect_remediation(zotero_status),
    }


@mcp.tool()
async def search_literature(
    topic: str,
    limit_per_source: int = 5,
    from_year: int | None = None,
    to_year: int | None = None,
    open_access_only: bool = True,
) -> dict:
    """Search Semantic Scholar, OpenAlex, Europe PMC, arXiv, Crossref, and DOAJ for a topic."""
    bundle = await get_academic_service().search_literature(
        topic=topic,
        limit_per_source=limit_per_source,
        from_year=from_year,
        to_year=to_year,
        open_access_only=open_access_only,
    )
    return {
        "topic": topic,
        "warnings": bundle.warnings,
        "results": [paper.to_dict() for paper in bundle.results],
    }


@mcp.tool()
async def find_similar_papers(
    seed_title: str,
    seed_doi: str | None = None,
    limit: int = 8,
    open_access_only: bool = True,
) -> dict:
    """Find similar papers starting from a seed paper title or DOI."""
    bundle = await get_academic_service().recommend_similar(
        seed_title=seed_title,
        seed_doi=seed_doi,
        limit=limit,
        open_access_only=open_access_only,
    )
    return {
        "seed_title": seed_title,
        "warnings": bundle.warnings,
        "results": [paper.to_dict() for paper in bundle.results[:limit]],
    }


@mcp.tool()
async def graph_topic(
    topic: str,
    limit_per_source: int = 5,
    related_limit: int = 8,
    from_year: int | None = None,
    to_year: int | None = None,
    open_access_only: bool = True,
) -> dict:
    """Search a topic and render an interactive citation/relatedness graph as a self-contained HTML file.

    Nodes are papers (size scales with citation count, color by year); edges connect papers that
    share keywords or a venue, plus similarity links radiating from the top result. Returns the
    local HTML path. Open it in a browser to explore or screenshot the landscape."""
    academic = get_academic_service()
    bundle = await academic.search_literature(
        topic=topic,
        limit_per_source=limit_per_source,
        from_year=from_year,
        to_year=to_year,
        open_access_only=open_access_only,
    )
    papers = bundle.results
    related_bundle = (
        await academic.recommend_similar(
            seed_title=papers[0].title,
            seed_doi=papers[0].doi,
            limit=related_limit,
            open_access_only=open_access_only,
        )
        if papers
        else None
    )
    related = related_bundle.results[:related_limit] if related_bundle else []
    graph_path = get_graph_service().write_graph(topic, papers, related)
    return {
        "topic": topic,
        "graph_path": str(graph_path),
        "node_count": len({p.dedupe_key() for p in [*papers, *related]}),
        "paper_count": len(papers),
        "related_count": len(related),
        "warnings": bundle.warnings,
    }


@mcp.tool()
async def inspect_open_access_pdf(pdf_url: str, filename_hint: str = "paper") -> dict:
    """Download an open-access PDF and return a local preview."""
    document = await get_open_access_service().inspect_remote_pdf(pdf_url, filename_hint)
    return document.to_dict()


@mcp.tool()
def extract_local_pdf_text(
    pdf_path: str,
    title_hint: str | None = None,
    research_question: str | None = None,
    chunk_size_chars: int = 5000,
    chunk_overlap_chars: int = 600,
    top_chunks: int = 5,
) -> dict:
    """Extract full text from a local PDF, save a text sidecar, and return top matching chunks."""
    artifact = get_deep_read_service().extract_local_pdf(
        pdf_path=pdf_path,
        title_hint=title_hint,
        research_question=research_question,
        chunk_size_chars=chunk_size_chars,
        chunk_overlap_chars=chunk_overlap_chars,
    )
    return artifact.to_dict(top_chunks=top_chunks)


@mcp.tool()
def render_pdf_pages(
    pdf_path: str,
    page_numbers: list[int],
    scale: float = 2.0,
) -> dict:
    """Render selected PDF pages to PNG so the agent can inspect figures, tables, and layout."""
    image_paths = get_deep_read_service().render_pages(pdf_path=pdf_path, page_numbers=page_numbers, scale=scale)
    return {
        "pdf_path": str(pdf_path),
        "images": [str(path) for path in image_paths],
    }


@mcp.tool()
async def search_libgen(
    query: str,
    search_type: str = "title",
    limit: int = 10,
    allowed_extensions: list[str] | None = None,
) -> dict:
    """Search LibGen mirrors for supplemental research material."""
    bundle = await get_libgen_service().search(
        query=query,
        search_type=search_type,
        limit=limit,
        allowed_extensions=tuple(allowed_extensions or ("pdf", "epub")),
    )
    return {
        "query": query,
        "warnings": bundle.warnings,
        "results": [paper.to_dict() for paper in bundle.results],
    }


@mcp.tool()
async def inspect_libgen_item(
    mirror_1: str,
    title: str,
    author: str = "",
    year: int | None = None,
    extension: str = "pdf",
    publisher: str | None = None,
    size: str | None = None,
) -> dict:
    """Resolve LibGen mirror links, download a PDF when possible, and return a preview."""
    item = {
        "Mirror_1": mirror_1,
        "Title": title,
        "Author": author,
        "Year": str(year) if year else "",
        "Extension": extension,
        "Publisher": publisher or "",
        "Size": size or "",
    }
    document = await get_libgen_service().download_preview(item, topic_hint=title)
    return document.to_dict()


@mcp.tool()
async def search_scihub(
    query: str,
    search_type: str = "doi",
    limit: int = 10,
    check_availability: bool = False,
) -> dict:
    """Search Sci-Hub for papers by DOI, title, or keyword. Use search_type='doi', 'title', or 'keyword'.
    Requires SCIHUB_ENABLED=true."""
    _require_scihub_enabled()
    service = get_scihub_service()
    if search_type == "doi":
        result = await service.fetch_by_doi(query)
        return {"search_type": "doi", "query": query, "result": result}
    elif search_type == "title":
        result = await service.search_by_title(query)
        return {"search_type": "title", "query": query, "result": result}
    else:
        results = await service.search_by_keyword(query, limit=limit, check_scihub=check_availability)
        return {"search_type": "keyword", "query": query, "results": results}


@mcp.tool()
async def download_scihub_paper(
    doi: str,
    topic_hint: str = "scihub",
) -> dict:
    """Download a paper PDF via Sci-Hub using its DOI. Returns local path and text preview.
    Requires SCIHUB_ENABLED=true."""
    _require_scihub_enabled()
    service = get_scihub_service()
    document = await service.download_paper(doi=doi, topic_hint=topic_hint)
    return document.to_dict()


@mcp.tool()
async def list_zotero_collections(query: str | None = None) -> dict:
    """List Zotero collections visible to the configured web or local Zotero integration."""
    collections = await get_zotero_service().list_collections(query=query)
    return {"collections": collections}


@mcp.tool()
async def research_topic(
    topic: str,
    limit_per_source: int = 4,
    related_limit: int = 6,
    download_top_n: int = 3,
    include_libgen: bool = False,
    libgen_limit: int = 5,
    libgen_download_top_n: int = 1,
    include_scihub: bool = False,
    scihub_fallback_limit: int = 2,
    from_year: int | None = None,
    to_year: int | None = None,
    open_access_only: bool = True,
    write_to_zotero: bool = False,
    existing_collection_key: str | None = None,
    existing_collection_name: str | None = None,
    create_collection_name: str | None = None,
    attach_pdfs: bool = True,
    write_graph: bool = False,
) -> dict:
    """Run the end-to-end research workflow and optionally sync the result into Zotero.
    Set include_scihub=True to use Sci-Hub as a fallback for papers without open-access PDFs.
    Set write_graph=True to also render an interactive citation graph HTML (path returned as graph_path)."""
    report_service = get_report_service()
    pipeline = await _run_research_pipeline(
        topic=topic,
        limit_per_source=limit_per_source,
        related_limit=related_limit,
        download_top_n=download_top_n,
        include_libgen=include_libgen,
        libgen_limit=libgen_limit,
        libgen_download_top_n=libgen_download_top_n,
        from_year=from_year,
        to_year=to_year,
        open_access_only=open_access_only,
        include_scihub=include_scihub,
        scihub_fallback_limit=scihub_fallback_limit,
    )
    top_papers = pipeline["top_papers"]
    related_papers = pipeline["related_papers"]
    downloads = pipeline["downloads"]
    scihub_downloads = pipeline["scihub_downloads"]
    libgen_results = pipeline["libgen_results"]
    libgen_downloads = pipeline["libgen_downloads"]
    all_warnings = pipeline["warnings"]

    zotero_collection_key = None
    zotero_sync = None
    if write_to_zotero:
        target_name = create_collection_name or existing_collection_name or f"Research - {topic}"
        collection = await get_zotero_service().resolve_collection(
            existing_collection_key=existing_collection_key,
            existing_collection_name=existing_collection_name,
            create_collection_name=target_name,
        )
        zotero_collection_key = collection["key"]

    markdown = report_service.render_markdown(
        topic=topic,
        papers=top_papers,
        related=related_papers,
        downloads=downloads,
        warnings=all_warnings,
        supplemental_records=libgen_results,
        supplemental_downloads=libgen_downloads,
        zotero_collection_key=zotero_collection_key,
    )
    report_path = report_service.write_report(topic, markdown)

    graph_path = None
    if write_graph:
        graph_path = str(get_graph_service().write_graph(topic, top_papers, related_papers))

    if write_to_zotero and zotero_collection_key:
        papers_for_zotero: list[PaperRecord] = top_papers + [paper for paper in related_papers if paper.dedupe_key() not in {top.dedupe_key() for top in top_papers}]
        zotero_sync = await get_zotero_service().sync_topic(
            collection_key=zotero_collection_key,
            papers=papers_for_zotero,
            downloads=downloads,
            report_markdown=markdown,
            topic=topic,
            attach_pdfs=attach_pdfs,
        )

    return {
        "topic": topic,
        "report_path": str(report_path),
        "graph_path": graph_path,
        "warnings": all_warnings,
        "top_papers": [paper.to_dict() for paper in top_papers],
        "related_papers": [paper.to_dict() for paper in related_papers],
        "downloads": [document.to_dict() for document in downloads],
        "scihub_downloads": [document.to_dict() for document in scihub_downloads],
        "libgen_results": [paper.to_dict() for paper in libgen_results],
        "libgen_downloads": [document.to_dict() for document in libgen_downloads],
        "zotero": zotero_sync,
        "report_markdown": markdown,
    }


@mcp.tool()
async def deep_read_topic(
    topic: str,
    research_question: str | None = None,
    limit_per_source: int = 4,
    related_limit: int = 6,
    download_top_n: int = 4,
    top_chunks_per_paper: int = 5,
    chunk_size_chars: int = 5000,
    chunk_overlap_chars: int = 600,
    include_scihub: bool = False,
    scihub_fallback_limit: int = 2,
    from_year: int | None = None,
    to_year: int | None = None,
    open_access_only: bool = True,
    write_to_zotero: bool = False,
    existing_collection_key: str | None = None,
    existing_collection_name: str | None = None,
    create_collection_name: str | None = None,
    attach_pdfs: bool = True,
    write_graph: bool = False,
) -> dict:
    """Search, download, extract full text, and return evidence chunks plus local PDF paths for direct inspection.
    Set include_scihub=True to use Sci-Hub as a fallback for papers without open-access PDFs.
    Set write_graph=True to also render an interactive citation graph HTML (path returned as graph_path)."""
    question = research_question or topic
    pipeline = await _run_research_pipeline(
        topic=topic,
        limit_per_source=limit_per_source,
        related_limit=related_limit,
        download_top_n=download_top_n,
        include_libgen=False,
        libgen_limit=0,
        libgen_download_top_n=0,
        from_year=from_year,
        to_year=to_year,
        open_access_only=open_access_only,
        include_scihub=include_scihub,
        scihub_fallback_limit=scihub_fallback_limit,
    )
    top_papers = pipeline["top_papers"]
    related_papers = pipeline["related_papers"]
    downloads = pipeline["downloads"]
    scihub_downloads = pipeline["scihub_downloads"]
    all_downloads = downloads + scihub_downloads
    all_warnings = pipeline["warnings"]
    if not all_downloads:
        all_warnings = all_warnings + [
            "No downloadable PDF found for deep read; returning bibliographic results only.",
        ]

    deep_read_service = get_deep_read_service()
    artifacts = [
        deep_read_service.extract_document(
            document,
            research_question=question,
            chunk_size_chars=chunk_size_chars,
            chunk_overlap_chars=chunk_overlap_chars,
        )
        for document in all_downloads
    ]

    report_service = get_report_service()
    zotero_collection_key = None
    if write_to_zotero:
        target_name = create_collection_name or existing_collection_name or f"Research - {topic}"
        collection = await get_zotero_service().resolve_collection(
            existing_collection_key=existing_collection_key,
            existing_collection_name=existing_collection_name,
            create_collection_name=target_name,
        )
        zotero_collection_key = collection["key"]
    markdown = report_service.render_deep_read_markdown(
        topic=topic,
        research_question=question,
        papers=top_papers,
        deep_reads=artifacts,
        warnings=all_warnings,
        related=related_papers,
        zotero_collection_key=zotero_collection_key,
    )
    report_path = report_service.write_report(f"{topic}-deep-read", markdown)

    graph_path = None
    if write_graph:
        graph_path = str(get_graph_service().write_graph(topic, top_papers, related_papers))

    zotero_sync = None
    if write_to_zotero and zotero_collection_key:
        papers_for_zotero: list[PaperRecord] = top_papers + [
            paper for paper in related_papers if paper.dedupe_key() not in {top.dedupe_key() for top in top_papers}
        ]
        zotero_sync = await get_zotero_service().sync_topic(
            collection_key=zotero_collection_key,
            papers=papers_for_zotero,
            downloads=downloads,
            report_markdown=markdown,
            topic=topic,
            attach_pdfs=attach_pdfs,
        )

    return {
        "topic": topic,
        "research_question": question,
        "report_path": str(report_path),
        "graph_path": graph_path,
        "warnings": all_warnings,
        "top_papers": [paper.to_dict() for paper in top_papers],
        "related_papers": [paper.to_dict() for paper in related_papers],
        "downloads": [document.to_dict() for document in downloads],
        "scihub_downloads": [document.to_dict() for document in scihub_downloads],
        "deep_reads": [artifact.to_dict(top_chunks=top_chunks_per_paper) for artifact in artifacts],
        "zotero": zotero_sync,
        "report_markdown": markdown,
        "agent_notes": [
            "For figures and tables, open the PDF directly via deep_reads[*].pdf_path.",
            "For text-based comparison, use deep_reads[*].text_path and chunk_manifest_path.",
        ],
    }
