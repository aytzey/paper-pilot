from pathlib import Path
from unittest.mock import patch

import pytest

from paper_pilot.config import Settings
from paper_pilot.models import PaperRecord
from paper_pilot.services.zotero import ZoteroService


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    base = {
        "openalex_email": "you@example.com",
        "semantic_scholar_api_key": None,
        "zotero_library_id": None,
        "zotero_library_type": "user",
        "zotero_api_key": None,
        "data_dir": tmp_path,
        "libgen_mirrors": ("https://libgen.is",),
        "libgen_timeout_sec": 10.0,
        "unpaywall_email": "you@example.com",
        "zotero_local": False,
        "zotero_connector_url": "http://127.0.0.1:23119/connector/saveItems",
        "zotero_bridge_url": "http://127.0.0.1:24119",
    }
    base.update(overrides)
    return Settings(**base)


def test_local_mode_defaults_to_user_library_zero(tmp_path: Path) -> None:
    settings = _settings(tmp_path, zotero_local=True)

    assert settings.zotero_mode == "local"
    assert settings.effective_zotero_library_id == "0"
    assert settings.zotero_enabled is True


def test_local_client_uses_pyzotero_local_flag(tmp_path: Path) -> None:
    service = ZoteroService(_settings(tmp_path, zotero_local=True))

    with patch("paper_pilot.services.zotero.pyzotero.Zotero") as zotero_ctor:
        service._client()

    args, kwargs = zotero_ctor.call_args
    assert args[:3] == ("0", "user", None)
    assert kwargs["local"] is True


def test_local_collection_creation_requires_bridge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = ZoteroService(_settings(tmp_path, zotero_local=True, zotero_bridge_url=None))
    monkeypatch.setattr(service, "_list_collections_sync", lambda query=None: [])
    monkeypatch.setattr(service, "_local_api_check", lambda: {"reachable": True})

    with pytest.raises(RuntimeError, match="ZOTERO_BRIDGE_URL"):
        service._resolve_collection_sync(None, None, "Research - Test")


def test_zotero_data_dir_config_and_staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    zdir = tmp_path / "zotero-data"
    zdir.mkdir()
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    service = ZoteroService(_settings(tmp_path, zotero_local=True, zotero_data_dir=str(zdir)))

    assert service._zotero_data_dir() == zdir.resolve()

    # a PDF already under the data dir is imported in place
    inside = zdir / "in.pdf"
    inside.write_bytes(b"%PDF-1.4")
    assert service._stage_path_for_local_zotero(inside) == inside.resolve()

    # a PDF outside the data dir and home is copied into the staging folder
    outside = tmp_path / "elsewhere" / "p.pdf"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_bytes(b"%PDF-1.4 body")
    staged = service._stage_path_for_local_zotero(outside)
    assert staged.parent == (zdir.resolve() / ".paper-pilot-staging")
    assert staged.read_bytes().startswith(b"%PDF")


def test_connector_item_marks_arxiv_metadata(tmp_path: Path) -> None:
    service = ZoteroService(_settings(tmp_path, zotero_local=True))
    paper = PaperRecord(
        source="arxiv",
        source_id="2401.12345",
        title="Test Paper",
        authors=["Jane Doe"],
        year=2024,
        url="https://arxiv.org/abs/2401.12345",
    )

    item = service._connector_item_for_paper(paper, "test-topic")

    assert item["itemType"] == "preprint"
    assert item["archive"] == "arXiv"
    assert item["archiveID"] == "arXiv:2401.12345"
