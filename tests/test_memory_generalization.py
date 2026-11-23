"""Generalization & negative-control tests for the de-hardcoded multilingual memory pipeline.

Verifies that entity/relation routing, contextual reference resolution, intent classification,
and bi-temporal retrieval generalize to unseen English, Turkish, mixed-language (code-switched),
paraphrased, indirect-reference, temporal-contradiction, and negative-control inputs without
hardcoded domain keyword lists.
"""
from __future__ import annotations

from datetime import date, timedelta
import json
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.core.orchestrator import AgentOrchestrator
from app.schemas.iacon import Command
from app.schemas.memory_runtime import Deadline
from app.services.episodic_cortex import classify_enrollment_intent, is_changed_journey
from app.services.iacon_catalogue import Catalogue
from app.services.memory_query import extract_owner_relation_phrases, query_terms, referential, turn_query
from app.services.memory_runtime import MemoryRuntime, packet_json
from app.services.memory_store import capture_source


# ---------------------------------------------------------------------------
# 1. Unseen Arbitrary Relationships (EN, TR, Mixed-Language, Paraphrase)
# ---------------------------------------------------------------------------

async def _seed_non_family_network(memory):
    await memory.seed(
        "## DOSSIER: Deniz Erol Kaya — Personal Profile\nBorn in Izmir.",
        title="Owner Profile", kind="core", aliases=(),
    )
    await memory.seed(
        "**Who:** Deniz Erol Kaya'nın tez danışmanı; MIS bölümü öğretim üyesi.",
        title="Dr. Selim Arslan", kind="person", aliases=("Selim",),
    )
    await memory.seed(
        "**Who:** The owner's roommate at the Çankaya apartment.",
        title="Mert Demir", kind="person", aliases=("Mert",),
    )
    await memory.seed(
        "**Who:** Deniz Erol'un diş hekimi (Çankaya kliniği).",
        title="Dt. Ayşe Yılmaz", kind="person", aliases=("Ayşe",),
    )
    # Negative controls: 2-hop possessive chains & other-owner isolation
    await memory.seed(
        "**Who:** The owner's roommate's cousin visiting for the weekend.",
        title="Burak Çelik", kind="person", aliases=("Burak",),
    )
    await memory.seed(
        "**Who:** Deniz Erol Kaya'nın tez danışmanının asistanı.",
        title="Elif Koç", kind="person", aliases=("Elif",),
    )
    await memory.seed(
        "**Who:** Another owner's thesis advisor.",
        owner=2, title="Prof. Other Owner", kind="person", aliases=(),
    )


@pytest.mark.parametrize("query, expected_topic, forbidden_topics", [
    # Unseen English -> Turkish record
    ("Who is my thesis advisor?", "Dr. Selim Arslan", ["Elif Koç", "Prof. Other Owner"]),
    # Unseen Turkish -> English record
    ("ev arkadaşımın adı ne?", "Mert Demir", ["Burak Çelik"]),
    # Unseen Turkish paraphrase -> Turkish record
    ("dişçimin adı neydi?", "Dt. Ayşe Yılmaz", ["Dr. Selim Arslan"]),
    # Mixed-language (EN+TR code-switching)
    ("my tez danışmanı kimdi?", "Dr. Selim Arslan", ["Elif Koç", "Prof. Other Owner"]),
    ("Ankara'daki roommate'im kim?", "Mert Demir", ["Burak Çelik"]),
])
async def test_unseen_multilingual_arbitrary_relations_without_hardcoded_dict(
    memory, query, expected_topic, forbidden_topics
):
    await _seed_non_family_network(memory)
    runtime = MemoryRuntime(memory.store, prepare_timeout=3.0)
    packet = await runtime.lookup(memory.context(query), query)
    result = json.loads(packet_json(packet))
    topics = [e["topic"] for e in result["evidence"]]
    assert expected_topic in topics, f"Expected {expected_topic} in {topics} for query={query!r}"
    for forbidden in forbidden_topics:
        assert forbidden not in topics, f"Forbidden {forbidden} leaked into {topics} for query={query!r}"


def test_structural_second_hop_possessive_guards_reject_indirect_chains():
    en_sections = [{"text": "**Who:** Leyla is the owner's advisor's assistant."}]
    tr_sections = [{"text": "**Who:** Elif, Deniz Erol Kaya'nın tez danışmanının asistanı."}]
    of_sections = [{"text": "**Who:** Cem is a friend of the owner's roommate."}]
    event_sections = [{"text": "- [2026-04-01] Met the owner's dentist at a cafe."}]
    assert extract_owner_relation_phrases(en_sections, "Deniz Erol Kaya") == []
    assert extract_owner_relation_phrases(tr_sections, "Deniz Erol Kaya") == []
    assert extract_owner_relation_phrases(of_sections, "Deniz Erol Kaya") == []
    assert extract_owner_relation_phrases(event_sections, "Deniz Erol Kaya") == []


# ---------------------------------------------------------------------------
# 2. Unseen Paraphrased & Multilingual Dated States + Temporal Contradiction
# ---------------------------------------------------------------------------

