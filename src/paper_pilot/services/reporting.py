from __future__ import annotations

import html
from pathlib import Path

from paper_pilot.config import Settings
from paper_pilot.models import DeepReadArtifact, DownloadedDocument, PaperRecord, slugify, utc_timestamp


class ReportService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def render_markdown(
        self,
        topic: str,
        papers: list[PaperRecord],
        related: list[PaperRecord],
        downloads: list[DownloadedDocument],
        warnings: list[str],
        supplemental_records: list[PaperRecord] | None = None,
        supplemental_downloads: list[DownloadedDocument] | None = None,
        zotero_collection_key: str | None = None,
    ) -> str:
        supplemental_records = supplemental_records or []
        supplemental_downloads = supplemental_downloads or []
        lines: list[str] = [f"# Research Report: {topic}", ""]
        if zotero_collection_key:
            lines.extend([f"- Zotero collection: `{zotero_collection_key}`", ""])

        lines.extend(
            [
                "## Short Take",
                "",
                f"- Unique records collected: {len(papers)}",
                f"- Similar work suggestions: {len(related)}",
                f"- Open-access PDFs downloaded: {len(downloads)}",
                f"- LibGen supplemental results: {len(supplemental_records)}",
                "",
            ]
        )

        if warnings:
            lines.append("## Warnings")
            lines.append("")
            for warning in warnings:
                lines.append(f"- {warning}")
            lines.append("")

        lines.append("## Priority Papers")
        lines.append("")
        for index, paper in enumerate(papers[:10], start=1):
            citation_text = f", citations={paper.citation_count}" if paper.citation_count is not None else ""
            lines.append(
                f"{index}. **{paper.title}** ({paper.year or 'n/a'}) - "
                f"{', '.join(paper.authors[:4]) or 'unknown'}; {paper.source}{citation_text}"
            )
            if paper.venue:
                lines.append(f"   Venue: {paper.venue}")
            if paper.doi:
                lines.append(f"   DOI: `{paper.doi}`")
            if paper.url:
                lines.append(f"   URL: {paper.url}")
            if paper.abstract:
                lines.append(f"   Abstract: {paper.abstract[:500]}{'...' if len(paper.abstract) > 500 else ''}")
        lines.append("")

        if related:
            lines.append("## Similar Work")
            lines.append("")
            for paper in related[:10]:
                lines.append(f"- **{paper.title}** ({paper.year or 'n/a'}) - {', '.join(paper.authors[:3]) or 'unknown'}")
                if paper.url:
                    lines.append(f"  URL: {paper.url}")
            lines.append("")

        if downloads:
            lines.append("## Open Access Full-Text Notes")
            lines.append("")
            for document in downloads:
                lines.append(f"### {document.paper.title}")
                lines.append("")
                lines.append(f"- Saved PDF: `{document.path}`")
                lines.append(f"- Page count: {document.page_count}")
                lines.append("- Preview:")
                lines.append("")
                lines.append("```text")
                lines.append(document.extracted_preview[:2000])
                lines.append("```")
                lines.append("")

        if supplemental_records:
            lines.append("## LibGen Supplemental Material")
            lines.append("")
            lines.append("This section contains shadow library results; accuracy and copyright status should be verified independently.")
            lines.append("")
            for index, record in enumerate(supplemental_records[:10], start=1):
                item = record.raw
                extension = item.get("Extension") or "unknown"
                size = item.get("Size") or "unknown"
                lines.append(
                    f"{index}. **{record.title}** ({record.year or 'n/a'}) - "
                    f"{', '.join(record.authors[:4]) or 'unknown'}; {extension}, {size}"
                )
                if record.url:
                    lines.append(f"   Mirror: {record.url}")
            lines.append("")

        if supplemental_downloads:
            lines.append("## LibGen PDF Notes")
            lines.append("")
            for document in supplemental_downloads:
                lines.append(f"### {document.paper.title}")
                lines.append("")
                lines.append(f"- Saved file: `{document.path}`")
                lines.append(f"- Page count: {document.page_count}")
                lines.append("")
                lines.append("```text")
                lines.append(document.extracted_preview[:2000])
                lines.append("```")
                lines.append("")

        lines.extend(
            [
                "## Next Steps",
                "",
                "- Compare the methods sections of the top 3-5 papers.",
                "- Extract shared datasets, benchmarks, and evaluation metrics.",
                "- Manually verify records missing DOI or venue information.",
                "- Organize Zotero with topic tags and sub-collections.",
                "",
            ]
        )
        return "\n".join(lines).strip() + "\n"

    def render_deep_read_markdown(
        self,
        topic: str,
        research_question: str,
        papers: list[PaperRecord],
        deep_reads: list[DeepReadArtifact],
        warnings: list[str],
        related: list[PaperRecord] | None = None,
        zotero_collection_key: str | None = None,
    ) -> str:
        related = related or []
        lines: list[str] = [f"# Deep Read Report: {topic}", ""]
        lines.extend(
            [
                f"- **Research question:** {research_question}",
                f"- **Deep-read papers:** {len(deep_reads)}",
                f"- **Total candidate papers:** {len(papers)}",
            ]
        )
        if zotero_collection_key:
            lines.append(f"- **Zotero collection:** `{zotero_collection_key}`")
        lines.append("")

        if warnings:
            lines.extend(["## Warnings", ""])
            for warning in warnings:
                lines.append(f"- {warning}")
            lines.append("")

        # Screenshot-worthy at-a-glance comparison of everything that was read.
        if deep_reads:
            lines.extend(
                [
                    "## Comparison",
                    "",
                    "| # | Paper | Year | Venue | Citations | Pages | Top evidence |",
                    "|---|-------|------|-------|-----------|-------|--------------|",
                ]
            )
            for index, artifact in enumerate(deep_reads, start=1):
                paper = artifact.paper
                top_pages = self._top_evidence_pages(artifact)
                citations = paper.citation_count if paper.citation_count is not None else "-"
                lines.append(
                    f"| {index} | {self._md_cell(paper.title, 70)} | {paper.year or '-'} | "
                    f"{self._md_cell(paper.venue or '-', 28)} | {citations} | "
                    f"{artifact.page_count} | {top_pages or '-'} |"
                )
            lines.append("")
            lines.append(
                "> Fill in Method / Key finding / Limitation per paper from the Evidence "
                "excerpts below, citing the page ranges shown."
            )
            lines.append("")

        lines.extend(["## Reading Pack", ""])
        for artifact in deep_reads:
            lines.append(f"- **{artifact.paper.title}**")
            lines.append(f"  PDF: `{artifact.pdf_path}`")
            lines.append(f"  Full text: `{artifact.text_path}`")
            lines.append(f"  Chunk manifest: `{artifact.chunk_manifest_path}`")
        lines.append("")

        lines.extend(["## Evidence", ""])
        for artifact in deep_reads:
            lines.append(f"### {artifact.paper.title}")
            lines.append("")
            ranked = sorted(
                artifact.chunks,
                key=lambda chunk: (chunk.score or 0.0, -chunk.chunk_index),
                reverse=True,
            )[:5]
            for chunk in ranked:
                matched = ", ".join(chunk.keyword_hits)
                header = f"**Pages {chunk.start_page}–{chunk.end_page}**"
                if matched:
                    header += f" · matched: {matched}"
                lines.append(header)
                lines.append("")
                lines.append("```text")
                excerpt = chunk.text[:1600]
                if len(chunk.text) > 1600:
                    excerpt += "..."
                lines.append(excerpt)
                lines.append("```")
                lines.append("")

        if related:
            lines.extend(["## Similar Work", ""])
            for paper in related[:10]:
                lines.append(f"- **{paper.title}** ({paper.year or 'n/a'})")
                if paper.url:
                    lines.append(f"  URL: {paper.url}")
            lines.append("")

        lines.extend(
            [
                "## How to use this report",
                "",
                "- For figures, tables, and layout review, open the PDF directly via its path.",
                "- For text-based comparison, use `text_path` and `chunk_manifest_path` "
                "(relevance scores live in the manifest JSON).",
                "- Cite the page range shown above each excerpt when extracting evidence.",
                "",
            ]
        )
        return "\n".join(lines).strip() + "\n"

    @staticmethod
    def _top_evidence_pages(artifact: DeepReadArtifact, limit: int = 3) -> str:
        ranked = sorted(
            artifact.chunks,
            key=lambda chunk: (chunk.score or 0.0, -chunk.chunk_index),
            reverse=True,
        )
        pages: list[int] = []
        for chunk in ranked:
            if (chunk.score or 0.0) <= 0:
                continue
            if chunk.start_page not in pages:
                pages.append(chunk.start_page)
            if len(pages) >= limit:
                break
        return ", ".join(f"p.{page}" for page in pages)

    @staticmethod
    def _md_cell(value: str, max_length: int = 60) -> str:
        cleaned = " ".join((value or "").split()).replace("|", "\\|")
        if len(cleaned) > max_length:
            cleaned = cleaned[: max_length - 1].rstrip() + "…"
        return cleaned or "-"

    def write_report(self, topic: str, markdown: str) -> Path:
        filename = f"{slugify(topic)}-{utc_timestamp()}.md"
        destination = self.settings.data_dir / "reports" / filename
        destination.write_text(markdown, encoding="utf-8")
        return destination

    @staticmethod
    def markdown_to_note_html(markdown: str) -> str:
        escaped = html.escape(markdown)
        return f"<pre style=\"white-space: pre-wrap;\">{escaped}</pre>"
