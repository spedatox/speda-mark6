# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Splitting the registry monoliths into one file per entity.

`projects.md` reached 38 KB and `social.md` 27 KB, and the memory tool has no
section-addressed read — so answering "what's the stack on Mark VI" fetched the
whole document (~10k tokens) and re-sent it on every following iteration of the
agentic loop. One entity per file makes that read ~1k and removes the write
hazard the verifier exists to catch, because a `str_replace` can only reach the
one entity its file holds.

Two properties are load-bearing and are what these tests pin down:

  1. The split is a CUT. Every byte of an entity's prose survives; the only edit
     is heading level. v3 asked a model to re-express the finance ledger and lost
     every relationship between its figures (v4 §2.3) — nothing here reads a word
     it writes.
  2. It refuses to guess. An ungrouped person, two names that collide on one
     filename, a heading with no body: each is reported and skipped. A migration
     that guesses is how `social.md` acquired a person called "Professional".
"""

import pytest

from app.services.memory_schema import MemorySchemaViolation, check_write, is_canonical
from app.services.memory_spec import collection_by_root, member_path, slugify, spec_for
from app.services.memory_split import plan_split
from app.services.memory_verify import verify_document
from app.services.memory_write import registry_upsert

PROJECTS = collection_by_root("/memories/projects")
SOCIAL = collection_by_root("/memories/social")


# ── Paths and names ───────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "title,expected",
    [
        ("Speda Mark VI", "speda-mark-vi"),
        # Turkish. `ı` and `ğ` have no compatibility decomposition, so an
        # NFKD-based slug leaves them intact and the path stops round-tripping.
        # "Pınar Uzun" and "Doğan" are real entries.
        ("Pınar Uzun", "pinar-uzun"),
        ("Doğan Şahin", "dogan-sahin"),
        ("İsmail Öztürk", "ismail-ozturk"),
        ("  Spaced   Out  ", "spaced-out"),
    ],
)
def test_slug_is_stable_and_turkish_safe(title, expected):
    assert slugify(title) == expected


def test_member_path_needs_a_real_category():
    assert member_path(SOCIAL, "Osman Bayrak", "Personal") == (
        "/memories/social/personal/osman-bayrak.md"
    )
    assert member_path(PROJECTS, "HISAR") == "/memories/projects/hisar.md"
    with pytest.raises(ValueError):
        member_path(SOCIAL, "Osman Bayrak", "Invented Category")
    with pytest.raises(ValueError):
        member_path(SOCIAL, "Osman Bayrak")


def test_member_resolves_to_a_spec_and_a_tight_cap():
    spec = spec_for("/memories/projects/hisar.md")
    assert spec is not None and spec.kind == "registry"
    # One entity has no business being large — that is the point of splitting.
    assert spec.max_bytes < spec_for("/memories/projects.md").max_bytes
    assert spec_for("/memories/social/bogus/x.md") is None


# ── The cut ───────────────────────────────────────────────────────────────────

PROJECTS_DOC = """# Active Projects

_One section per project._

## Speda Mark VI
Status: Active
Stack: FastAPI

### Architecture
```
app/
# a hash inside a fence is not a heading
### and neither is this
```

### Team
Solo.

## HISAR
Status: Paused
"""


def test_split_promotes_headings_and_respects_code_fences():
    plan = plan_split(PROJECTS_DOC, PROJECTS)
    assert [m.path for m in plan.members] == [
        "/memories/projects/speda-mark-vi.md",
        "/memories/projects/hisar.md",
    ]
    speda = plan.members[0].content
    # The entity heading becomes the document title; its children rise one level.
    assert speda.startswith("# Speda Mark VI\n")
    assert "\n## Architecture\n" in speda and "\n## Team\n" in speda
    # A directory tree inside a fence must not cut the document or get promoted.
    assert "# a hash inside a fence is not a heading" in speda
    assert "### and neither is this" in speda


def test_split_loses_no_prose():
    plan = plan_split(PROJECTS_DOC, PROJECTS)
    moved = "\n".join(m.content for m in plan.members)
    for fragment in ("Status: Active", "Stack: FastAPI", "Solo.", "Status: Paused"):
        assert fragment in moved


SOCIAL_DOC = """# Social

_Schema notes._

### Stray Person
**Who:** filed before any category heading.

## Professional
### Hakan Eren
**Who:** colleague.
**Events:**
- [2026-07-01] met.

