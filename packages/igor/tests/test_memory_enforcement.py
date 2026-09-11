# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
import app.models
from app.database import Base
from app.models.memory_file import MemoryFile
from app.models.memory_revision import MemoryRevision
from app.models.memory_write_receipt import MemoryWriteReceipt
from app.services import finance_records as finance
from app.services.memory_admission import resolve_evidence, purpose
from app.services.memory_schema import MemorySchemaViolation
from app.services.memory_store import mutate_file
from app.services.memory_states import version
from app.services.memory_audit import coverage, record_review
from app.skills.finance_record import FinanceRecordSkill


@pytest.fixture
async def sessions():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def transaction(**overrides):
    return dict(id="enpara-payment-20260909", type="transaction", description="Card repayment",
                status="active", date="2026-09-09", amount="3000.00", currency="TRY",
                movement="debt_payment", account="enpara", event_ref="payment-1",
                evidence=[{"ref":"/memories/projects/evidence.md", "quote":"Paid 3000 to the card."}], **overrides)


@pytest.mark.parametrize("amount", ["4.914", "4,914.00", "4.914,00", "850", 850, 4914.0, "NaN", "1e3", "-1.00"])
def test_ambiguous_financial_amounts_fail_closed(amount):
    with pytest.raises(ValueError):
        finance.money(amount)


def test_reports_and_recurring_rules_cannot_have_transaction_fields():
    r = transaction()
    r.update(type="report", report_ref="risk-1", body="Report")
    with pytest.raises(ValueError, match="do not belong"):
        finance.encode(r)
    r.update(type="recurring", frequency="monthly", effective_from="2026-09-01")
    with pytest.raises(ValueError, match="do not belong"):
        finance.encode(r)


def test_unknown_money_never_becomes_zero_and_debt_payments_are_not_expenses():
    r = transaction()
    r["amount"] = None
    after = finance.views([r])["/memories/finance/ledger/2026-09.md"]
    assert "unknown" in after and "debt_payment" in after
    assert "Expenses" not in after
    r["status"] = "void"
    assert "Card repayment" not in finance.views([r])["/memories/finance/ledger/2026-09.md"]


def test_record_render_cannot_drift_from_machine_metadata():
    r = transaction()
    encoded = finance.encode(r)
    assert finance.parse(encoded) == r
    with pytest.raises(ValueError, match="disagree"):
        finance.parse(encoded.replace("- amount: 3000.00", "- amount: 9000.00"))


async def test_raw_writes_blocked_even_for_domain_owner_and_orion(sessions):
    async with sessions() as db:
        for agent in ("sentinel", "orion"):
            with pytest.raises(MemorySchemaViolation, match="Raw agent writes"):
                await mutate_file(db, user_id=1, path="/memories/finance/notes.md", before=None,
                    after="# Notes\nSeptember payment 3000", author=agent, action="create")
        assert not (await db.execute(select(MemoryRevision))).scalars().all()


async def test_this_month_cannot_be_written_to_recurring_view_by_any_agent(sessions):
    async with sessions() as db:
        for agent in ("sentinel", "orion", "speda", "owner"):
            with pytest.raises(MemorySchemaViolation, match="computed"):
                await mutate_file(db, user_id=1, path="/memories/finance/monthly-structure.md", before=None,
                    after="# Monthly Structure\nSeptember payments", author=agent, action="edit", managed=True)
    assert "NEVER an actual month's" in purpose("/memories/finance/monthly-structure.md")["semantic_contract"]


async def test_missing_or_fabricated_quote_never_calls_reviewer(sessions, monkeypatch):
    ask = AsyncMock(return_value={"allow": True, "reason": "ok"})
    monkeypatch.setattr("app.services.memory_admission.ask_json", ask)
    async with sessions() as db:
        db.add(MemoryFile(user_id=1, path="/memories/projects/evidence.md", content="# Evidence\nReal quote."))
        await db.commit()
        for evidence in ([], [{"ref":"/memories/projects/evidence.md", "quote":"Invented quote"}]):
            with pytest.raises(MemorySchemaViolation):
                await mutate_file(db, user_id=1, path="/memories/finance/notes.md", before=None,
                    after="# Notes\nFake", author="sentinel", action="topic_patch", managed=True, evidence=evidence)
    ask.assert_not_called()


async def test_validator_rejection_or_outage_saves_nothing(sessions, monkeypatch):
    async with sessions() as db:
        db.add(MemoryFile(user_id=1, path="/memories/projects/evidence.md", content="# Evidence\nPaid 3000 to the card."))
        await db.commit()
        for response in ({"allow": False, "reason": "Payment does not belong in conventions"}, None):
            ask = AsyncMock(return_value=response) if response else AsyncMock(side_effect=TimeoutError())
            monkeypatch.setattr("app.services.memory_admission.ask_json", ask)
            with pytest.raises(MemorySchemaViolation):
                await mutate_file(db, user_id=1, path="/memories/finance/notes.md", before=None,
                    after="# Notes\nPaid 3000 to the card.", author="sentinel", action="topic_patch",
                    managed=True, evidence=transaction()["evidence"])
        assert not (await db.execute(select(MemoryRevision))).scalars().all()
        assert not (await db.execute(select(MemoryWriteReceipt))).scalars().all()


