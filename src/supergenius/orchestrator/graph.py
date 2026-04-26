"""LangGraph StateGraph：一次 tick 中，按顺序让各 Agent 各扫各的表。"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from supergenius.agents import (
    AgentBase,
    AgentContext,
    AnalystAgent,
    BusinessInterviewerAgent,
    CandidateSimulatorAgent,
    CultureInterviewerAgent,
    DebateAgent,
    HiringManagerAgent,
    HiringManagerArbiterAgent,
    InterviewFanoutAgent,
    JDStrategistAgent,
    OfferCounterAgent,
    OfferManagerAgent,
    PoolReactivatorAgent,
    PostInterviewAgent,
    ScreenerAgent,
    TechInterviewerAgent,
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
        PoolReactivatorAgent(ctx),
        InterviewFanoutAgent(ctx),
        TechInterviewerAgent(ctx),
        BusinessInterviewerAgent(ctx),
        CultureInterviewerAgent(ctx),
        PostInterviewAgent(ctx),
        DebateAgent(ctx),
        HiringManagerArbiterAgent(ctx),
        OfferManagerAgent(ctx),
        CandidateSimulatorAgent(ctx),
        OfferCounterAgent(ctx),
        AnalystAgent(ctx),
    ]
    g = StateGraph(TickState)
    prev = START
    for a in agents:
        node_name = f"agent_{a.name.replace('.', '_')}"
        g.add_node(node_name, _make_node(a))
        g.add_edge(prev, node_name)
        prev = node_name
    g.add_edge(prev, END)
    return g.compile()


def run_tick(graph: Any) -> TickState:
    state: TickState = {"processed": {}, "total": 0}
    result = graph.invoke(state)
    return result


__all__ = ["build_graph", "run_tick", "TickState"]
