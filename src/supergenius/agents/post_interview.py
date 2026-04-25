"""三条 interview 行均 done 后：根据分差进入 debate 或 hm_arbitration。"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from supergenius.agents.base import AgentBase, utc_now_iso
from supergenius.feishu.field_value import feishu_text_to_str
from supergenius.schema.tables import (
    AGENT_POST_INTERVIEW,
    EventAction,
    InterviewRole,
    InterviewRowStatus,
    PipelineStage,
    ResumeStatus,
)


def _list_interviews_for_resume(bitable: Any, table_ids: dict[str, str], resume_id: str) -> list[Any]:
    itid = table_ids["interviews"]
    return bitable.search_records(
        itid,
        filter_conditions=[{"field_name": "resume_id", "operator": "is", "value": [resume_id]}],
        page_size=50,
    )


class PostInterviewAgent(AgentBase):
    name = AGENT_POST_INTERVIEW
    watch_table = "resumes"
    in_progress_status = None
    rollback_status_on_error = None

    def claim_filter(self) -> list[dict[str, Any]]:
        return [
            {
                "field_name": "pipeline_stage",
                "operator": "is",
                "value": [PipelineStage.INTERVIEWS_IN_PROGRESS.value],
            }
        ]

    def handle(self, rec: object) -> dict[str, Any] | None:  # type: ignore[override]
        return None  # 基类不调用，逻辑在 tick

    def tick(self) -> int:  # type: ignore[override]
        tid = self.ctx.table_ids["resumes"]
        rows = self.ctx.bitable.search_records(
            tid,
            filter_conditions=self.claim_filter()
            + [{"field_name": "status", "operator": "is", "value": [ResumeStatus.SCREENED.value]}],
            page_size=30,
        )
        if not rows:
            return 0
        th = self.ctx.config.scheduler.interview_spread_threshold
        processed = 0
        for r in rows:
            resume_id = feishu_text_to_str(r.fields.get("resume_id")) or r.record_id
            ivs = _list_interviews_for_resume(self.ctx.bitable, self.ctx.table_ids, resume_id)
            by_role: dict[str, float] = {}
            for x in ivs:
                role = feishu_text_to_str(x.fields.get("role"))
                st = feishu_text_to_str(x.fields.get("status"))
                if st != InterviewRowStatus.DONE.value:
                    continue
                if role in (
                    InterviewRole.TECH.value,
                    InterviewRole.BUSINESS.value,
                    InterviewRole.CULTURE.value,
                ):
                    by_role[role] = float(x.fields.get("total_score") or 0)
            if set(by_role.keys()) != {
                InterviewRole.TECH.value,
                InterviewRole.BUSINESS.value,
                InterviewRole.CULTURE.value,
            }:
                continue
            scores = [
                by_role[InterviewRole.TECH.value],
                by_role[InterviewRole.BUSINESS.value],
                by_role[InterviewRole.CULTURE.value],
            ]
            spread = max(scores) - min(scores)
            next_stage = PipelineStage.DEBATE.value if spread >= th else PipelineStage.HM_ARBITRATION.value
            dr = "0" if next_stage == PipelineStage.DEBATE.value else (r.fields.get("debate_round") or "0")
            if isinstance(dr, (int, float)):
                dr = str(int(dr))
            self.ctx.bitable.update_record(
                tid,
                r.record_id,
                {
                    "pipeline_stage": next_stage,
                    "debate_round": dr,
                    "analyst_note": json.dumps(
                        {
                            "spread": spread,
                            "scores": {
                                "tech": by_role[InterviewRole.TECH.value],
                                "business": by_role[InterviewRole.BUSINESS.value],
                                "culture": by_role[InterviewRole.CULTURE.value],
                            },
                        },
                        ensure_ascii=False,
                    )[:2000],
                    "updated_at": utc_now_iso(),
                },
            )
            self.log_event(
                EventAction.UPDATE,
                "resumes",
                r.record_id,
                {"next_stage": next_stage, "spread": spread},
            )
            logger.info(
                f"[{self.name}] 简历 {resume_id} 分差 {spread:.2f} -> {next_stage}"
            )
            processed += 1
        return processed
