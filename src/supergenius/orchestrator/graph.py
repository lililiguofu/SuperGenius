"""LangGraph StateGraph：一次 tick 中，按顺序让三个 Agent 各自扫一遍表。

MVP 不做条件分支（状态机就在表里，Agent 按自己的 filter 各取各的）。
到阶段三（三人面试+辩论）再扩成带 condition 的图。
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from loguru import logger

from supergenius.agents import (
    AgentBase,
    AgentContext,
    HiringManagerAgent,
    JDStrategistAgent,
    ScreenerAgent,
)


class TickState(TypedDict, total=False):
    processed: dict[str, int]
    total: int


def _make_node(agent: AgentBase):
    def _node(state: TickState) -> TickState:
        count = agent.tick()
        processed = dict(state.get("processed") or {})
        processed[agent.name] = processed.get(agent.name, 0) + count
        total = int(state.get("total") or 0) + count
        return {"processed": processed, "total": total}

    _node.__name__ = f"node_{agent.name}"
    return _node


def build_graph(ctx: AgentContext) -> Any:
    """返回 compiled graph；每次 invoke 就是一次 tick。"""
    agents: list[AgentBase] = [
        JDStrategistAgent(ctx),
        HiringManagerAgent(ctx),
        ScreenerAgent(ctx),
    ]

    g = StateGraph(TickState)
    prev = START
    for a in agents:
        node_name = f"agent_{a.name}"
        g.add_node(node_name, _make_node(a))
        g.add_edge(prev, node_name)
        prev = node_name
    g.add_edge(prev, END)
    return g.compile()


def run_tick(graph: Any) -> TickState:
    state: TickState = {"processed": {}, "total": 0}
    result = graph.invoke(state)
    # 明细由 scheduler 统一打日志，避免与「空闲」重复
    return result


__all__ = ["build_graph", "run_tick", "TickState"]
