# Downloads a submitted link once, when it is submitted; the bytes are then kept with the submission.
# Public http(s) addresses only (no localhost or private networks), redirects re-checked, size capped.

import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx

from app import config

MAX_REDIRECTS = 3
TIMEOUT_S = 20


def check_url(url: str) -> str:
    u = urlparse(url.strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("Use a full http:// or https:// link.")
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise ValueError(f"Couldn't find {u.hostname}.") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            raise ValueError("That address is not public.")
    return url.strip()


async def download(url: str, max_bytes: int) -> tuple[bytes, str, str]:
    # Returns (bytes, content type, final url).
    current = await asyncio.to_thread(check_url, url)
    async with httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=False,
                                 headers={"User-Agent": config.NOMINATIM_USER_AGENT}) as client:
        for _ in range(MAX_REDIRECTS + 1):
            async with client.stream("GET", current) as r:
                if r.is_redirect:
                    current = await asyncio.to_thread(check_url, urljoin(current, r.headers.get("location", "")))
                    continue
                r.raise_for_status()
                chunks, total = [], 0
                async for chunk in r.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError(f"The linked file is over {max_bytes // (1024 * 1024)} MB.")
                    chunks.append(chunk)
                return b"".join(chunks), r.headers.get("content-type", ""), current
    raise ValueError("Too many redirects.")
