from __future__ import annotations

import json
import re
from pathlib import Path

try:
    import fitz
except ModuleNotFoundError:  # pragma: no cover - compatibility fallback
    import pymupdf as fitz

from paper_pilot.config import Settings
from paper_pilot.models import DeepReadArtifact, DownloadedDocument, PaperRecord, TextChunk, slugify

_MAX_RENDER_SCALE = 8.0

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "their",
    "this",
    "to",
    "with",
}


class DeepReadingService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.settings.deep_reads_dir.mkdir(parents=True, exist_ok=True)
        self.settings.render_dir.mkdir(parents=True, exist_ok=True)

    def extract_document(
        self,
        document: DownloadedDocument,
        research_question: str | None = None,
        chunk_size_chars: int = 5000,
        chunk_overlap_chars: int = 600,
    ) -> DeepReadArtifact:
        if chunk_size_chars <= 0:
            raise ValueError("chunk_size_chars must be positive.")
        if chunk_overlap_chars < 0:
            raise ValueError("chunk_overlap_chars must not be negative.")
        if chunk_overlap_chars >= chunk_size_chars:
            raise ValueError("chunk_overlap_chars must be less than chunk_size_chars.")

        pdf_path = document.path.expanduser().resolve()
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        page_blocks, page_count, preview = self._read_pdf_pages(pdf_path)
        full_text, page_spans = self._join_page_blocks(page_blocks)
        chunks = self._build_chunks(
            full_text=full_text,
            page_spans=page_spans,
            query=research_question or document.paper.title,
            chunk_size_chars=chunk_size_chars,
            chunk_overlap_chars=chunk_overlap_chars,
        )

        stem = pdf_path.stem
        text_path = self.settings.deep_reads_dir / f"{stem}.txt"
        manifest_path = self.settings.deep_reads_dir / f"{stem}.chunks.json"
        text_path.write_text(full_text, encoding="utf-8")
        manifest_path.write_text(
            json.dumps(
                {
                    "paper": document.paper.to_dict(),
                    "pdf_path": str(pdf_path),
                    "text_path": str(text_path),
                    "page_count": page_count,
                    "full_text_char_count": len(full_text),
                    "chunks": [chunk.to_dict() for chunk in chunks],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        return DeepReadArtifact(
            paper=document.paper,
            pdf_path=pdf_path,
            text_path=text_path,
            chunk_manifest_path=manifest_path,
            page_count=page_count,
            full_text_char_count=len(full_text),
            extracted_preview=preview,
            chunks=chunks,
        )

    def extract_local_pdf(
        self,
        pdf_path: str,
        title_hint: str | None = None,
        research_question: str | None = None,
        chunk_size_chars: int = 5000,
        chunk_overlap_chars: int = 600,
    ) -> DeepReadArtifact:
        path = Path(pdf_path).expanduser().resolve()
        paper = PaperRecord(
            source="local_pdf",
            source_id=str(path),
            title=title_hint or path.stem,
            url=str(path),
            pdf_url=str(path),
            is_open_access=True,
        )
        preview_doc = DownloadedDocument(
            paper=paper,
            path=path,
            page_count=0,
            extracted_preview="",
        )
        return self.extract_document(
            preview_doc,
            research_question=research_question,
            chunk_size_chars=chunk_size_chars,
            chunk_overlap_chars=chunk_overlap_chars,
        )

    def analyze_documents(
        self,
        documents: list[DownloadedDocument],
        research_question: str,
        top_chunks_per_document: int = 5,
        chunk_size_chars: int = 5000,
        chunk_overlap_chars: int = 600,
    ) -> list[DeepReadArtifact]:
        artifacts = [
            self.extract_document(
                document,
                research_question=research_question,
                chunk_size_chars=chunk_size_chars,
                chunk_overlap_chars=chunk_overlap_chars,
            )
            for document in documents
        ]
        return sorted(
            artifacts,
            key=lambda artifact: max((chunk.score or 0.0 for chunk in artifact.chunks), default=0.0),
            reverse=True,
        )[: len(documents)]

    def render_pages(
        self,
        pdf_path: str,
        page_numbers: list[int],
        scale: float = 2.0,
    ) -> list[Path]:
        path = Path(pdf_path).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"PDF not found: {path}")
        if scale <= 0:
            raise ValueError("scale must be positive.")
        if scale > _MAX_RENDER_SCALE:
            raise ValueError(f"scale must be <= {_MAX_RENDER_SCALE} to avoid excessive memory use.")

        renders: list[Path] = []
        with fitz.open(path) as document:
            for page_number in page_numbers:
                if page_number < 1 or page_number > document.page_count:
                    raise ValueError(f"Invalid page number: {page_number}")
                page = document.load_page(page_number - 1)
                pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                destination = self.settings.render_dir / f"{slugify(path.stem, 60)}-page-{page_number:03d}.png"
                pixmap.save(destination)
                renders.append(destination)
        return renders

    def _read_pdf_pages(self, pdf_path: Path) -> tuple[list[tuple[int, str]], int, str]:
        with fitz.open(pdf_path) as document:
            blocks: list[tuple[int, str]] = []
            preview_parts: list[str] = []
            for page_index in range(document.page_count):
                text = document.load_page(page_index).get_text("text").strip()
                blocks.append((page_index + 1, text))
                if page_index < 5 and text:
                    preview_parts.append(text)
            preview = "\n\n".join(preview_parts).strip()[:12000]
            return blocks, document.page_count, preview

    def _join_page_blocks(self, page_blocks: list[tuple[int, str]]) -> tuple[str, list[dict[str, int]]]:
        parts: list[str] = []
        spans: list[dict[str, int]] = []
        cursor = 0
        for page_number, text in page_blocks:
            page_text = f"[Page {page_number}]\n{text.strip()}\n\n"
            start = cursor
            cursor += len(page_text)
            parts.append(page_text)
            spans.append({"page": page_number, "start": start, "end": cursor})
        full_text = "".join(parts)
        return full_text, spans

    def _build_chunks(
        self,
        full_text: str,
        page_spans: list[dict[str, int]],
        query: str,
        chunk_size_chars: int,
        chunk_overlap_chars: int,
    ) -> list[TextChunk]:
        if not full_text.strip():
            return []
        step = max(chunk_size_chars - chunk_overlap_chars, 1)
        chunks: list[TextChunk] = []
        chunk_index = 0
        for start in range(0, len(full_text), step):
            end = min(len(full_text), start + chunk_size_chars)
            text = full_text[start:end].strip()
            if not text:
                if end >= len(full_text):
                    break
                continue
            pages = [span["page"] for span in page_spans if span["end"] > start and span["start"] < end]
            score, keyword_hits = self._score_chunk(text, query)
            chunks.append(
                TextChunk(
                    chunk_index=chunk_index,
                    start_page=pages[0] if pages else 1,
                    end_page=pages[-1] if pages else 1,
                    text=text,
                    score=round(score, 3),
                    keyword_hits=keyword_hits,
                )
            )
            chunk_index += 1
            if end >= len(full_text):
                break
        return chunks

    def _score_chunk(self, text: str, query: str) -> tuple[float, list[str]]:
        if not query.strip():
            return 0.0, []

        query_lower = query.casefold().strip()
        text_lower = text.casefold()
        keywords = [
            token
            for token in re.findall(r"\w[\w-]+", query_lower, flags=re.UNICODE)
            if token not in _STOPWORDS
        ]
        score = 0.0
        hits: list[str] = []

        if query_lower and query_lower in text_lower:
            score += 8.0

        for keyword in keywords:
            count = text_lower.count(keyword)
            if not count:
                continue
            hits.append(keyword)
            score += min(count, 6) * (1.0 + min(len(keyword), 12) / 12.0)

        for left, right in zip(keywords, keywords[1:], strict=False):
            phrase = f"{left} {right}"
            if phrase in text_lower:
                score += 2.5

        if hits and any(marker in text_lower for marker in ("figure", "table", "results", "method", "dataset", "benchmark")):
            score += 1.5

        return score, sorted(set(hits))
