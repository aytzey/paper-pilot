"""Shared network safety helpers.

Centralizes two concerns that were previously duplicated (and partly missing)
across the download paths:

* SSRF protection: only fetch ``http(s)`` URLs whose host is not an obviously
  internal / loopback / cloud-metadata target. This matters most for URLs that
  are extracted from *untrusted* HTML (Sci-Hub and LibGen mirror pages).
* Size-capped streaming downloads: never buffer an unbounded response body
  into memory; abort once a configurable byte budget is exceeded.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import httpx

PDF_MAGIC = b"%PDF"

_BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
}
_BLOCKED_SUFFIXES = (".local", ".internal", ".localhost")


class DownloadTooLargeError(RuntimeError):
    """Raised when a response body exceeds the configured size budget."""


def _ip_is_internal(ip: ipaddress._BaseAddress) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local  # 169.254.0.0/16: cloud metadata endpoint lives here
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def is_public_http_url(url: str) -> bool:
    """Return ``True`` only for ``http(s)`` URLs that target a public host.

    Blocks non-HTTP schemes, loopback/private/link-local IP literals, and a set
    of internal hostnames. Hostnames are resolved best-effort: if *any* resolved
    address is internal the URL is rejected; if resolution fails the URL is
    allowed (the request itself will then fail naturally rather than the guard
    producing false negatives for transient DNS hiccups).
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower().strip()
    if not host:
        return False
    if host in _BLOCKED_HOSTNAMES or host.endswith(_BLOCKED_SUFFIXES):
        return False

    try:
        return not _ip_is_internal(ipaddress.ip_address(host))
    except ValueError:
        pass  # not an IP literal; resolve the hostname below

    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except (socket.gaierror, OSError, UnicodeError):
        return True  # let the actual request surface the failure
    for info in infos:
        sockaddr = info[4]
        try:
            if _ip_is_internal(ipaddress.ip_address(sockaddr[0])):
                return False
        except ValueError:
            continue
    return True


async def download_capped(
    client: httpx.AsyncClient,
    url: str,
    max_bytes: int,
    *,
    require_public: bool = True,
) -> bytes:
    """Stream ``url`` into memory, aborting if it exceeds ``max_bytes``."""
    if require_public and not is_public_http_url(url):
        raise ValueError(f"Refusing to fetch non-public or non-HTTP URL: {url}")
    chunks: list[bytes] = []
    total = 0
    async with client.stream("GET", url) as response:
        response.raise_for_status()
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > max_bytes:
                raise DownloadTooLargeError(
                    f"Download exceeded {max_bytes} bytes; aborting ({url})."
                )
            chunks.append(chunk)
    return b"".join(chunks)


def download_capped_sync(
    session,
    url: str,
    max_bytes: int,
    *,
    timeout: float,
    verify=True,
    require_public: bool = True,
) -> bytes:
    """Synchronous (``requests``) counterpart to :func:`download_capped`."""
    if require_public and not is_public_http_url(url):
        raise ValueError(f"Refusing to fetch non-public or non-HTTP URL: {url}")
    chunks: list[bytes] = []
    total = 0
    with session.get(
        url,
        timeout=timeout,
        verify=verify,
        allow_redirects=True,
        stream=True,
        headers={"User-Agent": "Mozilla/5.0"},
    ) as response:
        response.raise_for_status()
        for chunk in response.iter_content(chunk_size=65536):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise DownloadTooLargeError(
                    f"Download exceeded {max_bytes} bytes; aborting ({url})."
                )
            chunks.append(chunk)
    return b"".join(chunks)
