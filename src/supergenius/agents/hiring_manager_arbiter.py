"""经理仲裁：综合三面评分与（可选）辩论记录；可选性别反事实公平性双评。"""

from __future__ import annotations

import json
import uuid
from typing import Any

from loguru import logger

from supergenius.agents.base import AgentBase, ClaimedRecord, utc_now_iso
from supergenius.feishu.field_value import feishu_text_to_str
from supergenius.llm.client import render_prompt
from supergenius.schema.tables import (
    AGENT_HM_ARB,
    PipelineStage,
    ReportKind,
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

_GENDER_INFER = {
    "name": "gender_infer",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "gender": {
                "type": "string",
                "enum": ["male", "female", "unknown"],
            }
        },
        "required": ["gender"],
        "additionalProperties": False,
    },
}

HM_DECISION_MANUAL = "manual_review"


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


def _normalize_gender_from_field(val: str) -> str:
    s = (val or "").strip().lower()
    if not s:
        return ""
    if s in ("male", "m", "男", "1"):
        return "male"
    if s in ("female", "f", "女", "0"):
        return "female"
    if "女" in val:
        return "female"
    if "男" in val:
        return "male"
    return ""


def _gender_label_zh(g: str) -> str:
    if g == "male":
        return "男性"
    if g == "female":
        return "女性"
    return "未确定/未知"


def _swap_gender(g: str) -> str:
    if g == "male":
        return "female"
    if g == "female":
        return "male"
    return "unknown"


