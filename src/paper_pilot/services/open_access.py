from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path

import httpx

try:
    import pymupdf as fitz
except ModuleNotFoundError:  # pragma: no cover - compatibility fallback
    import fitz

from paper_pilot import __version__
from paper_pilot.config import Settings
from paper_pilot.models import (
    DownloadedDocument,
    PaperRecord,
    normalize_doi,
    normalize_pmcid,
    normalize_title,
    slugify,
)
from paper_pilot.services.academic import AcademicSearchService
from paper_pilot.services.net import download_capped, is_public_http_url

# PMC's official login-free article store. The PDF links on pmc.ncbi.nlm.nih.gov and
# europepmc.org answer scripts with a bot challenge, so PMC ids are resolved here instead.
PMC_OPEN_DATA = "https://pmc-oa-opendata.s3.amazonaws.com"
_PMC_URL_RE = re.compile(r"(?:ncbi\.nlm\.nih\.gov|europepmc\.org)/.*PMC\d+", re.IGNORECASE)
_RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}
# Give a silent host ten seconds to answer, a large PDF a minute to arrive.
_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
# Open-access publishers whose PDF address follows from the DOI. Indexes often list only the
# landing page for them. A paywalled article answers with HTML and the next route takes over.
_PUBLISHER_PDF = {
    "10.3389/": "https://www.frontiersin.org/articles/{doi}/pdf",
    "10.1038/": "https://www.nature.com/articles/{suffix}.pdf",
    "10.1007/": "https://link.springer.com/content/pdf/{doi}.pdf",
    "10.1186/": "https://link.springer.com/content/pdf/{doi}.pdf",
}
# Below this much text a "paper" is usually an abstract page, cover sheet, erratum or poster.
SHORT_DOCUMENT_CHARS = 6000


