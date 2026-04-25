"""Offer 起草 + 发送；候选人模拟回复（接受/谈薪/拒绝/未响应）。"""

from __future__ import annotations

import json
import uuid
from typing import Any

from loguru import logger

from supergenius.agents.base import AgentBase, ClaimedRecord, utc_now_iso
from supergenius.feishu.field_value import feishu_text_to_str
from supergenius.llm.client import render_prompt
from supergenius.schema.tables import (
    AGENT_CANDIDATE,
    AGENT_OFFER,
    OfferStatus,
    PipelineStage,
    ResumeStatus,
)

_OFFER_DRAFT = {
    "name": "offer_draft",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "salary": {"type": "number"},
            "hm_notes": {"type": "string"},
        },
        "required": ["salary", "hm_notes"],
        "additionalProperties": False,
    },
}

_CANDIDATE_RESP = {
    "name": "cand",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["accept", "negotiate", "reject", "ghost"],
            },
            "message": {"type": "string"},
        },
        "required": ["action", "message"],
        "additionalProperties": False,
    },
}


def _find_job(bitable: Any, tids: dict[str, str], job_id: str) -> Any:
    rows = bitable.search_records(
        tids["jobs"],
        filter_conditions=[{"field_name": "job_id", "operator": "is", "value": [job_id]}],
        page_size=5,
    )
    for r in rows:
        if feishu_text_to_str(r.fields.get("job_id")) == job_id:
            return r
    for r in bitable.search_records(tids["jobs"], filter_conditions=None, page_size=200):
        if feishu_text_to_str(r.fields.get("job_id")) == job_id:
            return r
    return None


class OfferManagerAgent(AgentBase):
    name = AGENT_OFFER
    watch_table = "resumes"
    in_progress_status = None
    rollback_status_on_error = None

    def claim_filter(self) -> list[dict[str, Any]]:
        return [
            {
                "field_name": "pipeline_stage",
                "operator": "is",
                "value": [PipelineStage.OFFER_DRAFTING.value],
            }
        ]

    def handle(self, rec: ClaimedRecord) -> dict[str, Any] | None:
        if feishu_text_to_str(rec.fields.get("status")) != ResumeStatus.SCREENED.value:
            return None
        if feishu_text_to_str(rec.fields.get("hm_decision")) != "hire":
            return None
        job_id = feishu_text_to_str(rec.fields.get("job_id"))
        if not job_id:
            return None
        job = _find_job(self.ctx.bitable, self.ctx.table_ids, job_id)
        if not job:
            return None
        jd = feishu_text_to_str(job.fields.get("jd_text"))[:3000]
        bmin = job.fields.get("budget_min")
        bmax = job.fields.get("budget_max")
        try:
            out = self.ctx.llm.chat(
                system="You are the hiring manager making an offer.",
                user=render_prompt(
                    "offer_draft",
                    jd_excerpt=jd,
                    budget_min=str(bmin),
                    budget_max=str(bmax),
                    resume_excerpt=feishu_text_to_str(rec.fields.get("raw_text"))[:2000],
                ),
                json_schema=_OFFER_DRAFT,
            )
        except Exception as e:
            logger.exception(f"[{self.name}] offer LLM: {e}")
            return None
        if not isinstance(out, dict):
            return None
        salary = float(out.get("salary") or 0)
        notes = str(out.get("hm_notes") or "")
        offer_id = f"OF-{uuid.uuid4().hex[:10]}"
        self.ctx.bitable.create_record(
            self.ctx.table_ids["offers"],
            {
                "offer_id": offer_id,
                "resume_id": feishu_text_to_str(rec.fields.get("resume_id")) or rec.record_id,
                "job_id": job_id,
                "salary_offer": salary,
                "status": OfferStatus.SENT.value,
                "hm_notes": notes[:2000],
                "candidate_message": "",
                "owner_agent": "",
                "updated_at": utc_now_iso(),
            },
        )
        return {
            "pipeline_stage": PipelineStage.OFFER_SENT.value,
            "analyst_note": json.dumps(
                {"last_offer_id": offer_id, "salary": salary},
                ensure_ascii=False,
            )[:2000],
            "owner_agent": "",
        }


class CandidateSimulatorAgent(AgentBase):
    name = AGENT_CANDIDATE
    watch_table = "offers"
    in_progress_status = None
    rollback_status_on_error = None

    def claim_filter(self) -> list[dict[str, Any]]:
        return [
            {
                "field_name": "status",
                "operator": "is",
                "value": [OfferStatus.SENT.value],
            }
        ]

    def handle(self, rec: ClaimedRecord) -> dict[str, Any] | None:
        if feishu_text_to_str(rec.fields.get("candidate_message")):
            return None
        resume_id = feishu_text_to_str(rec.fields.get("resume_id"))
        tid = self.ctx.table_ids["resumes"]
        rows = self.ctx.bitable.search_records(
            tid,
            filter_conditions=[{"field_name": "resume_id", "operator": "is", "value": [resume_id]}],
            page_size=5,
        )
        resume = rows[0] if rows else None
        if not resume:
            return None
        raw = feishu_text_to_str(resume.fields.get("raw_text"))[:2000]
        try:
            out = self.ctx.llm.chat(
                system="You are the job candidate. Reply in short Chinese JSON only.",
                user=render_prompt(
                    "candidate_response",
                    salary_offer=str(rec.fields.get("salary_offer") or ""),
                    job_context=raw,
                ),
                json_schema=_CANDIDATE_RESP,
            )
        except Exception as e:
            logger.exception(f"[{self.name}] {e}")
            return None
        if not isinstance(out, dict):
            return None
        act = out.get("action")
        msg = str(out.get("message") or "")
        status_map = {
            "accept": OfferStatus.ACCEPTED.value,
            "negotiate": OfferStatus.NEGOTIATE.value,
            "reject": OfferStatus.REJECTED.value,
            "ghost": OfferStatus.NO_SHOW.value,
        }
        next_offer = status_map.get(str(act), OfferStatus.REJECTED.value)
        r_tid = self.ctx.table_ids["resumes"]
        r_rows = self.ctx.bitable.search_records(
            r_tid,
            filter_conditions=[{"field_name": "resume_id", "operator": "is", "value": [resume_id]}],
            page_size=3,
        )
        if r_rows:
            hr = feishu_text_to_str(resume.fields.get("hm_reason") or "")
            if str(act) in ("negotiate", "reject", "ghost"):
                hr = (msg or "")[:2000] or hr
            self.ctx.bitable.update_record(
                r_tid,
                r_rows[0].record_id,
                {
                    "pipeline_stage": PipelineStage.CLOSED.value,
                    "hm_reason": hr,
                    "updated_at": utc_now_iso(),
                },
            )
        return {
            "status": next_offer,
            "candidate_message": msg[:2000],
            "owner_agent": "",
            "updated_at": utc_now_iso(),
        }
