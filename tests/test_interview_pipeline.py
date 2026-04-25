"""面试扇出、一面打分、面后分岔（debate / hm）。"""

from __future__ import annotations

from supergenius.agents.base import utc_now_iso
from supergenius.agents.interview_fanout import InterviewFanoutAgent
from supergenius.agents.interviewers import TechInterviewerAgent
from supergenius.agents.post_interview import PostInterviewAgent
from supergenius.schema.tables import (
    InterviewRowStatus,
    JobStatus,
    PipelineStage,
    ResumeDecision,
    ResumeStatus,
)
from tests.conftest import StubLLM

_SCORE = {
    "total_score": 7.0,
    "dimension_json": {"a": 1},
    "notes": "ok",
    "quote_snippet": "quote",
}


def _seed_open_job(ctx, mem, job_id: str = "J1"):
    return mem.create_record(
        ctx.table_ids["jobs"],
        {
            "job_id": job_id,
            "jd_text": "需要 Python。",
            "status": JobStatus.OPEN.value,
        },
    )


def test_interview_fanout_creates_three_rows(agent_ctx, mem_bitable):
    _seed_open_job(agent_ctx, mem_bitable)
    r = mem_bitable.create_record(
        agent_ctx.table_ids["resumes"],
        {
            "resume_id": "R1",
            "job_id": "J1",
            "raw_text": "x",
            "status": ResumeStatus.SCREENED.value,
            "decision": ResumeDecision.PASS_.value,
            "pipeline_stage": PipelineStage.INTERVIEW_QUEUED.value,
        },
    )
    f = InterviewFanoutAgent(agent_ctx)
    assert f.tick() == 1
    rows = mem_bitable.search_records(agent_ctx.table_ids["interviews"], None, page_size=20)
    assert len(rows) == 3
    rec = mem_bitable.get_record(agent_ctx.table_ids["resumes"], r)
    assert rec.fields["pipeline_stage"] == PipelineStage.INTERVIEWS_IN_PROGRESS.value


def test_tech_interviewer_marks_done(agent_ctx, mem_bitable):
    _seed_open_job(agent_ctx, mem_bitable)
    mem_bitable.create_record(
        agent_ctx.table_ids["resumes"],
        {
            "resume_id": "R1",
            "job_id": "J1",
            "raw_text": "Python 3 年",
            "status": ResumeStatus.SCREENED.value,
        },
    )
    iid = mem_bitable.create_record(
        agent_ctx.table_ids["interviews"],
        {
            "interview_id": "I1",
            "resume_id": "R1",
            "job_id": "J1",
            "role": "tech",
            "status": InterviewRowStatus.PENDING.value,
            "total_score": 0,
            "dimension_json": "{}",
            "notes": "",
            "quote_snippet": "",
        },
    )
    agent_ctx.llm = StubLLM([_SCORE])
    ag = TechInterviewerAgent(agent_ctx)
    assert ag.tick() == 1
    rec = mem_bitable.get_record(agent_ctx.table_ids["interviews"], iid)
    assert rec.fields["status"] == InterviewRowStatus.DONE.value
    assert float(rec.fields.get("total_score", 0)) == 7.0


def test_post_interview_goes_hm_when_low_spread(agent_ctx, mem_bitable):
    _seed_open_job(agent_ctx, mem_bitable)
    mem_bitable.create_record(
        agent_ctx.table_ids["resumes"],
        {
            "resume_id": "R1",
            "job_id": "J1",
            "raw_text": "x",
            "status": ResumeStatus.SCREENED.value,
            "pipeline_stage": PipelineStage.INTERVIEWS_IN_PROGRESS.value,
        },
    )
    for role, sc in (("tech", 7), ("business", 7), ("culture", 6)):
        mem_bitable.create_record(
            agent_ctx.table_ids["interviews"],
            {
                "interview_id": f"IV-{role}",
                "resume_id": "R1",
                "job_id": "J1",
                "role": role,
                "status": InterviewRowStatus.DONE.value,
                "total_score": sc,
                "dimension_json": "{}",
                "notes": "",
                "quote_snippet": "",
                "updated_at": utc_now_iso(),
            },
        )
    p = PostInterviewAgent(agent_ctx)
    assert p.tick() == 1
    rows = mem_bitable.search_records(agent_ctx.table_ids["resumes"], None, page_size=10)
    assert len(rows) == 1
    assert rows[0].fields["pipeline_stage"] == PipelineStage.HM_ARBITRATION.value


def test_post_interview_goes_debate_when_high_spread(agent_ctx, mem_bitable):
    _seed_open_job(agent_ctx, mem_bitable)
    mem_bitable.create_record(
        agent_ctx.table_ids["resumes"],
        {
            "resume_id": "R1",
            "job_id": "J1",
            "raw_text": "x",
            "status": ResumeStatus.SCREENED.value,
            "pipeline_stage": PipelineStage.INTERVIEWS_IN_PROGRESS.value,
        },
    )
    for role, sc in (("tech", 9), ("business", 2), ("culture", 5)):
        mem_bitable.create_record(
            agent_ctx.table_ids["interviews"],
            {
                "interview_id": f"IV-{role}-2",
                "resume_id": "R1",
                "job_id": "J1",
                "role": role,
                "status": InterviewRowStatus.DONE.value,
                "total_score": sc,
                "dimension_json": "{}",
                "notes": "",
                "quote_snippet": "",
                "updated_at": utc_now_iso(),
            },
        )
    p = PostInterviewAgent(agent_ctx)
    assert p.tick() == 1
    rows = mem_bitable.search_records(agent_ctx.table_ids["resumes"], None, page_size=10)
    assert rows[0].fields["pipeline_stage"] == PipelineStage.DEBATE.value
