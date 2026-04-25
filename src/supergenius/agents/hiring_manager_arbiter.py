"""经理仲裁：综合三面评分与（可选）辩论记录，决定 hire / reject，进入 Offer 或结束。"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from supergenius.agents.base import AgentBase, ClaimedRecord
from supergenius.feishu.field_value import feishu_text_to_str
from supergenius.llm.client import render_prompt
from supergenius.schema.tables import (
    AGENT_HM_ARB,
    PipelineStage,
    ResumeStatus,
)

_ARB_SCHEMA = {
    "name": "hm_arb",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "decision": {"type": "string", "enum": ["hire", "reject"]},
            "reason": {"type": "string"},
        },
        "required": ["decision", "reason"],
        "additionalProperties": False,
    },
}


def _gather_debates(bitable: Any, table_ids: dict[str, str], resume_id: str) -> str:
    tid = table_ids["debates"]
    rows = bitable.search_records(
        tid,
        filter_conditions=[{"field_name": "resume_id", "operator": "is", "value": [resume_id]}],
        page_size=100,
    )
    lines: list[str] = []
    for r in rows:
        lines.append(
            f"R{r.fields.get('round')} [{feishu_text_to_str(r.fields.get('speaker_agent'))}]: "
            f"{feishu_text_to_str(r.fields.get('statement'))[:500]}"
        )
    return "\n".join(lines) if lines else "(无辩论记录)"


def _gather_interviews(bitable: Any, table_ids: dict[str, str], resume_id: str) -> str:
    tid = table_ids["interviews"]
    rows = bitable.search_records(
        tid,
        filter_conditions=[{"field_name": "resume_id", "operator": "is", "value": [resume_id]}],
        page_size=20,
    )
    return json.dumps(
        [
            {
                "role": feishu_text_to_str(r.fields.get("role")),
                "score": r.fields.get("total_score"),
                "notes": (feishu_text_to_str(r.fields.get("notes")) or "")[:300],
            }
            for r in rows
        ],
        ensure_ascii=False,
        indent=2,
    )


class HiringManagerArbiterAgent(AgentBase):
    name = AGENT_HM_ARB
    watch_table = "resumes"
    in_progress_status = None
    rollback_status_on_error = None

    def claim_filter(self) -> list[dict[str, Any]]:
        return [
            {
                "field_name": "pipeline_stage",
                "operator": "is",
                "value": [PipelineStage.HM_ARBITRATION.value],
            }
        ]

    def handle(self, rec: ClaimedRecord) -> dict[str, Any] | None:
        if feishu_text_to_str(rec.fields.get("status")) != ResumeStatus.SCREENED.value:
            return None
        rid = feishu_text_to_str(rec.fields.get("resume_id")) or rec.record_id
        iv = _gather_interviews(self.ctx.bitable, self.ctx.table_ids, rid)
        db = _gather_debates(self.ctx.bitable, self.ctx.table_ids, rid)
        prompt = render_prompt("hiring_manager_arbiter", interview_json=iv, debate_log=db)
        try:
            out = self.ctx.llm.chat(
                system="You are the Hiring Manager resolving interviewer disagreement or confirming hire.",
                user=prompt,
                json_schema=_ARB_SCHEMA,
            )
        except Exception as e:
            logger.exception(f"[{self.name}] LLM 失败: {e}")
            return None
        if not isinstance(out, dict):
            return None
        dec = out.get("decision")
        reason = str(out.get("reason") or "")
        if dec == "hire":
            p = PipelineStage.OFFER_DRAFTING.value
        else:
            p = PipelineStage.CLOSED.value
        return {
            "hm_decision": dec,
            "hm_reason": (reason or "")[:2000],
            "pipeline_stage": p,
            "owner_agent": "",
        }
