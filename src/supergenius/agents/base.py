"""Agent 基类。

核心抽象：每个 Agent 是"观察一张表 → 领取一批待办行 → 对每行做 handle 产出 patch → 写回"。

并发控制用**乐观领取**：领取阶段把 `owner_agent` 从空写成自己名字；只有写成功的那些
行才会在下一步被处理。飞书多维表格原生没有条件更新，这里用"读→比较→写"三步走；
MVP 并发度很低，碰撞概率可忽略，写时再校验一遍 owner 不被别人占走即可。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from loguru import logger

from supergenius.feishu.bitable import BitableClient, Record
from supergenius.schema.tables import EventAction


def utc_now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class AgentContext:
    """Agent 运行时共享的依赖。"""

    bitable: BitableClient
    table_ids: dict[str, str]  # "jobs" -> tbl_xxx
    llm: Any  # LLMClient，用 Any 避免循环 import
    config: Any  # Settings
    dry_run: bool = False


@dataclass
class ClaimedRecord:
    table_name: str
    record_id: str
    fields: dict[str, Any]


class AgentBase:
    """所有 Agent 的公共父类。

    子类需要实现：
        - `watch_table` (class var)：监听哪张业务表
        - `claim_filter(self) -> list[dict]`：筛选待领取行的条件
        - `handle(self, rec) -> dict[str, Any] | None`：产出对该行的字段 patch
          返回 None 表示这轮跳过（比如 LLM 出错）。
    """

    name: str = "agent"
    watch_table: str = ""
    # 领取成功后，先把记录置为哪个 status（处理中占位），避免其他 Agent/循环重复捞取
    in_progress_status: str | None = None
    # 若曾写入 in_progress_status，handle 失败时回滚到该状态（如 JD 从 draft 进到 jd_drafting 失败则回 draft）
    rollback_status_on_error: str | None = None

    def __init__(self, ctx: AgentContext) -> None:
        self.ctx = ctx

    # ---------- 子类必须实现 ----------

    def claim_filter(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def handle(self, rec: ClaimedRecord) -> dict[str, Any] | None:
        raise NotImplementedError

    # ---------- 框架复用逻辑 ----------

    def tick(self) -> int:
        """扫一遍，领取并处理所有能领到的行，返回处理条数。"""
        table_id = self.ctx.table_ids[self.watch_table]
        try:
            records = self.ctx.bitable.search_records(
                table_id,
                filter_conditions=self.claim_filter(),
                page_size=20,
            )
        except Exception as e:
            logger.exception(f"[{self.name}] 查询待办失败: {e}")
            return 0

        if not records:
            return 0

        logger.info(f"[{self.name}] 发现 {len(records)} 条待办")
        processed = 0
        for rec in records:
            if self._try_claim(table_id, rec):
                claimed = ClaimedRecord(
                    table_name=self.watch_table,
                    record_id=rec.record_id,
                    fields=rec.fields,
                )
                try:
                    patch = self.handle(claimed)
                except Exception as e:
                    logger.exception(f"[{self.name}] handle 异常: {e}")
                    self._release_on_error(table_id, rec.record_id, str(e))
                    continue

                if patch is None:
                    logger.warning(f"[{self.name}] 处理 {rec.record_id} 返回 None，释放")
                    self._release_on_error(table_id, rec.record_id, "handle returned None")
                    continue

                self._finalize(table_id, rec.record_id, patch)
                processed += 1
        return processed

    # ---------- 内部 ----------

    def _try_claim(self, table_id: str, rec: Record) -> bool:
        """把 owner_agent 置为自己，status 置为 in_progress_status（如果有）。

        飞书没有 CAS，这里用"读已领→写→再读→对比"的乐观策略。MVP 足够。
        """
        current_owner = str(rec.fields.get("owner_agent") or "")
        if current_owner and current_owner != self.name:
            logger.debug(f"[{self.name}] {rec.record_id} 已被 {current_owner} 持有，跳过")
            return False

        patch: dict[str, Any] = {"owner_agent": self.name, "updated_at": utc_now_iso()}
        if self.in_progress_status:
            patch["status"] = self.in_progress_status

        try:
            self.ctx.bitable.update_record(table_id, rec.record_id, patch)
        except Exception as e:
            logger.warning(f"[{self.name}] 领取 {rec.record_id} 失败: {e}")
            return False

        # 再读一次确认没有被别人抢走
        try:
            fresh = self.ctx.bitable.get_record(table_id, rec.record_id)
            if str(fresh.fields.get("owner_agent") or "") != self.name:
                logger.info(f"[{self.name}] {rec.record_id} 被他人抢占，放弃")
                return False
        except Exception as e:
            logger.warning(f"[{self.name}] 确认 {rec.record_id} 所有权失败: {e}")
            return False

        self.log_event(EventAction.CLAIM, self.watch_table, rec.record_id, {})
        return True

    def _finalize(self, table_id: str, record_id: str, patch: dict[str, Any]) -> None:
        patch = {**patch, "updated_at": utc_now_iso()}
        # 交接给下游或释放 owner
        if "owner_agent" not in patch:
            patch["owner_agent"] = ""
        if self.ctx.dry_run:
            logger.info(f"[{self.name}] DRY_RUN 更新 {record_id} -> {patch}")
            return
        self.ctx.bitable.update_record(table_id, record_id, patch)
        self.log_event(EventAction.UPDATE, self.watch_table, record_id, patch)

    def _release_on_error(self, table_id: str, record_id: str, msg: str) -> None:
        patch: dict[str, Any] = {"owner_agent": "", "updated_at": utc_now_iso()}
        if self.rollback_status_on_error is not None:
            patch["status"] = self.rollback_status_on_error
        try:
            self.ctx.bitable.update_record(table_id, record_id, patch)
        except Exception as e:
            logger.error(f"[{self.name}] 释放 {record_id} 失败: {e}")
        self.log_event(EventAction.ERROR, self.watch_table, record_id, {"error": msg})

    def log_event(
        self,
        action: EventAction,
        target_table: str,
        target_id: str,
        payload: dict[str, Any],
    ) -> None:
        events_tid = self.ctx.table_ids.get("events")
        if not events_tid or self.ctx.dry_run:
            return
        try:
            self.ctx.bitable.create_record(
                events_tid,
                {
                    "event_id": uuid.uuid4().hex[:12],
                    "ts": utc_now_iso(),
                    "actor_agent": self.name,
                    "action": action.value,
                    "target_table": target_table,
                    "target_id": target_id,
                    "payload": json.dumps(payload, ensure_ascii=False)[:4000],
                },
            )
        except Exception as e:
            logger.warning(f"[{self.name}] 写 events 失败(忽略): {e}")


def backoff_sleep(seconds: float) -> None:
    time.sleep(seconds)


__all__ = ["AgentBase", "AgentContext", "ClaimedRecord", "utc_now_iso"]
