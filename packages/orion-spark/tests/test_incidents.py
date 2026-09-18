# SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
# SPDX-License-Identifier: AGPL-3.0-or-later

from orion_spark.incidents import IncidentRecord, IncidentStore


def test_incident_record_creation():
    rec = IncidentRecord(
        service="speda",
        type="deployment_failure",
        failed_revision="a91bc72",
        restored_revision="83ce21f",
        recovery="rollback",
        result="success",
        downtime_seconds=47,
    )
    d = rec.to_dict()
    assert d["service"] == "speda"
    assert d["type"] == "deployment_failure"
    assert d["failed_revision"] == "a91bc72"
    assert d["restored_revision"] == "83ce21f"
    assert d["recovery"] == "rollback"
    assert d["result"] == "success"
    assert d["downtime_seconds"] == 47
    assert d["timestamp"] != ""


def test_incident_store_persistence(tmp_path):
    store = IncidentStore(tmp_path)
    assert len(store.get_all()) == 0

    rec1 = IncidentRecord(
        service="speda",
        type="process_crash",
        failed_revision="rev1",
        restored_revision="rev1",
        recovery="restart",
        result="success",
        downtime_seconds=15,
    )
    store.record(rec1)

    rec2 = IncidentRecord(
        service="speda",
        type="deployment_failure",
        failed_revision="rev2",
        restored_revision="rev1",
        recovery="rollback",
        result="critical",
        downtime_seconds=65,
    )
    store.record(rec2)

    all_recs = store.get_all()
    assert len(all_recs) == 2
    assert all_recs[0].recovery == "restart"
    assert all_recs[1].recovery == "rollback"

    recent = store.get_recent(1)
    assert len(recent) == 1
    assert recent[0].result == "critical"
