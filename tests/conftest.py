"""测试共享 fixture：用 MemoryBitable 代替真实飞书 API，用 StubLLM 代替真实 LLM。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest

from supergenius.agents.base import AgentContext
from supergenius.feishu.bitable import Record


@dataclass
class MemoryBitable:
    """内存版 BitableClient，只实现 Agent 基类/Screener 会用到的方法。"""

    tables: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    _auto_id: int = 0

    def _next_id(self, prefix: str = "rec") -> str:
        self._auto_id += 1
        return f"{prefix}{self._auto_id:06d}"

    # ---------- helpers for tests ----------

    def add_table(self, name: str) -> str:
        tid = f"tbl_{name}"
        self.tables.setdefault(tid, {})
        return tid

    def seed(self, table_id: str, fields: dict[str, Any]) -> str:
        rid = self._next_id()
        self.tables[table_id][rid] = dict(fields)
        return rid

    # ---------- BitableClient surface ----------

    def search_records(
        self,
        table_id: str,
        filter_conditions: list[dict[str, Any]] | None = None,
        conjunction: str = "and",
        sort: list[dict[str, Any]] | None = None,
        page_size: int = 100,
    ) -> list[Record]:
        def match(fields: dict[str, Any]) -> bool:
            if not filter_conditions:
                return True
            results = []
            for c in filter_conditions:
                fn = c["field_name"]
                op = c["operator"]
                val = c.get("value", [])
                actual = fields.get(fn)
                if op == "is":
                    results.append(actual in val)
                elif op == "isNot":
                    results.append(actual not in val)
                else:
                    results.append(str(actual) in [str(v) for v in val])
            if conjunction == "and":
                return all(results)
            return any(results)

        rows = self.tables.get(table_id, {})
        out: list[Record] = []
        for rid, f in rows.items():
            if match(f):
                out.append(Record(record_id=rid, fields=dict(f)))
        return out[:page_size]

    def get_record(self, table_id: str, record_id: str) -> Record:
        f = self.tables[table_id][record_id]
        return Record(record_id=record_id, fields=dict(f))

    def create_record(self, table_id: str, fields: dict[str, Any]) -> str:
        rid = self._next_id()
        self.tables.setdefault(table_id, {})[rid] = dict(fields)
        return rid

    def update_record(self, table_id: str, record_id: str, fields: dict[str, Any]) -> None:
        self.tables[table_id][record_id].update(fields)

    def batch_create_records(
        self, table_id: str, records: list[dict[str, Any]]
    ) -> list[str]:
        ids = []
        for f in records:
            ids.append(self.create_record(table_id, f))
        return ids

    def batch_update_records(
        self, table_id: str, updates: list[tuple[str, dict[str, Any]]]
    ) -> None:
        for rid, f in updates:
            self.update_record(table_id, rid, f)

    def delete_record(self, table_id: str, record_id: str) -> None:
        del self.tables[table_id][record_id]


class StubLLM:
    """可编程的 LLM 替身：按调用次数返回指定响应。"""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def chat(self, system: str, user: str, *, json_schema=None, temperature=None) -> Any:
        self.calls.append(
            {"system": system, "user": user, "json_schema": json_schema, "temperature": temperature}
        )
        if not self._responses:
            raise RuntimeError("StubLLM 响应队列耗尽")
        return self._responses.pop(0)


@pytest.fixture
def mem_bitable() -> MemoryBitable:
    return MemoryBitable()


@pytest.fixture
def agent_ctx(mem_bitable) -> AgentContext:
    jobs_tid = mem_bitable.add_table("jobs")
    resumes_tid = mem_bitable.add_table("resumes")
    events_tid = mem_bitable.add_table("events")

    cfg = MagicMock()
    cfg.scheduler.screener_var_threshold = 100.0
    cfg.scheduler.interview_spread_threshold = 3.0
    cfg.scheduler.debate_max_rounds = 3
    cfg.scheduler.reactivation_max_per_tick = 2
    cfg.report_webhook_url = ""
    cfg.fairness_counterfactual_enabled = True
    cfg.bot_notify_pipeline_steps = True
    cfg.feishu_bot_watcher_interval = 8.0
    cfg.llm.temperature = 0.3

    interviews = mem_bitable.add_table("interviews")
    debates = mem_bitable.add_table("debates")
    offers = mem_bitable.add_table("offers")
    reports = mem_bitable.add_table("reports")

    return AgentContext(
        bitable=mem_bitable,  # type: ignore[arg-type]
        table_ids={
            "jobs": jobs_tid,
            "resumes": resumes_tid,
            "events": events_tid,
            "interviews": interviews,
            "debates": debates,
            "offers": offers,
            "reports": reports,
        },
        llm=StubLLM([]),
        config=cfg,
        dry_run=False,
    )
