"""JD 策划 + 经理批 JD：对外仅暴露 `JDStrategistAgent`；内部用 `_JdStrategistCore` / `_HiringManagerJdApproval` 两段实现。"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from supergenius.agents.base import AgentBase, ClaimedRecord
from supergenius.feishu.field_value import feishu_text_to_str
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


def _impossible_job_filter() -> list[dict[str, Any]]:
    return [{"field_name": "job_id", "operator": "is", "value": ["__jd_pipeline_noop__"]}]


class _JdStrategistCore(AgentBase):
    """扫 jobs 表 status=draft，生成 JD → jd_pending_approval。"""

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
        jd_sug = feishu_text_to_str(rec.fields.get("jd_suggestion")) or "（暂无）"

        prompt = render_prompt(
            "jd_strategist",
            job_brief=jd_brief_json,
            jd_suggestion=jd_sug,
        )
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


class _HiringManagerJdApproval(AgentBase):
    """经理对 JD 的审批（与 README「招聘经理」批 JD 一致；独立类仅供单测/内部）。"""

    name = "hiring_manager"
    watch_table = "jobs"
    in_progress_status = None
    rollback_status_on_error = None

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
        return {"status": JobStatus.DRAFT.value, "owner_agent": ""}


class JDStrategistAgent(AgentBase):
    """README：JD 策划官 + 经理批 JD。单 tick 内先出稿、再待审批/审批。"""

    name = "jd_strategist"
    watch_table = "jobs"
    in_progress_status = None
    rollback_status_on_error = None

    def claim_filter(self) -> list[dict[str, Any]]:
        return _impossible_job_filter()

    def handle(self, rec: ClaimedRecord) -> dict[str, Any] | None:
        raise RuntimeError("JDStrategistAgent 仅通过 tick 编排子阶段")

    def tick(self) -> int:
        n = _JdStrategistCore(self.ctx).tick()
        n += _HiringManagerJdApproval(self.ctx).tick()
        return n


__all__ = ["JDStrategistAgent"]
