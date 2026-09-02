# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Sharding a ledger member by its index key — `finance/ledger/2026-09.md`.

The monthly ledger was one file gathering every `## YYYY-MM`. It is now a
DIRECTORY, one file per month, on the owner's instruction. Splitting by topic
still comes first — the repayment schedules stayed in `scholarships-and-loans`
and did not get scattered through the months — and this is that same cut one
storey down, applied only to the member whose index grows without bound.

What these tests pin down is what makes it safe:

  1. It is a CUT, like every other split here. Every table row survives, only
     the heading level changes, and nothing regenerates a word. v3 asked a model
     to re-express this exact ledger and lost every relationship between its
     figures (v4 §2.3).
  2. The KEY decides the file. An agent says `finance` and `2026-09`; nothing
     anywhere builds that path by hand, so a September row cannot land in
     August's file — and a month that does not exist yet is created rather than
     refused.
  3. The flat file it replaces is frozen, not silently writable beside it. Two
     authoritative ledgers is the failure the taxonomy exists to prevent.
"""

import pytest

from app.services.memory_schema import MemorySchemaViolation, check_write, max_bytes_for
from app.services.memory_spec import (
    collection_by_root,
    route_ledger,
    shard_member,
    shard_path,
    spec_for,
)
from app.services.memory_split import plan_shard
from app.services.memory_verify import verify_document
from app.services.memory_write import WriteRejected, ledger_append

FINANCE = collection_by_root("/memories/finance")
LEDGER = FINANCE.member("ledger")

# Two months in the shape the real document uses: the month at `##`, its tables
# under `###`. Taken from the owner's ledger, trimmed.
FLAT = """# Aylık Defter

## 2026-08

### Incomes

| Date | Source | Amount (TL) | Notes |
|---|---|---|---|
| 2026-08-06 | KYK loan disbursement (OSTİM) | 4,000.00 | Monthly loan |

### Debts

| Debt | Amount (TL) | Status | Notes |
|---|---|---|---|
| Enpara card | 18,761.30 | open | Due 13 Aug 2026 |

## 2026-09

### Expenses

