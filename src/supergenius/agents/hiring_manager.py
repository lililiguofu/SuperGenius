"""招聘经理：README 中本阶段为仲裁 + 发 Offer + 招聘方还价（不含批 JD，批 JD 在 `JDStrategistAgent` 内）。"""

from __future__ import annotations

from typing import Any

from supergenius.agents.base import AgentBase, ClaimedRecord
from supergenius.agents.hiring_manager_arbiter import HiringManagerArbiterAgent
from supergenius.agents.offer_and_candidate import OfferCounterAgent, OfferManagerAgent


def _impossible_job_filter() -> list[dict[str, Any]]:
    return [{"field_name": "job_id", "operator": "is", "value": ["__hm_pipeline_noop__"]}]


class HiringManagerAgent(AgentBase):
    name = "hiring_manager"
    watch_table = "jobs"
    in_progress_status = None
    rollback_status_on_error = None

    def claim_filter(self) -> list[dict[str, Any]]:
        return _impossible_job_filter()

    def handle(self, rec: ClaimedRecord) -> dict[str, Any] | None:
        raise RuntimeError("HiringManagerAgent 仅通过 tick 编排子阶段")

    def tick(self) -> int:
        n = HiringManagerArbiterAgent(self.ctx).tick()
        n += OfferManagerAgent(self.ctx).tick()
        n += OfferCounterAgent(self.ctx).tick()
        return n