async def test_finance_record_and_views_commit_together_and_duplicate_idempotent(sessions, monkeypatch):
    monkeypatch.setattr("app.services.memory_admission.admit", AsyncMock(return_value="Evidence and financial kind checked"))
    async with sessions() as db:
        db.add(MemoryFile(user_id=1, path="/memories/projects/evidence.md", content="# Evidence\nPaid 3000 to the card."))
        await db.commit()
        ctx = SimpleNamespace(db=db, user_id=1, agent_id="sentinel", request_id="test", model="test", session_id=1)
        skill = FinanceRecordSkill()
        result = json.loads(await skill.execute({"operation":"put", "version":"new", "record":transaction()}, ctx))
        assert result["views"] == "updated atomically"
        ledger = (await db.execute(select(MemoryFile).where(MemoryFile.path=="/memories/finance/ledger/2026-09.md"))).scalar_one()
        assert "debt_payment" in ledger.content
        assert (await db.execute(select(MemoryWriteReceipt))).scalar_one().evidence[0]["source_sha256"]
        repeat = await skill.execute({"operation":"put", "version":"new", "record":transaction()}, ctx)
        assert "stale version" in repeat
        duplicate = transaction(); duplicate["id"] = "same-payment-different-id"
        assert "already exists" in await skill.execute({"operation":"put", "version":"new", "record":duplicate}, ctx)


async def test_unmigrated_finance_history_cannot_be_overwritten(sessions, monkeypatch):
    monkeypatch.setattr("app.services.memory_admission.admit", AsyncMock(return_value="ok"))
    async with sessions() as db:
        db.add_all([MemoryFile(user_id=1, path="/memories/projects/evidence.md", content="# Evidence\nPaid 3000 to the card."),
                    MemoryFile(user_id=1, path="/memories/finance/monthly-structure.md", content="# Monthly Structure\nOriginal history")])
        await db.commit()
        ctx = SimpleNamespace(db=db, user_id=1, agent_id="sentinel", request_id="test", model="test", session_id=1)
        assert "migration required" in await FinanceRecordSkill().execute({"operation":"put", "version":"new", "record":transaction()}, ctx)
        assert not (await db.execute(select(MemoryFile).where(MemoryFile.path.startswith(finance.ROOT)))).scalars().all()
        assert not (await db.execute(select(MemoryRevision))).scalars().all()


async def test_atomic_move_preserves_source_if_destination_invalid(sessions, monkeypatch):
    from app.services.memory_audit_worker import repair_batch
    source = "/memories/academic/erasmus.md"
    async with sessions() as db:
        db.add(MemoryFile(user_id=1, path=source, content="# Erasmus+\n\nTemplate uses HTML."))
        await db.commit()
        with pytest.raises(ValueError):
            await repair_batch(db, user_id=1, request_id="test", model="test", repairs=[
                {"path":source, "old":"Template uses HTML.", "new":"", "evidence":[{"ref":source,"quote":"Template uses HTML."}]},
                {"path":"/memories/../../escape.md", "old":"", "new":"bad", "evidence":[]},
            ])
        assert "Template uses HTML." in (await db.execute(select(MemoryFile.content))).scalar_one()
        assert not (await db.execute(select(MemoryRevision))).scalars().all()


async def test_orion_cannot_write_his_own_success_log(sessions):
    async with sessions() as db:
        with pytest.raises(MemorySchemaViolation, match="system-generated"):
            await mutate_file(db, user_id=1, path="/memories/.audit/log.md", before=None,
                after="# Audit\nEverything reviewed, all clean!", author="orion", action="insert", managed=True)


