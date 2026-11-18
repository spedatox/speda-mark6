"""Fetch bounded public raster images for conversation presentation."""

import asyncio
import socket
from io import BytesIO
from ipaddress import IPv6Address, ip_address, ip_network

import httpx
from PIL import Image

MAX_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 16_000_000
MAX_URL_LENGTH = 4096
REDIRECTS = {301, 302, 303, 307, 308}
FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "GIF": "image/gif", "WEBP": "image/webp"}
GLOBAL_IPV6 = ip_network("2000::/3")


class ImageUnavailable(Exception):
    """The supplied URL could not be served as a public image."""


async def _public_addresses(url: httpx.URL) -> list[str]:
    if url.scheme not in {"http", "https"} or url.port is not None or not url.host or url.userinfo:
        raise ImageUnavailable
    host = url.raw_host.decode("ascii")
    if "%" in host:
        raise ImageUnavailable
    port = 443 if url.scheme == "https" else 80
    records = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses = []
    for record in records:
        address = ip_address(record[4][0])
        if not address.is_global or address.is_multicast:
            continue
        if isinstance(address, IPv6Address) and (
            address not in GLOBAL_IPV6 or address.sixtofour or address.teredo
        ):
            continue
        addresses.append(str(address))
    if not addresses:
        raise ImageUnavailable
    return list(dict.fromkeys(addresses))


async def _download(url: httpx.URL) -> bytes:
    async with httpx.AsyncClient(trust_env=False, timeout=4, limits=httpx.Limits(max_keepalive_connections=0)) as client:
        for _ in range(4):
            addresses = await _public_addresses(url)
            for address in addresses:
                # Connect to the checked address. Preserve the publisher's Host and TLS name.
                target = url.copy_with(host=address, fragment=None)
                client.cookies.clear()
                try:
                    async with client.stream("GET", target, headers={
                        "Host": url.netloc.decode("ascii"),
                        "Accept": ", ".join(FORMATS.values()),
                        "Accept-Encoding": "identity",
                        "User-Agent": "SPEDA-Mark-VI (+https://github.com/spedatox/speda-mark6)",
                    }, extensions={"sni_hostname": url.raw_host.decode("ascii")}) as response:
                        if response.status_code in REDIRECTS and (location := response.headers.get("location")):
                            url = url.join(location)
                            if len(str(url)) > MAX_URL_LENGTH:
                                raise ImageUnavailable
                            break
                        encoding = response.headers.get("content-encoding", "identity").lower()
                        length = response.headers.get("content-length", "")
                        if response.status_code != 200 or encoding != "identity":
                            raise ImageUnavailable
                        if length.isdecimal() and int(length) > MAX_BYTES:
                            raise ImageUnavailable
                        body = bytearray()
                        async for chunk in response.aiter_raw():
                            if len(body) + len(chunk) > MAX_BYTES:
                                raise ImageUnavailable
                            body.extend(chunk)
                        return bytes(body)
                except (httpx.ConnectError, httpx.ConnectTimeout):
                    continue
            else:
                raise ImageUnavailable
    raise ImageUnavailable


def _image_type(data: bytes) -> str:
    try:
        with Image.open(BytesIO(data), formats=list(FORMATS)) as image:
            if image.width * image.height > MAX_PIXELS:
                raise ImageUnavailable
            image.verify()
            return FORMATS[image.format]
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        raise ImageUnavailable from exc


async def fetch_image(source: str) -> tuple[bytes, str]:
    try:
        async with asyncio.timeout(10):
            data = await _download(httpx.URL(source))
            return data, await asyncio.to_thread(_image_type, data)
    except (TimeoutError, OSError, httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
        raise ImageUnavailable from exc
