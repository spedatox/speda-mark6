"""54-Question Unseen Multi-Session Benchmark & C2 vs. C3 Investigation.

Tests Operation Iacon at realistic autobiographical scale:
- 15 distinct Life Chapters (60+ entities, 75+ bi-temporal edges)
- Cross-chapter bridge edges connecting separate communities
- Superseded / historical bi-temporal relationships
- 54 unseen multi-session questions across 5 diagnostic categories:
  1. Intra-chapter unseen multi-hop (20 questions)
  2. Cross-chapter bridge multi-hop (14 questions) — isolates C3 sparse PPR value over C2 summaries
  3. Bi-temporal point-in-time & supersession (10 questions)
  4. Bilingual Turkish<->English multi-session paraphrase (5 questions)
  5. Abstention / negative controls (5 questions)
"""
from __future__ import annotations

import asyncio
from datetime import date
import json
import os
from pathlib import Path
import tempfile
from time import perf_counter
from types import SimpleNamespace

import numpy as np
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base
from app.models.user import User
from app.schemas.iacon import Command
from app.schemas.memory_runtime import MemoryBudget
from app.services.associative_graph import TemporalScope
from app.services.iacon_catalogue import Catalogue
from app.services.iacon_dreaming import OrionDreamer
from app.services.memory_runtime import MemoryRuntime, packet_json
from app.services.memory_store import MemoryStore, capture_source, init_indexes