async def test_worker_reads_and_records_each_document_instead_of_trusting_scan(sessions, monkeypatch):
    from app.services import memory_audit_worker as worker
    from app.models.memory_review import MemoryReview
    monkeypatch.setattr(worker, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(worker, "ask_json", AsyncMock(return_value={"findings":[],"rationale":"Read target and checked its subject.","repairs":[]}))
    async with sessions() as db:
        db.add_all([MemoryFile(user_id=1, path=f"/memories/projects/{p}.md", content=f"# {p}\n\nProject description.") for p in ("one", "two")])
        await db.commit()
    result = await worker.run_audit(None, "worker-test", 1, "test")
    assert result["reviewed"] == 2 and result["pending"] == 0
    assert worker.ask_json.await_count == 2
    async with sessions() as db:
        assert len((await db.execute(select(MemoryReview))).scalars().all()) == 2


async def test_worker_failure_is_not_a_clean_audit(sessions, monkeypatch):
    from app.services import memory_audit_worker as worker
    monkeypatch.setattr(worker, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(worker, "ask_json", AsyncMock(side_effect=TimeoutError("provider down")))
    async with sessions() as db:
        db.add(MemoryFile(user_id=1, path="/memories/projects/one.md", content="# One\n\nProject."))
        await db.commit()
    with pytest.raises(ValueError, match="reviews failed"):
        await worker.run_audit(None, "failed-test", 1, "test")
    async with sessions() as db:
        assert len((await coverage(db, 1))["pending_review"]) == 1


def test_project_upsert_replaces_description_and_keeps_one_log():
    from app.services.memory_write import registry_upsert
    original = "# Demo\n\nOld description.\n\n## Stack\nPython\n\n## Log\n- [2026-09-01] Created.\n"
    updated = registry_upsert(original, path="/memories/projects/demo.md", entity="Demo", who="New description.", event="Updated.", when="2026-09-09")
    assert "Old description" not in updated and "Python" in updated
    assert updated.count("## Log") == 1
    repeat = registry_upsert(updated, path="/memories/projects/demo.md", entity="Demo", event="Updated.", when="2026-09-09")
    assert repeat == updated


def test_summary_never_double_counts_balance_report_or_repayment_as_spending():
    r = transaction()
    expense = {**r, "id":"purchase", "event_ref":"purchase", "movement":"expense", "amount":"100.10"}
    uncertain = {**expense, "id":"unknown", "event_ref":"unknown", "amount":None}
    balance = {"id":"balance", "type":"balance", "description":"Account balance", "status":"active",
        "date":"2026-09-09", "account":"enpara", "amount":"14779.00", "currency":"TRY",
        "balance_kind":"liability", "evidence":r["evidence"]}
    report = {"id":"report", "type":"report", "description":"Credit report", "status":"active",
        "date":"2026-09-11", "report_ref":"report", "body":"Aggregate debt 19764", "evidence":r["evidence"]}
    result = finance.summarize([r, expense, uncertain, balance, report], "2026-09")
    assert result["totals"]["TRY"]["expense"] == "100.10"
    assert result["totals"]["TRY"]["debt_payment"] == "3000.00"
    assert len(result["excluded"]) == 1
    assert result["latest_reported_balances"][0]["amount"] == "14779.00"


async def test_observation_audit_rejects_incomplete_model_coverage(sessions, monkeypatch):
    from app.services import observation_audit
    from app.models.observation import Observation
    from app.models.memory_review import MemoryReview
    monkeypatch.setattr(observation_audit, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(observation_audit, "ask_json", AsyncMock(return_value={"reviews":[]}))
    monkeypatch.setattr("app.services.memory_audit_worker.progress", AsyncMock())
    async with sessions() as db:
        o = Observation(user_id=1, observer="speda", content="The owner submitted the application.", domain="event")
        db.add(o); await db.commit(); ident = o.id
    result = await observation_audit.audit_observations(1,[f"observation:{ident}"],"test","test")
    assert result["failed"] == 1 and result["reviewed"] == 0
    async with sessions() as db:
        assert not (await db.execute(select(MemoryReview))).scalars().all()


async def test_observation_domain_repair_is_revisioned_and_requires_re_review(sessions, monkeypatch):
    from app.services import observation_audit
    from app.models.observation import Observation
    monkeypatch.setattr(observation_audit, "AsyncSessionLocal", sessions)
    monkeypatch.setattr("app.services.memory_audit_worker.progress", AsyncMock())
    async with sessions() as db:
        o = Observation(user_id=1, observer="speda", content="The owner sent the application on 2026-09-09.", domain="state")
        db.add(o); await db.commit(); ident = o.id
    monkeypatch.setattr(observation_audit, "ask_json", AsyncMock(side_effect=[
        {"reviews":[{"id":ident,"findings":["Completed event filed as state"],"rationale":"Dated completed action", "domain":"event", "duplicate_of":None}]},
        {"allow":True,"reason":"The exact fact is a completed event."},
    ]))
    result = await observation_audit.audit_observations(1,[f"observation:{ident}"],"test","test")
    assert result["repaired"] == 1
    async with sessions() as db:
        assert (await db.get(Observation, ident)).domain == "event"
        assert (await db.execute(select(MemoryRevision))).scalar_one().action == "observation_repair"
        assert (await coverage(db,1))["pending_review"]


async def test_trigger_runs_controller_without_freeform_orion_turn(monkeypatch):
    from app.core.trigger_runner import start_trigger_turn
    queued = AsyncMock(return_value={"id":1})
    monkeypatch.setattr("app.services.memory_audit_worker.enqueue_audit",queued)
    profile = SimpleNamespace(agent_id="orion", allocate_model=lambda _:"test")
    result = await start_trigger_turn(db=None, profile=profile, payload={"job":"memory_audit","intent":"fake success"},
        output_mode="silent", request_id="test", orchestrator=None, turns=None, session_manager=None,telegram_bots=None)
    assert result == ("test",0)
    queued.assert_awaited_once()
