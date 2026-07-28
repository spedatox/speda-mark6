"""Unit tests for the web watch's pure extraction/diff logic.

This decides whether a page publication reaches the owner and whether an
agentic turn is spent, so both failure directions are tested: a real
announcement must survive extraction (with its link), and a rotating clock must
not be able to fake one.
"""

from app.services.web_watch import (
    extract_lines,
    fingerprint,
    matches_terms,
    page_title,
)

CALENDAR_HTML = """
<html><head><title>Akademik Takvim</title>
<style>.x{color:red}</style><script>var t=Date.now();</script></head>
<body>
  <nav><a href="/">Ana Sayfa</a><a href="/iletisim">İletişim</a></nav>
  <div id="clock">Son güncelleme: 28.07.2026 14:33</div>
  <ul>
    <li><a href="/files/takvim-2025.pdf">2025-2026 Akademik Takvim</a></li>
  </ul>
  <footer>&copy; Üniversite &nbsp; 2026</footer>
</body></html>
"""


def test_anchor_keeps_href_so_a_published_pdf_is_reachable():
    lines = extract_lines(CALENDAR_HTML)
    assert any("2025-2026 Akademik Takvim [/files/takvim-2025.pdf]" in l for l in lines)


def test_script_and_style_are_not_treated_as_text():
    lines = extract_lines(CALENDAR_HTML)
    joined = " ".join(lines)
    assert "Date.now" not in joined
    assert "color:red" not in joined


def test_entities_are_unescaped():
    assert any("© Üniversite" in l for l in extract_lines(CALENDAR_HTML))


def test_ignore_regex_drops_the_rotating_clock():
    lines = extract_lines(CALENDAR_HTML, ignore="son güncelleme|©")
    assert not any("Son güncelleme" in l for l in lines)
    assert not any("©" in l for l in lines)
    # …without taking the real content with it.
    assert any("Akademik Takvim" in l for l in lines)


def test_clock_alone_does_not_change_the_fingerprint_when_ignored():
    later = CALENDAR_HTML.replace("28.07.2026 14:33", "28.07.2026 15:03")
    assert fingerprint(extract_lines(CALENDAR_HTML, ignore="son güncelleme")) == fingerprint(
        extract_lines(later, ignore="son güncelleme")
    )
    # And without the ignore rule it DOES — i.e. the rule is what buys silence,
    # not luck.
    assert fingerprint(extract_lines(CALENDAR_HTML)) != fingerprint(extract_lines(later))


def test_new_announcement_shows_up_as_an_added_line():
    published = CALENDAR_HTML.replace(
        "</ul>",
        '<li><a href="/files/vize-sonuc.pdf">Vize sınav sonuçları açıklandı</a></li></ul>',
    )
    before = set(extract_lines(CALENDAR_HTML, ignore="son güncelleme"))
    added = [l for l in extract_lines(published, ignore="son güncelleme") if l not in before]
    assert added == ["Vize sınav sonuçları açıklandı [/files/vize-sonuc.pdf]"]


def test_keyword_match_folds_turkish_case_and_diacritics():
    line = "VİZE SINAV SONUÇLARI AÇIKLANDI"
    assert matches_terms(line, ["sınav sonuç"])
    assert matches_terms(line, ["vize"])
    assert not matches_terms(line, ["bütünleme"])
    # No terms configured = report anything new.
    assert matches_terms(line, [])


def test_page_title():
    assert page_title(CALENDAR_HTML) == "Akademik Takvim"
    assert page_title("<html><body>no title</body></html>") == ""


def test_javascript_rendered_page_yields_nothing_rather_than_garbage():
    spa = '<html><head><title>App</title></head><body><div id="root"></div><script>x()</script></body></html>'
    assert extract_lines(spa) == []
