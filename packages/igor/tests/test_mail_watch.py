"""Unit tests for the mail watch's pure filtering logic.

This is the half of the pipeline that decides whether an agentic turn happens at
all, so a false positive costs money and a false negative loses mail. Gmail's
own `from:` operator is too loose to be the answer (it token-matches, so
`from:tdv.org` also hits `info@nottdv.org` and display names containing the
string) — these tests pin the strict check that runs on top of it.
"""

from app.services.mail_watch import _sender_address, build_query, domain_matches


def test_domain_matches_exact_and_subdomain():
    assert domain_matches("burs@tdv.org", "tdv.org")
    assert domain_matches("bilgi@mail.tdv.org", "tdv.org")


def test_domain_rejects_lookalikes():
    # The whole reason the strict check exists — Gmail's fuzzy from: lets these through.
    assert not domain_matches("info@nottdv.org", "tdv.org")
    assert not domain_matches("spam@tdv.org.evil.com", "tdv.org")
    assert not domain_matches("someone@tdv.com", "tdv.org")
    assert not domain_matches("", "tdv.org")
    assert not domain_matches("burs@tdv.org", "")


def test_domain_matches_is_case_and_at_insensitive():
    assert domain_matches("BURS@TDV.ORG", "tdv.org")
    assert domain_matches("burs@tdv.org", "@TDV.org")


def test_sender_address_unwraps_display_name():
    assert _sender_address('"TDV Burs" <burs@tdv.org>') == "burs@tdv.org"
    assert _sender_address("burs@tdv.org") == "burs@tdv.org"
    assert _sender_address("no address here") == ""


def test_build_query_excludes_seen_label_and_bounds_age():
    q = build_query("tdv.org")
    assert "from:tdv.org" in q
    # Without the label exclusion every poll would re-fire on the same mail.
    assert "-label:SPEDA-Seen" in q
    # Without the age floor a fresh watch fires on the whole archive at once.
    assert "newer_than:2d" in q


def test_build_query_passes_extra_terms_through():
    q = build_query("tdv.org", extra_query="has:attachment", newer_than_days=0, label="")
    assert q == "from:tdv.org has:attachment"
