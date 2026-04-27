"""三位面试官：各自扫 interviews 表中对应 role 且 pending 的行，独立打分写回。"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from supergenius.agents.base import AgentBase, ClaimedRecord, utc_now_iso
from supergenius.agents.debate_agent import DebateAgent
from supergenius.agents.interview_fanout import InterviewFanoutAgent
from supergenius.agents.post_interview import PostInterviewAgent
from supergenius.feishu.bitable import Record
from supergenius.feishu.field_value import feishu_text_to_str
from supergenius.llm.client import render_prompt
from supergenius.schema.tables import (
    AGENT_BUSINESS,
    AGENT_CULTURE,
    AGENT_TECH,
    InterviewRole,
    InterviewRowStatus,
)

_SCORE_SCHEMA = {
    "name": "interview_score",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "total_score": {"type": "number", "minimum": 0, "maximum": 10},
            "dimension_json": {"type": "object"},
            "notes": {"type": "string"},
            "quote_snippet": {"type": "string"},
        },
        "required": ["total_score", "dimension_json", "notes", "quote_snippet"],
        "additionalProperties": False,
    },
}


def _find_job_by_job_id(bitable: Any, table_ids: dict[str, str], job_id: str) -> Record | None:
    if not job_id:
        return None
    jobs_tid = table_ids["jobs"]
    rows = bitable.search_records(
        jobs_tid,
        filter_conditions=[{"field_name": "job_id", "operator": "is", "value": [job_id]}],
        page_size=50,
    )
    for r in rows:
        if feishu_text_to_str(r.fields.get("job_id")) == job_id:
            return r
    rows = bitable.search_records(jobs_tid, filter_conditions=None, page_size=200)
    for r in rows:
        if feishu_text_to_str(r.fields.get("job_id")) == job_id:
            return r
    return None


def _load_resume(bitable: Any, table_ids: dict[str, str], resume_id: str) -> Record | None:
    if not resume_id:
        return None
    tid = table_ids["resumes"]
    rows = bitable.search_records(
        tid,
        filter_conditions=[{"field_name": "resume_id", "operator": "is", "value": [resume_id]}],
        page_size=20,
    )
    for r in rows:
        if feishu_text_to_str(r.fields.get("resume_id")) == resume_id:
            return r
    rows = bitable.search_records(tid, filter_conditions=None, page_size=200)
    for r in rows:
        if feishu_text_to_str(r.fields.get("resume_id")) == resume_id:
            return r
    return None


class _InterviewerBase(AgentBase):
    name: str = AGENT_TECH
    role: InterviewRole = InterviewRole.TECH
    prompt_name: str = "tech_interviewer"

    in_progress_status = InterviewRowStatus.IN_PROGRESS.value
    rollback_status_on_error = InterviewRowStatus.PENDING.value
    watch_table = "interviews"

    def claim_filter(self) -> list[dict[str, Any]]:
        return [
            {"field_name": "role", "operator": "is", "value": [self.role.value]},
            {"field_name": "status", "operator": "is", "value": [InterviewRowStatus.PENDING.value]},
        ]

    def handle(self, rec: ClaimedRecord) -> dict[str, Any] | None:
        job_id = feishu_text_to_str(rec.fields.get("job_id"))
        resume_id = feishu_text_to_str(rec.fields.get("resume_id"))
        job_row = _find_job_by_job_id(self.ctx.bitable, self.ctx.table_ids, job_id)
        resume_row = _load_resume(self.ctx.bitable, self.ctx.table_ids, resume_id)
        if not job_row or not resume_row:
            logger.warning(f"[{self.name}] 缺少 job/resume 行，回滚")
            return None
        jd_text = feishu_text_to_str(job_row.fields.get("jd_text")).strip()
        resume_text = feishu_text_to_str(resume_row.fields.get("raw_text")).strip()
        if not jd_text or not resume_text:
            logger.warning(f"[{self.name}] jd 或简历为空，回滚")
            return None

        prompt = render_prompt(
            self.prompt_name,
            jd_text=jd_text,
            resume_text=resume_text,
            role_hint=self.role.value,
        )
        try:
            result = self.ctx.llm.chat(
                system=f"You are interviewer for {self.role.value} round.",
                user=prompt,
                json_schema=_SCORE_SCHEMA,
                temperature=0.4,
            )
        except Exception as e:
            logger.exception(f"[{self.name}] LLM 失败: {e}")
            return None
        if not isinstance(result, dict):
            return None
        dim = result.get("dimension_json")
        if isinstance(dim, dict):
            dim_s = json.dumps(dim, ensure_ascii=False)
        else:
            dim_s = str(dim or "{}")
        return {
            "total_score": float(result.get("total_score") or 0),
            "dimension_json": dim_s[:4000],
            "notes": str(result.get("notes") or "")[:2000],
            "quote_snippet": str(result.get("quote_snippet") or "")[:500],
            "status": InterviewRowStatus.DONE.value,
            "owner_agent": "",
            "updated_at": utc_now_iso(),
        }


class TechInterviewerAgent(_InterviewerBase):
    name = AGENT_TECH
    role = InterviewRole.TECH
    prompt_name = "tech_interviewer"

    def tick(self) -> int:
        n = InterviewFanoutAgent(self.ctx).tick()
        n += super().tick()
        return n


class BusinessInterviewerAgent(_InterviewerBase):
    name = AGENT_BUSINESS
    role = InterviewRole.BUSINESS
    prompt_name = "business_interviewer"


class CultureInterviewerAgent(_InterviewerBase):
    name = AGENT_CULTURE
    role = InterviewRole.CULTURE
    prompt_name = "culture_interviewer"

    def tick(self) -> int:
        n = super().tick()
        n += PostInterviewAgent(self.ctx).tick()
        n += DebateAgent(self.ctx).tick()
        return n
