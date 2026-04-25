"""简历筛选官 Agent：对新投递的简历打分 + 决策 + 一致性自检。"""

from __future__ import annotations

import json
import statistics
from typing import Any

from loguru import logger

from supergenius.agents.base import AgentBase, ClaimedRecord
from supergenius.feishu.bitable import Record
from supergenius.feishu.field_value import feishu_text_to_str
from supergenius.llm.client import render_prompt
from supergenius.schema.tables import (  # noqa: F401
    PipelineStage,
    ResumeDecision,
    ResumeStatus,
)

_SCREEN_SCHEMA = {
    "name": "resume_screen",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "score": {"type": "integer"},
            "dimensions": {
                "type": "object",
                "properties": {
                    "hard_requirements": {"type": "integer"},
                    "relevance": {"type": "integer"},
                    "growth": {"type": "integer"},
                    "stability": {"type": "integer"},
                    "red_flags": {"type": "integer"},
                },
                "required": [
                    "hard_requirements",
                    "relevance",
                    "growth",
                    "stability",
                    "red_flags",
                ],
                "additionalProperties": False,
            },
            "decision": {"type": "string", "enum": ["pass", "hold", "reject"]},
            "reason": {"type": "string"},
            "parsed_skills": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["score", "dimensions", "decision", "reason", "parsed_skills"],
        "additionalProperties": False,
    },
}


