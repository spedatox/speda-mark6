"""Unit tests for the browser desk's pure logic.

Everything here runs without the Playwright container: the two decisions worth
pinning are made on this side of the network, and both of them are the kind that
fails silently rather than loudly.

`looks_like_login` decides whether an agent is reading the owner's grades or a
login form. Get it wrong in one direction and it re-authenticates needlessly
(six seconds); get it wrong in the other and an agent reports the contents of a
sign-in page as fact.

`lines_from_render` has to agree with `extract_lines` about what a line IS,
because one web-watch snapshot is fed by both — a watch that renders once and
fetches the next poll would otherwise report the entire page as newly published.
"""

from app.services.browser import format_page, looks_like_login, portal_allows
from app.services.web_watch import extract_lines, lines_from_render


# ── Is this a login wall? ────────────────────────────────────────────────────


def test_the_sidecars_password_field_report_settles_it():
    # The only signal here that is a fact rather than a reading of the page.
    assert looks_like_login({"url": "https://x/anything", "has_password": True}, {})
    assert not looks_like_login({"url": "https://x/login", "has_password": False}, {})


def test_configured_url_marker_outranks_even_the_password_report():
    # For the portal that keeps a hidden login form on every page — where the
    # password report is a permanent false positive and the owner said so.
    portal = {"success_url_contains": "/dashboard"}
    inside = {"url": "https://x/dashboard/main", "has_password": True}
    assert not looks_like_login(inside, portal)


def test_configured_url_marker_wins_over_the_page_text():
    # The owner naming the signed-in URL is them stating outright what "inside"
    # looks like. It outranks anything the page says about passwords.
    portal = {"success_url_contains": "/dashboard"}
    inside = {"url": "https://obs.example.edu.tr/dashboard/main", "text": "şifre değiştir"}
    outside = {"url": "https://obs.example.edu.tr/login.aspx", "text": "hoş geldiniz"}
    assert not looks_like_login(inside, portal)
    assert looks_like_login(outside, portal)


def test_password_field_in_the_aria_tree_is_a_login_wall():
    page = {
        "url": "https://portal.example.edu.tr/",
        "aria": '- textbox "Kullanıcı Adı"\n- textbox "Şifre"\n- button "Giriş"',
    }
    assert looks_like_login(page, {})


def test_login_shaped_url_alone_is_not_enough():
    # /login on its own is a false positive generator: plenty of signed-in pages
    # keep it in the path. It has to be backed by what the page actually says.
    page = {"url": "https://x.edu.tr/login/home", "title": "Ders Programı",
            "text": "Pazartesi 09:00 Fizik II"}
    assert not looks_like_login(page, {})


def test_login_url_plus_login_words_is_a_login_wall():
    page = {"url": "https://x.edu.tr/login", "title": "Giriş",
            "text": "Kullanıcı adı ve şifrenizi giriniz"}
    assert looks_like_login(page, {})


def test_ordinary_page_is_not_a_login_wall():
    page = {"url": "https://x.edu.tr/duyurular", "title": "Duyurular",
            "text": "Vize sonuçları açıklandı."}
    assert not looks_like_login(page, {})


# ── Extractor parity ─────────────────────────────────────────────────────────


def test_render_and_html_extractors_agree_on_line_shape():
    html = (
        "<html><body><h1>Duyurular</h1>"
        "<p>Vize sonuçları açıklandı.</p>"
        '<a href="/files/takvim.pdf">Akademik Takvim</a>'
        "</body></html>"
    )
    rendered = {
        "text": "Duyurular\nVize sonuçları açıklandı.\nAkademik Takvim",
        "links": [{"text": "Akademik Takvim", "href": "/files/takvim.pdf"}],
    }
    from_html = set(extract_lines(html))
    from_render = set(lines_from_render(rendered))

    # The publication on these pages IS the new link, so the anchor line is the
    # one that must survive both paths identically.
    assert "Akademik Takvim [/files/takvim.pdf]" in from_html
    assert "Akademik Takvim [/files/takvim.pdf]" in from_render
    assert "Vize sonuçları açıklandı." in from_html & from_render


def test_render_extractor_honours_ignore_and_drops_duplicates():
    page = {
        "text": "Ziyaretçi sayısı: 41221\nVize sonuçları açıklandı.\nVize sonuçları açıklandı.",
        "links": [],
    }
    lines = lines_from_render(page, ignore=r"ziyaretçi sayısı")
    assert lines == ["Vize sonuçları açıklandı."]


def test_render_extractor_drops_punctuation_fragments():
    page = {"text": "»\n-\nGerçek bir satır", "links": []}
    assert lines_from_render(page) == ["Gerçek bir satır"]


# ── Per-agent portal access ──────────────────────────────────────────────────


def test_empty_allowlist_means_every_agent():
    # Right for a library catalogue; the owner narrows it for anything that isn't.
    assert portal_allows({"allowed_agents": []}, "sentinel")
    assert portal_allows({}, "nightcrawler")


def test_named_allowlist_excludes_everyone_else():
    portal = {"allowed_agents": ["ultron"]}
    assert portal_allows(portal, "ultron")
    assert not portal_allows(portal, "sentinel")


# ── What the model sees ──────────────────────────────────────────────────────


def test_format_page_truncates_and_says_so():
    page = {"url": "https://x", "title": "T", "text": "a" * 500, "links": []}
    out = format_page(page, limit=100)
    assert "…(truncated)" in out
    assert "a" * 100 in out
    assert "a" * 101 not in out


def test_format_page_names_an_empty_render_rather_than_returning_nothing():
    # "" back from a tool reads as a bug; the actual finding is that the page
    # rendered and had nothing to read, which is a login wall's signature.
    out = format_page({"url": "https://x", "title": "", "text": "", "links": []})
    assert "no readable text" in out


def test_format_page_lists_links_once_each():
    page = {
        "url": "https://x", "title": "T", "text": "body",
        "links": [
            {"text": "Notlar", "href": "/notlar"},
            {"text": "Notlarım", "href": "/notlar"},   # same target, different label
            {"text": "", "href": "/gizli"},            # unlabelled — unusable to the model
        ],
    }
    out = format_page(page)
    assert out.count("/notlar") == 1
    assert "/gizli" not in out
