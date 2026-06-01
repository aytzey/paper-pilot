from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

try:
    import fitz
except ModuleNotFoundError:  # pragma: no cover - compatibility fallback
    import pymupdf as fitz

from paper_pilot.config import Settings
from paper_pilot.models import DownloadedDocument, PaperRecord, slugify, utc_timestamp
from paper_pilot.services.net import download_capped, is_public_http_url


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
        candidates = [paper for paper in papers if paper.pdf_url]
        async with httpx.AsyncClient(
            timeout=60.0,
            follow_redirects=True,
            trust_env=True,
            verify=self.settings.ssl_verify,
            headers={"User-Agent": "paper-pilot/0.4", "Accept": "application/pdf,*/*"},
        ) as client:
            for paper in candidates:
                if len(downloaded) >= max_papers:
                    break
                try:
                    path = await self._download_pdf(client, topic, paper)
                    document = await asyncio.to_thread(self.inspect_local_pdf, paper, path)
                    downloaded.append(document)
                except Exception as exc:
                    warnings.append(f"Failed to download {paper.title}: {exc}")
        return downloaded, warnings

    async def inspect_remote_pdf(
        self,
        pdf_url: str,
        filename_hint: str = "paper",
    ) -> DownloadedDocument:
        paper = PaperRecord(
            source="remote_pdf",
            source_id=pdf_url,
            title=filename_hint,
            url=pdf_url,
            pdf_url=pdf_url,
            is_open_access=True,
        )
        async with httpx.AsyncClient(
            timeout=60.0,
            follow_redirects=True,
            trust_env=True,
            verify=self.settings.ssl_verify,
            headers={"User-Agent": "paper-pilot/0.4", "Accept": "application/pdf,*/*"},
        ) as client:
            path = await self._download_pdf(client, filename_hint, paper)
        return await asyncio.to_thread(self.inspect_local_pdf, paper, path)

    async def _download_pdf(self, client: httpx.AsyncClient, topic: str, paper: PaperRecord) -> Path:
        if not paper.pdf_url:
            raise ValueError("No PDF URL available for this record.")
        if not is_public_http_url(paper.pdf_url):
            raise ValueError(f"Refusing to fetch non-public PDF URL: {paper.pdf_url}")
        last_error: Exception | None = None
        content = b""
        for attempt in range(3):
            try:
                content = await download_capped(client, paper.pdf_url, self.settings.max_download_bytes)
                if not content.startswith(b"%PDF"):
                    raise ValueError("Downloaded content does not appear to be a PDF.")
                break
            except Exception as exc:
                last_error = exc
                if attempt == 2:
                    raise
                await asyncio.sleep(1.5 * (attempt + 1))
        if not content.startswith(b"%PDF"):
            raise ValueError(f"Failed to download PDF: {last_error}")
        filename = f"{slugify(topic)}-{slugify(paper.title, 50)}-{utc_timestamp()}.pdf"
        downloads_dir = self.settings.data_dir / "downloads"
        downloads_dir.mkdir(parents=True, exist_ok=True)
        destination = downloads_dir / filename
        tmp = destination.with_suffix(".pdf.part")
        tmp.write_bytes(content)
        tmp.replace(destination)
        return destination

    def inspect_local_pdf(self, paper: PaperRecord, path: Path, max_pages: int = 5, max_chars: int = 12000) -> DownloadedDocument:
        with fitz.open(path) as document:
            extracted_parts: list[str] = []
            for page_index in range(min(max_pages, document.page_count)):
                extracted_parts.append(document.load_page(page_index).get_text("text"))
            preview = "\n".join(extracted_parts).strip()[:max_chars]
            return DownloadedDocument(
                paper=paper,
                path=path,
                page_count=document.page_count,
                extracted_preview=preview,
            )
