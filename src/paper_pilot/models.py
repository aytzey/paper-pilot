from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip().lower()
    cleaned = re.sub(r"^doi:\s*", "", cleaned)
    cleaned = re.sub(r"^(?:https?://)?(dx\.)?doi\.org/", "", cleaned)
    return cleaned or None


def normalize_arxiv_id(value: str | None) -> str | None:
    """Extract a version-stripped arXiv id from a URL, id, or 'arXiv:...' string.

    Collapses abs/pdf/versioned forms so the same paper dedupes (e.g.
    arxiv.org/abs/2301.12345v2 and arxiv.org/pdf/2301.12345 -> '2301.12345').
    """
    if not value:
        return None
    # Modern ids: 2301.12345 (optionally vN), with optional category prefix.
    match = re.search(r"(\d{4}\.\d{4,5})(?:v\d+)?", value)
    if match:
        return match.group(1)
    # Legacy ids: hep-th/9901001, math.AG/0309136 (optionally vN).
    match = re.search(r"([a-z][a-z\-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?", value)
    if match:
        return match.group(1)
    return None


_RANK_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "into",
        "is", "it", "of", "on", "or", "that", "the", "their", "this", "to", "with",
        "using", "based", "via", "toward", "towards", "study", "approach", "review",
    }
)


def _content_tokens(text: str | None) -> list[str]:
    """Lowercased content words (>=2 chars) with common stopwords removed."""
    return [
        token
        for token in re.findall(r"[a-z0-9][a-z0-9-]+", (text or "").lower())
        if token not in _RANK_STOPWORDS
    ]


def normalize_title(value: str | None) -> str:
    if not value:
        return ""
    cleaned = re.sub(r"[^a-z0-9]+", " ", value.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def slugify(value: str, max_length: int = 80) -> str:
    slug = normalize_title(value).replace(" ", "-")
    slug = slug.strip("-") or "untitled"
    return slug[:max_length].rstrip("-")


def utc_timestamp() -> str:
    return datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")


@dataclass(slots=True)
class PaperRecord:
    source: str
    source_id: str
    title: str
    authors: list[str] = field(default_factory=list)
    abstract: str | None = None
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    url: str | None = None
    pdf_url: str | None = None
    citation_count: int | None = None
    is_open_access: bool = False
    keywords: list[str] = field(default_factory=list)
    related_score: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def dedupe_key(self) -> str:
        doi = normalize_doi(self.doi)
        if doi:
            return f"doi:{doi}"
        if self.source == "arxiv" or (self.url and "arxiv.org" in self.url.lower()):
            arxiv_id = normalize_arxiv_id(self.url) or normalize_arxiv_id(self.source_id)
            if arxiv_id:
                return f"arxiv:{arxiv_id}"
        return f"title:{normalize_title(self.title)}:{self.year or 'na'}"

    def rank_score(self) -> float:
        citation_score = math.log1p(max(self.citation_count or 0, 0)) * 4.0
        recency_score = 0.0 if not self.year else max(min(self.year - 2000, 30), 0) * 0.2
        oa_score = 2.0 if (self.is_open_access or self.pdf_url) else 0.0
        related_score = self.related_score or 0.0
        abstract_score = min(len(self.abstract or "") / 700.0, 1.5)
        return citation_score + recency_score + oa_score + related_score + abstract_score

    def relevance_score(self, query: str | None) -> float:
        """Lexical relevance of this paper to the query topic (title + abstract coverage)."""
        tokens = list(dict.fromkeys(_content_tokens(query)))
        if not tokens:
            return 0.0
        title_tokens = set(normalize_title(self.title).split())
        abstract = (self.abstract or "").lower()
        title_coverage = sum(1 for token in tokens if token in title_tokens) / len(tokens)
        abstract_coverage = sum(1 for token in tokens if token in abstract) / len(tokens)
        score = title_coverage * 8.0 + abstract_coverage * 3.0
        normalized_query = normalize_title(query)
        if normalized_query and normalized_query in normalize_title(self.title):
            score += 3.0  # exact topic phrase in the title
        return score

    def quality_score(self, query: str | None = None) -> float:
        """Combined ranking: topic relevance dominates, then citations / recency / OA."""
        return self.relevance_score(query) * 4.0 + self.rank_score()

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "source_id": self.source_id,
            "title": self.title,
            "authors": self.authors,
            "abstract": self.abstract,
            "year": self.year,
            "venue": self.venue,
            "doi": self.doi,
            "url": self.url,
            "pdf_url": self.pdf_url,
            "citation_count": self.citation_count,
            "is_open_access": self.is_open_access,
            "keywords": self.keywords,
            "related_score": self.related_score,
            "raw": self.raw,
        }


