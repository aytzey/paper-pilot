from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup

try:
    import fitz
except ModuleNotFoundError:  # pragma: no cover
    import pymupdf as fitz

from paper_pilot.config import Settings
from paper_pilot.models import DownloadedDocument, PaperRecord, normalize_doi, slugify, utc_timestamp
from paper_pilot.services.net import download_capped, is_public_http_url

logger = logging.getLogger(__name__)

_DEFAULT_MIRRORS: tuple[str, ...] = (
    "https://sci-hub.se",
    "https://sci-hub.st",
    "https://sci-hub.ru",
)

_HEADERS = {
    "User-Agent": "paper-pilot/0.4",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


class ScihubService:
    """Sci-Hub paper resolver and downloader."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.mirrors = settings.scihub_mirrors or _DEFAULT_MIRRORS
        self.timeout = settings.scihub_timeout_sec
        # Verify TLS by default (respecting SSL_CERT_FILE); only disable when the
        # operator explicitly opts in via INSECURE_SHADOW_TLS=true.
        self._verify = False if settings.insecure_shadow_tls else settings.ssl_verify
        self._crossref_ua = self._build_crossref_ua(settings)

    @staticmethod
    def _build_crossref_ua(settings: Settings) -> str:
        contact = settings.unpaywall_email or settings.openalex_email
        return f"paper-pilot/0.4 (mailto:{contact})" if contact else "paper-pilot/0.4"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch_by_doi(self, doi: str) -> dict[str, Any]:
        """Resolve a DOI through Sci-Hub and return metadata + PDF URL."""
        metadata = await self._crossref_metadata(doi)
        pdf_url = await self._resolve_pdf_url(doi)
        return {
            "doi": doi,
            "title": metadata.get("title", ""),
            "authors": metadata.get("authors", []),
            "year": metadata.get("year"),
            "venue": metadata.get("venue"),
            "pdf_url": pdf_url,
            "status": "success" if pdf_url else "pdf_not_found",
        }

    async def search_by_title(self, title: str) -> dict[str, Any]:
        """Look up a paper by title via CrossRef, then resolve through Sci-Hub."""
        doi = await self._crossref_title_to_doi(title)
        if not doi:
            return {"title": title, "status": "not_found"}
        return await self.fetch_by_doi(doi)

    async def search_by_keyword(
        self,
        keyword: str,
        limit: int = 10,
        check_scihub: bool = False,
    ) -> list[dict[str, Any]]:
        """Search CrossRef by keyword and optionally check Sci-Hub availability."""
        papers = await self._crossref_keyword_search(keyword, limit)
        if check_scihub:
            for paper in papers:
                if paper.get("doi"):
                    paper["pdf_url"] = await self._resolve_pdf_url(paper["doi"])
                    paper["scihub_available"] = bool(paper["pdf_url"])
        return papers

    async def download_paper(
        self,
        doi: str,
        topic_hint: str = "scihub",
    ) -> DownloadedDocument:
        """Download a paper PDF via Sci-Hub and return a DownloadedDocument."""
        info = await self.fetch_by_doi(doi)
        pdf_url = info.get("pdf_url")
        if not pdf_url:
            raise ValueError(f"PDF not found via Sci-Hub: {doi}")

        paper = PaperRecord(
            source="scihub",
            source_id=doi,
            title=info.get("title") or doi,
            authors=info.get("authors", []),
            year=info.get("year"),
            venue=info.get("venue"),
            doi=doi,
            pdf_url=pdf_url,
            is_open_access=False,
        )

        content = await self._download_pdf_bytes(pdf_url)
        filename = f"{slugify(topic_hint)}-{slugify(paper.title, 50)}-{utc_timestamp()}.pdf"
        destination = self.settings.data_dir / "downloads" / filename
        destination.write_bytes(content)

        document = await asyncio.to_thread(self._inspect_pdf, paper, destination)
        return document

    # ------------------------------------------------------------------
    # Sci-Hub resolution
    # ------------------------------------------------------------------

    async def _resolve_pdf_url(self, doi: str, retries: int = 2) -> str | None:
        """Try each Sci-Hub mirror to find a PDF URL for the given DOI."""
        safe_doi = quote((normalize_doi(doi) or doi).strip(), safe="/:")
        for attempt in range(retries):
            if attempt > 0:
                await asyncio.sleep(2.0 * attempt)
            async with httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
                trust_env=True,
                verify=self._verify,
                headers=_HEADERS,
            ) as client:
                for mirror in self.mirrors:
                    try:
                        url = f"{mirror}/{safe_doi}"
                        resp = await client.get(url)
                        if resp.status_code != 200:
                            continue
                        pdf_url = self._extract_pdf_url(resp.text, mirror)
                        if pdf_url:
                            return pdf_url
                    except Exception as exc:
                        logger.debug("Sci-Hub mirror %s failed (attempt %d): %s", mirror, attempt, exc)
                        continue
        return None

    @staticmethod
    def _extract_pdf_url(html: str, mirror_base: str) -> str | None:
        """Parse Sci-Hub HTML page to find the embedded PDF URL."""
        soup = BeautifulSoup(html, "html.parser")

        # Try <embed> or <iframe> with src containing .pdf or /pdf
        for tag_name in ("embed", "iframe"):
            tag = soup.find(tag_name, src=True)
            if tag:
                src = tag["src"]
                if isinstance(src, list):
                    src = src[0]
                return _normalize_url(src, mirror_base)

        # Try <button onclick="location.href='...pdf...'">
        for button in soup.find_all("button", onclick=True):
            match = re.search(r"location\.href\s*=\s*['\"]([^'\"]+)['\"]", button["onclick"])
            if match:
                return _normalize_url(match.group(1), mirror_base)

        # Try any <a> tag with href ending in .pdf
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            if isinstance(href, list):
                href = href[0]
            if ".pdf" in href.lower():
                return _normalize_url(href, mirror_base)

        # Try regex on raw HTML for direct PDF link
        match = re.search(r'(https?://[^\s"\'<>]+\.pdf[^\s"\'<>]*)', html)
        if match:
            return match.group(1)

        return None

    # ------------------------------------------------------------------
    # PDF download & inspection
    # ------------------------------------------------------------------

    async def _download_pdf_bytes(self, pdf_url: str) -> bytes:
        """Download PDF content with retries, size cap, and SSRF guard.

        The URL originates from untrusted mirror HTML, so it is validated against
        an internal-host blocklist and streamed with a byte budget.
        """
        if not is_public_http_url(pdf_url):
            raise ValueError(f"Refusing to fetch non-public PDF URL: {pdf_url}")
        async with httpx.AsyncClient(
            timeout=60.0,
            follow_redirects=True,
            trust_env=True,
            verify=self._verify,
            headers={**_HEADERS, "Accept": "application/pdf,*/*"},
        ) as client:
            last_error: Exception | None = None
            for attempt in range(3):
                try:
                    content = await download_capped(client, pdf_url, self.settings.max_download_bytes)
                    if not content.startswith(b"%PDF"):
                        raise ValueError("Downloaded content is not a PDF.")
                    return content
                except Exception as exc:
                    last_error = exc
                    if attempt == 2:
                        raise
                    await asyncio.sleep(1.5 * (attempt + 1))
        raise ValueError(f"Failed to download PDF: {last_error}")

    @staticmethod
    def _inspect_pdf(
        paper: PaperRecord,
        path: Path,
        max_pages: int = 5,
        max_chars: int = 12000,
    ) -> DownloadedDocument:
        with fitz.open(path) as doc:
            parts: list[str] = []
            for i in range(min(max_pages, doc.page_count)):
                parts.append(doc.load_page(i).get_text("text"))
            preview = "\n".join(parts).strip()[:max_chars]
            return DownloadedDocument(
                paper=paper,
                path=path,
                page_count=doc.page_count,
                extracted_preview=preview,
            )

    # ------------------------------------------------------------------
    # CrossRef helpers
    # ------------------------------------------------------------------

    async def _crossref_metadata(self, doi: str) -> dict[str, Any]:
        """Fetch paper metadata from CrossRef by DOI."""
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            try:
                resp = await client.get(
                    f"https://api.crossref.org/works/{quote(doi, safe='/:')}",
                    headers={"User-Agent": self._crossref_ua},
                )
                if resp.status_code != 200:
                    return {}
                item = resp.json().get("message", {})
                return _parse_crossref_item(item)
            except Exception:
                return {}

    async def _crossref_title_to_doi(self, title: str) -> str | None:
        """Find a DOI by title via CrossRef."""
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            try:
                resp = await client.get(
                    "https://api.crossref.org/works",
                    params={"query.title": title, "rows": "1"},
                    headers={"User-Agent": self._crossref_ua},
                )
                if resp.status_code != 200:
                    return None
                items = resp.json().get("message", {}).get("items", [])
                return items[0]["DOI"] if items else None
            except Exception:
                return None

    async def _crossref_keyword_search(
        self, keyword: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Search CrossRef by keyword."""
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            try:
                resp = await client.get(
                    "https://api.crossref.org/works",
                    params={"query": keyword, "rows": str(limit)},
                    headers={"User-Agent": self._crossref_ua},
                )
                if resp.status_code != 200:
                    return []
                items = resp.json().get("message", {}).get("items", [])
                return [_parse_crossref_item(item) for item in items]
            except Exception:
                return []


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def _normalize_url(url: str, base: str) -> str:
    """Ensure URL is absolute."""
    url = url.strip()
    if url.startswith("//"):
        return f"https:{url}"
    if url.startswith("/"):
        return f"{base.rstrip('/')}{url}"
    if not url.startswith("http"):
        return f"{base.rstrip('/')}/{url}"
    return url


def _parse_crossref_item(item: dict[str, Any]) -> dict[str, Any]:
    """Extract a flat metadata dict from a CrossRef work item."""
    title_list = item.get("title", [])
    authors_raw = item.get("author", [])
    authors = []
    for a in authors_raw:
        name = f"{a.get('given', '')} {a.get('family', '')}".strip()
        if name:
            authors.append(name)

    year = None
    for date_field in ("published-print", "published-online", "created"):
        parts = item.get(date_field, {}).get("date-parts", [[]])
        if parts and parts[0] and parts[0][0]:
            year = parts[0][0]
            break

    venue_list = item.get("container-title", [])
    return {
        "doi": item.get("DOI"),
        "title": title_list[0] if title_list else "",
        "authors": authors,
        "year": year,
        "venue": venue_list[0] if venue_list else None,
    }