async def test_unseen_dated_states_semantic_recall_and_temporal_contradiction(memory):
    today = date.today()
    next_mon = today + timedelta(days=7 - today.weekday())
    next_sun = next_mon + timedelta(days=6)
    catalogue = Catalogue(memory.store)

    async def save_state(title, summary, *, status="planned", salience="normal", starts_on=None, ends_on=None, closed_on=None):
        async with memory.sessions() as db, db.begin():
            src = await capture_source(db, user_id=1, source_key=f"gen-state:{uuid4().hex}", content=summary)
            sid = src.id
        payload = {
            "summary": summary,
            "status": status,
            "salience": salience,
            "verified_on": str(today),
            "review_on": str(next_sun + timedelta(days=3)),
            "question": "Still current?",
        }
        if starts_on:
            payload["starts_on"] = str(starts_on)
        if ends_on:
            payload["ends_on"] = str(ends_on)
        if closed_on:
            payload["closed_on"] = str(closed_on)
        await catalogue.apply(1, Command(
            command_id=f"gen-cmd-{uuid4().hex[:8]}",
            kind="state",
            title=title,
            payload=payload,
            evidence=[{"source_id": sid, "start": 0, "end": len(summary), "quote": summary}],
        ))

    # Active competing high-salience undated noise
    for i in range(4):
        await save_state(f"Ops alert {i}", f"Background infrastructure alert {i}", status="active", salience="high")
    # Unseen domain dated states (zero overlap with legacy exam/trip/vacation keywords)
    await save_state(
        "Dentist appointment", "Routine dental checkup at Çankaya clinic",
        starts_on=next_mon, ends_on=next_mon,
    )
    await save_state(
        "Thesis defense", "Final committee presentation in Ankara",
        starts_on=next_mon + timedelta(days=2), ends_on=next_mon + timedelta(days=2),
    )
    # Temporal contradiction: expired / superseded dental appointment must NOT surface
    await save_state(
        "Old cancelled dentist visit", "Cancelled dental checkup from last month",
        status="superseded",
        starts_on=today - timedelta(days=20), ends_on=today - timedelta(days=20),
        closed_on=today - timedelta(days=19),
    )

    runtime = MemoryRuntime(memory.store, prepare_timeout=3.0)
    # Paraphrase with zero lexical overlap ('clinic visits' -> 'Dentist appointment')
    p1 = json.loads(packet_json(await runtime.prepare(memory.context("Do I have any clinic visits next week?"))))
    titles1 = [s["title"] for s in p1["current_states"]]
    assert titles1[0] == "Dentist appointment"
    assert "Old cancelled dentist visit" not in titles1

    # Mixed-language query
    p2 = json.loads(packet_json(await runtime.prepare(memory.context("Gelecek hafta thesis defense ne zaman?"))))
    titles2 = [s["title"] for s in p2["current_states"]]
    assert titles2[0] == "Thesis defense"


# ---------------------------------------------------------------------------
# 3. Contrastive Intent Classification (EN, TR, Mixed, Paraphrase, Negatives)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query", [
    "Where am I pursuing my bachelor's degree?",
    "Hangi fakültede eğitim görüyorum?",
    "What degree am I working toward right now?",
    "Şu an hangi üniversiteye gidiyorum?",
])
def test_enrollment_intent_generalizes_to_unseen_paraphrases_and_turkish(query):
    fields = classify_enrollment_intent(query)
    assert fields, f"Expected enrollment intent for unseen positive query: {query!r}"


@pytest.mark.parametrize("query", [
    "Which university does my sister attend?",
    "Kardeşim hangi üniversitede okuyor?",
    "Where did I graduate from previously?",
    "Eski okulum neresiydi?",
    "What are my midterm scores?",
    "Tell me a joke about Python",
    "Who is my thesis advisor?",
])
def test_enrollment_intent_rejects_past_third_person_and_unrelated_negatives(query):
    assert classify_enrollment_intent(query) == [], f"False positive enrollment intent for {query!r}"


@pytest.mark.parametrize("query", [
    "My flight got delayed by 2 hours",
    "Otobüs saatim değişti",
    "Tren biletimi yarına erteledim",
    "They moved my departure to 18:00",
    "flight'ım 2 saat delayed oldu",
])
def test_changed_journey_generalizes_multilingually_without_regex_verbs(query):
    assert is_changed_journey(query) is True, f"Expected changed_journey=True for {query!r}"


@pytest.mark.parametrize("query", [
    "How far is Ankara from Bursa?",
    "I like traveling by train",
    "My meeting was rescheduled",
    "What is my midterm score?",
])
def test_changed_journey_negative_controls(query):
    assert is_changed_journey(query) is False, f"False positive changed_journey for {query!r}"


# ---------------------------------------------------------------------------
# 4. Contextual Reference & Indirect-Reference vs Self-Contained Short Queries
# ---------------------------------------------------------------------------

def test_referential_distinguishes_indirect_followups_from_self_contained_questions(memory):
    # Indirect / elliptical / bridging anaphora -> True
    assert referential("names?") is True
    assert referential("adları?") is True
    assert referential("What is its name?") is True
    assert referential("What would the name be?") is True
    assert referential("Onun adı ne?") is True
    # Self-contained 4-6 word owner questions -> False (must NOT pollute with unrelated prior turn)
    assert referential("Which university do I attend?") is False
    assert referential("Where do I live right now?") is False
    assert referential("What is my brother's name?") is False

    # Verify self-contained question does NOT borrow unrelated prior turn
    ctx = memory.context()
    ctx.conversation_history = [
        {"role": "user", "content": "My sister Nora moved to Izmir."},
        {"role": "assistant", "content": "Noted that Nora is in Izmir."},
        {"role": "user", "content": "Which university do I attend?"},
    ]
    q = turn_query(ctx)
    assert "Nora" not in q and "Izmir" not in q


# ---------------------------------------------------------------------------
# 5. Negative Control: Unrelated Query Does Not Trigger False Relation Routing
# ---------------------------------------------------------------------------

async def test_unrelated_queries_produce_zero_relation_keys(memory):
    await _seed_non_family_network(memory)
    for query in ("Tell me a joke about Python", "Server CPU usage is high", "What is my midterm score?"):
        result = await memory.store.read(
            memory.context(query), query_terms(query), deadline=Deadline.after(5), raw_query=query,
        )
        assert not result["relation_keys"], f"Unexpected relation_keys for {query!r}: {result['relation_keys']}"