class ScreenerAgent(AgentBase):
    name = "screener"
    watch_table = "resumes"
    in_progress_status = ResumeStatus.SCREENING.value
    rollback_status_on_error = ResumeStatus.NEW.value

    def claim_filter(self) -> list[dict[str, Any]]:
        return [
            {
                "field_name": "status",
                "operator": "is",
                "value": [ResumeStatus.NEW.value],
            }
        ]

    def handle(self, rec: ClaimedRecord) -> dict[str, Any] | None:
        resume_text = feishu_text_to_str(rec.fields.get("raw_text"))
        job_id = feishu_text_to_str(rec.fields.get("job_id"))
        if not resume_text.strip():
            logger.warning(f"[{self.name}] {rec.record_id} raw_text 为空，reject")
            return self._patch_reject("简历原文为空")

        job_row, jd_text = self._resolve_job_jd(job_id)
        if not jd_text:
            if job_row is None:
                logger.warning(
                    f"[{self.name}] jobs 表无 job_id={job_id} 的行，保持 new 下轮再试"
                )
            else:
                logger.info(
                    f"[{self.name}] 岗位 {job_id} 已找到，jd_text 仍为空，等 JD 策划官/经理写完再筛；保持 new"
                )
            return self._patch_requeue_new("关联岗位未找到或 JD 尚未生成，待重试")

        # 一致性自检：采样两次（各一次 API，中间可能数十秒无其它日志，易误以为卡住）
        logger.info(
            f"[{self.name}] {rec.record_id} 开始双次打分（1/2、2/2 各需一次 LLM，请稍候）"
        )
        scores: list[dict[str, Any]] = []
        for i in range(2):
            logger.info(f"[{self.name}] {rec.record_id} 第 {i + 1}/2 次打分…")
            r = self._score_once(jd_text, resume_text, nth=i + 1)
            if r is None:
                return None
            scores.append(r)

        s0, s1 = scores[0]["score"], scores[1]["score"]
        variance = statistics.pvariance([s0, s1])
        threshold = self.ctx.config.scheduler.screener_var_threshold

        best = scores[0]  # 用第一次采样作为决策结果
        if variance > threshold:
            logger.warning(
                f"[{self.name}] {rec.record_id} 两次打分方差 {variance:.1f} > "
                f"{threshold}，转 hold 待复核"
            )
            return self._patch(
                ResumeDecision.HOLD,
                int((s0 + s1) / 2),
                best.get("parsed_skills") or [],
                f"一致性自检失败(方差={variance:.1f})；两次得分 {s0} / {s1}",
            )

        # decision 直接按字符串透传；ResumeDecision 枚举在 _patch 路径使用
        return self._patch_raw(best["decision"], best)

    # ---------- helpers ----------

    def _find_job_by_job_id(self, job_id: str) -> Record | None:
        """先按飞书条件查询；对不上时全表拉一页按纯文本比 job_id。"""
        if not job_id:
            return None
        jobs_tid = self.ctx.table_ids["jobs"]
        rows = self.ctx.bitable.search_records(
            jobs_tid,
            filter_conditions=[
                {"field_name": "job_id", "operator": "is", "value": [job_id]},
            ],
            page_size=50,
        )
        for r in rows:
            if feishu_text_to_str(r.fields.get("job_id")) == job_id:
                return r
        rows = self.ctx.bitable.search_records(
            jobs_tid,
            filter_conditions=None,
            page_size=200,
        )
        for r in rows:
            if feishu_text_to_str(r.fields.get("job_id")) == job_id:
                return r
        return None

    def _resolve_job_jd(self, job_id: str) -> tuple[Record | None, str]:
        """返回 (岗位行或 None, 归一化后的非空 jd_text)。"""
        if not job_id:
            return None, ""
        rec = self._find_job_by_job_id(job_id)
        if not rec:
            return None, ""
        jd = feishu_text_to_str(rec.fields.get("jd_text")).strip()
        return rec, jd

    def _score_once(self, jd_text: str, resume_text: str, nth: int) -> dict[str, Any] | None:
        prompt = render_prompt("screener", jd_text=jd_text, resume_text=resume_text)
        try:
            r = self.ctx.llm.chat(
                system=f"You are the Resume Screener agent. Sample #{nth}.",
                user=prompt,
                json_schema=_SCREEN_SCHEMA,
                temperature=0.5,
            )
        except Exception as e:
            logger.exception(f"[{self.name}] LLM 打分失败: {e}")
            return None
        if not isinstance(r, dict):
            return None
        return r

    def _patch_requeue_new(self, reason: str) -> dict[str, Any]:
        """岗位 JD 未就绪：把状态改回 new，避免误标 screened 后无人再处理。"""
        return {
            "score": 0,
            "decision": "",
            "reason": reason[:500],
            "parsed_skills": "[]",
            "status": ResumeStatus.NEW.value,
            "owner_agent": "",
            "pipeline_stage": "",
        }

    def _patch(
        self,
        decision: ResumeDecision,
        score: int,
        skills: list[str],
        reason: str,
    ) -> dict[str, Any]:
        if decision is ResumeDecision.PASS_:
            pstage = PipelineStage.INTERVIEW_QUEUED.value
        elif decision is ResumeDecision.HOLD:
            pstage = PipelineStage.HOLD_REVIEW.value
        else:
            pstage = PipelineStage.CLOSED.value
        return {
            "score": score,
            "decision": decision.value,
            "reason": reason[:500],
            "parsed_skills": json.dumps(skills, ensure_ascii=False),
            "status": ResumeStatus.SCREENED.value,
            "owner_agent": "",
            "pipeline_stage": pstage,
        }

    def _patch_raw(self, decision_str: str, result: dict[str, Any]) -> dict[str, Any]:
        if decision_str == "pass":
            pstage = PipelineStage.INTERVIEW_QUEUED.value
        elif decision_str == "hold":
            pstage = PipelineStage.HOLD_REVIEW.value
        else:
            pstage = PipelineStage.CLOSED.value
        return {
            "score": int(result.get("score") or 0),
            "decision": decision_str,
            "reason": str(result.get("reason") or "")[:500],
            "parsed_skills": json.dumps(result.get("parsed_skills") or [], ensure_ascii=False),
            "status": ResumeStatus.SCREENED.value,
            "owner_agent": "",
            "pipeline_stage": pstage,
        }

    def _patch_reject(self, reason: str) -> dict[str, Any]:
        return self._patch(ResumeDecision.REJECT, 0, [], reason)  # sets pipeline closed
