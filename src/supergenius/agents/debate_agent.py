"""面试分差过大时，最多 N 轮辩论；结束后进入经理仲裁。"""

from __future__ import annotations

import json
import uuid
from typing import Any

from loguru import logger

from supergenius.agents.base import AgentBase, utc_now_iso
from supergenius.feishu.field_value import feishu_text_to_str
from supergenius.llm.client import render_prompt
from supergenius.schema.tables import (
    AGENT_BUSINESS,
    AGENT_CULTURE,
    AGENT_DEBATE,
    AGENT_TECH,
    DebateStatus,
    PipelineStage,
    ResumeStatus,
)

_DEBATE_STATEMENT = {
    "name": "debate_stmt",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "statement": {"type": "string"},
        },
        "required": ["statement"],
        "additionalProperties": False,
    },
}

_CONV = {
    "name": "debate_converge",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {"converged": {"type": "boolean"}},
        "required": ["converged"],
        "additionalProperties": False,
    },
}


def _load_interview_scores(
    bitable: Any, table_ids: dict[str, str], resume_id: str
) -> str:
    itid = table_ids["interviews"]
    rows = bitable.search_records(
        itid,
        filter_conditions=[{"field_name": "resume_id", "operator": "is", "value": [resume_id]}],
        page_size=20,
    )
    parts: list[dict[str, Any]] = []
    for r in rows:
        parts.append(
            {
                "role": feishu_text_to_str(r.fields.get("role")),
                "total_score": r.fields.get("total_score"),
                "notes": (feishu_text_to_str(r.fields.get("notes")) or "")[:500],
            }
        )
    return json.dumps(parts, ensure_ascii=False, indent=2) if parts else "[]"


class DebateAgent(AgentBase):
    name = AGENT_DEBATE
    watch_table = "resumes"
    in_progress_status = None
    rollback_status_on_error = None

    def claim_filter(self) -> list[dict[str, Any]]:  # unused by tick
        return [
            {
                "field_name": "pipeline_stage",
                "operator": "is",
                "value": [PipelineStage.DEBATE.value],
            }
        ]

    def handle(self, rec: object) -> dict[str, Any] | None:  # type: ignore[override]
        return None

    def tick(self) -> int:  # type: ignore[override]
        tid = self.ctx.table_ids["resumes"]
        max_r = self.ctx.config.scheduler.debate_max_rounds
        rows = self.ctx.bitable.search_records(
            tid,
            filter_conditions=self.claim_filter()
            + [{"field_name": "status", "operator": "is", "value": [ResumeStatus.SCREENED.value]}],
            page_size=20,
        )
        if not rows:
            return 0
        deb_tid = self.ctx.table_ids["debates"]
        processed = 0
        speakers = [AGENT_TECH, AGENT_BUSINESS, AGENT_CULTURE]
        for r in rows:
            dr_s = feishu_text_to_str(r.fields.get("debate_round") or "0")
            try:
                dr = int(float(dr_s))
            except ValueError:
                dr = 0
            if dr >= max_r:
                self.ctx.bitable.update_record(
                    tid,
                    r.record_id,
                    {
                        "pipeline_stage": PipelineStage.HM_ARBITRATION.value,
                        "updated_at": utc_now_iso(),
                    },
                )
                logger.info(
                    f"[{self.name}] 简历 {r.record_id} 已达 {max_r} 轮，进入经理仲裁"
                )
                processed += 1
                continue

            resume_id = feishu_text_to_str(r.fields.get("resume_id")) or r.record_id
            summary = _load_interview_scores(self.ctx.bitable, self.ctx.table_ids, resume_id)
            rnd = dr + 1
            st_lines: list[str] = []
            for spk in speakers:
                user = render_prompt(
                    "debate_round",
                    round_number=str(rnd),
                    interview_summary=summary,
                    speaker=spk,
                )
                out: dict[str, Any] | None = None
                try:
                    out = self.ctx.llm.chat(
                        system="You are a debate participant. Be concise, professional, in Chinese.",
                        user=user,
                        json_schema=_DEBATE_STATEMENT,
                        temperature=0.3,
                    )
                except Exception as e:
                    logger.exception(f"[{self.name}] 辩论 LLM 失败: {e}")
                st = (out or {}).get("statement", "") if isinstance(out, dict) else ""
                st_lines.append(f"{spk}：{st or ''}"[:2000])
                self.ctx.bitable.create_record(
                    deb_tid,
                    {
                        "debate_id": f"DB-{uuid.uuid4().hex[:10]}",
                        "resume_id": resume_id,
                        "round": rnd,
                        "speaker_agent": spk,
                        "statement": (st or "")[:4000],
                        "status": DebateStatus.CLOSED.value,
                        "ts": utc_now_iso(),
                    },
                )
            stm_text = "\n\n".join(st_lines)
            converged = False
            try:
                conv = self.ctx.llm.chat(
                    system="You judge whether the debate can end early. JSON only.",
                    user=render_prompt(
                        "debate_convergence",
                        round_number=str(rnd),
                        interview_json=summary,
                        statements_text=stm_text,
                    ),
                    json_schema=_CONV,
                )
                if isinstance(conv, dict) and conv.get("converged") is True:
                    converged = True
            except Exception as e:
                logger.debug(f"[{self.name}] 收敛检查跳过: {e}")
            if converged:
                self.ctx.bitable.update_record(
                    tid,
                    r.record_id,
                    {
                        "pipeline_stage": PipelineStage.HM_ARBITRATION.value,
                        "debate_round": str(dr + 1),
                        "updated_at": utc_now_iso(),
                    },
                )
                logger.info(f"[{self.name}] 简历 {resume_id} 辩论第 {rnd} 轮后判定收敛，交经理")
                processed += 1
                continue
            new_dr = dr + 1
            patch: dict[str, Any] = {
                "debate_round": str(new_dr),
                "updated_at": utc_now_iso(),
            }
            if new_dr >= max_r:
                patch["pipeline_stage"] = PipelineStage.HM_ARBITRATION.value
            self.ctx.bitable.update_record(tid, r.record_id, patch)
            logger.info(f"[{self.name}] 简历 {resume_id} 完成辩论第 {rnd} 轮")
            processed += 1
        return processed
