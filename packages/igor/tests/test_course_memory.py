from app.services.memory_spec import course_path, is_course_path, spec_for
from app.services.memory_write import ledger_append
from app.skills.course_memory import _append_section, _new_course


def test_course_path_is_term_and_course_scoped():
    path = course_path("2026-2027 Spring", "ata101")
    assert path == "/memories/academic/courses/2026-2027-spring/ATA101.md"
    assert is_course_path(path)
    assert not is_course_path("/memories/academic/courses/2026-2027-spring/notes.md")


def test_course_record_keeps_notes_in_their_named_places():
    path = course_path("2026-2027-spring", "ATA101")
    text = _new_course("ATA101", "Atatürk İlkeleri")
    text = _append_section(text, "Materials", "Week 1 slides saved in Drive.")
    text = ledger_append(text, path=path, key="2027-02-16", lines_in=["Covered the first reform period."])
    assert "## Materials\n\n- Week 1 slides saved in Drive." in text
    assert "### 2027-02-16" in text
    assert "- Covered the first reform period." in text
    assert spec_for(path).owner_agent == "ultron"