## Personal
### Osman Bayrak
**Who:** friend.

### Osman Bayrak
**Who:** a different Osman, same spelling.
"""


def test_split_refuses_to_guess():
    plan = plan_split(SOCIAL_DOC, SOCIAL)
    kinds = {p.kind for p in plan.problems}
    assert "ungrouped" in kinds       # a person before any category
    assert "collision" in kinds       # two names mapping to one file

    written = {m.path for m in plan.members}
    assert "/memories/social/professional/hakan-eren.md" in written
    # Neither colliding entity is written: one would silently overwrite the other.
    assert "/memories/social/personal/osman-bayrak.md" not in written
    assert not plan.clean


def test_split_reports_the_sections_it_actually_found():
    # CollectionSpec.sections is deliberately empty until the real document has
    # been read. This histogram is the only honest source for filling it in —
    # a spec guessed from a design document is what makes a verifier cry wolf.
    plan = plan_split(PROJECTS_DOC, PROJECTS)
    assert plan.as_dict()["sections_seen"] == {"Architecture": 1, "Team": 1}


# ── The gate ──────────────────────────────────────────────────────────────────

def test_a_new_entity_file_is_expected_not_a_new_file_type():
    # The taxonomy stays closed, but a new project is one more instance of a kind
    # that already has a spec. Without this the closed-taxonomy rule rejects every
    # project and person the owner ever acquires again.
    assert is_canonical("/memories/projects/hisar.md")
    assert is_canonical("/memories/social/personal/osman-bayrak.md")
    assert not is_canonical("/memories/social/invented/x.md")
    assert not is_canonical("/memories/brand-new-idea.md")


def test_the_monolith_is_frozen_for_agents_but_not_for_orion():
    # Deployment is GitOps, so this code reaches prod before the split runs.
    # Freezing on deploy is what stops the two stores being writable at once —
    # the v1 "facts drift between files" failure, with both files authoritative.
    with pytest.raises(MemorySchemaViolation) as e:
        check_write(
            path="/memories/projects.md", before="# P", after="# P\n## New",
            is_create=False, author="speda",
        )
    assert "/memories/projects/" in str(e.value)

    # Orion repairs documents for a living; the owner is never blocked (§4.3).
    check_write(
        path="/memories/projects.md", before="# P", after="# P\n## New",
        is_create=False, author="orion",
    )


def test_a_person_file_still_needs_a_who_block():
    with pytest.raises(MemorySchemaViolation):
        check_write(
            path="/memories/social/personal/x.md", before="",
            after="# X\n\nsome loose text\n", is_create=True, author="speda",
        )


# ── Writing one entity ────────────────────────────────────────────────────────

def test_upsert_creates_the_full_shape_then_keeps_events_newest_first():
    path = "/memories/social/personal/osman-bayrak.md"
    doc = registry_upsert(
        "", path=path, entity="Osman Bayrak",
        who="friend from Bursa.", event="called", when="2026-08-01",
    )
    assert doc.startswith("# Osman Bayrak\n")
    assert "**Who:** friend from Bursa." in doc
    assert not verify_document(path, doc)

    doc = registry_upsert(doc, path=path, entity="Osman Bayrak",
                          event="met for coffee", when="2026-08-11")
    events = [l for l in doc.splitlines() if l.startswith("- [")]
    assert events == ["- [2026-08-11] met for coffee", "- [2026-08-01] called"]

    doc = registry_upsert(doc, path=path, entity="Osman Bayrak", who="now in Ankara.")
    assert "**Who:** now in Ankara." in doc
    assert len([l for l in doc.splitlines() if l.startswith("**Who:**")]) == 1


def test_upsert_does_not_force_a_person_shape_onto_a_project():
    doc = registry_upsert(
        "", path="/memories/projects/hisar.md", entity="HISAR",
        who="macOS-style file-transfer UI.",
    )
    assert doc.startswith("# HISAR\n")
    assert "**Who:**" not in doc


def test_title_and_filename_must_agree():
    # The filename is the entity's identity. Two of them disagreeing means one is
    # about somebody else — the split's replacement for the heading-level check
    # that a single document needed.
    findings = verify_document(
        "/memories/social/personal/osman-bayrak.md",
        "# Semra Bayrak\n\n**Who:** x\n\n**Events:**\n- [2026-08-01] y\n",
    )
    assert any(f.rule == "member_title" for f in findings)
