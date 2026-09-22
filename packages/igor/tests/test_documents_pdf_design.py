from app.profiles.atomix import AtomixProfile
from app.profiles.nightcrawler import NightCrawlerProfile
from app.profiles.sentinel import SentinelProfile
from app.profiles.speda import SPEDAProfile
from app.skills.documents import _palette, _pdf_layout_css


def test_profiles_select_distinct_pdf_compositions():
    assert SPEDAProfile.doc_theme.pdf_layout == "executive"
    assert SentinelProfile.doc_theme.pdf_layout == "ledger"
    assert NightCrawlerProfile.doc_theme.pdf_layout == "dossier"
    assert AtomixProfile.doc_theme.pdf_layout == "clinical"


def test_pdf_layout_css_falls_back_to_executive_and_uses_brand_colour():
    palette = _palette("#36abca")
    executive = _pdf_layout_css("executive", palette)

    assert _pdf_layout_css("unknown", palette) == executive
    assert "#36abca" in executive
    assert "masthead" in _pdf_layout_css("dossier", palette)
