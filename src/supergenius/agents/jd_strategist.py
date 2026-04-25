"""JD 策划官 Agent：扫 jobs 表 status=draft 的记录，生成 JD 后切到 jd_pending_approval。"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from supergenius.agents.base import AgentBase, ClaimedRecord
from supergenius.llm.client import render_prompt
from supergenius.schema.tables import JobStatus


class JDStrategistAgent(AgentBase):
    name = "jd_strategist"
    watch_table = "jobs"
    in_progress_status = JobStatus.JD_DRAFTING.value
    rollback_status_on_error = JobStatus.DRAFT.value

    def claim_filter(self) -> list[dict[str, Any]]:
        return [
            {
                "field_name": "status",
                "operator": "is",
                "value": [JobStatus.DRAFT.value],
            }
        ]

    def handle(self, rec: ClaimedRecord) -> dict[str, Any] | None:
        brief_fields = {
            k: rec.fields.get(k)
            for k in ("title", "level", "headcount", "budget_min", "budget_max", "urgency", "jd_brief")
        }
        jd_brief_json = json.dumps(brief_fields, ensure_ascii=False, indent=2)

        prompt = render_prompt("jd_strategist", job_brief=jd_brief_json)
        try:
            jd_text = self.ctx.llm.chat(
                system="You are the JD Strategist agent.",
                user=prompt,
            )
        except Exception as e:
            logger.exception(f"[{self.name}] LLM 调用失败: {e}")
            return None

        if isinstance(jd_text, dict):
            jd_text = json.dumps(jd_text, ensure_ascii=False)

        logger.info(f"[{self.name}] 已为 {rec.record_id} 生成 JD ({len(jd_text)} chars)")
        return {
            "jd_text": jd_text,
            "status": JobStatus.JD_PENDING_APPROVAL.value,
            "owner_agent": "",
        }
