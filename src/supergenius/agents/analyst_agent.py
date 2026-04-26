"""招聘分析师：多维度汇总、报告、轻量回写 jobs（jd_suggestion），可选推送到群机器人。"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

import httpx
from loguru import logger

from supergenius.agents.base import AgentBase, utc_now_iso
from supergenius.feishu.field_value import feishu_text_to_str
from supergenius.llm.client import render_prompt
from supergenius.schema.tables import (
    AGENT_ANALYST,
    JobStatus,
    OfferStatus,
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
            "funnel_narrative": {"type": "string"},
            "time_efficiency": {"type": "string"},
            "quality_signals": {"type": "string"},
            "interviewer_calibration": {"type": "string"},
            "jd_health": {"type": "string"},
            "reactivation_brief": {"type": "string"},
            "alerts": {"type": "string"},
        },
        "required": [
            "summary",
            "jd_suggestion",
            "funnel_narrative",
            "time_efficiency",
            "quality_signals",
            "interviewer_calibration",
            "jd_health",
            "reactivation_brief",
            "alerts",
        ],
        "additionalProperties": False,
    },
}


def _count_by(
    records: list[Any], field: str, default: str = ""
) -> dict[str, int]:
    c: Counter[str] = Counter()
    for r in records:
        v = feishu_text_to_str(r.fields.get(field)) or default
        c[v] += 1
    return dict(c)


def _interview_role_avgs(bitable: Any, table_ids: dict[str, str]) -> dict[str, Any]:
    itid = table_ids["interviews"]
    rows = bitable.search_records(itid, filter_conditions=None, page_size=2000)
    by_role: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        role = feishu_text_to_str(r.fields.get("role")) or "?"
        try:
            s = float(r.fields.get("total_score") or 0)
        except (TypeError, ValueError):
            s = 0.0
        by_role[role].append(s)
    out: dict[str, Any] = {}
    for role, vals in by_role.items():
        if not vals:
            continue
        out[role] = {
            "n": len(vals),
            "avg": round(sum(vals) / len(vals), 2),
        }
    return out


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
        otid = self.ctx.table_ids.get("offers", "")
        jobs = self.ctx.bitable.search_records(jtid, filter_conditions=None, page_size=200)
        resumes = self.ctx.bitable.search_records(rtid, filter_conditions=None, page_size=2000)
        offers: list[Any] = []
        if otid:
            offers = self.ctx.bitable.search_records(otid, filter_conditions=None, page_size=500)

        stage_c = _count_by(resumes, "pipeline_stage")
        status_c = _count_by(resumes, "status")
        off_c = _count_by(offers, "status") if offers else {}
        inv = _interview_role_avgs(self.ctx.bitable, self.ctx.table_ids)
        offers_accept = sum(
            1
            for o in offers
            if feishu_text_to_str(o.fields.get("status")) == OfferStatus.ACCEPTED.value
        )
        offers_sent = sum(
            1
            for o in offers
            if feishu_text_to_str(o.fields.get("status")) == OfferStatus.SENT.value
        )

        raw_metrics: dict[str, Any] = {
            "job_count": len(jobs),
            "resume_count": len(resumes),
            "resumes_by_status": status_c,
            "pipeline_stage_counts": stage_c,
            "screened_count": sum(
                1
                for r in resumes
                if feishu_text_to_str(r.fields.get("status")) == ResumeStatus.SCREENED.value
            ),
            "interview_queued": stage_c.get(PipelineStage.INTERVIEW_QUEUED.value, 0),
            "stage_debate": stage_c.get(PipelineStage.DEBATE.value, 0),
            "talent_pool": stage_c.get(PipelineStage.TALENT_POOL.value, 0),
            "offer_status_counts": off_c,
            "offers_accepted_total": offers_accept,
            "offers_sent_outstanding": offers_sent,
            "interviewer_role_stats": inv,
        }
        user = render_prompt(
            "analyst",
            metrics_json=json.dumps(raw_metrics, ensure_ascii=False, indent=2),
        )
        try:
            out = self.ctx.llm.chat(
                system=(
                    "You are a recruiting data analyst. Output JSON only; all narrative text in "
                    "Chinese. Base insights on the metrics; do not invent numbers."
                ),
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
        full_content = json.dumps(
            {k: out.get(k) for k in _ANALYST_SCHEMA["schema"]["properties"]},
            ensure_ascii=False,
        )[:10000]
        self.ctx.bitable.create_record(
            report_tid,
            {
                "report_id": rid,
                "period": period,
                "kind": ReportKind.WEEKLY.value,
                "content": full_content[:4000],
                "target_job_id": "",
                "ts": utc_now_iso(),
            },
        )
        self.ctx.bitable.create_record(
            report_tid,
            {
                "report_id": f"RP-{period}-F",
                "period": period,
                "kind": ReportKind.FUNNEL.value,
                "content": json.dumps(raw_metrics, ensure_ascii=False)[:4000],
                "target_job_id": "",
                "ts": utc_now_iso(),
            },
        )
        self.ctx.bitable.create_record(
            report_tid,
            {
                "report_id": f"RP-{period}-JD",
                "period": period,
                "kind": ReportKind.JD_HEALTH.value,
                "content": (str(out.get("jd_health") or ""))[:4000],
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
        hook = getattr(self.ctx.config, "report_webhook_url", None) or ""
        if hook:
            try:
                text = f"[SuperGenius 周报 {period}]\n{summary[:2000]}"
                payload: dict[str, Any] = {
                    "msg_type": "text",
                    "content": {"text": text},
                }
                with httpx.Client(timeout=10.0) as client:
                    r = client.post(hook, json=payload)
                    r.raise_for_status()
                logger.info(f"[{self.name}] 已推送到群机器人")
            except Exception as e:
                logger.warning(f"[{self.name}] 推送失败(忽略): {e}")
        logger.info(f"[{self.name}] 已生成 {period} 周报 {rid}")
        return 1
