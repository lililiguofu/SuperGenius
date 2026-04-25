"""初筛 Screener：双次打分、pipeline_stage。"""

from __future__ import annotations

from supergenius.agents.screener import ScreenerAgent
from supergenius.schema.tables import (
    JobStatus,
    PipelineStage,
    ResumeDecision,
    ResumeStatus,
)
from tests.conftest import StubLLM

_SCREEN = {
    "score": 8,
    "dimensions": {
        "hard_requirements": 8,
        "relevance": 8,
        "growth": 7,
        "stability": 7,
        "red_flags": 9,
    },
    "decision": "pass",
    "reason": "ok",
    "parsed_skills": ["Python"],
}


def test_screener_pass_sets_interview_queued(agent_ctx):
    # 两次同分，方差 0
    agent_ctx.llm = StubLLM([dict(_SCREEN), dict(_SCREEN)])

    jobs_tid = agent_ctx.table_ids["jobs"]
    agent_ctx.bitable.create_record(
        jobs_tid,
        {
            "job_id": "J1",
            "jd_text": "需要 Python 后端",
            "status": JobStatus.OPEN.value,
        },
    )
    resumes_tid = agent_ctx.table_ids["resumes"]
    rid = agent_ctx.bitable.create_record(
        resumes_tid,
        {
            "resume_id": "R1",
            "job_id": "J1",
            "raw_text": "5 年 Python 经验。",
            "status": ResumeStatus.NEW.value,
        },
    )
    s = ScreenerAgent(agent_ctx)
    assert s.tick() == 1
    rec = agent_ctx.bitable.get_record(resumes_tid, rid)
    assert rec.fields["decision"] == ResumeDecision.PASS_.value
    assert rec.fields["status"] == ResumeStatus.SCREENED.value
    assert rec.fields["pipeline_stage"] == PipelineStage.INTERVIEW_QUEUED.value


def test_screener_hold_on_high_variance(agent_ctx):
    # 0–10 量表下 pvariance 最大约 25；把阈值调低以触发 hold
    agent_ctx.config.scheduler.screener_var_threshold = 1.0
    a = {**_SCREEN, "score": 10}
    b = {**_SCREEN, "score": 0}
    agent_ctx.llm = StubLLM([a, b])

    jobs_tid = agent_ctx.table_ids["jobs"]
    agent_ctx.bitable.create_record(
        jobs_tid,
        {"job_id": "J1", "jd_text": "JD", "status": JobStatus.OPEN.value},
    )
    resumes_tid = agent_ctx.table_ids["resumes"]
    rid = agent_ctx.bitable.create_record(
        resumes_tid,
        {
            "resume_id": "R2",
            "job_id": "J1",
            "raw_text": "简历",
            "status": ResumeStatus.NEW.value,
        },
    )
    s = ScreenerAgent(agent_ctx)
    assert s.tick() == 1
    rec = agent_ctx.bitable.get_record(resumes_tid, rid)
    assert rec.fields["decision"] == ResumeDecision.HOLD.value
    assert rec.fields["pipeline_stage"] == PipelineStage.HOLD_REVIEW.value
