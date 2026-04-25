"""招聘经理 Agent（仅阶段一部分：审批 JD）。

后续阶段会在本类上扩展：接 screener 升级、面试官辩论仲裁、Offer 审批。
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from supergenius.agents.base import AgentBase, ClaimedRecord
from supergenius.llm.client import render_prompt
from supergenius.schema.tables import JobStatus

_DECISION_SCHEMA = {
    "name": "jd_approval",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "decision": {
                "type": "string",
                "enum": ["approve", "approve_with_notes", "request_revision"],
            },
            "notes": {"type": "string"},
        },
        "required": ["decision", "notes"],
        "additionalProperties": False,
    },
}


class HiringManagerAgent(AgentBase):
    name = "hiring_manager"
    watch_table = "jobs"
    in_progress_status = None  # 保持 jd_pending_approval 不变，只改 owner

    def claim_filter(self) -> list[dict[str, Any]]:
        return [
            {
                "field_name": "status",
                "operator": "is",
                "value": [JobStatus.JD_PENDING_APPROVAL.value],
            }
        ]

    def handle(self, rec: ClaimedRecord) -> dict[str, Any] | None:
        brief_fields = {
            k: rec.fields.get(k)
            for k in ("title", "level", "headcount", "budget_min", "budget_max", "urgency", "jd_brief")
        }
        jd_text = str(rec.fields.get("jd_text") or "")
        if not jd_text.strip():
            logger.warning(f"[{self.name}] {rec.record_id} 没有 jd_text，打回 draft")
            return {"status": JobStatus.DRAFT.value, "owner_agent": ""}

        prompt = render_prompt(
            "hiring_manager",
            job_brief=json.dumps(brief_fields, ensure_ascii=False, indent=2),
            jd_text=jd_text,
        )

        try:
            result = self.ctx.llm.chat(
                system="You are the Hiring Manager agent.",
                user=prompt,
                json_schema=_DECISION_SCHEMA,
            )
        except Exception as e:
            logger.exception(f"[{self.name}] LLM 调用失败: {e}")
            return None

        if not isinstance(result, dict):
            logger.warning(f"[{self.name}] 非预期返回: {result!r}")
            return None

        decision = result.get("decision")
        notes = result.get("notes") or ""
        logger.info(f"[{self.name}] {rec.record_id} 审批结果: {decision} notes={notes[:60]}")

        if decision in ("approve", "approve_with_notes"):
            return {"status": JobStatus.OPEN.value, "owner_agent": ""}
        # request_revision：打回给 JD 策划官重写
        return {"status": JobStatus.DRAFT.value, "owner_agent": ""}
