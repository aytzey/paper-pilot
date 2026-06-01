import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from paper_pilot import server
from paper_pilot.config import Settings
from paper_pilot.services.scihub import ScihubService, _normalize_url


def _settings(tmp_path: Path, **overrides) -> Settings:
    base = Settings(
        openalex_email="you@example.com",
        semantic_scholar_api_key=None,
        zotero_library_id=None,
        zotero_library_type="user",
        zotero_api_key=None,
        data_dir=tmp_path,
        libgen_mirrors=("https://libgen.is",),
        libgen_timeout_sec=10.0,
        unpaywall_email="you@example.com",
    )
    return replace(base, **overrides) if overrides else base


def test_extract_pdf_url_from_embed(tmp_path: Path) -> None:
    service = ScihubService(_settings(tmp_path))
    html = '<html><body><embed src="//dacemirror.sci-hub.se/x/file.pdf#view"></body></html>'
    url = service._extract_pdf_url(html, "https://sci-hub.se")
    assert url == "https://dacemirror.sci-hub.se/x/file.pdf#view"


def test_extract_pdf_url_returns_none_when_absent(tmp_path: Path) -> None:
    service = ScihubService(_settings(tmp_path))
    assert service._extract_pdf_url("<html><body>nothing</body></html>", "https://sci-hub.se") is None


def test_normalize_url_variants() -> None:
    assert _normalize_url("//host/a.pdf", "https://sci-hub.se") == "https://host/a.pdf"
    assert _normalize_url("/a.pdf", "https://sci-hub.se") == "https://sci-hub.se/a.pdf"
    assert _normalize_url("https://x/y.pdf", "https://sci-hub.se") == "https://x/y.pdf"


def test_verify_defaults_to_secure(tmp_path: Path) -> None:
    secure = ScihubService(_settings(tmp_path))
    assert secure._verify is True  # ssl_verify default
    insecure = ScihubService(_settings(tmp_path, insecure_shadow_tls=True))
    assert insecure._verify is False


def test_crossref_ua_uses_configured_email(tmp_path: Path) -> None:
    service = ScihubService(_settings(tmp_path))
    assert "you@example.com" in service._crossref_ua
    assert "example.com" in service._crossref_ua and "research@example.com" not in service._crossref_ua


def test_scihub_tools_gated_when_disabled(tmp_path: Path, monkeypatch) -> None:
    disabled = _settings(tmp_path, scihub_enabled=False)
    monkeypatch.setattr(server, "get_settings", lambda: disabled)

    with pytest.raises(RuntimeError, match="SCIHUB_ENABLED"):
        asyncio.run(server.search_scihub("10.1/x"))
    with pytest.raises(RuntimeError, match="SCIHUB_ENABLED"):
        asyncio.run(server.download_scihub_paper("10.1/x"))
