"""
Curated RSS feed registry for the Tier-1 news watcher.

Zero-cost, keyless, always-on: Turkish + English outlets that publish full RSS.
The collector (app/news/collector.py) fetches every enabled feed concurrently.
Owners can add feeds without a deploy via the `news_extra_feeds` setting
(comma-separated URLs); those join as outlet "custom".

Feed rot is expected — outlets change RSS paths silently. The collector isolates
per-feed failures (one 404 never fails a poll) and the router surfaces per-feed
health, so a dead URL here is a warning, not an outage.
"""

from app.config import settings

# (outlet, category, url). Categories are the outlet's own section names; they
# feed news_item.category for category-filtered briefings.
FEEDS: list[tuple[str, str, str]] = [
    # ── NTV ──────────────────────────────────────────────────────────────────
    ("NTV", "gundem", "https://www.ntv.com.tr/gundem.rss"),
    ("NTV", "dunya", "https://www.ntv.com.tr/dunya.rss"),
    ("NTV", "ekonomi", "https://www.ntv.com.tr/ekonomi.rss"),
    ("NTV", "teknoloji", "https://www.ntv.com.tr/teknoloji.rss"),
    ("NTV", "saglik", "https://www.ntv.com.tr/saglik.rss"),
    # ── Hürriyet ─────────────────────────────────────────────────────────────
    ("Hürriyet", "anasayfa", "https://www.hurriyet.com.tr/rss/anasayfa"),
    ("Hürriyet", "gundem", "https://www.hurriyet.com.tr/rss/gundem"),
    ("Hürriyet", "ekonomi", "https://www.hurriyet.com.tr/rss/ekonomi"),
    ("Hürriyet", "dunya", "https://www.hurriyet.com.tr/rss/dunya"),
    ("Hürriyet", "teknoloji", "https://www.hurriyet.com.tr/rss/teknoloji"),
    # ── Milliyet ─────────────────────────────────────────────────────────────
    ("Milliyet", "gundem", "https://www.milliyet.com.tr/rss/rssNew/gundemRss.xml"),
    ("Milliyet", "ekonomi", "https://www.milliyet.com.tr/rss/rssNew/ekonomiRss.xml"),
    ("Milliyet", "dunya", "https://www.milliyet.com.tr/rss/rssNew/dunyaRss.xml"),
    # ── Sabah ────────────────────────────────────────────────────────────────
    ("Sabah", "gundem", "https://www.sabah.com.tr/rss/gundem.xml"),
    ("Sabah", "ekonomi", "https://www.sabah.com.tr/rss/ekonomi.xml"),
    ("Sabah", "dunya", "https://www.sabah.com.tr/rss/dunya.xml"),
    ("Sabah", "teknoloji", "https://www.sabah.com.tr/rss/teknoloji.xml"),
    # ── Yeni Akit ────────────────────────────────────────────────────────────
    ("Yeni Akit", "ekonomi", "https://www.yeniakit.com.tr/rss/haber/ekonomi"),
    ("Yeni Akit", "dunya", "https://www.yeniakit.com.tr/rss/haber/dunya"),
    ("Yeni Akit", "teknoloji", "https://www.yeniakit.com.tr/rss/haber/teknoloji"),
    # ── English outlets ──────────────────────────────────────────────────────
    ("Daily Sabah", "world", "https://www.dailysabah.com/rss"),
    ("Yeni Şafak EN", "news", "https://www.yenisafak.com/en/rss"),
    # ── Additional Turkish outlets ───────────────────────────────────────────
    ("Haber7", "gundem", "https://www.haber7.com/rss"),
    ("Star", "gundem", "https://www.star.com.tr/rss/rss.asp"),
]


def enabled_feeds() -> list[tuple[str, str, str]]:
    """The active feed list: the curated registry plus any owner extras.

    Extras come from `news_extra_feeds` (comma-separated URLs) and are tagged
    outlet "Custom", category "" so they flow through the same pipeline.
    """
    feeds = list(FEEDS)
    extra = (settings.news_extra_feeds or "").strip()
    if extra:
        for url in (u.strip() for u in extra.split(",")):
            if url:
                feeds.append(("Custom", "", url))
    return feeds
