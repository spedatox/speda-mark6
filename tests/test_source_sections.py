from time import monotonic
from types import SimpleNamespace

from app.services.memory_store import exact_sections


def test_large_dense_document_with_trailing_newline_is_linear_and_retains_tail():
    # Regression: a lazy regex requiring a final non-whitespace character
    # backtracked across every possible start when a dense source ended in LF.
    content = ("A dense source line without blank separators.\n" * 2000) + "FINAL CORRECTION\n"
    source = SimpleNamespace(id=1,content=content,source_role="canonical")
    start = monotonic()
    sections = exact_sections(source)
    assert monotonic() - start < 0.5
    assert sections[-1].text.endswith("FINAL CORRECTION")
    assert all(content[s.start:s.end] == s.text for s in sections)


def test_windows_paragraph_boundaries_keep_original_offsets():
    content = "# Nora\r\n\r\nFirst statement.\r\n\r\nLast statement.\r\n"
    sections = exact_sections(SimpleNamespace(id=1,content=content,source_role="canonical"))
    assert [s.text for s in sections] == ["# Nora","First statement.","Last statement."]
    assert all(content[s.start:s.end] == s.text for s in sections)
