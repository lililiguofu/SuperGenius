"""SuperGenius Agents（MVP：招聘经理 / JD策划官 / 简历筛选官）。"""

from supergenius.agents.base import AgentBase, AgentContext, ClaimedRecord
from supergenius.agents.hiring_manager import HiringManagerAgent
from supergenius.agents.jd_strategist import JDStrategistAgent
from supergenius.agents.screener import ScreenerAgent

__all__ = [
    "AgentBase",
    "AgentContext",
    "ClaimedRecord",
    "HiringManagerAgent",
    "JDStrategistAgent",
    "ScreenerAgent",
]
