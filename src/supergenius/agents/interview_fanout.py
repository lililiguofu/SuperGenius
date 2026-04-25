"""初筛通过后，为同一简历创建三条 interview 行（技术/业务/文化）。"""

from __future__ import annotations

import uuid
from typing import Any

from loguru import logger

from supergenius.agents.base import AgentBase, ClaimedRecord, utc_now_iso
from supergenius.feishu.field_value import feishu_text_to_str
from supergenius.schema.tables import (
    AGENT_INTERVIEW_FANOUT,
    InterviewRole,
    InterviewRowStatus,
    PipelineStage,
    ResumeDecision,
    ResumeStatus,
)


class InterviewFanoutAgent(AgentBase):
    name = AGENT_INTERVIEW_FANOUT
    watch_table = "resumes"
    in_progress_status = None
    rollback_status_on_error = None

    def claim_filter(self) -> list[dict[str, Any]]:
        return [
            {
                "field_name": "pipeline_stage",
                "operator": "is",
                "value": [PipelineStage.INTERVIEW_QUEUED.value],
            },
            {
                "field_name": "status",
                "operator": "is",
                "value": [ResumeStatus.SCREENED.value],
            },
        ]

    def handle(self, rec: ClaimedRecord) -> dict[str, Any] | None:
        if feishu_text_to_str(rec.fields.get("status")) != ResumeStatus.SCREENED.value:
            return None
        if feishu_text_to_str(rec.fields.get("decision")) != ResumeDecision.PASS_.value:
            return None
        if feishu_text_to_str(rec.fields.get("interview_bundle_id")):
            return None

        resume_id = feishu_text_to_str(rec.fields.get("resume_id")) or rec.record_id
        job_id = feishu_text_to_str(rec.fields.get("job_id"))
        if not job_id:
            logger.warning(f"[{self.name}] 缺少 job_id，跳过")
            return None

        bundle = uuid.uuid4().hex[:16]
        itid = self.ctx.table_ids["interviews"]
        for role in (InterviewRole.TECH, InterviewRole.BUSINESS, InterviewRole.CULTURE):
            self.ctx.bitable.create_record(
                itid,
                {
                    "interview_id": f"IV-{uuid.uuid4().hex[:10]}",
                    "resume_id": resume_id,
                    "job_id": job_id,
                    "role": role.value,
                    "status": InterviewRowStatus.PENDING.value,
                    "total_score": 0,
                    "dimension_json": "{}",
                    "notes": "",
                    "quote_snippet": "",
                    "owner_agent": "",
                    "updated_at": utc_now_iso(),
                },
            )
        return {
            "interview_bundle_id": bundle,
            "pipeline_stage": PipelineStage.INTERVIEWS_IN_PROGRESS.value,
            "debate_round": "0",
            "owner_agent": "",
        }
