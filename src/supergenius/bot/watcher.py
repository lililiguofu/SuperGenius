"""后台线程：轮询简历流水线，向飞书汇报阶段与终态。

- 可选「每步」：pipeline_stage 每变一次就推一条短消息（FEISHU_BOT_NOTIFY_STEPS=1）。
- 终态：仍发原有详单；全批终态后发汇总。"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from supergenius.feishu.field_value import feishu_text_to_str

_TERMINAL_STAGES = frozenset(
    {"closed", "offer_sent", "offer_negotiation", "talent_pool", "hold_review"}
)

# 与 handler 中说明一致，覆盖中间态便于「每步汇报」
_STAGE_ZH: dict[str, str] = {
    "": "刚入库/待初筛",
    "new": "新投递",
    "screening": "初筛中",
    "screened": "初筛完成",
    "interview_queued": "排队面试",
    "interviews_in_progress": "面试中",
    "debate": "辩论中",
    "hm_arbitration": "经理仲裁中",
    "offer_drafting": "起草 Offer",
    "offer_sent": "Offer 已发",
    "offer_negotiation": "谈薪中",
    "talent_pool": "入人才库",
    "hold_review": "待人工复核",
    "closed": "已关闭",
}

_SUMMARY_STAGES: dict[str, str] = {
    "offer_sent": "[录用] 已发 Offer",
    "offer_negotiation": "[谈薪] 候选人在谈薪，等待进一步沟通",
    "talent_pool": "[人才池] 本次淘汰，已存入人才库",
    "hold_review": "[人工审核] 流程触发人工复核（如公平性告警）",
    "closed": "[结束] 流程已关闭",
}


@dataclass
class _Watch:
    chat_id: str
    resume_ids: set[str]
    notified_ids: set[str] = field(default_factory=set)  # 已发「终态详单」
    last_stage_by_resume: dict[str, str] = field(default_factory=dict)
    done: bool = False
    start_ts: float = field(default_factory=time.time)


class ResultWatcher:
    """线程安全的简历进度监控器。"""

    def __init__(self, ctx: Any, lark_client: Any, interval: float = 8.0) -> None:
        self._ctx = ctx
        self._client = lark_client
        self._interval = interval
        self._lock = threading.Lock()
        self._watches: dict[str, _Watch] = {}
        self._thread = threading.Thread(target=self._loop, daemon=True, name="watcher")
        self._thread.start()

    def register(self, batch_id: str, chat_id: str, resume_ids: list[str]) -> None:
        with self._lock:
            if batch_id in self._watches:
                self._watches[batch_id].resume_ids.update(resume_ids)
            else:
                self._watches[batch_id] = _Watch(
                    chat_id=chat_id,
                    resume_ids=set(resume_ids),
                )

    def _loop(self) -> None:
        while True:
            time.sleep(self._interval)
            try:
                self._tick()
            except Exception as exc:
                logger.exception(f"[watcher] 轮询异常: {exc}")

    def _tick(self) -> None:
        with self._lock:
            pending = [(bid, w) for bid, w in self._watches.items() if not w.done]
        for bid, watch in pending:
            try:
                self._check(bid, watch)
            except Exception as exc:
                logger.exception(f"[watcher] 检查批次 {bid}: {exc}")

    def _check(self, bid: str, watch: _Watch) -> None:
        notify_steps = bool(getattr(self._ctx.config, "bot_notify_pipeline_steps", True))
        rtid = self._ctx.table_ids["resumes"]
        newly_done: list[dict[str, Any]] = []

        for rid in list(watch.resume_ids - watch.notified_ids):
            rows = self._ctx.bitable.search_records(
                rtid,
                filter_conditions=[
                    {"field_name": "resume_id", "operator": "is", "value": [rid]}
                ],
                page_size=3,
            )
            if not rows:
                continue
            r = rows[0]
            stage = feishu_text_to_str(r.fields.get("pipeline_stage")) or feishu_text_to_str(
                r.fields.get("status")
            )
            name = feishu_text_to_str(r.fields.get("candidate_name")) or rid
            score = r.fields.get("score")
            prev = watch.last_stage_by_resume.get(rid)

            if stage != prev:
                if notify_steps and stage and stage not in _TERMINAL_STAGES:
                    self._notify_step(watch, name, stage, score, rid)
                watch.last_stage_by_resume[rid] = stage

            if stage not in _TERMINAL_STAGES:
                continue

            watch.notified_ids.add(rid)
            newly_done.append(
                {
                    "name": name,
                    "stage": stage,
                    "score": score,
                    "decision": feishu_text_to_str(r.fields.get("hm_decision")),
                    "reason": feishu_text_to_str(
                        r.fields.get("hm_reason") or r.fields.get("reason") or ""
                    )[:200],
                    "fairness_flag": json.loads(
                        feishu_text_to_str(r.fields.get("analyst_note") or "{}")
                        or "{}"
                    ).get("fairness_check"),
                }
            )

        if newly_done:
            self._notify(watch, newly_done)

        if watch.resume_ids and watch.notified_ids >= watch.resume_ids:
            with self._lock:
                watch.done = True
            self._notify_done(watch)

    def _notify_step(
        self,
        watch: _Watch,
        name: str,
        stage: str,
        score: Any,
        rid: str,
    ) -> None:
        from supergenius.bot.messenger import send_text

        label = _STAGE_ZH.get(stage, stage)
        sc = f" 初筛分 {score}" if score not in (None, "", 0) else ""
        send_text(
            self._client,
            watch.chat_id,
            f"进度 · `{rid}` {name} → {label}{sc}\n"
            f"（由 run_mvp 推进；终态时另有汇总条）",
        )

    def _notify(self, watch: _Watch, results: list[dict[str, Any]]) -> None:
        from supergenius.bot.messenger import send_text

        lines = ["--- 终态/节点结果 ---"]
        for r in results:
            label = _SUMMARY_STAGES.get(r["stage"], _STAGE_ZH.get(r["stage"], r["stage"]))
            score_str = f" (初筛得分 {r['score']})" if r.get("score") else ""
            lines.append(f"\n{r['name']}{score_str}\n  {label}")
            if r.get("reason"):
                lines.append(f"  原因：{r['reason'][:100]}")
            if r.get("fairness_flag"):
                lines.append("  [!] 已触发公平性检测，请在多维表格 reports 表查看详细告警")

        send_text(self._client, watch.chat_id, "\n".join(lines))

    def _notify_done(self, watch: _Watch) -> None:
        from supergenius.bot.messenger import send_text

        n = len(watch.resume_ids)
        elapsed = int(time.time() - watch.start_ts)
        send_text(
            self._client,
            watch.chat_id,
            f"本批 {n} 份简历已全部达终态（约 {elapsed} 秒）。\n"
            f"发「查进度」可看全局；详情见多维表格。",
        )