CHAPTER_SPECS = [
    # (slug, [(key, title, entity_type, desc, quote)], [(src_key, dst_key, rel, mode, vfrom)])
    ("robotics", [
        ("r_ws", "Bursa Robotics Bootcamp", "project", "Summer embedded control bootcamp.", "Attended Bursa Robotics Bootcamp in July 2025."),
        ("r_prof", "Prof Kerem", "person", "Control systems faculty mentor.", "Met Prof Kerem at the robotics bootcamp."),
        ("r_lab", "Autonomous UAV Lab", "organization", "University drone flight control lab.", "Prof Kerem invited me into Autonomous UAV Lab."),
        ("r_award", "Teknofest Flight Finals", "project", "National autonomous drone finalist.", "Our UAV lab team qualified for Teknofest Flight Finals."),
    ], [("r_ws", "r_prof", "met_at", "explicit", "2025-07-10"), ("r_prof", "r_lab", "invited_to", "explicit", "2025-09-01"), ("r_lab", "r_award", "qualified_for", "explicit", "2026-04-15")]),

    ("systems", [
        ("s_meet", "Izmir Kernel Meetup", "place", "Low-level systems engineering meetup.", "Went to Izmir Kernel Meetup in August 2025."),
        ("s_dev", "Deniz", "person", "Rust operating systems maintainer.", "Met Deniz at the Izmir Kernel Meetup."),
        ("s_repo", "Nebula Microkernel", "project", "Open-source Rust microkernel repository.", "Submitted memory allocator PRs to Nebula Microkernel with Deniz."),
        ("s_job", "CloudCore Internship", "organization", "Summer distributed infrastructure internship.", "Got CloudCore Internship through my Nebula Microkernel work."),
    ], [("s_meet", "s_dev", "met_at", "explicit", "2025-08-05"), ("s_dev", "s_repo", "collaborated_on", "explicit", "2025-10-12"), ("s_repo", "s_job", "led_to_offer", "explicit", "2026-05-02")]),

    ("cyber", [
        ("c_hack", "Stellar Hackathon", "project", "48-hour blockchain and security hackathon.", "Participated in Stellar Hackathon in November 2025."),
        ("c_liva", "Liva", "person", "Teammate met during the Stellar Hackathon.", "Met Liva at the Stellar Hackathon."),
        ("c_fair", "Campus Career Fair", "place", "Spring university career exhibition.", "Visited Campus Career Fair with university friends."),
        ("c_sib", "SIBERAY Presidency", "organization", "Led the SIBERAY cybersecurity student society.", "Became president of SIBERAY after visiting their stand."),
    ], [("c_hack", "c_liva", "met_at", "explicit", "2025-11-10"), ("c_liva", "c_fair", "subsequent_circle", "inferred", "2026-01-15"), ("c_fair", "c_sib", "encountered_stand", "explicit", "2026-02-20")]),

    ("bayes", [
        ("b_club", "Ankara Kitap Kulubu", "organization", "Haftalik bilim ve felsefe okuma grubu.", "Ankara Kitap Kulubu bulusmalarina katildim."),
        ("b_selin", "Selin", "person", "Istatistik yuksek lisans ogrencisi.", "Okuma kulubunde Selin ile tanistim."),
        ("b_sem", "Bayesian Reading Seminar", "organization", "Weekly probabilistic modeling seminar.", "Selin invited me to Bayesian Reading Seminar."),
        ("b_pap", "Causal Inference Preprint", "project", "Co-authored arXiv statistics preprint.", "Co-authored Causal Inference Preprint with the seminar group."),
    ], [("b_club", "b_selin", "met_at", "explicit", "2025-06-14"), ("b_selin", "b_sem", "invited_to", "explicit", "2025-10-03"), ("b_sem", "b_pap", "coauthored", "explicit", "2026-03-18")]),

    ("music", [
        ("m_shop", "Kadikoy Vinyl Shop", "place", "Analog synth and vinyl record store.", "Discovered Kadikoy Vinyl Shop in May 2025."),
        ("m_can", "Caner", "person", "Modular synthesizer builder.", "Met Caner while browsing synths at Kadikoy Vinyl Shop."),
        ("m_band", "Midnight Oscillator", "organization", "Ambient electronic trio.", "Formed Midnight Oscillator with Caner."),
        ("m_gig", "Moda Rooftop Set", "project", "Live ambient electronic performance.", "Performed Moda Rooftop Set with Midnight Oscillator."),
    ], [("m_shop", "m_can", "met_at", "explicit", "2025-05-11"), ("m_can", "m_band", "cofounded", "explicit", "2025-09-20"), ("m_band", "m_gig", "performed", "explicit", "2026-06-01")]),

    ("health", [
        ("h_run", "Eymir Lake Running Club", "organization", "Sunday morning endurance running group.", "Joined Eymir Lake Running Club in April 2025."),
        ("h_coach", "Coach Hakan", "person", "Former marathon pacer and trainer.", "Coach Hakan designed my aerobic base plan at Eymir."),
        ("h_plan", "Sub-45 10K Training Block", "project", "12-week threshold running program.", "Followed Sub-45 10K Training Block under Coach Hakan."),
        ("h_race", "Istanbul Half Marathon", "project", "Completed 21.1km race in 1h42m.", "Finished Istanbul Half Marathon in April 2026."),
    ], [("h_run", "h_coach", "trained_with", "explicit", "2025-04-06"), ("h_coach", "h_plan", "designed", "explicit", "2025-11-01"), ("h_plan", "h_race", "prepared_for", "explicit", "2026-04-26")]),

    ("startup", [
        ("st_pitch", "ODTU Teknokent Demo Day", "place", "University startup pitch showcase.", "Attended ODTU Teknokent Demo Day in October 2025."),
        ("st_emel", "Emel", "person", "Fintech product designer.", "Met Emel at ODTU Teknokent Demo Day."),
        ("st_app", "KasaFlow Ledger", "project", "Student expense splitting mobile app.", "Prototyped KasaFlow Ledger with Emel."),
        ("st_grant", "TUBITAK BIGG Grant", "project", "Early-stage entrepreneurship seed grant.", "Submitted KasaFlow Ledger to TUBITAK BIGG Grant."),
    ], [("st_pitch", "st_emel", "met_at", "explicit", "2025-10-18"), ("st_emel", "st_app", "built_with", "explicit", "2025-12-05"), ("st_app", "st_grant", "applied_for", "explicit", "2026-03-22")]),

    ("volunteer", [
        ("v_org", "Anatolia Code Mentors", "organization", "High-school Python mentorship initiative.", "Volunteered at Anatolia Code Mentors."),
        ("v_zey", "Zeynep", "person", "Regional education coordinator.", "Worked alongside Zeynep at Anatolia Code Mentors."),
        ("v_camp", "Eskisehir Youth Hackathon", "project", "Weekend coding camp for teenagers.", "Co-organized Eskisehir Youth Hackathon with Zeynep."),
        ("v_curr", "Turkish Python Workbook", "project", "Open-licensed beginner programming guide.", "Published Turkish Python Workbook after the Eskisehir camp."),
    ], [("v_org", "v_zey", "collaborated_with", "explicit", "2025-05-22"), ("v_zey", "v_camp", "coorganized", "explicit", "2025-11-28"), ("v_camp", "v_curr", "produced", "explicit", "2026-02-10")]),

    ("photo", [
        ("p_walk", "Galata Night Photowalk", "place", "Street photography evening walk.", "Joined Galata Night Photowalk in September 2025."),
        ("p_tarik", "Tarik", "person", "Analog 35mm darkroom photographer.", "Met Tarik during Galata Night Photowalk."),
        ("p_room", "Beyoglu Community Darkroom", "place", "Shared silver-gelatin film lab.", "Tarik introduced me to Beyoglu Community Darkroom."),
        ("p_zine", "Nocturne Ankara Zine", "project", "Limited-run monochrome photo zine.", "Printed Nocturne Ankara Zine at the Beyoglu darkroom."),
    ], [("p_walk", "p_tarik", "met_at", "explicit", "2025-09-12"), ("p_tarik", "p_room", "introduced_to", "explicit", "2025-10-25"), ("p_room", "p_zine", "printed_at", "explicit", "2026-04-04")]),

    ("chess", [
        ("ch_cafe", "Kizilay Chess Cafe", "place", "Late-night blitz chess hangout.", "Played rapid games at Kizilay Chess Cafe."),
        ("ch_murat", "Murat", "person", "FIDE Candidate Master.", "Met Murat at Kizilay Chess Cafe."),
        ("ch_team", "University Varsity Chess", "organization", "Intercollegiate chess squad.", "Murat recruited me to University Varsity Chess."),
        ("ch_cup", "Anatolian Intervarsity Cup", "project", "Won third place board 2 medal.", "Earned board 2 bronze at Anatolian Intervarsity Cup."),
    ], [("ch_cafe", "ch_murat", "met_at", "explicit", "2025-03-19"), ("ch_murat", "ch_team", "recruited_to", "explicit", "2025-10-08"), ("ch_team", "ch_cup", "competed_in", "explicit", "2026-05-14")]),

    ("hardware", [
        ("hw_fair", "Maker Faire Istanbul", "place", "DIY electronics and PCB exhibition.", "Visited Maker Faire Istanbul in October 2025."),
        ("hw_burak", "Burak", "person", "RF antenna hardware engineer.", "Met Burak at Maker Faire Istanbul."),
        ("hw_node", "LoRa Mesh Beacon", "project", "Solar-powered off-grid emergency communicator.", "Designed LoRa Mesh Beacon PCB with Burak."),
        ("hw_test", "Uludag Range Trial", "project", "18km mountain line-of-sight RF test.", "Completed Uludag Range Trial with LoRa Mesh Beacon."),
    ], [("hw_fair", "hw_burak", "met_at", "explicit", "2025-10-04"), ("hw_burak", "hw_node", "codesigned", "explicit", "2025-12-20"), ("hw_node", "hw_test", "tested_at", "explicit", "2026-06-12")]),

    ("linguistics", [
        ("lg_course", "Computational Linguistics Seminar", "course", "Graduate NLP elective course.", "Enrolled in Computational Linguistics Seminar."),
        ("lg_dr", "Dr Aylin", "person", "Turkish morphology researcher.", "Dr Aylin taught the Computational Linguistics Seminar."),
        ("lg_corp", "Ottoman OCR Corpus", "project", "Digitized historical Turkish text dataset.", "Built Ottoman OCR Corpus under Dr Aylin."),
        ("lg_conf", "SIU 2026 Poster", "project", "Accepted IEEE SIU conference poster.", "Presented Ottoman OCR Corpus as SIU 2026 Poster."),
    ], [("lg_course", "lg_dr", "taught_by", "explicit", "2025-09-15"), ("lg_dr", "lg_corp", "supervised", "explicit", "2025-11-18"), ("lg_corp", "lg_conf", "presented_as", "explicit", "2026-05-21")]),

    ("sailing", [
        ("sa_dock", "Urla Sailing School", "place", "Aegean dinghy sailing academy.", "Took beginner lessons at Urla Sailing School."),
        ("sa_kaptan", "Kaptan Demir", "person", "Offshore yachting instructor.", "Trained with Kaptan Demir at Urla Sailing School."),
        ("sa_cert", "ADB Skipper License", "project", "Turkish amateur seafarer certificate.", "Earned ADB Skipper License with Kaptan Demir."),
        ("sa_reg", "Cesme Autumn Regatta", "project", "Crewed J70 keelboat race.", "Crewed Cesme Autumn Regatta after getting ADB Skipper License."),
    ], [("sa_dock", "sa_kaptan", "trained_with", "explicit", "2025-06-28"), ("sa_kaptan", "sa_cert", "certified_by", "explicit", "2025-08-19"), ("sa_cert", "sa_reg", "enabled", "explicit", "2025-10-28")]),

    ("coffee", [
        ("co_bar", "Cankaya Micro Roastery", "place", "Specialty pour-over coffee bar.", "Regular customer at Cankaya Micro Roastery."),
        ("co_barista", "Ece", "person", "Q-grader coffee roaster.", "Learned V60 extraction ratios from Ece at the roastery."),
        ("co_blend", "Ethiopian Washed Experiment", "project", "Home refractometer brew log.", "Logged Ethiopian Washed Experiment with Ece's roast."),
        ("co_comp", "Ankara AeroPress Throwdown", "project", "Local amateur brewing competition.", "Entered Ankara AeroPress Throwdown with the washed blend."),
    ], [("co_bar", "co_barista", "met_at", "explicit", "2025-04-14"), ("co_barista", "co_blend", "guided", "explicit", "2025-09-02"), ("co_blend", "co_comp", "entered_in", "explicit", "2026-03-07")]),

    ("climate", [
        ("cl_conf", "Aegean Energy Forum", "place", "Renewable microgrid symposium.", "Attended Aegean Energy Forum in January 2026."),
        ("cl_onur", "Onur", "person", "Battery storage economist.", "Met Onur at Aegean Energy Forum."),
        ("cl_sim", "Solar Microgrid Simulator", "project", "Python dispatch optimization model.", "Built Solar Microgrid Simulator with Onur."),
        ("cl_pilot", "Datca Village Microgrid", "project", "Feasibility study for coastal town.", "Used Solar Microgrid Simulator for Datca Village Microgrid."),
    ], [("cl_conf", "cl_onur", "met_at", "explicit", "2026-01-22"), ("cl_onur", "cl_sim", "built_with", "explicit", "2026-02-28"), ("cl_sim", "cl_pilot", "applied_to", "explicit", "2026-06-18")]),
]

