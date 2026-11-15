from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
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


def normalize_pmcid(value: Any) -> str | None:
    """'PMC123', a bare number, or any PMC / Europe PMC URL -> 'PMC123'."""
    text = str(value or "").strip()
    match = re.search(r"PMC(\d+)", text, re.IGNORECASE) or re.fullmatch(r"(\d+)", text)
    return f"PMC{match.group(1)}" if match else None


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


def normalize_publication_date(value: Any) -> str | None:
    """Keep only supplied year/month/day precision; reject malformed calendar dates."""
    if isinstance(value, list):
        if not 1 <= len(value) <= 3 or any(type(part) is not int for part in value):
            return None
        value = "-".join(f"{part:04d}" if i == 0 else f"{part:02d}" for i, part in enumerate(value))
    value = f"{value:04d}" if type(value) is int else str(value or "")
    if not re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", value):
        return None
    parts = [int(part) for part in value.split("-")]
    try:
        date(*parts, *([1] * (3 - len(parts))))
    except ValueError:
        return None
    return value


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
    publication_date: str | None = None
    publication_date_source: str | None = None

    def date_evidence(self) -> list[dict[str, str]]:
        if "publication_dates" in self.raw:
            return self.raw["publication_dates"]
        if not self.publication_date:
            return []
        return [{"date": self.publication_date, "source": self.publication_date_source or self.source}]

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

    def pdf_candidates(self) -> list[str]:
        """Every known PDF URL for this paper, preferred first (alternates survive merging)."""
        urls = [self.pdf_url, self.raw.get("original_pdf_url"), *self.raw.get("pdf_urls", [])]
        return list(dict.fromkeys(url for url in urls if url))

    def brief(self, max_abstract_chars: int = 700) -> dict[str, Any]:
        """Compact record for MCP payloads: what an agent needs to screen and then download.

        Tool output is truncated by the client (roughly 10k tokens in Codex, 25k in Claude Code),
        so provider blobs stay out and the abstract is capped; the paper itself carries the rest.
        """
        abstract = (self.abstract or "") if max_abstract_chars > 0 else ""
        if len(abstract) > max_abstract_chars:
            abstract = abstract[:max_abstract_chars].rstrip() + "… [abstract truncated]"
        authors = self.authors[:5] + ([f"+{len(self.authors) - 5} more"] if len(self.authors) > 5 else [])
        download = self.raw.get("pdf_download")
        if download:  # which copy was read: source, format, and the licence/version Unpaywall reports
            download = {**{key: (download.get("location") or {}).get(key) for key in ("host_type", "license", "version")},
                        **{key: download.get(key) for key in ("url", "resolver", "format")}}
        record = {
            "title": self.title,
            "authors": authors,
            "year": self.year,
            "publication_date": self.publication_date,
            "publication_date_precision": {4: "year", 7: "month", 10: "day"}.get(len(self.publication_date or "")),
            # Listed only when providers disagree about the date.
            "publication_dates": self.date_evidence() if len(self.date_evidence()) > 1 else None,
            "venue": self.venue,
            "doi": self.doi,
            "arxiv_id": self.arxiv_id(),
            "pmcid": self.raw.get("pmcid"),
            "url": self.url,
            "pdf_url": self.pdf_url,
            "is_open_access": self.is_open_access,
            "citation_count": self.citation_count,
            "sources": self.raw.get("merged_sources") or [self.source],
            "abstract": abstract,
            "downloaded_from": {key: value for key, value in download.items() if value} if download else None,
        }
        return {key: value for key, value in record.items() if value not in (None, "", [])}

    def arxiv_id(self) -> str | None:
        if self.raw.get("arxiv_id"):
            return normalize_arxiv_id(self.raw["arxiv_id"])
        doi = normalize_doi(self.doi) or ""
        if doi.startswith("10.48550/arxiv."):
            return normalize_arxiv_id(doi.removeprefix("10.48550/arxiv."))
        if self.source == "arxiv":
            return normalize_arxiv_id(self.url) or normalize_arxiv_id(self.source_id)
        for url in (self.pdf_url, self.url):
            if url and "arxiv.org/" in url.lower():
                return normalize_arxiv_id(url)
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "source_id": self.source_id,
            "title": self.title,
            "authors": self.authors,
            "abstract": self.abstract,
            "year": self.year,
            "publication_date": self.publication_date,
            "publication_date_precision": {4: "year", 7: "month", 10: "day"}.get(len(self.publication_date or "")),
            "publication_date_source": self.publication_date_source,
            "publication_dates": self.date_evidence(),
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
    text_chars: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper": self.paper.to_dict(),
            "path": str(self.path),
            "pdf_path": str(self.path),
            "page_count": self.page_count,
            "extracted_preview": self.extracted_preview,
        }

    def summary(self) -> dict[str, Any]:
        """What was downloaded, without any of its text (that is read through read_pdf_text)."""
        return {
            "paper": self.paper.brief(max_abstract_chars=0),
            "pdf_path": str(self.path),
            "page_count": self.page_count,
            "text_chars": self.text_chars,
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
    extraction_status: str = "unknown"
    pages_without_text: list[int] = field(default_factory=list)

    def to_dict(self, top_chunks: int = 5, max_chunk_chars: int = 400) -> dict[str, Any]:
        ranked = sorted(
            self.chunks,
            key=lambda chunk: (chunk.score or 0.0, -chunk.chunk_index),
            reverse=True,
        )
        return {
            "paper": self.paper.brief(max_abstract_chars=0),
            "pdf_path": str(self.pdf_path),
            "text_path": str(self.text_path),
            "chunk_manifest_path": str(self.chunk_manifest_path),
            "page_count": self.page_count,
            "full_text_char_count": self.full_text_char_count,
            "extraction_status": self.extraction_status,
            "pages_without_text": self.pages_without_text,
            "chunk_count": len(self.chunks),
            # Pointers to where the question is discussed; the paper is read through full_text.
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
        dated = existing if existing.publication_date else record
        # Refine a compatible partial date; keep conflicting provider dates as evidence.
        if record.publication_date and record.publication_date.startswith(existing.publication_date or ""):
            dated = record
        dates = existing.date_evidence() + [d for d in record.date_evidence() if d not in existing.date_evidence()]
        combined[key] = PaperRecord(
            source=existing.source if existing.rank_score() >= record.rank_score() else record.source,
            source_id=existing.source_id if existing.rank_score() >= record.rank_score() else record.source_id,
            title=existing.title if len(existing.title) >= len(record.title) else record.title,
            authors=existing.authors if len(existing.authors) >= len(record.authors) else record.authors,
            abstract=existing.abstract if len(existing.abstract or "") >= len(record.abstract or "") else record.abstract,
            year=int(dated.publication_date[:4]) if dated.publication_date else existing.year or record.year,
            venue=existing.venue or record.venue,
            doi=existing.doi or record.doi,
            url=existing.url or record.url,
            pdf_url=existing.pdf_url or record.pdf_url,
            citation_count=max(existing.citation_count or 0, record.citation_count or 0) or None,
            is_open_access=existing.is_open_access or record.is_open_access,
            keywords=sorted(set(existing.keywords + record.keywords)),
            related_score=max(existing.related_score or 0.0, record.related_score or 0.0) or None,
            publication_date=dated.publication_date,
            publication_date_source=dated.publication_date_source,
            raw={
                **record.raw, **existing.raw,
                "merged_sources": sorted({existing.source, record.source,
                                          *existing.raw.get("merged_sources", []), *record.raw.get("merged_sources", [])}),
                "publication_dates": dates,
                # A dead link in one index must not hide a working copy from another.
                "pdf_urls": list(dict.fromkeys([*existing.pdf_candidates(), *record.pdf_candidates()])),
            },
        )
    return sorted(combined.values(), key=lambda item: item.rank_score(), reverse=True)
