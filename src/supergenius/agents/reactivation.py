"""人才池反向激活：「开放岗位」与历史淘汰简历（talent_pool）的匹配，生成新投递行。"""

from __future__ import annotations

import json
import uuid
from typing import Any

from loguru import logger

from supergenius.agents.base import AgentBase, utc_now_iso
from supergenius.feishu.field_value import feishu_text_to_str
from supergenius.llm.client import render_prompt
from supergenius.schema.tables import (
    AGENT_POOL_REACT,
    JobStatus,
    PipelineStage,
    ResumeDecision,
    ResumeStatus,
)

_REACTIVE_SCHEMA = {
    "name": "react_check",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "should_reactivate": {"type": "boolean"},
            "rationale": {"type": "string"},
        },
        "required": ["should_reactivate", "rationale"],
        "additionalProperties": False,
    },
}


class PoolReactivatorAgent(AgentBase):
    name = AGENT_POOL_REACT
    watch_table = "resumes"
    in_progress_status = None
    rollback_status_on_error = None

    def claim_filter(self) -> list[dict[str, Any]]:
        return [
            {
                "field_name": "pipeline_stage",
                "operator": "is",
                "value": [PipelineStage.TALENT_POOL.value],
            }
        ]

    def handle(self, rec: object) -> dict[str, Any] | None:  # type: ignore[override]
        return None

    def tick(self) -> int:  # type: ignore[override]
        max_t = int(getattr(self.ctx.config.scheduler, "reactivation_max_per_tick", 2))
        r_tid = self.ctx.table_ids["resumes"]
        j_tid = self.ctx.table_ids["jobs"]
        pool = self.ctx.bitable.search_records(
            r_tid,
            filter_conditions=self.claim_filter()
            + [{"field_name": "status", "operator": "is", "value": [ResumeStatus.SCREENED.value]}]
            + [{"field_name": "decision", "operator": "is", "value": [ResumeDecision.REJECT.value]}],
            page_size=15,
        )
        jobs = self.ctx.bitable.search_records(
            j_tid,
            filter_conditions=[
                {"field_name": "status", "operator": "is", "value": [JobStatus.OPEN.value]},
            ],
            page_size=20,
        )
        if not pool or not jobs:
            return 0
        done = 0
        for pr in pool:
            if done >= max_t:
                break
            rid = feishu_text_to_str(pr.fields.get("resume_id"))
            p_job = feishu_text_to_str(pr.fields.get("job_id"))
            raw = feishu_text_to_str(pr.fields.get("raw_text"))[:3000]
            persona = feishu_text_to_str(pr.fields.get("personality")) or "steady"
            for job in jobs:
                if done >= max_t:
                    break
                jid = feishu_text_to_str(job.fields.get("job_id"))
                if not jid or jid == p_job:
                    continue
                jd = feishu_text_to_str(job.fields.get("jd_text"))[:3000]
                if not jd.strip():
                    continue
                try:
                    out = self.ctx.llm.chat(
                        system="You decide if a past rejected candidate may fit a new open role. JSON only.",
                        user=render_prompt(
                            "reactivation_match",
                            jd_excerpt=jd,
                            old_job_id=p_job,
                            new_job_id=jid,
                            resume_excerpt=raw,
                        ),
                        json_schema=_REACTIVE_SCHEMA,
                    )
                except Exception as e:
                    logger.exception(f"[{self.name}] {e}")
                    continue
                if not isinstance(out, dict) or not out.get("should_reactivate"):
                    continue
                new_id = f"R-{uuid.uuid4().hex[:10]}"
                self.ctx.bitable.create_record(
                    r_tid,
                    {
                        "resume_id": new_id,
                        "job_id": jid,
                        "candidate_name": feishu_text_to_str(pr.fields.get("candidate_name")) or "候选人",
                        "raw_text": feishu_text_to_str(pr.fields.get("raw_text")),
                        "parsed_skills": "",
                        "score": 0,
                        "decision": "",
                        "reason": "",
                        "status": ResumeStatus.NEW.value,
                        "owner_agent": "",
                        "updated_at": utc_now_iso(),
                        "pipeline_stage": "",
                        "interview_bundle_id": "",
                        "debate_round": "0",
                        "hm_decision": "",
                        "hm_reason": "",
                        "analyst_note": json.dumps(
                            {"from_reactivation": True, "from_resume": rid, "to_job": jid},
                            ensure_ascii=False,
                        )[:2000],
                        "personality": persona,
                        "gender": feishu_text_to_str(pr.fields.get("gender")) or "",
                    },
                )
                logger.info(
                    f"[{self.name}] 反向激活 {rid} -> 新 {new_id} 投递 {jid}：{out.get('rationale', '')[:80]}"
                )
                done += 1
        return done