# Cross-chapter bridge edges connecting separate communities!
CROSS_CHAPTER_BRIDGES = [
    ("r_lab", "hw_node", "shared_telemetry_radio", "explicit", "2026-01-12", "Autonomous UAV Lab adopted LoRa Mesh Beacon for backup drone telemetry"),
    ("s_dev", "c_sib", "guest_speaker_at", "explicit", "2026-03-15", "Deniz gave a Rust kernel security talk at SIBERAY Presidency"),
    ("b_selin", "lg_corp", "advised_statistics_for", "explicit", "2026-02-05", "Selin advised Bayesian error bounds for Ottoman OCR Corpus"),
    ("st_emel", "v_curr", "designed_layout_for", "explicit", "2026-01-28", "Emel designed the visual layout of Turkish Python Workbook"),
    ("m_can", "p_zine", "composed_soundtrack_for", "explicit", "2026-04-10", "Caner composed a companion tape for Nocturne Ankara Zine"),
    ("h_coach", "sa_reg", "endurance_prep_for", "inferred", "2025-10-15", "Coach Hakan's endurance block helped stamina during Cesme Autumn Regatta"),
    ("cl_onur", "st_grant", "reviewed_financials_for", "explicit", "2026-03-20", "Onur reviewed unit economics for TUBITAK BIGG Grant application"),
]