def _merge_analyst_fairness(old: str, extra: dict[str, Any]) -> str:
    try:
        base: dict[str, Any] = json.loads(old) if (old or "").strip().startswith("{") else {}
    except json.JSONDecodeError:
        base = {}
    if not isinstance(base, dict):
        base = {}
    for k, v in extra.items():
        base[k] = v
    return json.dumps(base, ensure_ascii=False)[:2000]


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

    def _infer_gender(
        self, raw_text: str, resume_id: str
    ) -> tuple[str, bool]:  # (male|female|unknown, from_llm)
        excerpt = (raw_text or "")[:4000]
        if not excerpt.strip():
            return "unknown", True
        try:
            out = self.ctx.llm.chat(
                system="Infer only gender label for fairness tooling. JSON only in Chinese context.",
                user=render_prompt("gender_infer", resume_excerpt=excerpt),
                json_schema=_GENDER_INFER,
                temperature=0.0,
            )
        except Exception as e:
            logger.exception(f"[{self.name}] gender infer: {e}")
            return "unknown", True
        g = (out or {}).get("gender", "unknown") if isinstance(out, dict) else "unknown"  # type: ignore[union-attr]
        if g not in ("male", "female", "unknown"):
            g = "unknown"
        return g, True

    def _run_arb(
        self, iv: str, db: str, gender_block: str
    ) -> dict[str, Any] | None:
        prompt = render_prompt(
            "hiring_manager_arbiter",
            interview_json=iv,
            debate_log=db,
            gender_block=gender_block,
        )
        try:
            out = self.ctx.llm.chat(
                system="You are the Hiring Manager resolving interviewer disagreement or confirming hire. Output JSON only.",
                user=prompt,
                json_schema=_ARB_SCHEMA,
            )
        except Exception as e:
            logger.exception(f"[{self.name}] LLM 失败: {e}")
            return None
        if not isinstance(out, dict):
            return None
        return out

    def _write_fairness_report(
        self,
        job_id: str,
        payload: dict[str, Any],
    ) -> None:
        report_tid = self.ctx.table_ids.get("reports")
        if not report_tid or self.ctx.dry_run:
            return
        try:
            self.ctx.bitable.create_record(
                report_tid,
                {
                    "report_id": f"FR-{uuid.uuid4().hex[:10]}",
                    "period": "fairness",
                    "kind": ReportKind.FAIRNESS.value,
                    "content": json.dumps(payload, ensure_ascii=False)[:4000],
                    "target_job_id": (job_id or "")[:200],
                    "ts": utc_now_iso(),
                },
            )
        except Exception as e:
            logger.warning(f"[{self.name}] 写公平性 report 失败(忽略): {e}")

    def _gender_block(self, g: str, *, mode: str) -> str:
        """mode: 'single' 单次; 'base' 原始; 'counter' 反事实。"""
        label = _gender_label_zh(g)
        if mode == "single":
            return "（**说明**：未启用性别反事实，仅作单次经理仲裁。）\n"
        if mode == "base":
            return (
                f"## 公平性/反事实说明（原始）\n"
                f"系统假设本候选人**社会性别**为 **{label}**。"
                f" 请仅据岗位与面试/辩论材料判断；与另一组「仅性别标签互换」的评估材料一致，仅本说明中性别不同。\n\n"
            )
        return (
            f"## 公平性/反事实说明（性别反事实）\n"
            f"系统将性别假设改为 **{label}**；其余与「原始」评估同一份材料。"
            f" 请据同一套事实判断 hire/reject。\n\n"
        )

    def handle(self, rec: ClaimedRecord) -> dict[str, Any] | None:
        if feishu_text_to_str(rec.fields.get("status")) != ResumeStatus.SCREENED.value:
            return None
        rid = feishu_text_to_str(rec.fields.get("resume_id")) or rec.record_id
        job_id = feishu_text_to_str(rec.fields.get("job_id"))
        iv = _gather_interviews(self.ctx.bitable, self.ctx.table_ids, rid)
        db = _gather_debates(self.ctx.bitable, self.ctx.table_ids, rid)

        use_fair = bool(getattr(self.ctx.config, "fairness_counterfactual_enabled", True))
        if not use_fair:
            gb = self._gender_block("", mode="single")
            out1 = self._run_arb(iv, db, gb)
            if not out1:
                return None
            return self._result_from_hire_reject(
                out1, rec, gender_patch=None, analyst_extra=None
            )

        # --- 反事实双评 ---
        g_field = _normalize_gender_from_field(
            feishu_text_to_str(rec.fields.get("gender"))
        )
        g_from_llm = False
        g = g_field
        if not g:
            g, g_from_llm = self._infer_gender(
                feishu_text_to_str(rec.fields.get("raw_text")), rid
            )
        an_extra: dict[str, Any] = {
            "fairness_check": True,
            "gender_resolved": g,
            "gender_from_field": bool(g_field),
        }
        if g_from_llm and not g_field:
            an_extra["gender_inferred"] = True

        patch_gender: dict[str, str] = {}
        if g_from_llm and g in ("male", "female", "unknown"):
            patch_gender["gender"] = g

        if g not in ("male", "female"):
            reason = (
                "【公平性】无法确定候选人性别（推断为 unknown），"
                "无法做性别反事实对比；已转**人工审核**（manual_review）。"
            )
            self._write_fairness_report(
                job_id,
                {
                    "kind": "fairness_alert",
                    "variant": "gender_unknown",
                    "resume_id": rid,
                    "note": "性别未知，跳过反事实，强制人工。",
                },
            )
            return {
                "hm_decision": HM_DECISION_MANUAL,
                "hm_reason": reason[:2000],
                "pipeline_stage": PipelineStage.HOLD_REVIEW.value,
                "analyst_note": _merge_analyst_fairness(
                    feishu_text_to_str(rec.fields.get("analyst_note")),
                    {**an_extra, "fairness_outcome": "unknown_gender_manual"},
                ),
                "owner_agent": "",
                **patch_gender,
            }

        g2 = _swap_gender(g)
        out_base = self._run_arb(iv, db, self._gender_block(g, mode="base"))
        out_cf = self._run_arb(iv, db, self._gender_block(g2, mode="counter"))
        if not out_base or not out_cf:
            return None

        d0 = out_base.get("decision")
        d1 = out_cf.get("decision")
        an_extra["decision_baseline_gender"] = g
        an_extra["decision_counterfactual_gender"] = g2
        an_extra["decision_baseline"] = d0
        an_extra["decision_counterfactual"] = d1

        if d0 == d1:
            return self._result_from_hire_reject(
                out_base, rec, gender_patch=patch_gender, analyst_extra=an_extra
            )

        reason = (
            f"【公平性】性别反事实下结论不一致：在假设「{_gender_label_zh(g)}」时决策为 {d0}，"
            f"在假设「{_gender_label_zh(g2)}」时决策为 {d1}。"
            f" 已**阻断**自动发 Offer，转人工复核（manual_review）。"
        )
        self._write_fairness_report(
            job_id,
            {
                "kind": "fairness_alert",
                "variant": "counterfactual_mismatch",
                "resume_id": rid,
                "original_gender": g,
                "counterfactual_gender": g2,
                "decision_baseline": d0,
                "decision_counterfactual": d1,
                "reason_baseline": str(out_base.get("reason") or "")[:1500],
                "reason_counterfactual": str(out_cf.get("reason") or "")[:1500],
            },
        )
        return {
            "hm_decision": HM_DECISION_MANUAL,
            "hm_reason": reason[:2000],
            "pipeline_stage": PipelineStage.HOLD_REVIEW.value,
            "analyst_note": _merge_analyst_fairness(
                feishu_text_to_str(rec.fields.get("analyst_note")),
                {**an_extra, "fairness_outcome": "mismatch_hold"},
            ),
            "owner_agent": "",
            **patch_gender,
        }

    def _result_from_hire_reject(
        self,
        out1: dict[str, Any],
        rec: ClaimedRecord,
        *,
        gender_patch: dict[str, str] | None,
        analyst_extra: dict[str, Any] | None,
    ) -> dict[str, Any]:
        dec = out1.get("decision")
        reason = str(out1.get("reason") or "")
        p = (
            PipelineStage.OFFER_DRAFTING.value
            if dec == "hire"
            else PipelineStage.CLOSED.value
        )
        base: dict[str, Any] = {
            "hm_decision": dec,
            "hm_reason": (reason or "")[:2000],
            "pipeline_stage": p,
            "owner_agent": "",
        }
        if gender_patch:
            base.update(gender_patch)
        if analyst_extra is not None:
            extra = {**analyst_extra, "fairness_outcome": "ok_single_or_match"}
            base["analyst_note"] = _merge_analyst_fairness(
                feishu_text_to_str(rec.fields.get("analyst_note")),
                extra,
            )
        return base
