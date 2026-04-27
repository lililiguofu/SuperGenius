"""JD 策划官 + 招聘经理 的闭环测试。"""

from __future__ import annotations

from supergenius.agents.jd_strategist import _HiringManagerJdApproval, _JdStrategistCore
from supergenius.schema.tables import JobStatus
from tests.conftest import StubLLM


def test_jd_strategist_drafts_and_flags_for_approval(agent_ctx):
    agent_ctx.llm = StubLLM(["# 岗位标题\n\n这是一个可爱的 JD。\n\n## 硬要求\n- Python 3+ 年"])

    jobs_tid = agent_ctx.table_ids["jobs"]
    rid = agent_ctx.bitable.create_record(
        jobs_tid,
        {
            "job_id": "JOB-42",
            "title": "后端工程师",
            "level": "P5",
            "jd_brief": "要找个 Python 后端",
            "status": JobStatus.DRAFT.value,
            "owner_agent": "",
        },
    )
    jd = _JdStrategistCore(agent_ctx)
    assert jd.tick() == 1

    rec = agent_ctx.bitable.get_record(jobs_tid, rid)
    assert rec.fields["status"] == JobStatus.JD_PENDING_APPROVAL.value
    assert "Python" in rec.fields["jd_text"]
    assert rec.fields["owner_agent"] == ""


def test_hiring_manager_approves(agent_ctx):
    agent_ctx.llm = StubLLM([{"decision": "approve", "notes": "looks good"}])

    jobs_tid = agent_ctx.table_ids["jobs"]
    rid = agent_ctx.bitable.create_record(
        jobs_tid,
        {
            "job_id": "JOB-42",
            "jd_brief": "brief",
            "jd_text": "# JD 正文",
            "status": JobStatus.JD_PENDING_APPROVAL.value,
            "owner_agent": "",
        },
    )
    hm = _HiringManagerJdApproval(agent_ctx)
    assert hm.tick() == 1

    rec = agent_ctx.bitable.get_record(jobs_tid, rid)
    assert rec.fields["status"] == JobStatus.OPEN.value
    assert rec.fields["owner_agent"] == ""


def test_hiring_manager_requests_revision_goes_back_to_draft(agent_ctx):
    agent_ctx.llm = StubLLM(
        [{"decision": "request_revision", "notes": "薪资范围没写清楚"}]
    )
    jobs_tid = agent_ctx.table_ids["jobs"]
    rid = agent_ctx.bitable.create_record(
        jobs_tid,
        {
            "job_id": "JOB-43",
            "jd_brief": "brief",
            "jd_text": "# 粗糙的 JD",
            "status": JobStatus.JD_PENDING_APPROVAL.value,
            "owner_agent": "",
        },
    )
    hm = _HiringManagerJdApproval(agent_ctx)
    hm.tick()

    rec = agent_ctx.bitable.get_record(jobs_tid, rid)
    assert rec.fields["status"] == JobStatus.DRAFT.value


def test_hiring_manager_handles_empty_jd_text(agent_ctx):
    agent_ctx.llm = StubLLM([])  # 不会被调用
    jobs_tid = agent_ctx.table_ids["jobs"]
    rid = agent_ctx.bitable.create_record(
        jobs_tid,
        {
            "job_id": "JOB-44",
            "jd_brief": "brief",
            "jd_text": "",
            "status": JobStatus.JD_PENDING_APPROVAL.value,
            "owner_agent": "",
        },
    )
    hm = _HiringManagerJdApproval(agent_ctx)
    hm.tick()

    rec = agent_ctx.bitable.get_record(jobs_tid, rid)
    assert rec.fields["status"] == JobStatus.DRAFT.value
