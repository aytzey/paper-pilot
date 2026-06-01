"""Build MCP content blocks (images, embedded PDFs, resource links) from local files.

Kept separate from ``models.py`` so the data models stay MCP-agnostic. Modern
Claude and Codex read PDFs and page images directly, so these helpers let the
tools hand the model the actual pages and the actual PDF instead of only paths.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

try:
    import fitz
except ModuleNotFoundError:  # pragma: no cover - compatibility fallback
    import pymupdf as fitz

from mcp.server.fastmcp.utilities.types import Image
from mcp.types import BlobResourceContents, EmbeddedResource, ResourceLink

from paper_pilot.config import Settings

DEFAULT_MAX_RENDER_PAGES = 6
DEFAULT_RENDER_SCALE = 2.0
DEFAULT_PDF_EMBED_MAX_MB = 5.0
DEFAULT_PDF_EMBED_MAX_PAGES = 60
PDF_URI_SCHEME = "paperpilot"


class PdfTooLargeError(RuntimeError):
    """Raised when a PDF exceeds the embed size budget."""


def stable_doc_id(pdf_path: Path | str) -> str:
    resolved = str(Path(pdf_path).expanduser().resolve())
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]


def pdf_resource_uri(doc_id: str) -> str:
    return f"{PDF_URI_SCHEME}://pdf/{doc_id}"


def images_from_paths(paths: list[Path]) -> list[Image]:
    """Wrap rendered PNG page files as FastMCP Image objects (become ImageContent)."""
    return [Image(path=path) for path in paths]


def safe_pdf_path(raw: str | Path, settings: Settings) -> Path:
    """Resolve and validate a local PDF path before reading its bytes.

    Always requires a real ``.pdf`` file. When ``allow_external_pdf_paths`` is
    false (recommended for networked transports) the file must live inside the
    configured ``data_dir`` to prevent path traversal / arbitrary file reads.
    """
    resolved = Path(raw).expanduser().resolve()
    if resolved.suffix.lower() != ".pdf":
        raise ValueError(f"Not a PDF path: {resolved}")
    if not resolved.is_file():
        raise FileNotFoundError(f"PDF not found: {resolved}")
    if not settings.allow_external_pdf_paths:
        data_dir = settings.data_dir.resolve()
        if not resolved.is_relative_to(data_dir):
            raise ValueError(
                f"Refusing to read PDF outside the data directory: {resolved} "
                "(set PAPER_PILOT_ALLOW_EXTERNAL_PDF=true to allow)."
            )
    return resolved


def to_pdf_embedded_resource(
    pdf_path: Path,
    *,
    doc_id: str,
    max_mb: float = DEFAULT_PDF_EMBED_MAX_MB,
    max_pages: int | None = DEFAULT_PDF_EMBED_MAX_PAGES,
) -> EmbeddedResource:
    """Return a PDF as an embedded application/pdf resource, bounded by size and pages.

    Checks file size before reading the bytes and the page count before encoding,
    so an oversized PDF is rejected cheaply rather than loaded into memory.
    """
    size_bytes = pdf_path.stat().st_size
    if size_bytes > max_mb * 1024 * 1024:
        raise PdfTooLargeError(
            f"PDF is {size_bytes / 1024 / 1024:.1f} MB, over the {max_mb} MB embed limit."
        )
    if max_pages is not None:
        with fitz.open(pdf_path) as document:
            if document.page_count > max_pages:
                raise PdfTooLargeError(
                    f"PDF has {document.page_count} pages, over the {max_pages}-page embed limit."
                )
    blob = base64.b64encode(pdf_path.read_bytes()).decode("ascii")
    return EmbeddedResource(
        type="resource",
        resource=BlobResourceContents(
            uri=pdf_resource_uri(doc_id),
            mimeType="application/pdf",
            blob=blob,
        ),
    )


def pdf_resource_link(doc_id: str, *, name: str, size_bytes: int | None = None) -> ResourceLink:
    """A lightweight link to a registered PDF resource (no inlined bytes)."""
    return ResourceLink(
        type="resource_link",
        uri=pdf_resource_uri(doc_id),
        name=name,
        mimeType="application/pdf",
    )
