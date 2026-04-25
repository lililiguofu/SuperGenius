"""招聘分析师：周期性汇总漏斗并写 reports，并轻量回写 jobs（jd_suggestion）。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from loguru import logger

from supergenius.agents.base import AgentBase, utc_now_iso
from supergenius.feishu.field_value import feishu_text_to_str
from supergenius.llm.client import render_prompt
from supergenius.schema.tables import (
    AGENT_ANALYST,
    JobStatus,
    PipelineStage,
    ReportKind,
    ResumeStatus,
)

_ANALYST_SCHEMA = {
    "name": "analyst_out",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "jd_suggestion": {"type": "string"},
        },
        "required": ["summary", "jd_suggestion"],
        "additionalProperties": False,
    },
}


class AnalystAgent(AgentBase):
    name = AGENT_ANALYST
    watch_table = "jobs"
    in_progress_status = None
    rollback_status_on_error = None

    def claim_filter(self) -> list[dict[str, Any]]:
        return [
            {
                "field_name": "status",
                "operator": "is",
                "value": [JobStatus.OPEN.value],
            }
        ]

    def handle(self, rec: object) -> dict[str, Any] | None:  # type: ignore[override]
        return None

    def tick(self) -> int:  # type: ignore[override]
        period = datetime.utcnow().strftime("%Y-%m-%d")
        report_tid = self.ctx.table_ids["reports"]
        existing = self.ctx.bitable.search_records(
            report_tid,
            filter_conditions=[
                {"field_name": "period", "operator": "is", "value": [period]},
                {"field_name": "kind", "operator": "is", "value": [ReportKind.WEEKLY.value]},
            ],
            page_size=1,
        )
        if existing:
            return 0
        jtid = self.ctx.table_ids["jobs"]
        rtid = self.ctx.table_ids["resumes"]
        jobs = self.ctx.bitable.search_records(jtid, filter_conditions=None, page_size=200)
        resumes = self.ctx.bitable.search_records(rtid, filter_conditions=None, page_size=500)
        funnel = {
            "jobs": len(jobs),
            "resumes": len(resumes),
            "by_screened": sum(
                1
                for r in resumes
                if feishu_text_to_str(r.fields.get("status")) == ResumeStatus.SCREENED.value
            ),
            "by_interview_queued": sum(
                1
                for r in resumes
                if feishu_text_to_str(r.fields.get("pipeline_stage"))
                == PipelineStage.INTERVIEW_QUEUED.value
            ),
        }
        user = render_prompt("analyst", funnel_json=json.dumps(funnel, ensure_ascii=False, indent=2))
        try:
            out = self.ctx.llm.chat(
                system="You are a recruiting data analyst. Output JSON only; suggestions in Chinese.",
                user=user,
                json_schema=_ANALYST_SCHEMA,
            )
        except Exception as e:
            logger.exception(f"[{self.name}] {e}")
            return 0
        if not isinstance(out, dict):
            return 0
        summary = str(out.get("summary") or "")
        jd_sug = str(out.get("jd_suggestion") or "")
        rid = f"RP-{period}"
        self.ctx.bitable.create_record(
            report_tid,
            {
                "report_id": rid,
                "period": period,
                "kind": ReportKind.WEEKLY.value,
                "content": summary[:4000],
                "target_job_id": "",
                "ts": utc_now_iso(),
            },
        )
        for j in jobs[:5]:
            if feishu_text_to_str(j.fields.get("status")) == JobStatus.OPEN.value:
                self.ctx.bitable.update_record(
                    jtid,
                    j.record_id,
                    {
                        "jd_suggestion": jd_sug[:2000],
                        "updated_at": utc_now_iso(),
                    },
                )
                break
        logger.info(f"[{self.name}] 已生成 {period} 周报 {rid}")
        return 1
