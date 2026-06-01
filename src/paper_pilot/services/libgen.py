from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

try:
    import fitz
except ModuleNotFoundError:  # pragma: no cover - compatibility fallback
    import pymupdf as fitz

from paper_pilot.config import Settings
from paper_pilot.models import DownloadedDocument, PaperRecord, slugify, utc_timestamp
from paper_pilot.services.net import download_capped_sync, is_public_http_url

LIBGEN_COLUMNS = [
    "ID",
    "Author",
    "Title",
    "Publisher",
    "Year",
    "Pages",
    "Language",
    "Size",
    "Extension",
    "Mirror_1",
    "Mirror_2",
    "Mirror_3",
    "Mirror_4",
    "Mirror_5",
    "Edit",
]

DOWNLOAD_SOURCES = ["GET", "Cloudflare", "IPFS.io", "Infura"]


@dataclass(slots=True)
class LibgenSearchBundle:
    results: list[PaperRecord]
    warnings: list[str]


class LibgenService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        # Verify TLS by default; only disable when the operator opts in.
        self._verify = False if settings.insecure_shadow_tls else settings.ssl_verify

    async def search(
        self,
        query: str,
        search_type: str = "title",
        limit: int = 10,
        allowed_extensions: tuple[str, ...] = ("pdf", "epub"),
    ) -> LibgenSearchBundle:
        return await asyncio.to_thread(self._search_sync, query, search_type, limit, allowed_extensions)

    async def download_preview(self, item: dict[str, Any], topic_hint: str | None = None) -> DownloadedDocument:
        return await asyncio.to_thread(self._download_preview_sync, item, topic_hint)

    def _search_sync(
        self,
        query: str,
        search_type: str,
        limit: int,
        allowed_extensions: tuple[str, ...],
    ) -> LibgenSearchBundle:
        warnings: list[str] = []
        if len(query.strip()) < 3:
            return LibgenSearchBundle(results=[], warnings=["LibGen query must be at least 3 characters."])

        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )

        for mirror in self.settings.libgen_mirrors:
            try:
                records = self._search_mirror(session, mirror, query, search_type, limit, allowed_extensions)
                if records:
                    return LibgenSearchBundle(results=records, warnings=warnings)
                warnings.append(f"{mirror} returned no results.")
            except Exception as exc:
                warnings.append(f"{mirror} failed: {exc}")

        return LibgenSearchBundle(results=[], warnings=warnings)

    def _search_mirror(
        self,
        session: requests.Session,
        mirror: str,
        query: str,
        search_type: str,
        limit: int,
        allowed_extensions: tuple[str, ...],
    ) -> list[PaperRecord]:
        path = "/index.php" if mirror.endswith("libgen.li") else "/search.php"
        response = session.get(
            f"{mirror}{path}",
            params={"req": query, "column": search_type},
            timeout=self.settings.libgen_timeout_sec,
            verify=self._verify,
        )
        response.raise_for_status()
        return self._parse_search_results(response.text, str(response.url), limit, allowed_extensions)

    def _parse_search_results(
        self,
        html_text: str,
        base_url: str,
        limit: int,
        allowed_extensions: tuple[str, ...],
    ) -> list[PaperRecord]:
        soup = BeautifulSoup(html_text, "lxml")
        for element in soup.find_all("i"):
            element.decompose()

        table = soup.find("table", {"id": "tablelibgen"})
        if table is None:
            tables = soup.find_all("table")
            if len(tables) >= 3:
                table = tables[2]
        if table is None:
            return []

        rows = table.find_all("tr")[1:]
        records: list[PaperRecord] = []
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 10:
                continue
            raw_row: list[str] = []
            for cell in cells[: len(LIBGEN_COLUMNS)]:
                link = cell.find("a")
                if link and link.has_attr("title") and link.get("title"):
                    raw_row.append(urljoin(base_url, link.get("href", "")))
                else:
                    raw_row.append(" ".join(cell.stripped_strings))
            item = dict(zip(LIBGEN_COLUMNS, raw_row, strict=False))
            extension = (item.get("Extension") or "").lower()
            if allowed_extensions and extension not in allowed_extensions:
                continue
            records.append(self._paper_from_item(item))
            if len(records) >= limit:
                break
        return records

    def resolve_download_links(self, mirror_url: str) -> dict[str, str]:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        response = session.get(mirror_url, timeout=self.settings.libgen_timeout_sec, verify=self._verify)
        response.raise_for_status()
        return self._extract_download_links(response.text, str(response.url))

    def _extract_download_links(self, html_text: str, base_url: str) -> dict[str, str]:
        soup = BeautifulSoup(html_text, "lxml")
        links: dict[str, str] = {}
        for source in DOWNLOAD_SOURCES:
            anchor = soup.find("a", string=source)
            if anchor and anchor.get("href"):
                links[source] = urljoin(base_url, anchor["href"])
        if not links:
            for anchor in soup.find_all("a"):
                href = anchor.get("href", "")
                if "get.php" in href or "cloudflare-ipfs.com" in href:
                    label = anchor.get_text(strip=True) or "GET"
                    links[label] = urljoin(base_url, href)
        return links

    def _download_preview_sync(self, item: dict[str, Any], topic_hint: str | None) -> DownloadedDocument:
        extension = (item.get("Extension") or "").lower()
        if extension != "pdf":
            raise RuntimeError(f"Preview is only supported for PDF files. This record: {extension or 'unknown'}")

        mirror_url = item.get("Mirror_1")
        if not mirror_url:
            raise RuntimeError("Mirror_1 field is missing.")

        links = self.resolve_download_links(mirror_url)
        if not links:
            raise RuntimeError("No downloadable link could be resolved.")

        download_url = next((links[source] for source in DOWNLOAD_SOURCES if source in links), next(iter(links.values())))
        if not is_public_http_url(download_url):
            raise RuntimeError(f"Refusing to fetch non-public download URL: {download_url}")
        session = requests.Session()
        content = download_capped_sync(
            session,
            download_url,
            self.settings.max_download_bytes,
            timeout=max(self.settings.libgen_timeout_sec, 60),
            verify=self._verify,
        )
        if not content.startswith(b"%PDF"):
            raise RuntimeError("Downloaded content does not appear to be a PDF.")

        filename = f"{slugify(topic_hint or item.get('Title') or 'libgen')}-{utc_timestamp()}.pdf"
        destination = self.settings.data_dir / "downloads" / filename
        destination.write_bytes(content)

        paper = self._paper_from_item(item)
        paper.pdf_url = download_url
        with fitz.open(destination) as document:
            preview = "\n".join(document.load_page(i).get_text("text") for i in range(min(5, document.page_count))).strip()
            return DownloadedDocument(
                paper=paper,
                path=destination,
                page_count=document.page_count,
                extracted_preview=preview[:12000],
            )

    @staticmethod
    def _paper_from_item(item: dict[str, Any]) -> PaperRecord:
        authors = [part.strip() for part in (item.get("Author") or "").replace(";", ",").split(",") if part.strip()]
        year = None
        if (item.get("Year") or "").isdigit():
            year = int(item["Year"])
        return PaperRecord(
            source="libgen",
            source_id=item.get("ID") or item.get("Mirror_1") or item.get("Title") or "unknown",
            title=item.get("Title") or "Untitled",
            authors=authors,
            abstract=None,
            year=year,
            venue=item.get("Publisher") or "Library Genesis",
            doi=None,
            url=item.get("Mirror_1"),
            pdf_url=None,
            citation_count=None,
            is_open_access=False,
            keywords=[],
            raw=item,
        )
