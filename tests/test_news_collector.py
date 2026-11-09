import httpx

from app.news.collector import _fetch


async def test_feed_retries_connect_timeout_but_rejects_html():
    attempts = []

    def publisher(request):
        attempts.append(request.url.path)
        if request.url.path == "/directory":
            return httpx.Response(200, text="<html>RSS directory</html>")
        if attempts.count("/feed") == 1:
            raise httpx.ConnectTimeout("temporary timeout", request=request)
        return httpx.Response(200, text="""<rss version="2.0"><channel><title>News</title>
            <item><title>Verified headline</title><link>https://example.test/story</link></item>
            </channel></rss>""")

    async with httpx.AsyncClient(transport=httpx.MockTransport(publisher)) as client:
        items = await _fetch(client, "Publisher", "world", "https://example.test/feed")
        assert [row["title"] for row in items] == ["Verified headline"]
        assert await _fetch(client, "Publisher", "world", "https://example.test/directory") == []
    assert attempts == ["/feed", "/feed", "/directory"]