| Date | Item | Amount (TL) | Notes |
|---|---|---|---|
| 2026-09-01 | Enpara kredi kartı | 19,137.92 | Ağustos dönem harcamaları |
"""


# ── The shape of the folder ───────────────────────────────────────────────────

def test_a_month_is_a_file_and_the_key_is_its_title():
    plan = plan_shard(FLAT, FINANCE, LEDGER)
    assert not plan.problems
    assert [m.path for m in plan.members] == [
        "/memories/finance/ledger/2026-08.md",
        "/memories/finance/ledger/2026-09.md",
    ]
    august = plan.members[0].content
    # The month heading becomes the file's H1 and everything under it comes up
    # one level with it — the same promotion a single-section member gets.
    assert august.startswith("# 2026-08\n")
    assert "## Incomes" in august and "### Incomes" not in august


def test_the_shard_is_a_cut_not_a_rewrite():
    plan = plan_shard(FLAT, FINANCE, LEDGER)
    rows_in = [l for l in FLAT.splitlines() if l.strip().startswith("|")]
    rows_out = [
        l for m in plan.members for l in m.content.splitlines()
        if l.strip().startswith("|")
    ]
    assert rows_out == rows_in


def test_a_heading_that_is_not_a_key_is_reported_not_filed():
    """A `## Notes` inside the ledger belongs to another member. Guessing a
    month for it would file standing reference under a period."""
    plan = plan_shard(FLAT + "\n## Notes\n\nRent is due on the 5th.\n", FINANCE, LEDGER)
    assert [p.kind for p in plan.problems] == ["unmapped"]
    assert "Notes" in plan.problems[0].title
    assert len(plan.members) == 2


def test_every_shard_verifies_clean():
    for member in plan_shard(FLAT, FINANCE, LEDGER).members:
        assert verify_document(member.path, member.content) == []


def test_a_shard_carries_the_members_grammar_and_its_own_cap():
    spec = spec_for("/memories/finance/ledger/2026-09.md")
    assert spec is not None
    assert spec.kind == "ledger" and spec.owner_agent == "sentinel"
    assert spec.entry_sections == ("Incomes", "Expenses", "Debts")
    # The key is the H1 here, so the tables sit one level above where they sat
    # inside the gathered file.
    assert spec.index_level == 1
    # One month is capped as one month, not as the whole index.
    assert max_bytes_for("/memories/finance/ledger/2026-09.md") == 12_000
    # …and the member cap that the collection's default used to override is the
    # one that applies now. `wellness/sessions.md` declares 48K and was being
    # capped at the collection's 8K by the write gate alone.
    assert max_bytes_for("/memories/wellness/sessions.md") == 48_000


# ── The key decides the file ──────────────────────────────────────────────────

@pytest.mark.parametrize(
    "named",
    [
        "finance",                              # the domain
        "/memories/finance/ledger",             # the folder
        "/memories/finance/ledger.md",          # the file it used to be
        "/memories/finance/ledger/2026-01.md",  # some other month
    ],
)
def test_every_way_of_naming_the_ledger_routes_by_the_key(named):
    assert route_ledger(named, "2026-09") == ("/memories/finance/ledger/2026-09.md", "")


def test_a_key_that_is_not_a_month_is_refused_with_the_shape():
    path, err = route_ledger("/memories/finance/ledger", "2026-9")
    assert path == "" and "2026-9" in err


def test_a_row_cannot_be_written_into_another_months_file():
    """The only way to get here is to have built the path by hand."""
    with pytest.raises(WriteRejected):
        ledger_append(
            "# 2026-09\n", path="/memories/finance/ledger/2026-09.md", key="2026-08",
            section="Incomes", row=["2026-08-06", "KYK", "4,000.00", "Monthly loan"],
        )


def test_a_new_month_creates_its_file_with_the_key_as_the_title():
    path, err = route_ledger("finance", "2026-10")
    assert not err
    out = ledger_append(
        "", path=path, key="2026-10", section="Incomes",
        row=["2026-10-06", "KYK loan disbursement (OSTİM)", "4,000.00", "Monthly loan"],
    )
    assert out.startswith("# 2026-10\n")
    assert "| 2026-10-06 | KYK loan disbursement (OSTİM) | 4,000.00 | Monthly loan |" in out
    # A brand-new month must pass the write gate as a create, or the folder
    # could never grow a month.
    assert check_write(path=path, before="", after=out, is_create=True, author="sentinel") == []
    assert verify_document(path, out) == []


def test_a_row_lands_in_its_own_section_of_its_own_month():
    plan = plan_shard(FLAT, FINANCE, LEDGER)
    september = next(m for m in plan.members if m.title == "2026-09")
    out = ledger_append(
        september.content, path=september.path, key="2026-09", section="Incomes",
        row=["2026-09-02", "Arel Tarım", "2,800.00", "Salary"],
    )
    # Incomes did not exist in September yet: it is created under the month,
    # with its declared columns, and the existing Expenses table is untouched.
    assert "## Incomes" in out
    assert out.index("## Expenses") < out.index("## Incomes")
    assert "| 2026-09-02 | Arel Tarım | 2,800.00 | Salary |" in out
    assert "| 2026-09-01 | Enpara kredi kartı | 19,137.92 | Ağustos dönem harcamaları |" in out


# ── One authoritative ledger ──────────────────────────────────────────────────

def test_the_flat_file_is_frozen_for_agents():
    with pytest.raises(MemorySchemaViolation) as e:
        check_write(
            path="/memories/finance/ledger.md", before=FLAT, after=FLAT + "\n## 2026-10\n",
            is_create=False, author="sentinel",
        )
    assert "DIRECTORY" in str(e.value)
    assert "ledger_append" in str(e.value)


def test_orion_and_the_owner_can_still_repair_the_flat_file():
    """Whoever runs the migration has to be able to touch what it is migrating —
    and the owner is never blocked (§4.3)."""
    for author in ("orion", "owner"):
        check_write(
            path="/memories/finance/ledger.md", before=FLAT, after=FLAT + "\n",
            is_create=False, author=author,
        )


def test_the_folder_resolves_and_the_flat_file_does_not_pretend_to_be_a_month():
    assert shard_member("/memories/finance/ledger/2026-09.md") == (FINANCE, LEDGER)
    assert shard_member("/memories/finance/ledger.md") is None
    assert shard_member("/memories/finance/notes.md") is None
    assert shard_path(FINANCE, LEDGER, "2026-09") == "/memories/finance/ledger/2026-09.md"