def _is_transient(exc: Exception) -> bool:
    """Worth one more try: a busy server or a transfer that broke off. A host that does not
    answer at all is not; retrying it is what made one dead repository cost three minutes."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRY_STATUS
    return isinstance(exc, (httpx.ReadError, httpx.ReadTimeout, httpx.RemoteProtocolError))


def _text_to_pdf(text: str) -> bytes:
    """Lay plain text out as an A4 PDF so the page-based reading tools work on it unchanged."""
    text = re.sub(r"[\x80-\x9f]", "", text)  # PMC's section markers would render as stray glyphs
    with fitz.open(stream=text.encode("utf-8"), filetype="txt") as document:
        document.layout(width=595, height=842, fontsize=10)
        return document.convert_to_pdf()


class OpenAccessService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def download_best_papers(
        self,
        topic: str,
        papers: list[PaperRecord],
        max_papers: int = 3,
    ) -> tuple[list[DownloadedDocument], list[str]]:
        downloaded: list[DownloadedDocument] = []
        warnings: list[str] = []
        candidates = [
            paper for paper in papers
            if paper.pdf_candidates() or paper.doi or paper.arxiv_id() or paper.raw.get("pmcid")
        ]
        async with httpx.AsyncClient(
            timeout=_TIMEOUT,
            follow_redirects=True,
            trust_env=True,
            verify=self.settings.ssl_verify,
            headers={"User-Agent": f"paper-pilot/{__version__}", "Accept": "application/pdf,*/*"},
        ) as client:
            for paper in candidates:
                if len(downloaded) >= max_papers:
                    break
                try:
                    path = await self._download_pdf(client, topic, paper, warnings)
                    document = await asyncio.to_thread(self.inspect_local_pdf, paper, path)
                    downloaded.append(document)
                except Exception as exc:
                    warnings.append(f"Failed to download {paper.title}: {exc}")
        return downloaded, warnings

    async def download_paper(
        self,
        pdf_url: str | None = None,
        title: str | None = None,
        doi: str | None = None,
        arxiv_id: str | None = None,
        pmcid: str | None = None,
    ) -> DownloadedDocument:
        """Download one paper from whatever identifiers the caller has; a title alone is looked up."""
        # People paste what they have: a doi.org link is a DOI, not a PDF.
        if pdf_url and not doi and re.match(r"https?://(?:dx\.)?doi\.org/", pdf_url, re.IGNORECASE):
            doi, pdf_url = pdf_url, None
        doi = normalize_doi(doi)
        if doi and not re.fullmatch(r"10\.\d{4,9}/\S+", doi):
            raise ValueError("Invalid DOI.")
        if pdf_url or doi or arxiv_id or pmcid:
            name = title or doi or arxiv_id or pmcid or "paper"
            papers = [PaperRecord(
                source="remote_pdf" if pdf_url else "doi" if doi else "identifier",
                source_id=pdf_url or doi or arxiv_id or pmcid,
                title=name,
                doi=doi,
                url=pdf_url,
                pdf_url=pdf_url,
                is_open_access=bool(pdf_url),
                raw={key: value for key, value in {"arxiv_id": arxiv_id, "pmcid": normalize_pmcid(pmcid)}.items() if value},
            )]
        elif title and title.strip():
            name, papers = title, await self._find_by_title(title)
        else:
            raise ValueError("Provide a DOI, an arXiv id, a PMC id, a PDF URL or the paper's exact title.")
        documents, warnings = await self.download_best_papers(name, papers, 1)
        if not documents:
            raise ValueError("; ".join(warnings) or "No downloadable PDF found.")
        document = documents[0]
        document.paper.raw = {**document.paper.raw, "access_warnings": warnings}
        return document

    async def _find_by_title(self, title: str) -> list[PaperRecord]:
        """Search records with exactly this title, most reachable first.

        A near miss is refused: downloading a similar paper would have the agent read, and
        cite, the wrong one. A preprint and its journal version both match and are both tried.
        """
        bundle = await AcademicSearchService(self.settings).search_literature(
            title, limit_per_source=3, open_access_only=False,
        )
        wanted = normalize_title(title)
        matches = [paper for paper in bundle.results if normalize_title(paper.title) == wanted]
        if not matches:
            closest = "; ".join(f"{paper.title} ({paper.year}, doi: {paper.doi})" for paper in bundle.results[:5])
            raise ValueError(
                f"No paper titled exactly {title!r} was found. Closest results: {closest or 'none'}. "
                "Call download_paper again with the DOI or URL of the one you mean."
            )
        return sorted(matches, key=lambda paper: (not paper.pdf_candidates(), -paper.rank_score()))

    async def _get(self, client: httpx.AsyncClient, url: str, max_bytes: int | None = None) -> bytes:
        """Fetch a public URL, retrying transient failures only.

        A 403, a 404 or an HTML page does not turn into a PDF on the next attempt, and
        waiting on them is what used to make one unreachable paper cost twenty seconds.
        A transient failure gets one retry.
        """
        if not is_public_http_url(url):
            raise ValueError(f"Refusing to fetch non-public PDF URL: {url}")
        try:
            return await download_capped(client, url, max_bytes or self.settings.max_download_bytes)
        except Exception as exc:
            if not _is_transient(exc):
                raise
        await asyncio.sleep(1.5)
        return await download_capped(client, url, max_bytes or self.settings.max_download_bytes)

    async def _fetch_pdf(self, client: httpx.AsyncClient, url: str) -> bytes:
        content = await self._get(client, url)
        if b"%PDF" not in content[:1024]:
            raise ValueError("Downloaded content does not appear to be a PDF.")
        # Reject a corrupt or truncated file here so the next location gets its turn.
        with fitz.open(stream=content, filetype="pdf") as pdf:
            if pdf.page_count == 0:
                raise ValueError("Downloaded PDF has no pages.")
        return content

    async def _fetch_pmc(self, client: httpx.AsyncClient, pmcid: str) -> tuple[bytes, dict]:
        """The article from PMC open data: its PDF, or for text-only records (mostly author
        manuscripts) PMC's own plain-text rendering laid out as a PDF."""
        listing = await self._get(client, f"{PMC_OPEN_DATA}/?list-type=2&prefix={pmcid}.", 8 * 1024 * 1024)
        keys = re.findall(r"<Key>([^<]+)</Key>", listing.decode("utf-8", "replace"))
        for extension in ("pdf", "txt"):
            # The article is named after its version prefix (PMC123.1/PMC123.1.pdf); any other
            # PDF under that prefix is a supplement. Versions are not ordered by quality, so
            # this takes the newest one that has the wanted format.
            articles = [key for key in keys if re.fullmatch(rf"({pmcid}\.\d+)/\1\.{extension}", key)]
            if not articles:
                continue
            key = max(articles, key=lambda item: int(item.split("/")[0].rsplit(".", 1)[1]))
            url = f"{PMC_OPEN_DATA}/{key}"
            prefix = key.split("/")[0] + "/"
            provenance = {
                "url": url, "resolver": "pmc_open_data", "location": None, "pmcid": pmcid,
                "supplements": [f"{PMC_OPEN_DATA}/{item}" for item in keys
                                if item.startswith(prefix) and item.lower().endswith(".pdf") and item != key][:10],
            }
            if extension == "pdf":
                return await self._fetch_pdf(client, url), {**provenance, "format": "pdf"}
            text = (await self._get(client, url)).decode("utf-8", "replace")
            return _text_to_pdf(text), {**provenance, "format": "text_rendered_as_pdf"}
        raise ValueError(f"{pmcid} is not in the PMC open-data set.")

    async def _acquire(self, client: httpx.AsyncClient, paper: PaperRecord, warnings: list[str]) -> tuple[bytes, dict]:
        """Try every full-text route, most reliable first; raise with each route's reason if none works."""
        errors: list[str] = []
        tried: set[str] = set()
        academic = AcademicSearchService(self.settings)

        async def attempt(label: str, fetch):
            try:
                return await fetch()
            except Exception as exc:
                errors.append(f"{label}: {academic._format_exception(exc)}")
                return None

        async def try_urls() -> tuple[bytes, dict] | None:
            unpaywall = paper.raw.get("unpaywall") or {}
            locations = [unpaywall.get("best_oa_location") or {}, *(unpaywall.get("oa_locations") or [])]
            doi = normalize_doi(paper.doi) or ""
            publisher = next((pattern.format(doi=doi, suffix=doi.partition("/")[2])
                              for prefix, pattern in _PUBLISHER_PDF.items() if doi.startswith(prefix)), None)
            urls = [*(location.get("url_for_pdf") for location in locations), *paper.pdf_candidates(), publisher]
            # PMC-hosted links are bot-walled; they only run after every other location.
            for url in sorted(dict.fromkeys(url for url in urls if url), key=lambda url: bool(_PMC_URL_RE.search(url))):
                if url in tried:
                    continue
                tried.add(url)
                content = await attempt(url, lambda url=url: self._fetch_pdf(client, url))
                if content:
                    location = next((item for item in locations if item.get("url_for_pdf") == url), None)
                    resolver = "unpaywall" if location else "publisher" if url == publisher else paper.source
                    if not location and (paper.raw.get("openalex_doi_lookup") or {}).get("pdf_url") == url:
                        resolver = "openalex"
                    return content, {"url": url, "resolver": resolver, "location": location}
            return None

        arxiv_id = paper.arxiv_id()
        if arxiv_id:
            # Keep a version the caller asked for (…v7); without one arXiv serves the latest.
            given = " ".join(filter(None, [paper.raw.get("arxiv_id"), paper.pdf_url, paper.url]))
            version = re.search(rf"{re.escape(arxiv_id)}(v\d+)", given)
            url = f"https://arxiv.org/pdf/{arxiv_id}{version.group(1) if version else ''}"
            tried.add(url)
            if content := await attempt(url, lambda: self._fetch_pdf(client, url)):
                return content, {"url": url, "resolver": "arxiv", "location": None}

        pmcid = normalize_pmcid(paper.raw.get("pmcid")) or next(
            (normalize_pmcid(url) for url in [*paper.pdf_candidates(), paper.url or ""] if _PMC_URL_RE.search(url)), None,
        )
        if pmcid and (found := await attempt(pmcid, lambda: self._fetch_pmc(client, pmcid))):
            return found

        if found := await try_urls():
            return found

        if paper.doi and (paper.raw.get("unpaywall") or {}).get("status") != "ok":
            _, lookup_warnings = await academic._enrich_with_unpaywall(client, [paper], force_lookup=True)
            warnings.extend(lookup_warnings)
            if found := await try_urls():
                return found

        if paper.doi and not pmcid:
            pmcid = await attempt("PMC id lookup", lambda: academic.lookup_pmcid(client, paper.doi))
            if pmcid and (found := await attempt(pmcid, lambda: self._fetch_pmc(client, pmcid))):
                return found

        if not errors:
            raise ValueError(
                "No open-access full text is known for this paper: no PDF link, and neither Unpaywall "
                "nor PubMed Central has a copy. It is probably paywalled."
            )
        raise ValueError("No full text could be downloaded. Tried " + "; ".join(errors))

    async def _download_pdf(
        self,
        client: httpx.AsyncClient,
        topic: str,
        paper: PaperRecord,
        warnings: list[str] | None = None,
    ) -> Path:
        warnings = warnings if warnings is not None else []
        content, provenance = await self._acquire(client, paper, warnings)
        # Named by content: parallel downloads cannot overwrite each other, and fetching the
        # same paper again reuses the file instead of piling up copies.
        filename = f"{slugify(paper.title, 60)}-{hashlib.sha256(content).hexdigest()[:10]}.pdf"
        downloads_dir = self.settings.data_dir / "downloads"
        downloads_dir.mkdir(parents=True, exist_ok=True)
        destination = downloads_dir / filename
        tmp = destination.with_suffix(".pdf.part")
        tmp.write_bytes(content)
        tmp.replace(destination)
        paper.pdf_url = provenance["url"]
        paper.raw = {**paper.raw, "pdf_download": provenance}
        if provenance.get("format") == "text_rendered_as_pdf":
            warnings.append(
                f"{paper.title}: PMC holds this article as text only, so the PDF was generated from PMC's "
                "plain text. Page numbers are not the publisher's; cite sections. Figures and table images are absent."
            )
        return destination

    def inspect_local_pdf(self, paper: PaperRecord, path: Path, max_pages: int = 5, max_chars: int = 12000) -> DownloadedDocument:
        with fitz.open(path) as document:
            texts = [document.load_page(index).get_text("text") for index in range(document.page_count)]
            return DownloadedDocument(
                paper=paper,
                path=path,
                page_count=document.page_count,
                extracted_preview="\n".join(texts[:max_pages]).strip()[:max_chars],
                text_chars=sum(len(text) for text in texts),
            )
