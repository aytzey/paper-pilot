from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Settings:
    openalex_email: str | None
    semantic_scholar_api_key: str | None
    zotero_library_id: str | None
    zotero_library_type: str
    zotero_api_key: str | None
    data_dir: Path
    libgen_mirrors: tuple[str, ...]
    libgen_timeout_sec: float
    unpaywall_email: str | None = None
    ssl_cert_file: str | None = None
    http_proxy: str | None = None
    https_proxy: str | None = None
    no_proxy: str | None = None
    cache_ttl_sec: int = 86400
    zotero_local: bool = False
    zotero_connector_url: str = "http://127.0.0.1:23119/connector/saveItems"
    zotero_bridge_url: str | None = "http://127.0.0.1:24119"
    zotero_data_dir: str | None = None
    scihub_mirrors: tuple[str, ...] = ("https://sci-hub.se", "https://sci-hub.st", "https://sci-hub.ru")
    scihub_timeout_sec: float = 30.0
    scihub_enabled: bool = False
    max_download_bytes: int = 75 * 1024 * 1024
    insecure_shadow_tls: bool = False
    allow_external_pdf_paths: bool = True
    pdf_embed_max_mb: float = 5.0
    pdf_embed_max_pages: int = 60

    @property
    def effective_zotero_library_id(self) -> str | None:
        if self.zotero_library_id:
            return self.zotero_library_id
        if self.zotero_local and self.zotero_library_type == "user":
            return "0"
        return None

    @property
    def zotero_mode(self) -> str:
        if self.zotero_local:
            return "local"
        if self.zotero_library_id and self.zotero_api_key:
            return "web"
        return "disabled"

    @property
    def zotero_enabled(self) -> bool:
        return self.zotero_mode != "disabled"

    @property
    def zotero_bridge_enabled(self) -> bool:
        return bool(self.zotero_local and self.zotero_bridge_url)

    @property
    def unpaywall_enabled(self) -> bool:
        return bool(self.unpaywall_email)

    @property
    def proxy_configured(self) -> bool:
        return bool(self.http_proxy or self.https_proxy)

    @property
    def ssl_verify(self) -> str | bool:
        return self.ssl_cert_file or True

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def deep_reads_dir(self) -> Path:
        return self.data_dir / "deep_reads"

    @property
    def render_dir(self) -> Path:
        return self.data_dir / "renders"


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def load_settings() -> Settings:
    load_dotenv()
    data_dir = Path(os.getenv("PAPER_PILOT_DATA_DIR", os.getenv("DEEP_RESEARCH_DATA_DIR", os.getenv("ZOTERO_RESEARCHER_DATA_DIR", "./data")))).expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "downloads").mkdir(parents=True, exist_ok=True)
    (data_dir / "reports").mkdir(parents=True, exist_ok=True)
    (data_dir / "cache").mkdir(parents=True, exist_ok=True)
    (data_dir / "deep_reads").mkdir(parents=True, exist_ok=True)
    (data_dir / "renders").mkdir(parents=True, exist_ok=True)
    mirrors = tuple(
        mirror.strip().rstrip("/")
        for mirror in os.getenv(
            "LIBGEN_MIRRORS",
            "https://libgen.is,https://libgen.rs,https://libgen.li",
        ).split(",")
        if mirror.strip()
    )
    truthy = {"1", "true", "yes", "on"}
    zotero_local = os.getenv("ZOTERO_LOCAL", "").strip().lower() in truthy
    scihub_enabled = os.getenv("SCIHUB_ENABLED", "false").strip().lower() in truthy
    insecure_shadow_tls = os.getenv("INSECURE_SHADOW_TLS", "false").strip().lower() in truthy
    allow_external_pdf_paths = os.getenv("PAPER_PILOT_ALLOW_EXTERNAL_PDF", "true").strip().lower() in truthy
    scihub_mirrors = tuple(
        m.strip().rstrip("/")
        for m in os.getenv(
            "SCIHUB_MIRRORS",
            "https://sci-hub.se,https://sci-hub.st,https://sci-hub.ru",
        ).split(",")
        if m.strip()
    )
    return Settings(
        openalex_email=os.getenv("OPENALEX_EMAIL"),
        semantic_scholar_api_key=os.getenv("SEMANTIC_SCHOLAR_API_KEY"),
        zotero_library_id=os.getenv("ZOTERO_LIBRARY_ID"),
        zotero_library_type=os.getenv("ZOTERO_LIBRARY_TYPE", "user"),
        zotero_api_key=os.getenv("ZOTERO_API_KEY"),
        data_dir=data_dir,
        libgen_mirrors=mirrors,
        libgen_timeout_sec=_env_float("LIBGEN_TIMEOUT_SEC", 20.0),
        unpaywall_email=os.getenv("UNPAYWALL_EMAIL") or os.getenv("OPENALEX_EMAIL"),
        ssl_cert_file=os.getenv("SSL_CERT_FILE"),
        http_proxy=os.getenv("HTTP_PROXY") or os.getenv("http_proxy"),
        https_proxy=os.getenv("HTTPS_PROXY") or os.getenv("https_proxy"),
        no_proxy=os.getenv("NO_PROXY") or os.getenv("no_proxy"),
        cache_ttl_sec=_env_int("CACHE_TTL_SEC", 86400),
        zotero_local=zotero_local,
        zotero_connector_url=os.getenv("ZOTERO_CONNECTOR_URL", "http://127.0.0.1:23119/connector/saveItems"),
        zotero_bridge_url=os.getenv("ZOTERO_BRIDGE_URL", "http://127.0.0.1:24119") or None,
        zotero_data_dir=os.getenv("ZOTERO_DATA_DIR") or None,
        scihub_mirrors=scihub_mirrors,
        scihub_timeout_sec=_env_float("SCIHUB_TIMEOUT_SEC", 30.0),
        scihub_enabled=scihub_enabled,
        max_download_bytes=max(_env_int("MAX_DOWNLOAD_MB", 75), 1) * 1024 * 1024,
        insecure_shadow_tls=insecure_shadow_tls,
        allow_external_pdf_paths=allow_external_pdf_paths,
        pdf_embed_max_mb=_env_float("PDF_EMBED_MAX_MB", 5.0),
        pdf_embed_max_pages=_env_int("PDF_EMBED_MAX_PAGES", 60),
    )
