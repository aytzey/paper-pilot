import asyncio

import httpx
import pytest

from paper_pilot.services.net import (
    DownloadTooLargeError,
    download_capped,
    is_public_http_url,
)


def test_is_public_http_url_blocks_internal_and_non_http() -> None:
    # public IP literals are allowed (no DNS needed -> hermetic)
    assert is_public_http_url("https://93.184.216.34/paper.pdf") is True
    # internal / loopback / cloud-metadata / non-http are blocked
    assert is_public_http_url("http://127.0.0.1/x") is False
    assert is_public_http_url("http://169.254.169.254/latest/meta-data") is False
    assert is_public_http_url("http://10.0.0.5/x") is False
    assert is_public_http_url("http://192.168.1.1/x") is False
    assert is_public_http_url("https://localhost/x") is False
    assert is_public_http_url("ftp://example.com/x") is False
    assert is_public_http_url("file:///etc/passwd") is False
    assert is_public_http_url("not a url") is False


def test_download_capped_enforces_size_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"%PDF" + b"x" * 1000)

    async def run() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            # generous cap -> succeeds
            data = await download_capped(client, "https://93.184.216.34/a.pdf", 10_000)
            assert data.startswith(b"%PDF")
            # tiny cap -> aborts
            with pytest.raises(DownloadTooLargeError):
                await download_capped(client, "https://93.184.216.34/a.pdf", 10)

    asyncio.run(run())


def test_download_capped_rejects_internal_url() -> None:
    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as client:
            with pytest.raises(ValueError):
                await download_capped(client, "http://127.0.0.1/secret.pdf", 10_000)

    asyncio.run(run())
