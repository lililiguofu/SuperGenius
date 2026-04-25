"""MemoryBitable 本身也要测，否则其他测试建在沙上。"""

from __future__ import annotations


def test_create_and_search(mem_bitable):
    tid = mem_bitable.add_table("jobs")
    rid = mem_bitable.create_record(tid, {"job_id": "J1", "status": "draft"})
    found = mem_bitable.search_records(
        tid, filter_conditions=[{"field_name": "status", "operator": "is", "value": ["draft"]}]
    )
    assert len(found) == 1
    assert found[0].record_id == rid
    assert found[0].fields["job_id"] == "J1"


def test_update(mem_bitable):
    tid = mem_bitable.add_table("jobs")
    rid = mem_bitable.create_record(tid, {"status": "draft", "owner_agent": ""})
    mem_bitable.update_record(tid, rid, {"owner_agent": "jd_strategist"})
    rec = mem_bitable.get_record(tid, rid)
    assert rec.fields["owner_agent"] == "jd_strategist"
    assert rec.fields["status"] == "draft"


def test_filter_no_match(mem_bitable):
    tid = mem_bitable.add_table("jobs")
    mem_bitable.create_record(tid, {"status": "open"})
    found = mem_bitable.search_records(
        tid, filter_conditions=[{"field_name": "status", "operator": "is", "value": ["draft"]}]
    )
    assert found == []
