import httpx
import pytest

from app.news.collector import _fetch


@pytest.mark.parametrize("failure", [httpx.ConnectTimeout, httpx.ReadError, httpx.ReadTimeout, httpx.RemoteProtocolError])
async def test_feed_retries_transient_transport_failures_but_rejects_html(failure):
    attempts = []

    def publisher(request):
        attempts.append(request.url.path)
        if request.url.path == "/directory":
            return httpx.Response(200, text="<html>RSS directory</html>")
        if attempts.count("/feed") == 1:
            raise failure("temporary transport failure", request=request)
        return httpx.Response(200, text="""<rss version="2.0"><channel><title>News</title>
            <item><title>Verified headline</title><link>https://example.test/story</link></item>
            </channel></rss>""")

    async with httpx.AsyncClient(transport=httpx.MockTransport(publisher)) as client:
        items = await _fetch(client, "Publisher", "world", "https://example.test/feed")
        assert [row["title"] for row in items] == ["Verified headline"]
        assert await _fetch(client, "Publisher", "world", "https://example.test/directory") == []
    assert attempts == ["/feed", "/feed", "/directory"]
