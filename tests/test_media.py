import asyncio
from io import BytesIO

import httpx
import pytest
from PIL import Image

from app.main import create_app
from app.services import media


def png():
    buffer = BytesIO()
    Image.new('RGB', (3, 2), 'blue').save(buffer, 'PNG')
    return buffer.getvalue()


async def test_image_proxy_requires_auth_and_serves_verified_bytes(monkeypatch):
    data = png()

    async def download(url):
        assert str(url) == 'https://archive.example/photo'
        return data

    monkeypatch.setattr(media, '_download', download)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url='http://test') as client:
        path = '/media/proxy?url=https://archive.example/photo'
        assert (await client.get(path)).status_code == 401
        image = await client.get(path, headers={'X-API-Key': 'test-only-key'})
        assert image.status_code == 200
        assert image.headers['content-type'] == 'image/png'
        assert image.content == data
        data = b'<html>The image needs a login</html>'
        assert (await client.get(path, headers={'X-API-Key': 'test-only-key'})).status_code == 404


async def test_image_proxy_pins_host_and_rechecks_each_redirect(monkeypatch):
    addresses = {'archive.example': '93.184.215.14', 'cdn.example': '93.184.215.15', 'internal.example': '127.0.0.1'}
    destinations = []
    image = png()

    async def resolve(host, port, **kwargs):
        return [(None, None, None, '', (addresses[host], port))]

    def request(req):
        host = req.headers['Host']
        destinations.append((host, req.url.host, req.url.path))
        assert req.extensions['sni_hostname'] == host
        assert req.headers.get('X-API-Key') is None
        if req.url.path == '/unsafe':
            return httpx.Response(302, headers={'location': 'http://internal.example/image'})
        if req.url.path == '/start':
            return httpx.Response(302, headers={'location': '/image'})
        if host == 'archive.example':
            return httpx.Response(302, headers={'location': 'https://cdn.example/image'})
        return httpx.Response(200, stream=httpx.ByteStream(image))

    monkeypatch.setattr(asyncio.get_running_loop(), 'getaddrinfo', resolve)
    client_type = httpx.AsyncClient
    monkeypatch.setattr(media.httpx, 'AsyncClient', lambda **kwargs: client_type(transport=httpx.MockTransport(request)))
    assert await media.fetch_image('https://archive.example/start') == (image, 'image/png')
    assert destinations == [
        ('archive.example', addresses['archive.example'], '/start'),
        ('archive.example', addresses['archive.example'], '/image'),
        ('cdn.example', addresses['cdn.example'], '/image'),
    ]
    with pytest.raises(media.ImageUnavailable):
        await media.fetch_image('https://archive.example/unsafe')
    assert destinations[-1][0] == 'archive.example'