@dataclass(slots=True)
class DownloadedDocument:
    paper: PaperRecord
    path: Path
    page_count: int
    extracted_preview: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper": self.paper.to_dict(),
            "path": str(self.path),
            "pdf_path": str(self.path),
            "page_count": self.page_count,
            "extracted_preview": self.extracted_preview,
        }


@dataclass(slots=True)
class TextChunk:
    chunk_index: int
    start_page: int
    end_page: int
    text: str
    score: float | None = None
    keyword_hits: list[str] = field(default_factory=list)

    def to_dict(self, max_chars: int = 1200) -> dict[str, Any]:
        excerpt = self.text[:max_chars]
        if len(self.text) > max_chars:
            excerpt += "..."
        return {
            "chunk_index": self.chunk_index,
            "start_page": self.start_page,
            "end_page": self.end_page,
            "score": self.score,
            "keyword_hits": self.keyword_hits,
            "text": excerpt,
        }


@dataclass(slots=True)
class DeepReadArtifact:
    paper: PaperRecord
    pdf_path: Path
    text_path: Path
    chunk_manifest_path: Path
    page_count: int
    full_text_char_count: int
    extracted_preview: str
    chunks: list[TextChunk] = field(default_factory=list)

    def to_dict(self, top_chunks: int = 5, max_chunk_chars: int = 1200) -> dict[str, Any]:
        ranked = sorted(
            self.chunks,
            key=lambda chunk: (chunk.score or 0.0, -chunk.chunk_index),
            reverse=True,
        )
        return {
            "paper": self.paper.to_dict(),
            "pdf_path": str(self.pdf_path),
            "text_path": str(self.text_path),
            "chunk_manifest_path": str(self.chunk_manifest_path),
            "page_count": self.page_count,
            "full_text_char_count": self.full_text_char_count,
            "extracted_preview": self.extracted_preview,
            "chunk_count": len(self.chunks),
            "top_chunks": [chunk.to_dict(max_chars=max_chunk_chars) for chunk in ranked[:top_chunks]],
        }


def combine_papers(records: list[PaperRecord]) -> list[PaperRecord]:
    combined: dict[str, PaperRecord] = {}
    for record in records:
        key = record.dedupe_key()
        if key not in combined:
            combined[key] = record
            continue
        existing = combined[key]
        combined[key] = PaperRecord(
            source=existing.source if existing.rank_score() >= record.rank_score() else record.source,
            source_id=existing.source_id if existing.rank_score() >= record.rank_score() else record.source_id,
            title=existing.title if len(existing.title) >= len(record.title) else record.title,
            authors=existing.authors if len(existing.authors) >= len(record.authors) else record.authors,
            abstract=existing.abstract if len(existing.abstract or "") >= len(record.abstract or "") else record.abstract,
            year=existing.year or record.year,
            venue=existing.venue or record.venue,
            doi=existing.doi or record.doi,
            url=existing.url or record.url,
            pdf_url=existing.pdf_url or record.pdf_url,
            citation_count=max(existing.citation_count or 0, record.citation_count or 0) or None,
            is_open_access=existing.is_open_access or record.is_open_access,
            keywords=sorted(set(existing.keywords + record.keywords)),
            related_score=max(existing.related_score or 0.0, record.related_score or 0.0) or None,
            raw={"merged_sources": sorted({existing.source, record.source})},
        )
    return sorted(combined.values(), key=lambda item: item.rank_score(), reverse=True)
