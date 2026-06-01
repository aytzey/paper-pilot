from pathlib import Path

from paper_pilot.config import load_settings


def test_malformed_numeric_env_falls_back_to_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PAPER_PILOT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CACHE_TTL_SEC", "not-a-number")
    monkeypatch.setenv("SCIHUB_TIMEOUT_SEC", "abc")
    monkeypatch.setenv("MAX_DOWNLOAD_MB", "oops")

    settings = load_settings()  # must not raise

    assert settings.cache_ttl_sec == 86400
    assert settings.scihub_timeout_sec == 30.0
    assert settings.max_download_bytes == 75 * 1024 * 1024


def test_boolean_and_size_env_parsing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PAPER_PILOT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("INSECURE_SHADOW_TLS", "true")
    monkeypatch.setenv("SCIHUB_ENABLED", "yes")
    monkeypatch.setenv("MAX_DOWNLOAD_MB", "10")

    settings = load_settings()

    assert settings.insecure_shadow_tls is True
    assert settings.scihub_enabled is True
    assert settings.max_download_bytes == 10 * 1024 * 1024
    assert settings.ssl_verify is True