async def _add(catalogue, sessions, key, title, kind, payload, quote):
    async with sessions() as db, db.begin():
        src = await capture_source(db, user_id=1, source_key=f"b50:{key}", content=quote, source_role="owner")
        sid = src.id
    return await catalogue.apply(1, Command(
        command_id=f"b50:{key}", kind=kind, title=title, payload=payload,
        evidence=[{"source_id": sid, "start": 0, "end": len(quote), "quote": quote}],
    ), actor="orion_review")


async def run_54q_benchmark() -> dict:
    with tempfile.TemporaryDirectory(prefix="speda-bench54-") as tmp:
        engine = create_async_engine(f"sqlite+aiosqlite:///{Path(tmp) / 'bench54.db'}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await init_indexes(conn)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db, db.begin():
            db.add(User(id=1, name="Ahmet Erol Bayrak", timezone="Europe/Istanbul"))

        store = MemoryStore(sessions)
        catalogue = Catalogue(store)
        dreamer = OrionDreamer(catalogue)

        rec_ids: dict[str, str] = {}
        for _, nodes, edges in CHAPTER_SPECS:
            for key, title, etype, desc, quote in nodes:
                r = await _add(catalogue, sessions, key, title, "entity", {"entity_type": etype, "description": desc}, quote)
                rec_ids[key] = r["id"]
            for s_k, t_k, rel, mode, vfrom in edges:
                await catalogue.upsert_edge(
                    1, source_id=rec_ids[s_k], target_id=rec_ids[t_k],
                    relation=rel, epistemic_mode=mode, valid_from=vfrom,
                )

        # Add cross-chapter bridges
        for s_k, t_k, rel, mode, vfrom, desc in CROSS_CHAPTER_BRIDGES:
            await catalogue.upsert_edge(
                1, source_id=rec_ids[s_k], target_id=rec_ids[t_k],
                relation=rel, description=desc, epistemic_mode=mode, valid_from=vfrom,
            )

        # Add bi-temporal supersession records (for Bucket 3)
        r_me = await _add(catalogue, sessions, "owner_me", "Ahmet", "entity", {"entity_type": "person", "description": "Owner"}, "My name is Ahmet.")
        rec_ids["owner_me"] = r_me["id"]
        temporal_pairs = [
            ("old_dorm", "Gazi Dormitory", "new_apt", "Bahcelievler Flat", "lives_at", "2024-09-01", "2025-08-31", "2025-09-01"),
            ("old_lap", "ThinkPad T480", "new_lap", "Framework 16 Linux", "uses_laptop", "2024-01-01", "2025-11-30", "2025-12-01"),
            ("old_lang", "Java Swing Club", "new_lang", "Rust Systems Guild", "active_in", "2024-02-01", "2025-05-31", "2025-06-01"),
            ("old_gym", "Kizilay Fitness", "new_gym", "Bilkent Climbing Wall", "trains_at", "2024-03-01", "2025-09-30", "2025-10-01"),
            ("old_bank", "Campus Credit Coop", "new_bank", "Digital NeoBank", "banks_with", "2024-01-01", "2025-07-31", "2025-08-01"),
        ]
        for ok, ot, nk, nt, rel, vf1, vu1, vf2 in temporal_pairs:
            ro = await _add(catalogue, sessions, ok, ot, "entity", {"entity_type": "place", "description": f"Former {rel}: {ot}"}, f"Previously {rel} {ot}.")
            rn = await _add(catalogue, sessions, nk, nt, "entity", {"entity_type": "place", "description": f"Current {rel}: {nt}"}, f"Currently {rel} {nt}.")
            rec_ids[ok], rec_ids[nk] = ro["id"], rn["id"]
            e1 = await catalogue.upsert_edge(1, source_id=r_me["id"], target_id=ro["id"], relation=rel, valid_from=vf1)
            await catalogue.supersede_edge(1, e1["id"], valid_until=vu1)
            await catalogue.upsert_edge(1, source_id=r_me["id"], target_id=rn["id"], relation=rel, valid_from=vf2)

        # Dream chapters BEFORE cross-chapter bridges dominate or alongside them
        await dreamer.dream_user(1, use_llm=False)

        # Build 54 unseen questions:
        # Bucket 1: 20 Intra-Chapter Multi-Hop Questions
        b1_intra = [
            ("How did Bursa Robotics Bootcamp connect to Teknofest Flight Finals?", ["Bursa Robotics Bootcamp", "Prof Kerem", "Autonomous UAV Lab", "Teknofest Flight Finals"]),
            ("Who mentored me from the robotics bootcamp into the drone lab?", ["Prof Kerem", "Autonomous UAV Lab"]),
            ("What path led from Izmir Kernel Meetup to CloudCore Internship?", ["Izmir Kernel Meetup", "Deniz", "Nebula Microkernel", "CloudCore Internship"]),
            ("How did my open-source microkernel work connect to Deniz?", ["Deniz", "Nebula Microkernel"]),
            ("How did Stellar Hackathon lead to my SIBERAY Presidency?", ["Stellar Hackathon", "Liva", "Campus Career Fair", "SIBERAY Presidency"]),
            ("How did Ankara Kitap Kulubu connect to Causal Inference Preprint?", ["Ankara Kitap Kulubu", "Selin", "Bayesian Reading Seminar", "Causal Inference Preprint"]),
            ("How did Kadikoy Vinyl Shop lead to Moda Rooftop Set?", ["Kadikoy Vinyl Shop", "Caner", "Midnight Oscillator", "Moda Rooftop Set"]),
            ("Who did I cofound Midnight Oscillator with?", ["Caner", "Midnight Oscillator"]),
            ("How did Eymir Lake Running Club prepare me for Istanbul Half Marathon?", ["Eymir Lake Running Club", "Coach Hakan", "Sub-45 10K Training Block", "Istanbul Half Marathon"]),
            ("How did ODTU Teknokent Demo Day connect to TUBITAK BIGG Grant?", ["ODTU Teknokent Demo Day", "Emel", "KasaFlow Ledger", "TUBITAK BIGG Grant"]),
            ("How did Anatolia Code Mentors lead to Turkish Python Workbook?", ["Anatolia Code Mentors", "Zeynep", "Eskisehir Youth Hackathon", "Turkish Python Workbook"]),
            ("How did Galata Night Photowalk connect to Nocturne Ankara Zine?", ["Galata Night Photowalk", "Tarik", "Beyoglu Community Darkroom", "Nocturne Ankara Zine"]),
            ("How did Kizilay Chess Cafe lead to Anatolian Intervarsity Cup?", ["Kizilay Chess Cafe", "Murat", "University Varsity Chess", "Anatolian Intervarsity Cup"]),
            ("How did Maker Faire Istanbul connect to Uludag Range Trial?", ["Maker Faire Istanbul", "Burak", "LoRa Mesh Beacon", "Uludag Range Trial"]),
            ("How did Computational Linguistics Seminar lead to SIU 2026 Poster?", ["Computational Linguistics Seminar", "Dr Aylin", "Ottoman OCR Corpus", "SIU 2026 Poster"]),
            ("How did Urla Sailing School connect to Cesme Autumn Regatta?", ["Urla Sailing School", "Kaptan Demir", "ADB Skipper License", "Cesme Autumn Regatta"]),
            ("How did Cankaya Micro Roastery lead to Ankara AeroPress Throwdown?", ["Cankaya Micro Roastery", "Ece", "Ethiopian Washed Experiment", "Ankara AeroPress Throwdown"]),
            ("How did Aegean Energy Forum connect to Datca Village Microgrid?", ["Aegean Energy Forum", "Onur", "Solar Microgrid Simulator", "Datca Village Microgrid"]),
            ("What competition did my LoRa radio hardware get tested in?", ["LoRa Mesh Beacon", "Uludag Range Trial"]),
            ("Which professor supervised the Ottoman OCR Corpus?", ["Dr Aylin", "Ottoman OCR Corpus"]),
        ]

        # Bucket 2: 14 Cross-Chapter Bridge Questions (connects 2 separate communities!)
        b2_bridge = [
            ("How does Autonomous UAV Lab connect to LoRa Mesh Beacon?", ["Autonomous UAV Lab", "LoRa Mesh Beacon"]),
            ("Which hardware engineer's radio did our drone lab adopt?", ["Autonomous UAV Lab", "LoRa Mesh Beacon", "Burak"]),
            ("How is Deniz from the Rust meetup connected to SIBERAY Presidency?", ["Deniz", "SIBERAY Presidency"]),
            ("Who gave a Rust kernel security talk at SIBERAY Presidency?", ["Deniz", "SIBERAY Presidency"]),
            ("How did Selin from the statistics seminar help with Ottoman OCR Corpus?", ["Selin", "Ottoman OCR Corpus"]),
            ("Who advised Bayesian error bounds for Ottoman OCR Corpus?", ["Selin", "Ottoman OCR Corpus"]),
            ("How did Emel from Demo Day contribute to Turkish Python Workbook?", ["Emel", "Turkish Python Workbook"]),
            ("Who designed the layout of Turkish Python Workbook?", ["Emel", "Turkish Python Workbook"]),
            ("How does Caner from Midnight Oscillator connect to Nocturne Ankara Zine?", ["Caner", "Nocturne Ankara Zine"]),
            ("Who composed a soundtrack for Nocturne Ankara Zine?", ["Caner", "Nocturne Ankara Zine"]),
            ("How did Coach Hakan's running plan help with Cesme Autumn Regatta?", ["Coach Hakan", "Cesme Autumn Regatta"]),
            ("How is Onur from Aegean Energy Forum connected to TUBITAK BIGG Grant?", ["Onur", "TUBITAK BIGG Grant"]),
            ("Who reviewed unit economics for TUBITAK BIGG Grant?", ["Onur", "TUBITAK BIGG Grant"]),
            ("Which friend from KasaFlow Ledger also helped Anatolia Code Mentors' workbook?", ["Emel", "KasaFlow Ledger", "Turkish Python Workbook"]),
        ]

        # Bucket 3: 10 Bi-Temporal Point-in-Time & Supersession Questions (verified directly on graph TemporalScope!)
        b3_temporal = [
            ("current", date(2026, 11, 23), rec_ids["new_apt"], rec_ids["old_dorm"]),
            ("as_of", date(2025, 3, 15), rec_ids["old_dorm"], rec_ids["new_apt"]),
            ("current", date(2026, 11, 23), rec_ids["new_lap"], rec_ids["old_lap"]),
            ("as_of", date(2025, 4, 10), rec_ids["old_lap"], rec_ids["new_lap"]),
            ("current", date(2026, 11, 23), rec_ids["new_lang"], rec_ids["old_lang"]),
            ("as_of", date(2025, 2, 1), rec_ids["old_lang"], rec_ids["new_lang"]),
            ("current", date(2026, 11, 23), rec_ids["new_gym"], rec_ids["old_gym"]),
            ("as_of", date(2025, 5, 1), rec_ids["old_gym"], rec_ids["new_gym"]),
            ("current", date(2026, 11, 23), rec_ids["new_bank"], rec_ids["old_bank"]),
            ("as_of", date(2025, 3, 1), rec_ids["old_bank"], rec_ids["new_bank"]),
        ]

        # Bucket 4: 5 Bilingual Turkish<->English Multi-Session Questions
        b4_bilingual = [
            ("Ankara'daki kitap kulubu beni istatistik makalesine nasil bagladi?", ["Ankara Kitap Kulubu", "Selin", "Causal Inference Preprint"]),
            ("Teknofest finallerine giden insansiz hava araci laboratuvarina nasil girdim?", ["Prof Kerem", "Autonomous UAV Lab", "Teknofest Flight Finals"]),
            ("Izmir sistem bulusmasindan bulut stajina giden surec neydi?", ["Izmir Kernel Meetup", "Deniz", "CloudCore Internship"]),
            ("Kahve kavurma atolyesinden AeroPress yarismasina nasil gectim?", ["Cankaya Micro Roastery", "Ece", "Ankara AeroPress Throwdown"]),
            ("Urla yelken okulundan Cesme yarisina kadar olan surecim neydi?", ["Urla Sailing School", "Kaptan Demir", "Cesme Autumn Regatta"]),
        ]

        # Bucket 5: 5 Abstention / Negative Control Questions (must NOT hallucinate chains about non-existent topics)
        b5_abstain = [
            ("What happened during my Antarctic expedition in 2024?", ["Antarctic"]),
            ("Who did I meet at the Tokyo Jazz Festival?", ["Tokyo Jazz"]),
            ("Tell me about my underwater welding certification.", ["underwater welding"]),
            ("How did my ceramic pottery studio in Lisbon start?", ["Lisbon"]),
            ("What score did I get in Quantum Gastronomy 401?", ["Quantum Gastronomy"]),
        ]

        configs = [
            ("C0_BM25", False, False, False),
            ("C1_Hybrid_Semantic", True, False, False),
            ("C2_Dreaming_Only", True, True, False),
            ("C3_Full_Operation_Iacon", True, True, True),
        ]

        async with sessions() as db:
            epoch = await store.epoch(SimpleNamespace(user_id=1))
            graph = await store.graph.load(db, 1, epoch)

        summary = {}
        for name, sem_on, dream_on, ppr_on in configs:
            store.semantic.enabled = sem_on
            store.graph.enabled = ppr_on
            os.environ["SPEDA_DREAMING_CONTEXT"] = "true" if dream_on else "false"
            runtime = MemoryRuntime(store)

            async def eval_bucket(qs):
                recs, lats = [], []
                full_hits = 0
                for q_text, required in qs:
                    ctx = SimpleNamespace(
                        request_id=f"b54-{name}", user_id=1, agent_id="speda",
                        timezone="Europe/Istanbul", triggered_by="user", trigger_payload={},
                        parent_step_id=None, model_call_index=0, system_prompt=None,
                        task_role="interactive", run_id=None, room_id=None, project_id=None,
                        session_id=99, memory_source_id=None, allowed_tools=("memory_lookup",),
                        conversation_history=[{"role": "user", "content": q_text}],
                        memory_budget=MemoryBudget(), extra={},
                    )
                    t0 = perf_counter()
                    pkt = await runtime.prepare(ctx)
                    lats.append((perf_counter() - t0) * 1000.0)
                    blob = packet_json(pkt)
                    found = sum(1 for tok in required if tok in blob)
                    rec = found / len(required)
                    recs.append(rec)
                    if rec == 1.0:
                        full_hits += 1
                return round(float(np.mean(recs)), 3), f"{full_hits}/{len(qs)}", round(float(np.mean(lats)), 1), round(float(np.percentile(lats, 95)), 1)

            b1_r, b1_h, b1_avg, b1_p95 = await eval_bucket(b1_intra)
            b2_r, b2_h, b2_avg, b2_p95 = await eval_bucket(b2_bridge)
            b4_r, b4_h, b4_avg, b4_p95 = await eval_bucket(b4_bilingual)

            # Bucket 3: Bi-temporal precision (only C3 has TemporalScope graph filtering)
            b3_hits = 0
            if ppr_on:
                for mode, dt_val, must_have, must_exclude in b3_temporal:
                    scope = TemporalScope.current(dt_val) if mode == "current" else TemporalScope.at_date(dt_val)
                    res = dict(graph.personalized_pagerank({rec_ids["owner_me"]: 1.0}, scope=scope, top_k=20))
                    if must_have in res and must_exclude not in res:
                        b3_hits += 1

            # Bucket 5: Abstention (0 false fabricated tokens)
            b5_clean = 0
            for q_text, forbidden in b5_abstain:
                ctx = SimpleNamespace(
                    request_id=f"b54-abs-{name}", user_id=1, agent_id="speda",
                    timezone="Europe/Istanbul", triggered_by="user", trigger_payload={},
                    parent_step_id=None, model_call_index=0, system_prompt=None,
                    task_role="interactive", run_id=None, room_id=None, project_id=None,
                    session_id=99, memory_source_id=None, allowed_tools=("memory_lookup",),
                    conversation_history=[{"role": "user", "content": q_text}],
                    memory_budget=MemoryBudget(), extra={},
                )
                pkt = await runtime.prepare(ctx)
                blob = packet_json(pkt)
                if not any(tok in blob for tok in forbidden):
                    b5_clean += 1

            summary[name] = {
                "B1_unseen_intra_20q": {"recall": b1_r, "full_chain": b1_h, "avg_ms": b1_avg, "p95_ms": b1_p95},
                "B2_unseen_cross_chapter_14q": {"recall": b2_r, "full_chain": b2_h, "avg_ms": b2_avg, "p95_ms": b2_p95},
                "B3_bitemporal_10q": {"exact_temporal_accuracy": f"{b3_hits}/10" if ppr_on else "N/A (no temporal graph)"},
                "B4_bilingual_5q": {"recall": b4_r, "full_chain": b4_h, "avg_ms": b4_avg},
                "B5_abstention_5q": {"clean_abstentions": f"{b5_clean}/5"},
            }

        await engine.dispose()
        return {"total_questions": 54, "chapters": len(CHAPTER_SPECS), "cross_chapter_bridges": len(CROSS_CHAPTER_BRIDGES), "results": summary}


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run_54q_benchmark()), indent=2, ensure_ascii=False))
