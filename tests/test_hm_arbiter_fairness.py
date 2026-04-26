"""经理仲裁：性别反事实公平性检测。"""

from __future__ import annotations

from supergenius.agents.hiring_manager_arbiter import (
    HM_DECISION_MANUAL,
    HiringManagerArbiterAgent,
)
from supergenius.schema.tables import JobStatus, PipelineStage, ResumeStatus
from tests.conftest import StubLLM

_ARB = {"decision": "hire", "reason": "ok"}
_ARB_REJ = {"decision": "reject", "reason": "no"}


def _seed_job(ctx, mem):
    return mem.create_record(
        ctx.table_ids["jobs"],
        {"job_id": "J1", "jd_text": "JD", "status": JobStatus.OPEN.value},
    )


def test_hm_arbiter_fairness_disabled_single_call(agent_ctx, mem_bitable):
    agent_ctx.config.fairness_counterfactual_enabled = False
    agent_ctx.llm = StubLLM([_ARB])
    _seed_job(agent_ctx, mem_bitable)
    rid = mem_bitable.create_record(
        agent_ctx.table_ids["resumes"],
        {
            "resume_id": "R1",
            "job_id": "J1",
            "raw_text": "某男，5 年经验。",
            "status": ResumeStatus.SCREENED.value,
            "pipeline_stage": PipelineStage.HM_ARBITRATION.value,
            "gender": "male",
        },
    )
    a = HiringManagerArbiterAgent(agent_ctx)
    assert a.tick() == 1
    assert len(agent_ctx.llm.calls) == 1
    rec = mem_bitable.get_record(agent_ctx.table_ids["resumes"], rid)
    assert rec.fields["hm_decision"] == "hire"
    assert rec.fields["pipeline_stage"] == PipelineStage.OFFER_DRAFTING.value


def test_hm_arbiter_counterfactual_match(agent_ctx, mem_bitable):
    agent_ctx.config.fairness_counterfactual_enabled = True
    agent_ctx.llm = StubLLM([_ARB, _ARB])
    _seed_job(agent_ctx, mem_bitable)
    rid = mem_bitable.create_record(
        agent_ctx.table_ids["resumes"],
        {
            "resume_id": "R1",
            "job_id": "J1",
            "raw_text": "简历。",
            "status": ResumeStatus.SCREENED.value,
            "pipeline_stage": PipelineStage.HM_ARBITRATION.value,
            "gender": "male",
        },
    )
    a = HiringManagerArbiterAgent(agent_ctx)
    assert a.tick() == 1
    assert len(agent_ctx.llm.calls) == 2
    rec = mem_bitable.get_record(agent_ctx.table_ids["resumes"], rid)
    assert rec.fields["hm_decision"] == "hire"
    assert rec.fields["pipeline_stage"] == PipelineStage.OFFER_DRAFTING.value


def test_hm_arbiter_counterfactual_mismatch_hold(agent_ctx, mem_bitable):
    agent_ctx.config.fairness_counterfactual_enabled = True
    agent_ctx.llm = StubLLM([_ARB, _ARB_REJ])
    _seed_job(agent_ctx, mem_bitable)
    rid = mem_bitable.create_record(
        agent_ctx.table_ids["resumes"],
        {
            "resume_id": "R1",
            "job_id": "J1",
            "raw_text": "简历。",
            "status": ResumeStatus.SCREENED.value,
            "pipeline_stage": PipelineStage.HM_ARBITRATION.value,
            "gender": "female",
        },
    )
    a = HiringManagerArbiterAgent(agent_ctx)
    assert a.tick() == 1
    rec = mem_bitable.get_record(agent_ctx.table_ids["resumes"], rid)
    assert rec.fields["hm_decision"] == HM_DECISION_MANUAL
    assert rec.fields["pipeline_stage"] == PipelineStage.HOLD_REVIEW.value
    reps = mem_bitable.search_records(agent_ctx.table_ids["reports"], None, page_size=10)
    assert len(reps) == 1


def test_hm_arbiter_unknown_gender_manual(agent_ctx, mem_bitable):
    agent_ctx.config.fairness_counterfactual_enabled = True
    agent_ctx.llm = StubLLM([{"gender": "unknown"}])
    _seed_job(agent_ctx, mem_bitable)
    rid = mem_bitable.create_record(
        agent_ctx.table_ids["resumes"],
        {
            "resume_id": "R1",
            "job_id": "J1",
            "raw_text": "无性别线索。",
            "status": ResumeStatus.SCREENED.value,
            "pipeline_stage": PipelineStage.HM_ARBITRATION.value,
            "gender": "",
        },
    )
    a = HiringManagerArbiterAgent(agent_ctx)
    assert a.tick() == 1
    assert len(agent_ctx.llm.calls) == 1
    rec = mem_bitable.get_record(agent_ctx.table_ids["resumes"], rid)
    assert rec.fields["hm_decision"] == HM_DECISION_MANUAL
    assert rec.fields["pipeline_stage"] == PipelineStage.HOLD_REVIEW.value
    assert rec.fields.get("gender") == "unknown"


def test_hm_arbiter_infer_then_match(agent_ctx, mem_bitable):
    agent_ctx.config.fairness_counterfactual_enabled = True
    agent_ctx.llm = StubLLM([{"gender": "male"}, _ARB, _ARB])
    _seed_job(agent_ctx, mem_bitable)
    rid = mem_bitable.create_record(
        agent_ctx.table_ids["resumes"],
        {
            "resume_id": "R1",
            "job_id": "J1",
            "raw_text": "姓名：王小明，男。",
            "status": ResumeStatus.SCREENED.value,
            "pipeline_stage": PipelineStage.HM_ARBITRATION.value,
            "gender": "",
        },
    )
    a = HiringManagerArbiterAgent(agent_ctx)
    assert a.tick() == 1
    assert len(agent_ctx.llm.calls) == 3
    rec = mem_bitable.get_record(agent_ctx.table_ids["resumes"], rid)
    assert rec.fields["hm_decision"] == "hire"
    assert rec.fields.get("gender") == "male"
