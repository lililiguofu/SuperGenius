"""SuperGenius：对外只暴露 README 中的 8 个业务 Agent。

每个类可在一次 tick 内串联多段能力（初筛+人才池、三面+面后+辩论、经理仲裁+Offer+还价等），
子实现仍可在独立模块；功能与旧版多节点编排等效，未删表逻辑。"""

from supergenius.agents.analyst_agent import AnalystAgent
from supergenius.agents.base import AgentBase, AgentContext, ClaimedRecord
from supergenius.agents.hiring_manager import HiringManagerAgent
from supergenius.agents.interviewers import (
    BusinessInterviewerAgent,
    CultureInterviewerAgent,
    TechInterviewerAgent,
)
from supergenius.agents.jd_strategist import JDStrategistAgent
from supergenius.agents.offer_and_candidate import CandidateAgent
from supergenius.agents.screener import ScreenerAgent

__all__ = [
    "AgentBase",
    "AgentContext",
    "ClaimedRecord",
    "AnalystAgent",
    "BusinessInterviewerAgent",
    "CandidateAgent",
    "CultureInterviewerAgent",
    "HiringManagerAgent",
    "JDStrategistAgent",
    "ScreenerAgent",
    "TechInterviewerAgent",
]
