"""Unit tests for the news collector's pure dedup/normalization logic."""

from app.news.dedup import canonical_url, normalize_text, title_hash


def test_normalize_turkish_diacritics_fold():
    # The İ-decomposition bug: "OSTİM" must fold to plain "ostim" (no combining dot).
    assert "ostim" in normalize_text("OSTİM Savunma")
    assert "siber" in normalize_text("Büyük Siber Saldırı")
    assert normalize_text("Şırnak Çığ") == "sirnak cig"


def test_canonical_url_strips_tracking_and_fragment():
    a = canonical_url("https://www.NTV.com.tr/gundem/x?utm_source=fb&id=5#top")
    b = canonical_url("https://www.ntv.com.tr/gundem/x?id=5")
    assert a == b
    assert "utm_source" not in a
    assert "#top" not in a


def test_canonical_url_trailing_slash():
    assert canonical_url("https://x.com/a/") == canonical_url("https://x.com/a")


def test_title_hash_collapses_wording_variants():
    # Same story, punctuation/diacritic variants → same fingerprint.
    h1 = title_hash("Merkez Bankası faizi 500 baz puan artırdı")
    h2 = title_hash("Merkez Bankasi faizi 500 baz puan artirdi!")
    assert h1 == h2


def test_title_hash_distinguishes_different_stories():
    h1 = title_hash("Merkez Bankası faizi artırdı")
    h2 = title_hash("Tamamen farklı bir haber başlığı")
    assert h1 != h2
